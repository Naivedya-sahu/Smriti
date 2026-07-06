# Smriti

reMarkable 2 handwriting notebook assistant. **v0.1.1** — text-to-handwriting
engine + git-based update pipeline. No AI, no pen input yet (v0.1.2+).

Text → styled glyph raster (Pillow) → Zhang-Suen skeleton → stroke trace →
**synthetic stylus events** injected on the device. xochitl draws them, so the
result is real, saved, erasable notebook ink — no rm2fb, no display hacks.
Technique from [riddle](https://github.com/MaximeRivest/riddle), reimplemented
in Python for RM2 (armv7).

---

## Daily use (from the PC)

Everything runs over ssh; you never type on the tablet. **Open a notebook
page on the RM2 first** — ink lands on whatever page is open.

```sh
# on-device writer (uses styles installed by vellum):
ssh rm2 '/home/root/.vellum/bin/smriti-write "hello monke"'
ssh rm2 '/home/root/.vellum/bin/smriti-write -s print -y 400 "print style, lower on page"'
ssh rm2 '/home/root/.vellum/bin/smriti-write -h'          # list styles + flags

# host-side writer (better cursive joins, uses repo styles directly):
uv run python host/write.py "hello monke" --style cursive
```

`smriti-write` flags: `-s` style · `-x`/`-y` start position (px, 1404×1872
canvas) · `-w` wrap width · `-p` waypoint step.

> Full path needed because vellum's PATH line lives in `.bashrc`, which
> non-interactive ssh skips.

## One-time install (fresh device / after OS update)

Assumes ssh key auth to the device already works (`ssh rm2`).

```sh
# 1. install vellum (package manager, lives entirely in /home/root/.vellum)
ssh rm2
wget --no-check-certificate -O bootstrap.sh https://github.com/vellum-dev/vellum-cli/releases/latest/download/bootstrap.sh
echo "18a4b0123160a1b547fa9f396005ce8c9caf2330bf3ff6fa39bb2eb27891cca8  bootstrap.sh" | sha256sum -c && bash bootstrap.sh
exec bash --login

# 2. trust the Smriti repo (still on the device)
cd /home/root/.vellum/etc/apk
wget -q --no-check-certificate -O "keys/naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub" \
  "https://naivedya-sahu.github.io/Smriti/naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub"
echo "https://naivedya-sahu.github.io/Smriti" >> repositories

# 3. install
vellum update && vellum add smriti

# 4. open a notebook page, then:
/home/root/.vellum/bin/smriti-write "hello monke"
```

After an OS update: rerun step 1's `bootstrap.sh`? No — run `vellum reenable`
first; only re-bootstrap if vellum itself is gone.

## Updating (the whole point)

Every release lands with:

```sh
ssh rm2 '/home/root/.vellum/bin/vellum update && /home/root/.vellum/bin/vellum upgrade'
```

No scp, no scripts. GitHub Pages CDN caches the index up to 10 min — if
`upgrade` says nothing new right after a release, wait and retry.

## Changing styles / releasing (dev machine)

Styles live in [styles.toml](styles.toml): font + size + weight + slant +
pressure. Fonts: Dancing Script + Patrick Hand (SIL OFL, `fonts/`).

```sh
uv sync                                   # once
uv run python host/ink.py selfcheck
uv run python host/ink.py preview "test" --style cursive -o p.png   # no device needed

deploy/release.sh "message"               # Docker Desktop must be running
```

release.sh: regenerates stroke-fonts → bumps pkgrel → commits+pushes → builds
signed apk (abuild in Alpine container) → publishes to the gh-pages apk repo.
Then upgrade on device (command above).

## Layout

```
host/ink.py        engine: text → strokes; compiles device stroke-fonts (.sf)
host/write.py      host-side writer (renders full lines, pipes to lamp via ssh)
device/smriti-write  on-device writer (busybox sh + awk, zero deps)
device/replay.awk    stroke-font replayer (.sf → lamp commands)
device/bin/lamp      pen-event injector (armv7; salvaged Elxnk lamp, patched:
                     distance-scaled interpolation, ~10x faster than stock)
device/fonts/*.sf    compiled stroke-fonts (build artifacts, committed)
styles.toml        writing styles
VELBUILD           vellum/apk package recipe
deploy/release.sh  release pipeline
```

lamp source: `Archive\RM_Projects\rm2elxnk\archive\src\lamp` (+ this repo's
speed patch, see STATE.md). Rebuild: debian container, g++-arm-linux-gnueabihf
+ okp, `make compile`.

## Troubleshooting

- **No ink appears**: is a notebook page open (not the file browser)? Is the
  package installed (`vellum info smriti`)?
- **`smriti-write: not found`**: use the full path `/home/root/.vellum/bin/smriti-write`.
- **Upgrade sees nothing new**: CDN cache — wait up to 10 min. Verify the
  published index: the repo's gh-pages branch, `armv7/APKINDEX.tar.gz`.
- **Writing looks chunky**: raise waypoint density: `smriti-write -p 2 "..."`.
