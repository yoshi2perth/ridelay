"""アクティビティファイル (GPX / TCX) の統一ローダー."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List

from .gpx_parser import GpxError, TrackPoint, _local, parse_gpx
from .tcx_parser import TcxError, parse_tcx


class UnsupportedFormatError(GpxError):
    """対応していないファイル形式."""


def load_activity(path: "str | Path") -> List[TrackPoint]:
    """ファイル拡張子またはルート要素から判定して GPX または TCX をパースする."""
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix == ".tcx":
        return parse_tcx(p)
    elif suffix == ".gpx":
        return parse_gpx(p)

    # 拡張子から判定できない場合は XML ルート要素を検査
    try:
        # パースを軽量に行うため iterparse でルートタグのみ取得
        root_tag = None
        for _, elem in ET.iterparse(str(p), events=("start",)):
            root_tag = _local(elem.tag)
            break
    except Exception as e:
        raise GpxError(f"アクティビティファイルを解析できません: {e}") from e

    if root_tag == "trainingcenterdatabase":
        return parse_tcx(p)
    elif root_tag == "gpx":
        return parse_gpx(p)
    else:
        raise UnsupportedFormatError(
            f"未対応のファイル形式です (ルート要素: <{root_tag}>)。GPX または TCX ファイルを指定してください。"
        )
