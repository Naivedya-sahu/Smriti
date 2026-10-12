"""
auth.py — Smriti's OWN model credentials, per Windows user (Navy 2026-10-11).

"for new installation claude and chatgpt auth should be separate ie user based
one dont copy from hermes to the installer." So nothing here reads Hermes,
~/.codex or ~/.claude: every login / key is made by the user inside Smriti
and lives in ONE file, paths.credentials_file(), encrypted with Windows DPAPI
(CurrentUser scope — another Windows account cannot decrypt it). Off Windows
the file is plain JSON with 0600 permissions.

Providers:
  chatgpt    "Sign in with ChatGPT" device login (subscription), refreshed here
  openai     OpenAI API key (official API)                       — default
  anthropic  Anthropic API key
  local      optional key for an OpenAI-compatible server (never a fallback)
  claude     Claude Code CLI subscription login in paths.claude_home()
             (the CLI keeps its own .credentials.json there; not in this store)

Store shape: {"openai": {"api_key"}, "anthropic": {"api_key"}, "local": {"api_key"},
              "chatgpt": {"access_token", "refresh_token", "last_refresh"}}

CLI (Smriti.exe auth ... / python host/auth.py ...): see cli() / USAGE.
Secrets are never printed: mask() shows ****last4.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import paths  # noqa: E402

ISSUER = "https://auth.openai.com"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"          # public Codex/ChatGPT device client
TOKEN_URL = ISSUER + "/oauth/token"
DEVICE_PAGE = ISSUER + "/codex/device"
LOGIN_WAIT = 15 * 60
REFRESH_SKEW = 5 * 60                                 # refresh when exp is this close
UA = "smriti-auth/0.1"

KEY_PROVIDERS = ("openai", "anthropic", "local")
PROVIDERS = ("openai", "chatgpt", "anthropic", "claude", "local")
ENDPOINTS = {"chatgpt": "chatgpt_url", "openai": "openai_url",
             "anthropic": "anthropic_url", "local": "local_url"}
ENDPOINT_DEFAULTS = {"chatgpt_url": "https://chatgpt.com/backend-api/codex/responses",
                     "openai_url": "https://api.openai.com/v1",
                     "anthropic_url": "https://api.anthropic.com",
                     "local_url": "http://127.0.0.1:1234/v1"}
ENV_KEYS = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
CLAUDE_INSTALL = ("Claude Code CLI not found. Install it, then run `Smriti auth login claude`:\n"
                  "  npm i -g @anthropic-ai/claude-code\n"
                  "  or (PowerShell): irm https://claude.ai/install.ps1 | iex\n"
                  "  or set [claude] claude_bin to its full path.")

_LOCK = threading.RLock()


class AuthError(RuntimeError):
    pass


def mask(secret: str | None) -> str:
    s = (secret or "").strip()
    return "****" + s[-4:] if len(s) >= 8 else ("****" if s else "-")


# ── store: DPAPI-encrypted JSON ─────────────────────────────────────────────

MAGIC_DPAPI = b"SMRITI-DPAPI1\n"
MAGIC_PLAIN = b"SMRITI-PLAIN1\n"


def _dpapi(data: bytes, protect: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = BLOB()
    flags = 0x1                                       # CRYPTPROTECT_UI_FORBIDDEN
    if protect:
        ok = crypt32.CryptProtectData(ctypes.byref(blob_in), "smriti", None, None, None,
                                      flags, ctypes.byref(blob_out))
    else:
        ok = crypt32.CryptUnprotectData(ctypes.byref(blob_in), None, None, None, None,
                                        flags, ctypes.byref(blob_out))
    if not ok:
        raise AuthError(f"DPAPI {'protect' if protect else 'unprotect'} failed "
                        f"(winerror {ctypes.GetLastError()})")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _use_dpapi() -> bool:
    return os.name == "nt"


def load() -> dict:
    with _LOCK:
        try:
            raw = paths.credentials_file().read_bytes()
        except FileNotFoundError:
            return {}
        if raw.startswith(MAGIC_DPAPI):
            raw = _dpapi(raw[len(MAGIC_DPAPI):], protect=False)
        elif raw.startswith(MAGIC_PLAIN):
            raw = raw[len(MAGIC_PLAIN):]
        else:
            raise AuthError(f"unrecognised credentials file {paths.credentials_file()}")
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise AuthError("credentials file is corrupt — log in again") from None
        return data if isinstance(data, dict) else {}


def save(data: dict) -> Path:
    with _LOCK:
        path = paths.credentials_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(data, separators=(",", ":")).encode("utf-8")
        blob = MAGIC_DPAPI + _dpapi(body, protect=True) if _use_dpapi() else MAGIC_PLAIN + body
        tmp = path.with_name(path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(blob)
        os.replace(tmp, path)
        return path


def get(provider: str) -> dict:
    return dict(load().get(provider) or {})


def put(provider: str, values: dict) -> None:
    with _LOCK:
        data = load()
        data[provider] = values
        save(data)


def forget(provider: str) -> bool:
    with _LOCK:
        data = load()
        had = data.pop(provider, None) is not None
        if had:
            save(data)
        return had


def _cfg() -> dict:
    return paths.load_config()


def api_key(provider: str, cfg: dict | None = None) -> str | None:
    """Stored key; the process env only if [brain] allow_env_keys = true."""
    key = get(provider).get("api_key")
    if key:
        return key
    cfg = cfg if cfg is not None else _cfg()
    if (cfg.get("brain") or {}).get("allow_env_keys") and provider in ENV_KEYS:
        return os.environ.get(ENV_KEYS[provider]) or None
    return None


def endpoint(name: str, cfg: dict | None = None) -> str:
    key = ENDPOINTS[name]
    cfg = cfg if cfg is not None else _cfg()
    return str((cfg.get("endpoints") or {}).get(key) or ENDPOINT_DEFAULTS[key])


def check_url(url: str) -> str | None:
    """Raise on a bad URL; return a warning string for plain http off-loopback."""
    u = urllib.parse.urlparse(url.strip())
    if u.scheme not in ("http", "https") or not u.hostname:
        raise AuthError(f"not an http(s) URL: {url!r}")
    if u.scheme == "http" and u.hostname not in ("127.0.0.1", "localhost", "::1"):
        return f"warning: plain http to {u.hostname} — keys/prompts travel unencrypted"
    return None


# ── ChatGPT device login + refresh ──────────────────────────────────────────

def jwt_claim(token: str, name: str, default=None):
    try:
        payload = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if name == "chatgpt_account_id":
            return claims.get("https://api.openai.com/auth", {}).get(name, default)
        return claims.get(name, default)
    except Exception:
        return default


def _post(url: str, *, json_body=None, form=None, timeout=20) -> tuple[int, dict, dict]:
    """(status, json body or {}, headers). Never raises on HTTP status."""
    if json_body is not None:
        data, ctype = json.dumps(json_body).encode(), "application/json"
    else:
        data, ctype = urllib.parse.urlencode(form or {}).encode(), "application/x-www-form-urlencoded"
    req = urllib.request.Request(url, data, {"Content-Type": ctype, "Accept": "application/json",
                                             "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status, headers, raw = r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        status, headers, raw = e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, OSError) as e:
        raise AuthError(f"cannot reach {urllib.parse.urlparse(url).netloc}: {e}"[:200]) from None
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        body = {}
    return status, body if isinstance(body, dict) else {}, headers


def _retry_after(headers: dict) -> str:
    v = {k.lower(): x for k, x in headers.items()}.get("retry-after")
    return f" — retry in {v}s" if v else " — try again later"


def _now_z() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def login_chatgpt(out=print, open_browser=True, sleep=time.sleep, clock=time.monotonic) -> dict:
    """Device flow: show the code, poll, exchange, store. Returns the stored dict."""
    st, dev, h = _post(f"{ISSUER}/api/accounts/deviceauth/usercode", json_body={"client_id": CLIENT_ID})
    if st == 429:
        raise AuthError("OpenAI is rate-limiting logins (429)" + _retry_after(h))
    if st != 200 or not dev.get("user_code") or not dev.get("device_auth_id"):
        raise AuthError(f"device code request failed (HTTP {st})")
    interval = max(3, int(dev.get("interval") or 5))
    out(f"Open {DEVICE_PAGE} and enter the code:\n\n    {dev['user_code']}\n")
    out("Waiting for sign-in (15 min max, Ctrl+C cancels)...")
    if open_browser:
        try:
            import webbrowser
            webbrowser.open(DEVICE_PAGE)
        except Exception:
            pass
    start, code = clock(), None
    while clock() - start < LOGIN_WAIT:
        sleep(interval)
        st, body, h = _post(f"{ISSUER}/api/accounts/deviceauth/token",
                            json_body={"device_auth_id": dev["device_auth_id"],
                                       "user_code": dev["user_code"]})
        if st == 200:
            code = body
            break
        if st == 429:
            sleep(interval)
            continue
        if st not in (403, 404):                        # 403/404 = not finished yet
            raise AuthError(f"device login polling failed (HTTP {st})")
    if code is None:
        raise AuthError("login timed out after 15 minutes")
    if not code.get("authorization_code") or not code.get("code_verifier"):
        raise AuthError("device login answer is missing the authorization code")
    st, tok, h = _post(TOKEN_URL, form={
        "grant_type": "authorization_code", "code": code["authorization_code"],
        "redirect_uri": f"{ISSUER}/deviceauth/callback", "client_id": CLIENT_ID,
        "code_verifier": code["code_verifier"]})
    if st == 429:
        raise AuthError("token exchange rate-limited (429)" + _retry_after(h))
    if st != 200 or not tok.get("access_token"):
        raise AuthError(f"token exchange failed (HTTP {st})")
    rec = {"access_token": tok["access_token"], "refresh_token": tok.get("refresh_token", ""),
           "last_refresh": _now_z()}
    put("chatgpt", rec)
    return rec


def refresh_chatgpt() -> str:
    """Rotate Smriti's own ChatGPT tokens; returns the new access token."""
    with _LOCK:
        rec = get("chatgpt")
        if not rec.get("refresh_token"):
            raise AuthError("no ChatGPT login — run: Smriti auth login chatgpt")
        st, tok, h = _post(TOKEN_URL, form={"grant_type": "refresh_token",
                                            "refresh_token": rec["refresh_token"],
                                            "client_id": CLIENT_ID})
        if st == 429:
            raise AuthError("ChatGPT token refresh rate-limited (429)" + _retry_after(h))
        if st in (400, 401, 403):
            raise AuthError(f"ChatGPT login expired (HTTP {st}) — run: Smriti auth login chatgpt")
        if st != 200 or not tok.get("access_token"):
            raise AuthError(f"ChatGPT token refresh failed (HTTP {st})")
        rec.update(access_token=tok["access_token"], last_refresh=_now_z())
        if tok.get("refresh_token"):
            rec["refresh_token"] = tok["refresh_token"]   # else keep the old one
        put("chatgpt", rec)
        return rec["access_token"]


def chatgpt_access_token(force_refresh: bool = False) -> str:
    """A valid ChatGPT access token from Smriti's store; refreshed when it
    expires within REFRESH_SKEW or when force_refresh (the caller got a 401)."""
    with _LOCK:
        tok = get("chatgpt").get("access_token")
        if not tok:
            raise AuthError("no ChatGPT login — run: Smriti auth login chatgpt")
        if force_refresh or (jwt_claim(tok, "exp", 0) or 0) <= time.time() + REFRESH_SKEW:
            return refresh_chatgpt()
        return tok


# ── Claude Code CLI (subscription) ──────────────────────────────────────────

def claude_bin(cfg: dict | None = None) -> str | None:
    cfg = cfg if cfg is not None else _cfg()
    raw = str((cfg.get("claude") or {}).get("claude_bin") or "claude")
    try:
        first = shlex.split(raw, posix=os.name != "nt")[0].strip('"')
    except (ValueError, IndexError):
        first = raw
    if Path(first).is_file():
        return first
    return shutil.which(first)


def claude_dir(cfg: dict | None = None) -> Path:
    import oracle
    cfg = cfg if cfg is not None else _cfg()
    return oracle.claude_home(cfg.get("claude") or {})


# ── status + cli ────────────────────────────────────────────────────────────

def status_rows(cfg: dict | None = None) -> list[tuple[str, str, str, str]]:
    cfg = cfg if cfg is not None else _cfg()
    data = load()
    rows = []
    for p in ("openai", "anthropic", "local"):
        key = (data.get(p) or {}).get("api_key")
        env = None if key else api_key(p, cfg)
        detail = mask(key) if key else (f"env {ENV_KEYS[p]} {mask(env)}" if env else "-")
        rows.append((p, "yes" if key or env else "no", detail, endpoint(p, cfg)))
    c = data.get("chatgpt") or {}
    exp = jwt_claim(c.get("access_token", ""), "exp", 0) or 0
    detail = (time.strftime("token exp %Y-%m-%d %H:%M", time.localtime(exp)) if exp else
              ("token (no exp)" if c.get("access_token") else "-"))
    rows.append(("chatgpt", "yes" if c.get("refresh_token") else "no", detail, endpoint("chatgpt", cfg)))
    home = claude_dir(cfg)
    has = (home / ".credentials.json").exists()
    rows.append(("claude", "yes" if has else "no",
                 ("cli " + ("found" if claude_bin(cfg) else "MISSING")), str(home)))
    return rows


USAGE = """usage: Smriti auth <command>
  status
  login chatgpt | claude
  key set openai|anthropic|local [KEY]     (prompts when KEY is omitted)
  logout chatgpt|openai|anthropic|local|claude [--yes]
  endpoint set chatgpt|openai|anthropic|local URL
  endpoint show
  provider set openai|chatgpt|anthropic|claude|local [MODEL]
  test [provider]"""


def cli(argv: list[str], out=print) -> int:
    try:
        return _cli(list(argv), out)
    except AuthError as e:
        out(f"error: {e}")
        return 1
    except KeyboardInterrupt:
        out("cancelled")
        return 130


def _cli(a: list[str], out) -> int:
    cmd = a[0] if a else "status"
    if cmd in ("-h", "--help", "help"):
        out(USAGE)
        return 0
    if cmd == "status":
        cfg = _cfg()
        b = cfg.get("brain") or {}
        out(f"active: provider={b.get('provider', '?')} model={b.get('model', '?')}  "
            f"store={paths.credentials_file()}")
        out(f"{'provider':<10} {'set':<4} {'detail':<28} endpoint")
        for p, ok, det, ep in status_rows(cfg):
            out(f"{p:<10} {ok:<4} {det:<28} {ep}")
        return 0
    if cmd == "login" and len(a) == 2 and a[1] == "chatgpt":
        login_chatgpt(out=out)
        out("ChatGPT login saved for this Windows user.")
        return 0
    if cmd == "login" and len(a) == 2 and a[1] == "claude":
        return _login_claude(out)
    if cmd == "key" and len(a) in (3, 4) and a[1] == "set" and a[2] in KEY_PROVIDERS:
        key = a[3] if len(a) == 4 else getpass.getpass(f"{a[2]} API key (hidden): ")
        key = key.strip()
        if not key:
            raise AuthError("empty key — nothing saved")
        put(a[2], {"api_key": key})
        out(f"{a[2]} key saved ({mask(key)})")
        return 0
    if cmd == "logout" and len(a) >= 2 and a[1] in PROVIDERS:
        if a[1] == "claude":
            cred = claude_dir() / ".credentials.json"
            if "--yes" not in a:
                out(f"would delete {cred} — repeat with --yes")
                return 2
            if cred.exists():
                cred.unlink()
            out("Claude login removed from Smriti's profile.")
            return 0
        out(f"{a[1]}: " + ("removed" if forget(a[1]) else "nothing stored"))
        return 0
    if cmd == "endpoint" and len(a) == 2 and a[1] == "show":
        cfg = _cfg()
        for n in ENDPOINTS:
            out(f"{n:<10} {endpoint(n, cfg)}")
        return 0
    if cmd == "endpoint" and len(a) == 4 and a[1] == "set" and a[2] in ENDPOINTS:
        url = a[3].strip().rstrip("/")
        warn = check_url(url)
        paths.set_user("endpoints", ENDPOINTS[a[2]], url)
        if warn:
            out(warn)
        out(f"{a[2]} endpoint = {url}")
        return 0
    if cmd == "provider" and len(a) in (3, 4) and a[1] == "set" and a[2] in PROVIDERS:
        import brain
        model = a[3] if len(a) == 4 else brain.default_model(brain.load_config(), a[2])
        if not model:
            raise AuthError(f"name a model: Smriti auth provider set {a[2]} <model>")
        paths.set_user("brain", "provider", a[2])
        paths.set_user("brain", "model", model)
        out(f"provider = {a[2]}, model = {model}")
        if not paths.overlay_active():
            out("note: dev checkout without SMRITI_HOME — repo config.toml still wins")
        return 0
    if cmd == "test" and len(a) <= 2:
        return _test(a[1] if len(a) == 2 else None, out)
    out(USAGE)
    return 2


def _login_claude(out) -> int:
    exe = claude_bin()
    if not exe:
        out(CLAUDE_INSTALL)
        return 1
    home = claude_dir()
    home.mkdir(parents=True, exist_ok=True)
    out(f"Claude Code login into Smriti's own profile: {home}")
    rc = subprocess.call([exe, "auth", "login"], env=dict(os.environ, CLAUDE_CONFIG_DIR=str(home)))
    out("Claude login " + ("saved." if rc == 0 else f"failed (rc={rc})."))
    return rc


def _test(provider: str | None, out) -> int:
    import brain
    cfg = brain.load_config()
    provider = provider or cfg["provider"]
    if provider not in PROVIDERS:
        raise AuthError(f"unknown provider {provider}")
    spec = brain.default_spec(dict(cfg, provider=provider,
                                   model=cfg["model"] if provider == cfg["provider"]
                                   else brain.default_model(cfg, provider)))
    t0 = time.time()
    try:
        text = brain.chat([{"role": "system", "content": "Reply with exactly: OK"},
                           {"role": "user", "content": "reply OK"}], model=spec, cfg=cfg)
    except Exception as e:
        out(f"{provider}: FAILED after {time.time() - t0:.1f}s — {str(e)[:160]}")
        return 1
    out(f"{provider} ({spec}): {text.strip()[:40]!r} in {time.time() - t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(cli(sys.argv[1:]))
