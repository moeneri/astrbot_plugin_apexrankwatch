from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path

import apex_service
from PIL import Image, ImageChops, ImageDraw, ImageStat


def _install_astrbot_stubs() -> None:
    if "astrbot.api.event" in sys.modules:
        return

    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = types.SimpleNamespace(
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
        debug=lambda *args, **kwargs: None,
    )

    event_mod = types.ModuleType("astrbot.api.event")

    class DummyFilter:
        class EventMessageType:
            ALL = object()

        def command(self, *args, **kwargs):
            def decorator(func):
                func._test_command_name = args[0] if args else ""
                func._test_command_aliases = set(kwargs.get("alias") or set())
                return func

            return decorator

        def event_message_type(self, *args, **kwargs):
            return lambda func: func

    event_mod.filter = DummyFilter()
    event_mod.AstrMessageEvent = type("AstrMessageEvent", (), {})
    class DummyMessageChain:
        def __init__(self):
            self.chain = []

        def message(self, text):
            self.chain.append(("Plain", {"text": text}))
            return self

        def file_image(self, path):
            self.chain.append(("Image", {"path": path}))
            return self

    event_mod.MessageChain = DummyMessageChain

    comp_mod = types.ModuleType("astrbot.api.message_components")
    comp_mod.Reply = lambda **kwargs: ("Reply", kwargs)
    comp_mod.Plain = lambda **kwargs: ("Plain", kwargs)
    comp_mod.Image = types.SimpleNamespace(
        fromFileSystem=lambda path: ("Image", {"path": path})
    )

    star_mod = types.ModuleType("astrbot.api.star")
    star_mod.Context = type("Context", (), {})
    star_mod.Star = type("Star", (), {"__init__": lambda self, context: None})
    star_mod.StarTools = type(
        "StarTools", (), {"get_data_dir": staticmethod(lambda: ".")}
    )

    sys.modules.update(
        {
            "astrbot": astrbot,
            "astrbot.api": api,
            "astrbot.api.event": event_mod,
            "astrbot.api.message_components": comp_mod,
            "astrbot.api.star": star_mod,
        }
    )


_install_astrbot_stubs()
import main  # noqa: E402


class MainFormattingTests(unittest.TestCase):
    def test_ranked_map_command_has_requested_aliases(self) -> None:
        self.assertEqual(main.Main.apexmap._test_command_name, "map")
        self.assertEqual(
            main.Main.apexmap._test_command_aliases,
            {"地图", "排位地图", "apexmap", "apexrankmap"},
        )

    def test_match_map_command_name_is_chinese_match_map(self) -> None:
        self.assertEqual(main.Main.apexmatchmap._test_command_name, "匹配地图")

    def test_map_rotation_text_starts_with_current_ranked_map(self) -> None:
        payload = {
            "battle_royale": {
                "current": {
                    "start": 1778054400,
                    "end": 1778059800,
                    "map": "Kings Canyon",
                    "remainingTimer": "01:12:37",
                },
                "next": {
                    "start": 1778059800,
                    "end": 1778065200,
                    "map": "Olympus",
                },
            },
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "map": "Broken Moon",
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "Kings Canyon",
                },
            }
        }
        formatter = object.__new__(main.Main)

        text = formatter._format_map_rotation_text(
            apex_service._parse_map_rotation_info(payload)
        )

        self.assertTrue(text.startswith("📍 当前排位地图：残月 / Broken Moon"))
        self.assertIn("⏰ 本轮时间：2026-05-06 14:30 ~ 2026-05-06 19:00", text)
        self.assertIn("➡️ 下一张：诸王峡谷 / Kings Canyon", text)

    def test_match_map_rotation_text_uses_battle_royale_pool(self) -> None:
        payload = {
            "battle_royale": {
                "current": {
                    "start": 1778054400,
                    "end": 1778059800,
                    "map": "Kings Canyon",
                    "remainingTimer": "01:12:37",
                },
                "next": {
                    "start": 1778059800,
                    "end": 1778065200,
                    "map": "Olympus",
                },
            },
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "map": "Broken Moon",
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "Kings Canyon",
                },
            },
        }
        formatter = object.__new__(main.Main)

        text = formatter._format_map_rotation_text(
            apex_service._parse_map_rotation_info(payload), mode="battle_royale"
        )

        self.assertTrue(text.startswith("📍 当前三人赛地图：诸王峡谷 / Kings Canyon"))
        self.assertIn("🗺️ Apex 三人赛地图轮换", text)
        self.assertIn("⏰ 本轮时间：2026-05-06 16:00 ~ 2026-05-06 17:30", text)
        self.assertIn("➡️ 下一张：奥林匹斯 / Olympus", text)
        self.assertNotIn("Broken Moon", text)

    def test_map_asset_path_resolves_worlds_edge_filename(self) -> None:
        formatter = object.__new__(main.Main)

        asset_path = formatter._resolve_map_asset_path("World's Edge")

        self.assertEqual(asset_path.name, "Worlds_Edge.png")
        self.assertTrue(asset_path.exists())

    def test_map_rotation_image_card_is_generated(self) -> None:
        payload = {
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "map": "Broken Moon",
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "Kings Canyon",
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_map_rotation_image(
                apex_service._parse_map_rotation_info(payload)
            )

            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (900, 320))
                self.assertEqual(image.format, "PNG")

    def test_map_rotation_image_reuses_short_cache_for_same_rotation(self) -> None:
        payload = {
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "map": "Broken Moon",
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "Kings Canyon",
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            rotation_info = apex_service._parse_map_rotation_info(payload)

            first_path = formatter._render_map_rotation_image(rotation_info)
            second_path = formatter._render_map_rotation_image(rotation_info)

            self.assertEqual(first_path, second_path)
            self.assertEqual(
                len(list((Path(tmpdir) / "map_cards").glob("map_rotation_*.png"))),
                1,
            )

    def test_map_rotation_image_keeps_map_background_visible(self) -> None:
        payload = {
            "ranked": {
                "current": {
                    "start": 1778049000,
                    "end": 1778065200,
                    "map": "Broken Moon",
                    "remainingTimer": "02:42:23",
                },
                "next": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "Kings Canyon",
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_map_rotation_image(
                apex_service._parse_map_rotation_info(payload)
            )

            with Image.open(image_path).convert("RGB") as image:
                current_region = image.crop((380, 20, 720, 180))
                next_region = image.crop((360, 230, 620, 305))
                current_stat = ImageStat.Stat(current_region)
                next_stat = ImageStat.Stat(next_region)

            self.assertGreater(sum(current_stat.mean), 60)
            self.assertGreater(sum(next_stat.mean), 60)
            self.assertGreater(max(current_stat.stddev), 8)
            self.assertGreater(max(next_stat.stddev), 8)

    def test_map_rotation_image_keeps_long_next_time_inside_card(self) -> None:
        payload = {
            "ranked": {
                "current": {
                    "start": 1778065200,
                    "end": 1778081400,
                    "map": "E-District",
                    "remainingTimer": "01:12:00",
                },
                "next": {
                    "start": 1778081400,
                    "end": 1778097600,
                    "map": "Kings Canyon",
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_map_rotation_image(
                apex_service._parse_map_rotation_info(payload)
            )

            with Image.open(image_path).convert("RGB") as image:
                right_edge = image.crop((890, 250, 900, 300))
                pixel_data = getattr(
                    right_edge,
                    "get_flattened_data",
                    right_edge.getdata,
                )()
                bright_pixels = sum(1 for pixel in pixel_data if sum(pixel) > 650)

            self.assertLess(bright_pixels, 10)

    def test_predator_text_contains_master_count_in_required_format(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 3,
                }
            }
        }
        formatter = object.__new__(main.Main)

        text = formatter._format_predator_info_text(
            apex_service._parse_predator_info(payload), "PC"
        )

        self.assertIn("🖥️ PC：猎杀线 16,000 RP｜大师数量 3（包含猎杀）", text)

    def test_predator_info_image_card_is_generated_from_template(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 4,
                },
                "PS4": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 3,
                },
                "X1": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 0,
                },
                "SWITCH": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 0,
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_predator_info_image(
                apex_service._parse_predator_info(payload)
            )

            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (1122, 1402))
                self.assertEqual(image.format, "PNG")

    def test_predator_info_image_writes_values_into_empty_template_regions(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 4,
                },
                "PS4": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 3,
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_predator_info_image(
                apex_service._parse_predator_info(payload)
            )

            template = Image.open(main.Main._PREDATOR_TEMPLATE_PATH).convert("RGB")
            rendered = Image.open(image_path).convert("RGB")
            diff = ImageChops.difference(template, rendered)
            pc_threshold_region = diff.crop((310, 540, 740, 650))
            pc_master_region = diff.crop((820, 540, 1080, 650))
            untouched_logo_region = diff.crop((70, 86, 286, 356))

            self.assertGreater(ImageStat.Stat(pc_threshold_region).sum[0], 1000)
            self.assertGreater(ImageStat.Stat(pc_master_region).sum[0], 1000)
            self.assertLess(ImageStat.Stat(untouched_logo_region).sum[0], 100)

    def test_predator_info_image_centers_time_text_in_reserved_boxes(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 4,
                }
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_predator_info_image(
                apex_service._parse_predator_info(payload)
            )

            template = Image.open(main.Main._PREDATOR_TEMPLATE_PATH).convert("RGB")
            rendered = Image.open(image_path).convert("RGB")
            query_bbox = self._diff_bbox(template, rendered, (620, 230, 1060, 325))
            update_bbox = self._diff_bbox(template, rendered, (620, 340, 1060, 435))

            self.assertIsNotNone(query_bbox)
            self.assertIsNotNone(update_bbox)
            self.assertLess(
                abs(self._bbox_center(query_bbox)[0] - self._bbox_center((636, 244, 1053, 318))[0]),
                4,
            )
            self.assertLess(
                abs(self._bbox_center(update_bbox)[0] - self._bbox_center((636, 354, 1053, 426))[0]),
                4,
            )

    def test_predator_info_image_reuses_short_cache_for_same_data(self) -> None:
        payload = {
            "RP": {
                "PC": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 4,
                }
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            predator_info = apex_service._parse_predator_info(payload)

            first_path = formatter._render_predator_info_image(predator_info)
            second_path = formatter._render_predator_info_image(predator_info)

            self.assertEqual(first_path, second_path)
            self.assertEqual(
                len(list((Path(tmpdir) / "predator_cards").glob("predator_info_*.png"))),
                1,
            )

    def test_predator_info_image_keeps_master_numbers_inside_number_boxes(self) -> None:
        payload = {
            "RP": {
                "X1": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 0,
                },
                "SWITCH": {
                    "val": 16000,
                    "updateTimestamp": 1778054401,
                    "totalMastersAndPreds": 0,
                },
            }
        }
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_predator_info_image(
                apex_service._parse_predator_info(payload)
            )

            template = Image.open(main.Main._PREDATOR_TEMPLATE_PATH).convert("RGB")
            rendered = Image.open(image_path).convert("RGB")
            diff = ImageChops.difference(template, rendered)
            xbox_note_region = diff.crop((820, 1064, 1070, 1116))
            switch_note_region = diff.crop((820, 1295, 1070, 1347))

            self.assertLess(ImageStat.Stat(xbox_note_region).sum[0], 120)
            self.assertLess(ImageStat.Stat(switch_note_region).sum[0], 120)

    def test_predator_value_color_rules_match_thresholds(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertEqual(
            formatter._predator_threshold_fill(
                apex_service.PredatorPlatformStats("PC", 16000, 0, "", 0, 0)
            ),
            main.Main._PREDATOR_GREEN,
        )
        self.assertEqual(
            formatter._predator_threshold_fill(
                apex_service.PredatorPlatformStats("PC", 16001, 0, "", 0, 0)
            ),
            main.Main._PREDATOR_DEEP_RED,
        )
        self.assertEqual(
            formatter._predator_master_count_fill(
                apex_service.PredatorPlatformStats("PC", 16000, 0, "", 0, 749)
            ),
            main.Main._PREDATOR_GREEN,
        )
        self.assertEqual(
            formatter._predator_master_count_fill(
                apex_service.PredatorPlatformStats("PC", 16000, 0, "", 0, 750)
            ),
            main.Main._PREDATOR_DEEP_RED,
        )

    def test_season_info_image_card_is_generated(self) -> None:
        formatter = object.__new__(main.Main)
        season_info = apex_service.SeasonInfo(
            season_number=29,
            season_name="Overclocked",
            start_date="",
            end_date="",
            timezone="UTC",
            update_time_hint="",
            source="apexlegendsstatus.com",
            season_url="https://apexlegendsstatus.com/new-season-countdown",
            start_iso="2026-05-05T17:00:00Z",
            end_iso="2026-08-04T17:00:00Z",
            status_text="进行中",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_season_info_image(season_info)

            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (900, 320))
                self.assertEqual(image.format, "PNG")

    def test_season_info_image_uses_apex_red_and_logo_area(self) -> None:
        formatter = object.__new__(main.Main)
        season_info = apex_service.SeasonInfo(
            season_number=29,
            season_name="Overclocked",
            start_date="",
            end_date="",
            timezone="UTC",
            update_time_hint="",
            source="apexlegendsstatus.com",
            season_url="https://apexlegendsstatus.com/new-season-countdown",
            start_iso="2026-05-05T17:00:00Z",
            end_iso="2026-08-04T17:00:00Z",
            status_text="进行中",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_season_info_image(season_info)

            with Image.open(image_path).convert("RGB") as image:
                pixels = getattr(image, "get_flattened_data", image.getdata)()
                apex_red_pixels = sum(
                    1
                    for red, green, blue in pixels
                    if red > 150 and green < 95 and blue < 95
                )
                logo_region = image.crop((34, 24, 128, 118))
                logo_pixels = getattr(
                    logo_region,
                    "get_flattened_data",
                    logo_region.getdata,
                )()
                logo_colored_pixels = sum(
                    1
                    for red, green, blue in logo_pixels
                    if (red > 150 and green < 120 and blue < 120)
                    or (blue > 130 and green > 100 and red < 120)
                )

            self.assertGreater(apex_red_pixels, 5000)
            self.assertGreater(logo_colored_pixels, 800)

    def test_season_card_uses_requested_end_label(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertEqual(formatter._season_end_label(), "赛季结束时间")

    def test_season_source_label_keeps_readme_card_compact(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertEqual(
            formatter._season_source_label(
                "https://apexlegendsstatus.com/new-season-countdown"
            ),
            "来源 apexlegendsstatus.com",
        )
        self.assertEqual(
            formatter._season_source_label(
                "apexlegendsstatus.com/new-season-countdown"
            ),
            "来源 apexlegendsstatus.com",
        )

    @staticmethod
    def _bbox_center(box: tuple[int, int, int, int]) -> tuple[float, float]:
        return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)

    @staticmethod
    def _diff_bbox(
        before: Image.Image,
        after: Image.Image,
        region: tuple[int, int, int, int],
        threshold: int = 30,
    ) -> tuple[int, int, int, int] | None:
        diff = ImageChops.difference(before, after)
        left, top, right, bottom = region
        points = []
        for y in range(top, bottom):
            for x in range(left, right):
                red, green, blue = diff.getpixel((x, y))
                if max(red, green, blue) > threshold:
                    points.append((x, y))
        if not points:
            return None
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)

    def test_centered_text_x_places_text_in_bounds_center(self) -> None:
        formatter = object.__new__(main.Main)
        image = Image.new("RGB", (400, 120), "black")
        draw = ImageDraw.Draw(image)
        font = formatter._font(32, bold=True)

        x = formatter._centered_text_x(draw, "2026-08-05 01:00", font, 120, 360)
        box = draw.textbbox((x, 0), "2026-08-05 01:00", font=font)
        text_center = (box[0] + box[2]) / 2

        self.assertLess(abs(text_center - 240), 1.0)

    def test_rank_change_image_card_is_generated(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=None,
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_rank_change_image(
                player_data=player_data,
                old_score=12674,
                new_score=12671,
                platform="PC",
                is_season_reset=False,
            )

            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (1122, 1402))
                self.assertEqual(image.format, "PNG")

    def test_player_rank_image_card_is_generated(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=apex_service.LegendKillsRank(
                value=3456,
                global_percent="2.34",
            ),
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_player_rank_image(player_data)

            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.size, (1122, 1402))
                self.assertEqual(image.format, "PNG")

    def test_player_rank_image_reuses_rank_legend_avatar_and_status_assets(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=None,
            current_state="大厅中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_player_rank_image(player_data)

            with Image.open(image_path).convert("RGB") as image:
                avatar_region = image.crop((94, 385, 294, 585))
                rank_region = image.crop((780, 380, 1020, 610))
                legend_region = image.crop((54, 1010, 370, 1340))
                status_region = image.crop((724, 1010, 1070, 1340))
                avatar_pixels = list(getattr(avatar_region, "get_flattened_data", avatar_region.getdata)())
                rank_pixels = list(getattr(rank_region, "get_flattened_data", rank_region.getdata)())
                legend_pixels = list(getattr(legend_region, "get_flattened_data", legend_region.getdata)())
                status_pixels = list(getattr(status_region, "get_flattened_data", status_region.getdata)())

        avatar_color = sum(
            1
            for red, green, blue in avatar_pixels
            if max(red, green, blue) - min(red, green, blue) > 35
        )
        rank_color = sum(
            1
            for red, green, blue in rank_pixels
            if max(red, green, blue) - min(red, green, blue) > 35
            and max(red, green, blue) > 90
        )
        legend_color = sum(
            1
            for red, green, blue in legend_pixels
            if max(red, green, blue) - min(red, green, blue) > 45
        )
        status_amber = sum(
            1 for red, green, blue in status_pixels if red > 150 and 70 < green < 190 and blue < 90
        )

        self.assertGreater(avatar_color, 1500)
        self.assertGreater(rank_color, 1200)
        self.assertGreater(legend_color, 1500)
        self.assertGreater(status_amber, 1500)

    def test_player_rank_image_uses_large_score_focus_panel(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=apex_service.LegendKillsRank(
                value=3456,
                global_percent="2.34",
            ),
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_player_rank_image(player_data)

            with Image.open(image_path).convert("RGB") as image:
                score_region = image.crop((300, 720, 820, 950))
                bright_points = []
                for y in range(score_region.height):
                    for x in range(score_region.width):
                        red, green, blue = score_region.getpixel((x, y))
                        if red > 220 and green > 220 and blue > 210:
                            bright_points.append((x, y))

        self.assertGreater(len(bright_points), 8000)
        xs = [point[0] for point in bright_points]
        ys = [point[1] for point in bright_points]
        self.assertGreater(max(xs) - min(xs), 360)
        self.assertGreater(max(ys) - min(ys), 90)

    def test_player_rank_text_fallback_omits_kill_performance(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=apex_service.LegendKillsRank(
                value=3456,
                global_percent="2.34",
            ),
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )

        text = formatter._format_player_rank_text(player_data)

        self.assertNotIn("击杀", text)
        self.assertIn("当前英雄", text)

    def test_apexrank_command_returns_image_card(self) -> None:
        class FakeEvent:
            def get_platform_name(self):
                return "test"

            def chain_result(self, chain):
                return chain

            def plain_result(self, text):
                return [("Plain", {"text": text})]

        class FakeApi:
            async def fetch_player_stats_auto(self, identifier, platform, use_uid):
                return (
                    apex_service.ApexPlayerStats(
                        name=identifier,
                        uid="123456",
                        level=500,
                        rank_score=12671,
                        rank_name="钻石",
                        rank_div=4,
                        global_rank_percent="6.66",
                        is_online=True,
                        selected_legend="动力小子",
                        legend_kills_rank=None,
                        current_state="比赛中",
                        is_in_lobby_or_match=True,
                        platform="PC",
                    ),
                    "PC",
                )

        async def collect_results(formatter):
            results = []
            async for result in formatter.apexrank(FakeEvent(), "Yumola", "PC"):
                results.append(result)
            return results

        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(api_key="test-key", min_valid_score=0)
        formatter._api = FakeApi()
        formatter._guard_access = lambda event: ""
        formatter._parse_player_platform = lambda event, player_name, platform: (player_name, platform)
        formatter._is_blacklisted = lambda player_name: False
        formatter._is_query_blocked = lambda player_name: False
        formatter._parse_identifier = lambda player_name: (player_name, False)
        formatter._prefer_quote_reply = lambda event: False

        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            results = asyncio.run(collect_results(formatter))

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0][0], "Image")
            image_path = Path(results[0][0][1]["path"])
            self.assertTrue(image_path.exists())

    def test_apexhelp_command_returns_image_card(self) -> None:
        class FakeEvent:
            def get_platform_name(self):
                return "test"

            def chain_result(self, chain):
                return chain

            def plain_result(self, text):
                return [("Plain", {"text": text})]

        async def collect_results(formatter):
            results = []
            async for result in formatter.apexrankhelp(FakeEvent()):
                results.append(result)
            return results

        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(
            check_interval=2,
            min_valid_score=1,
            query_blocklist="",
        )
        formatter._runtime_blacklist = set()
        formatter._get_config_blacklist = lambda: set()
        formatter._guard_access = lambda event: ""
        formatter._prefer_quote_reply = lambda event: False

        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            results = asyncio.run(collect_results(formatter))

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0][0], "Image")
            image_path = Path(results[0][0][1]["path"])
            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.size[0], 900)

    def test_apexhelp_card_sections_are_vertical_and_include_aliases(self) -> None:
        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(
            check_interval=2,
            min_valid_score=1,
            query_blocklist="",
        )
        formatter._runtime_blacklist = set()
        formatter._get_config_blacklist = lambda: set()

        sections = formatter._help_card_sections()
        boxes = [box for box, _, _ in sections]
        y_positions = [box[1] for box in boxes]
        commands = "\n".join(command for _, _, rows in sections for command, _ in rows)

        self.assertEqual(y_positions, sorted(y_positions))
        self.assertTrue(all(box[0] == 54 and box[2] == 1068 for box in boxes))
        self.assertIn("/apexpredator [平台] /apex猎杀 /猎杀", commands)
        self.assertIn("/取消持续视奸", commands)

    def test_apexranklist_command_returns_image_card(self) -> None:
        class FakeEvent:
            unified_msg_origin = "origin"

            def get_platform_name(self):
                return "test"

            def chain_result(self, chain):
                return chain

            def plain_result(self, text):
                return [("Plain", {"text": text})]

        players = {
            "name:yumola@PC": main.PlayerRecord(
                player_name="Yumola",
                platform="PC",
                lookup_id="Yumola",
                use_uid=False,
                rank_score=12671,
                rank_name="钻石",
                rank_div=4,
                global_rank_percent="6.66",
                selected_legend="动力小子",
                legend_kills_percent="",
                last_checked=1778054400000,
            ),
            "name:axle@PC": main.PlayerRecord(
                player_name="AxleFan",
                platform="PC",
                lookup_id="AxleFan",
                use_uid=False,
                rank_score=18999,
                rank_name="大师",
                rank_div=0,
                global_rank_percent="0.50",
                selected_legend="艾克赛尔",
                legend_kills_percent="",
                last_checked=1778058000000,
            ),
        }
        group = types.SimpleNamespace(origin="origin", players=players)

        async def collect_results(formatter):
            results = []
            async for result in formatter.apexranklist(FakeEvent()):
                results.append(result)
            return results

        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(check_interval=2, min_valid_score=1)
        formatter._store = types.SimpleNamespace(
            get_group=lambda group_id: group,
            save=lambda: None,
        )
        formatter._guard_access = lambda event, require_group=False: ""
        formatter._get_group_id = lambda event: "10001"
        formatter._prefer_quote_reply = lambda event: False

        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            results = asyncio.run(collect_results(formatter))

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0][0], "Image")
            image_path = Path(results[0][0][1]["path"])
            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.size[0], 900)

    def test_apexrankwatch_success_returns_image_card(self) -> None:
        class FakeEvent:
            unified_msg_origin = "origin"

            def get_platform_name(self):
                return "test"

            def chain_result(self, chain):
                return chain

            def plain_result(self, text):
                return [("Plain", {"text": text})]

        class FakeApi:
            async def fetch_player_stats_auto(self, identifier, platform, use_uid):
                return (
                    apex_service.ApexPlayerStats(
                        name=identifier,
                        uid="123456",
                        level=500,
                        rank_score=12671,
                        rank_name="钻石",
                        rank_div=4,
                        global_rank_percent="6.66",
                        is_online=True,
                        selected_legend="动力小子",
                        legend_kills_rank=None,
                        current_state="比赛中",
                        is_in_lobby_or_match=True,
                        platform="PC",
                    ),
                    "PC",
                )

        group = types.SimpleNamespace(players={})

        def set_player(group_id, player_key, record):
            group.players[player_key] = record

        async def fake_send_active_message(origin, message):
            return True

        async def collect_results(formatter):
            results = []
            async for result in formatter.apexrankwatch(FakeEvent(), "Yumola", "PC"):
                results.append(result)
            return results

        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(api_key="test-key", min_valid_score=0)
        formatter._api = FakeApi()
        formatter._store = types.SimpleNamespace(
            ensure_group=lambda group_id, origin: group,
            set_player=set_player,
            save=lambda: None,
        )
        formatter._guard_access = lambda event, require_group=False: ""
        formatter._parse_player_platform = lambda event, player_name, platform: (player_name, platform)
        formatter._get_group_id = lambda event: "10001"
        formatter._is_blacklisted = lambda player_name: False
        formatter._is_query_blocked = lambda player_name: False
        formatter._parse_identifier = lambda player_name: (player_name, False)
        formatter._prefer_quote_reply = lambda event: False
        formatter._send_active_message = fake_send_active_message

        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            results = asyncio.run(collect_results(formatter))

            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0][0], "Image")
            image_path = Path(results[0][0][1]["path"])
            self.assertTrue(image_path.exists())
            with Image.open(image_path) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreaterEqual(image.size[0], 900)

    def test_monitor_added_card_draws_selected_legend_avatar(self) -> None:
        formatter = object.__new__(main.Main)
        formatter._config = types.SimpleNamespace(check_interval=2)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=None,
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)
            image_path = formatter._render_monitor_added_image(player_data, "PC")

            with Image.open(image_path).convert("RGB") as image:
                legend_region = image.crop((410, 826, 520, 914))
                legend_pixels = list(getattr(legend_region, "get_flattened_data", legend_region.getdata)())
                colorful_pixels = sum(
                    1
                    for red, green, blue in legend_pixels
                    if max(red, green, blue) - min(red, green, blue) > 45
                    and not (red > 220 and green > 220 and blue > 220)
                )

        self.assertGreater(colorful_pixels, 1000)

    def test_default_user_avatar_asset_is_formatted_png(self) -> None:
        avatar_path = main.Main._PLUGIN_ROOT / "assets" / "default_user_avatar.png"

        self.assertTrue(avatar_path.exists())
        with Image.open(avatar_path) as image:
            self.assertEqual(image.size, (256, 256))
            self.assertEqual(image.format, "PNG")

    def test_rank_change_image_uses_default_user_avatar(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=None,
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_rank_change_image(
                player_data=player_data,
                old_score=12674,
                new_score=12671,
                platform="PC",
                is_season_reset=False,
            )

            with Image.open(image_path).convert("RGB") as image:
                avatar_region = image.crop((150, 408, 292, 526))
                pixels = list(getattr(avatar_region, "get_flattened_data", avatar_region.getdata)())
                colorful_pixels = sum(
                    1
                    for red, green, blue in pixels
                    if max(red, green, blue) - min(red, green, blue) > 35
                    and not (red > 225 and green > 220 and blue > 210)
                )

        self.assertGreater(colorful_pixels, 800)

    def test_status_badge_assets_are_formatted_pngs(self) -> None:
        status_dir = main.Main._PLUGIN_ROOT / "assets" / "status"
        expected = {
            "in_match.png",
            "in_lobby.png",
            "offline.png",
        }

        self.assertTrue(status_dir.exists())
        self.assertEqual({path.name for path in status_dir.glob("*.png")}, expected)
        for filename in expected:
            with Image.open(status_dir / filename) as image:
                self.assertEqual(image.size, (346, 330))
                self.assertEqual(image.format, "PNG")

    def test_rank_change_status_assets_resolve_known_states(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertTrue(hasattr(formatter, "_resolve_status_badge_path"))
        self.assertEqual(formatter._resolve_status_badge_path("比赛中").name, "in_match.png")
        self.assertEqual(formatter._resolve_status_badge_path("在大厅").name, "in_lobby.png")
        self.assertEqual(formatter._resolve_status_badge_path("大厅中").name, "in_lobby.png")
        self.assertEqual(formatter._resolve_status_badge_path("离线").name, "offline.png")

    def test_rank_change_status_badge_fills_full_panel(self) -> None:
        formatter = object.__new__(main.Main)
        canvas = Image.new("RGBA", (420, 380), (0, 0, 0, 255))
        draw = ImageDraw.Draw(canvas)

        formatter._draw_rank_status_panel(draw, (24, 24, 370, 354), "大厅中")

        top_half = canvas.crop((24, 24, 370, 190)).convert("RGB")
        pixels = list(getattr(top_half, "get_flattened_data", top_half.getdata)())
        amber_pixels = sum(1 for red, green, blue in pixels if red > 150 and 70 < green < 190 and blue < 80)

        self.assertGreater(amber_pixels, 1000)

    def test_rank_change_image_uses_red_delta_for_score_drop(self) -> None:
        formatter = object.__new__(main.Main)
        player_data = apex_service.ApexPlayerStats(
            name="Yumola",
            uid="123456",
            level=500,
            rank_score=12671,
            rank_name="钻石",
            rank_div=4,
            global_rank_percent="6.66",
            is_online=True,
            selected_legend="动力小子",
            legend_kills_rank=None,
            current_state="比赛中",
            is_in_lobby_or_match=True,
            platform="PC",
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            formatter._data_dir = Path(tmpdir)

            image_path = formatter._render_rank_change_image(
                player_data=player_data,
                old_score=12674,
                new_score=12671,
                platform="PC",
                is_season_reset=False,
            )

            with Image.open(image_path).convert("RGB") as image:
                delta_region = image.crop((450, 700, 670, 930))
                pixels = getattr(delta_region, "get_flattened_data", delta_region.getdata)()
                red_pixels = sum(
                    1
                    for red, green, blue in pixels
                    if red > 150 and green < 90 and blue < 90
                )

            self.assertGreater(red_pixels, 3000)

    def test_rank_change_assets_resolve_chinese_rank_and_legend_names(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertEqual(formatter._resolve_rank_icon_path("钻石").name, "diamond.png")
        self.assertEqual(formatter._resolve_rank_icon_path("猎杀").name, "predator.png")
        self.assertEqual(formatter._resolve_legend_icon_path("动力小子").name, "octane.png")
        self.assertEqual(formatter._resolve_legend_icon_path("艾克赛尔").name, "axle.png")

    def test_rank_icons_resolve_division_specific_assets(self) -> None:
        formatter = object.__new__(main.Main)

        self.assertEqual(formatter._resolve_rank_icon_path("菜鸟", 4).name, "rookie_4.png")
        self.assertEqual(formatter._resolve_rank_icon_path("黄金", 1).name, "gold_1.png")
        self.assertEqual(formatter._resolve_rank_icon_path("Gold", 3).name, "gold_3.png")
        self.assertEqual(formatter._resolve_rank_icon_path("钻石", 2).name, "diamond_2.png")
        self.assertEqual(formatter._resolve_rank_icon_path("大师", 1).name, "master.png")
        self.assertEqual(formatter._resolve_rank_icon_path("猎杀", 4).name, "predator.png")

    def test_image_asset_draw_preserves_source_colors(self) -> None:
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_path = Path(tmpdir) / "asset.png"
            asset = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            asset_draw = ImageDraw.Draw(asset)
            asset_draw.rectangle((4, 4, 59, 59), fill=(220, 30, 40, 255))
            asset_draw.rectangle((24, 24, 40, 40), fill=(40, 200, 90, 255))
            asset.save(asset_path)

            canvas = Image.new("RGBA", (120, 120), (0, 0, 0, 255))
            draw = ImageDraw.Draw(canvas)
            formatter._draw_image_asset(draw, asset_path, (20, 20, 100, 100))

            pasted = canvas.crop((20, 20, 100, 100)).convert("RGB")
            colors = list(getattr(pasted, "get_flattened_data", pasted.getdata)())
            red_pixels = sum(1 for red, green, blue in colors if red > 180 and green < 80 and blue < 90)
            green_pixels = sum(1 for red, green, blue in colors if red < 80 and green > 160 and blue < 120)

        self.assertGreater(red_pixels, 1000)
        self.assertGreater(green_pixels, 100)

    def test_image_asset_draw_crops_transparent_padding_before_scaling(self) -> None:
        formatter = object.__new__(main.Main)
        with tempfile.TemporaryDirectory() as tmpdir:
            asset_path = Path(tmpdir) / "padded_asset.png"
            asset = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
            asset_draw = ImageDraw.Draw(asset)
            asset_draw.ellipse((96, 96, 160, 160), fill=(60, 170, 230, 255))
            asset.save(asset_path)

            canvas = Image.new("RGBA", (160, 160), (0, 0, 0, 255))
            draw = ImageDraw.Draw(canvas)
            formatter._draw_image_asset(draw, asset_path, (30, 30, 130, 130))

            pasted = canvas.crop((30, 30, 130, 130)).convert("RGB")
            pixels = list(getattr(pasted, "get_flattened_data", pasted.getdata)())
            blue_pixels = sum(
                1
                for red, green, blue in pixels
                if blue > 170 and green > 120 and red < 100
            )

        self.assertGreater(blue_pixels, 4500)

    def test_rank_change_asset_folders_include_required_icons(self) -> None:
        ranks = {path.stem for path in main.Main._RANK_ICON_DIR.glob("*.png")}
        legends = {path.stem for path in main.Main._LEGEND_ICON_DIR.glob("*.png")}

        self.assertTrue(
            {
                "rookie",
                "bronze",
                "silver",
                "gold",
                "platinum",
                "diamond",
                "master",
                "predator",
            }.issubset(ranks)
        )
        expected_divisions = {
            f"{rank}_{division}"
            for rank in ("rookie", "bronze", "silver", "gold", "platinum", "diamond")
            for division in (4, 3, 2, 1)
        }
        self.assertTrue(expected_divisions.issubset(ranks))
        self.assertTrue({"octane", "vantage", "sparrow", "axle"}.issubset(legends))

    def test_rank_division_assets_are_formatted_pngs(self) -> None:
        expected_files = [
            *(f"{rank}_{division}.png" for rank in ("rookie", "bronze", "silver", "gold", "platinum", "diamond") for division in (4, 3, 2, 1)),
            "master.png",
            "predator.png",
        ]

        for filename in expected_files:
            path = main.Main._RANK_ICON_DIR / filename
            self.assertTrue(path.exists(), filename)
            with Image.open(path) as image:
                self.assertEqual(image.size, (256, 256), filename)
                self.assertEqual(image.format, "PNG", filename)

    def test_legend_asset_manifest_keeps_chinese_names(self) -> None:
        manifest_path = main.Main._LEGEND_ICON_DIR / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(len(manifest), 28)
        self.assertEqual(manifest["octane"]["zh_name"], "动力小子")
        self.assertEqual(manifest["axle"]["zh_name"], "艾克赛尔")
        self.assertEqual(manifest["sparrow"]["zh_name"], "琉雀")


if __name__ == "__main__":
    unittest.main()
