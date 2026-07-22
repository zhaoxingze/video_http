from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse


TRUSTED_MEDIA_DOMAINS = (
    "meeting.tencent.com",
    "myqcloud.com",
    "qcloud.com",
    "qq.com",
)
TENCENT_WEBVIEW_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
POLL_MEDIA_SCRIPT = r"""
(() => {
  const videos = Array.from(document.querySelectorAll('video'));
  const video = videos.find((item) => item.currentSrc || item.src);
  const titleCandidates = Array.from(
    document.querySelectorAll('[class*="_subject__"], [class*="title-with-edit"]')
  )
    .map((item) => (item.textContent || '').trim())
    .filter((value) => value && value.length <= 180 && !value.startsWith('返回'));
  return {
    media_url: video ? (video.currentSrc || video.src || '') : '',
    title: titleCandidates[0] || document.title || '',
    page_url: location.href,
  };
})()
"""


@dataclass(frozen=True)
class TencentMeetingMedia:
    media_url: str
    title: str = ""
    cookie_header: str = ""


def _hostname_matches(hostname: str | None, domain: str) -> bool:
    if not hostname:
        return False
    normalized = hostname.rstrip(".").lower()
    return normalized == domain or normalized.endswith(f".{domain}")


def validate_tencent_media_url(value: str) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise ValueError("Tencent Meeting returned a non-HTTPS media URL.")
    if not any(_hostname_matches(parsed.hostname, domain) for domain in TRUSTED_MEDIA_DOMAINS):
        raise ValueError("Tencent Meeting returned an untrusted media host.")
    if not parsed.path.lower().endswith((".mp4", ".m4v", ".mov", ".webm")):
        raise ValueError("Tencent Meeting did not return a downloadable video URL.")
    return url


def normalize_tencent_title(value: str) -> str:
    title = " ".join(str(value or "").split())
    for suffix in (" - 腾讯会议", " | 腾讯会议", " - Tencent Meeting"):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return "" if title in {"录制文件", "腾讯会议"} else title


def cookie_header_from_browser(value: object, media_hostname: str | None = None) -> str:
    if not isinstance(value, list):
        return ""
    pairs: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        cookie_value = str(item.get("value") or "")
        domain = str(item.get("domain") or "").strip().lstrip(".")
        if domain and not _hostname_matches(media_hostname, domain):
            continue
        if name:
            pairs.append(f"{name}={cookie_value}")
    return "; ".join(pairs)


def serialize_webview_cookies(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    pairs: list[dict[str, str]] = []
    for cookie in value:
        if not isinstance(cookie, dict):
            continue
        if cookie.get("name"):
            pair = {
                "name": str(cookie["name"]),
                "value": str(cookie.get("value") or ""),
            }
            if cookie.get("domain"):
                pair["domain"] = str(cookie["domain"])
            pairs.append(pair)
            continue
        for name, morsel in cookie.items():
            cookie_value = getattr(morsel, "value", morsel)
            if name:
                pair = {"name": str(name), "value": str(cookie_value or "")}
                cookie_domain = (
                    str(morsel.get("domain") or "") if hasattr(morsel, "get") else ""
                )
                if cookie_domain:
                    pair["domain"] = cookie_domain
                pairs.append(pair)
    return pairs


def parse_browser_result(raw: str) -> TencentMeetingMedia:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("Tencent Meeting browser result is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise ValueError("Tencent Meeting browser result has an invalid format.")
    media_url = validate_tencent_media_url(str(payload.get("media_url") or ""))
    return TencentMeetingMedia(
        media_url=media_url,
        title=normalize_tencent_title(str(payload.get("title") or "")),
        cookie_header=cookie_header_from_browser(
            payload.get("cookies"),
            urlparse(media_url).hostname,
        ),
    )


def browser_helper_command(page_url: str, result_path: Path) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--tencent-browser-helper", page_url, str(result_path)]
    return [
        sys.executable,
        str(Path(__file__).with_name("app_gui.py")),
        "--tencent-browser-helper",
        page_url,
        str(result_path),
    ]


def resolve_tencent_media_with_browser(
    page_url: str,
    *,
    runner: Callable[..., object] = subprocess.run,
    temp_dir: Path | None = None,
) -> TencentMeetingMedia:
    working_dir = temp_dir or Path(tempfile.gettempdir())
    working_dir.mkdir(parents=True, exist_ok=True)
    result_path = working_dir / f"video-downloader-tencent-{uuid.uuid4().hex}.json"
    command = browser_helper_command(page_url, result_path)
    try:
        completed = runner(command, check=False)
        if getattr(completed, "returncode", 1) != 0 or not result_path.is_file():
            raise RuntimeError(
                "Tencent Meeting login window was closed before a playable video was found."
            )
        return parse_browser_result(result_path.read_text(encoding="utf-8"))
    finally:
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass


def tencent_webview_storage_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "VideoDownloaderApp" / "TencentMeetingWebView"
    return Path.home() / ".video_downloader" / "TencentMeetingWebView"


def run_tencent_browser_helper(
    page_url: str,
    result_path: Path,
    *,
    webview_module: object | None = None,
    poll_interval: float = 0.8,
) -> int:
    if webview_module is None:
        try:
            import webview as webview_module
        except ImportError:
            return 3
    webview = webview_module

    result_path.parent.mkdir(parents=True, exist_ok=True)
    storage_path = tencent_webview_storage_path()
    storage_path.mkdir(parents=True, exist_ok=True)
    found_media = False
    window_closed = threading.Event()
    window = webview.create_window(
        "腾讯会议登录与视频读取 - 网页视频下载器",
        page_url,
        width=1180,
        height=780,
        min_size=(900, 620),
        background_color="#F4F7FB",
    )
    window.events.closed += window_closed.set

    def monitor_media() -> None:
        nonlocal found_media
        while not found_media and not window_closed.is_set():
            try:
                payload = window.evaluate_js(POLL_MEDIA_SCRIPT)
            except Exception:
                window_closed.wait(poll_interval)
                continue
            if isinstance(payload, dict) and payload.get("media_url"):
                try:
                    browser_cookies = window.get_cookies() or []
                except Exception:
                    browser_cookies = []
                try:
                    payload["cookies"] = serialize_webview_cookies(browser_cookies)
                    media = parse_browser_result(json.dumps(payload, ensure_ascii=False))
                except ValueError:
                    window_closed.wait(poll_interval)
                    continue
                result_path.write_text(
                    json.dumps(
                        {
                            "media_url": media.media_url,
                            "title": media.title,
                            "cookies": payload.get("cookies", []),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                found_media = True
                window.destroy()
                return
            window_closed.wait(poll_interval)

    webview.start(
        monitor_media,
        private_mode=False,
        storage_path=str(storage_path),
        user_agent=TENCENT_WEBVIEW_USER_AGENT,
    )
    return 0 if found_media and result_path.is_file() else 2
