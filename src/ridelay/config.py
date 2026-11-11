"""描画設定."""

from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction
from typing import List, Optional


def parse_color(text: str) -> int:
    """'#RRGGBB' / '#RRGGBBAA' -> skia の ARGB int."""
    s = text.strip().lstrip("#")
    if len(s) == 6:
        s += "FF"
    if len(s) != 8:
        raise ValueError(f"色は #RRGGBB または #RRGGBBAA で指定してください: {text!r}")
    r, g, b, a = (int(s[i:i + 2], 16) for i in range(0, 8, 2))
    return (a << 24) | (r << 16) | (g << 8) | b


@dataclass
class RenderConfig:
    width: int = 3840
    height: int = 2160
    fps: Fraction = Fraction(30)
    start_s: float = 0.0              # GPX 開始からの秒
    end_s: float = 0.0
    fields: List[str] = field(default_factory=list)
    weight: Optional[float] = None
    scale: float = 1.0
    max_speed: float = 70.0
    show_gauge: bool = True
    show_map: bool = True
    map_scope: str = "full"           # full | segment
    map_width: float = 440.0          # 1080p 基準のピクセル
    map_height: float = 560.0
    text_color: int = 0xFFFFFFFF
    outline_color: int = 0xB4000000
    dot_color: int = 0xFF3DDC97
    map_color: int = 0xFFFF5A1F
    font_family: str = "Helvetica Neue"
    font_path: Optional[str] = None

    @property
    def unit(self) -> float:
        """1080p を基準にしたスケール係数."""
        return self.height / 1080.0 * self.scale

    @property
    def duration(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    @property
    def frame_count(self) -> int:
        return int(self.duration * self.fps)

    def frame_time(self, n: int) -> float:
        """フレーム番号 -> GPX 開始からの秒."""
        return self.start_s + float(Fraction(n) / self.fps)
