// Sign-in page (no framework). The server sets an HttpOnly session cookie on success.
(function () {
    const $ = (id) => document.getElementById(id);
    const show = (el, on) => el.classList.toggle('hidden', !on);
    // Where to go after signing in: only a page of this server, never another site
    // (the URL parser decides, so tricks like "/\\evil.com" or "/\t/evil.com" resolve elsewhere and are refused).
    const next = () => {
        // A redirect from the server keeps the page's #section in this address
        const n = new URLSearchParams(location.search).get('next') || ('/' + location.hash);
        if (!n.startsWith('/') || /[\\\x00-\x1f]/.test(n)) return '/';
        try {
            const u = new URL(n, location.origin);
            if (u.origin !== location.origin || u.pathname.startsWith('//')) return '/';
            return u.href;   // absolute, so it can't be read as another site
        } catch (_) { return '/'; }
    };
    const fail = (msg) => { $('error').textContent = msg; show($('error'), true); };
    const post = async (url, body) => {
        const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' },
                                       body: JSON.stringify(body), credentials: 'same-origin' });
        let data = {};
        try { data = await res.json(); } catch (_) {}
        return { res, data };
    };
    const local = ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname);

    // v0.8: the page's language: the server's choice, else this browser's last one.
    // Texts are English in the page (elements marked data-i18n) and in the tr calls below;
    // web/i18n/<code>.json maps them to the translation.
    let messages = {};
    const tr = (text) => messages[text] || text;
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
            if (out) el.innerHTML = out;     // our own file, same origin: never user text
        }
    }
    let early = 'en';
    try { early = localStorage.getItem('optimux.lang') || 'en'; } catch (_) { /* no storage */ }

    async function init() {
        let st;
        try { st = await (await fetch('/auth/status', { credentials: 'same-origin' })).json(); }
        catch (_) { await translate(early); $('subtitle').textContent = tr('The server is not reachable.'); return; }
        await translate(st.language || early);
        show($('http-warning'), location.protocol !== 'https:' && !local);
        // An invitation link keeps its token after the "#", which browsers never send to the server
        if (await openInvitation()) return;
        if (new URLSearchParams(location.search).has('disabled')) {
            history.replaceState(null, '', location.pathname);
            fail(tr('Your account has been disabled by the admin. Please contact your admin.'));
        }
        // A member who opens the admin door is shown the form (signing in there replaces their session)
        const adminDoor = new URLSearchParams(location.search).has('admin');
        if (st.signed_in && !(st.member && adminDoor)) { location.replace(next()); return; }
        teamMode = !!st.team;
        if (st.setup_needed) {
            $('subtitle').textContent = tr('Set up your password');
            show($('setup-form'), true);
            $('setup-token').focus();
        } else if (teamMode && !new URLSearchParams(location.search).has('admin')) {
            $('subtitle').textContent = tr('Sign in');
            show($('member-form'), true);
            $('member-name').focus();
        } else {
            $('subtitle').textContent = teamMode ? tr('Admin sign-in') : tr('Sign in');
            show($('login-form'), true);
            show($('to-member-row'), teamMode);
            show($('code-row'), !!st.totp);
            $('login-password').focus();
        }
    }
    let teamMode = false;
    let inviteToken = '';

    // The link's token is read once and removed from the address. Opening a link while this page is
    // already showing changes only the part after "#", so that is watched too.
    async function openInvitation() {
        const invite = new URLSearchParams(location.hash.slice(1)).get('invite');
        if (!invite) return false;
        history.replaceState(null, '', location.pathname);
        for (const id of ['setup-form', 'member-form', 'login-form']) show($(id), false);
        show($('error'), false);
        await showInvitation(invite);
        return true;
    }
    window.addEventListener('hashchange', openInvitation);

    async function showInvitation(token) {
        const { res, data } = await post('/auth/invite', { token });
        if (!res.ok) {
            $('subtitle').textContent = tr('Invitation');
            fail(data.detail || tr('This invitation does not work.'));
            return;
        }
        inviteToken = token;
        $('subtitle').textContent = tr('Create your account');
        $('invite-name').textContent = data.name;
        $('invite-consent').textContent = data.consent.text;
        show($('invite-form'), true);
        $('invite-password').focus();
    }

    $('invite-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        if ($('invite-password').value !== $('invite-password2').value) { fail(tr('The two passwords differ.')); return; }
        const chats = document.querySelector('input[name="invite-chats"]:checked').value === '1';
        const { res, data } = await post('/auth/activate', { token: inviteToken, password: $('invite-password').value, chat_access: chats });
        if (res.ok) location.replace('/');
        else fail(data.detail || tr('This invitation does not work.'));
    });

    $('member-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        const { res, data } = await post('/auth/login', { name: $('member-name').value.trim(), password: $('member-password').value });
        if (res.ok) { location.replace(next()); return; }
        fail(data.detail || tr('Sign-in failed.'));
        $('member-password').value = '';
    });

    $('setup-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        if ($('setup-password').value !== $('setup-password2').value) { fail(tr('The two passwords differ.')); return; }
        const { res, data } = await post('/auth/setup', { token: $('setup-token').value.trim(), password: $('setup-password').value });
        if (res.ok) location.replace(next());
        else fail(data.detail || tr('Setup failed.'));
    });

    $('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        const { res, data } = await post('/auth/login', { password: $('login-password').value, code: $('login-code').value.trim(), admin: teamMode });
        if (res.ok) { location.replace(next()); return; }
        if (data.need_code) {
            show($('code-row'), true);
            $('login-code').focus();
            fail(tr('Enter the 6-digit code from your authenticator app.'));
            return;
        }
        fail(data.detail || tr('Sign-in failed.'));
        $('login-code').value = '';
    });

    init();
})();
