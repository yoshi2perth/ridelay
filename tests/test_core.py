import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from ridelay.gpx_parser import GpxError, TrackPoint, parse_gpx, parse_iso_time
from ridelay.processing import available_fields, build_telemetry, compute_grade
from ridelay.timeutil import parse_duration, parse_fps, parse_size, parse_time_spec

GPX_HEAD = ('<?xml version="1.0"?><gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
            'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1"><trk><trkseg>')
GPX_TAIL = "</trkseg></trk></gpx>"


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "t.gpx"
    p.write_text(GPX_HEAD + body + GPX_TAIL)
    return p


def test_parse_garmin_extensions(tmp_path):
    body = (
        '<trkpt lat="36.0" lon="140.0"><ele>100</ele><time>2026-11-03T00:00:00Z</time>'
        '<extensions><power>250</power><gpxtpx:TrackPointExtension><gpxtpx:hr>120</gpxtpx:hr>'
        '<gpxtpx:cad>90</gpxtpx:cad></gpxtpx:TrackPointExtension></extensions></trkpt>'
        '<trkpt lat="36.0001" lon="140.0"><ele>101</ele><time>2026-11-03T00:00:01.5Z</time></trkpt>'
    )
    pts = parse_gpx(_write(tmp_path, body))
    assert len(pts) == 2
    assert pts[0].hr == 120 and pts[0].cad == 90 and pts[0].power == 250
    assert pts[1].power is None
    assert (pts[1].time - pts[0].time).total_seconds() == pytest.approx(1.5)


def test_parse_requires_two_points(tmp_path):
    body = '<trkpt lat="36" lon="140"><time>2026-11-03T00:00:00Z</time></trkpt>'
    with pytest.raises(GpxError):
        parse_gpx(_write(tmp_path, body))


def test_parse_iso_time_variants():
    a = parse_iso_time("2026-11-03T00:00:00Z")
    b = parse_iso_time("2026-11-03T09:00:00.1+09:00")
    assert (b - a).total_seconds() == pytest.approx(0.1)


def _straight_points(n=120, speed_mps=10.0, power=None, slope=0.0):
    t0 = datetime(2026, 11, 3, tzinfo=timezone.utc)
    m_per_deg = 111_195.0
    return [
        TrackPoint(time=t0 + timedelta(seconds=i), lat=36.0 + i * speed_mps / m_per_deg,
                   lon=140.0, ele=100 + i * speed_mps * slope, hr=130,
                   power=power, cad=None)
        for i in range(n)
    ]


def test_speed_from_positions():
    tel = build_telemetry(_straight_points(speed_mps=10.0))
    assert np.median(tel.speed) == pytest.approx(36.0, rel=0.01)


def test_grade_on_constant_slope():
    tel = build_telemetry(_straight_points(slope=0.05))
    assert np.median(tel.grade) == pytest.approx(5.0, abs=0.2)


def test_compute_grade_holds_when_stopped():
    dist = np.array([0, 10, 20, 30, 30, 30, 30, 40.0])
    ele = dist * 0.1
    g = compute_grade(dist, ele, half_window_m=10, min_span_m=5)
    assert np.all(np.isfinite(g))


def test_missing_power_is_hidden():
    tel = build_telemetry(_straight_points(power=None))
    assert tel.has["power"] is False
    fields, notes = available_fields(tel, ["speed", "hr", "cadence", "power", "wkg", "grade"], 65)
    assert fields == ["speed", "hr", "grade"]
    assert any("power" in n for n in notes)


def test_wkg_requires_weight():
    tel = build_telemetry(_straight_points(power=200))
    assert available_fields(tel, ["power", "wkg"], None)[0] == ["power"]
    assert available_fields(tel, ["power", "wkg"], 65)[0] == ["power", "wkg"]


def test_gap_sets_speed_zero():
    pts = _straight_points(n=30)
    t0 = pts[0].time
    # 60 秒のオートポーズ (同じ位置で再開)
    later = [TrackPoint(time=t0 + timedelta(seconds=90 + i), lat=pts[-1].lat, lon=140.0, ele=100)
             for i in range(10)]
    tel = build_telemetry(pts + later)
    assert tel.speed[60] == pytest.approx(0.0)


def test_time_specs():
    start = datetime(2026, 11, 3, 0, 15, 0, tzinfo=timezone.utc)  # 09:15 JST
    jst = timezone(timedelta(hours=9))
    assert parse_duration("1h2m3s") == 3723
    assert parse_duration("12:30") == 750
    assert parse_duration("90") == 90
    assert parse_time_spec("00:01:00", start, jst) == 60
    assert parse_time_spec("@09:20:00", start, jst) == 300
    assert parse_time_spec("2026-11-03T09:16:00", start, jst) == 60
    assert parse_time_spec("2026-11-03T00:16:00Z", start, jst) == 60


def test_fps_and_size():
    assert parse_fps("29.97").numerator == 30000
    assert float(parse_fps("60")) == 60
    assert parse_size("4k") == (3840, 2160)
    assert parse_size("2704x1520") == (2704, 1520)


def test_metrics_text_interpolation():
    from ridelay.config import RenderConfig
    from ridelay.widgets import MetricsText, load_typeface

    # パワーが徐々に変化するデータを作成 (t=0: 100W, t=1: 200W, t=2: 300W)
    t0 = datetime(2026, 11, 3, tzinfo=timezone.utc)
    pts = [
        TrackPoint(time=t0 + timedelta(seconds=i), lat=36.0 + i * 0.0001, lon=140.0,
                   power=100.0 + i * 100.0)
        for i in range(10)
    ]
    tel = build_telemetry(pts, smooth_power=1.0)
    # t=1 で 200, t=2 で 300
    tel.power[1] = 200.0
    tel.power[2] = 300.0

    # 1. デフォルト (interpolate_metrics=False) -> held
    cfg_held = RenderConfig(weight=50.0, interpolate_metrics=False)
    metrics_held = MetricsText(cfg_held, load_typeface(cfg_held), ["power", "wkg"])
    # ts = 1.5 のとき、held は t=1 の値 (200W) を保持
    assert metrics_held.format("power", tel, 1.5) == "200"
    assert metrics_held.format("wkg", tel, 1.5) == "4.0"

    # 2. 補間あり (interpolate_metrics=True) -> interp
    cfg_interp = RenderConfig(weight=50.0, interpolate_metrics=True)
    metrics_interp = MetricsText(cfg_interp, load_typeface(cfg_interp), ["power", "wkg"])
    # ts = 1.5 のとき、線形補間で 250W
    assert metrics_interp.format("power", tel, 1.5) == "250"
    assert metrics_interp.format("wkg", tel, 1.5) == "5.0"

