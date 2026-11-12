# attention.js Agent Details

## File Overview
`web/attention.js` (v0.8) decides what to say, and whether to say it outside the page, when a reply stops and waits for the user. Plain functions so Node can test them (`tests/test_attention.py`); loaded before `app.js` as `window.OptimuxAttention`, and `module.exports` under Node.

## Core Functionality
- `attentionText(data, translate)`: one short line for the `approval_request` message — a form, a `model_fallback` (quota or stuck), a push or update window, a file change, or a command (`data.command`, flattened to one line, newlines as `⏎`, cut at 120 characters). `translate` is the page's `tr`; without it the placeholders are filled as they are, which is what the Node tests read.
- `shouldNotify({hidden, settingsOpen, studioOpen, studioChat, replay, enabled, permission})`: true only when the chat is out of sight (hidden tab, the Dashboard over it, or the Studio without its chat column), the event isn't a replay, the checkbox is on and the browser granted permission.
- `tabTitle(base, project, waiting)`: `⚠ <project> · Optimux` while a reply waits, else the base title.

## Notes
- No DOM, no Vue: `app.js` owns the `Notification` object, the title and the setting (`localStorage` `optimux.notify`, per browser — notification permission is per browser too).
- Its texts are marked with `tr(...)`, and `tests/test_i18n.py` reads this file, so each one needs its Italian.
- Listed in `main.PAGE_FILES`, so a change offers the open pages a reload.
