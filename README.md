# Smriti

reMarkable 2 handwriting-AI notebook. **v0.1.1** — text-to-handwriting engine +
git-based update pipeline. No AI, no pen input yet (v0.1.2+).

Text → styled glyph raster (Pillow) → Zhang-Suen skeleton → stroke trace →
pen strokes on e-ink via [lamp](https://github.com/rmkit-dev/rmkit) / rm2fb.
Technique from [riddle](https://github.com/MaximeRivest/riddle), reimplemented
in Python for RM2 (armv7).

## Layout

```
host/ink.py        engine: text → strokes; compiles device stroke-fonts (.sf)
host/write.py      dev writer: renders host-side, pipes to lamp over ssh
device/smriti-write  on-device writer (busybox sh + awk, zero deps)
device/replay.awk    stroke-font replayer
device/bin/lamp      pen-event injector (armv7, salvaged Elxnk lamp) —
                     strokes become real xochitl notebook ink, no rm2fb
device/fonts/*.sf    compiled stroke-fonts (build artifacts, committed)
styles.toml        writing styles — edit here, release, vellum upgrade
VELBUILD           vellum/apk package recipe
deploy/release.sh  fonts → commit → signed apk → gh-pages apk repo
```

## Dev loop (host)

```sh
uv sync
uv run python host/ink.py selfcheck
uv run python host/ink.py preview "hello monke" --style cursive -o p.png
uv run python host/write.py "hello monke" --clear        # needs RM2 + lamp
```

## Release loop

```sh
deploy/release.sh "new style: xyz"     # Docker Desktop must be running
```

## Device one-time setup (RM2, ssh root@10.11.99.1)

```sh
# 1. trust the signing key (from gh-pages root)
wget -P /etc/apk/keys "https://naivedya-sahu.github.io/Smriti/naivedya.sahu2@gmail.com-6a4bae6f.rsa.pub"
# 2. add the repo
echo "https://naivedya-sahu.github.io/Smriti/armv7" >> /etc/apk/repositories
# 3. install
vellum update && vellum install smriti
```

Then every release lands with `vellum update && vellum upgrade`.

Open a notebook page first — strokes are injected as stylus events and
xochitl draws them as real, saved ink. Usage:

```sh
smriti-write "hello monke"
smriti-write -s print -y 400 "second thought"
smriti-write -h        # lists installed styles
```

## Fonts

Dancing Script + Patrick Hand (SIL OFL, see `fonts/`). Styles are defined in
`styles.toml`; a style = font + size + weight + slant + pressure.
