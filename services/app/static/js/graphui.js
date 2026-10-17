// Controls around the case graph: caption, legend, edge toggles, layouts, "Walk me through it",
// the text-alternative list view and the "Why this was flagged" bars. Plain words throughout.
import { h } from './dom.js';
import { caption, featureBars, listRows, storySteps } from './graphx.js';

const NS = 'http://www.w3.org/2000/svg';
function icon(inner) {
  const s = document.createElementNS(NS, 'svg');
  s.setAttribute('viewBox', '0 0 28 16');
  s.setAttribute('width', '28');
  s.setAttribute('height', '16');
  s.setAttribute('aria-hidden', 'true');
  s.innerHTML = inner; // static markup defined here
  return s;
}
const SHAPES = {
  account: '<circle cx="14" cy="8" r="6" fill="var(--g-account)"/>',
  device: '<path d="M14 1l7 7-7 7-7-7z" fill="var(--surface)" stroke="var(--g-device)" stroke-width="2.5"/>',
  funding: '<path d="M8 2h12l5 6-5 6H8L3 8z" fill="var(--surface)" stroke="var(--g-funding)" stroke-width="2.5"/>',
  devEdge: '<path d="M1 8h26" stroke="var(--g-device)" stroke-width="2.5"/>',
  fundEdge: '<path d="M1 8h26" stroke="var(--g-funding)" stroke-width="2.5" stroke-dasharray="6 4"/>',
  tradeEdge: '<path d="M1 8h26" stroke="var(--g-account)" stroke-width="3" stroke-dasharray="1.5 4" stroke-linecap="round"/>',
};

export function legend() {
  const item = (shape, text) => h('span', { class: 'gx-leg-item' }, icon(SHAPES[shape]), text);
  return h('div', { class: 'gx-legend', role: 'group', 'aria-label': 'Graph legend' },
    h('div', null, h('strong', null, 'Dots'), item('account', 'Account'), item('device', 'Shared device'), item('funding', 'Shared funding source')),
    h('div', null, h('strong', null, 'Lines'), item('devEdge', 'Shares a device'), item('fundEdge', 'Funded by the same source'), item('tradeEdge', 'Traded with each other')),
    h('div', { class: 'gx-leg-note' }, 'Bigger dots traded more money. A glow marks accounts the report cites. Dashed outline: linked but not flagged.'));
}

/** Caption, edge toggles, layout choice, "Walk me through it". Returns {node, story, destroy}. */
export function controls(g, alert, { onStoryChange } = {}) {
  const model = g.model;
  const cap = h('p', { class: 'gx-caption', id: 'gx-caption' }, caption(model, alert.scenario_hint));
  const box = (kind, text, shown) => {
    const input = h('input', { type: 'checkbox', checked: true, disabled: shown ? null : true });
    input.addEventListener('change', () => g.setEdgeVisible(kind, input.checked));
    return h('label', { class: 'gx-toggle' }, input, ` ${text}`);
  };
  const toggles = h('div', { class: 'gx-toggles', role: 'group', 'aria-label': 'Show or hide lines' },
    box('devices', 'Shares a device', model.edges.some((e) => e.type === 'UsesDevice')),
    box('funding', 'Funded by the same source', model.edges.some((e) => e.type === 'FundedBy')),
    box('trades', model.dense ? 'Traded with each other (one line per pair)' : 'Traded with each other', model.tradeEdges > 0));
  const layoutBtn = (name, text) => {
    const b = h('button', { type: 'button', class: 'btn small', 'aria-current': g.layout === name ? 'true' : null }, text);
    b.addEventListener('click', () => {
      g.setLayout(name);
      row.querySelectorAll('[data-layout]').forEach((x) => { if (x === b) x.setAttribute('aria-current', 'true'); else x.removeAttribute('aria-current'); });
    });
    b.dataset.layout = name;
    return b;
  };
  const walk = h('button', { type: 'button', class: 'btn small primary', id: 'gx-walk' }, 'Walk me through it');
  const row = h('div', { class: 'gx-row no-print' }, toggles,
    h('div', { class: 'gx-layouts', role: 'group', 'aria-label': 'Layout' }, h('span', { class: 'small muted' }, 'Layout '), layoutBtn('clusters', 'Clusters'), layoutBtn('circle', 'Circle')), walk);

  // ---- story ----
  const steps = storySteps(model);
  let idx = 0;
  let timer = null;
  const bar = h('div', { class: 'gx-story', hidden: true, role: 'region', 'aria-label': 'Walk-through', 'aria-live': 'polite' });
  const stepNo = h('strong', { class: 'gx-step-no' });
  const sentence = h('span', { class: 'gx-step-text' });
  const back = h('button', { type: 'button', class: 'btn small', id: 'gx-back' }, 'Back');
  const next = h('button', { type: 'button', class: 'btn small primary', id: 'gx-next' }, 'Next');
  const auto = h('button', { type: 'button', class: 'btn small', id: 'gx-auto' }, 'Autoplay');
  const exit = h('button', { type: 'button', class: 'btn small ghost', id: 'gx-exit' }, 'Exit (Esc)');
  bar.append(stepNo, sentence, h('span', { class: 'gx-story-btns' }, back, next, auto, exit));

  const stopAuto = () => { clearInterval(timer); timer = null; auto.textContent = 'Autoplay'; };
  function show(i) {
    idx = Math.max(0, Math.min(steps.length - 1, i));
    const s = steps[idx];
    stepNo.textContent = `Step ${idx + 1} of ${steps.length}: ${s.title}`;
    sentence.textContent = s.sentence;
    back.disabled = idx === 0;
    next.disabled = idx === steps.length - 1;
    g.setLayers(s.layers);
    g.fit();
    if (idx === steps.length - 1) stopAuto();
    onStoryChange && onStoryChange(idx);
  }
  function onEsc(e) { if (e.key === 'Escape' && !bar.hidden) leave(); }
  function enter() {
    bar.hidden = false;
    walk.disabled = true;
    toggles.classList.add('gx-locked');
    document.addEventListener('keydown', onEsc);
    show(0);
  }
  function leave() {
    stopAuto();
    bar.hidden = true;
    walk.disabled = false;
    toggles.classList.remove('gx-locked');
    document.removeEventListener('keydown', onEsc);
    g.setLayers(null);
    g.fit();
    onStoryChange && onStoryChange(null);
    walk.focus();
  }
  walk.addEventListener('click', enter);
  back.addEventListener('click', () => { stopAuto(); show(idx - 1); });
  next.addEventListener('click', () => { stopAuto(); show(idx + 1); });
  exit.addEventListener('click', leave);
  auto.addEventListener('click', () => {
    if (timer) { stopAuto(); return; }
    if (idx === steps.length - 1) show(0);
    auto.textContent = 'Pause';
    timer = setInterval(() => show(idx + 1), 3500);
  });

  return {
    node: h('div', { class: 'gx-top' }, cap, row, bar),
    destroy() { stopAuto(); document.removeEventListener('keydown', onEsc); },
  };
}

/** Collapsible text alternative: every account with what it shares and who it traded with. */
export function listView(model, onPick) {
  const rows = listRows(model);
  const head = ['Account', 'Role in the group', 'Shared device', 'Funding source', 'Traded with'];
  return h('details', { class: 'gx-list' }, h('summary', null, `List view (${rows.length} accounts)`),
    h('div', { class: 'table-wrap' }, h('table', { class: 'dense' },
      h('caption', { class: 'sr-only' }, 'Accounts in the group and their links'),
      h('thead', null, h('tr', null, head.map((x) => h('th', { scope: 'col' }, x)))),
      h('tbody', null, rows.map((r) => h('tr', null,
        h('td', null, h('button', { type: 'button', class: 'ev-chip', onclick: () => onPick(r.account) }, r.account)),
        h('td', null, r.role), h('td', null, r.devices || 'none'), h('td', null, r.funding || 'none'), h('td', null, r.tradesWith || 'none')))))));
}

/** "Why this was flagged": bars of the alert's top features with plain names and the value. */
export function whyFlagged(alert) {
  const bars = featureBars(alert.top_features, 4);
  return h('section', { class: 'card', 'aria-label': 'Why this was flagged' },
    h('header', null, h('h2', null, 'Why this was flagged')),
    h('div', { class: 'body' }, bars.length
      ? [h('p', { class: 'small muted' }, 'The strongest signals behind this alert. Longer bar, stronger signal.'),
        ...bars.map((b) => h('div', { class: 'gx-bar' },
          h('div', { class: 'gx-bar-label' }, h('span', null, b.name), h('span', { class: 'mono' }, b.value.toFixed(1))),
          h('div', { class: 'gx-bar-track', role: 'img', 'aria-label': `${b.name}: ${b.value.toFixed(1)}` }, h('div', { class: 'gx-bar-fill', style: `width:${Math.max(3, b.pct)}%` }))))]
      : h('p', { class: 'small muted' }, 'No signal breakdown is stored for this alert.')));
}
