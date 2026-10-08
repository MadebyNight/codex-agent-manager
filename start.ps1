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
$logDir = Join-Path $projectRoot '.local'
New-Item -ItemType Directory -Path $logDir -Force | Out-Null
$version = (& $pythonPath -B -m app.server --version).Trim()
$pidFile = Join-Path $logDir 'server.pid'
$portFile = Join-Path $logDir 'server.port'
$savedProcessId = 0
$savedPort = 0
if ((Test-Path -LiteralPath $pidFile) -and (Test-Path -LiteralPath $portFile)) {
    [void][int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$savedProcessId)
    [void][int]::TryParse((Get-Content -LiteralPath $portFile -Raw).Trim(), [ref]$savedPort)
}
if ($savedProcessId -gt 0 -and $savedPort -gt 0) {
    $existing = Get-CimInstance Win32_Process -Filter "ProcessId = $savedProcessId"
    $listener = Get-NetTCPConnection -LocalPort $savedPort -State Listen -ErrorAction SilentlyContinue |
        Where-Object OwningProcess -eq $savedProcessId | Select-Object -First 1
    if ($existing -and $existing.CommandLine.Contains($pythonPath) -and
        $existing.CommandLine -match 'app\.server' -and
        $existing.CommandLine -match ('--port\s+' + $savedPort + '(\s|$)') -and $listener) {
        try {
            $url = "http://127.0.0.1:$savedPort"
            $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 2
            if ($health.app -eq 'codex-agent-manager' -and $health.version -eq $version) {
                Start-Process $url
                exit
            }
        } catch { }
    }
}
$port = 8765
while (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
    $port++
    if ($port -gt 8865) { throw '没有可用的本地面板端口（8765–8865）。' }
}
$url = "http://127.0.0.1:$port"
$process = Start-Process -FilePath $pythonPath -ArgumentList '-B', '-m', 'app.server', '--port', $port -WorkingDirectory $projectRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $logDir 'server.log') -RedirectStandardError (Join-Path $logDir 'server-error.log') -PassThru
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 200
    if ($process.HasExited) { throw "服务启动失败，请查看 .local/server-error.log（端口 $port 可能被占用）。" }
    try {
        $health = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1
        if ($health.app -eq 'codex-agent-manager' -and $health.version -eq $version) {
            $listener = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
                Select-Object -First 1
            if (-not $listener) { continue }
            $serverProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
            if (-not $serverProcess -or -not $serverProcess.CommandLine.Contains($pythonPath) -or
                $serverProcess.CommandLine -notmatch 'app\.server') { continue }
            Set-Content -LiteralPath $pidFile -Value $listener.OwningProcess
            Set-Content -LiteralPath $portFile -Value $port
            Start-Process $url
            exit
        }
    } catch { }
}
throw '服务尚未就绪，请查看 .local/server-error.log。'
