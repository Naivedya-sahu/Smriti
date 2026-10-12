# Smriti

Smriti is a reMarkable 2 study companion. Write on an rM2 notebook; Smriti reads the visible page context, produces a structured plan, and renders a safe reply as real notebook ink.

## 0.5.0 scope

- Windows standalone app: Start Menu/Search, system tray, console diagnostics, isolated Python and TinyTeX.
- Tablet app: Vellum/reManager package recipe, AppLoad master switch and xovi floating eye.
- Wireless transport: Tailnet → USB → saved Wi-Fi/hotspot, plus bounded local rM2 discovery.
- Providers: fresh per-user ChatGPT sign-in, OpenAI API, Anthropic API, optional Claude CLI and explicit local OpenAI-compatible endpoint.
- Deterministic middleware: typed render plans, AST-safe plots, deterministic TeX/circuit/diagram rendering, model output validation.
- Eye states: closed idle, open watching, pulsing dots thinking, pen stroke drawing.

## Install paths

| Component | User path |
|---|---|
| Windows | Build `dist/installer/Smriti-Setup-<version>.exe`; distribute via GitHub Release |
| rM2 | Build signed Vellum APK with `deploy/release.sh`; distribute through the GitHub Pages package repo consumed by reManager/Vellum |

See [docs/RELEASE-PACKAGING.md](docs/RELEASE-PACKAGING.md) before publishing. No installer binary, APK, credential, log, notebook capture or signing key belongs in this Git repository.

## Documentation

- [Feature set](docs/FEATURESET.md)
- [Page-context awareness](docs/PAGE-CONTEXT.md)
- [Roadmap](docs/ROADMAP.md)
- [Development changelog](docs/DEVELOPMENT-CHANGELOG.md)
- [Historical development roadmap](docs/DEVELOPMENT-ROADMAP-HISTORY.md)
- [Windows installation](docs/install-windows.md)
- [Wireless operation](docs/operate-wireless.md)
- [Authentication boundary](docs/AUTHENTICATION.md)
- [Requirements and test boundary](docs/REQUIREMENTS-AND-TESTS.md)

## Development

```text
uv sync
PYTHONUTF8=1 uv run python -m unittest
```

Copy `config.example.toml` to a per-user location or use the installed app’s user overlay. Do not commit a real `config.toml`.

## Security boundary

New installs authenticate as their own user. Smriti never copies authentication from Hermes, browser profiles, Claude Code or another machine. The tablet holds only bridge configuration, never provider credentials.
