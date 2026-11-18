from __future__ import annotations

import asyncio
import html
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

import yt_dlp

from .ui import Quality


logger = logging.getLogger(__name__)


SUPPORTED_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "youtu.be",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "www.x.com",
    "twitter.com",
    "www.twitter.com",
}


class MediaError(RuntimeError):
    pass


def _extractor_args() -> dict[str, dict[str, list[str]]]:
    provider_url = os.getenv("YTDLP_POT_PROVIDER_URL", "").strip()
    if not provider_url:
        return {}
    return {
        "youtube": {"player_client": ["mweb"]},
        "youtubepot-bgutilhttp": {"base_url": [provider_url.rstrip("/")]},
    }


@dataclass(frozen=True)
class MediaInfo:
    url: str
    title: str
    uploader: str
    duration: int
    width: int | None
    height: int | None
    platform: str
    automatic_best: bool
    qualities: list[Quality]
    raw: dict[str, Any]


def supported_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False
    return (
        parsed.scheme == "https" and (parsed.hostname or "").lower() in SUPPORTED_HOSTS
    )


def _estimate_by_height(formats: list[dict[str, Any]], height: int) -> int | None:
    video_sizes = [
        int(item.get("filesize") or item.get("filesize_approx") or 0)
        for item in formats
        if item.get("height") == height and item.get("vcodec") != "none"
    ]
    audio_sizes = [
        int(item.get("filesize") or item.get("filesize_approx") or 0)
        for item in formats
        if item.get("vcodec") == "none" and item.get("acodec") != "none"
    ]
    video = max(video_sizes, default=0)
    audio = max(audio_sizes, default=0)
    return video + audio if video else None


def quality_options(info: dict[str, Any]) -> list[Quality]:
    formats = info.get("formats") or []
    available = {
        int(item["height"])
        for item in formats
        if item.get("height") and item.get("vcodec") != "none"
    }
    standard = [2160, 1440, 1080, 720, 480, 360]
    return [
        Quality(height, _estimate_by_height(formats, height))
        for height in standard
        if height in available
    ]


def _platform(info: dict[str, Any]) -> str:
    extractor = str(info.get("extractor_key") or info.get("extractor") or "").lower()
    if "youtube" in extractor:
        return "YouTube"
    if "instagram" in extractor:
        return "Instagram"
    if "twitter" in extractor or extractor == "x":
        return "X"
    return extractor.title() or "Media"


def _automatic_best(url: str, info: dict[str, Any], platform: str) -> bool:
    if platform != "YouTube":
        return True
    width = int(info.get("width") or 0)
    height = int(info.get("height") or 0)
    duration = int(info.get("duration") or 0)
    return "/shorts/" in url.lower() or (height > width > 0 and 0 < duration <= 180)


def _source_has_audio(info: dict[str, Any]) -> bool:
    if info.get("acodec") not in {None, "none"}:
        return True
    return any(
        item.get("acodec") not in {None, "none"} for item in (info.get("formats") or [])
    )


def _audio_is_required(info: MediaInfo) -> bool:
    if _source_has_audio(info.raw):
        return True
    if info.platform != "Instagram":
        return False
    formats = info.raw.get("formats") or []
    return not formats or any(item.get("acodec") is None for item in formats)


def _format_selector(height: int | None, *, combined_only: bool = False) -> str:
    limit = f"[height<={height}]" if height else ""
    if combined_only:
        return f"b{limit}[vcodec!=none][acodec!=none]"
    return f"bv{limit}+ba/b{limit}"


def _audio_stream(path: Path) -> dict[str, Any] | None:
    try:
        result = subprocess.run(
            [
                os.getenv("FFPROBE_BIN", "ffprobe"),
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_name,profile,channels,sample_rate",
                "-of",
                "json",
                str(path),
            ],
            capture_output=True,
            check=False,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError("Audio verification could not run") from exc
    if result.returncode != 0:
        raise MediaError("Could not read the downloaded media streams")
    try:
        streams = json.loads(result.stdout).get("streams") or []
    except (ValueError, AttributeError) as exc:
        raise MediaError("Invalid audio verification result") from exc
    return streams[0] if streams else None


def _audio_codec(path: Path) -> str | None:
    stream = _audio_stream(path)
    return stream.get("codec_name") if stream else None


def _telegram_audio_compatible(stream: dict[str, Any] | None) -> bool:
    return bool(
        stream
        and stream.get("codec_name") == "aac"
        and stream.get("profile") == "LC"
        and 1 <= int(stream.get("channels") or 0) <= 2
    )


def _has_audio_stream(path: Path) -> bool:
    return _audio_codec(path) is not None


def _instagram_candidate_formats(info: MediaInfo, height: int | None) -> list[str]:
    if info.platform != "Instagram":
        return []
    formats = [
        item
        for item in (info.raw.get("formats") or [])
        if item.get("format_id")
        and item.get("url")
        and item.get("vcodec") != "none"
        and (not height or not item.get("height") or int(item["height"]) <= height)
    ]
    formats.sort(
        key=lambda item: (
            int(item.get("height") or 0),
            int(item.get("width") or 0),
            float(item.get("fps") or 0),
            float(item.get("tbr") or 0),
            int(item.get("filesize") or item.get("filesize_approx") or 0),
        ),
        reverse=True,
    )
    return list(dict.fromkeys(str(item["format_id"]) for item in formats))[:10]


def _normalize_audio_for_telegram(path: Path) -> Path:
    stream = _audio_stream(path)
    if stream is None:
        raise MediaError("Downloaded video has no audio track")
    if _telegram_audio_compatible(stream) and path.suffix.lower() == ".mp4":
        return path

    logger.info(
        "Converting audio codec=%s profile=%s channels=%s to AAC-LC stereo; video=copy",
        stream.get("codec_name"),
        stream.get("profile"),
        stream.get("channels"),
    )

    converted = path.with_name(f"{path.stem}.telegram.mp4")
    result = _run_audio_conversion(
        [
            os.getenv("FFMPEG_BIN", "ffmpeg"),
            "-y",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-sn",
            "-dn",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-profile:a",
            "aac_low",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-disposition:a:0",
            "default",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(converted),
        ]
    )
    if result.returncode != 0 or not converted.exists():
        converted.unlink(missing_ok=True)
        raise MediaError("Could not convert the source audio for Telegram")

    if not _telegram_audio_compatible(_audio_stream(converted)):
        converted.unlink(missing_ok=True)
        raise MediaError("Telegram-compatible audio validation failed")
    final_path = path.with_suffix(".mp4")
    converted.replace(final_path)
    if final_path != path:
        path.unlink(missing_ok=True)
    return final_path


def _run_audio_conversion(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command, capture_output=True, check=False, text=True, timeout=1200
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError("Audio conversion could not finish") from exc


def _download_selectors(info: MediaInfo, height: int | None) -> list[str]:
    selectors = [
        _format_selector(height),
        _format_selector(height, combined_only=True),
        *_instagram_candidate_formats(info, height),
    ]
    return list(dict.fromkeys(selectors))


def _instagram_public_media_urls(info: MediaInfo, height: int | None) -> list[str]:
    if info.platform != "Instagram":
        return []
    try:
        from parth_dl import get_info

        public_info = get_info(info.url, verbose=False)
    except Exception as exc:
        logger.warning(
            "Anonymous Instagram fallback unavailable: %s", type(exc).__name__
        )
        return []

    formats = [
        item
        for entry in (public_info.get("entries") or [])
        if entry.get("kind") == "video"
        for item in (entry.get("formats") or [])
        if item.get("url")
        and (not height or not item.get("height") or int(item["height"]) <= height)
    ]
    formats.sort(
        key=lambda item: (
            bool(item.get("has_audio")),
            int(item.get("height") or 0),
            int(item.get("width") or 0),
        ),
        reverse=True,
    )
    return list(dict.fromkeys(str(item["url"]) for item in formats))[:10]


def _download_attempts(info: MediaInfo, height: int | None):
    for selector in _download_selectors(info, height):
        yield info.url, selector, "native"
    for media_url in _instagram_public_media_urls(info, height):
        yield media_url, "best", "public-embed"


def _media_files(directory: Path) -> list[Path]:
    return sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file() and path.suffix not in {".part", ".ytdl", ".json"}
        ),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )


def _clear_job_files(directory: Path) -> None:
    for path in directory.iterdir():
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)


def probe_sync(url: str) -> MediaInfo:
    if not supported_url(url):
        raise MediaError("Unsupported link")
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "extractor_args": _extractor_args(),
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except yt_dlp.utils.DownloadError as exc:
        raise MediaError(str(exc)) from exc
    if not info:
        raise MediaError("No media found")
    platform = _platform(info)
    return MediaInfo(
        url=url,
        title=str(info.get("title") or "Untitled video"),
        uploader=str(info.get("uploader") or info.get("channel") or ""),
        duration=int(info.get("duration") or 0),
        width=int(info["width"]) if info.get("width") else None,
        height=int(info["height"]) if info.get("height") else None,
        platform=platform,
        automatic_best=_automatic_best(url, info, platform),
        qualities=quality_options(info),
        raw=info,
    )


async def probe(url: str) -> MediaInfo:
    return await asyncio.to_thread(probe_sync, url)


ProgressCallback = Callable[[int, int | None, float | None], Awaitable[None]]


def _download_sync(
    info: MediaInfo,
    height: int | None,
    directory: Path,
    max_bytes: int,
    cancel: threading.Event,
    loop: asyncio.AbstractEventLoop,
    progress: ProgressCallback,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    last_update = 0.0

    def hook(status: dict[str, Any]) -> None:
        nonlocal last_update
        if cancel.is_set():
            raise yt_dlp.utils.DownloadError("Download cancelled")
        downloaded = int(status.get("downloaded_bytes") or 0)
        if downloaded > max_bytes:
            raise yt_dlp.utils.DownloadError(
                "Selected quality exceeds the configured size limit"
            )
        now = time.monotonic()
        if status.get("status") == "downloading" and now - last_update >= 2:
            last_update = now
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            speed = status.get("speed")
            asyncio.run_coroutine_threadsafe(
                progress(downloaded, int(total) if total else None, speed), loop
            )

    base_options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "cookiefile": None,
        "cookiesfrombrowser": None,
        "extractor_args": _extractor_args(),
        "merge_output_format": "mp4",
        "outtmpl": str(directory / "%(id)s.%(ext)s"),
        "progress_hooks": [hook],
        "overwrites": True,
        "retries": 3,
        "fragment_retries": 3,
        "concurrent_fragment_downloads": 2,
    }
    audio_required = _audio_is_required(info)
    output: Path | None = None
    last_download_error: yt_dlp.utils.DownloadError | None = None
    for attempt, (target_url, selector, source) in enumerate(
        _download_attempts(info, height)
    ):
        if cancel.is_set():
            raise MediaError("Download cancelled")
        if attempt:
            _clear_job_files(directory)
        options = {
            **base_options,
            "format": selector,
        }
        try:
            logger.info(
                "Download attempt platform=%s source=%s format=%s",
                info.platform,
                source,
                selector,
            )
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(target_url, download=True)
        except yt_dlp.utils.DownloadError as exc:
            if cancel.is_set() or "configured size limit" in str(exc):
                raise MediaError(str(exc)) from exc
            last_download_error = exc
            continue
        files = _media_files(directory)
        if not files:
            continue
        candidate = files[0]
        candidate_audio = _audio_stream(candidate)
        if audio_required and candidate_audio is None:
            logger.warning(
                "Rejected silent rendition platform=%s source=%s format=%s",
                info.platform,
                source,
                selector,
            )
            continue
        output = (
            _normalize_audio_for_telegram(candidate) if candidate_audio else candidate
        )
        if output:
            logger.info(
                "Selected rendition platform=%s source=%s format=%s",
                info.platform,
                source,
                selector,
            )
            break
    if output is None:
        if last_download_error and not audio_required:
            raise MediaError(str(last_download_error)) from last_download_error
        raise MediaError("Instagram did not expose an audio rendition for this Reel")
    if output.stat().st_size > max_bytes:
        raise MediaError("Selected quality exceeds the configured size limit")
    return output


async def download(
    info: MediaInfo,
    height: int | None,
    directory: Path,
    max_bytes: int,
    cancel: threading.Event,
    progress: ProgressCallback,
) -> Path:
    loop = asyncio.get_running_loop()
    return await asyncio.to_thread(
        _download_sync, info, height, directory, max_bytes, cancel, loop, progress
    )


def cleanup(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def safe(value: str) -> str:
    return html.escape(value, quote=False)
