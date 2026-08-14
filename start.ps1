# ============================================================
#   Novel-Agent 一键启动 (PowerShell 版)
#   双击 start.bat 即会调用此脚本
# ============================================================

$ErrorActionPreference = "Stop"
$Port = 7860
$Url = "http://127.0.0.1:$Port"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$Host.UI.RawUI.WindowTitle = "Novel-Agent"

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   Novel-Agent 一键启动" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── 1. 检查 Python ──────────────────────────────────
Write-Host "[1/5] 检查 Python..." -NoNewline
try {
    $pyVer = (python --version 2>&1) -replace "^Python ", ""
    if ($LASTEXITCODE -ne 0) { throw "not found" }
    Write-Host "  [OK] Python $pyVer" -ForegroundColor Green
} catch {
    Write-Host "  [X] 未找到 Python" -ForegroundColor Red
    Write-Host "  下载: https://www.python.org/downloads/"
    Read-Host "按回车键退出"
    exit 1
}

# ── 2. 检查依赖 ─────────────────────────────────────
Write-Host "[2/5] 检查项目依赖..." -NoNewline
python -c "import novel_agent" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "  [*] 首次运行，正在安装依赖..." -ForegroundColor Yellow
    pip install -e ".[dev]" -q
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] 依赖安装失败" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Write-Host "  [OK] 依赖安装完成" -ForegroundColor Green
} else {
    Write-Host "  [OK] 依赖已就绪" -ForegroundColor Green
}

# ── 3. 检查 Ollama ──────────────────────────────────
Write-Host "[3/5] 检查 Ollama..." -NoNewline
$ollamaExe = Get-Command ollama -ErrorAction SilentlyContinue
if (-not $ollamaExe) {
    Write-Host "  [X] 未找到 Ollama" -ForegroundColor Red
    Write-Host "  下载: https://ollama.com/download"
    Read-Host "按回车键退出"
    exit 1
}
Write-Host "  [OK] Ollama 已安装" -ForegroundColor Green

# 检查 Ollama 服务是否运行
try {
    $null = Invoke-RestMethod -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSec 2 -ErrorAction Stop
} catch {
    Write-Host "  [*] 启动 Ollama 服务..." -ForegroundColor Yellow
    Start-Process ollama -ArgumentList "serve" -WindowStyle Minimized
    Start-Sleep -Seconds 3
}

# ── 4. 检查模型 ─────────────────────────────────────
Write-Host "[4/5] 检查模型 qwen2.5:7b-instruct..." -NoNewline
$ollamaList = ollama list 2>$null
if ($ollamaList -match "qwen2.5:7b-instruct") {
    Write-Host "  [OK] 模型已就绪" -ForegroundColor Green
} else {
    Write-Host "  [*] 模型未找到，正在拉取（约 4.7GB）..." -ForegroundColor Yellow
    ollama pull qwen2.5:7b-instruct
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [X] 模型拉取失败" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Write-Host "  [OK] 模型拉取完成" -ForegroundColor Green
}

# ── 5. 检查 .env 配置 ───────────────────────────────
Write-Host "[5/5] 检查配置文件..." -NoNewline
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "  [OK] 已从 .env.example 创建 .env" -ForegroundColor Green
    } else {
        Write-Host "  [!] 未找到 .env，将使用默认配置" -ForegroundColor Yellow
    }
} else {
    Write-Host "  [OK] .env 已存在" -ForegroundColor Green
}

# ── 6. 初始化数据库 ─────────────────────────────────
if (-not (Test-Path "data\novel_agent.db")) {
    Write-Host "[*] 初始化数据库..." -NoNewline
    python scripts\migrate.py 2>$null
    if (Test-Path "data\novel_agent.db") {
        Write-Host "  [OK] 数据库已创建" -ForegroundColor Green
    }
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   所有检查通过，正在启动服务..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── 7. 杀掉旧进程 ───────────────────────────────────
$oldConns = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($oldConns) {
    foreach ($conn in $oldConns) {
        Write-Host "[*] 终止旧进程 (PID $($conn.OwningProcess))..." -ForegroundColor Yellow
        Stop-Process -Id $conn.OwningProcess -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
}

# ── 8. 启动服务器 ───────────────────────────────────
Write-Host "[*] 启动 Flask 服务 (端口 $Port)..." -NoNewline
$serverProc = Start-Process cmd -ArgumentList "/c python webui.py > server.log 2>&1" -WindowStyle Minimized -PassThru
Write-Host "  PID=$($serverProc.Id)" -ForegroundColor Gray

# ── 9. 等待端口就绪 ─────────────────────────────────
Write-Host "[*] 等待服务就绪..." -NoNewline
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) {
        $ready = $true
        break
    }
    Write-Host "." -NoNewline -ForegroundColor Gray
}

if ($ready) {
    Write-Host " OK" -ForegroundColor Green
} else {
    Write-Host " 超时" -ForegroundColor Yellow
    Write-Host "[!] 服务启动较慢，浏览器打开后如无法访问请等待几秒刷新" -ForegroundColor Yellow
}

# ── 10. 打开浏览器 ──────────────────────────────────
Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "   Novel-Agent 已启动: $Url" -ForegroundColor Green
Write-Host "   正在为您打开浏览器..." -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

Start-Process $Url

Write-Host ""
Write-Host "提示: 关闭最小化的 Python 窗口即可停止服务。"
Write-Host ""
Read-Host "按回车键关闭此窗口（服务会继续运行）"
