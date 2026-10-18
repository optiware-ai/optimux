#!/usr/bin/env bash
# shellcheck disable=SC2034,SC2088
# ----------------------------------------------------------------------------
#  Optimux installer (Feature 24)
#
#    curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh | bash
#
#  Installs Optimux on this computer (just you) or on a server (VPS) behind
#  HTTPS, with its own virtualenv, the sandbox, private file permissions and a
#  `optimux` command to start, stop, update and uninstall it. Run it again to
#  update, reconfigure or uninstall.
#
#  Prefer to read it first?
#    curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh -o install.sh
#    less install.sh && bash install.sh
#
#  Options (for scripts; everything is asked interactively otherwise):
#    --local | --server      where to install
#    --domain NAME           server: HTTPS for this domain (Caddy)
#    --no-domain             server: no public web access; use an SSH tunnel
#    --yes                   accept the defaults, skip optional questions
#    --dir PATH              local: where the program goes (~/.local/share/optimux)
#    --workspace PATH        the folder that holds your projects
#    --port N                local / tunnel: port on 127.0.0.1 (default 8000)
#    --ref REF               git branch or tag to install (default main)
#    --no-start              don't start Optimux at the end
#    --update | --uninstall  act on an existing install
#    -h, --help              this help
#
#  The whole file is functions; nothing runs until the last line, so a
#  download cut off halfway can't run half an installer.
# ----------------------------------------------------------------------------

CM_REPO="${OPTIMUX_REPO:-https://github.com/optiware-ai/optimux.git}"
CM_REF="${OPTIMUX_REF:-main}"
CM_MIN_PY="3.10"

# Models offered for a first provider (the Dashboard can add any other)
#   label | env key | conductor + simple model | complex model | names
CM_PROVIDERS=(
  "Google Gemini|GEMINI_API_KEY|gemini/gemini-3.6-flash|gemini/gemini-3.6-flash|Gemini 3.6 Flash|Gemini 3.6 Flash|https://aistudio.google.com/apikey"
  "Anthropic Claude|ANTHROPIC_API_KEY|anthropic/claude-haiku-4-5-20251001|anthropic/claude-sonnet-5-5|Claude Haiku 4.5|Claude Sonnet 5.5|https://console.anthropic.com/settings/keys"
  "OpenAI|OPENAI_API_KEY|openai/gpt-5-mini|openai/gpt-5|GPT-5 mini|GPT-5|https://platform.openai.com/api-keys"
)

# ============================================================================
#  Terminal interface
# ============================================================================

tui_init() {
  CM_TTY=""
  if { exec 3</dev/tty; } 2>/dev/null; then CM_TTY=1; fi   # questions work even under `curl | bash`
  # UTF-8 for the drawing characters, ASCII otherwise
  if [[ "$(locale charmap 2>/dev/null)" != "UTF-8" ]]; then
    if locale -a 2>/dev/null | grep -qiE '^c\.utf-?8$'; then export LC_ALL=C.UTF-8; fi
  fi
  if [[ "$(locale charmap 2>/dev/null)" == "UTF-8" ]]; then
    S_OK="✓" S_ERR="✗" S_WARN="!" S_ARROW="❯" S_STEP="◆" S_NOTE="↳" S_DOT="·" S_ON="●" S_OFF="○"
    B_TL="╭" B_TR="╮" B_BL="╰" B_BR="╯" B_H="─" B_V="│" SPIN=(⠋ ⠙ ⠹ ⠸ ⠼ ⠴ ⠦ ⠧ ⠇ ⠏)
  else
    S_OK="+" S_ERR="x" S_WARN="!" S_ARROW=">" S_STEP="*" S_NOTE="-" S_DOT="-" S_ON="(*)" S_OFF="( )"
    B_TL="+" B_TR="+" B_BL="+" B_BR="+" B_H="-" B_V="|" SPIN=(- '\' '|' /)
  fi
  if [[ -t 1 && -z "${NO_COLOR:-}" && "${TERM:-dumb}" != "dumb" ]]; then
    C0=$'\e[0m' CB=$'\e[1m' CD=$'\e[2m' CI=$'\e[3m'
    if [[ "$(tput colors 2>/dev/null || echo 8)" -ge 256 ]]; then
      CA=$'\e[38;5;75m' CG=$'\e[38;5;78m' CY=$'\e[38;5;221m' CR=$'\e[38;5;203m' CM=$'\e[38;5;245m' CW=$'\e[38;5;255m'
    else
      CA=$'\e[34m' CG=$'\e[32m' CY=$'\e[33m' CR=$'\e[31m' CM=$'\e[90m' CW=$'\e[37m'
    fi
    CM_FANCY=1
  else
    C0="" CB="" CD="" CI="" CA="" CG="" CY="" CR="" CM="" CW=""
    CM_FANCY=""
  fi
}

cursor_hide() { [[ -n "$CM_FANCY" ]] && printf '\e[?25l' || true; }
cursor_show() { [[ -n "$CM_FANCY" ]] && printf '\e[?25h' || true; }
strip_ansi() { sed -E 's/\x1b\[[0-9;?]*[a-zA-Z]//g'; }
vislen() { local s; s="$(printf '%s' "$1" | strip_ansi)"; echo "${#s}"; }

# Width to wrap at: the terminal's, at most 100 columns (80 when unknown)
term_cols() {
  local c
  c="$(tput cols 2>/dev/null || echo 80)"
  [[ "$c" =~ ^[0-9]+$ ]] || c=80
  ((c > 100)) && c=100
  ((c < 40)) && c=80
  echo "$c"
}

# emit PAD ICON STYLE TEXT: the icon, then TEXT wrapped with a hanging indent;
# STYLE (colour codes) is applied to every line of the text
emit() {
  local pad="$1" icon="$2" style="$3" text="$4" width line first=1
  width=$(( $(term_cols) - ${#pad} - 3 ))
  ((width < 20)) && width=20
  while IFS= read -r line; do
    line="${line% }"
    if ((first)); then printf '%s%s %s%s%s\n' "$pad" "$icon" "$style" "$line" "$C0"; first=0
    else printf '%s  %s%s%s\n' "$pad" "$style" "$line" "$C0"; fi
  done < <(printf '%s\n' "$text" | fold -s -w "$width")
}

say()   { printf '%s\n' "$*"; }
ok()    { emit '    ' "$CG$S_OK$C0" "" "$*"; }
warn()  { emit '    ' "$CY$S_WARN$C0" "" "$*"; }
err()   { emit '    ' "$CR$S_ERR$C0" "" "$*" >&2; }
info()  { emit '    ' "$CA$S_DOT$C0" "" "$*"; }
note()  { emit '      ' "$CM$S_NOTE$C0" "$CM$CI" "$*"; }
dim()   { emit '    ' "" "$CM" "$*"; }
die()   { printf '\n'; err "$*"; [[ -n "${CM_LOG:-}" ]] && dim "Install log: $CM_LOG"; printf '\n'; exit 1; }

banner() {
  local sub="$1" w=52 l1 l2
  if [[ "$(locale charmap 2>/dev/null)" == "UTF-8" ]]; then
    l1="▄▀▄ █▀▄ ▀█▀ █|█▄ ▄█ █ █ ▀▄▀"
    l2="▀▄▀ █▀   █  █|█ ▀ █ ▀▄▀ ▄▀▄"
  else
    l1="Opti|mux"; l2="|"
  fi
  printf '\n  %s%s%s\n' "$CA" "$B_TL$(rep "$B_H" $w)$B_TR" "$C0"
  local l code mux plain
  for l in "$l1" "$l2"; do
    code="${l%%|*}"; mux="${l#*|}"
    plain="   $code $mux"
    printf '  %s%s%s   %s%s%s %s%s%s%s%s%s%s\n' "$CA" "$B_V" "$C0" "$CW$CB" "$code" "$C0" "$CA$CB" "$mux" "$C0" \
      "$(rep ' ' $((w - ${#plain})))" "$CA" "$B_V" "$C0"
  done
  plain="   $sub"
  printf '  %s%s%s%s%s%s%s%s%s\n' "$CA" "$B_V" "$C0" "$CM" "$plain" "$C0" "$(rep ' ' $((w - ${#plain})))" "$CA$B_V" "$C0"
  printf '  %s%s%s\n\n' "$CA" "$B_BL$(rep "$B_H" $w)$B_BR" "$C0"
}

rep() { local out="" i; for ((i = 0; i < $2; i++)); do out+="$1"; done; printf '%s' "$out"; }

CM_STEP=0
step() {
  CM_STEP=$((CM_STEP + 1))
  printf '\n  %s%s%s %sStep %d of %d%s  %s%s%s\n\n' "$CA" "$S_STEP" "$C0" "$CM" "$CM_STEP" "$CM_STEPS" "$C0" "$CB" "$1" "$C0"
}

section() { printf '\n  %s%s%s %s%s%s\n\n' "$CA" "$S_STEP" "$C0" "$CB" "$1" "$C0"; }

# A box with a title and lines (lines may contain colours)
card() {
  local title="$1"; shift
  local w=0 l n
  for l in "$title" "$@"; do n=$(vislen "$l"); ((n > w)) && w=$n; done
  w=$((w + 4))
  printf '\n  %s%s%s %s%s%s %s%s%s\n' "$CA" "$B_TL$B_H" "$C0" "$CB" "$title" "$C0" "$CA" "$(rep "$B_H" $((w - $(vislen "$title") - 3)))$B_TR" "$C0"
  for l in "$@"; do
    n=$(vislen "$l")
    printf '  %s%s%s  %s%s%s%s\n' "$CA" "$B_V" "$C0" "$l" "$(rep ' ' $((w - n - 2)))" "$CA$B_V" "$C0"
  done
  printf '  %s%s%s\n\n' "$CA" "$B_BL$(rep "$B_H" "$w")$B_BR" "$C0"
}

# run "Title" command args...   -> spinner while it runs; output goes to the log
# A failure stops the installer, unless CM_SOFT=1 (then it returns 1: for optional steps)
run() {
  local title="$1"; shift
  local start=$SECONDS
  # A sudo password must be asked now, never behind the spinner
  if [[ -n "$CM_SUDO" && " $* " == *" sudo "* ]]; then sudo_ready || die "\"$title\" needs sudo, which isn't available without a password here."; fi
  printf '\n### %s\n### $ %s\n' "$title" "$*" >>"$CM_LOG"
  if [[ -z "$CM_FANCY" ]]; then
    printf '    %s ...\n' "$title"
  fi
  "$@" >>"$CM_LOG" 2>&1 </dev/null 3<&- &
  local pid=$! i=0
  CM_RUN_PID=$pid
  if [[ -z "$CM_FANCY" ]]; then
    if wait "$pid"; then CM_RUN_PID=""; ok "$title"; return 0; fi
    CM_RUN_PID=""
  else
    cursor_hide
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r    %s%s%s %s %s(%ds)%s\e[K' "$CA" "${SPIN[i % ${#SPIN[@]}]}" "$C0" "$title" "$CM" $((SECONDS - start)) "$C0"
      i=$((i + 1)); sleep 0.1
    done
    cursor_show
    if wait "$pid"; then
      CM_RUN_PID=""
      printf '\r    %s%s%s %s %s(%ds)%s\e[K\n' "$CG" "$S_OK" "$C0" "$title" "$CM" $((SECONDS - start)) "$C0"
      return 0
    fi
    CM_RUN_PID=""
    printf '\r    %s%s%s %s\e[K\n' "$CR" "$S_ERR" "$C0" "$title"
  fi
  printf '\n'
  tail -n 15 "$CM_LOG" | sed "s/^/      ${CM}│${C0} /"
  [[ -n "${CM_SOFT:-}" ]] && return 1
  die "\"$title\" failed. The lines above are the end of the log."
}

# sudo without a hidden prompt: already allowed (cached, or NOPASSWD), or ask on the terminal
sudo_ready() {
  [[ -z "$CM_SUDO" ]] && return 0
  command -v sudo >/dev/null 2>&1 || return 1
  sudo -n true 2>/dev/null && return 0
  [[ -n "$CM_TTY" ]] || return 1
  sudo -v <&3
}

need_tty() { [[ -n "$CM_TTY" ]] || die "This needs an answer, but there is no terminal. Re-run with --yes (and --local or --server)."; }

# confirm "Question" [Y|N]  -> 0 for yes
confirm() {
  local q="$1" def="${2:-Y}" ans hint
  if [[ -n "$CM_YES" ]]; then [[ "$def" == "Y" ]]; return; fi
  need_tty
  [[ "$def" == "Y" ]] && hint="Y/n" || hint="y/N"
  while true; do
    printf '    %s?%s %s %s(%s)%s %s›%s ' "$CA" "$C0" "$q" "$CM" "$hint" "$C0" "$CA" "$C0"
    IFS= read -r ans <&3 || ans=""
    case "${ans,,}" in
      "") [[ "$def" == "Y" ]]; return ;;
      y|yes) return 0 ;;
      n|no) return 1 ;;
    esac
  done
}

# ask "Question" "default"  -> REPLY
ask() {
  local q="$1" def="${2:-}"
  if [[ -n "$CM_YES" ]]; then REPLY="$def"; return; fi
  need_tty
  printf '    %s?%s %s' "$CA" "$C0" "$q"
  [[ -n "$def" ]] && printf ' %s(%s)%s' "$CM" "$def" "$C0"
  printf ' %s›%s ' "$CA" "$C0"
  IFS= read -r REPLY <&3 || REPLY=""
  [[ -z "$REPLY" ]] && REPLY="$def"
  return 0
}

# ask_secret "Question" -> REPLY (nothing is echoed)
ask_secret() {
  need_tty
  printf '    %s?%s %s %s›%s ' "$CA" "$C0" "$1" "$CA" "$C0"
  IFS= read -rs REPLY <&3 || REPLY=""
  # A fixed mark, so the length of a password or key isn't shown either
  if [[ -n "$REPLY" ]]; then printf '%s%s%s\n' "$CM" "$(rep '•' 8)" "$C0"; else printf '\n'; fi
}

# choose "Question" "Option 1" "Option 2" ... -> CHOICE (0-based). Arrow keys or numbers.
choose() {
  local q="$1"; shift
  local opts=("$@") n=$# sel=0 key rest i
  if [[ -n "$CM_YES" ]]; then CHOICE=0; return; fi
  need_tty
  printf '    %s?%s %s\n' "$CA" "$C0" "$q"
  if [[ -z "$CM_FANCY" ]]; then
    for i in "${!opts[@]}"; do printf '      %d) %s\n' $((i + 1)) "${opts[i]}"; done
    while true; do
      printf '    Number [1-%d] › ' "$n"; IFS= read -r key <&3 || key=1
      [[ -z "$key" ]] && key=1
      if [[ "$key" =~ ^[0-9]+$ ]] && ((key >= 1 && key <= n)); then CHOICE=$((key - 1)); return; fi
    done
  fi
  cursor_hide
  local first=1
  while true; do
    ((first)) || printf '\e[%dA' "$n"
    first=0
    for i in "${!opts[@]}"; do
      if ((i == sel)); then printf '\e[K      %s%s %s%s\n' "$CA$CB" "$S_ARROW" "${opts[i]}" "$C0"
      else printf '\e[K        %s%s%s\n' "$CM" "${opts[i]}" "$C0"; fi
    done
    IFS= read -rsn1 key <&3 || key=""
    if [[ "$key" == $'\e' ]]; then IFS= read -rsn2 -t 0.05 rest <&3 || rest=""; key+="$rest"; fi
    case "$key" in
      $'\e[A'|k) sel=$(((sel - 1 + n) % n)) ;;
      $'\e[B'|j) sel=$(((sel + 1) % n)) ;;
      [1-9]) ((key <= n)) && sel=$((key - 1)) ;;
      "") break ;;
    esac
  done
  cursor_show
  # Collapse the menu to the chosen line
  printf '\e[%dA' "$n"
  for ((i = 0; i < n; i++)); do printf '\e[K\n'; done
  printf '\e[%dA' "$n"
  printf '\e[K      %s%s%s %s\n' "$CG" "$S_OK" "$C0" "${opts[sel]}"
  CHOICE=$sel
}

# ============================================================================
#  System checks
# ============================================================================

detect_system() {
  CM_OS="$(uname -s)"
  CM_PM=""
  for pm in apt-get dnf pacman zypper brew; do
    if command -v "$pm" >/dev/null 2>&1; then CM_PM="$pm"; break; fi
  done
  CM_DISTRO="$CM_OS"
  if [[ -r /etc/os-release ]]; then CM_DISTRO="$(. /etc/os-release && echo "${PRETTY_NAME:-$NAME}")"; fi
  [[ "$CM_OS" == "Darwin" ]] && CM_DISTRO="macOS $(sw_vers -productVersion 2>/dev/null)"
  if [[ $EUID -eq 0 ]]; then CM_SUDO=""; else CM_SUDO="sudo"; fi
  CM_SYSTEMD=""
  if [[ -d /run/systemd/system ]]; then CM_SYSTEMD=1; fi
}

find_python() {
  CM_PY=""
  local c
  for c in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
      if "$c" -c "import venv, ensurepip" 2>/dev/null; then CM_PY="$(command -v "$c")"; return 0; fi
      CM_PY_NOVENV="$c"
    fi
  done
  return 1
}

# Packages to install for what's missing (by package manager)
packages_for() {
  local want=("$@") out=() w
  for w in "${want[@]}"; do
    case "$CM_PM:$w" in
      apt-get:python) out+=(python3 python3-venv) ;;
      dnf:python|zypper:python) out+=(python3) ;;
      pacman:python|brew:python) out+=(python) ;;
      *:git) out+=(git) ;;
      *:curl) out+=(curl ca-certificates) ;;
      brew:bubblewrap) ;;
      *:bubblewrap) out+=(bubblewrap) ;;
      *:caddy) out+=(caddy) ;;
      apt-get:ufw|pacman:ufw) out+=(ufw) ;;
    esac
  done
  printf '%s\n' "${out[@]}" | awk 'NF && !seen[$0]++'
}

pm_install() {
  case "$CM_PM" in
    apt-get) $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -q && $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q "$@" ;;
    dnf) $CM_SUDO dnf install -y "$@" ;;
    pacman) $CM_SUDO pacman -S --noconfirm --needed "$@" ;;
    zypper) $CM_SUDO zypper --non-interactive install "$@" ;;
    brew) brew install "$@" ;;
    *) return 1 ;;
  esac
}

check_system() {
  local missing=() p
  find_python || true
  if [[ -n "$CM_PY" ]]; then ok "Python $("$CM_PY" -c 'import platform; print(platform.python_version())') with venv"
  elif [[ -n "${CM_PY_NOVENV:-}" ]]; then warn "Python found, but its venv module is missing"; missing+=(python)
  else warn "Python $CM_MIN_PY or newer: not found"; missing+=(python); fi
  for p in git curl; do
    if command -v "$p" >/dev/null 2>&1; then ok "$p"; else warn "$p: not found"; missing+=("$p"); fi
  done
  if [[ "$CM_OS" == "Linux" ]]; then
    if command -v bwrap >/dev/null 2>&1; then ok "bubblewrap (the sandbox)"; else warn "bubblewrap (the sandbox): not found"; missing+=(bubblewrap); fi
  else
    warn "No sandbox on $CM_OS: commands run with your user's permissions (you approve each one)"
  fi
  CM_MISSING=("${missing[@]}")
}

install_missing() {
  local extra=("$@") pkgs
  mapfile -t pkgs < <(packages_for "${CM_MISSING[@]}" "${extra[@]}")
  [[ ${#pkgs[@]} -eq 0 ]] && return 0
  [[ -z "$CM_PM" ]] && die "Please install: ${pkgs[*]} (no supported package manager found)."
  printf '\n'
  info "To install: ${CB}${pkgs[*]}${C0}"
  if [[ -n "$CM_SUDO" ]]; then
    command -v sudo >/dev/null 2>&1 || die "Missing packages need root, and sudo isn't available. Install them, then run this again: ${pkgs[*]}"
    note "This uses sudo; you may be asked for your password."
  fi
  if ! confirm "Install them now?" Y; then
    local required=0 m
    for m in "${CM_MISSING[@]}"; do [[ "$m" != bubblewrap ]] && required=1; done
    ((required)) && die "Optimux needs these to run. Install them, then run this again: ${pkgs[*]}"
    warn "Continuing without the sandbox."
    return 0
  fi
  sudo_ready || die "Installing packages needs sudo. Install them yourself, then run this again: ${pkgs[*]}"
  run "Installing ${pkgs[*]}" pm_install "${pkgs[@]}"
  find_python || die "Python $CM_MIN_PY+ with venv is still missing after installing. Install it, then run this again."
}

port_free() { ! (exec 9<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null; }

# ============================================================================
#  Install manifest (what was installed where; read by the `optimux` command)
# ============================================================================

CM_ETC="${OPTIMUX_ETC:-/etc/optimux}"
manifest_path() {   # manifest_path server|local [name]
  local name="${2:-optimux}"
  if [[ "${1:-}" == server ]]; then echo "$CM_ETC/install.env"
  else echo "${XDG_CONFIG_HOME:-$HOME/.config}/$name/install.env"; fi
}
# The name this install's service, user and server folders use
nm() { echo "${M[NAME]:-optimux}"; }

manifest_write() {
  local f="$1" tmp
  tmp="$(mktemp)"
  {
    echo "# Written by the Optimux installer. Read by the optimux command."
    for k in MODE NAME APP_DIR DATA_DIR WORKSPACE PORT URL SERVICE RUN_USER DOMAIN REF REPO APPARMOR INSTALLED; do
      [[ "${M[$k]:-}" == *$'\n'* ]] && die "Unexpected newline in $k."
      printf '%s=%s\n' "$k" "${M[$k]:-}"      # raw value; read back by splitting at the first =
    done
  } >"$tmp"
  if [[ "${M[MODE]}" == server ]]; then
    $CM_SUDO install -d -m 755 "$(dirname "$f")"      # readable, so sudo users and `optimux` find the install
    $CM_SUDO install -m 644 "$tmp" "$f"
  else
    mkdir -p "$(dirname "$f")"; install -m 600 "$tmp" "$f"
  fi
  rm -f "$tmp"
}

# Parse KEY=value lines without running them.
manifest_read() {
  local f="$1" line k
  declare -gA M=()
  [[ -r "$f" ]] || return 1
  while IFS= read -r line; do
    [[ "$line" == *=* ]] || continue
    k="${line%%=*}"
    [[ "$k" =~ ^[A-Z_]+$ ]] || continue
    M[$k]="${line#*=}"
  done <"$f"
  M[NAME]="${M[NAME]:-optimux}"
  # A damaged manifest is treated as no install (with a note), never trusted
  if [[ "${M[MODE]:-}" != local && "${M[MODE]:-}" != server ]] || [[ "${M[APP_DIR]:-}" != /* || "${M[DATA_DIR]:-}" != /* ]] \
     || [[ "${M[SERVICE]:-}" != systemd && "${M[SERVICE]:-}" != systemd-user && "${M[SERVICE]:-}" != process ]] \
     || [[ "${M[NAME]}" != optimux ]]; then
    warn "Ignoring $f: it is incomplete or damaged."
    declare -gA M=()
    return 1
  fi
  return 0
}

find_install() {
  if manifest_read "$(manifest_path server)"; then return 0; fi
  manifest_read "$(manifest_path local)"
}

# ============================================================================
#  Running Optimux's own setup (python -m optimux_backend.admin)
# ============================================================================

admin() {
  if [[ "${M[MODE]}" == server ]]; then
    ( cd "${M[APP_DIR]}" && $CM_SUDO runuser -u "${M[RUN_USER]}" -- env HOME="/var/lib/$(nm)" OPTIMUX_HOME="${M[DATA_DIR]}" \
        OPTIMUX_SERVER=1 "${M[APP_DIR]}/.venv/bin/python" -m optimux_backend.admin "$@" )
  else
    ( cd "${M[APP_DIR]}" && OPTIMUX_HOME="${M[DATA_DIR]}" "${M[APP_DIR]}/.venv/bin/python" -m optimux_backend.admin "$@" )
  fi
}

password_is_set() { [[ "$(admin status 2>/dev/null)" == *'"password_set": true'* ]]; }

# Ask for a password twice and set it (nothing echoed or logged)
set_password_interactive() {
  local p1 p2
  while true; do
    ask_secret "Choose a password (12 characters or more)"; p1="$REPLY"
    [[ -z "$p1" ]] && { warn "Skipped: the sign-in page will ask for the access token instead."; return 1; }
    if ((${#p1} < 12)); then warn "Too short. A few random words work well, e.g. 'violet tractor mango river'."; continue; fi
    ask_secret "Repeat it"; p2="$REPLY"
    [[ "$p1" == "$p2" ]] || { warn "They differ; try again."; continue; }
    if printf '%s\n' "$p1" | admin set-password >/dev/null 2>>"$CM_LOG"; then ok "Password set"; return 0; fi
    warn "That password was refused; try another."
  done
}

configure_provider() {
  local labels=() p i
  for p in "${CM_PROVIDERS[@]}"; do labels+=("${p%%|*}"); done
  labels+=("Skip: I'll add models in the Dashboard")
  choose "Which AI provider do you want to start with?" "${labels[@]}"
  if ((CHOICE == ${#CM_PROVIDERS[@]})); then
    note "Add models and keys any time in Dashboard → Models, and pick who does what in Dashboard → Routing."
    return 0
  fi
  local label envkey small big small_name big_name url
  IFS='|' read -r label envkey small big small_name big_name url <<<"${CM_PROVIDERS[CHOICE]}"
  note "Get a key at $url"
  ask_secret "Paste your $label API key (input is hidden)"
  local key="$REPLY"
  if [[ -z "$key" ]]; then
    warn "No key given; add it later in Dashboard → Models."
    return 0
  fi
  printf '%s\n' "$key" | admin set-key "$envkey" >/dev/null 2>>"$CM_LOG" || { warn "The key was refused; add it later in Dashboard → Models."; return 0; }
  key=""
  admin add-model "$small" "$small_name" "$envkey" >>"$CM_LOG" 2>&1 || true
  admin add-model "$big" "$big_name" "$envkey" >>"$CM_LOG" 2>&1 || true
  if admin set-routing "$small" "$small" "$big" >>"$CM_LOG" 2>&1; then
    ok "$label key saved (readable only by Optimux)"
    ok "Routing: Conductor and Simple → $small_name, Complex → $big_name"
  fi
  if confirm "Test the key now? (one tiny call, a fraction of a cent)" Y; then
    local msg
    if msg="$(admin test-model "$small" 2>&1)"; then ok "$small_name answered"
    else warn "Test failed: $(printf '%s' "$msg" | tail -n 1)"; note "Check the key in Dashboard → Models → Test."; fi
  fi
  note "Change models, prices and routing later in Dashboard → Models and Routing."
}

# ---- GitHub (Feature 25): the GitHub CLI, and a token so Optimux can create repositories

gh_package() {
  case "$CM_PM" in
    pacman) echo github-cli ;;
    apt-get|dnf|zypper|brew) echo gh ;;
    *) echo "" ;;
  esac
}

# GitHub's own apt repository, for Debian/Ubuntu releases that don't ship gh.
# Every step must work (run's `||` turns set -e off here), and a half-written
# source is removed again, so a failure never leaves apt broken.
GH_LIST=/etc/apt/sources.list.d/github-cli.list
GH_KEYRING=/etc/apt/keyrings/githubcli-archive-keyring.gpg
add_gh_repo() {
  local key arch
  key="$(mktemp)"
  if ! curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o "$key" || [[ ! -s "$key" ]]; then
    rm -f "$key"; echo "Couldn't download GitHub's signing key." >&2; return 1
  fi
  arch="$(dpkg --print-architecture)" || { rm -f "$key"; return 1; }
  if ! { $CM_SUDO install -d -m 755 /etc/apt/keyrings &&
         $CM_SUDO install -m 644 "$key" "$GH_KEYRING" &&
         echo "deb [arch=$arch signed-by=$GH_KEYRING] https://cli.github.com/packages stable main" | $CM_SUDO tee "$GH_LIST" >/dev/null &&
         $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -q; }; then
    rm -f "$key"
    $CM_SUDO rm -f "$GH_LIST" "$GH_KEYRING"
    echo "Removed the half-added GitHub repository again." >&2
    return 1
  fi
  rm -f "$key"
}

# Install gh if it's missing. Optional: a failure is reported, never fatal.
install_gh() {
  if command -v gh >/dev/null 2>&1; then ok "GitHub CLI: $(gh --version 2>/dev/null | head -n 1 || true)"; return 0; fi
  # Packages need root even for a local install (`optimux github` runs without sudo otherwise);
  # local, so everything called from here sees it
  local CM_SUDO="$CM_SUDO"
  [[ $EUID -ne 0 && "$CM_PM" != brew ]] && CM_SUDO=sudo
  local pkg
  pkg="$(gh_package)"
  if [[ -z "$pkg" ]]; then warn "Install the GitHub CLI yourself: https://cli.github.com"; return 1; fi
  if [[ "$CM_PM" != brew ]] && ! sudo_ready; then warn "Installing the GitHub CLI needs sudo; install it later: https://cli.github.com"; return 1; fi
  if [[ "$CM_PM" == apt-get ]]; then
    $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -q >>"$CM_LOG" 2>&1 || true
    # Captured, not piped into grep -q: that ends the pipe early, and with pipefail
    # apt-cache's SIGPIPE would read as "no candidate"
    local policy
    policy="$(apt-cache policy gh 2>/dev/null || true)"
    if [[ "$policy" != *"Candidate: "[0-9]* ]]; then
      # A third-party package source is added only when someone says yes to it
      if [[ -n "${CM_YES:-}" ]]; then
        warn "This system's packages don't include gh; install it later: https://cli.github.com"; return 1
      fi
      note "This system's packages don't include gh. GitHub publishes it at cli.github.com."
      confirm "Add GitHub's package repository to install it?" Y || { note "Install it later: https://cli.github.com"; return 1; }
      CM_SOFT=1 run "Adding GitHub's package repository (cli.github.com)" add_gh_repo || { warn "Couldn't add it; install gh later: https://cli.github.com"; return 1; }
    fi
  fi
  CM_SOFT=1 run "Installing the GitHub CLI (gh)" pm_install "$pkg" || { warn "Couldn't install gh; install it later: https://cli.github.com"; return 1; }
}

# value of a "key": "text" pair in the admin command's JSON output
json_str() { sed -n "s/.*\"$1\": \"\([^\"]*\)\".*/\1/p" "$2" | head -n 1; }

# Ask for a token, have Optimux check it with GitHub and save it (never echoed or logged)
configure_github() {
  note "Optimux can also create a GitHub repository for each new project."
  note "That needs the GitHub CLI (gh) and a token from your GitHub account."
  if [[ -n "${CM_YES:-}" ]]; then
    install_gh || true
    note "Add a token later in Dashboard → GitHub (or run: optimux github)."
    return 0
  fi
  if ! confirm "Set up GitHub now?" Y; then
    note "No problem: set it up later in Dashboard → GitHub (or run: optimux github)."
    return 0
  fi
  install_gh || { note "Then add a token in Dashboard → GitHub."; return 0; }
  note "Create a token at https://github.com/settings/personal-access-tokens/new"
  note "Repository access: All repositories. Permissions: Administration and Contents, Read and write."
  note "A classic token with the 'repo' scope works too."
  local key out login warning hint
  out="$(mktemp)"
  while true; do
    ask_secret "Paste the GitHub token (empty to skip)"
    key="$REPLY"
    if [[ -z "$key" ]]; then note "Skipped: add it later in Dashboard → GitHub."; break; fi
    info "Checking it with GitHub…"
    if printf '%s\n' "$key" | admin set-github >"$out" 2>&1; then
      key=""
      login="$(json_str login "$out")"
      warning="$(json_str warning "$out")"
      hint="$(json_str hint "$out")"
      ok "GitHub: signed in as @${login} (the token is readable only by Optimux)"
      CM_GH_SAVED=1
      [[ -n "$warning" ]] && warn "$warning"
      [[ -n "$hint" ]] && note "$hint"
      [[ "$(cat "$out")" == *'"identity_set": true'* ]] && info "Commits will be made as @${login} with GitHub's private address; change it in Dashboard → GitHub."
      break
    fi
    key=""
    warn "$(tail -n 1 "$out")"
    confirm "Try another token?" Y || { note "Add one later in Dashboard → GitHub."; break; }
  done
  rm -f "$out"
  note "Change it any time in Dashboard → GitHub."
}

cli_github() {
  CM_LOG="${CM_LOG:-$(new_log)}"
  section "GitHub"
  CM_YES=""
  configure_github
  # A running Optimux reads secrets.env when it starts
  if [[ -n "${CM_GH_SAVED:-}" ]] && svc is-active >/dev/null 2>&1 && confirm "Restart Optimux now so it uses the token?" Y; then cli_restart; fi
}

configure_sandbox() {
  local st
  st="$(admin sandbox-check 2>>"$CM_LOG" || true)"
  if [[ "$st" == *'"works": true'* ]]; then
    if [[ "${M[MODE]}" == server ]]; then ok "Sandbox works (always on in server mode)"; return 0; fi
    note "With the sandbox, commands can change only the active project."
    if confirm "Turn on the sandbox?" Y; then
      admin sandbox on >>"$CM_LOG" 2>&1 && ok "Sandbox on"
    else
      note "Turn it on later in Dashboard → Execution → Sandbox."
    fi
    return 0
  fi
  return 1
}

# Ubuntu 23.10+ blocks the user namespaces bubblewrap needs; allow them for bwrap only
fix_apparmor() {
  [[ "$(cat /proc/sys/kernel/apparmor_restrict_unprivileged_userns 2>/dev/null)" == 1 ]] || return 1
  command -v apparmor_parser >/dev/null 2>&1 || return 1
  warn "AppArmor blocks the sandbox on this system (Ubuntu 23.10+)."
  note "The fix lets bubblewrap use user namespaces (deploy/apparmor-bwrap); it applies to every user of this machine."
  confirm "Apply the fix?" Y || return 1
  sudo_ready || { warn "Applying it needs sudo."; return 1; }
  [[ -f /etc/apparmor.d/bwrap ]] || M[APPARMOR]=1      # ours to remove on uninstall
  run "Allowing bubblewrap in AppArmor" bash -c "$CM_SUDO install -m 644 '${M[APP_DIR]}/deploy/apparmor-bwrap' /etc/apparmor.d/bwrap && $CM_SUDO apparmor_parser -r /etc/apparmor.d/bwrap"
}

# ============================================================================
#  Getting the code
# ============================================================================

fetch_code() {
  local dir="$1" as_root="${2:-}"
  local g="git"; [[ -n "$as_root" ]] && g="$CM_SUDO git"
  if [[ -d "$dir/.git" ]]; then
    local origin
    origin="$($g -C "$dir" remote get-url origin 2>/dev/null || true)"
    [[ "$origin" == "$CM_REPO" ]] || die "$dir is another git repository (origin: ${origin:-none}); not touching it. Choose another folder with --dir."
    run "Downloading Optimux ($CM_REF)" $g -C "$dir" fetch --depth 1 origin "$CM_REF"
    run "Switching to the new version" $g -C "$dir" reset --hard FETCH_HEAD
  else
    if [[ -e "$dir" ]] && [[ -n "$(ls -A "$dir" 2>/dev/null)" ]]; then die "$dir already exists and isn't empty; choose another folder with --dir."; fi
    run "Downloading Optimux ($CM_REF)" $g clone --depth 1 --branch "$CM_REF" "$CM_REPO" "$dir"
  fi
}

make_venv() {
  local dir="$1" as_root="${2:-}" s=""
  [[ -n "$as_root" ]] && s="$CM_SUDO"
  [[ -x "$dir/.venv/bin/python" ]] || run "Creating its virtual environment" $s "$CM_PY" -m venv "$dir/.venv"
  run "Installing Python packages (about a minute)" $s "$dir/.venv/bin/pip" install --quiet --disable-pip-version-check -r "$dir/requirements.txt"
}

install_cli() {
  local target="$1" wrapper
  wrapper="$(mktemp)"
  {
    echo '#!/usr/bin/env bash'
    echo '# The optimux command (installed by install.sh)'
    printf 'exec bash %q "$@"\n' "${M[APP_DIR]}/scripts/optimux"
  } >"$wrapper"
  if [[ "${M[MODE]}" == server ]]; then $CM_SUDO install -D -m 755 "$wrapper" "$target"
  else mkdir -p "$(dirname "$target")"; install -m 755 "$wrapper" "$target"; fi
  rm -f "$wrapper"
}

wait_http() {   # wait_http URL [unix-socket] [seconds]
  local url="$1" sock="${2:-}" t="${3:-30}" deadline
  deadline=$((SECONDS + t))
  while ((SECONDS < deadline)); do
    if [[ -n "$sock" ]]; then $CM_SUDO curl -fsS --max-time 2 --unix-socket "$sock" "$url" >/dev/null 2>&1 && return 0
    else curl -fsS --max-time 2 "$url" >/dev/null 2>&1 && return 0; fi
    sleep 0.5
  done
  return 1
}

# ============================================================================
#  This computer
# ============================================================================

user_unit_path() { echo "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/${1:-optimux}.service"; }

write_user_unit() {
  local unit
  unit="$(user_unit_path)"
  mkdir -p "$(dirname "$unit")"
  cat >"$unit" <<EOF
[Unit]
Description=Optimux (installed by install.sh)

[Service]
Environment=OPTIMUX_HOME=${M[DATA_DIR]}
WorkingDirectory=${M[APP_DIR]}
Environment=OPTIMUX_SUPERVISED=1
ExecStart=${M[APP_DIR]}/.venv/bin/uvicorn optimux_backend.main:app --host 127.0.0.1 --port ${M[PORT]} --no-proxy-headers --no-server-header
Restart=on-failure
RestartForceExitStatus=75
UMask=0077

[Install]
WantedBy=default.target
EOF
}

install_local() {
  CM_STEPS=6
  declare -gA M=([MODE]=local [NAME]=optimux [REPO]="$CM_REPO" [REF]="$CM_REF")
  M[APP_DIR]="${CM_DIR:-$HOME/.local/share/optimux}"
  M[DATA_DIR]="${OPTIMUX_HOME:-$HOME/.optimux}"

  step "Checking your system"
  info "$CM_DISTRO"
  check_system
  install_missing

  step "Getting Optimux"
  info "Program: ${CB}${M[APP_DIR]}${C0}"
  info "Your data (keys, chats, memory): ${CB}${M[DATA_DIR]}${C0}"
  mkdir -p "$(dirname "${M[APP_DIR]}")"
  fetch_code "${M[APP_DIR]}"
  make_venv "${M[APP_DIR]}"
  run "Preparing your data folder (private to you)" admin init

  step "Signing in"
  note "Other programs and users on this computer can't use Optimux without it."
  if confirm "Set your sign-in password now?" Y && [[ -z "$CM_YES" ]]; then
    set_password_interactive && CM_PW_SET=1 || true
  else
    note "No problem: the first visit asks for the access token and lets you choose one there."
  fi

  step "Your projects and models"
  note "Everything here can be changed later in the web interface; skip anything you're unsure about."
  local default_ws="$HOME/optimux-projects"
  [[ -d "$HOME/git" ]] && default_ws="$HOME/git"
  [[ -n "${CM_WORKSPACE:-}" ]] && default_ws="$CM_WORKSPACE"
  info "The Workspace folder holds your projects: every folder inside it is a project."
  local ws=""
  while [[ -z "$ws" ]]; do
    ask "Workspace folder" "$default_ws"
    ws="${REPLY/#\~/$HOME}"
    [[ "$ws" == /* ]] || ws="$HOME/$ws"                   # relative to your home folder
    ws="$(realpath -m -- "$ws" 2>/dev/null || echo "$ws")"
    case "$ws/" in
      "${M[APP_DIR]}/"*|"${M[DATA_DIR]}/"*) warn "Not inside Optimux's own folders; choose another."; ws=""; [[ -n "$CM_YES" ]] && ws="$HOME/optimux-projects" ;;
    esac
  done
  if admin set-workspace "$ws" --create >>"$CM_LOG" 2>&1; then ok "Workspace: $ws"; M[WORKSPACE]="$ws"
  else warn "Couldn't use $ws; set it in Dashboard → Scope."; fi
  note "Change it in Dashboard → Scope."
  printf '\n'
  [[ -n "$CM_YES" ]] || configure_provider
  printf '\n'
  configure_github
  printf '\n'
  if [[ "$CM_OS" == "Linux" ]]; then
    configure_sandbox || { fix_apparmor && configure_sandbox; } || warn "The sandbox doesn't work here; commands will run with your permissions. See Dashboard → Execution."
  fi

  step "Running Optimux"
  local port="${CM_PORT:-${CM_OLD_PORT:-8000}}"
  while ! port_free "$port"; do port=$((port + 1)); done
  if [[ "$port" != "${CM_PORT:-${CM_OLD_PORT:-8000}}" ]]; then info "Port ${CM_PORT:-${CM_OLD_PORT:-8000}} is busy; using $port."; fi
  M[PORT]="$port"
  M[URL]="http://127.0.0.1:$port"
  M[SERVICE]="process"
  if [[ "$CM_OS" == "Linux" ]] && systemctl --user show-environment >/dev/null 2>&1; then
    if confirm "Start Optimux automatically when you log in?" Y; then M[SERVICE]="systemd-user"; fi
  fi
  if [[ "${M[SERVICE]}" == systemd-user ]]; then
    write_user_unit
    run "Registering the Optimux service" systemctl --user daemon-reload
    run "Enabling it at login" systemctl --user enable optimux.service
  fi
  M[INSTALLED]="$(date +%F)"
  manifest_write "$(manifest_path local)"
  install_cli "$HOME/.local/bin/optimux"
  ok "The ${CB}optimux${C0} command is installed in ~/.local/bin"
  path_hint

  step "Starting"
  if [[ -n "$CM_NO_START" ]]; then info "Not started (--no-start). Run: optimux start"
  else
    cli_start >/dev/null 2>&1 || true
    if wait_http "${M[URL]}/auth/status" "" 30; then ok "Optimux is running at ${CB}${M[URL]}${C0}"
    else warn "It didn't answer yet. Check with: optimux status   (logs: optimux logs)"; fi
  fi
  finish_local
}

# ~/.local/bin must be in PATH for `optimux` to work in new terminals
path_hint() {
  CM_PATH_LATER=""
  case ":$PATH:" in *":$HOME/.local/bin:"*) return 0 ;; esac
  # This terminal can't find optimux by name yet: an installer started from it (curl | bash)
  # can't change its PATH. finish_local says so in the final card.
  CM_PATH_LATER=1
  local rc="$HOME/.bashrc"
  [[ "${SHELL:-}" == */zsh ]] && rc="$HOME/.zshrc"
  if grep -qsF 'export PATH="$HOME/.local/bin:$PATH"' "$rc"; then return 0; fi    # added by an earlier install
  # ~/.profile (Ubuntu, Debian) adds ~/.local/bin only at login, and only if the folder existed
  # then: new terminals need the line in the shell's rc file to find optimux before a new login.
  local def=Y; if [[ -n "${CM_YES:-}" ]]; then def=N; fi     # unattended: don't edit shell files unasked
  note "New terminals find 'optimux' only after you log in again, unless ~/.local/bin is added to $(basename "$rc")."
  if confirm "Add ~/.local/bin to $(basename "$rc")?" "$def"; then
    printf '\n# Added by the Optimux installer\nexport PATH="$HOME/.local/bin:$PATH"\n' >>"$rc"
    ok "Added to $rc: new terminals find optimux"
  else
    note "Then run it as ~/.local/bin/optimux until you log in again."
  fi
}

finish_local() {
  local signin commands_note=()
  if [[ -n "${CM_PATH_LATER:-}" ]]; then
    # shellcheck disable=SC2016  # shown literally, for the user to paste
    commands_note=("           ${CY}This terminal doesn't know the command yet. Run first:${C0}"
                   '           export PATH="$HOME/.local/bin:$PATH"')
  fi
  if [[ -n "${CM_PW_SET:-}" ]]; then signin="Sign in with the password you just chose."
  elif password_is_set; then signin="Sign in with your password."
  else signin="First visit: paste the access token ($(admin token 2>/dev/null)), then choose a password."; fi
  card "Optimux is installed" \
    "${CB}Open${C0}       ${CA}${M[URL]}${C0}" \
    "           $signin" \
    "" \
    "${CB}Commands${C0}   optimux start | stop | status | logs | open" \
    "           optimux update | password | uninstall" \
    "${commands_note[@]}" \
    "" \
    "${CB}Files${C0}      program  ${M[APP_DIR]}" \
    "           data     ${M[DATA_DIR]}  ${CM}(keys, chats, memory)${C0}" \
    "" \
    "${CB}Next${C0}       Dashboard → Guide explains everything." \
    "           Turn on two-step codes in Dashboard → System."
  if [[ -z "$CM_YES" && -z "$CM_NO_START" && -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && command -v xdg-open >/dev/null 2>&1; then
    confirm "Open it in your browser now?" Y && (xdg-open "${M[URL]}" >/dev/null 2>&1 &)
  elif [[ "$CM_OS" == Darwin && -z "$CM_YES" && -z "$CM_NO_START" ]]; then
    confirm "Open it in your browser now?" Y && open "${M[URL]}"
  fi
  dim "Install log: $CM_LOG"
  printf '\n'
}

# ============================================================================
#  A server (VPS)
# ============================================================================

# deploy/optimux.service with this install's name in its paths, user and socket
server_unit() {
  local n="$1"
  sed -e "s#/optimux#/$n#g" -e "s#optimux\.env#$n.env#g" -e "s#optimux\.sock#$n.sock#g" \
      -e "s#^User=optimux#User=$n#" -e "s#^Group=optimux#Group=$n#" -e "s#^RuntimeDirectory=optimux#RuntimeDirectory=$n#" \
      "${M[APP_DIR]}/deploy/optimux.service"
}

server_socket() { echo "/run/$(nm)/$(nm).sock"; }

# The systemd service from deploy/optimux.service; without a domain it listens on
# 127.0.0.1 (SSH tunnel). Also run on update, so an install gets the current unit.
install_server_unit() {
  local domain="${1:-}" n unit
  n="$(nm)"
  unit="$(mktemp)"
  server_unit "$n" >"$unit"
  if [[ -z "$domain" ]]; then
    sed -i -E "s#^ExecStart=.*#ExecStart=${M[APP_DIR]}/.venv/bin/uvicorn optimux_backend.main:app --host 127.0.0.1 --port ${M[PORT]} --no-proxy-headers --no-server-header#" "$unit"
  fi
  run "Installing the systemd service" bash -c "$CM_SUDO install -m 644 '$unit' /etc/systemd/system/$n.service && $CM_SUDO systemctl daemon-reload && $CM_SUDO systemctl enable $n.service"
  rm -f "$unit"
}

# The service was started but doesn't answer yet: usually a slow first start. A service
# that failed or keeps restarting is reported as a problem ($1: the systemd unit).
still_starting() {
  local state=""
  [[ -n "${1:-}" ]] && state="$($CM_SUDO systemctl show -p ActiveState -p SubState --value "$1" 2>/dev/null | tr '\n' ' ' || true)"
  if [[ "$state" == *failed* || "$state" == *auto-restart* ]]; then
    warn "Optimux didn't start. The log says why: sudo optimux logs"
  else
    info "Optimux is still starting (it may take a minute on a slow server)."
    note "Check in a minute: sudo optimux status. If it doesn't run, the log says why: sudo optimux logs"
  fi
}

public_ip() { curl -4 -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true; }

# How to reach an SSH-tunnel-only server from the user's own computer -> CM_TUNNEL_CMD
# (empty when no SSH server runs here) and CM_TUNNEL_NOTE (advice, may be empty).
# The command uses their login on this server (the one behind sudo), the server's
# public address and its SSH port.
tunnel_plan() {
  local user="${SUDO_USER:-}" pub own host port=""
  CM_TUNNEL_CMD=""; CM_TUNNEL_NOTE=""
  [[ -n "$user" ]] || user="$(id -un 2>/dev/null || echo root)"
  ssh_ports
  if ! ssh_listening; then
    if systemd-detect-virt -cq 2>/dev/null; then
      CM_TUNNEL_NOTE="No SSH server runs here, so an SSH tunnel can't reach Optimux. This is a container: on its host, forward the port (Incus: incus config device add $(hostname 2>/dev/null || echo '<container>') web proxy listen=tcp:127.0.0.1:${M[PORT]} connect=tcp:127.0.0.1:${M[PORT]}) and open http://localhost:${M[PORT]} there. From another computer, add an SSH tunnel to the host."
    else
      CM_TUNNEL_NOTE="No SSH server runs here, so an SSH tunnel can't reach Optimux yet. Install one ($(ssh_install_hint)), then run: sudo optimux open"
    fi
    return 0
  fi
  pub="$(public_ip)"
  own="$(hostname -I 2>/dev/null || true)"
  host="$pub"
  [[ -n "$host" ]] || host="$(awk '{print $1}' <<<"$own")"
  [[ -n "$host" ]] || host="<server-address>"
  if [[ -n "$pub" && -n "${own// }" && " $own " != *" $pub "* ]]; then
    # 1:1 NAT (AWS, Google Cloud...) still reaches this server; an office network,
    # a home router or a container host doesn't.
    CM_TUNNEL_NOTE="$pub is the address this server uses to reach the internet, not one of its own ($(own_ipv4 "$own")). On a cloud server that's fine. Behind an office or home network, or in a container, use an address your computer can reach instead."
  fi
  if [[ -n "${CM_SSH_SURE:-}" ]]; then port="${CM_SSH_PORTS%% *}"; fi
  if [[ -n "$port" && "$port" != 22 ]]; then port=" -p $port"; else port=""; fi
  CM_TUNNEL_CMD="ssh -N${port} -L ${M[PORT]}:127.0.0.1:${M[PORT]} ${user}@${host}"
}

# This machine's IPv4 addresses from `hostname -I` output, comma separated
own_ipv4() { tr ' ' '\n' <<<"$1" | grep -E '^[0-9]+(\.[0-9]+){3}$' | paste -sd, - | sed 's/,/, /g' || true; }

ssh_install_hint() {
  case "${CM_PM:-}" in
    dnf) echo "sudo dnf install openssh-server" ;;
    pacman) echo "sudo pacman -S openssh" ;;
    zypper) echo "sudo zypper install openssh" ;;
    *) echo "sudo apt install openssh-server" ;;
  esac
}

# An SSH server listens on one of CM_SSH_PORTS (call ssh_ports first). When ss can't
# tell, assume it does: better a command that may fail than no command at all.
ssh_listening() {
  local out p
  out="$(ss -Htln 2>/dev/null)" || return 0     # listening sockets: no root needed
  for p in $CM_SSH_PORTS; do
    grep -qE ":${p}[[:space:]]" <<<"$out" && return 0
  done
  return 1
}

# SSH port(s) to keep open -> CM_SSH_PORTS; CM_SSH_SURE=1 when really found (not a guess)
ssh_ports() {
  local p=""
  CM_SSH_SURE=1
  p="$(ss -Htlnp 2>/dev/null | awk '/sshd/ {print $4}' | sed -E 's/.*[:]//' | sort -un | tr '\n' ' ' || true)"
  [[ -z "${p// }" ]] && p="$($CM_SUDO sshd -T 2>/dev/null | awk '$1 == "port" {print $2}' | sort -un | tr '\n' ' ' || true)"
  [[ -z "${p// }" && -n "${SSH_CONNECTION:-}" ]] && p="$(awk '{print $4}' <<<"$SSH_CONNECTION")"
  if [[ -z "${p// }" ]]; then p=22; CM_SSH_SURE=""; fi
  CM_SSH_PORTS="$p"
}

install_server() {
  CM_STEPS=7
  local N=optimux
  declare -gA M=([MODE]=server [NAME]="$N" [REPO]="$CM_REPO" [REF]="$CM_REF" [RUN_USER]="$N")
  M[APP_DIR]="/opt/$N"
  M[DATA_DIR]="/var/lib/$N/.$N"
  M[WORKSPACE]="/var/lib/$N/workspace"

  step "Checking the server"
  info "$CM_DISTRO"
  [[ "$CM_OS" == Linux ]] || die "Server mode needs Linux."
  [[ -n "$CM_SYSTEMD" ]] || die "Server mode needs systemd, which isn't running here."
  if [[ -n "$CM_SUDO" ]]; then
    command -v sudo >/dev/null 2>&1 || die "Run this as root, or install sudo."
    note "Server setup needs root; you may be asked for your password."
    sudo_ready || die "Server mode needs sudo (or run it as root)."
  fi
  check_system

  step "Web address"
  note "With a domain name pointing at this server, Optimux gets HTTPS automatically (Caddy, Let's Encrypt)."
  note "Without one, Optimux is reachable only through an SSH tunnel: safe, but less convenient."
  local domain="${CM_DOMAIN:-}"
  if [[ -z "$domain" && -z "$CM_NODOMAIN" ]]; then
    if [[ -n "$CM_YES" ]]; then CM_NODOMAIN=1
    else
      choose "How will you reach Optimux?" "Through my domain name, over HTTPS (recommended)" "Only through an SSH tunnel (no public web access)"
      if ((CHOICE == 0)); then
        while [[ -z "$domain" ]]; do
          ask "Domain name (for example optimux.example.com)" ""
          domain="${REPLY,,}"; domain="${domain#http*://}"; domain="${domain%%/*}"
          valid_domain "$domain" || { warn "That isn't a domain name."; domain=""; }
        done
      else CM_NODOMAIN=1; fi
    fi
  fi
  M[DOMAIN]=""                         # a reinstall that moved to the SSH tunnel has none
  if [[ -n "$domain" ]]; then
    M[DOMAIN]="$domain"; M[URL]="https://$domain"
    local ip dns
    ip="$(public_ip)"; dns="$(getent ahostsv4 "$domain" 2>/dev/null | awk '{print $1; exit}' || true)"
    if [[ -z "$dns" ]]; then warn "$domain doesn't resolve yet."; note "Point its A record at ${ip:-this server}; HTTPS starts working once it does."
    elif [[ -n "$ip" && "$dns" != "$ip" ]]; then warn "$domain points at $dns, but this server is $ip."
      confirm "Continue anyway?" Y || die "Fix the DNS record, then run this again."
    else ok "$domain points at this server${ip:+ ($ip)}"; fi
  else
    local port="${CM_PORT:-${CM_OLD_PORT:-8000}}"
    while ! port_free "$port"; do port=$((port + 1)); done
    if [[ "$port" != "${CM_PORT:-${CM_OLD_PORT:-8000}}" ]]; then info "Port ${CM_PORT:-${CM_OLD_PORT:-8000}} is busy; using $port."; fi
    M[PORT]="$port"; M[URL]="http://127.0.0.1:${M[PORT]}"
    ok "SSH tunnel only: Optimux listens on 127.0.0.1:${M[PORT]}"
  fi

  step "Installing system packages"
  local extra=()
  [[ -n "$domain" ]] && extra+=(caddy)
  command -v ufw >/dev/null 2>&1 || [[ "$CM_PM" != apt-get ]] || extra+=(ufw)
  if [[ -n "$domain" && "$CM_PM" == apt-get ]] && ! apt-cache show caddy >/dev/null 2>&1; then
    run "Adding Caddy's package repository" add_caddy_repo
  fi
  CM_YES_SAVED="$CM_YES"; CM_YES=1; install_missing "${extra[@]}"; CM_YES="$CM_YES_SAVED"
  command -v bwrap >/dev/null 2>&1 || die "bubblewrap is required on a server (the sandbox)."

  step "Installing Optimux"
  if ! id "$N" >/dev/null 2>&1; then
    run "Creating the $N user (no login, no sudo)" $CM_SUDO useradd --system --create-home --home-dir "/var/lib/$N" \
      --shell "$(command -v nologin || echo /usr/sbin/nologin)" "$N"
  fi
  # The service user owns its home, so root never creates or changes anything inside it
  # (a symlink planted there could otherwise redirect a root operation)
  [[ -L "/var/lib/$N" ]] && die "/var/lib/$N is a symlink; refusing to continue."
  run "Locking down /var/lib/$N" bash -c "$CM_SUDO chmod 750 /var/lib/$N && $CM_SUDO runuser -u $N -- mkdir -p -m 750 /var/lib/$N/workspace"
  fetch_code "${M[APP_DIR]}" root
  make_venv "${M[APP_DIR]}" root
  run "Making the program read-only for the service" $CM_SUDO chown -R root:root "${M[APP_DIR]}"
  local envf
  envf="$(mktemp)"
  cat >"$envf" <<EOF
# Written by install.sh. Server mode: sandbox required, auto-approve off,
# commands without network by default, HTTPS-only cookies.
OPTIMUX_SERVER=1
OPTIMUX_HOME=${M[DATA_DIR]}
HOME=/var/lib/$N
EOF
  run "Writing /etc/$N/$N.env" bash -c "$CM_SUDO install -d -m 755 /etc/$N && $CM_SUDO install -m 640 -g $N '$envf' /etc/$N/$N.env"
  rm -f "$envf"
  run "Preparing the data folder (private to the $N user)" admin init

  step "Signing in and setup"
  note "Choose the password you'll sign in with. Set it here so nobody else can claim this server first."
  if [[ -z "$CM_YES" ]]; then set_password_interactive && CM_PW_SET=1 || true; fi
  if admin set-workspace "${M[WORKSPACE]}" >>"$CM_LOG" 2>&1; then ok "Workspace: ${M[WORKSPACE]}"
  else warn "Couldn't set the Workspace folder; set it in Dashboard → Scope."; fi
  note "Your projects live here. Change it in Dashboard → Scope."
  printf '\n'
  [[ -n "$CM_YES" ]] || configure_provider
  printf '\n'
  configure_github
  printf '\n'
  configure_sandbox || { fix_apparmor && configure_sandbox; } || warn "The sandbox doesn't work here yet, so commands will be refused. See Dashboard → Execution and the Guide."

  step "Service and HTTPS"
  install_server_unit "$domain"
  M[SERVICE]="systemd"
  if [[ -n "$domain" ]]; then configure_caddy "$domain"
  elif [[ -f "$(caddy_site)" ]]; then
    # A reinstall that moved from a domain to the SSH tunnel: the old web address goes
    caddy_remove
    ok "Removed the web address of the earlier setup (Caddy)"
    note "Firewall rules for 80/443 were left in place (other sites may use them): sudo ufw delete allow 80/tcp"
  fi
  configure_firewall "$domain"
  configure_auto_updates

  M[INSTALLED]="$(date +%F)"
  manifest_write "$(manifest_path server "$N")"
  install_cli /usr/local/bin/optimux
  ok "The ${CB}optimux${C0} command is installed"
  if ! sudo -n sh -c 'command -v optimux' >/dev/null 2>&1 && [[ -n "$CM_SUDO" ]]; then
    note "sudo doesn't search /usr/local/bin here: run it as sudo /usr/local/bin/optimux"
  fi

  step "Starting"
  run "Starting Optimux" $CM_SUDO systemctl restart "$N.service"
  if [[ -n "$domain" ]]; then
    if wait_http "http://localhost/auth/status" "$(server_socket)" 30; then ok "Optimux is running"
    else still_starting "$N.service"; fi
    info "Waiting for the HTTPS certificate (up to a minute)…"
    if wait_http "https://$domain/auth/status" "" 60; then ok "HTTPS works: ${CB}https://$domain${C0}"
    else warn "HTTPS isn't answering yet (DNS or ports 80/443?). Caddy keeps trying: sudo journalctl -u caddy -n 50"; fi
  else
    if wait_http "${M[URL]}/auth/status" "" 30; then ok "Optimux is running on 127.0.0.1:${M[PORT]}"
    else still_starting "$N.service"; fi
  fi
  finish_server
}

# Caddy: Optimux gets its own file, imported from the main Caddyfile, so other sites on
# this server keep working. A Caddyfile that is still the package's example is replaced.
caddy_site() { echo "/etc/caddy/$(nm).caddyfile"; }
caddy_bak() { echo "/etc/caddy/Caddyfile.before-$(nm)"; }

caddy_is_example() {
  local body
  body="$(grep -vE '^\s*(#|$)' /etc/caddy/Caddyfile 2>/dev/null | tr -d ' \t' | tr '\n' ' ' || true)"
  [[ -z "$body" || "$body" =~ ^(import[^ ]*Caddyfile\.d/\*\.caddyfile\ )?:80\{\ root\*/usr/share/caddy\ file_server\ \}\ ?$ ]]
}

configure_caddy() {
  local domain="$1" cf n CADDY_SITE CADDY_BAK
  n="$(nm)"; CADDY_SITE="$(caddy_site)"; CADDY_BAK="$(caddy_bak)"
  cf="$(mktemp)"
  sed -e "s/optimux.example.com/$domain/" -e "s#/run/optimux/optimux\.sock#/run/$n/$n.sock#" -e "s#optimux-access\.log#$n-access.log#" \
    "${M[APP_DIR]}/deploy/Caddyfile" >"$cf"
  $CM_SUDO install -m 644 "$cf" "$CADDY_SITE"
  rm -f "$cf"
  if [[ ! -f /etc/caddy/Caddyfile ]] || caddy_is_example; then
    [[ -f /etc/caddy/Caddyfile && ! -f "$CADDY_BAK" ]] && $CM_SUDO cp /etc/caddy/Caddyfile "$CADDY_BAK"
    printf '# Written by the Optimux installer (the previous file: %s)\nimport %s\n' "$CADDY_BAK" "$CADDY_SITE" | $CM_SUDO tee /etc/caddy/Caddyfile >/dev/null
  elif ! grep -qF "import $CADDY_SITE" /etc/caddy/Caddyfile; then
    info "Caddy already serves other sites; they stay as they are."
    [[ -f "$CADDY_BAK" ]] || $CM_SUDO cp /etc/caddy/Caddyfile "$CADDY_BAK"
    printf '\n# Added by the Optimux installer\nimport %s\n' "$CADDY_SITE" | $CM_SUDO tee -a /etc/caddy/Caddyfile >/dev/null
  fi
  if ! $CM_SUDO caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >>"$CM_LOG" 2>&1; then
    [[ -f "$CADDY_BAK" ]] && $CM_SUDO cp "$CADDY_BAK" /etc/caddy/Caddyfile
    $CM_SUDO rm -f "$CADDY_SITE"
    die "Caddy rejected the configuration, so the previous one was put back. See the log."
  fi
  run "Configuring Caddy for $domain" bash -c "$CM_SUDO usermod -aG $n caddy && $CM_SUDO install -d -o caddy -g caddy /var/log/caddy && $CM_SUDO systemctl enable caddy && $CM_SUDO systemctl restart caddy"
}

caddy_remove() {
  local CADDY_SITE CADDY_BAK
  CADDY_SITE="$(caddy_site)"; CADDY_BAK="$(caddy_bak)"
  [[ -f "$CADDY_SITE" ]] || return 0
  $CM_SUDO rm -f "$CADDY_SITE"
  if [[ -f "$CADDY_BAK" ]] && grep -q "^# Written by the Optimux installer" /etc/caddy/Caddyfile 2>/dev/null; then
    $CM_SUDO mv "$CADDY_BAK" /etc/caddy/Caddyfile
  else
    $CM_SUDO sed -i -E -e "\#^import $CADDY_SITE\$#d" -e '/^# Added by the Optimux installer$/d' /etc/caddy/Caddyfile 2>/dev/null || true
    $CM_SUDO rm -f "$CADDY_BAK"
  fi
  $CM_SUDO systemctl reload caddy >/dev/null 2>&1 || $CM_SUDO systemctl restart caddy >/dev/null 2>&1 || true
}

add_caddy_repo() {
  $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get update -q
  $CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q debian-keyring debian-archive-keyring apt-transport-https gnupg curl
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | $CM_SUDO gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | $CM_SUDO tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
}

# Services that listen on a public address (not 127.x, ::1 or link-local), one line per
# port: "port/proto name". Returns 1 when they can't be listed. DHCP and mDNS clients
# are left out: ufw lets their traffic in by itself (a DHCP client may also hold a
# random UDP port, which changes after a reboot).
public_listeners() {
  local out
  out="$($CM_SUDO ss -Hltunp 2>/dev/null)" || return 1
  awk 'NF >= 5 {
    proto = $1; addr = $5; port = addr
    sub(/.*:/, "", port); sub(/:[^:]*$/, "", addr); gsub(/[][]/, "", addr); sub(/%.*/, "", addr)
    a = tolower(addr)
    if (port !~ /^[0-9]+$/) next
    if (a ~ /^127\./ || a == "::1" || a ~ /^::ffff:127\./ || a ~ /^fe80:/) next
    if (proto == "udp" && (port == "68" || port == "546" || port == "5353")) next
    name = ""
    if (match($0, /\("[^"]+"/)) name = substr($0, RSTART + 2, RLENGTH - 3)
    if (proto == "udp" && (name == "dhclient" || name == "dhcpcd" || name == "systemd-network")) next
    if (name == "systemd") name = ""          # socket activation: the real service is unknown here
    key = port "/" proto
    if (!(key in seen)) { seen[key] = 1; print key, name }
  }' <<<"$out"
}

configure_firewall() {
  local domain="$1" ports
  if command -v firewall-cmd >/dev/null 2>&1 && $CM_SUDO firewall-cmd --state >/dev/null 2>&1; then
    if [[ -n "$domain" ]]; then
      run "Opening the web ports in firewalld (SSH stays open)" bash -c "$CM_SUDO firewall-cmd --permanent --add-service=http --add-service=https && $CM_SUDO firewall-cmd --reload"
    else ok "firewalld is on; nothing to open (SSH tunnel only)"; fi
    return 0
  fi
  command -v ufw >/dev/null 2>&1 || { info "No ufw here: make sure only SSH${domain:+, 80 and 443} are open in your provider's firewall."; return 0; }
  ssh_ports; ports="$CM_SSH_PORTS"
  if $CM_SUDO ufw status 2>/dev/null | grep -q "Status: active"; then
    local cmd="true"
    [[ -n "$domain" ]] && cmd="$CM_SUDO ufw allow 80/tcp && $CM_SUDO ufw allow 443/tcp"
    run "Opening the web ports in the firewall" bash -c "$cmd"
    return 0
  fi
  # The command to turn it on by hand: the same rules the installer would add
  local p later=""
  for p in $ports; do later+="sudo ufw allow $p/tcp && "; done
  [[ -n "$domain" ]] && later+="sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && "
  later+="sudo ufw enable"
  if [[ -z "$CM_SSH_SURE" ]]; then
    warn "Couldn't find which port SSH uses (assuming 22)."
    if [[ -n "$CM_YES" ]]; then note "Firewall left off so you can't be locked out. Turn it on yourself: sudo ufw allow <ssh port>/tcp && sudo ufw enable"; return 0; fi
  fi
  # Other services reachable from outside (another website, a VPN, ...) would be cut off
  # without a word: list them first. Caddy's HTTP/3 (443/udp) falls back to 443/tcp.
  local known="" listeners key name others="" decided="" allow_extra=""
  local -a shown=()
  for p in $ports; do known+=" $p/tcp"; done
  [[ -n "$domain" ]] && known+=" 80/tcp 443/tcp 443/udp"
  known+=" "
  if listeners="$(public_listeners)"; then
    while read -r key name; do
      [[ -n "$key" && "$known" != *" $key "* ]] || continue
      others+=" $key"; shown+=("$key${name:+  ($name)}")
    done <<<"$listeners"
  else
    warn "Couldn't list the services running here, so the firewall might block one of them."
    if [[ -n "$CM_YES" ]]; then note "Firewall left off so nothing is cut off. Turn it on yourself: $later"; return 0; fi
  fi
  if [[ -n "$others" ]]; then
    warn "Other services here listen on public ports. Turning on the firewall would block them:"
    for p in "${shown[@]}"; do info "$p"; done
    local allow=""
    for p in $others; do allow+="sudo ufw allow $p && "; done
    later="$allow$later"
    if [[ -n "$CM_YES" ]]; then note "Firewall left off so they keep working. Turn it on yourself: $later"; return 0; fi
    choose "What should the firewall do with them?" "Keep them reachable (allow these ports too)" "Leave the firewall off" "Block them (only SSH${domain:+ and the web} stay open)"
    case "$CHOICE" in
      0) allow_extra=1 ;;
      1) note "Firewall left off. Turn it on later: $later"; return 0 ;;
    esac
    decided=1
  fi
  note "The firewall allows only SSH (port ${ports% })${domain:+ and the web (80, 443)}${allow_extra:+ and${others}}; everything else is closed."
  if [[ -n "$decided" ]] || confirm "Turn on the firewall (ufw)?" Y; then
    local cmd=""
    for p in $ports; do cmd+="$CM_SUDO ufw allow $p/tcp && "; done
    [[ -n "$domain" ]] && cmd+="$CM_SUDO ufw allow 80/tcp && $CM_SUDO ufw allow 443/tcp && "
    if [[ -n "$allow_extra" ]]; then for p in $others; do cmd+="$CM_SUDO ufw allow $p && "; done; fi
    cmd+="$CM_SUDO ufw --force enable"
    run "Turning on the firewall (SSH stays open)" bash -c "$cmd"
  else
    note "Turn it on later: $later"
  fi
}

configure_auto_updates() {
  [[ "$CM_PM" == apt-get ]] || return 0
  dpkg -s unattended-upgrades >/dev/null 2>&1 && { ok "Automatic security updates are already set up"; return 0; }
  if confirm "Install security updates for the system automatically?" Y; then
    run "Turning on automatic security updates" bash -c "$CM_SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y -q unattended-upgrades && echo 'unattended-upgrades unattended-upgrades/enable_auto_updates boolean true' | $CM_SUDO debconf-set-selections && $CM_SUDO env DEBIAN_FRONTEND=noninteractive dpkg-reconfigure -f noninteractive unattended-upgrades"
  fi
}

finish_server() {
  local open=() signin
  CM_TUNNEL_CMD=""; CM_TUNNEL_NOTE=""
  if [[ -n "${M[DOMAIN]:-}" ]]; then open=("${CB}Open${C0}       ${CA}${M[URL]}${C0}")
  else
    tunnel_plan
    if [[ -n "$CM_TUNNEL_CMD" ]]; then
      open=("${CB}Open${C0}       ${CY}On your own computer${C0} (not on this server), run and keep it open:"
            "           $CM_TUNNEL_CMD"
            "           then open ${CA}http://localhost:${M[PORT]}${C0} in your browser.")
    else
      open=("${CB}Open${C0}       Optimux listens on 127.0.0.1:${M[PORT]} here. See the note below.")
    fi
  fi
  if [[ -n "${CM_PW_SET:-}" ]]; then signin="Sign in with the password you just chose."
  elif password_is_set; then signin="Sign in with your password."
  else signin="First visit: paste the access token (sudo optimux token), then choose a password."; fi
  card "Optimux is installed on this server" \
    "${open[@]}" \
    "           $signin" \
    "" \
    "${CB}Protected${C0}  server mode ${S_DOT} sandbox required ${S_DOT} every command needs approval" \
    "           commands have no network ${S_DOT} runs as the optimux user" \
    "           ${M[DOMAIN]:+HTTPS by Caddy ${S_DOT} }no open port for Optimux itself" \
    "" \
    "${CB}Commands${C0}   sudo optimux status | logs | restart | update" \
    "           sudo optimux password | token | uninstall" \
    "" \
    "${CB}Next${C0}       Turn on two-step codes in Dashboard → System." \
    "           Check the Security checklist there."
  if [[ -n "$CM_TUNNEL_NOTE" ]]; then note "$CM_TUNNEL_NOTE"; fi
  dim "Install log: $CM_LOG"
  printf '\n'
}

# ============================================================================
#  The optimux command (scripts/optimux sources this file and calls these)
# ============================================================================

svc() {   # svc start|stop|restart|is-active
  case "${M[SERVICE]}" in
    systemd) $CM_SUDO systemctl "$1" "$(nm).service" ;;
    systemd-user) systemctl --user "$1" "$(nm).service" ;;
    *) process_ctl "$1" ;;
  esac
}

pidfile() { echo "${M[DATA_DIR]}/logs/server.pid"; }

# This install's server processes (this user, this program, this port), found by their
# exact command line, so a stale pid (reused after a reboot) is never trusted or killed.
server_pids() {
  local app
  app="$(printf '%s' "${M[APP_DIR]}" | sed 's/[][\.*^$+?(){}|/]/\\&/g' || true)"
  pgrep -u "$(id -u)" -f "^[^ ]*python[0-9.]* ${app}/\.venv/bin/uvicorn optimux_backend\.main:app --host 127\.0\.0\.1 --port ${M[PORT]} " 2>/dev/null || true
}

process_ctl() {
  local pf; pf="$(pidfile)"
  case "$1" in
    is-active) [[ -n "$(server_pids)" ]] ;;
    start)
      process_ctl is-active && return 0
      mkdir -p "$(dirname "$pf")"
      (
        cd "${M[APP_DIR]}" || exit 1
        umask 077
        # nohup execs uvicorn, so $! is the server itself (the pid file is informational)
        OPTIMUX_HOME="${M[DATA_DIR]}" nohup "${M[APP_DIR]}/.venv/bin/uvicorn" optimux_backend.main:app \
          --host 127.0.0.1 --port "${M[PORT]}" --no-proxy-headers --no-server-header \
          </dev/null >>"${M[DATA_DIR]}/logs/server.log" 2>&1 3<&- &   # detached from this terminal
        echo $! >"$pf"
      ) ;;
    stop)
      local p
      for p in $(server_pids); do kill "$p" 2>/dev/null || true; done
      for _ in 1 2 3 4 5 6 7 8 9 10; do [[ -z "$(server_pids)" ]] && break; sleep 0.3; done
      rm -f "$pf" ;;
    restart) process_ctl stop; sleep 1; process_ctl start ;;
  esac
}

cli_start()   { svc start; }
cli_stop()    { svc stop; ok "Stopped"; }
cli_restart() { svc restart; ok "Restarted"; }

# What an install runs: the release from VERSION and the exact commit, like
# "v0.4.0 (5e0673b)" (installs are shallow clones, so git has no tags to show).
# The same format as the System page (snapshots.optimux_version).
app_version() {
  local dir="$1" v="" c=""
  [[ -r "$dir/VERSION" ]] && v="$(tr -d '[:space:]' < "$dir/VERSION")"
  c="$(git -c safe.directory="$dir" -C "$dir" describe --always --dirty --abbrev=7 --exclude='*' 2>/dev/null || true)"
  if [[ -n "$v" && -n "$c" ]]; then echo "v$v ($c)"
  elif [[ -n "$v" ]]; then echo "v$v"
  else echo "${c:-unknown}"; fi
}

cli_status() {
  if svc is-active >/dev/null 2>&1; then ok "Running ${CM}(${M[SERVICE]})${C0}"; else warn "Not running. Start it with: optimux start"; fi
  if [[ "${M[MODE]}" == server && -n "${M[DOMAIN]:-}" ]]; then
    wait_http "http://localhost/auth/status" "$(server_socket)" 1 && ok "Answers on its socket" || warn "Doesn't answer on $(server_socket)"
  else
    wait_http "http://127.0.0.1:${M[PORT]}/auth/status" "" 1 && ok "Answers on 127.0.0.1:${M[PORT]}" || warn "Doesn't answer on 127.0.0.1:${M[PORT]}"
  fi
  if [[ "${M[MODE]}" == server && -z "${M[DOMAIN]:-}" ]]; then
    tunnel_plan
    if [[ -n "$CM_TUNNEL_CMD" ]]; then info "Open:    on your own computer: $CM_TUNNEL_CMD, then http://localhost:${M[PORT]}"
    else info "Open:    it listens on 127.0.0.1:${M[PORT]} (no SSH server here, see below)"; fi
    if [[ -n "$CM_TUNNEL_NOTE" ]]; then note "$CM_TUNNEL_NOTE"; fi
  else info "Open:    ${M[URL]}"; fi
  info "Program: ${M[APP_DIR]}  ($(app_version "${M[APP_DIR]}"))"
  info "Data:    ${M[DATA_DIR]}"
}

cli_logs() {
  case "${M[SERVICE]}" in
    systemd) $CM_SUDO journalctl -u "$(nm).service" -n 100 -f ;;
    systemd-user) journalctl --user -u "$(nm).service" -n 100 -f ;;
    *) tail -n 100 -f "${M[DATA_DIR]}/logs/server.log" ;;
  esac
}

cli_open() {
  if [[ "${M[MODE]}" == server && -z "${M[DOMAIN]:-}" ]]; then
    tunnel_plan
    if [[ -n "$CM_TUNNEL_CMD" ]]; then
      say "Optimux on this server is reachable only through an SSH tunnel. On your own computer, run:"
      say "  $CM_TUNNEL_CMD"
      say "then open http://localhost:${M[PORT]}"
    fi
    if [[ -n "$CM_TUNNEL_NOTE" ]]; then say "$CM_TUNNEL_NOTE"; fi
    return 0
  fi
  say "${M[URL]}"
  if command -v xdg-open >/dev/null 2>&1 && [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]]; then xdg-open "${M[URL]}" >/dev/null 2>&1 &
  elif [[ "$(uname -s)" == Darwin ]]; then open "${M[URL]}"; fi
}

cli_update() {
  CM_REF="${M[REF]:-main}"; CM_REPO="${M[REPO]:-$CM_REPO}"
  CM_LOG="${CM_LOG:-$(new_log)}"
  find_python || die "Python $CM_MIN_PY+ with venv is missing."
  section "Updating Optimux"
  if [[ "${M[MODE]}" == server ]]; then
    fetch_code "${M[APP_DIR]}" root; make_venv "${M[APP_DIR]}" root
    run "Keeping the program read-only" $CM_SUDO chown -R root:root "${M[APP_DIR]}"
    [[ "${M[SERVICE]:-}" == systemd ]] && install_server_unit "${M[DOMAIN]:-}"   # the unit of this version
  else
    fetch_code "${M[APP_DIR]}"; make_venv "${M[APP_DIR]}"
    if [[ "${M[SERVICE]:-}" == systemd-user ]]; then
      write_user_unit && systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
  fi
  run "Restarting" svc restart
  ok "Updated to $(app_version "${M[APP_DIR]}")"
}

cli_password() {
  CM_LOG="${CM_LOG:-/dev/null}"
  section "New sign-in password"
  note "Every browser is signed out and needs the new password."
  set_password_interactive || true
}

cli_token() { admin token; }

# rm -rf, but only for a path that is clearly Optimux's own (the manifest could be damaged)
safe_rm() {
  local path="$1" real
  real="$(realpath -m -- "$path" 2>/dev/null || echo "$path")"
  case "$real" in
    ""|/|/home|/root|/opt|/var|/var/lib|/etc|/usr|"$HOME"|"$HOME/") err "Refusing to delete $path"; return 1 ;;
  esac
  if [[ "$(basename "$real")" != *optimux* ]]; then err "Refusing to delete $path (not an Optimux folder)"; return 1; fi
  local mode=""
  declare -p M >/dev/null 2>&1 && mode="${M[MODE]:-}"
  if [[ "$mode" == server ]]; then $CM_SUDO rm -rf -- "$real"; else rm -rf -- "$real"; fi
}

cli_uninstall() {
  CM_LOG="${CM_LOG:-$(new_log)}"
  section "Uninstalling Optimux"
  info "Program: ${M[APP_DIR]}"
  info "Data (keys, chats, memory): ${M[DATA_DIR]}"
  if [[ -z "$CM_YES" ]]; then confirm "Remove Optimux?" N || { info "Nothing changed."; return 0; }; fi
  svc stop >/dev/null 2>&1 || true
  if [[ "${M[MODE]}" == server ]]; then
    local n; n="$(nm)"
    $CM_SUDO systemctl disable "$n.service" >/dev/null 2>&1 || true
    $CM_SUDO rm -f "/etc/systemd/system/$n.service" /usr/local/bin/optimux
    $CM_SUDO systemctl daemon-reload || true
    safe_rm "${M[APP_DIR]}" || true
    caddy_remove
    if [[ "${M[APPARMOR]:-}" == 1 ]] && [[ -f /etc/apparmor.d/bwrap ]]; then
      $CM_SUDO apparmor_parser -R /etc/apparmor.d/bwrap >/dev/null 2>&1 || true
      $CM_SUDO rm -f /etc/apparmor.d/bwrap
    fi
    ok "Program, service and web configuration removed"
    [[ -n "${M[DOMAIN]:-}" ]] && note "Firewall rules for 80/443 were left in place (other sites may use them): sudo ufw delete allow 80/tcp"
    warn "Deleting /var/lib/$n removes all data and projects, and the $n user. It can't be undone."
    if confirm "Delete them too?" N; then
      $CM_SUDO userdel -r "$n" >/dev/null 2>&1 || safe_rm "/var/lib/$n" || true
      $CM_SUDO rm -rf "/etc/$n"
      ok "Data and user removed"
    else
      $CM_SUDO rm -f "$(manifest_path server "$n")"
      info "Kept /var/lib/$n (data and projects) and /etc/$n/$n.env"
    fi
  else
    if [[ "${M[SERVICE]}" == systemd-user ]]; then
      systemctl --user disable "$(nm).service" >/dev/null 2>&1 || true
      rm -f "$(user_unit_path "$(nm)")"
      systemctl --user daemon-reload >/dev/null 2>&1 || true
    fi
    safe_rm "${M[APP_DIR]}" || true
    rm -f "$HOME/.local/bin/optimux" "$(manifest_path local "$(nm)")"
    rmdir "$(dirname "$(manifest_path local "$(nm)")")" 2>/dev/null || true
    local rc
    for rc in "$HOME/.bashrc" "$HOME/.zshrc"; do      # the PATH line the installer added, if any
      [[ -f "$rc" ]] && grep -q '^# Added by the Optimux installer$' "$rc" && \
        sed -i -E '/^# Added by the Optimux installer$/{N;/\n(export PATH=)/d;}' "$rc"
    done
    if [[ "${M[APPARMOR]:-}" == 1 ]]; then note "The AppArmor rule for bubblewrap stays (other programs may use it): sudo rm /etc/apparmor.d/bwrap"; fi
    if [[ -d "${M[APP_DIR]}" ]]; then warn "Couldn't remove ${M[APP_DIR]} completely."; else ok "Program removed"; fi
    warn "Your data in ${M[DATA_DIR]}: keys, chats, memory. Deleting it can't be undone."
    if confirm "Delete your data too?" N; then
      safe_rm "${M[DATA_DIR]}" && ok "Data deleted"
    else info "Kept your data in ${M[DATA_DIR]}: a new install picks it up again."; fi
  fi
  ok "Optimux is uninstalled"
}

# ============================================================================
#  Main
# ============================================================================

new_log() {
  local f
  f="$(mktemp "${TMPDIR:-/tmp}/optimux-install-XXXXXX.log")"
  chmod 600 "$f"
  echo "$f"
}

usage() {
  cat <<'TXT'
Optimux installer

  curl -fsSL https://raw.githubusercontent.com/optiware-ai/optimux/main/install.sh | bash

Asks everything it needs. Options, for scripts:
  --local | --server      where to install (this computer, or a VPS)
  --domain NAME           server: HTTPS for this domain (Caddy)
  --no-domain             server: no public web access; use an SSH tunnel
  --yes                   accept the defaults, skip optional questions
  --dir PATH              local: where the program goes (~/.local/share/optimux)
  --workspace PATH        local: the folder that holds your projects
  --port N                local / tunnel: port on 127.0.0.1 (default 8000)
  --ref REF               git branch or tag to install (default main)
  --no-start              don't start Optimux at the end
  --update | --uninstall  act on an existing install
  -h, --help              this help

Run it again to update, reconfigure or uninstall.
TXT
}

valid_domain() { [[ "$1" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$ ]]; }

parse_args() {
  CM_MODE="" CM_DOMAIN="" CM_NODOMAIN="" CM_YES="" CM_NO_START="" CM_ACTION="" CM_DIR="" CM_WORKSPACE="" CM_PORT=""
  while (($#)); do
    case "$1" in
      --local) CM_MODE=local ;;
      --server) CM_MODE=server ;;
      --domain) CM_DOMAIN="${2:?--domain needs a name}"; shift ;;
      --no-domain) CM_NODOMAIN=1 ;;
      --yes|-y) CM_YES=1 ;;
      --dir) CM_DIR="${2:?}"; shift ;;
      --workspace) CM_WORKSPACE="${2:?}"; shift ;;
      --port) CM_PORT="${2:?}"; shift ;;
      --ref) CM_REF="${2:?}"; shift ;;
      --no-start) CM_NO_START=1 ;;
      --update) CM_ACTION=update ;;
      --uninstall) CM_ACTION=uninstall ;;
      -h|--help) usage; exit 0 ;;
      *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
  done
  if [[ -n "$CM_DOMAIN" ]]; then
    CM_DOMAIN="${CM_DOMAIN,,}"; CM_DOMAIN="${CM_DOMAIN#http*://}"; CM_DOMAIN="${CM_DOMAIN%%/*}"
    valid_domain "$CM_DOMAIN" || { echo "Not a domain name: $CM_DOMAIN" >&2; exit 2; }
    CM_MODE=server
  fi
  if [[ -n "$CM_PORT" ]] && ! [[ "$CM_PORT" =~ ^[0-9]+$ && "$CM_PORT" -ge 1024 && "$CM_PORT" -le 65535 ]]; then
    echo "--port must be a number from 1024 to 65535." >&2; exit 2
  fi
  if [[ -n "$CM_DIR" ]]; then
    [[ "$CM_DIR" == /* ]] || CM_DIR="$PWD/$CM_DIR"
    [[ "$CM_DIR" =~ [[:space:]] ]] && { echo "--dir must not contain spaces." >&2; exit 2; }
  fi
  [[ -n "$CM_YES" && -z "$CM_MODE" && -z "$CM_ACTION" ]] && CM_MODE=local
  return 0
}

on_exit() { cursor_show; [[ -n "$CM_TTY" ]] && stty sane <&3 2>/dev/null; return 0; }
# Stop a step and everything it started (apt, pip, git...): background jobs of a
# script ignore Ctrl+C, so they have to be stopped explicitly.
kill_tree() {
  local pid="$1" child
  for child in $(pgrep -P "$pid" 2>/dev/null || true); do kill_tree "$child"; done
  kill -TERM "$pid" 2>/dev/null || true
}

on_interrupt() {
  printf '\n'
  if [[ -n "${CM_RUN_PID:-}" ]]; then kill_tree "$CM_RUN_PID"; fi
  warn "Stopped. The step that was running was stopped too; run the installer again to continue."
  exit 130
}

main() {
  if ((BASH_VERSINFO[0] < 4 || (BASH_VERSINFO[0] == 4 && BASH_VERSINFO[1] < 4))); then
    echo "The Optimux installer needs bash 4.4 or newer (this is $BASH_VERSION)." >&2
    echo "On macOS: brew install bash, then run the same command with: | \$(brew --prefix)/bin/bash" >&2
    exit 1
  fi
  set -Eeuo pipefail
  export PATH="$PATH:/usr/local/sbin:/usr/sbin:/sbin"
  parse_args "$@"
  tui_init
  trap on_exit EXIT
  trap on_interrupt INT
  detect_system
  CM_LOG="$(new_log)"
  banner "Route each request to the right model."

  # Already installed: update, reconfigure, or uninstall
  if find_install; then
    local current="${M[MODE]}"
    info "Optimux is already installed here ${CM}(${current}, ${M[APP_DIR]})${C0}"
    local action="$CM_ACTION"
    if [[ -z "$action" ]]; then
      choose "What do you want to do?" "Update to the latest version" "Reinstall and go through the setup again" "Uninstall" "Nothing, exit"
      local actions=(update reinstall uninstall exit)
      action="${actions[CHOICE]}"
    fi
    case "$action" in
      update) cli_update; return 0 ;;
      uninstall) cli_uninstall; return 0 ;;
      exit) return 0 ;;
      reinstall)
        CM_MODE="$current"
        CM_OLD_PORT="${M[PORT]:-}"
        [[ "$current" == local ]] && CM_SUDO=""
        svc stop >/dev/null 2>&1 || true        # the new setup may change the port or service
        detect_system ;;
    esac
  elif [[ -n "$CM_ACTION" ]]; then
    die "Optimux isn't installed here, so there's nothing to ${CM_ACTION}."
  fi

  if [[ -z "$CM_MODE" ]]; then
    local hint=""
    [[ -n "${SSH_CONNECTION:-}" && -z "${DISPLAY:-}" ]] && hint="  ${CM}(you're on SSH: probably a server)${C0}"
    say "    Where are you installing Optimux?$hint"
    choose "Choose with ↑ ↓ and Enter" \
      "This computer: just me, at http://127.0.0.1" \
      "A server (VPS): reachable from anywhere, with HTTPS and server hardening"
    ((CHOICE == 0)) && CM_MODE=local || CM_MODE=server
  fi
  if [[ "$CM_MODE" == local && $EUID -eq 0 ]]; then
    warn "You're root. On your own computer, install as your normal user instead."
    confirm "Install for root anyway?" N || exit 1
  fi
  if [[ "$CM_MODE" == server ]]; then install_server; else install_local; fi
}

# Loaded by scripts/optimux for its functions: don't run the installer then
if [[ "${OPTIMUX_LIB_ONLY:-}" != 1 ]]; then main "$@"; fi
