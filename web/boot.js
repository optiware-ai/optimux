// Runs before the app. Signing in uses an HttpOnly session cookie (set by /login),
// so no secret is ever stored where page scripts can read it.
(function () {
    // Before Feature 23 the access token was kept in localStorage; remove it so
    // it doesn't linger where any script on the page could read it.
    try { localStorage.removeItem('optimux_token'); } catch (_) {}
    let leaving = false;
    window.optimuxToLogin = () => {
        if (leaving) return;
        leaving = true;
        location.href = '/login?next=' + encodeURIComponent(location.pathname + location.hash);
    };
    const origFetch = window.fetch.bind(window);
    window.fetch = async (input, init = {}) => {
        const res = await origFetch(input, init);
        const url = typeof input === 'string' ? input : input.url;
        const ours = url.startsWith('/api/') || url.startsWith(location.origin + '/api/');
        if (ours && res.status === 401) {
            window.optimuxToLogin();       // session expired or signed out elsewhere
            throw new Error('Unauthorized');
        }
        if (ours && res.status === 400) {
            // Validation errors (bad path, bad env key...): show them and
            // throw so the caller's catch skips its "Saved" message.
            let msg = 'Request rejected by the server.';
            try { msg = (await res.clone().json()).detail || msg; } catch (_) {}
            // The app shows it as a notice; before the app has started, a plain alert
            if (typeof window.optimuxShowError === 'function') window.optimuxShowError(msg); else alert(msg);
            throw new Error(msg);
        }
        return res;
    };
    // Everything shown as HTML (model replies, the guide, charts) passes through this.
    // With codeButtons, each <pre> also gets a "View" button, added to the cleaned
    // DOM (not by editing the HTML string, which could reopen what was removed).
    // codeButtons: 'copy' puts a bar above each block instead, with the language
    // and a Copy button, so the button never covers the code (used outside chat).
    const PURIFY = {
        FORBID_TAGS: ['style', 'form', 'input', 'button', 'textarea', 'select'],
        FORBID_ATTR: ['style'],
    };
    window.optimuxSafeHtml = (html, opts = {}) => {
        if (!opts.codeButtons) return window.DOMPurify.sanitize(html, PURIFY);
        const frag = window.DOMPurify.sanitize(html, { ...PURIFY, RETURN_DOM_FRAGMENT: true });
        frag.querySelectorAll('pre').forEach((pre) => {
            if (opts.codeButtons === 'copy') {
                const box = document.createElement('div');
                box.className = 'cm-pre-bar';
                const bar = document.createElement('div');
                bar.className = 'cm-bar';
                const lang = document.createElement('span');
                const m = ((pre.querySelector('code') || {}).className || '').match(/language-([\w+#-]+)/);
                lang.textContent = m ? m[1] : '';
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.className = 'cm-copy-btn';
                btn.setAttribute('data-cm-copy', '');
                btn.title = 'Copy to the clipboard';
                btn.textContent = 'Copy';
                bar.append(lang, btn);
                pre.replaceWith(box);
                box.append(bar, pre);
                return;
            }
            const box = document.createElement('div');
            box.className = 'cm-pre';
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'cm-view-btn';
            btn.setAttribute('data-cm-view', '');
            btn.title = 'Open in the code viewer';
            btn.textContent = 'View';
            pre.replaceWith(box);
            box.append(btn, pre);
        });
        const out = document.createElement('div');
        out.appendChild(frag);
        return out.innerHTML;
    };
})();
