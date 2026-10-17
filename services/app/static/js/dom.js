// Tiny DOM helper. Text always goes through textContent, never innerHTML.
export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v == null || v === false) continue;
    if (k === 'class') el.className = v;
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (v === true) el.setAttribute(k, '');
    else el.setAttribute(k, v);
  }
  for (const c of children.flat(Infinity)) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

export const clear = (el) => { el.replaceChildren(); return el; };
export const $ = (sel, root = document) => root.querySelector(sel);
export const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

/** headers: string or [label, className]; rows: arrays of cells (strings or Nodes). */
export function table(headers, rows, opts = {}) {
  const cls = (i) => (Array.isArray(headers[i]) ? headers[i][1] : '');
  const head = h('tr', null, headers.map((x, i) => h('th', { class: cls(i), scope: 'col' }, Array.isArray(x) ? x[0] : x)));
  const body = rows.map((r, ri) => h('tr', opts.rowAttrs ? opts.rowAttrs(ri) : null,
    r.map((c, i) => h('td', { class: cls(i) }, c))));
  return h('div', { class: 'table-wrap' },
    h('table', { class: opts.dense ? 'dense' : '' },
      opts.caption ? h('caption', { class: 'sr-only' }, opts.caption) : null,
      h('thead', null, head), h('tbody', null, body)));
}
