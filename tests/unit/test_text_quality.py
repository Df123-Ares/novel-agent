"""Tests for repetitive prose detection."""

from __future__ import annotations

from pathlib import Path

from novel_agent.domain.text_quality import (
    find_duplicate_sentences,
    fix_repetitive_text,
    strip_duplicate_sentences,
)


def test_no_false_positive_on_normal_prose() -> None:
    text = (
        "林默站在灯塔下，海风带着咸味扑面而来。"
        "他推开锈蚀的铁门，台阶在脚下发出不堪重负的呻吟。"
        "手电筒的光柱切开黑暗，墙上的潮痕像旧日的地图。"
        "他继续向上，直到听见远处隐约的金属碰撞声。"
    )
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is False
    assert fixed.text == text


def test_find_non_adjacent_duplicate_sentence() -> None:
    """Same sentence repeated in a later paragraph is reported once."""
    dup = "林默推开门，走进了那间昏暗的档案室。"
    text = (
        f"{dup}他打开台灯，开始翻阅卷宗。\n\n"
        "陆远敲了敲窗玻璃，递进来一杯热茶。\n\n"
        f"{dup}他低头继续翻看下一册档案。"
    )
    found = find_duplicate_sentences(text)
    assert len(found) == 1
    assert dup in found[0]


def test_strip_non_adjacent_duplicate_sentence() -> None:
    dup = "林默推开门，走进了那间昏暗的档案室。"
    text = (
        f"{dup}他打开台灯，开始翻阅卷宗。\n\n"
        "陆远敲了敲窗玻璃，递进来一杯热茶。\n\n"
        f"{dup}他低头继续翻看下一册档案。"
    )
    stripped, removed = strip_duplicate_sentences(text)
    assert removed == 1
    assert stripped.count(dup) == 1
    assert "陆远" in stripped


def test_strip_fuzzy_near_duplicate_sentence() -> None:
    """Slightly reworded copy is removed, original kept."""
    first = "他沉默了片刻，才开口回答陆远的问题。"
    reworded = "他沉默了片刻，才开口回答陆远的问题呢。"
    text = f"{first}\n\n{reworded}"
    stripped, removed = strip_duplicate_sentences(text)
    assert removed == 1
    assert stripped.count("他沉默了片刻") == 1


def test_short_sentences_never_removed() -> None:
    """Trivial dialogue tags and short sentences are never deduped."""
    tags = "他说。\n\n嗯。\n\n好。\n\n走吧。\n\n他点点头。"
    found = find_duplicate_sentences(tags)
    assert found == []
    stripped, removed = strip_duplicate_sentences(tags)
    assert removed == 0
    assert stripped == tags


def test_duplicate_sentence_participates_in_quality_gate() -> None:
    """Later repeated sentence is trimmed and flagged as truncated."""
    dup = "他缓缓站起身，环顾四周，确认没有异样。"
    text = (
        "林默走进灯塔。" + dup + "他继续向上攀登。" + dup + "他加快脚步。" + dup
    )
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is True
    assert fixed.text.count(dup) == 1
    assert "duplicate_sentence" in fixed.reason


def test_trims_block_loop() -> None:
    block = "他的思绪被一阵低语打断，那声音仿佛从灯塔的深处传来。他缓缓站起身，环顾四周。"
    text = "林默走进灯塔。" + block * 5
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is True
    assert fixed.final_len < fixed.original_len
    assert fixed.text.count(block) <= 1
    assert "林默走进灯塔" in fixed.text


def test_trims_repeated_sentences() -> None:
    sent = "他继续前行，直到他来到那扇紧闭的门前。"
    text = "林默深吸一口气。" + sent * 4
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is True
    assert fixed.text.count(sent) == 1


def test_chapter3_style_spam() -> None:
    a = (
        "他的思绪被一阵低语打断，那声音仿佛从灯塔的深处传来。"
        "他缓缓站起身，环顾四周，发现那声音似乎来自灯塔的某个角落。他走"
    )
    b = (
        "就在这时，一阵微弱的风掠过他的耳边，仿佛在低语着什么。"
        "他缓缓闭上眼睛，试图捕捉那声音的来源。他的内心深处，一个念头"
    )
    c = (
        "林默的脚步在黑暗中回响，他感到一阵莫名恐惧，但他没有停下。"
        "他知道，自己已经走到了真相的边缘。他继续前行，直到他来"
    )
    head = "第三章开头。林默站在灯塔下，决定继续深入。"
    text = head + (a + b + c) * 5
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is True
    assert fixed.final_len < len(text) * 0.5
    assert "第三章开头" in fixed.text


def test_non_adjacent_duplicate_paragraphs() -> None:
    """Same ending pasted again after more plot — keep middle, drop later copy."""
    early = (
        "林默的脚步在夜色中回响，仿佛在诉说着一个未完的故事。"
        "他知道，自己必须继续前行，直到找到真相。而真相，或许就在那座废弃的灯塔中。"
    )
    mid = (
        "“陆远。”林默的语气中带着一丝警惕和惊讶。\n\n"
        "陆远微微一笑，眼中闪过一丝复杂的情绪。"
    )
    late_end = "他不知道，自己是否已经走在一条不归之路上。"
    text = (
        f"他合上笔记本。\n\n{early}\n\n{late_end}\n\n"
        f"{mid}\n\n"
        f"林默深吸一口气，迈步向前。\n\n"
        f"{early}\n\n{late_end}\n"
    )
    fixed = fix_repetitive_text(text)
    assert fixed.truncated is True
    assert "陆远" in fixed.text
    assert fixed.text.count(early) == 1
    assert fixed.text.count(late_end) == 1
    assert "duplicate_paragraph" in fixed.reason


def test_real_export_chapter_duplicate_tail() -> None:
    path = Path("data/exports/6f153d67_雾港回声_partial.txt")
    if not path.exists():
        return
    raw = path.read_text(encoding="utf-8")
    start = raw.find("第1章")
    end = raw.find("--------", start + 1)
    chapter = raw[start:end] if start >= 0 else raw
    # strip title line for body-focused check
    body = chapter.split("\n", 1)[-1] if "\n" in chapter else chapter
    fixed = fix_repetitive_text(body)
    assert fixed.truncated is True
    marker = "林默的脚步在夜色中回响，仿佛在诉说着一个未完的故事"
    assert fixed.text.count(marker) == 1
    assert "陆远" in fixed.text
