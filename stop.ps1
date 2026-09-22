$ErrorActionPreference = 'Stop'
$pidFile = Join-Path $PSScriptRoot '.local/server.pid'
if (Test-Path -LiteralPath $pidFile) {
    $serviceProcessId = [int](Get-Content -LiteralPath $pidFile)
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $serviceProcessId"
    $expectedPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if ($process -and $process.ExecutablePath -eq $expectedPython -and $process.CommandLine -match 'app\.server') {
        Stop-Process -Id $serviceProcessId
        Write-Host '本地面板已停止。'
    } else {
        Write-Host '没有找到由此项目启动的服务。'
    }
}
