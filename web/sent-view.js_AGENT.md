# sent-view.js Agent Details

## File Overview
`web/sent-view.js` (v0.8, p78) tells the View button of a text pane in "What was sent" what the text is. Plain functions, loaded before `app.js` as `window.OptimuxSentView`; under Node it is `module.exports` (`tests/test_sent_view.py`).

## Core Functionality
- `detect(text)`: `'json'` (valid JSON, or a tool call part: a name, a line break and JSON arguments), `'md'` (a heading, a fence, a table, a quote, two list items or two inline marks), `'code'` (at least 60% of 3+ lines end like code, start with a keyword and hold code punctuation, or are indented), else `null` (plain words: no View button).
- `pretty(text)`: JSON with two-space indentation, the tool call's name kept above it.

## Notes
- The page only draws; the text is never changed. app.js (`SentCall.viewHtml`) shows `md` with `marked`, `json` and `code` with highlight.js, always through `optimuxSafeHtml`. Detection is cached by text in `sentKindOf` (cleared above 500 entries).
- Unfenced code is a guess from how lines look; a short snippet or prose that looks like code may not get a View button, and a View of prose never happens.
