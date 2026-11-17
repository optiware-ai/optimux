// A team member's account page (no framework): who they are, what the admin may see, and when the admin looked.
(function () {
    const $ = (id) => document.getElementById(id);
    const show = (el, on) => el.classList.toggle('hidden', !on);
    let messages = {};
    const tr = (text) => messages[text] || text;
    const tk = (text) => text;      // marks a text kept as data; translated with tr() where it is used
    const norm = (html) => html.replace(/\s+/g, ' ').trim();
    async function translate(code) {
        if (!code || code === 'en') return;
        try {
            const res = await fetch('/static/i18n/' + code + '.json');
            if (!res.ok) return;
            messages = await res.json();
        } catch (_) { return; }
        document.documentElement.lang = code;
        for (const el of document.querySelectorAll('[data-i18n]')) {
            const out = messages[norm(el.innerHTML)];
            if (out) el.innerHTML = out;
        }
    }
    const api = async (url, body) => {
        const res = await fetch(url, body === undefined ? { credentials: 'same-origin' } :
            { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), credentials: 'same-origin' });
        let data = {};
        try { data = await res.json(); } catch (_) {}
        return { res, data };
    };
    const fail = (msg) => { $('error').textContent = msg; show($('error'), true); };
    const when = (t) => new Date(t * 1000).toLocaleString();
    const KINDS = { chat: tk('A chat'), sent: tk('What was sent to the models'), usage: tk('Usage') };
    const radios = () => document.querySelectorAll('input[name="chats"]');

    function paintConsent(user) {
        for (const r of radios()) r.checked = (r.value === '1') === !!user.chat_access;
        $('current').textContent = user.chat_access ? tr('Admin may also read my chats') : tr('Admin sees usage and cost only');
        $('since').textContent = user.consent ? tr('Chosen on') + ' ' + when(user.consent.at) : '';
    }

    async function paintAudit() {
        const { data } = await api('/api/me/audit');
        const lines = data.lines || [];
        show($('audit-empty'), !lines.length);
        show($('audit'), !!lines.length);
        const body = $('audit-rows');
        body.textContent = '';
        for (const line of lines) {
            const tr_ = document.createElement('tr');
            tr_.className = 'border-t border-gray-800';
            for (const text of [when(line.at), line.admin || '', tr(KINDS[line.kind] || line.kind), line.ref || '']) {
                const td = document.createElement('td');
                td.className = 'py-1 pr-3 text-gray-300';
                td.textContent = text;
                tr_.appendChild(td);
            }
            body.appendChild(tr_);
        }
    }

    async function init() {
        let st = {};
        try { st = await (await fetch('/auth/status', { credentials: 'same-origin' })).json(); } catch (_) {}
        await translate(st.language);
        const { res, data } = await api('/api/me');
        if (data.disabled) { disabledNotice(data.detail); return; }
        if (!res.ok || !data.user || data.admin) { location.replace('/login'); return; }
        $('who').textContent = data.user.name;
        $('consent-text').textContent = data.consent_text || '';
        paintConsent(data.user);
        await paintAudit();
        setInterval(watchAccount, 5000);
    }

    // The admin may disable this account while the page is open: say so clearly, then sign out for real
    let leaving = false;
    function disabledNotice(detail) {
        if (leaving) return;
        leaving = true;
        $('disabled-note').textContent = (detail || tr('Your account has been disabled by the admin. Please contact your admin.')) +
            ' ' + tr('You will be signed out in a few seconds.');
        show($('disabled-note'), true);
        for (const id of ['save']) $(id).disabled = true;
        setTimeout(async () => {
            await api('/auth/logout', {});
            location.replace('/login?disabled=1');
        }, 6000);
    }
    async function watchAccount() {
        if (leaving) return;
        const { res, data } = await api('/api/me');
        if (data.disabled) disabledNotice(data.detail);
        else if (res.status === 401) location.replace('/login');
    }

    $('save').addEventListener('click', async () => {
        show($('error'), false);
        show($('saved'), false);
        const allow = document.querySelector('input[name="chats"]:checked');
        if (!allow) return;
        $('save').disabled = true;
        const { res, data } = await api('/api/me/consent', { chat_access: allow.value === '1' });
        $('save').disabled = false;
        if (!res.ok) { fail(data.detail || tr('Could not save.')); return; }
        paintConsent(data);
        show($('saved'), true);
    });
    // "Saved." belongs to the last save only: a new click on a choice clears it
    for (const r of radios()) r.addEventListener('change', () => show($('saved'), false));

    $('sign-out').addEventListener('click', async () => {
        await api('/auth/logout', {});
        location.replace('/login');
    });

    init();
})();
