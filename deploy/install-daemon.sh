#!/bin/bash
# install-daemon.sh — the Smriti daemon on any Linux box.
#
#   deploy/install-daemon.sh                # foreground use (dev / laptop)
#   deploy/install-daemon.sh --systemd      # server mode (Pi): unit installed
#
# Idempotent. Laptop-on-Windows is the PRIMARY workspace → use
# install-daemon.ps1 there; this script covers Pi/central-node and any
# Linux laptop. Uninstall: docs/UNINSTALL.md.
set -euo pipefail
REPO_URL="https://github.com/Naivedya-sahu/Smriti.git"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
# run from inside a checkout -> install THAT checkout (no lagging 2nd clone)
if [ -n "${SMRITI_DIR:-}" ]; then DIR="$SMRITI_DIR"
elif [ -d "$HERE/.git" ]; then DIR="$HERE"
else DIR="$HOME/smriti"; fi

echo "== uv"
command -v uv >/dev/null 2>&1 || command -v "$HOME/.local/bin/uv" >/dev/null 2>&1 || \
    curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "== repo -> $DIR"
if [ "$DIR" = "$HERE" ]; then echo "   using this checkout (no pull)"
elif [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"
uv sync
PYTHONUTF8=1 uv run python host/ink.py selfcheck

echo "== claude CLI (primary AI — rides the subscription)"
if ! command -v claude >/dev/null 2>&1 && [ ! -x "$HOME/.local/bin/claude" ]; then
    curl -fsSL https://claude.ai/install.sh | bash || \
        echo "   !! claude install failed — daemon will use the fallback chain"
fi
echo "   auth once: claude login   (headless box: claude setup-token)"

echo "== LaTeX (maths + circuit rendering)"
if [ "${SMRITI_NO_TEX:-0}" != "1" ] && ! command -v pdflatex >/dev/null 2>&1 \
        && [ ! -x "$(echo "$HOME"/.TinyTeX/bin/*/pdflatex 2>/dev/null)" ]; then
    echo "   installing TinyTeX (user-space, no root)…"
    curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh >/dev/null 2>&1 || \
        echo "   !! TinyTeX install failed — maths/circuits degrade to notes"
    "$HOME"/.TinyTeX/bin/*/tlmgr install standalone preview circuitikz pgf \
        xcolor amsmath amsfonts >/dev/null 2>&1 || true
fi

echo "== ssh key for the tablet"
if [ ! -f "$HOME/.ssh/id_ed25519_rm2" ]; then
    mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519_rm2" -N "" -C "smriti@$(hostname)" -q
fi
grep -q "^Host rm2$" "$HOME/.ssh/config" 2>/dev/null || cat >> "$HOME/.ssh/config" << 'EOF'
Host rm2
  HostName 10.11.99.1
  User root
  IdentityFile ~/.ssh/id_ed25519_rm2
  ConnectTimeout 5
EOF

if [ "${1:-}" = "--systemd" ]; then
    echo "== systemd unit (server mode)"
    if sudo -n true 2>/dev/null || [ -w /etc/systemd/system ]; then
        sudo cp deploy/smriti-monke.service "/etc/systemd/system/smriti-monke@.service"
        sudo systemctl daemon-reload
        echo "   installed: smriti-monke@$USER.service"
    else
        echo "   (no sudo — manually: sudo cp deploy/smriti-monke.service /etc/systemd/system/smriti-monke@.service)"
    fi
fi

echo "== setup wizard (keys + control token; rerun anytime)"
PYTHONUTF8=1 uv run python host/setup_wizard.py || true

cat << EOF
============================================================
Finish BY HAND:
1. Tablet key -> RM2 /home/root/.ssh/authorized_keys:
   $(cat "$HOME/.ssh/id_ed25519_rm2.pub")
2. \`ssh rm2 'echo ok'\` must print ok (edit ~/.ssh/config HostName).
3. claude login (or setup-token) for the primary brain.
4. Tablet pieces: deploy/install-tablet.sh + README "Tablet one-time setup".
5. Start: uv run python host/monke.py
   (server mode: sudo systemctl enable --now smriti-monke@$USER)
============================================================
EOF
