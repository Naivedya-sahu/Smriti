# Authentication boundary

Every Smriti installation has its own user authentication. ChatGPT device login, OpenAI/Anthropic API keys, Claude CLI login and local-endpoint tokens belong only to the installing Windows user’s Smriti state.

- Never read or copy Hermes, browser, Claude Code or another computer’s sign-in state.
- Never put provider credentials on rM2, a Pi, GitHub, `gh-pages`, a release asset or a log.
- Smriti owns a separate tablet SSH key under its per-user state; never reuse a private key from another app.
- On Windows, Smriti credential material is protected with the signed-in user’s DPAPI.

For a broken tablet key, run `smriti pair` and enter the tablet password, or correct the saved endpoint with `smriti config edit`. Do not bypass host verification by disabling SSH host-key checks.