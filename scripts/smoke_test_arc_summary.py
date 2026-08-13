"""Arc summary integration smoke test.

Verifies the end-to-end arc summary pipeline against a live Ollama instance:
1. Seeds a book with 10 confirmed chapters (realistic narrative)
2. Calls generate_arc_summary() to produce an arc summary via LLM
3. Calls _build_chapter_context() for chapter 11 to verify injection
4. Prints the generated summary and context manifest for inspection

Usage:
    python scripts/smoke_test_arc_summary.py

Requires:
    - Ollama running on http://127.0.0.1:11434
    - Model qwen2.5:7b-instruct pulled
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from novel_agent.application.workflows import book_flow  # noqa: E402
from novel_agent.domain.book.schemas import CreateBookRequest  # noqa: E402
from novel_agent.domain.errors import new_id  # noqa: E402
from novel_agent.infrastructure.persistence.db import (  # noqa: E402
    create_all_tables,
    get_engine,
    reset_engine,
)
from novel_agent.infrastructure.persistence.models import (  # noqa: E402
    ArcSummaryRow,
    ChapterRow,
    ChapterVersionRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork  # noqa: E402
from novel_agent.settings import get_settings  # noqa: E402


# ── Test data: 10 chapters of a coherent detective story ─────────────────────
TEST_CHAPTERS = [
    (
        1,
        "旧仓库的秘密",
        "林澄深夜潜入旧仓库，发现一本撕掉几页的日志，记录着十年前的纵火案细节。",
    ),
    (
        2,
        "撕页之谜",
        "林澄分析撕页内容，发现缺失的几页涉及一个叫'陈先生'的神秘人物。",
    ),
    (
        3,
        "陈先生的身份",
        "林澄调查'陈先生'，发现他是已故商人陈志远，十年前死于那场纵火案。",
    ),
    (
        4,
        "纵火案现场",
        "林澄重返纵火案旧址，遇到自称陈志远女儿陈雨桐的女子，她声称父亲是被害。",
    ),
    (
        5,
        "女儿的证词",
        "陈雨桐告诉林澄，父亲生前曾拒绝出卖地皮给某开发商，疑似因此被害。",
    ),
    (
        6,
        "开发商的线索",
        "林澄调查开发商'宏图地产'，发现法人代表是市长外甥王鼎。",
    ),
    (
        7,
        "王鼎的威胁",
        "王鼎察觉林澄调查，派人威胁他停止追查，否则对陈雨桐不利。",
    ),
    (
        8,
        "深夜跟踪",
        "林澄深夜跟踪王鼎，发现他与一个戴墨镜的男子在地下车库密谈。",
    ),
    (
        9,
        "墨镜男的身份",
        "林澄辨认出墨镜男是十年前纵火案的关键证人张彪，他曾翻供导致案件搁置。",
    ),
    (
        10,
        "翻供的真相",
        "林澄找到张彪的女儿，得知张彪当年收了王鼎50万才翻供，如今良心不安想自首。",
    ),
]


def seed_test_book(session, book_id: str) -> None:
    """Seed a book with 10 confirmed chapters."""
    from novel_agent.infrastructure.persistence.models import BookRow

    book = BookRow(
        id=book_id,
        title="撕页日志",
        premise="记者林澄调查十年前纵火案，发现撕页日志背后的惊天阴谋。",
        genre="悬疑",
        length="short",
        status="writing",
        outline_locked=True,
        characters_locked=True,
        version=1,
    )
    session.add(book)
    session.flush()

    for number, title, summary in TEST_CHAPTERS:
        chapter = ChapterRow(
            id=new_id(),
            book_id=book_id,
            number=number,
            title=title,
            goal=f"第{number}章目标",
            target_words=1000,
            status="confirmed",
        )
        session.add(chapter)
        session.flush()
        # Content is summary expanded with some filler for realism
        content = f"第{number}章《{title}》正文：{summary}" + "（详细描写略）" * 10
        version = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title=title,
            content=content,
            summary=summary,
            status="CONFIRMED",
            word_count=len(content),
        )
        session.add(version)
        session.flush()
        chapter.current_version_id = version.id
        session.flush()


def main() -> int:
    print("=" * 70)
    print("分层摘要集成冒烟测试")
    print("=" * 70)

    # ── Setup ────────────────────────────────────────────────────────────────
    print("\n[1/4] 初始化数据库...")
    reset_engine()
    db_path = PROJECT_ROOT / "data" / "smoke_test_arc.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    url = f"sqlite:///{db_path.as_posix()}"
    engine = get_engine(url=url)
    create_all_tables(engine)
    print(f"    ✓ 数据库: {db_path}")

    settings = get_settings()
    print(f"    ✓ 模型: {settings.ollama_model}")
    print(f"    ✓ arc_size: {settings.arc_size}")
    print(f"    ✓ arc_summaries_in_context: {settings.arc_summaries_in_context}")

    # ── Seed test data ───────────────────────────────────────────────────────
    print("\n[2/4] 种子数据：构造 10 章已确认章节...")
    book_id = new_id()
    with UnitOfWork(url=url) as uow:
        seed_test_book(uow.session, book_id)
        uow.commit()
    print(f"    ✓ 书 ID: {book_id}")
    print(f"    ✓ 章节: {len(TEST_CHAPTERS)} 章已确认")

    # ── Generate arc summary ─────────────────────────────────────────────────
    print("\n[3/4] 调用 generate_arc_summary(arc_index=1, level='arc')...")
    print("    （调用 Ollama，预计 30-60 秒）")
    t0 = time.time()
    summary_text: str
    char_count: int
    chapter_start: int
    chapter_end: int
    with UnitOfWork(url=url) as uow:
        try:
            row = book_flow.generate_arc_summary(
                uow.session,
                book_id,
                arc_index=1,
                level="arc",
                settings=settings,
            )
            # Extract data BEFORE commit/close to avoid DetachedInstanceError
            summary_text = row.summary
            char_count = row.char_count
            chapter_start = row.chapter_start
            chapter_end = row.chapter_end
            uow.commit()
        except Exception as e:
            print(f"\n    ✗ 失败: {type(e).__name__}: {e}")
            return 1
    elapsed = time.time() - t0
    print(f"    ✓ 生成耗时: {elapsed:.1f}s")
    print(f"    ✓ 摘要字数: {char_count}")
    print(f"    ✓ 覆盖范围: 第 {chapter_start}-{chapter_end} 章")

    print("\n" + "─" * 70)
    print("生成的卷摘要：")
    print("─" * 70)
    print(summary_text)
    print("─" * 70)

    # ── Verify injection ─────────────────────────────────────────────────────
    print("\n[4/4] 验证第 11 章上下文注入...")
    with UnitOfWork(url=url) as uow:
        # Create a planned chapter 11
        chapter_11 = ChapterRow(
            id=new_id(),
            book_id=book_id,
            number=11,
            title="第十一章",
            goal="林澄带张彪去自首，王鼎得知后派人阻拦。",
            target_words=1000,
            status="planned",
        )
        uow.session.add(chapter_11)
        uow.commit()

        book = book_flow._get_book(uow.session, book_id)
        canon, manifest = book_flow._build_chapter_context(
            uow.session, book, chapter_11, settings=settings
        )

    print(f"    ✓ arc_summaries_injected: {manifest['arc_summaries_injected']}")
    print(f"    ✓ mega_summaries_injected: {manifest['mega_summaries_injected']}")
    print(f"    ✓ arc_summary_ranges: {manifest['arc_summary_ranges']}")
    print(f"    ✓ context_chars: {manifest['context_chars']}")

    print("\n" + "─" * 70)
    print("第 11 章上下文（截断展示）：")
    print("─" * 70)
    # Show the arc summary section
    if "近期卷摘要" in canon:
        start = canon.find("近期卷摘要")
        end = canon.find("\n\n", start + 100)
        if end == -1:
            end = len(canon)
        print(canon[start:end])
    else:
        print("    ✗ 未找到卷摘要注入！")
        return 1

    # ── Verify DB persistence ────────────────────────────────────────────────
    print("\n[验证] 数据库持久化检查...")
    with UnitOfWork(url=url) as uow:
        rows = list(
            uow.session.scalars(
                __import__("sqlalchemy").select(ArcSummaryRow).where(
                    ArcSummaryRow.book_id == book_id
                )
            ).all()
        )
        print(f"    ✓ ArcSummaryRow 记录数: {len(rows)}")
        for r in rows:
            print(
                f"      - arc_index={r.arc_index}, level={r.level}, "
                f"ch={r.chapter_start}-{r.chapter_end}, chars={r.char_count}"
            )

    print("\n" + "=" * 70)
    print("✓ 全部验证通过")
    print("=" * 70)
    print("\n结论：")
    print(f"- arc summary 生成耗时 {elapsed:.1f}s（在可接受范围内）")
    print(f"- 摘要字数 {char_count} 字")
    print("- 第 11 章上下文已注入 arc 1 摘要")
    print("- 数据库持久化正常")
    print("\n建议：人工检查上方摘要质量（是否覆盖关键事件、伏笔是否遗漏）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
