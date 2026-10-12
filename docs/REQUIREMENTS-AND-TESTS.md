# Requirements and test boundary

## Runtime

- Windows 10/11 with OpenSSH Client; installer bundles Python and TinyTeX.
- rM2 with Vellum, xovi, AppLoad, qt-resource-rebuilder and SSH access for setup.
- One explicit provider route: direct ChatGPT, API key, Claude CLI or local OpenAI-compatible endpoint.

## Verified in current development session

- Floating-eye/AppLoad source contracts: 18 tests, OK.
- AppLoad resource built twice with byte-identical output.
- Eye QMD and AppLoad RCC hashes matched the files loaded on the current rM2.
- xochitl active, xovi loaded, eye bridge armed and stream active.

## Required but still local-only / not complete

- Clean-PC installer acceptance test.
- Signed 0.5.0 RM2 APK build and reManager/Vellum install.
- Laptop+rM2 physical reboot recovery test.
- Authenticated framebuffer visual evidence (plain URL returned HTTP 401).
- Mac mini local-model harness and textbook-first retrieval pilot.
- TinyTeX release provenance/signature policy check.

Do not turn these gaps into release claims.