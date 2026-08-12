from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo
from io import BytesIO
from math import ceil
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

try:
    from .score_report_rank_assets import (
        builtin_rank_icon_dir,
        rank_asset_file_for_label as _rank_asset_file_for_label,
    )
except ImportError:
    from score_report_rank_assets import (
        builtin_rank_icon_dir,
        rank_asset_file_for_label as _rank_asset_file_for_label,
    )


_logger = logging.getLogger("apexrankwatch.image_report")

_RANGE_PRESETS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
    "365d": timedelta(days=365),
    "all": None,
    "season": None,
    "today": None,
    "yesterday": None,
    "week": None,
    "month": None,
}
_CALENDAR_RANGE_DELTAS = {
    "today": timedelta(days=1),
    "yesterday": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=31),
}
_BEIJING_TZ = timezone(timedelta(hours=8))


def resolve_timezone(timezone_name: str) -> tzinfo:
    try:
        return ZoneInfo(timezone_name or "Asia/Shanghai")
    except Exception:
        return _BEIJING_TZ


def range_to_delta(range_key: str) -> timedelta | None:
    normalized = str(range_key or "7d").strip().lower()
    if normalized in _CALENDAR_RANGE_DELTAS:
        return _CALENDAR_RANGE_DELTAS[normalized]
    return _RANGE_PRESETS.get(normalized, _RANGE_PRESETS["7d"])


def parse_iso8601(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def utc_now() -> datetime:
    return datetime.now(timezone.utc)



@dataclass(frozen=True)
class Fonts:
    title: ImageFont.FreeTypeFont | ImageFont.ImageFont
    heading: ImageFont.FreeTypeFont | ImageFont.ImageFont
    subheading: ImageFont.FreeTypeFont | ImageFont.ImageFont
    body: ImageFont.FreeTypeFont | ImageFont.ImageFont
    small: ImageFont.FreeTypeFont | ImageFont.ImageFont
    tiny: ImageFont.FreeTypeFont | ImageFont.ImageFont


PALETTE = [
    "#7DD3FC",
    "#FBBF24",
    "#34D399",
    "#F472B6",
    "#C084FC",
    "#FB7185",
    "#2DD4BF",
    "#F97316",
]

BG_TOP = "#08121E"
BG_BOTTOM = "#0A1523"
PANEL_BG = "#0D1A2A"
PANEL_BG_SOFT = "#102135"
PANEL_BORDER = "#24435E"
TEXT = "#EDF5FF"
MUTED = "#8EA8C2"
MUTED_SOFT = "#B9C8D8"
GRID = "#21364A"
ACCENT = "#7DD3FC"
SUCCESS = "#86EFAC"
DANGER = "#FDA4AF"
ICON_MARK = "#08121E"
SEASON_ACCENT = "#F97316"
SEASON_LABEL_BG = "#3A190E"
RESET_TEXT = "#FED7AA"
ROW_POSITIVE_BG = "#0D2A24"
ROW_NEGATIVE_BG = "#2A151C"
ROW_NEUTRAL_BG = "#101F30"
MAX_COMPACT_IMAGE_HEIGHT = 8192
MAX_FULL_IMAGE_HEIGHT = 24000
CHART_PANEL_TITLE = "APEX分数/段位变化图"
RANK_PROGRESS_THRESHOLDS = [
    ("新秀 4", 0),
    ("新秀 3", 250),
    ("新秀 2", 500),
    ("新秀 1", 750),
    ("青铜 4", 1000),
    ("青铜 3", 1500),
    ("青铜 2", 2000),
    ("青铜 1", 2500),
    ("白银 4", 3250),
    ("白银 3", 3750),
    ("白银 2", 4250),
    ("白银 1", 4750),
    ("黄金 4", 5500),
    ("黄金 3", 6250),
    ("黄金 2", 7000),
    ("黄金 1", 7750),
    ("白金 4", 8500),
    ("白金 3", 9250),
    ("白金 2", 10000),
    ("白金 1", 11000),
    ("钻石 4", 12000),
    ("钻石 3", 13000),
    ("钻石 2", 14000),
    ("钻石 1", 15000),
    ("大师", 16000),
]
FRESHNESS_WARNING_MINUTES = 15


def _data_freshness_status(
    summary: dict | None,
    *,
    now: datetime | None = None,
    warning_minutes: int = FRESHNESS_WARNING_MINUTES,
) -> tuple[str, str]:
    if not summary:
        return "暂无采集时间", MUTED
    captured_at = parse_iso8601(str(summary.get("latest_captured_at") or ""))
    if captured_at is None:
        return "暂无采集时间", MUTED
    reference = now or utc_now()
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=captured_at.tzinfo)
    elapsed_minutes = max(0, int((reference - captured_at).total_seconds() // 60))
    if elapsed_minutes <= 2:
        return "刚刚采集", SUCCESS
    if elapsed_minutes >= max(1, int(warning_minutes)):
        return f"数据已延迟 {elapsed_minutes} 分钟", "#FDBA74"
    return f"{elapsed_minutes} 分钟前采集", MUTED_SOFT


def _font_candidates(bold: bool) -> list[str]:
    if bold:
        return [
            r"C:\Windows\Fonts\msyhbd.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansSC-Bold.otf",
            "/System/Library/Fonts/PingFang.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ]
    return [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
        "/System/Library/Fonts/PingFang.ttc",
        "/Library/Fonts/Arial Unicode.ttf",
    ]


_FONT_CACHE: dict[tuple[int, bool], "ImageFont.FreeTypeFont | ImageFont.ImageFont"] = {}
_FONT_OVERRIDES: dict[bool, str] = {}
_FONT_FALLBACK_WARNED = False


def configure_font_paths(regular: str | Path | None, bold: str | Path | None = None) -> None:
    """Configure plugin-managed CJK fonts without changing report layout."""
    overrides = {
        False: str(regular) if regular else "",
        True: str(bold or regular) if bold or regular else "",
    }
    normalized = {key: value for key, value in overrides.items() if value}
    if normalized == _FONT_OVERRIDES:
        return
    _FONT_OVERRIDES.clear()
    _FONT_OVERRIDES.update(normalized)
    _FONT_CACHE.clear()


def _load_font(size: int, bold: bool = False):
    cache_key = (int(size), bool(bold))
    cached = _FONT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    candidates = [_FONT_OVERRIDES.get(bool(bold), ""), *_font_candidates(bold)]
    for path in candidates:
        if not path:
            continue
        if Path(path).exists():
            try:
                font = ImageFont.truetype(path, size=size)
                _FONT_CACHE[cache_key] = font
                return font
            except Exception:
                continue
    global _FONT_FALLBACK_WARNED
    if not _FONT_FALLBACK_WARNED:
        _FONT_FALLBACK_WARNED = True
        _logger.warning(
            "未找到任何候选中文字体，已回退到 PIL 默认点阵字体，中文会渲染为方框；"
            "请安装微软雅黑或 Noto Sans CJK 等中文字体。"
        )
    fallback = ImageFont.load_default()
    _FONT_CACHE[cache_key] = fallback
    return fallback


def _build_fonts(scale: float) -> Fonts:
    return Fonts(
        title=_load_font(max(30, int(36 * scale)), bold=True),
        heading=_load_font(max(22, int(26 * scale)), bold=True),
        subheading=_load_font(max(17, int(20 * scale)), bold=True),
        body=_load_font(max(15, int(17 * scale))),
        small=_load_font(max(13, int(15 * scale))),
        tiny=_load_font(max(12, int(13 * scale))),
    )


def _text_size(draw: ImageDraw.ImageDraw, text: str, font) -> tuple[int, int]:
    if not text:
        return 0, 0
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    return right - left, bottom - top


def _wrap_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
    max_lines: int | None = None,
) -> list[str]:
    if not text:
        return []

    lines: list[str] = []
    current = ""

    for index, char in enumerate(text):
        candidate = current + char
        candidate_width, _ = _text_size(draw, candidate, font)
        if candidate_width <= max_width or not current:
            current = candidate
            continue

        lines.append(current)
        current = char

        if max_lines and len(lines) >= max_lines - 1:
            remaining = text[index:]
            while remaining and _text_size(draw, remaining + "…", font)[0] > max_width:
                remaining = remaining[:-1]
            lines.append((remaining or char) + "…")
            return lines

    if current:
        lines.append(current)

    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        last = lines[-1]
        while last and _text_size(draw, last + "…", font)[0] > max_width:
            last = last[:-1]
        lines[-1] = (last or "") + "…"

    return lines


def _draw_multiline(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    lines: list[str],
    font,
    fill: str,
    line_gap: int,
) -> None:
    current_y = y
    for line in lines:
        draw.text((x, current_y), line, font=font, fill=fill)
        current_y += _text_size(draw, line, font)[1] + line_gap


def _chip_width(draw: ImageDraw.ImageDraw, text: str, font, padding_x: int) -> int:
    text_width, _ = _text_size(draw, text, font)
    return text_width + padding_x * 2 + 18


def _draw_chip(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    font,
    color: str,
    fill: str,
    outline: str,
    height: int,
) -> int:
    width = _chip_width(draw, text, font, 12)
    draw.rounded_rectangle((x, y, x + width, y + height), radius=height // 2, fill=fill, outline=outline)
    dot_y = y + height // 2
    draw.ellipse((x + 10, dot_y - 4, x + 18, dot_y + 4), fill=color)
    draw.text((x + 24, y + (height - _text_size(draw, text, font)[1]) // 2 - 1), text, font=font, fill=TEXT)
    return width


def _format_axis_time(value: datetime, range_key: str, bucket: str) -> str:
    if bucket == "hour":
        return value.strftime("%m-%d %H:%M")
    if bucket == "month":
        return value.strftime("%Y-%m")
    if range_key == "24h":
        return value.strftime("%H:%M")
    if range_key == "season":
        return value.strftime("%m-%d")
    return value.strftime("%m-%d")


def _normalize_chart_x_axis(value: str) -> str:
    text = str(value or "time").strip().lower()
    if text in {
        "count",
        "counts",
        "event",
        "events",
        "match",
        "matches",
        "index",
        "次数",
        "比赛次数",
        "分数变化次数",
        "分数变动次数",
    }:
        return "count"
    return "time"


def _count_axis_tick_labels(point_count: int) -> list[tuple[int, str]]:
    count = max(0, int(point_count or 0))
    if count <= 0:
        return []
    if count == 1:
        return [(0, "第1次")]

    tick_count = min(6, count)
    labels: list[tuple[int, str]] = []
    seen: set[int] = set()
    for tick_index in range(tick_count):
        point_index = round(tick_index * (count - 1) / (tick_count - 1))
        if point_index in seen:
            continue
        seen.add(point_index)
        labels.append((point_index, f"第{point_index + 1}次"))
    return labels


def _score_color(delta: int) -> str:
    if delta > 0:
        return SUCCESS
    if delta < 0:
        return DANGER
    return MUTED_SOFT


def _format_active_duration(hours: float | int | str | None) -> str:
    try:
        value = float(hours or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return ""
    if value < 1:
        return f"约 {max(1, round(value * 60))} 分钟"
    if value.is_integer():
        return f"约 {int(value)} 小时"
    return f"约 {value:g} 小时"


def _summary_active_delta_metric(summary: dict) -> tuple[str, str, str, str]:
    has_active_delta = "active_delta_score" in summary
    delta_raw = summary.get("active_delta_score") if has_active_delta else summary.get("delta_score")
    delta_score = int(delta_raw or 0)
    label = "最近14小时净变化" if has_active_delta else "区间净变化"
    event_raw = summary.get("active_change_events") if has_active_delta else summary.get("change_events")
    event_count = int(event_raw or 0)
    subtext_parts = [f"{event_count} 次变动"]
    duration_text = _format_active_duration(summary.get("active_duration_hours")) if has_active_delta else ""
    if duration_text:
        subtext_parts.append(duration_text)
    return (
        label,
        f"{delta_score:+d}" if delta_score else "0",
        _score_color(delta_score),
        " / ".join(subtext_parts),
    )


def _summary_recent_event_metric(summary: dict) -> tuple[str, str, str, str]:
    has_recent_event_delta = "recent_event_delta_score" in summary
    delta_raw = (
        summary.get("recent_event_delta_score")
        if has_recent_event_delta
        else summary.get("current_season_delta_score")
    )
    delta_score = int(delta_raw or 0)
    event_count = int(summary.get("recent_event_count") or summary.get("change_events") or 0)
    label = "最近场次分数变化" if has_recent_event_delta else "当前赛季"
    subtext = f"最近 {event_count} 次合计" if has_recent_event_delta else "赛季累计"
    return (
        label,
        f"{delta_score:+d}" if delta_score else "0",
        _score_color(delta_score),
        subtext,
    )


def _event_kind(event: dict) -> str:
    if event.get("season_reset"):
        return "reset"
    if event.get("rank_changed"):
        direction = _rank_change_direction(
            str(event.get("from_rank_label") or ""),
            str(event.get("to_rank_label") or ""),
        )
        if direction == "升段":
            return "up"
        if direction == "掉段":
            return "down"
        return "rank"
    delta = int(event.get("score_delta") or 0)
    if delta > 0:
        return "up"
    if delta < 0:
        return "down"
    if event.get("rank_changed"):
        return "rank"
    return "same"


def _event_kind_label(event: dict) -> str:
    if event.get("rank_changed") and not event.get("season_reset"):
        direction = _rank_change_direction(
            str(event.get("from_rank_label") or ""),
            str(event.get("to_rank_label") or ""),
        )
        return "段位" if direction == "段位变化" else direction
    kind = _event_kind(event)
    if kind == "reset":
        return "重置"
    if kind == "up":
        return "上分"
    if kind == "down":
        return "掉分"
    if kind == "rank":
        return "段位"
    return "持平"


def _event_kind_color(event: dict) -> str:
    if event.get("rank_changed") and not event.get("season_reset"):
        direction = _rank_change_direction(
            str(event.get("from_rank_label") or ""),
            str(event.get("to_rank_label") or ""),
        )
        if direction == "升段":
            return SUCCESS
        if direction == "掉段":
            return DANGER
        return ACCENT
    kind = _event_kind(event)
    if kind == "reset":
        return SEASON_ACCENT
    if kind == "up":
        return SUCCESS
    if kind == "down":
        return DANGER
    if kind == "rank":
        return ACCENT
    return MUTED_SOFT


def _normalize_rank_progress_label(rank_label: str) -> str:
    text = str(rank_label or "").strip()
    lowered = text.lower()
    rank_name = ""
    for canonical, aliases in (
        ("猎杀", ("apex predator", "predator", "猎杀", "猎杀者")),
        ("大师", ("master", "大师")),
        ("钻石", ("diamond", "钻石")),
        ("白金", ("platinum", "白金", "铂金")),
        ("黄金", ("gold", "黄金")),
        ("白银", ("silver", "白银")),
        ("青铜", ("bronze", "青铜")),
        ("新秀", ("rookie", "unranked", "菜鸟", "新秀", "未定级")),
    ):
        if any(alias in lowered or alias in text for alias in aliases):
            rank_name = canonical
            break
    if not rank_name:
        return ""
    if rank_name in {"大师", "猎杀"}:
        return rank_name

    division = None
    digit_match = None
    for char in text:
        if char in "1234":
            digit_match = char
            break
    if digit_match:
        division = int(digit_match)
    else:
        lowered_parts = lowered.replace("ⅳ", "iv").replace("ⅲ", "iii").replace("Ⅱ".lower(), "ii")
        for token, value in (("iv", 4), ("iii", 3), ("ii", 2), ("i", 1)):
            if token in lowered_parts.split() or lowered_parts.endswith(token):
                division = value
                break
        if division is None:
            for token, value in (("四", 4), ("三", 3), ("二", 2), ("一", 1)):
                if token in text:
                    division = value
                    break
    if division is None:
        division = 4
    return f"{rank_name} {division}"


def _rank_progress(rank_label: str, rank_score: int) -> dict | None:
    normalized_label = _normalize_rank_progress_label(rank_label)
    if not normalized_label:
        return None
    if normalized_label == "猎杀":
        normalized_label = "大师"

    labels = [label for label, _ in RANK_PROGRESS_THRESHOLDS]
    if normalized_label not in labels:
        return None

    score = int(rank_score or 0)
    index = labels.index(normalized_label)
    floor_score = RANK_PROGRESS_THRESHOLDS[index][1]
    next_threshold = (
        RANK_PROGRESS_THRESHOLDS[index + 1][1]
        if index + 1 < len(RANK_PROGRESS_THRESHOLDS)
        else None
    )
    promotion_distance = None if next_threshold is None else max(0, next_threshold - score)
    demotion_distance = max(0, score - floor_score)
    if promotion_distance is None:
        text = f"猎杀排名竞争 / 距掉段 {demotion_distance} RP"
    else:
        text = f"距升段 {promotion_distance} RP / 距掉段 {demotion_distance} RP"
    progress_fraction = 1.0
    if next_threshold is not None and next_threshold > floor_score:
        progress_fraction = max(
            0.0,
            min((score - floor_score) / (next_threshold - floor_score), 1.0),
        )
    return {
        "rank_label": normalized_label,
        "floor_score": floor_score,
        "next_threshold": next_threshold,
        "next_rank_label": labels[index + 1] if index + 1 < len(labels) else "猎杀",
        "promotion_distance": promotion_distance,
        "demotion_distance": demotion_distance,
        "progress_fraction": progress_fraction,
        "text": text,
    }


def _hex_to_rgba(color: str, alpha: int) -> tuple[int, int, int, int]:
    value = str(color or "").strip().lstrip("#")
    if len(value) != 6:
        return (255, 255, 255, max(0, min(int(alpha), 255)))
    return (
        int(value[0:2], 16),
        int(value[2:4], 16),
        int(value[4:6], 16),
        max(0, min(int(alpha), 255)),
    )


def _draw_glow_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[int, int]],
    color: str,
    scale: float,
    *,
    base_width: int,
) -> None:
    if len(points) < 2:
        return
    outer_width = max(base_width + 5, int(base_width * 2.6))
    middle_width = max(base_width + 2, int(base_width * 1.7))
    draw.line(points, fill=_hex_to_rgba(color, 52), width=outer_width)
    draw.line(points, fill=_hex_to_rgba(color, 116), width=middle_width)
    draw.line(points, fill=color, width=max(2, int(base_width)))


def _draw_line_area_fill(
    canvas: Image.Image,
    points: list[tuple[int, int]],
    plot_bottom: int,
    color: str,
) -> None:
    if len(points) < 2:
        return
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    polygon = [*points, (points[-1][0], plot_bottom), (points[0][0], plot_bottom)]
    overlay_draw.polygon(polygon, fill=_hex_to_rgba(color, 28))
    canvas.alpha_composite(overlay)


def _draw_chart_value_tag(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    text: str,
    font,
    plot_rect: tuple[int, int, int, int],
    color: str,
    place_below: bool = False,
    occupied_boxes: list[tuple[int, int, int, int]] | None = None,
) -> tuple[int, int, int, int]:
    text_w, text_h = _text_size(draw, text, font)
    pad_x = 8
    pad_y = 5
    tag_w = text_w + pad_x * 2
    tag_h = text_h + pad_y * 2
    tag_x, tag_y = _chart_tag_position(
        x=x,
        y=y,
        tag_w=tag_w,
        tag_h=tag_h,
        plot_rect=plot_rect,
        place_below=place_below,
        occupied_boxes=occupied_boxes or [],
    )
    tag_box = (tag_x, tag_y, tag_x + tag_w, tag_y + tag_h)
    draw.rounded_rectangle(
        tag_box,
        radius=8,
        fill="#10263A",
        outline=_hex_to_rgba(color, 175),
    )
    draw.text((tag_x + pad_x, tag_y + pad_y - 1), text, font=font, fill=TEXT)
    if occupied_boxes is not None:
        occupied_boxes.append(tag_box)
    return tag_box


def _chart_tag_position(
    *,
    x: int,
    y: int,
    tag_w: int,
    tag_h: int,
    plot_rect: tuple[int, int, int, int],
    place_below: bool,
    occupied_boxes: list[tuple[int, int, int, int]],
) -> tuple[int, int]:
    plot_x, plot_y, plot_w, plot_h = plot_rect
    min_x = plot_x + 4
    max_x = plot_x + plot_w - tag_w - 4
    min_y = plot_y + 4
    max_y = plot_y + plot_h - tag_h - 4
    centered_x = max(min_x, min(x - tag_w // 2, max_x))
    above_y = max(min_y, min(y - tag_h - 15, max_y))
    below_y = max(min_y, min(y + 15, max_y))
    vertical_candidates = [below_y, above_y] if place_below else [above_y, below_y]
    horizontal_candidates = [
        centered_x,
        max(min_x, min(centered_x + tag_w // 2 + 8, max_x)),
        max(min_x, min(centered_x - tag_w // 2 - 8, max_x)),
    ]

    for tag_y in vertical_candidates:
        for tag_x in horizontal_candidates:
            candidate = (tag_x, tag_y, tag_x + tag_w, tag_y + tag_h)
            if not any(_boxes_overlap(candidate, occupied, padding=5) for occupied in occupied_boxes):
                return tag_x, tag_y
    return centered_x, vertical_candidates[0]


def _boxes_overlap(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
    *,
    padding: int = 0,
) -> bool:
    return not (
        first[2] + padding <= second[0]
        or second[2] + padding <= first[0]
        or first[3] + padding <= second[1]
        or second[3] + padding <= first[1]
    )


def _important_chart_point_indices(points: list[tuple[int, int]]) -> set[int]:
    if not points:
        return set()
    min_index = max(range(len(points)), key=lambda index: points[index][1])
    max_index = min(range(len(points)), key=lambda index: points[index][1])
    return {0, len(points) - 1, min_index, max_index}


def _draw_vertical_glow_line(
    draw: ImageDraw.ImageDraw,
    x: int,
    y1: int,
    y2: int,
    color: str,
    *,
    width: int,
) -> None:
    draw.line((x, y1, x, y2), fill=_hex_to_rgba(color, 42), width=max(width + 8, 10))
    draw.line((x, y1, x, y2), fill=_hex_to_rgba(color, 120), width=max(width + 3, 5))
    draw.line((x, y1, x, y2), fill=color, width=max(width, 2))


def _draw_highlight_dot(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    color: str,
    scale: float,
) -> None:
    glow_r = max(8, int(12 * scale))
    core_r = max(4, int(6 * scale))
    draw.ellipse(
        (x - glow_r, y - glow_r, x + glow_r, y + glow_r),
        fill=_hex_to_rgba(color, 70),
    )
    draw.ellipse(
        (x - core_r, y - core_r, x + core_r, y + core_r),
        fill=color,
        outline="#FFFFFF",
        width=1,
    )


def _draw_timeline_change_dot(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    color: str,
    scale: float,
    *,
    emphasized: bool = False,
) -> None:
    glow_r = max(5, int((9 if emphasized else 7) * scale))
    core_r = max(3, int((6 if emphasized else 5) * scale))
    draw.ellipse(
        (x - glow_r, y - glow_r, x + glow_r, y + glow_r),
        fill=_hex_to_rgba(color, 76 if emphasized else 58),
    )
    draw.ellipse(
        (x - core_r, y - core_r, x + core_r, y + core_r),
        fill=color,
    )


def _draw_event_icon(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    size: int,
    event: dict,
    *,
    glow: bool = False,
) -> None:
    color = _event_kind_color(event)
    kind = _event_kind(event)
    if glow:
        glow_r = max(size // 2 + 5, size)
        draw.ellipse(
            (x - glow_r, y - glow_r, x + glow_r, y + glow_r),
            fill=_hex_to_rgba(color, 50),
        )
    box = (x - size // 2, y - size // 2, x + size // 2, y + size // 2)
    if kind == "reset":
        outer = [
            (x, y - size // 2),
            (x + size // 2, y),
            (x, y + size // 2),
            (x - size // 2, y),
        ]
        draw.polygon(outer, fill=_hex_to_rgba(color, 230))
    else:
        draw.ellipse(
            box,
            fill=_hex_to_rgba(color, 225),
            outline=_hex_to_rgba(color, 180),
            width=1,
        )

    inner = max(5, int(size * 0.34))
    if kind == "up":
        points = [
            (x, y - inner),
            (x - inner, y + inner - 1),
            (x + inner, y + inner - 1),
        ]
        draw.polygon(points, fill=ICON_MARK)
    elif kind == "down":
        points = [
            (x - inner, y - inner + 1),
            (x + inner, y - inner + 1),
            (x, y + inner),
        ]
        draw.polygon(points, fill=ICON_MARK)
    elif kind == "reset":
        diamond = [
            (x, y - inner),
            (x + inner, y),
            (x, y + inner),
            (x - inner, y),
        ]
        draw.polygon(diamond, fill=ICON_MARK)
        dot_r = max(2, int(size * 0.08))
        draw.ellipse((x - dot_r, y - dot_r, x + dot_r, y + dot_r), fill=RESET_TEXT)
    elif kind == "rank":
        draw.line((x - inner, y + inner, x, y - inner, x + inner, y + inner), fill=ICON_MARK, width=max(2, size // 8))
        draw.line((x - inner // 2, y + inner // 2, x, y - inner // 2, x + inner // 2, y + inner // 2), fill=ICON_MARK, width=max(2, size // 10))
    else:
        bar_h = max(3, size // 7)
        draw.rounded_rectangle(
            (x - inner, y - bar_h // 2, x + inner, y + bar_h // 2),
            radius=bar_h,
            fill=ICON_MARK,
        )

def _trim_rank_icon_padding(icon: Image.Image) -> Image.Image:
    alpha_box = icon.getchannel("A").getbbox()
    if not alpha_box:
        return icon

    cropped = icon.crop(alpha_box)
    crop_w, crop_h = cropped.size
    side = max(crop_w, crop_h)
    if side <= 0:
        return icon

    # 段位素材通常有透明留白，先补成正方形再缩放，避免小图标被压得过小。
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.alpha_composite(cropped, ((side - crop_w) // 2, (side - crop_h) // 2))
    return square


def _paste_rank_icon(canvas: Image.Image, icon_path: Path, x: int, y: int, size: int) -> None:
    if not icon_path.exists():
        return
    try:
        icon = Image.open(icon_path).convert("RGBA")
        icon = _trim_rank_icon_padding(icon)
        icon = icon.resize((size, size), Image.Resampling.LANCZOS)
        canvas.alpha_composite(icon, (x, y))
    except Exception:
        return


def _resolve_rank_icon_path(
    icon_dir: Path,
    icon_name: str,
    rank_label: str,
    rank_asset_file: str = "",
) -> Path | None:
    normalized_name = Path(str(icon_name or "").strip()).name
    if normalized_name:
        cached_path = icon_dir / normalized_name
        if cached_path.exists():
            return cached_path

    builtin_dir = builtin_rank_icon_dir()
    for candidate in (
        Path(str(rank_asset_file or "").strip()).name,
        _rank_asset_file_for_label(rank_label),
    ):
        if not candidate:
            continue
        builtin_path = builtin_dir / candidate
        if builtin_path.exists():
            return builtin_path
    return None


def _fit_text_to_width(
    draw: ImageDraw.ImageDraw,
    text: str,
    font,
    max_width: int,
) -> str:
    value = str(text or "").strip()
    if not value or _text_size(draw, value, font)[0] <= max_width:
        return value

    suffix = "..."
    trimmed = value
    while trimmed and _text_size(draw, trimmed + suffix, font)[0] > max_width:
        trimmed = trimmed[:-1]
    return (trimmed or value[:1]) + suffix


def _draw_panel(draw: ImageDraw.ImageDraw, rect: tuple[int, int, int, int]) -> None:
    x, y, w, h = rect
    draw.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=22,
        fill=PANEL_BG,
        outline=PANEL_BORDER,
        width=1,
    )


def _parse_points(payload: dict, timezone_name: str, x_axis: str = "time") -> tuple[list[datetime], list[int]]:
    tz = resolve_timezone(timezone_name)
    times: list[datetime] = []
    scores: list[int] = []
    for series in payload.get("series", []):
        axis_points = []
        if _normalize_chart_x_axis(x_axis) == "time" and series.get("points"):
            axis_points.extend(series.get("points", []))
            axis_points.extend(series.get("change_points", []))
        elif series.get("line_points") or series.get("change_points"):
            axis_points.extend(series.get("line_points", []))
            axis_points.extend(series.get("change_points", []))
        else:
            axis_points.extend(series.get("points", []))
        for point in axis_points:
            dt = parse_iso8601(point.get("captured_at"))
            if dt is None:
                continue
            times.append(dt.astimezone(tz))
            scores.append(int(point.get("rank_score", 0)))
    return times, scores


def _chart_line_source(series: dict, x_axis: str = "time") -> list[dict]:
    if _normalize_chart_x_axis(x_axis) == "time":
        points = list(series.get("points", []))
        if len(points) >= 2:
            return points
    line_points = list(series.get("line_points", []))
    if len(line_points) >= 2:
        return line_points
    return list(series.get("points", []))


def _event_id_set(events: list[dict]) -> set[int]:
    result: set[int] = set()
    for event in events:
        try:
            result.add(int(event.get("id")))
        except (TypeError, ValueError):
            continue
    return result


def _line_point_is_visible(point: dict, visible_event_ids: set[int]) -> bool:
    event_ids = point.get("event_ids", [])
    if not isinstance(event_ids, list):
        event_ids = [event_ids]
    for event_id in event_ids:
        try:
            if int(event_id) in visible_event_ids:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _trim_payload_for_event_limit(payload: dict, event_limit: int) -> dict:
    result = dict(payload)
    events = list(result.get("change_events", []))[:event_limit]
    result["event_limit"] = event_limit
    result["change_events"] = events

    visible_event_ids = _event_id_set(events)
    if not visible_event_ids:
        return result

    trimmed_series = []
    for series in result.get("series", []):
        item = dict(series)
        item["change_points"] = [
            point
            for point in item.get("change_points", [])
            if int(point.get("event_id") or 0) in visible_event_ids
        ]
        item["line_points"] = [
            point
            for point in item.get("line_points", [])
            if _line_point_is_visible(point, visible_event_ids)
        ]
        trimmed_series.append(item)
    result["series"] = trimmed_series
    return result


def _draw_season_markers(
    draw: ImageDraw.ImageDraw,
    plot_rect: tuple[int, int, int, int],
    payload: dict,
    x_min: datetime,
    total_seconds: float,
    fonts: Fonts,
    scale: float,
) -> None:
    plot_x, plot_y, plot_w, plot_h = plot_rect
    markers = payload.get("season_resets", [])
    for index, marker in enumerate(markers):
        marker_dt = parse_iso8601(marker.get("captured_at"))
        if marker_dt is None:
            continue
        seconds = (marker_dt - x_min).total_seconds()
        if seconds < 0 or seconds > total_seconds:
            continue

        px = int(plot_x + (seconds / total_seconds) * plot_w)
        _draw_vertical_glow_line(
            draw,
            px,
            plot_y,
            plot_y + plot_h,
            SEASON_ACCENT,
            width=max(2, int(3 * scale)),
        )

        label = "新赛季重置"
        label_w, label_h = _text_size(draw, label, fonts.tiny)
        tag_w = label_w + int(14 * scale)
        tag_h = label_h + int(8 * scale)
        label_x = max(plot_x + 6, min(px - tag_w // 2, plot_x + plot_w - tag_w - 6))
        label_y = plot_y + int(8 * scale) + (index % 2) * int(26 * scale)
        draw.rounded_rectangle(
            (label_x, label_y, label_x + tag_w, label_y + tag_h),
            radius=10,
            fill=SEASON_LABEL_BG,
            outline=SEASON_ACCENT,
        )
        draw.text(
            (label_x + int(7 * scale), label_y + int(3 * scale) - 1),
            label,
            font=fonts.tiny,
            fill="#FFE4CC",
        )


def _draw_chart_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    payload: dict,
    icon_dir: Path,
    timezone_name: str,
    range_key: str,
    bucket: str,
    fonts: Fonts,
    scale: float,
    x_axis: str,
) -> None:
    x, y, w, h = rect
    _draw_panel(draw, rect)
    x_axis = _normalize_chart_x_axis(x_axis)
    axis_label = "分数变化次数" if x_axis == "count" else "时间"

    inner_x = x + int(24 * scale)
    inner_y = y + int(20 * scale)
    summaries = list(payload.get("summaries", []))
    single_summary = summaries[0] if len(summaries) == 1 else None
    single_series = payload.get("series", [])[0] if len(payload.get("series", [])) == 1 else None
    player_name = str(
        (single_summary or {}).get("display_name")
        or (single_series or {}).get("display_name")
        or ""
    ).strip()
    title = f"{player_name} · 排位走势" if player_name else CHART_PANEL_TITLE
    title_x = inner_x
    title_right_limit = x + w - int(24 * scale)

    if single_summary:
        rank_label = str(single_summary.get("latest_rank_label") or "暂无段位")
        latest_score = int(single_summary.get("latest_rank_score") or 0)
        delta_value = int(
            single_summary.get("recent_event_delta_score")
            if "recent_event_delta_score" in single_summary
            else single_summary.get("delta_score")
            or 0
        )
        recent_count = int(
            single_summary.get("recent_event_count")
            or single_summary.get("change_events")
            or 0
        )
        rank_icon_path = _resolve_rank_icon_path(
            icon_dir,
            str(single_summary.get("latest_rank_icon_file") or ""),
            rank_label,
            str(single_summary.get("rank_asset_file") or ""),
        )
        if rank_icon_path:
            rank_icon_size = max(48, int(58 * scale))
            _paste_rank_icon(
                canvas,
                rank_icon_path,
                inner_x,
                inner_y - int(2 * scale),
                rank_icon_size,
            )
            title_x += rank_icon_size + int(14 * scale)

        score_text = f"{latest_score:,} RP"
        score_w, score_h = _text_size(draw, score_text, fonts.title)
        score_x = x + w - int(24 * scale) - score_w
        draw.text((score_x, inner_y - int(4 * scale)), score_text, font=fonts.title, fill=TEXT)
        delta_text = f"最近 {recent_count} 次  {delta_value:+d} RP"
        delta_w, _ = _text_size(draw, delta_text, fonts.small)
        draw.text(
            (
                x + w - int(24 * scale) - delta_w,
                inner_y + score_h + int(3 * scale),
            ),
            delta_text,
            font=fonts.small,
            fill=_score_color(delta_value),
        )
        freshness_text, freshness_color = _data_freshness_status(single_summary)
        freshness_w, _ = _text_size(draw, freshness_text, fonts.tiny)
        draw.text(
            (
                x + w - int(24 * scale) - freshness_w,
                inner_y + score_h + int(24 * scale),
            ),
            freshness_text,
            font=fonts.tiny,
            fill=freshness_color,
        )
        title_right_limit = score_x - int(28 * scale)
        title = _fit_text_to_width(
            draw,
            title,
            fonts.heading,
            max(120, title_right_limit - title_x),
        )
        meta_text = (
            f"{rank_label}    {payload['range_label']}    "
            f"横轴  {axis_label}    "
            f"生成  {datetime.now(resolve_timezone(timezone_name)).strftime('%Y-%m-%d %H:%M')}"
        )
    else:
        meta_text = (
            f"时间范围  {payload['range_label']}    "
            f"精度  {payload['bucket_label']}    "
            f"横轴  {axis_label}    "
            f"生成时间  {datetime.now(resolve_timezone(timezone_name)).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        title = _fit_text_to_width(
            draw,
            title,
            fonts.heading,
            max(120, title_right_limit - title_x),
        )
    draw.text((title_x, inner_y), title, font=fonts.heading, fill=TEXT)
    draw.text(
        (title_x, inner_y + _text_size(draw, title, fonts.heading)[1] + int(8 * scale)),
        meta_text,
        font=fonts.small,
        fill=MUTED_SOFT,
    )

    chip_y = inner_y + int(64 * scale)
    if payload.get("selected_season_reset_local"):
        season_text = f"赛季起点  {payload['selected_season_reset_local']}"
        draw.text(
            (title_x, inner_y + _text_size(draw, title, fonts.heading)[1] + int(30 * scale)),
            season_text,
            font=fonts.small,
            fill="#FDBA74",
        )
        chip_y = inner_y + int(86 * scale)
    chip_x = inner_x
    chip_h = int(30 * scale)
    right_limit = x + w - int(24 * scale)

    for index, series in enumerate(payload.get("series", [])):
        if single_series:
            break
        latest_score = 0
        if series.get("points"):
            latest_score = int(series["points"][-1]["rank_score"])
        chip_text = f"{series['display_name']} · {latest_score} RP"
        chip_w = _chip_width(draw, chip_text, fonts.small, 12)
        if chip_x + chip_w > right_limit:
            chip_x = inner_x
            chip_y += chip_h + int(8 * scale)
        _draw_chip(
            draw,
            chip_x,
            chip_y,
            chip_text,
            fonts.small,
            PALETTE[index % len(PALETTE)],
            "#11263A",
            "#213E57",
            chip_h,
        )
        chip_x += chip_w + int(10 * scale)

    plot_x = x + int(64 * scale)
    plot_y = chip_y + chip_h + int(22 * scale)
    plot_w = w - int(94 * scale)
    plot_h = h - (plot_y - y) - int(58 * scale)

    draw.rounded_rectangle(
        (plot_x, plot_y, plot_x + plot_w, plot_y + plot_h),
        radius=18,
        fill=PANEL_BG_SOFT,
        outline="#183147",
    )

    times, scores = _parse_points(payload, timezone_name, x_axis)
    if not times or not scores:
        text = "当前选中玩家暂无可展示的采集数据。"
        text_w, text_h = _text_size(draw, text, fonts.body)
        draw.text(
            (plot_x + (plot_w - text_w) // 2, plot_y + (plot_h - text_h) // 2),
            text,
            font=fonts.body,
            fill=MUTED,
        )
        return

    x_min = min(times)
    x_max = max(times)
    if x_min == x_max:
        span = range_to_delta(range_key) or timedelta(hours=12)
        x_min = x_min - span / 2
        x_max = x_max + span / 2

    y_min = min(scores)
    y_max = max(scores)
    if y_min == y_max:
        padding = 200 if y_min >= 200 else 50
        y_min = max(0, y_min - padding)
        y_max = y_max + padding
    else:
        padding = max(100, int((y_max - y_min) * 0.12))
        y_min = max(0, y_min - padding)
        y_max = y_max + padding

    count_axis_points = [
        list(series.get("change_points", [])) or _chart_line_source(series, x_axis="count")
        for series in payload.get("series", [])
    ]
    count_axis_max = max((len(points) for points in count_axis_points), default=0)
    total_seconds = max((x_max - x_min).total_seconds(), 1)
    score_span = max(y_max - y_min, 1)

    def map_x(value: datetime) -> int:
        return int(plot_x + ((value - x_min).total_seconds() / total_seconds) * plot_w)

    def map_x_count(index: int) -> int:
        if count_axis_max <= 1:
            return plot_x + plot_w // 2
        return int(plot_x + (max(0, min(index, count_axis_max - 1)) / (count_axis_max - 1)) * plot_w)

    def map_y(value: int) -> int:
        return int(plot_y + plot_h - ((value - y_min) / score_span) * plot_h)

    for idx in range(6):
        y_value = y_min + (score_span / 5) * idx
        line_y = map_y(int(y_value))
        draw.line((plot_x, line_y, plot_x + plot_w, line_y), fill=GRID, width=1)
        label = str(int(y_value))
        label_w, label_h = _text_size(draw, label, fonts.tiny)
        draw.text((plot_x - label_w - int(12 * scale), line_y - label_h // 2), label, font=fonts.tiny, fill=MUTED)

    if x_axis == "count":
        for point_index, label in _count_axis_tick_labels(count_axis_max):
            line_x = map_x_count(point_index)
            draw.line((line_x, plot_y, line_x, plot_y + plot_h), fill=GRID, width=1)
            label_w, _ = _text_size(draw, label, fonts.tiny)
            draw.text((line_x - label_w // 2, plot_y + plot_h + int(10 * scale)), label, font=fonts.tiny, fill=MUTED)
    else:
        for idx in range(6):
            ratio = idx / 5
            tick_dt = x_min + timedelta(seconds=total_seconds * ratio)
            line_x = int(plot_x + plot_w * ratio)
            draw.line((line_x, plot_y, line_x, plot_y + plot_h), fill=GRID, width=1)
            label = _format_axis_time(tick_dt, range_key, bucket)
            label_w, _ = _text_size(draw, label, fonts.tiny)
            draw.text((line_x - label_w // 2, plot_y + plot_h + int(10 * scale)), label, font=fonts.tiny, fill=MUTED)

        _draw_season_markers(
            draw,
            (plot_x, plot_y, plot_w, plot_h),
            payload,
            x_min,
            total_seconds,
            fonts,
            scale,
        )

    tz = resolve_timezone(timezone_name)
    chart_tag_boxes: list[tuple[int, int, int, int]] = []
    for index, series in enumerate(payload.get("series", [])):
        color = PALETTE[index % len(PALETTE)]
        point_positions: list[tuple[int, int]] = []
        if x_axis == "count":
            count_points = list(series.get("change_points", [])) or _chart_line_source(series, x_axis="count")
            for point_index, point in enumerate(count_points):
                point_positions.append(
                    (map_x_count(point_index), map_y(int(point.get("rank_score", 0))))
                )
        else:
            for point in _chart_line_source(series, x_axis=x_axis):
                dt = parse_iso8601(point.get("captured_at"))
                if dt is None:
                    continue
                point_positions.append(
                    (map_x(dt.astimezone(tz)), map_y(int(point.get("rank_score", 0))))
                )

        if len(point_positions) >= 2:
            if single_series:
                _draw_line_area_fill(canvas, point_positions, plot_y + plot_h, color)
            _draw_glow_line(
                draw,
                point_positions,
                color,
                scale,
                base_width=max(3, int(4 * scale)),
            )
        elif len(point_positions) == 1:
            px, py = point_positions[0]
            r = max(4, int(6 * scale))
            draw.ellipse((px - r, py - r, px + r, py + r), fill=color)

        if point_positions:
            px, py = point_positions[-1]
            _draw_highlight_dot(draw, px, py, color, scale)

        if single_series and point_positions:
            important_indices = _important_chart_point_indices(point_positions)
            source_points = (
                list(series.get("change_points", []))
                or _chart_line_source(series, x_axis="count")
                if x_axis == "count"
                else _chart_line_source(series, x_axis=x_axis)
            )
            for point_index in sorted(important_indices):
                if point_index >= len(source_points):
                    continue
                px, py = point_positions[point_index]
                score = int(source_points[point_index].get("rank_score") or 0)
                is_last = point_index == len(point_positions) - 1
                is_low = py == max(position[1] for position in point_positions)
                label = (
                    f"当前 {score:,}"
                    if is_last
                    else f"最低 {score:,}"
                    if is_low
                    else f"最高 {score:,}"
                    if py == min(position[1] for position in point_positions)
                    else f"起点 {score:,}"
                )
                marker_color = ACCENT if is_last else DANGER if is_low else SUCCESS
                marker_r = max(4, int((7 if is_last else 5) * scale))
                draw.ellipse(
                    (px - marker_r, py - marker_r, px + marker_r, py + marker_r),
                    fill=PANEL_BG_SOFT,
                    outline=marker_color,
                    width=max(2, int(2 * scale)),
                )
                _draw_chart_value_tag(
                    draw,
                    x=px,
                    y=py,
                    text=label,
                    font=fonts.tiny,
                    plot_rect=(plot_x, plot_y, plot_w, plot_h),
                    color=marker_color,
                    place_below=is_low,
                    occupied_boxes=chart_tag_boxes,
                )

        change_positions: list[tuple[int, int, dict]] = []
        for change_index, change_point in enumerate(series.get("change_points", [])):
            if x_axis == "count":
                change_positions.append(
                    (
                        map_x_count(change_index),
                        map_y(int(change_point.get("rank_score", 0))),
                        change_point,
                    )
                )
            else:
                dt = parse_iso8601(change_point.get("captured_at"))
                if dt is None:
                    continue
                change_positions.append(
                    (
                        map_x(dt.astimezone(tz)),
                        map_y(int(change_point.get("rank_score", 0))),
                        change_point,
                    )
                )

        for px, py, change_point in change_positions:
            icon_name = str(change_point.get("rank_icon_file") or "").strip()
            icon_path = _resolve_rank_icon_path(
                icon_dir,
                icon_name,
                str(change_point.get("rank_label") or ""),
                str(change_point.get("rank_asset_file") or ""),
            )
            show_rank_icon = bool(change_point.get("show_rank_icon"))
            if show_rank_icon and icon_path:
                size = max(24, int((34 if change_point.get("season_reset") else 30) * scale))
                _paste_rank_icon(canvas, icon_path, px - size // 2, py - size // 2, size)
            else:
                if change_point.get("season_reset") or int(change_point.get("score_delta") or 0):
                    _draw_event_icon(
                        draw,
                        px,
                        py,
                        max(12, int((17 if show_rank_icon else 14) * scale)),
                        change_point,
                        glow=bool(change_point.get("season_reset")),
                    )
                else:
                    _draw_timeline_change_dot(
                        draw,
                        px,
                        py,
                        color,
                        scale,
                        emphasized=show_rank_icon,
                    )

            if show_rank_icon:
                label_font = fonts.small
                label = _fit_text_to_width(
                    draw,
                    str(change_point.get("rank_label") or "暂无段位"),
                    label_font,
                    max(62, int(120 * scale)),
                )
                label_w, label_h = _text_size(draw, label, label_font)
                pad_x = max(5, int(6 * scale))
                pad_y = max(3, int(4 * scale))
                tag_w = label_w + pad_x * 2
                tag_h = label_h + pad_y * 2
                label_left, label_top = _chart_tag_position(
                    x=px,
                    y=py - int(7 * scale),
                    tag_w=tag_w,
                    tag_h=tag_h,
                    plot_rect=(plot_x, plot_y, plot_w, plot_h),
                    place_below=False,
                    occupied_boxes=chart_tag_boxes,
                )
                label_box = (label_left, label_top, label_left + tag_w, label_top + tag_h)
                chart_tag_boxes.append(label_box)
                draw.rounded_rectangle(
                    label_box,
                    radius=8,
                    fill=SEASON_LABEL_BG if change_point.get("season_reset") else "#112A3E",
                    outline=SEASON_ACCENT if change_point.get("season_reset") else "#28455F",
                )
                draw.text(
                    (label_left + pad_x, label_top + pad_y - 1),
                    label,
                    font=label_font,
                    fill="#FFE4CC" if change_point.get("season_reset") else TEXT,
                )


def _summary_grid_columns(card_count: int, available_width: int, layout: str) -> int:
    if card_count <= 1:
        return 1
    if layout == "compact":
        if available_width < 720:
            return 1
        if available_width < 1040:
            return min(2, card_count)
        return min(3, card_count)
    if available_width < 980:
        return 1
    if available_width < 1480:
        return min(2, card_count)
    return min(3, card_count)


def _summary_card_height(scale: float, layout: str) -> int:
    return int((224 if layout == "full" else 138) * scale)


def _summary_uses_dashboard_layout(summary_count: int, available_width: int, layout: str) -> bool:
    return layout == "full" and summary_count == 1 and available_width >= 1180


def _measure_summary_panel_height(
    summary_count: int,
    available_width: int,
    scale: float,
    layout: str,
) -> int:
    if summary_count <= 0:
        return int(138 * scale)

    columns = _summary_grid_columns(summary_count, available_width, layout)
    rows = ceil(summary_count / columns)
    gap = int(12 * scale)
    card_h = _summary_card_height(scale, layout)
    return int(72 * scale) + rows * card_h + max(0, rows - 1) * gap + int(16 * scale)


def _draw_metric_tile(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    label: str,
    value: str,
    fonts: Fonts,
    scale: float,
    *,
    value_color: str = TEXT,
    subtext: str = "",
    progress_fraction: float | None = None,
) -> None:
    x, y, w, h = rect
    draw.rounded_rectangle(
        (x, y, x + w, y + h),
        radius=12,
        fill=PANEL_BG_SOFT,
        outline="#1C3449",
    )
    pad = int(12 * scale)
    draw.text((x + pad, y + int(10 * scale)), label, font=fonts.tiny, fill=MUTED)
    fitted_value = _fit_text_to_width(draw, value, fonts.subheading, w - pad * 2)
    draw.text((x + pad, y + int(30 * scale)), fitted_value, font=fonts.subheading, fill=value_color)
    if progress_fraction is not None:
        track_x = x + pad
        track_y = y + h - int(22 * scale)
        track_w = max(8, w - pad * 2)
        track_h = max(4, int(5 * scale))
        draw.rounded_rectangle(
            (track_x, track_y, track_x + track_w, track_y + track_h),
            radius=track_h,
            fill="#21384A",
        )
        fill_w = max(track_h, int(track_w * max(0.0, min(progress_fraction, 1.0))))
        draw.rounded_rectangle(
            (track_x, track_y, track_x + fill_w, track_y + track_h),
            radius=track_h,
            fill=ACCENT,
        )
    if subtext:
        fitted_subtext = _fit_text_to_width(draw, subtext, fonts.tiny, w - pad * 2)
        subtext_y = y + h - int((39 if progress_fraction is not None else 24) * scale)
        draw.text((x + pad, subtext_y), fitted_subtext, font=fonts.tiny, fill=MUTED_SOFT)


def _draw_single_summary_dashboard(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    summary: dict,
    icon_dir: Path,
    fonts: Fonts,
    scale: float,
) -> None:
    x, y, w, h = rect
    gap = int(12 * scale)
    left_w = max(int(390 * scale), int(w * 0.34))
    right_w = w - left_w - gap

    draw.rounded_rectangle(
        (x, y, x + left_w, y + h),
        radius=12,
        fill=PANEL_BG_SOFT,
        outline="#1C3449",
    )
    draw.rectangle((x, y, x + max(4, int(5 * scale)), y + h), fill=ACCENT)
    pad = int(16 * scale)
    rank_label = str(summary.get("latest_rank_label") or "暂无数据")
    latest_score = int(summary.get("latest_rank_score") or 0)
    progress = _rank_progress(rank_label, latest_score)
    rank_icon_path = _resolve_rank_icon_path(
        icon_dir,
        str(summary.get("latest_rank_icon_file") or ""),
        rank_label,
        str(summary.get("rank_asset_file") or ""),
    )
    icon_size = max(64, int(78 * scale)) if rank_icon_path else 0
    icon_gap = int(12 * scale) if rank_icon_path else 0
    text_width = left_w - pad * 2 - icon_size - icon_gap
    display_name = _fit_text_to_width(
        draw,
        str(summary.get("display_name") or "未知玩家"),
        fonts.subheading,
        max(100, text_width),
    )
    draw.text((x + pad, y + int(15 * scale)), display_name, font=fonts.subheading, fill=TEXT)
    draw.text(
        (x + pad, y + int(47 * scale)),
        _fit_text_to_width(draw, rank_label, fonts.small, max(100, text_width)),
        font=fonts.small,
        fill=MUTED_SOFT,
    )
    draw.text(
        (x + pad, y + int(76 * scale)),
        f"{latest_score:,} RP",
        font=fonts.heading,
        fill=TEXT,
    )
    if rank_icon_path:
        _paste_rank_icon(
            canvas,
            rank_icon_path,
            x + left_w - pad - icon_size,
            y + int(14 * scale),
            icon_size,
        )

    progress_y = y + int(112 * scale)
    progress_h = max(58, int(66 * scale))
    draw.rounded_rectangle(
        (x + pad, progress_y, x + left_w - pad, progress_y + progress_h),
        radius=10,
        fill="#172E27",
        outline="#2B6B50",
    )
    draw.text(
        (x + pad + int(11 * scale), progress_y + int(7 * scale)),
        _fit_text_to_width(
            draw,
            (
                f"段位进度 · {rank_label} → {progress['next_rank_label']}"
                if progress and progress.get("promotion_distance") is not None
                else "段位进度 · 大师 / 猎杀排名竞争"
                if progress
                else "段位进度"
            ),
            fonts.tiny,
            left_w - pad * 2 - int(78 * scale),
        ),
        font=fonts.tiny,
        fill="#9FE7C0",
    )
    progress_text = "当前段位暂无固定升段阈值"
    progress_fraction = 0.0
    if progress:
        progress_text = str(progress["text"])
        progress_fraction = float(progress.get("progress_fraction") or 0.0)
        progress_percent = (
            f"{progress_fraction * 100:.1f}%"
            if progress.get("promotion_distance") is not None
            else "排名制"
        )
        percent_w, _ = _text_size(draw, progress_percent, fonts.tiny)
        draw.text(
            (
                x + left_w - pad - int(11 * scale) - percent_w,
                progress_y + int(7 * scale),
            ),
            progress_percent,
            font=fonts.tiny,
            fill=SUCCESS,
        )
    draw.text(
        (x + pad + int(11 * scale), progress_y + int(25 * scale)),
        _fit_text_to_width(
            draw,
            progress_text,
            fonts.small,
            left_w - pad * 2 - int(22 * scale),
        ),
        font=fonts.small,
        fill=SUCCESS if progress else MUTED_SOFT,
    )
    track_x = x + pad + int(11 * scale)
    track_y = progress_y + progress_h - int(13 * scale)
    track_w = left_w - pad * 2 - int(22 * scale)
    track_h = max(5, int(6 * scale))
    draw.rounded_rectangle(
        (track_x, track_y, track_x + track_w, track_y + track_h),
        radius=track_h,
        fill="#274035",
    )
    if progress:
        fill_w = max(track_h, int(track_w * max(0.0, min(progress_fraction, 1.0))))
        draw.rounded_rectangle(
            (track_x, track_y, track_x + fill_w, track_y + track_h),
            radius=track_h,
            fill=SUCCESS,
        )
    time_text = _fit_text_to_width(
        draw,
        f"{summary.get('first_seen_at') or '暂无'}  →  {summary.get('last_seen_at') or '暂无'}",
        fonts.tiny,
        left_w - pad * 2,
    )
    draw.text(
        (x + pad, y + h - int(23 * scale)),
        time_text,
        font=fonts.tiny,
        fill=MUTED,
    )

    tile_cols = 2
    tile_rows = 2
    tile_w = int((right_w - gap * (tile_cols - 1)) / tile_cols)
    tile_h = int((h - gap * (tile_rows - 1)) / tile_rows)
    max_score = int(summary.get("max_score") or 0)
    min_score = int(summary.get("min_score") or 0)
    active_delta_label, active_delta_value, active_delta_color, active_delta_subtext = (
        _summary_active_delta_metric(summary)
    )
    recent_event_label, recent_event_value, recent_event_color, recent_event_subtext = (
        _summary_recent_event_metric(summary)
    )
    recent_delta_raw = (
        summary.get("recent_event_delta_score")
        if "recent_event_delta_score" in summary
        else summary.get("delta_score")
    )
    recent_delta = int(recent_delta_raw or 0)
    recent_count = int(summary.get("recent_event_count") or summary.get("change_events") or 0)
    up_count = int(summary.get("recent_up_count") or summary.get("up_count") or 0)
    down_count = int(summary.get("recent_down_count") or summary.get("down_count") or 0)
    up_score = int(summary.get("recent_up_score") or 0)
    down_score = int(summary.get("recent_down_score") or 0)
    if up_count or down_count:
        result_value = f"上分 {up_count} 次 / 掉分 {down_count} 次"
        result_subtext = (
            f"+{up_score} / {down_score} RP · 净变化 {recent_delta:+d} RP"
            if up_score or down_score
            else f"最近 {recent_count} 次合计 {recent_delta:+d} RP"
        )
    else:
        result_value = f"{recent_count} 次"
        result_subtext = f"分数变化合计 {recent_delta:+d} RP"
    metrics = [
        (active_delta_label, active_delta_value, active_delta_color, active_delta_subtext),
        ("最高 / 最低", f"{max_score:,} / {min_score:,}", TEXT, "当前窗口分数区间"),
        (recent_event_label, recent_event_value, recent_event_color, recent_event_subtext),
        ("上分 / 掉分", result_value, _score_color(recent_delta), result_subtext),
    ]

    for index, (label, value, color, subtext) in enumerate(metrics):
        col = index % tile_cols
        row = index // tile_cols
        tile_x = x + left_w + gap + col * (tile_w + gap)
        tile_y = y + row * (tile_h + gap)
        _draw_metric_tile(
            draw,
            (tile_x, tile_y, tile_w, tile_h),
            label,
            value,
            fonts,
            scale,
            value_color=color,
            subtext=subtext,
        )


def _draw_summary_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    payload: dict,
    icon_dir: Path,
    fonts: Fonts,
    scale: float,
    layout: str,
) -> None:
    x, y, w, h = rect
    _draw_panel(draw, rect)
    title = "数据摘要"
    title_x = x + int(22 * scale)
    title_y = y + int(16 * scale)
    draw.text((title_x, title_y), title, font=fonts.heading, fill=TEXT)

    summaries = list(payload.get("summaries", []))
    if layout == "compact":
        summaries = summaries[:6]

    subtitle = ""
    if payload.get("selected_season_reset_local"):
        subtitle = f"当前赛季从 {payload['selected_season_reset_local']} 起统计"
    elif payload.get("bucket_auto_expanded"):
        subtitle = "未满 24 小时的数据已按完整时间轴统计"
    else:
        subtitle = "基于后端采集样本与最近分数变化生成"

    if subtitle:
        draw.text(
            (title_x, title_y + _text_size(draw, title, fonts.heading)[1] + int(6 * scale)),
            subtitle,
            font=fonts.small,
            fill="#FDBA74" if payload.get("selected_season_reset_local") else MUTED,
        )

    if not summaries:
        draw.text((x + int(22 * scale), y + int(70 * scale)), "当前没有可展示的文字总结。", font=fonts.body, fill=MUTED)
        return

    inner_x = x + int(16 * scale)
    inner_y = y + int(64 * scale)
    inner_w = w - int(32 * scale)
    gap = int(12 * scale)
    columns = _summary_grid_columns(len(summaries), inner_w, layout)
    card_w = int((inner_w - gap * (columns - 1)) / columns)
    card_h = _summary_card_height(scale, layout)

    if _summary_uses_dashboard_layout(len(summaries), inner_w, layout):
        _draw_single_summary_dashboard(
            canvas,
            draw,
            (inner_x, inner_y, inner_w, card_h),
            summaries[0],
            icon_dir,
            fonts,
            scale,
        )
        return

    for index, summary in enumerate(summaries):
        row = index // columns
        col = index % columns
        card_x = inner_x + col * (card_w + gap)
        card_y = inner_y + row * (card_h + gap)

        draw.rounded_rectangle(
            (card_x, card_y, card_x + card_w, card_y + card_h),
            radius=12,
            fill=PANEL_BG_SOFT,
            outline="#1C3449",
        )
        accent_color = PALETTE[index % len(PALETTE)]
        draw.rounded_rectangle(
            (
                card_x + int(1 * scale),
                card_y + int(1 * scale),
                card_x + card_w - int(1 * scale),
                card_y + int(5 * scale),
            ),
            radius=5,
            fill=accent_color,
        )

        pad = int(14 * scale)
        cx = card_x + pad
        cy = card_y + int(12 * scale)
        display_name = str(summary.get("display_name") or "")
        score_text = f"{int(summary.get('latest_rank_score') or 0)} RP"
        score_w, _ = _text_size(draw, score_text, fonts.subheading)
        rank_label = str(summary.get("latest_rank_label") or "暂无数据")
        rank_icon_path = _resolve_rank_icon_path(
            icon_dir,
            str(summary.get("latest_rank_icon_file") or ""),
            rank_label,
            str(summary.get("rank_asset_file") or ""),
        )
        icon_size = max(36, int(44 * scale)) if rank_icon_path else 0
        icon_gap = int(10 * scale) if rank_icon_path else 0
        name_max_w = max(90, card_w - pad * 3 - score_w - icon_size - icon_gap)
        display_name = _fit_text_to_width(draw, display_name, fonts.subheading, name_max_w)
        if rank_icon_path:
            _paste_rank_icon(canvas, rank_icon_path, cx, cy - int(1 * scale), icon_size)
        text_x = cx + icon_size + icon_gap
        draw.text((text_x, cy), display_name, font=fonts.subheading, fill=TEXT)
        draw.text((card_x + card_w - pad - score_w, cy), score_text, font=fonts.subheading, fill=TEXT)

        rank_text = _fit_text_to_width(
            draw,
            rank_label,
            fonts.tiny,
            max(90, name_max_w),
        )
        draw.text(
            (text_x, cy + _text_size(draw, display_name, fonts.subheading)[1] + int(6 * scale)),
            rank_text,
            font=fonts.tiny,
            fill=MUTED,
        )

        active_delta_label, active_delta_value, active_delta_color, _ = _summary_active_delta_metric(summary)
        if active_delta_label == "最近14小时净变化":
            active_delta_label = "14小时净变化"
        metric_items = [
            (active_delta_label, active_delta_value, active_delta_color),
            ("变动", str(int(summary.get("change_events") or 0)), TEXT),
            ("最高", str(int(summary.get("max_score") or 0)), TEXT),
            ("最低", str(int(summary.get("min_score") or 0)), TEXT),
        ]
        if layout == "compact":
            metric_items = [
                metric_items[0],
                metric_items[1],
                (
                    "高/低",
                    f"{int(summary.get('max_score') or 0)}/{int(summary.get('min_score') or 0)}",
                    TEXT,
                ),
            ]

        stats_y = card_y + int((58 if layout == "compact" else 66) * scale)
        metric_count = len(metric_items)
        metric_w = max(76, int((card_w - pad * 2) / metric_count))
        for item_index, (label, value, color) in enumerate(metric_items):
            tx = cx + item_index * metric_w
            ty = stats_y
            draw.text((tx, ty), label, font=fonts.tiny, fill=MUTED)
            value = _fit_text_to_width(draw, value, fonts.body, max(54, metric_w - int(8 * scale)))
            draw.text((tx, ty + int(15 * scale)), value, font=fonts.body, fill=color)

        footer_y = card_y + card_h - int((44 if layout == "compact" else 48) * scale)
        draw.line(
            (cx, footer_y - int(8 * scale), card_x + card_w - pad, footer_y - int(8 * scale)),
            fill="#21384F",
            width=1,
        )
        recent_event_label, recent_event_value, recent_event_color, recent_event_subtext = (
            _summary_recent_event_metric(summary)
        )
        recent_event_text = _fit_text_to_width(
            draw,
            f"{recent_event_label}  {recent_event_value}  {recent_event_subtext}",
            fonts.tiny,
            card_w - pad * 2,
        )
        draw.text(
            (cx, footer_y),
            recent_event_text,
            font=fonts.tiny,
            fill=recent_event_color,
        )
        progress = _rank_progress(rank_label, int(summary.get("latest_rank_score") or 0))
        if progress:
            progress_text = _fit_text_to_width(draw, progress["text"], fonts.tiny, card_w - pad * 2)
            draw.text(
                (cx, footer_y + int(17 * scale)),
                progress_text,
                font=fonts.tiny,
                fill=SUCCESS,
            )
        time_text = _fit_text_to_width(
            draw,
            f"{summary.get('first_seen_at') or '暂无'} -> {summary.get('last_seen_at') or '暂无'}",
            fonts.tiny,
            card_w - pad * 2,
        )
        draw.text((cx, footer_y + int(34 * scale)), time_text, font=fonts.tiny, fill=MUTED)


def _event_row_height(scale: float, layout: str) -> int:
    return max(int((72 if layout == "compact" else 66) * scale), 54)


def _measure_event_panel_height(event_count: int, scale: float, layout: str) -> int:
    if event_count <= 0:
        return int(148 * scale)
    return int(110 * scale) + event_count * _event_row_height(scale, layout) + int(18 * scale)


def _max_canvas_height(layout: str, event_count: int, scale: float) -> int:
    if layout != "full":
        return MAX_COMPACT_IMAGE_HEIGHT
    needed_for_events = _measure_event_panel_height(max(0, event_count), scale, layout) + int(1100 * scale)
    return min(MAX_FULL_IMAGE_HEIGHT, max(MAX_COMPACT_IMAGE_HEIGHT, needed_for_events))


def _event_table_columns(
    inner_width: int,
    *,
    include_player: bool = False,
) -> list[tuple[str, int]]:
    if include_player:
        status_w = int(inner_width * 0.10)
        time_w = int(inner_width * 0.17)
        player_w = int(inner_width * 0.14)
        score_w = int(inner_width * 0.29)
        rank_w = inner_width - status_w - time_w - player_w - score_w
        return [
            ("状态", status_w),
            ("时间", time_w),
            ("玩家", player_w),
            ("分数变化", score_w),
            ("段位变化", rank_w),
        ]
    status_w = int(inner_width * 0.12)
    time_w = int(inner_width * 0.18)
    score_w = int(inner_width * 0.39)
    rank_w = inner_width - status_w - time_w - score_w
    return [
        ("状态", status_w),
        ("时间", time_w),
        ("分数变化", score_w),
        ("段位变化", rank_w),
    ]


def _event_player_key(event: dict) -> str:
    raw_id = event.get("player_id")
    if raw_id not in {None, ""}:
        return f"id:{raw_id}"
    return f"name:{str(event.get('display_name') or '').casefold()}"


def _rank_change_context_text(events: list[dict], index: int) -> str:
    if index < 0 or index >= len(events):
        return ""
    event = events[index]
    if not event.get("rank_changed") or event.get("season_reset"):
        return ""

    player_key = _event_player_key(event)
    window = [event]
    for older_event in events[index + 1 :]:
        if _event_player_key(older_event) != player_key:
            continue
        if older_event.get("rank_changed") or older_event.get("season_reset"):
            break
        window.append(older_event)

    total = len(window)
    deltas = [int(item.get("score_delta") or 0) for item in window]
    up_deltas = [value for value in deltas if value > 0]
    down_deltas = [value for value in deltas if value < 0]
    neutral_count = sum(1 for value in deltas if value == 0)
    from_label = str(event.get("from_rank_label") or "").strip()
    to_label = str(event.get("to_rank_label") or "").strip()
    change_kind = _rank_change_direction(from_label, to_label)
    neutral_text = f" · 持平 {neutral_count} 局" if neutral_count else ""
    return (
        f"本次{change_kind}历程 {total} 局 · "
        f"上分 {len(up_deltas)} 局 +{sum(up_deltas)} RP · "
        f"掉分 {len(down_deltas)} 局 {sum(down_deltas)} RP"
        f"{neutral_text} · 净变化 {sum(deltas):+d} RP"
    )


def _rank_change_direction(from_label: str, to_label: str) -> str:
    from_normalized = _normalize_rank_progress_label(from_label)
    to_normalized = _normalize_rank_progress_label(to_label)
    labels = [label for label, _ in RANK_PROGRESS_THRESHOLDS]
    if from_normalized == "猎杀":
        from_normalized = "大师"
    if to_normalized == "猎杀":
        to_normalized = "大师"
    if from_normalized in labels and to_normalized in labels:
        if labels.index(to_normalized) > labels.index(from_normalized):
            return "升段"
        if labels.index(to_normalized) < labels.index(from_normalized):
            return "掉段"
    return "段位变化"


def _key_event_palette(event: dict) -> tuple[str, str] | None:
    if event.get("season_reset"):
        return SEASON_ACCENT, "#2A2017"
    if not event.get("rank_changed"):
        return None

    direction = _rank_change_direction(
        str(event.get("from_rank_label") or ""),
        str(event.get("to_rank_label") or ""),
    )
    if direction == "升段":
        return "#34D399", "#102A23"
    if direction == "掉段":
        return "#FB7185", "#321820"
    return ACCENT, "#11283A"


def _draw_event_panel(
    canvas: Image.Image,
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    payload: dict,
    icon_dir: Path,
    fonts: Fonts,
    scale: float,
    layout: str,
) -> None:
    x, y, w, h = rect
    _draw_panel(draw, rect)
    title = "最近分数变化记录"
    title_x = x + int(22 * scale)
    title_y = y + int(18 * scale)
    draw.text((title_x, title_y), title, font=fonts.heading, fill=TEXT)

    events = list(payload.get("change_events", []))
    draw.text(
        (title_x, title_y + _text_size(draw, title, fonts.heading)[1] + int(6 * scale)),
        f"最近 {len(events)} 次分数变化，按后端采集到的 RP 变化生成",
        font=fonts.small,
        fill=MUTED,
    )

    if not events:
        draw.text((x + int(22 * scale), y + int(74 * scale)), "当前范围内暂无变化事件。", font=fonts.body, fill=MUTED)
        return

    header_y = y + int(74 * scale)
    inner_x = x + int(16 * scale)
    inner_w = w - int(32 * scale)
    row_h = _event_row_height(scale, layout)
    distinct_players = {_event_player_key(event) for event in events}
    include_player = len(payload.get("series", [])) > 1 or len(distinct_players) > 1
    columns = _event_table_columns(inner_w, include_player=include_player)

    cursor_x = inner_x + int(12 * scale)
    for title_text, col_w in columns:
        draw.text((cursor_x, header_y + int(8 * scale)), title_text, font=fonts.tiny, fill=MUTED_SOFT)
        cursor_x += col_w
    draw.line(
        (inner_x, header_y + int(31 * scale), inner_x + inner_w, header_y + int(31 * scale)),
        fill="#21384F",
        width=1,
    )

    for index, event in enumerate(events):
        row_y = header_y + int(42 * scale) + index * row_h
        score_delta = int(event.get("score_delta") or 0)
        key_palette = _key_event_palette(event)
        accent = "#35506A"
        if key_palette:
            accent = key_palette[0]
        elif score_delta > 0:
            accent = "#34D399"
        elif score_delta < 0:
            accent = "#FB7185"

        if key_palette:
            key_fill = key_palette[1]
            draw.rectangle(
                (inner_x, row_y, inner_x + inner_w, row_y + row_h - 1),
                fill=key_fill,
            )
            draw.rectangle(
                (
                    inner_x + max(2, int(3 * scale)),
                    row_y,
                    inner_x + inner_w,
                    row_y + max(2, int(2 * scale)),
                ),
                fill=_hex_to_rgba(accent, 155),
            )
        elif index % 2 == 0:
            draw.rectangle(
                (inner_x, row_y, inner_x + inner_w, row_y + row_h - 1),
                fill="#0F2031",
            )
        draw.rectangle(
            (inner_x, row_y + int(6 * scale), inner_x + max(2, int(3 * scale)), row_y + row_h - int(7 * scale)),
            fill=accent,
        )
        draw.line(
            (inner_x, row_y + row_h - 1, inner_x + inner_w, row_y + row_h - 1),
            fill="#1A3349",
            width=1,
        )

        time_text = str(event.get("captured_at_local") or "暂无")
        player_text = _fit_text_to_width(
            draw,
            str(event.get("display_name") or "未知玩家"),
            fonts.tiny,
            (columns[2][1] if include_player else inner_w) - int(14 * scale),
        )
        score_text = f"{event['from_rank_score']} -> {event['to_rank_score']} ({score_delta:+d})"
        rank_context_text = _rank_change_context_text(events, index)
        rank_icon_path = None
        if event.get("rank_changed"):
            rank_icon_path = _resolve_rank_icon_path(
                icon_dir,
                str(event.get("rank_icon_file") or ""),
                str(event.get("to_rank_label") or ""),
                str(event.get("rank_asset_file") or ""),
            )
        rank_icon_size = max(22, int(30 * scale)) if rank_icon_path else 0
        rank_text_left_pad = rank_icon_size + int(9 * scale) if rank_icon_path else 0
        rank_column_index = 4 if include_player else 3
        score_column_index = 3 if include_player else 2
        rank_changed = bool(event.get("rank_changed") or event.get("season_reset"))
        rank_text = _fit_text_to_width(
            draw,
            (
                f"{event['from_rank_label'] or '无'} -> {event['to_rank_label']}"
                if rank_changed
                else "—"
            ),
            fonts.tiny,
            columns[rank_column_index][1] - rank_text_left_pad - int(12 * scale),
        )

        values = [
            (_event_kind_label(event), fonts.tiny, _event_kind_color(event)),
            (time_text, fonts.tiny, MUTED_SOFT),
        ]
        if include_player:
            values.append((player_text, fonts.tiny, TEXT))
        values.extend(
            [
                (score_text, fonts.tiny, _score_color(score_delta)),
                (
                    rank_text,
                    fonts.tiny,
                    _event_kind_color(event) if rank_changed else MUTED,
                ),
            ]
        )

        cursor_x = inner_x + int(12 * scale)
        for value_index, ((text, font, color), (_, col_w)) in enumerate(zip(values, columns)):
            if value_index == 0:
                icon_size = max(20, int(24 * scale))
                icon_x = cursor_x + icon_size // 2
                icon_y = row_y + int(18 * scale)
                _draw_event_icon(draw, icon_x, icon_y, icon_size, event)
                draw.text(
                    (cursor_x + icon_size + int(7 * scale), row_y + int(10 * scale)),
                    text,
                    font=font,
                    fill=color,
                )
            elif value_index == score_column_index:
                draw.text((cursor_x, row_y + int(10 * scale)), text, font=font, fill=color)
                if rank_context_text:
                    context_text = _fit_text_to_width(
                        draw,
                        rank_context_text,
                        fonts.tiny,
                        col_w - int(12 * scale),
                    )
                    draw.text(
                        (cursor_x, row_y + int(30 * scale)),
                        context_text,
                        font=fonts.tiny,
                        fill=MUTED_SOFT,
                    )
            elif value_index == rank_column_index and rank_icon_path:
                icon_size = rank_icon_size
                visible_row_h = row_h
                icon_y = row_y + max(2, (visible_row_h - icon_size) // 2)
                _paste_rank_icon(canvas, rank_icon_path, cursor_x, icon_y, icon_size)
                draw.text(
                    (cursor_x + rank_text_left_pad, row_y + int(10 * scale)),
                    text,
                    font=font,
                    fill=color,
                )
            else:
                draw.text((cursor_x, row_y + int(10 * scale)), text, font=font, fill=color)
            cursor_x += col_w


def _normalize_layout(layout: str) -> str:
    value = str(layout or "full").strip().lower()
    if value not in {"compact", "full"}:
        return "full"
    return value


def render_dashboard_image_png(
    payload: dict,
    icon_dir: Path,
    timezone_name: str,
    range_key: str,
    bucket: str,
    width: int = 1920,
    height: int = 1080,
    layout: str = "full",
    event_limit: int = 20,
    x_axis: str = "time",
) -> bytes:
    layout = _normalize_layout(layout)
    x_axis = _normalize_chart_x_axis(x_axis)
    width = max(1280, min(int(width), 3840))
    max_height = MAX_FULL_IMAGE_HEIGHT if layout == "full" else MAX_COMPACT_IMAGE_HEIGHT
    height = max(720, min(int(height), max_height))
    event_limit = max(1, int(event_limit or 20))

    payload = _trim_payload_for_event_limit(payload, event_limit)

    if layout == "full":
        scale = max(0.92, min(width / 1440, 1.45))
    else:
        scale = max(0.8, min(min(width / 1920, height / 1080), 1.35))

    margin = int(36 * scale)
    gap = int(18 * scale)
    fonts = _build_fonts(scale)

    if layout == "full":
        chart_h = max(int(620 * scale), min(int(height * 0.56), int(860 * scale)))
        summary_h = _measure_summary_panel_height(len(payload.get("summaries", [])), width - margin * 2, scale, layout)
        event_h = _measure_event_panel_height(len(payload.get("change_events", [])), scale, layout)
        canvas_height = min(
            _max_canvas_height(layout, len(payload.get("change_events", [])), scale),
            max(height, margin * 2 + chart_h + gap + summary_h + gap + event_h),
        )
        chart_rect = (margin, margin, width - margin * 2, chart_h)
        summary_rect = (margin, margin + chart_h + gap, width - margin * 2, summary_h)
        event_rect = (
            margin,
            summary_rect[1] + summary_rect[3] + gap,
            width - margin * 2,
            min(event_h, canvas_height - (summary_rect[1] + summary_rect[3] + gap) - margin),
        )
    else:
        chart_h = int(height * 0.52)
        bottom_h = height - margin * 2 - gap - chart_h
        summary_w = int((width - margin * 2 - gap) * 0.60)
        events_w = width - margin * 2 - gap - summary_w
        canvas_height = height
        chart_rect = (margin, margin, width - margin * 2, chart_h)
        summary_rect = (margin, margin + chart_h + gap, summary_w, bottom_h)
        event_rect = (margin + summary_w + gap, margin + chart_h + gap, events_w, bottom_h)

    image = Image.new("RGBA", (width, canvas_height), BG_BOTTOM)
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, canvas_height), fill=BG_BOTTOM)
    draw.rectangle((0, 0, width, int(10 * scale)), fill="#13324A")
    band_step = max(180, int(220 * scale))
    for band_y in range(int(70 * scale), canvas_height, band_step):
        draw.line((0, band_y, width, band_y), fill="#0D1C2B", width=1)

    _draw_chart_panel(
        image,
        draw,
        chart_rect,
        payload,
        icon_dir,
        timezone_name,
        range_key,
        bucket,
        fonts,
        scale,
        x_axis,
    )
    _draw_summary_panel(image, draw, summary_rect, payload, icon_dir, fonts, scale, layout)
    _draw_event_panel(image, draw, event_rect, payload, icon_dir, fonts, scale, layout)

    buffer = BytesIO()
    image.convert("RGB").save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()
