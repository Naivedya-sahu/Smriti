# Release packaging

## Windows

`bash packaging/build.sh` runs focused checks, builds the isolated PyInstaller application and creates `dist/installer/Smriti-Setup-<version>.exe` with Inno Setup. Publish the EXE plus SHA-256 checksum as a GitHub Release asset. Do not commit it.

## rM2 / reManager

`deploy/release.sh` builds and signs an armv7 Vellum APK using Docker/abuild, builds `APKINDEX.tar.gz`, and publishes public package assets to `gh-pages`. reManager/Vellum then add/upgrade the `smriti` package.

The recipe intentionally expects `device/bin/lamp`, an ARM pen-injection executable. It is **not tracked** because its upstream license and reproducible source/provenance are not yet recorded. Supply a reviewed, redistributable replacement before an RM2 package build; otherwise `VELBUILD` fails rather than creating a partial APK.

Provenance notes (2026-10-12): the binary's command grammar (`pen down`, `pen up`) matches rmkit's `lamp` tool (MIT); it adds a custom `fastpen move` command and was built with `GCC (Debian 12.2.0-14)`, i.e. a locally patched rmkit lamp. The patched source is not in this repository. Plan: vendor the patched lamp source with rmkit's MIT notice and build it reproducibly before the first RM2 package release.

Private abuild keys never enter this repository.

## Important 0.5.0 gate

Before publishing, change the package version in `VELBUILD` from its historical value to the final release version, build the APK from the final source commit, and record its checksum. A recipe is not a shipped installer.