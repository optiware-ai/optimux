const { createApp, ref, onMounted, nextTick, watch } = Vue;

// A small "?" that shows help in a floating box (hover, or click to pin; outside click closes).
// Fixed positioning, so scroll containers never clip it.
const HelpTip = {
    props: { text: { type: String, default: '' }, wide: Boolean },
    data: () => ({ hover: false, pinned: false, pos: {} }),
    computed: { open() { return this.hover || this.pinned; } },
    methods: {
        place() {
            const r = this.$refs.btn.getBoundingClientRect();
            const w = this.wide ? 420 : 300;
            const left = Math.max(8, Math.min(r.left - 12, window.innerWidth - w - 8));
            const below = r.bottom + 8 + 200 < window.innerHeight;
            this.pos = below ? { left: left + 'px', top: (r.bottom + 6) + 'px', width: w + 'px' }
                             : { left: left + 'px', bottom: (window.innerHeight - r.top + 6) + 'px', width: w + 'px' };
        },
        enter() { this.place(); this.hover = true; },
        toggle() { this.place(); this.pinned = !this.pinned; },
        outside(e) { if (this.pinned && !this.$el.contains(e.target)) this.pinned = false; },
        close() { this.hover = false; this.pinned = false; },
    },
    mounted() { document.addEventListener('click', this.outside, true); window.addEventListener('scroll', this.close, true); },
    unmounted() { document.removeEventListener('click', this.outside, true); window.removeEventListener('scroll', this.close, true); },
    template: `<span class="cm-help" @mouseenter="enter" @mouseleave="hover = false"><button ref="btn" type="button" class="cm-help-btn" :class="{ 'cm-help-on': open }" @click.stop.prevent="toggle" aria-label="Help">?</button><span v-if="open" class="cm-help-box" :style="pos" @click.stop><slot>{{ text }}</slot></span></span>`,
};
// Feature 25: the New project form. Used in the chat (filled in by the model, it
// replaces the approval box) and in the New project dialog (no model involved).
// The server checks every value again; this only helps fill it in.
// What each role in Usage → By role is (shown on hover)
const ROLE_HELP = {
    conductor: 'the small call per message that decides whether the Simple or the Complex model handles it.',
    simple: 'replies and tool steps by the Simple model (most everyday requests).',
    complex: 'replies and tool steps by the Complex model: hard tasks, escalations (automatic or Escalate ↑) and /complex.',
    override: 'requests where you picked a specific model in the chat instead of the Auto-Router.',
    curator: 'automatic project memory updates when a chat ends (and "Update memory now").',
    skill_draft: 'drafting a skill from a chat with "Save as skill".',
    test: 'the Test button on a model in Dashboard → Models (one tiny call).',
};
const PROJECT_NAME_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/;
const ProjectForm = {
    props: {
        initial: { type: Object, default: () => ({}) },
        github: { type: Object, default: () => ({}) },
        error: { type: String, default: '' },
        busy: Boolean,
        root: { type: String, default: '' },
        activeProject: { type: String, default: '' },
        existing: { type: Array, default: () => [] },
        compact: Boolean,      // two columns on wide screens (in the chat)
    },
    emits: ['submit', 'cancel', 'setup-github'],
    data() {
        const f = this.initial || {};
        return { f: {
            name: f.name || '', template: f.template || 'basic', git_init: f.git_init !== false,
            github: f.github || '', description: f.description || '', switch: f.switch !== false,
        }, githubWanted: !!f.github };
    },
    computed: {
        ghReady() { return !!(this.github && this.github.ready); },
        nameProblem() {
            const n = this.f.name.trim();
            if (!n) return '';
            if (!PROJECT_NAME_RE.test(n)) return 'Use letters, digits, . _ or - (not starting with . or -).';
            if (this.existing.includes(n)) return 'A project with this name already exists.';
            return '';
        },
        canSubmit() { return !!this.f.name.trim() && !this.nameProblem && !this.busy && !(this.f.github && !this.ghReady); },
        // The end of the path, so the name stays visible with a long Workspace path
        where() {
            const parts = (this.root || '').replace(/\/+$/, '').split('/').filter(Boolean);
            const base = parts.length ? (parts.length > 1 ? '…/' : '/') + parts[parts.length - 1] + '/' : '';
            return base + (this.f.name.trim() || '…');
        },
    },
    watch: {
        'f.git_init'(on) { if (!on) this.f.github = ''; },
    },
    mounted() {
        if (this.githubWanted && !this.ghReady) this.f.github = '';
        this.$nextTick(() => { const el = this.$refs.name; if (el && !this.f.name) el.focus(); });
    },
    methods: {
        setGithub(v) { this.f.github = v; if (v) this.f.git_init = true; },
        submit() { if (this.canSubmit) this.$emit('submit', { ...this.f, name: this.f.name.trim(), description: this.f.description.trim() }); },
    },
    template: `
<form @submit.prevent="submit" class="space-y-4 text-sm">
 <div :class="compact ? 'grid md:grid-cols-2 gap-x-6 gap-y-4 items-start' : 'space-y-4'">
 <div class="space-y-4">
  <div>
    <label class="block text-xs uppercase tracking-wider text-gray-400 mb-1">Name</label>
    <input ref="name" v-model="f.name" maxlength="100" placeholder="my-project" spellcheck="false" autocomplete="off"
           class="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 font-mono text-gray-100 focus:outline-none focus:border-blue-500">
    <div class="mt-1 text-xs font-mono truncate" :class="nameProblem ? 'text-red-400' : 'text-gray-500'" :title="nameProblem ? '' : root">{{ nameProblem || where }}</div>
  </div>
  <div>
    <label class="block text-xs uppercase tracking-wider text-gray-400 mb-1">Starter files</label>
    <div class="grid grid-cols-2 gap-2">
      <button type="button" v-for="t in [['basic','Basic','README and .gitignore'],['python','Python','also src/ and tests/']]" :key="t[0]" @click="f.template = t[0]"
              :class="['text-left rounded-lg border px-3 py-2 transition', f.template === t[0] ? 'border-blue-500 bg-blue-950/40' : 'border-gray-700 hover:border-gray-500']">
        <div class="font-bold text-gray-100">{{ t[1] }}</div><div class="text-xs text-gray-400">{{ t[2] }}</div>
      </button>
    </div>
  </div>
  <div class="flex items-center justify-between gap-3">
    <span><span class="text-gray-100">Git repository</span><span class="block text-xs text-gray-500">git init and a first commit{{ f.github ? ' (needed for GitHub)' : '' }}</span></span>
    <button type="button" @click="f.git_init = !f.git_init" role="switch" :aria-checked="f.git_init" aria-label="Git repository"
            :class="['relative w-10 h-6 rounded-full transition flex-shrink-0', f.git_init ? 'bg-blue-600' : 'bg-gray-700']">
      <span :class="['absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition', f.git_init ? 'translate-x-4' : '']"></span>
    </button>
  </div>
  </div>
  <div class="space-y-4">
  <div :class="['rounded-lg border p-3 transition', f.github ? 'border-blue-800 bg-blue-950/20' : 'border-gray-800']">
    <div class="flex items-center justify-between gap-3">
      <span><span class="text-gray-100">Also on GitHub</span>
        <span v-if="ghReady" class="block text-xs text-gray-500">{{ github.login ? 'as @' + github.login : (github.source === 'gh' ? "with gh's own sign-in" : 'with your saved token') }}, pushed after the first commit</span>
        <span v-else class="block text-xs text-yellow-500/90">{{ github.reason || 'Checking GitHub…' }} <button v-if="github.reason" type="button" class="underline text-blue-400 hover:text-blue-300" @click="$emit('setup-github')">Set up GitHub</button></span>
      </span>
      <button type="button" :disabled="!ghReady" @click="setGithub(f.github ? '' : 'private')" role="switch" :aria-checked="!!f.github" aria-label="Also on GitHub"
              :class="['relative w-10 h-6 rounded-full transition flex-shrink-0 disabled:opacity-40', f.github ? 'bg-blue-600' : 'bg-gray-700']">
        <span :class="['absolute top-1 left-1 w-4 h-4 rounded-full bg-white transition', f.github ? 'translate-x-4' : '']"></span>
      </button>
    </div>
    <div v-if="f.github" class="grid grid-cols-2 gap-2 mt-3">
      <label v-for="v in [['private','🔒 Private','Only you and people you invite'],['public','🌐 Public','Anyone can see it']]" :key="v[0]"
             :class="['rounded-lg border px-3 py-2 cursor-pointer transition', f.github === v[0] ? 'border-blue-500 bg-blue-950/40' : 'border-gray-700 hover:border-gray-500']">
        <input type="radio" class="sr-only" :value="v[0]" v-model="f.github">
        <div class="font-bold text-gray-100">{{ v[1] }}</div><div class="text-xs text-gray-400">{{ v[2] }}</div>
      </label>
    </div>
  </div>
  <div>
    <label class="flex justify-between text-xs uppercase tracking-wider text-gray-400 mb-1"><span>Description <span class="normal-case tracking-normal text-gray-500">(optional, you can leave it empty for now)</span></span><span class="font-mono normal-case" :class="f.description.length > 350 ? 'text-red-400' : 'text-gray-600'">{{ f.description.length }}/350</span></label>
    <input v-model="f.description" maxlength="350" placeholder="What is it for?"
           class="w-full bg-gray-950 border border-gray-700 rounded-lg px-3 py-2 text-gray-100 focus:outline-none focus:border-blue-500">
  </div>
  <label class="flex items-center gap-2 cursor-pointer text-gray-300">
    <input type="checkbox" v-model="f.switch" class="w-4 h-4 accent-blue-600">
    Open it when it's ready<span class="text-xs text-gray-500">{{ activeProject ? '(leaves ' + activeProject + '; that chat stays in History)' : '' }}</span>
  </label>
  </div>
 </div>
  <div v-if="error" class="rounded-lg border border-red-800 bg-red-950/40 px-3 py-2 text-red-300">{{ error }}</div>
  <div class="flex justify-end gap-3 pt-1">
    <button type="button" @click="$emit('cancel')" class="px-4 py-2 rounded-lg border border-gray-700 text-gray-300 hover:border-gray-500">Cancel</button>
    <button type="submit" :disabled="!canSubmit" class="px-5 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-bold disabled:opacity-40 flex items-center gap-2">
      <span v-if="busy" class="w-3.5 h-3.5 rounded-full border-2 border-white/40 border-t-white animate-spin"></span>{{ busy ? 'Creating…' : 'Create project' }}
    </button>
  </div>
</form>`,
};
// fix25a: marks a deleted project whose chats, costs and memory were kept
const DeletedTag = {
    props: { at: { type: Number, default: 0 } },
    template: `<span v-if="at" class="ml-1.5 inline-block align-middle text-[10px] font-sans uppercase tracking-wider text-red-300/90 bg-red-950/50 border border-red-900/70 rounded px-1 py-px" :title="'Deleted on ' + new Date(at * 1000).toLocaleDateString() + '. Its folder is gone; its chats, costs and memory were kept.'">deleted</span>`,
};
createApp({
    setup() {
        const ws = ref(null);
        const userInput = ref('');
        const chatInputRef = ref(null);
        const messages = ref([{ role: 'agent', content: 'Optimux initialized. Configuration loaded. New here? Open the **Guide** with the **?** in the header.', system_note: 'System', commands: [] }]);
        const isGenerating = ref(false);
        
        const selectedModel = ref('auto');
        const availableModels = ref([]);
        const setupModels = ref([]);
        const routingConfig = ref({ conductor_model: '', simple_model: '', complex_model: '' });
        const newModelPrices = ref({ input_cost_per_mtok: '', output_cost_per_mtok: '', cached_input_cost_per_mtok: '' });
        const modelNotices = ref({});
        const testingModels = ref({});
        const editingPrices = ref({});
        const usageData = ref(null);
        const usagePeriod = ref('today');
        const cooldowns = ref({});
        const sessionUsage = ref({ tokens: 0, cost: 0, complete: true });
        const projectUsage = ref(null);
        const pendingAction = ref('');
        const pendingForm = ref(null);   // Feature 25: { form, error, github, active_project, key } for create_project
        let formSeq = 0;
        let moveChatTo = null;           // the model switched projects: open that project's chat when the reply ends

        // ---- Feature 25: toasts (slide in, leave by themselves)
        const toasts = ref([]);
        let toastSeq = 0;
        const dismissToast = (id) => { toasts.value = toasts.value.filter(t => t.id !== id); };
        const pushToast = (t) => {
            const id = ++toastSeq;
            toasts.value.push({ id, kind: 'success', text: '', ...t });
            setTimeout(() => dismissToast(id), t.timeout || (t.kind === 'warn' ? 12000 : 6000));
        };
        const projectCreatedToast = (name, ok, url) => pushToast(ok
            ? { kind: 'success', title: `Project created · ${name}`, text: url ? 'Its GitHub repository is ready.' : '', link: url || '', linkLabel: 'Open on GitHub' }
            : { kind: 'warn', title: `${name} created, with problems`, text: 'The folder is there, but some steps failed; the details are in the chat.' });

        // ---- Feature 25: GitHub (Dashboard → GitHub) and the New project dialog
        const githubInfo = ref({});
        const githubToken = ref('');
        const githubMsg = ref(null);     // { ok, text }
        const githubBusy = ref(false);
        const gitIdentity = ref({ git_name: '', git_email: '', msg: null });
        const fetchGithub = async (fresh = false) => {
            try {
                githubInfo.value = await (await fetch('/api/github' + (fresh ? '?fresh=1' : ''))).json();
                gitIdentity.value = { git_name: githubInfo.value.git_name || '', git_email: githubInfo.value.git_email || '', msg: null };
            } catch (_) {}
        };
        // Who commits: saved on its own (never with the other settings)
        const saveGitIdentity = async () => {
            const g = gitIdentity.value;
            try {
                await postJson('/api/github/identity', { git_name: g.git_name, git_email: g.git_email });
                g.msg = { ok: true, text: g.git_name ? `Commits are made as ${g.git_name} <${g.git_email}>.` : "Commits use git's own settings." };
            } catch (e) { g.msg = { ok: false, text: e.message }; }
        };
        const wsState = ref('connecting');  // connecting | connected | disconnected
        const storageInfo = ref(null);
        const budgetStatus = ref(null);   // budget of the active project, if any
        const budgets = ref({});          // all budgets with this month's status
        const budgetEdits = ref({});      // form values per project in the Usage tab
        const showRoutingSaved = ref(false);
        
        const showSetupModal = ref(false);
        const modalInstructions = ref('');
        const keyInput = ref('');
        const setupEnvKey = ref('');
        const isSavingKey = ref(false);

        const showSettingsModal = ref(false);
        const settingsTab = ref('overview');
        const newModelId = ref('');
        const newModelName = ref('');
        const newModelEnvKey = ref('');
        const newModelApiKey = ref('');
        const editingKeys = ref({});

        const appSettings = ref({ projects_root: '', active_project: '', auto_approve: false, max_steps_simple: 8, max_steps_complex: 25 });
        const projectsList = ref([]);
        const showSettingsSaved = ref(false);
        
        const memoryTab = ref('personal');
        const memoryContent = ref('');
        const showMemorySaved = ref(false);

        const pendingApproval = ref(false);
        const pendingCommand = ref('');
        const pendingOutside = ref([]);
        const pendingWorkdir = ref('');
        const pendingSandboxed = ref(false);
        // ---- Sandbox (bubblewrap)
        const sandboxInfo = ref({});
        const sandboxPathsText = ref('');
        const fetchSandbox = async (refresh = false) => {
            try { sandboxInfo.value = await (await fetch('/api/sandbox' + (refresh === true ? '?refresh=true' : ''))).json(); }
            catch (error) { console.error('Failed to check the sandbox', error); }
            sandboxPathsText.value = (appSettings.value.sandbox_ro_paths || []).join('\n');
        };
        watch(() => appSettings.value.sandbox_ro_paths, (v) => { sandboxPathsText.value = (v || []).join('\n'); });
        const reloadSettings = async () => {
            try { Object.assign(appSettings.value, await (await fetch('/api/settings')).json()); } catch (_) {}
        };
        const toggleSandbox = async (on) => {
            appSettings.value.sandbox = on;
            if (!await saveSettings()) appSettings.value.sandbox = !on;   // refused (e.g. bubblewrap missing)
            fetchSandbox(true);
        };
        const saveSandboxPaths = async () => {
            appSettings.value.sandbox_ro_paths = sandboxPathsText.value.split('\n').map(x => x.trim()).filter(Boolean);
            await saveSettings();
            await reloadSettings();    // the server's version (normalized, or unchanged if refused)
            fetchSandbox();
        };

				const renderMarkdown = (text) => {
            if (!text) return '';
            // Model output is untrusted: sanitized, with our own View button on each
            // fenced code block (handled by onChatClick)
            return optimuxSafeHtml(marked.parse(text), { codeButtons: true });
        };

        // The projects root is shown as "Workspace" (the backend keeps '' / '(no project)')
        const projectLabel = (p) => (!p || p === '(no project)') ? 'Workspace' : p;

        // ---- Code viewer (side panel with syntax highlighting)
        const viewer = ref({ open: false, title: '', subtitle: '', lang: 'plaintext', wrap: false, sections: [] });
        const viewerLanguages = [
            ['auto', 'Auto-detect'], ['plaintext', 'Plain text'], ['bash', 'Bash'], ['python', 'Python'],
            ['javascript', 'JavaScript'], ['typescript', 'TypeScript'], ['json', 'JSON'], ['xml', 'HTML / XML / Vue'],
            ['css', 'CSS'], ['markdown', 'Markdown'], ['yaml', 'YAML'], ['ini', 'INI / TOML'], ['diff', 'Diff'],
            ['sql', 'SQL'], ['go', 'Go'], ['rust', 'Rust'], ['java', 'Java'], ['c', 'C'], ['cpp', 'C++'],
        ];
        const EXT_LANG = { py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
            ts: 'typescript', tsx: 'typescript', json: 'json', html: 'xml', htm: 'xml', vue: 'xml', xml: 'xml', svg: 'xml',
            css: 'css', md: 'markdown', yml: 'yaml', yaml: 'yaml', toml: 'ini', ini: 'ini', cfg: 'ini', env: 'bash',
            sh: 'bash', bash: 'bash', zsh: 'bash', diff: 'diff', patch: 'diff', sql: 'sql', go: 'go', rs: 'rust',
            java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp' };
        const LANG_ALIAS = { sh: 'bash', shell: 'bash', zsh: 'bash', console: 'bash', py: 'python', js: 'javascript',
            ts: 'typescript', html: 'xml', vue: 'xml', yml: 'yaml', toml: 'ini', text: 'plaintext', txt: 'plaintext', md: 'markdown' };
        const escHtml = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        // Guess the language of a command's output from the command itself
        const guessOutputLang = (command, output) => {
            const cmd = (command || '').trim();
            // Look at each part of a chained command, last first ("cd x && cat y.py")
            const parts = cmd.split(/&&|\|\||;|\|/).map(x => x.trim()).reverse();
            if (/\bgit\s+(diff|show)\b|\bdiff\s+-u\b|\bgit\s+log\b.*\s-p\b/.test(cmd)) return 'diff';
            for (const part of parts) {
                if (!/^(cat|head|tail|nl|less|more|bat|sed\s+-n)\b/.test(part)) continue;
                const m = part.match(/([\w.\-\/]+)\.([A-Za-z0-9]+)\s*(?:>.*)?$/);
                if (m && EXT_LANG[m[2].toLowerCase()]) return EXT_LANG[m[2].toLowerCase()];
            }
            const o = (output || '').trim();
            if (o && '{['.includes(o[0])) { try { JSON.parse(o); return 'json'; } catch (_) {} }
            return 'plaintext';
        };
        // Highlighted HTML split into numbered lines; spans that cross a newline are closed and reopened
        const highlightLines = (text, lang) => {
            let html;
            const hl = window.hljs;
            if (!hl || lang === 'plaintext' || text.length > 400000) html = escHtml(text);
            else if (lang === 'auto') html = text.length > 100000 ? escHtml(text) : hl.highlightAuto(text).value;
            else if (hl.getLanguage(lang)) html = hl.highlight(text, { language: lang, ignoreIllegals: true }).value;
            else html = escHtml(text);
            const out = [];
            const open = [];
            let line = '';
            const re = /(<span[^>]*>)|(<\/span>)|(\n)|([^<\n]+)/g;
            let m;
            while ((m = re.exec(html)) !== null) {
                if (m[1]) { open.push(m[1]); line += m[1]; }
                else if (m[2]) { open.pop(); line += m[2]; }
                else if (m[3]) { out.push(line + '</span>'.repeat(open.length)); line = open.join(''); }
                else line += m[4];
            }
            out.push(line + '</span>'.repeat(open.length));
            if (out.length > 1 && out[out.length - 1] === '' && text.endsWith('\n')) out.pop();
            return {
                lines: out.length,
                html: out.map((l, i) => `<div class="cm-line"><span class="cm-ln">${i + 1}</span><span class="cm-lc">${l || ' '}</span></div>`).join(''),
            };
        };
        const buildSection = (sec, lang) => Object.assign(sec, { copied: false }, highlightLines(sec.text, sec.fixedLang || lang));
        const openViewer = (title, subtitle, lang, sections) => {
            viewer.value = { open: true, title, subtitle, lang, wrap: viewer.value.wrap, sections: sections.map(s => buildSection(s, lang)) };
        };
        const rehighlightViewer = () => { viewer.value.sections.forEach(s => buildSection(s, viewer.value.lang)); };
        const closeViewer = () => { viewer.value.open = false; };
        const openCmdViewer = (c) => {
            const lang = guessOutputLang(c.command, c.output);
            const status = c.exit_code === undefined ? 'still running (snapshot)' : `exit ${c.exit_code}`;
            openViewer('Command', `${status} · ${projectLabel(appSettings.value.active_project)}`, lang, [
                { label: 'Command', text: c.command || '', fixedLang: 'bash' },
                { label: 'Output', text: c.output || '' },
            ]);
        };
        // Clicks on the View button of a code block inside a reply
        const onChatClick = (e) => {
            const btn = e.target.closest && e.target.closest('[data-cm-view]');
            if (!btn) return;
            const code = btn.parentElement.querySelector('code');
            if (!code) return;
            const m = (code.className || '').match(/language-([\w+#-]+)/);
            let lang = m ? m[1].toLowerCase() : 'auto';
            lang = LANG_ALIAS[lang] || lang;
            openViewer('Code block', m ? m[1] : 'no language given', lang, [{ label: 'Code', text: code.textContent.replace(/\n$/, '') }]);
        };
        // Clicks on the Copy button of a code block outside chat (codeButtons: 'copy')
        const onCopyCodeClick = async (e) => {
            const btn = e.target.closest && e.target.closest('[data-cm-copy]');
            if (!btn) return;
            const code = btn.closest('.cm-pre-bar')?.querySelector('code');
            if (!code) return;
            const ok = await writeClipboard(code.textContent.replace(/\n$/, ''));
            btn.textContent = ok ? 'Copied ✓' : 'Copy failed';
            btn.classList.toggle('cm-copied', !!ok);
            setTimeout(() => { btn.textContent = 'Copy'; btn.classList.remove('cm-copied'); }, 2000);
        };
        const copyViewerSection = async (sec) => {
            sec.copied = await writeClipboard(sec.text);
            setTimeout(() => { sec.copied = false; }, 2000);
        };

        const fetchConfig = async () => {
            try {
                const response = await fetch('/api/config');
                const data = await response.json();
                availableModels.value = data.available;
                setupModels.value = data.requires_setup;
                
                const routingResponse = await fetch('/api/routing');
                routingConfig.value = await routingResponse.json();

                const settingsResponse = await fetch('/api/settings');
                appSettings.value = { max_steps_simple: 8, max_steps_complex: 25, memory_curator: true, curator_idle_minutes: 10, skills_mode: 'auto', confirm_update_memory: true, confirm_escalate: true, sandbox: false, sandbox_network: true, sandbox_ro_paths: [], command_env: [], limit_memory_mb: 0, limit_file_mb: 1024, limit_processes: 0, ...(await settingsResponse.json()) };
                fetchProjectUsage();
                fetchSkills();   // for /name suggestions in the chat input

                if (appSettings.value.projects_root) {
                    await fetchProjectsList();
                }
            } catch (error) {
                console.error("Failed to load configuration", error);
            }
        };

        const fetchProjectsList = async () => {
            try {
                const resp = await fetch('/api/projects');
                projectsList.value = await resp.json();
            } catch (error) {
                console.error("Failed to fetch projects list", error);
            }
        };

        // Switching project reopens that project's latest chat, so commands
        // and history always belong to the project shown in the header
        const changeProject = async () => {
            await saveSettings();
            if (!isGenerating.value && !pendingApproval.value && ws.value) {
                isGenerating.value = true;
                ws.value.send(JSON.stringify({ type: 'open_session', id: 'latest' }));
            }
        };

        const headerCostTitle = Vue.computed(() => {
            const u = projectUsage.value;
            if (!u) return '';
            const name = appSettings.value.active_project || '(no project)';
            const lines = [`${appSettings.value.active_project ? 'Project ' + name : 'Workspace'}: ${fmtCost(u.month.cost)} this month, ${fmtCost(u.today.cost)} today, ${fmtCost(u.all.cost)} all time.`];
            if (budgetStatus.value) lines.push(`Budget ${fmtCost(budgetStatus.value.monthly_usd)} (${budgetStatus.value.on_limit} at 100%).`);
            if (sessionUsage.value.tokens) lines.push(`This browser session: ${fmtTokens(sessionUsage.value.tokens)} tokens, ${fmtCost(sessionUsage.value.cost)}.`);
            return lines.join('\n');
        });

        const fetchProjectUsage = async () => {
            try {
                const name = appSettings.value.active_project || '(no project)';
                const data = await (await fetch('/api/usage?project=' + encodeURIComponent(name))).json();
                projectUsage.value = { today: data.today.total, all: data.all_time.total, month: data.this_month.total };
                budgets.value = await (await fetch('/api/budgets')).json();
                budgetStatus.value = budgets.value[name] || null;
            } catch (error) {
                console.error('Failed to load project usage', error);
            }
        };

        const saveSettings = async () => {
            try {
                await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(appSettings.value)
                });
                showSettingsSaved.value = true;
                fetchProjectUsage();
                setTimeout(() => { showSettingsSaved.value = false; }, 2000);
                if (appSettings.value.projects_root) {
                    await fetchProjectsList();
                }
                return true;
            } catch (error) {
                console.error("Failed to save settings", error);
                return false;
            }
        };
        
        const memoryProject = ref('');
        const memoryProjectExists = ref(true);
        const memoryUrl = () => memoryTab.value === 'project'
            ? `/api/memory/project/${encodeURIComponent(memoryProject.value)}` : `/api/memory/${memoryTab.value}`;
        const fetchMemory = async () => {
            if (memoryTab.value === 'project' && !memoryProject.value) {
                memoryProject.value = appSettings.value.active_project || (projectsList.value || [])[0] || '';
            }
            if (memoryTab.value === 'project' && !memoryProject.value) { memoryContent.value = ''; return; }
            try {
                const response = await fetch(memoryUrl());
                const data = await response.json();
                memoryContent.value = data.content;
                memoryProjectExists.value = data.exists !== false;
                if (memoryTab.value === 'project') fetchMemoryVersions();
            } catch (error) {
                console.error("Failed to fetch memory", error);
            }
        };

        const saveMemory = async () => {
            try {
                if (memoryTab.value === 'project') {
                    if (!memoryProject.value) return;
                    await fetch(memoryUrl(), {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ content: memoryContent.value })
                    });
                    memoryProjectExists.value = true;
                } else {
                    await fetch('/api/memory', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ file_type: memoryTab.value, content: memoryContent.value })
                    });
                }
                showMemorySaved.value = true;
                setTimeout(() => { showMemorySaved.value = false; }, 2000);
            } catch (error) {
                console.error("Failed to save memory", error);
            }
        };

        const saveRouting = async () => {
            try {
                await fetch('/api/routing', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        conductor_model: routingConfig.value.conductor_model || '',
                        simple_model: routingConfig.value.simple_model || '',
                        complex_model: routingConfig.value.complex_model || ''
                    })
                });
                showRoutingSaved.value = true;
                setTimeout(() => { showRoutingSaved.value = false; }, 2000);
            } catch (error) {
                console.error("Failed to save routing config", error);
            }
        };

        const handleModelChange = (event) => {
            const targetId = event.target.value;
            const setupTarget = setupModels.value.find(m => m.id === targetId);
            
            if (setupTarget) {
                modalInstructions.value = setupTarget.instructions || "This model requires an API key.";
                setupEnvKey.value = setupTarget.env_key;
                showSetupModal.value = true;
                selectedModel.value = 'auto'; 
            }
        };

        const closeModal = () => {
            showSetupModal.value = false;
            keyInput.value = '';
        };

        const saveKey = async () => {
            if (!keyInput.value || !setupEnvKey.value) return;
            isSavingKey.value = true;
            try {
                await fetch('/api/keys', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ env_key: setupEnvKey.value, api_key: keyInput.value })
                });
                
                await fetchConfig();
                
                messages.value.push({
                    role: 'agent', 
                    content: `Successfully configured and loaded credentials for ${setupEnvKey.value}.`, 
                    system_note: 'Configuration Updated'
                });
                scrollToBottom();
                closeModal();
            } catch (error) {
                console.error("Failed to save key", error);
            }
            isSavingKey.value = false;
        };

        const scrollToBottom = async () => {
            await nextTick();
            const container = document.getElementById('chat-container');
            container.scrollTop = container.scrollHeight;
        };

        // ---- Dashboard data (Overview, Projects, History page, System)
        const dailyUsage = ref([]);
        const chartProject = ref('');
        const recentChats = ref([]);
        const projectsOverview = ref([]);
        const historyPage = ref({ project: '', archived: false, q: '', period: 'all', sort: 'updated', desc: true });
        const historyPageList = ref([]);
        const systemInfo = ref(null);
        const newToken = ref('');
        const CHART_COLORS = ['#60a5fa', '#f59e0b', '#34d399', '#f472b6', '#a78bfa', '#f87171', '#2dd4bf', '#facc15'];

        const fetchDaily = async () => {
            const q = chartProject.value ? `&project=${encodeURIComponent(chartProject.value)}` : '';
            try { const d = await (await fetch(`/api/usage/daily?days=30${q}`)).json(); dailyUsage.value = Array.isArray(d) ? d : []; }
            catch (error) { console.error('Failed to load daily usage', error); }
        };
        const fetchRecentChats = async () => {
            try { const d = await (await fetch('/api/sessions?all_projects=true')).json(); recentChats.value = Array.isArray(d) ? d.slice(0, 5) : []; }
            catch (error) { console.error('Failed to load recent chats', error); }
        };
        const fetchProjectsOverview = async () => {
            try { const d = await (await fetch('/api/projects/overview')).json(); projectsOverview.value = Array.isArray(d) ? d : []; }
            catch (error) { console.error('Failed to load projects', error); }
            fetchArchived();
            fetchDeleted();
        };
        // ---- fix25a: archive, restore, delete (buttons only; the model can't restore or delete)
        const deletedProjects = ref({});    // {name: deleted_at}: deleted, with chats and costs kept
        const archivedProjects = ref([]);
        const fetchDeleted = async () => {
            try { deletedProjects.value = await (await fetch('/api/projects/deleted')).json() || {}; } catch (_) {}
        };
        const fetchArchived = async () => {
            try { const d = await (await fetch('/api/projects/archived')).json(); archivedProjects.value = Array.isArray(d) ? d : []; } catch (_) {}
        };
        const refreshProjectLists = async () => {
            await fetchProjectsList();
            fetchProjectsOverview();
        };
        const archiveFromDashboard = async (name) => {
            if (!await askConfirm({ title: `Archive ${name}?`, text: "Its folder moves to the Workspace's .archive folder. Its chats, costs and memory are kept, and you can restore it from this page.", ok: 'Archive' })) return;
            try {
                const r = await postJson(`/api/projects/${encodeURIComponent(name)}/archive`);
                pushToast({ kind: 'info', title: `Archived · ${name}`, text: `Moved to ${r.archived_to}. Restore it from Archived projects, below.`, timeout: 10000 });
                // Open pages leave the project through the project_archived message; this is the fallback
                if (r.was_active && appSettings.value.active_project === name) leaveArchived(name);
            } catch (e) { pushToast({ kind: 'warn', title: `Couldn't archive ${name}`, text: e.message }); }
            refreshProjectLists();
        };
        // fix25b: where things are on disk, shown wherever projects are
        const workspacePath = Vue.computed(() => (appSettings.value.projects_root || '').replace(/\/+$/, ''));
        const archivePath = Vue.computed(() => workspacePath.value ? workspacePath.value + '/.archive' : '');
        const projectPath = (name) => workspacePath.value ? (name ? workspacePath.value + '/' + name : workspacePath.value) : '';
        const copyPath = async (path) => {
            if (path && await writeClipboard(path)) pushToast({ kind: 'info', title: 'Path copied', text: path, timeout: 3000 });
        };
        const leaveArchived = (name) => {
            if (appSettings.value.active_project !== name) return;
            appSettings.value.active_project = '';
            changeProject();
            pushToast({ kind: 'info', title: 'Now in the Workspace', text: `${name} was archived.` });
        };
        const restoreArchived = async (a) => {
            try {
                const r = await postJson(`/api/projects/archived/${encodeURIComponent(a.folder)}/restore`);
                pushToast({ kind: 'success', title: `Restored · ${a.name}`, text: `Back in ${r.path}.` });
            } catch (e) { pushToast({ kind: 'warn', title: `Couldn't restore ${a.name}`, text: e.message }); }
            refreshProjectLists();
        };
        // ---- fix25e: deleted (and archived) projects in collapsible groups; open state kept per browser
        const openGroups = ref((() => {
            try { return JSON.parse(localStorage.getItem('optimux.openGroups') || '{}') || {}; } catch (_) { return {}; }
        })());
        const toggleGroup = (key) => {
            openGroups.value = { ...openGroups.value, [key]: !openGroups.value[key] };
            try { localStorage.setItem('optimux.openGroups', JSON.stringify(openGroups.value)); } catch (_) {}
        };
        const archivedNames = Vue.computed(() => {
            const live = new Set(Array.isArray(projectsList.value) ? projectsList.value : []);
            return new Set((archivedProjects.value || []).map(a => a.name).filter(n => !live.has(n)));
        });
        const USAGE_SUM = ['calls', 'prompt_tokens', 'cached_tokens', 'completion_tokens', 'reasoning_tokens', 'cost', 'calls_without_cost'];
        // Rows of a Usage table: live ones, then one summary line for deleted projects
        // (their sums, so the table still adds up), then those projects when opened
        const usageRows = (groupKey) => {
            const data = ((usageData.value || {})[usagePeriod.value] || {})[groupKey] || {};
            const perProject = ['by_project', 'memory_updates'].includes(groupKey);
            const live = [], gone = [];
            Object.entries(data).forEach(([name, b]) => (perProject && (deletedProjects.value || {})[name] ? gone : live)
                .push({ kind: 'live', name, b, archived: perProject && archivedNames.value.has(name) }));
            if (!gone.length) return live;
            const total = Object.fromEntries(USAGE_SUM.map(k => [k, gone.reduce((a, r) => a + (r.b[k] || 0), 0)]));
            const key = 'usage-' + groupKey;
            const rows = [...live, { kind: 'summary', name: `Deleted projects (${gone.length})`, b: total, key }];
            if (openGroups.value[key]) gone.forEach(r => rows.push({ ...r, kind: 'gone' }));
            return rows;
        };
        const deleteBox = ref(null);   // { item, typed, keep, busy, error }
        const openDelete = (a) => {
            const box = { item: a, typed: '', keep: true, busy: false, error: '', gh: null };
            deleteBox.value = box;
            nextTick(() => { const el = document.getElementById('cm-delete-confirm'); if (el) el.focus(); });
            // fix25c: is the GitHub repository still there? (one request, no model)
            if (a.github_settings) {
                box.gh = { state: 'checking' };
                fetch(`/api/projects/archived/${encodeURIComponent(a.folder)}/github`).then(r => r.json())
                    .then(d => { if (deleteBox.value) deleteBox.value.gh = d; })
                    .catch(() => { if (deleteBox.value) deleteBox.value.gh = { state: 'unknown', reason: "Couldn't ask Optimux." }; });
            }
        };
        // Same rule as the server: the words in any case and spacing, the name exactly
        const deletePhraseOk = Vue.computed(() => {
            const b = deleteBox.value;
            if (!b) return false;
            const got = b.typed.trim().split(/\s+/), want = b.item.phrase.split(' ');
            return got.length === want.length && got[got.length - 1] === want[want.length - 1]
                && got.slice(0, -1).every((w, i) => w.toLowerCase() === want[i].toLowerCase());
        });
        const confirmDelete = async () => {
            const b = deleteBox.value;
            if (!b || !deletePhraseOk.value || b.busy) return;
            b.busy = true; b.error = '';
            try {
                const r = await postJson(`/api/projects/archived/${encodeURIComponent(b.item.folder)}/delete`, { confirm: b.typed, keep_records: b.keep });
                deleteBox.value = null;
                pushToast({ kind: 'success', title: `Deleted · ${r.project}`,
                            text: r.records_kept ? 'Its chats, costs and memory are kept and marked as deleted.' : 'Its chats, costs and memory are gone too.' });
            } catch (e) {
                b.error = e.message; b.busy = false;
                if (deleteBox.value !== b) pushToast({ kind: 'warn', title: `Couldn't delete ${b.item.name}`, text: e.message });
            }
            refreshProjectLists();
        };
        const fetchHistoryPage = async () => {
            const h = historyPage.value;
            const params = new URLSearchParams();
            if (h.project) params.set('project', h.project); else params.set('all_projects', 'true');
            if (h.archived) params.set('archived', 'true');
            if (h.q.trim()) params.set('q', h.q.trim());
            try { const d = await (await fetch('/api/sessions?' + params.toString())).json(); historyPageList.value = Array.isArray(d) ? d : []; }
            catch (error) { console.error('Failed to load history', error); }
        };
        const sortHistory = (col) => {
            const h = historyPage.value;
            if (h.sort === col) h.desc = !h.desc; else { h.sort = col; h.desc = col !== 'title' && col !== 'project'; }
        };
        const historyFiltered = Vue.computed(() => {
            const h = historyPage.value;
            return !!(h.project || h.archived || h.q.trim() || h.period !== 'all');
        });
        const resetHistoryFilters = () => {
            Object.assign(historyPage.value, { project: '', archived: false, q: '', period: 'all' });
            fetchHistoryPage();
        };
        const historySorted = Vue.computed(() => {
            const { sort, desc, period } = historyPage.value;
            const since = period === 'all' ? 0 : (() => {
                const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - (Number(period) - 1));
                return d.getTime() / 1000;
            })();
            const list = historyPageList.value.filter(c => (c.updated || 0) >= since);
            list.sort((a, b) => {
                const x = a[sort] ?? '', y = b[sort] ?? '';
                const r = typeof x === 'string' ? x.localeCompare(y) : x - y;
                return desc ? -r : r;
            });
            return list;
        });
        // ---- v0.3: snapshots (Dashboard → Backups)
        const snapshotsInfo = ref({ snapshots: [], used_bytes: 0, limit_bytes: 0, free_bytes: 0, dir: '', running: false });
        const snapForm = ref({ kind: 'full', note: '', projects: [], skip_rebuildable: true });
        const snapKinds = [
            { id: 'full', label: 'Everything', hint: 'Your data and all projects, at the same moment' },
            { id: 'data', label: 'Data only', hint: 'Settings, models, memory, skills, chats, keys' },
            { id: 'workspace', label: 'Workspace only', hint: 'Project folders (all or some) and their memory' },
        ];
        const snapKindLabel = (k) => ({ full: 'Everything', data: 'Data', workspace: 'Workspace' })[k] || k;
        // Data only / Workspace only: Take first asks (Everything instead, or proceed)
        const snapConfirm = ref(false);
        const snapOpen = ref({});            // snapshot rows with their details open
        const snapLeftOutProjects = Vue.computed(() =>
            (projectsList.value || []).filter(p => !snapForm.value.projects.includes(p)));
        const snapProceedLabel = Vue.computed(() => {
            if (snapForm.value.kind === 'data') return 'Proceed with only my data';
            const n = snapForm.value.projects.length;
            if (!snapLeftOutProjects.value.length) return 'Proceed with only the projects';
            return `Proceed with only the selected project${n === 1 ? '' : 's'}`;
        });
        // Workspace only: every project starts ticked; All and None change the ticks
        let snapProjectsTouched = false;
        const snapSyncProjects = () => {
            const names = projectsList.value || [];
            snapConfirm.value = false;
            snapForm.value.projects = snapProjectsTouched
                ? snapForm.value.projects.filter(p => names.includes(p))   // drop removed projects
                : [...names];
        };
        const snapSetProjects = (all) => {
            snapProjectsTouched = true;
            snapForm.value.projects = all ? [...projectsList.value] : [];
            snapPreview.value = null; snapConfirm.value = false;
        };
        const snapTouchProjects = () => { snapProjectsTouched = true; snapPreview.value = null; snapConfirm.value = false; };
        const snapNothingChosen = Vue.computed(() => snapForm.value.kind === 'workspace' && !snapForm.value.projects.length);
        const snapPreview = ref(null);
        const snapBusy = ref(false);
        const snapError = ref('');
        const snapLimit = ref('');
        let snapTimer = null;
        // A 422's detail is a message, or a list of field errors from FastAPI
        const detailText = (d, fallback) => Array.isArray(d) ? d.map(x => x.msg).join('; ') : (d || fallback);
        const readJson = async (r) => { try { return await r.json(); } catch (_) { return {}; } };
        const snapProblems = (s) => [
            s.unreadable_count ? `${s.unreadable_count} file(s) couldn't be read and are not inside: ${(s.unreadable || []).join(', ')}` : '',
            s.changed_count ? `${s.changed_count} file(s) changed while they were copied (stored as they were when the copy began): ${(s.changed || []).join(', ')}` : '',
        ].filter(Boolean).join('\n');
        const fetchSnapshots = async () => {
            try {
                const r = await fetch('/api/snapshots');
                if (r.ok) snapshotsInfo.value = await r.json();
                if (snapLimit.value === '' && snapshotsInfo.value.limit_bytes) {
                    snapLimit.value = String(Math.round(snapshotsInfo.value.limit_bytes / 1024 ** 3 * 10) / 10);
                }
            } catch (error) { console.error('Failed to load snapshots', error); }
            clearTimeout(snapTimer);
            // Poll while one is being taken (only while the section is open)
            if (snapshotsInfo.value.running && showSettingsModal.value && settingsTab.value === 'backups') {
                snapTimer = setTimeout(fetchSnapshots, 1500);
            }
        };
        const snapPayload = () => ({ kind: snapForm.value.kind, note: snapForm.value.note,
            projects: snapForm.value.kind === 'workspace' ? snapForm.value.projects : [],
            skip_rebuildable: snapForm.value.skip_rebuildable });
        const previewSnapshot = async () => {
            snapBusy.value = true; snapError.value = '';
            try {
                const r = await fetch('/api/snapshots/preview', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapPayload()) });
                const d = await readJson(r);
                if (r.ok) snapPreview.value = d; else snapError.value = detailText(d.detail, 'Preview failed.');
            } catch (error) { snapError.value = 'Preview failed.'; }
            finally { snapBusy.value = false; }
        };
        const takeSnapshot = async (confirmed = false) => {
            if (snapForm.value.kind !== 'full' && confirmed !== true) { snapConfirm.value = true; return; }
            snapConfirm.value = false;
            snapBusy.value = true; snapError.value = '';
            try {
                const r = await fetch('/api/snapshots', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(snapPayload()) });
                const d = await readJson(r);
                if (!r.ok) { snapError.value = detailText(d.detail, 'The snapshot could not be started.'); return; }
                const kind = snapForm.value.kind;
                snapForm.value.note = ''; snapPreview.value = null;
                pushToast({ title: `Taking a snapshot: ${snapKindLabel(kind)}`, text: 'It appears in the list below; you can keep working meanwhile.' });
                await fetchSnapshots();
            } catch (error) { snapError.value = 'The snapshot could not be started.'; }
            finally { snapBusy.value = false; }
        };
        // Copy buttons on the paths: green "Copied ✓" for two seconds, like the README's
        const snapCopied = ref({});
        const snapCopy = async (key, text) => {
            if (!await writeClipboard(text)) return;
            snapCopied.value[key] = true;
            setTimeout(() => { delete snapCopied.value[key]; }, 2000);
        };
        // Delete asks inside the row (snapDeleting = the id waiting for an answer)
        const snapDeleting = ref('');
        const snapReadme = ref({});          // id -> README text (or an error), shown in Details
        // v0.3 C: downloads. The one-time key lives only in this page's memory and
        // is dropped when the panel closes; the server keeps it nowhere.
        const snapDl = ref({});
        const dlOpen = (s) => {
            snapDl.value[s.id] = { mode: 'key', recipient: appSettings.value.snapshot_recipient || '', remember: false,
                                   busy: false, error: '', result: null, saved: false };
        };
        const dlClose = (s) => { delete snapDl.value[s.id]; };
        const dlPrepare = async (s) => {
            const d = snapDl.value[s.id];
            d.busy = true; d.error = '';
            try {
                const r = await fetch(`/api/snapshots/${encodeURIComponent(s.id)}/export`, { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: d.mode, recipient: d.recipient, remember: d.remember }) });
                const body = await readJson(r);
                if (!r.ok) { d.error = detailText(body.detail, 'The download could not be prepared.'); return; }
                if (d.mode === 'recipient' && d.remember) appSettings.value.snapshot_recipient = d.recipient.trim();
                d.result = body;
                if (body.mode === 'plain') clickDownload(body.url, body.filename);   // nothing to save first
            } catch (error) { d.error = 'The download could not be prepared.'; }
            finally { d.busy = false; }
        };
        const clickDownload = (url, name) => {
            const a = document.createElement('a');
            a.href = url; a.download = name || '';
            document.body.appendChild(a); a.click(); a.remove();
        };
        const dlSaveKey = (s) => {
            const d = snapDl.value[s.id];
            const text = `# Optimux snapshot key for ${d.result.filename}\n# Open with: age -d -i optimux-key-${s.id}.txt -o snapshot.tar.gz ${d.result.filename}\n${d.result.key}\n`;
            const url = URL.createObjectURL(new Blob([text], { type: 'text/plain' }));
            const a = document.createElement('a');
            a.href = url; a.download = `optimux-key-${s.id}.txt`;
            document.body.appendChild(a); a.click(); a.remove();
            setTimeout(() => URL.revokeObjectURL(url), 1000);
        };
        const dlStarted = (s, e) => {
            const d = snapDl.value[s.id];
            if (d.result.key && !d.saved) { e.preventDefault(); return; }   // save the key first
            if (d.result.key) {
                // Saved and downloading: the key leaves this page too (the link still works for an hour)
                d.result.key = null;
                d.keyCleared = true;
            }
        };
        const dlCommand = (s) => {
            const d = snapDl.value[s.id];
            const rec = d.recipient || '';
            const keyArg = d.result.mode === 'key' ? `optimux-key-${s.id}.txt`
                : rec.includes('ssh-ed25519') ? '~/.ssh/id_ed25519'
                : rec.includes('ssh-rsa') ? '~/.ssh/id_rsa' : '<your-age-key-file>';
            return `age -d -i ${keyArg} -o snapshot.tar.gz ${d.result.filename}`;
        };
        // Leaving Backups drops any download panel (and a one-time key still shown in it)
        watch(() => [showSettingsModal.value, settingsTab.value], ([open, tab]) => {
            if (!open || tab !== 'backups') snapDl.value = {};
        });
        const loadSnapReadme = async (s) => {
            if (snapReadme.value[s.id]) { delete snapReadme.value[s.id]; return; }   // second click hides it
            snapReadme.value[s.id] = { loading: true };
            try {
                const r = await fetch(`/api/snapshots/${encodeURIComponent(s.id)}/readme`);
                const d = await readJson(r);
                // Markdown (new snapshots) is rendered like chat replies; older .txt READMEs stay plain text
                snapReadme.value[s.id] = !r.ok ? { error: detailText(d.detail, 'It could not be read.') }
                    : d.format === 'md' ? { html: optimuxSafeHtml(marked.parse(d.text), { codeButtons: 'copy' }) } : { text: d.text };
            } catch (error) { snapReadme.value[s.id] = { error: 'It could not be read.' }; }
        };
        const deleteSnapshot = async (s) => {
            snapDeleting.value = '';
            try {
                const r = await fetch(`/api/snapshots/${encodeURIComponent(s.id)}/delete`, { method: 'POST' });
                if (!r.ok) pushToast({ kind: 'warn', title: 'Not deleted', text: detailText((await readJson(r)).detail, '') });
                else pushToast({ title: 'Snapshot deleted', text: `${snapKindLabel(s.kind)} snapshot of ${new Date(s.created * 1000).toLocaleString()}${s.note ? ' (' + s.note + ')' : ''} was removed from the server.` });
            } catch (error) { console.error('Delete failed', error); }
            await fetchSnapshots();
        };
        // ---- v0.3 D: restore (from a snapshot in the list; a downloaded file is uploaded first)
        const rsStatus = ref({ pending: null, last: null, busy: false, can_restart: false, confirm_phrase: '' });
        const rsPanel = ref({});             // snapshot id -> the open restore panel
        const rsRestarting = ref(false);
        const fetchRestoreStatus = async () => {
            try { const r = await fetch('/api/restore/status'); if (r.ok) rsStatus.value = await r.json(); }
            catch (error) { console.error('Failed to load the restore status', error); }
        };
        const openRestore = async (s) => {
            snapDeleting.value = '';
            if (!systemInfo.value) fetchSystemInfo();       // to compare versions
            rsPanel.value[s.id] = { loading: true, info: null, chosen: {}, confirm: '', busy: false, error: '' };
            try {
                const r = await fetch(`/api/snapshots/${encodeURIComponent(s.id)}/restore`);
                const d = await readJson(r);
                const p = rsPanel.value[s.id];
                if (!p) return;
                if (!r.ok) { p.error = detailText(d.detail, 'This snapshot could not be read.'); p.loading = false; return; }
                p.info = d;
                for (const part of d.parts) p.chosen[part.path] = !!part.default && !part.blocked;
                p.loading = false;
            } catch (error) { if (rsPanel.value[s.id]) { rsPanel.value[s.id].error = 'This snapshot could not be read.'; rsPanel.value[s.id].loading = false; } }
        };
        const closeRestore = (s) => { delete rsPanel.value[s.id]; };
        const rsChosen = (s) => { const p = rsPanel.value[s.id]; return p && p.info ? p.info.parts.filter(x => p.chosen[x.path]) : []; };
        const rsReady = (s) => {
            const p = rsPanel.value[s.id];
            return !!(p && p.info && rsChosen(s).length && p.confirm.trim() === p.info.confirm_phrase && !p.busy);
        };
        const doRestore = async (s) => {
            const p = rsPanel.value[s.id];
            if (!rsReady(s)) return;
            p.busy = true; p.error = '';
            try {
                const r = await fetch(`/api/snapshots/${encodeURIComponent(s.id)}/restore`, { method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ parts: rsChosen(s).map(x => x.path), confirm: p.confirm }) });
                const d = await readJson(r);
                if (!r.ok) { p.error = detailText(d.detail, 'The restore failed.'); return; }
                closeRestore(s);
                const projects = d.projects.length ? `Projects restored: ${d.projects.join(', ')}.` : '';
                const archived = (d.archived.length ? ' Their previous folders are under Archived projects.' : '')
                    + (d.skipped_links && d.skipped_links.length ? ` ${d.skipped_links.length} link(s) in Optimux's data were left out (links are never restored there).` : '');
                pushToast(d.restart_needed
                    ? { title: 'Restore ready: restart Optimux to finish', text: `${projects}${archived} Your data is put back when Optimux restarts (see the top of Backups).` }
                    : { title: 'Restored', text: `${projects}${archived}` });
                if (d.projects.length) fetchProjectsList();
            } catch (error) { p.error = 'The restore failed.'; }
            finally { p.busy = false; await Promise.all([fetchSnapshots(), fetchRestoreStatus()]); }
        };
        const restartForRestore = async () => {
            try {
                const r = await fetch('/api/restore/restart', { method: 'POST' });
                if (!r.ok) { pushToast({ kind: 'warn', title: 'Not restarted', text: detailText((await readJson(r)).detail, '') }); return; }
            } catch (_) { /* the server may already be going down */ }
            rsRestarting.value = true;
            // Wait until Optimux answers again, then reload the page with the restored data
            const started = Date.now();
            await new Promise(res => setTimeout(res, 2000));
            while (Date.now() - started < 120000) {
                try { const r = await fetch('/auth/status', { cache: 'no-store' }); if (r.ok) { location.reload(); return; } }
                catch (_) { /* still restarting */ }
                await new Promise(res => setTimeout(res, 1500));
            }
            rsRestarting.value = false;
            pushToast({ kind: 'warn', title: 'Optimux is not back yet', text: 'Check it on the server: optimux status (sudo on a server).' });
        };
        const cancelRestore = async () => {
            try {
                const r = await fetch('/api/restore/cancel', { method: 'POST' });
                const d = await readJson(r);
                if (r.ok) pushToast({ title: 'Restore cancelled', text: `Nothing changed in your data. The prepared files were moved to ${d.moved_to}.` });
                else pushToast({ kind: 'warn', title: 'Not cancelled', text: detailText(d.detail, '') });
            } catch (error) { console.error('Cancel failed', error); }
            await fetchRestoreStatus();
        };
        const dismissRestore = async () => {
            try { await fetch('/api/restore/dismiss', { method: 'POST' }); } catch (_) { /* a notice only */ }
            await fetchRestoreStatus();
        };
        // Restore from a file: upload (raw body, with progress), then import (decrypt + check)
        const rsUp = ref({ file: null, progress: 0, busy: false, error: '', token: '', encrypted: false, key: '' });
        const rsPickFile = (e) => {
            const f = e.target.files && e.target.files[0];
            rsUp.value = { file: f || null, progress: 0, busy: false, error: '', token: '', encrypted: false, key: '' };
        };
        const rsReadKeyFile = (e) => {
            const f = e.target.files && e.target.files[0];
            if (!f) return;
            const reader = new FileReader();
            reader.onload = () => { rsUp.value.key = String(reader.result || ''); };
            reader.readAsText(f);
            e.target.value = '';
        };
        const rsUpload = () => new Promise((resolve) => {
            const u = rsUp.value;
            if (!u.file) return resolve();
            u.busy = true; u.error = ''; u.progress = 0;
            const xhr = new XMLHttpRequest();
            xhr.open('POST', '/api/restore/upload');
            xhr.setRequestHeader('Content-Type', 'application/octet-stream');
            xhr.upload.onprogress = (ev) => { if (ev.lengthComputable) u.progress = Math.round(ev.loaded / ev.total * 100); };
            xhr.onload = async () => {
                let d = {};
                try { d = JSON.parse(xhr.responseText); } catch (_) { /* keep {} */ }
                if (xhr.status !== 200) { u.busy = false; u.error = detailText(d.detail, 'The upload failed.'); return resolve(); }
                u.token = d.token; u.encrypted = d.encrypted; u.progress = 100;
                u.busy = false;
                if (!d.encrypted) await rsImport();             // nothing to ask: check it and add it
                resolve();
            };
            xhr.onerror = () => { u.busy = false; u.error = 'The upload failed (connection lost).'; resolve(); };
            xhr.send(u.file);
        });
        const rsImport = async () => {
            const u = rsUp.value;
            u.busy = true; u.error = '';
            try {
                const r = await fetch('/api/restore/import', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token: u.token, key: u.key, filename: u.file ? u.file.name : '' }) });
                const d = await readJson(r);
                if (!r.ok) {
                    u.error = detailText(d.detail, 'The file could not be checked.');
                    // The server keeps an encrypted upload only after a wrong key: anything else needs a new upload
                    const keyProblem = u.encrypted && /key/i.test(u.error) && !/expired|Upload the file again/.test(u.error);
                    if (!keyProblem) u.token = '';
                    return;
                }
                rsUp.value = { file: null, progress: 0, busy: false, error: '', token: '', encrypted: false, key: '' };
                const input = document.getElementById('rs-file');
                if (input) input.value = '';
                await fetchSnapshots();
                pushToast({ title: 'Snapshot added to the list', text: 'The file was checked: every part matches its checksum. Choose what to restore below.' });
                snapOpen.value[d.id] = true;
                openRestore(d);
            } catch (error) { u.error = 'The file could not be checked.'; }
            finally { u.busy = false; }
        };
        const saveSnapshotLimit = async () => {
            // Only the limit: other unsaved Dashboard edits stay unsaved
            const gb = Number(snapLimit.value);
            if (snapLimit.value === '' || !Number.isFinite(gb) || gb < 0.1) {
                pushToast({ kind: 'warn', title: 'Not saved', text: 'The limit must be at least 0.1 GB.' });
                return;
            }
            try {
                const r = await fetch('/api/snapshots/limit', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ gb }) });
                if (r.ok) { appSettings.value.snapshot_limit_gb = gb; pushToast({ title: 'Snapshot limit saved', text: `${gb} GB` }); }
                else pushToast({ kind: 'warn', title: 'Not saved', text: detailText((await readJson(r)).detail, '') });
            } catch (error) { console.error('Saving the limit failed', error); }
            await fetchSnapshots();
        };
        const fetchSystemInfo = async () => {
            try { systemInfo.value = await (await fetch('/api/system')).json(); }
            catch (error) { console.error('Failed to load system info', error); }
        };
        const rotateToken = async () => {
            const current = window.prompt('Rotate the access token. Type the current token to confirm:');
            if (!current) return;
            try {
                const resp = await fetch('/api/system/rotate-token', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ current: current.trim() })
                });
                const data = await resp.json();
                if (data.token) newToken.value = data.token;
            } catch (error) { console.error('Token rotation failed', error); }
        };
        // Feature 23: signing in, two-step codes, security checklist
        const authInfo = ref({ totp: false, sessions: [], server_mode: false, via_token: false });
        const securityChecks = ref([]);
        const pwForm = ref({ current: '', next: '', again: '', msg: '', ok: false });
        const totpSetup = ref({ step: 'idle', password: '', secret: '', qr: '', code: '', msg: '' });
        const totpOff = ref({ open: false, password: '', code: '', msg: '' });
        const postJson = async (url, body) => {
            // 400s already alert in boot.js; return the data for anything else
            const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
            let data = {};
            try { data = await res.json(); } catch (_) {}
            if (!res.ok) {
                const d = data.detail;
                throw new Error(Array.isArray(d) ? d.map(x => x.msg).join('; ') : (d || 'Request failed.'));
            }
            return data;
        };
        const saveGithubToken = async () => {
            if (!githubToken.value.trim() || githubBusy.value) return;
            githubBusy.value = true; githubMsg.value = null;
            try {
                const r = await postJson('/api/github', { token: githubToken.value });
                githubToken.value = '';
                githubMsg.value = { ok: true, warn: !!r.warning, text: `Signed in to GitHub as @${r.login}.` + (r.identity_set ? ' Commits will be made as this account (change it below).' : '') + (r.warning ? ' ' + r.warning : '') + (r.hint ? ' ' + r.hint : '') };
                await reloadSettings();
            } catch (e) { githubMsg.value = { ok: false, text: e.message }; }
            githubBusy.value = false;
            fetchGithub(true);
        };
        const checkGithub = async () => {
            githubBusy.value = true; githubMsg.value = null;
            try {
                const r = await postJson('/api/github/check');
                githubMsg.value = { ok: true, warn: !!r.warning, text: `The token works: @${r.login}.` + (r.warning ? ' ' + r.warning : '') + (r.hint ? ' ' + r.hint : '') };
            } catch (e) { githubMsg.value = { ok: false, text: e.message }; }
            githubBusy.value = false;
        };
        const removeGithub = async () => {
            if (!await askConfirm({ title: 'Remove the GitHub token?', text: 'Optimux can no longer create GitHub repositories until you add one again. Repositories already created are not touched.', ok: 'Remove', danger: true })) return;
            try { await fetch('/api/github', { method: 'DELETE' }); } catch (_) {}
            githubMsg.value = null;
            await reloadSettings();
            fetchGithub(true);
        };
        const newProject = ref({ open: false, busy: false, error: '', key: 0, tab: 'new' });
        const openNewProject = (tab = 'new') => {
            githubInfo.value = {};
            fetchGithub();
            newProject.value = { open: true, busy: false, error: '', key: newProject.value.key + 1, tab: typeof tab === 'string' ? tab : 'new' };
            if (newProject.value.tab === 'github') loadGithubRepos(true);
        };
        // ---- Feature 26: import a repository from GitHub (no model involved)
        const ghImport = ref({ loading: false, error: '', repos: null, query: '', picked: null, name: '', switch: true,
                               job: null, truncated: false, fine_grained: false, posting: false });
        const loadGithubRepos = async (force = false) => {
            const g = ghImport.value;
            if (g.loading || (g.repos && !force)) return;
            g.loading = true; g.error = '';
            try {
                const res = await fetch('/api/github/repos');
                const d = await res.json();
                if (!res.ok) throw new Error(d.detail || 'Request failed.');
                Object.assign(g, { repos: d.repos, truncated: d.truncated, fine_grained: d.fine_grained });
            } catch (e) { g.error = e.message; }
            g.loading = false;
        };
        // Loaded again each time the tab opens, so "in your Workspace" marks are current
        const showGithubTab = () => { newProject.value.tab = 'github'; loadGithubRepos(true); };
        const filteredRepos = Vue.computed(() => {
            const g = ghImport.value, q = g.query.trim().toLowerCase();
            const all = g.repos || [];
            return q ? all.filter(r => r.full_name.toLowerCase().includes(q) || (r.description || '').toLowerCase().includes(q)) : all;
        });
        const shownRepos = Vue.computed(() => filteredRepos.value.slice(0, 300));
        const pickRepo = (repo) => {
            if (repo.local || (ghImport.value.job && ghImport.value.job.state === 'running')) return;
            Object.assign(ghImport.value, { picked: repo, name: repo.name.replace(/^[.-]+/, '').replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 100), error: '' });
        };
        const importNameProblem = Vue.computed(() => {
            const n = ghImport.value.name.trim();
            if (!n) return 'Choose a folder name.';
            if (!PROJECT_NAME_RE.test(n)) return 'Use letters, digits, . _ or - (not starting with . or -).';
            if ((projectsList.value || []).includes(n)) return 'A project with this name already exists.';
            return '';
        });
        const pollClone = async (job, openWhenDone) => {
            while (true) {
                await new Promise(r => setTimeout(r, 700));
                let st;
                let res;
                try { res = await fetch(`/api/github/clone/${job.id}`); st = await res.json(); } catch (_) { continue; }
                if (!res.ok || !st || !st.state) {      // the server restarted (job gone) or the sign-in ended
                    st = { ...job, state: 'failed', error: 'Optimux lost track of this clone (it restarted, or you were signed out). Nothing was left behind; try again.' };
                }
                Object.assign(job, st);
                if (ghImport.value.job && ghImport.value.job.id === job.id) Object.assign(ghImport.value.job, st);
                if (st.state === 'running') continue;
                if (st.state === 'done') {
                    pushToast({ kind: 'success', title: `Cloned · ${st.name}`, text: `${st.full_name} is in your Workspace.` });
                    await fetchProjectsList();
                    // (compared by id: Vue keeps a proxy of the job, not the object itself)
                    const current = ghImport.value.job && ghImport.value.job.id === job.id;
                    if (current) Object.assign(ghImport.value, { job: null, picked: null, repos: null, query: '' });
                    if (current && newProject.value.open && newProject.value.tab === 'github') newProject.value.open = false;
                    if (openWhenDone) await switchProject(st.name);
                } else if (st.state === 'failed') {
                    pushToast({ kind: 'warn', title: `Couldn't clone ${st.full_name}`, text: st.error, timeout: 20000 });
                } else {
                    pushToast({ kind: 'info', title: 'Clone cancelled', text: 'Nothing was left behind.' });
                }
                return;
            }
        };
        const startClone = async () => {
            const g = ghImport.value;
            if (!g.picked || importNameProblem.value || g.posting || (g.job && g.job.state === 'running')) return;
            g.error = ''; g.posting = true;
            try {
                const job = await postJson('/api/github/clone', { full_name: g.picked.full_name, name: g.name.trim() });
                g.job = job;
                pollClone(job, g.switch);
            } catch (e) { g.error = e.message; }
            g.posting = false;
        };
        const cancelClone = async () => {
            const job = ghImport.value.job;
            if (!job) return;
            try { await fetch(`/api/github/clone/${job.id}`, { method: 'DELETE' }); } catch (_) {}
        };
        const createProjectFromForm = async (form) => {
            newProject.value.busy = true; newProject.value.error = '';
            try {
                const r = await postJson('/api/projects', form);
                newProject.value.open = false;
                projectCreatedToast(form.name, r.ok, r.github_url);
                if (!r.ok) pushToast({ kind: 'warn', title: 'What failed', text: r.report, timeout: 20000 });
                await fetchProjectsList();
                if (showSettingsModal.value && settingsTab.value === 'projects') fetchProjectsOverview();
                if (form.switch) await switchProject(form.name);
            } catch (e) { newProject.value.error = e.message; }
            newProject.value.busy = false;
        };
        const setupGithubFromForm = () => {
            newProject.value.open = false;
            openSettings('github');
        };
        // The chat form: answer the server's create_project request with the final values
        const sendProjectForm = (form) => {
            ws.value.send(JSON.stringify({ type: 'approval_response', approved: true, form }));
            pendingApproval.value = false;
            pendingForm.value = null;
            pendingCommand.value = '';
        };
        const signinNotice = ref(null);
        // "Firefox on Linux" from a user-agent string (good enough to recognise your own browsers)
        const browserLabel = (ua) => {
            if (!ua) return 'Unknown browser';
            const name = /Edg\//.test(ua) ? 'Edge' : /OPR\//.test(ua) ? 'Opera' : /Firefox\//.test(ua) ? 'Firefox'
                : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : /curl\//.test(ua) ? 'curl' : 'Browser';
            const os = /Android/.test(ua) ? 'Android' : /iPhone|iPad/.test(ua) ? 'iOS' : /Windows/.test(ua) ? 'Windows'
                : /Mac OS X/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : '';
            return os ? `${name} on ${os}` : name;
        };
        const signOutOthersFromNotice = async () => {
            signinNotice.value = null;
            await signOutOthers();
        };
        const fetchAuth = async () => {
            try { authInfo.value = await (await fetch('/api/auth')).json(); } catch (_) {}
            try { securityChecks.value = (await (await fetch('/api/security')).json()).checks || []; } catch (_) {}
        };
        const signOut = async () => {
            try { await fetch('/auth/logout', { method: 'POST' }); } catch (_) {}
            location.href = '/login';
        };
        const signOutOthers = async () => {
            if (!await askConfirm({ title: 'Sign out other browsers?', text: 'Every other browser and device will need the password again. This one stays signed in.', ok: 'Sign them out' })) return;
            try { await postJson('/api/auth/signout-others'); } catch (_) {}
            fetchAuth();
        };
        const changePassword = async () => {
            const f = pwForm.value;
            f.msg = ''; f.ok = false;
            if (f.next !== f.again) { f.msg = 'The two new passwords differ.'; return; }
            try {
                const r = await postJson('/api/auth/password', { current: f.current, new: f.next });
                pwForm.value = { current: '', next: '', again: '', ok: true,
                                 msg: 'Password changed.' + (r.ended ? ` ${r.ended} other sign-in${r.ended === 1 ? '' : 's'} ended.` : '') };
                fetchAuth();
            } catch (e) { f.msg = e.message; }
        };
        const totpStart = async () => {
            const t = totpSetup.value;
            t.msg = '';
            try {
                const r = await postJson('/api/auth/totp/start', { password: t.password });
                let qr = '';
                try { const q = qrcode(0, 'M'); q.addData(r.uri); q.make(); qr = q.createDataURL(4, 8); } catch (_) {}
                totpSetup.value = { step: 'scan', password: '', secret: r.secret, qr, code: '', msg: '' };
            } catch (e) { t.msg = e.message; }
        };
        const totpConfirm = async () => {
            const t = totpSetup.value;
            t.msg = '';
            try {
                await postJson('/api/auth/totp/confirm', { code: t.code });
                totpSetup.value = { step: 'idle', password: '', secret: '', qr: '', code: '', msg: '' };
                fetchAuth();
            } catch (e) { t.msg = e.message; }
        };
        const totpDisable = async () => {
            const t = totpOff.value;
            t.msg = '';
            try {
                await postJson('/api/auth/totp/disable', { password: t.password, code: t.code });
                totpOff.value = { open: false, password: '', code: '', msg: '' };
                fetchAuth();
            } catch (e) { t.msg = e.message; }
        };
        const commandEnvText = ref('');
        const saveCommandSettings = async () => {
            appSettings.value.command_env = commandEnvText.value.split(/[\s,]+/).map(x => x.trim()).filter(Boolean);
            if (await saveSettings()) { await reloadSettings(); commandEnvText.value = (appSettings.value.command_env || []).join('\n'); }
        };

        const openChatFromDashboard = (sid) => {
            closeSettings();
            openSession(sid);
        };
        const switchProject = async (name) => {
            appSettings.value.active_project = name;
            await changeProject();
            fetchProjectsOverview();
        };
        const routingRole = (id) => {
            const r = routingConfig.value || {};
            const roles = [];
            if (r.conductor_model === id) roles.push('conductor');
            if (r.simple_model === id) roles.push('simple');
            if (r.complex_model === id) roles.push('complex');
            return roles.join(' · ');
        };
        const fmtAgo = (ts) => {
            if (!ts) return '—';
            const s = Math.max(0, Date.now() / 1000 - ts);
            if (s < 60) return 'just now';
            if (s < 3600) return Math.floor(s / 60) + ' min ago';
            if (s < 86400) return Math.floor(s / 3600) + ' h ago';
            if (s < 86400 * 30) return Math.floor(s / 86400) + ' d ago';
            return new Date(ts * 1000).toLocaleDateString();
        };

        // 30-day stacked bar chart (one bar per day, one colour per model), drawn as SVG
        const costChart = Vue.computed(() => {
            const days = [];
            const today = new Date(); today.setHours(0, 0, 0, 0);
            for (let i = 29; i >= 0; i--) {
                const d = new Date(today); d.setDate(d.getDate() - i);
                days.push(`${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`);
            }
            const modelIds = [...new Set(dailyUsage.value.map(r => r.model))];
            const models = modelIds.map((id, i) => ({ id, color: CHART_COLORS[i % CHART_COLORS.length] }));
            const byDay = {};
            dailyUsage.value.forEach(r => { (byDay[r.day] = byDay[r.day] || {})[r.model] = r.cost || 0; });
            const totals = days.map(d => Object.values(byDay[d] || {}).reduce((a, b) => a + b, 0));
            const max = Math.max(...totals, 0);
            const W = 900, H = 180, left = 44, bottom = 22, top = 8;
            const plotH = H - bottom - top, step = (W - left) / days.length, barW = Math.max(2, step * 0.7);
            const esc = (t) => String(t).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
            let svg = `<svg viewBox="0 0 ${W} ${H}" class="w-full h-44" preserveAspectRatio="none" role="img" aria-label="Cost per day">`;
            [0, 0.5, 1].forEach(f => {
                const y = top + plotH * (1 - f);
                svg += `<line x1="${left}" x2="${W}" y1="${y}" y2="${y}" stroke="#1f2937" stroke-width="1"/>`;
                svg += `<text x="${left - 6}" y="${y + 3}" text-anchor="end" font-size="10" fill="#6b7280" font-family="monospace">${esc(fmtCost(max * f))}</text>`;
            });
            days.forEach((d, i) => {
                let y = top + plotH;
                const x = left + i * step + (step - barW) / 2;
                const parts = models.map(m => ({ m, v: (byDay[d] || {})[m.id] || 0 })).filter(p => p.v > 0);
                const tip = `${d}: ${fmtCost(totals[i])}` + parts.map(p => `\n${modelName(p.m.id)}: ${fmtCost(p.v)}`).join('');
                parts.forEach(p => {
                    const h = max ? (p.v / max) * plotH : 0;
                    y -= h;
                    svg += `<rect x="${x}" y="${y}" width="${barW}" height="${Math.max(h, 0.5)}" fill="${p.m.color}" rx="1"><title>${esc(tip)}</title></rect>`;
                });
                if (i % 5 === 4 || i === days.length - 1) {
                    svg += `<text x="${x + barW / 2}" y="${H - 6}" text-anchor="middle" font-size="10" fill="#6b7280" font-family="monospace">${d.slice(5)}</text>`;
                }
            });
            if (!max) svg += `<text x="${(W + left) / 2}" y="${H / 2}" text-anchor="middle" font-size="12" fill="#6b7280">No usage in the last 30 days</text>`;
            svg += '</svg>';
            return { svg: optimuxSafeHtml(svg), models, max };
        });

        const overviewKpis = Vue.computed(() => {
            const u = usageData.value;
            if (!u || !u.today) return [];
            const month = u.this_month ? u.this_month.total : u.today.total;
            const hit = month.prompt_tokens ? Math.round(100 * month.cached_tokens / month.prompt_tokens) : 0;
            return [
                { label: 'Today', value: fmtCost(u.today.total.cost) + (u.today.total.calls_without_cost ? '+' : ''), sub: `${u.today.total.calls} calls` },
                { label: 'This month', value: fmtCost(month.cost) + (month.calls_without_cost ? '+' : ''), sub: `${month.calls} calls` },
                { label: 'Cache hit · month', value: hit + '%', sub: `${fmtTokens(month.cached_tokens)} of ${fmtTokens(month.prompt_tokens)} input tokens` },
                { label: 'Output · month', value: fmtTokens(month.completion_tokens), sub: `${fmtTokens(month.reasoning_tokens || 0)} of it thinking` },
            ];
        });

        // ---- Project memory history and curator (Feature 17)
        // In-app confirm: await askConfirm({ title, detail, text, ok, danger }) -> true/false
        const confirmBox = ref(null);
        const confirmOkRef = ref(null);
        let confirmResolve = null;
        const askConfirm = (opts) => new Promise(resolve => {
            if (confirmResolve) confirmResolve(false);
            confirmResolve = resolve;
            confirmBox.value = { ok: 'OK', danger: false, detail: '', text: '', ...opts };
            nextTick(() => { if (confirmOkRef.value) confirmOkRef.value.focus(); });
        });
        const answerConfirm = (yes) => {
            const box = confirmBox.value;
            if (yes && box && box.skip && box.onSkip) box.onSkip();
            confirmBox.value = null;
            const r = confirmResolve; confirmResolve = null;
            if (r) r(yes);
        };
        // ---- Feature 18: cost estimates and background memory status
        const costEstimates = ref({});
        const fetchCostEstimates = async () => {
            try { costEstimates.value = await (await fetch('/api/costs/estimates')).json(); }
            catch (error) { console.error('Failed to load cost estimates', error); }
        };
        const estimateText = (est, what) => est
            ? `≈ ${fmtCost(est.avg)} ${what} (average of your last ${est.n}, ${modelName(est.model)})`
            : 'No priced call of this kind yet, so no estimate.';
        const memoryStatus = ref(null);   // { state: running|updated|unchanged, text, title }
        let memoryStatusTimer = null;
        const setMemoryStatus = (status, hideAfterMs = 0) => {
            clearTimeout(memoryStatusTimer);
            memoryStatus.value = status;
            if (hideAfterMs) memoryStatusTimer = setTimeout(() => { memoryStatus.value = null; }, hideAfterMs);
        };
        const memoryVersions = ref([]);
        const curating = ref(false);
        const curateResult = ref(null);
        const memoryToast = ref(null);
        const fetchMemoryVersions = async () => {
            if (!memoryProject.value) { memoryVersions.value = []; return; }
            try {
                const d = await (await fetch(`/api/memory/project/${encodeURIComponent(memoryProject.value)}/versions`)).json();
                memoryVersions.value = Array.isArray(d) ? d.map(v => ({ ...v, open: false })) : [];
            } catch (error) { console.error('Failed to load memory history', error); }
        };
        const undoMemoryVersion = async (v) => {
            if (!await askConfirm({ title: 'Undo this memory change?', detail: v.summary, ok: 'Undo',
                text: 'The project memory goes back to how it was before this change. The undo is recorded too, so it can be undone.' })) return;
            await fetch(`/api/memory/versions/${v.id}/undo`, { method: 'POST' });
            await fetchMemory();
        };
        const curateNow = async () => {
            if (curating.value) return;
            if (appSettings.value.confirm_update_memory !== false) {
                await fetchCostEstimates();
                if (!await askConfirm({ title: 'Update memory now?', ok: 'Update',
                    text: 'The Simple model reads the whole latest chat of this project and rewrites its memory. This is one paid model call.',
                    cost: estimateText(costEstimates.value.memory_update, 'per update'),
                    skipLabel: "Don't warn me again (turn it back on in Dashboard → Budgets → Cost warnings)",
                    onSkip: () => { appSettings.value.confirm_update_memory = false; saveSettings(); } })) return;
            }
            curating.value = true; curateResult.value = null;
            try {
                curateResult.value = await (await fetch(`/api/memory/project/${encodeURIComponent(memoryProject.value)}/curate`, { method: 'POST' })).json();
                await fetchMemory();
            } catch (error) { curateResult.value = { changed: false, reason: 'request failed' }; }
            finally { curating.value = false; fetchCostEstimates(); }
        };
        const viewMemoryFromToast = () => {
            const t = memoryToast.value;
            memoryToast.value = null;
            memoryTab.value = 'project';
            memoryProject.value = t.project;
            openSettings('memory');
            fetchMemory();
        };
        const undoFromToast = async () => {
            const t = memoryToast.value;
            memoryToast.value = null;
            await fetch(`/api/memory/versions/${t.version_id}/undo`, { method: 'POST' });
            if (showSettingsModal.value && settingsTab.value === 'memory') fetchMemory();
        };

        // ---- Skills
        const skillsInfo = ref({ skills: [], global_dir: '', project_dir: null });
        const skillEditor = ref(null);   // { scope, name, content, isNew, draft, error }
        const skillDrafting = ref(false);
        const fetchSkills = async () => {
            try {
                const d = await (await fetch('/api/skills')).json();
                if (d && Array.isArray(d.skills)) skillsInfo.value = d;
            } catch (error) { console.error('Failed to load skills', error); }
        };
        // ---- Feature 19: skill modes and slash commands
        const setSkillsMode = async (mode) => {
            appSettings.value.skills_mode = mode;
            await saveSettings();
            fetchSkills();
        };
        const setSkillMode = async (sk, mode) => {
            if (sk.mode === mode) return;
            await fetch(`/api/skills/${sk.scope}/${encodeURIComponent(sk.name)}/mode`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ mode }) });
            fetchSkills();
        };
        const SLASH_COMMANDS = [
            { cmd: '/new', hint: 'Start a new chat' },
            { cmd: '/simple', hint: 'Use the Simple model for this chat' },
            { cmd: '/complex', hint: 'Use the Complex model for this chat' },
            { cmd: '/plan', hint: 'Split this task into steps for the cheaper model' },
            { cmd: '/noplan', hint: 'Do this task without a plan' },
        ];
        const slashIndex = ref(0);
        const slashSuggestions = Vue.computed(() => {
            const t = userInput.value;
            if (!t.startsWith('/') || /\s/.test(t)) return [];
            const q = t.slice(1).toLowerCase();
            const skills = appSettings.value.skills_mode === 'off' ? [] : (skillsInfo.value.skills || [])
                .filter(sk => sk.usable && sk.active)
                .map(sk => ({ cmd: '/' + sk.name, hint: sk.description, mode: sk.mode || 'manual' }));
            return [...SLASH_COMMANDS, ...skills].filter(c => c.cmd.slice(1).startsWith(q) && c.cmd !== t).slice(0, 8);
        });
        watch(slashSuggestions, () => { slashIndex.value = 0; });
        // Slash suggestions need the active project's skills
        watch(() => appSettings.value.active_project, () => fetchSkills());
        const pickSlash = (c) => {
            userInput.value = c.cmd + ' ';
            nextTick(() => { if (chatInputRef.value) chatInputRef.value.focus(); });
        };
        const skillDialog = ref(null);   // { mode: 'new' | 'draft', name, scope, error, busy }
        const newSkill = () => {
            skillDialog.value = { mode: 'new', name: '', scope: appSettings.value.active_project ? 'project' : 'global', error: '', busy: false };
        };
        const editSkill = async (sk) => {
            try {
                const d = await (await fetch(`/api/skills/${sk.scope}/${sk.name}`)).json();
                skillEditor.value = { scope: sk.scope, name: sk.name, content: d.content || '', isNew: false, draft: false, error: '' };
            } catch (error) { console.error('Failed to open skill', error); }
        };
        const saveSkill = async () => {
            const e = skillEditor.value;
            const resp = await fetch(`/api/skills/${e.scope}/${encodeURIComponent(e.name)}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content: e.content })
            });
            if (resp.status === 200) { skillEditor.value = null; fetchSkills(); }
            else { const d = await resp.json(); e.error = d.detail || 'Could not save.'; }
        };
        const removeSkill = async (sk) => {
            if (!await askConfirm({ title: `Remove the skill "${sk.name}"?`, ok: 'Remove', danger: true,
                text: `This ${sk.scope} skill's folder is moved to ~/.optimux/legacy/skills/, not deleted.` })) return;
            await fetch(`/api/skills/${sk.scope}/${sk.name}/remove`, { method: 'POST' });
            fetchSkills();
        };
        const saveAsSkill = () => {
            if (!currentSession.value || skillDrafting.value) return;
            fetchCostEstimates();
            skillDialog.value = { mode: 'draft', name: '', scope: appSettings.value.active_project ? 'project' : 'global', error: '', busy: false };
        };
        const confirmSkillDialog = async () => {
            const d = skillDialog.value;
            if (!d || d.busy) return;
            d.name = (d.name || '').trim();
            if (!/^[a-z0-9][a-z0-9-]{0,63}$/.test(d.name)) { d.error = 'Use lowercase letters, digits and dashes (e.g. release-notes).'; return; }
            if (d.mode === 'new') {
                const t = await (await fetch('/api/skills/template?name=' + encodeURIComponent(d.name))).json();
                skillEditor.value = { name: d.name, scope: d.scope, content: t.content, isNew: true, draft: false, error: '' };
                skillDialog.value = null;
                return;
            }
            d.busy = true; d.error = '';
            skillDrafting.value = true;
            try {
                const resp = await fetch('/api/skills/draft', {
                    method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ session_id: currentSession.value.id, name: d.name })
                });
                const r = await resp.json();
                if (resp.status !== 200) { d.error = r.detail || 'Drafting failed.'; return; }
                skillEditor.value = { name: d.name, scope: d.scope, content: r.content, isNew: true, draft: true, error: '' };
                skillDialog.value = null;
                openSettings('skills');
            } catch (error) { d.error = 'Drafting failed.'; console.error(error); }
            finally { if (skillDialog.value) skillDialog.value.busy = false; skillDrafting.value = false; fetchCostEstimates(); }
        };

        // ---- Dashboard navigation: each section has an address (#/dashboard/<id>)
        const dashboardSections = [
            { id: 'overview', label: 'Overview', icon: '◎', group: 'Activity', hint: 'Spending, budgets, models and recent chats at a glance.' },
            { id: 'usage', label: 'Usage', icon: '▤', group: 'Activity', hint: 'Tokens, cost and time per project, role and model.' },
            { id: 'budgets', label: 'Budgets', icon: '$', group: 'Activity', hint: 'Monthly spending limits per project.' },
            { id: 'projects', label: 'Projects', icon: '▦', group: 'Activity', hint: 'Cost, budget, chats and memory per project.' },
            { id: 'history', label: 'History', icon: '◷', group: 'Activity', hint: 'Every stored chat; open one to continue it.' },
            { id: 'routing', label: 'Routing', icon: '⇄', group: 'Configuration', hint: 'Which model handles which tier.' },
            { id: 'models', label: 'Models', icon: '◆', group: 'Configuration', hint: 'Providers, keys, prices and thinking.' },
            { id: 'memory', label: 'Memory', icon: '✎', group: 'Configuration', hint: 'What every model is told about you and your work.' },
            { id: 'skills', label: 'Skills', icon: '✦', group: 'Configuration', hint: 'Reusable instructions loaded only when a task needs them.' },
            { id: 'execution', label: 'Execution', icon: '›', group: 'Configuration', hint: 'Command approval and how many steps a request may take.' },
            { id: 'github', label: 'GitHub', icon: '⑂', group: 'Configuration', hint: 'A token so Optimux can create GitHub repositories for new projects, and who commits.' },
            { id: 'scope', label: 'Scope', icon: '▣', group: 'Configuration', hint: 'The Workspace folder: the folder that holds your projects.' },
            { id: 'backups', label: 'Backups', icon: '⧉', group: 'Configuration', hint: 'Snapshots of your data and projects, kept on this machine.' },
            { id: 'system', label: 'System', icon: '≡', group: 'Configuration', hint: 'Files, storage, backups, access token and model cooldowns.' },
            { id: 'guide', label: 'Guide', icon: '?', group: 'Help', hint: 'Everything about Optimux: getting started, routing, memory, skills, costs, and how to spend less.' },
        ];
        const dashboardGroups = [...new Set(dashboardSections.map(x => x.group))];

        // ---- Feature 20: the Guide (docs/GUIDE.md, rendered with a contents list and search)
        const guide = ref({ html: '', toc: [], chunks: [], error: '' });
        const guideQuery = ref('');
        const guideActive = ref('');
        const guideSlug = (t, used) => {
            let base = t.toLowerCase().replace(/<[^>]+>/g, '').replace(/&[a-z]+;/g, '').replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '') || 'section';
            let slug = base, n = 2;
            while (used.has(slug)) slug = `${base}-${n++}`;
            used.add(slug);
            return slug;
        };
        const plainMd = (t) => t.replace(/```[\s\S]*?```/g, ' ').replace(/[`*_>#|]/g, ' ').replace(/\[([^\]]+)\]\([^)]*\)/g, '$1').replace(/\s+/g, ' ').trim();
        const parseGuide = (md) => {
            // headings outside code fences, in order
            const toc = [], chunks = [], used = new Set();
            let fence = false, cur = null, h2 = '';
            for (const line of md.split('\n')) {
                if (/^```/.test(line)) fence = !fence;
                const m = !fence && line.match(/^(##|###) (.+)$/);
                if (m) {
                    const level = m[1].length, title = m[2].trim();
                    if (level === 2) h2 = title;
                    const id = guideSlug(title, used);
                    toc.push({ id, title, level });
                    cur = { id, title, path: level === 3 ? `${h2} › ${title}` : title, lines: [] };
                    chunks.push(cur);
                } else if (cur) cur.lines.push(line);
            }
            chunks.forEach(c => { c.text = plainMd(c.lines.join('\n')); delete c.lines; });
            let i = 0;
            const html = marked.parse(md).replace(/<h([23])([^>]*)>([\s\S]*?)<\/h\1>/g, (all, lvl, attrs, inner) =>
                toc[i] ? `<h${lvl} id="g-${toc[i++].id}">${inner}</h${lvl}>` : all);
            return { html: optimuxSafeHtml(html), toc, chunks };
        };
        const loadGuide = async () => {
            if (guide.value.html) return;
            try {
                const r = await fetch('/api/guide');
                const d = await r.json();
                if (r.status !== 200) { guide.value = { ...guide.value, error: d.detail || 'The guide could not be loaded.' }; return; }
                guide.value = { ...parseGuide(d.content || ''), error: '' };
            } catch (error) { guide.value = { ...guide.value, error: 'The guide could not be loaded.' }; }
        };
        const escapeHtml = (t) => t.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
        const guideResults = Vue.computed(() => {
            const words = guideQuery.value.toLowerCase().split(/\s+/).filter(Boolean);
            if (!words.length) return [];
            const out = [];
            for (const c of guide.value.chunks) {
                const hay = (c.title + ' ' + c.text).toLowerCase();
                if (!words.every(w => hay.includes(w))) continue;
                const at = Math.max(0, c.text.toLowerCase().indexOf(words[0]));
                let snip = (at > 40 ? '…' : '') + c.text.slice(Math.max(0, at - 40), at + 120) + '…';
                snip = escapeHtml(snip);
                for (const w of words) {
                    const re = new RegExp('(' + w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'ig');
                    snip = snip.replace(re, '<mark class="bg-yellow-700/60 text-yellow-100 rounded px-0.5">$1</mark>');
                }
                out.push({ id: c.id, path: c.path, snippet: optimuxSafeHtml(snip), score: c.title.toLowerCase().includes(words[0]) ? 0 : 1 });
            }
            return out.sort((a, b) => a.score - b.score).slice(0, 25);
        });
        const goGuide = (id) => {
            const el = document.getElementById('g-' + id);
            if (el) el.scrollIntoView({ block: 'start', behavior: 'smooth' });
            guideActive.value = id;
        };
        const onDashScroll = (e) => {
            if (settingsTab.value !== 'guide' || !guide.value.toc.length) return;
            const top = e.target.getBoundingClientRect().top + 24;
            let active = guide.value.toc[0].id;
            for (const h of guide.value.toc) {
                const el = document.getElementById('g-' + h.id);
                if (el && el.getBoundingClientRect().top <= top) active = h.id; else if (el) break;
            }
            guideActive.value = active;
        };
        const enterSection = (id) => {
            if (['overview', 'usage', 'budgets', 'system'].includes(id)) fetchUsage();
            if (['overview', 'usage'].includes(id)) fetchDaily();
            if (id === 'overview') { fetchRecentChats(); fetchDeleted(); }
            if (id === 'projects') fetchProjectsOverview();
            if (id === 'history') fetchHistoryPage();
            if (id === 'memory') { fetchMemory(); fetchCostEstimates(); }
            if (id === 'system') { fetchSystemInfo(); fetchAuth(); }
            if (id === 'github') { githubMsg.value = null; fetchGithub(true); }
            if (id === 'guide') loadGuide();
            if (id === 'execution') { fetchSandbox(); commandEnvText.value = (appSettings.value.command_env || []).join('\n'); }
            if (id === 'skills') fetchSkills();
            if (id === 'models') { fetchCatalog(); loadProviderModels(); }
            if (id === 'backups') { fetchSnapshots(); fetchRestoreStatus(); fetchProjectsList().then(snapSyncProjects); }
        };
        const dashboardHash = () => `#/dashboard/${settingsTab.value}`;
        const openSettings = (section) => {
            if (typeof section === 'string' && dashboardSections.some(x => x.id === section)) settingsTab.value = section;
            if (!dashboardSections.some(x => x.id === settingsTab.value)) settingsTab.value = 'overview';
            showSettingsModal.value = true;
            enterSection(settingsTab.value);
            if (location.hash !== dashboardHash()) location.hash = dashboardHash();
        };
        const closeSettings = () => {
            showSettingsModal.value = false;
            if (location.hash.startsWith('#/dashboard')) location.hash = '#/';
        };
        // Back/forward buttons and typed addresses
        const applyHash = () => {
            const m = location.hash.match(/^#\/dashboard(?:\/([\w-]+))?/);
            if (m) {
                const id = m[1] && dashboardSections.some(x => x.id === m[1]) ? m[1] : settingsTab.value;
                if (!showSettingsModal.value || id !== settingsTab.value) openSettings(id);
            } else if (showSettingsModal.value) {
                showSettingsModal.value = false;
            }
        };
        window.addEventListener('hashchange', applyHash);
        // Code that sets settingsTab directly still updates the address and loads data
        watch(settingsTab, (id) => {
            if (!showSettingsModal.value) return;
            enterSection(id);
            if (location.hash !== dashboardHash()) location.hash = dashboardHash();
        });

        const saveKeyDirect = async (envKey, modelId, event) => {
            const inputField = event.target.elements['key_' + modelId];
            if (!inputField || !inputField.value) return;
            
            try {
                const resp = await fetch('/api/keys', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ env_key: envKey, api_key: inputField.value })
                });
                const result = await resp.json();
                const cleared = (result.cooldowns_cleared || []).length
                    ? ` Cooldown cleared for ${result.cooldowns_cleared.map(modelName).join(', ')}.` : '';
                modelNotices.value[modelId] = { ok: true, text: 'Key saved. Use Test to check it.' + cleared };
                inputField.value = '';
                editingKeys.value[modelId] = false;
                await fetchConfig();
            } catch (error) {
                console.error("Failed to save key", error);
            }
        };

        // ---- v0.3: the model catalog (Models screen). Offline list from LiteLLM;
        // online only when allowed AND on Refresh.
        const cat = ref({ online: false, providers: [], prices_refreshed: null, prices_count: 0 });
        const catPid = ref('');                // '' = not chosen; 'other' = any LiteLLM model ID, by hand
        const catRows = ref([]);
        const catInfo = ref(null);
        const catModel = ref('');
        const catToolsOnly = ref(true);
        const catMineOnly = ref(false);
        const catHideRetired = ref(true);
        const catSearch = ref('');
        const catSort = ref({ key: 'input', desc: false });
        const catBusy = ref('');
        const catMsg = ref(null);
        const catRefreshMsg = ref(null);      // the Online catalog panel's own message
        const catOwnPrices = ref(false);
        const catKey = ref('');
        const catReplaceKey = ref(false);
        const catName = ref('');
        const catProvider = Vue.computed(() => cat.value.providers.find(p => p.id === catPid.value) || null);
        // "anthropic/claude-x" and "claude-x" (also openai/) are the same model for LiteLLM
        const bareId = (id) => (id || '').replace(/^(anthropic|openai)\//, '');
        const configuredIds = Vue.computed(() => {
            const ids = new Set();
            for (const m of [...availableModels.value, ...setupModels.value]) { ids.add(m.id); ids.add(bareId(m.id)); }
            return ids;
        });
        const fetchCatalog = async () => {
            try { const r = await fetch('/api/catalog'); if (r.ok) cat.value = await r.json(); }
            catch (error) { console.error('Failed to load the model catalog', error); }
        };
        const loadProviderModels = async (keepFilters = false) => {
            if (!catPid.value || catPid.value === 'other') { catRows.value = []; catInfo.value = null; return; }
            try {
                const r = await fetch(`/api/catalog/${encodeURIComponent(catPid.value)}`);
                if (!r.ok) return;
                const d = await r.json();
                catInfo.value = d; catRows.value = d.models;
                if (keepFilters !== true) catMineOnly.value = !!d.models_refreshed;
            } catch (error) { console.error('Failed to load models', error); }
        };
        const pickProvider = async (pid) => {
            catPid.value = pid; catModel.value = ''; catName.value = ''; catKey.value = '';
            catReplaceKey.value = false; catOwnPrices.value = false; catMsg.value = null; catSearch.value = '';
            await loadProviderModels();
        };
        const catVisible = Vue.computed(() => {
            const q = catSearch.value.trim().toLowerCase();
            const { key, desc } = catSort.value;
            const rows = catRows.value.filter(r =>
                (!catToolsOnly.value || r.tools !== false) &&
                (!catMineOnly.value || r.available !== false) &&
                (!catHideRetired.value || !r.retired || r.id === catModel.value) &&
                (!q || r.id.toLowerCase().includes(q)));
            const val = (r) => key === 'id' ? r.id : r[key];
            return rows.sort((a, b) => {
                const x = val(a), y = val(b);
                if (x == null && y == null) return 0;
                if (x == null) return 1;               // unknown prices always last
                if (y == null) return -1;
                const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
                return desc ? -c : c;
            });
        });
        const catSelected = Vue.computed(() => catRows.value.find(r => r.id === catModel.value) || null);
        const catSortBy = (key) => {
            catSort.value = catSort.value.key === key ? { key, desc: !catSort.value.desc } : { key, desc: false };
        };
        const fmtMtok = (v) => v == null ? '—' : v === 0 ? 'free' : '$' + (v < 0.1 ? v.toFixed(3) : v < 10 ? v.toFixed(2) : v.toFixed(1));
        const fmtCtx = (n) => !n ? '—' : n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : `${Math.round(n / 1000)}k`;
        const fmtWhen = (ts) => ts ? new Date(ts * 1000).toLocaleString() : 'never';
        // Rate limits learned from replies: "500 requests/min · 200k tokens/min"
        const PER = { minute: 'min', second: 's', hour: 'h', day: 'day', month: 'month' };
        const fmtNum = (n) => n >= 1e6 ? `${+(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1000)}k` : n.toLocaleString('en-US');
        const fmtLimits = (l) => !(l && l.items && l.items.length) ? '' :
            l.items.map(i => `${fmtNum(i.limit)} ${i.what}/${PER[i.per] || i.per}`).join(' · ');
        const fmtLimitsTitle = (l) => {
            if (!(l && l.items && l.items.length)) return '';
            const hit = l.items.filter(i => i.hit);
            const free = l.items.some(i => i.free_tier) ? ', free tier' : '';
            return hit.length ? `reached ${fmtWhen(l.hit_at || l.seen)}${free}` : `seen ${fmtWhen(l.seen)}`;
        };
        const catChoose = (id) => {
            catModel.value = id;
            catName.value = id.split('/').pop();
            catOwnPrices.value = false;
            const r = catRows.value.find(x => x.id === id);
            newModelPrices.value = { input_cost_per_mtok: r && r.input != null && r.new ? r.input : '',
                                     output_cost_per_mtok: r && r.output != null && r.new ? r.output : '',
                                     cached_input_cost_per_mtok: '' };
        };
        const catNeedsKey = Vue.computed(() => !!(catProvider.value && catProvider.value.env &&
            (!catProvider.value.key_saved || catReplaceKey.value)));
        const addCatalogModel = async () => {
            const p = catProvider.value;
            if (!p || !catModel.value) return;
            catMsg.value = null;
            if (catNeedsKey.value && !catKey.value.trim()) {
                catMsg.value = { ok: false, text: `Paste your ${p.label} API key: Optimux can't call ${p.label} without it.` };
                return;
            }
            catBusy.value = 'add';
            try {
                if (catNeedsKey.value) {
                    const k = await fetch('/api/keys', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ env_key: p.env, api_key: catKey.value.trim() }) });
                    if (!k.ok) { catMsg.value = { ok: false, text: detailText((await readJson(k)).detail, 'The key was not saved.') }; return; }
                }
                const r = await fetch('/api/models', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: catModel.value, name: catName.value.trim() || catModel.value,
                        env_key: p.env || '', ...(catOwnPrices.value || (catSelected.value && catSelected.value.new)
                            ? pricePayload(newModelPrices.value) : pricePayload({})) }) });
                if (!r.ok) { catMsg.value = { ok: false, text: detailText((await readJson(r)).detail, 'The model was not added.') }; return; }
                pushToast({ title: 'Model added', text: `${catName.value || catModel.value}: assign it in Routing to use it.` });
                catKey.value = ''; catReplaceKey.value = false; catModel.value = ''; catName.value = '';
                newModelPrices.value = { input_cost_per_mtok: '', output_cost_per_mtok: '', cached_input_cost_per_mtok: '' };
                await fetchConfig(); await fetchCatalog(); await loadProviderModels();
            } catch (error) { catMsg.value = { ok: false, text: 'The model was not added.' }; }
            finally { catBusy.value = ''; }
        };
        const setCatalogOnline = async (on) => {
            try {
                const r = await fetch('/api/catalog/online', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ on }) });
                if (r.ok) { cat.value.online = on; appSettings.value.online_catalog = on; if (catInfo.value) catInfo.value.online = on; }
            } catch (error) { console.error('Failed to change the online catalog setting', error); }
        };
        const catRefresh = async (what) => {
            catBusy.value = what; catRefreshMsg.value = null;
            try {
                const r = await fetch('/api/catalog/refresh', { method: 'POST', headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ what, provider: catPid.value }) });
                const d = await readJson(r);
                if (!r.ok) { catRefreshMsg.value = { ok: false, text: detailText(d.detail, 'The refresh failed.') }; return; }
                catRefreshMsg.value = { ok: true, text: what === 'prices'
                    ? `Prices updated: ${d.count} models. Costs in Usage use them from now on.`
                    : `${catProvider.value ? catProvider.value.label : 'The provider'} lists ${d.count} models for your key.` };
                await fetchCatalog(); await loadProviderModels();
                if (what === 'prices') await fetchConfig();     // configured models' "price known" follows
            } catch (error) { catRefreshMsg.value = { ok: false, text: 'The refresh failed.' }; }
            finally { catBusy.value = ''; }
        };
        const addCustomModel = async () => {
            if (!newModelId.value || !newModelName.value) return;
            try {
                const envKey = newModelEnvKey.value ? newModelEnvKey.value.trim().toUpperCase() : "";
                await fetch('/api/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ 
                        id: newModelId.value.trim(), 
                        name: newModelName.value,
                        env_key: envKey,
                        ...pricePayload(newModelPrices.value)
                    })
                });
                
                if (envKey && newModelApiKey.value) {
                    const resp = await fetch('/api/keys', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ env_key: envKey, api_key: newModelApiKey.value.trim() })
                    });
                    await resp.json();
                }
                newModelPrices.value = { input_cost_per_mtok: '', output_cost_per_mtok: '', cached_input_cost_per_mtok: '' };

                newModelId.value = '';
                newModelName.value = '';
                newModelEnvKey.value = '';
                newModelApiKey.value = '';
                await fetchConfig();
            } catch (error) {
                console.error("Failed to add model", error);
            }
        };

        const deleteCustomModel = async (modelId) => {
            if (!await askConfirm({ title: 'Delete this model?', detail: modelId, ok: 'Delete', danger: true,
                text: 'It will be removed from the model list. Its API key stays in secrets.env.' })) return;
            try {
                await fetch(`/api/models/${encodeURIComponent(modelId)}`, {
                    method: 'DELETE'
                });
                await fetchConfig();
            } catch (error) {
                console.error("Failed to delete model", error);
            }
        };

        // ---- Models: prices, test
        const pricePayload = (p) => {
            const out = {};
            for (const k of ['input_cost_per_mtok', 'output_cost_per_mtok', 'cached_input_cost_per_mtok']) {
                const v = p[k];
                out[k] = (v === '' || v === null || v === undefined) ? null : Number(v);
            }
            return out;
        };
        const togglePriceEdit = (model) => {
            if (editingPrices.value[model.id]) { delete editingPrices.value[model.id]; return; }
            editingPrices.value[model.id] = {
                input_cost_per_mtok: model.input_cost_per_mtok ?? '',
                output_cost_per_mtok: model.output_cost_per_mtok ?? '',
                cached_input_cost_per_mtok: model.cached_input_cost_per_mtok ?? ''
            };
        };
        const savePrices = async (model) => {
            const prices = pricePayload(editingPrices.value[model.id]);
            if ((prices.input_cost_per_mtok == null) !== (prices.output_cost_per_mtok == null)) {
                alert('Set both input and output prices, or leave both empty.');
                return;
            }
            try {
                await fetch('/api/models', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: model.id, name: model.name, env_key: model.env_key || '', instructions: model.instructions || '', ...prices })
                });
                delete editingPrices.value[model.id];
                modelNotices.value[model.id] = { ok: true, text: 'Prices saved.' };
                await fetchConfig();
            } catch (error) {
                console.error('Failed to save prices', error);
            }
        };
        const setThinking = async (model, level) => {
            try {
                await fetch('/api/models/thinking', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: model.id, thinking: level })
                });
                modelNotices.value[model.id] = { ok: true, text: `Thinking set to ${level}.` };
                await fetchConfig();
            } catch (error) {
                console.error('Failed to set thinking', error);
            }
        };
        const testModel = async (modelId) => {
            testingModels.value[modelId] = true;
            try {
                const resp = await fetch('/api/models/test', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id: modelId })
                });
                const r = await resp.json();
                modelNotices.value[modelId] = { ok: r.ok, text: r.message };
                // the reply may have carried the key's rate limits (or the error named one)
                fetchConfig(); loadProviderModels(true);
            } catch (error) {
                modelNotices.value[modelId] = { ok: false, text: String(error) };
            }
            delete testingModels.value[modelId];
        };

        // ---- Budgets
        // fix25e: live projects (editable), archived ones (budget paused) and deleted ones, apart
        const budgetRows = Vue.computed(() => {
            const deleted = deletedProjects.value || {};
            const archived = archivedNames.value;
            const names = new Set([...(Array.isArray(projectsList.value) ? projectsList.value : []), ...Object.keys(budgets.value || {}),
                                   ...Object.keys(deleted), ...archived]);
            const month = (usageData.value && usageData.value.this_month) ? usageData.value.this_month.by_project : {};
            const rows = [...names].sort().map(project => {
                const b = budgets.value[project];
                return { project, budget: b || null, spent: b ? b.spent : ((month[project] || {}).cost || 0), state: b ? b.state : 'ok',
                         deleted: deleted[project] || 0, archived: archived.has(project) };
            });
            const gone = rows.filter(r => r.deleted && !r.archived);
            return { live: rows.filter(r => !r.deleted && !r.archived), archived: rows.filter(r => r.archived), deleted: gone,
                     deletedSpent: gone.reduce((a, r) => a + (r.spent || 0), 0) };
        });
        const activeBudgets = Vue.computed(() => Object.fromEntries(Object.entries(budgets.value || {}).filter(([, b]) => b && !b.paused)));
        // Form values for each project row; called whenever budgets are (re)loaded
        const syncBudgetEdits = () => {
            const names = new Set([...(Array.isArray(projectsList.value) ? projectsList.value : []), ...Object.keys(budgets.value || {})]);
            const edits = {};
            names.forEach(project => {
                const b = budgets.value[project];
                edits[project] = { amount: b ? b.monthly_usd : '', on_limit: b ? b.on_limit : 'block' };
            });
            budgetEdits.value = edits;
        };
        const saveBudget = async (project) => {
            const edit = budgetEdits.value[project];
            const amount = edit.amount === '' || edit.amount === null ? null : Number(edit.amount);
            try {
                await fetch('/api/budgets', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ project, monthly_usd: amount, on_limit: edit.on_limit })
                });
                await fetchProjectUsage();
            } catch (error) {
                console.error('Failed to save budget', error);
            }
        };
        const openBudgets = () => openSettings('budgets');

        // ---- Usage
        const clearCooldown = async (model) => {
            try {
                await fetch('/api/cooldowns/clear', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(model ? { model } : {})
                });
                cooldowns.value = await (await fetch('/api/cooldowns')).json();
            } catch (error) {
                console.error('Failed to clear cooldown', error);
            }
        };
        const fetchUsage = async () => {
            try {
                budgets.value = await (await fetch('/api/budgets')).json();
                syncBudgetEdits();
                usageData.value = await (await fetch('/api/usage')).json();
                fetchDeleted();
                fetchArchived();
                cooldowns.value = await (await fetch('/api/cooldowns')).json();
                storageInfo.value = await (await fetch('/api/storage')).json();
            } catch (error) {
                console.error('Failed to load usage', error);
            }
        };
        const fmtTokens = (n) => {
            n = n || 0;
            if (n >= 1e6) return (n / 1e6).toFixed(n >= 1e7 ? 0 : 1) + 'M';
            if (n >= 1e3) return (n / 1e3).toFixed(n >= 1e4 ? 0 : 1) + 'k';
            return String(n);
        };
        const fmtCost = (c) => {
            c = c || 0;
            if (c === 0) return '$0';
            if (c < 0.01) return '$' + c.toFixed(4);
            return '$' + c.toFixed(c < 1 ? 3 : 2);
        };
        const fmtBytes = (n) => {
            n = n || 0;
            if (n >= 1073741824) return (n / 1073741824).toFixed(1).replace(/\.0$/, '') + ' GB';
            if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
            if (n >= 1024) return Math.round(n / 1024) + ' KB';
            return n + ' B';
        };
        const fmtMs = (ms) => {
            ms = ms || 0;
            return ms >= 1000 ? (ms / 1000).toFixed(ms >= 10000 ? 0 : 1) + 's' : ms + 'ms';
        };
        // "Gemini 8 + Claude Sonnet 5.5 1 calls" when a reply used several models
        const callsLabel = (u) => {
            const entries = Object.entries(u.byModel || {});
            if (entries.length <= 1) {
                const name = entries.length ? modelName(entries[0][0]) + ' · ' : '';
                return `${name}${u.calls} call${u.calls === 1 ? '' : 's'}`;
            }
            return entries.map(([m, n]) => `${modelName(m)} ${n}`).join(' + ') + ' calls';
        };
        const modelName = (id) => {
            const m = [...availableModels.value, ...setupModels.value].find(x => x.id === id);
            return m ? m.name : id;
        };

        // ---- Chat controls
        const welcomeMessage = (text) => ({ role: 'agent', content: text, system_note: 'System', commands: [] });
        const newChat = () => {
            if (isGenerating.value || pendingApproval.value) return;
            currentSession.value = null;
            readOnlyChat.value = null;
            isGenerating.value = true;
            ws.value.send(JSON.stringify({ type: 'new_chat' }));
        };
        const canEscalate = (msg, index) =>
            msg.role === 'agent' && index === messages.value.length - 1 && msg.tier === 'simple'
            && !isGenerating.value && !pendingApproval.value && !!routingConfig.value.complex_model;
        const escalate = async () => {
            if (appSettings.value.confirm_escalate !== false) {
                await fetchCostEstimates();
                const model = modelName(routingConfig.value.complex_model);
                if (!await askConfirm({ title: `Re-run on ${model}?`, ok: 'Escalate',
                    text: 'Your last request runs again on the Complex model with the chat so far. It usually takes several calls, each costing more than on the Simple model.',
                    cost: estimateText(costEstimates.value.complex_call, 'per call'),
                    skipLabel: "Don't warn me again (turn it back on in Dashboard → Budgets → Cost warnings)",
                    onSkip: () => { appSettings.value.confirm_escalate = false; saveSettings(); } })) return;
                if (!canEscalate(messages.value[messages.value.length - 1], messages.value.length - 1)) return;
            }
            messages.value.push({ role: 'agent', content: '', events: [], commands: [], blocks: [] });
            isGenerating.value = true;
            ws.value.send(JSON.stringify({ type: 'escalate' }));
            scrollToBottom();
        };
        // Things a reply gets once it's complete (live on "done", or after a replay)
        const finalizeReply = (msg) => {
            if (!msg || msg.role !== 'agent') return;
            // Thinking was billed but no text arrived: say so instead of showing nothing
            if (msg.blocks && msg.usage && msg.usage.replyReasoning && !msg.blocks.some(b => b.kind === 'thinking')) {
                msg.blocks.unshift({ kind: 'thinking', text: '', tokens: msg.usage.replyReasoning });
            }
        };
        let loadSession = () => {};

        // ---- Session history
        const currentSession = ref(null);
        // A chat whose project was archived or deleted: shown to read, not to continue (fix38)
        const readOnlyChat = ref(null);
        const historyOpen = ref(false);
        const historyScope = ref('project');   // project | all | archived
        const historyQuery = ref('');
        const historyList = ref([]);
        const fetchSessions = async () => {
            const params = new URLSearchParams();
            if (historyScope.value === 'all') params.set('all_projects', 'true');
            if (historyScope.value === 'archived') { params.set('all_projects', 'true'); params.set('archived', 'true'); }
            if (historyQuery.value.trim()) params.set('q', historyQuery.value.trim());
            try {
                historyList.value = await (await fetch('/api/sessions?' + params.toString())).json();
            } catch (error) {
                console.error('Failed to load sessions', error);
            }
        };
        const toggleHistory = () => {
            historyOpen.value = !historyOpen.value;
            if (historyOpen.value) fetchSessions();
        };
        const openSession = (sid) => {
            if (isGenerating.value || pendingApproval.value) return;
            historyOpen.value = false;
            isGenerating.value = true;
            ws.value.send(JSON.stringify({ type: 'open_session', id: sid }));
        };
        // The logo and the name: back to the most recent chat (any project), or the Workspace
        const goHome = () => {
            if (showSettingsModal.value) closeSettings();
            historyOpen.value = false;
            if (isGenerating.value || pendingApproval.value || !ws.value) return;
            openSession('last');
        };
        // Leave a read-only chat: back to the active project's latest chat
        const leaveReadOnly = () => {
            readOnlyChat.value = null;
            openSession('latest');
        };
        const renameSession = async (s) => {
            const title = window.prompt('New title for this chat:', s.title || '');
            if (title === null || !title.trim()) return;
            try {
                await fetch(`/api/sessions/${s.id}/rename`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title })
                });
                if (currentSession.value && currentSession.value.id === s.id) currentSession.value.title = title.trim();
                fetchSessions(); fetchHistoryPage();
            } catch (error) { console.error('Rename failed', error); }
        };
        const archiveSession = async (s, archived) => {
            try {
                await fetch(`/api/sessions/${s.id}/archive`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ archived })
                });
                fetchSessions(); fetchHistoryPage();
            } catch (error) { console.error('Archive failed', error); }
        };
        const deleteSession = async (s) => {
            const expected = s.title || s.id;
            const typed = window.prompt(`Delete this chat permanently? Its messages can't be recovered (cost records are kept).\n\nType the title to confirm:\n${expected}`);
            if (typed === null) return;
            if (typed.trim() !== expected.trim()) { alert('The title didn\'t match; nothing was deleted.'); return; }
            try {
                await fetch(`/api/sessions/${s.id}/delete`, {
                    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ confirm: typed })
                });
                if (currentSession.value && currentSession.value.id === s.id) newChat();
                fetchSessions(); fetchHistoryPage();
            } catch (error) { console.error('Delete failed', error); }
        };

        const lastPromptBefore = (index) => {
            for (let i = index - 1; i >= 0; i--) {
                const m = messages.value[i];
                if (m.role === 'user') return m.content === 'Continue from where you stopped.' ? lastPromptBefore(i) : m.content;
            }
            return '';
        };
        // Send the original request again (e.g. after raising the budget or a provider error)
        const retryFrom = (index) => {
            const prompt = lastPromptBefore(index);
            if (!prompt) return;
            userInput.value = prompt;
            sendMessage();
        };
        const interruptionLabel = (kind) => ({
            stopped: 'Stopped by you.', step_limit: 'Paused at the step limit.', paused: 'Paused: budget reached.',
            blocked: 'Not sent: budget reached.', error: 'Failed.', aborted: 'The model repeated itself and was stopped.',
        }[kind] || '');
        const continueTurn = () => {
            userInput.value = 'Continue from where you stopped.';
            sendMessage();
        };
        const addEvent = (msg, text, kind) => {
            if (!msg.events) msg.events = [];
            msg.events.push({ text, kind });
        };

        onMounted(() => {
            fetchConfig();
            fetchSandbox();   // also tells the UI whether this is a server (OPTIMUX_SERVER=1)
            applyHash();  // opened with a dashboard address
            
            // Connection with automatic reconnect (server restarts, network blips).
            // On every (re)connect, auth_ok reopens the project's latest chat.
            const wsProto = location.protocol === 'https:' ? 'wss' : 'ws';
            let retryDelay = 1000;
            let activeSock = null;  // raw socket (ws.value is a reactive proxy, not comparable)
            const connect = () => {
                wsState.value = 'connecting';
                const sock = new WebSocket(`${wsProto}://${window.location.host}/ws`);
                activeSock = sock;
                ws.value = sock;
                // Signed in with the session cookie: the server sends auth_ok right away.
                sock.addEventListener('close', (e) => {
                    if (activeSock !== sock) return;
                    wsState.value = 'disconnected';
                    isGenerating.value = false;
                    pendingApproval.value = false;
                    if (e.code === 4401) { window.optimuxToLogin(); return; }
                    setTimeout(connect, retryDelay);
                    retryDelay = Math.min(retryDelay * 2, 15000);
                });
                sock.onmessage = (event) => {
                    const data = JSON.parse(event.data);
                    if (data.type === 'auth_ok') { wsState.value = 'connected'; retryDelay = 1000; }
                    handleMessage(data);
                };
            };
            // replay=true while rebuilding a stored session: only the chat view changes
            const handleMessage = (data, replay = false) => {
                if (data.type === 'auth_ok') {
                    // Reopen where we left off: the active project's latest session
                    moveChatTo = null;   // a reconnect opens the right chat already
                    isGenerating.value = true;
                    ws.value.send(JSON.stringify({ type: 'open_session', id: 'latest' }));
                    return;
                }
                if (data.type === 'session_loaded') { moveChatTo = null; loadSession(data); return; }
                if (data.type === 'session_started') { currentSession.value = data.session; return; }
                if (data.type === 'memory_updating') {
                    if (!replay) setMemoryStatus({ state: 'running', text: 'Updating memory…',
                        title: `Reading the finished chat to update ${projectLabel(data.project)}'s memory in the background` });
                    return;
                }
                if (data.type === 'memory_unchanged') {
                    if (!replay) setMemoryStatus({ state: 'unchanged', text: 'Memory: nothing new',
                        title: data.reason + (data.cost != null ? ` (cost ${fmtCost(data.cost)})` : '') }, 4000);
                    return;
                }
                if (data.type === 'project_archived') {
                    // From the Dashboard, in this page or another one (fix25a)
                    fetchProjectsList();
                    if (showSettingsModal.value && settingsTab.value === 'projects') fetchProjectsOverview();
                    leaveArchived(data.project);
                    return;
                }
                if (data.type === 'auth_event') {
                    // Someone signed in or out in another browser (Feature 23)
                    if (showSettingsModal.value && settingsTab.value === 'system') fetchAuth();
                    if (data.event === 'signin') signinNotice.value = data;   // stays until dismissed
                    return;
                }
                if (data.type === 'memory_updated') {
                    if (!replay) {
                        setMemoryStatus({ state: 'updated', text: '✓ Memory updated',
                            title: data.summary + (data.cost != null ? ` (cost ${fmtCost(data.cost)})` : '') }, 4000);
                        memoryToast.value = data;
                        setTimeout(() => { if (memoryToast.value === data) memoryToast.value = null; }, 20000);
                        if (showSettingsModal.value && settingsTab.value === 'memory') { fetchMemory(); fetchCostEstimates(); }
                    }
                    return;
                }
                const lastMsg = messages.value[messages.value.length - 1];

                if (data.type === "stopped") {
                    if (lastMsg.role === 'agent') lastMsg.interrupted = 'stopped';
                    return;
                }
                if (data.type === "system") {
                    if (lastMsg.role !== 'agent') return;
                    if (data.content.startsWith('Execution finished (LLM loop gracefully aborted)')) lastMsg.interrupted = 'aborted';
                    const isSwitch = /^(Escalated|Fell back|Skipping|Conductor:)/.test(data.content);
                    const kind = data.tier ? 'route' : (isSwitch ? 'switch' : 'info');
                    // Mark where a model switch happened inside an answer already under way
                    if (/^(Escalated to |Fell back)/.test(data.content) && (lastMsg.content || (lastMsg.commands || []).length)) {
                        if (lastMsg.blocks) lastMsg.blocks.push({ kind: 'note', text: data.content });
                        else lastMsg.content += `\n\n---\n\n*↪ ${data.content}*\n\n`;
                    }
                    addEvent(lastMsg, data.content, kind);
                    if (data.tier) { lastMsg.tier = data.tier; lastMsg.model = data.model; }
                    else if (/^Escalated to /.test(data.content)) lastMsg.tier = 'complex';
                }
                else if (data.type === "usage") {
                    if (!replay) sessionUsage.value = { tokens: data.session_tokens, cost: data.session_cost, complete: data.session_cost_complete };
                    if (lastMsg.role !== 'agent') return;
                    const u = lastMsg.usage || (lastMsg.usage = { calls: 0, tokens: 0, cost: 0, costKnown: true, ms: 0, estimated: false, byModel: {} });
                    if (!u.byModel) u.byModel = {};
                    u.byModel[data.model] = (u.byModel[data.model] || 0) + 1;
                    u.calls += 1;
                    u.tokens += (data.prompt_tokens || 0) + (data.completion_tokens || 0);
                    u.reasoning = (u.reasoning || 0) + (data.reasoning_tokens || 0);
                    if (data.role !== 'conductor') u.replyReasoning = (u.replyReasoning || 0) + (data.reasoning_tokens || 0);
                    if (data.cost === null || data.cost === undefined) u.costKnown = false; else u.cost += data.cost;
                    u.ms += data.duration_ms || 0;
                    if (data.estimated) u.estimated = true;
                    if (data.role !== 'conductor') lastMsg.model = data.model;
                }
                else if (data.type === "plan") {
                    // v0.4: a plan card; a later event for the same plan updates it in place
                    if (lastMsg.role !== 'agent' || !lastMsg.blocks || !data.plan) return;
                    const old = lastMsg.blocks.find(b => b.kind === 'plan' && b.plan.id === data.plan.id);
                    if (old) old.plan = data.plan; else lastMsg.blocks.push({ kind: 'plan', plan: data.plan });
                }
                else if (data.type === "thinking") {
                    if (lastMsg.role !== 'agent' || !lastMsg.blocks) return;
                    const last = lastMsg.blocks[lastMsg.blocks.length - 1];
                    if (last && last.kind === 'thinking') last.text += data.content;
                    else lastMsg.blocks.push({ kind: 'thinking', text: data.content, open: false });
                }
                else if (data.type === "budget") {
                    if (!replay) budgetStatus.value = data.budget || budgetStatus.value;
                    if (lastMsg.role !== 'agent') return;
                    if (lastMsg.blocks) lastMsg.blocks.push({ kind: 'note', text: data.content });
                    else addEvent(lastMsg, data.content, 'switch');
                    if (data.state === 'blocked' || data.state === 'paused') lastMsg.interrupted = data.state;
                }
                else if (data.type === "step_limit") {
                    if (lastMsg.role !== 'agent') return;
                    // Shown where it happened, at the end of the reply
                    if (lastMsg.blocks) lastMsg.blocks.push({ kind: 'note', text: data.content });
                    else addEvent(lastMsg, data.content, 'switch');
                    lastMsg.interrupted = 'step_limit';
                }
                else if (data.type === "chat_reset") {
                    messages.value = [welcomeMessage('New chat started. The model no longer sees the previous conversation.')];
                    currentSession.value = null;
                }
                else if (data.type === "approval_request") {
                    pendingOutside.value = data.outside_paths || [];
                    pendingSandboxed.value = !!data.sandboxed;
                    pendingWorkdir.value = data.workdir || '';
                    pendingAction.value = data.action || '';  // project actions always ask
                    pendingForm.value = data.form ? { form: data.form, error: data.error || '', github: data.github || {},
                                                      active_project: data.active_project || '', key: ++formSeq } : null;
                    if (appSettings.value.auto_approve && pendingOutside.value.length === 0 && !data.always_ask) {
                        ws.value.send(JSON.stringify({ type: 'approval_response', approved: true }));
                        return;
                    }
                    pendingCommand.value = data.command;
                    pendingApproval.value = true;
                    scrollToBottom();
                }
                else if (data.type === "project_created") {
                    if (!replay) projectCreatedToast(data.project, data.ok, data.github_url);
                }
                else if (data.type === "project_switched") {
                    if (replay) return;  // a reopened chat doesn't change the active project
                    moveChatTo = data.project || '';   // this chat belongs to the old project
                    appSettings.value.active_project = data.project || '';
                    fetchProjectsList();
                    fetchProjectUsage();
                }
                else if (data.type === "projects_changed") {
                    // Also sent when a folder appears or goes in the Workspace outside Optimux (Feature 26)
                    if (!replay) {
                        fetchProjectsList();
                        if (showSettingsModal.value && settingsTab.value === 'projects') fetchProjectsOverview();
                    }
                }
                else if (data.type === "error") {
                    // Reuse the empty reply box opened for this request (e.g. a refused /name), so
                    // no blank box is left above the error
                    const blank = lastMsg && lastMsg.role === 'agent' && !lastMsg.content && !lastMsg.usage
                        && !(lastMsg.blocks || []).length && !(lastMsg.commands || []).length && !(lastMsg.events || []).length;
                    if (blank) Object.assign(lastMsg, { system_note: 'System Error', blocks: [], commands: [] });
                    else messages.value.push({ role: 'agent', content: '', system_note: 'System Error', commands: [] });
                    const errorMsg = messages.value[messages.value.length - 1];
                    errorMsg.content = `❌ **Error:** ${data.content}`;
                    errorMsg.interrupted = 'error';
                    isGenerating.value = false;
                    scrollToBottom();
                }
                else if (data.type === "command_start") {
                    if (!lastMsg.commands) lastMsg.commands = [];
                    const cmd = { kind: 'cmd', command: data.command, output: "" };
                    lastMsg.commands.push(cmd);
                    if (lastMsg.blocks) lastMsg.blocks.push(cmd);  // same object: output updates both
                    scrollToBottom();
                } else if (data.type === "command_output") {
                    const cmdObj = lastMsg.commands.find(c => c.command === data.command && c.exit_code === undefined);
                    if (cmdObj) { cmdObj.output = data.output; cmdObj.exit_code = data.exit_code; }
                    scrollToBottom();
                    if (!replay && appSettings.value.projects_root) {
                        fetchProjectsList();
                    }
                } else if (data.type === "token") {
                    lastMsg.content += data.content;
                    if (lastMsg.blocks) {
                        const last = lastMsg.blocks[lastMsg.blocks.length - 1];
                        if (last && last.kind === 'text') last.text += data.content;
                        else lastMsg.blocks.push({ kind: 'text', text: data.content });
                    }
                    scrollToBottom();
                } else if (data.type === "done") {
                    finalizeReply(lastMsg);
                    if (!replay) { fetchProjectUsage(); if (currentSession.value) fetchSessions(); }
                    isGenerating.value = false;
                    isStopping.value = false;
                    pendingApproval.value = false;
                    pendingForm.value = null;
                    pendingCommand.value = '';
                    if (!replay && moveChatTo !== null && ws.value) {
                        // The model switched projects: the reply is done, so continue in the
                        // new project's own chat (this one stays in History under the old project)
                        const to = moveChatTo;
                        moveChatTo = null;
                        isGenerating.value = true;
                        ws.value.send(JSON.stringify({ type: 'open_session', id: 'latest' }));
                        pushToast({ kind: 'info', title: `Now in ${to || 'the Workspace'}`, text: 'The previous chat is in History.' });
                    }
                }
            };

            // Rebuild a stored session by replaying its events through handleMessage
            loadSession = (data) => {
                readOnlyChat.value = data.read_only && data.session
                    ? { project: data.session.project, reason: data.reason || 'missing' } : null;
                if (data.active_project_changed) fetchConfig();  // the server changed the active project
                if (!data.session) {
                    currentSession.value = null;
                    if (messages.value.length > 1) {
                        messages.value = [welcomeMessage('No previous chat in this project. Start one below.')];
                    }
                    return;
                }
                currentSession.value = data.session;
                const when = new Date(data.session.updated * 1000).toLocaleString();
                const rebuilt = [welcomeMessage(readOnlyChat.value
                    ? `Viewing "${data.session.title || 'Untitled chat'}" from ${when}, read only: its project ${data.session.project} is ${readOnlyChat.value.reason === 'archived' ? 'archived' : 'no longer in the Workspace'}.`
                    : `Reopened "${data.session.title || 'Untitled chat'}" from ${when}.`)];
                messages.value = rebuilt;
                for (const ev of data.events) {
                    const last = messages.value[messages.value.length - 1];
                    if (ev.type === 'prompt' || ev.type === 'escalate') {
                        if (last && last.role === 'agent') finalizeReply(last);
                        if (ev.type === 'prompt') messages.value.push({ role: 'user', content: ev.content });
                        messages.value.push({ role: 'agent', content: '', events: [], commands: [], blocks: [] });
                    } else {
                        handleMessage(ev, true);
                    }
                }
                finalizeReply(messages.value[messages.value.length - 1]);
                if (!data.active_project_changed && !readOnlyChat.value && data.session.project && data.session.project !== appSettings.value.active_project) {
                    fetchConfig();  // the server switched to the session's project
                }
                nextTick(scrollToBottom);
            };

            connect();
        });

        // ---- Stop: cancel the running request on the server
        const isStopping = ref(false);
        const stopGeneration = () => {
            if (isStopping.value) return;
            isStopping.value = true;
            pendingApproval.value = false;
            pendingForm.value = null;
            pendingCommand.value = '';
            try { ws.value.send(JSON.stringify({ type: 'stop' })); } catch (_) {}
            // The server answers with "done" almost at once; release the
            // input anyway if it doesn't (e.g. the connection dropped).
            setTimeout(() => {
                if (isStopping.value) {
                    isStopping.value = false;
                    isGenerating.value = false;
                }
            }, 5000);
        };
        window.addEventListener('keydown', (e) => {
            if (e.key !== 'Escape') return;
            if (confirmBox.value) { answerConfirm(false); return; }
            if (deleteBox.value) { deleteBox.value = null; return; }
            if (newProject.value.open) { newProject.value.open = false; return; }
            if (viewer.value.open) { closeViewer(); return; }  // the code viewer closes first
            if (showSettingsModal.value) { closeSettings(); return; }  // leave the dashboard first
            if (isGenerating.value || pendingApproval.value) stopGeneration();
        });

        const sendApprovalResponse = async (isApproved, alwaysApprove = false) => {
            if (alwaysApprove && isApproved) {
                appSettings.value.auto_approve = true;
                await saveSettings();
            }
            ws.value.send(JSON.stringify({ type: 'approval_response', approved: isApproved }));
            pendingApproval.value = false;
            pendingForm.value = null;
            pendingCommand.value = '';
            pendingOutside.value = [];
        };

        const sendMessage = () => {
            if (!userInput.value.trim() || readOnlyChat.value) return;

            messages.value.push({ role: 'user', content: userInput.value });
            messages.value.push({ role: 'agent', content: '', events: [], commands: [], blocks: [] });
            
            ws.value.send(JSON.stringify({ prompt: userInput.value, model: selectedModel.value }));
            
            userInput.value = '';
            isGenerating.value = true;
            scrollToBottom();
        };

        const autoResize = () => {
            if (chatInputRef.value) {
                chatInputRef.value.style.height = 'auto';
                chatInputRef.value.style.height = (chatInputRef.value.scrollHeight) + 'px';
            }
        };

        watch(userInput, () => {
            nextTick(() => {
                autoResize();
            });
        });

        // ---- Copy session as Markdown
        const copyLabel = ref('Copy session as Markdown');

        // A code fence longer than any backtick run inside the text
        const fenceFor = (text) => {
            const runs = (text || '').match(/`+/g) || [];
            const longest = runs.reduce((max, r) => Math.max(max, r.length), 0);
            return '`'.repeat(Math.max(3, longest + 1));
        };

        // Markdown lines for one message (shared by the session and single-reply copies)
        const messageLines = (m) => {
            const lines = [];
            if (m.role === 'user') {
                lines.push('## You', '', m.content || '', '');
                return lines;
            }
            lines.push('## Optimux', '');
            if (m.events && m.events.length) {
                m.events.forEach(ev => lines.push(`> ${ev.text}`));
                lines.push('');
            } else if (m.system_note) {
                lines.push(`> ${m.system_note}`, '');
            }
            const items = (m.blocks && m.blocks.length) ? m.blocks
                : [...(m.commands || []).map(c => ({ kind: 'cmd', ...c })), ...(m.content ? [{ kind: 'text', text: m.content }] : [])];
            items.forEach(b => {
                if (b.kind === 'text') { lines.push(b.text, ''); return; }
                if (b.kind === 'note') { lines.push(`*↪ ${b.text}*`, ''); return; }
                if (b.kind === 'plan') { lines.push(...planLines(b.plan), ''); return; }
                if (b.kind === 'thinking' && !b.text) { lines.push(`*Thinking: ${b.tokens} tokens (text not shared by the model)*`, ''); return; }
                if (b.kind === 'thinking') { lines.push('<details><summary>Thinking</summary>', '', b.text.trim(), '', '</details>', ''); return; }
                const output = (b.output || '').replace(/\n+$/, '');
                const fence = fenceFor(b.command + '\n' + output);
                lines.push(fence + 'bash', `$ ${b.command}`);
                if (output) lines.push(output);
                lines.push(fence, b.exit_code !== undefined ? `_exit ${b.exit_code}_` : '_not finished_', '');
            });
            if (m.usage) {
                const u = m.usage;
                lines.push(`_${callsLabel(u)} · ${u.estimated ? '≈' : ''}${fmtTokens(u.tokens)} tokens${u.reasoning ? ' (' + fmtTokens(u.reasoning) + ' thinking)' : ''} · ${u.costKnown ? fmtCost(u.cost) : (u.cost > 0 ? fmtCost(u.cost) + '+' : 'cost unknown')} · ${fmtMs(u.ms)}_`, '');
            }
            return lines;
        };

        // A plan card as Markdown (chat export)
        const planLines = (p) => {
            const out = [`**Plan: ${p.goal}** (${p.status})`, ''];
            (p.steps || []).forEach(s => {
                out.push(`${s.id}. **${s.title}** [${s.tier}]`, `   - Do: ${s.do}`, `   - Done when: ${s.done_when}`);
                if (s.files && s.files.length) out.push(`   - Files: ${s.files.map(f => '`' + f + '`').join(', ')}`);
            });
            if (p.check) out.push('', `Final check: ${p.check}`);
            return out;
        };

        const sessionMarkdown = () => {
            const lines = ['# Optimux session', '', `- Exported: ${new Date().toLocaleString()}`];
            if (appSettings.value.active_project) lines.push(`- Project: ${appSettings.value.active_project}`);
            lines.push('');
            messages.value.forEach((m, i) => {
                // Skip the local "Optimux initialized" greeting
                if (i === 0 && m.role === 'agent' && m.system_note === 'System') return;
                lines.push(...messageLines(m));
            });
            return lines.join('\n').trim() + '\n';
        };

        // One question and its answer: the reply at `index` plus the user message before it
        const exchangeMarkdown = (index) => {
            const lines = [];
            const prev = messages.value[index - 1];
            if (prev && prev.role === 'user') lines.push(...messageLines(prev));
            lines.push(...messageLines(messages.value[index]));
            return lines.join('\n').trim() + '\n';
        };

        const writeClipboard = async (text) => {
            try {
                // Needs a secure context: HTTPS or localhost
                await navigator.clipboard.writeText(text);
                return true;
            } catch (_) {
                // Fallback for plain-HTTP access (e.g. a VPS without TLS)
                const ta = document.createElement('textarea');
                ta.value = text;
                ta.style.position = 'fixed';
                ta.style.opacity = '0';
                document.body.appendChild(ta);
                ta.select();
                let ok = false;
                try { ok = document.execCommand('copy'); } catch (_) { ok = false; }
                document.body.removeChild(ta);
                return ok;
            }
        };

        const copySession = async () => {
            const ok = await writeClipboard(sessionMarkdown());
            copyLabel.value = ok ? 'Copied!' : 'Copy failed';
            setTimeout(() => { copyLabel.value = 'Copy session as Markdown'; }, 2000);
        };

        const copyThinking = async (blk) => {
            blk.copied = await writeClipboard(blk.text);
            setTimeout(() => { blk.copied = false; }, 2000);
        };
        const copiedIndex = ref(null);
        const copyExchange = async (index) => {
            const ok = await writeClipboard(exchangeMarkdown(index));
            copiedIndex.value = ok ? index : -1;
            setTimeout(() => { copiedIndex.value = null; }, 2000);
        };

        const handleEnter = (e) => {
            if (slashSuggestions.value.length) {
                const n = slashSuggestions.value.length;
                if (e.key === 'ArrowDown') { e.preventDefault(); slashIndex.value = (slashIndex.value + 1) % n; return; }
                if (e.key === 'ArrowUp') { e.preventDefault(); slashIndex.value = (slashIndex.value + n - 1) % n; return; }
                if (e.key === 'Tab' || (e.key === 'Enter' && !e.shiftKey)) { e.preventDefault(); pickSlash(slashSuggestions.value[slashIndex.value]); return; }
            }
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                if (!isGenerating.value && !pendingApproval.value && userInput.value.trim()) {
                    sendMessage();
                }
            }
        };

        return { 
            userInput, chatInputRef, messages, sendMessage, handleEnter, isGenerating,
            selectedModel, availableModels, setupModels, handleModelChange,
            showSetupModal, modalInstructions, closeModal,
            keyInput, isSavingKey, saveKey,
            pendingApproval, pendingCommand, sendApprovalResponse,
					renderMarkdown,
            showSettingsModal, openSettings, closeSettings, settingsTab, dashboardSections, dashboardGroups,
            saveKeyDirect, newModelId, newModelName, newModelEnvKey, newModelApiKey, addCustomModel, deleteCustomModel,
            editingKeys, routingConfig, saveRouting, showRoutingSaved,
            appSettings, projectsList, saveSettings, showSettingsSaved,
            memoryTab, memoryContent, fetchMemory, saveMemory, showMemorySaved,
            copySession, copyLabel,
            stopGeneration, isStopping,
            newModelPrices, modelNotices, cat, catPid, catRows, catInfo, catModel, catToolsOnly, catMineOnly, catHideRetired, catSearch, catSort, catBusy, catMsg, catRefreshMsg, catOwnPrices, catKey, catReplaceKey, catName, catProvider, configuredIds, catVisible, catSelected, catNeedsKey, pickProvider, catSortBy, fmtMtok, fmtCtx, fmtWhen, fmtLimits, fmtLimitsTitle, catChoose, addCatalogModel, setCatalogOnline, catRefresh, testingModels, editingPrices, togglePriceEdit, savePrices, testModel,
            usageData, usagePeriod, cooldowns, fetchUsage, sessionUsage, clearCooldown,
            fmtTokens, fmtCost, fmtMs, modelName,
            newChat, canEscalate, escalate, continueTurn, pendingOutside, pendingWorkdir,
            projectUsage, callsLabel, copyExchange, copiedIndex,
            budgetStatus, budgets, budgetEdits, budgetRows, saveBudget, openBudgets,
            setThinking, copyThinking, storageInfo, fmtBytes,
            currentSession, readOnlyChat, leaveReadOnly, historyOpen, historyScope, historyQuery, historyList, fetchSessions,
            toggleHistory, openSession, goHome, renameSession, archiveSession, deleteSession, changeProject,
            memoryProject, memoryProjectExists, dailyUsage, chartProject, fetchDaily, recentChats, projectsOverview,
            snapshotsInfo, snapForm, snapKinds, snapSetProjects, snapTouchProjects, snapNothingChosen, snapKindLabel, snapConfirm, snapOpen, snapDeleting, snapReadme, loadSnapReadme, onCopyCodeClick, snapCopied, snapCopy, snapDl, dlOpen, dlClose, dlPrepare, dlSaveKey, dlStarted, dlCommand, snapLeftOutProjects, snapProceedLabel, snapPreview, snapBusy, snapError, snapLimit, snapProblems, previewSnapshot, takeSnapshot, deleteSnapshot, saveSnapshotLimit,
            rsStatus, rsPanel, rsRestarting, fetchRestoreStatus, openRestore, closeRestore, rsChosen, rsReady, doRestore, restartForRestore, cancelRestore, dismissRestore, rsUp, rsPickFile, rsReadKeyFile, rsUpload, rsImport,
            historyPage, historySorted, fetchHistoryPage, sortHistory, systemInfo, newToken, rotateToken,
            authInfo, securityChecks, signinNotice, browserLabel, signOutOthersFromNotice, pwForm, totpSetup, totpOff, fetchAuth, signOut, signOutOthers, changePassword,
            totpStart, totpConfirm, totpDisable, commandEnvText, saveCommandSettings,
            openChatFromDashboard, switchProject, routingRole, fmtAgo, costChart, overviewKpis, writeClipboard,
            historyFiltered, resetHistoryFilters, lastPromptBefore, retryFrom, interruptionLabel, wsState, headerCostTitle,
            pendingAction, pendingForm, sendProjectForm, toasts, dismissToast, githubInfo, githubToken, githubMsg, githubBusy,
            fetchGithub, saveGithubToken, gitIdentity, saveGitIdentity, checkGithub, removeGithub, newProject, openNewProject, createProjectFromForm,
            ROLE_HELP, setupGithubFromForm, ghImport, loadGithubRepos, showGithubTab, filteredRepos, shownRepos, pickRepo, importNameProblem,
            startClone, cancelClone, openGroups, toggleGroup, archivedNames, usageRows, activeBudgets, workspacePath, archivePath, projectPath, copyPath, deletedProjects, archivedProjects, archiveFromDashboard, restoreArchived, deleteBox,
            openDelete, deletePhraseOk, confirmDelete, skillsInfo, skillEditor, skillDrafting, fetchSkills, newSkill, editSkill, saveSkill,
            removeSkill, saveAsSkill, skillDialog, confirmSkillDialog,
            setSkillsMode, setSkillMode, slashSuggestions, slashIndex, pickSlash,
            memoryVersions, curating, curateResult, memoryToast, fetchMemoryVersions, undoMemoryVersion,
            curateNow, viewMemoryFromToast, undoFromToast,
            confirmBox, confirmOkRef, answerConfirm, costEstimates, memoryStatus,
            guide, guideQuery, guideActive, guideResults, goGuide, onDashScroll, loadGuide,
            sandboxInfo, sandboxPathsText, fetchSandbox, saveSandboxPaths, pendingSandboxed, toggleSandbox,
            projectLabel, viewer, viewerLanguages, openCmdViewer, onChatClick, closeViewer, rehighlightViewer, copyViewerSection
        };
    }
}).component('help-tip', HelpTip).component('project-form', ProjectForm).component('deleted-tag', DeletedTag).mount('#app');
