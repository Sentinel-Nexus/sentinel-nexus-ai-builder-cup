import { get } from '../api.js';
import { $$, h, table } from '../dom.js';
import { empty, errorState, evalCasesOn, evalToggle, fmtPct, injectedChip, laneChip, scenarioChip, sevChip, statusChip } from '../ui.js';

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
        h('button', { class: 'btn', type: 'button', id: 'replay-btn', 'aria-describedby': 'replay-note' }, 'Replay a recorded investigation'),
        h('span', { id: 'replay-note', class: 'small muted' }, 'Plays back the stored timeline of a cached case in your browser (recorded run, no new analysis).'))),
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
  const det = summary.detector_alerts || { alerts: summary.alerts, open: summary.open, decided: summary.decided, lanes: summary.lanes };
  const evl = summary.evaluation_cases || { alerts: 0, lanes: {} };
  page.append(h('section', { class: 'grid kpis', 'aria-label': 'Key figures (detector alerts only)' },
    kpi('Open detector alerts', det.open, `${det.alerts} detector alerts, ${det.decided} decided by an analyst`),
    kpi('Escalated', det.lanes.escalated, 'detector alerts the agent escalated, or analyst override', '#/lanes'),
    kpi('Agent-cleared', det.lanes.agent_cleared, 'reason and evidence on file', '#/lanes'),
    kpi('Needs analyst', det.lanes.needs_analyst, 'agent was uncertain', '#/lanes'),
    d ? h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Detector precision'), versus(d.precision, b.precision),
      h('div', { class: 'sub' }, 'vs flat funding rule, seed 42')) : null,
    d ? h('div', { class: 'card kpi' }, h('div', { class: 'label' }, 'Detector recall'), versus(d.recall, b.recall),
      h('div', { class: 'sub' }, `${d.tp} of 39 attackers; synthetic data`)) : null));

  const COLS = ['Severity', 'Alert', 'Ring', 'Scenario hint', ['Score', 'num'], ['Members', 'num'], 'Detector', 'Agent lane'];
  const rowOf = (a) => [sevChip(a.severity), h('span', null, h('a', { href: `#/case/${encodeURIComponent(a.alert_id)}`, class: 'mono' }, a.alert_id), a.injected ? [' ', injectedChip()] : null),
    h('span', { class: 'mono' }, a.ring_id), scenarioChip(a.scenario_hint), a.score.toFixed(2), a.n_members,
    statusChip(a.status), a.analyst_decision ? h('span', null, laneChip(a.lane), ' ', h('span', { class: 'chip t-neutral' }, a.analyst_decision)) : laneChip(a.lane)];
  const queueTable = (list, caption) => table(COLS, list.map(rowOf),
    { caption, rowAttrs: (i) => ({ class: 'row-link', 'data-id': list[i].alert_id, tabindex: 0 }) });

  const real = queue.filter((a) => !a.injected);
  const evalCases = queue.filter((a) => a.injected);
  const body = real.length === 0
    ? empty('No alerts', 'The detector has not produced any alert.')
    : queueTable(real, 'Alert queue');

  page.append(h('section', { class: 'card', 'aria-label': 'Alert queue' },
    h('header', null, h('h2', null, 'Alert queue'),
      h('span', { class: 'small muted' }, `Detector alerts only. Severity is a display rule on the detector score (critical 60+, high 50+); threshold ${queue[0]?.threshold ?? ''}`)),
    body));

  let paintEval = null;
  if (evalCases.length) {
    const slot = h('div');
    const paint = () => {
      slot.replaceChildren();
      if (!evalCasesOn()) return;
      slot.append(h('section', { class: 'card eval-section', 'aria-label': 'Evaluation cases' },
        h('header', null, h('h2', null, 'Evaluation cases'), h('span', { class: 't-review chip' }, `${evl.alerts} injected`),
          h('span', { class: 'small muted' }, 'Built from real accounts at their real scores to test the agents in isolation. The detector did not raise them and they are not in the counts above.')),
        queueTable(evalCases, 'Evaluation cases')));
      wire();
    };
    page.append(h('div', { class: 'row' }, evalToggle(evalCases.length, paint)), slot);
    paintEval = paint;
  }

  // ---- replay of a recorded run: client-side animation of the stored timeline ----
  let replayTimer = null;
  const replaySlot = h('div');
  page.prepend(replaySlot);
  $('#replay-btn').addEventListener('click', async () => {
    const withCase = queue.filter((a) => a.has_case && !a.injected);
    if (!withCase.length) { replaySlot.replaceChildren(empty('Nothing to replay', 'No cached case exists yet.')); return; }
    const id = withCase[0].alert_id;
    clearInterval(replayTimer);
    const list = h('ol', { class: 'timeline', style: 'max-height:260px;overflow:auto' });
    const note = h('div', { class: 'small muted' }, 'Loading the stored timeline…');
    replaySlot.replaceChildren(h('section', { class: 'card', 'aria-label': 'Recorded run replay', 'aria-live': 'polite' },
      h('header', null, h('h2', null, `Replay of ${id}`), h('span', { class: 'badge cached', title: 'Stored events replayed in the browser' }, 'Recorded run')),
      h('div', { class: 'body' }, note, list)));
    let events = [];
    try { events = (await get(`/cases/${encodeURIComponent(id)}/timeline`)).events; } catch (e) { note.textContent = `Could not load the timeline: ${e.message}`; return; }
    const t0 = events.length ? new Date(events[0].ts) : null;
    const quick = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let i = 0;
    note.textContent = `Recorded run: ${events.length} stored events, shown one by one. Nothing is running now.`;
    const tick = () => {
      const batch = quick ? events.length : 1;
      for (let k = 0; k < batch && i < events.length; k += 1, i += 1) {
        const e = events[i];
        const secs = t0 ? ((new Date(e.ts) - t0) / 1000).toFixed(1) : '';
        list.append(h('li', { class: 'tl-item' }, h('span', { class: 't' }, secs ? `+${secs}s` : e.seq), h('span', null, `${e.agent}: ${e.summary || e.event_type}`)));
      }
      list.scrollTop = list.scrollHeight;
      if (i >= events.length) { clearInterval(replayTimer); note.textContent = `Recorded run finished (${events.length} events).`; }
    };
    replayTimer = setInterval(tick, 250);
    tick();
  });

  let rows = [];
  let sel = -1;
  const open = (r) => { location.hash = `#/case/${encodeURIComponent(r.dataset.id)}`; };
  const select = (i) => {
    if (!rows.length) return;
    sel = Math.max(0, Math.min(rows.length - 1, i));
    rows.forEach((r, k) => r.setAttribute('aria-selected', String(k === sel)));
    rows[sel].focus();
    rows[sel].scrollIntoView({ block: 'nearest' });
  };
  function wire() {
    rows = $$('tbody tr', page);
    rows.forEach((r, i) => {
      if (r.dataset.wired) return;
      r.dataset.wired = '1';
      r.addEventListener('click', (e) => { if (e.target.tagName !== 'A') open(r); });
      r.addEventListener('focus', () => { sel = i; rows.forEach((x, k) => x.setAttribute('aria-selected', String(k === i))); });
      r.addEventListener('keydown', (e) => { if (e.key === 'Enter') open(r); });
    });
  }
  wire();
  if (paintEval) paintEval();
  keys.set({ j: () => select(sel + 1), k: () => select(sel - 1) });
  return () => clearInterval(replayTimer);
}
