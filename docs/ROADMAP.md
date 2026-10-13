# Roadmap

## Release gates before public 0.5.0

1. Align tablet package version in `VELBUILD` with `0.5.0`.
2. Build a signed armv7 Vellum APK from final committed source.
3. Rebuild Windows installer from final committed source; publish binaries only as GitHub Release assets.
4. Validate clean-PC first install, fresh user ChatGPT sign-in, pairing, tray restart and diagnostics.
5. Validate clean/tablet update through reManager/Vellum.
6. Run physical laptop + rM2 reboot test: idle eye → watching → thinking → drawing.
7. Verify authenticated framebuffer capture and preserve redacted visual evidence.

## Next product work

| Priority | Work | Outcome |
|---|---|---|
| High | Textbook-first source mode | page-bound indexed prescribed sources, citations and refusal on weak evidence |
| High | RM2 live validation | prove current SPP, placement and safety paths on device |
| Medium | Ermine mascot | replace eye art in tray/installer/tablet UI without changing state semantics |
| Medium | Local Mac mini pilot | serial specialist harness: controller + visual reader first; no automatic fallback |
| Later | Per-kind ink layers | graph/drawing/prose separation within rM2 layer-limit budget |

## Non-goals until explicitly reopened

- Exposing LM Studio publicly or binding it to LAN by default.
- Giving model output unrestricted tool authority.
- Copying auth into tablet, Pi or another user’s installation.

See `DEVELOPMENT-ROADMAP-HISTORY.md` for historical exploration and `DEVELOPMENT-CHANGELOG.md` for dated development record.