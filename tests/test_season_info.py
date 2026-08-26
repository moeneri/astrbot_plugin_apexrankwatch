"""赛季分段（split）时间的单元测试。

数据源只有一个：ALS `/bridge` 的 `global.rank.rankedSeasonMeta`。
本文件不再包含任何第三方网页抓取相关的测试 —— 那些代码已整体移除。
"""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import apex_service
from utils import SHANGHAI_TZ


# S30 上半赛季的真实 ALS 返回值（2026-08 实网抓取）。
# start = 2026-08-04 17:00Z = 北京 08-05 01:00 周三
# end   = 2026-09-15 17:00Z = 北京 09-16 01:00 周三
S30_SPLIT1_START = 1785862800
S30_SPLIT1_END = 1789491600


class _SilentLogger:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


def _bridge_payload(
    ranked_season: str = "br_ranked_s30_s1",
    start: int | None = S30_SPLIT1_START,
    end: int | None = S30_SPLIT1_END,
    include_meta: bool = True,
):
    rank: dict = {"rankScore": 5771, "rankName": "Gold", "rankedSeason": ranked_season}
    if include_meta:
        rank["rankedSeasonMeta"] = {"start": start, "end": end}
    return {
        "global": {
            "name": "MoeNerii",
            "rank": rank,
            # 竞技场是废弃模式，且不带 Meta：解析必须忽略它。
            "arena": {"rankedSeason": "arenas29_split_2"},
        }
    }


def _client(payload=None, calls: list | None = None):
    client = object.__new__(apex_service.ApexApiClient)
    client._logger = _SilentLogger()
    client._api_key = "test-key"
    client._season_cache_ttl_seconds = 1800
    client._season_cache = {}
    client._season_lock = asyncio.Lock()
    client._season_probe_player = "moeneri"
    client._season_probe_cooldown_seconds = 0  # 测试默认关闭冷却
    client._last_season_probe = None

    async def request_player_data(_url, params, _ident):
        if calls is not None:
            calls.append(params.get("player"))
        if isinstance(payload, Exception):
            raise payload
        return payload

    client._request_player_data = request_player_data
    return client


def _beijing(iso: str) -> datetime:
    return apex_service._parse_iso_datetime(iso).astimezone(SHANGHAI_TZ)


# --------------------------------------------------------------------------
# _parse_ranked_split_window
# --------------------------------------------------------------------------


def test_parses_ranked_season_meta_into_window():
    window = apex_service._parse_ranked_split_window(_bridge_payload())
    assert window is not None
    assert window.season_number == 30
    assert window.split_index == 1
    assert window.ranked_season_id == "br_ranked_s30_s1"
    assert window.start == datetime(2026, 8, 4, 17, 0, tzinfo=timezone.utc)
    assert window.end == datetime(2026, 9, 15, 17, 0, tzinfo=timezone.utc)


def test_parses_split_two_and_legacy_id_forms():
    for ranked_season, season, split in (
        ("br_ranked_s30_s2", 30, 2),
        ("season30_split1", 30, 1),
        ("S31_S2", 31, 2),
    ):
        window = apex_service._parse_ranked_split_window(
            _bridge_payload(ranked_season=ranked_season)
        )
        assert window is not None, ranked_season
        assert (window.season_number, window.split_index) == (season, split)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"global": {}},
        {"global": {"rank": "not-a-dict"}},
        # 只有竞技场数据：没有 br 排位段，必须拒绝
        {"global": {"arena": {"rankedSeason": "arenas29_split_2"}}},
    ],
)
def test_rejects_payloads_without_ranked_section(payload):
    assert apex_service._parse_ranked_split_window(payload) is None


def test_rejects_missing_or_unusable_meta():
    # 缺少 rankedSeasonMeta
    assert apex_service._parse_ranked_split_window(
        _bridge_payload(include_meta=False)
    ) is None
    # 认不出的赛季标识
    assert apex_service._parse_ranked_split_window(
        _bridge_payload(ranked_season="br_ranked_unknown")
    ) is None
    # 时间戳缺失 / 反序 / 相等
    for start, end in ((None, S30_SPLIT1_END), (S30_SPLIT1_START, None),
                       (S30_SPLIT1_END, S30_SPLIT1_START),
                       (S30_SPLIT1_START, S30_SPLIT1_START), (0, 0)):
        assert apex_service._parse_ranked_split_window(
            _bridge_payload(start=start, end=end)
        ) is None, (start, end)


# --------------------------------------------------------------------------
# 北京时间凌晨 1 点锚定（提交 b30150f 的刻意行为）
# --------------------------------------------------------------------------


def test_summer_split_already_lands_on_beijing_wednesday_one():
    """夏令时期间美西 10:00 == 北京 01:00，归一化是空操作。"""
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(_bridge_payload())
    )
    for iso in (info.start_iso, info.end_iso):
        local = _beijing(iso)
        assert (local.hour, local.minute) == (1, 0)
        assert local.weekday() == 2  # 周三


def test_winter_boundary_is_pinned_to_beijing_one_not_two():
    """冬令时不能顺着太平洋时间漂到北京 02:00。

    这是刻意行为，不是时区 bug：国服口径恒为周三 01:00 更新
    （00:30 关排位）。若有人“修正”成按 PT 换算，此测试必须失败。
    """
    # 2026-11-03 18:00Z 是美西 10:00 PST，落在北京 11-04 02:00。
    raw = "2026-11-03T18:00:00Z"
    assert _beijing(raw).hour == 2, "前置假设：原始值确实是北京 02:00"

    fixed = apex_service._normalize_season_boundary_to_beijing_one(raw)
    local = _beijing(fixed)
    assert (local.hour, local.minute) == (1, 0)
    assert local.weekday() == 2
    assert local.date().isoformat() == "2026-11-04", "只改钟点，不改日期"


def test_normalize_keeps_unparsable_values_untouched():
    for value in ("", "   ", "not-a-timestamp", "2026-08-04"):
        assert (
            apex_service._normalize_season_boundary_to_beijing_one(value)
            == value.strip()
        )


# --------------------------------------------------------------------------
# _build_season_info_from_split
# --------------------------------------------------------------------------


def test_builds_season_info_fields_from_window():
    now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(_bridge_payload()), now=now
    )
    assert info.season_number == 30
    assert info.split_index == 1
    assert info.ranked_season_id == "br_ranked_s30_s1"
    assert info.source == "api.mozambiquehe.re"
    assert info.status_text == "进行中"
    assert info.current_split_label == "上半赛季"
    assert info.next_transition_label == "上半赛季结束"
    assert info.next_transition_iso == info.end_iso
    assert info.split_note


def test_split_two_is_labelled_lower_half():
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(
            _bridge_payload(ranked_season="br_ranked_s30_s2")
        ),
        now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc),
    )
    assert info.current_split_label == "下半赛季"


def test_s30_split_end_is_september_16_not_the_midpoint_guess():
    """回归钉子：旧的“赛季中点后首个周三”启发式会算出 09-23，偏 7 天。

    S30 赛季长 91 天（08-04 → 11-03），中点在 09-19，因此中点推测法必然
    落到 09-23；而游戏内的真实分段边界是 09-16（北京时间）。
    """
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(_bridge_payload())
    )
    assert _beijing(info.end_iso).date().isoformat() == "2026-09-16"
    assert _beijing(info.start_iso).date().isoformat() == "2026-08-05"
    # 分段长度恰好 6 周
    span = apex_service._parse_iso_datetime(
        info.end_iso
    ) - apex_service._parse_iso_datetime(info.start_iso)
    assert span == timedelta(days=42)


# --------------------------------------------------------------------------
# _update_current_split_state
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected_label,expected_transition",
    [
        (datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc), "未开始", "上半赛季开始"),
        (datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc), "上半赛季", "上半赛季结束"),
        (datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc), "上半赛季已结束", ""),
    ],
)
def test_split_state_tracks_now(now, expected_label, expected_transition):
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(_bridge_payload()), now=now
    )
    assert info.current_split_label == expected_label
    assert info.next_transition_label == expected_transition


def test_boundary_instants_are_inclusive_at_start_exclusive_at_end():
    window = apex_service._parse_ranked_split_window(_bridge_payload())
    start = apex_service._parse_iso_datetime(
        apex_service._normalize_season_boundary_to_beijing_one(
            apex_service._to_iso_datetime(window.start)
        )
    )
    end = apex_service._parse_iso_datetime(
        apex_service._normalize_season_boundary_to_beijing_one(
            apex_service._to_iso_datetime(window.end)
        )
    )
    at_start = apex_service._build_season_info_from_split(window, now=start)
    at_end = apex_service._build_season_info_from_split(window, now=end)
    assert at_start.current_split_label == "上半赛季"
    assert at_end.current_split_label == "上半赛季已结束"


# --------------------------------------------------------------------------
# 客户端取数与缓存
# --------------------------------------------------------------------------


def test_fetch_current_season_uses_probe_player():
    calls: list = []
    client = _client(_bridge_payload(), calls)
    info = asyncio.run(
        client.fetch_current_season_info(
            now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        )
    )
    assert calls == ["moeneri"]
    assert info.season_number == 30
    assert info.split_index == 1


def test_fetch_current_season_serves_from_cache_without_second_request():
    calls: list = []
    client = _client(_bridge_payload(), calls)
    now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
    asyncio.run(client.fetch_current_season_info(now=now))
    asyncio.run(client.fetch_current_season_info(now=now))
    assert calls == ["moeneri"], "第二次应命中缓存"


def test_player_query_populates_split_cache_for_free():
    calls: list = []
    client = _client(_bridge_payload(), calls)
    client._note_ranked_split_payload(_bridge_payload())
    info = asyncio.run(
        client.fetch_current_season_info(
            now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        )
    )
    assert calls == [], "已有玩家查询喂过缓存，不该再发探测请求"
    assert info.season_number == 30


def test_note_payload_ignores_unusable_data():
    client = _client()
    for bad in (None, {}, _bridge_payload(include_meta=False)):
        client._note_ranked_split_payload(bad)
    assert client._season_cache == {}


def test_cache_is_dropped_once_the_split_window_has_passed():
    client = _client(_bridge_payload())
    now = datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
    asyncio.run(client.fetch_current_season_info(now=now))
    assert client._get_cached_season("season:current", now=now) is not None
    after_end = datetime(2026, 10, 1, 0, 0, tzinfo=timezone.utc)
    assert client._get_cached_season("season:current", now=after_end) is None


def test_missing_ranked_meta_raises_instead_of_guessing():
    client = _client(_bridge_payload(include_meta=False))
    with pytest.raises(RuntimeError, match="分段"):
        asyncio.run(
            client.fetch_current_season_info(
                now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
            )
        )


def test_numbered_season_query_falls_back_to_current_split():
    """历史赛季查询已不支持：ALS 只暴露当前分段。"""
    calls: list = []
    client = _client(_bridge_payload(), calls)
    info = asyncio.run(client.fetch_season_info(25))
    assert info.season_number == 30, "忽略赛季号，返回当前分段"
    assert calls == ["moeneri"]


# --------------------------------------------------------------------------
# _resolve_season_status
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "now,expected",
    [
        (datetime(2026, 8, 1, tzinfo=timezone.utc), "未开始"),
        (datetime(2026, 8, 25, tzinfo=timezone.utc), "进行中"),
        (datetime(2026, 10, 1, tzinfo=timezone.utc), "已结束"),
    ],
)
def test_resolve_season_status(now, expected):
    assert (
        apex_service._resolve_season_status(
            "2026-08-04T17:00:00Z", "2026-09-15T17:00:00Z", now=now
        )
        == expected
    )


# --------------------------------------------------------------------------
# 回归：SeasonInfo 瘦身后的下游消费者
# --------------------------------------------------------------------------


def test_daily_map_season_key_does_not_touch_removed_season_name():
    """回归：_daily_map_season_key 曾访问已删除的 season_name，导致全天地图刷新崩溃。"""
    info = apex_service._build_season_info_from_split(
        apex_service._parse_ranked_split_window(_bridge_payload())
    )
    key = apex_service._daily_map_season_key(info)
    assert key == "br_ranked_s30_s1"
    assert apex_service._daily_map_season_key(None) == ""


def test_daily_map_season_key_changes_across_splits():
    """地图池按分段轮换：跨分段时 key 必须变化，触发重新学习。"""
    keys = {
        apex_service._daily_map_season_key(
            apex_service._build_season_info_from_split(
                apex_service._parse_ranked_split_window(
                    _bridge_payload(ranked_season=rs)
                )
            )
        )
        for rs in ("br_ranked_s30_s1", "br_ranked_s30_s2")
    }
    assert len(keys) == 2


def test_every_seasoninfo_attribute_touched_by_code_actually_exists():
    """静态兜底：扫描源码里所有 season_info.<attr>，确保都还在 dataclass 上。

    SeasonInfo 本次大幅瘦身，ruff 抓不到属性级别的失效引用（F821 只管名字），
    这条测试就是那道网。
    """
    import dataclasses
    import re
    from pathlib import Path

    valid = {f.name for f in dataclasses.fields(apex_service.SeasonInfo)}
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(r"season_info\.([a-z_]+)")
    offenders = {}
    for name in ("apex_service.py", "main.py"):
        text = (root / name).read_text(encoding="utf-8")
        for attr in set(pattern.findall(text)):
            if attr not in valid:
                offenders.setdefault(name, set()).add(attr)
    assert offenders == {}, f"引用了已不存在的 SeasonInfo 字段: {offenders}"


def test_probe_is_throttled_while_anchored_end_precedes_real_rollover():
    """冬令时翻页空档：锚定 end 已过但 ALS 仍返回旧窗口时，不能每次都打接口。

    没有冷却的话，_get_cached_season 会因为 now 落在窗口外而反复清缓存，
    而关键词监听器（任何含「赛季」的群消息都会触发）会把它放大成刷接口。
    """
    calls: list = []
    # 真实 end = 2026-11-03 18:00Z（北京 11-04 02:00），锚定后变成北京 01:00
    winter_end = int(datetime(2026, 11, 3, 18, 0, tzinfo=timezone.utc).timestamp())
    payload = _bridge_payload(ranked_season="br_ranked_s30_s2", end=winter_end)
    client = _client(payload, calls)
    client._season_probe_cooldown_seconds = 300

    gap = datetime(2026, 11, 3, 17, 30, tzinfo=timezone.utc)  # 北京 01:30
    for _ in range(5):
        info = asyncio.run(client.fetch_current_season_info(now=gap))

    assert len(calls) == 1, f"冷却期内应只打一次接口，实际 {len(calls)} 次"
    assert info.season_number == 30


def test_player_query_refreshes_the_probe_cooldown_source():
    """任何玩家查询都应喂饱冷却源，让 /新赛季 完全不必自己发请求。"""
    calls: list = []
    client = _client(_bridge_payload(), calls)
    client._season_probe_cooldown_seconds = 300
    client._note_ranked_split_payload(_bridge_payload())
    assert client._last_season_probe is not None

    client._season_cache.clear()  # 缓存没了，但冷却源还在
    info = asyncio.run(
        client.fetch_current_season_info(
            now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
        )
    )
    assert calls == [], "应由冷却源重建，不该发请求"
    assert info.season_number == 30


def test_note_payload_never_breaks_the_player_query():
    """簿记失败不能影响本次段位查询本身。"""
    client = _client()
    for bad in (None, {}, {"global": "not-a-dict"}, {"global": {"rank": []}},
                _bridge_payload(start=10**15, end=10**15 + 1)):
        client._note_ranked_split_payload(bad)  # 不得抛异常
    assert client._season_cache == {}


def test_out_of_range_timestamps_are_rejected_not_crashed():
    """毫秒纪元之类的越界时间戳应按「拿不到分段」处理，而不是抛 OSError。"""
    assert apex_service._parse_ranked_split_window(
        _bridge_payload(start=10**15, end=10**15 + 1)
    ) is None


def test_probe_account_failure_names_the_config_key():
    """探测账号失效时，报错必须指向 season_probe_player，否则管理员无从下手。"""
    client = _client(apex_service.PlayerNotFoundError("nope"))
    with pytest.raises(apex_service.ApexApiError, match="season_probe_player"):
        asyncio.run(
            client.fetch_current_season_info(
                now=datetime(2026, 8, 25, 3, 0, tzinfo=timezone.utc)
            )
        )
