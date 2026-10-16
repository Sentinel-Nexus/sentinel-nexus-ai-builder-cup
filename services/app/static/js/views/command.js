import { get } from '../api.js';
import { $$, h, table } from '../dom.js';
import { empty, errorState, fmtPct, laneChip, scenarioChip, sevChip, statusChip } from '../ui.js';

export const title = 'Command centre';

function kpi(label, value, sub, href) {
  const body = [h('div', { class: 'label' }, label), h('div', { class: 'value' }, value), sub ? h('div', { class: 'sub' }, sub) : null];
  return href
    ? h('a', { class: 'card kpi link', href }, body)
    : h('div', { class: 'card kpi' }, body);
}

function versus(detector, baseline) {
  return h('div', { class: 'vs' }, h('span', { class: 'value' }, fmtPct(detector)), h('span', { class: 'b' }, `flat rule ${fmtPct(baseline)}`));
}

export async function render(root, { keys }) {
  const page = h('div', { class: 'stack' });
  root.append(
    h('div', { class: 'page-head' },
      h('div', { class: 'grow' }, h('h1', null, 'Command centre'),
        h('p', null, 'Detector alerts, what the agents concluded, and what still needs an analyst.')),
      h('div', { class: 'row' },
        h('button', { class: 'btn', type: 'button', disabled: true, 'aria-describedby': 'replay-note' }, 'Replay scenario'),
        h('span', { id: 'replay-note', class: 'small muted' }, 'Disabled: streaming ships in the next phase.'))),
    page);

  let summary;
  let queue;
  try {
    [summary, queue] = await Promise.all([get('/summary', { fresh: true }), get('/queue', { fresh: true })]);
  } catch (e) {
    page.append(errorState(e, () => location.reload()));
    return;
  }

  const d = summary.detector;
  const b = summary.baseline;
  page.append(h('section', { class: 'grid kpis', 'aria-label': 'Key figures' },
    kpi('Open alerts', summary.open, `${summary.alerts} alerts, ${summary.decided} decided by an analyst`),
    kpi('Escalated', summary.lanes.escalated, 'agent escalated, or analyst override', '#/lanes'),
    kpi('Agent-cleared', summary.lanes.agent_cleared, 'reason and evidence on file', '#/lanes'),
    kpi('Needs analyst', summary.lanes.needs_analyst, 'agent was uncertain', '#/lanes'),
    d ? h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Detector precision'), versus(d.precision, b.precision),
      h('div', { class: 'sub' }, 'vs flat funding rule, seed 42')) : null,
    d ? h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Detector recall'), versus(d.recall, b.recall),
      h('div', { class: 'sub' }, `${d.tp} of 39 attackers; synthetic data`)) : null));

  const body = queue.length === 0
    ? empty('No alerts', 'The detector has not produced any alert.')
    : table(
      ['Severity', 'Alert', 'Ring', 'Scenario hint', ['Score', 'num'], ['Members', 'num'], 'Detector', 'Agent lane'],
      queue.map((a) => [sevChip(a.severity), h('a', { href: `#/case/${encodeURIComponent(a.alert_id)}`, class: 'mono' }, a.alert_id),
        h('span', { class: 'mono' }, a.ring_id), scenarioChip(a.scenario_hint), a.score.toFixed(1), a.n_members,
        statusChip(a.status), a.analyst_decision ? h('span', null, laneChip(a.lane), ' ', h('span', { class: 'chip t-neutral' }, a.analyst_decision)) : laneChip(a.lane)]),
      { caption: 'Alert queue', rowAttrs: (i) => ({ class: 'row-link', 'data-id': queue[i].alert_id, tabindex: 0 }) });

  page.append(h('section', { class: 'card', 'aria-label': 'Alert queue' },
    h('header', null, h('h2', null, 'Alert queue'),
      h('span', { class: 'small muted' }, `Severity is a display rule on the detector score (critical 60+, high 50+); threshold ${queue[0]?.threshold ?? ''}`)),
    body));

  const rows = $$('tbody tr', page);
  let sel = -1;
  const open = (r) => { location.hash = `#/case/${encodeURIComponent(r.dataset.id)}`; };
  const select = (i) => {
    if (!rows.length) return;
    sel = Math.max(0, Math.min(rows.length - 1, i));
    rows.forEach((r, k) => r.setAttribute('aria-selected', String(k === sel)));
    rows[sel].focus();
    rows[sel].scrollIntoView({ block: 'nearest' });
  };
  rows.forEach((r, i) => {
    r.addEventListener('click', (e) => { if (e.target.tagName !== 'A') open(r); });
    r.addEventListener('focus', () => { sel = i; rows.forEach((x, k) => x.setAttribute('aria-selected', String(k === i))); });
    r.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(r); });
  });
  keys.set({ j: () => select(sel + 1), k: () => select(sel - 1) });
}
