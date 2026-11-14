# web/connection.js

The page's own check of its line to Optimux (v0.8). Plain functions on `window.OptimuxConnection` (and `module.exports` for Node), no Vue, no DOM; `app.js` keeps the state and the timer. Tests: `tests/test_connection.py`.

**Why:** a WebSocket on a network that died does not close for a long time. The page looked connected and showed nothing, while the request ran on (or was stopped) on the server.

- `linkState({socket, online, now, pingAt, heardAt, rtt})`: `'down'` with no connected socket, with the browser offline, or with a ping unanswered for `LOST_AFTER` (10 s); `'weak'` with a ping unanswered for `WEAK_AFTER` (3 s) or a last round trip of 2 s or more; else `'ok'`. Any message from the server counts as an answer (`heardAt`).
- `shouldReconnect(s)`: the socket is "connected" but a ping went unanswered for `LOST_AFTER`: the page drops it and opens a new one.
- `shouldPing(s)`: connected, no ping still out, and `PING_EVERY` (5 s) since the last one. While one is out, its wait is the measure.
- `linkNote({state, secs, working, grace}, tr)`: the bar's text. Weak says the reply may be late; down says for how long and, with a request running, that it goes on on the server for `grace` seconds and comes back. Down for under 3 s says nothing (a reload, a blink).
- `waitNote({net, host}, tr)`: for a silent model call, what the server's check of the provider's address found (`watch_silence` in `main.py`).
- `CONNECT_WAIT` (8 s): how long `app.js` gives a new socket to come up.

The texts are marked for translation here (`tr(...)`), so the file is in `tests/test_i18n.py`'s `SOURCES`, and in `PAGE_FILES` (`main.py`) so a change offers open pages a reload.
