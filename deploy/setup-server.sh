#!/bin/bash
# setup-server.sh — deploy the Smriti Monke daemon on a Pi / any Linux box.
# Idempotent; run as the user that will own the daemon:
#
#   curl -LsSf https://raw.githubusercontent.com/Naivedya-sahu/Smriti/main/deploy/setup-server.sh | bash
#   # or: git clone https://github.com/Naivedya-sahu/Smriti ~/smriti && ~/smriti/deploy/setup-server.sh
#
# What it does: uv + repo + venv + RM2 ssh key + config templates + systemd
# unit (template: smriti-monke@<user>.service). What it does NOT do: touch
# your network — YOU make `ssh rm2` work (see printed checklist).

set -euo pipefail
REPO_URL="https://github.com/Naivedya-sahu/Smriti.git"
DIR="$HOME/smriti"

echo "== uv"
command -v uv >/dev/null 2>&1 || command -v "$HOME/.local/bin/uv" >/dev/null 2>&1 || \
    curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"

echo "== repo -> $DIR"
if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q "$REPO_URL" "$DIR"; fi
cd "$DIR"
uv sync
PYTHONUTF8=1 uv run python host/ink.py selfcheck

echo "== LaTeX (maths + circuit rendering)"
# monke renders $$...$$ replies through pdflatex; without it those degrade
# to a short note. Skip if you don't need maths/circuits (SMRITI_NO_TEX=1).
if [ "${SMRITI_NO_TEX:-0}" != "1" ] && ! command -v pdflatex >/dev/null 2>&1 \
        && [ ! -x "$(echo "$HOME"/.TinyTeX/bin/*/pdflatex 2>/dev/null)" ]; then
    echo "   installing TinyTeX (user-space, no root)…"
    curl -sL https://yihui.org/tinytex/install-bin-unix.sh | sh >/dev/null 2>&1 || \
        echo "   !! TinyTeX install failed — maths/circuits will degrade to notes"
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

echo "== api key file"
mkdir -p "$HOME/.config/smriti"
[ -f "$HOME/.config/smriti/env" ] || { echo "GEMINI_API_KEY=" > "$HOME/.config/smriti/env"; chmod 600 "$HOME/.config/smriti/env"; }

echo "== systemd unit"
if command -v systemctl >/dev/null 2>&1 && [ -w /etc/systemd/system ] 2>/dev/null || sudo -n true 2>/dev/null; then
    sudo cp deploy/smriti-monke.service "/etc/systemd/system/smriti-monke@.service"
    sudo systemctl daemon-reload
    echo "   installed: smriti-monke@$USER.service"
else
    echo "   (no sudo — install manually: sudo cp deploy/smriti-monke.service /etc/systemd/system/smriti-monke@.service)"
fi

cat << EOF

============================================================
Setup done. Finish these BY HAND, then start the daemon:

1. Tablet key — append this line to /home/root/.ssh/authorized_keys on the RM2:
   $(cat "$HOME/.ssh/id_ed25519_rm2.pub")

2. Network — edit ~/.ssh/config 'Host rm2' HostName to the tablet's address
   on YOUR network (USB 10.11.99.1 / LAN IP / tailscale name), until:
   ssh rm2 'echo ok'        # prints ok

3. API key — put your key in ~/.config/smriti/env:
   GEMINI_API_KEY=AIza...   # aistudio.google.com, free
   (any OpenAI-compatible endpoint works — edit [ai] in $DIR/config.toml)

4. Tablet package (once, on the RM2 — see README "Device one-time setup"):
   vellum + Smriti apk repo + 'vellum add smriti'

5. Start:
   sudo systemctl enable --now smriti-monke@$USER
   journalctl -fu smriti-monke@$USER
============================================================
EOF
