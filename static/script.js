/**
 * Novel-Agent · 小说创作助手 — 前端交互
 * 暖阳奶油风设计 · 本地大模型生成（Ollama）
 */

// ========== 工具函数 ==========
function toast(msg) {
    const el = document.createElement('div');
    el.className = 'toast';
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 3000);
}

let toastTimer = null;
function debouncedToast(msg, delay = 600) {
    if (toastTimer) {
        const existing = document.querySelector('.toast');
        if (existing) existing.textContent = msg;
        return;
    }
    toast(msg);
    toastTimer = setTimeout(() => { toastTimer = null; }, delay);
}

function showLoading(btn) {
    btn.disabled = true;
    const orig = btn.textContent;
    btn.dataset.origText = orig;
    btn.innerHTML = '<span class="loading-spinner"></span> 处理中...';
}

function hideLoading(btn) {
    btn.disabled = false;
    btn.textContent = btn.dataset.origText || btn.textContent;
}

function escapeHtml(s) {
    return String(s == null ? '' : s)
        .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

async function postJSON(url, payload) {
    const resp = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload || {}),
    });
    return resp.json();
}

// ========== Ollama 状态检测 ==========
async function checkOllamaStatus() {
    const dot = document.getElementById('ollamaDot');
    const text = document.getElementById('ollamaStatus');
    const badge = document.getElementById('ollamaBadge');
    if (badge) badge.classList.add('checking');
    try {
        const resp = await fetch('/api/health');
        const data = await resp.json();
        if (data.ollama && data.modelAvailable) {
            dot.className = 'status-dot online';
            text.textContent = `${data.model} 已就绪`;
            if (badge) badge.title = `✅ 已连接\n模型: ${data.model}\n点击重新检测`;
        } else if (data.ollama) {
            dot.className = 'status-dot warn';
            text.textContent = `缺少模型 ${data.model}`;
            if (badge) badge.title = `⚠️ Ollama 在线，但未安装 ${data.model}\n已安装: ${(data.installedModels || []).join(', ') || '（无）'}\n请运行: ollama pull ${data.model}\n点击重新检测`;
        } else {
            dot.className = 'status-dot offline';
            text.textContent = 'Ollama 未连接';
            if (badge) badge.title = `❌ 未检测到 Ollama 服务\n请先启动 Ollama（ollama serve）\n点击重新检测`;
        }
    } catch (e) {
        dot.className = 'status-dot offline';
        text.textContent = '后端未启动';
        if (badge) badge.title = '❌ 后端服务未启动';
    } finally {
        if (badge) badge.classList.remove('checking');
    }
}

document.getElementById('ollamaBadge').addEventListener('click', async function() {
    await checkOllamaStatus();
    const dot = document.getElementById('ollamaDot');
    const text = document.getElementById('ollamaStatus');
    const badge = document.getElementById('ollamaBadge');
    try {
        const health = await (await fetch('/api/health')).json();
        if (!(health.ollama && health.modelAvailable)) return;
        dot.className = 'status-dot checking';
        text.textContent = '生成测试中...';
        const r = await (await fetch('/api/test-generate')).json();
        if (r.ok) {
            dot.className = 'status-dot online';
            text.textContent = '✅ 连通测试通过';
            badge.title = `✅ 模型 ${health.model} 真实生成成功\n返回: ${r.output}\n点击重新检测`;
        } else {
            dot.className = 'status-dot warn';
            text.textContent = '⚠️ 生成测试失败';
            badge.title = `❌ 模型存在但生成失败:\n${r.error}\n点击重试`;
        }
    } catch (e) {
        dot.className = 'status-dot warn';
        text.textContent = '⚠️ 生成测试异常';
    }
});

// ========== 模型选择 ==========
async function loadModels() {
    const picker = document.getElementById('modelPicker');
    try {
        const resp = await fetch('/api/models');
        const data = await resp.json();
        const models = data.models || [];
        if (models.length) {
            document.getElementById('modelTriggerText').textContent = data.current || models[0];
            renderModelOptions(models, data.current);
            picker.style.display = 'inline-block';
        } else {
            picker.style.display = 'none';
        }
    } catch (e) {
        picker.style.display = 'none';
    }
}

function renderModelOptions(models, current) {
    const dd = document.getElementById('modelDropdown');
    dd.innerHTML = `
        <div class="model-option auto-select" id="autoSelectOpt" title="自动测试本机所有模型，选中最优可用者">
            <span>⚡ 自动选择最优模型</span>
        </div>
        <div class="dd-hint">本机已安装模型</div>
        ${models.map(m => `
            <div class="model-option ${m === current ? 'active' : ''}" data-model="${m}">
                <span>${m}</span>
                ${m === current ? '<span class="check">✓</span>' : ''}
            </div>
        `).join('')}
    `;
}

async function runAutoSelect() {
    const dd = document.getElementById('modelDropdown');
    const trig = document.getElementById('modelTrigger');
    dd.classList.remove('open');
    trig.classList.remove('open');
    const dot = document.getElementById('ollamaDot');
    const text = document.getElementById('ollamaStatus');
    dot.className = 'status-dot checking';
    text.textContent = '自动检测模型...';
    try {
        const resp = await fetch('/api/models/auto-select', { method: 'POST' });
        const data = await resp.json();
        if (data.ok) {
            toast('✅ 已自动选择最优模型：' + data.model);
        } else {
            toast('⚠️ ' + (data.error || '自动选择失败'));
        }
        document.getElementById('modelTriggerText').textContent = data.current || data.model || '模型';
        renderModelOptions(data.models || [], data.current || data.model);
        checkOllamaStatus();
    } catch (e) {
        toast('⚠️ 自动选择失败：' + e.message);
        checkOllamaStatus();
    }
}

document.getElementById('modelTrigger').addEventListener('click', function(e) {
    e.stopPropagation();
    const dd = document.getElementById('modelDropdown');
    dd.classList.toggle('open');
    this.classList.toggle('open', dd.classList.contains('open'));
});

document.getElementById('modelDropdown').addEventListener('click', async function(e) {
    if (e.target.closest('.auto-select')) {
        runAutoSelect();
        return;
    }
    const opt = e.target.closest('.model-option');
    if (!opt) return;
    const model = opt.dataset.model;
    document.getElementById('modelDropdown').classList.remove('open');
    document.getElementById('modelTrigger').classList.remove('open');
    try {
        const resp = await fetch('/api/models/select', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ model }),
        });
        const data = await resp.json();
        if (data.error) {
            toast('⚠️ ' + data.error);
            loadModels();
            return;
        }
        toast('✅ 已切换到模型：' + data.current);
        document.getElementById('modelTriggerText').textContent = data.current;
        renderModelOptions((data.models || [data.current]), data.current);
        checkOllamaStatus();
    } catch (e) {
        toast('⚠️ 切换模型失败：' + e.message);
    }
});

document.addEventListener('click', function() {
    document.getElementById('modelDropdown').classList.remove('open');
    document.getElementById('modelTrigger').classList.remove('open');
});

// ========== Tab 切换 ==========
document.querySelectorAll('.tab-btn').forEach(btn => {
    btn.addEventListener('click', function() {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        this.classList.add('active');
        const target = this.dataset.tab;
        document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
        document.getElementById('page-' + target).classList.add('active');
    });
});

// ========== 通用：Pill 单选 ==========
document.querySelectorAll('.pill-group').forEach(group => {
    group.querySelectorAll('.pill').forEach(p => {
        p.addEventListener('click', function() {
            group.querySelectorAll('.pill').forEach(x => x.classList.remove('active'));
            this.classList.add('active');
        });
    });
});

function activePillData(groupId, key) {
    const el = document.querySelector(`#${groupId} .pill.active`);
    return el ? el.dataset[key] : '';
}

// ========== 基础版：一键成书 ==========
document.getElementById('jbGenerateBtn').addEventListener('click', async function() {
    const title = document.getElementById('jbTitle').value.trim();
    if (!title) {
        toast('⚠️ 请先输入书名');
        return;
    }
    const length = activePillData('jbLengthGroup', 'length') || 'short';

    document.getElementById('jbProgress').style.display = 'block';
    document.getElementById('jbResult').style.display = 'none';
    document.getElementById('jbActions').style.display = 'none';
    const logBox = document.getElementById('jbLog');
    logBox.classList.remove('hidden');
    logBox.textContent = '📖 开始创作《' + title + '》...\n';
    document.getElementById('jbProgressText').textContent = '生成中（整本小说，本地模型可能需要几分钟），请勿关闭页面...';

    showLoading(this);
    try {
        const data = await postJSON('/api/basic/generate', { title, length });
        logBox.textContent = data.log || '';
        document.getElementById('jbProgressText').textContent = data.success ? '✅ 创作完成' : '⚠️ 生成中断';

        document.getElementById('jbResultLog').textContent = data.log || '';
        if (data.success && data.download) {
            document.getElementById('jbDownload').href = '/api/download?name=' + encodeURIComponent(data.download);
            document.getElementById('jbActions').style.display = 'flex';
        }
        document.getElementById('jbResult').style.display = 'block';
    } catch (e) {
        logBox.textContent += '\n❌ ' + e.message;
        document.getElementById('jbProgressText').textContent = '⚠️ 请求失败';
    } finally {
        hideLoading(this);
    }
});

// ========== 进阶版：逐步引导 ==========
let jjBookId = '';
let jjChapterId = '';

function showStatus(elId, html) {
    const el = document.getElementById(elId);
    el.innerHTML = html;
    el.style.display = 'block';
}

function renderBookInfo(data) {
    if (!data.bookId) return;
    jjBookId = data.bookId;
    showStatus('jjBookInfo',
        `<div class="info-panel">📖 书名：${escapeHtml(data.title || '')}<br>` +
        `🏷️ ${escapeHtml(data.genre || '')} · ${escapeHtml(data.style || '')} · ${escapeHtml(data.length || '')}<br>` +
        `👁️ ${escapeHtml(data.perspective || '')} · 🌤 ${escapeHtml(data.tone || '')}<br>` +
        `<span class="status-muted" style="font-size:11px;">ID: ${escapeHtml(data.bookId)}</span></div>`);
}

document.getElementById('jjCreateBtn').addEventListener('click', async function() {
    const title = document.getElementById('jjTitle').value.trim();
    if (!title) {
        toast('⚠️ 请先输入书名');
        return;
    }
    const premise = document.getElementById('jjPremise').value.trim();
    const length = activePillData('jjLengthGroup', 'length') || 'medium';
    const pov = activePillData('jjPovGroup', 'pov') || '第三人称';
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/create', {
            title,
            premise,
            genre: document.getElementById('jjGenre').value,
            style: document.getElementById('jjStyle').value,
            tone: document.getElementById('jjTone').value,
            length,
            perspective: pov,
        });
        if (data.error) {
            toast('⚠️ ' + data.error);
            return;
        }
        toast('✅ 项目创建成功');
        renderBookInfo(data);
    } catch (e) {
        toast('⚠️ 创建失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

function requireBook() {
    if (!jjBookId) {
        toast('⚠️ 请先在左侧「📖 创作卡片」创建项目');
        return false;
    }
    return true;
}

function renderCharacters(list) {
    const box = document.getElementById('jjCharsList');
    if (!list || !list.length) {
        box.innerHTML = '<div class="sec-body">❌ 暂无人物</div>';
        return;
    }
    box.innerHTML = list.map(c => `
        <div class="person-card">
            <div class="p-name">${escapeHtml(c.name)} <span class="p-role">${escapeHtml(c.role || '')}</span></div>
            <div class="p-desc">🧬 性格：${escapeHtml(c.personality || '')}</div>
            <div class="p-desc">🎭 外貌：${escapeHtml(c.appearance || '')}</div>
            <div class="p-desc">🏠 背景：${escapeHtml(c.background || '')}</div>
        </div>
    `).join('');
}

const jjGenCharsBtn = document.getElementById('jjGenCharsBtn');
jjGenCharsBtn.addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/characters/generate', { bookId: jjBookId });
        showStatus('jjCharsStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (!data.error) renderCharacters(data.characters);
        if (!data.error) toast('✅ 人物生成完成');
    } catch (e) {
        showStatus('jjCharsStatus', `<span class="status-err">${escapeHtml(e.message)}</span>`);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjListCharsBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/characters/list', { bookId: jjBookId });
        showStatus('jjCharsStatus', `<span class="${data.error ? 'status-err' : 'status-muted'}">${escapeHtml(data.status || data.error || '')}</span>`);
        renderCharacters(data.characters);
    } catch (e) {
        toast('⚠️ 刷新失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjLockCharsBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/characters/lock', { bookId: jjBookId });
        showStatus('jjCharsStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (!data.error) toast('🔒 人物已锁定');
    } catch (e) {
        toast('⚠️ 锁定失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

function outlineItemHtml(n, depth) {
    const badge = n.level === 'arc' ? '🎢 卷' : n.level === 'chapter_goal' ? '📄 章' : '·';
    return `
        <div class="ol-item ${n.level === 'arc' ? 'arc' : 'chapter_goal'}" style="margin-left:${depth * 22}px;">
            ${n.level === 'arc' ? '<span class="ol-badge">🎢 阶段</span>' : '<span class="ol-badge">📄 章</span>'}
            <div><span class="ol-title">${escapeHtml(n.title || '')}</span>${n.summary ? `<br><span class="status-muted">${escapeHtml(n.summary)}</span>` : ''}</div>
        </div>
    `;
}

function renderOutlineList(nodes) {
    const box = document.getElementById('jjOutlineList');
    if (!nodes || !nodes.length) {
        box.innerHTML = '<div class="sec-body">❌ 暂无大纲节点</div>';
        return;
    }
    box.innerHTML = nodes.map(n => outlineItemHtml(n, 0)).join('');
}

const jjGenOutlineBtn = document.getElementById('jjGenOutlineBtn');
jjGenOutlineBtn.addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/outline/generate', { bookId: jjBookId });
        showStatus('jjOutlineStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (!data.error) renderOutlineList(data.outline);
        if (!data.error) toast('✅ 大纲生成完成');
    } catch (e) {
        showStatus('jjOutlineStatus', `<span class="status-err">${escapeHtml(e.message)}</span>`);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjListOutlineBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/outline/list', { bookId: jjBookId });
        showStatus('jjOutlineStatus', `<span class="${data.error ? 'status-err' : 'status-muted'}">${escapeHtml(data.status || data.error || '')}</span>`);
        renderOutlineList(data.outline);
    } catch (e) {
        toast('⚠️ 刷新失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjLockOutlineBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/outline/lock', { bookId: jjBookId });
        showStatus('jjOutlineStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (!data.error) toast('🔒 大纲已锁定');
    } catch (e) {
        toast('⚠️ 锁定失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

function renderChapterList(list, containerId) {
    const box = document.getElementById(containerId);
    if (!list || !list.length) {
        box.innerHTML = '<div class="sec-body">❌ 暂无章节</div>';
        return;
    }
    const statusMap = { planned: '📋 待写', drafted: '📝 草稿', confirmed: '✅ 已确认' };
    box.innerHTML = list.map(ch => `
        <div class="ch-item" data-id="${ch.id}">
            <span class="ch-no">第${ch.number}章</span>
            <span class="ch-title">${escapeHtml(ch.title || '')}</span>
            <span class="ch-status ${ch.status}">${statusMap[ch.status] || ch.status} · ${ch.target_words || 0}字</span>
        </div>
    `).join('');
}

const jjPlanBtn = document.getElementById('jjPlanBtn');
jjPlanBtn.addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/plan', { bookId: jjBookId });
        showStatus('jjPlanStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        renderChapterList(data.chapters, 'jjPlanList');
        if (!data.error) toast('✅ 章节规划完成');
    } catch (e) {
        showStatus('jjPlanStatus', `<span class="status-err">${escapeHtml(e.message)}</span>`);
    } finally {
        hideLoading(this);
    }
});

function renderChapters(list) {
    const box = document.getElementById('jjChaptersList');
    if (!list || !list.length) {
        box.innerHTML = '';
        return;
    }
    const statusMap = { planned: '📋 待写', drafted: '📝 草稿', confirmed: '✅ 已确认' };
    box.innerHTML = list.map(ch => `
        <div class="ch-item" data-chid="${ch.id}">
            <span class="ch-no">第${ch.number}章</span>
            <span class="ch-title">${escapeHtml(ch.title || '')}</span>
            <span class="ch-status ${ch.status}">${statusMap[ch.status] || ch.status} · ${ch.target_words || 0}字</span>
        </div>
    `).join('');

    const sel = document.getElementById('jjPreviewSel');
    sel.innerHTML = '<option value="">选择章节预览...</option>' + list.map(ch =>
        `<option value="${ch.id}">第${ch.number}章《${escapeHtml(ch.title || '')}》${statusMap[ch.status] || ''}</option>`
    ).join('');
}

document.getElementById('jjListChaptersBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/chapters/list', { bookId: jjBookId });
        renderChapters(data.chapters || []);
    } catch (e) {
        toast('⚠️ 刷新失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

const jjLocateBtn = document.getElementById('jjLocateBtn');
jjLocateBtn.addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/locate', { bookId: jjBookId });
        const infoEl = document.getElementById('jjTargetInfo');
        const titleEl = document.getElementById('jjChTitle');
        if (data.error) {
            infoEl.innerHTML = `<span class="${data.error.includes('❌') ? 'status-err' : 'status-warn'}">${escapeHtml(data.status || data.error || '')}</span>`;
            infoEl.classList.remove('hidden');
            titleEl.style.display = 'none';
            return;
        }
        jjChapterId = data.chapterId;
        infoEl.innerHTML = escapeHtml(data.status || data.info || '').replace(/\n/g, '<br>');
        infoEl.classList.remove('hidden');
        titleEl.textContent = data.titleMd || '';
        titleEl.style.display = data.titleMd ? 'block' : 'none';
        document.getElementById('jjContent').value = data.content || '';
        showStatus('jjWriteStatus', escapeHtml(data.changesMd || ''));
        const changes = document.getElementById('jjChanges');
        changes.innerHTML = data.changes && data.changes.length
            ? data.changes.map(c => `<div class="fact-item">[${escapeHtml(c.kind)}] ${escapeHtml(c.subject)}：${escapeHtml(c.claim)}（置信度 ${(c.confidence || 0).toFixed(2)}）</div>`).join('')
            : '';
        const val = document.getElementById('jjValidation');
        val.innerHTML = data.validation && data.validation.length
            ? data.validation.map(v => `<div class="issue-item ${v.severity}">${issueIcon(v.severity)} [${escapeHtml(v.category)}] ${escapeHtml(v.subject)}：${escapeHtml(v.description)}</div>`).join('')
            : '';
        if (!data.error) toast('📍 ' + (data.chapterNo ? `已定位到第${data.chapterNo}章` : '已定位'));
    } catch (e) {
        toast('⚠️ 定位失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

function issueIcon(sev) {
    return sev === 'error' ? '🔴' : sev === 'warning' ? '🟡' : '🔵';
}

function renderWriteResult(data) {
    const titleEl = document.getElementById('jjChTitle');
    titleEl.textContent = data.title || '';
    titleEl.style.display = data.title ? 'block' : 'none';
    showStatus('jjWriteStatus', escapeHtml(data.status || '').replace(/\n/g, '<br>'));
    document.getElementById('jjContent').value = data.content || '';
    const changes = document.getElementById('jjChanges');
    changes.innerHTML = data.changes && data.changes.length
        ? data.changes.map(c => `<div class="fact-item">[${escapeHtml(c.kind)}] ${escapeHtml(c.subject)}：${escapeHtml(c.claim)}（置信度 ${(c.confidence || 0).toFixed(2)}）</div>`).join('')
        : '<div class="sec-body">无候选事实</div>';
    const val = document.getElementById('jjValidation');
    if (data.validation && data.validation.length) {
        val.innerHTML = data.validation.map(v => `<div class="issue-item ${v.severity}">${issueIcon(v.severity)} [${escapeHtml(v.category)}] ${escapeHtml(v.subject)}：${escapeHtml(v.description)}${v.suggestion ? `<br>💡 ${escapeHtml(v.suggestion)}` : ''}</div>`).join('');
    } else {
        val.innerHTML = '<div class="issue-item good">✅ 未发现一致性问题</div>';
    }
}

const jjWriteBtn = document.getElementById('jjWriteBtn');
jjWriteBtn.addEventListener('click', async function() {
    if (!jjChapterId) {
        toast('⚠️ 请先点击「📍 定位到下一章」');
        return;
    }
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/write', { chapterId: jjChapterId });
        if (data.error) {
            showStatus('jjWriteStatus', `<span class="status-err">${escapeHtml((data.status || '') + (data.error || ''))}</span>`);
            return;
        }
        renderWriteResult(data);
        toast('✍️ 章节已生成');
    } catch (e) {
        toast('⚠️ 生成失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjPolishBtn').addEventListener('click', async function() {
    if (!jjChapterId) {
        toast('⚠️ 请先定位到已生成的章节');
        return;
    }
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/polish', { chapterId: jjChapterId });
        if (data.error) {
            showStatus('jjWriteStatus', `<span class="status-err">${escapeHtml((data.status || '') + (data.error || ''))}</span>`);
            return;
        }
        renderWriteResult(data);
        toast('🔧 修复润色完成');
    } catch (e) {
        toast('⚠️ 润色失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjConfirmBtn').addEventListener('click', async function() {
    if (!jjChapterId) {
        toast('⚠️ 没有可确认的章节');
        return;
    }
    const force = document.getElementById('jjForce').checked;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/confirm', { chapterId: jjChapterId, bookId: jjBookId, force });
        showStatus('jjConfirmStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (data.chapters && data.chapters.length) renderChapters(data.chapters);
        if (!data.error) toast('✅ 已确认本章');
    } catch (e) {
        toast('⚠️ 确认失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjPreviewBtn').addEventListener('click', async function() {
    const chId = document.getElementById('jjPreviewSel').value;
    if (!chId) {
        toast('⚠️ 请先选择章节');
        return;
    }
    try {
        const data = await postJSON('/api/guided/view', { chapterId: chId });
        if (data.error) {
            toast('⚠️ ' + data.error);
            return;
        }
        document.getElementById('jjPreviewTitle').textContent = data.title || '';
        document.getElementById('jjPreviewTitle').style.display = 'block';
        document.getElementById('jjPreviewContent').value = data.content || '';
    } catch (e) {
        toast('⚠️ 预览失败：' + e.message);
    }
});

const jjCompleteBtn = document.getElementById('jjCompleteBtn');
jjCompleteBtn.addEventListener('click', async function() {
    if (!requireBook()) return;
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/complete', { bookId: jjBookId });
        showStatus('jjCompleteStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        if (!data.error) toast('🏁 完本成功');
    } catch (e) {
        toast('⚠️ 完本失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

document.getElementById('jjExportBtn').addEventListener('click', async function() {
    if (!requireBook()) return;
    const scope = activePillData('jjScopeGroup', 'scope') || 'confirmed';
    showLoading(this);
    try {
        const data = await postJSON('/api/guided/export', { bookId: jjBookId, scope });
        showStatus('jjCompleteStatus', `<span class="${data.error ? 'status-err' : 'status-ok'}">${escapeHtml(data.status || data.error || '')}</span>`);
        const link = document.getElementById('jjExportLink');
        if (data.download) {
            link.href = '/api/download?name=' + encodeURIComponent(data.download);
            link.style.display = 'inline-flex';
        } else {
            link.style.display = 'none';
        }
    } catch (e) {
        toast('⚠️ 导出失败：' + e.message);
    } finally {
        hideLoading(this);
    }
});

// ========== 高级版：上传大纲 ==========
const uploadZone = document.getElementById('uploadZone');
const fileInput = document.getElementById('fileInput');

uploadZone.addEventListener('click', () => fileInput.click());
uploadZone.addEventListener('dragover', e => { e.preventDefault(); uploadZone.style.borderColor = 'rgba(255,180,140,0.6)'; });
uploadZone.addEventListener('dragleave', () => { uploadZone.style.borderColor = ''; });
uploadZone.addEventListener('drop', e => {
    e.preventDefault();
    uploadZone.style.borderColor = '';
    if (e.dataTransfer.files.length) fileInput.files = e.dataTransfer.files;
});

let gzBookId = '';

async function uploadAndParse() {
    const file = fileInput.files[0];
    if (!file) {
        toast('⚠️ 请先选择文件');
        return;
    }
    document.getElementById('gzProgress').style.display = 'block';
    const steps = document.querySelectorAll('#gzSteps .step-item');
    steps.forEach(s => s.classList.remove('active', 'done'));

    const setStep = (i, state) => {
        steps.forEach((s, idx) => {
            s.classList.remove('active', 'done');
            if (idx === i && state === 'active') s.classList.add('active');
            if (idx < i || state === 'done') s.classList.add('done');
        });
    };

    setStep(0, 'active');
    try {
        const form = new FormData();
        form.append('file', file);
        const resp = await fetch('/api/advanced/parse', { method: 'POST', body: form });
        const data = await resp.json();
        setStep(3, 'done');
        document.getElementById('gzParsed').style.display = 'block';
        const parsed = document.getElementById('gzParsed');
        const outline = data.outline || [];
        gzBookId = data.bookId || '';
        parsed.innerHTML = `
            <div class="parsed-grid">
                <div class="parsed-card">
                    <div class="pc-title">📖 项目已创建</div>
                    <div class="pc-line">书名：${escapeHtml(data.title || '')}</div>
                    <div class="pc-line">共解析到 ${outline.length} 个大纲节点</div>
                    <div class="pc-line" style="font-size:11px;">${escapeHtml(data.status || '')}</div>
                </div>
            </div>
            ${outline.length ? `
            <div class="ol-list" style="margin-top:12px;">
                ${outline.map(n => `
                    <div class="ol-item" style="margin-left:${n.level === 'chapter_goal' ? 22 : 0}px;">
                        <span class="ol-badge">${n.level === 'arc' ? '🎢 阶段' : '📄 章'}</span>
                        <span class="ol-title">${escapeHtml(n.title || '')}</span>
                    </div>`).join('')}
            </div>` : ''}
        `;
        document.getElementById('gzGenerateRow').style.display = 'block';
        toast('✅ 文件解析完成');
    } catch (e) {
        toast('⚠️ 解析失败：' + e.message);
    }
}

fileInput.addEventListener('change', uploadAndParse);

document.getElementById('gzGenerateBtn').addEventListener('click', async function() {
    if (!gzBookId) {
        toast('⚠️ 请先上传并解析文件');
        return;
    }
    showLoading(this);
    const logBox = document.getElementById('gzLog');
    document.getElementById('gzResult').style.display = 'block';
    document.getElementById('gzDownload').style.display = 'none';
    logBox.textContent = '📖 开始生成整本小说...\n';
    try {
        const data = await postJSON('/api/advanced/generate', { bookId: gzBookId });
        logBox.textContent = data.log || '';
        if (data.success && data.download) {
            document.getElementById('gzDownload').href = '/api/download?name=' + encodeURIComponent(data.download);
            document.getElementById('gzDownload').style.display = 'inline-flex';
        }
    } catch (e) {
        logBox.textContent += '\n❌ ' + e.message;
    } finally {
        hideLoading(this);
    }
});

// ========== 初始化 ==========
checkOllamaStatus();
loadModels();