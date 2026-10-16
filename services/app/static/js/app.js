// Router, shell and keyboard shortcuts. Views live in ./views and return an optional cleanup.
import { $, $$, clear, h } from './dom.js';
import { closeDialog, dialog, errorState, loading, store } from './ui.js';

const VIEWS = {
  command: () => import('./views/command.js'),
  case: () => import('./views/case.js'),
  lanes: () => import('./views/lanes.js'),
  benchmark: () => import('./views/benchmark.js'),
  governance: () => import('./views/governance.js'),
};

let cleanup = null;
let keyMap = {};
let chord = null;
let renderToken = 0;

/** Views register their own shortcuts here; they are dropped when the view changes. */
export const keys = {
  set(map) { keyMap = map; },
};

function parse(hash) {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  if (!parts.length) return { name: 'command', nav: 'command', args: [] };
  if (parts[0] === 'case' && parts[1]) return { name: 'case', nav: 'command', args: parts.slice(1) };
  if (VIEWS[parts[0]]) return { name: parts[0], nav: parts[0], args: parts.slice(1) };
  return { name: 'command', nav: 'command', args: [], notFound: true };
}

async function route() {
  const token = ++renderToken;
  const root = $('#view');
  if (cleanup) { try { cleanup(); } catch { /* ignore */ } cleanup = null; }
  keyMap = {};
  const r = parse(location.hash);
  $$('[data-nav]').forEach((a) => {
    if (a.dataset.nav === r.nav) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
  clear(root).append(loading());
  try {
    const mod = await VIEWS[r.name]();
    if (token !== renderToken) return;
    clear(root);
    cleanup = (await mod.render(root, { args: r.args, keys })) || null;
    document.title = `${mod.title ? `${mod.title} · ` : ''}MarketSentinel Workbench`;
    if (r.notFound) root.prepend(h('div', { class: 'notice warn' }, 'That page does not exist; showing the command centre.'));
  } catch (e) {
    if (token === renderToken) clear(root).append(errorState(e, route));
  }
  window.scrollTo(0, 0);
}

function setTheme(t) {
  document.documentElement.dataset.theme = t;
  store('ms-theme', t);
  window.dispatchEvent(new CustomEvent('themechange'));
}
const toggleTheme = () => setTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');

export function showHelp() {
  const rows = [
    ['g c / g l / g b / g g', 'Go to Command centre, Lanes, Benchmark, Governance'],
    ['j / k, Enter', 'Command centre: move through the queue, open the case'],
    ['a / r / e', 'Case: approve, reject or escalate (asks for a reason)'],
    ['i', 'Case: investigate live (asks first)'],
    ['x / p', 'Case: export JSON / open the print view'],
    ['t', 'Toggle light and dark theme'],
    ['?', 'This help; Esc closes dialogs'],
  ];
  dialog('Keyboard shortcuts', h('table', { class: 'dense' }, h('tbody', null,
    rows.map(([k, v]) => h('tr', null, h('td', null, h('kbd', null, k)), h('td', null, v))))),
  h('button', { class: 'btn primary', type: 'submit' }, 'Close'));
}

function onKey(e) {
  const tag = (e.target.tagName || '').toLowerCase();
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  if (['input', 'textarea', 'select'].includes(tag) || e.target.isContentEditable) return;
  if ($('#dialog').open) { if (e.key === 'Escape') closeDialog(); return; }
  if (chord) {
    const dest = { c: '#/', l: '#/lanes', b: '#/benchmark', g: '#/governance' }[e.key];
    chord = null;
    if (dest) { location.hash = dest; e.preventDefault(); }
    return;
  }
  if (e.key === 'g') { chord = true; setTimeout(() => { chord = null; }, 1200); return; }
  if (e.key === '?') { showHelp(); e.preventDefault(); return; }
  if (e.key === 't') { toggleTheme(); return; }
  const fn = keyMap[e.key];
  if (fn) { fn(e); }
}

window.addEventListener('hashchange', route);
window.addEventListener('keydown', onKey);
$('#theme-btn').addEventListener('click', toggleTheme);
$('#help-btn').addEventListener('click', showHelp);
$('#dialog').addEventListener('click', (e) => { if (e.target.id === 'dialog') closeDialog(); });
route();
