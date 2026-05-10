from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools

try:
    from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps
except Exception:
    Image = ImageDraw = ImageFilter = ImageFont = ImageOps = None

if __package__:
    from .apex_service import (
        ApexApiClient,
        ApexApiError,
        ApexPlayerStats,
        DailyMapPoolState,
        DailyMapScheduleInfo,
        MapRotationEntry,
        MapRotationInfo,
        MapScheduleEntry,
        PlayerNotFoundError,
        PredatorInfo,
        PredatorPlatformStats,
        SeasonInfo,
        format_schedule_remaining,
        is_likely_season_reset,
        is_score_drop_abnormal,
        normalize_platform,
    )
    from .storage import GroupStore, PlayerRecord
    from .utils import SHANGHAI_TZ, coerce_bool, coerce_int, now_epoch_ms, now_str
else:
    from apex_service import (
        ApexApiClient,
        ApexApiError,
        ApexPlayerStats,
        DailyMapPoolState,
        DailyMapScheduleInfo,
        MapRotationEntry,
        MapRotationInfo,
        MapScheduleEntry,
        PlayerNotFoundError,
        PredatorInfo,
        PredatorPlatformStats,
        SeasonInfo,
        format_schedule_remaining,
        is_likely_season_reset,
        is_score_drop_abnormal,
        normalize_platform,
    )
    from storage import GroupStore, PlayerRecord
    from utils import SHANGHAI_TZ, coerce_bool, coerce_int, now_epoch_ms, now_str


def _split_csv(raw: str, lowercase: bool = False) -> set[str]:
    if not raw:
        return set()
    normalized = str(raw).replace("，", ",")
    items = set()
    for item in normalized.split(","):
        text = item.strip()
        if not text:
            continue
        items.add(text.lower() if lowercase else text)
    return items


@dataclass
class PluginConfig:
    api_key: str
    debug_logging: bool
    check_interval: int
    timeout_ms: int
    max_retries: int
    min_valid_score: int
    blacklist: str
    query_blocklist: str
    user_blacklist: str
    owner_qq: str
    whitelist_enabled: bool
    whitelist_groups: str
    allow_private: bool
    data_dir: str

    @staticmethod
    def from_raw(raw) -> "PluginConfig":
        return PluginConfig(
            api_key=str(raw.get("api_key", "")).strip(),
            debug_logging=coerce_bool(raw.get("debug_logging", False), False),
            check_interval=max(1, coerce_int(raw.get("check_interval", 2), 2)),
            timeout_ms=max(1000, coerce_int(raw.get("timeout_ms", 10000), 10000)),
            max_retries=max(0, coerce_int(raw.get("max_retries", 3), 3)),
            min_valid_score=max(0, coerce_int(raw.get("min_valid_score", 1), 1)),
            blacklist=str(raw.get("blacklist", "")).strip(),
            query_blocklist=str(raw.get("query_blocklist", "")).strip(),
            user_blacklist=str(raw.get("user_blacklist", "")).strip(),
            owner_qq=str(raw.get("owner_qq", "")).strip(),
            whitelist_enabled=coerce_bool(raw.get("whitelist_enabled", False), False),
            whitelist_groups=str(raw.get("whitelist_groups", "")).strip(),
            allow_private=coerce_bool(raw.get("allow_private", True), True),
            data_dir=str(raw.get("data_dir", "")).strip(),
        )


@dataclass
class PollFetchResult:
    group_id: str
    group_origin: str
    player: PlayerRecord
    platform: str
    player_data: ApexPlayerStats | None = None
    not_found: bool = False
    error: Exception | None = None


class Main(Star):
    _PLUGIN_ROOT = Path(__file__).resolve().parent
    _MAP_ASSET_DIR = _PLUGIN_ROOT / "assets" / "maps"
    _RANK_ICON_DIR = _PLUGIN_ROOT / "assets" / "ranks"
    _LEGEND_ICON_DIR = _PLUGIN_ROOT / "assets" / "legends"
    _STATUS_BADGE_DIR = _PLUGIN_ROOT / "assets" / "status"
    _DEFAULT_USER_AVATAR_PATH = _PLUGIN_ROOT / "assets" / "default_user_avatar.png"
    _PREDATOR_TEMPLATE_PATH = _PLUGIN_ROOT / "assets" / "predator_template.png"
    _MAP_CARD_SIZE = (900, 320)
    _MAP_CURRENT_HEIGHT = 212
    _MAP_IMAGE_CACHE_TTL_SECONDS = 60
    _SEASON_CARD_SIZE = (900, 320)
    _SEASON_IMAGE_CACHE_TTL_SECONDS = 60
    _RANK_CHANGE_CARD_SIZE = (1122, 1402)
    _PLAYER_RANK_CARD_SIZE = (1122, 1402)
    _HELP_CARD_SIZE = (1122, 2380)
    _MONITOR_ADDED_CARD_SIZE = (1122, 1040)
    _MONITOR_LIST_CARD_WIDTH = 1122
    _MONITOR_LIST_ROW_LIMIT = 8
    _PREDATOR_IMAGE_CACHE_TTL_SECONDS = 60
    _PREDATOR_GREEN = (88, 210, 126, 255)
    _PREDATOR_DEEP_RED = (158, 31, 36, 255)
    _PREDATOR_NEUTRAL = (246, 241, 232, 255)

    _KEYWORD_COMMAND_BLOCKLIST = {
        # 英文指令
        "apexrank",
        "apexrankwatch",
        "apexranklist",
        "apexrankremove",
        "apexseason",
        "apexpredator",
        "map",
        "apexmap",
        "apexrankmap",
        "dailymap",
        "apextest",
        "apexhelp",
        "apex帮助",
        "apexrankhelp",
        "apexblacklist",
        # 中文别名
        "apex监控",
        "apex列表",
        "apex移除",
        "apex查询",
        "地图",
        "排位地图",
        "匹配地图",
        "全天地图",
        "全天排位地图",
        "今日地图",
        "今日排位地图",
        "apex猎杀",
        "猎杀",
        "视奸",
        "持续视奸",
        "取消持续视奸",
        "apex赛季",
        "新赛季",
        "apex测试",
        "apex黑名单",
        "不准视奸",
        "apexban",
        # 赛季关键词开关
        "赛季关闭",
        "赛季开启",
    }

    def __init__(self, context: Context, config=None) -> None:
        super().__init__(context)

        if config is None:
            config = {}
        self._config = PluginConfig.from_raw(config)
        self._data_dir = self._resolve_data_dir(self._config.data_dir)
        self._settings_file = self._data_dir / "settings.json"
        self._daily_map_pool_state_file = self._data_dir / "daily_map_pool_state.json"
        settings = self._load_settings()
        self._runtime_blacklist = self._normalize_settings_list(
            settings.get("runtime_blacklist", []),
            lowercase=True,
        )
        self._season_keyword_disabled_groups = self._normalize_settings_list(
            settings.get("season_keyword_disabled_groups", []),
            lowercase=False,
        )
        self._store = GroupStore(self._data_dir / "groups.json", logger)
        self._store.load()
        self._migrate_store_keys()
        self._daily_map_pool_state = self._load_daily_map_pool_state()

        self._api = ApexApiClient(
            api_key=self._config.api_key,
            debug_enabled=self._config.debug_logging,
            timeout_ms=self._config.timeout_ms,
            max_retries=self._config.max_retries,
            logger=logger,
        )

        self._stop_event = asyncio.Event()
        self._poll_task = None
        self._poll_concurrency = 5
        self._poll_semaphore = asyncio.Semaphore(self._poll_concurrency)
        self._runtime_started = False

        logger.info(
            f"Apex Rank Watch 已加载，检测间隔 {self._config.check_interval} 分钟"
        )
        if self._config.debug_logging:
            logger.info("Apex Rank Watch 调试日志已开启")

        self._schedule_runtime_start()

    async def _shutdown_runtime(self) -> None:
        self._stop_event.set()
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        await self._api.close()

    async def terminate(self):
        await self._shutdown_runtime()

    async def on_unload(self):
        await self._shutdown_runtime()

    @staticmethod
    def _resolve_data_dir(data_dir: str) -> Path:
        if data_dir:
            return Path(data_dir)
        return Path(StarTools.get_data_dir())

    def _schedule_runtime_start(self) -> None:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        if loop.is_closed():
            return
        loop.call_soon(self._start_runtime)

    def _start_runtime(self) -> None:
        if self._runtime_started:
            return
        self._runtime_started = True
        self._ensure_poll_task()

    def _ensure_poll_task(self) -> bool:
        if self._poll_task and not self._poll_task.done():
            return True
        if self._poll_task and self._poll_task.done():
            try:
                exc = self._poll_task.exception()
            except asyncio.CancelledError:
                exc = None
            if exc:
                logger.error(f"轮询任务异常退出，正在重建任务: {exc}")
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._poll_task = loop.create_task(self._poll_loop())
        return True

    @staticmethod
    def _normalize_settings_list(value, lowercase: bool = True) -> set[str]:
        if not isinstance(value, list):
            return set()
        items = []
        for item in value:
            text = str(item).strip()
            if not text:
                continue
            items.append(text.lower() if lowercase else text)
        return set(items)

    def _load_settings(self) -> dict:
        defaults = {
            "runtime_blacklist": [],
            "season_keyword_disabled_groups": [],
        }
        if not self._settings_file.exists():
            return defaults
        try:
            raw = json.loads(self._settings_file.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return defaults
            merged = dict(defaults)
            merged.update(raw)
            return merged
        except Exception as exc:
            logger.error(f"加载插件设置失败: {exc}")
            return defaults

    def _save_settings(self) -> None:
        payload = {
            "runtime_blacklist": sorted(self._runtime_blacklist),
            "season_keyword_disabled_groups": sorted(self._season_keyword_disabled_groups),
        }
        try:
            self._settings_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self._settings_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_file.replace(self._settings_file)
        except Exception as exc:
            logger.error(f"保存插件设置失败: {exc}")

    def _load_daily_map_pool_state(self) -> DailyMapPoolState:
        if not self._daily_map_pool_state_file.exists():
            return DailyMapPoolState()
        try:
            raw = json.loads(self._daily_map_pool_state_file.read_text(encoding="utf-8"))
            return DailyMapPoolState.from_dict(raw)
        except Exception as exc:
            logger.error(f"加载排位地图池学习状态失败: {exc}")
            return DailyMapPoolState()

    def _save_daily_map_pool_state(self) -> None:
        try:
            self._daily_map_pool_state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self._daily_map_pool_state_file.with_suffix(".tmp")
            tmp_file.write_text(
                json.dumps(
                    self._daily_map_pool_state.to_dict(),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp_file.replace(self._daily_map_pool_state_file)
        except Exception as exc:
            logger.error(f"保存排位地图池学习状态失败: {exc}")

    def _normalize_platform(self, platform: str) -> str:
        return normalize_platform(platform)

    def _format_platform(self, platform: str) -> str:
        mapping = {
            "PC": "PC",
            "PS4": "PlayStation",
            "X1": "Xbox",
            "SWITCH": "Switch",
        }
        return mapping.get(platform, platform)

    @staticmethod
    def _now_str() -> str:
        return now_str()

    def _time_line(self) -> str:
        return f"🕒 时间: {self._now_str()}"

    @staticmethod
    def _event_mentions_me(event: AstrMessageEvent) -> bool:
        message_obj = getattr(event, "message_obj", None)
        if not message_obj:
            return False
        self_id = str(getattr(message_obj, "self_id", "") or "")
        if not self_id:
            return False
        chain = getattr(message_obj, "message", None)
        if not isinstance(chain, list):
            return False
        for seg in chain:
            if isinstance(seg, dict):
                if str(seg.get("type") or "").lower() != "at":
                    continue
                payload = seg.get("data") if isinstance(seg.get("data"), dict) else seg
                target = (
                    payload.get("qq")
                    or payload.get("target")
                    or payload.get("user_id")
                    or payload.get("id")
                    or ""
                )
                if str(target) == self_id:
                    return True
                continue

            target = (
                getattr(seg, "qq", None)
                or getattr(seg, "target", None)
                or getattr(seg, "user_id", None)
                or getattr(seg, "id", None)
            )
            if target is not None and str(target) == self_id:
                return True
        return False

    def _format_passive_text(self, event: AstrMessageEvent, text: str) -> str:
        if not text:
            return ""
        # 兼容会裁剪开头换行或空格的适配器。
        group_id = self._get_group_id(event)
        if group_id and self._event_mentions_me(event):
            return "\u200b\n" + text.lstrip("\n")
        return text

    @staticmethod
    def _read_first_available(source, *names):
        if source is None:
            return None
        if isinstance(source, dict):
            for name in names:
                value = source.get(name)
                if value not in (None, ""):
                    return value
            return None
        for name in names:
            value = getattr(source, name, None)
            if value not in (None, ""):
                return value
        return None

    def _get_reply_message_id(self, event: AstrMessageEvent):
        message_obj = getattr(event, "message_obj", None)
        if not message_obj:
            return None
        value = self._read_first_available(message_obj, "message_id", "id")
        if value:
            return value
        raw = getattr(message_obj, "raw_message", None)
        value = self._read_first_available(raw, "message_id", "id", "messageId", "msg_id")
        return value or None

    @staticmethod
    def _coerce_reply_id(value):
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        text = str(value).strip()
        if not text:
            return None
        try:
            return int(text)
        except Exception:
            return text

    def _prefer_quote_reply(self, event: AstrMessageEvent) -> bool:
        # QQ 个人号（aiocqhttp / OneBot v11）支持引用回复。
        try:
            return str(event.get_platform_name() or "").lower() == "aiocqhttp"
        except Exception:
            return False

    def _plain(self, event: AstrMessageEvent, text: str):
        if text is None:
            text = ""
        if self._prefer_quote_reply(event):
            reply_id = self._coerce_reply_id(self._get_reply_message_id(event))
            if reply_id is not None:
                # QQ 个人号优先使用引用回复，避免频繁 @ 用户。
                return event.chain_result(
                    [Comp.Reply(id=reply_id), Comp.Plain(text=text.lstrip("\n"))]
                )
        return event.plain_result(self._format_passive_text(event, text))

    def _image(self, event: AstrMessageEvent, path: Path):
        image = Comp.Image.fromFileSystem(str(path))
        if self._prefer_quote_reply(event):
            reply_id = self._coerce_reply_id(self._get_reply_message_id(event))
            if reply_id is not None:
                return event.chain_result([Comp.Reply(id=reply_id), image])
        return event.chain_result([image])

    @staticmethod
    def _parse_list(raw: str) -> set[str]:
        return _split_csv(raw)

    @staticmethod
    def _split_blacklist_items(raw: str) -> list[str]:
        return sorted(_split_csv(raw))

    def _normalize_lookup_value(self, value: str) -> str:
        identifier, _ = self._parse_identifier(value)
        normalized = identifier or value
        return normalized.strip().lower()

    def _get_config_blacklist(self) -> set[str]:
        return {
            self._normalize_lookup_value(item)
            for item in _split_csv(self._config.blacklist)
        }

    def _extract_command_args(self, event: AstrMessageEvent) -> str:
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return ""
        raw = text.lstrip("/").lstrip("／").strip()
        if not raw:
            return ""
        parts = raw.split(maxsplit=1)
        command = parts[0].lower()
        if command in self._KEYWORD_COMMAND_BLOCKLIST:
            return parts[1].strip() if len(parts) > 1 else ""
        return raw

    def _parse_blacklist_command(
        self, event: AstrMessageEvent, action: str, player_name: str
    ) -> tuple[str, str]:
        text = self._extract_command_args(event)
        if text:
            parts = text.split(maxsplit=1)
            if parts:
                action = parts[0]
            if len(parts) > 1:
                player_name = parts[1]
        return (action or "").strip(), (player_name or "").strip()

    def _parse_season_query(self, event: AstrMessageEvent, season: str = "") -> int | None:
        raw = self._extract_command_args(event) or str(season or "").strip()
        if not raw:
            return None

        token = raw.split(maxsplit=1)[0].strip().lower()
        if not token:
            return None
        if token in {"current", "curr", "now", "latest", "最新", "当前"}:
            return None
        if token.startswith("s") and token[1:].isdigit():
            token = token[1:]
        if token.isdigit():
            value = int(token)
            if value <= 0:
                raise ValueError("赛季号必须大于 0")
            return value
        raise ValueError("请输入正确的赛季号，例如 /apexseason 28 或 /apexseason current")

    def _is_season_keyword_disabled(self, group_id: str) -> bool:
        if not group_id:
            return False
        return group_id in self._season_keyword_disabled_groups

    def _set_season_keyword_disabled(self, group_id: str, disabled: bool) -> None:
        if not group_id:
            return
        if disabled:
            self._season_keyword_disabled_groups.add(group_id)
        else:
            self._season_keyword_disabled_groups.discard(group_id)
        self._save_settings()

    def _get_user_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        user_id = ""
        if message_obj:
            user_id = str(self._read_first_available(message_obj, "user_id") or "")
            if not user_id:
                sender = getattr(message_obj, "sender", None)
                if sender:
                    user_id = str(self._read_first_available(sender, "user_id", "id", "uid") or "")
        if not user_id:
            user_id = str(self._read_first_available(event, "user_id", "sender_id") or "")
        return user_id

    def _is_owner(self, user_id: str) -> bool:
        if not user_id:
            return False
        owners = self._parse_list(self._config.owner_qq)
        return user_id in owners

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        user_id = self._get_user_id(event)
        if self._is_owner(user_id):
            return True
        message_obj = getattr(event, "message_obj", None)
        sender = getattr(message_obj, "sender", None) if message_obj else None
        role = str(getattr(sender, "role", "") or "").lower()
        return role in {"admin", "owner"}

    def _guard_admin(self, event: AstrMessageEvent) -> str | None:
        if self._is_admin(event):
            return None
        return "⚠️ 此命令仅管理员可用，请在配置中设置 owner_qq"

    def _is_user_blacklisted(self, user_id: str) -> bool:
        if not user_id:
            return False
        users = self._parse_list(self._config.user_blacklist)
        return user_id in users

    def _is_group_allowed(self, group_id: str) -> bool:
        if not self._config.whitelist_enabled:
            return True
        groups = self._parse_list(self._config.whitelist_groups)
        return group_id in groups

    def _guard_access(self, event: AstrMessageEvent, require_group: bool = False) -> str | None:
        user_id = self._get_user_id(event)
        if self._is_owner(user_id):
            return None

        if self._is_user_blacklisted(user_id):
            return "⛔ 你的QQ已被禁止使用该插件"

        group_id = self._get_group_id(event)
        if require_group and not group_id:
            return "⚠️ 此命令仅适用于群聊，请在群聊中使用"

        if not group_id and not self._config.allow_private:
            return "⚠️ 当前不允许私聊使用，请在群聊中使用"

        if group_id and not self._is_group_allowed(group_id):
            return "⚠️ 本群未在白名单中，无法使用此插件"

        return None

    @staticmethod
    def _api_key_apply_url() -> str:
        return "https://portal.apexlegendsapi.com/"

    @staticmethod
    def _api_verify_url() -> str:
        return "https://portal.apexlegendsapi.com/discord-auth"

    def _missing_api_key_text(self) -> str:
        return "\n".join(
            [
                self._time_line(),
                "⚠️ 请先在插件配置中填写 API Key",
                f"🔑 Key 申请地址: {self._api_key_apply_url()}",
            ]
        )

    def _api_request_failed_text(
        self, action: str = "查询", error: Exception | None = None
    ) -> str:
        lines = [self._time_line()]
        if isinstance(error, ApexApiError):
            lines.append(f"❌ {action}失败：{error.user_message}")
            if "验证" in error.user_message:
                lines.append(f"🔗 验证入口: {self._api_verify_url()}")
            else:
                lines.append(f"🔑 Key 申请地址: {self._api_key_apply_url()}")
            return "\n".join(lines)

        lines.extend(
            [
                f"❌ {action}失败：请检查网络、API Key 是否有效，或稍后再试",
                f"🔑 Key 申请地址: {self._api_key_apply_url()}",
            ]
        )
        return "\n".join(lines)

    def _is_platform_token(self, token: str) -> bool:
        normalized = self._normalize_platform(token)
        return normalized in {"PC", "PS4", "X1", "SWITCH"}

    def _parse_player_platform(
        self, event: AstrMessageEvent, player_name: str, platform: str
    ) -> tuple[str, str]:
        text = self._extract_command_args(event)
        if text:
            parts = text.split()
            if len(parts) >= 2 and self._is_platform_token(parts[-1]):
                parsed_name = " ".join(parts[:-1]).strip()
                if parsed_name:
                    return parsed_name, parts[-1].strip()
            return text.strip(), platform.strip() if self._is_platform_token(platform) else ""
        if player_name and platform:
            return player_name.strip(), platform.strip()
        if player_name and not platform:
            return player_name.strip(), ""
        return "", ""

    def _is_blacklisted(self, player_name: str) -> bool:
        if not player_name:
            return False
        name = self._normalize_lookup_value(player_name)
        if not name:
            return False
        if name in self._runtime_blacklist:
            return True
        if not self._config.blacklist:
            return False
        return name in self._get_config_blacklist()

    def _is_query_blocked(self, player_name: str) -> bool:
        blocked = {
            self._normalize_lookup_value(item)
            for item in _split_csv(self._config.query_blocklist)
        }
        return self._normalize_lookup_value(player_name) in blocked

    def _get_group_id(self, event: AstrMessageEvent) -> str:
        message_obj = getattr(event, "message_obj", None)
        group_id = getattr(message_obj, "group_id", "") if message_obj else ""
        if group_id:
            return str(group_id)
        getter = getattr(event, "get_group_id", None)
        if callable(getter):
            try:
                value = getter()
                if value:
                    return str(value)
            except Exception:
                pass
        return str(getattr(event, "group_id", "") or "")

    async def _send_active_message(self, origin: str, message: str) -> bool:
        if not origin:
            logger.warning("会话标识缺失，无法发送通知")
            return False
        try:
            chain = MessageChain().message(message)
            await self.context.send_message(origin, chain)
            return True
        except Exception as exc:
            logger.error(f"发送群消息失败: {exc}")
            return False

    async def _send_active_image(self, origin: str, path: Path) -> bool:
        if not origin:
            logger.warning("会话标识缺失，无法发送图片通知")
            return False
        try:
            chain = MessageChain().file_image(str(path))
            await self.context.send_message(origin, chain)
            return True
        except Exception as exc:
            logger.error(f"发送群图片失败: {exc}")
            return False

    def _parse_identifier(self, player_name: str) -> tuple[str, bool]:
        name = player_name.strip()
        lowered = name.lower()
        if lowered.startswith("uid:"):
            return name[4:].strip(), True
        if lowered.startswith("uuid:"):
            return name[5:].strip(), True
        return name, False

    def _build_player_key(self, lookup_id: str, platform: str, use_uid: bool) -> str:
        base = lookup_id.strip().lower()
        prefix = "uid:" if use_uid else "name:"
        return f"{prefix}{base}@{platform}"

    def _find_player_key(self, group, player_name: str, platform: str, use_uid: bool) -> str:
        identifier, is_uid = self._parse_identifier(player_name)
        use_uid = use_uid or is_uid
        if platform:
            normalized = self._normalize_platform(platform)
            key = self._build_player_key(identifier, normalized, use_uid)
            return key if key in group.players else ""

        prefix = "uid:" if use_uid else "name:"
        key_base = f"{prefix}{identifier.strip().lower()}@"
        matches = [k for k in group.players.keys() if k.startswith(key_base)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            return "__MULTI__"
        return ""

    def _migrate_store_keys(self) -> None:
        changed = False
        for group_id, group in self._store.iter_groups():
            new_players = {}
            for key, record in group.players.items():
                platform = getattr(record, "platform", "PC") or "PC"
                normalized = self._normalize_platform(platform)
                lookup_id = getattr(record, "lookup_id", "") or record.player_name
                use_uid = bool(getattr(record, "use_uid", False))
                new_key = self._build_player_key(lookup_id, normalized, use_uid)
                record.platform = normalized
                record.lookup_id = lookup_id
                record.use_uid = use_uid
                new_players[new_key] = record
                if new_key != key:
                    changed = True
            group.players = new_players
        if changed:
            self._store.save()

    @filter.command("apextest")
    async def apextest(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        group_id = self._get_group_id(event)
        if group_id:
            success = await self._send_active_message(
                event.unified_msg_origin, "✅ Apex Legends 排名监控测试消息"
            )
            if success:
                yield self._plain(
                    event,
                    "\n".join(
                        [
                            self._time_line(),
                            "✅ Apex Legends 排名监控插件正常运行中，测试消息已发送到当前会话",
                        ]
                    ),
                )
            else:
                yield self._plain(
                    event,
                    "\n".join(
                        [
                            self._time_line(),
                            "⚠️ 插件指令可用，但当前平台或会话不支持主动消息推送",
                        ]
                    ),
                )
            return
        yield self._plain(event, 
            "\n".join([self._time_line(), "✅ Apex Legends 排名监控插件正常运行中"])
        )

    @filter.command("apexhelp", alias={"apex帮助"})
    async def apexhelp(self, event: AstrMessageEvent):
        async for result in self.apexrankhelp(event):
            yield result

    @filter.command("apexrankhelp")
    async def apexrankhelp(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        lines = self._build_apex_help_lines()
        try:
            image_path = self._render_apex_help_image()
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"帮助图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, "\n".join(lines))

    @filter.command("apexrank")
    async def apexrank(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        player_name, platform = self._parse_player_platform(event, player_name, platform)
        if not player_name:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 请提供玩家名称，例如: /apexrank PlayerName"])
            )
            return

        if self._is_blacklisted(player_name) or self._is_query_blocked(player_name):
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        f"⛔ 该ID（{player_name}）已被管理员加入黑名单，禁止查询",
                    ]
                )
            )
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        identifier, use_uid = self._parse_identifier(player_name)
        if not identifier:
            yield self._plain(
                event,
                "\n".join([self._time_line(), "⚠️ 请提供有效的玩家名称或 UID"]),
            )
            return

        try:
            player_data, used_platform = await self._api.fetch_player_stats_auto(
                identifier, platform, use_uid
            )
        except PlayerNotFoundError:
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        "⚠️ 未找到该玩家，请检查名称或指定平台",
                    ]
                )
            )
            return
        except ApexApiError as exc:
            logger.error(f"API 查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("查询", exc))
            return
        except Exception as exc:
            logger.error(f"API 查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("查询"))
            return

        if player_data.rank_score < self._config.min_valid_score:
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        f"⚠️ 查询到 {player_name} 的分数为 {player_data.rank_score}，低于最小有效分数 {self._config.min_valid_score}，可能是API错误，请稍后再试",
                    ]
                )
            )
            return

        player_data.platform = used_platform
        try:
            image_path = self._render_player_rank_image(player_data)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"生成玩家段位信息图片失败: {exc}")
            yield self._plain(event, self._format_player_rank_text(player_data))

    @filter.command("map", alias={"地图", "排位地图", "apexmap", "apexrankmap"})
    async def apexmap(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        try:
            rotation_info = await self._api.fetch_map_rotation_info()
        except ApexApiError as exc:
            logger.error(f"地图轮换查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("地图轮换查询", exc))
            return
        except Exception as exc:
            logger.error(f"地图轮换查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("地图轮换查询"))
            return

        try:
            image_path = self._render_map_rotation_image(rotation_info)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"地图轮换图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, self._format_map_rotation_text(rotation_info))

    @filter.command("匹配地图")
    async def apexmatchmap(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        try:
            rotation_info = await self._api.fetch_map_rotation_info()
        except ApexApiError as exc:
            logger.error(f"匹配地图轮换查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("匹配地图轮换查询", exc))
            return
        except Exception as exc:
            logger.error(f"匹配地图轮换查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("匹配地图轮换查询"))
            return

        try:
            image_path = self._render_map_rotation_image(
                rotation_info, mode="battle_royale"
            )
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"匹配地图轮换图片生成失败，已回退文字输出: {exc}")
            yield self._plain(
                event,
                self._format_map_rotation_text(rotation_info, mode="battle_royale"),
            )

    @filter.command("全天地图", alias={"全天排位地图", "今日地图", "今日排位地图", "dailymap"})
    async def apexdailymap(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        season_info = None
        try:
            season_info = await self._api.fetch_current_season_info()
        except Exception as exc:
            logger.warning(f"全天地图赛季信息获取失败，将仅使用 API 轮换学习状态: {exc}")

        try:
            schedule = await self._api.fetch_daily_map_schedule(
                "ranked",
                pool_state=self._daily_map_pool_state,
                season_info=season_info,
            )
            if schedule.pool_state is not None:
                self._daily_map_pool_state = schedule.pool_state
                self._save_daily_map_pool_state()
        except Exception as exc:
            logger.error(f"全天地图查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("全天地图查询"))
            return

        if not schedule.entries:
            yield self._plain(
                event,
                "\n".join(
                    [
                        self._time_line(),
                        "⚠️ 暂未获取到全天地图排期，请稍后再试",
                        "ℹ️ /map 当前地图仍使用 API 实时查询",
                    ]
                ),
            )
            return

        try:
            image_path = self._render_daily_map_schedule_image(schedule)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"全天地图图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, self._format_daily_map_schedule_text(schedule))

    @filter.command("apexpredator", alias={"apex猎杀", "猎杀"})
    async def apexpredator(self, event: AstrMessageEvent, platform: str = ""):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        raw_platform = (self._extract_command_args(event) or platform or "").strip()
        selected_platform = ""
        if raw_platform:
            if not self._is_platform_token(raw_platform):
                yield self._plain(
                    event,
                    "\n".join(
                        [
                            self._time_line(),
                            "⚠️ 平台仅支持 PC / PS4 / X1 / SWITCH",
                            "例：/apexpredator pc  或  /猎杀",
                        ]
                    ),
                )
                return
            selected_platform = self._normalize_platform(raw_platform)

        try:
            predator_info = await self._api.fetch_predator_info()
        except ApexApiError as exc:
            logger.error(f"猎杀线查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("猎杀线查询", exc))
            return
        except Exception as exc:
            logger.error(f"猎杀线查询失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("猎杀线查询"))
            return

        try:
            image_path = self._render_predator_info_image(predator_info)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"猎杀线图片生成失败，已回退文字输出: {exc}")
            yield self._plain(
                event, self._format_predator_info_text(predator_info, selected_platform)
            )

    @filter.command("apexseason")
    async def apexseason(self, event: AstrMessageEvent, season: str = ""):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        try:
            season_number = self._parse_season_query(event, season)
        except ValueError as exc:
            yield self._plain(
                event,
                "\n".join([self._time_line(), f"⚠️ {exc}"]),
            )
            return

        try:
            season_info = await self._api.fetch_season_info(season_number)
        except Exception as exc:
            logger.error(f"赛季时间查询失败: {exc}")
            yield self._plain(event, 
                "\n".join([self._time_line(), "❌ 查询失败：无法获取赛季时间"])
            )
            return

        try:
            image_path = self._render_season_info_image(season_info)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"赛季图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, self._format_season_info(season_info))

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def _season_keyword_listener(self, event: AstrMessageEvent):
        text = (getattr(event, "message_str", "") or "").strip()
        if not text:
            return
        raw = text.lstrip()
        if raw.startswith(("/", "／")):
            return
        first = raw.split(maxsplit=1)[0].lstrip("/").lstrip("／").strip()
        if first and first.lower() in self._KEYWORD_COMMAND_BLOCKLIST:
            # 避免同一条消息同时触发指令和关键词监听。
            return
        if "赛季" not in raw:
            return

        group_id = self._get_group_id(event)
        if group_id and self._is_season_keyword_disabled(group_id):
            return

        if self._guard_access(event):
            return

        try:
            season_info = await self._api.fetch_current_season_info()
        except Exception as exc:
            logger.error(f"赛季时间查询失败: {exc}")
            return

        try:
            image_path = self._render_season_info_image(season_info)
            yield self._image(event, image_path)
            if group_id:
                yield self._plain(event, "🔕 关闭赛季关键词回复：/赛季关闭")
            return
        except Exception as exc:
            logger.error(f"赛季关键词图片生成失败，已回退文字输出: {exc}")

        message = self._format_season_info(season_info)
        if group_id:
            message = "\n".join([message, "🔕 关闭赛季关键词回复：/赛季关闭"])
        yield self._plain(event, message)

    @filter.command("赛季关闭")
    async def season_keyword_off(self, event: AstrMessageEvent):
        deny = self._guard_access(event, require_group=True)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return
        admin_deny = self._guard_admin(event)
        if admin_deny:
            yield self._plain(event, "\n".join([self._time_line(), admin_deny]))
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 此命令仅适用于群聊，请在群聊中使用"])
            )
            return

        self._set_season_keyword_disabled(group_id, True)
        yield self._plain(event, 
            "\n".join([self._time_line(), "🔕 已关闭本群赛季关键词自动回复"])
        )

    @filter.command("赛季开启")
    async def season_keyword_on(self, event: AstrMessageEvent):
        deny = self._guard_access(event, require_group=True)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return
        admin_deny = self._guard_admin(event)
        if admin_deny:
            yield self._plain(event, "\n".join([self._time_line(), admin_deny]))
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 此命令仅适用于群聊，请在群聊中使用"])
            )
            return

        self._set_season_keyword_disabled(group_id, False)
        yield self._plain(event, 
            "\n".join([self._time_line(), "✅ 已开启本群赛季关键词自动回复"])
        )

    @filter.command("apexrankwatch")
    async def apexrankwatch(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        deny = self._guard_access(event, require_group=True)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        player_name, platform = self._parse_player_platform(event, player_name, platform)
        if not player_name:
            yield self._plain(event, 
                "\n".join(
                    [self._time_line(), "⚠️ 请提供要监控的玩家名称，例如: /apexrankwatch PlayerName"]
                )
            )
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 此命令仅适用于群聊，请在群聊中使用"])
            )
            return

        if self._is_blacklisted(player_name) or self._is_query_blocked(player_name):
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        f"⛔ 该ID（{player_name}）已被管理员加入黑名单，禁止监控",
                    ]
                )
            )
            return

        if not self._config.api_key:
            yield self._plain(event, self._missing_api_key_text())
            return

        identifier, use_uid = self._parse_identifier(player_name)
        if not identifier:
            yield self._plain(
                event,
                "\n".join([self._time_line(), "⚠️ 请提供有效的玩家名称或 UID"]),
            )
            return

        try:
            player_data, used_platform = await self._api.fetch_player_stats_auto(
                identifier, platform, use_uid
            )
        except PlayerNotFoundError:
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        "⚠️ 未找到该玩家，请检查名称或指定平台",
                    ]
                )
            )
            return
        except ApexApiError as exc:
            logger.error(f"添加群监控失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("添加监控", exc))
            return
        except Exception as exc:
            logger.error(f"添加群监控失败: {exc}")
            yield self._plain(event, self._api_request_failed_text("添加监控"))
            return

        if player_data.rank_score < self._config.min_valid_score:
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        f"⚠️ 查询到 {player_name} 的分数为 {player_data.rank_score}，低于最小有效分数 {self._config.min_valid_score}，可能是API错误，请稍后再试",
                    ]
                )
            )
            return

        normalized_platform = self._normalize_platform(used_platform)
        player_key = self._build_player_key(identifier, normalized_platform, use_uid)
        group = self._store.ensure_group(group_id, event.unified_msg_origin)

        if player_key in group.players:
            yield self._plain(event, f"本群已经在监控 {player_name} 的排名变化了")
            return

        record = PlayerRecord(
            player_name=player_data.name,
            platform=normalized_platform,
            lookup_id=identifier,
            use_uid=use_uid,
            rank_score=player_data.rank_score,
            rank_name=player_data.rank_name,
            rank_div=player_data.rank_div,
            global_rank_percent=player_data.global_rank_percent,
            selected_legend=player_data.selected_legend,
            legend_kills_percent=(
                player_data.legend_kills_rank.global_percent
                if player_data.legend_kills_rank
                else ""
            ),
            last_checked=now_epoch_ms(),
        )
        self._store.set_player(group_id, player_key, record)
        self._store.save()

        await self._send_active_message(
            event.unified_msg_origin, f"✅ 测试消息: 已添加对 {player_data.name} 的排名监控"
        )

        player_data.platform = normalized_platform
        success_text = "\n".join(
            [
                self._time_line(),
                f"✅ 成功添加对 {player_data.name} 的排名监控",
                f"🖥️ 平台: {self._format_platform(normalized_platform)}",
                f"🏆 当前排名: {self._get_rank_display_text(player_data)}",
            ]
        )
        try:
            image_path = self._render_monitor_added_image(player_data, normalized_platform)
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"监控添加确认图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, success_text)

    @filter.command("apexranklist")
    async def apexranklist(self, event: AstrMessageEvent):
        deny = self._guard_access(event, require_group=True)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 此命令仅适用于群聊，请在群聊中使用"])
            )
            return

        group = self._store.get_group(group_id)
        if group and event.unified_msg_origin and group.origin != event.unified_msg_origin:
            group.origin = event.unified_msg_origin
            self._store.save()
        if not group or not group.players:
            yield self._plain(event, 
                "\n".join([self._time_line(), "ℹ️ 本群目前没有监控任何玩家的排名"])
            )
            return

        response_lines = self._build_rank_watch_list_lines(group.players.values())
        try:
            image_path = self._render_rank_watch_list_image(group.players.values())
            yield self._image(event, image_path)
        except Exception as exc:
            logger.error(f"群监控列表图片生成失败，已回退文字输出: {exc}")
            yield self._plain(event, "\n".join(response_lines))

    @filter.command("apexrankremove")
    async def apexrankremove(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        deny = self._guard_access(event, require_group=True)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        player_name, platform = self._parse_player_platform(event, player_name, platform)
        if not player_name:
            yield self._plain(event, 
                "\n".join(
                    [self._time_line(), "⚠️ 请提供要移除监控的玩家名称，例如: /apexrankremove PlayerName"]
                )
            )
            return

        group_id = self._get_group_id(event)
        if not group_id:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 此命令仅适用于群聊，请在群聊中使用"])
            )
            return
        identifier, use_uid = self._parse_identifier(player_name)
        if not identifier:
            yield self._plain(
                event,
                "\n".join([self._time_line(), "⚠️ 请提供有效的玩家名称或 UID"]),
            )
            return

        group = self._store.get_group(group_id)
        if group and event.unified_msg_origin and group.origin != event.unified_msg_origin:
            group.origin = event.unified_msg_origin
            self._store.save()
        if not group:
            yield self._plain(event, 
                "\n".join(
                    [self._time_line(), f"ℹ️ 本群没有监控 {player_name} 的排名"]
                )
            )
            return

        lookup_name = f"uid:{identifier}" if use_uid else identifier
        player_key = self._find_player_key(group, lookup_name, platform, use_uid)
        if player_key == "__MULTI__":
            yield self._plain(event, 
                "\n".join(
                    [
                        self._time_line(),
                        "⚠️ 检测到同名多平台监控，请指定平台，例如: /apexrankremove PlayerName pc",
                    ]
                )
            )
            return
        if not player_key or not self._store.remove_player(group_id, player_key):
            yield self._plain(event, 
                "\n".join(
                    [self._time_line(), f"ℹ️ 本群没有监控 {player_name} 的排名"]
                )
            )
            return

        self._store.save()
        yield self._plain(event, 
            "\n".join([self._time_line(), f"✅ 已移除本群对 {player_name} 的排名监控"])
        )

    @filter.command("apexblacklist")
    async def apexblacklist(self, event: AstrMessageEvent, action: str = "", player_name: str = ""):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return
        admin_deny = self._guard_admin(event)
        if admin_deny:
            yield self._plain(event, "\n".join([self._time_line(), admin_deny]))
            return

        action, player_name = self._parse_blacklist_command(event, action, player_name)
        action_lower = action.lower()
        config_blacklist = self._get_config_blacklist()

        def _format_items(items: set[str]) -> str:
            if not items:
                return "无"
            return "，".join(sorted(items))

        if not action or action_lower in {"help", "?", "h", "帮助"}:
            lines = [
                self._time_line(),
                "🚫 Apex 黑名单管理（管理员）",
                "用法：/apexblacklist add <玩家ID>",
                "用法：/apexblacklist remove <玩家ID>",
                "用法：/apexblacklist list",
                "用法：/apexblacklist clear",
                f"配置黑名单：{_format_items(config_blacklist)}",
                f"动态黑名单：{_format_items(self._runtime_blacklist)}",
                "提示：配置黑名单需在插件配置中修改，动态黑名单可用本命令管理",
            ]
            yield self._plain(event, "\n".join(lines))
            return

        if action_lower in {"list", "ls", "查看", "列表"}:
            lines = [
                self._time_line(),
                "🚫 Apex 黑名单列表",
                f"配置黑名单：{_format_items(config_blacklist)}",
                f"动态黑名单：{_format_items(self._runtime_blacklist)}",
            ]
            yield self._plain(event, "\n".join(lines))
            return

        if action_lower in {"clear", "清空", "clean"}:
            if not self._runtime_blacklist:
                yield self._plain(event, "\n".join([self._time_line(), "ℹ️ 动态黑名单已为空"]))
                return
            self._runtime_blacklist.clear()
            self._save_settings()
            yield self._plain(event, "\n".join([self._time_line(), "✅ 已清空动态黑名单"]))
            return

        items = self._split_blacklist_items(player_name)
        if not items:
            yield self._plain(event, 
                "\n".join([self._time_line(), "⚠️ 请提供玩家ID，例如：/apexblacklist add PlayerName"])
            )
            return

        if action_lower in {"add", "+", "新增", "添加", "加入"}:
            added = []
            existed_config = []
            existed_runtime = []
            for raw in items:
                name = self._normalize_lookup_value(raw)
                if not name:
                    continue
                if name in config_blacklist:
                    existed_config.append(name)
                    continue
                if name in self._runtime_blacklist:
                    existed_runtime.append(name)
                    continue
                self._runtime_blacklist.add(name)
                added.append(name)
            if added:
                self._save_settings()
            lines = [
                self._time_line(),
                f"✅ 已添加 {len(added)} 个黑名单ID",
            ]
            if added:
                lines.append(f"新增：{_format_items(set(added))}")
            if existed_config:
                lines.append(f"已在配置黑名单：{_format_items(set(existed_config))}")
            if existed_runtime:
                lines.append(f"已在动态黑名单：{_format_items(set(existed_runtime))}")
            yield self._plain(event, "\n".join(lines))
            return

        if action_lower in {"remove", "del", "delete", "rm", "-", "移除", "删除"}:
            removed = []
            not_found = []
            in_config = []
            for raw in items:
                name = self._normalize_lookup_value(raw)
                if not name:
                    continue
                if name in self._runtime_blacklist:
                    self._runtime_blacklist.remove(name)
                    removed.append(name)
                elif name in config_blacklist:
                    in_config.append(name)
                else:
                    not_found.append(name)
            if removed:
                self._save_settings()
            lines = [
                self._time_line(),
                f"✅ 已移除 {len(removed)} 个动态黑名单ID",
            ]
            if removed:
                lines.append(f"移除：{_format_items(set(removed))}")
            if in_config:
                lines.append(f"配置黑名单需在配置中删除：{_format_items(set(in_config))}")
            if not_found:
                lines.append(f"未找到：{_format_items(set(not_found))}")
            yield self._plain(event, "\n".join(lines))
            return

        yield self._plain(event, 
            "\n".join([self._time_line(), "⚠️ 未识别的操作，请使用 add/remove/list/clear"])
        )

    @filter.command("apex监控")
    async def apexrankwatch_cn(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrankwatch(event, player_name, platform):
            yield result

    @filter.command("apex列表")
    async def apexranklist_cn(self, event: AstrMessageEvent):
        async for result in self.apexranklist(event):
            yield result

    @filter.command("apex移除")
    async def apexrankremove_cn(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrankremove(event, player_name, platform):
            yield result

    @filter.command("apex查询")
    async def apexrank_query_cn(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrank(event, player_name, platform):
            yield result

    @filter.command("视奸")
    async def apexrank_query_cn_alt(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrank(event, player_name, platform):
            yield result

    @filter.command("持续视奸")
    async def apexrankwatch_cn_alt(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrankwatch(event, player_name, platform):
            yield result

    @filter.command("取消持续视奸")
    async def apexrankremove_cn_alt(self, event: AstrMessageEvent, player_name: str = "", platform: str = ""):
        async for result in self.apexrankremove(event, player_name, platform):
            yield result


    @filter.command("apex赛季")
    async def apexseason_cn(self, event: AstrMessageEvent, season: str = ""):
        async for result in self.apexseason(event, season):
            yield result

    @filter.command("新赛季")
    async def apexseason_new_cn(self, event: AstrMessageEvent, season: str = ""):
        async for result in self.apexseason(event, season):
            yield result

    @filter.command("apex测试")
    async def apextest_cn(self, event: AstrMessageEvent):
        async for result in self.apextest(event):
            yield result

    @filter.command("apex黑名单")
    async def apexblacklist_cn(self, event: AstrMessageEvent, action: str = "", player_name: str = ""):
        async for result in self.apexblacklist(event, action, player_name):
            yield result

    @filter.command("不准视奸")
    async def apexblacklist_cn_alt(self, event: AstrMessageEvent, action: str = "", player_name: str = ""):
        async for result in self.apexblacklist(event, action, player_name):
            yield result

    @filter.command("apexban")
    async def apexblacklist_en_alt(self, event: AstrMessageEvent, action: str = "", player_name: str = ""):
        async for result in self.apexblacklist(event, action, player_name):
            yield result

    async def _poll_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"轮询任务执行失败: {exc}")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._config.check_interval * 60
                )
            except asyncio.TimeoutError:
                continue

    async def _poll_fetch_player(
        self,
        group_id: str,
        group_origin: str,
        player: PlayerRecord,
        platform: str,
        lookup_id: str,
        use_uid: bool,
    ) -> PollFetchResult:
        async with self._poll_semaphore:
            try:
                player_data, _ = await self._api.fetch_player_stats_auto(
                    lookup_id, platform, use_uid
                )
                return PollFetchResult(
                    group_id=group_id,
                    group_origin=group_origin,
                    player=player,
                    platform=platform,
                    player_data=player_data,
                )
            except PlayerNotFoundError:
                return PollFetchResult(
                    group_id=group_id,
                    group_origin=group_origin,
                    player=player,
                    platform=platform,
                    not_found=True,
                )
            except Exception as exc:
                return PollFetchResult(
                    group_id=group_id,
                    group_origin=group_origin,
                    player=player,
                    platform=platform,
                    error=exc,
                )

    def _should_skip_poll_player(self, player: PlayerRecord) -> bool:
        lookup_id = getattr(player, "lookup_id", "") or ""
        candidates = [player.player_name, lookup_id]
        for candidate in candidates:
            if not candidate:
                continue
            if self._is_blacklisted(candidate) or self._is_query_blocked(candidate):
                return True
        return False

    async def _poll_once(self) -> None:
        if not self._config.api_key:
            return

        poll_targets = []
        for group_id, group in self._store.iter_groups():
            for _, player in list(group.players.items()):
                if self._should_skip_poll_player(player):
                    lookup_id = getattr(player, "lookup_id", "") or player.player_name
                    logger.warning(f"跳过黑名单ID的定时检查: {lookup_id}")
                    continue

                platform = getattr(player, "platform", "PC") or "PC"
                use_uid = bool(getattr(player, "use_uid", False))
                lookup_id = getattr(player, "lookup_id", "") or player.player_name
                poll_targets.append(
                    (
                        group_id,
                        group.origin,
                        player,
                        platform,
                        lookup_id,
                        use_uid,
                    )
                )

        if not poll_targets:
            return

        queue: asyncio.Queue[tuple[str, str, PlayerRecord, str, str, bool]] = asyncio.Queue()
        for target in poll_targets:
            queue.put_nowait(target)

        results: list[PollFetchResult] = []

        async def worker() -> None:
            while True:
                try:
                    target = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    result = await self._poll_fetch_player(*target)
                    results.append(result)
                finally:
                    queue.task_done()

        worker_count = min(self._poll_concurrency, len(poll_targets))
        workers = [asyncio.create_task(worker()) for _ in range(worker_count)]
        try:
            await queue.join()
        finally:
            await asyncio.gather(*workers, return_exceptions=True)

        dirty = False
        fatal_api_error: ApexApiError | None = None
        for result in results:
            player = result.player
            platform = result.platform
            if result.not_found:
                logger.warning(f"玩家 {player.player_name} 在平台 {platform} 未找到")
                continue
            if result.error:
                if isinstance(result.error, ApexApiError):
                    fatal_api_error = fatal_api_error or result.error
                    continue
                logger.error(f"检查玩家 {player.player_name} 排名失败: {result.error}")
                continue
            player_data = result.player_data
            if player_data is None:
                continue

            new_score = player_data.rank_score
            old_score = player.rank_score
            is_valid_score = new_score >= self._config.min_valid_score
            is_abnormal_drop = is_score_drop_abnormal(old_score, new_score)
            is_season_reset = is_likely_season_reset(old_score, new_score)

            if is_valid_score and not is_abnormal_drop and new_score != old_score:
                diff = new_score - old_score
                diff_text = f"上升 {diff}" if diff > 0 else f"下降 {abs(diff)}"

                player.rank_score = new_score
                player.rank_name = player_data.rank_name
                player.rank_div = player_data.rank_div
                player.global_rank_percent = player_data.global_rank_percent
                player.selected_legend = player_data.selected_legend
                player.legend_kills_percent = (
                    player_data.legend_kills_rank.global_percent
                    if player_data.legend_kills_rank
                    else ""
                )
                player.last_checked = now_epoch_ms()
                dirty = True

                date_str = self._now_str()
                new_rank_display = (
                    f"{player.rank_name} {player.rank_div}"
                    if player.rank_div
                    else player.rank_name
                )

                message_lines = [
                    "📈 Apex 排位分数变化",
                    f"🕒 时间: {date_str}",
                    f"👤 玩家: {player.player_name}",
                    f"🖥️ 平台: {self._format_platform(platform)}",
                    f"🔢 原分数: {old_score}",
                    f"🔢 当前分数: {new_score}",
                    f"🏆 段位: {new_rank_display}",
                    f"📊 变动: {diff_text} 分",
                ]

                if is_season_reset:
                    message_lines.append("⚠️ 注意: 检测到大幅度分数下降，可能是赛季重置导致")

                if player_data.global_rank_percent and player_data.global_rank_percent != "未知":
                    message_lines.append(
                        f"🌎 全球排名: {player_data.global_rank_percent}%"
                    )

                if player_data.is_online and player_data.selected_legend:
                    message_lines.append(f"🎮 当前英雄: {player_data.selected_legend}")
                    if player_data.legend_kills_rank:
                        message_lines.append(
                            f"📊 击杀排名: 全球 {player_data.legend_kills_rank.global_percent}%"
                        )

                if player_data.is_online and player_data.current_state:
                    message_lines.append(f"🎯 当前状态: {player_data.current_state}")

                if result.group_origin:
                    sent_image = False
                    try:
                        image_path = self._render_rank_change_image(
                            player_data=player_data,
                            old_score=old_score,
                            new_score=new_score,
                            platform=platform,
                            is_season_reset=is_season_reset,
                        )
                        sent_image = await self._send_active_image(
                            result.group_origin, image_path
                        )
                    except Exception as exc:
                        logger.error(f"排位分数变化图片生成失败，已回退文字通知: {exc}")
                    if not sent_image:
                        await self._send_active_message(
                            result.group_origin, "\n".join(message_lines)
                        )
                else:
                    logger.warning(f"群 {result.group_id} 缺少 unified_msg_origin，无法主动推送通知")
            elif not is_valid_score:
                logger.warning(
                    f"玩家 {player.player_name} 的分数 {new_score} 无效，保留原分数 {old_score}"
                )
            elif is_abnormal_drop:
                logger.warning(
                    f"玩家 {player.player_name} 的分数从 {old_score} 下降到 {new_score}，可能是API错误"
                )

        if fatal_api_error is not None:
            logger.error(f"轮询阶段 API 认证/限流失败: {fatal_api_error.user_message}")
        if dirty:
            self._store.save()

    @staticmethod
    def _get_rank_display_text(player_data: ApexPlayerStats) -> str:
        rank_display = (
            f"{player_data.rank_name} {player_data.rank_div}"
            if player_data.rank_div
            else player_data.rank_name
        )
        return f"{rank_display} ({player_data.rank_score}分)"

    def _format_player_rank_text(self, player_data: ApexPlayerStats) -> str:
        date_str = self._now_str()
        rank_display = (
            f"{player_data.rank_name} {player_data.rank_div}"
            if player_data.rank_div
            else player_data.rank_name
        )

        lines = [
            "📊 Apex 段位信息",
            f"🕒 时间: {date_str}",
            f"👤 玩家: {player_data.name}",
            f"🖥️ 平台: {self._format_platform(player_data.platform)}",
            f"🆔 UID: {player_data.uid}" if player_data.uid else "🆔 UID: 未知",
            f"🏆 段位: {rank_display}",
            f"🔢 分数: {player_data.rank_score}",
        ]

        if player_data.global_rank_percent and player_data.global_rank_percent != "未知":
            lines.append(f"🌎 全球排名: {player_data.global_rank_percent}%")
        lines.append(f"👑 等级: {player_data.level}")

        if player_data.is_online:
            lines.append("🎮 在线状态: 在线")
            if player_data.selected_legend:
                lines.append(f"🎯 当前英雄: {player_data.selected_legend}")
            if player_data.is_in_lobby_or_match:
                lines.append(f"🎯 当前状态: {player_data.current_state}")
        else:
            lines.append("🎮 在线状态: 离线")

        return "\n".join(lines)

    def _build_apex_help_lines(self) -> list[str]:
        lines = [
            self._time_line(),
            "📋 Apex Rank Watch 帮助（/apexhelp 或 /apex帮助）",
            "——",
            "【查询】",
            "/apexrank <玩家|uid:...> [平台]  别名：/apex查询 /视奸",
            "例：/apexrank PlayerName pc",
            "——",
            "【监控（群聊）】",
            "/apexrankwatch <玩家|uid:...> [平台]  别名：/apex监控 /持续视奸",
            "/apexranklist  别名：/apex列表",
            "/apexrankremove <玩家|uid:...> [平台]  别名：/apex移除 /取消持续视奸",
            "——",
            "【信息】",
            "/apexseason [赛季号]  别名：/apex赛季 /新赛季",
            "/map  别名：/地图 /排位地图 /apexmap /apexrankmap（排位地图轮换）",
            "/全天地图（API 学习确认排位未来 24 小时地图）",
            "/匹配地图（三人赛地图轮换）",
            "/apexpredator [平台]  别名：/apex猎杀 /猎杀",
            "例：/apexseason 28  或  /apexseason current",
            "关键词：消息包含「赛季」自动回复（/赛季关闭，/赛季开启）",
            "——",
            "【管理】",
            "/apexblacklist <add|remove|list|clear> <玩家ID>  别名：/apex黑名单 /不准视奸 /apexban",
            "——",
            "【参数】",
            "平台：PC / PS4 / X1 / SWITCH（默认 PC；PC 无数据自动尝试其他平台）",
            "UUID：使用 uid: 或 uuid: 前缀（例：/apexrank uid:000000）",
            "——",
            f"⏱️ 监控间隔：{getattr(self._config, 'check_interval', 2)} 分钟",
            f"🔻 最小有效分：{getattr(self._config, 'min_valid_score', 1)} 分",
            "⚠️ 异常分数：仅当高分(>1000)掉到接近0分(<10)时判定为异常",
            "🛡️ 权限：群白名单、QQ 黑名单、主人 QQ、私聊开关",
        ]

        try:
            config_blacklist = self._get_config_blacklist()
        except Exception:
            config_blacklist = set()
        runtime_blacklist = getattr(self, "_runtime_blacklist", set())
        total_blacklist = len(config_blacklist) + len(runtime_blacklist)
        if total_blacklist:
            lines.append(
                "⛔ 黑名单说明：配置黑名单 "
                f"{len(config_blacklist)} 个，动态黑名单 {len(runtime_blacklist)} 个"
            )
            lines.append("⛔ 黑名单ID无法被查询或监控")

        query_blocklist = getattr(self._config, "query_blocklist", "")
        if query_blocklist:
            count = len(_split_csv(query_blocklist))
            lines.append(f"⛔ 禁止查询玩家：已设置 {count} 个玩家ID")
        return lines

    def _render_apex_help_image(self) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成帮助图片")

        output_dir = self._generated_card_output_dir("help_cards")
        output_dir.mkdir(parents=True, exist_ok=True)
        image = self._build_apex_help_card()
        output_path = output_dir / f"apex_help_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._cleanup_generated_images(output_dir, "apex_help_*.png", keep=8)
        return output_path

    def _build_apex_help_card(self):
        width, height = self._HELP_CARD_SIZE
        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        self._draw_rank_change_background(canvas)
        self._draw_rank_change_outer_frame(draw, width, height)
        self._draw_help_card_header(canvas, draw)

        for box, title, rows in self._help_card_sections():
            self._draw_help_section(draw, box, title, rows)
        return canvas.convert("RGB")

    def _help_card_sections(self) -> list[tuple[tuple[int, int, int, int], str, list[tuple[str, str]]]]:
        return [
            (
                (54, 250, 1068, 500),
                "查询",
                [
                    ("/apexrank 玩家 [平台]", "查询玩家段位、分数、在线状态"),
                    ("/apex查询 /视奸", "中文别名，默认 PC，支持 uid:"),
                ],
            ),
            (
                (54, 520, 1068, 900),
                "监控",
                [
                    ("/apexrankwatch 玩家 [平台]", "添加群内持续监控"),
                    ("/apexranklist /apex列表", "查看本群监控列表"),
                    ("/apexrankremove 玩家 [平台] /取消持续视奸", "移除指定玩家监控"),
                    ("/apex监控 /持续视奸", "添加监控中文别名"),
                ],
            ),
            (
                (54, 920, 1068, 1370),
                "信息",
                [
                    ("/map /地图 /排位地图", "排位地图轮换，默认输出图片"),
                    ("/全天地图", "API 学习确认排位未来 24 小时地图"),
                    ("/匹配地图", "三人赛地图轮换"),
                    ("/apexpredator [平台] /apex猎杀 /猎杀", "大师数量与猎杀底分"),
                    ("/apexseason /新赛季", "当前赛季结束时间"),
                    ("赛季关键词", "群消息包含赛季时自动回复"),
                ],
            ),
            (
                (54, 1390, 1068, 1680),
                "管理",
                [
                    ("/apexblacklist add 玩家ID", "加入动态黑名单"),
                    ("/apexblacklist list", "查看配置与动态黑名单"),
                    ("/赛季关闭 /赛季开启", "管理本群赛季关键词回复"),
                ],
            ),
            (
                (54, 1700, 1068, 2296),
                "参数",
                self._help_parameter_rows(),
            ),
        ]

    def _help_parameter_rows(self) -> list[tuple[str, str]]:
        try:
            config_blacklist = self._get_config_blacklist()
        except Exception:
            config_blacklist = set()
        runtime_blacklist = getattr(self, "_runtime_blacklist", set())
        query_blocklist = getattr(self._config, "query_blocklist", "")
        rows = [
            ("平台", "PC / PS4 / X1 / SWITCH；PC 无数据会自动尝试其他平台"),
            ("UID", "使用 uid: 或 uuid: 前缀，例如 /apexrank uid:000000"),
            ("监控间隔", f"{getattr(self._config, 'check_interval', 2)} 分钟"),
            ("最小有效分", f"{getattr(self._config, 'min_valid_score', 1)} 分"),
            ("异常分数", "仅当高分掉到接近 0 分时判定为异常"),
            ("权限", "支持群白名单、QQ 黑名单、主人 QQ、私聊开关"),
        ]
        if config_blacklist or runtime_blacklist:
            rows.append(("黑名单", f"配置 {len(config_blacklist)} 个，动态 {len(runtime_blacklist)} 个"))
        if query_blocklist:
            rows.append(("禁止查询", f"已设置 {len(_split_csv(query_blocklist))} 个玩家ID"))
        return rows

    def _draw_help_card_header(self, canvas, draw) -> None:
        box = (54, 54, 1068, 224)
        self._draw_rank_panel_base(draw, box, fill=(12, 14, 19, 238), outline_alpha=150)
        badge = self._apex_logo_badge(118)
        canvas.alpha_composite(badge, (92, 80))

        title = "Apex Rank Watch"
        subtitle = "QQ 命令帮助卡 · 图片化速查"
        title_font = self._fit_font(draw, title, 68, 48, 690, bold=True)
        subtitle_font = self._fit_font(draw, subtitle, 32, 24, 690, bold=True)
        self._draw_text_stroked(
            draw,
            (250, self._centered_text_y(draw, title, title_font, 70, 150)),
            title,
            title_font,
            fill=(248, 248, 244, 255),
            stroke_width=2,
        )
        self._draw_text_stroked(
            draw,
            (254, self._centered_text_y(draw, subtitle, subtitle_font, 150, 202)),
            subtitle,
            subtitle_font,
            fill=(205, 212, 222, 255),
        )
        draw.rectangle((780, 214, 928, 220), fill=(236, 48, 52, 220))

    def _draw_help_section(
        self,
        draw,
        box: tuple[int, int, int, int],
        title: str,
        rows: list[tuple[str, str]],
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 238), outline_alpha=110)
        title_font = self._font(38, bold=True)
        self._draw_text_stroked(
            draw,
            (box[0] + 34, box[1] + 24),
            title,
            title_font,
            fill=(246, 246, 241, 255),
            stroke_width=2,
        )
        draw.rectangle((box[0] + 34, box[1] + 74, box[0] + 142, box[1] + 80), fill=(232, 48, 52, 230))

        row_top = box[1] + 92
        available = max(1, box[3] - row_top - 22)
        row_height = max(54, available // max(1, len(rows)))
        for index, (command, desc) in enumerate(rows):
            top = row_top + index * row_height
            if top + 56 > box[3] - 16:
                break
            dot_y = top + 18
            draw.rounded_rectangle((box[0] + 34, dot_y - 6, box[0] + 46, dot_y + 6), radius=3, fill=(232, 48, 52, 235))
            command_font = self._fit_font(draw, command, 29, 21, box[2] - box[0] - 102, bold=True)
            desc_font = self._fit_font(draw, desc, 23, 17, box[2] - box[0] - 102)
            self._draw_text_stroked(
                draw,
                (box[0] + 60, top),
                command,
                command_font,
                fill=(250, 250, 246, 255),
            )
            self._draw_text_stroked(
                draw,
                (box[0] + 60, top + 34),
                desc,
                desc_font,
                fill=(172, 181, 194, 255),
            )

    def _build_rank_watch_list_lines(self, players_iter) -> list[str]:
        players = list(players_iter)
        response_lines = [self._time_line(), "📋 本群 Apex 排名监控列表"]
        for index, player in enumerate(players, start=1):
            rank_display = self._record_rank_display(player)
            platform = getattr(player, "platform", "PC") or "PC"
            response_lines.append(f"👤 玩家 {index}: {player.player_name}")
            response_lines.append(f"🖥️ 平台: {self._format_platform(platform)}")
            response_lines.append(f"🏆 段位: {rank_display}")
            response_lines.append(f"🔢 分数: {player.rank_score}")
            if player.global_rank_percent and player.global_rank_percent != "未知":
                response_lines.append(f"🌎 全球排名: {self._format_rank_percent(player.global_rank_percent)}")
            if player.selected_legend:
                response_lines.append(f"🎮 当前英雄: {player.selected_legend}")
                if player.legend_kills_percent:
                    response_lines.append(
                        f"📊 击杀排名: 全球 {self._format_rank_percent(player.legend_kills_percent)}"
                    )
            response_lines.append("➖ —")

        response_lines.append(f"🔢 总计: {len(players)} 个玩家")
        response_lines.append(f"⏱️ 检测间隔: {getattr(self._config, 'check_interval', 2)} 分钟")
        response_lines.append(
            "⚠️ 分数异常判断: 仅当高分(>1000)掉到接近0分(<10)时判定为异常"
        )
        response_lines.append(f"🔻 最小有效分数: {getattr(self._config, 'min_valid_score', 1)} 分")
        return response_lines

    def _render_rank_watch_list_image(self, players_iter) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成群监控列表图片")

        players = list(players_iter)
        output_dir = self._generated_card_output_dir("rank_watch_list_cards")
        output_dir.mkdir(parents=True, exist_ok=True)
        image = self._build_rank_watch_list_card(players)
        output_path = output_dir / f"rank_watch_list_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._cleanup_generated_images(output_dir, "rank_watch_list_*.png", keep=10)
        return output_path

    def _build_rank_watch_list_card(self, players: list[PlayerRecord]):
        width = self._MONITOR_LIST_CARD_WIDTH
        shown_players = players[: self._MONITOR_LIST_ROW_LIMIT]
        row_height = 118
        row_gap = 14
        footer_height = 92
        list_top = 366
        height = max(
            760,
            list_top + len(shown_players) * (row_height + row_gap) + footer_height,
        )
        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        self._draw_rank_change_background(canvas)
        self._draw_rank_change_outer_frame(draw, width, height)
        self._draw_monitor_list_header(canvas, draw, len(players))
        self._draw_monitor_list_summary(draw, len(players))

        y = list_top
        for index, player in enumerate(shown_players, start=1):
            self._draw_monitor_list_row(draw, (54, y, width - 54, y + row_height), index, player)
            y += row_height + row_gap

        footer_text = "时间均为北京时间"
        if len(players) > len(shown_players):
            footer_text = f"已展示前 {len(shown_players)} 位，还有 {len(players) - len(shown_players)} 位玩家未展示"
        footer_font = self._fit_font(draw, footer_text, 28, 20, width - 150, bold=True)
        self._draw_centered_stroked_text(
            draw,
            footer_text,
            footer_font,
            54,
            width - 54,
            height - 84,
            height - 40,
            (202, 210, 220, 255),
        )
        return canvas.convert("RGB")

    def _draw_monitor_list_header(self, canvas, draw, total_players: int) -> None:
        box = (54, 54, 1068, 224)
        self._draw_rank_panel_base(draw, box, fill=(12, 14, 19, 238), outline_alpha=150)
        badge = self._apex_logo_badge(118)
        canvas.alpha_composite(badge, (92, 80))

        title = "Apex 群监控列表"
        subtitle = f"{total_players} 位玩家 · 每 {getattr(self._config, 'check_interval', 2)} 分钟检测一次"
        title_font = self._fit_font(draw, title, 66, 46, 690, bold=True)
        subtitle_font = self._fit_font(draw, subtitle, 32, 23, 690, bold=True)
        self._draw_text_stroked(
            draw,
            (250, self._centered_text_y(draw, title, title_font, 70, 150)),
            title,
            title_font,
            fill=(248, 248, 244, 255),
            stroke_width=2,
        )
        self._draw_text_stroked(
            draw,
            (254, self._centered_text_y(draw, subtitle, subtitle_font, 150, 202)),
            subtitle,
            subtitle_font,
            fill=(205, 212, 222, 255),
        )
        draw.rectangle((760, 214, 930, 220), fill=(236, 48, 52, 220))

    def _draw_monitor_list_summary(self, draw, total_players: int) -> None:
        panels = [
            ((54, 244, 370, 338), "监控玩家", f"{total_players} 位"),
            ((388, 244, 734, 338), "检测间隔", f"{getattr(self._config, 'check_interval', 2)} 分钟"),
            ((752, 244, 1068, 338), "最小有效分", f"{getattr(self._config, 'min_valid_score', 1)} 分"),
        ]
        for box, label, value in panels:
            self._draw_mini_info_pill(draw, box, label, value)

    def _draw_monitor_list_row(
        self,
        draw,
        box: tuple[int, int, int, int],
        index: int,
        player: PlayerRecord,
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=92)
        rank_name = getattr(player, "rank_name", "") or "未知"
        rank_div = getattr(player, "rank_div", 0) or 0
        rank_icon = self._resolve_rank_icon_path(rank_name, rank_div)
        icon_box = (box[0] + 24, box[1] + 16, box[0] + 112, box[1] + 104)
        if rank_icon.exists():
            self._draw_image_asset(draw, rank_icon, icon_box)
            if rank_div and "_" not in rank_icon.stem and rank_icon.stem not in {"master", "predator"}:
                div_font = self._font(28, bold=True)
                self._draw_centered_stroked_text(
                    draw,
                    str(rank_div),
                    div_font,
                    icon_box[0] + 26,
                    icon_box[2] - 26,
                    icon_box[1] + 40,
                    icon_box[3] - 18,
                    (255, 255, 255, 255),
                    stroke_width=2,
                )
        else:
            self._draw_icon_octagon(draw, icon_box)
            self._draw_rank_icon(draw, "target", icon_box)

        name = str(getattr(player, "player_name", "") or "未知玩家")
        platform = self._format_platform(getattr(player, "platform", "PC") or "PC")
        checked_at = self._format_record_checked_at(getattr(player, "last_checked", 0))
        rank_text = self._record_rank_display(player)
        name_text = f"{index}. {name}"
        meta_text = f"{platform} · {checked_at} · {rank_text}"
        name_font = self._fit_font(draw, name_text, 34, 24, 390, bold=True)
        meta_font = self._fit_font(draw, meta_text, 25, 18, 430)
        text_left = box[0] + 132
        self._draw_text_stroked(draw, (text_left, box[1] + 18), name_text, name_font, fill=(250, 250, 246, 255))
        self._draw_text_stroked(draw, (text_left, box[1] + 64), meta_text, meta_font, fill=(190, 198, 210, 255))

        legend_name = str(getattr(player, "selected_legend", "") or "未知")
        legend_icon = self._resolve_legend_icon_path(legend_name)
        legend_box = (box[0] + 600, box[1] + 22, box[0] + 674, box[1] + 96)
        self._draw_icon_octagon(draw, legend_box)
        if legend_icon.exists():
            self._draw_image_asset(draw, legend_icon, legend_box, clip_octagon=True)
        else:
            self._draw_rank_icon(draw, "legend", legend_box)
        legend_font = self._fit_font(draw, legend_name, 26, 18, 140, bold=True)
        self._draw_centered_stroked_text(
            draw,
            legend_name,
            legend_font,
            box[0] + 682,
            box[0] + 830,
            box[1] + 34,
            box[1] + 86,
            (236, 240, 246, 255),
        )

        score_text = self._format_rank_score_number(getattr(player, "rank_score", 0))
        score_font = self._fit_font(draw, score_text, 46, 30, 190, bold=True)
        label_font = self._font(22, bold=True)
        score_left = box[2] - 222
        self._draw_centered_stroked_text(
            draw,
            "当前分数",
            label_font,
            score_left,
            box[2] - 28,
            box[1] + 18,
            box[1] + 48,
            (176, 184, 196, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            score_text,
            score_font,
            score_left,
            box[2] - 28,
            box[1] + 48,
            box[1] + 104,
            (250, 250, 246, 255),
            stroke_width=2,
        )
        draw.rectangle((score_left + 28, box[3] - 18, box[2] - 58, box[3] - 12), fill=(221, 48, 52, 185))

    def _render_monitor_added_image(self, player_data: ApexPlayerStats, platform: str) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成监控添加确认图片")

        output_dir = self._generated_card_output_dir("monitor_added_cards")
        output_dir.mkdir(parents=True, exist_ok=True)
        image = self._build_monitor_added_card(player_data, platform)
        output_path = output_dir / f"monitor_added_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._cleanup_generated_images(output_dir, "monitor_added_*.png", keep=12)
        return output_path

    def _build_monitor_added_card(self, player_data: ApexPlayerStats, platform: str):
        width, height = self._MONITOR_ADDED_CARD_SIZE
        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        self._draw_rank_change_background(canvas)
        self._draw_rank_change_outer_frame(draw, width, height)
        self._draw_monitor_added_header(canvas, draw)
        self._draw_monitor_added_status(draw)

        rank_display = (
            f"{player_data.rank_name} {player_data.rank_div}"
            if player_data.rank_div
            else player_data.rank_name or "未知"
        )
        self._draw_player_profile_panel(draw, (54, 482, 518, 792), player_data)
        self._draw_rank_badge_panel(
            draw,
            (536, 482, 1070, 792),
            "当前段位",
            rank_display,
            player_data.rank_name,
            player_data.rank_div,
            secondary_value=f"{self._format_rank_score_number(player_data.rank_score)} 分",
        )
        self._draw_mini_info_pill(draw, (54, 820, 370, 920), "平台", self._format_platform(platform))
        self._draw_monitor_added_legend_pill(
            draw,
            (388, 820, 734, 920),
            player_data.selected_legend or "未知",
        )
        self._draw_mini_info_pill(draw, (752, 820, 1068, 920), "检测间隔", f"{getattr(self._config, 'check_interval', 2)} 分钟")
        return canvas.convert("RGB")

    def _draw_monitor_added_header(self, canvas, draw) -> None:
        box = (54, 54, 1068, 224)
        self._draw_rank_panel_base(draw, box, fill=(12, 14, 19, 238), outline_alpha=150)
        badge = self._apex_logo_badge(118)
        canvas.alpha_composite(badge, (92, 80))
        title = "Apex 监控已添加"
        subtitle = "当排位分数变化时会自动推送图片通知"
        title_font = self._fit_font(draw, title, 66, 46, 690, bold=True)
        subtitle_font = self._fit_font(draw, subtitle, 32, 22, 690, bold=True)
        self._draw_text_stroked(
            draw,
            (250, self._centered_text_y(draw, title, title_font, 70, 150)),
            title,
            title_font,
            fill=(248, 248, 244, 255),
            stroke_width=2,
        )
        self._draw_text_stroked(
            draw,
            (254, self._centered_text_y(draw, subtitle, subtitle_font, 150, 202)),
            subtitle,
            subtitle_font,
            fill=(205, 212, 222, 255),
        )
        draw.rectangle((790, 214, 938, 220), fill=(236, 48, 52, 220))

    def _draw_monitor_added_status(self, draw) -> None:
        time_box = (54, 244, 1068, 338)
        self._draw_rank_panel_base(draw, time_box, fill=(14, 17, 23, 240), outline_alpha=120)
        self._draw_clock_icon(draw, (92, 265), 28)
        label_font = self._font(32, bold=True)
        value = self._now_str()
        value_font = self._fit_font(draw, value, 34, 24, 560, bold=True)
        self._draw_text_stroked(
            draw,
            (176, self._centered_text_y(draw, "时间:", label_font, time_box[1], time_box[3])),
            "时间:",
            label_font,
            fill=(226, 226, 220, 255),
        )
        self._draw_text_stroked(
            draw,
            (286, self._centered_text_y(draw, value, value_font, time_box[1], time_box[3])),
            value,
            value_font,
            fill=(244, 244, 239, 255),
        )

        status_box = (54, 358, 1068, 460)
        self._draw_rank_panel_base(draw, status_box, fill=(36, 16, 18, 242), outline_alpha=150)
        status = "已加入本群排位监控"
        status_font = self._fit_font(draw, status, 46, 30, 740, bold=True)
        self._draw_centered_stroked_text(
            draw,
            status,
            status_font,
            status_box[0],
            status_box[2],
            status_box[1],
            status_box[3],
            (255, 246, 236, 255),
            stroke_width=2,
        )

    def _draw_mini_info_pill(
        self,
        draw,
        box: tuple[int, int, int, int],
        label: str,
        value: str,
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 238), outline_alpha=92)
        label_font = self._fit_font(draw, label, 24, 18, box[2] - box[0] - 34, bold=True)
        value_font = self._fit_font(draw, str(value), 30, 21, box[2] - box[0] - 34, bold=True)
        self._draw_centered_stroked_text(
            draw,
            label,
            label_font,
            box[0] + 12,
            box[2] - 12,
            box[1] + 12,
            box[1] + 38,
            (172, 181, 194, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            str(value),
            value_font,
            box[0] + 12,
            box[2] - 12,
            box[1] + 38,
            box[3] - 10,
            (250, 250, 246, 255),
            stroke_width=2,
        )

    def _draw_monitor_added_legend_pill(
        self,
        draw,
        box: tuple[int, int, int, int],
        legend_name: str,
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 238), outline_alpha=92)
        icon_box = (box[0] + 22, box[1] + 14, box[0] + 100, box[1] + 92)
        self._draw_icon_octagon(draw, icon_box)
        legend_icon = self._resolve_legend_icon_path(legend_name)
        if legend_icon.exists():
            self._draw_image_asset(draw, legend_icon, icon_box, clip_octagon=True)
        else:
            self._draw_rank_icon(draw, "legend", icon_box)

        label_font = self._fit_font(draw, "当前英雄", 23, 18, box[2] - box[0] - 134, bold=True)
        value_font = self._fit_font(draw, legend_name, 30, 21, box[2] - box[0] - 134, bold=True)
        text_left = box[0] + 116
        text_right = box[2] - 18
        self._draw_centered_stroked_text(
            draw,
            "当前英雄",
            label_font,
            text_left,
            text_right,
            box[1] + 16,
            box[1] + 42,
            (172, 181, 194, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            legend_name,
            value_font,
            text_left,
            text_right,
            box[1] + 44,
            box[3] - 12,
            (250, 250, 246, 255),
            stroke_width=2,
        )

    def _generated_card_output_dir(self, name: str) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / name
        return Path(data_dir) / name

    def _cleanup_generated_images(self, output_dir: Path, pattern: str, keep: int) -> None:
        try:
            files = sorted(
                output_dir.glob(pattern),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理生成图片缓存失败: {exc}")

    @staticmethod
    def _record_rank_display(player: PlayerRecord) -> str:
        rank_name = str(getattr(player, "rank_name", "") or "未知")
        rank_div = getattr(player, "rank_div", 0) or 0
        if rank_div:
            return f"{rank_name} {rank_div}"
        return rank_name

    def _format_record_checked_at(self, value: int) -> str:
        try:
            raw = int(value)
            if raw <= 0:
                return "未知时间"
            seconds = raw / 1000 if raw > 10_000_000_000 else raw
            dt = datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(SHANGHAI_TZ)
            return dt.strftime("%m-%d %H:%M")
        except Exception:
            return "未知时间"

    def _render_player_rank_image(self, player_data: ApexPlayerStats) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成玩家段位信息图片")

        output_dir = self._player_rank_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        image = self._build_player_rank_card(player_data)
        output_path = output_dir / f"player_rank_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._cleanup_old_player_rank_cards(output_dir)
        return output_path

    def _player_rank_card_output_dir(self) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / "player_rank_cards"
        return Path(data_dir) / "player_rank_cards"

    def _cleanup_old_player_rank_cards(self, output_dir: Path, keep: int = 16) -> None:
        try:
            files = sorted(
                output_dir.glob("player_rank_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理玩家段位信息缓存图片失败: {exc}")

    def _build_player_rank_card(self, player_data: ApexPlayerStats):
        width, height = self._PLAYER_RANK_CARD_SIZE
        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        self._draw_rank_change_background(canvas)
        self._draw_rank_change_outer_frame(draw, width, height)
        self._draw_player_rank_header(draw, width)
        self._draw_rank_change_time_bar(draw)

        rank_display = (
            f"{player_data.rank_name} {player_data.rank_div}"
            if player_data.rank_div
            else player_data.rank_name or "未知"
        )
        status = player_data.current_state or ("在线" if player_data.is_online else "离线")
        platform = self._format_platform(player_data.platform)
        global_percent = self._format_rank_percent(player_data.global_rank_percent)
        legend_name = player_data.selected_legend or "未知"

        self._draw_player_profile_panel(draw, (54, 360, 518, 670), player_data)
        self._draw_rank_badge_panel(
            draw,
            (536, 360, 1070, 670),
            "当前段位",
            rank_display,
            player_data.rank_name,
            player_data.rank_div,
        )
        self._draw_player_score_panel(draw, (54, 690, 1070, 1000), player_data, rank_display, global_percent)
        self._draw_rank_detail_panel(
            draw,
            (54, 1010, 370, 1340),
            "legend",
            "当前英雄",
            legend_name,
        )
        self._draw_rank_info_panel(
            draw,
            (376, 1010, 720, 1340),
            "crown",
            "等级",
            str(player_data.level if player_data.level is not None else "未知"),
            secondary_value=f"平台 {platform}",
        )
        self._draw_rank_status_panel(draw, (724, 1010, 1070, 1340), status)
        return canvas.convert("RGB")

    def _draw_player_rank_header(self, draw, width: int) -> None:
        title_box = (54, 54, width - 54, 224)
        self._draw_rank_panel_base(draw, title_box, fill=(12, 14, 19, 238), outline_alpha=150)
        self._draw_player_card_icon(draw, (94, 82, 220, 190))
        title = "Apex 玩家档案"
        title_font = self._fit_font(draw, title, 78, 50, width - 310, bold=True)
        self._draw_text_stroked(
            draw,
            (260, self._centered_text_y(draw, title, title_font, 68, 202)),
            title,
            title_font,
            fill=(248, 248, 244, 255),
            stroke_width=2,
        )
        draw.rectangle((508, 217, 620, 222), fill=(236, 48, 52, 220))

    def _draw_player_card_icon(self, draw, box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        red = (238, 62, 66, 255)
        white = (230, 232, 229, 255)
        self._draw_icon_octagon(draw, box)
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        draw.ellipse((cx - 22, cy - 42, cx + 22, cy + 2), fill=white)
        draw.pieslice((cx - 54, cy - 6, cx + 54, cy + 78), 180, 360, fill=white)
        draw.rectangle((cx - 54, cy + 36, cx + 54, cy + 78), fill=white)
        draw.line((left + 18, bottom - 14, right - 14, top + 18), fill=red, width=8)
        draw.polygon(
            [(right - 14, top + 18), (right - 18, top + 50), (right - 46, top + 24)],
            fill=red,
        )

    def _draw_player_profile_panel(
        self,
        draw,
        box: tuple[int, int, int, int],
        player_data: ApexPlayerStats,
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=105)
        avatar_box = (box[0] + 38, box[1] + 48, box[0] + 192, box[1] + 202)
        self._draw_icon_octagon(draw, avatar_box)
        if self._DEFAULT_USER_AVATAR_PATH.exists():
            self._draw_image_asset(draw, self._DEFAULT_USER_AVATAR_PATH, avatar_box, clip_octagon=True)
        else:
            self._draw_rank_icon(draw, "player", avatar_box)

        label_font = self._font(30, bold=True)
        name = player_data.name or "未知"
        name_font = self._fit_font(draw, name, 54, 34, box[2] - box[0] - 245, bold=True)
        uid_text = f"UID {player_data.uid}" if player_data.uid else "UID 未知"
        uid_font = self._fit_font(draw, uid_text, 27, 20, box[2] - box[0] - 245)
        text_left = box[0] + 220
        text_right = box[2] - 24
        self._draw_centered_stroked_text(
            draw,
            "玩家信息",
            label_font,
            text_left,
            text_right,
            box[1] + 48,
            box[1] + 92,
            (190, 192, 196, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            name,
            name_font,
            text_left,
            text_right,
            box[1] + 112,
            box[1] + 174,
            (250, 250, 246, 255),
            stroke_width=2,
        )
        self._draw_centered_stroked_text(
            draw,
            uid_text,
            uid_font,
            text_left,
            text_right,
            box[1] + 192,
            box[1] + 236,
            (170, 176, 186, 255),
        )
        draw.rectangle((text_left + 22, box[3] - 38, text_right - 22, box[3] - 32), fill=(221, 48, 52, 185))

    def _draw_player_score_panel(
        self,
        draw,
        box: tuple[int, int, int, int],
        player_data: ApexPlayerStats,
        rank_display: str,
        global_percent: str,
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 15, 19, 244), outline_alpha=135)
        draw.line((box[0] + 362, box[1] + 24, box[0] + 310, box[3] - 24), fill=(154, 40, 44, 86), width=2)
        draw.line((box[2] - 362, box[1] + 24, box[2] - 310, box[3] - 24), fill=(154, 40, 44, 86), width=2)

        score = self._format_rank_score_number(player_data.rank_score)
        score_font = self._fit_font(draw, score, 150, 96, 620, bold=True)
        rank_font = self._fit_font(draw, rank_display or "未知", 38, 26, 300, bold=True)
        label_font = self._font(34, bold=True)
        percent_font = self._fit_font(draw, global_percent or "未知", 34, 22, 250, bold=True)
        left = box[0] + 34
        right = box[2] - 34
        self._draw_centered_stroked_text(
            draw,
            "段位分数",
            label_font,
            box[0],
            box[2],
            box[1] + 42,
            box[1] + 92,
            (190, 192, 196, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            score,
            score_font,
            box[0] + 160,
            box[2] - 160,
            box[1] + 98,
            box[1] + 230,
            (250, 250, 246, 255),
            stroke_width=2,
        )
        self._draw_centered_stroked_text(
            draw,
            "当前段位",
            self._font(28, bold=True),
            left,
            left + 300,
            box[3] - 76,
            box[3] - 38,
            (190, 192, 196, 255),
        )
        self._draw_centered_stroked_text(
            draw,
            rank_display or "未知",
            rank_font,
            left + 250,
            left + 570,
            box[3] - 80,
            box[3] - 34,
            (250, 250, 246, 255),
            stroke_width=2,
        )
        self._draw_centered_stroked_text(
            draw,
            f"全球前 {global_percent}" if global_percent and global_percent != "未知" else "全球排名未知",
            percent_font,
            right - 280,
            right,
            box[3] - 82,
            box[3] - 34,
            (214, 218, 224, 255),
        )
        draw.rectangle((box[0] + 24, box[3] - 28, box[0] + 296, box[3] - 22), fill=(221, 48, 52, 185))
        draw.rectangle((box[2] - 296, box[3] - 28, box[2] - 24, box[3] - 22), fill=(221, 48, 52, 185))

    def _render_rank_change_image(
        self,
        player_data: ApexPlayerStats,
        old_score: int,
        new_score: int,
        platform: str,
        is_season_reset: bool = False,
    ) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成排位分数变化图片")

        output_dir = self._rank_change_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        image = self._build_rank_change_card(
            player_data=player_data,
            old_score=old_score,
            new_score=new_score,
            platform=platform,
            is_season_reset=is_season_reset,
        )
        output_path = output_dir / f"rank_change_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._cleanup_old_rank_change_cards(output_dir)
        return output_path

    def _rank_change_card_output_dir(self) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / "rank_change_cards"
        return Path(data_dir) / "rank_change_cards"

    def _cleanup_old_rank_change_cards(self, output_dir: Path, keep: int = 16) -> None:
        try:
            files = sorted(
                output_dir.glob("rank_change_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理排位分数变化缓存图片失败: {exc}")

    def _build_rank_change_card(
        self,
        player_data: ApexPlayerStats,
        old_score: int,
        new_score: int,
        platform: str,
        is_season_reset: bool,
    ):
        width, height = self._RANK_CHANGE_CARD_SIZE
        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        self._draw_rank_change_background(canvas)
        self._draw_rank_change_outer_frame(draw, width, height)
        self._draw_rank_change_header(draw, width)
        self._draw_rank_change_time_bar(draw)

        rank_display = (
            f"{player_data.rank_name} {player_data.rank_div}"
            if player_data.rank_div
            else player_data.rank_name or "未知"
        )
        self._draw_rank_info_panel(
            draw,
            (54, 360, 388, 670),
            "player",
            "玩家",
            player_data.name or "未知",
        )
        self._draw_rank_info_panel(
            draw,
            (402, 360, 720, 670),
            "platform",
            "平台",
            self._format_platform(platform or player_data.platform),
        )
        self._draw_rank_badge_panel(
            draw,
            (734, 360, 1070, 670),
            "段位",
            rank_display,
            player_data.rank_name,
            player_data.rank_div,
        )
        self._draw_rank_score_change_panel(
            draw,
            old_score=old_score,
            new_score=new_score,
            is_season_reset=is_season_reset,
        )
        self._draw_rank_detail_panel(
            draw,
            (54, 1010, 370, 1340),
            "globe",
            "全球排名",
            self._format_rank_percent(player_data.global_rank_percent),
        )
        self._draw_rank_detail_panel(
            draw,
            (376, 1010, 720, 1340),
            "legend",
            "当前英雄",
            player_data.selected_legend or "未知",
        )
        status = player_data.current_state or ("在线" if player_data.is_online else "离线")
        self._draw_rank_status_panel(draw, (724, 1010, 1070, 1340), status)
        return canvas.convert("RGB")

    def _draw_rank_change_background(self, canvas) -> None:
        width, height = canvas.size
        draw = ImageDraw.Draw(canvas)
        for row in range(height):
            ratio = row / max(1, height - 1)
            shade = int(8 + 14 * ratio)
            draw.line((0, row, width, row), fill=(shade, shade + 1, shade + 4, 255))
        line_overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        line_draw = ImageDraw.Draw(line_overlay)
        for x in range(-260, width, 220):
            line_draw.line((x, 0, x + 470, height), fill=(255, 255, 255, 18), width=1)
        canvas.alpha_composite(line_overlay)
        glow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        glow_draw = ImageDraw.Draw(glow)
        glow_draw.rectangle((250, 218, 872, 224), fill=(230, 43, 45, 165))
        glow_draw.rectangle((420, 1358, 760, 1365), fill=(230, 43, 45, 155))
        glow = glow.filter(ImageFilter.GaussianBlur(radius=8))
        canvas.alpha_composite(glow)

    def _draw_rank_change_outer_frame(self, draw, width: int, height: int) -> None:
        red = (228, 48, 52, 255)
        silver = (112, 118, 126, 210)
        outer = [
            (36, 18),
            (width - 52, 18),
            (width - 18, 54),
            (width - 18, height - 68),
            (width - 60, height - 18),
            (54, height - 18),
            (18, height - 58),
            (18, 56),
        ]
        inner = [
            (50, 36),
            (width - 68, 36),
            (width - 36, 68),
            (width - 36, height - 82),
            (width - 76, height - 36),
            (68, height - 36),
            (36, height - 72),
            (36, 70),
        ]
        draw.line(outer + [outer[0]], fill=red, width=4)
        draw.line(inner + [inner[0]], fill=silver, width=2)
        for x in (70, 340, 764, 1014):
            draw.line((x, 44, x + 80, 44), fill=(224, 45, 48, 170), width=5)
        for y in (242, 688, 1000):
            draw.line((58, y, width - 58, y), fill=(210, 48, 52, 110), width=2)

    def _draw_rank_change_header(self, draw, width: int) -> None:
        title_box = (54, 54, width - 54, 224)
        self._draw_rank_panel_base(draw, title_box, fill=(12, 14, 19, 238), outline_alpha=150)
        self._draw_chart_icon(draw, (94, 82, 220, 190))
        title = "Apex 排位分数变化"
        title_font = self._fit_font(draw, title, 78, 50, width - 310, bold=True)
        self._draw_text_stroked(
            draw,
            (260, self._centered_text_y(draw, title, title_font, 68, 202)),
            title,
            title_font,
            fill=(248, 248, 244, 255),
            stroke_width=2,
        )
        draw.rectangle((508, 217, 620, 222), fill=(236, 48, 52, 220))

    def _draw_rank_change_time_bar(self, draw) -> None:
        box = (54, 244, 1070, 338)
        self._draw_rank_panel_base(draw, box, fill=(14, 17, 23, 240), outline_alpha=120)
        self._draw_clock_icon(draw, (92, 265), 28)
        label_font = self._font(34, bold=True)
        value = self._now_str()
        value_font = self._fit_font(draw, value, 37, 26, 600, bold=True)
        self._draw_text_stroked(
            draw,
            (176, self._centered_text_y(draw, "时间:", label_font, box[1], box[3])),
            "时间:",
            label_font,
            fill=(226, 226, 220, 255),
        )
        self._draw_text_stroked(
            draw,
            (288, self._centered_text_y(draw, value, value_font, box[1], box[3])),
            value,
            value_font,
            fill=(244, 244, 239, 255),
        )

    def _draw_rank_info_panel(
        self,
        draw,
        box: tuple[int, int, int, int],
        icon: str,
        label: str,
        value: str,
        secondary_value: str = "",
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=105)
        center_x = (box[0] + box[2]) // 2
        icon_box = (center_x - 58, box[1] + 48, center_x + 58, box[1] + 164)
        self._draw_icon_octagon(draw, icon_box)
        if icon == "player" and self._DEFAULT_USER_AVATAR_PATH.exists():
            self._draw_image_asset(
                draw,
                self._DEFAULT_USER_AVATAR_PATH,
                icon_box,
                clip_octagon=True,
            )
        else:
            self._draw_rank_icon(draw, icon, icon_box)
        label_font = self._font(34, bold=True)
        value_bottom = box[3] - 58 if secondary_value else box[3] - 26
        value_font = self._fit_font(draw, value, 46, 30, box[2] - box[0] - 56, bold=True)
        self._draw_centered_stroked_text(
            draw, label, label_font, box[0], box[2], box[1] + 178, box[1] + 224, (190, 192, 196, 255)
        )
        self._draw_centered_stroked_text(
            draw, value, value_font, box[0] + 18, box[2] - 18, box[1] + 232, value_bottom, (250, 250, 246, 255)
        )
        if secondary_value:
            secondary_font = self._fit_font(draw, secondary_value, 27, 20, box[2] - box[0] - 56, bold=True)
            self._draw_centered_stroked_text(
                draw,
                secondary_value,
                secondary_font,
                box[0] + 22,
                box[2] - 22,
                box[3] - 58,
                box[3] - 22,
                (207, 211, 218, 255),
            )

    def _draw_rank_badge_panel(
        self,
        draw,
        box: tuple[int, int, int, int],
        label: str,
        value: str,
        rank_name: str,
        rank_div: int,
        secondary_value: str = "",
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=105)
        center_x = (box[0] + box[2]) // 2
        rank_icon = self._resolve_rank_icon_path(rank_name, rank_div)
        if rank_icon.exists():
            self._draw_image_asset(
                draw,
                rank_icon,
                (center_x - 92, box[1] + 24, center_x + 92, box[1] + 178),
            )
            if rank_div and "_" not in rank_icon.stem and rank_icon.stem not in {"master", "predator"}:
                badge_font = self._font(42, bold=True)
                self._draw_centered_stroked_text(
                    draw,
                    str(rank_div),
                    badge_font,
                    center_x - 36,
                    center_x + 36,
                    box[1] + 84,
                    box[1] + 142,
                    (255, 255, 255, 255),
                    stroke_width=2,
                )
        else:
            self._draw_rank_diamond(draw, (center_x, box[1] + 110), rank_div)
        label_font = self._font(34, bold=True)
        label_top = box[1] + 176 if secondary_value else box[1] + 178
        label_bottom = box[1] + 216 if secondary_value else box[1] + 224
        value_top = box[1] + 214 if secondary_value else box[1] + 232
        value_bottom = box[1] + 268 if secondary_value else box[3] - 26
        value_size = 42 if secondary_value else 47
        value_font = self._fit_font(draw, value, value_size, 30, box[2] - box[0] - 50, bold=True)
        self._draw_centered_stroked_text(
            draw, label, label_font, box[0], box[2], label_top, label_bottom, (190, 192, 196, 255)
        )
        self._draw_centered_stroked_text(
            draw, value, value_font, box[0], box[2], value_top, value_bottom, (250, 250, 246, 255)
        )
        if secondary_value:
            secondary_font = self._fit_font(draw, secondary_value, 27, 20, box[2] - box[0] - 60, bold=True)
            self._draw_centered_stroked_text(
                draw,
                secondary_value,
                secondary_font,
                box[0] + 30,
                box[2] - 30,
                box[1] + 266,
                box[3] - 18,
                (214, 218, 224, 255),
            )

    def _draw_rank_score_change_panel(
        self,
        draw,
        old_score: int,
        new_score: int,
        is_season_reset: bool,
    ) -> None:
        box = (52, 690, 1070, 992)
        self._draw_rank_panel_base(draw, box, fill=(13, 15, 19, 244), outline_alpha=130)
        draw.line((424, 716, 365, 966), fill=(154, 40, 44, 86), width=2)
        draw.line((698, 716, 758, 966), fill=(154, 40, 44, 86), width=2)

        diff = new_score - old_score
        color = (88, 210, 126, 255) if diff > 0 else (238, 62, 66, 255)
        direction = "上升" if diff > 0 else "下降"
        sign_text = f"+{diff}" if diff > 0 else str(diff)
        if is_season_reset:
            direction = "赛季重置"
        old_text = self._format_rank_score_number(old_score)
        new_text = self._format_rank_score_number(new_score)
        old_font = self._fit_font(draw, old_text, 94, 62, 320, bold=True)
        new_font = self._fit_font(draw, new_text, 94, 62, 320, bold=True)
        label_font = self._font(34, bold=True)
        delta_font = self._fit_font(draw, sign_text, 76, 46, 220, bold=True)
        desc = f"{direction} {abs(diff)} 分" if not is_season_reset else f"下降 {abs(diff)} 分"
        desc_font = self._fit_font(draw, desc, 38, 26, 248, bold=True)

        self._draw_centered_stroked_text(
            draw, "原分数", label_font, 74, 404, 724, 780, (198, 202, 207, 255)
        )
        self._draw_centered_stroked_text(
            draw, old_text, old_font, 74, 404, 794, 906, (248, 248, 244, 255), stroke_width=2
        )
        self._draw_centered_stroked_text(
            draw, "当前分数", label_font, 718, 1048, 724, 780, (198, 202, 207, 255)
        )
        self._draw_centered_stroked_text(
            draw, new_text, new_font, 718, 1048, 794, 906, (248, 248, 244, 255), stroke_width=2
        )
        self._draw_delta_arrows(draw, (561, 743), diff)
        self._draw_centered_stroked_text(
            draw, sign_text, delta_font, 448, 674, 790, 870, color, stroke_width=2
        )
        self._draw_centered_stroked_text(
            draw, desc, desc_font, 430, 692, 882, 944, color, stroke_width=2
        )
        draw.rectangle((118, 944, 382, 950), fill=(221, 48, 52, 185))
        draw.rectangle((740, 944, 1002, 950), fill=(221, 48, 52, 185))

    def _draw_rank_detail_panel(
        self,
        draw,
        box: tuple[int, int, int, int],
        icon: str,
        label: str,
        value: str,
        subtitle: str = "",
    ) -> None:
        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=100)
        center_x = (box[0] + box[2]) // 2
        icon_box = (center_x - 58, box[1] + 48, center_x + 58, box[1] + 164)
        self._draw_icon_octagon(draw, icon_box)
        legend_icon = self._resolve_legend_icon_path(value) if icon == "legend" else Path("")
        if icon == "legend" and legend_icon.exists():
            self._draw_image_asset(draw, legend_icon, icon_box, clip_octagon=True)
        else:
            self._draw_rank_icon(draw, icon, icon_box)
        label_font = self._font(32, bold=True)
        value_font = self._fit_font(draw, value, 48, 30, box[2] - box[0] - 56, bold=True)
        label_top = box[1] + 174 if subtitle else box[1] + 178
        label_bottom = box[1] + 216 if subtitle else box[1] + 226
        value_top = box[1] + 216 if subtitle else box[1] + 238
        value_bottom = box[1] + 272 if subtitle else box[3] - 32
        self._draw_centered_stroked_text(
            draw, label, label_font, box[0], box[2], label_top, label_bottom, (190, 192, 196, 255)
        )
        self._draw_centered_stroked_text(
            draw, value, value_font, box[0] + 22, box[2] - 22, value_top, value_bottom, (250, 250, 246, 255)
        )
        if subtitle:
            subtitle_font = self._fit_font(draw, subtitle, 26, 18, box[2] - box[0] - 56, bold=True)
            self._draw_centered_stroked_text(
                draw,
                subtitle,
                subtitle_font,
                box[0] + 22,
                box[2] - 22,
                box[1] + 270,
                box[3] - 22,
                (207, 211, 218, 255),
            )

    def _draw_rank_status_panel(
        self, draw, box: tuple[int, int, int, int], status: str
    ) -> None:
        status_badge = self._resolve_status_badge_path(status)
        if status_badge.exists():
            self._draw_image_asset(draw, status_badge, box)
            return

        self._draw_rank_panel_base(draw, box, fill=(13, 16, 21, 240), outline_alpha=100)
        center_x = (box[0] + box[2]) // 2
        icon_box = (center_x - 58, box[1] + 48, center_x + 58, box[1] + 164)
        self._draw_icon_octagon(draw, icon_box)
        self._draw_rank_icon(draw, "target", icon_box)
        label_font = self._font(32, bold=True)
        status_font = self._fit_font(draw, status or "未知", 42, 28, 206, bold=True)
        self._draw_centered_stroked_text(
            draw, "当前状态", label_font, box[0], box[2], box[1] + 178, box[1] + 226, (190, 192, 196, 255)
        )
        button = (box[0] + 58, box[1] + 238, box[2] - 58, box[1] + 320)
        draw.rounded_rectangle(button, radius=8, fill=(186, 42, 42, 245), outline=(255, 98, 84, 230), width=2)
        self._draw_centered_stroked_text(
            draw, status or "未知", status_font, button[0], button[2], button[1], button[3], (255, 247, 235, 255)
        )

    def _draw_rank_panel_base(
        self,
        draw,
        box: tuple[int, int, int, int],
        fill: tuple[int, int, int, int],
        outline_alpha: int,
    ) -> None:
        draw.rounded_rectangle(box, radius=8, fill=fill, outline=(92, 98, 108, 150), width=2)
        inset = (box[0] + 8, box[1] + 8, box[2] - 8, box[3] - 8)
        draw.rounded_rectangle(inset, radius=7, outline=(224, 48, 52, outline_alpha), width=2)
        draw.line((box[0] + 24, box[1] + 28, box[0] + 118, box[1] + 28), fill=(220, 48, 52, 120), width=2)
        draw.line((box[2] - 118, box[3] - 28, box[2] - 24, box[3] - 28), fill=(220, 48, 52, 120), width=2)

    def _draw_centered_stroked_text(
        self,
        draw,
        text: str,
        font,
        left: int,
        right: int,
        top: int,
        bottom: int,
        fill: tuple[int, int, int, int],
        stroke_width: int = 1,
    ) -> None:
        text = str(text or "")
        x = self._centered_text_x(draw, text, font, left, right)
        y = self._centered_text_y(draw, text, font, top, bottom)
        self._draw_text_stroked(draw, (x, y), text, font, fill=fill, stroke_width=stroke_width)

    @staticmethod
    def _draw_text_stroked(
        draw,
        xy,
        text: str,
        font,
        fill: tuple[int, int, int, int],
        stroke_width: int = 1,
    ) -> None:
        draw.text(
            xy,
            text,
            font=font,
            fill=fill,
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 205),
        )

    def _draw_icon_octagon(self, draw, box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        cut = 24
        points = [
            (left + cut, top),
            (right - cut, top),
            (right, top + cut),
            (right, bottom - cut),
            (right - cut, bottom),
            (left + cut, bottom),
            (left, bottom - cut),
            (left, top + cut),
        ]
        draw.polygon(points, fill=(23, 24, 29, 230), outline=(218, 49, 52, 190))

    def _draw_image_asset(
        self,
        draw,
        path: Path,
        box: tuple[int, int, int, int],
        clip_octagon: bool = False,
    ) -> None:
        try:
            with Image.open(path) as raw:
                image = raw.convert("RGBA")
        except Exception as exc:
            logger.debug(f"读取图片资源失败 {path}: {exc}")
            return

        alpha_box = image.getchannel("A").getbbox()
        if alpha_box:
            image = image.crop(alpha_box)

        width = max(1, box[2] - box[0])
        height = max(1, box[3] - box[1])
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        image = ImageOps.contain(image, (width, height), method=resampling)
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        canvas.alpha_composite(image, ((width - image.width) // 2, (height - image.height) // 2))
        if clip_octagon:
            mask = Image.new("L", (width, height), 0)
            mask_draw = ImageDraw.Draw(mask)
            cut = max(12, min(width, height) // 5)
            mask_draw.polygon(
                [
                    (cut, 0),
                    (width - cut, 0),
                    (width, cut),
                    (width, height - cut),
                    (width - cut, height),
                    (cut, height),
                    (0, height - cut),
                    (0, cut),
                ],
                fill=255,
            )
            clipped = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            clipped.paste(canvas, (0, 0), mask)
            canvas = clipped
        target = getattr(draw, "_image", None)
        if target is not None:
            try:
                if getattr(target, "mode", "") == "RGBA":
                    target.alpha_composite(canvas, (box[0], box[1]))
                else:
                    target.paste(canvas.convert(target.mode), (box[0], box[1]), canvas.getchannel("A"))
                return
            except Exception as exc:
                logger.debug(f"粘贴图片资源失败 {path}: {exc}")

        try:
            draw.bitmap((box[0], box[1]), canvas)
        except Exception as exc:
            logger.debug(f"绘制图片资源失败 {path}: {exc}")

    def _resolve_rank_icon_path(self, rank_name: str, rank_div: int = 0) -> Path:
        normalized = self._normalize_asset_token(rank_name)
        aliases = {
            "rookie": "rookie",
            "unranked": "rookie",
            "novice": "rookie",
            "菜鸟": "rookie",
            "青铜": "bronze",
            "bronze": "bronze",
            "白银": "silver",
            "silver": "silver",
            "黄金": "gold",
            "gold": "gold",
            "白金": "platinum",
            "platinum": "platinum",
            "铂金": "platinum",
            "钻石": "diamond",
            "diamond": "diamond",
            "大师": "master",
            "master": "master",
            "猎杀": "predator",
            "apex猎杀者": "predator",
            "apexpredator": "predator",
            "predator": "predator",
        }
        key = aliases.get(normalized)
        if key is None and "猎杀" in normalized:
            key = "predator"
        if key is None:
            key = normalized
        if key in {"rookie", "bronze", "silver", "gold", "platinum", "diamond"}:
            try:
                division = int(rank_div)
            except Exception:
                division = 0
            if division in {1, 2, 3, 4}:
                division_path = self._RANK_ICON_DIR / f"{key}_{division}.png"
                if division_path.exists():
                    return division_path
        return self._RANK_ICON_DIR / f"{key}.png"

    def _resolve_legend_icon_path(self, legend_name: str) -> Path:
        normalized = self._normalize_asset_token(legend_name)
        aliases = {
            "艾许": "ash",
            "ash": "ash",
            "班加罗尔": "bangalore",
            "bangalore": "bangalore",
            "寻血猎犬": "bloodhound",
            "bloodhound": "bloodhound",
            "卡特莉丝": "catalyst",
            "催化姬": "catalyst",
            "catalyst": "catalyst",
            "侵蚀": "caustic",
            "caustic": "caustic",
            "密客": "crypto",
            "crypto": "crypto",
            "暴雷": "fuse",
            "fuse": "fuse",
            "直布罗陀": "gibraltar",
            "gibraltar": "gibraltar",
            "地平线": "horizon",
            "horizon": "horizon",
            "命脉": "lifeline",
            "lifeline": "lifeline",
            "罗芭": "loba",
            "loba": "loba",
            "疯玛吉": "mad_maggie",
            "madmaggie": "mad_maggie",
            "madmaggie": "mad_maggie",
            "幻象": "mirage",
            "mirage": "mirage",
            "纽卡斯尔": "newcastle",
            "newcastle": "newcastle",
            "动力小子": "octane",
            "octane": "octane",
            "探路者": "pathfinder",
            "pathfinder": "pathfinder",
            "兰伯特": "rampart",
            "rampart": "rampart",
            "亡灵": "revenant",
            "revenant": "revenant",
            "希尔": "seer",
            "seer": "seer",
            "琉雀": "sparrow",
            "麻雀": "sparrow",
            "sparrow": "sparrow",
            "瓦尔基里": "valkyrie",
            "valkyrie": "valkyrie",
            "万蒂奇": "vantage",
            "vantage": "vantage",
            "沃特森": "wattson",
            "wattson": "wattson",
            "恶灵": "wraith",
            "wraith": "wraith",
            "导管": "conduit",
            "导线管": "conduit",
            "conduit": "conduit",
            "弹道": "ballistic",
            "ballistic": "ballistic",
            "变幻": "alter",
            "alter": "alter",
            "艾克赛尔": "axle",
            "axle": "axle",
        }
        key = aliases.get(normalized, normalized)
        return self._LEGEND_ICON_DIR / f"{key}.png"

    def _resolve_status_badge_path(self, status: str) -> Path:
        normalized = self._normalize_asset_token(status)
        aliases = {
            "比赛中": "in_match",
            "正在比赛": "in_match",
            "游戏中": "in_match",
            "inmatch": "in_match",
            "match": "in_match",
            "在大厅": "in_lobby",
            "大厅中": "in_lobby",
            "等待中": "in_lobby",
            "inlobby": "in_lobby",
            "lobby": "in_lobby",
            "离线": "offline",
            "offline": "offline",
        }
        key = aliases.get(normalized)
        if key is None and ("比赛" in normalized or "match" in normalized):
            key = "in_match"
        if key is None and ("大厅" in normalized or "lobby" in normalized):
            key = "in_lobby"
        if key is None and ("离线" in normalized or "offline" in normalized):
            key = "offline"
        if key is None:
            key = normalized
        return self._STATUS_BADGE_DIR / f"{key}.png"

    @staticmethod
    def _normalize_asset_token(value: str) -> str:
        text = str(value or "").strip()
        text = text.replace("·", "").replace("'", "").replace("’", "")
        text = text.replace("-", "").replace("_", "").replace(" ", "")
        return text.lower()

    def _draw_rank_icon(self, draw, icon: str, box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        cx = (left + right) // 2
        cy = (top + bottom) // 2
        white = (244, 241, 235, 255)
        if icon == "player":
            draw.ellipse((cx - 20, cy - 38, cx + 20, cy + 2), fill=white)
            draw.pieslice((cx - 52, cy - 4, cx + 52, cy + 76), 180, 360, fill=white)
            draw.rectangle((cx - 52, cy + 36, cx + 52, cy + 76), fill=white)
        elif icon == "platform":
            self._draw_monitor_icon(draw, (cx, cy), 76, white)
        elif icon == "globe":
            draw.ellipse((cx - 42, cy - 42, cx + 42, cy + 42), outline=white, width=5)
            draw.arc((cx - 22, cy - 42, cx + 22, cy + 42), 90, 270, fill=white, width=4)
            draw.arc((cx - 22, cy - 42, cx + 22, cy + 42), -90, 90, fill=white, width=4)
            draw.line((cx - 42, cy, cx + 42, cy), fill=white, width=4)
            draw.line((cx, cy - 42, cx, cy + 42), fill=white, width=4)
        elif icon == "legend":
            draw.ellipse((cx - 30, cy - 16, cx + 30, cy + 40), outline=white, width=5)
            spikes = [
                (cx - 38, cy - 8), (cx - 24, cy - 44), (cx - 12, cy - 16),
                (cx, cy - 54), (cx + 12, cy - 16), (cx + 28, cy - 44),
                (cx + 38, cy - 8),
            ]
            draw.line(spikes, fill=white, width=8, joint="curve")
            draw.ellipse((cx - 26, cy + 2, cx - 2, cy + 24), outline=white, width=5)
            draw.ellipse((cx + 2, cy + 2, cx + 26, cy + 24), outline=white, width=5)
            draw.line((cx - 2, cy + 13, cx + 2, cy + 13), fill=white, width=4)
        elif icon == "target":
            draw.ellipse((cx - 38, cy - 38, cx + 38, cy + 38), outline=white, width=5)
            draw.ellipse((cx - 15, cy - 15, cx + 15, cy + 15), outline=white, width=4)
            draw.line((cx - 56, cy, cx + 56, cy), fill=white, width=4)
            draw.line((cx, cy - 56, cx, cy + 56), fill=white, width=4)
        elif icon == "crown":
            crown = [
                (cx - 48, cy + 34),
                (cx - 38, cy - 26),
                (cx - 14, cy + 4),
                (cx, cy - 42),
                (cx + 14, cy + 4),
                (cx + 38, cy - 26),
                (cx + 48, cy + 34),
            ]
            draw.line(crown, fill=white, width=8, joint="curve")
            draw.rounded_rectangle((cx - 44, cy + 28, cx + 44, cy + 50), radius=4, fill=white)

    def _draw_monitor_icon(self, draw, center: tuple[int, int], size: int, fill) -> None:
        cx, cy = center
        w = size
        h = int(size * 0.58)
        draw.rounded_rectangle((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2), radius=6, outline=fill, width=7)
        draw.line((cx, cy + h // 2, cx, cy + h // 2 + 22), fill=fill, width=7)
        draw.line((cx - 34, cy + h // 2 + 24, cx + 34, cy + h // 2 + 24), fill=fill, width=7)

    def _draw_chart_icon(self, draw, box: tuple[int, int, int, int]) -> None:
        left, top, right, bottom = box
        white = (230, 232, 229, 255)
        red = (238, 62, 66, 255)
        draw.rectangle((left, top, right, bottom), outline=white, width=3)
        for index in range(1, 4):
            x = left + (right - left) * index // 4
            y = top + (bottom - top) * index // 4
            draw.line((x, top, x, bottom), fill=(white[0], white[1], white[2], 70), width=2)
            draw.line((left, y, right, y), fill=(white[0], white[1], white[2], 70), width=2)
        points = [
            (left + 18, bottom - 18),
            (left + 46, bottom - 52),
            (left + 72, bottom - 28),
            (left + 100, top + 42),
            (right - 10, top + 16),
        ]
        draw.line(points, fill=red, width=9, joint="curve")
        draw.polygon(
            [(right - 10, top + 16), (right - 12, top + 52), (right - 42, top + 24)],
            fill=red,
        )

    def _draw_clock_icon(self, draw, center: tuple[int, int], radius: int) -> None:
        cx, cy = center
        red = (238, 62, 66, 255)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline=red, width=5)
        draw.line((cx, cy, cx, cy - 18), fill=red, width=5)
        draw.line((cx, cy, cx + 18, cy + 10), fill=red, width=5)

    def _draw_rank_diamond(self, draw, center: tuple[int, int], rank_div: int) -> None:
        cx, cy = center
        outer = [(cx, cy - 74), (cx + 82, cy), (cx, cy + 74), (cx - 82, cy)]
        inner = [(cx, cy - 52), (cx + 58, cy), (cx, cy + 52), (cx - 58, cy)]
        draw.polygon(outer, fill=(45, 47, 122, 235), outline=(184, 188, 255, 255))
        draw.line(outer + [outer[0]], fill=(158, 164, 255, 255), width=5)
        draw.polygon(inner, fill=(35, 31, 84, 245), outline=(230, 232, 255, 205))
        for offset in (94, -94):
            draw.line((cx + offset, cy, cx + offset // 2, cy - 42), fill=(156, 188, 255, 165), width=5)
            draw.line((cx + offset, cy, cx + offset // 2, cy + 42), fill=(156, 188, 255, 165), width=5)
        number = str(rank_div or "")
        if number:
            font = self._font(58, bold=True)
            self._draw_centered_stroked_text(
                draw, number, font, cx - 48, cx + 48, cy - 42, cy + 48, (255, 255, 255, 255), stroke_width=2
            )

    def _draw_delta_arrows(self, draw, center: tuple[int, int], diff: int) -> None:
        cx, cy = center
        color = (88, 210, 126, 255) if diff > 0 else (238, 62, 66, 255)
        direction = -1 if diff > 0 else 1
        for offset in (0, 38):
            y = cy + offset * direction
            if diff > 0:
                points = [(cx, y - 30), (cx - 42, y + 16), (cx - 22, y + 16), (cx, y - 7), (cx + 22, y + 16), (cx + 42, y + 16)]
            else:
                points = [(cx, y + 30), (cx - 42, y - 16), (cx - 22, y - 16), (cx, y + 7), (cx + 22, y - 16), (cx + 42, y - 16)]
            draw.line(points, fill=color, width=10, joint="curve")

    def _format_rank_percent(self, value: str) -> str:
        text = str(value or "").strip()
        if not text or text == "未知":
            return "未知"
        if text.endswith("%"):
            return text
        return f"{text}%"

    @staticmethod
    def _format_rank_score_number(value: int) -> str:
        try:
            return str(int(value))
        except Exception:
            return str(value)

    def _render_season_info_image(self, season_info: SeasonInfo) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成赛季图片")

        output_dir = self._season_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = self._season_info_image_cache_key(output_dir, season_info)
        cached_path = self._get_cached_season_info_image(cache_key)
        if cached_path is not None:
            return cached_path

        image = self._build_season_info_card(season_info)
        output_path = output_dir / f"season_info_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._set_cached_season_info_image(cache_key, output_path)
        self._cleanup_old_season_cards(output_dir)
        return output_path

    def _get_cached_season_info_image(self, cache_key: tuple) -> Path | None:
        cache = getattr(self, "_season_info_image_cache", None)
        if not isinstance(cache, dict):
            return None
        cached = cache.get(cache_key)
        if not cached:
            return None
        saved_at, image_path = cached
        if time.monotonic() - saved_at > self._SEASON_IMAGE_CACHE_TTL_SECONDS:
            cache.pop(cache_key, None)
            return None
        image_path = Path(image_path)
        if not image_path.exists():
            cache.pop(cache_key, None)
            return None
        return image_path

    def _set_cached_season_info_image(self, cache_key: tuple, image_path: Path) -> None:
        cache = getattr(self, "_season_info_image_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._season_info_image_cache = cache
        cache[cache_key] = (time.monotonic(), Path(image_path))

    def _season_info_image_cache_key(
        self, output_dir: Path, season_info: SeasonInfo
    ) -> tuple:
        return (
            str(output_dir.resolve()),
            season_info.season_number,
            season_info.season_name,
            season_info.start_iso,
            season_info.end_iso,
        )

    def _season_card_output_dir(self) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / "season_cards"
        return Path(data_dir) / "season_cards"

    def _cleanup_old_season_cards(self, output_dir: Path, keep: int = 8) -> None:
        try:
            files = sorted(
                output_dir.glob("season_info_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理赛季缓存图片失败: {exc}")

    def _build_season_info_card(self, season_info: SeasonInfo):
        width, height = self._SEASON_CARD_SIZE
        canvas = Image.new("RGBA", (width, height), (10, 11, 14, 255))
        draw = ImageDraw.Draw(canvas)

        # 使用 Apex 红黑配色，保证 QQ 缩略图里也能快速读到赛季和结束时间。
        for row in range(height):
            ratio = row / max(1, height - 1)
            red = int(12 + 34 * ratio)
            green = int(13 + 7 * ratio)
            blue = int(17 + 5 * ratio)
            draw.line((0, row, width, row), fill=(red, green, blue, 255))
        apex_red = (216, 35, 42, 255)
        apex_amber = (240, 174, 72, 255)
        draw.polygon((560, 0, width, 0, width, height, 690, height), fill=(94, 15, 20, 215))
        draw.polygon((716, 0, width, 0, width, 136, 794, 94), fill=(236, 50, 42, 205))
        draw.polygon((0, 0, 286, 0, 196, height, 0, height), fill=(18, 21, 27, 210))
        draw.rectangle((0, 0, width, 9), fill=apex_red)
        draw.rectangle((0, height - 56, width, height), fill=(6, 7, 10, 175))
        for x in range(120, width, 168):
            draw.line((x, 18, x - 76, height - 16), fill=(255, 255, 255, 12), width=2)

        season_label = self._season_card_label(season_info)
        end_time = self._to_beijing_time(season_info.end_iso) or season_info.end_date or "未知"
        start_time = self._to_beijing_time(season_info.start_iso) or season_info.start_date or "未知"
        remaining = self._format_remaining(season_info.end_iso) or "未知"
        progress = self._season_progress_fraction(season_info)

        logo = self._apex_logo_badge(92)
        canvas.alpha_composite(logo, (34, 28))

        header_font = self._font(24, bold=True)
        title_font = self._fit_font(draw, season_label, 62, 38, width - 330, bold=True)
        label_font = self._font(25, bold=True)
        value_font = self._fit_font(draw, end_time, 46, 30, width - 405, bold=True)
        small_font = self._fit_font(draw, f"剩余 {remaining}", 22, 16, 250, bold=False)
        source_font = self._font(18)
        source_text = self._season_source_label(season_info.source)
        source_font = self._fit_font(draw, source_text, 18, 14, 250, bold=False)

        draw.text((146, 31), "APEX LEGENDS", font=source_font, fill=(216, 35, 42, 255))
        self._draw_text_with_shadow(
            draw,
            (146, 54),
            "当前赛季",
            header_font,
            fill=(236, 239, 244, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (146, 88),
            season_label,
            title_font,
            fill=(255, 255, 255, 255),
        )

        status = season_info.status_text or "未知"
        status_font = self._fit_font(draw, status, 24, 18, 120, bold=True)
        draw.rounded_rectangle((728, 34, 850, 72), radius=8, fill=(232, 43, 45, 245))
        status_box = draw.textbbox((0, 0), status, font=status_font)
        draw.text(
            (789 - (status_box[2] - status_box[0]) / 2, 43),
            status,
            font=status_font,
            fill=(255, 255, 255, 255),
        )

        panel = (34, 164, 866, 250)
        label_bounds = (64, 164, 292, 250)
        time_bounds = (304, 164, 846, 250)
        draw.rounded_rectangle(panel, radius=8, fill=(15, 18, 24, 238))
        draw.rounded_rectangle(panel, radius=8, outline=(232, 43, 45, 160), width=2)
        draw.rounded_rectangle((34, 164, 48, 250), radius=7, fill=apex_red)
        end_label = self._season_end_label()
        self._draw_text_with_shadow(
            draw,
            (
                label_bounds[0],
                self._centered_text_y(
                    draw, end_label, label_font, label_bounds[1], label_bounds[3]
                ),
            ),
            end_label,
            label_font,
            fill=(232, 215, 191, 255),
        )
        time_x = self._centered_text_x(
            draw, end_time, value_font, time_bounds[0], time_bounds[2]
        )
        self._draw_text_with_shadow(
            draw,
            (
                time_x,
                self._centered_text_y(
                    draw, end_time, value_font, time_bounds[1], time_bounds[3]
                ),
            ),
            end_time,
            value_font,
            fill=(255, 246, 228, 255),
        )

        bar_x, bar_y, bar_w, bar_h = 58, 270, 520, 14
        draw.rounded_rectangle(
            (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
            radius=7,
            fill=(50, 55, 64, 255),
        )
        self._draw_gradient_progress_bar(
            canvas,
            (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
            progress,
            apex_red,
            apex_amber,
        )
        draw.text(
            (58, 288),
            f"开始 {start_time}",
            font=source_font,
            fill=(191, 198, 209, 235),
        )
        draw.text(
            (620, 267),
            f"剩余 {remaining}",
            font=small_font,
            fill=(255, 235, 204, 255),
        )
        draw.text(
            (620, 296),
            source_text,
            font=source_font,
            fill=(178, 190, 204, 230),
        )

        return canvas.convert("RGB")

    @staticmethod
    def _season_end_label() -> str:
        return "赛季结束时间"

    @staticmethod
    def _season_source_label(source: str) -> str:
        text = str(source or "").strip()
        if not text:
            return "来源 未知"
        parsed = urlsplit(text if "://" in text else f"https://{text}")
        host = parsed.netloc or parsed.path.split("/")[0]
        return f"来源 {host or text}"

    @staticmethod
    def _centered_text_x(draw, text: str, font, left: int, right: int) -> float:
        box = draw.textbbox((0, 0), text, font=font)
        return left + ((right - left) - (box[2] - box[0])) / 2

    @staticmethod
    def _centered_text_y(draw, text: str, font, top: int, bottom: int) -> float:
        box = draw.textbbox((0, 0), text, font=font)
        return top + ((bottom - top) - (box[3] - box[1])) / 2 - box[1]

    @staticmethod
    def _draw_gradient_progress_bar(
        canvas,
        box: tuple[int, int, int, int],
        progress: float,
        start_color: tuple[int, int, int, int],
        end_color: tuple[int, int, int, int],
    ) -> None:
        left, top, right, bottom = box
        width = max(0, int((right - left) * min(1.0, max(0.0, progress))))
        height = bottom - top
        if width <= 0 or height <= 0:
            return

        gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        pixels = gradient.load()
        for x in range(width):
            ratio = x / max(1, width - 1)
            color = tuple(
                int(start_color[index] + (end_color[index] - start_color[index]) * ratio)
                for index in range(4)
            )
            for y in range(height):
                pixels[x, y] = color

        mask = Image.new("L", (width, height), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.rounded_rectangle((0, 0, width, height), radius=height // 2, fill=255)
        clipped = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        clipped.paste(gradient, (0, 0), mask)
        canvas.alpha_composite(clipped, (left, top))

    def _apex_logo_badge(self, size: int):
        badge = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(badge)
        draw.ellipse((0, 0, size - 1, size - 1), fill=(7, 9, 13, 238))
        draw.ellipse((4, 4, size - 5, size - 5), outline=(232, 43, 45, 255), width=3)

        logo_path = self._PLUGIN_ROOT / "logo.png"
        if logo_path.exists():
            with Image.open(logo_path) as raw:
                logo = raw.convert("RGBA")
            logo = ImageOps.fit(
                logo,
                (size - 12, size - 12),
                method=getattr(Image, "Resampling", Image).LANCZOS,
                centering=(0.5, 0.5),
            )
            mask = Image.new("L", logo.size, 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, logo.size[0] - 1, logo.size[1] - 1), fill=255)
            clipped_logo = Image.new("RGBA", logo.size, (0, 0, 0, 0))
            clipped_logo.paste(logo, (0, 0), mask)
            badge.alpha_composite(clipped_logo, (6, 6))
        else:
            font = self._font(54, bold=True)
            draw.text((size * 0.28, size * 0.15), "A", font=font, fill=(232, 43, 45, 255))

        return badge

    def _season_card_label(self, season_info: SeasonInfo) -> str:
        if season_info.season_number is not None and season_info.season_name:
            return f"S{season_info.season_number} · {season_info.season_name}"
        if season_info.season_number is not None:
            return f"S{season_info.season_number}"
        return season_info.season_name or "未知赛季"

    @staticmethod
    def _season_progress_fraction(season_info: SeasonInfo) -> float:
        try:
            start = season_info.start_iso
            end = season_info.end_iso
            if not start or not end:
                return 0.0
            start_dt = datetime.fromisoformat(
                start.replace("Z", "+00:00") if start.endswith("Z") else start
            )
            end_dt = datetime.fromisoformat(
                end.replace("Z", "+00:00") if end.endswith("Z") else end
            )
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            total = (end_dt - start_dt).total_seconds()
            if total <= 0:
                return 0.0
            elapsed = (datetime.now(timezone.utc) - start_dt.astimezone(timezone.utc)).total_seconds()
            return min(1.0, max(0.0, elapsed / total))
        except Exception:
            return 0.0

    def _format_map_rotation_text(
        self, rotation_info: MapRotationInfo, mode: str = "ranked"
    ) -> str:
        if mode == "battle_royale":
            rotation_mode = rotation_info.battle_royale
            current_label = "当前三人赛地图"
            title = "🗺️ Apex 三人赛地图轮换"
            missing_text = "⚠️ 暂未获取到三人赛地图轮换数据，请稍后再试"
        else:
            rotation_mode = rotation_info.ranked
            current_label = "当前排位地图"
            title = "🗺️ Apex 排位地图轮换"
            missing_text = "⚠️ 暂未获取到排位地图轮换数据，请稍后再试"

        current = rotation_mode.current
        next_entry = rotation_mode.next
        if current is None:
            return "\n".join(
                [
                    self._time_line(),
                    missing_text,
                ]
            )

        lines = [
            f"📍 {current_label}：{self._format_map_name(current)}",
            f"🕒 查询时间：{self._now_str()}",
            title,
            "——",
            f"⏰ 本轮时间：{self._format_rotation_range(current)}",
        ]
        if current.remaining_timer:
            lines.append(f"⏳ 剩余时间：{current.remaining_timer}")

        if next_entry is not None:
            lines.extend(
                [
                    "——",
                    f"➡️ 下一张：{self._format_map_name(next_entry)}",
                    f"⏰ 下轮时间：{self._format_rotation_range(next_entry)}",
                ]
            )

        lines.append("ℹ️ 时间均为北京时间")
        return "\n".join(lines)

    def _format_daily_map_schedule_text(self, schedule: DailyMapScheduleInfo) -> str:
        lines = [
            self._time_line(),
            f"🗺️ {schedule.title}",
            f"📅 日期：{schedule.date_label}（北京时间）",
            "——",
        ]
        for entry in schedule.entries:
            lines.append(
                f"{self._schedule_entry_status(entry)} {self._schedule_entry_source_label(schedule, entry)} "
                f"{self._map_card_name(entry)}："
                f"{self._format_schedule_entry_range(entry)}"
            )
        lines.extend(
            [
                "——",
                f"ℹ️ {schedule.source_note}",
                "ℹ️ /map 当前地图仍使用 API 实时数据",
            ]
        )
        return "\n".join(lines)

    def _render_daily_map_schedule_image(self, schedule: DailyMapScheduleInfo) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成全天地图图片")

        if not schedule.entries:
            raise ValueError("缺少全天地图排期数据")

        output_dir = self._map_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        render_minute = int(time.time() // 60)
        cache_key = self._daily_map_schedule_image_cache_key(
            output_dir, schedule, render_minute=render_minute
        )
        cached_path = self._get_cached_map_rotation_image(cache_key)
        if cached_path is not None:
            return cached_path

        image = self._build_daily_map_schedule_card(schedule)
        output_path = output_dir / f"daily_map_{schedule.mode}_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._set_cached_map_rotation_image(cache_key, output_path)
        self._cleanup_old_daily_map_cards(output_dir)
        return output_path

    def _daily_map_schedule_image_cache_key(
        self,
        output_dir: Path,
        schedule: DailyMapScheduleInfo,
        render_minute: int | None = None,
    ) -> tuple:
        if render_minute is None:
            render_minute = int(time.time() // 60)
        return (
            "daily_map",
            "v2",
            str(output_dir.resolve()),
            schedule.mode,
            schedule.date_label,
            int(render_minute),
            tuple(
                (
                    entry.map_name,
                    entry.start_timestamp,
                    entry.end_timestamp,
                    getattr(entry, "source", ""),
                )
                for entry in schedule.entries
            ),
        )

    def _cleanup_old_daily_map_cards(self, output_dir: Path, keep: int = 8) -> None:
        try:
            files = sorted(
                output_dir.glob("daily_map_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理全天地图缓存图片失败: {exc}")

    def _build_daily_map_schedule_card(self, schedule: DailyMapScheduleInfo):
        width = 900
        entries = list(schedule.entries)
        row_gap = 10
        row_height = 88 if len(entries) <= 8 else 72
        header_height = 154
        footer_height = 64
        height = header_height + len(entries) * (row_height + row_gap) + footer_height + 18

        canvas = Image.new("RGBA", (width, height), (7, 8, 11, 255))
        draw = ImageDraw.Draw(canvas)
        hero_entry = self._current_schedule_entry(entries) or entries[0]
        hero_bg = self._map_background(hero_entry, (width, header_height), focus_y=0.42)
        canvas.alpha_composite(hero_bg, (0, 0))
        self._overlay_rect(canvas, (0, 0, width, header_height), (0, 0, 0, 126))
        self._draw_horizontal_gradient(canvas, 0, header_height, 210, left=True)

        title_font = self._font(42, bold=True)
        meta_font = self._font(22)
        badge_font = self._font(20, bold=True)
        self._draw_text_with_shadow(
            draw,
            (34, 26),
            schedule.title,
            title_font,
            fill=(255, 255, 255, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (36, 82),
            f"{schedule.date_label}  北京时间",
            meta_font,
            fill=(226, 232, 240, 238),
        )
        badge = (690, 28, 852, 66)
        draw.rounded_rectangle(
            badge,
            radius=8,
            fill=(232, 43, 45, 235),
            outline=(255, 118, 92, 180),
            width=1,
        )
        self._draw_centered_stroked_text(
            draw,
            "排位全天",
            badge_font,
            badge[0],
            badge[2],
            badge[1],
            badge[3],
            (255, 255, 255, 255),
        )
        draw.rectangle((34, 124, 260, 130), fill=(232, 43, 45, 230))

        now = datetime.now(SHANGHAI_TZ)
        y = header_height + 14
        for index, entry in enumerate(entries):
            self._draw_daily_map_row(
                canvas=canvas,
                draw=draw,
                entry=entry,
                index=index,
                x=28,
                y=y,
                width=width - 56,
                height=row_height,
                now=now,
                source_label=self._schedule_entry_source_label(schedule, entry),
            )
            y += row_height + row_gap

        footer_y = height - footer_height
        draw.rectangle((0, footer_y, width, height), fill=(14, 16, 22, 236))
        footer_font = self._font(17)
        note = f"{schedule.source_note} · 生成 {schedule.generated_at} · /map 使用 API 实时数据"
        note_font = self._fit_font(draw, note, 17, 13, width - 64)
        draw.text((32, footer_y + 20), note, font=note_font, fill=(224, 232, 242, 238))
        return canvas.convert("RGB")

    def _draw_daily_map_row(
        self,
        canvas,
        draw,
        entry: MapScheduleEntry,
        index: int,
        x: int,
        y: int,
        width: int,
        height: int,
        now: datetime,
        source_label: str,
    ) -> None:
        bg = self._map_background(entry, (width, height), focus_y=0.48)
        canvas.alpha_composite(bg, (x, y))
        active = self._schedule_entry_is_current(entry, now)
        overlay_alpha = 88 if active else 138
        self._overlay_rect(canvas, (x, y, x + width, y + height), (0, 0, 0, overlay_alpha))
        outline = (232, 43, 45, 230) if active else (255, 255, 255, 42)
        draw.rounded_rectangle((x, y, x + width, y + height), radius=8, outline=outline, width=2)

        line_x = x + 34
        if index > 0:
            draw.line((line_x, y - 10, line_x, y + 22), fill=(232, 43, 45, 160), width=3)
        if index < 99:
            draw.line((line_x, y + height - 22, line_x, y + height + 10), fill=(232, 43, 45, 120), width=3)
        dot_fill = (232, 43, 45, 255) if active else (238, 242, 246, 230)
        draw.ellipse((line_x - 8, y + height // 2 - 8, line_x + 8, y + height // 2 + 8), fill=dot_fill)

        status = self._schedule_entry_status(entry, now)
        status_box = (x + 58, y + 18, x + 136, y + 48)
        status_fill = (232, 43, 45, 230) if status == "当前" else (15, 18, 24, 205)
        draw.rounded_rectangle(status_box, radius=6, fill=status_fill, outline=(255, 255, 255, 50), width=1)
        self._draw_centered_stroked_text(
            draw,
            status,
            self._font(17, bold=True),
            status_box[0],
            status_box[2],
            status_box[1],
            status_box[3],
            (255, 255, 255, 245),
        )

        source = source_label
        source_box = (x + 58, y + 50, x + 136, y + 70)
        source_fill = (
            (49, 191, 139, 225)
            if source == "API"
            else (58, 104, 173, 218)
            if source == "网页"
            else (84, 94, 112, 210)
        )
        draw.rounded_rectangle(source_box, radius=5, fill=source_fill)
        self._draw_centered_stroked_text(
            draw,
            source,
            self._font(13, bold=True),
            source_box[0],
            source_box[2],
            source_box[1],
            source_box[3],
            (255, 255, 255, 240),
        )

        name = self._map_card_name(entry)
        name_font = self._fit_font(draw, name, 34, 24, max_width=360, bold=True)
        self._draw_text_with_shadow(
            draw,
            (x + 156, y + 15),
            name,
            name_font,
            fill=(255, 255, 255, 255),
        )
        if entry.map_name and entry.map_name != name:
            draw.text(
                (x + 158, y + 52),
                entry.map_name,
                font=self._font(16),
                fill=(218, 226, 235, 215),
            )

        time_text = self._format_schedule_entry_range(entry)
        time_font = self._fit_font(draw, time_text, 24, 17, max_width=270, bold=True)
        time_box = draw.textbbox((0, 0), time_text, font=time_font)
        time_w = time_box[2] - time_box[0]
        self._draw_text_with_shadow(
            draw,
            (x + width - 28 - time_w, y + 21),
            time_text,
            time_font,
            fill=(255, 255, 255, 255),
        )
        detail_text = (
            format_schedule_remaining(entry, now)
            if active
            else f"{max(1, entry.duration_secs // 60)} 分钟"
        )
        detail_font = self._fit_font(
            draw,
            detail_text,
            20 if active else 17,
            15 if active else 14,
            max_width=270,
            bold=active,
        )
        detail_box = draw.textbbox((0, 0), detail_text, font=detail_font)
        detail_w = detail_box[2] - detail_box[0]
        detail_x = x + width - 28 - detail_w
        detail_y = y + 52
        detail_fill = (118, 236, 188, 250) if active else (218, 226, 235, 215)
        if active:
            self._draw_text_with_shadow(
                draw,
                (detail_x, detail_y),
                detail_text,
                detail_font,
                fill=detail_fill,
            )
        else:
            draw.text(
                (detail_x, detail_y),
                detail_text,
                font=detail_font,
                fill=detail_fill,
            )

        if active:
            self._draw_schedule_progress(
                draw,
                entry,
                (x + 156, y + height - 13, x + width - 28, y + height - 7),
                now,
            )

    def _draw_schedule_progress(
        self,
        draw,
        entry: MapScheduleEntry,
        box: tuple[int, int, int, int],
        now: datetime,
    ) -> None:
        start = self._timestamp_to_beijing_datetime(entry.start_timestamp)
        end = self._timestamp_to_beijing_datetime(entry.end_timestamp)
        if not start or not end:
            return
        total = max(1, (end - start).total_seconds())
        elapsed = min(total, max(0, (now - start).total_seconds()))
        fraction = elapsed / total
        draw.rounded_rectangle(box, radius=3, fill=(255, 255, 255, 48))
        fill_box = (box[0], box[1], box[0] + int((box[2] - box[0]) * fraction), box[3])
        draw.rounded_rectangle(fill_box, radius=3, fill=(49, 191, 139, 235))

    def _current_schedule_entry(
        self, entries: list[MapScheduleEntry]
    ) -> MapScheduleEntry | None:
        now = datetime.now(SHANGHAI_TZ)
        for entry in entries:
            if self._schedule_entry_is_current(entry, now):
                return entry
        return None

    def _schedule_entry_status(
        self, entry: MapScheduleEntry, now: datetime | None = None
    ) -> str:
        current = now or datetime.now(SHANGHAI_TZ)
        start = self._timestamp_to_beijing_datetime(entry.start_timestamp)
        end = self._timestamp_to_beijing_datetime(entry.end_timestamp)
        if start and end and start <= current < end:
            return "当前"
        if end and end <= current:
            return "已过"
        return "待轮换"

    def _schedule_entry_source_label(
        self, schedule: DailyMapScheduleInfo, entry: MapScheduleEntry
    ) -> str:
        source = str(getattr(entry, "source", "") or "").lower()
        if source == "api":
            return "API"
        if source == "web" and "API" not in schedule.source_note:
            return "网页"
        if source in {"web", "inferred"}:
            return "推断"
        for item in schedule.entries:
            if (
                item.map_name == entry.map_name
                and item.start_timestamp == entry.start_timestamp
                and item.end_timestamp == entry.end_timestamp
            ):
                item_source = str(getattr(item, "source", "") or "").lower()
                if item_source == "api":
                    return "API"
                if item_source == "web" and "API" not in schedule.source_note:
                    return "网页"
        return "推断"

    def _schedule_entry_is_current(
        self, entry: MapScheduleEntry, now: datetime | None = None
    ) -> bool:
        return self._schedule_entry_status(entry, now) == "当前"

    def _format_schedule_entry_range(self, entry: MapScheduleEntry) -> str:
        start = self._timestamp_to_beijing_datetime(entry.start_timestamp)
        end = self._timestamp_to_beijing_datetime(entry.end_timestamp)
        if start and end:
            if start.date() == end.date():
                return f"{start:%H:%M} - {end:%H:%M}"
            return f"{start:%m-%d %H:%M} - {end:%m-%d %H:%M}"
        return f"{entry.readable_start} - {entry.readable_end}"

    def _render_map_rotation_image(
        self, rotation_info: MapRotationInfo, mode: str = "ranked"
    ) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成地图轮换图片")

        if mode == "battle_royale":
            rotation_mode = rotation_info.battle_royale
            current_label = "当前三人赛地图"
            next_label = "下一张三人赛地图"
        else:
            rotation_mode = rotation_info.ranked
            current_label = "当前排位地图"
            next_label = "下一张排位地图"

        current = rotation_mode.current
        if current is None:
            raise ValueError("缺少当前地图轮换数据")

        output_dir = self._map_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = self._map_rotation_image_cache_key(
            mode=mode,
            output_dir=output_dir,
            current=current,
            next_entry=rotation_mode.next,
        )
        cached_path = self._get_cached_map_rotation_image(cache_key)
        if cached_path is not None:
            return cached_path

        image = self._build_map_rotation_card(
            current=current,
            next_entry=rotation_mode.next,
            current_label=current_label,
            next_label=next_label,
        )
        output_path = output_dir / f"map_rotation_{mode}_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._set_cached_map_rotation_image(cache_key, output_path)
        self._cleanup_old_map_cards(output_dir)
        return output_path

    def _get_cached_map_rotation_image(self, cache_key: tuple) -> Path | None:
        cache = getattr(self, "_map_rotation_image_cache", None)
        if not isinstance(cache, dict):
            return None
        cached = cache.get(cache_key)
        if not cached:
            return None
        saved_at, image_path = cached
        if time.monotonic() - saved_at > self._MAP_IMAGE_CACHE_TTL_SECONDS:
            cache.pop(cache_key, None)
            return None
        image_path = Path(image_path)
        if not image_path.exists():
            cache.pop(cache_key, None)
            return None
        return image_path

    def _set_cached_map_rotation_image(self, cache_key: tuple, image_path: Path) -> None:
        cache = getattr(self, "_map_rotation_image_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._map_rotation_image_cache = cache
        cache[cache_key] = (time.monotonic(), Path(image_path))

    def _map_rotation_image_cache_key(
        self,
        mode: str,
        output_dir: Path,
        current: MapRotationEntry,
        next_entry: MapRotationEntry | None,
    ) -> tuple:
        next_key = None
        if next_entry is not None:
            next_key = (
                next_entry.map_name,
                next_entry.start_timestamp,
                next_entry.end_timestamp,
            )
        return (
            mode,
            str(output_dir.resolve()),
            current.map_name,
            current.start_timestamp,
            current.end_timestamp,
            next_key,
        )

    def _map_card_output_dir(self) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / "map_cards"
        return Path(data_dir) / "map_cards"

    def _cleanup_old_map_cards(self, output_dir: Path, keep: int = 12) -> None:
        try:
            files = sorted(
                output_dir.glob("map_rotation_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理地图轮换缓存图片失败: {exc}")

    def _build_map_rotation_card(
        self,
        current: MapRotationEntry,
        next_entry: MapRotationEntry | None,
        current_label: str,
        next_label: str,
    ):
        width, height = self._MAP_CARD_SIZE
        current_height = self._MAP_CURRENT_HEIGHT
        next_height = height - current_height

        canvas = Image.new("RGBA", (width, height), (8, 10, 14, 255))
        current_bg = self._map_background(current, (width, current_height), focus_y=0.42)
        canvas.alpha_composite(current_bg, (0, 0))

        draw = ImageDraw.Draw(canvas)
        self._overlay_rect(canvas, (0, 0, width, current_height), (0, 0, 0, 80))
        self._draw_horizontal_gradient(canvas, 0, current_height, 190, left=True)

        self._draw_map_card_current_section(
            draw=draw,
            current=current,
            label=current_label,
            width=width,
            height=current_height,
        )

        if next_entry is not None:
            next_bg = self._map_background(next_entry, (width, next_height), focus_y=0.48)
            canvas.alpha_composite(next_bg, (0, current_height))
            self._overlay_rect(
                canvas, (0, current_height, width, height), (0, 0, 0, 94)
            )
            self._draw_horizontal_gradient(canvas, current_height, next_height, 165, left=True)
            self._draw_map_card_next_section(
                draw=draw,
                next_entry=next_entry,
                label=next_label,
                y=current_height,
                width=width,
                height=next_height,
            )
        else:
            draw.rectangle((0, current_height, width, height), fill=(9, 12, 18, 255))
            font = self._font(30, bold=True)
            self._draw_text_with_shadow(
                draw,
                (34, current_height + 34),
                "暂无下一张地图",
                font,
                fill=(238, 242, 246, 255),
            )

        draw.line((0, current_height, width, current_height), fill=(255, 255, 255, 36), width=2)
        return canvas.convert("RGB")

    @staticmethod
    def _overlay_rect(canvas, box: tuple[int, int, int, int], fill: tuple[int, int, int, int]) -> None:
        overlay = Image.new("RGBA", (box[2] - box[0], box[3] - box[1]), fill)
        canvas.alpha_composite(overlay, (box[0], box[1]))

    def _draw_map_card_current_section(
        self,
        draw,
        current: MapRotationEntry,
        label: str,
        width: int,
        height: int,
    ) -> None:
        title = self._map_card_name(current)
        time_range = self._format_rotation_range_short(current)
        remaining = self._format_remaining_for_card(current)

        label_font = self._font(27, bold=True)
        title_font = self._fit_font(draw, title, 61, 36, max_width=width - 330, bold=True)
        time_font = self._font(28)

        self._draw_text_with_shadow(
            draw,
            (34, 24),
            label,
            label_font,
            fill=(231, 236, 243, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (34, 62),
            title,
            title_font,
            fill=(255, 255, 255, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (36, 133),
            f"本轮时间：{time_range}",
            time_font,
            fill=(238, 242, 246, 255),
        )
        self._draw_remaining_ring(draw, (width - 150, 36), remaining, current)

    def _draw_map_card_next_section(
        self,
        draw,
        next_entry: MapRotationEntry,
        label: str,
        y: int,
        width: int,
        height: int,
    ) -> None:
        name = self._map_card_name(next_entry)
        time_range = self._format_rotation_range_short(next_entry)
        time_text = f"下轮时间：{time_range}"
        label_font = self._font(24, bold=True)
        name_font = self._fit_font(draw, name, 42, 30, max_width=width - 460, bold=True)
        time_font = self._fit_font(draw, time_text, 25, 18, max_width=width - 420)
        time_box = draw.textbbox((0, 0), time_text, font=time_font)
        time_width = time_box[2] - time_box[0]
        time_x = max(34, width - 34 - time_width)

        self._draw_text_with_shadow(
            draw,
            (34, y + 16),
            label,
            label_font,
            fill=(219, 226, 235, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (34, y + 45),
            name,
            name_font,
            fill=(255, 255, 255, 255),
        )
        self._draw_text_with_shadow(
            draw,
            (time_x, y + 42),
            time_text,
            time_font,
            fill=(238, 242, 246, 255),
        )

    def _draw_remaining_ring(
        self,
        draw,
        top_left: tuple[int, int],
        remaining_text: str,
        entry: MapRotationEntry,
    ) -> None:
        x, y = top_left
        size = 112
        box = (x, y, x + size, y + size)
        draw.ellipse(box, fill=(0, 0, 0, 156), outline=(255, 255, 255, 34), width=2)
        draw.arc(box, start=-90, end=-90 + int(360 * self._remaining_fraction(entry)), fill=(49, 191, 139, 255), width=10)

        text_font = self._fit_font(draw, remaining_text, 28, 18, max_width=size - 22, bold=True)
        label_font = self._font(19)
        text_box = draw.textbbox((0, 0), remaining_text, font=text_font)
        text_width = text_box[2] - text_box[0]
        self._draw_text_with_shadow(
            draw,
            (x + (size - text_width) / 2, y + 36),
            remaining_text,
            text_font,
            fill=(255, 255, 255, 255),
        )
        label = "剩余"
        label_box = draw.textbbox((0, 0), label, font=label_font)
        label_width = label_box[2] - label_box[0]
        draw.text(
            (x + (size - label_width) / 2, y + 69),
            label,
            font=label_font,
            fill=(220, 226, 233, 230),
        )

    def _draw_horizontal_gradient(
        self,
        canvas,
        y: int,
        height: int,
        alpha: int,
        left: bool = True,
    ) -> None:
        width = canvas.size[0]
        gradient = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        pixels = gradient.load()
        for x in range(width):
            ratio = 1 - (x / max(1, width - 1)) if left else x / max(1, width - 1)
            current_alpha = int(alpha * (ratio ** 1.7))
            for row in range(height):
                pixels[x, row] = (0, 0, 0, current_alpha)
        canvas.alpha_composite(gradient, (0, y))

    def _map_background(
        self,
        entry: MapRotationEntry,
        size: tuple[int, int],
        focus_y: float,
    ):
        asset_path = self._resolve_map_asset_path(entry.map_name)
        if asset_path.exists():
            with Image.open(asset_path) as raw:
                source = raw.convert("RGBA")
        else:
            source = self._placeholder_map_background(size, entry.map_name)

        resampling = getattr(Image, "Resampling", Image).LANCZOS
        background = ImageOps.fit(
            source,
            size,
            method=resampling,
            centering=(0.5, focus_y),
        )
        return background.filter(ImageFilter.GaussianBlur(radius=0.4))

    def _placeholder_map_background(self, size: tuple[int, int], map_name: str):
        width, height = size
        image = Image.new("RGBA", size, (23, 28, 36, 255))
        draw = ImageDraw.Draw(image)
        for row in range(height):
            shade = int(28 + 36 * row / max(1, height - 1))
            draw.line((0, row, width, row), fill=(shade, shade + 8, shade + 18, 255))
        font = self._fit_font(draw, map_name or "未知地图", 42, 24, width - 80, bold=True)
        self._draw_text_with_shadow(
            draw,
            (38, height // 2 - 24),
            map_name or "未知地图",
            font,
            fill=(230, 236, 244, 230),
        )
        return image

    def _resolve_map_asset_path(self, map_name: str) -> Path:
        normalized = str(map_name or "").strip()
        aliases = {
            "Broken Moon": "Broken_Moon.png",
            "E-District": "E-District.png",
            "Kings Canyon": "Kings_Canyon.png",
            "Olympus": "Olympus.png",
            "Storm Point": "Storm_Point.png",
            "World's Edge": "Worlds_Edge.png",
            "Worlds Edge": "Worlds_Edge.png",
        }
        candidates: list[str] = []
        if normalized in aliases:
            candidates.append(aliases[normalized])
        if normalized:
            slug = normalized.replace("'", "").replace("’", "").replace(" ", "_")
            slug = "".join(ch for ch in slug if ch.isalnum() or ch in {"_", "-"})
            if slug:
                candidates.append(f"{slug}.png")
        candidates.append("unknown.png")

        for candidate in candidates:
            path = self._MAP_ASSET_DIR / candidate
            if path.exists():
                return path
        return self._MAP_ASSET_DIR / candidates[0]

    def _map_card_name(self, entry: MapRotationEntry) -> str:
        name = (entry.map_name_zh or entry.map_name or "未知地图").strip()
        return name or "未知地图"

    def _format_rotation_range_short(self, entry: MapRotationEntry) -> str:
        start = self._timestamp_to_beijing_datetime(entry.start_timestamp)
        end = self._timestamp_to_beijing_datetime(entry.end_timestamp)
        if start and end:
            if start.date() == end.date():
                return f"{start:%H:%M} - {end:%H:%M}"
            return f"{start:%m-%d %H:%M} - {end:%m-%d %H:%M}"
        start_text = entry.readable_start or "未知"
        end_text = entry.readable_end or "未知"
        return f"{start_text} - {end_text}"

    def _format_remaining_for_card(self, entry: MapRotationEntry) -> str:
        seconds = entry.remaining_secs
        if seconds is None:
            seconds = self._remaining_seconds_from_timer(entry.remaining_timer)
        if seconds is None and entry.end_timestamp:
            seconds = max(0, int(entry.end_timestamp - datetime.now(timezone.utc).timestamp()))
        if seconds is None:
            return entry.remaining_timer or "未知"

        minutes = max(0, int(seconds) // 60)
        days = minutes // 1440
        hours = (minutes % 1440) // 60
        mins = minutes % 60
        if days:
            return f"{days}天{hours}时"
        if hours:
            return f"{hours}时{mins}分"
        return f"{mins}分"

    @staticmethod
    def _remaining_seconds_from_timer(timer: str) -> int | None:
        parts = str(timer or "").strip().split(":")
        if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
            return None
        numbers = [int(part) for part in parts]
        if len(numbers) == 2:
            minutes, seconds = numbers
            return minutes * 60 + seconds
        hours, minutes, seconds = numbers
        return hours * 3600 + minutes * 60 + seconds

    def _remaining_fraction(self, entry: MapRotationEntry) -> float:
        if entry.remaining_secs is not None and entry.duration_secs:
            return min(1.0, max(0.0, entry.remaining_secs / entry.duration_secs))
        seconds = self._remaining_seconds_from_timer(entry.remaining_timer)
        if seconds is not None and entry.duration_secs:
            return min(1.0, max(0.0, seconds / entry.duration_secs))
        if entry.start_timestamp and entry.end_timestamp:
            total = entry.end_timestamp - entry.start_timestamp
            if total > 0:
                remaining = entry.end_timestamp - datetime.now(timezone.utc).timestamp()
                return min(1.0, max(0.0, remaining / total))
        return 0.72

    @staticmethod
    def _timestamp_to_beijing_datetime(timestamp: int):
        if not timestamp:
            return None
        try:
            return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(
                SHANGHAI_TZ
            )
        except Exception:
            return None

    def _font(self, size: int, bold: bool = False):
        candidates = [
            r"C:\Windows\Fonts\msyhbd.ttc" if bold else r"C:\Windows\Fonts\msyh.ttc",
            r"C:\Windows\Fonts\simhei.ttf",
            r"C:\Windows\Fonts\simsun.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"
            if bold
            else "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
            if bold
            else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ]
        for font_path in candidates:
            try:
                path = Path(font_path)
                if path.exists():
                    return ImageFont.truetype(str(path), size=size)
            except Exception:
                continue
        return ImageFont.load_default()

    def _fit_font(
        self,
        draw,
        text: str,
        size: int,
        min_size: int,
        max_width: int,
        bold: bool = False,
    ):
        current_size = size
        while current_size >= min_size:
            font = self._font(current_size, bold=bold)
            box = draw.textbbox((0, 0), text, font=font)
            if box[2] - box[0] <= max_width:
                return font
            current_size -= 2
        return self._font(min_size, bold=bold)

    @staticmethod
    def _draw_text_with_shadow(draw, xy, text: str, font, fill) -> None:
        x, y = xy
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 180))
        draw.text((x, y), text, font=font, fill=fill)

    def _render_predator_info_image(self, predator_info: PredatorInfo) -> Path:
        if Image is None:
            raise RuntimeError("缺少 Pillow，无法生成猎杀线图片")
        if not self._PREDATOR_TEMPLATE_PATH.exists():
            raise FileNotFoundError(f"缺少猎杀线图片模板: {self._PREDATOR_TEMPLATE_PATH}")

        output_dir = self._predator_card_output_dir()
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_key = self._predator_info_image_cache_key(output_dir, predator_info)
        cached_path = self._get_cached_predator_info_image(cache_key)
        if cached_path is not None:
            return cached_path

        image = self._build_predator_info_card(predator_info)
        output_path = output_dir / f"predator_info_{now_epoch_ms()}.png"
        image.save(output_path, format="PNG", optimize=True)
        self._set_cached_predator_info_image(cache_key, output_path)
        self._cleanup_old_predator_cards(output_dir)
        return output_path

    def _get_cached_predator_info_image(self, cache_key: tuple) -> Path | None:
        cache = getattr(self, "_predator_info_image_cache", None)
        if not isinstance(cache, dict):
            return None
        cached = cache.get(cache_key)
        if not cached:
            return None
        saved_at, image_path = cached
        if time.monotonic() - saved_at > self._PREDATOR_IMAGE_CACHE_TTL_SECONDS:
            cache.pop(cache_key, None)
            return None
        image_path = Path(image_path)
        if not image_path.exists():
            cache.pop(cache_key, None)
            return None
        return image_path

    def _set_cached_predator_info_image(
        self, cache_key: tuple, image_path: Path
    ) -> None:
        cache = getattr(self, "_predator_info_image_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._predator_info_image_cache = cache
        cache[cache_key] = (time.monotonic(), Path(image_path))

    def _predator_info_image_cache_key(
        self, output_dir: Path, predator_info: PredatorInfo
    ) -> tuple:
        platform_key = []
        for platform in ("PC", "PS4", "X1", "SWITCH"):
            stats = predator_info.platforms.get(platform)
            if stats is None:
                platform_key.append((platform, None))
                continue
            platform_key.append(
                (
                    platform,
                    stats.threshold_rp,
                    stats.masters_and_preds,
                    stats.update_timestamp,
                )
            )

        try:
            template_mtime = self._PREDATOR_TEMPLATE_PATH.stat().st_mtime_ns
        except OSError:
            template_mtime = 0
        return (str(output_dir.resolve()), template_mtime, tuple(platform_key))

    def _predator_card_output_dir(self) -> Path:
        data_dir = getattr(self, "_data_dir", None)
        if data_dir is None:
            return self._PLUGIN_ROOT / "_generated" / "predator_cards"
        return Path(data_dir) / "predator_cards"

    def _cleanup_old_predator_cards(self, output_dir: Path, keep: int = 8) -> None:
        try:
            files = sorted(
                output_dir.glob("predator_info_*.png"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for file in files[keep:]:
                file.unlink(missing_ok=True)
        except Exception as exc:
            logger.debug(f"清理猎杀线缓存图片失败: {exc}")

    def _build_predator_info_card(self, predator_info: PredatorInfo):
        with Image.open(self._PREDATOR_TEMPLATE_PATH) as raw:
            canvas = raw.convert("RGBA")
        draw = ImageDraw.Draw(canvas)

        # 模板来自用户提供的固定版式，这里只在预留黑框内写入实时数据。
        query_text = self._now_str()
        time_font = self._fit_font(draw, query_text, 38, 28, 350, bold=True)
        update_text = self._format_predator_update_time(
            self._select_predator_platforms(predator_info)
        ) or "暂无"
        update_font = self._fit_font(draw, update_text, 38, 28, 350, bold=True)
        value_fill = self._PREDATOR_NEUTRAL

        self._draw_centered_predator_text(
            draw, (636, 244, 1053, 318), query_text, time_font, value_fill
        )
        self._draw_centered_predator_text(
            draw, (636, 354, 1053, 426), update_text, update_font, value_fill
        )

        rows = {
            "PC": ((290, 535, 715, 648), (790, 536, 1078, 615)),
            "PS4": ((290, 756, 715, 869), (790, 756, 1078, 835)),
            "X1": ((290, 976, 715, 1089), (790, 976, 1078, 1055)),
            "SWITCH": ((290, 1197, 715, 1311), (790, 1197, 1078, 1276)),
        }
        for platform, (threshold_box, count_box) in rows.items():
            stats = predator_info.platforms.get(platform)
            threshold = self._predator_threshold_text(stats)
            count = self._predator_master_count_text(stats)
            threshold_font = self._fit_font(
                draw,
                threshold,
                58,
                36,
                threshold_box[2] - threshold_box[0] - 36,
                bold=True,
            )
            count_font = self._fit_font(
                draw,
                count,
                60,
                34,
                count_box[2] - count_box[0] - 42,
                bold=True,
            )
            self._draw_centered_predator_text(
                draw,
                threshold_box,
                threshold,
                threshold_font,
                self._predator_threshold_fill(stats),
            )
            self._draw_centered_predator_text(
                draw,
                count_box,
                count,
                count_font,
                self._predator_master_count_fill(stats),
            )

        return canvas.convert("RGB")

    def _draw_centered_predator_text(
        self,
        draw,
        box: tuple[int, int, int, int],
        text: str,
        font,
        fill: tuple[int, int, int, int],
    ) -> None:
        left, top, right, bottom = box
        x = self._centered_text_x(draw, text, font, left, right)
        y = self._centered_text_y(draw, text, font, top, bottom)
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=2,
            stroke_fill=(0, 0, 0, 185),
        )

    def _predator_threshold_text(self, stats: PredatorPlatformStats | None) -> str:
        if stats is None:
            return "暂无"
        return f"{self._format_number(stats.threshold_rp)} RP"

    def _predator_master_count_text(self, stats: PredatorPlatformStats | None) -> str:
        if stats is None:
            return "暂无"
        return self._format_number(stats.masters_and_preds)

    def _predator_threshold_fill(
        self, stats: PredatorPlatformStats | None
    ) -> tuple[int, int, int, int]:
        if stats is None:
            return self._PREDATOR_NEUTRAL
        if stats.threshold_rp <= 16000:
            return self._PREDATOR_GREEN
        return self._PREDATOR_DEEP_RED

    def _predator_master_count_fill(
        self, stats: PredatorPlatformStats | None
    ) -> tuple[int, int, int, int]:
        if stats is None:
            return self._PREDATOR_NEUTRAL
        if stats.masters_and_preds < 750:
            return self._PREDATOR_GREEN
        return self._PREDATOR_DEEP_RED

    def _format_predator_info_text(
        self, predator_info: PredatorInfo, selected_platform: str = ""
    ) -> str:
        platform_stats = self._select_predator_platforms(predator_info, selected_platform)
        if not platform_stats:
            return "\n".join(
                [
                    self._time_line(),
                    "⚠️ 暂未获取到本赛季猎杀线数据，请稍后再试",
                ]
            )

        updated_at = self._format_predator_update_time(platform_stats)
        lines = [
            "🏆 本赛季猎杀线",
            f"🕒 查询时间：{self._now_str()}",
        ]
        if updated_at:
            lines.append(f"🔄 数据更新：{updated_at}")
        lines.append("——")

        for stats in platform_stats:
            lines.append(self._format_predator_platform_line(stats))

        lines.append("ℹ️ 大师数量来自 API 的大师+猎杀总数")
        return "\n".join(lines)

    def _select_predator_platforms(
        self, predator_info: PredatorInfo, selected_platform: str = ""
    ) -> list[PredatorPlatformStats]:
        if selected_platform:
            stats = predator_info.platforms.get(selected_platform)
            return [stats] if stats else []

        ordered = []
        for platform in ("PC", "PS4", "X1", "SWITCH"):
            stats = predator_info.platforms.get(platform)
            if stats is not None:
                ordered.append(stats)
        return ordered

    def _format_predator_platform_line(self, stats: PredatorPlatformStats) -> str:
        platform = self._format_platform_with_icon(stats.platform)
        threshold = self._format_number(stats.threshold_rp)
        count = self._format_number(stats.masters_and_preds)
        return f"{platform}：猎杀线 {threshold} RP｜大师数量 {count}（包含猎杀）"

    def _format_predator_update_time(
        self, platform_stats: list[PredatorPlatformStats]
    ) -> str:
        timestamps = [item.update_timestamp for item in platform_stats if item.update_timestamp]
        if not timestamps:
            return ""
        return self._format_timestamp_to_beijing(max(timestamps))

    def _format_map_name(self, entry: MapRotationEntry) -> str:
        if entry.map_name_zh and entry.map_name_zh != entry.map_name:
            return f"{entry.map_name_zh} / {entry.map_name}"
        return entry.map_name_zh or entry.map_name or "未知"

    def _format_rotation_range(self, entry: MapRotationEntry) -> str:
        start_text = self._format_timestamp_to_beijing(entry.start_timestamp)
        end_text = self._format_timestamp_to_beijing(entry.end_timestamp)
        if not start_text:
            start_text = entry.readable_start or "未知"
        if not end_text:
            end_text = entry.readable_end or "未知"
        return f"{start_text} ~ {end_text}"

    def _format_timestamp_to_beijing(self, timestamp: int) -> str:
        if not timestamp:
            return ""
        try:
            dt = datetime.fromtimestamp(int(timestamp), tz=timezone.utc)
            bj = dt.astimezone(SHANGHAI_TZ)
            return bj.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    def _format_platform_with_icon(self, platform: str) -> str:
        icons = {
            "PC": "🖥️ PC",
            "PS4": "🎮 PlayStation",
            "X1": "🎮 Xbox",
            "SWITCH": "🎮 Switch",
        }
        return icons.get(platform, self._format_platform(platform))

    @staticmethod
    def _format_number(value: int) -> str:
        try:
            return f"{int(value):,}"
        except Exception:
            return str(value)

    def _format_season_info(self, season_info: SeasonInfo) -> str:
        season_label = "未知"
        if season_info.season_number is not None:
            if season_info.season_name:
                season_label = f"S{season_info.season_number} · {season_info.season_name}"
            else:
                season_label = f"S{season_info.season_number}"

        lines = [
            self._time_line(),
            "🗓️ Apex 赛季时间信息",
            f"📌 查询赛季: {season_label}",
        ]
        if season_info.status_text and season_info.status_text != "未知":
            lines.append(f"📍 赛季状态: {season_info.status_text}")

        start_bj = self._to_beijing_time(season_info.start_iso)
        end_bj = self._to_beijing_time(season_info.end_iso)
        if start_bj:
            lines.append(f"🟢 开始时间(北京时间): {start_bj}")
        if end_bj:
            lines.append(f"🔴 结束时间(北京时间): {end_bj}")
        if not start_bj and season_info.start_date and season_info.start_date != "未知":
            lines.append(f"🟢 开始时间: {season_info.start_date}")
        if not end_bj and season_info.end_date and season_info.end_date != "未知":
            lines.append(f"🔴 结束时间: {season_info.end_date}")

        remaining = self._format_remaining(season_info.end_iso)
        if remaining:
            lines.append(f"⏳ 剩余时间: {remaining}")

        progress = self._format_progress(season_info.start_iso, season_info.end_iso)
        if progress:
            lines.append(f"📈 赛季进度: {progress}")

        if season_info.supports_ranked_splits and season_info.splits:
            if season_info.current_split_label:
                lines.append(f"🧩 当前阶段: {season_info.current_split_label}")
            if season_info.next_transition_label and season_info.next_transition_iso:
                transition_bj = self._to_beijing_time(season_info.next_transition_iso)
                if transition_bj:
                    lines.append(
                        f"🔄 {season_info.next_transition_label}(北京时间): {transition_bj}"
                    )
                transition_remaining = self._format_remaining(season_info.next_transition_iso)
                if transition_remaining:
                    lines.append(f"⏱️ 阶段剩余: {transition_remaining}")
            for split in season_info.splits:
                split_start = self._to_beijing_time(split.start_iso) or split.start_date
                split_end = self._to_beijing_time(split.end_iso) or split.end_date
                lines.append(
                    f"{split.index}️⃣ {split.stage_name}: {split_start} ~ {split_end}"
                )
            if any(not split.exact for split in season_info.splits):
                lines.append(
                    "⚠️ Split 时间说明: 以下上下半赛季时间已转换为北京时间；若来源未提供精确时刻，则按北京时间周三凌晨 1 点推导。"
                )
            if season_info.split_source:
                split_meta = season_info.split_source
                if season_info.split_note:
                    split_meta = f"{split_meta}（{season_info.split_note}）"
                lines.append(f"🧠 Split 数据: {split_meta}")
        elif season_info.split_note:
            lines.append(f"🧩 排位阶段: {season_info.split_note}")

        lines.append(f"ℹ️ 数据来源: {season_info.source}")
        lines.append("⚠️ 第三方来源仅供参考")
        return "\n".join(lines)

    @staticmethod
    def _to_beijing_time(iso_value: str) -> str:
        if not iso_value:
            return ""
        try:
            value = iso_value
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            dt = datetime.fromisoformat(value)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            bj = dt.astimezone(SHANGHAI_TZ)
            return bj.strftime("%Y-%m-%d %H:%M")
        except Exception:
            return ""

    @staticmethod
    def _format_remaining(end_iso: str) -> str:
        if not end_iso:
            return ""
        try:
            value = end_iso
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            end_dt = datetime.fromisoformat(value)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            delta = end_dt - now
            total_seconds = int(delta.total_seconds())
            if total_seconds <= 0:
                return "已结束"
            days = total_seconds // 86400
            hours = (total_seconds % 86400) // 3600
            minutes = (total_seconds % 3600) // 60
            return f"{days} 天 {hours} 小时 {minutes} 分钟"
        except Exception:
            return ""

    @staticmethod
    def _format_progress(start_iso: str, end_iso: str) -> str:
        if not start_iso or not end_iso:
            return ""
        try:
            start_value = start_iso.replace("Z", "+00:00") if start_iso.endswith("Z") else start_iso
            end_value = end_iso.replace("Z", "+00:00") if end_iso.endswith("Z") else end_iso
            start_dt = datetime.fromisoformat(start_value)
            end_dt = datetime.fromisoformat(end_value)
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=timezone.utc)
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            total_seconds = (end_dt - start_dt).total_seconds()
            if total_seconds <= 0:
                return ""
            now = datetime.now(timezone.utc)
            elapsed_seconds = (now - start_dt).total_seconds()
            if elapsed_seconds < 0:
                elapsed_seconds = 0
            if elapsed_seconds > total_seconds:
                elapsed_seconds = total_seconds
            percent = elapsed_seconds / total_seconds * 100
            elapsed_days = int(elapsed_seconds // 86400)
            elapsed_hours = int((elapsed_seconds % 86400) // 3600)
            elapsed_minutes = int((elapsed_seconds % 3600) // 60)
            total_days = int(total_seconds // 86400)
            total_hours = int((total_seconds % 86400) // 3600)
            total_minutes = int((total_seconds % 3600) // 60)
            elapsed_text = f"{elapsed_days} 天 {elapsed_hours} 小时 {elapsed_minutes} 分钟"
            total_text = f"{total_days} 天 {total_hours} 小时 {total_minutes} 分钟"
            return f"{percent:.2f}%（已过 {elapsed_text} / 共 {total_text}）"
        except Exception:
            return ""
