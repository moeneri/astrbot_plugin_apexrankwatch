from __future__ import annotations

import re
from pathlib import Path


RANK_ALIASES = (
    ("predator", ("apex predator", "predator", "猎杀", "猎杀者")),
    ("master", ("master", "大师")),
    ("diamond", ("diamond", "钻石")),
    ("platinum", ("platinum", "白金", "铂金")),
    ("gold", ("gold", "黄金")),
    ("silver", ("silver", "白银")),
    ("bronze", ("bronze", "青铜")),
    ("rookie", ("rookie", "unranked", "菜鸟", "新秀", "未定级")),
)
ROMAN_DIVISIONS = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
CN_DIVISIONS = {"一": 1, "二": 2, "三": 3, "四": 4}


def builtin_rank_icon_dir() -> Path:
    return Path(__file__).resolve().parent / "assets" / "ranks"


def rank_asset_file_for_label(rank_label: str) -> str:
    text = str(rank_label or "").strip()
    if not text:
        return ""

    lowered = text.lower()
    rank_key = ""
    for candidate, aliases in RANK_ALIASES:
        if any(alias in lowered or alias in text for alias in aliases):
            rank_key = candidate
            break
    if not rank_key:
        return ""

    if rank_key in {"master", "predator"}:
        return f"{rank_key}.png"

    division = _parse_division(text)
    if division:
        return f"{rank_key}_{division}.png"
    return f"{rank_key}.png"


def _parse_division(text: str) -> int | None:
    lowered = text.lower()

    digit_match = re.search(r"(?<!\d)([1-4])(?!\d)", lowered)
    if digit_match:
        return int(digit_match.group(1))

    roman_match = re.search(r"\b(iv|iii|ii|i)\b", lowered)
    if roman_match:
        return ROMAN_DIVISIONS.get(roman_match.group(1))

    for value, division in CN_DIVISIONS.items():
        if value in text:
            return division
    return None
