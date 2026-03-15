from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dataclasses import dataclass
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
import astrbot.api.message_components as Comp
from astrbot.api.star import Context, Star, StarTools

try:
    from .apex_service import (
        ApexApiClient,
        ApexPlayerStats,
        SeasonInfo,
        PlayerNotFoundError,
        normalize_platform,
        is_likely_season_reset,
        is_score_drop_abnormal,
    )
    from .storage import GroupStore, PlayerRecord
    from .utils import coerce_bool, coerce_int, now_epoch_ms, now_str
except ImportError:
    from apex_service import (
        ApexApiClient,
        ApexPlayerStats,
        SeasonInfo,
        PlayerNotFoundError,
        normalize_platform,
        is_likely_season_reset,
        is_score_drop_abnormal,
    )
    from storage import GroupStore, PlayerRecord
    from utils import coerce_bool, coerce_int, now_epoch_ms, now_str


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
    _KEYWORD_COMMAND_BLOCKLIST = {
        # English commands
        "apexrank",
        "apexrankwatch",
        "apexranklist",
        "apexrankremove",
        "apexseason",
        "apextest",
        "apexhelp",
        "apex帮助",
        "apexrankhelp",
        "apexblacklist",
        # CN aliases
        "apex监控",
        "apex列表",
        "apex移除",
        "apex查询",
        "视奸",
        "持续视奸",
        "取消持续视奸",
        "apex赛季",
        "新赛季",
        "apex测试",
        "apex黑名单",
        "不准视奸",
        "apexban",
        # Season keyword switches
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
        # Fallback for adapters that strip leading newlines/spaces.
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
        # "引用回复" is supported by QQ 个人号（aiocqhttp / OneBot v11）.
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
                # Use QQ 引用回复 instead of @mentioning the sender.
                return event.chain_result(
                    [Comp.Reply(id=reply_id), Comp.Plain(text=text.lstrip("\n"))]
                )
        return event.plain_result(self._format_passive_text(event, text))

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

    def _missing_api_key_text(self) -> str:
        return "\n".join(
            [
                self._time_line(),
                "⚠️ 请先在插件配置中填写 API Key",
                f"🔑 Key 申请地址: {self._api_key_apply_url()}",
            ]
        )

    def _api_request_failed_text(self, action: str = "查询") -> str:
        return "\n".join(
            [
                self._time_line(),
                f"❌ {action}失败：请检查网络、API Key 是否有效，或稍后再试",
                f"🔑 Key 申请地址: {self._api_key_apply_url()}",
            ]
        )

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

        lines = [
            self._time_line(),
            "📋 Apex Rank Watch 帮助（/apexhelp 或 /apex帮助）",
            "——",
            "【查询】",
            "/apexrank <玩家|uid:...> [平台]  别名：/apex查询 /视奸",
            "例：/apexrank moeneri pc",
            "——",
            "【监控（群聊）】",
            "/apexrankwatch <玩家|uid:...> [平台]  别名：/apex监控 /持续视奸",
            "/apexranklist  别名：/apex列表",
            "/apexrankremove <玩家|uid:...> [平台]  别名：/apex移除 /取消持续视奸",
            "——",
            "【信息】",
            "/apexseason  别名：/apex赛季 /新赛季",
            "关键词：消息包含「赛季」自动回复（/赛季关闭，/赛季开启）",
            "——",
            "【管理】",
            "/apexblacklist <add|remove|list|clear> <玩家ID>  别名：/apex黑名单 /不准视奸 /apexban",
            "——",
            "【参数】",
            "平台：PC / PS4 / X1 / SWITCH（默认 PC；PC 无数据自动尝试其他平台）",
            "UUID：使用 uid: 或 uuid: 前缀（例：/apexrank uid:123456）",
            "——",
            f"⏱️ 监控间隔：{self._config.check_interval} 分钟",
            f"🔻 最小有效分：{self._config.min_valid_score} 分",
            "⚠️ 异常分数：仅当高分(>1000)掉到接近0分(<10)时判定为异常",
            "🛡️ 权限：群白名单、QQ 黑名单、主人 QQ、私聊开关",
        ]

        config_blacklist = self._get_config_blacklist()
        runtime_blacklist = self._runtime_blacklist
        total_blacklist = len(config_blacklist) + len(runtime_blacklist)
        if total_blacklist:
            lines.append(
                "⛔ 黑名单说明：配置黑名单 "
                f"{len(config_blacklist)} 个，动态黑名单 {len(runtime_blacklist)} 个"
            )
            lines.append("⛔ 黑名单ID无法被查询或监控")

        if self._config.query_blocklist:
            count = len(_split_csv(self._config.query_blocklist))
            lines.append(f"⛔ 禁止查询玩家：已设置 {count} 个玩家ID")

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
                "\n".join([self._time_line(), "⚠️ 请提供玩家名称，例如: /apexrank moeneri"])
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
        yield self._plain(event, self._format_player_rank_text(player_data))

    @filter.command("apexseason")
    async def apexseason(self, event: AstrMessageEvent):
        deny = self._guard_access(event)
        if deny:
            yield self._plain(event, "\n".join([self._time_line(), deny]))
            return

        try:
            season_info = await self._api.fetch_current_season_info()
        except Exception as exc:
            logger.error(f"赛季时间查询失败: {exc}")
            yield self._plain(event, 
                "\n".join([self._time_line(), "❌ 查询失败：无法获取赛季时间"])
            )
            return

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
            # Avoid double trigger: command + keyword listener.
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
                    [self._time_line(), "⚠️ 请提供要监控的玩家名称，例如: /apexrankwatch moeneri"]
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

        yield self._plain(event, 
            "\n".join(
                [
                    self._time_line(),
                    f"✅ 成功添加对 {player_data.name} 的排名监控",
                    f"🖥️ 平台: {self._format_platform(normalized_platform)}",
                    f"🏆 当前排名: {self._get_rank_display_text(player_data)}",
                ]
            )
        )

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

        response_lines = [self._time_line(), "📋 本群 Apex 排名监控列表"]
        for index, player in enumerate(group.players.values(), start=1):
            rank_display = (
                f"{player.rank_name} {player.rank_div}"
                if player.rank_div
                else player.rank_name
            )
            platform = getattr(player, "platform", "PC") or "PC"
            response_lines.append(f"👤 玩家 {index}: {player.player_name}")
            response_lines.append(f"🖥️ 平台: {self._format_platform(platform)}")
            response_lines.append(f"🏆 段位: {rank_display}")
            response_lines.append(f"🔢 分数: {player.rank_score}")
            if player.global_rank_percent and player.global_rank_percent != "未知":
                response_lines.append(f"🌎 全球排名: {player.global_rank_percent}%")
            if player.selected_legend:
                response_lines.append(f"🎮 当前英雄: {player.selected_legend}")
                if player.legend_kills_percent:
                    response_lines.append(
                        f"📊 击杀排名: 全球 {player.legend_kills_percent}%"
                    )
            response_lines.append("➖ —")

        response_lines.append(f"🔢 总计: {len(group.players)} 个玩家")
        response_lines.append(f"⏱️ 检测间隔: {self._config.check_interval} 分钟")
        response_lines.append(
            "⚠️ 分数异常判断: 仅当高分(>1000)掉到接近0分(<10)时判定为异常"
        )
        response_lines.append(f"🔻 最小有效分数: {self._config.min_valid_score} 分")

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
                    [self._time_line(), "⚠️ 请提供要移除监控的玩家名称，例如: /apexrankremove moeneri"]
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
                        "⚠️ 检测到同名多平台监控，请指定平台，例如: /apexrankremove moeneri pc",
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
                "\n".join([self._time_line(), "⚠️ 请提供玩家ID，例如：/apexblacklist add moeneri"])
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
    async def apexseason_cn(self, event: AstrMessageEvent):
        async for result in self.apexseason(event):
            yield result

    @filter.command("新赛季")
    async def apexseason_new_cn(self, event: AstrMessageEvent):
        async for result in self.apexseason(event):
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

    async def _poll_once(self) -> None:
        if not self._config.api_key:
            return

        fetch_jobs = []
        for group_id, group in self._store.iter_groups():
            for _, player in list(group.players.items()):
                if self._is_blacklisted(player.player_name) or self._is_query_blocked(player.player_name):
                    logger.warning(f"跳过黑名单ID的定时检查: {player.player_name}")
                    continue

                platform = getattr(player, "platform", "PC") or "PC"
                use_uid = bool(getattr(player, "use_uid", False))
                lookup_id = getattr(player, "lookup_id", "") or player.player_name
                fetch_jobs.append(
                    self._poll_fetch_player(
                        group_id=group_id,
                        group_origin=group.origin,
                        player=player,
                        platform=platform,
                        lookup_id=lookup_id,
                        use_uid=use_uid,
                    )
                )

        if not fetch_jobs:
            return

        results = await asyncio.gather(*fetch_jobs)
        dirty = False
        for result in results:
            player = result.player
            platform = result.platform
            if result.not_found:
                logger.warning(f"玩家 {player.player_name} 在平台 {platform} 未找到")
                continue
            if result.error:
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
                if player_data.legend_kills_rank:
                    lines.append(
                        f"📊 击杀排名: 全球 {player_data.legend_kills_rank.global_percent}%"
                    )
            if player_data.is_in_lobby_or_match:
                lines.append(f"🎯 当前状态: {player_data.current_state}")
        else:
            lines.append("🎮 在线状态: 离线")

        return "\n".join(lines)

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
            f"📌 当前赛季: {season_label}",
        ]

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
            bj = dt.astimezone(ZoneInfo("Asia/Shanghai"))
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
