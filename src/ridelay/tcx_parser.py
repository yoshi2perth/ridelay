"""TCX (Training Center XML) ファイルの解析.

Garmin や各種サイコン・トレーニングアプリが出力する TCX ファイルから
TrackPoint のリストを抽出する。
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional

from .gpx_parser import GpxError, TrackPoint, _EXT_MAP, _local, _to_float, parse_iso_time


class TcxError(GpxError):
    """TCX の読み込み・解析に失敗した."""


def parse_tcx(path: "str | Path") -> List[TrackPoint]:
    """TCX を読み込み、時刻順に並んだ TrackPoint のリストを返す."""
    try:
        tree = ET.parse(str(path))
    except (ET.ParseError, OSError) as e:
        raise TcxError(f"TCX を読み込めません: {e}") from e

    root = tree.getroot()
    if _local(root.tag) != "trainingcenterdatabase":
        raise TcxError(f"TCX 形式のルート要素 (TrainingCenterDatabase) が見つかりません: <{root.tag}>")

    points: List[TrackPoint] = []
    for el in root.iter():
        if _local(el.tag) != "trackpoint":
            continue

        time = None
        lat = None
        lon = None
        pt_vals = {}

        for child in el:
            tag = _local(child.tag)
            if tag == "time" and child.text:
                time = parse_iso_time(child.text)
            elif tag == "position":
                for pos_child in child:
                    pos_tag = _local(pos_child.tag)
                    if pos_tag == "latitudedegrees":
                        lat = _to_float(pos_child.text)
                    elif pos_tag == "longitudedegrees":
                        lon = _to_float(pos_child.text)
            elif tag == "altitudemeters":
                pt_vals["ele"] = _to_float(child.text)
            elif tag == "heartratebpm":
                for hr_child in child:
                    if _local(hr_child.tag) == "value":
                        pt_vals["hr"] = _to_float(hr_child.text)
            elif tag == "cadence":
                pt_vals["cad"] = _to_float(child.text)
            elif tag == "extensions":
                for ext in child.iter():
                    attr = _EXT_MAP.get(_local(ext.tag))
                    if attr and attr not in pt_vals:
                        v = _to_float(ext.text)
                        if v is not None:
                            pt_vals[attr] = v

        if time is None or lat is None or lon is None:
            continue

        points.append(TrackPoint(time=time, lat=lat, lon=lon, **pt_vals))

    if len(points) < 2:
        raise TcxError("位置と時刻付きのトラックポイントが 2 点以上必要です")

    points.sort(key=lambda p: p.time)
    # 同一時刻の重複を除去 (先勝ち)
    dedup = [points[0]]
    for p in points[1:]:
        if p.time > dedup[-1].time:
            dedup.append(p)
    if len(dedup) < 2:
        raise TcxError("時刻の異なるトラックポイントが 2 点以上必要です")
    return dedup
