# install-daemon.ps1 - the Smriti daemon on Windows (laptop = primary workspace).
#
#   powershell -ExecutionPolicy Bypass -File deploy\install-daemon.ps1
#
# Idempotent. Needs: git, an ssh client (Windows OpenSSH is fine).
# Uninstall: docs\UNINSTALL.md.
$ErrorActionPreference = "Stop"
# Run from inside a checkout -> install THAT checkout (no second clone that
# silently lags your working copy). Else SMRITI_DIR, else ~\smriti.
$Here = Split-Path -Parent $PSScriptRoot
$Dir = if ($env:SMRITI_DIR) { $env:SMRITI_DIR } elseif (Test-Path "$Here\.git") { $Here } else { "$HOME\smriti" }

Write-Host "== uv"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    irm https://astral.sh/uv/install.ps1 | iex
    $env:Path = "$HOME\.local\bin;$env:Path"
}

Write-Host "== repo -> $Dir"
if ($Dir -eq $Here) { Write-Host "   using this checkout (no pull)" }
elseif (Test-Path "$Dir\.git") { git -C $Dir pull -q } else { git clone -q https://github.com/Naivedya-sahu/Smriti.git $Dir }
Set-Location $Dir
uv sync
$env:PYTHONUTF8 = "1"
uv run python host/ink.py selfcheck

Write-Host "== local AI (primary, offline): LM Studio + Qwen3-VL-4B"
$lms = "$HOME\.lmstudio\bin\lms.exe"
if (Test-Path $lms) {
    & $lms get qwen/qwen3-vl-4b --yes
    & $lms server start
} else {
    Write-Host "   !! LM Studio missing - install from lmstudio.ai, open it once, rerun."
}
Write-Host "   online fallbacks (optional): GEMINI_API_KEY via wizard; Claude CLI - see config.toml"

Write-Host "== LaTeX check (maths/circuits need pdflatex - MiKTeX on Windows)"
if (-not (Get-Command pdflatex -ErrorAction SilentlyContinue)) {
    Write-Host "   !! pdflatex not found - install MiKTeX (miktex.org), run initexmf once."
    Write-Host "      Without it maths/circuit replies degrade to notes."
}

Write-Host "== tablet ssh alias (edit HostName for your network)"
$sshCfg = "$HOME\.ssh\config"
if (-not (Test-Path $sshCfg) -or -not (Select-String -Path $sshCfg -Pattern "^Host rm2$" -Quiet)) {
    New-Item -ItemType Directory -Force "$HOME\.ssh" | Out-Null
    Add-Content $sshCfg "`nHost rm2`n  HostName 10.11.99.1`n  User root`n  ConnectTimeout 5"
}

Write-Host "== setup wizard (keys + control token; rerun anytime)"
uv run python host/setup_wizard.py

Write-Host ""
Write-Host "Start the daemon:   uv run python host/monke.py"
Write-Host "OCR on this box too? deploy\install-scribe.ps1"
