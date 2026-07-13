import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch
import json

import downloader
from app_gui import (
    PREVIEW_DEBOUNCE_MS,
    PreviewInfo,
    default_font_spec,
    load_default_output_dir,
    extract_preview_info,
    field_specs,
    format_finished_message,
    format_progress_message,
    progress_percent,
    is_probable_url,
    make_output_name,
    main,
    platform_badges,
    primary_button_options,
    remember_output_dir,
    ui_palette,
    window_config,
)


class AppGuiHelperTests(unittest.TestCase):
    def test_load_default_output_dir_uses_saved_directory(self):
        with TemporaryDirectory() as tmpdir:
            saved_dir = Path(tmpdir) / "custom-downloads"
            settings_path = Path(tmpdir) / "settings.json"
            settings_path.write_text(json.dumps({"output_dir": str(saved_dir)}), encoding="utf-8")

            self.assertEqual(load_default_output_dir(settings_path=settings_path), saved_dir)

    def test_remember_output_dir_overwrites_saved_default(self):
        with TemporaryDirectory() as tmpdir:
            settings_path = Path(tmpdir) / "settings.json"
            first = Path(tmpdir) / "first"
            second = Path(tmpdir) / "second"

            remember_output_dir(first, settings_path=settings_path)
            remember_output_dir(second, settings_path=settings_path)

            self.assertEqual(load_default_output_dir(settings_path=settings_path), second)

    def test_preview_debounce_is_short_enough_for_paste_feedback(self):
        self.assertGreaterEqual(PREVIEW_DEBOUNCE_MS, 300)
        self.assertLessEqual(PREVIEW_DEBOUNCE_MS, 900)

    def test_progress_percent_clamps_to_valid_range(self):
        self.assertEqual(progress_percent(0, 100), 0)
        self.assertEqual(progress_percent(75, 100), 75)
        self.assertEqual(progress_percent(150, 100), 100)
        self.assertIsNone(progress_percent(10, 0))

    def test_format_progress_message_uses_percentage_when_total_known(self):
        self.assertEqual(format_progress_message(75, 100), "下载进度：75%")
        self.assertEqual(format_progress_message(5, 0), "下载中：已下载 5 B")

    def test_extract_preview_info_reads_title_thumbnail_with_ytdlp(self):
        captured_options = {}
        calls = []

        class FakeYDL:
            def __init__(self, options):
                captured_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, url, download=False):
                calls.append((url, download))
                return {
                    "title": "示例视频",
                    "thumbnail": "https://i.example.test/cover.jpg",
                    "extractor": "BiliBili",
                }

        info = extract_preview_info(
            "https://example.com/watch/abc",
            ydl_factory=FakeYDL,
        )

        self.assertEqual(
            info,
            PreviewInfo(
                title="示例视频",
                source="BiliBili",
                thumbnail_url="https://i.example.test/cover.jpg",
            ),
        )
        self.assertEqual(calls, [("https://example.com/watch/abc", False)])
        self.assertNotIn("headers", captured_options)
        self.assertNotIn("http_headers", captured_options)
        self.assertTrue(captured_options["skip_download"])

    def test_extract_preview_info_uses_bilibili_api_before_ytdlp(self):
        def fake_fetch(page_url):
            self.assertEqual(page_url, "https://www.bilibili.com/video/BV1jL5F6PEog/")
            return downloader.BilibiliVideoInfo(
                bvid="BV1jL5F6PEog",
                cid=38779030352,
                title="Bili title",
                thumbnail_url="https://i.example.test/cover.jpg",
            )

        class FailingYDL:
            def __init__(self, _options):
                raise AssertionError("yt-dlp should not run for Bilibili preview")

        with patch("app_gui.fetch_bilibili_video_info", side_effect=fake_fetch):
            info = extract_preview_info(
                "https://www.bilibili.com/video/BV1jL5F6PEog/",
                ydl_factory=FailingYDL,
            )

        self.assertEqual(
            info,
            PreviewInfo(
                title="Bili title",
                source="BiliBili",
                thumbnail_url="https://i.example.test/cover.jpg",
            ),
        )

    def test_extract_preview_info_does_not_add_bilibili_headers_to_other_hosts(self):
        captured_options = {}

        class FakeYDL:
            def __init__(self, options):
                captured_options.update(options)

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                return {"title": "Other", "extractor": "Generic"}

        info = extract_preview_info("https://example.com/video/1", ydl_factory=FakeYDL)

        self.assertEqual(info.title, "Other")
        self.assertNotIn("headers", captured_options)
        self.assertNotIn("http_headers", captured_options)

    def test_preview_source_uses_title_fallback_for_blank_metadata(self):
        class FakeYDL:
            def __init__(self, _options):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def extract_info(self, _url, download=False):
                return {}

        info = extract_preview_info("https://example.com/path/fallback-video", ydl_factory=FakeYDL)

        self.assertEqual(info.title, "fallback-video")
        self.assertEqual(info.source, "网页视频")

    def test_main_platform_smoke_test_file_runs_without_tk_and_writes_diagnostics(self):
        with TemporaryDirectory() as tmpdir:
            marker = Path(tmpdir) / "platform-smoke.txt"
            with patch("app_gui.tk.Tk", side_effect=AssertionError("Tk should not be created")):
                exit_code = main(["--platform-smoke-test-file", str(marker)])

            self.assertEqual(exit_code, 0)
            self.assertTrue(marker.exists())
            diagnostics = marker.read_text(encoding="utf-8")
            self.assertIn("yt-dlp", diagnostics)
            self.assertIn("ffmpeg", diagnostics)

    def test_is_probable_url_accepts_http_urls(self):
        self.assertTrue(is_probable_url("https://news.cctv.com/example.shtml"))
        self.assertTrue(is_probable_url("http://example.com/video.mp4"))

    def test_is_probable_url_rejects_blank_or_non_web_values(self):
        self.assertFalse(is_probable_url(""))
        self.assertFalse(is_probable_url("F:/videos/a.mp4"))

    def test_make_output_name_returns_none_for_blank_input(self):
        self.assertIsNone(make_output_name("   "))

    def test_make_output_name_sanitizes_user_input(self):
        self.assertEqual(make_output_name("央视:清明/测试.mp4"), "央视_清明_测试")

    def test_default_font_spec_uses_tuple_for_font_family_with_spaces(self):
        self.assertEqual(default_font_spec(), ("Microsoft YaHei UI", 10))

    def test_field_specs_attach_filename_hint_to_filename_input(self):
        filename = field_specs()["name"]

        self.assertEqual(filename.placeholder, "留空则使用视频标题")
        self.assertEqual(filename.inline_help, "")

    def test_primary_button_options_make_download_action_visually_dominant(self):
        options = primary_button_options()

        self.assertEqual(options["bg"], ui_palette()["primary"])
        self.assertEqual(options["fg"], "#ffffff")
        self.assertGreaterEqual(options["font"][1], 12)
        self.assertEqual(options["font"][2], "bold")

    def test_window_config_uses_reference_image_scale(self):
        config = window_config()

        self.assertEqual(config["geometry"], "960x620")
        self.assertEqual(config["minsize"], (860, 560))
        self.assertGreaterEqual(config["title_font"][1], 22)

    def test_ui_palette_matches_light_glass_download_style(self):
        palette = ui_palette()

        self.assertEqual(palette["primary"], "#0078d7")
        self.assertEqual(palette["background"], "#eef5ff")
        self.assertEqual(palette["surface"], "#fbfdff")
        self.assertEqual(palette["accent"], "#21b7d7")

    def test_platform_badges_show_common_video_sources(self):
        badges = platform_badges()

        self.assertEqual([badge["text"] for badge in badges], ["Y", "B", "D", "V"])
        self.assertTrue(all(badge["bg"].startswith("#") for badge in badges))

    def test_format_finished_message_includes_path_and_size(self):
        path = Path.cwd() / "_format_message_test_clip.mp4"
        path.write_bytes(b"123456")
        try:
            message = format_finished_message(path)
        finally:
            try:
                path.unlink()
            except OSError:
                pass

        self.assertIn("clip.mp4", message)
        self.assertIn("6 B", message)


if __name__ == "__main__":
    unittest.main()
