#!/usr/bin/env python3
"""Batch YouTube playlist transcription: captions first, optional faster-whisper fallback."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

try:
    import yt_dlp
except ImportError:
    print("Missing dependency: run pip install -r requirements.txt", file=sys.stderr)
    raise SystemExit(2)

_WHISPER_MODELS: dict[str, Any] = {}


def with_cookies(options: dict[str, Any]) -> dict[str, Any]:
    """Use a private Netscape-format cookie file when configured via environment."""
    cookie_file = os.environ.get("YOUTUBE_COOKIES_FILE")
    if cookie_file and Path(cookie_file).is_file():
        options["cookiefile"] = cookie_file
    return options


DEFAULT_PLAYLIST = (
    "https://youtube.com/playlist?"
    "list=PLsb6UHPckO4tUqnS53ef529-7neQ6btEz"
)
CAPTION_EXTENSIONS = {".vtt", ".srt", ".srv1", ".srv2", ".srv3", ".ttml"}


def safe_name(value: str, max_length: int = 120) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    value = re.sub(r"\s+", " ", value).strip(" ._")
    return (value or "video")[:max_length]


def video_id_from_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.hostname and "youtu.be" in parsed.hostname:
        return parsed.path.strip("/").split("/")[0]
    if parsed.path == "/watch":
        return parse_qs(parsed.query).get("v", [""])[0]
    parts = [part for part in parsed.path.split("/") if part]
    if parts and parts[0] in {"shorts", "live", "embed"} and len(parts) > 1:
        return parts[1]
    return ""


def clean_caption_file(path: Path) -> str:
    """Remove subtitle timing/markup while retaining spoken words and repeated captions."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines: list[str] = []
    previous = None
    for raw in text.splitlines():
        line = raw.strip().lstrip("\ufeff")
        if not line:
            continue
        if line.upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        if line.isdigit():
            continue
        if "-->" in line:
            continue
        if re.match(r"^\d{2}:\d{2}:\d{2}[,.]\d{3}", line):
            continue
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"&nbsp;", " ", line, flags=re.I)
        line = (line.replace("&amp;", "&").replace("&lt;", "<")
                    .replace("&gt;", ">").replace("&quot;", '"'))
        line = re.sub(r"\s+", " ", line).strip()
        if not line:
            continue
        # YouTube auto-captions often repeat the previous rolling caption.
        # Only remove an exact consecutive duplicate line, not repeated words in a sentence.
        if line == previous:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def get_playlist_entries(url: str, limit: int | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": True,
    }
    with yt_dlp.YoutubeDL(with_cookies(opts)) as ydl:
        info = ydl.extract_info(url, download=False)
    if not info:
        raise RuntimeError("Не удалось получить сведения о плейлисте.")
    entries = []
    for entry in info.get("entries") or []:
        if not entry:
            continue
        video_id = entry.get("id")
        video_url = entry.get("url") or entry.get("webpage_url")
        if not video_url or not str(video_url).startswith(("http://", "https://")):
            video_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else None
        if not video_url:
            continue
        entries.append({
            "id": str(video_id or video_id_from_url(video_url) or ""),
            "title": str(entry.get("title") or video_id or "Untitled video"),
            "url": video_url,
            "duration": entry.get("duration"),
            "playlist_index": entry.get("playlist_index"),
        })
    if limit is not None:
        entries = entries[:max(0, limit)]
    playlist_meta = {
        "title": info.get("title") or "YouTube playlist",
        "id": info.get("id"),
        "webpage_url": info.get("webpage_url") or url,
        "video_count_found": len(entries),
    }
    return playlist_meta, entries


def select_caption_language(available: dict[str, Any], requested: str) -> str | None:
    if not available:
        return None
    keys = list(available.keys())
    priorities = [requested, f"{requested}-orig", "ru", "ru-orig", "en", "en-orig"]
    for language in priorities:
        if language in available:
            return language
    # Use a language variant such as ru-xxxxx if exact language isn't available.
    for language in priorities:
        prefix = language.split("-")[0]
        match = next((key for key in keys if key == prefix or key.startswith(prefix + "-")), None)
        if match:
            return match
    return keys[0] if keys else None


def download_caption(video_url: str, video_id: str, temp_dir: Path, language: str) -> tuple[str, str] | None:
    inspect_opts = {
        "skip_download": True, "quiet": True, "no_warnings": True,
        "ignoreerrors": False,
    }
    with yt_dlp.YoutubeDL(with_cookies(inspect_opts)) as ydl:
        info = ydl.extract_info(video_url, download=False)
    if not info:
        return None
    manual = info.get("subtitles") or {}
    automatic = info.get("automatic_captions") or {}
    selected = select_caption_language(manual, language)
    source = "captions"
    if selected is None:
        selected = select_caption_language(automatic, language)
        source = "auto-captions"
    if selected is None:
        return None

    before = set(temp_dir.iterdir())
    opts = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": [selected],
        "subtitlesformat": "vtt/best",
        "outtmpl": str(temp_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video_url])
    candidates = [p for p in temp_dir.iterdir()
                  if p not in before and p.suffix.lower() in CAPTION_EXTENSIONS]
    if not candidates:
        candidates = [p for p in temp_dir.glob(f"{video_id}*")
                      if p.suffix.lower() in CAPTION_EXTENSIONS]
    if not candidates:
        return None
    chosen = max(candidates, key=lambda p: p.stat().st_mtime)
    transcript = clean_caption_file(chosen)
    if not transcript:
        return None
    return transcript, f"{source}:{selected}"


def transcribe_whisper(video_url: str, video_id: str, temp_dir: Path,
                       model_name: str, language: str | None) -> tuple[str, str]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Для Whisper установи зависимости: pip install -r requirements.txt"
        ) from exc

    audio_dir = temp_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    audio_path = audio_dir / f"{safe_name(video_id)}.%(ext)s"
    opts = {
        "format": "bestaudio/best",
        "outtmpl": str(audio_path),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "wav",
        }],
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.download([video_url])

    audio_files = list(audio_dir.glob(f"{safe_name(video_id)}*.wav"))
    if not audio_files:
        audio_files = [p for p in audio_dir.glob(f"{safe_name(video_id)}*") if p.is_file()]
    if not audio_files:
        raise RuntimeError("Не удалось скачать/подготовить аудио. Проверь установку FFmpeg.")
    model = _WHISPER_MODELS.get(model_name)
    if model is None:
        model = WhisperModel(model_name, device="auto", compute_type="int8")
        _WHISPER_MODELS[model_name] = model
    segments, info = model.transcribe(
        str(audio_files[0]),
        language=language or None,
        beam_size=5,
        vad_filter=False,
        condition_on_previous_text=True,
    )
    text_parts = [segment.text for segment in segments]
    transcript = "".join(text_parts).strip()
    if not transcript:
        raise RuntimeError("Whisper не вернул текст.")
    detected = getattr(info, "language", None) or language or "auto"
    return transcript, f"whisper:{model_name}:{detected}"


def write_report(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = ["playlist_index", "video_id", "title", "url", "status", "source",
              "transcript_file", "error"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def build_zip(transcripts_dir: Path, output_dir: Path, report_path: Path,
              metadata_path: Path) -> Path:
    archive_path = output_dir / "youtube-transcripts.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(transcripts_dir.glob("*.txt")):
            archive.write(path, arcname=f"transcripts/{path.name}")
        for path in (report_path, metadata_path):
            if path.exists():
                archive.write(path, arcname=path.name)
    return archive_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Batch transcription of YouTube playlists into plain-text files."
    )
    parser.add_argument("--playlist", default=DEFAULT_PLAYLIST, help="YouTube playlist URL")
    parser.add_argument("--mode", choices=("auto", "captions", "whisper"), default="auto",
                        help="auto: captions first, Whisper fallback; captions: no audio download")
    parser.add_argument("--output", default="output", help="Output directory (default: output)")
    parser.add_argument("--limit", type=int, default=None, help="Process only first N videos")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing non-empty TXT files")
    parser.add_argument("--whisper-model", default="small",
                        help="faster-whisper model size/name (default: small)")
    parser.add_argument("--language", default="ru",
                        help="Preferred caption/Whisper language (default: ru; use auto for detection)")
    parser.add_argument("--delay", type=float, default=1.0,
                        help="Delay in seconds between videos (default: 1)")
    args = parser.parse_args()

    output_dir = Path(args.output).resolve()
    transcripts_dir = output_dir / "transcripts"
    temp_dir = output_dir / "tmp"
    transcripts_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "report.csv"
    metadata_path = output_dir / "metadata.json"

    try:
        playlist_meta, entries = get_playlist_entries(args.playlist, args.limit)
    except Exception as exc:
        print(f"Не удалось прочитать плейлист: {exc}", file=sys.stderr)
        return 1

    metadata_path.write_text(
        json.dumps({"playlist": playlist_meta, "videos": entries},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Плейлист: {playlist_meta['title']}")
    print(f"Видео в очереди: {len(entries)}")
    rows: list[dict[str, Any]] = []
    failures = 0
    language = None if args.language.lower() == "auto" else args.language

    for position, video in enumerate(entries, start=1):
        video_id = video["id"] or video_id_from_url(video["url"]) or f"video-{position}"
        filename = f"{position:04d}_{safe_name(video_id, 32)}_{safe_name(video['title'], 90)}.txt"
        transcript_path = transcripts_dir / filename
        row = {
            "playlist_index": video.get("playlist_index") or position,
            "video_id": video_id,
            "title": video["title"],
            "url": video["url"],
            "status": "",
            "source": "",
            "transcript_file": str(Path("transcripts") / filename),
            "error": "",
        }
        if transcript_path.exists() and transcript_path.stat().st_size and not args.overwrite:
            row["status"] = "skipped-existing"
            rows.append(row)
            print(f"[{position}/{len(entries)}] Уже готово: {video['title']}")
            continue

        print(f"[{position}/{len(entries)}] {video['title']}")
        try:
            result = None
            if args.mode in ("auto", "captions"):
                result = download_caption(video["url"], video_id, temp_dir, args.language)
                if result:
                    transcript, source = result
            if not result and args.mode == "captions":
                raise RuntimeError("Доступные субтитры не найдены.")
            if not result:
                transcript, source = transcribe_whisper(
                    video["url"], video_id, temp_dir, args.whisper_model, language
                )
            transcript_path.write_text(transcript.rstrip() + "\n", encoding="utf-8")
            row["status"] = "ok"
            row["source"] = source
            print(f"  OK ({source}; {len(transcript)} символов)")
        except Exception as exc:
            failures += 1
            row["status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"[:1500]
            print(f"  ОШИБКА: {row['error']}", file=sys.stderr)
        rows.append(row)
        write_report(report_path, rows)
        if args.delay > 0 and position < len(entries):
            time.sleep(args.delay)

    write_report(report_path, rows)
    archive_path = build_zip(transcripts_dir, output_dir, report_path, metadata_path)
    succeeded = sum(row["status"] == "ok" for row in rows)
    skipped = sum(row["status"] == "skipped-existing" for row in rows)
    print("\nГотово.")
    print(f"Успешно: {succeeded}; пропущено ранее готовых: {skipped}; ошибок: {failures}")
    print(f"Отчёт: {report_path}")
    print(f"ZIP: {archive_path}")
    print("Временные файлы можно удалить после проверки архива: " + str(temp_dir))
    return 0 if failures == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
