// Overview: what the app is and what it achieves, in plain words. Reads one static JSON file
// (static/data/overview.json) and makes no BigQuery or Gemini call. All data is synthetic.
import { get } from '../api.js';
import { h } from '../dom.js';
import { errorState, fmtN } from '../ui.js';

export const title = 'Overview';

const C = { blue: '#4285F4', green: '#34A853', yellow: '#FBBC04', orange: '#FF7A00', red: '#EA4335', purple: '#A142F4' };
const reduced = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
const svgNS = 'http://www.w3.org/2000/svg';

function svg(viewBox, inner, cls) {
  const el = document.createElementNS(svgNS, 'svg');
  el.setAttribute('viewBox', viewBox);
  el.setAttribute('aria-hidden', 'true');
  if (cls) el.setAttribute('class', cls);
  el.innerHTML = inner; // static markup defined in this file, never data
  return el;
}

/** Generic four-point sparkle. */
const sparkle = () => svg('0 0 40 40', `<defs><linearGradient id="ovg" x1="0" x2="1"><stop offset="0" stop-color="${C.blue}"/><stop offset=".5" stop-color="${C.yellow}"/><stop offset="1" stop-color="${C.purple}"/></linearGradient></defs><path d="M20 1C21.5 12 28 18.5 39 20 28 21.5 21.5 28 20 39 18.5 28 12 21.5 1 20 12 18.5 18.5 12 20 1Z" fill="url(#ovg)"/>`, 'ov-spark');

const ICONS = {
  trades: `<path d="M4 26l6-8 5 4 6-9 7 5" fill="none" stroke="${C.blue}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/><path d="M4 30h24" stroke="#c9ced6" stroke-width="2"/>`,
  map: `<g stroke="${C.green}" stroke-width="2"><path d="M8 8l16 6M8 8l6 16M24 14l-10 10"/></g><g fill="#000" stroke="#fff" stroke-width="2"><circle cx="8" cy="8" r="4"/><circle cx="24" cy="14" r="4"/><circle cx="14" cy="24" r="4"/></g>`,
  ai: `<circle cx="14" cy="14" r="8" fill="none" stroke="${C.yellow}" stroke-width="2.5"/><path d="M20 20l8 8" stroke="${C.yellow}" stroke-width="3" stroke-linecap="round"/><path d="M10 14l3 3 5-6" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`,
  analyst: `<circle cx="16" cy="10" r="5" fill="none" stroke="${C.purple}" stroke-width="2.5"/><path d="M6 28c0-6 4-9 10-9s10 3 10 9" fill="none" stroke="${C.purple}" stroke-width="2.5" stroke-linecap="round"/>`,
};

// One popover open at a time. The panel is position: fixed, placed from its trigger's box, flipped
// and clamped to stay inside the viewport; the block that holds it is raised (.ov-open) above its
// siblings so no later card paints over it.
let openPop = null;

function placePop(btn, panel) {
  const m = 8;
  const r = btn.getBoundingClientRect();
  const vw = document.documentElement.clientWidth;
  const vh = window.innerHeight;
  panel.style.left = '0px';
  panel.style.top = '0px';
  const w = panel.offsetWidth;
  const hgt = panel.offsetHeight;
  let left = r.left; // left-aligned under the trigger ...
  if (left + w > vw - m) left = r.right - w; // ... or right-aligned to it near the right edge
  left = Math.max(m, Math.min(left, vw - w - m));
  let top = r.bottom + 6;
  if (top + hgt > vh - m && r.top - 6 - hgt >= m) top = r.top - 6 - hgt; // open upwards near the bottom
  top = Math.max(m, Math.min(top, vh - hgt - m));
  panel.style.left = `${Math.round(left)}px`;
  panel.style.top = `${Math.round(top)}px`;
}

function closePop(focusTrigger = false) {
  if (!openPop) return;
  const { btn, panel, block, home } = openPop;
  panel.hidden = true;
  if (home) home.append(panel);
  btn.setAttribute('aria-expanded', 'false');
  if (block) block.classList.remove('ov-open');
  openPop = null;
  if (focusTrigger) btn.focus();
}

function pop(text, label, { wide = false } = {}) {
  const glossary = label.startsWith('Glossary');
  const panel = h('div', { class: `ov-pop${wide ? ' ov-pop-wide' : ''}`, role: 'region', 'aria-label': label, hidden: true }, text);
  const btn = h('button', { type: 'button', class: glossary ? 'ov-q' : 'ov-i', 'aria-expanded': 'false', 'aria-label': label }, glossary ? '?' : 'i');
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const wasMine = openPop && openPop.btn === btn;
    closePop();
    if (wasMine) return;
    const block = btn.closest('.ov-fade') || btn.closest('.ov-card');
    if (block) block.classList.add('ov-open');
    const home = panel.parentNode;
    document.body.append(panel); // out of every card and animation layer: nothing can paint over it
    panel.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    openPop = { btn, panel, block, home };
    placePop(btn, panel);
  });
  panel.addEventListener('click', (e) => e.stopPropagation());
  return { btn, panel };
}

function countUp(el, target, fmt = fmtN) {
  if (reduced() || !Number.isFinite(target)) { el.textContent = fmt(target); return; }
  const t0 = performance.now();
  const step = (t) => {
    const k = Math.min(1, (t - t0) / 700);
    el.textContent = fmt(target * (1 - (1 - k) ** 3));
    if (k < 1) requestAnimationFrame(step); else el.textContent = fmt(target);
  };
  requestAnimationFrame(step);
}

function kpi({ label, big, unit, text, how, fmt, delay }) {
  const num = h('span', null, '0');
  const p = pop(how, `How this is measured: ${label}`);
  const card = h('div', { class: 'ov-card ov-kpi ov-fade', style: `animation-delay:${delay}ms` },
    h('div', { class: 'ov-label' }, h('span', null, label), p.btn),
    h('div', { class: 'ov-big' }, num, unit ? h('small', null, ` ${unit}`) : null),
    h('p', null, text), p.panel);
  countUp(num, big, fmt);
  return card;
}

const usd = (v) => `$${fmtN(Math.round(v))}`;

// ---- charts ----

function hatch(color) {
  const c = document.createElement('canvas');
  c.width = 10; c.height = 10;
  const x = c.getContext('2d');
  x.fillStyle = '#1d2025'; x.fillRect(0, 0, 10, 10);
  x.strokeStyle = color; x.lineWidth = 2; x.beginPath(); x.moveTo(0, 10); x.lineTo(10, 0); x.stroke();
  return x.createPattern(c, 'repeat');
}

/** Prints each bar's value at its end, so values never depend on colour or hover. */
const valueLabels = (fmt) => ({
  id: 'ovLabels',
  afterDatasetsDraw(chart) {
    const { ctx } = chart;
    ctx.save();
    ctx.font = '600 12px system-ui, sans-serif';
    ctx.fillStyle = window.Chart.defaults.color;
    ctx.textBaseline = 'middle';
    chart.data.datasets.forEach((ds, i) => {
      if (chart.getDatasetMeta(i).hidden || ds.noLabels) return;
      chart.getDatasetMeta(i).data.forEach((bar, j) => {
        const v = ds.data[j];
        if (v == null) return;
        const horizontal = chart.options.indexAxis === 'y';
        if (horizontal) { ctx.textAlign = 'left'; ctx.fillText(fmt(v), bar.x + 6, bar.y); } else { ctx.textAlign = 'center'; ctx.fillText(fmt(v), bar.x, bar.y - 9); }
      });
    });
    ctx.restore();
  },
});

function chartCard({ title: t, note, label, build, table, wide }) {
  const canvas = h('canvas', { role: 'img', 'aria-label': label });
  const box = h('div', { class: 'ov-box' }, canvas);
  const card = h('section', { class: `ov-card ov-chart ov-fade${wide ? ' ov-wide' : ''}`, 'aria-label': t },
    h('h3', null, t), h('p', { class: 'ov-note' }, note), box,
    h('details', { class: 'ov-table-alt' }, h('summary', null, 'Show as a table'),
      h('table', null, h('thead', null, h('tr', null, table.head.map((x) => h('th', { scope: 'col' }, x)))),
        h('tbody', null, table.rows.map((r) => h('tr', null, r.map((c) => h('td', null, c))))))));
  let inst = null;
  const draw = () => {
    if (!window.Chart) { box.replaceChildren(h('p', { class: 'ov-note' }, 'The chart library did not load; use the table below.')); return; }
    if (inst) inst.destroy();
    inst = new window.Chart(canvas, build());
  };
  return { card, draw, destroy: () => inst && inst.destroy() };
}

const grid = '#2e3238';
const baseOpts = (extra = {}) => ({
  maintainAspectRatio: false,
  animation: reduced() ? false : { duration: 600 },
  plugins: { legend: { position: 'bottom', labels: { boxWidth: 14, color: window.Chart.defaults.color } } },
  ...extra,
});
const pctAxis = () => ({ y: { min: 0, max: 115, grid: { color: grid }, ticks: { callback: (v) => (v <= 100 ? `${v}%` : '') } }, x: { grid: { display: false } } });

function charts(d) {
  const L = d.catch_by_type.map((r) => r.label);
  const out = [];
  out.push(chartCard({
    title: 'The simple rule misses half the attackers',
    note: 'Share of planted manipulators caught, by type. Hatched bars: simple rule. Solid bars: MarketSentinel.',
    label: 'Grouped bars: share of manipulators caught by type, simple rule versus MarketSentinel',
    table: { head: ['Type', 'Simple rule caught', 'MarketSentinel caught'], rows: d.catch_by_type.map((r) => [r.label, `${r.simple_rule}%`, `${r.marketsentinel}%`]) },
    build: () => ({ type: 'bar', plugins: [valueLabels((v) => `${v}%`)],
      data: { labels: L, datasets: [
        { label: 'Simple rule', data: d.catch_by_type.map((r) => r.simple_rule), backgroundColor: hatch(C.orange), borderColor: C.orange, borderWidth: 2 },
        { label: 'MarketSentinel', data: d.catch_by_type.map((r) => r.marketsentinel), backgroundColor: C.blue } ] },
      options: baseOpts({ scales: pctAxis() }) }),
  }));
  out.push(chartCard({
    title: '...and raises false alarms',
    note: 'Innocent accounts wrongly flagged, by group. Hatched: simple rule. Solid: MarketSentinel.',
    label: 'Grouped bars: innocent accounts wrongly flagged by group, simple rule versus MarketSentinel',
    table: { head: ['Group', 'Accounts', 'Simple rule flagged', 'MarketSentinel flagged'], rows: d.false_alarms.map((r) => [r.label, fmtN(r.accounts), r.simple_rule, r.marketsentinel]) },
    build: () => ({ type: 'bar', plugins: [valueLabels((v) => String(v))],
      data: { labels: d.false_alarms.map((r) => r.label), datasets: [
        { label: 'Simple rule', data: d.false_alarms.map((r) => r.simple_rule), backgroundColor: hatch(C.red), borderColor: C.red, borderWidth: 2 },
        { label: 'MarketSentinel', data: d.false_alarms.map((r) => r.marketsentinel), backgroundColor: C.green } ] },
      options: baseOpts({ scales: { y: { min: 0, suggestedMax: 10, grid: { color: grid }, ticks: { precision: 0 } }, x: { grid: { display: false } } } }) }),
  }));
  out.push(chartCard({
    title: 'Most manipulators found are funded from shared sources',
    note: 'Accounts found by type (the doughnut shows counts and names, not just colours).',
    label: 'Doughnut: accounts found by type of manipulation',
    table: { head: ['Type', 'Accounts found'], rows: d.types_found.map((r) => [r.label, r.accounts]) },
    build: () => ({ type: 'doughnut',
      data: { labels: d.types_found.map((r) => `${r.label}: ${r.accounts}`), datasets: [{ data: d.types_found.map((r) => r.accounts), backgroundColor: [C.blue, C.yellow, C.purple], borderColor: '#000', borderWidth: 3 }] },
      options: baseOpts({ cutout: '58%' }) }),
  }));
  out.push(chartCard({
    title: 'Suspicious trading volume by type (USD, synthetic)',
    note: 'Total traded by the flagged accounts, by type of manipulation.',
    label: 'Bars: suspicious trading volume in US dollars by type',
    table: { head: ['Type', 'USD traded'], rows: d.volume_by_type.map((r) => [r.label, usd(r.usd)]) },
    build: () => ({ type: 'bar', plugins: [valueLabels(usd)],
      data: { labels: d.volume_by_type.map((r) => r.label), datasets: [{ label: 'USD traded', data: d.volume_by_type.map((r) => r.usd), backgroundColor: [C.blue, C.yellow, C.purple] }] },
      options: baseOpts({ indexAxis: 'y', layout: { padding: { right: 70 } }, plugins: { legend: { display: false } }, scales: { x: { grid: { color: grid }, ticks: { callback: (v) => `$${fmtN(v / 1000)}k` } }, y: { grid: { display: false } } } }) }),
  }));
  const cases = d.speed.map((c) => ({ ...c, label: c.alert_id.length > 14 ? `${c.alert_id.slice(0, 14)}…` : c.alert_id }));
  out.push(chartCard({
    wide: false,
    title: `A case report takes about ${d.kpi.report_time.median_s} seconds, a stored one opens instantly`,
    note: `Seconds to produce each case report in recorded runs (median ${d.kpi.report_time.median_s} s). A stored case opens in ${d.kpi.report_time.cached_read_ms} ms on the server, too small to draw.`,
    label: 'Bars: seconds to produce a case report for each case',
    table: { head: ['Case', 'Seconds', 'Kind'], rows: d.speed.map((c) => [c.alert_id, c.seconds, c.injected ? 'test case' : 'detector alert']) },
    build: () => ({ type: 'bar', plugins: [valueLabels((v) => `${v} s`)],
      data: { labels: cases.map((c) => (c.injected ? `${c.label} (test)` : c.label)), datasets: [{ label: 'Seconds', data: cases.map((c) => c.seconds), backgroundColor: cases.map((c) => (c.injected ? hatch(C.orange) : C.orange)), borderColor: C.orange, borderWidth: 1 }] },
      options: baseOpts({ indexAxis: 'y', layout: { padding: { right: 40 } }, plugins: { legend: { display: false } }, scales: { x: { min: 0, grid: { color: grid }, title: { display: true, text: 'seconds', color: window.Chart.defaults.color } }, y: { grid: { display: false } } } }) }),
  }));
  out.push(chartCard({
    title: 'Every statement is checked; unsupported ones are removed',
    note: 'Statements kept (linked to data) and removed by the safety check, per case. Solid: kept. Hatched: removed.',
    label: 'Stacked bars: statements kept versus removed by the safety check, per case',
    table: { head: ['Case', 'Kept', 'Removed'], rows: d.speed.map((c) => [c.alert_id, c.kept, c.removed]) },
    build: () => ({ type: 'bar',
      data: { labels: cases.map((c) => (c.injected ? `${c.label} (test)` : c.label)), datasets: [
        { label: 'Kept (linked to data)', data: cases.map((c) => c.kept), backgroundColor: C.green },
        { label: 'Removed (listed, not used)', data: cases.map((c) => c.removed), backgroundColor: hatch(C.red), borderColor: C.red, borderWidth: 2 } ] },
      options: baseOpts({ indexAxis: 'y', scales: { x: { stacked: true, grid: { color: grid }, ticks: { precision: 0 } }, y: { stacked: true, grid: { display: false } } } }) }),
  }));
  return out;
}

function funnel(d) {
  const colors = [C.blue, C.green, C.yellow, C.orange, C.red, C.purple];
  const max = Math.log10(Math.max(...d.funnel.map((f) => f.value)) + 1);
  return h('section', { class: 'ov-card ov-chart ov-fade ov-wide', 'aria-label': 'From thousands of trades to a few decisions' },
    h('h3', null, 'From thousands of trades to a few decisions'),
    h('p', { class: 'ov-note' }, 'Bar length uses a log scale so the small numbers stay visible; the real count is on every bar.'),
    h('div', { class: 'ov-funnel' }, d.funnel.map((f, i) => {
      const w = Math.max(6, (Math.log10(f.value + 1) / max) * 100);
      return h('div', { class: 'ov-fun-row' }, h('span', { class: 'ov-fun-name' }, f.label),
        h('div', { class: `ov-fun-bar${f.value === 0 ? ' ov-zero' : ''}`, style: `width:${w}%;background:${colors[i]}` },
          f.value === 0 ? '0 so far: waiting for an analyst' : fmtN(f.value)));
    })));
}

export async function render(root) {
  let link = document.getElementById('ov-css');
  if (!link) {
    link = h('link', { id: 'ov-css', rel: 'stylesheet', href: '/static/css/overview.css' });
    document.head.append(link);
  }
  document.body.classList.add('ov-on');
  let d;
  try { d = await get('/static/data/overview.json'); } catch (e) { root.append(errorState(e, () => location.reload())); return () => document.body.classList.remove('ov-on'); }
  const k = d.kpi;
  let streamSecs = null;
  try { streamSecs = (await get('/static/data/stream.json')).seconds; } catch { /* optional */ }
  const gloss = pop('Hatched or solid bars compare two ways of finding manipulation. "Simple rule" flags any group sharing one funding source. "Caught" is the share of planted manipulators that were flagged.', 'Glossary: simple rule and caught', { wide: true });

  const hero = h('header', { class: 'ov-hero ov-fade' }, h('div', { class: 'ov-hero-main' }, sparkle(),
    h('h1', null, 'MarketSentinel helps prediction-market operators catch market manipulation, explain it in plain language, and leave the final call to a human analyst.'),
    h('p', { class: 'ov-lede' }, 'All data on this page is synthetic: generated trades with planted manipulation, so the answers are known.'),
    h('div', { class: 'ov-cta' },
      h('a', { class: 'ov-btn ov-primary', href: d.top_alert ? `#/case/${encodeURIComponent(d.top_alert)}` : '#/' }, 'Open a flagged case'),
      h('a', { class: 'ov-btn ov-ghost', href: '#/benchmark' }, 'See how we measured this'))),
    h('div', { class: 'ov-hero-side' }, h('ol', { class: 'ov-steps', 'aria-label': 'How it works' },
      [['trades', 'Trades arrive'], ['map', 'We map who is connected to whom'], ['ai', 'AI investigates and shows its evidence'], ['analyst', 'An analyst decides']]
        .map(([ic, t]) => h('li', null, svg('0 0 32 32', ICONS[ic]), h('strong', null, t)))),
    streamSecs != null ? h('p', { class: 'ov-stream-line', id: 'ov-stream-line' }, `A synthetic burst can be streamed through Pub/Sub; alerts appear within about ${streamSecs} seconds.`) : null));

  const kpis = h('section', { class: 'ov-kpis', 'aria-label': 'Key results' },
    kpi({ label: 'Hidden manipulators caught', big: k.caught.caught, unit: `of ${k.caught.total}`, delay: 0,
      text: 'Planted manipulators that were flagged. The simple rule caught 20 of 39.',
      how: `${k.caught.caught} of ${k.caught.total} planted manipulators flagged on a synthetic data set held back for testing; one slipped under the line by 0.01 points. From the Benchmark page.` }),
    kpi({ label: 'False alarms', big: k.false_alarms.marketsentinel, unit: 'innocent accounts flagged', delay: 80,
      text: `Out of 2,000 innocent accounts. The simple rule flagged ${k.false_alarms.simple_rule}.`,
      how: 'Counted over 23 look-alike innocent accounts (households, an exchange cluster, busy honest traders) plus normal accounts. From the Benchmark page.' }),
    kpi({ label: 'Suspicious trading surfaced', big: k.volume_usd, fmt: usd, delay: 160,
      text: 'US dollars (synthetic) traded by the flagged accounts.',
      how: 'Sum of every trade made by the 38 flagged accounts, from one capped BigQuery query when this page\'s data was built.' }),
    kpi({ label: 'Time to a case report', big: k.report_time.median_s, unit: 's', delay: 240, fmt: (v) => Number(v).toFixed(1),
      text: `Median of ${k.report_time.n} recorded runs. A stored case opens instantly (${k.report_time.cached_read_ms} ms).`,
      how: `Wall-clock time of the recorded investigations (range ${k.report_time.min_s} to ${k.report_time.max_s} s). The stored read is the server-side median over repeated reads, network not included.` }),
    kpi({ label: 'Evidence', big: k.evidence.kept, unit: 'statements checked', delay: 320,
      text: `Each links to data. ${k.evidence.removed} unsupported ones were removed and listed.`,
      how: `Across ${k.evidence.cases} stored case reports: statements kept with evidence links versus statements the safety check removed.` }));

  const control = h('section', { class: 'ov-two' },
    h('div', { class: 'ov-card ov-panel ov-fade' }, h('h2', null, 'Who stays in charge'),
      h('ul', null, h('li', null, 'The AI recommends.'), h('li', null, 'Analysts approve, reject or override, with a written reason.'), h('li', null, 'Every step is logged.')),
      h('a', { href: '#/governance' }, 'See the rules and limits')),
    h('div', { class: 'ov-card ov-panel ov-fade' }, h('h2', null, 'Honest limits'),
      h('ul', null, h('li', null, 'Synthetic data from one generator, so results show the method works on that world, not on real markets.'),
        h('li', null, 'On injected look-alike cases the AI agents were not consistent between runs. That is why alerts come from the detector and an analyst makes every final call.')),
      h('a', { href: '#/benchmark' }, 'Read the limits on the Benchmark page')));

  const cs = charts(d);
  const wrapper = h('div', { class: 'ov-page' }, h('div', { class: 'ov-wrap' },
    hero, h('div', { class: 'ov-fade' }, h('div', { style: 'display:flex;gap:8px;align-items:center;position:relative' }, gloss.btn, h('span', { class: 'ov-foot' }, 'Plain-word glossary'), gloss.panel)), kpis,
    h('section', { 'aria-label': 'Charts' }, h('h2', { class: 'ov-sec-title' }, 'What the numbers show'),
      h('p', { class: 'ov-sub' }, `Pre-computed from recorded runs, as of ${d.as_of}. Synthetic data.`),
      h('div', { class: 'ov-charts' }, funnel(d), cs.map((c) => c.card))),
    control,
    h('p', { class: 'ov-foot' }, 'Synthetic data only. No savings, return or hours-saved figures are claimed.')));
  root.append(wrapper);

  const drawAll = () => { if (window.Chart) { window.Chart.defaults.color = document.body.classList.contains('ov-print') ? '#000' : '#c9ced6'; window.Chart.defaults.font.family = 'system-ui, sans-serif'; } cs.forEach((c) => c.draw()); };
  if (window.Chart) drawAll(); else window.addEventListener('load', drawAll, { once: true });
  const onDoc = () => closePop();
  const onKey = (e) => { if (e.key === 'Escape' && openPop) { e.stopPropagation(); closePop(true); } };
  const onMove = () => { if (openPop) placePop(openPop.btn, openPop.panel); };
  const before = () => { document.body.classList.add('ov-print'); drawAll(); };
  const after = () => { document.body.classList.remove('ov-print'); drawAll(); };
  document.addEventListener('click', onDoc);
  document.addEventListener('keydown', onKey);
  window.addEventListener('scroll', onMove, { passive: true });
  window.addEventListener('resize', onMove);
  window.addEventListener('beforeprint', before);
  window.addEventListener('afterprint', after);
  return () => {
    document.body.classList.remove('ov-on', 'ov-print');
    document.removeEventListener('click', onDoc);
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('scroll', onMove);
    window.removeEventListener('resize', onMove);
    closePop();
    window.removeEventListener('beforeprint', before);
    window.removeEventListener('afterprint', after);
    cs.forEach((c) => c.destroy());
  };
}
