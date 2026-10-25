param([ValidateSet('collector','legacy')][string]$Mode='collector', [switch]$CheckOnly, [switch]$DesktopTest)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object System.Text.UTF8Encoding($false)
$OutputEncoding=[Console]::OutputEncoding
[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
$code=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$bundle=Test-Path -LiteralPath (Join-Path $code '..\启动程序\启动.py')
if ($bundle) { $tools=[IO.Path]::GetFullPath((Join-Path $code '..\依赖工具')) }
elseif ($env:YT_DOWNLOADER_TOOLS_DIR) { $tools=[IO.Path]::GetFullPath($env:YT_DOWNLOADER_TOOLS_DIR) }
else { $tools=Join-Path $env:USERPROFILE '.yt_dlp_tools' }
$env:PYTHONHOME=$null; $env:PYTHONPATH=$null; $env:VIRTUAL_ENV=$null
$env:TCL_LIBRARY=$null; $env:TK_LIBRARY=$null
$env:PYTHONUTF8='1'; $env:PYTHONNOUSERSITE='1'; $env:PYTHONDONTWRITEBYTECODE='1'
function Test-Python([string]$exe) {
    if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { return $false }
    try {
        & $exe -I -B -c 'import sys,struct,ssl,tkinter,venv,ensurepip,pip; from pathlib import Path; assert sys.version_info[:2]==(3,13) and struct.calcsize(chr(80))==8; assert sys.prefix!=sys.base_prefix or Path(sys.prefix).resolve().is_relative_to(Path(sys.argv[1]).resolve()); tkinter.Tcl()' $tools *> $null
        return $LASTEXITCODE -eq 0
    } catch { return $false }
}
function Remove-Managed([string]$target) {
    $full=[IO.Path]::GetFullPath($target)
    if (-not $full.StartsWith($tools.TrimEnd('\')+'\',[StringComparison]::OrdinalIgnoreCase)) { throw '修复路径超出依赖工具目录' }
    if (Test-Path -LiteralPath $full) {
        if ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '不允许清理链接目录' }
        Remove-Item -LiteralPath $full -Recurse -Force
    }
}
function Hash-File([string]$file) {
    $stream=[IO.File]::OpenRead($file)
    $digest=[Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($digest.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
    finally { $stream.Dispose(); $digest.Dispose() }
}
function Get-VerifiedArchive([string]$url,[string]$dest,[string]$hash) {
    if ((Test-Path -LiteralPath $dest) -and (Hash-File $dest) -eq $hash) { return }
    $urls=@($url,('https://gh-proxy.com/'+$url),('https://ghfast.top/'+$url))
    foreach ($item in $urls) {
        if ($item -ne $urls[0]) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }
        Write-Host '[依赖] 下载固定版本 Python；正在传输时会继续等待，断线可续传。'
        for ($attempt=0;$attempt -lt 2;$attempt++) {
            try {
                $curl=Join-Path $env:SystemRoot 'System32\curl.exe'
                if (Test-Path -LiteralPath $curl) {
                    & $curl --fail --location --continue-at - --connect-timeout 30 --speed-time 60 --speed-limit 1 --output $dest $item
                    if ($LASTEXITCODE -ne 0) { if ($LASTEXITCODE -in @(33,36)) { Remove-Item -LiteralPath $dest -Force -ErrorAction SilentlyContinue }; throw '下载中断' }
                } else { Invoke-WebRequest -UseBasicParsing -Uri $item -OutFile $dest -TimeoutSec 120 }
                if ((Hash-File $dest) -ne $hash) { Remove-Item -LiteralPath $dest -Force; throw 'Python 文件摘要不匹配' }
                return
            } catch { Write-Host ('[依赖] '+$_.Exception.Message) }
        }
    }
    throw 'Python 下载失败；已保留同源可续传文件，网络恢复后重新启动即可。'
}
try {
    if (-not [Environment]::Is64BitOperatingSystem) { throw '需要 Windows 64 位系统' }
    New-Item -ItemType Directory -Path $tools -Force | Out-Null
    $hash=[Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($tools.ToLowerInvariant()))
    $mutex=New-Object Threading.Mutex($false,('Local\media-dl-python-'+([BitConverter]::ToString($hash).Replace('-',''))))
    $owned=$false
    try {
        Write-Host '[依赖] 检查 Python 运行环境…'
        try { $owned=$mutex.WaitOne(300000) } catch [Threading.AbandonedMutexException] { $owned=$true }
        if (-not $owned) { throw '另一个启动程序仍在准备 Python，请稍后重试' }
        $python=Join-Path $tools 'python\python.exe'
        if (-not $bundle) {
            foreach ($candidate in @($env:YT_DOWNLOADER_PYTHON,'D:\python-envs\yt-downloader\Scripts\python.exe',$python)) {
                if ($candidate -and (Test-Python $candidate)) { $python=$candidate; break }
            }
        }
        if (-not (Test-Python $python)) {
            $python=Join-Path $tools 'python\python.exe'
            $cache=Join-Path $tools 'repair-cache'
            New-Item -ItemType Directory -Path $cache -Force | Out-Null
            $stage=Join-Path $tools ('.python-stage-'+[guid]::NewGuid().ToString('N'))
            New-Item -ItemType Directory -Path $stage | Out-Null
            try {
                $record=Join-Path $cache 'python.json'
                $zip=Join-Path $cache 'python.zip'
                $local=$false
                if ((Test-Path -LiteralPath $record) -and (Test-Path -LiteralPath $zip)) {
                    $meta=Get-Content -LiteralPath $record -Raw -Encoding UTF8 | ConvertFrom-Json
                    if ($meta.sha256 -match '^[a-fA-F0-9]{64}$' -and (Hash-File $zip) -eq $meta.sha256) {
                        Write-Host '[依赖] 使用随包校验过的 Python 修复缓存。'
                        Add-Type -AssemblyName System.IO.Compression.FileSystem
                        $archive=[IO.Compression.ZipFile]::OpenRead($zip)
                        try { foreach ($entry in $archive.Entries) { $p=[IO.Path]::GetFullPath((Join-Path $stage $entry.FullName)); if (-not $p.StartsWith($stage+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Python 修复包包含越界路径' } } } finally { $archive.Dispose() }
                        [IO.Compression.ZipFile]::ExtractToDirectory($zip,$stage)
                        $candidate=Join-Path $stage 'python.exe'; $local=$true
                    }
                }
                if (-not $local) {
                    $pin=(Get-Content -LiteralPath (Join-Path $PSScriptRoot 'runtime-lock.json') -Raw -Encoding UTF8 | ConvertFrom-Json).python
                    $archive=Join-Path $cache 'python-upstream.tar.gz'
                    Get-VerifiedArchive $pin.url $archive $pin.sha256
                    $tar=Join-Path $env:SystemRoot 'System32\tar.exe'
                    $names=& $tar -tzf $archive
                    if ($LASTEXITCODE -ne 0) { throw 'Python 压缩包无法读取' }
                    foreach ($name in $names) { if ($name -notmatch '^python(?:/|$)' -or $name -match '(^|/)\.\.(/|$)|\\|:') { throw 'Python 压缩包路径异常' } }
                    & $tar -xzf $archive -C $stage
                    if ($LASTEXITCODE -ne 0) { throw 'Python 解压失败' }
                    $candidate=Join-Path $stage 'python\python.exe'
                }
                if (-not (Test-Python $candidate)) { throw '新 Python 无法运行，保留原环境并停止' }
                $incoming=Split-Path -Parent $candidate
                $destination=Join-Path $tools 'python'
                $backup=Join-Path $tools ('.python-backup-'+[guid]::NewGuid().ToString('N'))
                if (Test-Path -LiteralPath $destination) { Move-Item -LiteralPath $destination -Destination $backup }
                try { Move-Item -LiteralPath $incoming -Destination $destination }
                catch { if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $destination }; throw }
                if (Test-Path -LiteralPath $backup) { Remove-Managed $backup }
            } finally { Remove-Managed $stage }
        }
    } finally { if ($owned) { $mutex.ReleaseMutex() }; $mutex.Dispose() }
    $env:YT_DOWNLOADER_PYTHON=$python; $env:YT_DOWNLOADER_TOOLS_DIR=$tools
    $launch=@('-B','-X','utf8',(Join-Path $PSScriptRoot 'runtime_setup.py'),'--mode',$Mode)
    if ($CheckOnly) { $launch+='--check' }
    if ($DesktopTest) { $launch+='--test-electron' }
    & $python @launch
    exit $LASTEXITCODE
} catch { Write-Host ('[依赖] 准备失败：'+$_.Exception.Message); exit 1 }
