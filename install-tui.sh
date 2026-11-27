#!/usr/bin/env bash
# ----------------------------------------------------------------------------
#  optimux-tui installer: the terminal client, on the computer you work from
#
#    curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install-tui.sh | bash
#
#  A separate package from the server (install.sh): it installs only the
#  `optimux-tui` command, a single program that talks to an Optimux server
#  (on this computer or elsewhere). It downloads the ready-made build for this
#  computer from the GitHub release, checks its SHA-256 checksum, and puts it in
#  ~/.local/bin. When there is no ready-made build for this computer, it builds
#  it from the source with Rust (asking first if Rust has to be installed).
#  Run it again to update.
#
#  Prefer to read it first?
#    curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install-tui.sh -o install-tui.sh
#    less install-tui.sh && bash install-tui.sh
#
#  Options:
#    --dir PATH          where the command goes (default ~/.local/bin)
#    --version TAG       a release, like v0.8.0 (default: the latest)
#    --from-source       build it here with Rust, even when a ready-made one exists
#    --ref REF           with --from-source: the git branch or tag (default: the
#                        release's tag, else main)
#    --server ADDRESS    sign in to this Optimux server at the end
#    --yes               accept the defaults (installing Rust when needed)
#    --uninstall         remove the command (your sign-in is kept; see below)
#    -h, --help          this help
#
#  Nothing here needs sudo. The whole file is functions; nothing runs until the
#  last line, so a download cut off halfway can't run half an installer.
# ----------------------------------------------------------------------------

TUI_REPO="${OPTIMUX_REPO:-https://github.com/optiware-ai/optimux}"
TUI_API="${OPTIMUX_TUI_API:-https://api.github.com/repos/optiware-ai/optimux/releases/latest}"
TUI_DOWNLOAD="${OPTIMUX_TUI_DOWNLOAD:-$TUI_REPO/releases/download}"
TUI_MIN_RUST="1.85"
# the ready-made Linux builds are made on Ubuntu 22.04: they need this glibc or newer
TUI_MIN_GLIBC="2.35"

say()  { printf '%s\n' "$*"; }
note() { printf '  %s\n' "$*"; }
warn() { printf 'Warning: %s\n' "$*" >&2; }
die()  { printf 'Error: %s\n' "$*" >&2; exit 1; }

usage() {
  sed -n '/^#  Options:/,/^#  Nothing here/p' "${BASH_SOURCE[0]:-$0}" 2>/dev/null | sed 's/^#//' ||
    say "Options: --dir PATH --version TAG --from-source --ref REF --server ADDRESS --yes --uninstall"
}

# Questions work even under `curl | bash`: they read the terminal, not the pipe.
TUI_TTY=0
open_tty() {
  if { exec 3</dev/tty; } 2>/dev/null; then TUI_TTY=1; fi
}

# confirm "question" Y|N  (the default when there is no terminal, or with --yes)
confirm() {
  local q="$1" def="${2:-N}" a=""
  if [[ "${TUI_YES:-0}" == 1 ]]; then [[ "$def" == Y ]]; return; fi
  if [[ "$TUI_TTY" != 1 ]]; then [[ "$def" == Y ]]; return; fi
  printf '%s [%s] ' "$q" "$([[ "$def" == Y ]] && echo Y/n || echo y/N)"
  read -r a <&3 || a=""
  a="${a:-$def}"
  [[ "$a" =~ ^[Yy] ]]
}

# The Rust target this computer runs, or nothing when there is no ready-made build.
target_for() {
  local os="$1" arch="$2" a
  case "$arch" in
    x86_64 | amd64) a=x86_64 ;;
    aarch64 | arm64) a=aarch64 ;;
    *) return 0 ;;
  esac
  case "$os" in
    Linux) echo "$a-unknown-linux-gnu" ;;
    Darwin) echo "$a-apple-darwin" ;;
    *) return 0 ;;
  esac
}

# 0 when version $1 >= $2 (dotted numbers)
version_ge() {
  [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n1)" == "$2" ]]
}

# This Linux's glibc version ("" on musl, like Alpine, or when unknown).
glibc_version() {
  local v=""
  v="$(getconf GNU_LIBC_VERSION 2>/dev/null | awk '{print $2}')" || true
  echo "$v"
}

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    return 1
  fi
}

# The latest release's tag, from GitHub ("" when there is none or GitHub can't be asked).
latest_tag() {
  local json=""
  json="$(curl -fsSL "$TUI_API" 2>/dev/null)" || true
  printf '%s' "$json" | grep -o '"tag_name"[[:space:]]*:[[:space:]]*"[^"]*"' | head -n1 | sed 's/.*"\([^"]*\)"$/\1/' || true
}

# Downloads, checks and unpacks the ready-made build into $2; 1 when it can't.
fetch_ready_made() {
  local tag="$1" work="$2" target="$3" name file want got
  name="optimux-tui-$tag-$target"
  file="$work/$name.tar.gz"
  say "Downloading optimux-tui $tag for $target"
  if ! curl -fsSL "$TUI_DOWNLOAD/$tag/$name.tar.gz" -o "$file" ||
    ! curl -fsSL "$TUI_DOWNLOAD/$tag/$name.tar.gz.sha256" -o "$file.sha256"; then
    warn "No ready-made build of $tag for $target."
    return 1
  fi
  want="$(awk '{print $1; exit}' "$file.sha256")"
  got="$(sha256_of "$file")" || die "Neither sha256sum nor shasum is installed: the download can't be checked, so it isn't installed."
  if [[ -z "$want" || "$want" != "$got" ]]; then
    rm -f "$file"
    die "The download's checksum doesn't match ($got, expected ${want:-nothing}): it was not installed. Try again later, or use --from-source."
  fi
  note "Checksum (SHA-256) checked."
  tar -xzf "$file" -C "$work" || die "Couldn't unpack $file."
  [[ -x "$work/$name/optimux-tui" ]] || die "The download has no optimux-tui in it."
  # it must run here (an older system library is the usual reason it doesn't)
  if ! "$work/$name/optimux-tui" --version >/dev/null 2>&1; then
    warn "The ready-made build doesn't run on this computer."
    return 1
  fi
  mv "$work/$name/optimux-tui" "$work/optimux-tui"
}

# Rust's cargo, new enough; offers to install Rust (rustup, for this user only).
ensure_rust() {
  # shellcheck disable=SC1091
  [[ -f "$HOME/.cargo/env" ]] && . "$HOME/.cargo/env"
  if command -v cargo >/dev/null 2>&1; then
    local v=""
    v="$(rustc --version 2>/dev/null | awk '{print $2}')" || true
    if [[ -n "$v" ]] && version_ge "$v" "$TUI_MIN_RUST"; then return 0; fi
    if command -v rustup >/dev/null 2>&1; then
      say "Rust $v is older than $TUI_MIN_RUST: updating it with rustup"
      rustup update stable || die "rustup couldn't update Rust."
      return 0
    fi
    die "Rust $v is older than $TUI_MIN_RUST, and rustup isn't installed: update Rust, then run this again."
  fi
  say "Building optimux-tui needs Rust $TUI_MIN_RUST or newer, which isn't installed."
  note "rustup (rustup.rs) installs it for you only, in ~/.cargo and ~/.rustup, without sudo."
  confirm "Install Rust with rustup now?" Y || die "Install Rust (https://rustup.rs), then run this again."
  curl -fsSL https://sh.rustup.rs | sh -s -- -y --profile minimal --no-modify-path || die "rustup couldn't install Rust."
  # shellcheck disable=SC1091
  . "$HOME/.cargo/env"
}

# Builds from the source at $ref into $work/optimux-tui.
build_from_source() {
  local ref="$1" work="$2"
  command -v git >/dev/null 2>&1 || die "Building needs git: install it, then run this again."
  ensure_rust
  say "Downloading the source ($ref)"
  git clone --quiet --depth 1 --branch "$ref" "$TUI_REPO.git" "$work/src" || die "Couldn't download the source at $ref."
  say "Building optimux-tui (a few minutes, the first time)"
  (cd "$work/src/tui" && cargo build --release --locked) || die "The build failed (above: why)."
  cp "$work/src/tui/target/release/optimux-tui" "$work/optimux-tui"
}

# Puts the program in $dir, replacing an older one in one step.
put_in_place() {
  local work="$1" dir="$2"
  mkdir -p "$dir" || die "Can't create $dir."
  install -m 0755 "$work/optimux-tui" "$dir/.optimux-tui.new" || die "Can't write to $dir."
  mv -f "$dir/.optimux-tui.new" "$dir/optimux-tui"
}

on_path() {
  case ":$PATH:" in *":$1:"*) return 0 ;; esac
  return 1
}

uninstall() {
  local dir="$1"
  if [[ -e "$dir/optimux-tui" ]]; then
    rm -f "$dir/optimux-tui"
    say "Removed $dir/optimux-tui."
  else
    say "There is no optimux-tui in $dir (--dir chooses another folder)."
  fi
  note "Your sign-in and layout stay in ${OPTIMUX_TUI_HOME:-${XDG_CONFIG_HOME:-$HOME/.config}/optimux-tui}."
  note "Delete that folder to remove them too (sign out first with optimux-tui logout to end the session)."
}

main() {
  set -Eeuo pipefail
  local dir="$HOME/.local/bin" tag="" source=0 ref="" server="" remove=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dir) dir="${2:?--dir needs a folder}"; shift ;;
      --version) tag="${2:?--version needs a tag, like v0.8.0}"; shift ;;
      --from-source) source=1 ;;
      --ref) ref="${2:?--ref needs a branch or tag}"; shift ;;
      --server) server="${2:?--server needs an address}"; shift ;;
      --yes | -y) TUI_YES=1 ;;
      --uninstall) remove=1 ;;
      -h | --help) usage; exit 0 ;;
      *) printf 'Unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
  done
  open_tty
  if [[ "$remove" == 1 ]]; then uninstall "$dir"; return 0; fi
  command -v curl >/dev/null 2>&1 || die "curl is needed: install it, then run this again."

  local work
  work="$(mktemp -d)"
  # shellcheck disable=SC2064
  trap "rm -rf '$work'" EXIT

  local target os
  os="$(uname -s)"
  target="$(target_for "$os" "$(uname -m)")"
  if [[ "$os" == Linux && -n "$target" ]]; then
    local libc
    libc="$(glibc_version)"
    if [[ -z "$libc" ]]; then
      note "This Linux doesn't use glibc (Alpine?): the ready-made build won't run, so it is built here."
      target=""
    elif ! version_ge "$libc" "$TUI_MIN_GLIBC"; then
      note "This Linux's glibc is $libc, older than $TUI_MIN_GLIBC: the ready-made build won't run, so it is built here."
      target=""
    fi
  fi

  [[ -n "$tag" ]] || tag="$(latest_tag)"
  local ready=0
  if [[ "$source" != 1 && -n "$target" ]]; then
    if [[ -z "$tag" ]]; then
      warn "No release found on GitHub (or GitHub couldn't be asked): building from the source."
    elif fetch_ready_made "$tag" "$work" "$target"; then
      ready=1
    fi
  fi
  if [[ "$ready" != 1 ]]; then
    [[ "$source" == 1 || -n "$target" ]] || note "There is no ready-made build for $os $(uname -m): it is built here."
    build_from_source "${ref:-${tag:-main}}" "$work"
  fi

  put_in_place "$work" "$dir"
  say ""
  say "✓ optimux-tui is installed: $dir/optimux-tui ($("$dir/optimux-tui" --version 2>/dev/null || echo "?"))"
  if ! on_path "$dir"; then
    note "$dir isn't on your PATH. Add it (bash: echo 'export PATH=\"$dir:\$PATH\"' >> ~/.bashrc, then open a new terminal),"
    note "or run it as $dir/optimux-tui."
  fi
  say ""
  say "Next: sign in to your Optimux server, then open it."
  note "optimux-tui login --server 127.0.0.1:8000             (Optimux on this computer)"
  note "optimux-tui login --server https://optimux.example.com (a server with a domain)"
  note "optimux-tui                                           (the chat; Ctrl+b ? lists every key)"
  if [[ -n "$server" ]]; then
    if [[ "$TUI_TTY" == 1 ]]; then
      say ""
      "$dir/optimux-tui" --server "$server" login <&3 || warn "Not signed in: run optimux-tui login --server $server later."
    else
      note "No terminal to type the password in: run optimux-tui login --server $server yourself."
    fi
  fi
}

# Loaded by the tests for its functions: don't run the installer then
if [[ "${OPTIMUX_LIB_ONLY:-}" != 1 ]]; then main "$@"; fi
