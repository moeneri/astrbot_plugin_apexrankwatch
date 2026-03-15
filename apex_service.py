from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx


_TRANSLATIONS_FILE = Path(__file__).with_name("translations.json")


NAME_MAP: dict[str, str] = {
    # Rank
    "Unranked": "菜鸟",
    "Bronze": "青铜",
    "Silver": "白银",
    "Gold": "黄金",
    "Platinum": "白金",
    "Diamond": "钻石",
    "Master": "大师",
    "Apex Predator": "Apex 猎杀者",
    # States
    "offline": "离线",
    "online": "在线",
    "inLobby": "在大厅",
    "in Lobby": "在大厅",
    "In lobby": "在大厅",
    "In Lobby": "在大厅",
    "inMatch": "比赛中",
    "in Match": "比赛中",
    "In match": "比赛中",
    "In Match": "比赛中",
    "Offline": "离线",
    "Online": "在线",
    "true": "是",
    "false": "否",
    # Legends
    "Bloodhound": "寻血猎犬",
    "Gibraltar": "直布罗陀",
    "Lifeline": "命脉",
    "Pathfinder": "探路者",
    "Wraith": "恶灵",
    "Bangalore": "班加罗尔",
    "Caustic": "侵蚀",
    "Mirage": "幻象",
    "Octane": "动力小子",
    "Wattson": "沃特森",
    "Crypto": "密客",
    "Revenant": "亡灵",
    "Loba": "罗芭",
    "Rampart": "兰伯特",
    "Horizon": "地平线",
    "Fuse": "暴雷",
    "Valkyrie": "瓦尔基里",
    "Seer": "希尔",
    "Ash": "艾许",
    "Mad Maggie": "疯玛吉",
    "Newcastle": "纽卡斯尔",
    "Vantage": "万蒂奇",
    "Catalyst": "卡特莉丝",
    "Ballistic": "弹道",
    "Conduit": "导管",
    "Alter": "变幻",
    "Sparrow": "琉雀",
    # Stat names
    "BR Kills": "击杀数",
    "BR Wins": "胜场数",
    "BR Damage": "造成伤害",
    "kills": "击杀数",
    "wins": "胜场数",
    "damage": "造成伤害",
}


@dataclass
class LegendKillsRank:
    value: int
    global_percent: str


@dataclass
class ApexPlayerStats:
    name: str
    uid: str
    level: int
    rank_score: int
    rank_name: str
    rank_div: int
    global_rank_percent: str
    is_online: bool
    selected_legend: str
    legend_kills_rank: LegendKillsRank | None
    current_state: str
    is_in_lobby_or_match: bool
    platform: str


@dataclass
class SeasonInfo:
    season_number: int | None
    season_name: str
    start_date: str
    end_date: str
    timezone: str
    update_time_hint: str
    source: str
    season_url: str
    start_iso: str
    end_iso: str


class PlayerNotFoundError(Exception):
    pass


class ApexApiClient:
    def __init__(
        self,
        api_key: str,
        timeout_ms: int,
        max_retries: int,
        logger,
        debug_enabled: bool = False,
    ) -> None:
        self._api_key = api_key
        self._timeout = max(1, int(timeout_ms)) / 1000.0
        self._max_retries = max(0, int(max_retries))
        self._logger = logger
        self._debug_enabled = debug_enabled
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            headers={"User-Agent": "AstrBot-ApexRankWatch/1.0"},
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_player_stats_by_name(
        self, player_name: str, platform: str
    ) -> ApexPlayerStats:
        api_url = "https://api.mozambiquehe.re/bridge"
        params = {"auth": self._api_key, "player": player_name, "platform": platform}
        data = await self._request_player_data(api_url, params, player_name)
        return _parse_player_stats(data, platform, player_name)

    async def fetch_player_stats_by_uid(self, uid: str, platform: str) -> ApexPlayerStats:
        api_url = "https://api.mozambiquehe.re/bridge"
        params = {"auth": self._api_key, "uid": uid, "platform": platform}
        data = await self._request_player_data(api_url, params, uid)
        return _parse_player_stats(data, platform, uid)

    async def fetch_player_stats_auto(
        self, identifier: str, platform: str | None = None, use_uid: bool = False
    ) -> tuple[ApexPlayerStats, str]:
        if platform:
            normalized = normalize_platform(platform)
            stats = await self._fetch_player(identifier, normalized, use_uid)
            return stats, normalized

        for candidate in ["PC", "PS4", "X1", "SWITCH"]:
            try:
                stats = await self._fetch_player(identifier, candidate, use_uid)
                return stats, candidate
            except PlayerNotFoundError:
                continue
        raise PlayerNotFoundError(f"Player not found: {identifier}")

    async def fetch_current_season_info(self) -> SeasonInfo:
        url = "https://apexseasons.online/"
        html = await self._request_text_with_retry(url)
        try:
            season_info = _parse_current_season(html)
        except Exception as exc:
            self._logger.error(f"解析赛季首页失败: {exc}")
            raise RuntimeError("无法解析赛季首页数据") from exc

        if season_info.season_url:
            try:
                season_html = await self._request_text_with_retry(season_info.season_url)
                _apply_season_page_overrides(season_info, season_html)
            except Exception as exc:
                self._logger.warning(f"获取赛季详情页失败: {exc}")

        return season_info

    async def _fetch_player(
        self, identifier: str, platform: str, use_uid: bool
    ) -> ApexPlayerStats:
        if use_uid:
            return await self.fetch_player_stats_by_uid(identifier, platform)
        return await self.fetch_player_stats_by_name(identifier, platform)

    async def _request_player_data(
        self, url: str, params: dict[str, Any], identifier: str
    ) -> dict[str, Any]:
        try:
            data = await self._request_with_retry(url, params)
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code if exc.response else 0
            if status in (400, 404):
                raise PlayerNotFoundError(f"Player not found: {identifier}") from exc
            raise

        if _is_player_not_found(data):
            raise PlayerNotFoundError(f"Player not found: {identifier}")
        return data

    async def _request_with_retry(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        return await self._request_with_retry_with_headers(url, params, headers=None)

    async def _request_with_retry_with_headers(
        self,
        url: str,
        params: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = self._retry_delay(attempt)
                self._logger.info(
                    f"API 请求失败，正在重试 {attempt}/{self._max_retries}，延迟 {delay}s..."
                )
                await asyncio.sleep(delay)

            try:
                self._debug_log_request("JSON", url, params=params, headers=headers)
                response = await self._client.get(url, params=params, headers=headers)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Retryable HTTP status",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data = response.json()
                self._debug_log_response("JSON", url, data, response.status_code)
                return data
            except httpx.HTTPStatusError as exc:
                last_error = exc
                self._debug_log_error("JSON", url, exc)
                status = exc.response.status_code if exc.response else 0
                retriable = status == 429 or status >= 500
                if retriable and attempt < self._max_retries:
                    continue
                raise
            except httpx.RequestError as exc:
                last_error = exc
                self._debug_log_error("JSON", url, exc)
                if attempt < self._max_retries:
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("API 请求失败")

    def _debug_log_request(
        self,
        kind: str,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        if not self._debug_enabled:
            return
        safe_params = self._sanitize_mapping(params)
        safe_headers = self._sanitize_mapping(headers)
        self._logger.info(
            f"[DEBUG] {kind} 请求 => url={url}, params={safe_params}, headers={safe_headers}"
        )

    def _debug_log_response(self, kind: str, url: str, payload: Any, status_code: int) -> None:
        if not self._debug_enabled:
            return
        preview = self._serialize_debug_payload(payload)
        self._logger.info(
            f"[DEBUG] {kind} 响应 <= url={url}, status={status_code}, payload={preview}"
        )

    def _debug_log_error(self, kind: str, url: str, exc: Exception) -> None:
        if not self._debug_enabled:
            return
        self._logger.error(f"[DEBUG] {kind} 异常 !! url={url}, error={exc}")

    @staticmethod
    def _sanitize_mapping(value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if str(key).lower() in {"auth", "authorization", "api_key", "apikey", "token"}:
                sanitized[key] = ApexApiClient._mask_secret(item)
            else:
                sanitized[key] = item
        return sanitized

    @staticmethod
    def _mask_secret(value: Any) -> str:
        text = str(value or "")
        if len(text) <= 6:
            return "*" * len(text)
        return text[:3] + "*" * (len(text) - 6) + text[-3:]

    @staticmethod
    def _serialize_debug_payload(payload: Any) -> str:
        try:
            text = (
                payload
                if isinstance(payload, str)
                else json.dumps(payload, ensure_ascii=False)
            )
        except Exception:
            text = repr(payload)
        if len(text) > 4000:
            return text[:4000] + "...(truncated)"
        return text

    async def _request_text_with_retry(self, url: str) -> str:
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            if attempt > 0:
                delay = self._retry_delay(attempt)
                self._logger.info(
                    f"页面请求失败，正在重试 {attempt}/{self._max_retries}，延迟 {delay}s..."
                )
                await asyncio.sleep(delay)

            try:
                self._debug_log_request("TEXT", url, params=None, headers=None)
                response = await self._client.get(url)
                if response.status_code == 429 or response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Retryable HTTP status",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                self._debug_log_response("TEXT", url, response.text, response.status_code)
                return response.text
            except httpx.HTTPStatusError as exc:
                last_error = exc
                self._debug_log_error("TEXT", url, exc)
                status = exc.response.status_code if exc.response else 0
                retriable = status == 429 or status >= 500
                if retriable and attempt < self._max_retries:
                    continue
                raise
            except httpx.RequestError as exc:
                last_error = exc
                self._debug_log_error("TEXT", url, exc)
                if attempt < self._max_retries:
                    continue
                raise

        if last_error:
            raise last_error
        raise RuntimeError("页面请求失败")

    @staticmethod
    def _retry_delay(attempt: int) -> int:
        return min(5, max(1, 2 ** (attempt - 1)))


def _load_external_name_map() -> dict[str, str]:
    try:
        raw = json.loads(_TRANSLATIONS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return raw if isinstance(raw, dict) else {}


_EXTERNAL_NAME_MAP = _load_external_name_map()


def translate(name: str) -> str:
    return _EXTERNAL_NAME_MAP.get(name) or NAME_MAP.get(name, name)


def translate_state(state_text: Any) -> str:
    if not state_text:
        return "离线"

    if isinstance(state_text, (int, float)):
        numeric_state = _to_int(state_text)
        state_text = {
            1: "online",
            2: "inLobby",
            3: "inMatch",
        }.get(numeric_state, "offline")
    else:
        text = str(state_text).strip()
        numeric_state = _to_int(text)
        if numeric_state is not None and text.isdigit():
            state_text = {
                1: "online",
                2: "inLobby",
                3: "inMatch",
            }.get(numeric_state, "offline")
        else:
            state_text = text

    time_info = ""
    match = re.search(r"\((\d{1,2}:\d{2})\)\s*$", state_text)
    if match:
        time_info = f" ({match.group(1)})"
        state_text = state_text[: match.start()].strip()

    translated = translate(state_text)
    if time_info:
        translated += time_info
    return translated


def is_score_drop_abnormal(old_score: int, new_score: int) -> bool:
    return old_score > 1000 and new_score < 10 and new_score < old_score


def is_likely_season_reset(old_score: int, new_score: int) -> bool:
    return new_score < old_score and (old_score - new_score) > 1000 and new_score >= 10


def normalize_platform(platform: str) -> str:
    key = platform.strip().lower()
    mapping = {
        "pc": "PC",
        "ps": "PS4",
        "ps4": "PS4",
        "ps5": "PS4",
        "playstation": "PS4",
        "xbox": "X1",
        "x1": "X1",
        "switch": "SWITCH",
        "ns": "SWITCH",
        "nintendo": "SWITCH",
    }
    return mapping.get(key, platform.strip().upper())


def _is_player_not_found(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict):
        return True
    for key in ("Error", "error", "message"):
        value = data.get(key)
        if isinstance(value, str) and "not found" in value.lower():
            return True
    global_data = data.get("global")
    if not isinstance(global_data, dict):
        return True
    name = global_data.get("name")
    return not name


def _parse_player_stats(
    data: dict[str, Any], platform: str, fallback_name: str
) -> ApexPlayerStats:
    global_data = data.get("global", {})
    realtime_data = data.get("realtime", {})
    rank_data = global_data.get("rank", {})
    legends_data = data.get("legends", {})

    is_online = _to_int(realtime_data.get("isOnline", 0)) == 1
    global_rank_percent = rank_data.get("ALStopPercentGlobal")
    if global_rank_percent is None or global_rank_percent == "":
        global_rank_percent_text = "未知"
    else:
        global_rank_percent_text = str(global_rank_percent)

    selected_legend_raw = realtime_data.get("selectedLegend", "") or ""
    selected_legend = translate(selected_legend_raw)

    legend_kills_rank: LegendKillsRank | None = None
    legend_stats_data = (
        legends_data.get("selected", {}).get("data", [])
        if isinstance(legends_data, dict)
        else []
    )
    for stat in legend_stats_data:
        if not isinstance(stat, dict):
            continue
        if stat.get("name") == "BR Kills" or stat.get("key") == "specialEvent_kills":
            rank = stat.get("rank") or {}
            top_percent = _to_float(rank.get("topPercent"))
            if top_percent is not None:
                legend_kills_rank = LegendKillsRank(
                    value=_to_int(stat.get("value", 0)) or 0,
                    global_percent=f"{top_percent:.2f}",
                )
                break

    current_state_raw = (
        realtime_data.get("currentStateAsText")
        or realtime_data.get("currentState")
        or "offline"
    )
    translated_state = translate_state(current_state_raw)
    is_in_lobby_or_match = ("大厅" in translated_state) or ("比赛" in translated_state)

    return ApexPlayerStats(
        name=global_data.get("name") or fallback_name,
        uid=str(global_data.get("uid") or ""),
        level=_to_int(global_data.get("level")) or 0,
        rank_score=_to_int(rank_data.get("rankScore")) or 0,
        rank_name=translate(rank_data.get("rankName") or "Unranked"),
        rank_div=_to_int(rank_data.get("rankDiv")) or 0,
        global_rank_percent=global_rank_percent_text,
        is_online=is_online,
        selected_legend=selected_legend,
        legend_kills_rank=legend_kills_rank,
        current_state=translated_state,
        is_in_lobby_or_match=is_in_lobby_or_match,
        platform=platform,
    )
def _to_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError):
        pass

    text = str(value).strip()
    if not text:
        return None

    cleaned = text.replace(",", "").replace("，", "")
    try:
        return int(float(cleaned))
    except (TypeError, ValueError):
        try:
            match = re.search(r"-?\d+(?:\.\d+)?", cleaned)
            if match:
                return int(float(match.group(0)))
        except (TypeError, ValueError):
            pass
    return None
def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_current_season(html: str) -> SeasonInfo:
    season_number: int | None = None
    season_name = ""
    start_date = "未知"
    end_date = "未知"
    timezone = "未知"
    update_time_hint = "未知"
    season_url = ""
    start_iso = ""
    end_iso = ""

    if html:
        jsonld_blocks = re.findall(
            r"<script\s+type=\"application/ld\+json\">(.*?)</script>",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        try:
            season_number, season_name, season_url = _extract_season_from_jsonld(
                jsonld_blocks
            )
        except Exception:
            season_number, season_name, season_url = None, "", ""
        if season_number is None or not season_name:
            match = re.search(
                r"Season\s+(\d+)\s+·\s+([^\n]+?)\s+is live now",
                html,
                re.IGNORECASE,
            )
            if not match:
                match = re.search(
                    r"Season\s+(\d+)\s+·\s+([^\n]+?)\s+Started",
                    html,
                    re.IGNORECASE,
                )
            if match:
                season_number = _to_int(match.group(1))
                season_name = match.group(2).strip()

        end_iso = _extract_countdown_target(html) or ""
        if end_iso:
            end_date = _format_iso_date(end_iso)

        date_match = re.search(
            r"Started\s+([A-Za-z]{3}\s+\d{1,2})\s+(\d{4})\s+"
            r"Ends\s+([A-Za-z]{3}\s+\d{1,2})\s+(\d{4})",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if date_match:
            start_date = f"{date_match.group(1)} {date_match.group(2)}"
            end_date = f"{date_match.group(3)} {date_match.group(4)}"

        tz_match = re.search(r"Timezone\s+·\s+([^\n]+)", html, re.IGNORECASE)
        if tz_match:
            timezone = _clean_timezone(tz_match.group(1))

        hint_match = re.search(
            r"Respawn\s+deploys\s+all\s+major\s+updates\s+at\s+([^\n\.]+)",
            html,
            re.IGNORECASE,
        )
        if hint_match:
            update_time_hint = hint_match.group(1).strip()

    return SeasonInfo(
        season_number=season_number,
        season_name=season_name,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        update_time_hint=update_time_hint,
        source="apexseasons.online",
        season_url=season_url,
        start_iso=start_iso,
        end_iso=end_iso,
    )


def _extract_season_from_jsonld(
    blocks: list[str],
) -> tuple[int | None, str, str]:
    best_number: int | None = None
    best_name = ""
    best_url = ""
    best_position = None

    for block in blocks:
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except Exception:
            continue
        candidates = []
        if isinstance(data, dict):
            candidates.append(data)
        elif isinstance(data, list):
            candidates.extend([d for d in data if isinstance(d, dict)])

        for item in candidates:
            if item.get("@type") != "ItemList":
                continue
            elements = item.get("itemListElement") or []
            for element in elements:
                if not isinstance(element, dict):
                    continue
                position = element.get("position")
                name = element.get("name", "")
                url = element.get("url", "")
                if not name:
                    continue
                num, title = _parse_season_name(name)
                if num is None:
                    continue
                if best_position is None or (position is not None and position < best_position):
                    best_position = position
                    best_number = num
                    best_name = title
                    best_url = url

    return best_number, best_name, best_url


def _parse_season_name(text: str) -> tuple[int | None, str]:
    match = re.search(r"Season\s+(\d+)\s+·\s+(.+)", text, re.IGNORECASE)
    if not match:
        return None, ""
    number = _to_int(match.group(1))
    name = match.group(2).strip()
    return number, name


def _apply_season_page_overrides(season_info: SeasonInfo, html: str) -> None:
    if not html:
        return

    start_iso, end_iso = _extract_event_dates_from_jsonld(html)
    if start_iso:
        season_info.start_iso = start_iso
        season_info.start_date = _format_iso_date(start_iso)
    if end_iso:
        season_info.end_iso = end_iso
        season_info.end_date = _format_iso_date(end_iso)

    start = _extract_date(html, "Start Date")
    end = _extract_date(html, "End Date")
    if start:
        season_info.start_date = start
    if end:
        season_info.end_date = end

    if season_info.start_date == "未知" or season_info.end_date == "未知":
        match = re.search(
            r"Started\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}).*?"
            r"Ends\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
            html,
            re.IGNORECASE | re.DOTALL,
        )
        if match:
            season_info.start_date = match.group(1).strip()
            season_info.end_date = match.group(2).strip()

    tz_match = re.search(r"Timezone\s*:?\s*([^\n<]+)", html, re.IGNORECASE)
    if tz_match:
        season_info.timezone = _clean_timezone(tz_match.group(1))
    elif start_iso and start_iso.endswith("Z"):
        season_info.timezone = "UTC"


def _extract_event_dates_from_jsonld(html: str) -> tuple[str | None, str | None]:
    import json

    blocks = re.findall(
        r"<script\s+type=\"application/ld\+json\">(.*?)</script>",
        html,
        re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        try:
            data = json.loads(block)
        except Exception:
            continue
        items = []
        if isinstance(data, dict):
            items.append(data)
        elif isinstance(data, list):
            items.extend([d for d in data if isinstance(d, dict)])

        for item in items:
            if item.get("@type") == "Event":
                start = item.get("startDate")
                end = item.get("endDate")
                return start, end
    return None, None


def _format_iso_date(value: str) -> str:
    try:
        if value.endswith("Z"):
            value = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value)
        if dt.tzinfo:
            return dt.strftime("%Y-%m-%d %H:%M %Z").strip()
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return value


def _clean_timezone(value: str) -> str:
    tz = value.strip()
    tz = tz.replace("<!-- -->", " ")
    tz = tz.lstrip("·").strip()
    tz = re.sub(r"\s+", " ", tz).strip()
    return tz or "未知"


def _extract_countdown_target(html: str) -> str | None:
    match = re.search(
        r"targetDate\"\s*:\s*\[0\s*,\s*\"([^\"]+)\"\]",
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    match = re.search(
        r"targetDate\"\s*:\s*\"([^\"]+)\"",
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def _extract_date(html: str, label: str) -> str | None:
    match = re.search(
        rf"{re.escape(label)}\s*:?\s*([A-Za-z]{{3,9}}\s+\d{{1,2}},?\s+\d{{4}})",
        html,
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None
