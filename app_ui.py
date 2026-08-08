"""Novel-Agent Gradio UI — interactive novel creation assistant.

Launch:  python app_ui.py
Open:    http://127.0.0.1:7860
"""

from __future__ import annotations

import json
from pathlib import Path

import gradio as gr
from sqlalchemy.orm import Session

from novel_agent.application.workflows import book_flow
from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.domain.book.schemas import (
    CreateBookRequest,
    UpdateCharacterRequest,
    UpdateOutlineNodeRequest,
)
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    get_session_factory,
)
from novel_agent.settings import get_settings

# ── bootstrap ────────────────────────────────────────────────────────────────
settings = ensure_data_dirs(get_settings())
engine = get_engine(settings)
create_all_tables(engine)
SessionLocal = get_session_factory(settings)
gateway = LLMGateway(settings=settings)

DATA_EXPORTS = settings.export_dir


def _session() -> Session:
    """Create a fresh database session."""
    return SessionLocal()


# ═══════════════════════════════════════════════════════════════════════════════
#  Handlers — each gets its own DB session
# ═══════════════════════════════════════════════════════════════════════════════

# ── Step ①  Create Book ──────────────────────────────────────────────────────


def handle_create_book_simple(title: str) -> tuple[str, str, str]:
    """Create book with just a title — auto-fill defaults."""
    return handle_create_book(
        title=title, genre="", style="", length="short",
        perspective="第三人称", tone="", premise=title,
    )


def handle_create_book(
    title: str,
    genre: str,
    style: str,
    length: str,
    perspective: str,
    tone: str,
    premise: str,
) -> tuple[str, str, str]:
    """Create a new book, return (book_id, summary_text, book_json)."""
    if not title.strip():
        return "", "❌ 请输入书名", ""
    if not premise.strip():
        premise = title
    # Extract value from dropdown labels like "short - 短篇 (~3万字)"
    length = length.split(" - ")[0] if " - " in length else length
    perspective = perspective.split(" - ")[0] if " - " in perspective else perspective

    session = _session()
    try:
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=title.strip(),
                genre=genre,
                style=style,
                length=length,
                perspective=perspective,
                tone=tone,
                premise=premise.strip(),
            ),
        )
        session.commit()
        info = (
            f"✅ 书籍创建成功\n\n"
            f"📖 书名：{book.title}\n"
            f"🏷️  类型：{book.genre}  |  风格：{book.style}  |  篇幅：{book.length}\n"
            f"👁️  视角：{book.perspective}  |  基调：{book.tone}\n"
            f"📝 梗概：{book.premise}\n\n"
            f"ID: `{book.id}`"
        )
        return (
            book.id,
            info,
            json.dumps(book.model_dump(), ensure_ascii=False, indent=2),
        )
    except Exception as exc:
        session.rollback()
        return "", f"❌ 创建失败：{exc}", ""
    finally:
        session.close()


# ── Step ②  Outline ──────────────────────────────────────────────────────────


def handle_generate_outline(book_id: str) -> tuple[str, str]:
    """Generate outline for a book, return (status_text, outline_display)."""
    if not book_id:
        return "❌ 请先创建书籍", ""

    session = _session()
    try:
        nodes = book_flow.generate_outline(session, book_id, gateway=gateway)
        session.commit()
        lines = [f"共 {len(nodes)} 个大纲节点\n"]
        for n in nodes:
            indent = "  " * (
                1 if n.level == "arc" else 2 if n.level == "chapter_goal" else 0
            )
            lines.append(f"{indent}[{n.level}] {n.title}")
            if n.summary:
                lines.append(f"{indent}    {n.summary}")
        return "✅ 大纲生成成功！", "\n".join(lines)
    except Exception as exc:
        session.rollback()
        return f"❌ 大纲生成失败：{exc}", ""
    finally:
        session.close()


def handle_list_outline(book_id: str) -> tuple[str, list[list]]:
    """List outline nodes as a table for editing."""
    if not book_id:
        return "❌ 请先创建书籍", []
    session = _session()
    try:
        nodes = book_flow.list_outline(session, book_id)
        data = []
        for n in nodes:
            data.append(
                [
                    n.id,
                    n.level,
                    n.title,
                    n.summary or "",
                    n.version,
                    "🔒" if n.locked else "✏️",
                ]
            )
        return f"共 {len(nodes)} 个节点", data
    except Exception as exc:
        return f"❌ {exc}", []
    finally:
        session.close()


def handle_lock_outline(book_id: str) -> str:
    if not book_id:
        return "❌ 请先创建书籍"
    session = _session()
    try:
        book = book_flow.lock_outline(session, book_id)
        session.commit()
        return f"✅ 大纲已锁定（version={book.version}）"
    except Exception as exc:
        session.rollback()
        return f"❌ 锁定失败：{exc}"
    finally:
        session.close()


# ── Step ③  Characters ───────────────────────────────────────────────────────


def handle_generate_characters(book_id: str) -> tuple[str, str]:
    if not book_id:
        return "❌ 请先创建书籍并锁定大纲", ""
    session = _session()
    try:
        chars = book_flow.generate_characters(session, book_id, gateway=gateway)
        session.commit()
        lines = [f"共 {len(chars)} 个人物\n"]
        for c in chars:
            lines.append(f"### {c.name}（{c.role}）")
            lines.append(f"- 性格：{c.personality}")
            lines.append(f"- 外貌：{c.appearance}")
            lines.append(f"- 背景：{c.background}")
            lines.append("")
        return "✅ 人物生成成功！", "\n".join(lines)
    except Exception as exc:
        session.rollback()
        return f"❌ 人物生成失败：{exc}", ""
    finally:
        session.close()


def handle_list_characters(book_id: str) -> tuple[str, list[list]]:
    if not book_id:
        return "❌ 请先创建书籍", []
    session = _session()
    try:
        chars = book_flow.list_characters(session, book_id)
        data = []
        for c in chars:
            data.append(
                [
                    c.id,
                    c.name,
                    c.role,
                    c.personality,
                    c.appearance,
                    c.background,
                    c.version,
                ]
            )
        return f"共 {len(chars)} 个人物", data
    except Exception as exc:
        return f"❌ {exc}", []
    finally:
        session.close()


def handle_lock_characters(book_id: str) -> str:
    if not book_id:
        return "❌ 请先创建书籍"
    session = _session()
    try:
        book = book_flow.lock_characters(session, book_id)
        session.commit()
        return f"✅ 人物已锁定（version={book.version}）"
    except Exception as exc:
        session.rollback()
        return f"❌ 锁定失败：{exc}"
    finally:
        session.close()


# ── Step ④  Chapter Planning ─────────────────────────────────────────────────


def handle_plan_chapters(book_id: str) -> tuple[str, str]:
    if not book_id:
        return "❌ 请先创建书籍", ""
    session = _session()
    try:
        chapters = book_flow.plan_chapters(session, book_id)
        session.commit()
        lines = [f"共规划 {len(chapters)} 章\n"]
        for ch in chapters:
            lines.append(
                f"第{ch.number}章：{ch.title}  |  目标字数：{ch.target_words}  |  状态：{ch.status}"
            )
        return "✅ 章节规划完成！", "\n".join(lines)
    except Exception as exc:
        session.rollback()
        return f"❌ 章节规划失败：{exc}", ""
    finally:
        session.close()


# ── Step ⑤  Writing ──────────────────────────────────────────────────────────


def handle_locate_target(book_id: str) -> tuple[str, str, str, str, str]:
    """Locate the next writable chapter. Load existing content if drafted.
    Returns (chapter_id, info_md, content, title_md, changes_md)."""
    if not book_id:
        return "", "❌ 请先创建书籍并完成规划", "", "", ""
    session = _session()
    try:
        ch = book_flow.get_next_writable_chapter(session, book_id)
        info = (
            f"### 📍 当前写作目标\n"
            f"第 **{ch.number}** 章《**{ch.title}**》\n"
            f"目标字数：{ch.target_words} 字 | 状态：{ch.status}\n"
            f"ID: `{ch.id}`"
        )

        # If chapter already has a draft, load it
        content = ""
        title_md = ""
        changes_md = ""
        if ch.current_version_id:
            detail = book_flow.get_chapter(session, ch.id)
            if detail.current_version:
                v = detail.current_version
                title_md = f"第{detail.chapter.number}章：{v.title} | 字数：{v.word_count} | 状态：{v.status}"
                content = v.content
                changes_md = f"（已有 {v.word_count} 字草稿，点击「生成/重写」覆盖）"

        return ch.id, info, content, title_md, changes_md
    except Exception as exc:
        return "", f"❌ {exc}", "", "", ""
    finally:
        session.close()


def handle_write_target(chapter_id: str) -> tuple[str, str, str, str, str]:
    """Generate/regenerate a specific chapter by ID.
    Returns (title_md, status_md, content, changes_md, validation_md)."""
    if not chapter_id:
        return "", "❌ 请先点击「定位到下一章」", "", "", ""
    session = _session()
    try:
        result = book_flow.generate_chapter(session, chapter_id, gateway=gateway)
        session.commit()

        title = f"### 第{result.chapter.number}章：{result.version.title}"
        status = (
            f"✅ 生成完成 | 字数：{result.version.word_count} | "
            f"质量：{'✅ 通过' if result.context_manifest.get('quality_ok') else '⚠️ 未通过'}\n"
            f"扩写：{result.context_manifest.get('expanded_count', 0)} 次 | "
            f"重写：{result.context_manifest.get('rewritten_count', 0)} 次"
        )
        content = result.version.content

        changes_lines = [f"**提取 {len(result.changes)} 条候选事实：**\n"]
        for chg in result.changes:
            changes_lines.append(
                f"- [{chg.kind}] {chg.subject}: {chg.claim} "
                f"（置信度: {chg.confidence:.2f}）"
            )
        changes_md = "\n".join(changes_lines)

        val_lines = [f"### 📋 一致性检查：{result.validation_summary or '无'}\n"]
        if result.validation_issues:
            for vi in result.validation_issues:
                icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(
                    vi.severity, "⚪"
                )
                val_lines.append(
                    f"{icon} **[{vi.category}]** {vi.subject}：{vi.description}"
                )
                if vi.suggestion:
                    val_lines.append(f"   💡 {vi.suggestion}")
                val_lines.append("")
        else:
            val_lines.append("✅ 未发现一致性问题")
        validation_md = "\n".join(val_lines)

        return title, status, content, changes_md, validation_md
    except Exception as exc:
        session.rollback()
        return "", f"❌ 生成失败：{exc}", "", "", ""
    finally:
        session.close()


def handle_polish_chapter(chapter_id: str) -> tuple[str, str, str, str, str]:
    """Fix consistency issues + polish prose. Validation runs first to find targets.
    Returns (title_md, status_md, content, changes_md, validation_md)."""
    if not chapter_id:
        return "", "❌ 请先定位到已生成的章节", "", "", ""
    session = _session()
    try:
        result = book_flow.polish_chapter_draft(session, chapter_id, gateway=gateway)
        session.commit()

        orig = result.context_manifest.get("original_word_count", "?")
        new_wc = result.context_manifest.get("polished_word_count", "?")
        issues_before = result.context_manifest.get("issues_before_fix", 0)
        issues_after = result.context_manifest.get("issues_after_fix", 0)
        before_issues = result.context_manifest.get("before_issues", [])

        title = f"### 第{result.chapter.number}章：{result.version.title}"

        # Build fix summary
        if issues_before > 0:
            status_lines = [
                f"🔧 修复完成 | 原 {orig} 字 → 现 {new_wc} 字",
                f"修复前 **{issues_before}** 个问题 → 修复后 **{issues_after}** 个问题",
                "",
                "**修复目标：**",
            ]
            for bi in before_issues:
                icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(
                    bi.get("severity", ""), "⚪"
                )
                status_lines.append(
                    f"{icon} [{bi.get('category', '')}] {bi.get('subject', '')}：{bi.get('description', '')}"
                )
            status = "\n".join(status_lines)
        else:
            status = f"✨ 润色完成（未发现一致性问题）| 原 {orig} 字 → 现 {new_wc} 字"
        content = result.version.content

        changes_lines = [f"**提取 {len(result.changes)} 条候选事实：**\n"]
        for chg in result.changes:
            changes_lines.append(
                f"- [{chg.kind}] {chg.subject}: {chg.claim} "
                f"（置信度: {chg.confidence:.2f}）"
            )
        changes_md = "\n".join(changes_lines)

        val_lines = [f"### 📋 一致性检查：{result.validation_summary or '无'}\n"]
        if result.validation_issues:
            for vi in result.validation_issues:
                icon = {"error": "🔴", "warning": "🟡", "info": "🔵"}.get(
                    vi.severity, "⚪"
                )
                val_lines.append(
                    f"{icon} **[{vi.category}]** {vi.subject}：{vi.description}"
                )
                if vi.suggestion:
                    val_lines.append(f"   💡 {vi.suggestion}")
                val_lines.append("")
        else:
            val_lines.append("✅ 未发现一致性问题")
        validation_md = "\n".join(val_lines)

        return title, status, content, changes_md, validation_md
    except Exception as exc:
        session.rollback()
        return "", f"❌ 润色失败：{exc}", "", "", ""
    finally:
        session.close()


def handle_confirm_and_refresh(
    chapter_id: str, force: bool, book_id: str
) -> tuple[str, str]:
    """Confirm chapter then refresh chapter list.
    Returns (confirm_status, chapters_list)."""
    if not chapter_id:
        return "❌ 没有可确认的章节", ""
    session = _session()
    try:
        result = book_flow.confirm_chapter(session, chapter_id, force=force)
        session.commit()
        status = f"✅ 已确认！状态：{result.get('status')} | 新增事实：{result.get('facts_added', 0)}"

        # Refresh chapter list
        chapters = book_flow.list_chapters(session, book_id)
        lines = []
        for ch in chapters:
            icon = {"planned": "📋", "drafted": "📝", "confirmed": "✅"}.get(
                ch.status, "❓"
            )
            lines.append(
                f"{icon} 第{ch.number}章《{ch.title}》— {ch.status} ({ch.target_words}字)\n"
                f"   `{ch.id}`"
            )
        return status, "\n".join(lines)
    except Exception as exc:
        session.rollback()
        return f"❌ 确认失败：{exc}", ""
    finally:
        session.close()


def handle_list_chapters_info(book_id: str) -> str:
    """List all chapters for this book with IDs."""
    if not book_id:
        return ""
    session = _session()
    try:
        chapters = book_flow.list_chapters(session, book_id)
        lines = []
        for ch in chapters:
            icon = {"planned": "📋", "drafted": "📝", "confirmed": "✅"}.get(
                ch.status, "❓"
            )
            lines.append(
                f"{icon} 第{ch.number}章《{ch.title}》— {ch.status} ({ch.target_words}字)\n"
                f"   `{ch.id}`"
            )
        return "\n".join(lines)
    except Exception as exc:
        return f"❌ {exc}"
    finally:
        session.close()


def handle_view_chapter(chapter_id: str) -> tuple[str, str]:
    """View chapter detail by ID."""
    if not chapter_id:
        return "", "❌ 请粘贴章节 ID"
    session = _session()
    try:
        detail = book_flow.get_chapter(session, chapter_id)
        if detail.current_version:
            v = detail.current_version
            title = f"第{detail.chapter.number}章：{v.title} | 字数：{v.word_count} | 状态：{v.status}"
            return title, v.content
        return f"第{detail.chapter.number}章：{detail.chapter.title}（无内容）", ""
    except Exception as exc:
        return "", f"❌ {exc}"
    finally:
        session.close()


def handle_get_chapter_choices(book_id: str) -> gr.Dropdown:
    """Return chapter dropdown choices for a book."""
    if not book_id:
        return gr.Dropdown(choices=[], value=None)
    session = _session()
    try:
        chapters = book_flow.list_chapters(session, book_id)
        choices = [
            (f"第{ch.number}章《{ch.title}》— {ch.status} ({ch.target_words}字)", ch.id)
            for ch in chapters
        ]
        return gr.Dropdown(choices=choices, value=chapters[0].id if chapters else None)
    except Exception:
        return gr.Dropdown(choices=[], value=None)
    finally:
        session.close()


# ── Step ⑥  Complete & Export ────────────────────────────────────────────────


def handle_complete_book(book_id: str) -> str:
    if not book_id:
        return "❌ 请先创建书籍"
    session = _session()
    try:
        result = book_flow.complete_book(session, book_id)
        session.commit()
        return (
            f"✅ 完本成功！\n\n"
            f"📊 章节数：{result.chapter_count}\n"
            f"📊 总字数：{result.total_chars}\n"
            f"📝 摘要：{result.summary}"
        )
    except Exception as exc:
        session.rollback()
        return f"❌ 完本失败：{exc}"
    finally:
        session.close()


def handle_export(book_id: str, scope: str) -> tuple[str, str | None]:
    if not book_id:
        return "❌ 请先创建书籍", None
    scope = scope.split(" - ")[0] if " - " in scope else scope
    session = _session()
    try:
        result = book_flow.export_book_txt(session, book_id, scope=scope)
        session.commit()
        info = (
            f"✅ 导出成功！\n\n"
            f"📄 文件：{result.path}\n"
            f"📊 字数：{result.chars}\n"
            f"📊 章节数：{result.chapter_count}/{result.planned_chapter_count}\n"
            f"🎯 范围：{result.scope}"
        )
        return info, result.path
    except Exception as exc:
        session.rollback()
        return f"❌ 导出失败：{exc}", None
    finally:
        session.close()


# ── Basic Tier: One-click full book generation ────────────────────────────────


def handle_one_click_generate(title: str) -> tuple[str, str | None]:
    """基础版：只需书名→一键生成整本小说。"""
    if not title.strip():
        return "❌ 请输入书名", None

    log_lines: list[str] = []
    session = _session()
    try:
        log_lines.append(f"📖 开始创作《{title.strip()}》...")
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=title.strip(),
                genre="",
                style="",
                length="short",
                perspective="第三人称",
                tone="",
                premise=f"《{title.strip()}》",
            ),
        )
        session.commit()
        log_lines.append("✅ 书籍创建成功")

        result = book_flow.generate_full_book(session, book.id, gateway=gateway)
        session.commit()

        for msg in result["log"]:
            log_lines.append(msg)

        if result["success"]:
            log_lines.append(f"\n📥 下载：{result['path']}")
            return "\n".join(log_lines), result["path"]
        else:
            log_lines.append(f"\n❌ {result.get('error', '未知错误')}")
            return "\n".join(log_lines), None
    except Exception as exc:
        session.rollback()
        log_lines.append(f"❌ {exc}")
        return "\n".join(log_lines), None
    finally:
        session.close()


# ── Advanced Tier: Custom outline ─────────────────────────────────────────────


def handle_upload_outline_file(book_id: str, file: str | None) -> tuple[str, str, str]:
    """高级版：上传文件→解析内容→导入大纲。Returns (status, outline_display, extracted_text)."""
    if not book_id:
        return "❌ 请先创建书籍", "", ""
    if not file:
        return "❌ 请上传文件", "", ""
    try:
        content = Path(file).read_text(encoding="utf-8")
    except Exception as exc:
        return f"❌ 无法读取文件：{exc}", "", ""
    if not content.strip():
        return "❌ 文件为空", "", ""

    # Try to parse — let import_custom_outline handle the structure
    return handle_import_outline(book_id, content)


def handle_import_outline(book_id: str, outline_text: str) -> tuple[str, str]:
    """高级版：导入用户自定义大纲。Returns (status, outline_display)."""
    if not book_id:
        return "❌ 请先创建书籍", ""
    if not outline_text.strip():
        return "❌ 请粘贴大纲内容", ""
    session = _session()
    try:
        nodes = book_flow.import_custom_outline(session, book_id, outline_text)
        session.commit()
        lines = [f"✅ 导入 {len(nodes)} 个大纲节点\n"]
        for n in nodes:
            indent = "  " * (
                1 if n.level == "arc" else 2 if n.level == "chapter_goal" else 0
            )
            lines.append(f"{indent}[{n.level}] {n.title}")
            if n.summary:
                lines.append(f"{indent}  {n.summary}")
        return "✅ 大纲导入成功！请锁定大纲后生成章节。", "\n".join(lines)
    except Exception as exc:
        session.rollback()
        return f"❌ 导入失败：{exc}", ""
    finally:
        session.close()


def handle_generate_from_custom(book_id: str) -> tuple[str, str | None]:
    """高级版：基于已导入大纲生成整本小说。Returns (log_md, file_path)."""
    if not book_id:
        return "❌ 请先创建书籍并导入大纲", None
    log_lines: list[str] = []
    session = _session()
    try:
        book = book_flow.get_book(session, book_id)
        if not book.outline_locked:
            log_lines.append("🔒 锁定大纲...")
            book_flow.lock_outline(session, book_id)
            session.commit()
            log_lines.append("✅ 大纲已锁定")

        if not book.characters_locked:
            log_lines.append("📝 自动生成人物...")
            book_flow.generate_characters(session, book_id, gateway=gateway)
            session.commit()
            book_flow.lock_characters(session, book_id)
            session.commit()
            log_lines.append("✅ 人物已生成并锁定")

        log_lines.append("📊 章节规划...")
        chapters = book_flow.plan_chapters(session, book_id)
        session.commit()
        log_lines.append(f"✅ 共 {len(chapters)} 章")

        for i, ch in enumerate(chapters):
            log_lines.append(f"✍️ 写作第 {i+1}/{len(chapters)} 章《{ch.title}》...")
            book_flow.generate_chapter(session, ch.id, gateway=gateway)
            session.commit()
            book_flow.confirm_chapter(session, ch.id, force=True)
            session.commit()
            log_lines.append(f"✅ 第 {i+1}/{len(chapters)} 章完成")

        log_lines.append("🏁 完本...")
        book_flow.complete_book(session, book_id)
        session.commit()

        log_lines.append("📥 导出...")
        result = book_flow.export_book_txt(session, book_id, scope="all")
        session.commit()
        log_lines.append(f"✅ 导出至 {result.path}")

        return "\n".join(log_lines), result.path
    except Exception as exc:
        session.rollback()
        log_lines.append(f"❌ {exc}")
        return "\n".join(log_lines), None
    finally:
        session.close()


def handle_parse_and_create(file: str | None) -> tuple[str, str, str]:
    """高级版：上传文件→创建书籍→解析大纲。Returns (status, book_id, content)."""
    if not file:
        return "❌ 请上传文件", "", ""
    try:
        content = Path(file).read_text(encoding="utf-8")
    except Exception as exc:
        return f"❌ 无法读取文件：{exc}", "", ""
    if not content.strip():
        return "❌ 文件为空", "", ""

    # Auto-create book from file content
    session = _session()
    try:
        # Use filename as title, first line as premise
        fname = Path(file).stem
        first_line = content.strip().split("\n")[0][:100]
        title = fname if len(fname) <= 50 else fname[:50]

        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=title, genre="", style="", length="short",
                perspective="第三人称", tone="", premise=first_line,
            ),
        )
        session.commit()

        # Import outline from file content
        nodes = book_flow.import_custom_outline(session, book.id, content)
        session.commit()

        lines = [f"✅ 项目创建成功\n📖 书名：{title}\n📋 解析到 {len(nodes)} 个大纲节点\n"]
        for n in nodes:
            indent = "  " * (1 if n.level == "arc" else 2 if n.level == "chapter_goal" else 0)
            lines.append(f"{indent}[{n.level}] {n.title}")

        return "\n".join(lines), book.id, content
    except Exception as exc:
        session.rollback()
        return f"❌ 解析失败：{exc}", "", ""
    finally:
        session.close()


def handle_generate_from_upload(book_id: str, content: str) -> tuple[str, str | None]:
    """高级版：从上传的大纲生成整本小说。"""
    if not book_id:
        return "❌ 请先上传并解析文件", None

    log_lines: list[str] = []
    session = _session()
    try:
        book = book_flow.get_book(session, book_id)

        # Lock outline
        if not book.outline_locked:
            log_lines.append("🔒 锁定大纲...")
            book_flow.lock_outline(session, book_id)
            session.commit()

        # Auto-generate characters
        log_lines.append("📝 生成人物...")
        book_flow.generate_characters(session, book_id, gateway=gateway)
        session.commit()
        book_flow.lock_characters(session, book_id)
        session.commit()
        log_lines.append("✅ 人物已生成")

        # Plan chapters
        log_lines.append("📊 章节规划...")
        chapters = book_flow.plan_chapters(session, book_id)
        session.commit()
        log_lines.append(f"✅ 共 {len(chapters)} 章")

        # Write all chapters
        for i, ch in enumerate(chapters):
            log_lines.append(f"✍️ 写作第 {i+1}/{len(chapters)} 章《{ch.title}》...")
            book_flow.generate_chapter(session, ch.id, gateway=gateway)
            session.commit()
            book_flow.confirm_chapter(session, ch.id, force=True)
            session.commit()
            log_lines.append(f"✅ 第 {i+1}/{len(chapters)} 章完成")

        # Complete & export
        log_lines.append("🏁 完本...")
        book_flow.complete_book(session, book_id)
        session.commit()

        log_lines.append("📥 导出...")
        result = book_flow.export_book_txt(session, book_id, scope="all")
        session.commit()
        log_lines.append(f"✅ 导出至 {result.path}")

        return "\n".join(log_lines), result.path
    except Exception as exc:
        session.rollback()
        log_lines.append(f"❌ {exc}")
        return "\n".join(log_lines), None
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Gradio UI
# ═══════════════════════════════════════════════════════════════════════════════

CSS = """
/* ═══════════════════════════════════════════════════════════════════════
   Novel-Agent — Notion-AI Style Writing Workbench
   #F7F6F3 · Inter · Subtle Glass · Restrained & Quiet
   ═══════════════════════════════════════════════════════════════════════ */

@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

:root {
  --bg: #F7F6F3;
  --card-bg: rgba(255,255,255,0.68);
  --card-border: rgba(0,0,0,0.06);
  --text-heading: #1A1A1A;
  --text-body: #555555;
  --text-muted: #999999;
  --btn-primary: #C4A882;
  --btn-primary-hover: #B8956E;
  --btn-primary-text: #FFFFFF;
  --btn-secondary-bg: #FFFFFF;
  --btn-secondary-border: #E0E0E0;
  --btn-secondary-text: #555555;
  --input-border: #E5E5E0;
  --input-focus: #B8A088;
  --tab-active-bg: rgba(0,0,0,0.03);
  --tab-active-line: #8B7355;
  --tab-inactive-text: #AAAAAA;
  --radius-sm: 8px;
  --radius-md: 10px;
}

* { box-sizing: border-box; }

body, .gradio-container {
  font-family: 'Inter', 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif !important;
  background: var(--bg) !important;
  color: var(--text-body) !important;
  -webkit-font-smoothing: antialiased !important;
  min-height: 100vh !important;
}

.gradio-container {
  max-width: 1060px !important;
  width: 94% !important;
  margin: 0 auto !important;
  padding: 32px 20px !important;
}

/* ── Typography ──────────────────────────────────────────────────────── */
h1 { font-size: 1.6em !important; font-weight: 700 !important; color: var(--text-heading) !important; letter-spacing: -0.02em !important; }
h2 { font-size: 1.25em !important; font-weight: 600 !important; color: var(--text-heading) !important; }
h3 { font-size: 1.1em !important; font-weight: 600 !important; color: var(--text-heading) !important; }
h4 { font-size: 1em !important; font-weight: 600 !important; color: var(--text-heading) !important; }
.gradio-container label { color: var(--text-body) !important; font-weight: 500 !important; font-size: 0.85em !important; }
.gradio-container .prose, .gradio-container .md, .gradio-container p { color: var(--text-body) !important; }

/* ── Top Header ──────────────────────────────────────────────────────── */
.gradio-container > .prose:first-of-type h1 { margin-bottom: 4px !important; }
.gradio-container > .prose:first-of-type p { color: var(--text-muted) !important; font-size: 0.9em !important; }

/* ── Capsule Tabs ────────────────────────────────────────────────────── */
.gradio-container .tabs { gap: 4px !important; border-bottom: 1px solid #EBEBE8 !important; padding-bottom: 0 !important; margin-bottom: 28px !important; }
.gradio-container button[class*="tab"] {
  background: transparent !important;
  border: none !important;
  border-radius: var(--radius-sm) !important;
  color: var(--tab-inactive-text) !important;
  font-weight: 500 !important;
  font-size: 0.9em !important;
  padding: 8px 18px !important;
  margin-right: 2px !important;
  transition: all 0.2s ease !important;
  box-shadow: none !important;
  min-height: auto !important;
}
.gradio-container button[class*="tab"]:hover {
  background: var(--tab-active-bg) !important;
  color: var(--text-body) !important;
}
.gradio-container button[class*="tab"][class*="selected"] {
  background: var(--tab-active-bg) !important;
  color: var(--text-heading) !important;
  font-weight: 600 !important;
  border-bottom: 2px solid var(--tab-active-line) !important;
  border-radius: var(--radius-sm) var(--radius-sm) 0 0 !important;
}

/* ── Cards (subtle glass, ONLY on functional panels) ─────────────────── */
.gradio-container .gr-box,
.gradio-container .gr-panel,
.gradio-container .gr-group,
.gradio-container fieldset {
  background: var(--card-bg) !important;
  backdrop-filter: blur(8px) !important;
  -webkit-backdrop-filter: blur(8px) !important;
  border: 1px solid var(--card-border) !important;
  border-radius: var(--radius-md) !important;
  box-shadow: none !important;
}
/* Other containers: no glass */
gradio-app,
.gradio-app,
.gradio-container .app {
  background: transparent !important;
  color: var(--text-body) !important;
}
.gradio-container .gr-form,
.gradio-container [data-testid="block"],
.gradio-container [class*="container"]:not(.gradio-container),
.gradio-container [class*="panel"],
.gradio-container [class*="block"] {
  background: transparent !important;
  backdrop-filter: none !important;
  -webkit-backdrop-filter: none !important;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
}

/* ── Kill white boxes on File / Dataframe / Markdown / Code containers ─── */
.gradio-container [class*="file"],
.gradio-container [class*="File"],
.gradio-container [class*="dataframe"],
.gradio-container [class*="Dataframe"],
.gradio-container [class*="markdown"],
.gradio-container [class*="Markdown"],
.gradio-container [class*="code"],
.gradio-container [class*="Code"],
.gradio-container [class*="json"],
.gradio-container [class*="Json"],
.gradio-container .prose,
.gradio-container [class*="wrap"][class*="svelte-"],
.gradio-container [class*="upload"],
.gradio-container [class*="download"],
.gradio-container [class*="file-label"],
.gradio-container [data-testid="file"] {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  outline: none !important;
}

/* ── File empty-value placeholder: completely invisible ────────────────── */
.gradio-container .empty,
.gradio-container div[aria-label="Empty value"] {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 0 !important;
  min-height: 0 !important;
  outline: none !important;
}
.gradio-container .empty .icon,
.gradio-container div[aria-label="Empty value"] .icon {
  opacity: 0.25 !important;
}

/* ── Kill tan/cream block on native form wrappers (Gradio 6.22) ───────── */
.gradio-container .form,
.gradio-container .gradio-container .form,
.gradio-container form,
.gradio-container .gradio-container [class*="field"],
.gradio-container .gradio-container [class*="textbox-wrap"] {
  background: transparent !important;
  border: none !important;
  border-radius: 0 !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 0 !important;
}
.gradio-container .form .input-container,
.gradio-container form .input-container {
  background: transparent !important;
  border: none !important;
}

/* ── File / component label chip: transparent, no colored pill ─────────── */
.gradio-container label[data-testid="block-label"],
.gradio-container .gradio-container [data-testid="block-label"],
.gradio-container div[data-testid="block-label"] {
  background: transparent !important;
  border: none !important;
  box-shadow: none !important;
  padding: 0 !important;
  margin: 2px 0 4px !important;
}
.gradio-container label[data-testid="block-label"] span:first-child svg {
  width: 14px !important;
  height: 14px !important;
}

/* ── Inputs: minimal line border ─────────────────────────────────────── */
.gradio-container input[type="text"],
.gradio-container input[type="number"],
.gradio-container input:not([type]),
.gradio-container textarea,
.gradio-container select {
  background: #FFFFFF !important;
  backdrop-filter: none !important;
  border: 1px solid var(--input-border) !important;
  border-radius: var(--radius-sm) !important;
  color: var(--text-heading) !important;
  font-size: 0.9em !important;
  padding: 9px 13px !important;
  box-shadow: none !important;
  transition: border-color 0.2s ease !important;
  min-height: 40px !important;
}
.gradio-container input:focus,
.gradio-container textarea:focus {
  border-color: var(--input-focus) !important;
  box-shadow: 0 0 0 2px rgba(184,160,136,0.12) !important;
  outline: none !important;
}

/* ── Buttons: clean, no outlines ─────────────────────────────────────── */
.gradio-container button {
  font-family: inherit !important;
  border-radius: var(--radius-sm) !important;
  padding: 9px 20px !important;
  font-weight: 500 !important;
  font-size: 0.88em !important;
  cursor: pointer !important;
  border: none !important;
  outline: none !important;
  box-shadow: none !important;
  min-height: 38px !important;
  transition: background 0.15s ease, border-color 0.15s ease !important;
  transform: none !important;
}
.gradio-container button:hover { transform: none !important; box-shadow: none !important; outline: none !important; }
.gradio-container button:active { transform: none !important; outline: none !important; }
.gradio-container button:focus { outline: none !important; box-shadow: none !important; }
.gradio-container button:focus-visible { outline: none !important; box-shadow: none !important; }

/* Primary: low-sat caramel */
.gradio-container button.primary,
.gradio-container button[variant="primary"] {
  background: var(--btn-primary) !important;
  color: var(--btn-primary-text) !important;
  font-weight: 600 !important;
}
.gradio-container button.primary:hover,
.gradio-container button[variant="primary"]:hover {
  background: var(--btn-primary-hover) !important;
}

/* Secondary: white + gray border */
.gradio-container button.secondary,
.gradio-container button[variant="secondary"] {
  background: var(--btn-secondary-bg) !important;
  color: var(--btn-secondary-text) !important;
  border: 1px solid var(--btn-secondary-border) !important;
  font-weight: 500 !important;
}
.gradio-container button.secondary:hover,
.gradio-container button[variant="secondary"]:hover {
  background: #F9F9F8 !important;
  border-color: #CCC !important;
}

/* ── Dropdowns & Radios ──────────────────────────────────────────────── */
.gradio-container [class*="dropdown"] input { background: #FFF !important; border: 1px solid var(--input-border) !important; }
.gradio-container input[type="radio"]:checked,
.gradio-container input[type="checkbox"]:checked { accent-color: var(--tab-active-line) !important; }

/* ── Content area (serif for reading) ───────────────────────────────── */
.content-area textarea {
  font-family: 'Inter', 'PingFang SC', 'Noto Serif CJK SC', 'STSong', serif !important;
  font-size: 15px !important;
  line-height: 1.85 !important;
  background: #FFFFFF !important;
}

/* ── Log output area ─────────────────────────────────────────────────── */
.log-area textarea, .log-area {
  background: #F5F4F1 !important;
  border: 1px solid #E8E7E4 !important;
  border-radius: var(--radius-sm) !important;
  color: var(--text-body) !important;
  font-size: 0.85em !important;
}

/* ── File upload ─────────────────────────────────────────────────────── */
.gradio-container [class*="file"] {
  background: #FAFAF8 !important;
  border: 1px solid #E5E5E0 !important;
  border-radius: var(--radius-sm) !important;
  padding: 20px !important;
}
.gradio-container [class*="file"]:hover {
  border-color: #CCC !important;
  background: #F8F7F4 !important;
}

/* ── Global: kill all outlines/focus rings ───────────────────────────── */
*, *:focus, *:focus-visible, *:active {
  outline: none !important;
}
.gradio-container [class*="ring"], .gradio-container [class*="focus"] {
  box-shadow: none !important;
  outline: none !important;
}

/* ── Spacing ─────────────────────────────────────────────────────────── */
.gr-box, .gr-panel, .gr-group { padding: 24px !important; margin-bottom: 20px !important; }
.gr-row { gap: 20px !important; }

/* ── Scrollbar ───────────────────────────────────────────────────────── */
::-webkit-scrollbar { width: 5px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #D5D3CE; border-radius: 3px; }

footer { display: none !important; }
"""









def build_ui() -> gr.Blocks:
    with gr.Blocks(title="Novel-Agent") as app:

        gr.Markdown("""
        # Novel-Agent 小说创作助手
        本地大模型驱动 · 安静克制的写作工作台
        """)

        book_id_state = gr.State("")

        with gr.Tabs():
            with gr.TabItem("基础版"):
                gr.Markdown("### 输入书名，一键成书")
                gr.Markdown("系统自动完成人物设计、大纲构思、全部章节写作与导出。")

                with gr.Row():
                    basic_title = gr.Textbox(label="书名", placeholder="为你脑海中的故事起个名字", scale=4)
                    basic_btn = gr.Button("开始创作", variant="primary", scale=1, min_width=120)

                basic_log = gr.Markdown("")
                basic_download = gr.File(label="下载小说")
                basic_btn.click(fn=handle_one_click_generate, inputs=[basic_title], outputs=[basic_log, basic_download])
            with gr.TabItem("进阶版"):
                gr.Markdown("### 逐步引导，精细控制")

                with gr.Tabs():
                    with gr.TabItem("创作卡片"):
                        with gr.Row():
                            with gr.Column(scale=2):
                                title = gr.Textbox(label="书名", placeholder="为你的小说起个名字")
                                premise = gr.Textbox(label="一句话梗概", placeholder="港口档案馆夜班管理员发现与失踪妹妹相关的灯塔日志...", lines=2)
                            with gr.Column(scale=3):
                                genre = gr.Dropdown(label="类型", choices=["都市","悬疑","玄幻","言情","科幻","历史","武侠","恐怖","其他"], value="悬疑")
                                style = gr.Dropdown(label="风格", choices=["网文通俗","轻松","严肃","爆款网文","文学性"], value="网文通俗")
                                tone = gr.Dropdown(label="基调", choices=["暗黑","热血","治愈","悲剧","轻松","悬疑"], value="暗黑")
                                length = gr.Dropdown(label="篇幅", choices=["short - 短篇 (~3万字)","medium - 中篇 (~12万字)","long - 长篇 (~30万字)"], value="short - 短篇 (~3万字)")
                                perspective = gr.Dropdown(label="视角", choices=["第一人称","第三人称","多视角"], value="第三人称")
                        create_btn = gr.Button("创建项目", variant="primary")
                        create_status = gr.Markdown("")
                        create_json = gr.Code(label="项目详情", language="json", visible=False)
                        create_btn.click(
                            fn=handle_create_book,
                            inputs=[title, genre, style, length, perspective, tone, premise],
                            outputs=[book_id_state, create_status, create_json])
                    with gr.TabItem("人物设计"):
                        gr.Markdown("基于创作卡片设计主要人物。锁定后大纲围绕角色展开。")
                        gen_chars_btn = gr.Button("生成人物", variant="primary")
                        chars_status = gr.Markdown("")
                        chars_display = gr.Markdown("")
                        gen_chars_btn.click(fn=handle_generate_characters, inputs=[book_id_state], outputs=[chars_status, chars_display])
                        gr.Markdown("---")
                        refresh_chars_btn = gr.Button("刷新列表", variant="secondary")
                        chars_table = gr.Dataframe(headers=["ID","姓名","角色","性格","外貌","背景","版本"], label="人物列表", interactive=False)
                        refresh_chars_btn.click(fn=handle_list_characters, inputs=[book_id_state], outputs=[chars_status, chars_table])
                        lock_chars_btn = gr.Button("锁定人物", variant="secondary")
                        lock_chars_btn.click(fn=handle_lock_characters, inputs=[book_id_state], outputs=[chars_status])
                    with gr.TabItem("大纲"):
                        gr.Markdown("基于已锁定人物生成分层大纲。")
                        gen_outline_btn = gr.Button("生成大纲", variant="primary")
                        outline_status = gr.Markdown("")
                        outline_display = gr.Markdown("")
                        gen_outline_btn.click(fn=handle_generate_outline, inputs=[book_id_state], outputs=[outline_status, outline_display])
                        gr.Markdown("---")
                        refresh_outline_btn = gr.Button("刷新列表", variant="secondary")
                        outline_table = gr.Dataframe(headers=["ID","层级","标题","摘要","版本","状态"], label="大纲节点", interactive=False)
                        refresh_outline_btn.click(fn=handle_list_outline, inputs=[book_id_state], outputs=[outline_status, outline_table])
                        lock_outline_btn = gr.Button("锁定大纲", variant="secondary")
                        lock_outline_btn.click(fn=handle_lock_outline, inputs=[book_id_state], outputs=[outline_status])
                    with gr.TabItem("章节规划"):
                        gr.Markdown("大纲节点映射为章节，按篇幅均分字数。")
                        plan_btn = gr.Button("生成规划", variant="primary")
                        plan_status = gr.Markdown("")
                        plan_display = gr.Markdown("")
                        plan_btn.click(fn=handle_plan_chapters, inputs=[book_id_state], outputs=[plan_status, plan_display])
                    with gr.TabItem("逐章写作"):
                        refresh_list_btn = gr.Button("刷新章节列表", variant="secondary")
                        chapters_list = gr.Markdown("")
                        refresh_list_btn.click(fn=handle_list_chapters_info, inputs=[book_id_state], outputs=[chapters_list])
                        gr.Markdown("---")
                        next_chapter_id_state = gr.State("")
                        locate_btn = gr.Button("定位到下一章", variant="secondary")
                        target_info = gr.Markdown("点击按钮加载待写章节")
                        gr.Markdown("---")
                        chapter_title_display = gr.Markdown("")
                        chapter_status_display = gr.Markdown("")
                        chapter_content = gr.Textbox(label="章节正文", lines=20, max_lines=40, elem_classes=["content-area"])
                        chapter_changes = gr.Textbox(label="候选事实", lines=4)
                        chapter_validation = gr.Markdown("")
                        with gr.Row():
                            write_btn = gr.Button("生成/重写本章", variant="primary")
                            polish_btn = gr.Button("修复 + 润色", variant="secondary")
                        locate_btn.click(fn=handle_locate_target, inputs=[book_id_state], outputs=[next_chapter_id_state, target_info, chapter_content, chapter_title_display, chapter_changes])
                        write_btn.click(fn=handle_write_target, inputs=[next_chapter_id_state], outputs=[chapter_title_display, chapter_status_display, chapter_content, chapter_changes, chapter_validation])
                        polish_btn.click(fn=handle_polish_chapter, inputs=[next_chapter_id_state], outputs=[chapter_title_display, chapter_status_display, chapter_content, chapter_changes, chapter_validation])
                        gr.Markdown("---")
                        with gr.Row():
                            confirm_btn = gr.Button("确认本章", variant="primary")
                            force_cb = gr.Checkbox(label="强制确认", value=False)
                        confirm_status = gr.Markdown("")
                        confirm_btn.click(fn=handle_confirm_and_refresh, inputs=[next_chapter_id_state, force_cb, book_id_state], outputs=[confirm_status, chapters_list])
                        gr.Markdown("---")
                        gr.Markdown("#### 预览章节")
                        with gr.Row():
                            refresh_dropdown_btn = gr.Button("加载列表", variant="secondary")
                            chapter_dropdown = gr.Dropdown(label="选择章节", choices=[], interactive=True, scale=3)
                        refresh_dropdown_btn.click(fn=handle_get_chapter_choices, inputs=[book_id_state], outputs=[chapter_dropdown])
                        view_title = gr.Markdown("")
                        view_content = gr.Textbox(label="章节内容", lines=15, elem_classes=["content-area"])
                        chapter_dropdown.change(fn=handle_view_chapter, inputs=[chapter_dropdown], outputs=[view_title, view_content])
                    with gr.TabItem("完本 & 导出"):
                        gr.Markdown("全部章节确认后标记完本并导出。")
                        complete_btn = gr.Button("完本", variant="primary")
                        complete_status = gr.Markdown("")
                        complete_btn.click(fn=handle_complete_book, inputs=[book_id_state], outputs=[complete_status])
                        gr.Markdown("---")
                        with gr.Row():
                            export_scope = gr.Dropdown(label="导出范围", choices=["confirmed - 仅已确认章节","all - 全部章节"], value="confirmed - 仅已确认章节")
                            export_btn = gr.Button("导出 TXT", variant="primary")
                        export_status = gr.Markdown("")
                        export_file = gr.File(label="下载文件")
                        export_btn.click(fn=handle_export, inputs=[book_id_state, export_scope], outputs=[export_status, export_file])
            with gr.TabItem("高级版"):
                gr.Markdown("### 上传大纲文件，自动解析并生成小说")
                gr.Markdown("支持 `.txt` 或 `.md` 格式。系统自动识别章节结构。")

                adv_file = gr.File(label="上传大纲文件", file_types=[".txt", ".md"])

                with gr.Row():
                    adv_parse_btn = gr.Button("解析文件", variant="secondary")
                    adv_generate_btn = gr.Button("生成小说", variant="primary", min_width=140)

                adv_outline_status = gr.Markdown("")
                adv_log = gr.Markdown("")
                adv_download = gr.File(label="下载小说")
                adv_book_state = gr.State("")
                adv_content_state = gr.State("")

                adv_parse_btn.click(fn=handle_parse_and_create, inputs=[adv_file], outputs=[adv_outline_status, adv_book_state, adv_content_state])
                adv_generate_btn.click(fn=handle_generate_from_upload, inputs=[adv_book_state, adv_content_state], outputs=[adv_log, adv_download])

    return app

if __name__ == "__main__":
    theme = gr.themes.Soft(
        primary_hue="orange",
        secondary_hue="amber",
        neutral_hue="stone",
    ).set(
        body_background_fill="*neutral_50",
        block_background_fill="transparent",
        block_border_width="0px",
        block_label_background_fill="transparent",
        button_primary_background_fill="rgba(255, 248, 235, 0.65)",
        button_primary_text_color="#5C3D2E",
        button_primary_border_color="rgba(255, 200, 140, 0.35)",
        button_secondary_background_fill="rgba(255, 250, 240, 0.50)",
        button_secondary_text_color="#6B4F3A",
        button_secondary_border_color="rgba(230, 195, 155, 0.35)",
        input_background_fill="rgba(255,252,245,0.7)",
        input_border_color="#F0DCC0",
        input_border_color_focus="#E8A86C",
        color_accent="#E8A86C",
        border_color_primary="#F0DCC0",
        background_fill_primary="#FFF8E7",
        background_fill_secondary="#FDEBD0",
    )

    build_ui().launch(
        server_name="127.0.0.1",
        server_port=7860,
        share=False,
        show_error=True,
        css=CSS,
        theme=theme,
    )
