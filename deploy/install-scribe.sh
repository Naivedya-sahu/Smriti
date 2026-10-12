#!/bin/bash
# install-scribe.sh — the scribe OCR service on a Linux GPU box.
#
#   deploy/install-scribe.sh            # install + selfcheck
#   deploy/install-scribe.sh --systemd  # + user service (autostart)
#
# Uninstall: docs/UNINSTALL.md.
set -euo pipefail
DIR="${SMRITI_DIR:-$HOME/smriti}"
[ -d "$DIR/.git" ] || git clone -q https://github.com/Naivedya-sahu/Smriti.git "$DIR"
cd "$DIR"
export PATH="$HOME/.local/bin:$PATH"

echo "== scribe extra (torch, transformers, sentencepiece)"
uv sync --extra scribe
if command -v nvidia-smi >/dev/null 2>&1; then
    echo "== NVIDIA GPU found — swapping in CUDA torch"
    uv pip install torch --index-url https://download.pytorch.org/whl/cu126
fi

echo "== selfcheck (downloads TrOCR ~1.4GB on first run)"
PYTHONUTF8=1 uv run python host/scribe.py selfcheck

if [ "${1:-}" = "--systemd" ]; then
    mkdir -p "$HOME/.config/systemd/user"
    cat > "$HOME/.config/systemd/user/smriti-scribe.service" << EOF
[Unit]
Description=Smriti scribe OCR service
[Service]
WorkingDirectory=$DIR
ExecStart=$HOME/.local/bin/uv run python host/scribe.py serve
Restart=on-failure
[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable --now smriti-scribe
    echo "service up (survives reboot after: sudo loginctl enable-linger $USER)"
fi
echo "scribe: http://<this-box>:7433/health — add to config.toml [ocr] endpoints."
