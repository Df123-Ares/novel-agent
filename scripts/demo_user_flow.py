#!/usr/bin/env python3
"""Demo the user flow: create → plan → write N chapters → complete → export txt."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from novel_agent.application.workflows import book_flow
from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, session_scope
from novel_agent.infrastructure.persistence.fts import search_fts
from novel_agent.settings import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description="Novel-Agent user-flow demo")
    parser.add_argument(
        "--max-chapters",
        type=int,
        default=3,
        help="How many chapters to write after planning (default: 3)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Write all planned chapters then complete+export (overrides --max-chapters)",
    )
    parser.add_argument(
        "--complete",
        action="store_true",
        help="After writing, mark complete and export txt if all chapters confirmed",
    )
    args = parser.parse_args()

    settings = ensure_data_dirs(get_settings())
    create_all_tables(get_engine(settings))

    with session_scope(settings) as session:
        print("== 1. 创作卡片 ==")
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title="雾港回声",
                genre="都市悬疑",
                style="网文通俗",
                length="short",
                perspective="第三人称",
                tone="暗黑",
                premise="港口档案馆夜班管理员发现与失踪妹妹相关的灯塔日志，被迫揭开废弃灯塔的秘密。",
            ),
        )
        print(json.dumps(book.model_dump(), ensure_ascii=False, indent=2))

        print("\n== 2. 生成大纲 ==")
        nodes = book_flow.generate_outline(session, book.id)
        print(f"nodes={len(nodes)}")
        for n in nodes[:8]:
            print(f"  [{n.level}] {n.title}")

        print("\n== 3. 锁定大纲 ==")
        book = book_flow.lock_outline(session, book.id)
        print(f"outline_locked={book.outline_locked}")

        print("\n== 4. 生成人物 ==")
        chars = book_flow.generate_characters(session, book.id)
        for c in chars:
            print(f"  - {c.name} ({c.role})")

        print("\n== 5. 锁定人物 ==")
        book = book_flow.lock_characters(session, book.id)
        print(f"characters_locked={book.characters_locked}")

        print("\n== 6. 章节规划 ==")
        chapters = book_flow.plan_chapters(session, book.id)
        for ch in chapters:
            print(f"  ch{ch.number}: {ch.title} ~{ch.target_words}字")

        max_chapters = len(chapters) if args.all else args.max_chapters
        print(f"\n== 7. 连续写作（最多 {max_chapters} 章，自动确认） ==")
        loop = book_flow.write_chapters_loop(
            session,
            book.id,
            max_chapters=max_chapters,
            auto_confirm=True,
        )
        for item in loop.written:
            gen = item.generate
            print(
                f"  ch{item.chapter_number}: chars={gen.version.word_count} "
                f"target={gen.context_manifest.get('target_words')} "
                f"min={gen.context_manifest.get('min_words')} "
                f"quality_ok={gen.context_manifest.get('quality_ok')} "
                f"expanded={gen.context_manifest.get('expanded_count', 0)} "
                f"rewritten={gen.context_manifest.get('rewritten_count', 0)} "
                f"confirm={item.confirm.get('status')}"
            )
            if item.confirm.get("status") == "skipped_quality":
                print(f"  !! quality gate blocked confirm: {item.confirm}")
        print(f"confirmed_facts_total={loop.confirmed_facts_total}")

        print("\n== 8. 导出已确认章节 ==")
        try:
            exported = book_flow.export_book_txt(
                session, book.id, settings=settings, scope="confirmed"
            )
            print(json.dumps(exported.model_dump(), ensure_ascii=False, indent=2))
        except Exception as exc:  # noqa: BLE001
            print(f"export skipped: {exc}")
            print(
                "（通常表示质量门禁未确认任何章节；正文可能仍是 drafted，"
                "可用 API 查看后 force 确认或重新 generate）"
            )

        do_complete = args.all or args.complete
        if do_complete:
            print("\n== 9. 完本 + 全书导出 ==")
            try:
                done = book_flow.complete_book(session, book.id)
                print(json.dumps(done.model_dump(), ensure_ascii=False, indent=2))
                full = book_flow.export_book_txt(
                    session, book.id, settings=settings, scope="all"
                )
                print(json.dumps(full.model_dump(), ensure_ascii=False, indent=2))
            except Exception as exc:  # noqa: BLE001
                print(f"complete/full-export skipped: {exc}")

        hits = search_fts(session, book.id, chars[0].name if chars else "林")
        print(f"fts_hits={len(hits)}")

    print("\nDONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
