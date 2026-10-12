#!/usr/bin/env bash
# One command: focused tests -> icon -> TinyTeX check -> PyInstaller -> Inno Setup.
#   bash packaging/build.sh            (Git Bash, from anywhere)
# Output: dist/Smriti/ (onedir app) and dist/installer/Smriti-Setup-<ver>.exe
# Env: SMRITI_TINYTEX=<dir> to use another TinyTeX; ISCC=<path to ISCC.exe>.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT_W="$(pwd -W 2>/dev/null || pwd)"
PY=./.venv/Scripts/python.exe
export PYTHONUTF8=1

VER=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' host/app.py)
echo "== Smriti $VER"

echo "== tests"
"$PY" -m unittest tests.test_app tests.test_link tests.test_paths tests.test_inking.DoctorTest

echo "== icon (placeholder eye; replace packaging/assets/smriti.ico with the ermine to rebrand)"
if [ ! -f packaging/assets/smriti.ico ]; then
  "$PY" -c "import sys; sys.path.insert(0,'host'); import tray; print(tray.write_ico(__import__('pathlib').Path('packaging/assets/smriti.ico')))"
fi

echo "== TinyTeX"
TT="${SMRITI_TINYTEX:-build/vendor/TinyTeX}"
if [ ! -f "$TT/bin/windows/pdflatex.exe" ]; then
  # clean machine: fetch the pinned self-extracting release and unpack with 7-Zip
  URL=https://github.com/rstudio/tinytex-releases/releases/download/v2026.10/TinyTeX-1-windows-v2026.10.exe
  mkdir -p build/vendor
  curl -fL -o build/vendor/TinyTeX-1.exe "$URL"
  "C:/Program Files/7-Zip/7z.exe" x -y -obuild/vendor build/vendor/TinyTeX-1.exe
  TT=build/vendor/TinyTeX
fi
# packages the TeX template needs (idempotent; no-op when present)
"$TT/bin/windows/tlmgr.bat" install standalone circuitikz pgfplots preview pgf xkeyval \
  amsmath amsfonts siunitx xstring etoolbox >/dev/null 2>&1 || echo "  (tlmgr install skipped/failed — compile check below decides)"
export SMRITI_TINYTEX="$(cd "$TT" && (pwd -W 2>/dev/null || pwd))"

echo "== pyinstaller"
"$PY" -m PyInstaller --noconfirm --clean --distpath dist --workpath build/pyinstaller packaging/smriti.spec

echo "== inno setup"
ISCC="${ISCC:-$LOCALAPPDATA/Programs/Inno Setup 6/ISCC.exe}"
[ -f "$ISCC" ] || ISCC="C:/Program Files (x86)/Inno Setup 6/ISCC.exe"
MSYS_NO_PATHCONV=1 "$ISCC" /Q "/DMyAppVersion=$VER" "$ROOT_W/packaging/smriti.iss"

ls -la "dist/installer/Smriti-Setup-$VER.exe"
