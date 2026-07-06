#!/bin/bash
# release.sh — one-command release: fonts → commit → signed apk → gh-pages repo.
# Run from Git Bash (needs Docker Desktop running + gh auth).
#
#   deploy/release.sh "commit message"
#
# Flow: regenerate stroke fonts from styles.toml, commit+push, build a signed
# noarch .apk in an Alpine container (VELBUILD is APKBUILD-compatible; vellum
# extras are plain shell vars), index it, publish to the gh-pages branch.
# Device then just: vellum update && vellum upgrade.
#
# Signing keys live in ~/.abuild (generated on first run, NEVER in repo).

set -euo pipefail
cd "$(dirname "$0")/.."
MSG="${1:-release}"

# 1 — regenerate stroke fonts
for s in $(PYTHONUTF8=1 uv run python -c \
    "import tomllib; print(' '.join(tomllib.load(open('styles.toml','rb'))['style']))"); do
    PYTHONUTF8=1 uv run python host/ink.py compile --style "$s" -o "device/fonts/$s.sf"
done
PYTHONUTF8=1 uv run python host/ink.py selfcheck

# 2 — bump pkgrel (device only upgrades on version change), commit + push
if ! git diff --quiet -- . ':!VELBUILD' || ! git diff --cached --quiet; then
    REL=$(sed -n 's/^pkgrel=//p' VELBUILD)
    sed -i "s/^pkgrel=$REL/pkgrel=$((REL + 1))/" VELBUILD
fi
git add -A
git diff --cached --quiet || git commit -m "$MSG"
git push origin main
COMMIT=$(git rev-parse HEAD)

# 3 — build dir with pinned VELBUILD → APKBUILD
rm -rf dist build && mkdir -p build dist
sed "s/COMMIT_PLACEHOLDER/$COMMIT/" VELBUILD > build/APKBUILD

# 4 — abuild in Alpine container (keys persisted to ~/.abuild on host)
mkdir -p "$HOME/.abuild"
MSYS_NO_PATHCONV=1 docker run --rm \
    -v "$(pwd -W 2>/dev/null || pwd):/work" \
    -v "$(cd "$HOME" && pwd -W 2>/dev/null || echo "$HOME")/.abuild:/root/.abuild" \
    alpine:3.22 sh -ec '
        apk add -q alpine-sdk
        grep -q "^PACKAGER=" /root/.abuild/abuild.conf 2>/dev/null || \
            echo "PACKAGER=\"Naivedya Sahu <naivedya.sahu2@gmail.com>\"" >> /root/.abuild/abuild.conf
        ls /root/.abuild/*.rsa >/dev/null 2>&1 || abuild-keygen -a -n
        cp /root/.abuild/*.rsa.pub /etc/apk/keys/
        grep -q PACKAGER_PRIVKEY /root/.abuild/abuild.conf || \
            echo "PACKAGER_PRIVKEY=$(ls /root/.abuild/*.rsa | head -1)" >> /root/.abuild/abuild.conf
        cd /work/build
        abuild -F checksum
        # CARCH forced: package is prebuilt-binary + scripts, nothing compiles
        CARCH=armv7 REPODEST=/tmp/out abuild -F -r
        mkdir -p /work/dist/armv7
        find /tmp/out -name "*.apk" -exec cp {} /work/dist/armv7/ \;
        cd /work/dist/armv7
        apk index -o APKINDEX.unsigned.tar.gz *.apk
        cp APKINDEX.unsigned.tar.gz APKINDEX.tar.gz
        abuild-sign APKINDEX.tar.gz
        rm APKINDEX.unsigned.tar.gz
        cp /root/.abuild/*.rsa.pub /work/dist/
    '

# 5 — publish dist/ to gh-pages (repo URL for the device = the Pages URL;
# apk appends /armv7 itself). gh-pages is an orphan branch: apk repo + pubkey
# + .nojekyll only (Jekyll chokes on the tree and stalls Pages builds).
git fetch -q origin gh-pages
git worktree remove --force /tmp/smriti-pages 2>/dev/null || true
git worktree add -f -B gh-pages /tmp/smriti-pages origin/gh-pages
cp -r dist/* /tmp/smriti-pages/
touch /tmp/smriti-pages/.nojekyll
cd /tmp/smriti-pages
git add -A
git diff --cached --quiet || git commit -m "apk: $MSG ($COMMIT)"
git push -u origin gh-pages
cd - >/dev/null
git worktree remove --force /tmp/smriti-pages

echo
echo "published. device one-time setup (see README), then:"
echo "  vellum update && vellum upgrade"
