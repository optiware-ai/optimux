// What kind of text a pane holds, for its View button (v0.8). Plain functions, used by app.js and
// tested with Node (tests/test_sent_view.py).
//  - detect(text): 'json' | 'md' | 'code' | null (null: plain words, nothing to show better).
//  - pretty(text): the same JSON with two-space indentation; a tool call's name stays above it.
(function (root) {
    // a tool call part is its name, a line break and the arguments as JSON
    const CALL = /^([\w.-]+)\n([[{][\s\S]*[\]}])$/;

    function parse(t) {
        try { return JSON.parse(t); } catch (_) { return undefined; }
    }

    function isJson(t) {
        if (/^[[{]/.test(t) && parse(t) !== undefined) return true;
        const m = t.match(CALL);
        return !!m && parse(m[2]) !== undefined;
    }

    // Markdown shows by its marks: a heading, a fence, a table, two list items, or two inline marks
    function isMarkdown(t) {
        const lines = t.split('\n');
        const count = (re) => lines.filter((l) => re.test(l)).length;
        if (count(/^#{1,6}\s+\S/) || count(/^\s*```/) || count(/^\s*\|.+\|\s*$/) >= 2 || count(/^>\s+\S/)) return true;
        if (count(/^\s*([-*+]|\d+[.)])\s+\S/) >= 2) return true;
        const inline = (t.match(/\*\*[^*\n]+\*\*|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\)/g) || []).length;
        return inline >= 2;
    }

    // Code shows by how its lines look: most of them end like code or start with a keyword
    function isCode(t) {
        const lines = t.split('\n').filter((l) => l.trim());
        if (lines.length < 3) return false;
        const word = /^\s*(def|class|import|from|function|const|let|var|return|if|for|while|elif|else|try|except|public|private)\b/;
        const looks = lines.filter((l) => /[;{}]\s*$/.test(l) || /^\s*(#include\b|<\/?[a-z][\w-]*[\s>])/.test(l) || /^(\t| {2,})\S/.test(l) || (word.test(l) && /[(){}:=<>]/.test(l))).length;
        return looks / lines.length >= 0.6;
    }

    function detect(text) {
        const t = String(text == null ? '' : text).trim();
        if (t.length < 2) return null;
        if (isJson(t)) return 'json';
        if (isMarkdown(t)) return 'md';
        if (isCode(t)) return 'code';
        return null;
    }

    function pretty(text) {
        const t = String(text == null ? '' : text).trim();
        const m = t.match(CALL);
        if (m && parse(m[2]) !== undefined) return m[1] + '\n' + JSON.stringify(parse(m[2]), null, 2);
        const v = parse(t);
        return v === undefined ? String(text) : JSON.stringify(v, null, 2);
    }

    const api = { detect, pretty };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OptimuxSentView = api;
})(typeof window !== 'undefined' ? window : globalThis);
