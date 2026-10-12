"""One persistent shell to the tablet, reused by every lamp and QML call.

Each call used to spawn `ssh` (~0.4 s): a rich reply paid 13+ of them
(device 2026-09-30). Now: `ssh <host> sh` once, scripts written to it, stdin
fed through a uniquely tagged heredoc, output read up to an end marker that
carries the exit code. A timeout or a dead link kills the shell; the next
call opens a fresh one.
"""

from __future__ import annotations

import queue
import subprocess

from deps import ssh_argv
import threading
import uuid


class Channel:
    def __init__(self, argv: list[str]):
        self.argv, self.p, self.q = argv, None, None
        self.lock = threading.Lock()

    def _open(self) -> None:
        self.p = subprocess.Popen(self.argv, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.q = queue.Queue()
        threading.Thread(target=self._pump, args=(self.p, self.q), daemon=True).start()

    @staticmethod
    def _pump(p, q) -> None:
        for chunk in iter(lambda: p.stdout.read1(65536), b""):
            q.put(chunk)
        q.put(None)                                   # shell gone

    def close(self) -> None:
        if self.p is not None:
            try:
                self.p.kill()
            except OSError:
                pass
        self.p = None

    def run(self, script: str, stdin: bytes = b"", timeout: float = 30) -> tuple[int, bytes]:
        """(exit code, stdout) of `script` run with `stdin`. Raises
        TimeoutError / ConnectionError; the channel is reset either way."""
        import time
        with self.lock:
            if self.p is None or self.p.poll() is not None:
                self._open()
            tag = uuid.uuid4().hex
            try:
                body = stdin.decode("utf-8")
            except UnicodeDecodeError:                # binary (png): the tablet
                import base64                         # has openssl, not base64
                body = base64.b64encode(stdin).decode()
                script = f"openssl base64 -d -A | {{ {script}\n}}"
            if body and not body.endswith("\n"):
                body += "\n"
            end = f"__end_{tag}_"
            self.p.stdin.write(f"{{ {script}\n}} <<'{tag}'\n{body}{tag}\n"
                               f"printf '\\n{end}%s\\n' $?\n".encode("utf-8"))
            self.p.stdin.flush()
            buf, deadline = b"", time.time() + timeout
            while True:
                i = buf.find(("\n" + end).encode())
                if i >= 0 and buf.endswith(b"\n"):
                    rc = int(buf[i + 1 + len(end):].strip() or 0)
                    return rc, buf[:i]
                try:
                    chunk = self.q.get(timeout=max(0.0, deadline - time.time()))
                except queue.Empty:
                    self.close()
                    raise TimeoutError(f"tablet call timed out after {timeout} s")
                if chunk is None:
                    self.close()
                    raise ConnectionError("tablet shell closed")
                buf += chunk


_CHANNELS: dict[str, Channel] = {}


def run(host: str, script: str, stdin: bytes = b"", timeout: float = 30) -> tuple[int, bytes]:
    """Run on the tablet over the host's persistent shell (opened on demand)."""
    ch = _CHANNELS.setdefault(host, Channel([*ssh_argv(host, "-o", "ServerAliveInterval=20"), "sh"]))
    return ch.run(script, stdin, timeout)
