#!/usr/bin/env python3
"""Download a page's video by trying download links first, then media fallbacks."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlparse
from urllib.request import Request, urlopen


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

VIDEO_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm", ".flv", ".ts")
HLS_EXTENSION = ".m3u8"
MEDIA_RE = re.compile(
    r"(?P<url>(?:(?:https?:)?//|/)[^'\"\s<>\\]+?"
    r"\.(?:mp4|m4v|mov|webm|flv|m3u8)(?:\?[^'\"\s<>\\]*)?)",
    re.IGNORECASE,
)
WINDOWS_RESERVED_CHARS = '<>:"/\\|?*'


@dataclass(frozen=True)
class Candidate:
    url: str
    kind: str
    source: str
    score: int = 0


@dataclass(frozen=True)
class HlsVariant:
    url: str
    bandwidth: int = 0
    resolution: str = ""


@dataclass(frozen=True)
class ResolvedVideo:
    url: str
    kind: str
    title: str = ""
    source: str = ""


class DownloadError(RuntimeError):
    """Raised when the program cannot find or save a usable video."""


class MediaHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.anchors: list[dict[str, object]] = []
        self.media_sources: list[tuple[str, str]] = []
        self.scripts: list[str] = []
        self.title = ""
        self._current_anchor: dict[str, object] | None = None
        self._in_script = False
        self._in_title = False
        self._script_parts: list[str] = []
        self._title_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()

        if tag == "a" and attr.get("href"):
            self._current_anchor = {
                "href": urljoin(self.base_url, attr["href"]),
                "attrs": attr,
                "text": [],
            }
        elif tag in {"video", "source", "embed", "iframe"} and attr.get("src"):
            self.media_sources.append((tag, urljoin(self.base_url, attr["src"])))
        elif tag == "script":
            self._in_script = True
            self._script_parts = []
            if attr.get("src"):
                self.media_sources.append(("script-src", urljoin(self.base_url, attr["src"])))
        elif tag == "title":
            self._in_title = True
            self._title_parts = []

    def handle_data(self, data: str) -> None:
        if self._current_anchor is not None:
            text_parts = self._current_anchor["text"]
            assert isinstance(text_parts, list)
            text_parts.append(data)
        if self._in_script:
            self._script_parts.append(data)
        if self._in_title:
            self._title_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._current_anchor is not None:
            text_parts = self._current_anchor["text"]
            assert isinstance(text_parts, list)
            self._current_anchor["text"] = " ".join("".join(text_parts).split())
            self.anchors.append(self._current_anchor)
            self._current_anchor = None
        elif tag == "script" and self._in_script:
            self.scripts.append("\n".join(self._script_parts))
            self._in_script = False
            self._script_parts = []
        elif tag == "title" and self._in_title:
            self.title = " ".join("".join(self._title_parts).split())
            self._in_title = False
            self._title_parts = []


def http_get(url: str, *, timeout: int = 30) -> bytes:
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def http_head_content_length(url: str, *, timeout: int = 12) -> int:
    req = Request(url, method="HEAD", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=timeout) as response:
            value = response.headers.get("Content-Length", "0")
            return int(value or 0)
    except (HTTPError, URLError, TimeoutError, ValueError):
        return 0


def decode_html(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def is_hls_url(url: str) -> bool:
    return HLS_EXTENSION in urlparse(url).path.lower()


def is_video_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    return path.endswith(VIDEO_EXTENSIONS) or is_hls_url(url)


def anchor_looks_like_download(anchor: dict[str, object]) -> bool:
    attrs = anchor.get("attrs", {})
    text = str(anchor.get("text", ""))
    if not isinstance(attrs, dict):
        attrs = {}
    signal = " ".join(
        [
            text,
            str(attrs.get("download", "")),
            str(attrs.get("class", "")),
            str(attrs.get("id", "")),
            str(attrs.get("title", "")),
            str(attrs.get("aria-label", "")),
        ]
    ).lower()
    return any(token in signal for token in ("下载", "download", "save", "保存"))


def dedupe_candidates(candidates: Iterable[Candidate]) -> list[Candidate]:
    seen: set[tuple[str, str]] = set()
    result: list[Candidate] = []
    for candidate in candidates:
        key = (candidate.kind, candidate.url)
        if key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def discover_candidates(html: str, page_url: str) -> list[Candidate]:
    parser = MediaHTMLParser(page_url)
    parser.feed(html)

    candidates: list[Candidate] = []

    for anchor in parser.anchors:
        href = str(anchor.get("href", ""))
        if not href or href.startswith(("javascript:", "mailto:")):
            continue
        if anchor_looks_like_download(anchor):
            kind = "hls" if is_hls_url(href) else "direct-video"
            candidates.append(Candidate(href, kind, "download-link", 100))
        elif is_video_url(href):
            kind = "hls" if is_hls_url(href) else "direct-video"
            candidates.append(Candidate(href, kind, "video-link", 60))

    for tag, src in parser.media_sources:
        if is_video_url(src):
            kind = "hls" if is_hls_url(src) else "direct-video"
            candidates.append(Candidate(src, kind, "media-tag", 50))

    for match in MEDIA_RE.finditer(html):
        media_url = urljoin(page_url, match.group("url"))
        kind = "hls" if is_hls_url(media_url) else "direct-video"
        candidates.append(Candidate(media_url, kind, "html-media-url", 40))

    video_center_id = extract_cctv_video_center_id(html)
    if video_center_id:
        candidates.append(Candidate(video_center_id, "cctv-video-center-id", "cctv-player", 30))

    return sorted(dedupe_candidates(candidates), key=lambda c: c.score, reverse=True)


def extract_cctv_video_center_id(html: str) -> str | None:
    patterns = [
        r"videoCenterId\s*[:=]\s*['\"]([0-9a-fA-F]{32})['\"]",
        r"videoCenterId['\"]?\s*[:=]\s*['\"]([0-9a-fA-F]{32})['\"]",
        r"pid\s*[:=]\s*['\"]([0-9a-fA-F]{32})['\"]",
    ]
    for pattern in patterns:
        match = re.search(pattern, html)
        if match:
            return match.group(1)
    return None


def parse_hls_master(manifest: str, manifest_url: str) -> list[HlsVariant]:
    variants: list[HlsVariant] = []
    pending: dict[str, str] | None = None

    for raw_line in manifest.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-STREAM-INF:"):
            pending = parse_hls_attributes(line.split(":", 1)[1])
        elif pending is not None and not line.startswith("#"):
            variants.append(
                HlsVariant(
                    urljoin(manifest_url, line),
                    int(pending.get("BANDWIDTH", "0") or 0),
                    pending.get("RESOLUTION", ""),
                )
            )
            pending = None

    return sorted(variants, key=lambda variant: variant.bandwidth, reverse=True)


def parse_hls_attributes(value: str) -> dict[str, str]:
    attrs: dict[str, str] = {}
    for part in value.split(","):
        if "=" not in part:
            continue
        key, attr_value = part.split("=", 1)
        attrs[key.strip().upper()] = attr_value.strip().strip('"')
    return attrs


def build_cctv_variant_candidates(hls_url: str) -> list[str]:
    clean_url = hls_url.split("?", 1)[0]
    match = re.search(r"/asp/(?P<prefix>[^/]+(?:/[^/]+)?)/main/(?P<rest>.+)/main\.m3u8$", clean_url)
    if not match:
        return [clean_url]

    prefix = match.group("prefix")
    rest = match.group("rest")
    root = clean_url.split("/asp/", 1)[0]
    candidates = []
    for bitrate in ("2000", "1200", "850", "450"):
        candidates.append(f"{root}/asp/{prefix}/{bitrate}/{rest}/{bitrate}.m3u8")
    return candidates


def select_best_cctv_playlist(hls_url: str) -> str:
    candidates = build_cctv_variant_candidates(hls_url)
    measured: list[tuple[int, int, str]] = []
    for index, candidate in enumerate(candidates):
        try:
            manifest = decode_html(http_get(candidate, timeout=12))
        except (HTTPError, URLError, TimeoutError):
            continue
        segment = first_hls_segment_url(manifest, candidate)
        size = http_head_content_length(segment) if segment else 0
        bitrate = bitrate_from_url(candidate)
        measured.append((size, bitrate - index, candidate))

    if measured:
        measured.sort(reverse=True)
        return measured[0][2]
    return hls_url


def bitrate_from_url(url: str) -> int:
    match = re.search(r"/(450|850|1200|2000)/", url)
    return int(match.group(1)) if match else 0


def first_hls_segment_url(manifest: str, manifest_url: str) -> str | None:
    for raw_line in manifest.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        return urljoin(manifest_url, line)
    return None


def resolve_page_video(page_url: str) -> tuple[ResolvedVideo, str]:
    raw = http_get(page_url)
    html = decode_html(raw)
    title = extract_title(html) or filename_from_url(page_url) or "downloaded_video"
    candidates = discover_candidates(html, page_url)
    if not candidates:
        return ResolvedVideo(page_url, "platform-video", title, "yt-dlp"), html

    errors: list[str] = []
    for candidate in candidates:
        try:
            if candidate.kind == "cctv-video-center-id":
                return resolve_cctv_video(candidate.url), html
            if candidate.kind == "hls":
                return ResolvedVideo(candidate.url, "hls", title, candidate.source), html
            return ResolvedVideo(candidate.url, "direct-video", title, candidate.source), html
        except DownloadError as exc:
            errors.append(str(exc))
            continue

    raise DownloadError("找到候选视频源，但都无法使用：" + "; ".join(errors))


def resolve_cctv_video(video_center_id: str) -> ResolvedVideo:
    api_url = f"https://vdn.apps.cntv.cn/api/getHttpVideoInfo.do?pid={video_center_id}"
    try:
        payload = json.loads(decode_html(http_get(api_url)))
    except (json.JSONDecodeError, HTTPError, URLError, TimeoutError) as exc:
        raise DownloadError(f"央视视频接口读取失败：{exc}") from exc

    title = payload.get("title") or payload.get("tag") or video_center_id
    hls_url = payload.get("hls_url") or payload.get("manifest", {}).get("hls_h5e_url")
    if not hls_url:
        raise DownloadError("央视接口没有返回可下载的 HLS 地址。")

    playlist = select_best_cctv_playlist(str(hls_url))
    return ResolvedVideo(playlist, "hls", str(title), "cctv-api")


def resolve_url(url: str) -> ResolvedVideo:
    if is_hls_url(url):
        return ResolvedVideo(url, "hls", filename_from_url(url), "input-url")
    if is_video_url(url):
        return ResolvedVideo(url, "direct-video", filename_from_url(url), "input-url")
    video, _html = resolve_page_video(url)
    return video


def download_resolved_video(video: ResolvedVideo, output_dir: Path, output_name: str | None = None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = sanitize_filename(output_name or video.title or filename_from_url(video.url) or "downloaded_video")

    if video.kind == "platform-video":
        return download_platform_video(video.url, output_dir, base_name)
    if video.kind == "hls":
        return download_hls(video.url, output_dir, base_name)
    return download_direct(video.url, output_dir, base_name)


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


def download_platform_video(
    page_url: str,
    output_dir: Path,
    base_name: str,
    *,
    ydl_factory: Callable[[dict[str, object]], object] | None = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise DownloadError("需要 FFmpeg 才能合并平台高清视频和音频。请先安装 FFmpeg 后重试。")

    if ydl_factory is None:
        try:
            from yt_dlp import YoutubeDL
        except ImportError as exc:
            raise DownloadError("程序缺少 yt-dlp 平台解析组件，请重新安装或重新打包。") from exc
        ydl_factory = YoutubeDL

    unique_name = unique_path(output_dir / f"{base_name}.mp4").stem
    options = build_yt_dlp_options(output_dir, unique_name, ffmpeg)
    finished_paths: list[Path] = []

    def remember_candidate(value: object) -> None:
        if value:
            finished_paths.append(Path(str(value)))

    def remember_finished(item: dict[str, object]) -> None:
        if item.get("status") != "finished":
            return
        remember_candidate(item.get("filepath"))
        info = item.get("info_dict")
        if isinstance(info, dict):
            remember_candidate(info.get("filepath"))

    options["postprocessor_hooks"] = [remember_finished]

    try:
        with ydl_factory(options) as ydl:
            info = ydl.extract_info(page_url, download=True)
    except Exception as exc:
        raise DownloadError(f"平台视频下载失败：{exc}") from exc

    candidate_paths = list(finished_paths)
    if isinstance(info, dict):
        remember_candidate(info.get("filepath"))
        remember_candidate(info.get("_filename"))
        requested_downloads = info.get("requested_downloads")
        if isinstance(requested_downloads, list):
            for item in requested_downloads:
                if isinstance(item, dict):
                    remember_candidate(item.get("filepath"))
        candidate_paths = list(finished_paths)

    candidate_paths.extend(output_dir.glob(f"{unique_name}.*"))

    seen: set[Path] = set()
    completed: list[Path] = []
    for candidate in candidate_paths:
        resolved = Path(candidate)
        if resolved in seen:
            continue
        seen.add(resolved)
        if not resolved.is_file():
            continue
        if resolved.suffix.lower() in {".part", ".ytdl"}:
            continue
        completed.append(resolved)

    mp4_candidates = [path for path in completed if path.suffix.lower() == ".mp4"]
    if mp4_candidates:
        return max(mp4_candidates, key=lambda path: path.stat().st_mtime_ns)
    if completed:
        return max(completed, key=lambda path: path.stat().st_mtime_ns)
    raise DownloadError("平台下载结束，但没有找到生成的视频文件。")


def download_direct(url: str, output_dir: Path, base_name: str) -> Path:
    suffix = Path(urlparse(url).path).suffix or ".mp4"
    output_path = unique_path(output_dir / f"{base_name}{suffix}")
    req = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=60) as response, output_path.open("wb") as target:
        total = int(response.headers.get("Content-Length", "0") or 0)
        downloaded = 0
        while True:
            chunk = response.read(1024 * 256)
            if not chunk:
                break
            target.write(chunk)
            downloaded += len(chunk)
            print_progress(downloaded, total)
    print()
    return output_path


def download_hls(playlist_url: str, output_dir: Path, base_name: str) -> Path:
    selected = select_best_hls_variant(playlist_url)
    output_path = unique_path(output_dir / f"{base_name}.mp4")
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        raise DownloadError(
            "需要 ffmpeg 才能把 m3u8/HLS 视频保存成 MP4。请运行 "
            "`pip install -r requirements.txt` 后重试，或安装系统 ffmpeg。"
        )

    command = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "warning",
        "-user_agent",
        USER_AGENT,
        "-i",
        selected,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    print(f"使用 HLS 源：{selected}")
    subprocess.run(command, check=True)
    return output_path


def select_best_hls_variant(playlist_url: str) -> str:
    try:
        manifest = decode_html(http_get(playlist_url, timeout=20))
    except (HTTPError, URLError, TimeoutError):
        return playlist_url

    variants = parse_hls_master(manifest, playlist_url)
    if variants:
        return variants[0].url
    return playlist_url


def find_ffmpeg() -> str | None:
    explicit = os.environ.get("FFMPEG")
    if explicit and Path(explicit).exists():
        return explicit

    system = shutil.which("ffmpeg")
    if system:
        return system

    try:
        import imageio_ffmpeg  # type: ignore

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def extract_title(html: str) -> str:
    parser = MediaHTMLParser("")
    parser.feed(html)
    title = parser.title
    for suffix in ("_央视网", "_CCTV", "- CCTV", "_新闻频道"):
        if suffix in title:
            title = title.split(suffix, 1)[0]
    return title.strip()


def filename_from_url(url: str) -> str:
    path_name = Path(urlparse(url).path).stem
    return sanitize_filename(unquote(path_name)) if path_name else ""


def sanitize_filename(value: str) -> str:
    cleaned = "".join("_" if char in WINDOWS_RESERVED_CHARS else char for char in value)
    cleaned = re.sub(r"[\x00-\x1f]+", "_", cleaned)
    cleaned = cleaned.strip().strip(".")
    return cleaned or "downloaded_video"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for index in range(2, 10_000):
        candidate = parent / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
    raise DownloadError(f"无法生成不重复的输出文件名：{path}")


def print_progress(done: int, total: int) -> None:
    if total > 0:
        percent = done / total * 100
        print(f"\r下载中：{done / 1024 / 1024:.1f}MB / {total / 1024 / 1024:.1f}MB ({percent:.1f}%)", end="")
    else:
        print(f"\r下载中：{done / 1024 / 1024:.1f}MB", end="")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从网页链接下载视频，优先使用页面下载按钮，找不到时解析视频源。")
    parser.add_argument("url", help="网页、mp4 或 m3u8 地址")
    parser.add_argument("-o", "--output-dir", default="downloads", help="输出目录，默认 downloads")
    parser.add_argument("-n", "--name", default=None, help="自定义输出文件名，不需要写扩展名")
    parser.add_argument("--dry-run", action="store_true", help="只解析视频源，不下载")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    try:
        video = resolve_url(args.url)
        print(f"标题：{video.title or '(未命名)'}")
        print(f"来源：{video.source}")
        print(f"类型：{video.kind}")
        print(f"视频源：{video.url}")
        if args.dry_run:
            return 0
        output_path = download_resolved_video(video, Path(args.output_dir), args.name)
        print(f"已保存：{output_path.resolve()}")
        return 0
    except subprocess.CalledProcessError as exc:
        print(f"ffmpeg 转封装失败，退出码：{exc.returncode}", file=sys.stderr)
        return 2
    except DownloadError as exc:
        print(f"下载失败：{exc}", file=sys.stderr)
        return 1
    except (HTTPError, URLError, TimeoutError) as exc:
        print(f"网络请求失败：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
