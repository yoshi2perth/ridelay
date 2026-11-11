"""時刻・時間指定の文字列解析."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, tzinfo
from fractions import Fraction
from typing import Optional


class TimeSpecError(ValueError):
    pass


_DUR_RE = re.compile(
    r"^(?:(?P<h>\d+(?:\.\d+)?)h)?(?:(?P<m>\d+(?:\.\d+)?)m)?(?:(?P<s>\d+(?:\.\d+)?)s)?$"
)


def parse_duration(text: str) -> float:
    """'90', '90s', '10m', '1h2m3s', '01:02:03', '12:30' -> 秒."""
    s = text.strip().lower()
    if not s:
        raise TimeSpecError("空の時間指定です")
    if ":" in s:
        parts = s.split(":")
        if len(parts) > 3:
            raise TimeSpecError(f"時間を解釈できません: {text!r}")
        try:
            nums = [float(p) for p in parts]
        except ValueError as e:
            raise TimeSpecError(f"時間を解釈できません: {text!r}") from e
        total = 0.0
        for n in nums:
            total = total * 60 + n
        return total
    try:
        return float(s)
    except ValueError:
        pass
    m = _DUR_RE.match(s)
    if not m or not any(m.groupdict().values()):
        raise TimeSpecError(f"時間を解釈できません: {text!r}")
    return sum(float(m.group(k) or 0) * mul for k, mul in (("h", 3600), ("m", 60), ("s", 1)))


def local_tz() -> tzinfo:
    return datetime.now().astimezone().tzinfo  # type: ignore[return-value]


def parse_time_spec(text: str, start_utc: datetime, tz: Optional[tzinfo] = None) -> float:
    """時刻指定を、GPX 開始からの経過秒に変換する.

    - ``2026-11-03T09:15:30``  日付付き: 絶対時刻 (タイムゾーン無しはローカル時刻)
    - ``@09:15:30``           時刻のみ: GPX 開始日のローカル時刻
    - ``00:12:30`` / ``750`` / ``12m30s``  : GPX 開始からの経過時間
    """
    tz = tz or local_tz()
    s = text.strip()
    if s.startswith("@"):
        clock = s[1:]
        parts = clock.split(":")
        try:
            nums = [float(p) for p in parts]
        except ValueError as e:
            raise TimeSpecError(f"時刻を解釈できません: {text!r}") from e
        if not 2 <= len(nums) <= 3:
            raise TimeSpecError(f"時刻は @HH:MM または @HH:MM:SS で指定してください: {text!r}")
        h, m = nums[0], nums[1]
        sec = nums[2] if len(nums) == 3 else 0.0
        start_local = start_utc.astimezone(tz)
        midnight = start_local.replace(hour=0, minute=0, second=0, microsecond=0)
        dt = midnight + timedelta(hours=h, minutes=m, seconds=sec)
        # 日付を跨ぐライドへの対応
        if dt < start_local - timedelta(hours=12):
            dt += timedelta(days=1)
        return (dt - start_local).total_seconds()
    if re.match(r"^\d{4}-\d{2}-\d{2}", s):
        from .gpx_parser import GpxError, parse_iso_time

        has_tz = bool(re.search(r"(Z|z|[+-]\d{2}:?\d{2})$", s[10:]))
        try:
            if has_tz:
                dt = parse_iso_time(s)
            else:
                dt = parse_iso_time(s + "+00:00").replace(tzinfo=tz)
        except GpxError as e:
            raise TimeSpecError(str(e)) from e
        return (dt - start_utc).total_seconds()
    return parse_duration(s)


_NTSC = {"23.976": Fraction(24000, 1001), "23.98": Fraction(24000, 1001),
         "29.97": Fraction(30000, 1001), "59.94": Fraction(60000, 1001),
         "119.88": Fraction(120000, 1001)}


def parse_fps(text: str) -> Fraction:
    s = text.strip()
    if s in _NTSC:
        return _NTSC[s]
    try:
        fps = Fraction(s).limit_denominator(1001)
    except (ValueError, ZeroDivisionError) as e:
        raise TimeSpecError(f"フレームレートを解釈できません: {text!r}") from e
    if fps <= 0:
        raise TimeSpecError("フレームレートは正の値にしてください")
    return fps


def parse_size(text: str) -> "tuple[int, int]":
    presets = {"4k": (3840, 2160), "2.7k": (2704, 1520), "1080p": (1920, 1080),
               "720p": (1280, 720), "5.3k": (5312, 2988)}
    s = text.strip().lower()
    if s in presets:
        return presets[s]
    m = re.match(r"^(\d+)x(\d+)$", s)
    if not m:
        raise TimeSpecError(f"サイズは 3840x2160 の形式か 4k/2.7k/1080p で指定してください: {text!r}")
    w, h = int(m.group(1)), int(m.group(2))
    if w % 2 or h % 2:
        raise TimeSpecError("幅・高さは偶数にしてください")
    return w, h


def format_hms(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 3600:d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"
