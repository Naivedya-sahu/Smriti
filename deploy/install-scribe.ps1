# install-scribe.ps1 - the scribe OCR service on a Windows GPU box
# (laptop 3050 / desktop 5060).
#
#   powershell -ExecutionPolicy Bypass -File deploy\install-scribe.ps1
#
# Installs the `scribe` extra (torch + transformers; TrOCR ~1.4GB cached on
# first run), a CUDA torch when an NVIDIA GPU is present, and a logon
# Scheduled Task so the service survives reboots.
# Uninstall: docs\UNINSTALL.md.
$ErrorActionPreference = "Stop"
$Dir = if ($env:SMRITI_DIR) { $env:SMRITI_DIR } else { "$HOME\smriti" }
if (-not (Test-Path "$Dir\.git")) { git clone -q https://github.com/Naivedya-sahu/Smriti.git $Dir }
Set-Location $Dir

Write-Host "== scribe extra (torch, transformers, sentencepiece)"
uv sync --extra scribe

if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
    Write-Host "== NVIDIA GPU found - swapping in CUDA torch"
    uv pip install torch --index-url https://download.pytorch.org/whl/cu126
}

Write-Host "== selfcheck (downloads TrOCR on first run)"
$env:PYTHONUTF8 = "1"
uv run python host/scribe.py selfcheck

Write-Host "== autostart (Scheduled Task at logon)"
$uv = (Get-Command uv).Source
$act = New-ScheduledTaskAction -Execute $uv -Argument "run python host/scribe.py serve" -WorkingDirectory $Dir
$trg = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "smriti-scribe" -Action $act -Trigger $trg -Force | Out-Null
Start-ScheduledTask -TaskName "smriti-scribe"

Write-Host ""
Write-Host "scribe up: http://<this-box>:7433/health"
Write-Host "Add this box to config.toml [ocr] endpoints on the daemon machine."
