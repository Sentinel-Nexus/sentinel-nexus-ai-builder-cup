import { h, clear } from './dom.js';

export const SEVERITY = {
  critical: ['t-crit', 'Critical'], high: ['t-high', 'High'], medium: ['t-med', 'Medium'], review: ['t-review', 'Review band'],
};
export const LANE = {
  escalated: ['t-crit', 'Escalated'], agent_cleared: ['t-ok', 'Agent-cleared'], needs_analyst: ['t-high', 'Needs analyst'],
};
export const SCENARIO = {
  wash_trading: 'Wash trading', coordinated_accumulation: 'Coordinated buying', mule_funding: 'Mule funding',
};

export const sevChip = (s) => h('span', { class: `chip dot ${(SEVERITY[s] || ['t-neutral'])[0]}` }, (SEVERITY[s] || [0, s])[1]);
export const laneChip = (l) => (l
  ? h('span', { class: `chip ${(LANE[l] || ['t-neutral'])[0]}` }, (LANE[l] || [0, l])[1])
  : h('span', { class: 'chip t-neutral' }, 'No case yet'));
export const scenarioChip = (s) => h('span', { class: 'chip t-neutral', title: 'Detector hint (a hypothesis, not a finding)' }, SCENARIO[s] || s || 'unclassified');
export const statusChip = (s) => h('span', { class: `chip ${s === 'needs_review' ? 't-review' : 't-med'}` }, s === 'needs_review' ? 'needs review' : 'flagged');

export function sourceBadge(cached, when) {
  return h('span', {
    class: `badge ${cached ? 'cached' : 'live'}`,
    title: cached ? `Stored case${when ? ` from ${when}` : ''}` : 'Produced by a live investigation just now',
  }, cached ? 'Cached' : 'Live');
}

export const fmtPct = (v) => (v == null ? 'n/a' : `${Number(v).toFixed(0)}%`);
export const fmtMs = (ms) => (ms == null ? '' : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);
export const fmtN = (n) => (n == null ? '' : Number(n).toLocaleString('en-US'));

export function fmtTime(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return String(ts);
  return `${d.toISOString().replace('T', ' ').slice(0, 19)} UTC`;
}

export function loading(lines = 4) {
  return h('div', { class: 'card body', role: 'status', 'aria-label': 'Loading' },
    Array.from({ length: lines }, (_, i) => h('div', { class: 'skeleton', style: `width:${90 - i * 12}%` })));
}

export const empty = (title, text) => h('div', { class: 'state' }, h('strong', null, title), text);

export function errorState(err, retry) {
  return h('div', { class: 'state error', role: 'alert' },
    h('strong', null, 'This could not be loaded'), err.message || String(err),
    retry ? h('div', { style: 'margin-top:8px' }, h('button', { class: 'btn small', type: 'button', onclick: retry }, 'Retry')) : null);
}

export function toast(message, kind = '') {
  const el = h('div', { class: `toast ${kind}` }, message);
  document.getElementById('toasts').append(el);
  setTimeout(() => el.remove(), kind === 'err' ? 9000 : 4500);
}

/** Open the shared native <dialog>. */
export function dialog(title, body, actions) {
  const dlg = document.getElementById('dialog');
  clear(dlg);
  dlg.append(h('form', { method: 'dialog' }, h('h2', { id: 'dialog-title' }, title), body,
    h('div', { class: 'row', style: 'justify-content:flex-end' }, actions)));
  dlg.showModal();
  return dlg;
}
export const closeDialog = () => {
  const d = document.getElementById('dialog');
  if (d.open) d.close();
};

export function download(name, text, type = 'application/json') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const a = h('a', { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();

export function store(key, value) {
  try {
    if (value === undefined) return localStorage.getItem(key);
    localStorage.setItem(key, value);
  } catch { /* storage may be blocked */ }
  return value;
}
