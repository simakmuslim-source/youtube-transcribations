#!/usr/bin/env python3
"""Conservative, deterministic light editing for generated YouTube transcripts.

Original transcript files are never modified. Only whitespace and spacing around
punctuation are normalized; words, word order, and punctuation marks are preserved.
"""
from __future__ import annotations

import csv
import re
import zipfile
from pathlib import Path

OUT = Path("output")
ORIGINALS = OUT / "transcripts"
EDITED = OUT / "edited_transcripts"

# Each rule is deliberately narrow. Every substitution is recorded in the change log.
RULES = [
    ("nonbreaking_space", "\u00a0", " ", "Replace non-breaking space with regular space"),
    ("tabs_to_spaces", r"\t+", " ", "Replace tabs with spaces"),
    ("multiple_spaces", r" {2,}", " ", "Collapse repeated horizontal spaces"),
    ("space_before_punctuation", r" +([,.;:!?…])", r"\1", "Remove space before punctuation"),
    ("space_after_opening_mark", r"([(\[{«]) +", r"\1", "Remove space after opening bracket/quote"),
    ("space_before_closing_mark", r" +([)\]}»])", r"\1", "Remove space before closing bracket/quote"),
    ("trailing_line_spaces", r"[ \t]+(?=\n|$)", "", "Remove trailing spaces at line ends"),
    ("excess_blank_lines", r"\n{3,}", "\n\n", "Limit blank lines to one"),
]


def apply_rules(text: str, filename: str) -> tuple[str, list[dict]]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    changes: list[dict] = []
    for rule_id, pattern, replacement, description in RULES:
        regex = re.compile(pattern)
        def replace(match, *, _rule=rule_id, _description=description, _replacement=replacement):
            before = match.group(0)
            after = match.expand(_replacement)
            if before != after:
                changes.append({
                    "file": filename,
                    "rule": _rule,
                    "description": _description,
                    "before": before.replace("\n", "\\n"),
                    "after": after.replace("\n", "\\n"),
                })
            return after
        text = regex.sub(replace, text)
    cleaned = text.strip()
    if cleaned != text:
        changes.append({
            "file": filename,
            "rule": "trim_outer_whitespace",
            "description": "Remove whitespace at the beginning/end of the file",
            "before": text[:80].replace("\n", "\\n") + ("…" if len(text) > 80 else ""),
            "after": cleaned[:80].replace("\n", "\\n") + ("…" if len(cleaned) > 80 else ""),
        })
    # A final newline is file formatting, not a change to spoken words.
    return cleaned + ("\n" if cleaned else ""), changes


def write_csv(path: Path, rows: list[dict], fields: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def rebuild_zip() -> None:
    archive_path = OUT / "youtube-transcripts.zip"
    candidates = []
    for folder, archive_folder in ((ORIGINALS, "transcripts"), (EDITED, "edited_transcripts")):
        if folder.exists():
            candidates.extend((path, f"{archive_folder}/{path.name}") for path in sorted(folder.glob("*.txt")))
    report_names = [
        "report.csv", "failed_videos.csv", "failed_videos.txt", "summary.json",
        "metadata.json", "editorial_summary.csv", "editorial_changes.csv",
    ]
    candidates.extend((OUT / name, name) for name in report_names if (OUT / name).is_file())
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, archive_name in candidates:
            archive.write(path, archive_name)


def main() -> int:
    if not ORIGINALS.exists():
        print(f"No original transcripts directory found: {ORIGINALS}")
        return 1
    EDITED.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict] = []
    change_rows: list[dict] = []

    for source in sorted(ORIGINALS.glob("*.txt")):
        original = source.read_text(encoding="utf-8", errors="replace")
        edited, changes = apply_rules(original, source.name)
        destination = EDITED / source.name
        destination.write_text(edited, encoding="utf-8")
        summary_rows.append({
            "file": source.name,
            "edited_file": str(destination),
            "status": "changed" if changes else "unchanged",
            "change_count": len(changes),
            "original_characters": len(original),
            "edited_characters": len(edited.rstrip("\n")),
            "editing_scope": "whitespace and punctuation spacing only",
        })
        for change in changes:
            change_rows.append({
                **change,
                "edited_file": str(destination),
            })

    write_csv(
        OUT / "editorial_summary.csv",
        summary_rows,
        ["file", "edited_file", "status", "change_count", "original_characters",
         "edited_characters", "editing_scope"],
    )
    write_csv(
        OUT / "editorial_changes.csv",
        change_rows,
        ["file", "edited_file", "rule", "description", "before", "after"],
    )
    rebuild_zip()
    print(
        f"Light editing complete: {len(summary_rows)} files; "
        f"{sum(row['change_count'] for row in summary_rows)} formatting changes. "
        "Original transcripts were not modified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
