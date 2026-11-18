// A team member's chat page (no framework): their project, the chat, approvals, and their own chats.
(function () {
    const $ = (id) => document.getElementById(id);
    const show = (el, on) => el.classList.toggle('hidden', !on);
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

    let ws = null, current = '', busy = false, agent = null, chatId = '', first = true, leaving = false, retry = 1000, projects = [];
    const chat = $('chat');

    function notice(text) { $('notice').textContent = text || ''; show($('notice'), !!text); }
    function scroll() { chat.scrollTop = chat.scrollHeight; }
    function add(cls, text) {
        const el = document.createElement('div');
        el.className = cls;
        el.textContent = text;
        chat.appendChild(el);
        return el;
    }
    const USER = 'self-end ml-auto max-w-[85%] bg-blue-900/40 border border-blue-800 rounded-xl px-3 py-2 text-sm text-gray-100 whitespace-pre-wrap break-words';
    const AGENT = 'max-w-[95%] space-y-2';
    const TEXT = 'text-sm text-gray-200 whitespace-pre-wrap break-words';
    const NOTE = 'text-[12px] text-gray-500 italic';
    const FAIL = 'text-sm text-red-300 bg-red-950/50 border border-red-900 rounded-lg px-3 py-2 whitespace-pre-wrap break-words';

    function empty(text) {
        chat.textContent = '';
        agent = null;
        add('text-[13px] text-gray-500 italic', text);
    }
    function agentBox() {
        if (!agent) {
            const box = document.createElement('div');
            box.className = AGENT;
            chat.appendChild(box);
            agent = { box, text: null, commands: [] };
        }
        return agent;
    }
    function putText(text) {
        const a = agentBox();
        if (!a.text) {
            a.text = document.createElement('div');
            a.text.className = TEXT;
            a.box.appendChild(a.text);
        }
        a.text.textContent += text;
    }
    function putNote(text, cls) {
        const a = agentBox();
        const el = document.createElement('div');
        el.className = cls || NOTE;
        el.textContent = text;
        a.box.appendChild(el);
        a.text = null;
    }
    function command(msg) {
        const a = agentBox();
        const d = document.createElement('details');
        d.className = 'bg-gray-900 border border-gray-800 rounded-lg';
        const s = document.createElement('summary');
        s.className = 'cursor-pointer px-3 py-1.5 text-[13px] font-mono text-gray-300 break-all';
        s.textContent = msg.command;
        const pre = document.createElement('pre');
        pre.className = 'px-3 py-2 text-[12px] text-gray-400 whitespace-pre-wrap break-words max-h-64 overflow-y-auto border-t border-gray-800';
        d.append(s, pre);
        a.box.appendChild(d);
        a.commands.push({ name: msg.command, pre, summary: s, done: false });
        a.text = null;
    }
    function commandOutput(msg) {
        const a = agentBox();
        const c = a.commands.find((x) => x.name === msg.command && !x.done);
        if (!c) return;
        c.done = true;
        c.pre.textContent = msg.output || '';
        if (msg.exit_code) c.summary.classList.add('text-red-300');
    }

    function setBusy(on) {
        busy = on;
        show($('stop'), on);
        show($('send'), !on);
        $('prompt').disabled = on;
        if (!on) { show($('approval'), false); $('prompt').focus(); }
    }

    function paintProjects(state) {
        projects = state.projects || [];
        current = state.project || '';
        const sel = $('project');
        sel.textContent = '';
        for (const name of projects) {
            const o = document.createElement('option');
            o.value = name; o.textContent = name;
            sel.appendChild(o);
        }
        sel.value = state.project || '';
    }
    async function paintChats() {
        const { res, data } = await api('/api/me/chats');
        if (!res.ok) return;
        const sel = $('chats');
        sel.textContent = '';
        const none = document.createElement('option');
        none.value = ''; none.textContent = tr('Open a chat…');
        sel.appendChild(none);
        for (const c of data.chats || []) {
            const o = document.createElement('option');
            o.value = c.id;
            o.textContent = (c.title || tr('Untitled chat')) + ' · ' + c.project;
            sel.appendChild(o);
        }
        sel.value = chatId || '';
    }

    function handle(m, replay) {
        switch (m.type) {
            case 'member_state':
                paintProjects(m);
                if (first) { first = false; send({ type: 'open_session', id: 'latest' }); }
                paintChats();
                break;
            case 'no_projects':
                notice(m.content);
                $('prompt').disabled = true; $('send').disabled = true; $('project').disabled = true;
                break;
            case 'sandbox_problem': notice(m.content); break;
            case 'session_loaded':
                chat.textContent = ''; agent = null;
                chatId = m.session ? m.session.id : '';
                if (!m.session) add('text-[13px] text-gray-500 italic', tr('A new chat. Ask for something below.'));
                for (const ev of m.events || []) handle(ev, true);
                if (m.read_only) putNote(m.reason || '');
                agent = null;
                setBusy(!!m.running);
                paintChats();
                scroll();
                break;
            case 'chat_reset':
                chatId = '';
                empty(tr('A new chat. Ask for something below.'));
                paintChats();
                break;
            case 'prompt': add(USER, m.content); agent = null; scroll(); break;
            case 'prompt_masked': { const u = [...chat.children].reverse().find((e) => e.className === USER); if (u) u.textContent = m.content; break; }
            case 'token': putText(m.content || ''); scroll(); break;
            case 'command_start': command(m); scroll(); break;
            case 'command_output': commandOutput(m); scroll(); break;
            case 'approval_request':
                if (replay) break;
                $('approval-text').textContent = m.command || '';
                $('reason').value = '';
                show($('approval'), true);
                scroll();
                break;
            case 'system': case 'budget': case 'step_limit':
                if (m.content) { putNote(m.content); scroll(); }
                break;
            case 'error': putNote('❌ ' + (m.content || ''), FAIL); scroll(); break;
            case 'done': agent = null; if (!replay) { setBusy(false); paintChats(); } break;
            default: break;      // usage, status, thinking, plan, changes... are for the full page
        }
    }

    function send(payload) {
        if (!ws || ws.readyState !== 1) { notice(tr('Not connected. Trying again…')); return false; }
        ws.send(JSON.stringify(payload));
        return true;
    }

    function connect() {
        const proto = location.protocol === 'https:' ? 'wss' : 'ws';
        ws = new WebSocket(proto + '://' + location.host + '/ws');
        ws.onopen = () => { retry = 1000; notice(''); if (!first && !busy) send({ type: 'open_session', id: chatId || 'latest' }); };
        ws.onmessage = (e) => { try { handle(JSON.parse(e.data), false); } catch (err) { console.error(err); } };
        ws.onclose = (e) => {
            if (leaving) return;
            if (e.code === 4401) { location.replace('/login'); return; }
            if (e.code === 4403) { if (!$('notice').textContent) check(); return; }
            setBusy(false);
            notice(tr('Not connected. Trying again…'));
            setTimeout(connect, retry);
            retry = Math.min(retry * 2, 15000);
        };
    }

    // The admin may disable the account or change the projects while the page is open
    function disabledNotice(detail) {
        if (leaving) return;
        leaving = true;
        notice((detail || tr('Your account has been disabled by the admin. Please contact your admin.')) + ' ' + tr('You will be signed out in a few seconds.'));
        $('prompt').disabled = true; $('send').disabled = true;
        setTimeout(async () => { await api('/auth/logout', {}); location.replace('/login?disabled=1'); }, 6000);
    }
    async function check() {
        const { res, data } = await api('/api/me');
        if (data.disabled) return disabledNotice(data.detail);
        if (res.status === 401) { location.replace('/login'); return; }
        if (res.ok && data.projects && data.projects.join('|') !== projects.join('|')) {
            const keep = $('project').value;
            paintProjects({ projects: data.projects, project: data.projects.includes(keep) ? keep : '' });
            if (!data.projects.includes(keep) && keep) notice(tr('You are no longer on this project. Pick another one.'));
        }
    }

    $('form').addEventListener('submit', (e) => {
        e.preventDefault();
        const text = $('prompt').value.trim();
        if (!text || busy) return;
        if (!send({ prompt: text, model: 'auto' })) return;
        notice('');
        add(USER, text);
        agent = null;
        $('prompt').value = '';
        setBusy(true);
        scroll();
    });
    $('prompt').addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); $('form').requestSubmit(); }
    });
    $('stop').addEventListener('click', () => send({ type: 'stop' }));
    const answer = (approved) => {
        show($('approval'), false);
        send({ type: 'approval_response', approved, reason: approved ? '' : $('reason').value.trim() });
    };
    $('approve').addEventListener('click', () => answer(true));
    $('decline').addEventListener('click', () => answer(false));
    $('project').addEventListener('change', () => {
        const wanted = $('project').value;
        if (busy) { notice(tr('Still working on the previous request. Stop it first.')); $('project').value = current; return; }
        send({ type: 'switch_project', project: wanted });
    });
    $('new-chat').addEventListener('click', () => { if (!busy) send({ type: 'new_chat' }); });
    $('chats').addEventListener('change', () => { const id = $('chats').value; if (id && !busy) send({ type: 'open_session', id }); });
    $('sign-out').addEventListener('click', async () => { leaving = true; await api('/auth/logout', {}); location.replace('/login'); });

    async function start() {
        const status = await api('/auth/status');
        await translate(status.data.language);
        $('prompt').placeholder = tr('Ask for something. Enter sends, Shift+Enter adds a line.');
        $('reason').placeholder = tr('Reason (optional)');
        const { res, data } = await api('/api/me');
        if (data.disabled) { disabledNotice(data.detail); return; }
        if (!res.ok || !data.user || data.admin) { location.replace('/login'); return; }
        $('who').textContent = data.user.name;
        empty(tr('Connecting…'));
        connect();
        setInterval(check, 5000);
    }
    start();
})();
