// "The chat needs you" (v0.8). Plain functions, used by app.js and tested with
// Node (tests/test_attention.py).
//  - attentionText: one short line saying what is waiting, for a desktop
//    notification, a toast or the tab's title.
//  - noticeFor: where to say it: Optimux's own card inside the page (the chat is
//    covered), the browser's notification (the tab is not in front), or nowhere.
//  - chatOutOfSight: the chat is covered by the Dashboard or the Studio.
//  - tabTitle: the browser tab says it too, with no permission needed.
(function (root) {
    const LINE = 120;

    function oneLine(text) {
        const flat = String(text || '').trim().replace(/\s*\n\s*/g, ' ⏎ ').replace(/[ \t]+/g, ' ');
        return flat.length > LINE ? flat.slice(0, LINE - 1).trimEnd() + '…' : flat;
    }

    function attentionText(data, translate) {
        const tr = (text, vars) => (translate ? translate(text, vars) : String(text).replace(/\{(\w+)\}/g,
            (m, k) => (vars && vars[k] !== undefined ? vars[k] : m)));
        data = data || {};
        const command = oneLine(data.command);
        if (data.form) return tr('A form in the chat is waiting for you.');
        if (data.action === 'model_fallback') {
            const f = data.fallback || {};
            return f.kind === 'stuck'
                ? tr('{model} is stuck. Continue on {alt}?', { model: f.model, alt: f.alt })
                : tr('{model} isn\'t answering. Use {alt} for this request?', { model: f.model, alt: f.alt });
        }
        if (data.sync) {
            const p = data.sync.project || '';
            return data.sync.kind === 'update' ? tr('Update {project} from GitHub?', { project: p })
                                               : tr('Push {project} to GitHub?', { project: p });
        }
        if (data.file_change) return tr('May it change {path}?', { path: data.file_change.path || command });
        if (!command) return tr('The chat needs your answer.');
        return data.action ? tr('May it do this: {command}', { command: command })
                           : tr('May it run: {command}', { command: command });
    }

    // The chat is covered: the Dashboard over it, or the Studio without its chat column.
    function chatOutOfSight(state) {
        state = state || {};
        return !!(state.settingsOpen || (state.studioOpen && !state.studioChat));
    }

    // Where to say it. 'page': Optimux's own card, when the tab is in front but the chat
    // is covered (no permission needed). 'desktop': the browser's notification, only when
    // the tab itself is out of sight, the checkbox is on and the browser allowed it.
    function noticeFor(state) {
        state = state || {};
        if (state.replay) return 'none';
        if (state.hidden) return state.enabled && state.permission === 'granted' ? 'desktop' : 'none';
        return chatOutOfSight(state) ? 'page' : 'none';
    }

    function tabTitle(base, project, waiting) {
        if (!waiting) return base;
        return project ? '⚠ ' + project + ' · ' + base : '⚠ ' + base;
    }

    // A PNG: browsers show their own generic icon for an SVG one
    const ICON = '/static/logo-192.png';

    const api = { attentionText, chatOutOfSight, noticeFor, tabTitle, ICON };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OptimuxAttention = api;
})(typeof window !== 'undefined' ? window : globalThis);
