import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import downloader
from downloader import (
    DownloadError,
    ResolvedVideo,
    build_cctv_variant_candidates,
    build_yt_dlp_options,
    discover_candidates,
    download_resolved_video,
    download_direct,
    download_tencent_meeting,
    extract_cctv_video_center_id,
    http_get,
    is_tencent_meeting_url,
    normalize_tencent_meeting_url,
    parse_hls_master,
    platform_http_headers,
    resolve_url,
    sanitize_filename,
)


class DownloaderDiscoveryTests(unittest.TestCase):
    def test_build_script_collects_all_yt_dlp_assets(self):
        build_script = Path(__file__).with_name("build_app.ps1").read_text(encoding="utf-8")
        spec_text = Path(__file__).with_name("VideoDownloaderApp.spec").read_text(encoding="utf-8")
        build_lines = [line.strip() for line in build_script.splitlines() if line.strip()]

        self.assertIn("--collect-all yt_dlp", build_script)
        command_end = build_lines.index("app_gui.py")
        self.assertIn("if ($LASTEXITCODE -ne 0) {", build_lines)
        guard_index = build_lines.index("if ($LASTEXITCODE -ne 0) {")
        success_index = build_lines.index('Write-Host "Built app:"')
        self.assertEqual(guard_index, command_end + 1)
        self.assertLess(guard_index, success_index)
        self.assertIn("tmp_ret = collect_all('yt_dlp')", spec_text)
        self.assertIn("datas += tmp_ret[0]", spec_text)
        self.assertIn("binaries += tmp_ret[1]", spec_text)
        self.assertIn("hiddenimports += tmp_ret[2]", spec_text)

    def test_build_script_collects_pywebview_assets(self):
        build_script = Path(__file__).with_name("build_app.ps1").read_text(encoding="utf-8")
        spec_text = Path(__file__).with_name("VideoDownloaderApp.spec").read_text(encoding="utf-8")
        requirements = Path(__file__).with_name("requirements.txt").read_text(encoding="utf-8")

        self.assertIn("--collect-data webview", build_script)
        self.assertIn("--collect-binaries webview", build_script)
        self.assertIn("--hidden-import webview.platforms.winforms", build_script)
        self.assertIn("--exclude-module webview.platforms.qt", build_script)
        self.assertIn("collect_data_files('webview')", spec_text)
        self.assertIn("collect_dynamic_libs('webview')", spec_text)
        self.assertNotIn("collect_all('webview')", spec_text)
        self.assertIn("pywebview", requirements.lower())

    def test_prefers_explicit_download_anchor(self):
        html = """
        <html><body>
          <video src="/watch/preview.mp4"></video>
          <a class="download" href="/files/high.mp4">下载高清视频</a>
        </body></html>
        """

        candidates = discover_candidates(html, "https://example.com/news/page.html")

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0].url, "https://example.com/files/high.mp4")
        self.assertEqual(candidates[0].kind, "direct-video")
        self.assertEqual(candidates[0].source, "download-link")

    def test_detects_video_source_when_no_download_link(self):
        html = """
        <html><body>
          <video controls>
            <source src="//cdn.example.com/media/clip.mp4" type="video/mp4">
          </video>
        </body></html>
        """

        candidates = discover_candidates(html, "https://example.com/post")

        self.assertEqual(candidates[0].url, "https://cdn.example.com/media/clip.mp4")
        self.assertEqual(candidates[0].source, "media-tag")

    def test_extracts_cctv_video_center_id(self):
        html = """
        <script>
        var playerParas = {
          videoCenterId: "063b7096c2004a109dfc12c012bda2c9",
          videoId: "VIDE100215108600"
        };
        </script>
        """

        self.assertEqual(
            extract_cctv_video_center_id(html),
            "063b7096c2004a109dfc12c012bda2c9",
        )

    def test_parse_hls_master_sorts_by_bandwidth(self):
        manifest = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=460800,RESOLUTION=720x1280
/asp/hls/450/id/450.m3u8
#EXT-X-STREAM-INF:BANDWIDTH=870400,RESOLUTION=720x1280
/asp/hls/850/id/850.m3u8
"""

        variants = parse_hls_master(manifest, "https://cdn.example.com/master.m3u8")

        self.assertEqual(variants[0].url, "https://cdn.example.com/asp/hls/850/id/850.m3u8")
        self.assertEqual(variants[0].bandwidth, 870400)
        self.assertEqual(variants[0].resolution, "720x1280")

    def test_builds_cctv_variant_candidates_from_main_playlist(self):
        hls_url = (
            "https://newcntv.qcloudcdn.com/asp/hls/main/0303000a/3/default/"
            "063b7096c2004a109dfc12c012bda2c9/main.m3u8?maxbr=2048"
        )

        candidates = build_cctv_variant_candidates(hls_url)

        self.assertIn(
            "https://newcntv.qcloudcdn.com/asp/hls/850/0303000a/3/default/"
            "063b7096c2004a109dfc12c012bda2c9/850.m3u8",
            candidates,
        )
        self.assertIn(
            "https://newcntv.qcloudcdn.com/asp/hls/2000/0303000a/3/default/"
            "063b7096c2004a109dfc12c012bda2c9/2000.m3u8",
            candidates,
        )

    def test_sanitize_filename_removes_windows_reserved_characters(self):
        self.assertEqual(
            sanitize_filename('央视: 清明/高清视频? "test"'),
            "央视_ 清明_高清视频_ _test_",
        )

    def test_builds_yt_dlp_options_for_mp4_merge(self):
        options = build_yt_dlp_options(Path("C:/Downloads"), "my_clip", "C:/ffmpeg.exe")

        self.assertEqual(options["format"], "bv*+ba/b")
        self.assertEqual(options["merge_output_format"], "mp4")
        self.assertEqual(options["ffmpeg_location"], "C:/ffmpeg.exe")
        self.assertEqual(options["outtmpl"], str(Path("C:/Downloads") / "my_clip.%(ext)s"))
        self.assertEqual(options["noplaylist"], True)
        self.assertEqual(options["quiet"], True)
        self.assertEqual(options["no_warnings"], True)
        self.assertEqual(options["windowsfilenames"], True)

    def test_platform_video_dispatches_to_platform_adapter(self):
        video = ResolvedVideo(
            url="https://example.com/watch/abc",
            kind="platform-video",
            title="Platform clip",
            source="yt-dlp",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "downloads"
            with patch(
                "downloader.download_platform_video",
                return_value=output_dir / "my_clip.mp4",
            ) as mocked:
                result = download_resolved_video(video, output_dir, "my_clip")

        mocked.assert_called_once_with(
            "https://example.com/watch/abc",
            output_dir,
            "my_clip",
            progress_callback=None,
        )
        self.assertEqual(result, output_dir / "my_clip.mp4")

    def test_platform_video_download_returns_completed_mp4_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            final_path = output_dir / "my_clip.mp4"

            class FakeYDL:
                instances = []

                def __init__(self, options):
                    self.options = options
                    self.calls = []
                    FakeYDL.instances.append(self)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    self.calls.append((page_url, download))
                    final_path.write_bytes(b"video-bytes")
                    for hook in self.options["postprocessor_hooks"]:
                        hook(
                            {
                                "status": "finished",
                                "postprocessor": "MoveFilesAfterDownload",
                                "info_dict": {"filepath": str(final_path)},
                            }
                        )
                    return {"filepath": str(final_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                result = downloader.download_platform_video(
                    page_url="https://example.com/watch/abc",
                    output_dir=output_dir,
                    base_name="my_clip",
                    ydl_factory=FakeYDL,
                )

        self.assertEqual(result, final_path)
        self.assertEqual(len(FakeYDL.instances), 1)
        fake = FakeYDL.instances[0]
        self.assertEqual(fake.calls, [("https://example.com/watch/abc", True)])
        self.assertEqual(fake.options["ffmpeg_location"], "C:/ffmpeg.exe")
        self.assertEqual(fake.options["merge_output_format"], "mp4")
        self.assertEqual(fake.options["outtmpl"], str(output_dir / "my_clip.%(ext)s"))
        self.assertIn("postprocessor_hooks", fake.options)

    def test_bilibili_platform_video_adds_origin_and_referer_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            final_path = output_dir / "bilibili_clip.mp4"

            class FakeYDL:
                instances = []

                def __init__(self, options):
                    self.options = options
                    self.calls = []
                    FakeYDL.instances.append(self)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    self.calls.append((page_url, download))
                    final_path.write_bytes(b"video-bytes")
                    for hook in self.options["postprocessor_hooks"]:
                        hook(
                            {
                                "status": "finished",
                                "info_dict": {"filepath": str(final_path)},
                            }
                        )
                    return {"filepath": str(final_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                result = downloader.download_platform_video(
                    page_url="https://www.bilibili.com/video/BV1jL5F6PEog/",
                    output_dir=output_dir,
                    base_name="bilibili_clip",
                    ydl_factory=FakeYDL,
                )

        self.assertEqual(result, final_path)
        self.assertEqual(len(FakeYDL.instances), 1)
        fake = FakeYDL.instances[0]
        self.assertEqual(fake.calls, [("https://www.bilibili.com/video/BV1jL5F6PEog/", True)])
        self.assertEqual(
            fake.options.get("headers"),
            {
                "Origin": "https://www.bilibili.com",
                "Referer": "https://www.bilibili.com/",
            },
        )
        self.assertNotIn("http_headers", fake.options)

    def test_platform_http_headers_matches_only_bilibili_hosts(self):
        expected = {
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com/",
        }

        accepted_urls = [
            "https://bilibili.com/video/BV1jL5F6PEog/",
            "https://api.bilibili.com/x/player/wbi/playurl",
            "https://www.bilibili.com/video/BV1jL5F6PEog/",
        ]
        rejected_urls = [
            "https://bilibili.com.evil.example/video/BV1jL5F6PEog/",
            "https://notbilibili.com/video/BV1jL5F6PEog/",
        ]

        for url in accepted_urls:
            with self.subTest(url=url, expected_headers=True):
                self.assertEqual(platform_http_headers(url), expected)

        for url in rejected_urls:
            with self.subTest(url=url, expected_headers=False):
                self.assertEqual(platform_http_headers(url), {})

    def test_bilibili_resolve_uses_public_api_without_fetching_page(self):
        calls = []

        def fake_http_get(url, **_kwargs):
            calls.append(url)
            if "x/web-interface/view" in url:
                return (
                    b'{"code":0,"data":{"bvid":"BV1jL5F6PEog","cid":38779030352,'
                    b'"title":"Bili title","pic":"http://i.example.test/cover.jpg"}}'
                )
            if "x/player/wbi/playurl" in url:
                self.assertIn("bvid=BV1jL5F6PEog", url)
                self.assertIn("cid=38779030352", url)
                return (
                    b'{"code":0,"data":{"durl":[{"url":"https://upos.example.test/video.mp4"}]}}'
                )
            raise AssertionError(f"unexpected fetch: {url}")

        with patch("downloader.http_get", side_effect=fake_http_get):
            video = resolve_url("https://www.bilibili.com/video/BV1jL5F6PEog/")

        self.assertEqual(video.kind, "direct-video")
        self.assertEqual(video.source, "bilibili-api")
        self.assertEqual(video.title, "Bili title")
        self.assertEqual(video.url, "https://upos.example.test/video.mp4")
        self.assertEqual(len(calls), 2)

    def test_bilivideo_direct_download_sends_bilibili_referer(self):
        captured_headers = {}
        test_case = self

        class FakeResponse(BytesIO):
            headers = {"Content-Length": "11"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeOpener:
            def open(self, request, timeout=60):
                captured_headers.update(dict(request.header_items()))
                test_case.assertEqual(timeout, 60)
                return FakeResponse(b"video-bytes")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("downloader.build_opener", return_value=FakeOpener()):
                result = download_direct(
                    "https://upos.example.bilivideo.com/path/video.mp4?token=1",
                    Path(tmpdir),
                    "clip",
                )

        self.assertEqual(result.name, "clip.mp4")
        self.assertEqual(captured_headers["User-agent"], downloader.USER_AGENT)
        self.assertEqual(captured_headers["Origin"], "https://www.bilibili.com")
        self.assertEqual(captured_headers["Referer"], "https://www.bilibili.com/")

    def test_bilivideo_direct_headers_use_full_chrome_user_agent(self):
        headers = downloader.direct_download_headers("https://upos.example.bilivideo.com/path/video.mp4")

        self.assertIn("Chrome/126.0.0.0", headers["User-Agent"])

    def test_http_get_bypasses_proxy_for_bilibili_hosts(self):
        opened = {}

        class FakeResponse(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        class FakeOpener:
            def open(self, request, timeout=30):
                opened["url"] = request.full_url
                opened["timeout"] = timeout
                return FakeResponse(b'{"code":0}')

        with patch("downloader.build_opener", return_value=FakeOpener()) as build_opener:
            with patch("downloader.urlopen", side_effect=AssertionError("proxy urlopen should not run")):
                raw = http_get("https://api.bilibili.com/x/web-interface/view?bvid=BV1TcMJ6XE8M")

        self.assertEqual(raw, b'{"code":0}')
        self.assertEqual(opened["url"], "https://api.bilibili.com/x/web-interface/view?bvid=BV1TcMJ6XE8M")
        self.assertEqual(opened["timeout"], 30)
        self.assertEqual(build_opener.call_count, 1)

    def test_http_get_uses_default_proxy_policy_for_other_hosts(self):
        class FakeResponse(BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with patch("downloader.urlopen", return_value=FakeResponse(b"ok")) as default_urlopen:
            with patch("downloader.build_opener", side_effect=AssertionError("should not bypass proxy")):
                raw = http_get("https://example.com/video")

        self.assertEqual(raw, b"ok")
        self.assertEqual(default_urlopen.call_count, 1)

    def test_direct_download_rejects_incomplete_response(self):
        class FakeResponse(BytesIO):
            headers = {"Content-Length": "20"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("downloader.urlopen", return_value=FakeResponse(b"short")):
                with self.assertRaises(DownloadError) as cm:
                    download_direct(
                        "https://cdn.example.test/video.mp4",
                        Path(tmpdir),
                        "clip",
                    )
            self.assertFalse((Path(tmpdir) / "clip.mp4").exists())

        self.assertIn("下载不完整", str(cm.exception))

    def test_direct_download_reports_progress_callback(self):
        class FakeResponse(BytesIO):
            headers = {"Content-Length": "11"}

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        progress = []
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("downloader.urlopen", return_value=FakeResponse(b"video-bytes")):
                result = download_direct(
                    "https://cdn.example.test/video.mp4",
                    Path(tmpdir),
                    "clip",
                    progress_callback=lambda done, total: progress.append((done, total)),
                )

        self.assertEqual(result.name, "clip.mp4")
        self.assertEqual(progress, [(11, 11)])

    def test_unrelated_platform_video_does_not_add_bilibili_headers(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            final_path = output_dir / "other_clip.mp4"

            class FakeYDL:
                instances = []

                def __init__(self, options):
                    self.options = options
                    FakeYDL.instances.append(self)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    final_path.write_bytes(b"video-bytes")
                    for hook in self.options["postprocessor_hooks"]:
                        hook(
                            {
                                "status": "finished",
                                "info_dict": {"filepath": str(final_path)},
                            }
                        )
                    return {"filepath": str(final_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                result = downloader.download_platform_video(
                    page_url="https://www.bilibili.com.example.org/video/BV1jL5F6PEog/",
                    output_dir=output_dir,
                    base_name="other_clip",
                    ydl_factory=FakeYDL,
                )

        self.assertEqual(result, final_path)
        self.assertEqual(len(FakeYDL.instances), 1)
        fake = FakeYDL.instances[0]
        self.assertNotIn("headers", fake.options)
        self.assertNotIn("http_headers", fake.options)

    def test_platform_video_download_uses_new_base_when_sibling_media_exists(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            stale_path = output_dir / "clip.webm"
            stale_path.write_bytes(b"old-video")

            class FakeYDL:
                instances = []

                def __init__(self, options):
                    self.options = options
                    FakeYDL.instances.append(self)

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    final_path = output_dir / "clip_2.mp4"
                    final_path.write_bytes(b"new-video")
                    return {"filepath": str(final_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                result = downloader.download_platform_video(
                    page_url="https://example.com/watch/abc",
                    output_dir=output_dir,
                    base_name="clip",
                    ydl_factory=FakeYDL,
                )

                self.assertEqual(result, output_dir / "clip_2.mp4")
                self.assertEqual(stale_path.read_bytes(), b"old-video")
                self.assertEqual(len(FakeYDL.instances), 1)
                self.assertTrue(FakeYDL.instances[0].options["outtmpl"].endswith("clip_2.%(ext)s"))

    def test_platform_video_download_rejects_external_existing_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            temp_root = Path(tmpdir)
            output_dir = temp_root / "downloads"
            output_dir.mkdir()
            external_path = temp_root / "unrelated.mp4"
            external_path.write_bytes(b"external-video")

            class ExternalPathYDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    return {"filepath": str(external_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                with self.assertRaises(DownloadError) as cm:
                    downloader.download_platform_video(
                        page_url="https://example.com/watch/abc",
                        output_dir=output_dir,
                        base_name="my_clip",
                        ydl_factory=ExternalPathYDL,
                    )

        self.assertIn("生成的视频文件", str(cm.exception))

    def test_platform_video_download_rejects_same_directory_mismatched_base_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            mismatched_path = output_dir / "otherclip.mp4"
            mismatched_path.write_bytes(b"wrong-video")

            class MismatchedBaseYDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    return {"filepath": str(mismatched_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                with self.assertRaises(DownloadError) as cm:
                    downloader.download_platform_video(
                        page_url="https://example.com/watch/abc",
                        output_dir=output_dir,
                        base_name="my_clip",
                        ydl_factory=MismatchedBaseYDL,
                    )

        self.assertIn("生成的视频文件", str(cm.exception))

    def test_platform_video_download_wraps_ydl_errors(self):
        class BrokenYDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def extract_info(self, page_url, download=True):
                raise RuntimeError("login required")

        with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
            with self.assertRaises(DownloadError) as cm:
                downloader.download_platform_video(
                    page_url="https://example.com/watch/abc",
                    output_dir=Path("C:/Downloads"),
                    base_name="my_clip",
                    ydl_factory=BrokenYDL,
                )

        self.assertIn("login required", str(cm.exception))

    def test_platform_video_download_requires_completed_output_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            missing_path = output_dir / "ghost.mp4"

            class MissingOutputYDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    return {"filepath": str(missing_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                with self.assertRaises(DownloadError) as cm:
                    downloader.download_platform_video(
                        page_url="https://example.com/watch/abc",
                        output_dir=output_dir,
                        base_name="my_clip",
                        ydl_factory=MissingOutputYDL,
                    )

        self.assertIn("生成的视频文件", str(cm.exception))

    def test_platform_video_download_rejects_metadata_only_outputs(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            info_json = output_dir / "clip.info.json"

            class MetadataOnlyYDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    info_json.write_text('{"id":"abc"}', encoding="utf-8")
                    return {"filepath": str(info_json)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                with self.assertRaises(DownloadError) as cm:
                    downloader.download_platform_video(
                        page_url="https://example.com/watch/abc",
                        output_dir=output_dir,
                        base_name="clip",
                        ydl_factory=MetadataOnlyYDL,
                    )

        self.assertIn("生成的视频文件", str(cm.exception))

    def test_platform_video_download_wraps_selection_filesystem_errors(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            final_path = output_dir / "my_clip.mp4"
            final_path.write_bytes(b"video-bytes")

            class FakeYDL:
                def __init__(self, options):
                    self.options = options

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def extract_info(self, page_url, download=True):
                    return {"filepath": str(final_path)}

            with patch("downloader.find_ffmpeg", return_value="C:/ffmpeg.exe"):
                with patch(
                    "downloader.iter_existing_base_paths",
                    side_effect=[[], OSError("access denied")],
                ):
                    with self.assertRaises(DownloadError) as cm:
                        downloader.download_platform_video(
                            page_url="https://example.com/watch/abc",
                            output_dir=output_dir,
                            base_name="my_clip",
                            ydl_factory=FakeYDL,
                        )

        self.assertIn("access denied", str(cm.exception))

    def test_platform_video_rejects_fourth_positional_argument(self):
        with self.assertRaises(TypeError):
            downloader.download_platform_video(
                "https://example.com/watch/abc",
                Path("C:/Downloads"),
                "my_clip",
                lambda options: object(),
            )

    def test_falls_back_to_platform_downloader_when_html_has_no_direct_media(self):
        html = b"<html><head><title>Platform clip</title></head><body></body></html>"
        with patch("downloader.http_get", return_value=html):
            video = resolve_url("https://example.com/watch/abc")

        self.assertEqual(video.kind, "platform-video")
        self.assertEqual(video.source, "yt-dlp")
        self.assertEqual(video.url, "https://example.com/watch/abc")

    def test_normalizes_tencent_meeting_share_links(self):
        self.assertTrue(is_tencent_meeting_url("https://meeting.tencent.com/crm/KD9ZEJ3B7a"))
        self.assertTrue(is_tencent_meeting_url("https://meeting.tencent.com/cw/KD9ZEJ3B7a"))
        self.assertEqual(
            normalize_tencent_meeting_url("https://meeting.tencent.com/crm/KD9ZEJ3B7a"),
            "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
        )

    def test_tencent_meeting_resolves_before_generic_html_discovery(self):
        with patch("downloader.http_get", side_effect=AssertionError("HTML fallback must not run")):
            video = resolve_url("https://meeting.tencent.com/crm/KD9ZEJ3B7a")

        self.assertEqual(video.kind, "tencent-meeting")
        self.assertEqual(video.source, "tencent-webview")
        self.assertEqual(video.title, "KD9ZEJ3B7a")
        self.assertEqual(video.url, "https://meeting.tencent.com/cw/KD9ZEJ3B7a")

    def test_tencent_meeting_download_uses_browser_media_and_progress(self):
        progress = object()
        page_url = "https://meeting.tencent.com/cw/KD9ZEJ3B7a"
        media = downloader.TencentMeetingMedia(
            media_url="https://ylz.cos.meeting.tencent.com/cos/example/recording.mp4?token=test",
            title="Meeting lesson",
            cookie_header="meeting_auth=secret",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            expected = output_dir / "Meeting lesson.mp4"
            with patch("downloader.download_direct", return_value=expected) as mocked:
                result = download_tencent_meeting(
                    page_url,
                    output_dir,
                    None,
                    fallback_title="KD9ZEJ3B7a",
                    media_resolver=lambda _url: media,
                    progress_callback=progress,
                )

        self.assertEqual(result, expected)
        mocked.assert_called_once_with(
            media.media_url,
            output_dir,
            "Meeting lesson",
            headers={
                "Cookie": "meeting_auth=secret",
                "Origin": "https://meeting.tencent.com",
                "Referer": page_url,
                "User-Agent": downloader.TENCENT_WEBVIEW_USER_AGENT,
            },
            progress_callback=progress,
        )

    def test_tencent_meeting_video_dispatches_to_browser_adapter(self):
        video = ResolvedVideo(
            "https://meeting.tencent.com/cw/KD9ZEJ3B7a",
            "tencent-meeting",
            "KD9ZEJ3B7a",
            "tencent-webview",
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            expected = output_dir / "lesson.mp4"
            with patch("downloader.download_tencent_meeting", return_value=expected) as mocked:
                result = download_resolved_video(video, output_dir, "lesson")

        self.assertEqual(result, expected)
        mocked.assert_called_once_with(
            video.url,
            output_dir,
            "lesson",
            fallback_title=video.title,
            progress_callback=None,
        )


if __name__ == "__main__":
    unittest.main()
