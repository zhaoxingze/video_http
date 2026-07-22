import json
import tempfile
import unittest
from http.cookies import SimpleCookie
from pathlib import Path

from tencent_meeting import (
    TencentMeetingMedia,
    parse_browser_result,
    resolve_tencent_media_with_browser,
    run_tencent_browser_helper,
    serialize_webview_cookies,
    validate_tencent_media_url,
)


class TencentMeetingTests(unittest.TestCase):
    def test_parse_browser_result_accepts_trusted_signed_mp4(self):
        payload = {
            "media_url": (
                "https://ylz.cos.meeting.tencent.com/cos/example/recording.mp4"
                "?token=temporary"
            ),
            "title": "Meeting lesson - Tencent Meeting",
            "cookies": [
                {"name": "meeting_auth", "value": "secret", "domain": ".meeting.tencent.com"},
                {"name": "unrelated", "value": "do-not-send", "domain": ".qq.com"},
                {"name": "", "value": "ignored"},
            ],
        }

        media = parse_browser_result(json.dumps(payload, ensure_ascii=False))

        self.assertEqual(media.media_url, payload["media_url"])
        self.assertEqual(media.title, "Meeting lesson")
        self.assertEqual(media.cookie_header, "meeting_auth=secret")

    def test_validate_tencent_media_url_rejects_untrusted_or_non_https_urls(self):
        rejected = [
            "blob:https://meeting.tencent.com/id",
            "http://ylz.cos.meeting.tencent.com/video.mp4",
            "https://example.com/video.mp4",
            "https://meeting.tencent.com/not-a-video.js",
        ]

        for value in rejected:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    validate_tencent_media_url(value)

    def test_serialize_webview_cookies_flattens_windows_simple_cookies(self):
        cookie = SimpleCookie()
        cookie["meeting_auth"] = "secret"
        cookie["meeting_auth"]["domain"] = ".meeting.tencent.com"
        cookie["meeting_session"] = "session-value"

        result = serialize_webview_cookies([cookie])

        self.assertEqual(
            result,
            [
                {
                    "name": "meeting_auth",
                    "value": "secret",
                    "domain": ".meeting.tencent.com",
                },
                {"name": "meeting_session", "value": "session-value"},
            ],
        )

    def test_resolve_tencent_media_with_browser_reads_helper_result(self):
        expected = TencentMeetingMedia(
            media_url="https://ylz.cos.meeting.tencent.com/cos/example/recording.mp4?token=test",
            title="Meeting lesson",
        )

        def fake_runner(command, **_kwargs):
            result_path = Path(command[-1])
            result_path.write_text(
                json.dumps({"media_url": expected.media_url, "title": expected.title}),
                encoding="utf-8",
            )

            class Result:
                returncode = 0

            return Result()

        with tempfile.TemporaryDirectory() as tmpdir:
            media = resolve_tencent_media_with_browser(
                "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
                runner=fake_runner,
                temp_dir=Path(tmpdir),
            )

        self.assertEqual(media, expected)

    def test_resolve_tencent_media_with_browser_reports_closed_login_window(self):
        class Result:
            returncode = 2

        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaisesRegex(RuntimeError, "login window"):
                resolve_tencent_media_with_browser(
                    "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
                    runner=lambda *_args, **_kwargs: Result(),
                    temp_dir=Path(tmpdir),
                )

    def test_browser_helper_exits_when_login_window_is_closed(self):
        class FakeEvent:
            def __init__(self):
                self.handlers = []

            def __iadd__(self, handler):
                self.handlers.append(handler)
                return self

            def fire(self):
                for handler in self.handlers:
                    handler()

        class FakeWindow:
            def __init__(self):
                self.events = type("Events", (), {"closed": FakeEvent()})()

            def evaluate_js(self, _script):
                raise RuntimeError("window closed")

        class FakeWebview:
            def __init__(self):
                self.window = FakeWindow()

            def create_window(self, *_args, **_kwargs):
                return self.window

            def start(self, monitor, **_kwargs):
                self.window.events.closed.fire()
                monitor()

        with tempfile.TemporaryDirectory() as tmpdir:
            result = run_tencent_browser_helper(
                "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
                Path(tmpdir) / "result.json",
                webview_module=FakeWebview(),
                poll_interval=0.001,
            )

        self.assertEqual(result, 2)

    def test_browser_helper_still_returns_media_when_cookie_read_fails(self):
        class FakeEvent:
            def __iadd__(self, _handler):
                return self

        class FakeWindow:
            def __init__(self):
                self.events = type("Events", (), {"closed": FakeEvent()})()

            def evaluate_js(self, _script):
                return {
                    "media_url": (
                        "https://ylz.cos.meeting.tencent.com/cos/example/recording.mp4"
                        "?token=temporary"
                    ),
                    "title": "Meeting lesson",
                }

            def get_cookies(self):
                raise RuntimeError("cookie API unavailable")

            def destroy(self):
                pass

        class FakeWebview:
            def create_window(self, *_args, **_kwargs):
                return FakeWindow()

            def start(self, monitor, **_kwargs):
                monitor()

        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "result.json"
            result = run_tencent_browser_helper(
                "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
                result_path,
                webview_module=FakeWebview(),
                poll_interval=0.001,
            )
            payload = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(result, 0)
        self.assertEqual(payload["cookies"], [])


if __name__ == "__main__":
    unittest.main()
