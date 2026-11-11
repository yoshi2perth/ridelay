"""トラックポイントからテレメトリー (1Hz の等間隔データ) を生成する.

- 速度: 座標と時刻から距離を計算し、その時間微分を平滑化
- 斜度: 平滑化した標高を、前後の一定距離の区間で差分
- 欠損: power / cadence は欠損を 0、心拍は前後から補間
- 記録の途切れ (オートポーズ等): 速度・パワー・ケイデンスを 0 とする
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Sequence

import numpy as np

from .gpx_parser import TrackPoint

EARTH_RADIUS_M = 6371008.8

# 描画対象になり得るフィールド
FIELDS = ("speed", "hr", "cadence", "power", "wkg", "grade")


def haversine_m(lat1, lon1, lat2, lon2):
    lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


def moving_average(x: np.ndarray, window: float) -> np.ndarray:
    """中心移動平均 (端は端の値で延長)。window はサンプル数 (=秒)."""
    w = int(round(window))
    if w <= 1 or len(x) < 2:
        return x.astype(float).copy()
    if w % 2 == 0:
        w += 1
    pad = w // 2
    xp = np.pad(x.astype(float), pad, mode="edge")
    kernel = np.ones(w) / w
    return np.convolve(xp, kernel, mode="valid")


def _forward_fill(x: np.ndarray, initial: float = 0.0) -> np.ndarray:
    out = x.copy()
    last = initial
    for i in range(len(out)):
        if np.isfinite(out[i]):
            last = out[i]
        else:
            out[i] = last
    return out


def compute_grade(dist: np.ndarray, ele: np.ndarray, half_window_m: float = 25.0,
                  min_span_m: float = 10.0, limit: float = 30.0) -> np.ndarray:
    """距離ベースの窓で斜度 (%) を計算する。停止中など距離が稼げない区間は直前値を保持."""
    lo = np.searchsorted(dist, dist - half_window_m, side="left")
    hi = np.searchsorted(dist, dist + half_window_m, side="right") - 1
    lo = np.clip(lo, 0, len(dist) - 1)
    hi = np.clip(hi, 0, len(dist) - 1)
    span = dist[hi] - dist[lo]
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.where(span >= min_span_m, (ele[hi] - ele[lo]) / span * 100.0, np.nan)
    g = _forward_fill(g, 0.0)
    return np.clip(g, -limit, limit)


@dataclass
class Telemetry:
    start: datetime          # t=0 の UTC 時刻
    t: np.ndarray            # 秒 (1Hz グリッド)
    lat: np.ndarray
    lon: np.ndarray
    dist: np.ndarray         # 累積距離 (m)
    speed: np.ndarray        # km/h
    ele: np.ndarray          # m (無ければ NaN)
    grade: np.ndarray        # % (無ければ NaN)
    hr: np.ndarray           # bpm (無ければ NaN)
    cadence: np.ndarray      # rpm (無ければ NaN)
    power: np.ndarray        # W (無ければ NaN)
    has: Dict[str, bool] = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return float(self.t[-1])

    def _arr(self, name: str) -> np.ndarray:
        return getattr(self, name)

    def interp(self, name: str, ts):
        """滑らかな補間値 (ゲージやマップのドット用)."""
        return np.interp(ts, self.t, self._arr(name))

    def held(self, name: str, ts: float) -> float:
        """1 秒単位で保持した値 (サイコンと同じ 1Hz 更新の数値表示用)."""
        i = int(np.clip(np.floor(ts), 0, len(self.t) - 1))
        return float(self._arr(name)[i])

    def index_range(self, t0: float, t1: float) -> slice:
        i0 = int(np.clip(np.floor(t0), 0, len(self.t) - 1))
        i1 = int(np.clip(np.ceil(t1), 0, len(self.t) - 1))
        return slice(i0, i1 + 1)


def _optional_series(points: Sequence[TrackPoint], attr: str) -> np.ndarray:
    return np.array(
        [getattr(p, attr) if getattr(p, attr) is not None else np.nan for p in points],
        dtype=float,
    )


def _interp_valid(grid: np.ndarray, t_raw: np.ndarray, values: np.ndarray) -> np.ndarray:
    ok = np.isfinite(values)
    if not ok.any():
        return np.full_like(grid, np.nan)
    return np.interp(grid, t_raw[ok], values[ok])


def build_telemetry(points: List[TrackPoint], smooth_speed: float = 3.0,
                    smooth_power: float = 3.0, gap_s: float = 10.0) -> Telemetry:
    t0 = points[0].time
    t_raw = np.array([(p.time - t0).total_seconds() for p in points], dtype=float)
    lat_raw = np.array([p.lat for p in points], dtype=float)
    lon_raw = np.array([p.lon for p in points], dtype=float)

    seg = haversine_m(lat_raw[:-1], lon_raw[:-1], lat_raw[1:], lon_raw[1:])
    cum = np.concatenate([[0.0], np.cumsum(seg)])

    grid = np.arange(0.0, np.floor(t_raw[-1]) + 1.0, 1.0)
    if len(grid) < 2:
        grid = np.array([0.0, 1.0])

    dist = np.interp(grid, t_raw, cum)
    lat = np.interp(grid, t_raw, lat_raw)
    lon = np.interp(grid, t_raw, lon_raw)

    # 記録の途切れ (オートポーズ等) の区間を検出
    idx = np.clip(np.searchsorted(t_raw, grid, side="right") - 1, 0, len(t_raw) - 2)
    in_gap = (t_raw[idx + 1] - t_raw[idx]) > gap_s

    speed = np.gradient(dist, grid) * 3.6
    speed[in_gap] = 0.0
    speed = np.clip(moving_average(speed, smooth_speed), 0.0, None)

    has: Dict[str, bool] = {}

    # 心拍: 欠損は前後から補間
    hr_raw = _optional_series(points, "hr")
    has["hr"] = bool(np.isfinite(hr_raw).any())
    hr = _interp_valid(grid, t_raw, hr_raw)

    # ケイデンス・パワー: 欠損は 0 (センサーが値を送っていない=踏んでいない とみなす)
    def zero_filled(attr: str) -> Optional[np.ndarray]:
        raw = _optional_series(points, attr)
        if not np.isfinite(raw).any():
            return None
        raw = np.where(np.isfinite(raw), raw, 0.0)
        out = np.interp(grid, t_raw, raw)
        out[in_gap] = 0.0
        return out

    cad = zero_filled("cad")
    has["cadence"] = cad is not None
    if cad is None:
        cad = np.full_like(grid, np.nan)

    power = zero_filled("power")
    has["power"] = power is not None
    if power is None:
        power = np.full_like(grid, np.nan)
    else:
        power = moving_average(power, smooth_power)

    # 標高・斜度
    ele_raw = _optional_series(points, "ele")
    has["ele"] = bool(np.isfinite(ele_raw).any())
    if has["ele"]:
        ele = moving_average(_interp_valid(grid, t_raw, ele_raw), 5)
        grade = moving_average(compute_grade(dist, ele), 3)
    else:
        ele = np.full_like(grid, np.nan)
        grade = np.full_like(grid, np.nan)

    return Telemetry(
        start=t0, t=grid, lat=lat, lon=lon, dist=dist, speed=speed, ele=ele,
        grade=grade, hr=hr, cadence=cad, power=power, has=has,
    )


def available_fields(tel: Telemetry, requested: Sequence[str],
                     weight: Optional[float]) -> "tuple[list[str], list[str]]":
    """描画可能なフィールドと、その理由付きの除外メッセージを返す."""
    ok: List[str] = []
    notes: List[str] = []
    for f in requested:
        if f == "speed":
            ok.append(f)
        elif f == "hr":
            (ok.append(f) if tel.has["hr"] else notes.append("心拍データが無いため bpm は描画しません"))
        elif f == "cadence":
            (ok.append(f) if tel.has["cadence"] else notes.append("ケイデンスデータが無いため rpm は描画しません"))
        elif f == "power":
            (ok.append(f) if tel.has["power"] else notes.append("<power> タグが無いため W は描画しません"))
        elif f == "wkg":
            if not tel.has["power"]:
                notes.append("<power> タグが無いため W/kg は描画しません")
            elif not weight:
                notes.append("--weight が未指定のため W/kg は描画しません")
            else:
                ok.append(f)
        elif f == "grade":
            (ok.append(f) if tel.has["ele"] else notes.append("標高データが無いため斜度は描画しません"))
    return ok, notes
