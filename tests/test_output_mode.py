from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _install_astrbot_stubs() -> None:
    if "astrbot.api" in sys.modules:
        return

    astrbot_mod = types.ModuleType("astrbot")
    api_mod = types.ModuleType("astrbot.api")
    event_mod = types.ModuleType("astrbot.api.event")
    components_mod = types.ModuleType("astrbot.api.message_components")
    star_mod = types.ModuleType("astrbot.api.star")

    class _Logger:
        def __getattr__(self, _name):
            return lambda *args, **kwargs: None

    class _Filter:
        class EventMessageType:
            ALL = "ALL"

        @staticmethod
        def command(*_args, **_kwargs):
            return lambda func: func

        @staticmethod
        def event_message_type(*_args, **_kwargs):
            return lambda func: func

    class _MessageChain:
        pass

    class _AstrMessageEvent:
        pass

    class _Star:
        pass

    class _Context:
        pass

    class _StarTools:
        @staticmethod
        def get_data_dir(*_args, **_kwargs):
            return Path("data")

    api_mod.logger = _Logger()
    event_mod.AstrMessageEvent = _AstrMessageEvent
    event_mod.MessageChain = _MessageChain
    event_mod.filter = _Filter()
    star_mod.Context = _Context
    star_mod.Star = _Star
    star_mod.StarTools = _StarTools

    sys.modules["astrbot"] = astrbot_mod
    sys.modules["astrbot.api"] = api_mod
    sys.modules["astrbot.api.event"] = event_mod
    sys.modules["astrbot.api.message_components"] = components_mod
    sys.modules["astrbot.api.star"] = star_mod


def _load_main_module():
    _install_astrbot_stubs()
    return importlib.import_module("main")


def _sample_player(main_module):
    return main_module.ApexPlayerStats(
        name="yumola",
        uid="123456",
        level=321,
        rank_score=18888,
        rank_name="Master",
        rank_div=0,
        global_rank_percent="0.7",
        is_online=True,
        selected_legend="Wraith",
        legend_kills_rank=None,
        current_state="in Lobby",
        is_in_lobby_or_match=True,
        platform="PC",
    )


def _plugin_config(main_module, **overrides):
    raw = {
        "api_key": "key",
        "min_valid_score": 1,
        "output_mode": "text",
        "player_aliases": "",
        "alias_enabled": True,
        "alias_admin_only": True,
    }
    raw.update(overrides)
    return main_module.PluginConfig.from_raw(raw)


def _history_record(
    main_module,
    *,
    captured_at: int = 1_780_000_000_000,
    from_score: int = 18000,
    to_score: int = 18120,
    player_name: str = "yumola",
):
    return main_module.ScoreChangeRecord(
        captured_at=captured_at,
        player_name=player_name,
        platform="PC",
        from_score=from_score,
        to_score=to_score,
        score_delta=to_score - from_score,
        from_rank_name="Master",
        from_rank_div=0,
        to_rank_name="Master",
        to_rank_div=0,
        global_rank_percent="0.7",
        selected_legend="Wraith",
        is_season_reset=False,
    )


def _watch_record(
    main_module,
    *,
    score: int = 18000,
    watch_mode: str = "notify",
    player_name: str = "yumola",
    lookup_id: str = "1007669673322",
    use_uid: bool = True,
):
    record = main_module.PlayerRecord(
        player_name=player_name,
        platform="PC",
        lookup_id=lookup_id,
        use_uid=use_uid,
        rank_score=score,
        rank_name="Master",
        rank_div=0,
        last_checked=0,
    )
    record.watch_mode = watch_mode
    record.history = []
    return record


def _blank_name_uid_payload():
    return {
        "global": {
            "name": "",
            "uid": "1007669673322",
            "level": 358,
            "rank": {
                "rankScore": 5933,
                "rankName": "Gold",
                "rankDiv": 4,
                "ALStopPercentGlobal": 54,
            },
        },
        "realtime": {
            "isOnline": 0,
            "selectedLegend": "Mad Maggie",
            "currentStateAsText": "Offline",
        },
        "legends": {"selected": {"data": []}},
    }


def test_output_mode_defaults_to_image_and_accepts_text():
    main_module = _load_main_module()

    default_config = main_module.PluginConfig.from_raw({})
    text_config = main_module.PluginConfig.from_raw({"output_mode": "text"})
    invalid_config = main_module.PluginConfig.from_raw({"output_mode": "bad"})

    assert default_config.output_mode == "image"
    assert text_config.output_mode == "text"
    assert invalid_config.output_mode == "image"


def test_player_record_legacy_json_defaults_to_notify_history():
    main_module = _load_main_module()

    record = main_module.PlayerRecord.from_dict(
        {
            "player_name": "LegacyPlayer",
            "platform": "PC",
            "lookup_id": "LegacyPlayer",
            "use_uid": False,
            "rank_score": 15000,
            "rank_name": "钻石",
            "rank_div": 1,
            "last_checked": 123,
        }
    )

    assert record.watch_mode == "notify"
    assert record.history == []


def test_player_record_history_round_trips_valid_rows():
    main_module = _load_main_module()

    record = main_module.PlayerRecord.from_dict(
        {
            "player_name": "yumola",
            "platform": "PC",
            "lookup_id": "1007669673322",
            "use_uid": True,
            "rank_score": 18120,
            "rank_name": "Master",
            "rank_div": 0,
            "last_checked": 123,
            "watch_mode": "record",
            "history": [
                {
                    "captured_at": 1_780_000_000_000,
                    "player_name": "yumola",
                    "platform": "PC",
                    "from_score": 18000,
                    "to_score": 18120,
                    "score_delta": 120,
                    "from_rank_name": "Master",
                    "from_rank_div": 0,
                    "to_rank_name": "Master",
                    "to_rank_div": 0,
                    "global_rank_percent": "0.7",
                    "selected_legend": "Wraith",
                    "is_season_reset": False,
                },
                "bad-row",
            ],
        }
    )

    assert record.watch_mode == "record"
    assert len(record.history) == 1
    assert record.history[0].score_delta == 120
    payload = record.to_dict()
    assert payload["history"][0]["to_score"] == 18120


def test_player_alias_config_is_parsed_from_panel_string():
    main_module = _load_main_module()

    config = main_module.PluginConfig.from_raw(
        {
            "player_aliases": "测试=uid:1234,小明=EaName pc",
            "alias_admin_only": False,
        }
    )
    plugin = object.__new__(main_module.Main)
    plugin._config = config
    plugin._runtime_player_aliases = {}

    assert config.alias_admin_only is False
    assert config.alias_enabled is True
    assert plugin._get_player_aliases() == {
        "测试": "uid:1234",
        "小明": "EaName pc",
    }


def test_player_alias_switch_can_disable_alias_resolution():
    main_module = _load_main_module()

    config = main_module.PluginConfig.from_raw(
        {
            "player_aliases": "测试=uid:1234",
            "alias_enabled": False,
        }
    )
    plugin = object.__new__(main_module.Main)
    plugin._config = config
    plugin._runtime_player_aliases = {}

    assert config.alias_enabled is False
    assert plugin._resolve_player_alias("测试", "") == ("测试", "")


@pytest.mark.parametrize(
    "command",
    [
        "apexrank",
        "apex查询",
        "视奸",
        "apexrankwatch",
        "apex监控",
        "持续视奸",
        "apexrankremove",
        "apex移除",
        "取消持续视奸",
        "apex绑定",
    ],
)
def test_player_alias_commands_strip_command_name(command):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)

    class _Event:
        message_str = f"/{command} 测试"

    assert plugin._extract_command_args(_Event()) == "测试"


def test_player_rank_text_mode_does_not_render_image(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = types.SimpleNamespace(api_key="key", min_valid_score=1, output_mode="text")
    plugin._api = types.SimpleNamespace(
        fetch_player_stats_auto=lambda identifier, platform, use_uid: _async_return(
            (_sample_player(main_module), "PC")
        )
    )

    render_calls = []
    monkeypatch.setattr(plugin, "_render_player_rank_image", lambda player: render_calls.append(player))
    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_parse_player_platform", lambda event, player, platform: (player, platform))
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        pass

    async def collect():
        return [item async for item in plugin.apexrank(_Event(), "yumola", "pc")]

    result = asyncio.run(collect())

    assert render_calls == []
    assert result[0][0] == "plain"
    assert "yumola" in result[0][1]


def test_player_rank_image_mode_uses_rendered_image(monkeypatch, tmp_path):
    main_module = _load_main_module()
    image_path = tmp_path / "rank.png"
    plugin = object.__new__(main_module.Main)
    plugin._config = types.SimpleNamespace(api_key="key", min_valid_score=1, output_mode="image")
    plugin._api = types.SimpleNamespace(
        fetch_player_stats_auto=lambda identifier, platform, use_uid: _async_return(
            (_sample_player(main_module), "PC")
        )
    )

    monkeypatch.setattr(plugin, "_render_player_rank_image", lambda player: image_path)
    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_parse_player_platform", lambda event, player, platform: (player, platform))
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        pass

    async def collect():
        return [item async for item in plugin.apexrank(_Event(), "yumola", "pc")]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]


def test_cn_query_uid_command_accepts_api_response_with_blank_player_name(monkeypatch):
    main_module = _load_main_module()
    api_client = object.__new__(main_module.ApexApiClient)
    api_client._api_key = "key"

    async def fake_request(_url, _params):
        return _blank_name_uid_payload()

    api_client._request_with_retry = fake_request

    plugin = object.__new__(main_module.Main)
    plugin._config = types.SimpleNamespace(api_key="key", min_valid_score=1, output_mode="text")
    plugin._api = api_client

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        pass

    async def collect():
        return [
            item
            async for item in plugin.apexrank_query_cn(
                _Event(), "uid:1007669673322", "pc"
            )
        ]

    result = asyncio.run(collect())

    assert len(result) == 1
    assert result[0][0] == "plain"
    assert "未找到该玩家" not in result[0][1]
    assert "1007669673322" in result[0][1]
    assert "5933" in result[0][1]
    assert "黄金 4" in result[0][1]


def test_apexrank_uses_configured_alias_target_for_query(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )

    calls = []

    async def fake_fetch(identifier, platform, use_uid):
        calls.append((identifier, platform, use_uid))
        return _sample_player(main_module), "PC"

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexrank 测试"

    async def collect():
        return [item async for item in plugin.apexrank(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert calls == [("1007669673322", "", True)]


def test_apexrank_blocks_blacklisted_raw_alias_before_fetch(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="BlockedAlias=uid:1007669673322",
        blacklist="BlockedAlias",
    )
    plugin._runtime_blacklist = set()
    plugin._runtime_player_aliases = {}

    async def fake_fetch(identifier, platform, use_uid):
        raise AssertionError("blacklisted raw alias should not reach the API")

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexrank BlockedAlias"

    async def collect():
        return [item async for item in plugin.apexrank(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "禁止查询" in result[0][1]


def test_apexrank_alias_metadata_is_attached_for_image_render(monkeypatch, tmp_path):
    main_module = _load_main_module()
    image_path = tmp_path / "rank.png"
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="image",
        player_aliases="TestAlias=uid:1007669673322",
    )
    plugin._runtime_player_aliases = {}

    rendered_aliases = []

    async def fake_fetch(identifier, platform, use_uid):
        assert (identifier, platform, use_uid) == ("1007669673322", "", True)
        return _sample_player(main_module), "PC"

    def fake_render(player):
        rendered_aliases.append(
            (
                getattr(player, "display_alias", ""),
                getattr(player, "alias_target", ""),
            )
        )
        return image_path

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_render_player_rank_image", fake_render)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexrank TestAlias"

    async def collect():
        return [item async for item in plugin.apexrank(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert rendered_aliases == [("TestAlias", "uid:1007669673322")]


def test_apexrank_alias_metadata_is_shown_in_text_mode(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="text",
        player_aliases="TestAlias=uid:1007669673322",
    )
    plugin._runtime_player_aliases = {}

    async def fake_fetch(identifier, platform, use_uid):
        assert (identifier, platform, use_uid) == ("1007669673322", "", True)
        return _sample_player(main_module), "PC"

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexrank TestAlias"

    async def collect():
        return [item async for item in plugin.apexrank(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "🔖 查询别名: TestAlias" in result[0][1]
    assert "👤 玩家: yumola" in result[0][1]


def test_apexrank_without_name_uses_user_binding(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module)
    plugin._runtime_player_aliases = {}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}

    calls = []

    async def fake_fetch(identifier, platform, use_uid):
        calls.append((identifier, platform, use_uid))
        return _sample_player(main_module), "PC"

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex查询"
        user_id = "10001"

    async def collect():
        return [item async for item in plugin.apexrank_query_cn(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert calls == [("1007669673322", "", True)]
    assert "🔖 查询别名: 我的绑定" in result[0][1]


@pytest.mark.parametrize(
    ("message", "method_name"),
    [
        ("/apex查询 测试", "apexrank_query_cn"),
        ("/视奸 测试", "apexrank_query_cn_alt"),
    ],
)
def test_cn_query_commands_use_configured_alias_target(monkeypatch, message, method_name):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )

    calls = []

    async def fake_fetch(identifier, platform, use_uid):
        calls.append((identifier, platform, use_uid))
        return _sample_player(main_module), "PC"

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = message

    async def collect():
        method = getattr(plugin, method_name)
        return [item async for item in method(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert calls == [("1007669673322", "", True)]


def test_apexrankwatch_uses_configured_alias_target_for_monitor(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )

    calls = []

    async def fake_fetch(identifier, platform, use_uid):
        calls.append((identifier, platform, use_uid))
        return _sample_player(main_module), "PC"

    class _Store:
        def __init__(self):
            self.saved_record = None

        def ensure_group(self, group_id, origin):
            return types.SimpleNamespace(players={})

        def set_player(self, group_id, player_key, record):
            self.saved_record = (group_id, player_key, record)

        def save(self):
            pass

    store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}
    plugin._store = store

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: _async_return(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexrankwatch 测试"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexrankwatch(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert calls == [("1007669673322", "", True)]
    assert store.saved_record[1] == "uid:1007669673322@PC"


def test_apexrankwatch_blocks_query_blocked_raw_alias_before_fetch(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="BlockedAlias=uid:1007669673322",
        query_blocklist="BlockedAlias",
    )
    plugin._runtime_blacklist = set()
    plugin._runtime_player_aliases = {}

    async def fake_fetch(identifier, platform, use_uid):
        raise AssertionError("query-blocked raw alias should not reach the API")

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexrankwatch BlockedAlias"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexrankwatch(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "禁止监控" in result[0][1]


def test_apexrankwatch_stores_alias_display_for_monitor(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="TestAlias=uid:1007669673322",
    )

    async def fake_fetch(identifier, platform, use_uid):
        assert (identifier, platform, use_uid) == ("1007669673322", "", True)
        return _sample_player(main_module), "PC"

    class _Store:
        def __init__(self):
            self.saved_record = None

        def ensure_group(self, group_id, origin):
            return types.SimpleNamespace(players={})

        def set_player(self, group_id, player_key, record):
            self.saved_record = record

        def save(self):
            pass

    store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}
    plugin._store = store

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: _async_return(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexrankwatch TestAlias"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexrankwatch(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert store.saved_record.display_alias == "TestAlias"
    assert "成功添加对 TestAlias（yumola）" in result[0][1]


def test_apexrankrecord_stores_record_mode_without_active_test_message(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )

    async def fake_fetch(identifier, platform, use_uid):
        assert (identifier, platform, use_uid) == ("1007669673322", "", True)
        return _sample_player(main_module), "PC"

    class _Store:
        def __init__(self):
            self.saved_record = None

        def ensure_group(self, group_id, origin):
            return types.SimpleNamespace(players={})

        def set_player(self, group_id, player_key, record):
            self.saved_record = record

        def save(self):
            pass

    active_messages = []
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}
    plugin._store = _Store()

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: active_messages.append((origin, message)) or _async_return(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/持续记录 测试"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexrankrecord(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert plugin._store.saved_record.watch_mode == "record"
    assert active_messages == []
    assert "仅记录" in result[0][1]


def test_apexrankwatch_stores_notify_mode(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module)

    async def fake_fetch(identifier, platform, use_uid):
        return _sample_player(main_module), "PC"

    class _Store:
        def __init__(self):
            self.saved_record = None

        def ensure_group(self, group_id, origin):
            return types.SimpleNamespace(players={})

        def set_player(self, group_id, player_key, record):
            self.saved_record = record

        def save(self):
            pass

    active_messages = []
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}
    plugin._store = _Store()

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: active_messages.append((origin, message)) or _async_return(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/持续视奸 yumola"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexrankwatch(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert plugin._store.saved_record.watch_mode == "notify"
    assert len(active_messages) == 1
    assert "通报+记录" in result[0][1]


def test_cn_rank_watch_list_alt_calls_list(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="text")
    record = _watch_record(main_module, watch_mode="record")

    class _Store:
        def get_group(self, group_id):
            return types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record})

        def save(self):
            pass

    plugin._store = _Store()
    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/持续视奸列表"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apexranklist_cn_alt(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "仅记录" in result[0][1]


@pytest.mark.parametrize(
    ("message", "method_name"),
    [
        ("/apex监控 测试", "apexrankwatch_cn"),
        ("/持续视奸 测试", "apexrankwatch_cn_alt"),
    ],
)
def test_cn_apexrankwatch_alias_uses_configured_alias_target(monkeypatch, message, method_name):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )

    calls = []

    async def fake_fetch(identifier, platform, use_uid):
        calls.append((identifier, platform, use_uid))
        return _sample_player(main_module), "PC"

    class _Store:
        def ensure_group(self, group_id, origin):
            return types.SimpleNamespace(players={})

        def set_player(self, group_id, player_key, record):
            pass

        def save(self):
            pass

    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)
    plugin._runtime_player_aliases = {}
    plugin._store = _Store()

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: _async_return(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = message
        unified_msg_origin = "origin-1"

    async def collect():
        method = getattr(plugin, method_name)
        return [item async for item in method(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert calls == [("1007669673322", "", True)]


@pytest.mark.parametrize(
    ("message", "method_name"),
    [
        ("/apexrankremove 测试", "apexrankremove"),
        ("/apex移除 测试", "apexrankremove_cn"),
        ("/取消持续视奸 测试", "apexrankremove_cn_alt"),
    ],
)
def test_remove_watch_commands_use_configured_alias_target(monkeypatch, message, method_name):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        player_aliases="测试=uid:1007669673322",
    )
    plugin._runtime_player_aliases = {}

    class _Store:
        def __init__(self):
            self.group = types.SimpleNamespace(
                origin="origin-1",
                players={"uid:1007669673322@PC": object()},
            )
            self.removed = []
            self.save_calls = 0

        def get_group(self, group_id):
            assert group_id == "group-1"
            return self.group

        def remove_player(self, group_id, player_key):
            self.removed.append((group_id, player_key))
            return self.group.players.pop(player_key, None) is not None

        def save(self):
            self.save_calls += 1

    store = _Store()
    plugin._store = store

    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = message
        unified_msg_origin = "origin-1"

    async def collect():
        method = getattr(plugin, method_name)
        return [item async for item in method(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert store.removed == [("group-1", "uid:1007669673322@PC")]
    assert store.save_calls == 1
    assert "已移除本群对 测试（uid:1007669673322） 的排名监控" in result[0][1]


def test_rank_watch_list_prefers_alias_display_name():
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module)

    record = main_module.PlayerRecord(
        player_name="yumola",
        platform="PC",
        lookup_id="1007669673322",
        use_uid=True,
        rank_score=18888,
        rank_name="Master",
        rank_div=0,
        last_checked=0,
    )
    record.display_alias = "TestAlias"

    lines = plugin._build_rank_watch_list_lines([record])

    assert "👤 玩家 1: TestAlias（yumola）" in lines


def test_poll_rank_change_image_receives_record_alias(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._poll_concurrency = 1
    plugin._poll_semaphore = asyncio.Semaphore(1)

    old_record = main_module.PlayerRecord(
        player_name="yumola",
        platform="PC",
        lookup_id="1007669673322",
        use_uid=True,
        rank_score=18000,
        rank_name="Master",
        rank_div=0,
        last_checked=0,
    )
    old_record.display_alias = "TestAlias"

    class _Store:
        def __init__(self):
            self.save_calls = 0

        def iter_groups(self):
            return [
                (
                    "group-1",
                    types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": old_record}),
                )
            ]

        def save(self):
            self.save_calls += 1

    async def fake_fetch(identifier, platform, use_uid):
        player = _sample_player(main_module)
        player.rank_score = 18888
        return player, "PC"

    render_calls = []
    image_path = tmp_path / "rank-change.png"

    def fake_render(**kwargs):
        player = kwargs["player_data"]
        render_calls.append(getattr(player, "display_alias", ""))
        return image_path

    plugin._store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_render_rank_change_image", fake_render)
    monkeypatch.setattr(plugin, "_send_active_image", lambda origin, path: _async_return(True))

    asyncio.run(plugin._poll_once())

    assert render_calls == ["TestAlias"]
    assert plugin._store.save_calls == 1


def test_poll_record_mode_appends_history_without_notification(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._poll_concurrency = 1
    plugin._poll_semaphore = asyncio.Semaphore(1)
    record = _watch_record(main_module, watch_mode="record", score=18000)

    class _Store:
        def __init__(self):
            self.save_calls = 0

        def iter_groups(self):
            return [
                (
                    "group-1",
                    types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record}),
                )
            ]

        def save(self):
            self.save_calls += 1

    async def fake_fetch(identifier, platform, use_uid):
        player = _sample_player(main_module)
        player.rank_score = 18120
        return player, "PC"

    active_images = []
    active_messages = []
    plugin._store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_render_rank_change_image", lambda **kwargs: tmp_path / "rank-change.png")
    monkeypatch.setattr(plugin, "_send_active_image", lambda origin, path: active_images.append((origin, path)) or _async_return(True))
    monkeypatch.setattr(plugin, "_send_active_message", lambda origin, message: active_messages.append((origin, message)) or _async_return(True))

    asyncio.run(plugin._poll_once())

    assert len(record.history) == 1
    assert record.history[0].score_delta == 120
    assert active_images == []
    assert active_messages == []
    assert plugin._store.save_calls == 1


def test_poll_notify_mode_appends_history_and_sends_image(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._poll_concurrency = 1
    plugin._poll_semaphore = asyncio.Semaphore(1)
    record = _watch_record(main_module, watch_mode="notify", score=18000)

    class _Store:
        def __init__(self):
            self.save_calls = 0

        def iter_groups(self):
            return [
                (
                    "group-1",
                    types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record}),
                )
            ]

        def save(self):
            self.save_calls += 1

    async def fake_fetch(identifier, platform, use_uid):
        player = _sample_player(main_module)
        player.rank_score = 18120
        return player, "PC"

    active_images = []
    plugin._store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)
    monkeypatch.setattr(plugin, "_render_rank_change_image", lambda **kwargs: tmp_path / "rank-change.png")
    monkeypatch.setattr(plugin, "_send_active_image", lambda origin, path: active_images.append((origin, path)) or _async_return(True))

    asyncio.run(plugin._poll_once())

    assert len(record.history) == 1
    assert record.history[0].from_score == 18000
    assert record.history[0].to_score == 18120
    assert len(active_images) == 1
    assert plugin._store.save_calls == 1


def test_poll_no_change_does_not_append_history(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._poll_concurrency = 1
    plugin._poll_semaphore = asyncio.Semaphore(1)
    record = _watch_record(main_module, watch_mode="notify", score=18888)

    class _Store:
        def __init__(self):
            self.save_calls = 0

        def iter_groups(self):
            return [
                (
                    "group-1",
                    types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record}),
                )
            ]

        def save(self):
            self.save_calls += 1

    async def fake_fetch(identifier, platform, use_uid):
        player = _sample_player(main_module)
        player.rank_score = 18888
        return player, "PC"

    plugin._store = _Store()
    plugin._api = types.SimpleNamespace(fetch_player_stats_auto=fake_fetch)

    monkeypatch.setattr(plugin, "_is_blacklisted", lambda player: False)
    monkeypatch.setattr(plugin, "_is_query_blocked", lambda player: False)

    asyncio.run(plugin._poll_once())

    assert record.history == []
    assert plugin._store.save_calls == 0


def test_score_history_is_trimmed_to_50():
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    record = _watch_record(main_module, watch_mode="record", score=18000)

    for index in range(55):
        player = _sample_player(main_module)
        player.rank_score = 18001 + index
        plugin._append_score_history(
            record,
            player_data=player,
            old_score=18000 + index,
            is_season_reset=False,
            captured_at=1_780_000_000_000 + index,
        )

    assert len(record.history) == 50
    assert record.history[0].to_score == 18006
    assert record.history[-1].to_score == 18055


def test_apexalias_add_command_allows_everyone_when_switch_is_off(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_admin_only=False)
    plugin._runtime_player_aliases = {}
    save_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: save_calls.append(dict(plugin._runtime_player_aliases)))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias add 测试 uid:1234"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_player_aliases == {"测试": "uid:1234"}
    assert save_calls == [{"测试": "uid:1234"}]
    assert "已添加别名" in result[0][1]


def test_apexalias_preserves_english_alias_display_name(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_admin_only=False)
    plugin._runtime_player_aliases = {}
    plugin._runtime_player_alias_display_names = {}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: None)
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias add MyMain uid:1234"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_player_aliases == {"mymain": "uid:1234"}
    assert plugin._runtime_player_alias_display_names == {"mymain": "MyMain"}
    assert "已添加别名：MyMain => uid:1234" in result[0][1]


def test_apexalias_add_command_requires_admin_when_switch_is_on(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_admin_only=True)
    plugin._runtime_player_aliases = {}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_guard_admin", lambda event: "ADMIN ONLY")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias add 测试 uid:1234"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_player_aliases == {}
    assert result == [("plain", "TIME\nADMIN ONLY")]


def test_apexbind_stores_user_binding_with_uid_slash_prefix(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module)
    plugin._runtime_user_bindings = {}
    save_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: save_calls.append(dict(plugin._runtime_user_bindings)))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex绑定 /uid:1007669673322"
        user_id = "10001"

    async def collect():
        return [item async for item in plugin.apexbind(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_user_bindings == {"10001": "uid:1007669673322"}
    assert save_calls == [{"10001": "uid:1007669673322"}]
    assert "已绑定" in result[0][1]
    assert "uid:1007669673322" in result[0][1]


def test_apexbind_respects_alias_switch(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_enabled=False)
    plugin._runtime_user_bindings = {}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: pytest.fail("should not save"))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex绑定 uid:1007669673322"
        user_id = "10001"

    async def collect():
        return [item async for item in plugin.apexbind(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_user_bindings == {}
    assert "别名功能已关闭" in result[0][1]


def test_apexalias_list_image_mode_uses_rendered_alias_list(monkeypatch, tmp_path):
    main_module = _load_main_module()
    image_path = tmp_path / "aliases.png"
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="image",
        player_aliases="测试=uid:1234",
    )
    plugin._runtime_player_aliases = {"小明": "EaName pc"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}
    render_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_guard_admin", lambda event: None)
    monkeypatch.setattr(plugin, "_render_alias_list_image", lambda aliases: render_calls.append(aliases) or image_path)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexalias list"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls == [
        [
            ("测试", "uid:1234", "配置（生效）"),
            ("小明", "EaName pc", "动态（生效）"),
        ]
    ]


def test_apexalias_help_image_mode_uses_rendered_alias_list(monkeypatch, tmp_path):
    main_module = _load_main_module()
    image_path = tmp_path / "aliases.png"
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="image",
        player_aliases="测试=uid:1234",
        alias_admin_only=False,
    )
    plugin._runtime_player_aliases = {"小明": "EaName pc"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}
    render_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_render_alias_list_image", lambda aliases: render_calls.append(aliases) or image_path)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexalias"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls == [
        [
            ("测试", "uid:1234", "配置（生效）"),
            ("小明", "EaName pc", "动态（生效）"),
        ]
    ]


def test_apexalias_list_text_mode_does_not_include_user_bindings(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="text",
        player_aliases="测试=uid:1234",
        alias_admin_only=False,
    )
    plugin._runtime_player_aliases = {"小明": "EaName pc"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias list"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "Apex 查询别名列表" in result[0][1]
    assert "测试=uid:1234" in result[0][1]
    assert "小明=EaName pc" in result[0][1]
    assert "个人绑定" not in result[0][1]
    assert "QQ 10001" not in result[0][1]


def test_apexalias_list_marks_runtime_override_and_shows_effective_text(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="text",
        player_aliases="MyMain=uid:1111",
        alias_admin_only=False,
    )
    plugin._runtime_player_aliases = {"mymain": "uid:2222"}
    plugin._runtime_player_alias_display_names = {"mymain": "MyMain"}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias list"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "有效别名：MyMain=uid:2222" in result[0][1]
    assert "配置别名：MyMain=uid:1111（已被动态覆盖）" in result[0][1]
    assert "动态别名：MyMain=uid:2222" in result[0][1]


def test_apexbind_list_image_mode_uses_rendered_binding_list(monkeypatch, tmp_path):
    main_module = _load_main_module()
    image_path = tmp_path / "bindings.png"
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._runtime_user_bindings = {
        "10001": "uid:1007669673322",
        "10002": "PlayerName pc",
    }
    render_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_get_user_id", lambda event: "10001")
    monkeypatch.setattr(
        plugin,
        "_render_binding_list_image",
        lambda bindings: render_calls.append(bindings) or image_path,
        raising=False,
    )
    monkeypatch.setattr(plugin, "_save_settings", lambda: None)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apex绑定 list"

    async def collect():
        return [item async for item in plugin.apexbind(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls == [
        [
            ("QQ 10001", "uid:1007669673322", "个人绑定"),
            ("QQ 10002", "PlayerName pc", "个人绑定"),
        ]
    ]
    assert plugin._runtime_user_bindings == {
        "10001": "uid:1007669673322",
        "10002": "PlayerName pc",
    }


def test_apexbind_list_text_mode_shows_only_user_bindings(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(
        main_module,
        output_mode="text",
        player_aliases="测试=uid:1234",
    )
    plugin._runtime_player_aliases = {"小明": "EaName pc"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_get_user_id", lambda event: "10001")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_save_settings", lambda: None)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex绑定 list"

    async def collect():
        return [item async for item in plugin.apexbind(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "Apex 查询绑定列表" in result[0][1]
    assert "QQ 10001=uid:1007669673322" in result[0][1]
    assert "有效别名" not in result[0][1]
    assert "测试=uid:1234" not in result[0][1]


def test_apexbindinglist_command_shows_binding_list(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="text")
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex绑定列表"

    async def collect():
        return [item async for item in plugin.apexbindinglist(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "Apex 查询绑定列表" in result[0][1]
    assert "QQ 10001=uid:1007669673322" in result[0][1]


def test_apexunbind_command_removes_current_user_binding(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module)
    plugin._runtime_user_bindings = {
        "10001": "uid:1007669673322",
        "10002": "PlayerName pc",
    }
    save_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_get_user_id", lambda event: "10001")
    monkeypatch.setattr(plugin, "_save_settings", lambda: save_calls.append(dict(plugin._runtime_user_bindings)))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex解绑"

    if not hasattr(plugin, "apexunbind"):
        pytest.fail("缺少独立的 /apex解绑 取消绑定命令")

    async def collect():
        return [item async for item in plugin.apexunbind(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_user_bindings == {"10002": "PlayerName pc"}
    assert save_calls == [{"10002": "PlayerName pc"}]
    assert "已解除你的 Apex 查询绑定" in result[0][1]


def test_apexunalias_command_removes_alias_without_removing_binding(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_admin_only=False)
    plugin._runtime_player_aliases = {"测试": "uid:1234"}
    plugin._runtime_player_alias_display_names = {"测试": "测试"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}
    save_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: save_calls.append(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apex取消别名 测试"

    async def collect():
        return [item async for item in plugin.apexunalias(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_player_aliases == {}
    assert plugin._runtime_user_bindings == {"10001": "uid:1007669673322"}
    assert save_calls == [True]
    assert "已移除动态别名" in result[0][1]


def test_apexalias_remove_does_not_remove_user_binding(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, alias_admin_only=False)
    plugin._runtime_player_aliases = {"测试": "uid:1234"}
    plugin._runtime_player_alias_display_names = {"测试": "测试"}
    plugin._runtime_user_bindings = {"10001": "uid:1007669673322"}
    save_calls = []

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_save_settings", lambda: save_calls.append(True))
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/apexalias remove 测试"

    async def collect():
        return [item async for item in plugin.apexalias(_Event())]

    result = asyncio.run(collect())

    assert plugin._runtime_player_aliases == {}
    assert plugin._runtime_user_bindings == {"10001": "uid:1007669673322"}
    assert save_calls == [True]
    assert "已移除动态别名" in result[0][1]


def test_alias_list_image_renderer_creates_png(tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image", alias_admin_only=True)
    plugin._data_dir = tmp_path

    path = plugin._render_alias_list_image(
        [
            ("测试", "uid:1234", "配置"),
            ("小明", "EaName pc", "动态"),
        ]
    )

    assert path.exists()
    assert path.suffix == ".png"
    assert path.parent == tmp_path / "alias_list_cards"


def test_score_change_command_renders_all_group_players_by_default(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    record = _watch_record(main_module, watch_mode="notify")
    record.history = [_history_record(main_module)]
    image_path = tmp_path / "score-change.png"
    render_calls = []

    class _Store:
        def get_group(self, group_id):
            return types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record})

    plugin._store = _Store()
    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_render_score_change_chart", lambda players, limit: render_calls.append((players, limit)) or image_path)
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/分数变化"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apex_score_changes(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls[0][1] == 20
    assert render_calls[0][0] == [record]


def test_score_change_command_filters_player_and_clamps_limit(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    selected = _watch_record(
        main_module,
        watch_mode="notify",
        player_name="yumola",
        lookup_id="yumola",
        use_uid=False,
    )
    selected.history = [_history_record(main_module, player_name="yumola")]
    other = _watch_record(
        main_module,
        watch_mode="record",
        player_name="other",
        lookup_id="other",
        use_uid=False,
    )
    other.history = [_history_record(main_module, player_name="other")]
    image_path = tmp_path / "score-change.png"
    render_calls = []

    class _Store:
        def get_group(self, group_id):
            return types.SimpleNamespace(
                origin="origin-1",
                players={"name:yumola@PC": selected, "name:other@PC": other},
            )

    plugin._store = _Store()
    plugin._runtime_player_aliases = {}
    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_render_score_change_chart", lambda players, limit: render_calls.append((players, limit)) or image_path)
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/分数变化 yumola 80"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apex_score_changes(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls[0][1] == 50
    assert render_calls[0][0] == [selected]


def test_score_change_text_mode_returns_summary(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="text")
    record = _watch_record(main_module, watch_mode="record")
    record.history = [_history_record(main_module)]

    class _Store:
        def get_group(self, group_id):
            return types.SimpleNamespace(origin="origin-1", players={"uid:1007669673322@PC": record})

    plugin._store = _Store()
    monkeypatch.setattr(plugin, "_guard_access", lambda event, require_group=False: "")
    monkeypatch.setattr(plugin, "_get_group_id", lambda event: "group-1")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))

    class _Event:
        message_str = "/分数变化"
        unified_msg_origin = "origin-1"

    async def collect():
        return [item async for item in plugin.apex_score_changes(_Event())]

    result = asyncio.run(collect())

    assert result[0][0] == "plain"
    assert "最近 1 次分数变化" in result[0][1]
    assert "yumola" in result[0][1]
    assert "仅记录" in result[0][1]


def test_score_change_chart_renderer_creates_png(tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._data_dir = tmp_path
    record = _watch_record(main_module, watch_mode="notify")
    record.history = [
        _history_record(main_module, captured_at=1_780_000_000_000, from_score=18000, to_score=18120),
        _history_record(main_module, captured_at=1_780_000_060_000, from_score=18120, to_score=18060),
    ]

    path = plugin._render_score_change_chart([record], 50)

    assert path.exists()
    assert path.suffix == ".png"
    assert path.parent == tmp_path / "score_change_charts"
    assert path.stat().st_size > 1000


def test_score_change_event_panel_does_not_render_mode_column(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._data_dir = tmp_path
    record = _watch_record(main_module, watch_mode="record")
    item = _history_record(main_module)
    canvas = main_module.Image.new("RGBA", (1800, 420), (8, 18, 30, 255))
    draw = main_module.ImageDraw.Draw(canvas)

    def fail_mode_label(_player):
        raise AssertionError("mode label should not be rendered in score-change event rows")

    monkeypatch.setattr(plugin, "_watch_mode_label", fail_mode_label)

    plugin._draw_score_change_event_panel(draw, (44, 44, 1756, 380), [(record, item)])


def test_score_change_chart_uses_rank_icon_for_rank_change(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._data_dir = tmp_path
    record = _watch_record(main_module, watch_mode="notify", score=12120)
    item = _history_record(main_module, from_score=11980, to_score=12120)
    item.from_rank_name = "Platinum"
    item.from_rank_div = 1
    item.to_rank_name = "Diamond"
    item.to_rank_div = 4
    canvas = main_module.Image.new("RGBA", (1800, 760), (8, 18, 30, 255))
    draw = main_module.ImageDraw.Draw(canvas)
    drawn_assets = []

    def record_image_asset(_draw, path, box, clip_octagon=False):
        drawn_assets.append((path, box, clip_octagon))

    monkeypatch.setattr(plugin, "_draw_image_asset", record_image_asset)

    plugin._draw_score_change_chart_panel(
        canvas,
        draw,
        (44, 44, 1756, 700),
        [record],
        [(record, item)],
    )

    assert drawn_assets
    assert drawn_assets[0][0].name == "diamond_4.png"


def test_predator_image_mode_keeps_four_platform_overview(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    image_path = tmp_path / "predator.png"

    predator_info = main_module.PredatorInfo(
        platforms={
            "PC": main_module.PredatorPlatformStats("PC", 20000, 750, "", 1000, 1200),
            "PS4": main_module.PredatorPlatformStats("PS4", 18000, 750, "", 900, 800),
        }
    )

    async def fake_fetch():
        return predator_info

    render_calls = []

    def fake_render(info, selected_platform=""):
        render_calls.append(selected_platform)
        return image_path

    plugin._api = types.SimpleNamespace(fetch_predator_info=fake_fetch)

    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_render_predator_info_image", fake_render)
    monkeypatch.setattr(plugin, "_plain", lambda event, text: ("plain", text))
    monkeypatch.setattr(plugin, "_image", lambda event, path: ("image", path))

    class _Event:
        message_str = "/apexpredator pc"

    async def collect():
        return [item async for item in plugin.apexpredator(_Event())]

    result = asyncio.run(collect())

    assert result == [("image", image_path)]
    assert render_calls == [""]


def test_predator_image_renderer_requires_template(monkeypatch, tmp_path):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._config = _plugin_config(main_module, output_mode="image")
    plugin._data_dir = tmp_path
    monkeypatch.setattr(
        main_module.Main,
        "_PREDATOR_TEMPLATE_PATH",
        tmp_path / "missing_predator_template.png",
    )

    predator_info = main_module.PredatorInfo(
        platforms={
            "PC": main_module.PredatorPlatformStats("PC", 20000, 750, "", 1000, 1200),
            "PS4": main_module.PredatorPlatformStats("PS4", 18000, 750, "", 900, 800),
            "X1": main_module.PredatorPlatformStats("X1", 17500, 750, "", 800, 700),
            "SWITCH": main_module.PredatorPlatformStats("SWITCH", 15000, 750, "", 700, 300),
        }
    )

    with pytest.raises(FileNotFoundError):
        plugin._render_predator_info_image(predator_info, "PC")


def test_ranked_daily_map_schedule_uses_web_fallback_when_pool_learning_blocks_prediction():
    main_module = _load_main_module()
    current = main_module.MapRotationEntry(
        map_name="Olympus",
        map_name_zh="奥林匹斯",
        start_timestamp=1778268600,
        end_timestamp=1778284800,
        readable_start="",
        readable_end="",
        duration_secs=16200,
        duration_minutes=270,
        asset="",
        code="",
    )
    next_entry = main_module.MapRotationEntry(
        map_name="Broken Moon",
        map_name_zh="残月",
        start_timestamp=1778284800,
        end_timestamp=1778301000,
        readable_start="",
        readable_end="",
        duration_secs=16200,
        duration_minutes=270,
        asset="",
        code="",
    )
    html = """
    <div><h3>Olympus</h3><p>From <span data-tz="1778268600">21:30</span> to <span data-tz="1778284800">02:00</span></p></div>
    <div><h3>Broken Moon</h3><p>From <span data-tz="1778284800">02:00</span> to <span data-tz="1778301000">06:30</span></p></div>
    <div><h3>Kings Canyon</h3><p>From <span data-tz="1778301000">06:30</span> to <span data-tz="1778317200">11:00</span></p></div>
    <div><h3>Olympus</h3><p>From <span data-tz="1778317200">11:00</span> to <span data-tz="1778333400">15:30</span></p></div>
    """
    client = object.__new__(main_module.ApexApiClient)
    client._daily_map_lock = asyncio.Lock()
    client._daily_map_cache = {}
    client._daily_map_cache_ttl_seconds = 600
    client._logger = types.SimpleNamespace(debug=lambda *_args, **_kwargs: None)
    rotation_info = main_module.MapRotationInfo()
    rotation_info.ranked.current = current
    rotation_info.ranked.next = next_entry

    async def fetch_map_rotation_info():
        return rotation_info

    async def request_text(_url):
        return html

    client.fetch_map_rotation_info = fetch_map_rotation_info
    client._request_text_with_retry = request_text
    state = main_module.DailyMapPoolState(
        season_key="S29:Season 29",
        status="confirmed",
        cycle=["Storm Point", "World's Edge", "E-District"],
        reason="API 已确认排位地图池闭环",
    )

    schedule = asyncio.run(
        main_module.ApexApiClient.fetch_daily_map_schedule(
            client,
            "ranked",
            pool_state=state,
            season_info=None,
        )
    )

    assert schedule.pool_state is not None
    assert schedule.pool_state.status == "learning"
    assert "网页地图池推断" in schedule.source_note
    assert len(schedule.entries) > 2
    assert [entry.map_name for entry in schedule.entries[:4]] == [
        "Olympus",
        "Broken Moon",
        "Kings Canyon",
        "Olympus",
    ]


async def _async_return(value):
    return value
