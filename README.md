# Novel-Agent

![Version](https://img.shields.io/badge/version-0.1.0-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-green)
![License](https://img.shields.io/badge/license-MIT-green)
![CI](https://github.com/Novel-Agent/novel-agent/workflows/CI/badge.svg)

**本地大模型驱动的长篇小说创作辅助引擎（本地大模型优先 + 多前端）**

> 当前阶段：**Phase 1.3** —— 按规划字数写作 + 完本标记 + txt 导出 + 一致性校验 + 事实抽取

## 目录
- [快速开始](#快速开始)
- [三大使用模式](#三大使用模式)
- [环境要求](#环境要求)
- [配置说明](#配置说明)
- [API 文档](#api-文档)
- [核心流程](#核心流程)
- [架构决策](#架构决策为何不使用-langchain--langgraph)
- [质量门禁与一致性](#质量门禁与一致性)
- [测试](#测试)
- [故障排查](#故障排查)
- [项目结构](#项目结构)
- [许可证](#许可证)

---

## 快速开始

### 方式一：Windows 一键启动（推荐新手）
```cmd
# 双击运行
start.bat
```
> 自动启动 Flask WebUI (http://127.0.0.1:7860) 并打开浏览器

### 方式二：Python 直接运行
```powershell
# 1. 安装依赖
pip install -e ".[dev]"

# 2. 复制配置
Copy-Item .env.example .env

# 3. 初始化数据库
python scripts/migrate.py

# 4a. 启动 FastAPI 服务（仅 API，端口 8000）
python server.py

# 4b. 启动 Flask WebUI（三模式界面，端口 7860）
python webui.py

# 4c. 启动 Gradio UI（更丰富的交互，端口 7860）
python app_ui.py

# 4d. CLI 演示（无需前端）
python scripts/demo_user_flow.py --max-chapters 3
```

### 方式三：Docker（待补充 Dockerfile）
```bash
# TODO: docker compose up -d
```

---

## 三大使用模式

| 模式 | 适用场景 | 入口 | 特点 |
|------|----------|------|------|
| **基础版** | 想快速得到一本完整小说 | WebUI "基础版" / Gradio "基础版" / CLI | 只需书名，自动完成：人物→大纲→章节规划→全书写作→确认→完本→导出 |
| **进阶版** | 想精细控制每一步 | WebUI "进阶版" / Gradio "进阶版" | 6 步引导：创作卡片 → 人物设计 → 大纲 → 章节规划 → 逐章写作(含润色/一致性检查) → 完本导出 |
| **高级版** | 已有大纲/梗概文本 | WebUI "高级版" / Gradio "高级版" | 上传 .txt/.md 大纲 → 自动解析结构 → 接入进阶版流程 |

> **WebUI** (Flask + 原生 JS/CSS) 与 **Gradio UI** 功能对等，风格不同：
> - WebUI：暖阳奶油风，轻量，单文件部署
> - Gradio：Notion-AI 风，组件丰富，适合二次开发

---

## 环境要求

| 组件 | 最低版本 | 推荐 | 说明 |
|------|----------|------|------|
| Python | 3.11 | 3.11+ | 运行 API/脚本/UI |
| Ollama | 0.3.x | 最新 | `ollama pull deepseek-r1:8b` |
| 显存 | — | **≥8 GB** | 8b 模型量化后约 5-6 GB VRAM |
| 内存 | 8 GB | 16 GB+ | CPU 推理回退时需更大内存 |
| 磁盘 | 10 GB | 20 GB+ | 模型权重 + SQLite + 导出文件 |

> ⚠️ **CPU 推理**：无 GPU 时 Ollama 自动回退，速度约为 GPU 的 1/10-1/20，仅建议测试用。

---

## 配置说明

所有配置通过 `.env` 读取（优先级：环境变量 > `.env` > 默认值）。  
复制 `.env.example` 为 `.env` 后按需修改。

| 变量 | 默认值 | 说明 | 必填 |
|------|--------|------|------|
| `OLLAMA_BASE_URL` | `http://127.0.0.1:11434` | Ollama HTTP 端点 | 是 |
| `OLLAMA_MODEL` | `deepseek-r1:8b` | 已 `ollama pull` 的模型名 | 是 |
| `OLLAMA_THINK` | `false` | 推理模型需关闭以输出纯 JSON（qwen3/qwq 必须关） | 否 |
| `CONTEXT_LIMIT` | `6144` | 传给 Ollama 的 num_ctx；6144 可覆盖实测 prompt+输出总长，避免 4096 默认值下 >4096 token 的 prompt 被静默截断 | 否 |
| `DEFAULT_NUM_PREDICT` | `2048` | 单次生成默认 token 上限 | 否 |
| `SCHEMA_REPAIR_RETRIES` | `2` | JSON 修复重试次数 | 否 |
| `DATABASE_URL` | `sqlite:///data/novel_agent.db` | SQLAlchemy 连接串 | 否 |
| `WORDS_SHORT` | `30000` | 短篇目标总字数 | 否 |
| `WORDS_MEDIUM` | `120000` | 中篇目标总字数 | 否 |
| `WORDS_LONG` | `300000` | 长篇目标总字数 | 否 |
| `PREV_CHAPTER_TAIL_CHARS` | `1500` | 上文回溯字符数 | 否 |
| `MAX_FACTS_IN_CONTEXT` | `50` | 上下文注入的最大事实条数 | 否 |
| `WRITER_NUM_PREDICT_FLOOR` | `4096` | 写作最小 token 预算 | 否 |
| `WRITER_NUM_PREDICT_CEIL` | `12288` | 写作最大 token 预算 | 否 |
| `WRITER_MIN_WORDS_RATIO` | `0.55` | 实际字数/目标字数 < 此值判定不足 | 否 |
| `WRITER_REPAIR_REPETITION` | `true` | 是否启用重复检测+重写 | 否 |
| `WRITER_MAX_REPAIR` | `2` | 单章最大扩写/重写轮数 | 否 |
| `WRITER_MAX_TRIM_RATIO` | `0.25` | 去重截断比例 ≥ 此值触发重写 | 否 |
| `WRITER_ENFORCE_QUALITY_ON_CONFIRM` | `true` | 质量不达标时自动确认是否跳过 | 否 |

---

## API 文档

基础路径：`/api/v1`  
统一响应：`{ success, data, error, meta }`  
错误码：`NOT_FOUND(404)`, `VERSION_CONFLICT(409)`, `PRECONDITION_FAILED(412)`, `EXPORT_FORMAT(412)`, `INTERNAL_ERROR(500)`

### 核心端点（22 个，完整列表见 `/docs`）

| 端点 | 方法 | 说明 |
|------|------|------|
| `/books` | POST | 创建书籍（创作卡片） |
| `/books/{id}` | GET | 获取书籍详情 |
| `/books/{id}/outline/generate` | POST | 基于卡片+人物生成分层大纲 |
| `/books/{id}/outline/lock` | POST | 锁定大纲（版本控制） |
| `/books/{id}/characters/generate` | POST | 生成人物设定 |
| `/books/{id}/characters/lock` | POST | 锁定人物 |
| `/books/{id}/chapters/plan` | POST | 大纲节点映射为章节，按篇幅均分字数 |
| `/books/{id}/chapters/write-next` | POST | 写下一章（含幂等键） |
| `/chapters/{id}/generate` | POST | 指定章节生成/重写 |
| `/chapters/{id}/confirm` | POST | 确认章节（`force=true` 覆盖质量门禁） |
| `/books/{id}/complete` | POST | 完本（需全章 confirmed）→ 生成摘要 |
| `/books/{id}/export` | GET | 导出 txt（`scope=confirmed\|all`） |
| `/tasks/{id}` | GET | 查询异步任务状态 |

> 示例请求/响应见 [OpenAPI 文档](http://localhost:8000/docs)（启动 `python server.py` 后访问）。

---

## 核心流程

```mermaid
sequenceDiagram
    participant U as 用户
    participant A as API (FastAPI/Flask/Gradio)
    participant L as LLM Gateway (Ollama)
    participant D as SQLite + FTS5
    U->>A: 创建书籍 (POST /books)
    U->>A: 生成大纲 (POST /outline/generate)
    U->>A: 锁定大纲 (POST /outline/lock)
    U->>A: 生成人物 (POST /characters/generate)
    U->>A: 锁定人物 (POST /characters/lock)
    U->>A: 章节规划 (POST /chapters/plan)
    loop 每章
        U->>A: 写作 (POST /write-next 或 /generate)
        A->>L: 生成草稿 (结构化输出)
        L-->>A: 章节内容 + 候选事实
        A->>D: 质量检测(字数/重复/一致性)
        U->>A: 确认章节 (POST /confirm)
    end
    U->>A: 完本 (POST /complete)
    U->>A: 导出 (GET /export)
```

---

## 架构决策：为何不使用 langchain / langgraph

**结论：保持自研编排。** 本项目属于「确定性流水线 + 数据库状态推进」，而非自主 agent 图；langgraph 的核心价值（工具调用、条件路由、并行子图）在本项目无用武之地。

| 决策点 | 现状 | 引入框架的问题 |
|--------|------|----------------|
| **编排** | 自研状态机（`book_flow.py`）+ phase0 循环，任务表持久化，天然断点续跑 | langgraph checkpoint 抽象与自建 SQLite 状态重复，需重写核心 |
| **Agent 能力** | 全项目零 tool calling，无需自主决策 | 框架核心价值闲置，只增加抽象层 |
| **容错** | 自研 JSON schema 修复重试 + 字数/重复/一致性质量闸门 | 属于领域逻辑，框架不提供等价物，引入后仍需保留（双重实现） |
| **模型接入** | `gateway.py` 直连 Ollama（JSON mode/raw 精确控制），仅 113 行 | langchain 的 Ollama 抽象对本地小模型是隔层，关键参数难透传 |
| **依赖** | 17 个显式依赖，无传递膨胀，pydantic 版本可控 | langchain 依赖树庞大，与 FastAPI/SQLAlchemy 栈存在 pydantic 版本冲突风险 |

**触发引入的条件**（当前均不满足）：
1. 需要自主 agent 式写作（模型自行决策调用工具/检索/改写）
2. 需要多模型统一抽象（OpenAI/Claude/本地混用）
3. 需要 RAG 检索增强
4. 团队深度熟悉该生态，人效优先

**原则**：未来若需引入，先以最小模块（如 `phase0_loop.py`，137 行）做 POC 对照，验证收益后再决定是否重写。

---

## 质量门禁与一致性

| 机制 | 触发条件 | 行为 |
|------|----------|------|
| **字数达标** | `actual_words / target_words < WRITER_MIN_WORDS_RATIO (0.55)` | `quality_ok=false`，自动扩写（最多 `WRITER_MAX_REPAIR=2` 轮） |
| **重复检测** | 连续 n-gram 重复/循环复读 | 截断重复段，若截断比例 ≥ `WRITER_MAX_TRIM_RATIO (0.25)` 触发重写 |
| **一致性校验** | 章节生成后自动运行 | 抽取候选事实 → 对比已确认事实库 → 产出 `error/warning/info` 级问题 |
| **润色修复** | 手动点击"修复+润色" | 基于校验问题定向重写，输出修复前后对比 |
| **确认门禁** | `quality_ok=false` 时 | 默认跳过自动确认；`force=true` 可强制通过（需配置允许） |

---

## 测试

```powershell
# 单元测试（无需 Ollama，约 10s）
pytest tests/unit -q

# 集成测试（需本地 Ollama + deepseek-r1:8b）
pytest tests/integration -q -m integration

# 代码规范
ruff check src tests
mypy src
```

---

## 故障排查

| 现象 | 原因 | 解决 |
|------|------|------|
| `Connection refused` | Ollama 未启动 | `ollama serve` |
| `model not found` | 未拉取模型 | `ollama pull deepseek-r1:8b` |
| `CUDA out of memory` | 显存不足 | 关闭其他进程 / 用更小量化模型 / CPU 模式 |
| `quality_ok=false` 章节不自动确认 | 字数不足或重复过多 | 调大 `WRITER_NUM_PREDICT_CEIL` / 手动 `force=true` 确认 |
| `VERSION_CONFLICT` | 并发修改同一实体 | 使用 `expected_version` 乐观锁重试 |
| `database is locked` | SQLite 并发写入 | 单进程运行 / 改用 PostgreSQL |
| 结构化输出解析失败 | `OLLAMA_THINK=true` 导致思考内容混入 JSON | 设置 `OLLAMA_THINK=false`（默认已关） |

---

## 项目结构

```
novel-agent/
├── server.py              # FastAPI 入口 (端口 8000)
├── webui.py               # Flask WebUI 入口 (端口 7860，三模式界面)
├── app_ui.py              # Gradio UI 入口 (端口 7860，Notion-AI 风)
├── start.bat              # Windows 一键启动脚本
├── pyproject.toml         # 包配置 (依赖、pytest、版本)
├── .env.example           # 配置模板 (21 项)
├── alembic/               # 数据库迁移
├── prompts/               # 提示词模板
│   ├── planner/           # 大纲、人物生成
│   ├── writer/            # 章节起草、润色
│   ├── validator/         # 一致性校验
│   └── extractor/         # 候选事实抽取
├── scripts/
│   ├── demo_user_flow.py  # CLI 完整演示
│   ├── migrate.py         # 数据库初始化
│   └── probe_models.py    # 模型能力探测
├── src/novel_agent/
│   ├── api/               # FastAPI 路由、schemas、错误处理
│   ├── application/       # 工作流编排 (book_flow.py 核心)
│   ├── domain/            # 领域模型、错误、质量评估
│   ├── infrastructure/    # LLM网关、持久化、提示词注册
│   └── settings.py        # Pydantic Settings 配置
├── templates/index.html   # Flask 前端模板
├── static/                # Flask 前端资源
├── tests/                 # 单测(5) + 集成测试(3)
└── data/                  # SQLite、导出文件、探测报告 (gitignore)
```

---

## 许可证

[MIT License](LICENSE) © 2024 Novel-Agent Contributors