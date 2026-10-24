"""Shared download options for the Tkinter and Electron frontends (no UI imports)."""
from __future__ import annotations

import os
from urllib.parse import parse_qs, urlparse


def site_for_url(url: str) -> str | None:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    for site, domains in {
        "youtube": ("youtube.com", "youtu.be"),
        "bilibili": ("bilibili.com", "b23.tv"),
        "xiaohongshu": ("xiaohongshu.com", "xhslink.com"),
        "vimeo": ("vimeo.com",),
        "douyin": ("douyin.com", "iesdouyin.com", "amemv.com"),
    }.items():
        if any(host == domain or host.endswith("." + domain) for domain in domains):
            return site
    return None


def youtube_single_video(url: str) -> bool:
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path.rstrip("/")
        if host == "youtu.be":
            return bool(path.strip("/"))
        if host == "youtube.com" or host.endswith(".youtube.com"):
            if path == "/watch":
                return bool(parse_qs(parsed.query).get("v"))
            return any(path.startswith(prefix) for prefix in ("/shorts/", "/live/", "/embed/"))
    except ValueError:
        pass
    return False


def build_download_options(
    save_dir: str, *, height=None, format_name="mp4", audio_bitrate="192",
    subtitles=False, proxy="", js_runtime=None, logger=None,
    progress_hooks=(), postprocessor_hooks=(),
) -> dict:
    options = {
        "outtmpl": os.path.join(save_dir, "%(title)s [%(id)s].%(ext)s"),
        "progress_hooks": list(progress_hooks), "postprocessor_hooks": list(postprocessor_hooks),
        "noprogress": True, "quiet": True, "no_warnings": False, "ignoreerrors": False,
        "retries": 5, "fragment_retries": 20, "skip_unavailable_fragments": False,
        "concurrent_fragment_downloads": 2, "socket_timeout": 30,
        "retry_sleep_functions": {"fragment": lambda attempt: min(2 ** max(attempt - 1, 0), 20)},
    }
    if logger is not None:
        options["logger"] = logger
    if height == "audio":
        options.update(format="bestaudio/best", postprocessors=[{
            "key": "FFmpegExtractAudio", "preferredcodec": format_name,
            "preferredquality": str(audio_bitrate),
        }])
    else:
        selector = "bestvideo+bestaudio/best" if height is None else (
            f"bestvideo[height<={height}]+bestaudio/best[height<={height}]/best"
        )
        options.update(format=selector, merge_output_format=format_name)
    if subtitles:
        options.update(writesubtitles=True, writeautomaticsub=True,
                       subtitleslangs=["zh-Hans", "zh-CN", "zh", "en"], subtitlesformat="srt/best")
    if proxy.strip():
        options["proxy"] = proxy.strip()
    if js_runtime:
        options.update(js_runtimes={js_runtime: {}}, remote_components=["ejs:github"])
    return options
