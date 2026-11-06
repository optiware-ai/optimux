// v0.8: the page's languages. English is the source: every text on the page is
// written in English and passed through $t (templates) or tr (scripts). Another
// language is a JSON file in web/i18n/<code>.json mapping the English text to its
// translation; a text missing there stays in English. {name} in a text is a
// placeholder filled from the second argument: tr('{n} chats', { n: 3 }). A
// translation may leave a placeholder out (an English plural "s", say).
// The choice is saved on the server (settings.language, POST /api/language); this
// browser also remembers it, so the page starts in that language before the
// settings arrive.
(function () {
    const LANGS = [
        { code: 'en', name: 'English' },
        { code: 'it', name: 'Italiano' },
    ];
    const lang = Vue.ref('en');
    const messages = Vue.ref({});
    const loaded = { en: {} };
    const KEY = 'optimux.lang';

    function fill(text, vars) {
        if (!vars) return text;
        return text.replace(/\{(\w+)\}/g, (m, k) => (k in vars ? String(vars[k]) : m));
    }
    // Reading lang and messages here makes every template that calls it follow a change
    function tr(text, vars) {
        if (text === undefined || text === null) return '';
        const s = String(text);
        const out = lang.value !== 'en' ? messages.value[s] : undefined;
        return fill(out === undefined || out === '' ? s : out, vars);
    }
    async function load(code) {
        if (loaded[code]) return loaded[code];
        const res = await fetch('/static/i18n/' + code + '.json');
        if (!res.ok) throw new Error('No translation file for ' + code);
        loaded[code] = await res.json();
        return loaded[code];
    }
    // Switch the page (does not save on the server: the app does that)
    async function setLanguage(code) {
        if (!LANGS.some(l => l.code === code)) code = 'en';
        try { messages.value = await load(code); }
        catch (e) { console.error(e); code = 'en'; messages.value = {}; }
        lang.value = code;
        document.documentElement.lang = code;
        try { localStorage.setItem(KEY, code); } catch (_) { /* private mode: only for this visit */ }
        return code;
    }
    // The page waits for this before it starts, so texts made at start are translated too
    // (a browser that hasn't chosen yet asks the server, which knows the saved choice)
    const ready = (async () => {
        let code = null;
        try { code = localStorage.getItem(KEY); } catch (_) { /* no storage */ }
        if (!code) {
            try { code = (await (await fetch('/auth/status', { credentials: 'same-origin' })).json()).language; }
            catch (_) { code = 'en'; }
        }
        return code && code !== 'en' ? setLanguage(code) : 'en';
    })();

    // The locale for dates and numbers: the browser's own in English, else the language's
    const LOCALES = { it: 'it-IT' };
    const locale = () => LOCALES[lang.value];
    // A translated text with inline markup (<b>, <code>…), for v-html. The values are
    // escaped, so a project name or path can never inject markup: $th('In <code>{p}</code>', { p }).
    const ESC = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };
    function trh(text, vars) {
        const safe = {};
        for (const k in (vars || {})) safe[k] = String(vars[k] ?? '').replace(/[&<>"']/g, c => ESC[c]);
        return tr(text, safe);
    }
    // tk marks a text kept as data and translated where it is shown ($t(item.label))
    const tk = (text) => text;
    window.OptimuxI18n = { LANGS, lang, tr, trh, setLanguage, locale, ready };
    window.appLocale = locale;
    window.tr = tr;
    window.trh = trh;
    window.tk = tk;
})();
