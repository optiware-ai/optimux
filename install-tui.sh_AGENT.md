# install-tui.sh Agent Details

## File Overview
The terminal client's own installer, a separate package from the server's `install.sh`: `curl -fsSL .../install-tui.sh | bash`. Pure bash, no sudo; it installs only the `optimux-tui` command (default `~/.local/bin`). Every statement is in a function; only the last line calls `main` (not with `OPTIMUX_LIB_ONLY=1`, which the tests use to load the functions), so a truncated download runs nothing.

## Flow
1. Options: `--dir`, `--version TAG` (default: the latest release, from the GitHub API), `--from-source`, `--ref` (source build; default the tag, else main), `--server` (runs `optimux-tui --server X login` at the end when there is a terminal), `--yes`, `--uninstall`, `--help`.
2. `target_for <uname -s> <uname -m>`: `x86_64|aarch64` × `unknown-linux-gnu|apple-darwin`, or nothing (no ready-made build). On Linux the glibc (`getconf GNU_LIBC_VERSION`) must be at least `TUI_MIN_GLIBC` (2.35: the builds are made on Ubuntu 22.04); musl or older → source build.
3. `fetch_ready_made`: downloads `<TUI_DOWNLOAD>/<tag>/optimux-tui-<tag>-<target>.tar.gz` and its `.sha256`, compares the SHA-256 (`sha256sum` or `shasum -a 256`; neither → stops: nothing unchecked is installed); a mismatch stops with nothing installed. The unpacked program must run (`--version`), else the source build takes over.
4. `build_from_source`: needs git; `ensure_rust` (cargo with rustc ≥ `TUI_MIN_RUST` 1.85; an older one is updated with rustup; none → asks, then rustup with `--profile minimal --no-modify-path`, for this user only), shallow clone at the ref, `cargo build --release --locked` in `tui/`.
5. `put_in_place`: `install -m 0755` to a temporary name in the folder, then `mv` (one step); PATH hint when the folder isn't on PATH; next steps (login, open).
6. `uninstall`: removes the command only; the sign-in and layout (`$OPTIMUX_TUI_HOME`, else `$XDG_CONFIG_HOME/optimux-tui`, else `~/.config/optimux-tui`) stay, with how to remove them.

## Overrides (tests, forks)
`OPTIMUX_REPO` (source repository, without `.git`), `OPTIMUX_TUI_API` (the latest-release URL), `OPTIMUX_TUI_DOWNLOAD` (the releases' download base; `file://` works, which the tests use for a fake release).

## Where the builds come from
`.github/workflows/release-tui.yml`, on every `v*` tag (or by hand for an existing tag): creates the release if missing, builds `optimux-tui` for the four targets (Linux on ubuntu-22.04 and ubuntu-22.04-arm, macOS on macos-15, Intel cross-built), runs the native ones once, and attaches `optimux-tui-<tag>-<target>.tar.gz` (the program, tui/README.md, LICENSE) and its `.sha256`.

## Tests
`tests/test_install_tui.py`: syntax, shellcheck, help and a bad option, last-line rule, targets, version comparison, a fake release installed with its checksum checked (0755) and uninstalled (sign-in kept), a wrong checksum installing nothing, a build that doesn't run falling back to the source.
