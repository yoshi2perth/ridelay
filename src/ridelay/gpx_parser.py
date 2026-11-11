"""GPX ファイルの解析.

Garmin の TrackPointExtension (hr / cad / atemp) や、Strava 等が出力する
``<power>`` / ``PowerInWatts`` 拡張に対応する。名前空間のプレフィックスは
ファイルによって異なる (gpxtpx / ns3 など) ため、タグのローカル名で判定する。
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


class GpxError(Exception):
    """GPX の読み込み・解析に失敗した."""


@dataclass
class TrackPoint:
    time: datetime  # timezone-aware (UTC)
    lat: float
    lon: float
    ele: Optional[float] = None
    hr: Optional[float] = None
    cad: Optional[float] = None
    power: Optional[float] = None
    temp: Optional[float] = None


# 拡張タグのローカル名 (小文字) -> TrackPoint の属性名
_EXT_MAP = {
    "hr": "hr",
    "heartrate": "hr",
    "cad": "cad",
    "cadence": "cad",
    "power": "power",
    "powerinwatts": "power",
    "watts": "power",
    "atemp": "temp",
    "temp": "temp",
}

_FRACTION_RE = re.compile(r"\.(\d+)")


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_iso_time(text: str) -> datetime:
    """GPX の時刻文字列を UTC の aware datetime に変換する."""
    s = text.strip()
    if s.endswith(("Z", "z")):
        s = s[:-1] + "+00:00"
    # Python 3.9 の fromisoformat は小数秒が 3 桁 or 6 桁でないと失敗するため正規化
    s = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], s, count=1)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError as e:
        raise GpxError(f"時刻を解釈できません: {text!r}") from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _to_float(text: Optional[str]) -> Optional[float]:
    if text is None:
        return None
    try:
        return float(text.strip())
    except ValueError:
        return None


def parse_gpx(path: "str | Path") -> List[TrackPoint]:
    """GPX を読み込み、時刻順に並んだ TrackPoint のリストを返す."""
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError) as e:
        raise GpxError(f"GPX を読み込めません: {e}") from e

    points: List[TrackPoint] = []
    for el in tree.getroot().iter():
        if _local(el.tag) != "trkpt":
            continue
        lat = _to_float(el.get("lat"))
        lon = _to_float(el.get("lon"))
        if lat is None or lon is None:
            continue
        time = None
        pt_vals = {}
        for child in el:
            name = _local(child.tag)
            if name == "time" and child.text:
                time = parse_iso_time(child.text)
            elif name == "ele":
                pt_vals["ele"] = _to_float(child.text)
            elif name == "extensions":
                for ext in child.iter():
                    attr = _EXT_MAP.get(_local(ext.tag))
                    if attr and attr not in pt_vals:
                        v = _to_float(ext.text)
                        if v is not None:
                            pt_vals[attr] = v
        if time is None:
            continue
        points.append(TrackPoint(time=time, lat=lat, lon=lon, **pt_vals))

    if len(points) < 2:
        raise GpxError("時刻付きのトラックポイントが 2 点以上必要です")

    points.sort(key=lambda p: p.time)
    # 同一時刻の重複を除去 (先勝ち)
    dedup = [points[0]]
    for p in points[1:]:
        if p.time > dedup[-1].time:
            dedup.append(p)
    if len(dedup) < 2:
        raise GpxError("時刻の異なるトラックポイントが 2 点以上必要です")
    return dedup
