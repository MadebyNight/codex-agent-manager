$ErrorActionPreference = 'Stop'
$projectRoot = $PSScriptRoot
$pythonPath = Join-Path $projectRoot '.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    Write-Host '首次启动：正在创建项目虚拟环境并安装依赖。'
    python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw '需要 Python 3.11 或更高版本。' }
    & $pythonPath -m pip install -r (Join-Path $projectRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查网络后重试。' }
}
$url = 'http://127.0.0.1:8765'
try {
    $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
    if ($health.app -eq 'codex-agent-manager') {
        Start-Process $url
        exit
    }
} catch { }
$logDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$process = Start-Process -FilePath $pythonPath -ArgumentList '-B', '-m', 'app.server' -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'server.log') -RedirectStandardError (Join-Path $logDir 'server-error.log') -PassThru
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 200
    if ($process.HasExited) { throw '服务启动失败，请查看 .local/server-error.log（端口 8765 可能被占用）。' }
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1
        if ($health.app -eq 'codex-agent-manager') {
            Set-Content -LiteralPath (Join-Path $logDir 'server.pid') -Value $process.Id
            Start-Process $url
            exit
        }
    } catch { }
}
throw '服务尚未就绪，请查看 .local/server-error.log。'
