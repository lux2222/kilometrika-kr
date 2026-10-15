"""Read-only archive analysis; deliberately independent of ingestion and Store."""
from datetime import datetime
from pathlib import Path
import hashlib
import math
import re
import sqlite3
import xml.etree.ElementTree as ET

# Personal manual configuration. Bounds are inclusive integer bpm.
ZONES = (("Z1", 87, 104), ("Z2", 105, 121), ("Z3", 122, 139),
         ("Z4", 140, 156), ("Z5", 157, 174))
MAX_GAP_SECONDS = 15
NS = '{http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2}'


def classify(value):
    try:
        value = float(value)
    except (ValueError, TypeError):
        return None
    # TCX HeartRateInBeatsPerMinute is an unsigned byte; zero is missing/invalid.
    if not math.isfinite(value) or not value.is_integer() or not 1 <= value <= 255:
        return None
    for name, low, high in ZONES:
        if low <= value <= high:
            return name
    return 'below' if value < ZONES[0][1] else 'above'


def criteria():
    return {'zones_bpm': {n: [lo, hi] for n, lo, hi in ZONES},
            'bounds': 'inclusive integers', 'valid_hr_bpm': [1, 255],
            'max_adjacent_gap_s': MAX_GAP_SECONDS,
            'allocation': 'preceding point HR; both adjacent points must be valid',
            'order': 'original Lap/Track/Trackpoint order; never bridge boundaries',
            'gap_over_threshold': 'unconfirmed, not pause',
            'gps_speed_filter': False, 'extend_last_point': False,
            'pause_policy': 'no inferred pause; unsupported explicit event metadata fails closed',
            'hash_algorithm': 'sha256 first 16 hex characters (existing stored format)'}


def analyze_tcx(raw):
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/entity is not supported')
    root = ET.fromstring(raw)
    if root.tag != NS + 'TrainingCenterDatabase':
        raise ValueError('unsupported TCX namespace')
    # TCX core has no timer-event stream. Do not silently include explicit
    # vendor timer/pause markers whose semantics have not been verified.
    for el in root.iter():
        local = el.tag.rsplit('}', 1)[-1].lower()
        if any(word in local for word in ('pause', 'resume', 'timerevent')):
            raise ValueError('explicit pause metadata requires verified adapter')
    activities = root.findall('./' + NS + 'Activities/' + NS + 'Activity')
    if len(activities) != 1:
        raise ValueError('expected exactly one Activity')
    seconds = {n: 0.0 for n, _, _ in ZONES}
    seconds.update(below=0.0, above=0.0)
    skipped = {'missing_or_invalid_hr': 0, 'invalid_time': 0,
               'duplicate_or_reversed_time': 0, 'gap_over_threshold': 0}
    unconfirmed_gap_s = 0.0
    lap_times = []
    tracks = 0
    for lap in activities[0].findall(NS + 'Lap'):
        try:
            duration = float(lap.findtext(NS + 'TotalTimeSeconds'))
            lap_times.append(duration if math.isfinite(duration) and duration >= 0 else None)
        except (TypeError, ValueError):
            lap_times.append(None)
        for track in lap.findall(NS + 'Track'):
            tracks += 1
            points = track.findall(NS + 'Trackpoint')
            for a, b in zip(points, points[1:]):
                try:
                    ta, tb = [datetime.fromisoformat(p.findtext(NS + 'Time').replace('Z', '+00:00')) for p in (a, b)]
                    if ta.tzinfo is None or tb.tzinfo is None:
                        raise ValueError('timezone required')
                    dt = (tb - ta).total_seconds()
                except (ValueError, TypeError, AttributeError):
                    skipped['invalid_time'] += 1
                    continue
                if dt <= 0:
                    skipped['duplicate_or_reversed_time'] += 1
                    continue
                if dt > MAX_GAP_SECONDS:
                    skipped['gap_over_threshold'] += 1
                    unconfirmed_gap_s += dt
                    continue
                ca, cb = [classify(p.findtext(NS + 'HeartRateBpm/' + NS + 'Value')) for p in (a, b)]
                if ca is None or cb is None:
                    skipped['missing_or_invalid_hr'] += 1
                    continue
                seconds[ca] += dt
    return {'status': 'ok', 'zones_seconds': {n: seconds[n] for n, _, _ in ZONES},
            'below_range_seconds': seconds['below'], 'above_range_seconds': seconds['above'],
            'out_of_range_seconds': seconds['below'] + seconds['above'],
            'valid_observed_seconds': sum(seconds.values()),
            'original_duration_seconds': sum(lap_times) if lap_times and None not in lap_times else None,
            'original_duration_source': 'TCX Lap/TotalTimeSeconds sum',
            'unconfirmed_gap_seconds': unconfirmed_gap_s,
            'unobservable_seconds': None, 'excluded_interval_counts': skipped,
            'track_count': tracks, 'criteria': criteria(),
            'limitations': ['Unobservable time is not inferred by subtracting from Lap duration.',
                            'Track boundaries and final sample tails are not connected or extended.',
                            'No pause is inferred from gaps, missing GPS, or zero speed.',
                            'Overlapping/reversed source timelines are not repaired; totals need not reconcile.']}


def query_analysis(data_dir, activity_id):
    base = {'activity_id': activity_id, 'status': 'unavailable', 'criteria': criteria()}
    def unavailable(reason):
        return dict(base, reason=reason)
    data_dir = Path(data_dir)
    try:
        with sqlite3.connect((data_dir / 'tcx.db').resolve().as_uri() + '?mode=ro', uri=True) as conn:
            row = conn.execute('SELECT file_hash, category FROM activities WHERE id = ?', (activity_id,)).fetchone()
    except sqlite3.Error:
        return unavailable('database_unavailable')
    if not row:
        return unavailable('activity_not_found')
    if row[1] != 'strength':
        return unavailable('category_not_strength')
    if not isinstance(row[0], str) or not re.fullmatch(r'[0-9a-f]{16}', row[0]):
        return unavailable('unsupported_stored_hash')
    candidates = list((data_dir / 'archive').glob('*.tcx'))
    matches = []
    for path in candidates:
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest()[:16] == row[0]:
            matches.append(raw)
    if not matches:
        return unavailable('original_missing_or_hash_mismatch')
    if len({hashlib.sha256(raw).digest() for raw in matches}) != 1:
        return unavailable('ambiguous_stored_hash')
    try:
        result = analyze_tcx(matches[0])
    except (ET.ParseError, ValueError) as exc:
        return unavailable(str(exc))
    return dict(result, activity_id=activity_id, source_hash=row[0],
                source_sha256=hashlib.sha256(matches[0]).hexdigest())
