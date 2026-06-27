# Bilibili Platform Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a packaged `yt-dlp` fallback so the desktop app downloads public Bilibili DASH videos as merged MP4 files without changing existing direct-link, HLS, or CCTV behavior.

**Architecture:** Keep the current resolver first. If an HTML page has no existing media candidate, classify it as `platform-video`; the downloader delegates that type to a focused `yt-dlp` adapter that selects the best video and audio, uses bundled FFmpeg, and returns the finished file path.

**Tech Stack:** Python 3, `unittest`, `yt-dlp` Python API, `imageio-ffmpeg`, Tkinter, PyInstaller.

---

## File Map

- Modify `downloader.py`: fallback classification, yt-dlp options, adapter, path detection, error conversion.
- Modify `test_downloader.py`: regression tests for fallback, dispatch, options, and failures.
- Modify `requirements.txt`: add yt-dlp.
- Modify `build_app.ps1` and regenerate `VideoDownloaderApp.spec`: package yt-dlp.
- Modify `README.md`: document platform support and limits.
- Replace `dist/VideoDownloaderApp.exe` and the desktop copy after verification.

### Task 1: Platform Fallback Classification

**Files:**
- Modify: `test_downloader.py`
- Modify: `downloader.py:315-364`

- [ ] **Step 1: Write the failing fallback test**

```python
from unittest.mock import patch
from downloader import resolve_url

def test_falls_back_to_platform_downloader_when_html_has_no_direct_media(self):
    html = b"<html><head><title>Platform clip</title></head><body></body></html>"
    with patch("downloader.http_get", return_value=html):
        video = resolve_url("https://www.bilibili.com/video/BV1jL5F6PEog/")
    self.assertEqual(video.kind, "platform-video")
    self.assertEqual(video.source, "yt-dlp")
```

- [ ] **Step 2: Run and verify RED**

Run: `python -B -m unittest -v test_downloader.DownloaderDiscoveryTests.test_falls_back_to_platform_downloader_when_html_has_no_direct_media`

Expected: FAIL because `resolve_page_video()` raises `DownloadError`.

- [ ] **Step 3: Implement the minimal classification**

```python
if not candidates:
    return ResolvedVideo(page_url, "platform-video", title, "yt-dlp"), html
```

- [ ] **Step 4: Run `python -B -m unittest -v test_downloader.py` and expect all tests to pass.**

- [ ] **Step 5: Commit**

```powershell
git add -- downloader.py test_downloader.py
git commit -m "Add platform video fallback classification"
```

### Task 2: yt-dlp Options and Dispatch

**Files:**
- Modify: `requirements.txt`
- Modify: `test_downloader.py`
- Modify: `downloader.py:367-420`

- [ ] **Step 1: Add `yt-dlp>=2025.1.15` to requirements, install it, and write failing tests**

```python
def test_yt_dlp_options_merge_best_streams_to_mp4(self):
    options = build_yt_dlp_options(Path("C:/Downloads"), "my_clip", "C:/ffmpeg.exe")
    self.assertEqual(options["format"], "bv*+ba/b")
    self.assertEqual(options["merge_output_format"], "mp4")
    self.assertEqual(options["ffmpeg_location"], "C:/ffmpeg.exe")
    self.assertTrue(options["noplaylist"])

def test_platform_video_dispatches_to_yt_dlp_adapter(self):
    video = ResolvedVideo("https://www.bilibili.com/video/BV1test/", "platform-video", "clip", "yt-dlp")
    with TemporaryDirectory() as temp_dir:
        expected = Path(temp_dir) / "clip.mp4"
        with patch("downloader.download_platform_video", return_value=expected) as adapter:
            result = download_resolved_video(video, Path(temp_dir))
    self.assertEqual(result, expected)
    adapter.assert_called_once()
```

- [ ] **Step 2: Run the two tests and verify RED due to missing helpers.**

- [ ] **Step 3: Add the option builder and dispatch**

```python
def build_yt_dlp_options(output_dir: Path, base_name: str, ffmpeg: str) -> dict[str, object]:
    return {
        "format": "bv*+ba/b",
        "merge_output_format": "mp4",
        "ffmpeg_location": ffmpeg,
        "outtmpl": str(output_dir / f"{base_name}.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "windowsfilenames": True,
    }
```

In `download_resolved_video()`, route `platform-video` to `download_platform_video()` before HLS and direct downloads.

- [ ] **Step 4: Run both focused tests and expect PASS.**

- [ ] **Step 5: Commit**

```powershell
git add -- downloader.py test_downloader.py requirements.txt
git commit -m "Add yt-dlp platform download adapter"
```

### Task 3: Download Completion and Error Conversion

**Files:**
- Modify: `test_downloader.py`
- Modify: `downloader.py`

- [ ] **Step 1: Write failing tests using an injected fake `YoutubeDL` factory**

```python
def test_platform_adapter_returns_final_merged_file(self):
    with TemporaryDirectory() as temp_dir:
        output_dir = Path(temp_dir)
        final_path = output_dir / "clip.mp4"

        class FakeYDL:
            def __init__(self, options):
                self.options = options
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def extract_info(self, _url, download):
                self.assert_download = download
                final_path.write_bytes(b"video")
                return {"filepath": str(final_path)}

        result = download_platform_video(
            "https://example.com/watch", output_dir, "clip", ydl_factory=FakeYDL
        )
        self.assertEqual(result, final_path)

def test_platform_adapter_converts_extractor_failure(self):
    class BrokenYDL:
        def __init__(self, _options):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def extract_info(self, _url, download):
            raise RuntimeError("login required")

    with TemporaryDirectory() as temp_dir:
        with self.assertRaisesRegex(DownloadError, "login required"):
            download_platform_video(
                "https://example.com/watch", Path(temp_dir), "clip", ydl_factory=BrokenYDL
            )
```

- [ ] **Step 2: Run the two focused tests and verify RED because `download_platform_video()` is absent.**

- [ ] **Step 3: Implement the adapter**

```python
def download_platform_video(
    page_url: str,
    output_dir: Path,
    base_name: str,
    *,
    ydl_factory=None,
) -> Path:
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise DownloadError("需要 FFmpeg 才能合并平台的高清画面和音频。")

    if ydl_factory is None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise DownloadError("程序缺少 yt-dlp 平台解析组件，请重新安装或重新打包。") from exc
        ydl_factory = YoutubeDL

    finished_paths: list[Path] = []

    def remember_finished(item: dict[str, object]) -> None:
        if item.get("status") != "finished":
            return
        info = item.get("info_dict")
        path_value = info.get("filepath") if isinstance(info, dict) else item.get("filepath")
        if path_value:
            finished_paths.append(Path(str(path_value)))

    unique_name = unique_path(output_dir / f"{base_name}.mp4").stem
    options = build_yt_dlp_options(output_dir, unique_name, ffmpeg)
    options["postprocessor_hooks"] = [remember_finished]

    try:
        with ydl_factory(options) as ydl:
            info = ydl.extract_info(page_url, download=True)
    except Exception as exc:
        raise DownloadError(f"平台视频下载失败：{exc}") from exc

    candidates = list(finished_paths)
    if isinstance(info, dict):
        for key in ("filepath", "_filename"):
            if info.get(key):
                candidates.append(Path(str(info[key])))
        requested = info.get("requested_downloads")
        if isinstance(requested, list):
            candidates.extend(
                Path(str(item["filepath"]))
                for item in requested
                if isinstance(item, dict) and item.get("filepath")
            )

    candidates.extend(output_dir.glob(f"{unique_name}.*"))
    completed = [
        path for path in candidates
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl"}
    ]
    if not completed:
        raise DownloadError("平台下载结束，但没有找到生成的视频文件。")
    return max(completed, key=lambda path: path.stat().st_mtime_ns)
```

- [ ] **Step 4: Run `python -B -m unittest -v test_downloader.py test_app_gui.py` and expect all tests to pass.**

- [ ] **Step 5: Commit**

```powershell
git add -- downloader.py test_downloader.py
git commit -m "Download and merge platform videos with yt-dlp"
```

### Task 4: Packaging and Documentation

**Files:**
- Modify: `build_app.ps1`
- Modify: `README.md`
- Regenerate: `VideoDownloaderApp.spec`
- Modify: `test_downloader.py`

- [ ] **Step 1: Write a failing packaging test**

```python
def test_build_collects_yt_dlp_package(self):
    build_script = Path("build_app.ps1").read_text(encoding="utf-8")
    self.assertIn("--collect-all yt_dlp", build_script)
```

- [ ] **Step 2: Run it and verify RED.**

- [ ] **Step 3: Add `--collect-all yt_dlp` to `build_app.ps1` and document Bilibili DASH support plus login/member/DRM limits in `README.md`.**

- [ ] **Step 4: Run the full tests, then `.\build_app.ps1`; expect tests to pass and a fresh `dist\VideoDownloaderApp.exe`.**

- [ ] **Step 5: Commit**

```powershell
git add -- build_app.ps1 README.md VideoDownloaderApp.spec test_downloader.py
git commit -m "Package yt-dlp with the desktop app"
```

### Task 5: Real Bilibili Acceptance and Delivery

- [ ] **Step 1: Run the exact user URL with `--dry-run`; expect source `yt-dlp`, kind `platform-video`, exit code 0.**

- [ ] **Step 2: Download the exact URL to a temporary acceptance directory. Verify the MP4 exists, is non-empty, and FFmpeg reports one video and one audio stream; then remove only that verified temporary directory.**

- [ ] **Step 3: Launch `python -B app_gui.py` and the packaged EXE separately; each must remain alive for at least five seconds without an exception dialog.**

- [ ] **Step 4: Copy the rebuilt EXE to `C:\Users\zhao'xing'ze\Desktop\网页视频下载器.exe` and verify its SHA256 equals the source EXE.**

- [ ] **Step 5: Run final verification and push**

```powershell
python -B -m unittest -v test_downloader.py test_app_gui.py
git diff --check
git status -sb
git push origin main
```

Expected: all tests pass, no whitespace errors, all implementation commits reach `origin/main`, and the desktop executable matches the tested build.
