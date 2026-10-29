#!/usr/bin/env python3
"""Super-hybrid YouTube playlist transcription: API captions, native captions, then Whisper."""
from __future__ import annotations

import csv
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

PLAYLIST_ID = "PLsb6UHPckO4tUqnS53ef529-7neQ6btEz"
PLAYLIST_TITLE = "ТОВАРНЫЕ ЗНАКИ"
PLAYLIST_URL = f"https://www.youtube.com/playlist?list={PLAYLIST_ID}"
ADDITIONAL_PLAYLISTS = [
    "https://www.youtube.com/playlist?list=PLKDgr33hAwrqJ72ch2oKaRaXVNyhjUpAt",
    "https://www.youtube.com/playlist?list=PLmyeVC0yzg1UCGKhRxAIvzwyiauZjXT-A",
    "https://www.youtube.com/playlist?list=PLHpFjahfyry8",  # fourth requested playlist
]
REQUEST_GAP_SECONDS = 185
API_MAX_ATTEMPTS = 2
API_RETRY_SECONDS = 60

VIDEOS = [
    ("tNiUSSZj2n4", "Бесплатная бригада #бизнес #shorts #shortvideo"),
    ("zxo7lBZI0jM", "Ботокс обойдется слишком дорого #бизнес #shorts #shortvideo"),
    ("czfMQSCb2YQ", "Ботокс на дому #бизнес #shorts #shortvideo"),
    ("-P1wT4pI0vo", "Какие слова нельзя использовать при регистрации товарного знака? Готовим заявку без ошибок"),
    ("GzokZAVbczo", "News March 2025"),
    ("9V47e2mPqi4", "Не используй в своем бренде иностранные слова"),
    ("2vgJZr2OPuc", "Как передается по наследству интеллектуальная собственность?"),
    ("WJ_Mnu9Jvk0", "Как передать или продать авторские права?"),
    ("LMtS6oBJYUw", "Новости ноябрь 2024"),
    ("a4Q153JFHg4", "Как передать или продать авторские права?"),
    ("ORaz2iKcXbk", "Новости за октябрь 2024 года"),
    ("L-KnwaauG_c", "Зачем регистрировать товарный знак?"),
    ("Z4hE7rCG9no", "Плюсы регистрации собственного бренда"),
    ("VEgh9bBZxno", "С октября 2024 г. использование сиротских произведений придется оплачивать"),
    ("srzSNxxRXGM", "Верховный суд обобщил практику по спорам о нарушении авторских прав в интернете"),
    ("pRESfK5DyBM", "Спорный интеллект: судебная практика"),
    ("LxZxxkiRPbA", "Авторские права на конференции"),
    ("i1fqznpS0LQ", "Новости февраль 2024"),
    ("YRd3v17SkxU", "Что поменять в товарном знаке нельзя и какие изменения можно внести?"),
    ("EoTgcYtNjRs", "Какие необычные товарные знаки бывают и зачем их регистрируют?"),
    ("iH9GsfGpRec", "Лицо компании: какие бренды изменили логотип в 2023 году?"),
    ("opzdZEOByPw", "Новости ноябрь 2023"),
    ("eEHR9C7-ARo", "Какие товарные знаки нельзя зарегистрировать?"),
    ("-OIjzYcFQM4", "Цвет как товарный знак: монополия или признак свободной конкуренции"),
    ("PSwo6IIC4qE", "Что делать, если получили претензию за использование чужого бренда"),
    ("ekRCROt5U3k", "Товарный знак. Как проверить, можно ли зарегистрировать бренд в Роспатенте?"),
    ("nebdKzwkfJo", "Расторжение лицензионного договора: юридическая консультация"),
    ("NdURxB3h-Ig", "Виды лицензионных договоров"),
    ("Kl_GHnO7d0E", "Претензия по агентскому договору: как составить?"),
    ("cQrYVpjV2eo", "Регистрация товарного знака самозанятыми"),
    ("dEBjt01f4vI", "Нарушение авторских прав: примеры, способы защиты, ответственность"),
    ("nY8ojyIqPH0", "Может ли быть одинаковое название у разных компаний?"),
    ("xbSuN3hDyjk", "Что такое фирменное наименование и каким оно должно быть?"),
]

FIELDS = [
    "playlist_index", "video_id", "title", "url", "status", "source",
    "transcript_file", "download_method", "attempted_at",
    "api_error", "captions_error", "pytubefix_error", "yt_dlp_error",
    "whisper_error", "error",
]
_WHISPER_MODEL = None
_API_RATE_LIMITED = False


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def safe_name(value: str) -> str:
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value)
    return re.sub(r"\s+", " ", value).strip(" ._")[:100] or "video"


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"


def text_from_payload(payload) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts = []
        for item in payload:
            if isinstance(item, str):
                if item.strip():
                    parts.append(item.strip())
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("transcript")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
        return "\n".join(parts).strip()
    if isinstance(payload, dict):
        for key in ("transcript", "text", "captions", "segments", "data"):
            if key in payload:
                result = text_from_payload(payload[key])
                if result:
                    return result
    return ""


def fetch_transcript_api(video_id: str) -> str:
    """Try the public transcript endpoint first; retry only transient errors."""
    global _API_RATE_LIMITED
    if _API_RATE_LIMITED:
        raise RuntimeError("Public transcript API skipped: rate limit was reached earlier in this run")
    query = urllib.parse.urlencode({"video_url": video_id})
    url = "https://api.freetranscriptapi.com/v1/transcript?" + query
    last_error = None
    for attempt in range(1, API_MAX_ATTEMPTS + 1):
        request = urllib.request.Request(
            url, headers={"User-Agent": "youtube-transcribations/2.0"}
        )
        try:
            with urllib.request.urlopen(request, timeout=75) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            transcript = text_from_payload(payload)
            if transcript:
                return transcript
            raise RuntimeError(
                "API returned no transcript text: "
                + json.dumps(payload, ensure_ascii=False)[:500]
            )
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
            if exc.code == 429:
                _API_RATE_LIMITED = True
                raise RuntimeError(
                    f"HTTP 429: public transcript API rate limit reached; "
                    f"disabled for the rest of this run: {detail}"
                ) from exc
            if not 500 <= exc.code <= 599:
                raise last_error
            wait = API_RETRY_SECONDS
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            wait = API_RETRY_SECONDS
        if attempt < API_MAX_ATTEMPTS:
            print(
                f"  API temporary error ({attempt}/{API_MAX_ATTEMPTS}): {last_error}; "
                f"retry in {wait}s",
                flush=True,
            )
            time.sleep(wait)
    raise RuntimeError(
        f"API failed after {API_MAX_ATTEMPTS} attempts: "
        f"{type(last_error).__name__}: {last_error}"
    )


def caption_items_to_text(items) -> str:
    parts = []
    for item in items:
        if isinstance(item, dict):
            part = item.get("text", "")
        else:
            part = getattr(item, "text", "")
        if part and str(part).strip():
            parts.append(str(part).strip())
    # No editorial corrections are applied; the text is taken from the source captions.
    return " ".join(parts).strip()


def fetch_youtube_captions(video_id: str) -> str:
    """Second source: YouTube's own captions, preferring Russian and then English."""
    from youtube_transcript_api import YouTubeTranscriptApi

    api = YouTubeTranscriptApi()
    try:
        fetched = api.fetch(video_id, languages=["ru", "en"])
        text = caption_items_to_text(fetched)
        if text:
            return text
    except Exception as preferred_error:
        print(f"  Preferred caption language lookup failed: {preferred_error}", flush=True)

    # If Russian/English captions were not found, try another available caption track.
    transcript_list = api.list(video_id)
    tracks = list(transcript_list)
    if not tracks:
        raise RuntimeError("YouTube returned no caption tracks")
    preferred = None
    for track in tracks:
        language_code = getattr(track, "language_code", "")
        if language_code in ("ru", "ru-RU", "en", "en-US"):
            preferred = track
            if language_code in ("ru", "ru-RU"):
                break
    selected = preferred or tracks[0]
    fetched = selected.fetch()
    text = caption_items_to_text(fetched)
    if not text:
        raise RuntimeError("YouTube caption track contained no text")
    return text


def download_audio_pytubefix(video_id: str, directory: Path) -> Path:
    from pytubefix import YouTube

    yt = YouTube(video_url(video_id))
    stream = yt.streams.filter(only_audio=True).first()
    if stream is None:
        raise RuntimeError("pytubefix: YouTube returned no audio-only stream")
    path = Path(stream.download(output_path=str(directory), filename="audio"))
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("pytubefix returned a missing or empty audio file")
    return path


def download_audio_ytdlp(video_id: str, directory: Path) -> Path:
    output_template = str(directory / "audio.%(ext)s")
    command = [
        sys.executable, "-m", "yt_dlp", "--no-playlist", "--no-warnings",
        "--retries", "2", "--fragment-retries", "2",
        "-f", "bestaudio/best", "-o", output_template, video_url(video_id),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=1200, check=False
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "yt-dlp exited unsuccessfully")[-1200:]
        raise RuntimeError(f"yt-dlp exited {result.returncode}: {details}")
    candidates = sorted(
        p for p in directory.glob("audio.*")
        if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    )
    if not candidates or candidates[0].stat().st_size == 0:
        raise RuntimeError("yt-dlp did not produce a non-empty audio file")
    return candidates[0]


def get_whisper_model():
    global _WHISPER_MODEL
    if _WHISPER_MODEL is None:
        from faster_whisper import WhisperModel
        print("  Loading faster-whisper small model (CPU/int8)...", flush=True)
        _WHISPER_MODEL = WhisperModel("small", device="cpu", compute_type="int8")
    return _WHISPER_MODEL


def fetch_with_whisper(video_id: str) -> tuple[str, str, str, str]:
    """Third source: download audio, using two downloaders, then local Whisper."""
    import_errors = []
    with tempfile.TemporaryDirectory(prefix="youtube-transcribations-") as tmp:
        directory = Path(tmp)
        audio_path = None
        method = ""
        pytubefix_error = ""
        yt_dlp_error = ""
        try:
            print("  Whisper fallback: trying pytubefix audio download...", flush=True)
            audio_path = download_audio_pytubefix(video_id, directory)
            method = "pytubefix"
        except Exception as exc:
            pytubefix_error = f"{type(exc).__name__}: {exc}"[:1200]
            print(f"  pytubefix download failed: {pytubefix_error}", flush=True)
            try:
                print("  Trying yt-dlp audio download...", flush=True)
                audio_path = download_audio_ytdlp(video_id, directory)
                method = "yt-dlp"
            except Exception as ytdlp_exc:
                yt_dlp_error = f"{type(ytdlp_exc).__name__}: {ytdlp_exc}"[:1200]
                raise RuntimeError(
                    "Audio download failed; pytubefix: "
                    + (pytubefix_error or "unknown error")
                    + "; yt-dlp: "
                    + (yt_dlp_error or "unknown error")
                ) from ytdlp_exc

        model = get_whisper_model()
        segments, info = model.transcribe(
            str(audio_path), beam_size=5, vad_filter=True
        )
        parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        text = " ".join(parts).strip()
        if not text:
            raise RuntimeError("Whisper finished but returned no speech text")
        print(
            f"  Whisper succeeded: {len(text)} chars; language={info.language}; "
            f"downloader={method}",
            flush=True,
        )
        return text, method, pytubefix_error, yt_dlp_error


def resolve_title_from_oembed(video_id: str, fallback_title: str) -> str:
    """Best-effort title lookup for the failure report; retain playlist title if blocked."""
    url = "https://www.youtube.com/oembed?" + urllib.parse.urlencode(
        {"url": video_url(video_id), "format": "json"}
    )
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "youtube-transcribations/2.0"}
        )
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        title = payload.get("title")
        return title.strip() if isinstance(title, str) and title.strip() else fallback_title
    except Exception:
        return fallback_title


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def persist_reports(out: Path, rows: list[dict]) -> None:
    """Persist an all-videos report and a title-rich list of incomplete videos."""
    write_csv(out / "report.csv", rows, FIELDS)
    incomplete = [row for row in rows if row.get("status") != "ok"]
    write_csv(out / "failed_videos.csv", incomplete, FIELDS)

    lines = [
        f"Плейлист: {PLAYLIST_TITLE}",
        f"URL: {PLAYLIST_URL}",
        f"Всего видео: {len(rows)}",
        f"Успешно обработано: {sum(row.get('status') == 'ok' for row in rows)}",
        f"Не завершено: {len(incomplete)}",
        "",
    ]
    if not incomplete:
        lines.append("Все видео обработаны успешно.")
    else:
        lines.append("Список видео, которые не удалось завершить на момент формирования отчёта:")
        lines.append("")
        for row in incomplete:
            index = row.get("playlist_index", "?")
            title = row.get("title") or "(название не определено)"
            status = row.get("status", "unknown")
            reason = row.get("error") or (
                "Видео ещё не было обработано (запуск остановлен или не дошёл до него)."
                if status in ("pending", "processing")
                else "Причина не записана."
            )
            lines.extend([
                f"{index}. {title}",
                f"   Статус: {status}",
                f"   ID: {row.get('video_id', '')}",
                f"   URL: {row.get('url', '')}",
                f"   Причина: {reason}",
                "",
            ])
    (out / "failed_videos.txt").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    counts = {}
    for row in rows:
        status = row.get("status", "unknown")
        counts[status] = counts.get(status, 0) + 1
    summary = {
        "playlist_title": PLAYLIST_TITLE,
        "playlist_id": PLAYLIST_ID,
        "playlist_url": PLAYLIST_URL,
        "updated_at_utc": now_utc(),
        "total_videos": len(rows),
        "status_counts": counts,
        "successful": counts.get("ok", 0),
        "incomplete": len(incomplete),
        "incomplete_videos": [
            {
                "playlist_index": row.get("playlist_index"),
                "title": row.get("title"),
                "video_id": row.get("video_id"),
                "url": row.get("url"),
                "status": row.get("status"),
                "error": row.get("error", ""),
            }
            for row in incomplete
        ],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    metadata = {
        "playlist": {
            "title": PLAYLIST_TITLE,
            "id": PLAYLIST_ID,
            "webpage_url": PLAYLIST_URL,
            "video_count_found": len(VIDEOS),
        },
        "videos": rows,
    }
    (out / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def discover_additional_playlist(url: str) -> list[tuple[str, str]]:
    """Read playlist entries without downloading video or audio files."""
    command = [
        sys.executable, "-m", "yt_dlp", "--flat-playlist",
        "--dump-single-json", "--skip-download", url,
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=300, check=False
    )
    if result.returncode != 0:
        details = (result.stderr or result.stdout or "yt-dlp failed")[-1200:]
        raise RuntimeError(f"Could not read additional playlist {url}: {details}")
    payload = json.loads(result.stdout)
    videos = []
    for entry in payload.get("entries") or []:
        if not isinstance(entry, dict):
            continue
        video_id = entry.get("id")
        title = entry.get("title") or video_id or "Untitled video"
        if video_id and re.fullmatch(r"[A-Za-z0-9_-]{11}", str(video_id)):
            videos.append((str(video_id), str(title)))
    return videos


def main() -> int:
    out = Path("output")
    transcripts_dir = out / "transcripts"
    transcripts_dir.mkdir(parents=True, exist_ok=True)

    all_videos = list(VIDEOS)
    seen_ids = {video_id for video_id, _ in all_videos}
    for additional_url in ADDITIONAL_PLAYLISTS:
        print(f"Reading additional playlist: {additional_url}", flush=True)
        try:
            additional_videos = discover_additional_playlist(additional_url)
            added = 0
            for video_id, title in additional_videos:
                if video_id not in seen_ids:
                    all_videos.append((video_id, title))
                    seen_ids.add(video_id)
                    added += 1
            print(f"  Added {added} unique videos from additional playlist.", flush=True)
        except Exception as exc:
            print(f"  WARNING: additional playlist could not be listed: {exc}", flush=True)

    rows = []
    for index, (video_id, title) in enumerate(all_videos, 1):
        output_path = transcripts_dir / f"{index:04d}_{video_id}_{safe_name(title)}.txt"
        rows.append({
            "playlist_index": index,
            "video_id": video_id,
            "title": title,
            "url": video_url(video_id),
            "status": "pending",
            "source": "",
            "transcript_file": str(output_path),
            "download_method": "",
            "attempted_at": "",
            "api_error": "",
            "captions_error": "",
            "pytubefix_error": "",
            "yt_dlp_error": "",
            "whisper_error": "",
            "error": "",
        })
    # Mark recovered transcripts before writing reports, including files outside this batch.
    for row in rows:
        existing = Path(row["transcript_file"])
        if existing.exists() and existing.stat().st_size > 0:
            row["status"] = "ok"
            row["source"] = "cached-transcript"

    persist_reports(out, rows)

    total = len(rows)
    batch_start = max(1, int(os.environ.get("BATCH_START", "1")))
    batch_size = max(1, int(os.environ.get("BATCH_SIZE", str(total))))
    batch_start_index = batch_start - 1
    batch_end_index = min(total, batch_start_index + batch_size)
    print(
        f"Processing playlist indexes {batch_start_index + 1}-{batch_end_index} "
        f"of {total}; existing transcripts will be preserved.",
        flush=True,
    )
    for index, row in enumerate(rows):
        if not (batch_start_index <= index < batch_end_index):
            continue
        video_id = row["video_id"]
        title = row["title"]
        output_path = Path(row["transcript_file"])
        print(f"[{index + 1}/{total}] {title}", flush=True)
        if output_path.exists() and output_path.stat().st_size > 0:
            row["status"] = "ok"
            row["source"] = "cached-transcript"
            print("  SKIP: preserving an existing transcript", flush=True)
            persist_reports(out, rows)
            continue
        row["status"] = "processing"
        row["attempted_at"] = now_utc()
        persist_reports(out, rows)

        try:
            # Reuse a transcript restored by GitHub Actions cache on reruns.
            if output_path.exists() and output_path.stat().st_size > 0:
                row["status"] = "ok"
                row["source"] = "cached-transcript"
                print("  SKIP: reusing transcript from cache", flush=True)
            else:
                try:
                    text = fetch_transcript_api(video_id)
                    row["source"] = "freetranscriptapi"
                except Exception as api_error:
                    row["api_error"] = f"{type(api_error).__name__}: {api_error}"[:1500]
                    print(f"  Primary transcript API failed: {row['api_error']}", flush=True)
                    try:
                        text = fetch_youtube_captions(video_id)
                        row["source"] = "youtube-transcript-api"
                    except Exception as captions_error:
                        row["captions_error"] = (
                            f"{type(captions_error).__name__}: {captions_error}"
                        )[:1500]
                        print(f"  YouTube captions failed: {row['captions_error']}", flush=True)
                        try:
                            text, method, pytubefix_error, yt_dlp_error = fetch_with_whisper(video_id)
                            row["source"] = "faster-whisper-small"
                            row["download_method"] = method
                            row["pytubefix_error"] = pytubefix_error
                            row["yt_dlp_error"] = yt_dlp_error
                        except Exception as whisper_error:
                            row["whisper_error"] = (
                                f"{type(whisper_error).__name__}: {whisper_error}"
                            )[:1800]
                            if "pytubefix:" in str(whisper_error):
                                details = str(whisper_error)
                                match = re.search(r"pytubefix: (.*?); yt-dlp: (.*)", details, re.S)
                                if match:
                                    row["pytubefix_error"] = match.group(1)[:1200]
                                    row["yt_dlp_error"] = match.group(2)[:1200]
                            raise RuntimeError("All transcription methods failed")
                if not isinstance(text, str) or not text.strip():
                    raise RuntimeError("Selected provider returned an empty transcript")
                output_path.write_text(text.rstrip() + "\n", encoding="utf-8")
                row["status"] = "ok"
                print(
                    f"  OK via {row['source']}: {len(text)} characters",
                    flush=True,
                )
        except Exception as error:
            row["status"] = "error"
            row["title"] = resolve_title_from_oembed(video_id, row["title"])
            diagnostics = [
                f"API: {row['api_error']}" if row["api_error"] else "",
                f"YouTube captions: {row['captions_error']}" if row["captions_error"] else "",
                f"Audio: pytubefix: {row['pytubefix_error']}" if row["pytubefix_error"] else "",
                f"Audio: yt-dlp: {row['yt_dlp_error']}" if row["yt_dlp_error"] else "",
                f"Whisper: {row['whisper_error']}" if row["whisper_error"] else "",
                f"Final error: {type(error).__name__}: {error}",
            ]
            row["error"] = " | ".join(part for part in diagnostics if part)[:5000]
            print(f"  FAILED: {row['title']} ({video_id})", flush=True)
            print(f"  Reason: {row['error']}", flush=True)
        finally:
            persist_reports(out, rows)

        if index < batch_end_index - 1:
            # The public transcript endpoint documents a 20-requests/hour limit.
            time.sleep(REQUEST_GAP_SECONDS)

    # Build one ZIP containing successful transcripts and the all-failures report.
    persist_reports(out, rows)
    archive_path = out / "youtube-transcripts.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(transcripts_dir.glob("*.txt")):
            archive.write(path, f"transcripts/{path.name}")
        for name in (
            "report.csv", "failed_videos.csv", "failed_videos.txt",
            "summary.json", "metadata.json",
        ):
            path = out / name
            if path.exists():
                archive.write(path, name)

    successful = sum(row["status"] == "ok" for row in rows)
    failed = [row for row in rows if row["status"] != "ok"]
    print(
        f"Finished: {successful}/{total} successful; {len(failed)} incomplete. "
        "The ZIP contains failed_videos.txt with titles, links, IDs, and reasons.",
        flush=True,
    )
    # Return zero so GitHub uploads transcripts and the failure report even if some videos fail.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
