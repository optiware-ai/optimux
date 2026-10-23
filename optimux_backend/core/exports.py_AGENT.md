# exports.py Agent Details

## File Overview
`exports.py` prepares snapshot downloads, encrypted with [age](https://age-encryption.org) through `pyrage` (v0.3 C). A snapshot that holds keys (Data, Everything) can only leave the server encrypted.

## Modes (`MODES`)
- **key** (the Dashboard's default): a one-time x25519 key pair is generated; the archive is encrypted to its public key, and the secret key (`AGE-SECRET-KEY-1…`) is returned **once** in the response. It is never written to disk or logged. Lost key = lost download, by design.
- **recipient**: the user's own public key: `age1…`, `ssh-ed25519 …` or `ssh-rsa …` (`parse_recipient`; a pasted secret key or private key is refused with a clear message).
- **plain**: no encryption; only for a Workspace snapshot (kind and id `-workspace`, `includes_secrets` false) whose description says `home_excluded` (taken by a version that keeps Optimux's own data folder out of projects; older ones must be downloaded encrypted). Served straight from the archive by `GET /api/snapshots/{id}/download`.
- Why not a passphrase: `pyrage.passphrase` only encrypts bytes held in memory; a 5 GB snapshot would need 5 GB of RAM.
- **`pyrage.encrypt_io` on open files, never `encrypt_file`:** both stream (about 11 MB of memory for any size), but `encrypt_file` holds Python's GIL for the whole run, which froze the server (chat sockets, other requests) while a big snapshot was encrypted; `encrypt_io` releases it. `test_preparing_a_download_does_not_freeze_the_server` guards this.
- Recipients: an SSH key is accepted as in `id_*.pub` or an `authorized_keys` line with options, with any spacing; only type + key go to pyrage. RSA keys under 2048 bits are refused by pyrage, with that reason; hardware-token keys (`sk-ssh-…`) are refused with their own message (age can't use them).

## Files
- `SNAPSHOT_DIR/exports/` (mode 700): `<token>.age` (mode 600 from creation, written as `.age.partial` and renamed) + `<token>.json` (sid, created, file name, mode, size; no key).
- `token` = `secrets.token_urlsafe(24)` (32 characters, `TOKEN_RE`); every lookup validates it, so no path can be passed in.
- A download is kept for `EXPORT_TTL` (1 hour) and can be downloaded again during that time; `sweep()` removes older files (at server start, on `GET /api/snapshots`, and before each new download). Exports don't count toward the snapshot space limit, so: one download is prepared at a time (`_lock`, a second one is refused), a new download replaces the snapshot's earlier one (removed only after the new one is ready, and its size counts as free space in the check), deleting a snapshot removes its download (`remove_for`), and a snapshot deleted while its download is being encrypted drops that download at the end of `prepare`.
- Free disk is checked first (archive size + `FREE_MARGIN`, the same 200 MB snapshots keep free).
- File names: `optimux-snapshot-<id>.tar.gz.age` (or `.tar.gz` plain).

## Core Functionality
- **`prepare(sid, mode, recipient)`** → `{mode, url, filename, size, key, expires_in}`; raises `ExportError` (shown to the user).
- **`export_file(token)`** → `(path, filename)` of an unexpired download.
- **`parse_recipient(text)`**, **`download_name(sid, encrypted)`**, **`sweep(now)`**, **`remove(token)`**, **`remove_for(sid)`**, **`exports_dir()`**.
- Opening a download: `age -d -i optimux-key-<id>.txt -o snapshot.tar.gz <file>.age` (the key file may hold `#` comment lines; age skips them). Restore in the Dashboard comes in v0.3 D.

## Links & Dependencies
- `snapshots.py` (`get`, `archive_path`, `free_bytes`), `paths.py` (`SNAPSHOT_DIR`, `write_private`), `pyrage==1.4.0` (wheels for Linux x86_64/aarch64 and macOS, Python 3.10–3.13).
- Used by `main.py`: `POST /api/snapshots/{id}/export`, `GET /api/snapshots/exports/{token}`, `GET /api/snapshots/{id}/download`; `config_manager.py` validates the remembered `snapshot_recipient` with `parse_recipient`.
- Tests: `tests/test_exports.py` (including one that opens a download with the `age` command when it is installed; the SSH RSA tests make their key with `openssl`; `tests/data/test-only-ssh-ed25519.pub` is a throwaway public key).
- **`prepare_file(owner, path, filename, mode, recipient)` (v0.5.2):** an encrypted download of any file Optimux keeps (a daily backup: owner `daily-<name>`); only `key` and `recipient` modes. `prepare` and it share `_encrypt`.
