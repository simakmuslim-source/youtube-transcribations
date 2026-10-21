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
    query = urllib.parse.urlencode({"video_url": video_id})
    url = "https://api.freetranscriptapi.com/v1/transcript?" + query
    req = urllib.request.Request(url, headers={"User-Agent": "youtube-transcribations/1.0"})
    with urllib.request.urlopen(req, timeout=45) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    text = text_from_payload(payload)
    if not text:
        raise RuntimeError("API вернул ответ без текста: " + json.dumps(payload, ensure_ascii=False)[:600])
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
            print(f"  OK: {len(text)} chars", flush=True)
        except urllib.error.HTTPError as e:
            row["status"] = "error"
            row["error"] = f"HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:500]}"
            print("  ERROR: " + row["error"], flush=True)
            if e.code == 429:
                print("  Rate limited; waiting 30 minutes before continuing.", flush=True)
                time.sleep(1800)
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
