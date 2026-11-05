#!/usr/bin/env bash
# Rebuild the Studio's code editor (CodeMirror 6) into one pinned file in web/vendor/.
# Needs Node.js; the versions are pinned so the output only changes when they do.
# The source is scripts/editor/entry.js; the page loads web/vendor/codemirror-<view version>.min.js.
set -euo pipefail
cd "$(dirname "$0")/.."
VIEW=6.43.15
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
npm install --prefix "$tmp" --no-save --silent esbuild@0.28.2 \
  @codemirror/state@6.7.6 @codemirror/view@$VIEW @codemirror/commands@6.11.1 @codemirror/language@6.13.1 \
  @codemirror/search@6.7.2 @codemirror/autocomplete@6.20.4 @codemirror/lang-javascript@6.2.5 \
  @codemirror/lang-html@6.4.12 @codemirror/lang-css@6.3.1 @codemirror/lang-json@6.0.2 \
  @codemirror/lang-markdown@6.5.2 @codemirror/lang-python@6.2.1 @codemirror/lang-xml@6.1.0 \
  @codemirror/lang-yaml@6.1.3 @codemirror/lang-sql@6.10.0 @codemirror/legacy-modes@6.5.5 @lezer/highlight@1.2.5
cp scripts/editor/entry.js "$tmp/entry.js"
out="web/vendor/codemirror-$VIEW.min.js"
"$tmp/node_modules/.bin/esbuild" "$tmp/entry.js" --bundle --minify --format=iife --global-name=OptimuxEditor \
  --legal-comments=none --target=es2020 --banner:js="/*! CodeMirror 6 and its language packages (codemirror.net): MIT License, (C) Marijn Haverbeke and others. Lezer: MIT License. Built by scripts/build-editor.sh. */" --outfile="$out" --log-level=warning
echo "$out rebuilt ($(wc -c < "$out") bytes)"
