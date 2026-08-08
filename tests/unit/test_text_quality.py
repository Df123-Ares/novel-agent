"""Tests for repetitive prose detection."""

from __future__ import annotations

from pathlib import Path

from novel_agent.domain.text_quality import fix_repetitive_text


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
