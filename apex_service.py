from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

if __package__:
    from .utils import SHANGHAI_TZ
else:
    from utils import SHANGHAI_TZ


_TRANSLATIONS_FILE = Path(__file__).with_name("translations.json")

APEX_API_PORTAL_URL = "https://portal.apexlegendsapi.com/"
APEX_API_VERIFY_URL = "https://portal.apexlegendsapi.com/discord-auth"
APEX_SEASONS_HOME_URL = "https://apexseasons.online/"
ESPORTSTALES_SEASONS_URL = "https://www.esportstales.com/apex-legends/season-end-date"

JSONLD_SCRIPT_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
SEASON_LIVE_RE = re.compile(
    r"Season\s+(\d+)\s*[·•:：\-–—]?\s*([^\n]+?)\s+is\s+live\s+now",
    re.IGNORECASE,
)
SEASON_STARTED_RE = re.compile(
    r"Season\s+(\d+)\s*[·•:：\-–—]?\s*([^\n]+?)\s+Started",
    re.IGNORECASE,
)
SEASON_NAME_RE = re.compile(r"Season\s+(\d+)\s*[·•:：\-–—]?\s*(.+)", re.IGNORECASE)
DATE_RANGE_RE = re.compile(
    r"Started\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}).*?"
    r"Ends\s+([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4})",
    re.IGNORECASE | re.DOTALL,
)
TIMEZONE_RE = re.compile(r"Timezone\s*[·•:：]?\s*([^\n<]+)", re.IGNORECASE)
UPDATE_HINT_RE = re.compile(
    r"Respawn\s+deploys\s+all\s+major\s+(?:Apex\s+Legends\s+)?updates\s+at\s+([^.]+)",
    re.IGNORECASE,
)
COUNTDOWN_ARRAY_RE = re.compile(
    r'targetDate"\s*:\s*\[0\s*,\s*"([^"]+)"\]',
    re.IGNORECASE,
)
COUNTDOWN_STRING_RE = re.compile(
    r'targetDate"\s*:\s*"([^"]+)"',
    re.IGNORECASE,
)
SEASON_SECTION_RE = re.compile(
    r"<h3[^>]*>\s*Season\s+(?P<number>\d+):\s*(?P<name>[^<]+)</h3>(?P<body>.*?)(?=<h3[^>]*>\s*Season\s+\d+:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
SPLIT_LINE_RE = re.compile(
    r"Season\s+(?P<number>\d+)\s+Split\s+(?P<split>\d+)\s*:\s*from\s+(?P<start>.+?)\s+to\s+(?P<end>.+?)(?:\.|$)",
    re.IGNORECASE,
)
MONTH_DAY_RE = re.compile(r"([A-Za-z]{3,9})\s+(\d{1,2})(?:,\s*(\d{4}))?")
FUZZY_DATE_TOKEN_RE = re.compile(r"/|around|approx|estimate|estimated", re.IGNORECASE)


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
    "Catalyst": "催化剂",
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
    status_text: str = "未知"
    current_split_label: str = ""
    current_split_index: int | None = None
    next_transition_label: str = ""
    next_transition_iso: str = ""
    split_source: str = ""
    split_note: str = ""
    supports_ranked_splits: bool = False
    splits: list["SeasonSplitInfo"] = field(default_factory=list)


@dataclass
class SeasonSplitInfo:
    index: int
    label: str
    stage_name: str
    start_iso: str
    end_iso: str
    start_date: str
    end_date: str
    source: str
    exact: bool = True
    note: str = ""


@dataclass
class _SeasonReference:
    season_number: int | None
    season_name: str
    season_url: str
    position: int = 0


@dataclass
class _SplitTextRange:
    index: int
    start_text: str
    end_text: str


@dataclass
class _SplitBoundaryResolution:
    boundary: datetime
    source: str
    note: str
    exact: bool


class PlayerNotFoundError(Exception):
    pass


class ApexApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        user_message: str | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.user_message = user_message or message
        self.status_code = status_code


class ApiAuthenticationError(ApexApiError):
    pass


class ApiAccountVerificationRequiredError(ApiAuthenticationError):
    pass


class ApiRateLimitError(ApexApiError):
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
        self._season_cache_ttl_seconds = 30 * 60
        self._season_cache: dict[str, tuple[float, SeasonInfo]] = {}
        self._season_lock = asyncio.Lock()
        self._split_index_cache: tuple[float, dict[int, list[_SplitTextRange]]] | None = None
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

    async def fetch_season_info(self, season_number: int | None = None) -> SeasonInfo:
        cache_key = f"season:{season_number if season_number is not None else 'current'}"
        cached = self._get_cached_season(cache_key)
        if cached is not None:
            return cached

        async with self._season_lock:
            cached = self._get_cached_season(cache_key)
            if cached is not None:
                return cached

            home_html = await self._request_text_with_retry(APEX_SEASONS_HOME_URL)
            references = _extract_season_references(home_html)
            if not references:
                raise RuntimeError("无法从赛季首页提取赛季列表")

            if season_number is None:
                target = references[0]
            else:
                target = next(
                    (
                        item
                        for item in references
                        if item.season_number == season_number
                    ),
                    None,
                )
                if target is None:
                    raise RuntimeError(f"未找到 S{season_number} 的赛季数据")

            detail_html = ""
            if target.season_url:
                detail_html = await self._request_text_with_retry(target.season_url)

            season_info = _build_season_info(
                reference=target,
                home_html=home_html,
                detail_html=detail_html,
            )

            split_index = await self._get_split_index()
            _apply_ranked_split_details(season_info, split_index)

            self._season_cache[cache_key] = (time.monotonic(), season_info)
            return season_info

    async def fetch_current_season_info(self) -> SeasonInfo:
        return await self.fetch_season_info()

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
            error_text = _extract_response_error_message(exc.response)
            if status in (400, 404) or "not found" in error_text.lower():
                raise PlayerNotFoundError(f"Player not found: {identifier}") from exc
            raise
        except ApexApiError:
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
                if response.status_code == 429:
                    error_text = _extract_response_error_message(response)
                    if _requires_api_verification(error_text):
                        raise ApiAccountVerificationRequiredError(
                            error_text or "API Key 尚未完成账号验证",
                            user_message=(
                                "当前 Apex API Key 尚未完成账号验证。"
                                f"请前往 {APEX_API_VERIFY_URL} 绑定 Discord 后再使用。"
                            ),
                            status_code=response.status_code,
                        )
                    if attempt < self._max_retries:
                        last_error = ApiRateLimitError(
                            error_text or "Apex API 请求过于频繁",
                            user_message="Apex API 当前限流，请稍后再试。",
                            status_code=response.status_code,
                        )
                        continue
                    raise ApiRateLimitError(
                        error_text or "Apex API 请求过于频繁",
                        user_message="Apex API 当前限流，请稍后再试。",
                        status_code=response.status_code,
                    )
                if response.status_code in (401, 403):
                    raise ApiAuthenticationError(
                        _extract_response_error_message(response) or "API Key 无效或权限不足",
                        user_message=(
                            "Apex API Key 无效、已过期或权限不足，请检查配置后重试。"
                        ),
                        status_code=response.status_code,
                    )
                if response.status_code >= 500:
                    raise httpx.HTTPStatusError(
                        "Retryable HTTP status",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                data = response.json()
                api_error = _build_api_error_from_payload(data)
                if api_error is not None:
                    raise api_error
                self._debug_log_response("JSON", url, data, response.status_code)
                return data
            except ApexApiError:
                raise
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

    def _get_cached_season(self, cache_key: str) -> SeasonInfo | None:
        cached = self._season_cache.get(cache_key)
        if not cached:
            return None
        saved_at, season_info = cached
        if time.monotonic() - saved_at > self._season_cache_ttl_seconds:
            self._season_cache.pop(cache_key, None)
            return None
        return season_info

    async def _get_split_index(self) -> dict[int, list[_SplitTextRange]]:
        cached = self._split_index_cache
        if cached and (time.monotonic() - cached[0]) <= self._season_cache_ttl_seconds:
            return cached[1]

        html = await self._request_text_with_retry(ESPORTSTALES_SEASONS_URL)
        index = _parse_split_index_from_esportstales(html)
        self._split_index_cache = (time.monotonic(), index)
        return index

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


def _extract_jsonld_blocks(html: str) -> list[str]:
    return JSONLD_SCRIPT_RE.findall(html or "")


def _extract_jsonld_items(html: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for block in _extract_jsonld_blocks(html):
        try:
            parsed = json.loads(block.strip())
        except Exception:
            continue
        if isinstance(parsed, dict):
            items.append(parsed)
        elif isinstance(parsed, list):
            items.extend(item for item in parsed if isinstance(item, dict))
    return items


def _extract_season_references(home_html: str) -> list[_SeasonReference]:
    references: list[_SeasonReference] = []
    for item in _extract_jsonld_items(home_html):
        if item.get("@type") != "ItemList":
            continue
        for element in item.get("itemListElement") or []:
            if not isinstance(element, dict):
                continue
            number, name = _parse_season_name(str(element.get("name", "")))
            if number is None or not name:
                continue
            position = _to_int(element.get("position")) or 0
            season_url = urljoin(APEX_SEASONS_HOME_URL, str(element.get("url", "")))
            references.append(
                _SeasonReference(
                    season_number=number,
                    season_name=name,
                    season_url=season_url,
                    position=position,
                )
            )

    if references:
        deduped: dict[int, _SeasonReference] = {}
        for item in sorted(
            references,
            key=lambda value: (value.position or 9999, -(value.season_number or 0)),
        ):
            if item.season_number is None:
                continue
            deduped.setdefault(item.season_number, item)
        return list(deduped.values())

    number, name, season_url = _extract_current_season_identity(home_html)
    if number is None:
        return []
    return [
        _SeasonReference(
            season_number=number,
            season_name=name,
            season_url=season_url,
            position=1,
        )
    ]


def _extract_current_season_identity(html: str) -> tuple[int | None, str, str]:
    plain_text = _html_to_text(html)
    match = SEASON_LIVE_RE.search(plain_text) or SEASON_STARTED_RE.search(plain_text)
    if not match:
        return None, "", ""
    number = _to_int(match.group(1))
    name = match.group(2).strip()
    return number, name, ""


def _parse_season_name(text: str) -> tuple[int | None, str]:
    match = SEASON_NAME_RE.search(text)
    if not match:
        return None, ""
    number = _to_int(match.group(1))
    name = unescape(match.group(2)).strip()
    return number, name


def _build_season_info(
    reference: _SeasonReference,
    home_html: str,
    detail_html: str,
) -> SeasonInfo:
    detail = detail_html or home_html
    start_iso, end_iso = _extract_event_dates_from_jsonld(detail)
    if not end_iso:
        end_iso = _extract_countdown_target(detail) or _extract_countdown_target(home_html) or ""
    timezone_text = _extract_timezone(home_html) or _extract_timezone(detail)
    update_time_hint = _extract_update_time_hint(home_html)

    start_date = _format_iso_date(start_iso) if start_iso else "未知"
    end_date = _format_iso_date(end_iso) if end_iso else "未知"
    if start_date == "未知" or end_date == "未知":
        fallback_start, fallback_end = _extract_date_range(detail)
        if fallback_start and start_date == "未知":
            start_date = fallback_start
        if fallback_end and end_date == "未知":
            end_date = fallback_end

    season_info = SeasonInfo(
        season_number=reference.season_number,
        season_name=reference.season_name,
        start_date=start_date,
        end_date=end_date,
        timezone=timezone_text or ("UTC" if start_iso.endswith("Z") else "未知"),
        update_time_hint=update_time_hint or "未知",
        source="apexseasons.online",
        season_url=reference.season_url,
        start_iso=start_iso,
        end_iso=end_iso,
        status_text=_resolve_season_status(start_iso, end_iso),
    )
    return season_info


def _apply_ranked_split_details(
    season_info: SeasonInfo,
    split_index: dict[int, list[_SplitTextRange]],
) -> None:
    season_info.status_text = _resolve_season_status(
        season_info.start_iso,
        season_info.end_iso,
    )
    if not _season_uses_ranked_splits(season_info.season_number):
        season_info.supports_ranked_splits = False
        season_info.split_source = "赛制规则"
        season_info.split_note = "该赛季没有上下半赛季排位重置。"
        return

    season_start = _parse_iso_datetime(season_info.start_iso)
    season_end = _parse_iso_datetime(season_info.end_iso)
    if season_start is None or season_end is None or season_end <= season_start:
        season_info.supports_ranked_splits = False
        season_info.split_source = "未知"
        season_info.split_note = "缺少可信的赛季起止时间，无法计算上下半赛季。"
        return

    boundary = _resolve_split_boundary(
        season_info.season_number,
        split_index,
        season_start,
        season_end,
    )
    if boundary is not None:
        boundary_dt = boundary.boundary
        boundary_note = boundary.note
        split_source = boundary.source
        exact = boundary.exact
    else:
        boundary_dt = _infer_split_boundary(season_start, season_end)
        if boundary_dt is None:
            season_info.supports_ranked_splits = False
            season_info.split_source = "未知"
            season_info.split_note = "无法推导上下半赛季的分界时间。"
            return
        boundary_note = (
            "未找到公开 split 精确时间，已按赛季中点附近的北京时间周三凌晨 1 点推导。"
        )
        split_source = "推导"
        exact = False

    season_info.supports_ranked_splits = True
    season_info.split_source = split_source
    season_info.split_note = boundary_note
    season_info.splits = [
        SeasonSplitInfo(
            index=1,
            label="Split 1",
            stage_name="上半赛季",
            start_iso=_to_iso_datetime(season_start),
            end_iso=_to_iso_datetime(boundary_dt),
            start_date=_format_iso_date(_to_iso_datetime(season_start)),
            end_date=_format_iso_date(_to_iso_datetime(boundary_dt)),
            source=split_source,
            exact=exact,
            note=boundary_note,
        ),
        SeasonSplitInfo(
            index=2,
            label="Split 2",
            stage_name="下半赛季",
            start_iso=_to_iso_datetime(boundary_dt),
            end_iso=_to_iso_datetime(season_end),
            start_date=_format_iso_date(_to_iso_datetime(boundary_dt)),
            end_date=_format_iso_date(_to_iso_datetime(season_end)),
            source=split_source,
            exact=exact,
            note=boundary_note,
        ),
    ]
    _update_current_split_state(season_info)


def _extract_event_dates_from_jsonld(html: str) -> tuple[str | None, str | None]:
    for item in _extract_jsonld_items(html):
        if item.get("@type") != "Event":
            continue
        start = item.get("startDate")
        end = item.get("endDate")
        if isinstance(start, str) or isinstance(end, str):
            return str(start or ""), str(end or "")
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
    tz = unescape(value).strip()
    tz = tz.replace("<!-- -->", " ")
    tz = tz.lstrip("·").strip()
    tz = re.sub(r"\s+", " ", tz).strip()
    return tz or "未知"


def _extract_countdown_target(html: str) -> str | None:
    match = COUNTDOWN_ARRAY_RE.search(html)
    if match:
        return match.group(1).strip()
    match = COUNTDOWN_STRING_RE.search(html)
    if match:
        return match.group(1).strip()
    return None


def _extract_date(html: str, label: str) -> str | None:
    match = re.search(
        rf"{re.escape(label)}\s*:?\s*([A-Za-z]{{3,9}}\s+\d{{1,2}},?\s+\d{{4}})",
        _html_to_text(html),
        re.IGNORECASE,
    )
    if match:
        return match.group(1).strip()
    return None


def _extract_timezone(html: str) -> str:
    plain_text = _html_to_text(html)
    match = TIMEZONE_RE.search(plain_text)
    if not match:
        return "未知"
    return _clean_timezone(match.group(1))


def _extract_update_time_hint(html: str) -> str:
    plain_text = _html_to_text(html)
    match = UPDATE_HINT_RE.search(plain_text)
    if not match:
        return "未知"
    return re.sub(r"\s+", " ", match.group(1)).strip()


def _extract_date_range(html: str) -> tuple[str, str]:
    plain_text = _html_to_text(html)
    match = DATE_RANGE_RE.search(plain_text)
    if not match:
        return "", ""
    return match.group(1).strip(), match.group(2).strip()


def _html_to_text(html: str) -> str:
    text = re.sub(r"(?is)<script.*?</script>", " ", html or "")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|li|h\d|tr|td|th|ul|ol|section|div)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s+", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _parse_split_index_from_esportstales(html: str) -> dict[int, list[_SplitTextRange]]:
    index: dict[int, list[_SplitTextRange]] = {}
    for section in SEASON_SECTION_RE.finditer(html or ""):
        season_number = _to_int(section.group("number"))
        if season_number is None:
            continue
        body_text = _html_to_text(section.group("body"))
        ranges: list[_SplitTextRange] = []
        for match in SPLIT_LINE_RE.finditer(body_text):
            if _to_int(match.group("number")) != season_number:
                continue
            split_number = _to_int(match.group("split"))
            if split_number not in {1, 2}:
                continue
            ranges.append(
                _SplitTextRange(
                    index=split_number,
                    start_text=match.group("start").strip(),
                    end_text=match.group("end").strip(),
                )
            )
        if ranges:
            index[season_number] = sorted(ranges, key=lambda item: item.index)
    return index


def _resolve_split_boundary(
    season_number: int | None,
    split_index: dict[int, list[_SplitTextRange]],
    season_start: datetime,
    season_end: datetime,
) -> _SplitBoundaryResolution | None:
    if season_number is None:
        return None
    ranges = split_index.get(season_number)
    if not ranges:
        return None

    midpoint = season_start + ((season_end - season_start) / 2)
    exact_candidates: list[datetime] = []
    date_only_candidates: list[datetime] = []
    for item in ranges:
        if item.index == 1:
            value = _resolve_partial_date(item.end_text, season_start, season_end, midpoint)
        else:
            value = _resolve_partial_date(item.start_text, season_start, season_end, midpoint)
        if value is None:
            continue
        if value.tzinfo is not None:
            if season_start < value < season_end:
                exact_candidates.append(value)
            continue

        inferred = _infer_split_boundary_from_source_date(value, season_start, season_end)
        if inferred is not None and season_start < inferred < season_end:
            date_only_candidates.append(inferred)

    if exact_candidates:
        boundary = min(
            exact_candidates,
            key=lambda value: abs((value - midpoint).total_seconds()),
        )
        return _SplitBoundaryResolution(
            boundary=boundary,
            source="esportstales.com",
            note="来自 Esports Tales 的公开 split 时间表。",
            exact=True,
        )

    if not date_only_candidates:
        return None

    boundary = min(
        date_only_candidates,
        key=lambda value: abs((value - midpoint).total_seconds()),
    )
    return _SplitBoundaryResolution(
        boundary=boundary,
        source="esportstales.com",
        note=(
            "日期来自 Esports Tales；网站未提供精确时刻，已按北京时间周三凌晨 1 点推导。"
        ),
        exact=False,
    )


def _resolve_partial_date(
    text: str,
    season_start: datetime,
    season_end: datetime,
    anchor: datetime,
) -> datetime | None:
    if not text or FUZZY_DATE_TOKEN_RE.search(text):
        return None
    match = MONTH_DAY_RE.search(text)
    if not match:
        return None

    month = _month_name_to_number(match.group(1))
    if month is None:
        return None
    day = _to_int(match.group(2))
    explicit_year = _to_int(match.group(3))
    if day is None:
        return None

    years = [explicit_year] if explicit_year is not None else list(
        {
            season_start.year - 1,
            season_start.year,
            season_end.year,
            season_end.year + 1,
        }
    )

    season_start_date = season_start.astimezone(SHANGHAI_TZ).date()
    season_end_date = season_end.astimezone(SHANGHAI_TZ).date()
    anchor_local = anchor.astimezone(SHANGHAI_TZ).replace(tzinfo=None)

    candidates: list[datetime] = []
    for year in years:
        if year is None:
            continue
        try:
            candidate = datetime(year, month, day)
        except ValueError:
            continue
        if season_start_date - timedelta(days=14) <= candidate.date() <= season_end_date + timedelta(days=14):
            candidates.append(candidate)

    if not candidates:
        return None
    return min(candidates, key=lambda value: abs((value - anchor_local).total_seconds()))


def _infer_split_boundary(season_start: datetime, season_end: datetime) -> datetime | None:
    if season_end <= season_start:
        return None
    midpoint = season_start + ((season_end - season_start) / 2)
    return _nearest_beijing_update_boundary(midpoint, season_start, season_end)


def _infer_split_boundary_from_source_date(
    source_date: datetime,
    season_start: datetime,
    season_end: datetime,
) -> datetime | None:
    anchor = datetime(
        source_date.year,
        source_date.month,
        source_date.day,
        0,
        0,
        tzinfo=SHANGHAI_TZ,
    )
    return _nearest_beijing_update_boundary(anchor, season_start, season_end)


def _nearest_beijing_update_boundary(
    anchor: datetime,
    season_start: datetime,
    season_end: datetime,
) -> datetime | None:
    if season_end <= season_start:
        return None

    anchor_local = anchor.astimezone(SHANGHAI_TZ)
    season_start_local = season_start.astimezone(SHANGHAI_TZ)
    season_end_local = season_end.astimezone(SHANGHAI_TZ)

    start_date = season_start_local.date() - timedelta(days=7)
    end_date = season_end_local.date() + timedelta(days=7)
    total_days = (end_date - start_date).days
    candidates: list[datetime] = []

    for offset in range(total_days + 1):
        day = start_date + timedelta(days=offset)
        if day.weekday() != 2:
            continue
        candidate_local = datetime(
            day.year,
            day.month,
            day.day,
            1,
            0,
            tzinfo=SHANGHAI_TZ,
        )
        candidate_utc = candidate_local.astimezone(timezone.utc)
        if season_start < candidate_utc < season_end:
            candidates.append(candidate_utc)

    if not candidates:
        return None

    return min(
        candidates,
        key=lambda value: abs((value.astimezone(SHANGHAI_TZ) - anchor_local).total_seconds()),
    )


def _season_uses_ranked_splits(season_number: int | None) -> bool:
    if season_number is None:
        return True
    if season_number < 4:
        return False
    if 17 <= season_number <= 19:
        return False
    return True


def _resolve_season_status(start_iso: str, end_iso: str) -> str:
    now = datetime.now(timezone.utc)
    start = _parse_iso_datetime(start_iso)
    end = _parse_iso_datetime(end_iso)
    if start is not None and now < start:
        return "未开始"
    if end is not None and now >= end:
        return "已结束"
    if start is not None or end is not None:
        return "进行中"
    return "未知"


def _update_current_split_state(season_info: SeasonInfo) -> None:
    if not season_info.splits:
        if not season_info.supports_ranked_splits:
            season_info.current_split_label = ""
        season_info.current_split_index = None
        season_info.next_transition_label = ""
        season_info.next_transition_iso = ""
        return

    now = datetime.now(timezone.utc)
    split_one, split_two = season_info.splits
    split_one_start = _parse_iso_datetime(split_one.start_iso)
    split_one_end = _parse_iso_datetime(split_one.end_iso)
    split_two_end = _parse_iso_datetime(split_two.end_iso)
    if not split_one_start or not split_one_end or not split_two_end:
        return

    if now < split_one_start:
        season_info.current_split_label = "未开始"
        season_info.current_split_index = None
        season_info.next_transition_label = "上半赛季开始"
        season_info.next_transition_iso = split_one.start_iso
        return
    if now < split_one_end:
        season_info.current_split_label = split_one.stage_name
        season_info.current_split_index = split_one.index
        season_info.next_transition_label = "下半赛季开始"
        season_info.next_transition_iso = split_one.end_iso
        return
    if now < split_two_end:
        season_info.current_split_label = split_two.stage_name
        season_info.current_split_index = split_two.index
        season_info.next_transition_label = "赛季结束"
        season_info.next_transition_iso = split_two.end_iso
        return

    season_info.current_split_label = "赛季已结束"
    season_info.current_split_index = None
    season_info.next_transition_label = ""
    season_info.next_transition_iso = ""


def _parse_iso_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _to_iso_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _month_name_to_number(value: str) -> int | None:
    try:
        return datetime.strptime(value[:3], "%b").month
    except Exception:
        return None


def _extract_response_error_message(response: httpx.Response | None) -> str:
    if response is None:
        return ""

    try:
        payload = response.json()
    except Exception:
        payload = None

    if isinstance(payload, dict):
        for key in ("Error", "error", "message", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()

    text = response.text or ""
    return _html_to_text(text)[:500]


def _requires_api_verification(message: str) -> bool:
    lowered = (message or "").lower()
    return (
        "verify your api account" in lowered
        or ("discord" in lowered and "verify" in lowered)
        or "discord-auth" in lowered
    )


def _build_api_error_from_payload(data: Any) -> ApexApiError | None:
    if not isinstance(data, dict):
        return None
    message = ""
    for key in ("Error", "error", "message", "detail"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            message = value.strip()
            break
    if not message:
        return None
    lowered = message.lower()
    if _requires_api_verification(message):
        return ApiAccountVerificationRequiredError(
            message,
            user_message=(
                "当前 Apex API Key 尚未完成账号验证。"
                f"请前往 {APEX_API_VERIFY_URL} 绑定 Discord 后再使用。"
            ),
        )
    if any(token in lowered for token in {"invalid token", "unauthorized", "forbidden"}):
        return ApiAuthenticationError(
            message,
            user_message="Apex API Key 无效、已过期或权限不足，请检查配置后重试。",
        )
    if any(token in lowered for token in {"too many requests", "rate limit"}):
        return ApiRateLimitError(
            message,
            user_message="Apex API 当前限流，请稍后再试。",
        )
    return None
