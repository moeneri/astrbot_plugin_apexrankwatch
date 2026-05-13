from __future__ import annotations

import hashlib
import importlib
import asyncio
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


def test_font_auto_download_is_enabled_by_default():
    main_module = _load_main_module()

    config = main_module.PluginConfig.from_raw({})

    assert config.font_auto_download is True
    assert config.font_download_url == ""


def test_cached_cjk_font_requires_matching_sha256(tmp_path, monkeypatch):
    main_module = _load_main_module()
    payload = b"fake-font"
    digest = hashlib.sha256(payload).hexdigest()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path

    monkeypatch.setattr(main_module.Main, "_FONT_FILE_NAME", "font.otf")
    monkeypatch.setattr(main_module.Main, "_FONT_SHA256", digest)
    font_path = plugin._font_cache_path()
    font_path.parent.mkdir(parents=True)
    font_path.write_bytes(payload)

    assert plugin._resolve_cached_cjk_font_path() == font_path

    font_path.write_bytes(b"bad-font")

    assert plugin._resolve_cached_cjk_font_path() is None
    assert not font_path.exists()


def test_cjk_font_download_is_cached_and_verified(tmp_path, monkeypatch):
    main_module = _load_main_module()
    payload = b"downloaded-font"
    digest = hashlib.sha256(payload).hexdigest()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path
    plugin._config = types.SimpleNamespace(
        font_auto_download=True,
        font_download_url="https://example.invalid/font.otf",
        debug_logging=False,
    )

    class _Response:
        headers = {"content-length": str(len(payload))}
        content = payload

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, **kwargs):
        calls.append((url, kwargs))
        return _Response()

    monkeypatch.setattr(main_module.Main, "_FONT_FILE_NAME", "font.otf")
    monkeypatch.setattr(main_module.Main, "_FONT_SHA256", digest)
    monkeypatch.setattr(main_module.httpx, "get", fake_get)

    first = plugin._download_cjk_font_if_needed()
    second = plugin._download_cjk_font_if_needed()

    assert first == tmp_path / "fonts" / "font.otf"
    assert second == first
    assert first.read_bytes() == payload
    assert calls == [
        (
            "https://example.invalid/font.otf",
            {"timeout": main_module.Main._FONT_DOWNLOAD_TIMEOUT_SECONDS, "follow_redirects": True},
        )
    ]


def test_cjk_font_status_reports_missing_when_system_and_cache_absent(tmp_path, monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path

    monkeypatch.setattr(plugin, "_candidate_system_cjk_font_paths", lambda bold=False: [])

    status = plugin._get_cjk_font_status()

    assert status.available is False
    assert status.source == "missing"
    assert status.path is None


def test_font_notice_is_consumed_once_when_font_is_missing(tmp_path, monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path
    plugin._font_notice_sent = False
    plugin._config = types.SimpleNamespace(font_auto_download=True)

    monkeypatch.setattr(
        plugin,
        "_get_cjk_font_status",
        lambda: main_module.FontStatus(False, "missing", None),
    )

    assert plugin._consume_font_download_notice_needed() is True
    assert plugin._consume_font_download_notice_needed() is False


def test_manual_font_download_can_retry_after_auto_attempt(tmp_path, monkeypatch):
    main_module = _load_main_module()
    payload = b"manual-download-font"
    digest = hashlib.sha256(payload).hexdigest()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path
    plugin._font_download_attempted = True
    plugin._config = types.SimpleNamespace(
        font_auto_download=True,
        font_download_url="https://example.invalid/font.otf",
        debug_logging=False,
    )

    class _Response:
        headers = {"content-length": str(len(payload))}
        content = payload

        def raise_for_status(self):
            return None

    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return _Response()

    monkeypatch.setattr(main_module.Main, "_FONT_FILE_NAME", "font.otf")
    monkeypatch.setattr(main_module.Main, "_FONT_SHA256", digest)
    monkeypatch.setattr(main_module.httpx, "get", fake_get)

    assert plugin._download_cjk_font_if_needed() is None
    assert plugin._download_cjk_font_if_needed(force=True) == tmp_path / "fonts" / "font.otf"
    assert calls == ["https://example.invalid/font.otf"]


def test_apex_download_command_returns_font_status_message(tmp_path, monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)
    plugin._data_dir = tmp_path
    plugin._config = types.SimpleNamespace(font_auto_download=True)

    downloaded_path = tmp_path / "fonts" / "font.otf"
    monkeypatch.setattr(plugin, "_download_cjk_font_if_needed", lambda force=False: downloaded_path)
    monkeypatch.setattr(
        plugin,
        "_get_cjk_font_status",
        lambda: main_module.FontStatus(True, "cache", downloaded_path),
    )
    monkeypatch.setattr(plugin, "_guard_access", lambda event: "")
    monkeypatch.setattr(plugin, "_time_line", lambda: "TIME")
    monkeypatch.setattr(plugin, "_plain", lambda event, text: text)

    class _Event:
        pass

    async def collect():
        return [item async for item in plugin.apex_download(_Event())]

    result = asyncio.run(collect())

    assert len(result) == 1
    assert "中文字体已可用" in result[0]
    assert str(downloaded_path) in result[0]


def test_first_plugin_command_can_emit_font_download_notice(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)

    monkeypatch.setattr(plugin, "_consume_font_download_notice_needed", lambda: True)
    monkeypatch.setattr(plugin, "_build_font_download_notice_lines", lambda: ["字体提示"])
    monkeypatch.setattr(plugin, "_plain", lambda event, text: text)

    class _Event:
        message_str = "/apexrank moeneri"

    async def collect():
        return [item async for item in plugin._season_keyword_listener(_Event())]

    result = asyncio.run(collect())

    assert result == ["字体提示"]


def test_apex_download_command_does_not_emit_extra_font_notice(monkeypatch):
    main_module = _load_main_module()
    plugin = object.__new__(main_module.Main)

    monkeypatch.setattr(plugin, "_consume_font_download_notice_needed", lambda: True)
    monkeypatch.setattr(plugin, "_build_font_download_notice_lines", lambda: ["字体提示"])
    monkeypatch.setattr(plugin, "_plain", lambda event, text: text)

    class _Event:
        message_str = "/apex_download"

    async def collect():
        return [item async for item in plugin._season_keyword_listener(_Event())]

    result = asyncio.run(collect())

    assert result == []
