"""Admin alerts (also used by the systemd OnFailure units).

Every alert goes FIRST through an optional host gate command (`MIKIWAME_NOTIFY_GATE`, empty
by default): body on stdin as a Telegram-HTML subset (<b> <i> <s> <code> <pre> <a href>), at
most 16384 bytes. rc 0 or 10 = delivered by the host: we are done and never post ourselves.
rc 1/2, a launch failure or a timeout (60 s) fall back to the path below, unchanged.

The fallback is a private kChat channel through an incoming webhook. The webhook lives in a
0600 file, read on every send (rotating it needs no restart) and written by
`save_kchat_webhook`. With the file in place there is no Telegram fallback: a send that fails
twice is logged locally. Until it exists, alerts go to the admin Telegram chat.

sendMessage does not conflict with the long-polling loop; only getUpdates would.
"""

from __future__ import annotations

import datetime as dt
import html
import logging
import os
import re
import shlex
import subprocess
import time
from pathlib import Path

import httpx

from mikiwame import PROJECT_DIR

log = logging.getLogger(__name__)

_last_sent: dict[str, float] = {}

# Telegram rejects texts over 4096 characters; leave room for the label.
MAX_CHARS = 4000

KCHAT_WEBHOOK_RE = re.compile(r"^https://[a-z0-9-]+\.kchat\.infomaniak\.com/hooks/[A-Za-z0-9-]+$")
KCHAT_ATTEMPTS = 2
UNDELIVERED_LOG = PROJECT_DIR / "var" / "alerts-undelivered.log"

# Host gate (08-11-2026). MIKIWAME_NOTIFY_GATE overrides the command; empty or "off" skips it.
NOTIFY_GATE_DEFAULT = ""  # no host gate by default; set MIKIWAME_NOTIFY_GATE to use one
GATE_TIMEOUT_S = 60.0
GATE_MAX_BYTES = 16384
GATE_DELIVERED = {0: "gate", 10: "gate-fallback"}  # rc -> how the host delivered it


def split_text(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Cut `text` into chunks of at most `limit` characters, on line boundaries when possible."""
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        while len(line) > limit:  # a single overlong line is cut hard
            if current:
                chunks.append(current)
                current = ""
            chunks.append(line[:limit])
            line = line[limit:]
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current or not chunks:
        chunks.append(current)
    return chunks


def notify_gate() -> list[str] | None:
    """The host gate command, or None when it is switched off."""
    raw = os.environ.get("MIKIWAME_NOTIFY_GATE", NOTIFY_GATE_DEFAULT).strip()
    if not raw or raw.lower() == "off":
        return None
    return shlex.split(raw)


def kchat_webhook_file() -> Path:
    return Path(
        os.environ.get("MIKIWAME_KCHAT_WEBHOOK_FILE")
        or Path.home() / ".config" / "mikiwame" / "kchat-webhook"
    )


def kchat_webhook() -> str | None:
    """The installed webhook, or None when there is none (or the file holds something else)."""
    try:
        url = kchat_webhook_file().read_text().strip()
    except FileNotFoundError:
        return None
    if not KCHAT_WEBHOOK_RE.match(url):
        log.warning("kChat webhook file holds no valid webhook; ignored")
        return None
    return url


def save_kchat_webhook(url: str) -> Path:
    """Validate and store the webhook (0600, atomic). ValueError on anything else."""
    url = url.strip()
    if not KCHAT_WEBHOOK_RE.match(url):
        raise ValueError("not a kChat incoming webhook URL")
    path = kchat_webhook_file()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(url + "\n")
    os.replace(tmp, path)
    return path


_MD_SPECIAL = re.compile(r"([\\*_~`|\[\]])")
_URL = re.compile(r"(https?://\S+)")


def to_markdown(text: str) -> str:
    """Plain admin text as kChat Markdown that shows as written: specials escaped (not inside
    URLs, so they still link) and leading spaces as no-break spaces (four spaces would turn a
    digest line into a code block)."""
    lines = []
    for line in text.split("\n"):
        body = line.lstrip(" ")
        indent = " " * (len(line) - len(body))
        parts = _URL.split(body)
        body = "".join(p if i % 2 else _MD_SPECIAL.sub(r"\\\1", p) for i, p in enumerate(parts))
        if body[:1] in ("#", ">"):
            body = "\\" + body
        lines.append(indent + body)
    return "\n".join(lines)


def _log_undelivered(label: str, text: str) -> None:
    """Admin texts carry aggregates and case ids, never third-party data: safe to keep here."""
    try:
        UNDELIVERED_LOG.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(UNDELIVERED_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as f:
            f.write(f"--- {dt.datetime.now().isoformat(timespec='seconds')} {label}\n{text}\n")
    except OSError as e:
        log.warning("could not log an undelivered alert: %s", type(e).__name__)


def to_html(text: str) -> str:
    """Plain admin text as the gate's Telegram-HTML subset: everything escaped, nothing marked up
    (a '<' in a digest line must show as written)."""
    return html.escape(text, quote=False)


def _run_gate(cmd: list[str], body: str) -> int | None:
    """rc of the gate, or None when it could not even be asked (ssh missing, timeout)."""
    try:
        r = subprocess.run(cmd, input=body.encode(), capture_output=True, timeout=GATE_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        log.warning("host gate timed out after %.0f s", GATE_TIMEOUT_S)
        return None
    except OSError as e:
        log.warning("host gate could not run: %s", type(e).__name__)
        return None
    if r.returncode not in GATE_DELIVERED:
        # stderr is the host's usage line, never our body.
        err = r.stderr.decode(errors="replace").strip()[:200]
        log.warning("host gate rc=%d: %s", r.returncode, err)
    return r.returncode


def _send_gate(cmd: list[str], text: str, *, label: str, icon: str) -> str | None:
    """Deliver through the host gate. None when everything went out; otherwise the text that is
    still undelivered (the caller hands it to the fallback), cut between lines and never over
    the gate's byte limit."""
    limit = MAX_CHARS
    while True:
        chunks = split_text(text, limit)
        bodies = []
        for n, chunk in enumerate(chunks, start=1):
            part = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
            bodies.append(f"{icon} <b>{to_html(label)}{part}</b>: {to_html(chunk)}")
        if all(len(b.encode()) <= GATE_MAX_BYTES for b in bodies) or limit <= 256:
            break
        limit //= 2
    for n, body in enumerate(bodies, start=1):
        rc = _run_gate(cmd, body)
        if rc not in GATE_DELIVERED:
            return "\n".join(chunks[n - 1 :])
        log.info("admin alert delivered via %s (host gate)", GATE_DELIVERED[rc])
    return None


def _post_kchat(url: str, text: str) -> bool:
    for attempt in range(1, KCHAT_ATTEMPTS + 1):
        try:
            r = httpx.post(url, json={"text": text}, timeout=20)
        except httpx.HTTPError as e:
            log.warning("kChat alert failed (try %d): %s", attempt, type(e).__name__)
            continue
        if r.is_success:  # kChat answers 201 Created (23-10: a 200-only check posted twice)
            return True
        # Never log the URL: it is the secret.
        log.warning("kChat alert rejected (try %d): HTTP %s", attempt, r.status_code)
    return False


def send_admin(
    token: str,
    chat_id: int | None,
    text: str,
    *,
    key: str | None = None,
    every_s: float = 3600,
    label: str = "Mikiwame",
    icon: str = "⚠️",
) -> bool:
    """Send `text` to the admin: through the host gate (WhatsApp) first, then kChat once its
    webhook is installed, else Telegram; with `key`, at most once per `every_s` seconds.

    A long text (the weekly digest) goes out as several messages, cut between lines.
    """
    now = time.monotonic()
    if key and now - _last_sent.get(key, -1e9) < every_s:
        return False
    gate = notify_gate()
    rest: str | None = text
    if gate:
        rest = _send_gate(gate, text, label=label, icon=icon)
    if rest is None:
        ok = True
    elif webhook := kchat_webhook():
        ok = _send_kchat(webhook, rest, label=label, icon=icon)
    else:
        ok = _send_telegram(token, chat_id, rest, label=label, icon=icon)
    if ok and key:
        _last_sent[key] = now  # only once it was really delivered
    return ok


def _send_kchat(url: str, text: str, *, label: str, icon: str) -> bool:
    chunks = split_text(text, MAX_CHARS - len(label) - 32)
    for n, chunk in enumerate(chunks, start=1):
        part = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
        if not _post_kchat(url, f"{icon} **{to_markdown(label)}{part}**: {to_markdown(chunk)}"):
            _log_undelivered(label, "\n".join(chunks[n - 1 :]))
            return False
    return True


def _send_telegram(token: str, chat_id: int | None, text: str, *, label: str, icon: str) -> bool:
    if not chat_id:
        log.warning("no admin chat configured; alert dropped: %s", text)
        return False
    head = f"{icon} {label}: "
    chunks = split_text(text, MAX_CHARS - len(head) - 8)
    for n, chunk in enumerate(chunks, start=1):
        part = f" ({n}/{len(chunks)})" if len(chunks) > 1 else ""
        try:
            r = httpx.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": f"{icon} {label}{part}: {chunk}"},
                timeout=20,
            )
        except httpx.HTTPError as e:
            log.warning("admin alert failed: %s", type(e).__name__)
            return False
        if r.status_code != 200:
            # Never log the URL: it carries the bot token.
            log.warning("admin alert rejected: HTTP %s", r.status_code)
            return False
    return True
