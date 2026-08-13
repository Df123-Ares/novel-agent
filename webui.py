"""Novel-Agent Web UI — Flask + 暖阳奶油风前端

Launch:  python webui.py
Open:    http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 保证以 `python webui.py` 直接运行时能导入 src 下的 novel_agent 包
_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from flask import Flask, jsonify, render_template, request, send_from_directory
from sqlalchemy.orm import Session

from novel_agent.application.workflows import book_flow
from novel_agent.bootstrap import ensure_data_dirs
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.infrastructure.llm.gateway import LLMGateway
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    get_session_factory,
)
from novel_agent.settings import get_settings

app = Flask(__name__)

# ── bootstrap ────────────────────────────────────────────────────────────────
settings = ensure_data_dirs(get_settings())
engine = get_engine(settings)
create_all_tables(engine)
SessionLocal = get_session_factory(settings)
gateway = LLMGateway(settings=settings)

DATA_EXPORTS = settings.export_dir


def _session() -> Session:
    return SessionLocal()


def _gateway_for(model: str) -> LLMGateway:
    """Rebuild the gateway against a user-selected model."""
    global gateway
    if model and model != settings.ollama_model:
        settings.ollama_model = model
        gateway = LLMGateway(settings=settings)
    return gateway


# ═══════════════════════════════════════════════════════════════════════════════
#  Pages
# ═══════════════════════════════════════════════════════════════════════════════


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/download")
def download():
    name = request.args.get("name", "")
    if not name:
        return jsonify({"error": "missing name"}), 400
    safe = Path(name).name
    return send_from_directory(DATA_EXPORTS, safe, as_attachment=True)


# ═══════════════════════════════════════════════════════════════════════════════
#  Ollama health / models
# ═══════════════════════════════════════════════════════════════════════════════


def _installed_models() -> list[str]:
    try:
        return gateway.adapter.list_models()
    except Exception:
        return []


@app.route("/api/health")
def health():
    try:
        models = gateway.adapter.list_models()
    except Exception:
        return jsonify({"ollama": False, "model": settings.ollama_model, "reason": "request_failed"})
    model_names = [m for m in models if m]
    current = settings.ollama_model
    available = any(current in name for name in model_names)
    return jsonify({
        "ollama": True,
        "model": current,
        "modelAvailable": available,
        "installedModels": model_names,
    })


@app.route("/api/models")
def api_list_models():
    return jsonify({"models": _installed_models(), "current": settings.ollama_model})


@app.route("/api/models/select", methods=["POST"])
def api_select_model():
    data = request.get_json() or {}
    model = (data.get("model") or "").strip()
    installed = _installed_models()
    if not model:
        return jsonify({"error": "未指定模型"}), 400
    if installed and model not in installed:
        return jsonify({"error": f"模型「{model}」不在本机，可用: {', '.join(installed) or '（无）'}"}), 400
    _gateway_for(model)
    return jsonify({"ok": True, "current": settings.ollama_model, "models": installed or [model]})


@app.route("/api/models/auto-select", methods=["POST"])
def api_auto_select():
    installed = _installed_models()
    if not installed:
        return jsonify({
            "ok": False,
            "current": settings.ollama_model,
            "models": [],
            "error": "本机未检测到任何模型。请先运行: ollama pull qwen2.5:7b-instruct",
        })
    tested: list[str] = []
    chosen = None
    passed = False
    for model in installed:
        try:
            result = gateway.adapter.chat(
                [{"role": "user", "content": "请只回复两个字：你好"}],
                num_predict=5,
                temperature=0.1,
                model=model,
            )
            if (result.content or "").strip():
                chosen = model
                passed = True
                break
        except Exception:
            pass
        tested.append(model)
    if chosen is None:
        chosen = installed[0]
    _gateway_for(chosen)
    resp = {
        "ok": passed,
        "current": settings.ollama_model,
        "models": installed,
        "tested": tested,
    }
    if not passed:
        resp["error"] = f"所有模型的生成测试均失败（共 {len(installed)} 个），已暂时选中「{chosen}」。请检查 Ollama 是否正常运行。"
    return jsonify(resp)


@app.route("/api/test-generate")
def test_generate():
    try:
        result = gateway.adapter.chat(
            [{"role": "user", "content": "请只回复两个字：你好"}],
            num_predict=10,
            temperature=0.1,
        )
        return jsonify({"ok": True, "output": (result.content or "").strip()[:50]})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)})


# ═══════════════════════════════════════════════════════════════════════════════
#  基础版：一键成书
# ═══════════════════════════════════════════════════════════════════════════════


@app.route("/api/basic/generate", methods=["POST"])
def basic_generate():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    length = str(data.get("length") or "short").split(" - ")[0]
    if length not in {"short", "medium", "long"}:
        length = "short"
    if not title:
        return jsonify({"success": False, "log": "❌ 请输入书名"}), 400

    log_lines: list[str] = []
    session = _session()
    try:
        log_lines.append(f"📖 开始创作《{title}》...")
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=title, genre="", style="", length=length,
                perspective="第三人称", tone="", premise=f"《{title}》",
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
            return jsonify({"success": True, "log": "\n".join(log_lines), "download": Path(result["path"]).name})
        log_lines.append(f"\n❌ {result.get('error', '未知错误')}")
        return jsonify({"success": False, "log": "\n".join(log_lines)})
    except Exception as exc:
        session.rollback()
        log_lines.append(f"\n❌ {exc}")
        return jsonify({"success": False, "log": "\n".join(log_lines)})
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  进阶版：逐步引导
# ═══════════════════════════════════════════════════════════════════════════════


def _err(msg: str):
    return jsonify({"error": True, "status": msg})


@app.route("/api/guided/create", methods=["POST"])
def guided_create():
    data = request.get_json() or {}
    title = (data.get("title") or "").strip()
    if not title:
        return jsonify({"error": "请输入书名"})
    length = str(data.get("length") or "medium")
    length = length.split(" - ")[0] if " - " in length else length
    session = _session()
    try:
        book = book_flow.create_book(
            session,
            CreateBookRequest(
                title=title,
                genre=(data.get("genre") or "").strip(),
                style=(data.get("style") or "").strip(),
                length=length,
                perspective=(data.get("perspective") or "第三人称").strip(),
                tone=(data.get("tone") or "").strip(),
                premise=((data.get("premise") or "").strip() or title),
            ),
        )
        session.commit()
        return jsonify({
            "bookId": book.id,
            "title": book.title,
            "genre": book.genre,
            "style": book.style,
            "length": book.length,
            "perspective": book.perspective,
            "tone": book.tone,
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"error": f"❌ 创建失败：{exc}"})
    finally:
        session.close()


def _char_out(c) -> dict:
    return {
        "id": c.id, "name": c.name, "role": c.role,
        "personality": c.personality, "appearance": c.appearance,
        "background": c.background, "version": c.version,
    }


@app.route("/api/guided/characters/generate", methods=["POST"])
def guided_chars_generate():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        chars = book_flow.generate_characters(session, book_id, gateway=gateway)
        session.commit()
        return jsonify({"status": f"✅ 人物生成成功！共 {len(chars)} 个人物", "characters": [_char_out(c) for c in chars]})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 人物生成失败：{exc}", "characters": []})
    finally:
        session.close()


@app.route("/api/guided/characters/list", methods=["POST"])
def guided_chars_list():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        chars = book_flow.list_characters(session, book_id)
        return jsonify({"status": f"共 {len(chars)} 个人物", "characters": [_char_out(c) for c in chars]})
    except Exception as exc:
        return jsonify({"error": True, "status": f"❌ {exc}", "characters": []})
    finally:
        session.close()


@app.route("/api/guided/characters/lock", methods=["POST"])
def guided_chars_lock():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        book = book_flow.lock_characters(session, book_id)
        session.commit()
        return jsonify({"status": f"✅ 人物已锁定（version={book.version}）"})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 锁定失败：{exc}"})
    finally:
        session.close()


def _outline_out(n) -> dict:
    return {"id": n.id, "parent_id": n.parent_id, "level": n.level, "title": n.title, "summary": n.summary}


@app.route("/api/guided/outline/generate", methods=["POST"])
def guided_outline_generate():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        nodes = book_flow.generate_outline(session, book_id, gateway=gateway)
        session.commit()
        return jsonify({"status": f"✅ 大纲生成成功！共 {len(nodes)} 个节点", "outline": [_outline_out(n) for n in nodes]})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 大纲生成失败：{exc}", "outline": []})
    finally:
        session.close()


@app.route("/api/guided/outline/list", methods=["POST"])
def guided_outline_list():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        nodes = book_flow.list_outline(session, book_id)
        return jsonify({"status": f"共 {len(nodes)} 个节点", "outline": [_outline_out(n) for n in nodes]})
    except Exception as exc:
        return jsonify({"error": True, "status": f"❌ {exc}", "outline": []})
    finally:
        session.close()


@app.route("/api/guided/outline/lock", methods=["POST"])
def guided_outline_lock():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        book = book_flow.lock_outline(session, book_id)
        session.commit()
        return jsonify({"status": f"✅ 大纲已锁定（version={book.version}）"})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 锁定失败：{exc}"})
    finally:
        session.close()


@app.route("/api/guided/plan", methods=["POST"])
def guided_plan():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        chapters = book_flow.plan_chapters(session, book_id)
        session.commit()
        return jsonify({
            "status": f"✅ 章节规划完成！共 {len(chapters)} 章",
            "chapters": [_chapter_out(c) for c in chapters],
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 章节规划失败：{exc}", "chapters": []})
    finally:
        session.close()


def _chapter_out(ch) -> dict:
    return {"id": ch.id, "number": ch.number, "title": ch.title, "status": ch.status, "target_words": ch.target_words}


@app.route("/api/guided/chapters/list", methods=["POST"])
def guided_chapters_list():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        chapters = book_flow.list_chapters(session, book_id)
        return jsonify({"chapters": [_chapter_out(c) for c in chapters]})
    except Exception as exc:
        return jsonify({"error": True, "status": f"❌ {exc}", "chapters": []})
    finally:
        session.close()


def _change_out(chg) -> dict:
    return {"kind": chg.kind, "subject": chg.subject, "claim": chg.claim, "confidence": chg.confidence}


def _issue_out(vi) -> dict:
    return {
        "severity": vi.severity, "category": vi.category,
        "subject": vi.subject, "description": vi.description,
        "suggestion": vi.suggestion,
    }


@app.route("/api/guided/locate", methods=["POST"])
def guided_locate():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        ch = book_flow.get_next_writable_chapter(session, book_id)
        info = (
            f"📍 当前写作目标：第 {ch.number} 章《{ch.title}》\n"
            f"目标字数：{ch.target_words} 字 | 状态：{ch.status}"
        )
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
        return jsonify({
            "chapterId": ch.id,
            "chapterNo": ch.number,
            "status": info,
            "content": content,
            "titleMd": title_md,
            "changesMd": changes_md,
        })
    except Exception as exc:
        return jsonify({"error": True, "status": f"❌ {exc}"})
    finally:
        session.close()


def _write_result(result) -> dict:
    validation = [_issue_out(v) for v in result.validation_issues]
    status_lines = [
        f"✅ 生成完成 | 字数：{result.version.word_count} | "
        f"质量：{'✅ 通过' if result.context_manifest.get('quality_ok') else '⚠️ 未通过'}",
        f"扩写：{result.context_manifest.get('expanded_count', 0)} 次 | "
        f"重写：{result.context_manifest.get('rewritten_count', 0)} 次",
    ]
    return {
        "title": f"第{result.chapter.number}章：{result.version.title}",
        "status": "\n".join(status_lines),
        "content": result.version.content,
        "changes": [_change_out(c) for c in result.changes],
        "validation": validation,
        "validationSummary": result.validation_summary,
    }


@app.route("/api/guided/write", methods=["POST"])
def guided_write():
    chapter_id = (request.get_json() or {}).get("chapterId", "")
    session = _session()
    try:
        result = book_flow.generate_chapter(session, chapter_id, gateway=gateway)
        session.commit()
        return jsonify(_write_result(result))
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 生成失败：{exc}", "content": ""})
    finally:
        session.close()


@app.route("/api/guided/polish", methods=["POST"])
def guided_polish():
    chapter_id = (request.get_json() or {}).get("chapterId", "")
    session = _session()
    try:
        result = book_flow.polish_chapter_draft(session, chapter_id, gateway=gateway)
        session.commit()
        orig = result.context_manifest.get("original_word_count", "?")
        new_wc = result.context_manifest.get("polished_word_count", "?")
        issues_before = result.context_manifest.get("issues_before_fix", 0)
        issues_after = result.context_manifest.get("issues_after_fix", 0)
        if issues_before > 0:
            status = f"🔧 修复完成 | 原 {orig} 字 → 现 {new_wc} 字 | 问题 {issues_before} → {issues_after}"
        else:
            status = f"✨ 润色完成（未发现一致性问题）| 原 {orig} 字 → 现 {new_wc} 字"
        return jsonify({
            "title": f"第{result.chapter.number}章：{result.version.title}",
            "status": status,
            "content": result.version.content,
            "changes": [_change_out(c) for c in result.changes],
            "validation": [_issue_out(v) for v in result.validation_issues],
            "validationSummary": result.validation_summary,
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 润色失败：{exc}", "content": ""})
    finally:
        session.close()


@app.route("/api/guided/confirm", methods=["POST"])
def guided_confirm():
    data = request.get_json() or {}
    chapter_id = data.get("chapterId", "")
    book_id = data.get("bookId", "")
    force = bool(data.get("force", False))
    session = _session()
    try:
        result = book_flow.confirm_chapter(session, chapter_id, force=force)
        session.commit()
        status = f"✅ 已确认！状态：{result.get('status')} | 新增事实：{result.get('facts_added', 0)}"
        chapters = book_flow.list_chapters(session, book_id)
        return jsonify({"status": status, "chapters": [_chapter_out(c) for c in chapters]})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 确认失败：{exc}", "chapters": []})
    finally:
        session.close()


@app.route("/api/guided/view", methods=["POST"])
def guided_view():
    chapter_id = (request.get_json() or {}).get("chapterId", "")
    session = _session()
    try:
        detail = book_flow.get_chapter(session, chapter_id)
        if detail.current_version:
            v = detail.current_version
            title = f"第{detail.chapter.number}章：{v.title} | 字数：{v.word_count} | 状态：{v.status}"
            return jsonify({"title": title, "content": v.content})
        return jsonify({"title": f"第{detail.chapter.number}章：{detail.chapter.title}（无内容）", "content": ""})
    except Exception as exc:
        return jsonify({"error": True, "status": f"❌ {exc}"})
    finally:
        session.close()


@app.route("/api/guided/complete", methods=["POST"])
def guided_complete():
    book_id = (request.get_json() or {}).get("bookId", "")
    session = _session()
    try:
        result = book_flow.complete_book(session, book_id)
        session.commit()
        status = (
            f"✅ 完本成功！\n\n"
            f"📊 章节数：{result.chapter_count}\n"
            f"📊 总字数：{result.total_chars}\n"
            f"📝 摘要：{result.summary}"
        )
        return jsonify({"status": status})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 完本失败：{exc}"})
    finally:
        session.close()


@app.route("/api/guided/export", methods=["POST"])
def guided_export():
    data = request.get_json() or {}
    book_id = data.get("bookId", "")
    scope = str(data.get("scope") or "confirmed")
    scope = scope.split(" - ")[0] if " - " in scope else scope
    session = _session()
    try:
        result = book_flow.export_book_txt(session, book_id, scope=scope)
        session.commit()
        status = (
            f"✅ 导出成功！\n\n"
            f"📄 文件：{result.path}\n"
            f"📊 字数：{result.chars}\n"
            f"📊 章节数：{result.chapter_count}/{result.planned_chapter_count}\n"
            f"🎯 范围：{result.scope}"
        )
        return jsonify({"status": status, "download": Path(result.path).name})
    except Exception as exc:
        session.rollback()
        return jsonify({"error": True, "status": f"❌ 导出失败：{exc}"})
    finally:
        session.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  高级版：上传大纲
# ═══════════════════════════════════════════════════════════════════════════════


@app.route("/api/advanced/parse", methods=["POST"])
def advanced_parse():
    file = request.files.get("file")
    if not file or not file.filename:
        return jsonify({"error": "请上传文件"}), 400
    try:
        content = file.read().decode("utf-8")
    except Exception as exc:
        return jsonify({"error": f"❌ 无法读取文件：{exc}"}), 400
    if not content.strip():
        return jsonify({"error": "文件为空"}), 400

    session = _session()
    try:
        fname = Path(file.filename).stem
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
        nodes = book_flow.import_custom_outline(session, book.id, content)
        session.commit()
        return jsonify({
            "bookId": book.id,
            "title": title,
            "status": f"✅ 项目创建成功 | 📋 解析到 {len(nodes)} 个大纲节点",
            "outline": [_outline_out(n) for n in nodes],
        })
    except Exception as exc:
        session.rollback()
        return jsonify({"error": f"❌ 解析失败：{exc}"}), 400
    finally:
        session.close()


@app.route("/api/advanced/generate", methods=["POST"])
def advanced_generate():
    book_id = (request.get_json() or {}).get("bookId", "")
    if not book_id:
        return jsonify({"success": False, "log": "❌ 请先上传并解析文件"}), 400

    log_lines: list[str] = []
    session = _session()
    try:
        book = book_flow.get_book(session, book_id)
        if not book.outline_locked:
            log_lines.append("🔒 锁定大纲...")
            book_flow.lock_outline(session, book_id)
            session.commit()

        log_lines.append("📝 生成人物...")
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
        return jsonify({"success": True, "log": "\n".join(log_lines), "download": Path(result.path).name})
    except Exception as exc:
        session.rollback()
        log_lines.append(f"\n❌ {exc}")
        return jsonify({"success": False, "log": "\n".join(log_lines)})
    finally:
        session.close()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=7860, debug=False, threaded=True)