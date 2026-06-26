import unittest
from pathlib import Path

from app_gui import (
    default_font_spec,
    field_specs,
    format_finished_message,
    is_probable_url,
    make_output_name,
    platform_badges,
    primary_button_options,
    ui_palette,
    window_config,
)


class AppGuiHelperTests(unittest.TestCase):
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
