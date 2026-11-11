// The chat box's typing helpers (v0.8). Plain functions, used by app.js and tested
// with Node (tests/test_chat_input.py).
//  - listEnter: Shift+Enter in a list line continues the list ("2. " after "1. x",
//    "- " after "- x"); on an empty item it ends the list instead.
//  - listSpace: the space after "1." or "-" at the start of a line indents the item.
//  - cleanForSend: drops an unused marker left on the last line.
//  - userMarkdown: a line that is only `text` becomes a terminal block (```term).
(function (root) {
    // indent, number, "." or ")", bullet, the item's text (none for an empty item)
    const ITEM = /^(\s*)(?:(\d{1,6})([.)])|([-*•]))(?:\s+(.*))?$/;
    const EMPTY_ITEM = /^\s*(?:\d{1,6}[.)]|[-*•])\s*$/;

    function listEnter(value, start, end) {
        if (start !== end) return null;
        const lineStart = value.lastIndexOf('\n', start - 1) + 1;
        const nl = value.indexOf('\n', start);
        const lineEnd = nl === -1 ? value.length : nl;
        const line = value.slice(lineStart, lineEnd);
        const m = line.match(ITEM);
        if (!m) return null;
        const [, indent, num, delim, bullet, text = ''] = m;
        if (!text.trim()) {
            // An empty item: Shift+Enter ends the list (the line goes back to plain text)
            return { value: value.slice(0, lineStart) + value.slice(lineEnd), cursor: lineStart };
        }
        if (start < lineEnd - text.length) return null;          // the cursor is in the marker: a plain newline
        const next = bullet || String(Number(num) + 1) + delim;
        const before = value.slice(0, start).replace(/[ \t]+$/, '');
        const after = value.slice(start).replace(/^[ \t]+/, '');
        const insert = '\n' + indent + next + ' ';
        return { value: before + insert + after, cursor: before.length + insert.length };
    }

    const INDENT = '  ';      // two spaces: still a list for Markdown (up to three are allowed)

    function listSpace(value, start, end) {
        if (start !== end) return null;
        const lineStart = value.lastIndexOf('\n', start - 1) + 1;
        const marker = value.slice(lineStart, start);
        if (!/^(?:\d{1,6}[.)]|[-*•])$/.test(marker)) return null;
        const rest = value.slice(start);
        const insert = INDENT + marker + ' ';
        return { value: value.slice(0, lineStart) + insert + rest.replace(/^[ \t]+/, ''), cursor: lineStart + insert.length };
    }

    function cleanForSend(text) {
        const lines = text.replace(/\s+$/, '').split('\n');
        while (lines.length && EMPTY_ITEM.test(lines[lines.length - 1])) lines.pop();
        return lines.join('\n').replace(/\s+$/, '');
    }

    function userMarkdown(text) {
        let fence = false;
        return text.split('\n').map((line) => {
            if (/^\s*(```|~~~)/.test(line)) { fence = !fence; return line; }
            if (fence) return line;
            const m = line.match(/^\s*`([^`]+)`\s*$/);
            return m ? '```term\n' + m[1] + '\n```' : line;
        }).join('\n');
    }

    const api = { listEnter, listSpace, cleanForSend, userMarkdown };
    if (typeof module !== 'undefined' && module.exports) module.exports = api;
    else root.OptimuxChatInput = api;
})(typeof window !== 'undefined' ? window : globalThis);
