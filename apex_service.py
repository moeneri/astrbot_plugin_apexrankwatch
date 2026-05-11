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
APEX_STATUS_SEASON_COUNTDOWN_URL = "https://apexlegendsstatus.com/new-season-countdown"
APEX_STATUS_RANKED_MAP_URL = "https://apexlegendsstatus.com/current-map/battle_royale/ranked"
APEX_STATUS_PUBS_MAP_URL = "https://apexlegendsstatus.com/current-map/battle_royale/pubs"
APEX_STATUS_SEASON_DURATION_DAYS = 91
MAX_DAILY_MAP_CALIBRATION_OFFSET_SECS = 12 * 60 * 60
SEASON_MAP_POOL_LOCK_BEFORE_END_SECS = 2 * 60 * 60
SEASON_MAP_POOL_LOCK_AFTER_END_SECS = 12 * 60 * 60

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
APEX_STATUS_START_TIME_RE = re.compile(r"\bstartTime\s*=\s*(\d{9,12})\b", re.IGNORECASE)
APEX_STATUS_SEASON_TITLE_RE = re.compile(
    r"Countdown\s+to\s+Season\s+(\d+)(?:\s*:\s*([^\"<\n\r]+))?",
    re.IGNORECASE,
)
MAP_SCHEDULE_ENTRY_RE = re.compile(
    r"<h3[^>]*>(?P<map>.*?)</h3>.*?"
    r"From\s*<span\s+data-tz=[\"'](?P<start>\d+)[\"'][^>]*>.*?</span>"
    r"\s*to\s*<span\s+data-tz=[\"'](?P<end>\d+)[\"'][^>]*>.*?</span>",
    re.IGNORECASE | re.DOTALL,
)


NAME_MAP: dict[str, str] = {
    # 段位
    "Unranked": "菜鸟",
    "Bronze": "青铜",
    "Silver": "白银",
    "Gold": "黄金",
    "Platinum": "白金",
    "Diamond": "钻石",
    "Master": "大师",
    "Apex Predator": "Apex 猎杀者",
    # 状态
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
    # 英雄
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
    "Axle": "艾克赛尔",
    # 地图
    "Broken Moon": "破碎月球",
    "Kings Canyon": "诸王峡谷",
    "Olympus": "奥林匹斯",
    "Storm Point": "风暴点",
    "World's Edge": "世界尽头",
    "Worlds Edge": "世界尽头",
    "E-District": "E-District",
    # 统计项
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
class MapRotationEntry:
    map_name: str
    map_name_zh: str
    start_timestamp: int
    end_timestamp: int
    readable_start: str
    readable_end: str
    duration_secs: int
    duration_minutes: int
    asset: str
    code: str
    remaining_secs: int | None = None
    remaining_mins: int | None = None
    remaining_timer: str = ""


@dataclass
class MapRotationMode:
    current: MapRotationEntry | None = None
    next: MapRotationEntry | None = None


@dataclass
class MapRotationInfo:
    battle_royale: MapRotationMode = field(default_factory=MapRotationMode)
    ranked: MapRotationMode = field(default_factory=MapRotationMode)
    ltm: MapRotationMode = field(default_factory=MapRotationMode)


@dataclass
class MapScheduleEntry:
    map_name: str
    map_name_zh: str
    start_timestamp: int
    end_timestamp: int
    readable_start: str
    readable_end: str
    duration_secs: int
    asset: str = ""
    code: str = ""
    source: str = "web"


@dataclass
class DailyMapPoolState:
    season_key: str = ""
    season_end_iso: str = ""
    status: str = "learning"
    cycle: list[str] = field(default_factory=list)
    last_current: str = ""
    last_next: str = ""
    last_current_start: int = 0
    updated_at: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "season_key": self.season_key,
            "season_end_iso": self.season_end_iso,
            "status": self.status,
            "cycle": list(self.cycle),
            "last_current": self.last_current,
            "last_next": self.last_next,
            "last_current_start": self.last_current_start,
            "updated_at": self.updated_at,
            "reason": self.reason,
        }

    @staticmethod
    def from_dict(data: Any) -> "DailyMapPoolState":
        if not isinstance(data, dict):
            return DailyMapPoolState()
        cycle_raw = data.get("cycle", [])
        cycle = [
            str(item).strip()
            for item in cycle_raw
            if str(item).strip()
        ] if isinstance(cycle_raw, list) else []
        status = str(data.get("status", "learning") or "learning").strip().lower()
        if status not in {"learning", "confirmed"}:
            status = "learning"
        return DailyMapPoolState(
            season_key=str(data.get("season_key", "") or ""),
            season_end_iso=str(data.get("season_end_iso", "") or ""),
            status=status,
            cycle=cycle,
            last_current=str(data.get("last_current", "") or ""),
            last_next=str(data.get("last_next", "") or ""),
            last_current_start=_to_int(data.get("last_current_start")) or 0,
            updated_at=_to_int(data.get("updated_at")) or 0,
            reason=str(data.get("reason", "") or ""),
        )


@dataclass
class DailyMapScheduleInfo:
    mode: str
    title: str
    date_label: str
    generated_at: str
    source_url: str
    source_note: str
    entries: list[MapScheduleEntry] = field(default_factory=list)
    pool_state: DailyMapPoolState | None = None


@dataclass
class PredatorPlatformStats:
    platform: str
    threshold_rp: int
    found_rank: int
    uid: str
    update_timestamp: int
    masters_and_preds: int


@dataclass
class PredatorInfo:
    platforms: dict[str, PredatorPlatformStats] = field(default_factory=dict)


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
    _SECRET_DEBUG_KEYS = {
        "auth",
        "authorization",
        "api_key",
        "apikey",
        "token",
        "secret",
        "password",
        "cookie",
    }
    _PRIVATE_DEBUG_KEYS = {
        "uid",
        "uuid",
        "user_id",
        "group_id",
        "player",
        "player_name",
        "name",
        "display_name",
        "displayname",
    }
    _MAX_DEBUG_PAYLOAD_CHARS = 4000

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
        self._live_cache_ttl_seconds = 60
        self._map_rotation_cache: tuple[float, MapRotationInfo] | None = None
        self._map_rotation_lock = asyncio.Lock()
        self._daily_map_cache_ttl_seconds = 10 * 60
        self._daily_map_cache: dict[str, tuple[float, DailyMapScheduleInfo]] = {}
        self._daily_map_lock = asyncio.Lock()
        self._predator_cache: tuple[float, PredatorInfo] | None = None
        self._predator_lock = asyncio.Lock()
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

    async def fetch_map_rotation_info(self) -> MapRotationInfo:
        cached = self._get_cached_map_rotation()
        if cached is not None:
            return cached

        async with self._map_rotation_lock:
            cached = self._get_cached_map_rotation()
            if cached is not None:
                return cached

            api_url = "https://api.mozambiquehe.re/maprotation"
            params = {"auth": self._api_key, "version": 2}
            data = await self._request_with_retry(api_url, params)
            rotation_info = _parse_map_rotation_info(data)
            self._map_rotation_cache = (time.monotonic(), rotation_info)
            return rotation_info

    async def fetch_daily_map_schedule(
        self,
        mode: str = "ranked",
        pool_state: DailyMapPoolState | None = None,
        season_info: SeasonInfo | None = None,
    ) -> DailyMapScheduleInfo:
        normalized_mode = _normalize_daily_map_mode(mode)
        now = datetime.now(SHANGHAI_TZ)
        use_pool_learning = normalized_mode == "ranked" and pool_state is not None
        cache_key = f"{normalized_mode}:{now:%Y-%m-%d}"
        cached = None if use_pool_learning else self._get_cached_daily_map_schedule(cache_key)
        if cached is not None:
            return cached

        async with self._daily_map_lock:
            cached = None if use_pool_learning else self._get_cached_daily_map_schedule(cache_key)
            if cached is not None:
                return cached

            rotation_info = await self.fetch_map_rotation_info()
            rotation_mode = (
                rotation_info.battle_royale
                if normalized_mode == "battle_royale"
                else rotation_info.ranked
            )
            source_url = _daily_map_schedule_url(normalized_mode)
            updated_pool_state: DailyMapPoolState | None = None
            if use_pool_learning:
                updated_pool_state = update_daily_map_pool_state(
                    pool_state,
                    rotation_mode.current,
                    rotation_mode.next,
                    season_info,
                    now,
                )
                daily_entries, source_note = build_daily_map_entries_from_pool_state(
                    updated_pool_state,
                    rotation_mode.current,
                    rotation_mode.next,
                )
            else:
                html = await self._request_text_with_retry(source_url)
                raw_entries = parse_map_schedule_page(html)
                daily_entries, source_note = build_daily_map_entries_from_api(
                    raw_entries,
                    now,
                    current=rotation_mode.current,
                    next_entry=rotation_mode.next,
                )
            schedule = DailyMapScheduleInfo(
                mode=normalized_mode,
                title=_daily_map_schedule_title(normalized_mode),
                date_label=f"{now:%Y-%m-%d}",
                generated_at=f"{now:%Y-%m-%d %H:%M:%S}",
                source_url=source_url,
                source_note=source_note,
                entries=daily_entries,
                pool_state=updated_pool_state,
            )
            if not use_pool_learning:
                self._daily_map_cache[cache_key] = (time.monotonic(), schedule)
            return schedule

    async def fetch_predator_info(self) -> PredatorInfo:
        cached = self._get_cached_predator()
        if cached is not None:
            return cached

        async with self._predator_lock:
            cached = self._get_cached_predator()
            if cached is not None:
                return cached

            api_url = "https://api.mozambiquehe.re/predator"
            params = {"auth": self._api_key}
            data = await self._request_with_retry(api_url, params)
            predator_info = _parse_predator_info(data)
            self._predator_cache = (time.monotonic(), predator_info)
            return predator_info

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
        if season_number is None:
            return await self.fetch_current_season_info()

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
        cache_key = "season:current"
        cached = self._get_cached_season(cache_key)
        if cached is not None:
            return cached

        async with self._season_lock:
            cached = self._get_cached_season(cache_key)
            if cached is not None:
                return cached

            html = await self._request_text_with_retry(APEX_STATUS_SEASON_COUNTDOWN_URL)
            season_info = _parse_apex_status_current_season_info(html)
            self._season_cache[cache_key] = (time.monotonic(), season_info)
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
        safe_error = self._sanitize_debug_text(str(exc))
        self._logger.error(f"[DEBUG] {kind} 异常 !! url={url}, error={safe_error}")

    @classmethod
    def _sanitize_mapping(cls, value: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            sanitized[key] = cls._sanitize_debug_payload(item, str(key))
        return sanitized

    @staticmethod
    def _mask_secret(value: Any) -> str:
        text = str(value or "")
        if len(text) <= 6:
            return "*" * len(text)
        return text[:3] + "*" * (len(text) - 6) + text[-3:]

    @staticmethod
    def _mask_identifier(value: Any) -> str:
        text = str(value or "")
        if not text:
            return ""
        if len(text) <= 4:
            return "*" * len(text)
        return text[:2] + "*" * max(3, len(text) - 4) + text[-2:]

    @classmethod
    def _sanitize_debug_payload(cls, payload: Any, key: str = "") -> Any:
        lowered = str(key).lower()
        if lowered in cls._SECRET_DEBUG_KEYS:
            return cls._mask_secret(payload)
        if lowered in cls._PRIVATE_DEBUG_KEYS:
            return cls._mask_identifier(payload)
        if isinstance(payload, dict):
            return {
                item_key: cls._sanitize_debug_payload(item_value, str(item_key))
                for item_key, item_value in payload.items()
            }
        if isinstance(payload, list):
            return [cls._sanitize_debug_payload(item) for item in payload]
        if isinstance(payload, tuple):
            return [cls._sanitize_debug_payload(item) for item in payload]
        if isinstance(payload, str):
            return cls._sanitize_debug_text(payload)
        return payload

    @classmethod
    def _sanitize_debug_text(cls, value: str) -> str:
        text = str(value or "")

        def mask_secret_match(match: re.Match[str]) -> str:
            return f"{match.group(1)}={cls._mask_secret(match.group(2))}"

        def mask_private_match(match: re.Match[str]) -> str:
            return f"{match.group(1)}={cls._mask_identifier(match.group(2))}"

        text = re.sub(
            r"(?i)\b(auth|authorization|api_key|apikey|token|secret|password|cookie)=([^&\s]+)",
            mask_secret_match,
            text,
        )
        text = re.sub(
            r"(?i)\b(uid|uuid|user_id|group_id|player|player_name)=([^&\s]+)",
            mask_private_match,
            text,
        )
        text = re.sub(
            r"(?i)(Bearer\s+)([A-Za-z0-9._~+/=-]{8,})",
            lambda match: match.group(1) + cls._mask_secret(match.group(2)),
            text,
        )
        text = re.sub(
            r"(?<![A-Za-z0-9])[A-Fa-f0-9]{32,}(?![A-Za-z0-9])",
            lambda match: cls._mask_secret(match.group(0)),
            text,
        )
        return text

    @classmethod
    def _serialize_debug_payload(cls, payload: Any) -> str:
        safe_payload = cls._sanitize_debug_payload(payload)
        try:
            text = (
                safe_payload
                if isinstance(safe_payload, str)
                else json.dumps(safe_payload, ensure_ascii=False)
            )
        except Exception:
            text = repr(safe_payload)
        if len(text) > cls._MAX_DEBUG_PAYLOAD_CHARS:
            return text[: cls._MAX_DEBUG_PAYLOAD_CHARS] + "...(truncated)"
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

    def _get_cached_map_rotation(self) -> MapRotationInfo | None:
        cached = self._map_rotation_cache
        if not cached:
            return None
        saved_at, rotation_info = cached
        if time.monotonic() - saved_at > self._live_cache_ttl_seconds:
            self._map_rotation_cache = None
            return None
        return rotation_info

    def _get_cached_daily_map_schedule(
        self, cache_key: str
    ) -> DailyMapScheduleInfo | None:
        cached = self._daily_map_cache.get(cache_key)
        if not cached:
            return None
        saved_at, schedule = cached
        if time.monotonic() - saved_at > self._daily_map_cache_ttl_seconds:
            self._daily_map_cache.pop(cache_key, None)
            return None
        return schedule

    def _get_cached_predator(self) -> PredatorInfo | None:
        cached = self._predator_cache
        if not cached:
            return None
        saved_at, predator_info = cached
        if time.monotonic() - saved_at > self._live_cache_ttl_seconds:
            self._predator_cache = None
            return None
        return predator_info


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


def parse_map_schedule_page(html: str) -> list[MapScheduleEntry]:
    entries: list[MapScheduleEntry] = []
    seen: set[tuple[str, int, int]] = set()
    for match in MAP_SCHEDULE_ENTRY_RE.finditer(str(html or "")):
        map_name = _clean_html_text(match.group("map")) or "未知"
        start_timestamp = _to_int(match.group("start")) or 0
        end_timestamp = _to_int(match.group("end")) or 0
        if not start_timestamp or not end_timestamp or end_timestamp <= start_timestamp:
            continue
        key = (map_name, start_timestamp, end_timestamp)
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            _make_map_schedule_entry(
                map_name=map_name,
                start_timestamp=start_timestamp,
                end_timestamp=end_timestamp,
            )
        )
    return sorted(entries, key=lambda item: item.start_timestamp)


def filter_daily_map_entries(
    entries: list[MapScheduleEntry], day_ref: datetime | None = None
) -> list[MapScheduleEntry]:
    day_start, day_end = _beijing_day_bounds(day_ref)
    return [
        entry
        for entry in sorted(entries, key=lambda item: item.start_timestamp)
        if _entry_end_dt(entry) > day_start and _entry_start_dt(entry) < day_end
    ]


def build_daily_map_entries(
    entries: list[MapScheduleEntry], day_ref: datetime | None = None
) -> list[MapScheduleEntry]:
    ordered = _dedupe_schedule_entries(entries)
    if not ordered:
        return []

    day_start, day_end = _beijing_day_bounds(day_ref)
    expanded = list(ordered)
    cycle = _infer_map_cycle(expanded)
    duration_secs = _infer_schedule_duration(expanded)
    if cycle and duration_secs > 0:
        expanded = _expand_schedule_to_bounds(
            expanded, cycle, duration_secs, day_start, day_end
        )
    return filter_daily_map_entries(expanded, day_ref)


def update_daily_map_pool_state(
    state: DailyMapPoolState | dict[str, Any] | None,
    current: MapRotationEntry | None,
    next_entry: MapRotationEntry | None,
    season_info: SeasonInfo | None = None,
    now: datetime | None = None,
) -> DailyMapPoolState:
    current_state = (
        state
        if isinstance(state, DailyMapPoolState)
        else DailyMapPoolState.from_dict(state)
    )
    now_dt = _coerce_utc_datetime(now) or datetime.now(timezone.utc)
    now_ts = int(now_dt.timestamp())
    season_key = _daily_map_season_key(season_info) or current_state.season_key
    season_end_iso = (
        str(season_info.end_iso or "")
        if season_info is not None
        else current_state.season_end_iso
    )

    if current is None or next_entry is None:
        return DailyMapPoolState(
            season_key=season_key,
            season_end_iso=season_end_iso,
            status="learning",
            cycle=list(current_state.cycle),
            updated_at=now_ts,
            reason="API 未返回完整地图轮换，暂时无法确认排位地图池",
        )

    current_name = str(current.map_name or "").strip()
    next_name = str(next_entry.map_name or "").strip()
    if not current_name or not next_name:
        return DailyMapPoolState(
            season_key=season_key,
            season_end_iso=season_end_iso,
            status="learning",
            updated_at=now_ts,
            reason="API 地图名称缺失，暂时无法确认排位地图池",
        )

    if _is_in_season_map_pool_lock_window(season_info, now_dt, season_end_iso):
        return _new_learning_map_pool_state(
            season_key,
            season_end_iso,
            current,
            next_entry,
            now_ts,
            "临近赛季更新，排位地图池重新确认中，仅显示 API 当前/下一张",
        )

    if (
        current_state.season_key
        and season_key
        and current_state.season_key != season_key
    ):
        return _new_learning_map_pool_state(
            season_key,
            season_end_iso,
            current,
            next_entry,
            now_ts,
            "赛季已变化，排位地图池重新学习中，仅显示 API 当前/下一张",
        )

    if current_state.status == "confirmed" and len(current_state.cycle) >= 3:
        if _daily_map_pair_matches_cycle(current_state.cycle, current_name, next_name):
            return DailyMapPoolState(
                season_key=season_key,
                season_end_iso=season_end_iso,
                status="confirmed",
                cycle=list(current_state.cycle),
                last_current=current_name,
                last_next=next_name,
                last_current_start=int(current.start_timestamp or 0),
                updated_at=now_ts,
                reason="API 已确认排位地图池闭环",
            )
        return _new_learning_map_pool_state(
            season_key,
            season_end_iso,
            current,
            next_entry,
            now_ts,
            "API 轮换显示地图池变化，重新学习中，仅显示 API 当前/下一张",
        )

    if _is_same_api_pair(current_state, current, next_entry):
        current_state.season_key = season_key
        current_state.season_end_iso = season_end_iso
        current_state.updated_at = now_ts
        if not current_state.reason:
            current_state.reason = "新赛季地图池学习中，仅显示 API 当前/下一张"
        return current_state

    cycle = _advance_learning_map_cycle(
        list(current_state.cycle),
        current_name,
        next_name,
    )
    status = "confirmed" if _learning_cycle_is_closed(cycle, current_name, next_name) else "learning"
    reason = (
        "API 已确认排位地图池闭环"
        if status == "confirmed"
        else "新赛季地图池学习中，仅显示 API 当前/下一张"
    )
    return DailyMapPoolState(
        season_key=season_key,
        season_end_iso=season_end_iso,
        status=status,
        cycle=cycle,
        last_current=current_name,
        last_next=next_name,
        last_current_start=int(current.start_timestamp or 0),
        updated_at=now_ts,
        reason=reason,
    )


def build_daily_map_entries_from_pool_state(
    state: DailyMapPoolState | dict[str, Any] | None,
    current: MapRotationEntry | None,
    next_entry: MapRotationEntry | None,
    hours: int = 24,
) -> tuple[list[MapScheduleEntry], str]:
    pool_state = (
        state
        if isinstance(state, DailyMapPoolState)
        else DailyMapPoolState.from_dict(state)
    )
    anchors = [
        _schedule_entry_from_rotation_entry(item)
        for item in (current, next_entry)
        if item is not None
    ]
    anchors = [item for item in anchors if item.start_timestamp and item.end_timestamp]

    if (
        pool_state.status == "confirmed"
        and len(pool_state.cycle) >= 3
        and current is not None
        and next_entry is not None
    ):
        entries = build_rolling_map_entries_from_cycle(
            pool_state.cycle,
            current,
            next_entry,
            hours=hours,
        )
        if entries:
            return (
                entries,
                "API 已确认排位地图池闭环，当前/下一张为 API，后续按已确认地图池推断",
            )
        return (
            _dedupe_schedule_entries(anchors),
            "API 轮换与已确认地图池不一致，仅显示 API 当前/下一张",
        )

    if (
        pool_state.status == "learning"
        and len(pool_state.cycle) >= 2
        and current is not None
        and next_entry is not None
        and _learning_map_pool_allows_tentative_forecast(pool_state)
    ):
        entries = build_tentative_map_entries_from_cycle(
            pool_state.cycle,
            current,
            next_entry,
            hours=hours,
        )
        if len(entries) > len(anchors):
            return (
                entries,
                "地图池仍在学习中，当前/下一张为 API，后续按已观测顺序临时推测，可能随 API 下一次校正",
            )

    note = pool_state.reason or "新赛季地图池学习中，仅显示 API 当前/下一张"
    if "仅显示 API 当前/下一张" not in note:
        note = f"{note}，仅显示 API 当前/下一张"
    return (_dedupe_schedule_entries(anchors), note)


def build_rolling_map_entries_from_cycle(
    cycle: list[str],
    current: MapRotationEntry,
    next_entry: MapRotationEntry,
    hours: int = 24,
) -> list[MapScheduleEntry]:
    normalized_cycle = _dedupe_map_cycle(cycle)
    if len(normalized_cycle) < 3:
        return []
    if not _daily_map_pair_matches_cycle(
        normalized_cycle, current.map_name, next_entry.map_name
    ):
        return []

    duration = _rotation_duration_seconds(current) or _rotation_duration_seconds(next_entry)
    if duration <= 0 or not current.start_timestamp:
        return []

    window_end = int(current.start_timestamp) + max(1, int(hours)) * 60 * 60
    entries: list[MapScheduleEntry] = []
    current_index = _map_index_in_cycle(normalized_cycle, current.map_name)
    if current_index < 0:
        return []

    start = int(current.start_timestamp)
    index = current_index
    while start < window_end:
        map_name = normalized_cycle[index % len(normalized_cycle)]
        end = start + duration
        source = "inferred"
        if _same_map_name(map_name, current.map_name) and abs(start - int(current.start_timestamp)) <= 60:
            end = int(current.end_timestamp or end)
            source = "api"
        elif _same_map_name(map_name, next_entry.map_name) and abs(start - int(next_entry.start_timestamp or 0)) <= 60:
            end = int(next_entry.end_timestamp or end)
            source = "api"
        entries.append(_make_map_schedule_entry(map_name, start, end, source=source))
        start = end
        index += 1
    return _dedupe_schedule_entries(entries)


def build_tentative_map_entries_from_cycle(
    cycle: list[str],
    current: MapRotationEntry,
    next_entry: MapRotationEntry,
    hours: int = 24,
) -> list[MapScheduleEntry]:
    normalized_cycle = _dedupe_map_cycle(cycle)
    if len(normalized_cycle) < 2:
        return []
    if not _daily_map_pair_matches_cycle(
        normalized_cycle, current.map_name, next_entry.map_name
    ):
        return []

    duration = _rotation_duration_seconds(current) or _rotation_duration_seconds(next_entry)
    if duration <= 0 or not current.start_timestamp:
        return []

    window_end = int(current.start_timestamp) + max(1, int(hours)) * 60 * 60
    entries: list[MapScheduleEntry] = []
    current_index = _map_index_in_cycle(normalized_cycle, current.map_name)
    if current_index < 0:
        return []

    start = int(current.start_timestamp)
    index = current_index
    while start < window_end:
        map_name = normalized_cycle[index % len(normalized_cycle)]
        end = start + duration
        source = "inferred"
        if _same_map_name(map_name, current.map_name) and abs(start - int(current.start_timestamp)) <= 60:
            end = int(current.end_timestamp or end)
            source = "api"
        elif _same_map_name(map_name, next_entry.map_name) and abs(start - int(next_entry.start_timestamp or 0)) <= 60:
            end = int(next_entry.end_timestamp or end)
            source = "api"
        entries.append(_make_map_schedule_entry(map_name, start, end, source=source))
        start = end
        index += 1
    return _dedupe_schedule_entries(entries)


def build_daily_map_entries_from_api(
    entries: list[MapScheduleEntry],
    day_ref: datetime | None = None,
    current: MapRotationEntry | None = None,
    next_entry: MapRotationEntry | None = None,
) -> tuple[list[MapScheduleEntry], str]:
    anchors = [
        _schedule_entry_from_rotation_entry(item)
        for item in (current, next_entry)
        if item is not None
    ]
    anchors = [item for item in anchors if item.start_timestamp and item.end_timestamp]

    calibrated_entries, verified = _calibrate_schedule_entries_with_api(
        entries,
        current=current,
        next_entry=next_entry,
    )
    if verified and calibrated_entries:
        merged = _merge_api_anchor_entries(calibrated_entries, anchors)
        return (
            build_rolling_map_entries(merged, current.start_timestamp),
            "API 已确认当前/下一张，后续为网页地图池推断，赛季换池时仅供参考",
        )

    if anchors:
        return (
            _dedupe_schedule_entries(anchors),
            "网页全天排期未通过 API 校验，仅显示 API 当前/下一张",
        )

    return (
        build_daily_map_entries(entries, day_ref),
        "网页排期抓取，时间均为北京时间，仅供参考",
    )


def build_rolling_map_entries(
    entries: list[MapScheduleEntry],
    start_timestamp: int,
    hours: int = 24,
) -> list[MapScheduleEntry]:
    ordered = _dedupe_schedule_entries(entries)
    if not ordered or not start_timestamp:
        return []

    window_start = datetime.fromtimestamp(
        int(start_timestamp), tz=timezone.utc
    ).astimezone(SHANGHAI_TZ)
    window_end = window_start + timedelta(hours=max(1, int(hours)))
    expanded = list(ordered)
    cycle = _infer_map_cycle(expanded)
    duration_secs = _infer_schedule_duration(expanded)
    if cycle and duration_secs > 0:
        expanded = _expand_schedule_to_bounds(
            expanded, cycle, duration_secs, window_start, window_end
        )
    rolling = [
        entry
        for entry in _dedupe_schedule_entries(expanded)
        if _entry_end_dt(entry) > window_start and _entry_start_dt(entry) < window_end
    ]
    return [_mark_schedule_entry_source(entry, "inferred") for entry in rolling]


def _calibrate_schedule_entries_with_api(
    entries: list[MapScheduleEntry],
    current: MapRotationEntry | None = None,
    next_entry: MapRotationEntry | None = None,
) -> tuple[list[MapScheduleEntry], bool]:
    ordered = _dedupe_schedule_entries(entries)
    if not ordered or current is None or not current.start_timestamp:
        return ordered, False

    first = ordered[0]
    if not _same_map_name(first.map_name, current.map_name):
        return ordered, False

    offset = int(current.start_timestamp) - int(first.start_timestamp)
    if abs(offset) > MAX_DAILY_MAP_CALIBRATION_OFFSET_SECS:
        return ordered, False

    if not _schedule_entry_matches_api(first, current, offset):
        return ordered, False

    if next_entry is not None and next_entry.start_timestamp:
        if len(ordered) < 2:
            return ordered, False
        if not _schedule_entry_matches_api(ordered[1], next_entry, offset):
            return ordered, False

    if offset == 0:
        return ordered, True
    return [_shift_schedule_entry(entry, offset) for entry in ordered], True


def _schedule_entry_matches_api(
    entry: MapScheduleEntry,
    api_entry: MapRotationEntry,
    offset: int,
) -> bool:
    if not _same_map_name(entry.map_name, api_entry.map_name):
        return False
    shifted_start = int(entry.start_timestamp) + offset
    shifted_end = int(entry.end_timestamp) + offset
    api_start = int(api_entry.start_timestamp or 0)
    api_end = int(api_entry.end_timestamp or 0)
    if not api_start or not api_end:
        return False
    return abs(shifted_start - api_start) <= 60 and abs(shifted_end - api_end) <= 60


def _same_map_name(left: str, right: str) -> bool:
    return _normalize_map_name_for_compare(left) == _normalize_map_name_for_compare(right)


def _normalize_map_name_for_compare(value: str) -> str:
    return "".join(
        ch
        for ch in str(value or "").lower().replace("'", "").replace("’", "")
        if ch.isalnum()
    )


def _shift_schedule_entry(entry: MapScheduleEntry, offset: int) -> MapScheduleEntry:
    return _make_map_schedule_entry(
        entry.map_name,
        int(entry.start_timestamp) + int(offset),
        int(entry.end_timestamp) + int(offset),
        source=entry.source,
    )


def _mark_schedule_entry_source(entry: MapScheduleEntry, source: str) -> MapScheduleEntry:
    if entry.source == "api":
        return entry
    return _make_map_schedule_entry(
        entry.map_name,
        int(entry.start_timestamp),
        int(entry.end_timestamp),
        source=source,
    )


def _schedule_entry_from_rotation_entry(
    entry: MapRotationEntry | None,
) -> MapScheduleEntry | None:
    if entry is None or not entry.start_timestamp or not entry.end_timestamp:
        return None
    return _make_map_schedule_entry(
        entry.map_name,
        int(entry.start_timestamp),
        int(entry.end_timestamp),
        source="api",
    )


def _merge_api_anchor_entries(
    entries: list[MapScheduleEntry],
    anchors: list[MapScheduleEntry | None],
) -> list[MapScheduleEntry]:
    merged = list(entries)
    for anchor in anchors:
        if anchor is None:
            continue
        replaced = False
        for index, entry in enumerate(merged):
            if (
                _same_map_name(entry.map_name, anchor.map_name)
                and abs(entry.start_timestamp - anchor.start_timestamp) <= 60
            ):
                merged[index] = anchor
                replaced = True
                break
        if not replaced:
            merged.append(anchor)
    return _dedupe_schedule_entries(merged)


def _new_learning_map_pool_state(
    season_key: str,
    season_end_iso: str,
    current: MapRotationEntry,
    next_entry: MapRotationEntry,
    updated_at: int,
    reason: str,
) -> DailyMapPoolState:
    return DailyMapPoolState(
        season_key=season_key,
        season_end_iso=season_end_iso,
        status="learning",
        cycle=_dedupe_map_cycle([current.map_name, next_entry.map_name]),
        last_current=str(current.map_name or ""),
        last_next=str(next_entry.map_name or ""),
        last_current_start=int(current.start_timestamp or 0),
        updated_at=updated_at,
        reason=reason,
    )


def _learning_map_pool_allows_tentative_forecast(pool_state: DailyMapPoolState) -> bool:
    reason = str(pool_state.reason or "")
    blocked_tokens = (
        "临近赛季更新",
        "赛季已变化",
        "地图池变化",
        "未返回完整",
        "名称缺失",
    )
    return not any(token in reason for token in blocked_tokens)


def _daily_map_season_key(season_info: SeasonInfo | None) -> str:
    if season_info is None:
        return ""
    if season_info.season_number is not None:
        name = str(season_info.season_name or "").strip()
        return f"S{season_info.season_number}:{name}"
    if season_info.start_iso or season_info.end_iso:
        return f"{season_info.start_iso}:{season_info.end_iso}"
    return ""


def _is_in_season_map_pool_lock_window(
    season_info: SeasonInfo | None,
    now: datetime | None = None,
    fallback_end_iso: str = "",
) -> bool:
    end_iso = (
        str(season_info.end_iso or "")
        if season_info is not None and season_info.end_iso
        else str(fallback_end_iso or "")
    )
    if not end_iso:
        return False
    end_dt = _parse_iso_datetime(end_iso)
    now_dt = _coerce_utc_datetime(now) or datetime.now(timezone.utc)
    if end_dt is None:
        return False
    window_start = end_dt - timedelta(seconds=SEASON_MAP_POOL_LOCK_BEFORE_END_SECS)
    window_end = end_dt + timedelta(seconds=SEASON_MAP_POOL_LOCK_AFTER_END_SECS)
    return window_start <= now_dt <= window_end


def _is_same_api_pair(
    state: DailyMapPoolState,
    current: MapRotationEntry,
    next_entry: MapRotationEntry,
) -> bool:
    if not state.last_current_start or state.last_current_start != int(current.start_timestamp or 0):
        return False
    return _same_map_name(state.last_current, current.map_name) and _same_map_name(
        state.last_next, next_entry.map_name
    )


def _advance_learning_map_cycle(
    cycle: list[str], current_name: str, next_name: str
) -> list[str]:
    current_cycle = _dedupe_map_cycle(cycle)
    if len(current_cycle) < 2:
        return _dedupe_map_cycle([current_name, next_name])

    if _same_map_name(current_cycle[-1], current_name):
        if _same_map_name(current_cycle[0], next_name) and len(current_cycle) >= 3:
            return current_cycle
        if not any(_same_map_name(item, next_name) for item in current_cycle):
            return [*current_cycle, next_name]
        return _dedupe_map_cycle([current_name, next_name])

    if _daily_map_pair_matches_cycle(current_cycle, current_name, next_name):
        return current_cycle

    if (
        len(current_cycle) >= 2
        and _same_map_name(current_cycle[0], next_name)
        and not any(_same_map_name(item, current_name) for item in current_cycle)
    ):
        return [*current_cycle, current_name]

    return _dedupe_map_cycle([current_name, next_name])


def _learning_cycle_is_closed(
    cycle: list[str], current_name: str, next_name: str
) -> bool:
    return (
        len(cycle) >= 3
        and _same_map_name(cycle[-1], current_name)
        and _same_map_name(cycle[0], next_name)
    )


def _daily_map_pair_matches_cycle(
    cycle: list[str], current_name: str, next_name: str
) -> bool:
    index = _map_index_in_cycle(cycle, current_name)
    if index < 0 or not cycle:
        return False
    expected_next = cycle[(index + 1) % len(cycle)]
    return _same_map_name(expected_next, next_name)


def _map_index_in_cycle(cycle: list[str], map_name: str) -> int:
    for index, item in enumerate(cycle):
        if _same_map_name(item, map_name):
            return index
    return -1


def _dedupe_map_cycle(cycle: list[str]) -> list[str]:
    result: list[str] = []
    for item in cycle:
        name = str(item or "").strip()
        if not name:
            continue
        if any(_same_map_name(existing, name) for existing in result):
            continue
        result.append(name)
    return result


def _rotation_duration_seconds(entry: MapRotationEntry | None) -> int:
    if entry is None:
        return 0
    if entry.start_timestamp and entry.end_timestamp:
        return max(0, int(entry.end_timestamp) - int(entry.start_timestamp))
    return int(entry.duration_secs or 0)


def format_schedule_remaining(
    entry: MapScheduleEntry, now: datetime | None = None
) -> str:
    current = _coerce_beijing_datetime(now)
    end = _entry_end_dt(entry)
    remaining_seconds = max(0, int((end - current).total_seconds()))
    minutes = remaining_seconds // 60
    days = minutes // 1440
    hours = (minutes % 1440) // 60
    mins = minutes % 60
    if days:
        return f"剩余 {days}天{hours}时"
    if hours:
        return f"剩余 {hours}时{mins}分"
    return f"剩余 {mins}分"


def _make_map_schedule_entry(
    map_name: str,
    start_timestamp: int,
    end_timestamp: int,
    source: str = "web",
) -> MapScheduleEntry:
    duration_secs = max(0, int(end_timestamp) - int(start_timestamp))
    code = _map_code_from_name(map_name)
    return MapScheduleEntry(
        map_name=map_name,
        map_name_zh=translate(map_name),
        start_timestamp=int(start_timestamp),
        end_timestamp=int(end_timestamp),
        readable_start=_format_schedule_timestamp(start_timestamp),
        readable_end=_format_schedule_timestamp(end_timestamp),
        duration_secs=duration_secs,
        asset=f"https://apexlegendsstatus.com/assets/maps/{code}.png" if code else "",
        code=code,
        source=source,
    )


def _expand_schedule_to_bounds(
    entries: list[MapScheduleEntry],
    cycle: list[str],
    duration_secs: int,
    day_start: datetime,
    day_end: datetime,
) -> list[MapScheduleEntry]:
    expanded = list(entries)

    while expanded and _entry_start_dt(expanded[0]) > day_start:
        first = expanded[0]
        prev_name = _cycle_neighbor(cycle, first.map_name, step=-1)
        if not prev_name:
            break
        prev_end = first.start_timestamp
        prev_start = prev_end - duration_secs
        expanded.insert(
            0, _make_map_schedule_entry(prev_name, prev_start, prev_end, source="inferred")
        )

    while expanded and _entry_end_dt(expanded[-1]) < day_end:
        last = expanded[-1]
        next_name = _cycle_neighbor(cycle, last.map_name, step=1)
        if not next_name:
            break
        next_start = last.end_timestamp
        next_end = next_start + duration_secs
        expanded.append(
            _make_map_schedule_entry(next_name, next_start, next_end, source="inferred")
        )

    return _dedupe_schedule_entries(expanded)


def _cycle_neighbor(cycle: list[str], map_name: str, step: int) -> str:
    if not cycle:
        return ""
    try:
        index = cycle.index(map_name)
    except ValueError:
        return ""
    return cycle[(index + step) % len(cycle)]


def _infer_map_cycle(entries: list[MapScheduleEntry]) -> list[str]:
    names = [entry.map_name for entry in entries if entry.map_name]
    if not names:
        return []
    max_cycle_len = min(8, len(names))
    for cycle_len in range(1, max_cycle_len + 1):
        candidate = names[:cycle_len]
        if all(name == candidate[index % cycle_len] for index, name in enumerate(names)):
            return candidate
    return names[:max_cycle_len]


def _infer_schedule_duration(entries: list[MapScheduleEntry]) -> int:
    durations: dict[int, int] = {}
    for entry in entries:
        duration = max(0, entry.end_timestamp - entry.start_timestamp)
        if not duration:
            continue
        durations[duration] = durations.get(duration, 0) + 1
    if not durations:
        return 0
    return max(durations.items(), key=lambda item: (item[1], item[0]))[0]


def _dedupe_schedule_entries(entries: list[MapScheduleEntry]) -> list[MapScheduleEntry]:
    result: list[MapScheduleEntry] = []
    seen: set[tuple[str, int, int]] = set()
    for entry in sorted(entries, key=lambda item: item.start_timestamp):
        key = (entry.map_name, entry.start_timestamp, entry.end_timestamp)
        if key in seen:
            continue
        seen.add(key)
        result.append(entry)
    return result


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", "", str(value or ""))
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _beijing_day_bounds(day_ref: datetime | None = None) -> tuple[datetime, datetime]:
    current = _coerce_beijing_datetime(day_ref)
    day_start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return day_start, day_start + timedelta(days=1)


def _coerce_beijing_datetime(value: datetime | None = None) -> datetime:
    if value is None:
        return datetime.now(SHANGHAI_TZ)
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def _entry_start_dt(entry: MapScheduleEntry) -> datetime:
    return datetime.fromtimestamp(entry.start_timestamp, tz=timezone.utc).astimezone(
        SHANGHAI_TZ
    )


def _entry_end_dt(entry: MapScheduleEntry) -> datetime:
    return datetime.fromtimestamp(entry.end_timestamp, tz=timezone.utc).astimezone(
        SHANGHAI_TZ
    )


def _format_schedule_timestamp(timestamp: int) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(
            SHANGHAI_TZ
        ).strftime("%m-%d %H:%M")
    except Exception:
        return "未知"


def _map_code_from_name(map_name: str) -> str:
    text = str(map_name or "").strip()
    aliases = {
        "Broken Moon": "Broken_Moon",
        "E-District": "E-District",
        "Kings Canyon": "Kings_Canyon",
        "Olympus": "Olympus",
        "Storm Point": "Storm_Point",
        "World's Edge": "Worlds_Edge",
        "Worlds Edge": "Worlds_Edge",
    }
    if text in aliases:
        return aliases[text]
    return "".join(
        ch
        for ch in text.replace("'", "").replace("’", "").replace(" ", "_")
        if ch.isalnum() or ch in {"_", "-"}
    )


def _normalize_daily_map_mode(mode: str) -> str:
    normalized = str(mode or "").strip().lower()
    if normalized in {
        "battle_royale",
        "battle-royale",
        "pubs",
        "pub",
        "match",
        "匹配",
        "三人赛",
    }:
        return "battle_royale"
    return "ranked"


def _daily_map_schedule_url(mode: str) -> str:
    if _normalize_daily_map_mode(mode) == "battle_royale":
        return APEX_STATUS_PUBS_MAP_URL
    return APEX_STATUS_RANKED_MAP_URL


def _daily_map_schedule_title(mode: str) -> str:
    if _normalize_daily_map_mode(mode) == "battle_royale":
        return "Apex 三人赛全天地图"
    return "Apex 排位全天地图"


def _parse_map_rotation_info(data: Any) -> MapRotationInfo:
    if not isinstance(data, dict):
        return MapRotationInfo()

    if any(key in data for key in ("battle_royale", "ranked", "ltm")):
        return MapRotationInfo(
            battle_royale=_parse_map_rotation_mode(data.get("battle_royale")),
            ranked=_parse_map_rotation_mode(data.get("ranked")),
            ltm=_parse_map_rotation_mode(data.get("ltm")),
        )

    return MapRotationInfo(
        battle_royale=_parse_map_rotation_mode(data),
    )


def _parse_map_rotation_mode(data: Any) -> MapRotationMode:
    if not isinstance(data, dict):
        return MapRotationMode()
    return MapRotationMode(
        current=_parse_map_rotation_entry(data.get("current")),
        next=_parse_map_rotation_entry(data.get("next")),
    )


def _parse_map_rotation_entry(data: Any) -> MapRotationEntry | None:
    if not isinstance(data, dict):
        return None

    map_name = str(data.get("map") or "").strip() or "未知"
    return MapRotationEntry(
        map_name=map_name,
        map_name_zh=translate(map_name),
        start_timestamp=_to_int(data.get("start")) or 0,
        end_timestamp=_to_int(data.get("end")) or 0,
        readable_start=str(data.get("readableDate_start") or ""),
        readable_end=str(data.get("readableDate_end") or ""),
        duration_secs=_to_int(data.get("DurationInSecs")) or 0,
        duration_minutes=_to_int(data.get("DurationInMinutes")) or 0,
        asset=str(data.get("asset") or ""),
        code=str(data.get("code") or ""),
        remaining_secs=_to_int(data.get("remainingSecs")),
        remaining_mins=_to_int(data.get("remainingMins")),
        remaining_timer=str(data.get("remainingTimer") or ""),
    )


def _parse_predator_info(data: Any) -> PredatorInfo:
    if not isinstance(data, dict):
        return PredatorInfo()

    rp_data = data.get("RP", {})
    if not isinstance(rp_data, dict):
        return PredatorInfo()

    platforms: dict[str, PredatorPlatformStats] = {}
    for platform in ("PC", "PS4", "X1", "SWITCH"):
        raw = rp_data.get(platform)
        if not isinstance(raw, dict):
            continue
        platforms[platform] = PredatorPlatformStats(
            platform=platform,
            threshold_rp=_to_int(raw.get("val")) or 0,
            found_rank=_to_int(raw.get("foundRank")) or 0,
            uid=str(raw.get("uid") or ""),
            update_timestamp=_to_int(raw.get("updateTimestamp")) or 0,
            masters_and_preds=_to_int(raw.get("totalMastersAndPreds")) or 0,
        )

    return PredatorInfo(platforms=platforms)


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


def _parse_apex_status_current_season_info(
    html: str, now: datetime | None = None
) -> SeasonInfo:
    target_timestamp = _extract_apex_status_start_timestamp(html)
    if target_timestamp is None:
        raise RuntimeError("无法从 Apex Legends Status 页面提取赛季时间戳")

    target_number, target_name = _extract_apex_status_season_title(html)
    target_dt = datetime.fromtimestamp(target_timestamp, tz=timezone.utc)
    now_dt = _coerce_utc_datetime(now) or datetime.now(timezone.utc)

    if target_number is not None and now_dt < target_dt:
        # 倒计时还没结束时，时间戳就是当前赛季结束、下一赛季开始的时刻。
        season_number = max(1, target_number - 1)
        season_name = ""
        start_dt = target_dt - timedelta(days=APEX_STATUS_SEASON_DURATION_DAYS)
        end_dt = target_dt
    else:
        # 倒计时已到达时，页面标题对应当前赛季；按常规 13 周赛季长度推算结束。
        season_number = target_number
        season_name = target_name
        start_dt = target_dt
        end_dt = target_dt + timedelta(days=APEX_STATUS_SEASON_DURATION_DAYS)

    start_iso = _to_iso_datetime(start_dt)
    end_iso = _to_iso_datetime(end_dt)
    return SeasonInfo(
        season_number=season_number,
        season_name=season_name,
        start_date=_format_iso_date(start_iso),
        end_date=_format_iso_date(end_iso),
        timezone="Asia/Shanghai",
        update_time_hint="Apex Legends Status countdown 时间戳",
        source="apexlegendsstatus.com",
        season_url=APEX_STATUS_SEASON_COUNTDOWN_URL,
        start_iso=start_iso,
        end_iso=end_iso,
        status_text=_resolve_season_status(start_iso, end_iso),
        supports_ranked_splits=False,
    )


def _extract_apex_status_start_timestamp(html: str) -> int | None:
    match = APEX_STATUS_START_TIME_RE.search(html or "")
    if not match:
        return None
    return _to_int(match.group(1))


def _extract_apex_status_season_title(html: str) -> tuple[int | None, str]:
    matches = list(APEX_STATUS_SEASON_TITLE_RE.finditer(unescape(html or "")))
    if not matches:
        return None, ""
    match = next((item for item in matches if item.group(2)), matches[0])
    number = _to_int(match.group(1))
    name = re.sub(r"\s+", " ", match.group(2) or "").strip(" -:|")
    return number, name


def _coerce_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
            dt = dt.astimezone(SHANGHAI_TZ)
        else:
            dt = dt.replace(tzinfo=SHANGHAI_TZ)
        return dt.strftime("%Y-%m-%d %H:%M 北京时间")
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
