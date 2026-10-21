#!/usr/bin/env python3
"""Fetch public YouTube captions through a public transcript endpoint.

This is a fallback for GitHub runners that YouTube blocks directly. It does not
use account cookies. The endpoint is third-party and may rate-limit requests.
"""
from __future__ import annotations
import csv, json, re, time, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path

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

def safe_name(s: str) -> str:
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", s)
    return re.sub(r"\s+", " ", s).strip(" ._")[:100] or "video"

def text_from_payload(payload):
    if isinstance(payload, str):
        return payload.strip()
    if isinstance(payload, list):
        parts = []
        for item in payload:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                t = item.get("text") or item.get("content") or item.get("transcript")
                if isinstance(t, str) and t.strip():
                    parts.append(t.strip())
        return "\n".join(parts).strip()
    if isinstance(payload, dict):
        for key in ("transcript", "text", "captions", "segments", "data"):
            if key in payload:
                found = text_from_payload(payload[key])
                if found:
                    return found
    return ""

def fetch_transcript(video_id: str):
    """Fetch a transcript, retrying transient failures without retrying permanent 4xx errors."""
    query = urllib.parse.urlencode({"video_url": video_id})
    url = "https://api.freetranscriptapi.com/v1/transcript?" + query
    last_error = None
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
        req = urllib.request.Request(url, headers={"User-Agent": "youtube-transcribations/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=90) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
            text = text_from_payload(payload)
            if not text:
                raise RuntimeError("API вернул ответ без текста: " + json.dumps(payload, ensure_ascii=False)[:600])
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="replace")[:500]
            last_error = RuntimeError(f"HTTP {e.code}: {detail}")
            # Missing videos/transcripts and other client errors won't improve on retry.
            if e.code != 429 and not 500 <= e.code <= 599:
                raise last_error
            if e.code == 429:
                wait = 1800
            else:
                wait = 185
        except (TimeoutError, urllib.error.URLError, json.JSONDecodeError) as e:
            last_error = e
            wait = 185
        if attempt < max_attempts:
            print(f"  Temporary failure (attempt {attempt}/{max_attempts}): {last_error}; retrying in {wait}s", flush=True)
            time.sleep(wait)
    raise RuntimeError(f"Failed after {max_attempts} attempts: {type(last_error).__name__}: {last_error}")

def fetch_with_whisper(video_id: str) -> str:
    """Fallback: download audio and transcribe it locally with faster-whisper."""
    import tempfile
    from pytubefix import YouTube
    from faster_whisper import WhisperModel

    print("  Trying Whisper fallback: downloading audio...", flush=True)
    with tempfile.TemporaryDirectory() as temp_dir:
        yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
        stream = yt.streams.filter(only_audio=True).first()
        if stream is None:
            raise RuntimeError("YouTube не предоставил аудиопоток")
        audio_path = stream.download(output_path=temp_dir, filename="audio")
        print("  Audio downloaded; loading Whisper small model on CPU...", flush=True)
        model = WhisperModel("small", device="cpu", compute_type="int8")
        segments, info = model.transcribe(audio_path, beam_size=5, vad_filter=True)
        parts = [segment.text.strip() for segment in segments if segment.text.strip()]
        text = " ".join(parts).strip()
        if not text:
            raise RuntimeError("Whisper не распознал речь")
        print(f"  Whisper fallback OK: {len(text)} chars; language={info.language}", flush=True)
        return text

def main():
    out = Path("output")
    transcripts = out / "transcripts"
    transcripts.mkdir(parents=True, exist_ok=True)
    rows = []
    total = len(VIDEOS)
    for i, (video_id, title) in enumerate(VIDEOS, 1):
        path = transcripts / f"{i:04d}_{video_id}_{safe_name(title)}.txt"
        row = {"playlist_index": i, "video_id": video_id, "title": title,
               "url": f"https://www.youtube.com/watch?v={video_id}",
               "status": "", "source": "freetranscriptapi", "transcript_file": str(path),
               "error": ""}
        print(f"[{i}/{total}] {title}", flush=True)
        try:
            text = fetch_transcript(video_id)
            path.write_text(text.rstrip() + "\n", encoding="utf-8")
            row["status"] = "ok"
            print(f"  OK via public transcript API: {len(text)} chars", flush=True)
        except Exception as api_error:
            print(f"  Public transcript API failed: {type(api_error).__name__}: {api_error}", flush=True)
            try:
                text = fetch_with_whisper(video_id)
                path.write_text(text.rstrip() + "\n", encoding="utf-8")
                row["status"] = "ok"
                row["source"] = "faster-whisper-small"
                row["error"] = f"Primary API failed; recovered with Whisper: {type(api_error).__name__}: {api_error}"[:1000]
            except Exception as whisper_error:
                row["status"] = "error"
                row["error"] = (
                    f"API: {type(api_error).__name__}: {api_error}; "
                    f"Whisper: {type(whisper_error).__name__}: {whisper_error}"
                )[:1500]
                print("  ERROR: " + row["error"], flush=True)
        except Exception as e:
            row["status"] = "error"
            row["error"] = f"{type(e).__name__}: {e}"[:1000]
            print("  ERROR: " + row["error"], flush=True)
        rows.append(row)
        with (out / "report.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(row.keys()))
            w.writeheader()
            w.writerows(rows)
        if i < total:
            # Public endpoint documents a limit of 20 requests/hour per IP.
            time.sleep(185)
    metadata = {"playlist": {"title": "ТОВАРНЫЕ ЗНАКИ",
        "id": "PLsb6UHPckO4tUqnS53ef529-7neQ6btEz",
        "webpage_url": "https://www.youtube.com/playlist?list=PLsb6UHPckO4tUqnS53ef529-7neQ6btEz",
        "video_count_found": total},
        "videos": [{"id": vid, "title": title, "url": f"https://www.youtube.com/watch?v={vid}",
                    "playlist_index": i} for i, (vid, title) in enumerate(VIDEOS, 1)]}
    (out / "metadata.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(out / "youtube-transcripts.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(transcripts.glob("*.txt")):
            z.write(p, f"transcripts/{p.name}")
        for p in (out / "report.csv", out / "metadata.json"):
            if p.exists(): z.write(p, p.name)
    ok = sum(r["status"] == "ok" for r in rows)
    print(f"Finished: {ok}/{total} transcripts. ZIP: output/youtube-transcripts.zip", flush=True)
    # Return success so the artifact is uploaded even if some videos have no captions.
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
