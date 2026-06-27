import tempfile
import unittest
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
    extract_cctv_video_center_id,
    parse_hls_master,
    resolve_url,
    sanitize_filename,
)


class DownloaderDiscoveryTests(unittest.TestCase):
    def test_prefers_explicit_download_anchor(self):
        html = """
        <html><body>
          <video src="/watch/preview.mp4"></video>
          <a class="download" href="/files/high.mp4">download video</a>
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
            sanitize_filename('CCTV: clear/video? "test"'),
            "CCTV_ clear_video_ _test_",
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

        mocked.assert_called_once_with("https://example.com/watch/abc", output_dir, "my_clip")
        self.assertEqual(result, output_dir / "my_clip.mp4")

    def test_platform_video_download_returns_completed_mp4_path(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir)
            final_path = output_dir / "clip.mp4"

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
                                "filepath": str(final_path),
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
        self.assertIn("postprocessor_hooks", fake.options)

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
            video = resolve_url("https://www.bilibili.com/video/BV1jL5F6PEog/")

        self.assertEqual(video.kind, "platform-video")
        self.assertEqual(video.source, "yt-dlp")
        self.assertEqual(video.url, "https://www.bilibili.com/video/BV1jL5F6PEog/")


if __name__ == "__main__":
    unittest.main()
