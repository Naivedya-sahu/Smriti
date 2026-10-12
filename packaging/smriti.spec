# PyInstaller spec — Smriti (onedir, two exes over one bundle).
#   smriti.exe      console: CLI switches (smriti --help)
#   SmritiTray.exe  windowed: system tray (Start Menu shortcut target)
# Build: packaging/build.sh   (or: pyinstaller --noconfirm packaging/smriti.spec)
# ruff: noqa
import os
from pathlib import Path

ROOT = Path(SPECPATH).resolve().parent          # repo root
HOST = ROOT / "host"
TINYTEX = Path(os.environ.get("SMRITI_TINYTEX", ROOT / "build" / "vendor" / "TinyTeX"))
ICON = ROOT / "packaging" / "assets" / "smriti.ico"

# every host module (lazy imports included): auth/brain/oracle/monke/tray/...
SKIP = {"app", "setup_wizard", "demo_diode"}
hidden = sorted(p.stem for p in HOST.glob("*.py") if p.stem not in SKIP)
hidden += ["pystray._win32", "PIL._tkinter_finder"]

datas = [
    (str(ROOT / "config.toml"), "."),
    (str(ROOT / "styles.toml"), "."),
    (str(ROOT / "fonts"), "fonts"),
    (str(ROOT / "skill" / "smriti"), "skill/smriti"),
    (str(ICON), "assets"),
]

a = Analysis(
    [str(HOST / "app.py")],
    pathex=[str(HOST)],
    binaries=[],
    datas=datas,
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    excludes=["torch", "transformers", "sentencepiece", "tkinter", "matplotlib",
              "pytest", "IPython", "scipy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)

common = dict(exclude_binaries=True, debug=False, strip=False, upx=False,
              icon=str(ICON), bootloader_ignore_signals=False)
cli = EXE(pyz, a.scripts, [], name="smriti", console=True, **common)
tray = EXE(pyz, a.scripts, [], name="SmritiTray", console=False, **common)

if not (TINYTEX / "bin" / "windows" / "pdflatex.exe").is_file():
    raise SystemExit(f"TinyTeX not found at {TINYTEX} (see packaging/build.sh)")
tex = Tree(str(TINYTEX), prefix="tinytex",
           excludes=["backups", "*.log", "tlmgr-commands.log", "texmf-var/web2c/tlmgr*.log"])

coll = COLLECT(cli, tray, a.binaries, a.datas, tex,
               strip=False, upx=False, name="Smriti")
