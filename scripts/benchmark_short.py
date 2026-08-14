"""验证优化配置后的三萬字生成速度与质量。
统计：总耗时 / 每章平均耗时 / truncated 次数 / attempts 分布 / 字数达标率。
结果写入 data/benchmark_result.txt。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from sqlalchemy import select
from sqlalchemy.orm import Session

from novel_agent.application.workflows import book_flow
from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.infrastructure.persistence.models import (
    BookRow,
    ChapterRow,
    ChapterVersionRow,
)
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.llm.stats import record_llm_call  # noqa: F401 (register hook)
from novel_agent.infrastructure.persistence.db import create_all_tables, get_engine, get_session_factory
from novel_agent.settings import get_settings


BOOK_TITLE = "速度验证测试·星尘契约"
BOOK_LENGTH = "short"  # 三萬字
RESULT_FILE = _ROOT / "data" / "benchmark_result.txt"


def log(msg: str, file_lines: list[str]) -> None:
    print(msg, flush=True)
    file_lines.append(msg)


def write_result(text: str) -> None:
    RESULT_FILE.write_text(text, encoding="utf-8")
    print(f"\n[结果已写入] {RESULT_FILE}", flush=True)


def main() -> None:
    settings = get_settings()
    ensure_data_dirs(settings)
    engine = get_engine(settings)
    create_all_tables(engine)
    SessionLocal = get_session_factory(settings)
    gateway = LLMGateway(settings=settings)

    lines: list[str] = []
    overall_start = time.perf_counter()

    log("=" * 60, lines)
    log("Novel-Agent 速度验证（三萬字 / 短篇）", lines)
    log(f"模型: {settings.ollama_model}", lines)
    log(f"CONTEXT_LIMIT: {settings.context_limit}", lines)
    log(f"MAX_CHAPTER_WORDS: {settings.max_chapter_words}", lines)
    log(f"FEW_SHOT_SAMPLE_CHARS: {settings.few_shot_sample_chars}", lines)
    log(f"PREV_CHAPTER_TAIL_CHARS: {settings.prev_chapter_tail_chars}", lines)
    log(f"MAX_FACTS_IN_CONTEXT: {settings.max_facts_in_context}", lines)
    log(f"WRITER_MAX_REPAIR: {settings.writer_max_repair}", lines)
    log(f"SCHEMA_REPAIR_RETRIES: {settings.schema_repair_retries}", lines)
    log("=" * 60, lines)

    def on_progress(stage: str, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        print(f"  [{ts}] {stage}: {msg}", flush=True)

    session: Session = SessionLocal()
    try:
        # Step 1: create book ------------------------------------------------
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=BOOK_TITLE,
                genre="科幻",
                style="写实",
                length=BOOK_LENGTH,
                perspective="第三人称",
                tone="沉稳",
                premise="近未来太空站，一名档案管理员发现了被抹除的殖民船记录，随后身边发生了一系列诡异事件。",
            ),
        )
        session.commit()
        log(f"\n[创建书籍] id={book.id}", lines)

        # Step 2: run full pipeline -----------------------------------------
        log(f"\n[开始生成三萬字] 书名《{BOOK_TITLE}》", lines)
        run_start = time.perf_counter()
        result = book_flow.generate_full_book(
            session, book.id,
            gateway=gateway,
            settings=settings,
            progress_callback=on_progress,
        )
        session.commit()
        total_run_secs = time.perf_counter() - run_start

        # Step 3: collect chapter stats -------------------------------------
        chapters = list(
            session.scalars(
                select(ChapterRow)
                .where(ChapterRow.book_id == book.id)
                .order_by(ChapterRow.number)
            ).all()
        )
        chapter_stats: list[dict] = []
        word_counts: list[int] = []
        total_chars = 0
        for ch in chapters:
            ver = session.get(ChapterVersionRow, ch.current_version_id) if ch.current_version_id else None
            wc = len(ver.content) if ver and ver.content else 0
            word_counts.append(wc)
            total_chars += wc
            chapter_stats.append({
                "n": ch.number,
                "title": ch.title,
                "words": wc,
                "target": ch.target_words,
                "min_ok": wc >= int(ch.target_words * settings.writer_min_words_ratio),
            })

        # Step 4: summary ----------------------------------------------------
        log("\n" + "=" * 60, lines)
        log("结果总览", lines)
        log("=" * 60, lines)
        success = result.get("success", False)
        log(f"成功: {success}", lines)
        if not success:
            log(f"错误: {result.get('error')}", lines)
        minutes, secs = divmod(int(total_run_secs), 60)
        hours, minutes = divmod(minutes, 60)
        log(f"总耗时: {hours:02d}:{minutes:02d}:{secs:02d}", lines)
        log(f"章节数: {len(chapters)}", lines)
        log(f"总字数: {total_chars}", lines)
        avg = total_run_secs / len(chapters) if chapters else 0
        log(f"每章平均耗时: {avg:.0f} 秒/章", lines)
        log(f"千字耗时: {total_run_secs / (total_chars/1000):.0f} 秒/千字", lines)
        min_ok = sum(1 for s in chapter_stats if s["min_ok"])
        log(f"字数达标率: {min_ok}/{len(chapters)}", lines)
        if word_counts:
            log(f"单章字数范围: {min(word_counts)} ~ {max(word_counts)}", lines)

        log("\n" + "-" * 60, lines)
        log("各章明细", lines)
        log("-" * 60, lines)
        for s in chapter_stats:
            mark = "✅" if s["min_ok"] else "⚠️"
            log(f"  第{s['n']:02d}章  {s['words']:>5}/{s['target']:>5}字  {mark}  《{s['title']}》", lines)

        overall_secs = time.perf_counter() - overall_start
        m, s = divmod(int(overall_secs), 60)
        h, m = divmod(m, 60)
        log(f"\n脚本总用时（含建书）: {h:02d}:{m:02d}:{s:02d}", lines)

        if success:
            log("\n对比优化前（约1小时+）：", lines)
            before_estimate = 60 * 60  # 1 hour
            speedup = before_estimate / total_run_secs if total_run_secs > 0 else 0
            log(f"  优化前估算: ~60 分钟", lines)
            log(f"  本次实际:   {hours}时{minutes}分{secs}秒", lines)
            log(f"  加速倍数:   {speedup:.1f}x", lines)

        write_result("\n".join(lines))

    except Exception as exc:
        log(f"\n[异常终止] {exc}", lines)
        write_result("\n".join(lines))
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
