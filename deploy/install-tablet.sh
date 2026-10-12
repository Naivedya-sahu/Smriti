#!/bin/sh
# install-tablet.sh — install/upgrade the smriti apk on the RM2 over ssh.
#
#     deploy/install-tablet.sh [ssh-host]     # default host: rm2
#
# Same result as reManager (add the repo once, then install "smriti"): the
# apk carries the ink engine, eye bridge + service, CLIs, floating eye and
# the Smriti app; its post-install hook (smriti-setup) wires the rootfs unit
# and power gate. Needs vellum on the tablet (reManager installs it). After a
# reMarkable OS update: `vellum reenable` (or reManager's reenable) — no rerun.
set -e
H="${1:-rm2}"
REPO=https://naivedya-sahu.github.io/Smriti
KEY=naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub

ssh "$H" "set -e
V=/home/root/.vellum
[ -x \$V/bin/vellum ] || { echo 'vellum missing — install it with reManager first'; exit 1; }
cd \$V/etc/apk
[ -f keys/$KEY ] || wget -q --no-check-certificate -O keys/$KEY $REPO/$KEY
grep -qx '$REPO' repositories || echo '$REPO' >> repositories
\$V/bin/vellum update
if \$V/bin/vellum info smriti >/dev/null 2>&1; then \$V/bin/vellum upgrade smriti; else \$V/bin/vellum add smriti; fi"
echo "done. Point the tablet at the daemon (laptop over USB = 10.11.99.2):"
echo "  ssh $H 'echo SMRITI_DAEMON=10.11.99.2:7333 > /home/root/.config/smriti-eye'"
