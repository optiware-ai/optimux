"""Downloads of snapshots, encrypted with age (https://age-encryption.org).

A snapshot that leaves the server is encrypted unless it holds no keys. Two ways:
- key:       Optimux makes a one-time key pair, encrypts to it, and returns the
             secret key ONCE. It is never written to disk or logged: without it
             the download can't be opened, by anyone.
- recipient: the user's own public key (age1..., or an ssh-ed25519 / ssh-rsa key).
- plain:     no encryption; only for snapshots without keys (Workspace only).

Encryption streams with pyrage.encrypt_io, which needs little memory for any size
and, unlike pyrage.encrypt_file, lets the rest of the server keep running (it
releases Python's lock while it works). One download is prepared at a time; the
encrypted file goes to SNAPSHOT_DIR/exports/<token>.age (mode 600), replaces any
earlier download of the same snapshot, and can be downloaded for EXPORT_TTL
seconds; older ones are swept (at start, on listing, before each new one).
Open it with:  age -d -i optimux-key.txt -o snapshot.tar.gz <file>.age
(or restore it in Optimux: Dashboard -> Backups).
"""
import json
import os
import re
import secrets
import stat
import threading
import time

import pyrage

from optimux_backend.core import paths, snapshots

MODES = ("key", "recipient", "plain")
EXPORT_TTL = 3600                       # an encrypted download is kept for an hour
FREE_MARGIN = snapshots.FREE_MARGIN       # never fill the disk to the last byte
KEY_TYPES = ("ssh-ed25519", "ssh-rsa")
_lock = threading.Lock()
TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{32}$")


class ExportError(Exception):
    """A download that can't be prepared; the message is shown to the user."""


def exports_dir() -> str:
    folder = os.path.join(paths.SNAPSHOT_DIR, "exports")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    try:
        os.chmod(folder, stat.S_IRWXU)
    except OSError:
        pass
    return folder


def parse_recipient(text: str):
    """An age or SSH public key, as a pyrage recipient. Refuses secret keys with
    a clear message (the most likely mistake)."""
    key = (text or "").strip()
    if not key:
        raise ExportError("Paste a public key: age1... or ssh-ed25519 ... / ssh-rsa ...")
    if key.upper().startswith("AGE-SECRET-KEY") or "PRIVATE KEY" in key.upper():
        raise ExportError("That is a secret key. Paste the PUBLIC key instead (age1... or ssh-ed25519 ...), "
                          "and keep the secret one to yourself.")
    if "\n" in key or len(key) > 4096:
        raise ExportError("Paste one public key, on one line.")
    words = key.split()
    if any(w.startswith("sk-") for w in words[:3]):
        raise ExportError("Keys kept in a hardware token (sk-ssh-...) don't work with age. Use an ordinary "
                          "ssh-ed25519 key or an age key (age-keygen).")
    try:
        if words[0].startswith("age1") and len(words) == 1:
            return pyrage.x25519.Recipient.from_str(words[0])
        # An SSH key as in id_ed25519.pub or an authorized_keys line: options and the
        # comment are left out, any spacing between the parts is accepted
        found = next((i for i, w in enumerate(words) if w in KEY_TYPES), None)
        if found is not None and found + 1 < len(words):
            return pyrage.ssh.Recipient.from_str(f"{words[found]} {words[found + 1]}")
    except Exception:
        if words and "ssh-rsa" in words:
            raise ExportError("This public key is not valid: copy it whole. RSA keys must have at least 2048 bits.")
        raise ExportError("This public key is not valid. Check that it was copied whole.")
    raise ExportError("Not a public key Optimux can use: it must start with age1, ssh-ed25519 or ssh-rsa.")


def download_name(sid: str, encrypted: bool) -> str:
    return f"optimux-snapshot-{sid}.tar.gz" + (".age" if encrypted else "")


def _token_path(token: str, ext: str) -> str:
    if not TOKEN_RE.fullmatch(token or ""):
        raise ExportError("This download link is not valid.")
    return os.path.join(exports_dir(), f"{token}{ext}")


def _infos() -> list[tuple[str, dict]]:
    """(token, info) of the prepared downloads."""
    out = []
    try:
        names = os.listdir(os.path.join(paths.SNAPSHOT_DIR, "exports"))
    except FileNotFoundError:
        return out
    for name in names:
        token = name[:-5] if name.endswith(".json") else ""
        if TOKEN_RE.fullmatch(token):
            try:
                with open(_token_path(token, ".json")) as f:
                    out.append((token, json.load(f)))
            except (OSError, ValueError):
                pass
    return out


def remove(token: str) -> None:
    for ext in (".age", ".age.partial", ".json"):
        try:
            os.unlink(_token_path(token, ext))
        except FileNotFoundError:
            pass


def remove_for(sid: str) -> None:
    """Remove the prepared downloads of one snapshot (replaced, or the snapshot deleted)."""
    for token, info in _infos():
        if info.get("sid") == sid:
            remove(token)


def sweep(now: float | None = None) -> None:
    """Remove encrypted downloads older than EXPORT_TTL (and any half-written one)."""
    now = now or time.time()
    folder = os.path.join(paths.SNAPSHOT_DIR, "exports")
    try:
        names = os.listdir(folder)
    except FileNotFoundError:
        return
    for name in names:
        full = os.path.join(folder, name)
        try:
            if now - os.path.getmtime(full) > EXPORT_TTL:
                os.unlink(full)
        except OSError:
            pass


def prepare(sid: str, mode: str, recipient: str = "") -> dict:
    """Get a snapshot ready to download. Returns the link, the file name, the size
    and, in "key" mode, the secret key (returned here once, kept nowhere)."""
    if mode not in MODES:
        raise ExportError(f"Unknown download mode '{mode}'.")
    try:
        meta = snapshots.get(sid)
    except snapshots.SnapshotError as e:
        raise ExportError(str(e))
    if meta.get("status") != "ready":
        raise ExportError("This snapshot is not ready.")
    archive = snapshots.archive_path(sid)
    if mode == "plain":
        if meta.get("includes_secrets") or meta.get("kind") != "workspace" or not sid.endswith("-workspace"):
            raise ExportError("This snapshot contains your API keys and sign-in data, so it can only be "
                              "downloaded encrypted.")
        if not meta.get("home_excluded"):
            # Taken before projects were checked for Optimux's own data folder inside them
            raise ExportError("This snapshot was taken by an older Optimux that didn't check projects for its "
                              "own data folder (with your keys) inside them. Download it encrypted, or take a new one.")
        return {"mode": "plain", "url": f"/api/snapshots/{sid}/download", "filename": download_name(sid, False),
                "size": os.path.getsize(archive), "key": None}

    return _encrypt(sid, archive, download_name(sid, True), mode, recipient)


def prepare_file(owner: str, path: str, filename: str, mode: str, recipient: str = "") -> dict:
    """An encrypted download of any file Optimux keeps (v0.5.2: a daily database
    backup). owner identifies it, for replacing an earlier download; only "key"
    and "recipient" modes: these files hold your chats, never sent in the clear."""
    if mode not in ("key", "recipient"):
        raise ExportError("This file can only be downloaded encrypted.")
    if not os.path.isfile(path):
        raise ExportError("This file doesn't exist anymore.")
    return _encrypt(owner, path, filename + ".age", mode, recipient)


def _encrypt(sid: str, archive: str, filename: str, mode: str, recipient: str) -> dict:
    """Encrypt archive into a download (see the module notes); sid: whose download it is."""
    recipients, secret = None, None
    if mode == "key":
        identity = pyrage.x25519.Identity.generate()
        recipients, secret = [identity.to_public()], str(identity)
    else:
        recipients = [parse_recipient(recipient)]

    if not _lock.acquire(blocking=False):
        raise ExportError("Another download is being prepared. Wait for it to finish.")
    try:
        sweep()
        earlier = [(t, i) for t, i in _infos() if i.get("sid") == sid]
        size = os.path.getsize(archive)
        # The earlier download of this snapshot is replaced, so its space counts as free
        if snapshots.free_bytes() + sum(i.get("size", 0) for _, i in earlier) < size + FREE_MARGIN:
            raise ExportError("Not enough free disk space to prepare the encrypted download.")
        token = secrets.token_urlsafe(24)           # 32 characters
        out, partial = _token_path(token, ".age"), _token_path(token, ".age.partial")
        try:
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)   # private from the start
            with open(archive, "rb") as src, os.fdopen(fd, "wb") as dst:
                pyrage.encrypt_io(src, dst, recipients)
            os.replace(partial, out)
        except Exception as e:
            try:
                os.unlink(partial)
            except FileNotFoundError:
                pass
            raise ExportError(f"The download could not be prepared: {e}")
        if not os.path.exists(archive):
            # The snapshot was deleted while this was encrypted: its download goes too
            remove(token)
            raise ExportError("This snapshot was deleted while the download was being prepared.")
        info = {"sid": sid, "created": time.time(), "filename": filename,
                "mode": mode, "size": os.path.getsize(out)}
        paths.write_private(_token_path(token, ".json"), json.dumps(info))
        for old_token, _ in earlier:                # one prepared download per snapshot,
            remove(old_token)                        # replaced only once the new one is ready
    finally:
        _lock.release()
    return {"mode": mode, "url": f"/api/snapshots/exports/{token}", "filename": info["filename"],
            "size": info["size"], "key": secret, "expires_in": EXPORT_TTL}


def export_file(token: str) -> tuple[str, str]:
    """(path, download file name) of a prepared download that hasn't expired."""
    path, info_path = _token_path(token, ".age"), _token_path(token, ".json")
    try:
        with open(info_path) as f:
            info = json.load(f)
    except (OSError, ValueError):
        raise ExportError("This download has expired or doesn't exist. Prepare it again.")
    if time.time() - info.get("created", 0) > EXPORT_TTL or not os.path.exists(path):
        raise ExportError("This download has expired or doesn't exist. Prepare it again.")
    return path, info["filename"]
