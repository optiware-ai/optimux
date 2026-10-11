#!/usr/bin/env bash
# Rebuild web/app.css (Tailwind) after changing classes in index.html or web/*.js.
# Needs Node.js; the versions are pinned so the output only changes with your classes.
set -euo pipefail
cd "$(dirname "$0")/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
npm install --prefix "$tmp" --no-save --silent tailwindcss@3.4.17 @tailwindcss/typography@0.5.16
NODE_PATH="$tmp/node_modules" "$tmp/node_modules/.bin/tailwindcss" \
  -c web/tailwind.config.js -i web/tailwind.input.css -o web/app.css --minify
echo "web/app.css rebuilt ($(wc -c < web/app.css) bytes)"
