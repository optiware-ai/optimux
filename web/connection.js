// The page's own check of its line to Optimux (v0.8). Plain functions, used by app.js
// and tested with Node (tests/test_connection.py).
// A WebSocket on a network that died does not say so for a long time: the page looked
// connected and showed nothing. So the page pings every PING_EVERY ms, the server
// answers "pong" at once, and from the wait the page knows:
//  - linkState: 'ok', 'weak' (a check waits, or the last one was slow) or 'down'
//    (no socket, the browser is offline, or a check went unanswered for LOST_AFTER);
//  - shouldReconnect: the socket is dead without knowing it, so the page opens a new one;
//  - shouldPing: time for the next check;
//  - linkNote: the banner's line, and what happens to a running request;
//  - waitNote: for a silent model call, whether the provider's server answers.
(function (root) {
    const PING_EVERY = 5000;
    const WEAK_AFTER = 3000;      // a check unanswered this long
    const SLOW_RTT = 2000;        // or the last answer took this long
    const LOST_AFTER = 10000;     // unanswered this long: this socket is gone
    const CONNECT_WAIT = 8000;    // a new socket that is not up by then is dropped and tried again
    const NOTE_AFTER = 3;         // seconds the line has not been fine before the banner says there is none

    const waiting = (s) => (s.pingAt > s.heardAt ? s.now - s.pingAt : 0);

    function linkState(s) {
        if (s.socket !== 'connected' || s.online === false) return 'down';
        const w = waiting(s);
        if (w >= LOST_AFTER) return 'down';
        if (w >= WEAK_AFTER || (s.rtt || 0) >= SLOW_RTT) return 'weak';
        return 'ok';
    }

    function shouldReconnect(s) {
        return s.socket === 'connected' && waiting(s) >= LOST_AFTER;
    }

    function shouldPing(s) {
        return s.socket === 'connected' && s.pingAt <= s.heardAt && s.now - s.pingAt >= PING_EVERY;
    }

    const fill = (translate) => (text, vars) => (translate ? translate(text, vars) : String(text).replace(/\{(\w+)\}/g,
        (m, k) => (vars && vars[k] !== undefined ? vars[k] : m)));

    function linkNote(info, translate) {
        const tr = fill(translate);
        info = info || {};
        if (info.state === 'weak') {
            return info.working ? tr('Weak connection to Optimux. The reply may arrive late; nothing is lost.')
                                : tr('Weak connection to Optimux.');
        }
        if (info.state !== 'down' || (info.secs || 0) < NOTE_AFTER) return '';
        return info.working
            ? tr('No connection to Optimux for {n} s. The request goes on there for up to {grace} s and comes back here when the connection does.', { n: info.secs, grace: info.grace })
            : tr('No connection to Optimux for {n} s. Reconnecting…', { n: info.secs });
    }

    function waitNote(st, translate) {
        const tr = fill(translate);
        st = st || {};
        if (st.net === 'down') return tr('This Optimux machine can\'t reach {host}: check its internet connection.', { host: st.host });
        if (st.net === 'ok') return tr('{host} answers, so the model itself is slow or thinking.', { host: st.host });
        return '';
    }

    const api = { linkState, shouldReconnect, shouldPing, linkNote, waitNote, PING_EVERY, WEAK_AFTER, LOST_AFTER, CONNECT_WAIT };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OptimuxConnection = api;
})(typeof window !== 'undefined' ? window : globalThis);
