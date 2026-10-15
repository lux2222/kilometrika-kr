"""API поверх FastAPI TestClient: загрузка, дедуп, заметки, фото, кадр рамки."""
import io
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

FIX = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # main читает env на импорте — сбрасываем модуль между тестами
    for m in ("main",):
        sys.modules.pop(m, None)
    import main
    from fastapi.testclient import TestClient
    with TestClient(main.app) as c:
        yield c


def _upload(client, name):
    data = (FIX / name).read_bytes()
    return client.post("/api/upload", files={"file": (name, io.BytesIO(data))})


def test_upload_list_detail(client):
    r = _upload(client, "garmin_run.tcx")
    assert r.status_code == 200
    assert r.json()["sport"] == "Running"
    acts = client.get("/api/activities").json()
    assert len(acts) == 1 and acts[0]["category"] == "run"
    detail = client.get(f"/api/activities/{acts[0]['id']}").json()
    assert detail["photos"] == [] and "laps" in detail


def test_duplicate_409(client):
    assert _upload(client, "garmin_run.tcx").status_code == 200
    assert _upload(client, "garmin_run.tcx").status_code == 409


def test_bad_extension_400(client):
    r = client.post("/api/upload", files={"file": ("x.fit", io.BytesIO(b"123"))})
    assert r.status_code == 400


def test_gpx_upload(client):
    r = _upload(client, "strava_ride.gpx")
    assert r.status_code == 200 and r.json()["sport"] == "Biking"


def test_notes_roundtrip(client):
    _upload(client, "garmin_run.tcx")
    r = client.patch("/api/activities/1/notes", json={"notes": "жара +30"})
    assert r.status_code == 200
    assert client.get("/api/activities/1").json()["notes"] == "жара +30"


def test_photos_roundtrip(client):
    _upload(client, "garmin_run.tcx")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post("/api/activities/1/photos", files={"file": ("p.png", io.BytesIO(png))})
    assert r.status_code == 200
    pid = r.json()["id"]
    assert client.get(f"/api/photos/{pid}").status_code == 200
    assert client.post("/api/activities/1/photos",
                       files={"file": ("x.txt", io.BytesIO(b"no"))}).status_code == 400
    assert client.delete(f"/api/photos/{pid}").json()["ok"]


def test_media_keeps_original_name_and_bytes(client):
    """Файл возвращается байт-в-байт (метаданные целы) и под своим именем —
    включая кириллицу, иначе браузер сохранит его как номер из URL."""
    from urllib.parse import unquote
    _upload(client, "garmin_run.tcx")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    for name in ("IMG_20130509_132600.png", "Пробежка у канала.png"):
        pid = client.post("/api/activities/1/photos",
                          files={"file": (name, io.BytesIO(png))}).json()["id"]
        r = client.get(f"/api/photos/{pid}")
        assert r.content == png, "оригинал обязан отдаваться без перекодирования"
        assert name in unquote(r.headers["content-disposition"])


def test_frame_png_and_etag(client):
    _upload(client, "garmin_run.tcx")
    r = client.get("/api/frame.png")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    etag = r.headers["etag"]
    r2 = client.get("/api/frame.png", headers={"If-None-Match": etag})
    assert r2.status_code == 304


def test_frame_etag_changes_on_category(client):
    """Ручная смена бег↔велосипед меняет итоги на рамке — ETag обязан смениться,
    иначе рамка получит 304 и останется со старыми цифрами."""
    _upload(client, "garmin_run.tcx")
    before = client.get("/api/frame.png").headers["etag"]
    client.patch("/api/activities/1/category", json={"category": "bike"})
    r = client.get("/api/frame.png", headers={"If-None-Match": before})
    assert r.status_code == 200 and r.headers["etag"] != before


def test_thumbnails_dont_collide_on_same_stem(client, tmp_path):
    """sunset.jpg и sunset.png в одной тренировке — разные миниатюры,
    удаление одного фото не трогает миниатюру другого."""
    _upload(client, "garmin_run.tcx")
    from PIL import Image
    ids = {}
    for name, color in (("sunset.jpg", (200, 0, 0)), ("sunset.png", (0, 0, 200))):
        p = tmp_path / name
        Image.new("RGB", (800, 600), color).save(p)
        ids[name] = client.post("/api/activities/1/photos",
                                files={"file": (name, p.read_bytes())}).json()["id"]

    def thumb_color(pid):
        th = client.get(f"/api/photos/{pid}?thumb=1").content
        return Image.open(io.BytesIO(th)).convert("RGB").getpixel((5, 5))

    jpg, png = thumb_color(ids["sunset.jpg"]), thumb_color(ids["sunset.png"])
    assert jpg[0] > 150 and png[2] > 150, (jpg, png)
    client.delete(f"/api/photos/{ids['sunset.png']}")
    assert thumb_color(ids["sunset.jpg"])[0] > 150


def test_splits_track_starting_offset(client):
    """Трек возобновлённого круга: накопленная дистанция начинается с 1500 м.
    Сплиты считаются от старта трека, без отрицательных/выдуманных времён."""
    from datetime import datetime, timedelta, timezone

    import main
    from parser import Activity, Trackpoint

    t0 = datetime(2026, 7, 10, 6, 0, tzinfo=timezone.utc)
    tps = [Trackpoint(time=t0 + timedelta(seconds=30 * i), lat=None, lon=None,
                      elevation=None, distance=1500 + 100.0 * i, hr=None,
                      cadence=None, speed=None, watts=None) for i in range(21)]
    act = Activity(file_hash="offset0000000001", sport="Running", category="run",
                   start_time=t0, duration_s=600, distance_m=2000, calories=None,
                   hr_avg=None, hr_max=None, ascent_m=None, descent_m=None,
                   cadence_avg=None, trackpoints=tps)
    act_id = main.store.save(act, "offset.tcx")
    s = client.get(f"/api/activities/{act_id}/splits").json()
    assert [x["km"] for x in s] == [1.0, 2.0]
    assert all(abs(x["pace_s_km"] - 300) < 1 for x in s), s


def test_upload_too_large_rejected_early(client, monkeypatch):
    """Слишком большое тело отбивается по Content-Length до приёма целиком."""
    import main
    _upload(client, "garmin_run.tcx")
    monkeypatch.setattr(main, "_MAX_BODY", 1000)
    r = client.post("/api/activities/1/photos",
                    files={"file": ("big.mp4", io.BytesIO(b"0" * 5000))})
    assert r.status_code == 413
    assert client.get("/api/activities/1").json()["photos"] == []


def test_video_roundtrip(client):
    _upload(client, "garmin_run.tcx")
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 200      # минимальная mp4-сигнатура
    r = client.post("/api/activities/1/photos", files={"file": ("clip.mp4", io.BytesIO(mp4))})
    assert r.status_code == 200
    assert r.json()["kind"] == "video"
    detail = client.get("/api/activities/1").json()
    kinds = {p["filename"]: p["kind"] for p in detail["photos"]}
    assert kinds["clip.mp4"] == "video"
    assert client.get(f"/api/photos/{r.json()['id']}").status_code == 200


def test_media_kind_image(client):
    _upload(client, "garmin_run.tcx")
    png = b"\x89PNG\r\n\x1a\n" + b"0" * 100
    r = client.post("/api/activities/1/photos", files={"file": ("p.png", io.BytesIO(png))})
    assert r.json()["kind"] == "image"


def test_photo_thumbnail(client, tmp_path):
    _upload(client, "garmin_run.tcx")
    from PIL import Image
    big = tmp_path / "big.jpg"
    Image.new("RGB", (2000, 1500), (30, 120, 60)).save(big, quality=90)
    r = client.post("/api/activities/1/photos",
                    files={"file": ("big.jpg", big.read_bytes())})
    pid = r.json()["id"]
    orig = client.get(f"/api/photos/{pid}")
    th = client.get(f"/api/photos/{pid}?thumb=1")
    assert th.status_code == 200
    assert len(th.content) < len(orig.content) / 3, "тумба обязана быть сильно меньше"
    # у видео thumb молча отдаёт оригинал
    mp4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 200
    vid = client.post("/api/activities/1/photos",
                      files={"file": ("c.mp4", io.BytesIO(mp4))}).json()["id"]
    assert client.get(f"/api/photos/{vid}?thumb=1").status_code == 200


def test_splits_endpoint(client):
    """Синтетический GPX 2.5 км ровным темпом 5:00/км: два полных километра
    и хвост; темп восстанавливается интерполяцией границ."""
    v = 1000 / 300                       # м/с при темпе 5:00/км
    pts = []
    for i in range(76):                  # 750 c => 2.5 км
        t = i * 10
        lat = 55.55 + v * t / 111320
        pts.append(f'<trkpt lat="{lat:.6f}" lon="37.55"><ele>{100 + i * 0.1:.1f}</ele>'
                   f'<time>2026-07-10T06:{t // 60:02d}:{t % 60:02d}Z</time></trkpt>')
    gpx = ('<?xml version="1.0"?><gpx version="1.1" creator="t" '
           'xmlns="http://www.topografix.com/GPX/1/1"><trk><name>Running</name>'
           f'<trkseg>{"".join(pts)}</trkseg></trk></gpx>').encode()
    r = client.post("/api/upload", files={"file": ("run25.gpx", io.BytesIO(gpx))})
    assert r.status_code == 200
    s = client.get("/api/activities/1/splits").json()
    assert [x["km"] for x in s] == [1.0, 2.0, 2.5]
    assert all(abs(x["pace_s_km"] - 300) < 6 for x in s)
    assert abs(s[-1]["dist_m"] - 500) < 30
    assert s[0]["elev_gain_m"] > 0


def test_set_category(client):
    _upload(client, "garmin_run.tcx")
    assert client.get("/api/activities/1").json()["category"] == "run"
    r = client.patch("/api/activities/1/category", json={"category": "bike"})
    assert r.status_code == 200
    assert client.get("/api/activities/1").json()["category"] == "bike"
    assert client.patch("/api/activities/1/category",
                        json={"category": "swim"}).status_code == 400
    assert client.patch("/api/activities/99/category",
                        json={"category": "run"}).status_code == 404


def test_mqtt_publishes_stats_even_if_frame_render_fails(client, monkeypatch):
    """Падение рендера кадра (нет Pillow/шрифтов) не должно лишать
    Home Assistant цифр — состояние публикуется в любом случае."""
    import paho.mqtt.publish as mqtt_publish

    import frame
    import main

    _upload(client, "garmin_run.tcx")
    sent = []

    def boom(_store):
        raise RuntimeError("No module named 'PIL'")

    monkeypatch.setattr(main, "MQTT_HOST", "broker.invalid")
    monkeypatch.setattr(frame, "render_frame", boom)
    monkeypatch.setattr(mqtt_publish, "multiple", lambda msgs, **kw: sent.extend(msgs))

    main.publish_mqtt_state()

    topics = [m["topic"] for m in sent]
    assert topics == [f"{main.MQTT_PREFIX}/state"]
    assert "run" in json.loads(sent[0]["payload"])
