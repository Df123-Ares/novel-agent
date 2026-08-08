# Novel-Agent

本地大模型驱动的长篇小说创作辅助引擎（API 优先）。

当前进度：**阶段 1.3** —— 按规划字数写作 + 完本标记 + txt 导出（前端仍延后）。

## 环境要求

- Python >= 3.11
- Ollama 已运行：`ollama pull deepseek-r1:8b`

## 安装

```powershell
cd novel-agent
pip install -e ".[dev]"
Copy-Item .env.example .env
python scripts/migrate.py
```

## 验收

### 单元测试

```powershell
pytest tests/unit -q
```

### 演示（写作较慢，字数已按 target_words 放大）

```powershell
# 默认写 3 章，并自动导出已确认章节到 data/exports/
python scripts/demo_user_flow.py --max-chapters 3

# 写完全部章节并完本 + 全书导出
python scripts/demo_user_flow.py --all
```

导出文件目录：`data/exports/`

### API

```powershell
python server.py
```

- `POST /api/v1/books/{id}/complete` — 全部章节 confirmed 后标记完本并生成摘要
- `GET  /api/v1/books/{id}/export?format=txt&scope=confirmed` — 导出**已确认**章节（默认，可部分导出）
- `GET  /api/v1/books/{id}/export?format=txt&scope=all` — 导出全书（须全部 confirmed）
- 写作时 `num_predict` 随 `target_words` 缩放；过短会自动扩写（可多轮，`WRITER_MAX_REPAIR`）
- 生成后会做**重复检测**：截断循环复读；去重过狠则触发重写
- 质量门禁：字数不足或严重重复时 `quality_ok=false`，自动确认会跳过；手动确认可用 `?force=true` 覆盖（`WRITER_ENFORCE_QUALITY_ON_CONFIRM`）

## 说明

- 默认 `OLLAMA_THINK=false`
- 单章目标字数来自章节规划（短篇总字数 ÷ 章数）
- 前端、SSE、向量库、epub/pdf 仍延后
