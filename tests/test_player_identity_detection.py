from __future__ import annotations

import asyncio

import pytest

from apex_service import ApexApiClient, PlayerNotFoundError, _parse_player_stats


def _player_payload(**global_overrides):
    global_data = {
        "name": "TestPlayer",
        "uid": "1007669673322",
        "level": 358,
        "rank": {
            "rankScore": 5933,
            "rankName": "Gold",
            "rankDiv": 4,
            "ALStopPercentGlobal": 54,
        },
    }
    global_data.update(global_overrides)
    return {
        "global": global_data,
        "realtime": {
            "isOnline": 0,
            "selectedLegend": "Mad Maggie",
            "currentStateAsText": "Offline",
        },
        "legends": {"selected": {"data": []}},
    }


def test_player_response_with_blank_name_and_uid_is_valid():
    client = object.__new__(ApexApiClient)
    payload = _player_payload(name="")

    async def fake_request(_url, _params):
        return payload

    client._request_with_retry = fake_request

    result = asyncio.run(
        client._request_player_data(
            "https://api.mozambiquehe.re/bridge",
            {"uid": "1007669673322", "platform": "PC"},
            "1007669673322",
        )
    )

    stats = _parse_player_stats(result, "PC", "1007669673322")
    assert stats.uid == "1007669673322"
    assert stats.name == "1007669673322"
    assert stats.rank_score == 5933


def test_player_response_without_name_or_uid_is_not_found():
    client = object.__new__(ApexApiClient)
    payload = _player_payload(name="", uid="")

    async def fake_request(_url, _params):
        return payload

    client._request_with_retry = fake_request

    with pytest.raises(PlayerNotFoundError):
        asyncio.run(
            client._request_player_data(
                "https://api.mozambiquehe.re/bridge",
                {"player": "missing", "platform": "PC"},
                "missing",
            )
        )
