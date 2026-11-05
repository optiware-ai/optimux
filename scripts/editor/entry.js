// The Studio's code editor (v0.8): CodeMirror 6, bundled once into one pinned file in
// web/vendor/ by scripts/build-editor.sh (Optimux has no bundler at run time).
// window.OptimuxEditor.create(parent, options) returns a small handle the page drives.
import { EditorState, Compartment } from '@codemirror/state';
import {
  EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter, drawSelection, highlightSpecialChars,
} from '@codemirror/view';
import { defaultKeymap, history, historyKeymap, indentWithTab } from '@codemirror/commands';
import { searchKeymap, highlightSelectionMatches } from '@codemirror/search';
import { closeBrackets, closeBracketsKeymap } from '@codemirror/autocomplete';
import { HighlightStyle, bracketMatching, indentOnInput, syntaxHighlighting, StreamLanguage, foldGutter, foldKeymap } from '@codemirror/language';
import { tags as t } from '@lezer/highlight';
import { javascript } from '@codemirror/lang-javascript';
import { html } from '@codemirror/lang-html';
import { css } from '@codemirror/lang-css';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { python } from '@codemirror/lang-python';
import { xml } from '@codemirror/lang-xml';
import { yaml } from '@codemirror/lang-yaml';
import { sql } from '@codemirror/lang-sql';
import { shell } from '@codemirror/legacy-modes/mode/shell';
import { toml } from '@codemirror/legacy-modes/mode/toml';
import { dockerFile } from '@codemirror/legacy-modes/mode/dockerfile';
import { go } from '@codemirror/legacy-modes/mode/go';
import { rust } from '@codemirror/legacy-modes/mode/rust';
import { properties } from '@codemirror/legacy-modes/mode/properties';
import { nginx } from '@codemirror/legacy-modes/mode/nginx';

// Keywords purple, types teal, strings green, numbers amber, function calls blue, comments grey italic
const colours = HighlightStyle.define([
  { tag: [t.keyword, t.controlKeyword, t.moduleKeyword, t.operatorKeyword], color: '#c084fc' },
  { tag: [t.typeName, t.className, t.namespace], color: '#5eead4' },
  { tag: [t.string, t.special(t.string), t.regexp], color: '#86efac' },
  { tag: [t.number, t.integer, t.float], color: '#fbbf24' },
  { tag: [t.atom, t.bool, t.null, t.unit], color: '#fca5a5' },
  { tag: [t.meta, t.annotation, t.processingInstruction], color: '#f9a8d4' },
  { tag: [t.function(t.variableName), t.function(t.propertyName)], color: '#93c5fd' },
  { tag: [t.tagName], color: '#f87171' },
  { tag: [t.attributeName, t.propertyName], color: '#7dd3fc' },
  { tag: [t.heading], color: '#f3f4f6', fontWeight: 'bold' },
  { tag: [t.link, t.url], color: '#60a5fa', textDecoration: 'underline' },
  { tag: [t.emphasis], fontStyle: 'italic' },
  { tag: [t.strong], fontWeight: 'bold' },
  { tag: [t.comment, t.lineComment, t.blockComment], color: '#6b7280', fontStyle: 'italic' },
  { tag: [t.operator, t.punctuation], color: '#d1d5db' },
  { tag: [t.variableName], color: '#e5e7eb' },
  { tag: [t.invalid], color: '#f87171' },
]);

const theme = EditorView.theme({
  '&': { backgroundColor: '#030712', color: '#e5e7eb', fontSize: '13px', height: '100%' },
  '.cm-content': { fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', caretColor: '#f3f4f6', padding: '8px 0' },
  '.cm-scroller': { lineHeight: '20px', fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace', overflow: 'auto' },
  '.cm-gutters': { backgroundColor: '#030712', color: '#4b5563', border: 'none', borderRight: '1px solid #1f2937' },
  '.cm-activeLine': { backgroundColor: 'rgba(255,255,255,0.03)' },
  '.cm-activeLineGutter': { backgroundColor: 'transparent', color: '#9ca3af' },
  '&.cm-focused': { outline: 'none' },
  '&.cm-focused .cm-cursor': { borderLeftColor: '#f3f4f6' },
  '&.cm-focused .cm-selectionBackground, .cm-selectionBackground, ::selection': { backgroundColor: '#1e3a8a' },
  '.cm-matchingBracket': { backgroundColor: 'rgba(59,130,246,0.25)', outline: 'none' },
  '.cm-panels': { backgroundColor: '#111827', color: '#e5e7eb' },
  '.cm-panels input, .cm-panels button': { color: '#e5e7eb' },
  '.cm-searchMatch': { backgroundColor: 'rgba(251,191,36,0.25)' },
  '.cm-foldGutter span': { color: '#4b5563' },
}, { dark: true });

const legacy = (mode) => StreamLanguage.define(mode);
const BY_EXT = {
  js: () => javascript(), mjs: () => javascript(), cjs: () => javascript(), jsx: () => javascript({ jsx: true }),
  ts: () => javascript({ typescript: true }), mts: () => javascript({ typescript: true }), tsx: () => javascript({ jsx: true, typescript: true }),
  html: () => html(), htm: () => html(), vue: () => html(), svelte: () => html(),
  css: () => css(), scss: () => css(), less: () => css(),
  json: () => json(), webmanifest: () => json(),
  md: () => markdown(), markdown: () => markdown(),
  py: () => python(), pyi: () => python(),
  xml: () => xml(), svg: () => xml(),
  yml: () => yaml(), yaml: () => yaml(),
  sql: () => sql(),
  sh: () => legacy(shell), bash: () => legacy(shell), zsh: () => legacy(shell),
  toml: () => legacy(toml), go: () => legacy(go), rs: () => legacy(rust),
  ini: () => legacy(properties), cfg: () => legacy(properties), conf: () => legacy(nginx), env: () => legacy(properties),
};
const BY_NAME = { dockerfile: () => legacy(dockerFile), makefile: () => legacy(shell), '.env': () => legacy(properties),
                  '.bashrc': () => legacy(shell), '.gitignore': () => legacy(properties) };

/** The language support for a file path, or null (plain text). */
export function languageFor(path) {
  const name = String(path || '').split('/').pop().toLowerCase();
  if (BY_NAME[name]) return BY_NAME[name]();
  if (name.startsWith('.env')) return legacy(properties);
  const ext = name.includes('.') ? name.split('.').pop() : '';
  return BY_EXT[ext] ? BY_EXT[ext]() : null;
}

/** A language name for the status line. */
export function languageName(path) {
  const name = String(path || '').split('/').pop().toLowerCase();
  const ext = name.includes('.') ? name.split('.').pop() : '';
  const names = { js: 'JavaScript', mjs: 'JavaScript', cjs: 'JavaScript', jsx: 'JSX', ts: 'TypeScript', mts: 'TypeScript', tsx: 'TSX',
                  html: 'HTML', htm: 'HTML', vue: 'Vue', svelte: 'Svelte', css: 'CSS', scss: 'SCSS', less: 'Less', json: 'JSON',
                  md: 'Markdown', markdown: 'Markdown', py: 'Python', xml: 'XML', svg: 'SVG', yml: 'YAML', yaml: 'YAML', sql: 'SQL',
                  sh: 'Shell', bash: 'Shell', toml: 'TOML', go: 'Go', rs: 'Rust', ini: 'INI', conf: 'Config' };
  if (name === 'dockerfile') return 'Dockerfile';
  return names[ext] || 'Plain text';
}

/**
 * create(parent, { label, nonce, onChange(text), onSave() }) -> handle
 * nonce: the page's CSP style nonce (CodeMirror adds its styles as <style> elements)
 * Each open file keeps its own state (undo history, selection) while the page keeps it open.
 * handle.show(id, text, path): show a file (a new id gets a fresh state); handle.replace(text): new text
 * handle.selection(): the main selection with its lines; handle.selectAll().
 * for the shown file (a reload from disk); handle.text(); handle.forget(ids kept); handle.focus(); handle.destroy().
 */
export function create(parent, options = {}) {
  const states = new Map();
  let shown = null;
  const readOnly = new Compartment();
  const make = (text, path) => EditorState.create({
    doc: text,
    extensions: [
      lineNumbers(), foldGutter(), highlightActiveLineGutter(), highlightSpecialChars(), history(), drawSelection(),
      indentOnInput(), bracketMatching(), closeBrackets(), highlightActiveLine(), highlightSelectionMatches(),
      keymap.of([
        { key: 'Mod-s', preventDefault: true, run: () => { if (options.onSave) options.onSave(); return true; } },
        ...closeBracketsKeymap, ...defaultKeymap, ...historyKeymap, ...searchKeymap, ...foldKeymap, indentWithTab,
      ]),
      ...(languageFor(path) ? [languageFor(path)] : []),
      syntaxHighlighting(colours), theme, readOnly.of(EditorState.readOnly.of(false)),
      EditorView.contentAttributes.of({ 'aria-label': options.label || 'Code editor' }),
      ...(options.nonce ? [EditorView.cspNonce.of(options.nonce)] : []),
      EditorView.updateListener.of((u) => { if (u.docChanged && options.onChange) options.onChange(u.state.doc.toString()); }),
    ],
  });
  const view = new EditorView({ state: make('', ''), parent });
  return {
    show(id, text, path) {
      if (shown !== null) states.set(shown, view.state);
      view.setState(states.get(id) || make(text, path));
      shown = id;
    },
    replace(text) {
      if (view.state.doc.toString() !== text) view.dispatch({ changes: { from: 0, to: view.state.doc.length, insert: text } });
    },
    text() { return view.state.doc.toString(); },
    forget(keep) { for (const id of Array.from(states.keys())) if (!keep.includes(id)) states.delete(id); if (!keep.includes(shown)) states.delete(shown); },
    drop(id) { states.delete(id); },
    focus() { view.focus(); },
    // v0.8: the main selection, for "Send to chat": {text, fromLine, toLine, empty}; the cursor's line when empty
    selection() {
      const r = view.state.selection.main, doc = view.state.doc;
      const from = doc.lineAt(r.from), to = doc.lineAt(r.to);
      return { text: doc.sliceString(r.from, r.to), fromLine: from.number, toLine: to.number, empty: r.empty, lineText: from.text };
    },
    selectAll() { view.dispatch({ selection: { anchor: 0, head: view.state.doc.length } }); view.focus(); },
    destroy() { view.destroy(); states.clear(); },
  };
}
