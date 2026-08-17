"""Audit repetitive prose across exported chapters.

Scans data/exports/*.txt (chapters >= MIN_CHARS), reports duplicate-sentence
detection, fix_repetitive_text trims and repeated-phrase extraction, and
writes original/fixed pairs for suspicious files under data/exports/_audit/.

Usage:
    python scripts/audit_repetition.py            # full scan, report to stdout
    python scripts/audit_repetition.py --out x    # also write detailed report to x

Designed as a cheap regression oracle: run it on fresh exports after a
writer/template change to confirm no false positives on normal prose.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from novel_agent.domain.text_quality import (
    find_duplicate_sentences,
    find_repeated_phrases,
    fix_repetitive_text,
)

EXPORTS = Path("data/exports")
MIN_CHARS = 1500
MAX_FILES = 200
SEPARATOR = re.compile(r"^-{3,}$", re.MULTILINE)


def split_chapters(raw: str) -> list[str]:
    parts = re.split(r"\n\s*第\d+章\s*", raw)
    return [p.strip() for p in parts if len(p.strip()) >= MIN_CHARS]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        help="also append a UTF-8 detail report to this path (default: none)",
    )
    args = parser.parse_args()

    if not EXPORTS.is_dir():
        print(f"exports dir not found: {EXPORTS}", file=sys.stderr)
        return 2

    files = sorted(p for p in EXPORTS.glob("*.txt") if p.stat().st_size >= MIN_CHARS)
    files = files[:MAX_FILES]
    out_fh = open(args.out, "a", encoding="utf-8") if args.out else None

    total_chapters = total_dups = total_trimmed = 0
    for path in files:
        raw = path.read_text(encoding="utf-8")
        chapters = split_chapters(raw)
        if not chapters and len(raw.strip()) >= MIN_CHARS:
            chapters = [raw.strip()]
        line = f"== {path.name}: {len(chapters)} chapter(s)"
        print(line)
        if out_fh:
            out_fh.write(line + "\n")
        for ci, chapter in enumerate(chapters):
            body = SEPARATOR.sub("", chapter)
            total_chapters += 1
            dups = find_duplicate_sentences(body)
            fixed = fix_repetitive_text(body)
            phrases = find_repeated_phrases(body)
            total_dups += len(dups)
            if fixed.truncated:
                total_trimmed += 1
            tag = f"  ch{ci + 1}: {len(body)} chars, dups={len(dups)}, trimmed={fixed.truncated}"
            if fixed.truncated:
                cut = 1.0 - fixed.final_len / fixed.original_len if fixed.original_len else 0.0
                tag += f" (cut {cut:.2%}, {fixed.reason})"
            tag += f", top_phrases={len(phrases)}"
            print(tag)
            if out_fh:
                out_fh.write(tag + "\n")
                for d in dups:
                    out_fh.write(f"    DUP: {d[:80]}\n")
                for p in phrases:
                    out_fh.write(f"    PHRASE: {p[:60]}\n")

    summary = (
        f"\nsummary: files={len(files)} chapters={total_chapters} "
        f"dups={total_dups} trimmed={total_trimmed}"
    )
    print(summary)
    if out_fh:
        out_fh.write(summary + "\n")
        out_fh.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())