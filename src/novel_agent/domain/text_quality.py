"""Detect and trim repetitive LLM prose loops."""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class RepetitionFix:
    text: str
    truncated: bool
    reason: str = ""
    original_len: int = 0
    final_len: int = 0


@dataclass
class DraftQuality:
    """Post-process assessment used by the chapter writing quality gate."""

    text: str
    quality_ok: bool
    too_short: bool
    repetition_truncated: bool
    repetition_severe: bool
    reason: str = ""
    original_len: int = 0
    final_len: int = 0
    cut_ratio: float = 0.0
    word_count: int = 0
    min_words: int = 0


def assess_draft_quality(
    text: str,
    min_words: int,
    *,
    repair_repetition: bool = True,
    max_trim_ratio: float = 0.25,
) -> DraftQuality:
    """
    Trim repetitive prose then decide whether the draft is acceptable.

    - too_short: after trim, length < min_words → needs expand
    - repetition_severe: trim removed >= max_trim_ratio → needs rewrite
    - quality_ok: long enough and not severely repetitive
    """
    original = text or ""
    if repair_repetition:
        fixed = fix_repetitive_text(original)
    else:
        fixed = RepetitionFix(
            text=original,
            truncated=False,
            original_len=len(original),
            final_len=len(original),
        )

    orig_len = fixed.original_len or len(original)
    final_len = len(fixed.text)
    cut_ratio = (1.0 - (final_len / orig_len)) if orig_len > 0 else 0.0
    repetition_severe = bool(fixed.truncated and cut_ratio >= max_trim_ratio)
    too_short = final_len < min_words
    quality_ok = (not too_short) and (not repetition_severe)

    reasons: list[str] = []
    if too_short:
        reasons.append("too_short")
    if repetition_severe:
        reasons.append("repetition_severe")
    elif fixed.truncated and fixed.reason:
        reasons.append(fixed.reason)

    return DraftQuality(
        text=fixed.text,
        quality_ok=quality_ok,
        too_short=too_short,
        repetition_truncated=fixed.truncated,
        repetition_severe=repetition_severe,
        reason="+".join(reasons),
        original_len=orig_len,
        final_len=final_len,
        cut_ratio=cut_ratio,
        word_count=final_len,
        min_words=min_words,
    )


def _norm_key(text: str) -> str:
    return re.sub(r"\s+", "", text.strip())


def _split_paragraphs(text: str) -> list[str]:
    parts = re.split(r"\n\s*\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _dedupe_paragraphs(
    text: str,
    *,
    min_len: int = 18,
    similarity: float = 0.92,
) -> tuple[str, bool, str]:
    """
    Remove later copies of long paragraphs (including non-adjacent duplicates).
    Keeps the first occurrence and all unique content between repeats.
    """
    paras = _split_paragraphs(text)
    if len(paras) < 2:
        return text, False, ""

    kept: list[str] = []
    kept_keys: list[str] = []
    removed = False

    for para in paras:
        key = _norm_key(para)
        if len(key) < min_len:
            kept.append(para)
            kept_keys.append(key)
            continue

        is_dup = False
        if key in kept_keys:
            is_dup = True
        else:
            for prev in kept_keys:
                if len(prev) < min_len:
                    continue
                # similar length only
                if abs(len(prev) - len(key)) > max(12, int(0.15 * max(len(prev), len(key)))):
                    continue
                if SequenceMatcher(None, prev, key).ratio() >= similarity:
                    is_dup = True
                    break

        if is_dup:
            removed = True
            continue
        kept.append(para)
        kept_keys.append(key)

    if not removed:
        return text, False, ""

    # Preserve single newlines inside paragraphs; separate paras with blank line
    rebuilt = "\n\n".join(kept)
    # If original used \n\n endings, fine; trailing newline optional
    if text.endswith("\n"):
        rebuilt += "\n"
    return rebuilt, True, "duplicate_paragraph"


def _dedupe_trailing_block_vs_earlier(text: str, min_span: int = 18) -> tuple[str, bool, str]:
    """
    If the ending block (last 1–3 paragraphs) already appeared earlier, drop the trailing copy.
    """
    paras = _split_paragraphs(text)
    if len(paras) < 3:
        return text, False, ""

    for tail_n in (3, 2, 1):
        if len(paras) <= tail_n + 1:
            continue
        tail = paras[-tail_n:]
        tail_key = _norm_key("".join(tail))
        if len(tail_key) < min_span:
            continue
        head = paras[:-tail_n]
        head_join = _norm_key("\n\n".join(head))
        # exact containment of normalized tail in earlier text
        if tail_key in head_join:
            rebuilt = "\n\n".join(head)
            if text.endswith("\n"):
                rebuilt += "\n"
            return rebuilt, True, "trailing_duplicate_block"
        # fuzzy: compare tail string to sliding windows of earlier paragraphs
        for i in range(len(head) - tail_n + 1):
            window = head[i : i + tail_n]
            win_key = _norm_key("".join(window))
            if abs(len(win_key) - len(tail_key)) > max(20, int(0.2 * len(tail_key))):
                continue
            if SequenceMatcher(None, win_key, tail_key).ratio() >= 0.93:
                rebuilt = "\n\n".join(head)
                if text.endswith("\n"):
                    rebuilt += "\n"
                return rebuilt, True, "trailing_duplicate_block"
    return text, False, ""


def _find_block_loop(text: str, min_block: int = 20, max_block: int = 160) -> int | None:
    """If a trailing region is A repeated 3+ times, keep only the first A."""
    n = len(text)
    if n < min_block * 3:
        return None

    best: tuple[int, int] | None = None
    for block_len in range(min(max_block, n // 3), min_block - 1, -1):
        end = text[-block_len:]
        if text[-2 * block_len : -block_len] != end:
            continue
        if text[-3 * block_len : -2 * block_len] != end:
            continue
        start = n - 3 * block_len
        while start - block_len >= 0 and text[start - block_len : start] == end:
            start -= block_len
        copies = (n - start) // block_len
        if copies >= 3:
            keep_end = start + block_len
            if best is None or copies > best[1] or (copies == best[1] and keep_end < best[0]):
                best = (keep_end, copies)
    return best[0] if best else None


def _find_near_duplicate_sentence_run(text: str, min_len: int = 12) -> int | None:
    """If the same sentence appears 3+ times consecutively, cut after the first."""
    parts = re.split(r"(?<=[。！？\.!?])", text)
    parts = [p for p in parts if p.strip()]
    if len(parts) < 4:
        return None

    i = 0
    while i < len(parts) - 2:
        cur = parts[i].strip()
        if len(cur) < min_len:
            i += 1
            continue
        run = 1
        j = i + 1
        while j < len(parts) and parts[j].strip() == cur:
            run += 1
            j += 1
        if run >= 3:
            prefix = "".join(parts[: i + 1])
            return len(prefix)
        i += 1
    return None


def _find_overlapping_phrase_spam(text: str, window: int = 36, min_hits: int = 3) -> int | None:
    """If a mid-length phrase repeats densely, truncate after its first occurrence."""
    n = len(text)
    if n < window * min_hits:
        return None
    search_from = max(0, n // 4)
    best_cut: int | None = None
    step = max(6, window // 4)
    for i in range(search_from, n - window, step):
        phrase = text[i : i + window]
        if len(phrase.strip()) < window // 2:
            continue
        count = 1
        pos = i + window
        while True:
            found = text.find(phrase, pos)
            if found < 0:
                break
            count += 1
            pos = found + window
            if count >= min_hits:
                cut = i + window
                if best_cut is None or cut < best_cut:
                    best_cut = cut
                break
    return best_cut


def _find_abc_cycle(text: str, min_unit: int = 30) -> int | None:
    """Detect (A+B+C) repeated as a whole cycle near the end."""
    n = len(text)
    if n < min_unit * 6:
        return None
    for cycle in range(min(400, n // 3), min_unit * 2, -1):
        if n < cycle * 3:
            continue
        chunk = text[-cycle:]
        if text[-2 * cycle : -cycle] != chunk:
            continue
        if text[-3 * cycle : -2 * cycle] != chunk:
            continue
        start = n - 3 * cycle
        while start - cycle >= 0 and text[start - cycle : start] == chunk:
            start -= cycle
        return start + cycle
    return None


def _apply_prefix_cut(original: str, cut_at: int, reason: str) -> RepetitionFix | None:
    min_keep = max(20, len(original) // 25)
    if cut_at < min_keep:
        return None
    fixed = original[:cut_at].rstrip()
    for sep in ("。", "！", "？", ".", "!", "?"):
        idx = fixed.rfind(sep)
        if idx >= len(fixed) * 0.5:
            fixed = fixed[: idx + 1]
            break
    if len(fixed) >= len(original) * 0.95:
        return None
    return RepetitionFix(
        text=fixed,
        truncated=True,
        reason=reason,
        original_len=len(original),
        final_len=len(fixed),
    )


def fix_repetitive_text(text: str) -> RepetitionFix:
    """
    Fix repetitive prose:
    1) drop later duplicate paragraphs (non-adjacent OK)
    2) drop trailing block that already appeared earlier
    3) truncate dense consecutive loops
    """
    original = text or ""
    if not original.strip():
        return RepetitionFix(text=original, truncated=False, original_len=0, final_len=0)

    working = original
    reasons: list[str] = []

    # Pass A: paragraph-level later-duplicate removal (preferred for non-adjacent copies)
    deduped, changed, reason = _dedupe_paragraphs(working)
    if changed:
        working = deduped
        reasons.append(reason)

    # Pass B: trailing multi-paragraph block duplicate
    deduped, changed, reason = _dedupe_trailing_block_vs_earlier(working)
    if changed:
        working = deduped
        reasons.append(reason)

    # Pass C: consecutive loop truncation on current working text
    candidates: list[tuple[int, str]] = []
    for finder, name in (
        (_find_block_loop, "block_loop"),
        (_find_abc_cycle, "abc_cycle"),
        (_find_near_duplicate_sentence_run, "sentence_run"),
        (_find_overlapping_phrase_spam, "phrase_spam"),
    ):
        cut = finder(working)
        if cut is not None:
            candidates.append((cut, name))

    if candidates:
        candidates.sort(key=lambda x: x[0])
        for cut_at, name in candidates:
            cut_fix = _apply_prefix_cut(working, cut_at, name)
            if cut_fix is not None:
                working = cut_fix.text
                reasons.append(name)
                break

    if not reasons or working == original:
        return RepetitionFix(
            text=original,
            truncated=False,
            original_len=len(original),
            final_len=len(original),
        )

    if len(working) >= len(original) * 0.98 and working.strip() == original.strip():
        return RepetitionFix(
            text=original,
            truncated=False,
            reason="negligible",
            original_len=len(original),
            final_len=len(original),
        )

    return RepetitionFix(
        text=working,
        truncated=True,
        reason="+".join(reasons),
        original_len=len(original),
        final_len=len(working),
    )
