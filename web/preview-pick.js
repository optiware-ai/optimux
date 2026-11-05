/* Optimux Studio picker (v0.8): select an element or an area of a previewed page and
   send it to the chat. The server puts this script into every HTML page the Studio
   previews. The page runs sandboxed with an opaque origin, so this script talks to the
   Studio only through postMessage, and it does nothing when the page isn't inside the
   Studio's frame (e.g. opened in its own tab).

   Studio → page: {optimux: 'pick-cmd', cmd: 'element' | 'area' | 'at' | 'locate' (want) | 'match-css' (rules) | 'cancel', x, y}
   Page → Studio: {optimux: 'pick', type: 'menu' (x, y, what) | 'dismiss' | 'mode' (mode)
                   | 'selected' (payload) | 'cancelled' | 'locate' (want, what, payload) | 'css' (what, matches)}
   Shift + right-click keeps the browser's own menu. */
(function () {
    'use strict';
    if (window.parent === window || window.__optimuxPick) return;
    window.__optimuxPick = true;

    // The Studio's scrollbars in the preview too (only inside the Studio's frame). Put first in
    // <head>, so a page's own scrollbar styles still win; a mid-grey thumb reads on light and dark pages.
    try {
        var sb = document.createElement('style');
        sb.setAttribute('data-optimux', 'scrollbars');
        sb.textContent =
            '::-webkit-scrollbar{width:10px;height:10px}' +
            '::-webkit-scrollbar-track,::-webkit-scrollbar-corner{background:transparent}' +
            '::-webkit-scrollbar-thumb{background-color:rgba(100,116,139,.45);border-radius:9999px;border:2px solid transparent;background-clip:content-box;min-height:32px}' +
            '::-webkit-scrollbar-thumb:hover{background-color:#3b82f6}::-webkit-scrollbar-thumb:active{background-color:#60a5fa}' +
            '::-webkit-scrollbar-button{display:none;width:0;height:0}' +
            '@supports not selector(::-webkit-scrollbar){html{scrollbar-width:thin;scrollbar-color:rgba(100,116,139,.55) transparent}}';
        var head = document.head || document.documentElement;
        head.insertBefore(sb, head.firstChild);
    } catch (e) { /* no styles: the browser's scrollbars */ }

    var LIMIT_HTML = 4000, LIMIT_TEXT = 1500, LIMIT_AREA_HTML = 8000, MAX_ELEMENTS = 25, MIN_AREA = 8;
    var STYLE_KEYS = ['display', 'position', 'font-family', 'font-size', 'font-weight', 'line-height', 'color',
                      'background-color', 'background-image', 'margin', 'padding', 'border', 'border-radius', 'gap', 'text-align'];
    var host = null, root = null, els = {};
    var mode = null;            // null | 'element' | 'area' | 'chosen'
    var hoverEl = null, chosen = null, frame = 0;

    function post(msg) {
        msg.optimux = 'pick';
        try { window.parent.postMessage(msg, '*'); } catch (e) { /* the Studio is gone */ }
    }

    // ---- what the chat gets
    function clean(s, max) {
        s = String(s || '').replace(/\s+/g, ' ').trim();
        return s.length > max ? s.slice(0, max) + '…' : s;
    }
    function cut(s, max) {
        s = String(s || '');
        return s.length > max ? s.slice(0, max) + '\n… (cut, ' + s.length.toLocaleString() + ' characters in all)' : s;
    }
    function esc(s) { return window.CSS && CSS.escape ? CSS.escape(s) : s; }
    function selector(el) {
        var parts = [];
        for (var e = el; e && e.nodeType === 1 && e !== document.documentElement && parts.length < 6; e = e.parentElement) {
            var s = e.tagName.toLowerCase();
            if (e.id) { parts.unshift(s + '#' + esc(e.id)); break; }
            var cls = Array.prototype.filter.call(e.classList, function (c) { return c.length < 40; }).slice(0, 3);
            if (cls.length) s += '.' + cls.map(esc).join('.');
            var p = e.parentElement;
            if (p) {
                var same = Array.prototype.filter.call(p.children, function (c) { return c.tagName === e.tagName; });
                if (same.length > 1) s += ':nth-of-type(' + (same.indexOf(e) + 1) + ')';
            }
            parts.unshift(s);
        }
        return parts.join(' > ');
    }
    function box(el) {
        var r = el.getBoundingClientRect();
        return { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY), w: Math.round(r.width), h: Math.round(r.height) };
    }
    function page() {
        // /preview/<project>/<token>/<path>: never send the private token
        var parts = location.pathname.split('/');
        return decodeURIComponent(parts.slice(4).join('/')) || 'index.html';
    }
    function short(el) {
        var t = clean(el.innerText || el.getAttribute('alt') || '', 40);
        return '<' + el.tagName.toLowerCase() + '>' + (t ? ' ' + t : '');
    }
    function describe(el) {
        var cs = getComputedStyle(el), styles = {};
        STYLE_KEYS.forEach(function (k) {
            var v = cs.getPropertyValue(k);
            if (v && !/^(none|normal|auto|0px|rgba\(0, 0, 0, 0\))$/.test(v) && !/^0px none/.test(v)) styles[k] = v;
        });
        var d = { kind: 'element', page: page(), selector: selector(el), tag: el.tagName.toLowerCase(),
                  text: clean(el.innerText, LIMIT_TEXT), html: cut(el.outerHTML, LIMIT_HTML), styles: styles,
                  rect: box(el), viewport: { w: window.innerWidth, h: window.innerHeight } };
        if (el.tagName === 'IMG') d.image = { src: el.currentSrc || el.src, alt: el.alt || '', natural: el.naturalWidth + '×' + el.naturalHeight };
        return d;
    }
    function inArea(a) {
        var picked = [], total = 0, more = 0;
        var walker = document.createTreeWalker(document.body || document.documentElement, NodeFilter.SHOW_ELEMENT);
        for (var el = walker.currentNode; el; el = walker.nextNode()) {
            if (el === host || /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|BODY|HTML|HEAD)$/.test(el.tagName)) continue;
            if (picked.some(function (p) { return p.contains(el); })) continue;
            var b = box(el);
            if (!b.w || !b.h || b.x < a.x || b.y < a.y || b.x + b.w > a.x + a.w || b.y + b.h > a.y + a.h) continue;
            if (getComputedStyle(el).visibility === 'hidden') continue;
            if (picked.length >= MAX_ELEMENTS) { more++; continue; }
            picked.push(el);
        }
        var list = picked.map(function (el) {
            var html = el.outerHTML;
            var room = Math.max(0, LIMIT_AREA_HTML - total);
            total += Math.min(html.length, room);
            return { selector: selector(el), tag: el.tagName.toLowerCase(), text: clean(el.innerText, 300),
                     html: room ? cut(html, Math.min(room, LIMIT_HTML)) : '', rect: box(el) };
        });
        var d = { kind: 'area', page: page(), rect: a, viewport: { w: window.innerWidth, h: window.innerHeight },
                  elements: list, more: more };
        if (!list.length) {
            // The area lies inside one element: say which
            var c = document.elementFromPoint(a.x - window.scrollX + a.w / 2, a.y - window.scrollY + a.h / 2);
            if (c && c !== host) d.inside = { selector: selector(c), tag: c.tagName.toLowerCase(), text: clean(c.innerText, 300) };
        }
        return d;
    }

    // ---- the overlay (in a closed shadow root, so the page's CSS can't touch it)
    function ui() {
        if (host) return;
        host = document.createElement('optimux-pick');
        host.style.cssText = 'all:initial;position:fixed;inset:0;z-index:2147483647;pointer-events:none;display:block;';
        root = host.attachShadow({ mode: 'closed' });
        root.innerHTML =
            '<style>' +
            ':host{all:initial}*{box-sizing:border-box;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}' +
            '.box{position:fixed;border:2px solid #3b82f6;background:rgba(59,130,246,.12);border-radius:4px;pointer-events:none}' +
            '.box.on{border-color:#60a5fa;background:rgba(59,130,246,.08);box-shadow:0 0 0 9999px rgba(3,7,18,.35)}' +
            '.label{position:fixed;background:#1d4ed8;color:#fff;font:600 11px/1.5 ui-monospace,SFMono-Regular,Menlo,monospace;padding:1px 6px;border-radius:4px;white-space:nowrap;max-width:60vw;overflow:hidden;text-overflow:ellipsis;pointer-events:none}' +
            '.veil{position:fixed;inset:0;cursor:crosshair;background:rgba(3,7,18,.18);pointer-events:auto}' +
            '.hint{position:fixed;top:10px;left:50%;transform:translateX(-50%);background:rgba(17,24,39,.94);color:#e5e7eb;border:1px solid #374151;border-radius:9999px;padding:6px 14px;font-size:12.5px;box-shadow:0 8px 24px rgba(0,0,0,.45);white-space:nowrap;pointer-events:none}' +
            '.hint b{color:#93c5fd;font-weight:600}' +
            '.badge{position:fixed;display:flex;align-items:center;gap:2px;background:#111827;border:1px solid #374151;border-radius:9999px;padding:3px;box-shadow:0 10px 28px rgba(0,0,0,.5);pointer-events:auto;animation:in .14s ease-out}' +
            '.badge button{border:0;cursor:pointer;font-size:12.5px;border-radius:9999px;padding:5px 12px;line-height:1.3}' +
            '.send{background:#2563eb;color:#fff;font-weight:600}.send:hover{background:#3b82f6}' +
            '.x{background:transparent;color:#9ca3af;padding:5px 9px!important}.x:hover{color:#f3f4f6;background:#1f2937}' +
            '.done{color:#86efac;font-size:12.5px;font-weight:600;padding:5px 12px}' +
            '[hidden]{display:none!important}' +
            '@keyframes in{from{opacity:0;transform:translateY(-3px)}to{opacity:1;transform:none}}' +
            '</style>' +
            '<div class="box" hidden></div><div class="label" hidden></div><div class="veil" hidden></div>' +
            '<div class="hint" hidden></div>' +
            '<div class="badge" hidden role="group" aria-label="Selection"><button class="send" type="button">↗ Send to chat</button>' +
            '<button class="x" type="button" title="Cancel" aria-label="Cancel">✕</button><span class="done" hidden>Sent ✓</span></div>';
        ['box', 'label', 'veil', 'hint', 'badge', 'send', 'x', 'done'].forEach(function (k) { els[k] = root.querySelector('.' + k); });
        els.send.addEventListener('click', send);
        els.x.addEventListener('click', function () { reset(true); });
        els.veil.addEventListener('mousedown', areaStart);
        document.documentElement.appendChild(host);
    }
    function show(el, on) { el.hidden = !on; }
    function place(rect, label) {
        // rect in viewport coordinates
        els.box.style.left = rect.left + 'px'; els.box.style.top = rect.top + 'px';
        els.box.style.width = Math.max(0, rect.width) + 'px'; els.box.style.height = Math.max(0, rect.height) + 'px';
        show(els.box, true);
        if (label) {
            els.label.textContent = label;
            show(els.label, true);
            var lh = els.label.offsetHeight || 18;
            var top = rect.top - lh - 4 >= 0 ? rect.top - lh - 4 : rect.top + rect.height + 4;
            els.label.style.left = Math.max(4, Math.min(rect.left, window.innerWidth - els.label.offsetWidth - 4)) + 'px';
            els.label.style.top = Math.max(4, Math.min(top, window.innerHeight - lh - 4)) + 'px';
        } else show(els.label, false);
    }
    function hint(html) {
        if (html) { els.hint.innerHTML = html; show(els.hint, true); } else show(els.hint, false);
    }
    function sizeOf(r) { return Math.round(r.width) + '×' + Math.round(r.height); }

    // The chosen element or area stays outlined and follows scrolling; the badge sits beside it
    function viewRect() {
        if (!chosen) return null;
        if (chosen.el) return chosen.el.isConnected ? chosen.el.getBoundingClientRect() : null;
        var a = chosen.area;
        return { left: a.x - window.scrollX, top: a.y - window.scrollY, width: a.w, height: a.h };
    }
    function follow() {
        cancelAnimationFrame(frame);
        if (mode !== 'chosen') return;
        var r = viewRect();
        if (!r) { reset(true); return; }
        place(r, chosen.label);
        var bw = els.badge.offsetWidth || 180, bh = els.badge.offsetHeight || 34, gap = 8;
        var top = r.top + r.height + gap;
        if (top + bh > window.innerHeight - 6) top = r.top - bh - gap;
        if (top < 6) top = Math.min(Math.max(6, r.top + 6), window.innerHeight - bh - 6);
        var left = Math.max(6, Math.min(r.left + r.width - bw, window.innerWidth - bw - 6));
        els.badge.style.left = left + 'px'; els.badge.style.top = top + 'px';
        frame = requestAnimationFrame(follow);
    }
    function choose(target) {
        ui();
        chosen = target;
        mode = 'chosen';
        show(els.veil, false); hint('');
        els.box.classList.add('on');
        show(els.badge, true); show(els.send, true); show(els.x, true); show(els.done, false);
        follow();
        post({ type: 'mode', mode: 'chosen' });
        setTimeout(function () { try { els.send.focus(); } catch (e) { /* ignore */ } }, 0);
    }
    function reset(tell) {
        cancelAnimationFrame(frame);
        var was = mode;
        mode = null; hoverEl = null; chosen = null; drag = null;
        if (host) {
            ['box', 'label', 'veil', 'hint', 'badge'].forEach(function (k) { show(els[k], false); });
            els.box.classList.remove('on');
        }
        if (tell && was) post({ type: 'cancelled' });
    }
    function send() {
        if (mode !== 'chosen' || !chosen) return;
        var payload = chosen.el ? describe(chosen.el) : inArea(chosen.area);
        post({ type: 'selected', payload: payload });
        show(els.send, false); show(els.x, false); show(els.done, true);
        setTimeout(function () { if (mode === 'chosen') reset(false); }, 900);
    }

    // ---- element mode: hover outlines, click chooses
    function start(m) {
        reset(false);
        ui();
        mode = m;
        if (m === 'element') {
            hint('<b>Click</b> a title, text, image or block to select it · <b>Esc</b> to cancel');
        } else {
            hint('<b>Drag</b> over the part of the page to select · <b>Esc</b> to cancel');
            show(els.veil, true);
        }
        post({ type: 'mode', mode: m });
    }
    function pickable(el) {
        return el && el.nodeType === 1 && el !== host && el !== document.documentElement && el.tagName !== 'BODY';
    }
    window.addEventListener('mousemove', function (e) {
        if (mode !== 'element') return;
        var el = e.target;
        if (!pickable(el) || el === hoverEl) return;
        hoverEl = el;
        var r = el.getBoundingClientRect();
        place(r, selectorLabel(el) + '  ' + sizeOf(r));
    }, true);
    function selectorLabel(el) {
        var s = el.tagName.toLowerCase();
        if (el.id) return s + '#' + el.id;
        var c = Array.prototype.slice.call(el.classList, 0, 2);
        return c.length ? s + '.' + c.join('.') : s;
    }
    ['mousedown', 'mouseup', 'pointerdown', 'pointerup', 'click', 'dblclick', 'auxclick'].forEach(function (type) {
        window.addEventListener(type, function (e) {
            if (mode !== 'element') return;
            if (e.target === host) return;
            e.preventDefault(); e.stopPropagation(); e.stopImmediatePropagation();
            if (type === 'click' && pickable(e.target)) {
                var el = e.target;
                choose({ el: el, label: selectorLabel(el) });
            }
        }, true);
    });

    // ---- area mode: drag a rectangle on the veil
    var drag = null;
    function areaStart(e) {
        if (mode !== 'area' || e.button !== 0) return;
        e.preventDefault();
        drag = { x: e.clientX, y: e.clientY };
        hint('');
        window.addEventListener('mousemove', areaMove, true);
        window.addEventListener('mouseup', areaEnd, true);
    }
    function dragRect(e) {
        var x = Math.max(0, Math.min(drag.x, e.clientX)), y = Math.max(0, Math.min(drag.y, e.clientY));
        return { left: x, top: y, width: Math.min(window.innerWidth, Math.max(drag.x, e.clientX)) - x,
                 height: Math.min(window.innerHeight, Math.max(drag.y, e.clientY)) - y };
    }
    function areaMove(e) {
        if (!drag) return;
        var r = dragRect(e);
        place(r, sizeOf(r));
    }
    function areaEnd(e) {
        window.removeEventListener('mousemove', areaMove, true);
        window.removeEventListener('mouseup', areaEnd, true);
        if (!drag) return;
        var r = dragRect(e);
        drag = null;
        if (r.width < MIN_AREA || r.height < MIN_AREA) {    // a click, not a drag: try again
            show(els.box, false); show(els.label, false);
            hint('<b>Drag</b> over the part of the page to select · <b>Esc</b> to cancel');
            return;
        }
        var area = { x: Math.round(r.left + window.scrollX), y: Math.round(r.top + window.scrollY),
                     w: Math.round(r.width), h: Math.round(r.height) };
        choose({ area: area, label: 'Area ' + area.w + '×' + area.h });
    }

    // ---- matching CSS selectors (for "Open the CSS")
    var STATE = /::?(hover|focus-visible|focus-within|focus|active|visited|link|target|before|after|placeholder|selection|marker|first-line|first-letter|backdrop|file-selector-button)(?![\w-])/gi;
    function splitSelectors(s) {
        var out = [], depth = 0, cur = '';
        for (var i = 0; i < s.length; i++) {
            var ch = s[i];
            if (ch === '(' || ch === '[') depth++;
            else if (ch === ')' || ch === ']') depth = Math.max(0, depth - 1);
            if (ch === ',' && !depth) { out.push(cur.trim()); cur = ''; } else cur += ch;
        }
        if (cur.trim()) out.push(cur.trim());
        return out;
    }
    function matchSelectors(el, selectors) {
        var list = splitSelectors(selectors);
        for (var i = 0; i < list.length; i++) {
            var sel = list[i], plain = sel.replace(STATE, '').trim();
            if (!plain || /[>+~]$/.test(plain)) continue;
            try { if (el.matches(plain)) return { selector: sel, state: plain !== sel }; } catch (e) { /* a selector this browser can't read */ }
        }
        return null;
    }

    // ---- the menu, the keyboard and the Studio's commands
    window.addEventListener('contextmenu', function (e) {
        if (e.shiftKey) return;                       // Shift + right-click: the browser's own menu
        e.preventDefault(); e.stopPropagation();
        if (mode === 'element' || mode === 'area') { reset(true); return; }
        var el = document.elementFromPoint(e.clientX, e.clientY);
        post({ type: 'menu', x: e.clientX, y: e.clientY, what: pickable(el) ? short(el) : '' });
    }, true);
    window.addEventListener('mousedown', function () { post({ type: 'dismiss' }); }, true);
    window.addEventListener('keydown', function (e) {
        if (e.key === 'Escape') {
            if (mode) { e.preventDefault(); e.stopPropagation(); reset(true); }
            post({ type: 'dismiss' });
        }
    }, true);
    window.addEventListener('message', function (e) {
        if (e.source !== window.parent || !e.data || e.data.optimux !== 'pick-cmd') return;
        var d = e.data;
        if (d.cmd === 'element' || d.cmd === 'area') start(d.cmd);
        else if (d.cmd === 'cancel') reset(true);
        else if (d.cmd === 'match-css') {
            // "Open the CSS": which of the page's rules apply to the element (states like
            // :hover and pseudo-elements like ::before count, marked as such)
            var el = document.elementFromPoint(Number(d.x) || 0, Number(d.y) || 0);
            if (!pickable(el)) return;
            var matches = [];
            (Array.isArray(d.rules) ? d.rules : []).slice(0, 5000).forEach(function (r) {
                var hit = matchSelectors(el, String(r.selectors || ''));
                if (hit) matches.push({ i: r.i, selector: hit.selector, state: hit.state });
            });
            post({ type: 'css', what: short(el), matches: matches });
        }
        else if (d.cmd === 'locate') {
            // "Open the HTML" / "Open the script": what the Studio needs to find the element's code
            var t = document.elementFromPoint(Number(d.x) || 0, Number(d.y) || 0);
            if (!pickable(t)) return;
            var attrs = {};
            Array.prototype.forEach.call(t.attributes, function (a) { if (a.value.length < 300) attrs[a.name] = a.value; });
            post({ type: 'locate', want: String(d.want || 'html'), what: short(t), payload: {
                page: page(), tag: t.tagName.toLowerCase(), id: t.id || '', attrs: attrs,
                classes: Array.prototype.slice.call(t.classList, 0, 12),
                text: clean(t.innerText || t.getAttribute('alt') || '', 200),
                index: Array.prototype.indexOf.call(document.getElementsByTagName(t.tagName), t) } });
        }
        else if (d.cmd === 'at') {
            var el = document.elementFromPoint(Number(d.x) || 0, Number(d.y) || 0);
            if (pickable(el)) { reset(false); choose({ el: el, label: selectorLabel(el) }); }
        }
    });
})();
