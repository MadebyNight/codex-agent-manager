$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.local/server.pid'
$portFile = Join-Path $PSScriptRoot '.local/server.port'
if (Test-Path -LiteralPath $pidFile) {
    $serviceProcessId = [int](Get-Content -LiteralPath $pidFile)
    $port = if (Test-Path -LiteralPath $portFile) { [int](Get-Content -LiteralPath $portFile) } else { 0 }
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId"
    $listener = if ($port -gt 0) {
        Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue |
            Where-Object OwningProcess -eq $serviceProcessId | Select-Object -First 1
    }
    $pythonPath = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if ($process -and $listener -and $process.CommandLine.Contains($pythonPath) -and
        $process.CommandLine -match 'app\.server' -and
        $process.CommandLine -match ('--port\s+' + $port + '(\s|$)')) {
        Stop-Process -Id $serviceProcessId
        Remove-Item -LiteralPath $pidFile, $portFile
        Write-Host '本地面板已停止。'
    } else {
        Write-Host '没有找到由此项目启动的服务。'
    }
}
