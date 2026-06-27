import unittest
from pathlib import Path
from unittest.mock import patch

import downloader
from downloader import (
    build_cctv_variant_candidates,
    build_yt_dlp_options,
    DownloadError,
    download_resolved_video,
    discover_candidates,
    extract_cctv_video_center_id,
    ResolvedVideo,
    parse_hls_master,
    resolve_url,
    sanitize_filename,
)


class DownloaderDiscoveryTests(unittest.TestCase):
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
        html = '''
        <script>
        var playerParas = {
          videoCenterId: "063b7096c2004a109dfc12c012bda2c9",
          videoId: "VIDE100215108600"
        };
        </script>
        '''

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


    def test_falls_back_to_platform_downloader_when_html_has_no_direct_media(self):
        html = b"<html><head><title>Platform clip</title></head><body></body></html>"
        with patch("downloader.http_get", return_value=html):
            video = resolve_url("https://www.bilibili.com/video/BV1jL5F6PEog/")

        self.assertEqual(video.kind, "platform-video")
        self.assertEqual(video.source, "yt-dlp")
        self.assertEqual(video.url, "https://www.bilibili.com/video/BV1jL5F6PEog/")


def test_builds_yt_dlp_options_for_mp4_merge():
    options = build_yt_dlp_options(Path("C:/Downloads"), "my_clip", "C:/ffmpeg.exe")

    assert options["format"] == "bv*+ba/b"
    assert options["merge_output_format"] == "mp4"
    assert options["ffmpeg_location"] == "C:/ffmpeg.exe"
    assert options["noplaylist"] is True
    assert "my_clip" in str(options["outtmpl"])


def test_platform_video_dispatches_to_platform_adapter():
    video = ResolvedVideo(
        url="https://example.com/watch/abc",
        kind="platform-video",
        title="Platform clip",
        source="yt-dlp",
    )

    with patch("downloader.download_platform_video", return_value=Path("C:/Downloads/my_clip.mp4")) as mocked:
        result = download_resolved_video(video, Path("C:/Downloads"), "my_clip")

    mocked.assert_called_once_with("https://example.com/watch/abc", Path("C:/Downloads"), "my_clip")
    assert result == Path("C:/Downloads/my_clip.mp4")


def test_platform_video_keyword_invocation_reaches_stub_error():
    with unittest.TestCase().assertRaisesRegex(DownloadError, "平台视频下载适配器尚未实现"):
        downloader.download_platform_video(
            page_url="https://example.com/watch/abc",
            output_dir=Path("C:/Downloads"),
            base_name="my_clip",
        )


if __name__ == "__main__":
    unittest.main()
