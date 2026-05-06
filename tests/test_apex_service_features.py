from __future__ import annotations

import unittest
from datetime import datetime, timezone

import apex_service


class ApexServiceFeatureTests(unittest.TestCase):
    def test_translate_axle_to_chinese_name(self) -> None:
        self.assertEqual(apex_service.translate("Axle"), "艾克赛尔")

    def test_translate_e_district_to_chinese_name(self) -> None:
        self.assertEqual(apex_service.translate("E-District"), "电力区")

    def test_translate_broken_moon_to_chinese_name(self) -> None:
        self.assertEqual(apex_service.translate("Broken Moon"), "残月")

    def test_parse_ranked_map_rotation_returns_current_and_next_maps(self) -> None:
        payload = {
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "readableDate_start": "2026-05-06 06:30:00",
                    "readableDate_end": "2026-05-06 11:00:00",
                    "map": "Broken Moon",
                    "code": "broken_moon_rotation",
                    "DurationInSecs": 16200,
                    "DurationInMinutes": 270,
                    "asset": "https://example.com/broken_moon.png",
                    "remainingSecs": 9743,
                    "remainingMins": 162,
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "readableDate_start": "2026-05-06 11:00:00",
                    "readableDate_end": "2026-05-06 15:30:00",
                    "map": "Kings Canyon",
                    "code": "kings_canyon_rotation",
                    "DurationInSecs": 16200,
                    "DurationInMinutes": 270,
                    "asset": "https://example.com/kings_canyon.png",
                },
            }
        }

        info = apex_service._parse_map_rotation_info(payload)

        self.assertEqual(info.ranked.current.map_name, "Broken Moon")
        self.assertEqual(info.ranked.current.map_name_zh, "残月")
        self.assertEqual(info.ranked.current.remaining_timer, "02:42:23")
        self.assertEqual(info.ranked.next.map_name, "Kings Canyon")
        self.assertEqual(info.ranked.next.map_name_zh, "诸王峡谷")

    def test_parse_predator_info_uses_pc_values(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "foundRank": -1,
                    "val": 16000,
                    "uid": "-1",
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 3,
                },
                "PS4": {
                    "foundRank": -1,
                    "val": 16000,
                    "uid": "-1",
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 2,
                },
            }
        }

        info = apex_service._parse_predator_info(payload)
        pc = info.platforms["PC"]

        self.assertEqual(pc.threshold_rp, 16000)
        self.assertEqual(pc.masters_and_preds, 3)
        self.assertEqual(pc.update_timestamp, 1778054401)
        self.assertEqual(pc.found_rank, -1)

    def test_parse_apex_status_countdown_builds_current_season_end_time(self) -> None:
        html = """
        <meta property="og:title" content="Countdown to Season 29: Overclocked" />
        <script>
            let startTime = 1778000400; // database unix-timestamp value
        </script>
        """
        now = datetime(2026, 5, 6, 2, 0, tzinfo=timezone.utc)

        info = apex_service._parse_apex_status_current_season_info(html, now=now)

        self.assertEqual(info.season_number, 29)
        self.assertEqual(info.season_name, "Overclocked")
        self.assertEqual(info.start_iso, "2026-05-05T17:00:00Z")
        self.assertEqual(info.end_iso, "2026-08-04T17:00:00Z")
        self.assertEqual(info.source, "apexlegendsstatus.com")

    def test_apex_status_season_date_fields_default_to_beijing_time(self) -> None:
        html = """
        <meta property="og:title" content="Countdown to Season 29: Overclocked" />
        <script>let startTime = 1778000400;</script>
        """
        now = datetime(2026, 5, 6, 2, 0, tzinfo=timezone.utc)

        info = apex_service._parse_apex_status_current_season_info(html, now=now)

        self.assertEqual(info.timezone, "Asia/Shanghai")
        self.assertEqual(info.start_date, "2026-05-06 01:00 北京时间")
        self.assertEqual(info.end_date, "2026-08-05 01:00 北京时间")

    def test_parse_apex_status_countdown_prefers_title_with_season_name(self) -> None:
        html = """
        <meta name="description" content="Countdown to season 29 in Apex Legends" />
        <meta property="og:title" content="Countdown to Season 29: Overclocked" />
        <script>let startTime = 1778000400;</script>
        """

        number, name = apex_service._extract_apex_status_season_title(html)

        self.assertEqual(number, 29)
        self.assertEqual(name, "Overclocked")

    def test_debug_payload_masks_secret_and_player_identifiers(self) -> None:
        payload = {
            "auth": "fake_api_key_value",
            "global": {"uid": "0000000000000", "name": "ExamplePlayer"},
            "nested": [{"player": "sample_player"}],
        }

        text = apex_service.ApexApiClient._serialize_debug_payload(payload)

        self.assertNotIn("fake_api_key_value", text)
        self.assertNotIn("0000000000000", text)
        self.assertNotIn("ExamplePlayer", text)
        self.assertNotIn("sample_player", text)
        self.assertIn("***", text)

    def test_debug_error_text_masks_query_parameters(self) -> None:
        text = (
            "Request URL https://api.mozambiquehe.re/bridge?"
            "auth=fake_api_key_value&player=ExamplePlayer&platform=PC"
        )

        sanitized = apex_service.ApexApiClient._sanitize_debug_text(text)

        self.assertNotIn("fake_api_key_value", sanitized)
        self.assertNotIn("player=ExamplePlayer", sanitized)
        self.assertIn("platform=PC", sanitized)


if __name__ == "__main__":
    unittest.main()
