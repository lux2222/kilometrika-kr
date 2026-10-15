"""Hash-verified, transactional repair using the SAME parser as upload.
Never creates an activity, changes its identity, or replaces a valid value.
"""
import hashlib
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from parser import parse_activity


def missing(key, value):
    if key == 'start_time':
        try:
            datetime.fromisoformat(value.replace('Z', '+00:00'))
            return False
        except (ValueError, TypeError, AttributeError):
            return True
    return value is None or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0 or (key in ('duration_s', 'hr_avg', 'hr_max') and value == 0)


def recover_missing(store, activity_id, archive):
    current = store.get_activity(activity_id)
    fields = ('start_time', 'duration_s', 'calories', 'hr_avg', 'hr_max')
    required = [k for k in fields if missing(k, current[k])]
    points = store.get_trackpoints(activity_id)
    if not any(p['time'] and p['hr'] and p['hr'] > 0 for p in points):
        required.append('heart_rate_series')
    expected = current['file_hash']
    if not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{16}', expected):
        return {'status': 'failed', 'reason': 'unsupported_stored_hash'}
    matches = []
    for path in Path(archive).glob('*.tcx'):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if digest[:16] == expected:
            matches.append((path, digest))
    if not matches:
        return {'status': 'failed' if required else 'not_needed',
                'reason': 'original_missing_or_hash_mismatch', 'missing': required}
    if len({digest for _, digest in matches}) != 1:
        return {'status': 'failed', 'reason': 'ambiguous_stored_hash'}
    path, digest = matches[0]
    try:
        parsed = parse_activity(str(path))
        if parsed.file_hash != expected or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            return {'status': 'failed', 'reason': 'original_changed_during_parse'}
        values = {k: getattr(parsed, k) for k in fields}
        if values['start_time']:
            values['start_time'] = values['start_time'].isoformat()
        changed = []
        inserted = updated = 0
        def timekey(value):
            try:
                stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
                return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp()
            except (ValueError, AttributeError, TypeError):
                return None
        with store._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            live = c.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
            if not live or live['file_hash'] != expected:
                raise ValueError('activity_identity_changed')
            for key, value in values.items():
                if missing(key, live[key]) and not missing(key, value):
                    c.execute(f'UPDATE activities SET {key}=? WHERE id=?', (value, activity_id))
                    changed.append(key)
            existing = {}
            for row in c.execute('SELECT rowid,* FROM trackpoints WHERE activity_id=?', (activity_id,)):
                key = timekey(row['time'])
                if key is not None:
                    existing.setdefault(key, []).append(row)
            columns = ('lat','lon','elevation','distance','hr','cadence','speed','watts')
            for point in parsed.trackpoints:
                if point.time is None:
                    continue
                stamp = point.time.isoformat(); key = timekey(stamp)
                if key is None:
                    continue
                row = tuple(getattr(point, col) for col in columns)
                if key not in existing:
                    c.execute('INSERT INTO trackpoints (activity_id,time,lat,lon,elevation,distance,hr,cadence,speed,watts) VALUES (?,?,?,?,?,?,?,?,?,?)', (activity_id,stamp,*row))
                    existing[key] = []
                    inserted += 1
                elif len(existing[key]) == 1:
                    old = existing[key][0]
                    for col, value in zip(columns, row):
                        if value is not None and (old[col] is None or (col == 'hr' and old[col] <= 0)):
                            c.execute(f'UPDATE trackpoints SET {col}=? WHERE rowid=?', (value,old['rowid']))
                            updated += 1
            result = c.execute('SELECT * FROM activities WHERE id=?', (activity_id,)).fetchone()
            remaining = [k for k in fields if missing(k, result[k])]
            if not c.execute('SELECT 1 FROM trackpoints WHERE activity_id=? AND time != ? AND hr > 0 LIMIT 1', (activity_id,'')).fetchone():
                remaining.append('heart_rate_series')
        return {'status': 'partial' if remaining else 'recovered' if changed or inserted or updated else 'not_needed',
                'reason': 'source_missing_required_data' if remaining else None,
                'fields_restored': changed, 'points_inserted': inserted,
                'point_values_restored': updated, 'missing': remaining}
    except Exception as exc:
        # Store connection closes without commit on exception: repair is rolled back.
        return {'status': 'failed', 'reason': type(exc).__name__ + ': ' + str(exc)}
