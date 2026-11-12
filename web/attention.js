// "The chat needs you" (v0.8). Plain functions, used by app.js and tested with
// Node (tests/test_attention.py).
//  - attentionText: one short line saying what is waiting, for a desktop
//    notification, a toast or the tab's title.
//  - shouldNotify: whether a desktop notification is right, i.e. the chat is out
//    of sight and this browser allowed it.
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

    function shouldNotify(state) {
        state = state || {};
        if (state.replay || !state.enabled || state.permission !== 'granted') return false;
        return !!(state.hidden || state.settingsOpen || (state.studioOpen && !state.studioChat));
    }

    function tabTitle(base, project, waiting) {
        if (!waiting) return base;
        return project ? '⚠ ' + project + ' · ' + base : '⚠ ' + base;
    }

    const api = { attentionText, shouldNotify, tabTitle };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OptimuxAttention = api;
})(typeof window !== 'undefined' ? window : globalThis);
