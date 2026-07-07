#!/bin/sh
# install-tablet.sh — put everything Smriti needs ON THE TABLET.
# Run from the repo root on any machine with ssh access to the RM2:
#
#     deploy/install-tablet.sh [ssh-host]     # default host: rm2
#
# Installs:
#   1. smriti-eye CLI + smriti-eye-watch bridge (+systemd unit)
#   2. floating in-notebook eye (xovi qt-resource-rebuilder patch)
#   3. Smriti toolbox app (xovi AppLoad)  [optional control panel]
# 2 and 3 are skipped with a note when xovi isn't installed (get it via
# reManager). The smriti-write ink engine itself comes from the vellum apk
# repo — see README "Tablet one-time setup".
#
# reMarkable OS updates wipe /etc/systemd/system — rerun this after updating.
set -e
H="${1:-rm2}"
cd "$(dirname "$0")/.."

echo "== smriti-eye CLI + bridge =="
scp device/bin/smriti-eye device/bin/smriti-eye-watch "$H":/home/root/.vellum/bin/
scp device/xovi/smriti-eye-watch.service "$H":/etc/systemd/system/
ssh "$H" 'chmod +x /home/root/.vellum/bin/smriti-eye /home/root/.vellum/bin/smriti-eye-watch
mkdir -p /home/root/.smriti
systemctl daemon-reload
systemctl enable --now smriti-eye-watch'

if ssh "$H" '[ -d /home/root/xovi/exthome/qt-resource-rebuilder ]'; then
    echo "== floating eye (qmldiff patch) =="
    scp device/xovi/smriti-eye.qmd "$H":/home/root/xovi/exthome/qt-resource-rebuilder/
    NEED_RESTART=1
else
    echo "!! xovi/qt-resource-rebuilder not found — floating eye skipped (install xovi via reManager)"
fi

if ssh "$H" '[ -d /home/root/xovi/exthome/appload ]'; then
    echo "== toolbox app (AppLoad) =="
    ssh "$H" 'mkdir -p /home/root/xovi/exthome/appload/smriti'
    scp device/appload/smriti/manifest.json device/appload/smriti/icon.png \
        device/appload/smriti/resources.rcc "$H":/home/root/xovi/exthome/appload/smriti/
    NEED_RESTART=1
else
    echo "!! xovi/appload not found — toolbox app skipped"
fi

if [ -n "$NEED_RESTART" ]; then
    echo "== restarting xochitl (closes the open notebook) =="
    ssh "$H" 'systemctl restart xochitl'
fi
echo "done. Configure the daemon address on the tablet if it isn't the default:"
echo "  ssh $H 'echo SMRITI_DAEMON=<pi-tailscale-ip>:7333 > /home/root/.config/smriti-eye'"
