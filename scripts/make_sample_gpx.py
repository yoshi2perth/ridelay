"""動作確認用のサンプル GPX (Garmin 形式) を生成する.

縦長の周回コースを 1Hz で記録した想定。心拍・ケイデンス・パワー・標高を含む。

    python scripts/make_sample_gpx.py samples/sample.gpx --minutes 5
    python scripts/make_sample_gpx.py samples/sample_nopower.gpx --no-power
"""

from __future__ import annotations

import argparse
import math
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

LAT0, LON0 = 36.5680, 139.9930   # 宇都宮市清原付近
W_M, H_M = 180.0, 900.0          # コースの幅・高さ (m)
M_PER_DEG_LAT = 111_320.0
M_PER_DEG_LON = M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def course_xy(s: float) -> "tuple[float, float]":
    """周回距離 s (m) -> コース上の (x, y) [m]. 角を丸めた長方形."""
    r = 40.0
    straights = [H_M - 2 * r, W_M - 2 * r, H_M - 2 * r, W_M - 2 * r]
    arc = math.pi * r / 2
    lap = sum(straights) + 4 * arc
    s %= lap
    # 開始: 右辺の下 (x=W, y=r) から上向き、反時計回り
    corners = [(W_M - r, H_M - r, 0), (r, H_M - r, 90), (r, r, 180), (W_M - r, r, 270)]
    starts = [(W_M, r, (0, 1)), (W_M - r, H_M, (-1, 0)), (0, H_M - r, (0, -1)), (r, 0, (1, 0))]
    for i in range(4):
        sx, sy, (dx, dy) = starts[i]
        if s < straights[i]:
            return sx + dx * s, sy + dy * s
        s -= straights[i]
        if s < arc:
            cx, cy, a0 = corners[i]
            a = math.radians(a0 + s / r * 180 / math.pi)
            return cx + r * math.cos(a), cy + r * math.sin(a)
        s -= arc
    return W_M, r


def lap_length() -> float:
    r = 40.0
    return 2 * (H_M - 2 * r) + 2 * (W_M - 2 * r) + 2 * math.pi * r


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out", type=Path)
    ap.add_argument("--minutes", type=float, default=5.0)
    ap.add_argument("--no-power", action="store_true")
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rnd = random.Random(args.seed)

    start = datetime(2026, 11, 3, 0, 15, 0, tzinfo=timezone.utc)  # 09:15 JST
    n = int(args.minutes * 60)
    lap = lap_length()
    arc = math.pi * 40.0 / 2
    corner_pos, acc = [], 0.0
    for straight in (H_M - 80, W_M - 80, H_M - 80, W_M - 80):
        acc += straight + arc / 2
        corner_pos.append(acc)
        acc += arc / 2
    corner_pos.append(corner_pos[-1] - lap)
    s = 0.0
    v = 0.0
    hr = 100.0
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<gpx creator="Garmin Edge 840" version="1.1" xmlns="http://www.topografix.com/GPX/1/1" '
        'xmlns:ns3="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">',
        '  <metadata><time>%s</time></metadata>' % start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        '  <trk><name>Sample Criterium</name><type>road_biking</type><trkseg>',
    ]
    for i in range(n + 1):
        t = start + timedelta(seconds=i)
        pos = s % lap
        # 角の手前で減速、直線で加速 (最初の 15 秒はスタート待ち)
        near_corner = min(abs(pos - c) for c in corner_pos)
        target = 0.0 if i < 15 else (26.0 if near_corner < 60 else 44.0) / 3.6
        accel = 1.2 if target > v else -2.5
        v = max(0.0, min(target, v + accel)) if accel > 0 else max(target, v + accel)
        s += v
        x, y = course_xy(s)
        lat = LAT0 + y / M_PER_DEG_LAT + rnd.gauss(0, 0.3) / M_PER_DEG_LAT
        lon = LON0 + x / M_PER_DEG_LON + rnd.gauss(0, 0.3) / M_PER_DEG_LON
        ele = 110 + 6 * math.sin(2 * math.pi * pos / lap) + rnd.gauss(0, 0.15)
        pedaling = v > 1 and target >= v - 0.3
        power = (rnd.gauss(280, 40) if target > v + 0.5 else rnd.gauss(180, 30)) if pedaling else 0
        cad = rnd.gauss(92, 4) if pedaling else 0
        hr += ((165 if pedaling else 140) - hr) * 0.03 + rnd.gauss(0, 0.5)
        ext = "<extensions>"
        if not args.no_power:
            ext += f"<power>{max(0, power):.0f}</power>"
        ext += (f"<ns3:TrackPointExtension><ns3:atemp>16.0</ns3:atemp><ns3:hr>{hr:.0f}</ns3:hr>"
                f"<ns3:cad>{max(0, cad):.0f}</ns3:cad></ns3:TrackPointExtension></extensions>")
        lines.append(
            f'    <trkpt lat="{lat:.7f}" lon="{lon:.7f}"><ele>{ele:.1f}</ele>'
            f'<time>{t.strftime("%Y-%m-%dT%H:%M:%S.000Z")}</time>{ext}</trkpt>'
        )
    lines += ["  </trkseg></trk>", "</gpx>", ""]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("\n".join(lines))
    print(f"wrote {args.out} ({n + 1} points)")


if __name__ == "__main__":
    main()
