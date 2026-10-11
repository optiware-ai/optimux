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

    async function init() {
        let st;
        try { st = await (await fetch('/auth/status', { credentials: 'same-origin' })).json(); }
        catch (_) { $('subtitle').textContent = 'The server is not reachable.'; return; }
        if (st.signed_in) { location.replace(next()); return; }
        show($('http-warning'), location.protocol !== 'https:' && !local);
        if (st.setup_needed) {
            $('subtitle').textContent = 'Set up your password';
            show($('setup-form'), true);
            $('setup-token').focus();
        } else {
            $('subtitle').textContent = 'Sign in';
            show($('login-form'), true);
            show($('code-row'), !!st.totp);
            $('login-password').focus();
        }
    }

    $('setup-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        if ($('setup-password').value !== $('setup-password2').value) { fail('The two passwords differ.'); return; }
        const { res, data } = await post('/auth/setup', { token: $('setup-token').value.trim(), password: $('setup-password').value });
        if (res.ok) location.replace(next());
        else fail(data.detail || 'Setup failed.');
    });

    $('login-form').addEventListener('submit', async (e) => {
        e.preventDefault();
        show($('error'), false);
        const { res, data } = await post('/auth/login', { password: $('login-password').value, code: $('login-code').value.trim() });
        if (res.ok) { location.replace(next()); return; }
        if (data.need_code) {
            show($('code-row'), true);
            $('login-code').focus();
            fail('Enter the 6-digit code from your authenticator app.');
            return;
        }
        fail(data.detail || 'Sign-in failed.');
        $('login-code').value = '';
    });

    init();
})();
