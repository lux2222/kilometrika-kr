"""Parse TCX files into summary + trackpoint series."""
from __future__ import annotations

import hashlib
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone


CATEGORIES = {
    "run":  ("бег", "run", "jog"),
    "bike": ("вел", "bik", "cycl", "ride"),
}


def sport_category(sport: str | None) -> str:
    """Единственное место, где решается 'бег это или велосипед'."""
    s = (sport or "").lower()
    for cat, keys in CATEGORIES.items():
        if any(k in s for k in keys):
            return cat
    return "other"


def _as_utc(dt: datetime | None) -> datetime | None:
    """TCX/GPX времена — UTC; tcxreader теряет tzinfo, восстанавливаем."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

from math import asin, cos, radians, sin, sqrt

from tcxreader.tcxreader import TCXReader


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    """Distance in meters between two lat/lon points."""
    rlat1, rlon1, rlat2, rlon2 = map(radians, (lat1, lon1, lat2, lon2))
    dlat, dlon = rlat2 - rlat1, rlon2 - rlon1
    a = sin(dlat / 2) ** 2 + cos(rlat1) * cos(rlat2) * sin(dlon / 2) ** 2
    return 6_371_000 * 2 * asin(sqrt(a))


@dataclass
class Trackpoint:
    time: datetime
    lat: float | None
    lon: float | None
    elevation: float | None
    distance: float | None       # cumulative meters
    hr: int | None
    cadence: int | None
    speed: float | None          # m/s (from TPX extension if present)
    watts: float | None


@dataclass
class Activity:
    file_hash: str
    sport: str
    category: str
    start_time: datetime
    duration_s: float
    distance_m: float
    calories: int | None
    hr_avg: float | None
    hr_max: float | None
    ascent_m: float | None
    descent_m: float | None
    cadence_avg: float | None
    laps: list[dict] = field(default_factory=list)
    trackpoints: list[Trackpoint] = field(default_factory=list)

    @property
    def avg_speed_kmh(self) -> float | None:
        if self.duration_s and self.distance_m:
            return round(self.distance_m / self.duration_s * 3.6, 2)
        return None

    @property
    def avg_pace_min_km(self) -> float | None:
        """Minutes per km — the runner's view of the same number."""
        if self.duration_s and self.distance_m:
            return round((self.duration_s / 60) / (self.distance_m / 1000), 2)
        return None


def _fill_missing_from_gps(tps: list[Trackpoint]) -> None:
    """Some exports (e.g. Huawei/Mi 'Health' apps) write only GPS + altitude:
    no per-point DistanceMeters and no Speed. Reconstruct both from
    coordinates so charts and pace still work."""
    if not tps:
        return
    have_distance = sum(1 for t in tps if t.distance is not None)
    if have_distance >= len(tps) * 0.5:      # file already has proper data
        return

    cum = 0.0
    prev = None
    for tp in tps:
        if not (isinstance(tp.lat, (int, float)) and isinstance(tp.lon, (int, float))
                and math.isfinite(tp.lat) and math.isfinite(tp.lon)
                and abs(tp.lat) <= 90 and abs(tp.lon) <= 180):
            prev = None
            continue
        if prev is not None and None not in (tp.lat, tp.lon, prev.lat, prev.lon):
            step = _haversine_m(prev.lat, prev.lon, tp.lat, tp.lon)
            dt = (tp.time - prev.time).total_seconds() if tp.time and prev.time else 0
            # ignore GPS jitter jumps (>30 m/s is not a human on foot/bike)
            if dt > 0 and step / dt < 30:
                cum += step
                if tp.speed is None:
                    tp.speed = round(step / dt, 2)
        tp.distance = round(cum, 1)
        prev = tp


def parse_tcx(path: str) -> Activity:
    with open(path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    data = TCXReader().read(path, only_gps=False)
    indoor_summary = sport_category(data.activity_type) == 'other' or not any(
        t.latitude is not None and t.longitude is not None
        and math.isfinite(t.latitude) and math.isfinite(t.longitude)
        and abs(t.latitude) <= 90 and abs(t.longitude) <= 180
        for t in data.trackpoints)
    # Indoor/Other summaries must not depend on the presence of GPS.
    # Keep the established bicycle timing contract unchanged.
    if indoor_summary:
        root = ET.parse(path).getroot()
        ns = {'t': 'http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2'}
        raw_laps = root.findall('.//t:Activity/t:Lap', ns)
        durations = []
        for raw, lap in zip(raw_laps, data.laps):
            text = raw.findtext('t:TotalTimeSeconds', namespaces=ns)
            try:
                value = float(text) if text is not None else None
            except (TypeError, ValueError):
                value = None
            if value is not None and math.isfinite(value) and value >= 0:
                lap.duration = value
                durations.append(value)
            stamp = raw.get('StartTime')
            if stamp:
                try:
                    lap.start_time = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
                except ValueError:
                    pass
        if raw_laps and len(durations) == len(raw_laps):
            data.duration = sum(durations)
        else:
            times = [stamp for t in data.trackpoints if (stamp := _as_utc(t.time)) is not None]
            data.duration = (max(times)-min(times)).total_seconds() if len(times) > 1 else 0.0
        if data.laps and data.laps[0].start_time:
            data.start_time = data.laps[0].start_time

    laps = []
    for lap in getattr(data, "laps", []) or []:
        laps.append({
            "start_time": lap.start_time.isoformat() if lap.start_time else None,
            "duration_s": lap.duration,
            "distance_m": lap.distance,
            "calories": lap.calories,
            "hr_avg": lap.hr_avg,
            "hr_max": lap.hr_max,
        })

    tps = []
    for tp in data.trackpoints:
        ext = tp.tpx_ext or {}
        tps.append(Trackpoint(
            time=tp.time,
            lat=tp.latitude,
            lon=tp.longitude,
            elevation=tp.elevation,
            distance=tp.distance,
            hr=tp.hr_value,
            cadence=tp.cadence,
            speed=ext.get("Speed"),
            watts=ext.get("Watts"),
        ))

    _fill_missing_from_gps(tps)

    if indoor_summary:
        def weighted_hr(points):
            weighted = seconds = 0.0
            for a, b in zip(points, points[1:]):
                if a.time is None or b.time is None:
                    continue
                dt = (_as_utc(b.time) - _as_utc(a.time)).total_seconds()
                if (0 < dt <= 15 and a.hr is not None and b.hr is not None
                        and math.isfinite(a.hr) and math.isfinite(b.hr)
                        and a.hr > 0 and b.hr > 0):
                    weighted += a.hr * dt
                    seconds += dt
            return weighted / seconds if seconds else None
        data.hr_avg = weighted_hr(tps)
        for summary, lap in zip(laps, data.laps):
            points = [Trackpoint(t.time, None, None, None, None, t.hr_value, None, None, None)
                      for t in lap.trackpoints]
            summary['hr_avg'] = weighted_hr(points)

        # GPS-independent raw Lap summaries also cover summary-only TCX exports.
        def raw_number(lap, tag):
            try:
                value = float(lap.findtext(tag, namespaces=ns))
                return value if math.isfinite(value) and value >= 0 else None
            except (TypeError, ValueError):
                return None
        raw_durations = [raw_number(lap, 't:TotalTimeSeconds') for lap in raw_laps]
        if raw_durations and all(v is not None for v in raw_durations):
            data.duration = sum(raw_durations)
        if not laps:
            laps = [{'start_time': lap.get('StartTime'),
                     'duration_s': raw_number(lap, 't:TotalTimeSeconds'),
                     'distance_m': raw_number(lap, 't:DistanceMeters'),
                     'calories': raw_number(lap, 't:Calories'),
                     'hr_avg': raw_number(lap, 't:AverageHeartRateBpm/t:Value'),
                     'hr_max': raw_number(lap, 't:MaximumHeartRateBpm/t:Value')}
                    for lap in raw_laps]
        calories = [raw_number(lap, 't:Calories') for lap in raw_laps]
        if calories and all(v is not None for v in calories):
            data.calories = sum(calories)
        averages = [(raw_number(lap, 't:AverageHeartRateBpm/t:Value'),
                     raw_number(lap, 't:TotalTimeSeconds')) for lap in raw_laps]
        if data.hr_avg is None and averages and all(h is not None and 0 < h <= 255 and d is not None and d > 0 for h, d in averages):
            data.hr_avg = sum(h*d for h,d in averages) / sum(d for _,d in averages)
        maxima = [raw_number(lap, 't:MaximumHeartRateBpm/t:Value') for lap in raw_laps]
        maxima = [h for h in maxima if h is not None and 0 < h <= 255]
        if data.hr_max is None and maxima:
            data.hr_max = max(maxima)
        if data.start_time is None:
            stamp = root.findtext('.//t:Activity/t:Id', namespaces=ns)
            try:
                data.start_time = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except (ValueError, AttributeError):
                pass

    return Activity(
        file_hash=file_hash,
        sport=data.activity_type or "Other",
        category=sport_category(data.activity_type),
        start_time=_as_utc(data.start_time),
        duration_s=data.duration or 0.0,
        distance_m=data.distance or 0.0,
        calories=data.calories,
        hr_avg=data.hr_avg,
        hr_max=data.hr_max,
        ascent_m=data.ascent,
        descent_m=data.descent,
        cadence_avg=data.cadence_avg,
        laps=laps,
        trackpoints=tps,
    )


# ---------------------------------------------------------------- GPX

_GPX_SPORT = {
    # частые значения <type> у Strava/Garmin/OsmAnd
    "running": "Running", "run": "Running", "trail_running": "Running",
    "cycling": "Biking", "biking": "Biking", "ride": "Biking",
    "walking": "Walking", "hiking": "Hiking", "swimming": "Swimming",
}


def _gpx_ext_int(point, *names) -> int | None:
    """Достаёт hr/cad из TrackPointExtension независимо от namespace-префикса."""
    for ext in point.extensions or []:
        for el in ext.iter() if hasattr(ext, "iter") else [ext]:
            tag = el.tag.rsplit("}", 1)[-1].lower()
            if tag in names and el.text and el.text.strip().isdigit():
                return int(el.text.strip())
    return None


def parse_gpx(path: str) -> Activity:
    import gpxpy

    with open(path, "rb") as f:
        file_hash = hashlib.sha256(f.read()).hexdigest()[:16]

    with open(path, encoding="utf-8-sig") as f:   # -sig: некоторые экспортёры пишут BOM
        gpx = gpxpy.parse(f)

    tps: list[Trackpoint] = []
    sport = "Other"
    for track in gpx.tracks:
        if track.type:
            sport = _GPX_SPORT.get(track.type.strip().lower(), track.type)
        elif track.name and sport == "Other":
            sport = track.name
        for seg in track.segments:
            for p in seg.points:
                tps.append(Trackpoint(
                    time=p.time,
                    lat=p.latitude, lon=p.longitude,
                    elevation=p.elevation,
                    distance=None,             # в GPX нет — восстановим по GPS
                    hr=_gpx_ext_int(p, "hr", "heartrate"),
                    cadence=_gpx_ext_int(p, "cad", "cadence"),
                    speed=None,
                    watts=None,
                ))

    if not tps:
        raise ValueError("GPX has no trackpoints")
    timed = [t for t in tps if t.time is not None]
    if timed:
        tps = sorted(timed, key=lambda t: t.time)
        _fill_missing_from_gps(tps)
        start = _as_utc(tps[0].time)
        duration = (tps[-1].time - tps[0].time).total_seconds() if len(tps) > 1 else 0.0
    else:
        # маршрут без времени (AllTrails и т.п.): дистанция чисто по координатам,
        # даты/темпа нет — календарную статистику такие записи не трогают
        cum, prev = 0.0, None
        for tp in tps:
            if prev is not None and None not in (tp.lat, tp.lon, prev.lat, prev.lon):
                cum += _haversine_m(prev.lat, prev.lon, tp.lat, tp.lon)
            tp.distance = round(cum, 1)
            prev = tp
        start, duration = None, 0.0
    distance = tps[-1].distance if tps[-1].distance else 0.0
    hrs = [t.hr for t in tps if t.hr]
    up, _down = gpx.get_uphill_downhill()

    return Activity(
        file_hash=file_hash,
        sport=sport,
        category=sport_category(sport),
        start_time=start,
        duration_s=duration,
        distance_m=distance,
        calories=None,
        hr_avg=round(sum(hrs) / len(hrs), 1) if hrs else None,
        hr_max=max(hrs) if hrs else None,
        ascent_m=round(up, 1) if up else None,
        descent_m=round(_down, 1) if _down else None,
        cadence_avg=None,
        laps=[],
        trackpoints=tps,
    )


# ---------------------------------------------------------------- dispatcher

PARSERS = {".tcx": parse_tcx, ".gpx": parse_gpx}
SUPPORTED_EXTENSIONS = tuple(PARSERS)


def parse_activity(path: str) -> Activity:
    from pathlib import Path
    ext = Path(path).suffix.lower()
    if ext not in PARSERS:
        raise ValueError(f"unsupported format: {ext} (supported: {', '.join(PARSERS)})")
    return PARSERS[ext](path)
