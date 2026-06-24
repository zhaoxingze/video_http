import unittest

from downloader import (
    build_cctv_variant_candidates,
    discover_candidates,
    extract_cctv_video_center_id,
    parse_hls_master,
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


if __name__ == "__main__":
    unittest.main()
