"""Tests for draft quality assessment and confirm / generate quality gate."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from novel_agent.application.workflows import book_flow
from novel_agent.domain.book.schemas import CreateBookRequest
from novel_agent.domain.errors import PreconditionError, new_id
from novel_agent.domain.story.llm_outputs import (
    ChapterCheckLLMOutput,
    ExtractorLLMOutput,
    WriterLLMOutput,
)
from novel_agent.domain.text_quality import assess_draft_quality
from novel_agent.infrastructure.persistence.db import (
    create_all_tables,
    get_engine,
    reset_engine,
)
from novel_agent.infrastructure.persistence.models import (
    ChapterRow,
    ChapterVersionRow,
    CharacterRow,
    OutlineNodeRow,
)
from novel_agent.infrastructure.persistence.unit_of_work import UnitOfWork
from novel_agent.settings import Settings


@pytest.fixture()
def db_url(tmp_path: Path) -> str:
    reset_engine()
    url = f"sqlite:///{(tmp_path / 'quality.db').as_posix()}"
    create_all_tables(get_engine(url=url))
    yield url
    reset_engine()


def _seed_writable_chapter(session, *, target_words: int = 1000) -> tuple[str, str]:
    book = book_flow.create_book(
        session, CreateBookRequest(title="T", premise="港口档案馆夜班", length="short")
    )
    b = book_flow._get_book(session, book.id)
    b.outline_locked = True
    b.characters_locked = True
    session.add(
        CharacterRow(
            id=new_id(),
            book_id=book.id,
            name="林默",
            personality="冷静",
            appearance="清瘦",
            background="港口档案馆夜班管理员",
            role="protagonist",
            locked=True,
        )
    )
    node = OutlineNodeRow(
        id=new_id(),
        book_id=book.id,
        parent_id=None,
        level="chapter_goal",
        title="夜班发现",
        summary="在档案馆发现灯塔日志",
        sort_order=0,
        locked=True,
    )
    session.add(node)
    session.flush()
    chapter = ChapterRow(
        id=new_id(),
        book_id=book.id,
        outline_node_id=node.id,
        number=1,
        title="夜班发现",
        goal="在档案馆发现灯塔日志",
        target_words=target_words,
        status="planned",
    )
    session.add(chapter)
    session.flush()
    return book.id, chapter.id


def test_assess_too_short() -> None:
    q = assess_draft_quality("很短的一段话。", min_words=400)
    assert q.too_short is True
    assert q.quality_ok is False
    assert "too_short" in q.reason


def test_assess_ok_long_enough() -> None:
    sentences = [
        "林默在夜班巡视时发现档案柜上有一层薄灰。",
        "他抽出那本烫金封面的旧册子，纸页已经发脆。",
        "陆远在门外压低声音，提醒他灯塔今晚又有亮光。",
        "手电筒在书脊上扫过，映出妹妹名字的缩写。",
        "窗外的雾越来越浓，海风里裹着铁锈的气味。",
        "林默把卷宗摊在桌面上，用铅笔圈出那行日期。",
        "值班室的电话突然响起来，把他吓了一跳。",
        "他接起电话，那头只有沙沙的电流声。",
        "挂断后，他在登记簿上补写了今晚的异常。",
        "楼梯间传来脚步声，却又在二楼停住了。",
        "林默犹豫片刻，还是决定下楼去看一眼。",
        "他沿着走廊数着门牌，最后停在了档案室前。",
        "门锁是新的，钥匙却打不开，他皱了皱眉。",
        "他从口袋里摸出备用钥匙，插进去转了半圈。",
        "门开了，一股陈旧的纸墨气息扑面而来。",
        "柜顶的吊灯忽明忽暗，像随时会熄灭。",
        "他弯腰捡起地上散落的几张白纸。",
        "纸上只有一句话：别再查灯塔的事了。",
        "林默把纸折好放进胸袋，继续查看现场。",
        "墙角的铁皮柜被撬过，锁孔边缘有新的划痕。",
        "他拍下几张照片，打算天亮后报警。",
        "离开前，他最后看了一眼那排档案柜。",
        "雾里，灯塔的方向隐约传来一声汽笛。",
        "林默裹紧外套，快步走回值班室。",
        "他把手电筒放回原位，在椅子上坐下。",
        "桌上的那本旧册子还摊开着，灯光昏黄。",
        "他重新拿起册子，翻到夹着纸条的那一页。",
        "纸条上的字迹和妹妹的一模一样。",
        "林默的手微微发抖，窗外传来第二声汽笛。",
    ]
    q = assess_draft_quality("".join(sentences), min_words=200)
    assert q.too_short is False
    assert q.repetition_severe is False
    assert q.quality_ok is True


def test_assess_severe_repetition() -> None:
    block = "他的思绪被一阵低语打断，那声音仿佛从灯塔的深处传来。他缓缓站起身，环顾四周。"
    text = "林默走进灯塔。" + block * 8
    q = assess_draft_quality(text, min_words=50, max_trim_ratio=0.25)
    assert q.repetition_truncated is True
    assert q.repetition_severe is True
    assert q.quality_ok is False
    assert q.final_len < q.original_len


def test_confirm_rejects_short_draft(db_url: str) -> None:
    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        _book_id, chapter_id = _seed_writable_chapter(session, target_words=1000)
        chapter = session.get(ChapterRow, chapter_id)
        assert chapter is not None
        ver = ChapterVersionRow(
            id=new_id(),
            chapter_id=chapter.id,
            title=chapter.title,
            content="只有一点点正文。",
            summary="短",
            status="DRAFT",
            word_count=8,
        )
        session.add(ver)
        session.flush()
        chapter.current_version_id = ver.id
        chapter.status = "drafted"

        settings = Settings(writer_enforce_quality_on_confirm=True)
        with pytest.raises(PreconditionError) as exc:
            book_flow.confirm_chapter(session, chapter.id, settings=settings)
        assert exc.value.code == "CHAPTER_QUALITY_FAILED"

        ok = book_flow.confirm_chapter(session, chapter.id, force=True, settings=settings)
        assert ok["status"] == "confirmed"


def _unique_long_chapter(min_chars: int = 600) -> str:
    sentences = [
        "林默把最后一摞卷宗放回原处，揉了揉发酸的眼角。",
        "值班室的老式挂钟指向凌晨两点，指针咔哒作响。",
        "他端起凉透的茶抿了一口，视线落在窗外的雾上。",
        "抽屉深处压着一封没寄出的信，收信人是他自己。",
        "信封里只有一张照片，背面写着今天这个日期。",
        "照片上的人站在灯塔前，身形模糊，笑容熟悉。",
        "林默认出那是妹妹失踪前最后一张留影。",
        "他记得那天她在电话里说，要去码头见一个人。",
        "档案里却没有那天任何一艘船的出港记录。",
        "陆远提着手电进来，说巡查时发现后门没锁。",
        "两人沿着湿滑的台阶下到负一层，霉味更重了。",
        "墙角的旧木箱被人翻动过，箱盖上的灰尘有掌印。",
        "箱子里只剩几卷发黄的航海日志，日期都已模糊。",
        "林默小心翻开其中一卷，第一页写着灯塔二字。",
        "日志记录到三年前就中断了，最后一页被撕走。",
        "他翻到夹层，找到一片干枯的银杏叶。",
        "陆远认出银杏是档案馆后院那棵老树落下的。",
        "两人对视一眼，决定天亮前再去灯塔看一眼。",
        "雾在凌晨散开了一些，塔尖露出锈迹斑斑的轮廓。",
        "林默拨通了刑警队老周的电话，号码还是那个。",
        "老周在睡梦里含糊应了一声，说天亮再查。",
        "林默放下电话，把照片和银杏叶收进胸袋。",
        "他关掉值班室的灯，让黑暗自己填满房间。",
        "海风从窗缝挤进来，吹得桌上的纸页微微翻动。",
        "远处传来一声汽笛，像是某个迟到的告别。",
        "林默闭上眼睛，脑海里全是那张照片上的笑容。",
        "他站起身，把茶缸里的凉茶倒进水池。",
        "水池边贴着去年春节的旧福字，边角已经卷起。",
        "林默撕下福字，发现后面藏着一枚钥匙。",
        "钥匙上系着褪色的红绳，像是妹妹的手笔。",
        "他把钥匙举到灯下，钥匙齿纹和档案室那扇门吻合。",
        "陆远凑过来看了一眼，低声说这锁去年才换过。",
        "林默没有答话，把钥匙收进口袋，转身就走。",
        "两人一前一后穿过走廊，脚步声在夜里格外清晰。",
        "推开档案室的门，那排铁柜在月光下泛着冷光。",
        "林默摸到柜侧第三格，钥匙顺畅地转动了半圈。",
        "柜门弹开，里面放着一只密封的牛皮纸袋。",
        "纸袋上的收件人写着林默的名字，邮戳是三年前的。",
        "他撕开封口，抽出一张泛白的船票存根。",
        "存根上的目的地，正是灯塔所在的那座码头。",
    ]
    chunks: list[str] = []
    i = 0
    while sum(len(c) for c in chunks) < min_chars:
        if i >= len(sentences):
            raise AssertionError("sentence pool too small for min_chars")
        chunks.append(sentences[i])
        i += 1
    return "".join(chunks)


def test_generate_expands_short_then_marks_quality(db_url: str) -> None:
    short = WriterLLMOutput(
        title="夜班发现",
        content="林默站在档案馆门口。",
        scene_summary="到了门口。",
    )
    long = WriterLLMOutput(
        title="夜班发现",
        content=_unique_long_chapter(700),
        scene_summary="发现灯塔日志线索。",
    )
    extractor = ExtractorLLMOutput(changes=[])

    gateway = MagicMock()
    validator = ChapterCheckLLMOutput(issues=[], overall_assessment="通过")
    gateway.chat_structured.side_effect = [
        (short, {}),
        (long, {}),
        (extractor, {}),
        (validator, {}),
    ]

    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        _book_id, chapter_id = _seed_writable_chapter(session, target_words=800)
        settings = Settings(
            writer_max_repair=2,
            writer_min_words_ratio=0.55,
            writer_repair_repetition=True,
            writer_max_trim_ratio=0.25,
            run_consistency_check=True,
            # 测试场景需要 extractor 调用以验证完整流程；默认值已改为 False 以提速生产环境
            run_extractor=True,
        )
        result = book_flow.generate_chapter(
            session, chapter_id, gateway=gateway, settings=settings
        )
        assert result.context_manifest.get("quality_ok") is True
        assert result.context_manifest.get("expanded_count", 0) >= 1
        assert result.version.word_count >= result.context_manifest["min_words"]
        # initial + expand + extractor + validator
        assert gateway.chat_structured.call_count == 4


def test_write_loop_skips_confirm_when_quality_fails(db_url: str) -> None:
    always_short = WriterLLMOutput(
        title="夜班发现",
        content="太短了。",
        scene_summary="无。",
    )
    extractor = ExtractorLLMOutput(changes=[])
    validator = ChapterCheckLLMOutput(issues=[], overall_assessment="通过")
    gateway = MagicMock()
    # initial + 2 repairs + extractor + validator
    gateway.chat_structured.side_effect = [
        (always_short, {}),   # initial
        (always_short, {}),   # repair 1
        (always_short, {}),   # repair 2
        (extractor, {}),      # extractor
        (validator, {}),      # validator (consistency check enabled in settings)
    ]

    with UnitOfWork(url=db_url) as uow:
        session = uow.session
        assert session is not None
        book_id, _chapter_id = _seed_writable_chapter(session, target_words=800)
        settings = Settings(
            writer_max_repair=2,
            writer_enforce_quality_on_confirm=True,
            run_consistency_check=True,
            # 测试场景需要 extractor 调用以验证完整流程；默认值已改为 False 以提速生产环境
            run_extractor=True,
        )
        loop = book_flow.write_chapters_loop(
            session,
            book_id,
            max_chapters=1,
            auto_confirm=True,
            gateway=gateway,
            settings=settings,
        )
        assert len(loop.written) == 1
        assert loop.written[0].confirm.get("status") == "skipped_quality"
        assert loop.written[0].generate.context_manifest.get("quality_ok") is False
        chapter = session.get(ChapterRow, loop.written[0].chapter_id)
        assert chapter is not None
        assert chapter.status == "drafted"
