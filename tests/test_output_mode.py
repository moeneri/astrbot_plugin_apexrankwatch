from __future__ import annotations

import asyncio
import importlib
import sys
import types
from pathlib import Path


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


def test_output_mode_defaults_to_image_and_accepts_text():
    main_module = _load_main_module()

    default_config = main_module.PluginConfig.from_raw({})
    text_config = main_module.PluginConfig.from_raw({"output_mode": "text"})
    invalid_config = main_module.PluginConfig.from_raw({"output_mode": "bad"})

    assert default_config.output_mode == "image"
    assert text_config.output_mode == "text"
    assert invalid_config.output_mode == "image"


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


async def _async_return(value):
    return value
