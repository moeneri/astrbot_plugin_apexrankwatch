from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def _resolve_shanghai_tz():
    try:
        return ZoneInfo("Asia/Shanghai")
    except Exception:
        return timezone(timedelta(hours=8), name="Asia/Shanghai")


SHANGHAI_TZ = _resolve_shanghai_tz()


def coerce_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"1", "true", "yes", "y", "on", "enabled"}:
            return True
        if text in {"0", "false", "no", "n", "off", "disabled"}:
            return False
    return default


def now_shanghai() -> datetime:
    return datetime.now(SHANGHAI_TZ)


def now_str() -> str:
    return now_shanghai().strftime("%Y/%m/%d %H:%M:%S")


def now_epoch_ms() -> int:
    return int(now_shanghai().timestamp() * 1000)
