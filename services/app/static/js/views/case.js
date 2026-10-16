// Case view: ring graph, agent timeline, draft report with evidence chips, analyst actions.
import { ApiError, forget, get, post, stream } from '../api.js';
import { $, clear, h, table } from '../dom.js';
import { createGraph } from '../graph.js';
import {
  INJECTED_LABEL, closeDialog, dialog, download, empty, errorState, fmtMs, fmtN, fmtTime, laneChip, loading,
  scenarioChip, sevChip, sourceBadge, statusChip, store, toast,
} from '../ui.js';

export const title = 'Case';
const LIVE_LIMIT_S = 120;
const enc = encodeURIComponent;

// ---------- timeline ----------

const AGENT_LABEL = {
  orchestrator: 'Orchestrator', investigator: 'Investigator', critic: 'Critic', policy: 'Policy retrieval',
  drafter: 'Drafter', analyst: 'Analyst',
};
const STAGES = [['Prefetch', (e) => e.agent === 'orchestrator' || e.event_type === 'tool_call' && e.agent !== 'investigator'],
  ['Investigator', (e) => e.agent === 'investigator'], ['Critic', (e) => e.agent === 'critic'],
  ['Policy', (e) => e.agent === 'policy'], ['Drafter', (e) => e.agent === 'drafter']];

function detailOf(e) {
  if (!e.detail) return {};
  try { return typeof e.detail === 'string' ? JSON.parse(e.detail) : e.detail; } catch { return {}; }
}

function engineTag(e) {
  if (!e.engine) return null;
  const label = e.engine === 'gql' ? 'GQL' : e.engine === 'fallback' ? 'Fallback' : e.engine;
  return h('span', { class: `chip ${e.engine === 'fallback' ? 't-high' : 't-med'}`, title: e.engine === 'fallback' ? 'GQL failed or was skipped: SQL + NetworkX answered' : 'Answered by the BigQuery graph' }, label);
}

function timelineItem(e, t0) {
  const d = detailOf(e);
  const verdict = e.event_type === 'verdict';
  const bad = (verdict && d.accepted === false) || ['limit', 'error', 'retry'].includes(e.event_type);
  const good = verdict && d.accepted === true;
  const secs = t0 && e.ts ? ((new Date(e.ts) - t0) / 1000).toFixed(1) : '';
  const tags = [];
  if (e.tool) tags.push(h('span', { class: 'chip t-neutral mono' }, e.tool));
  if (e.event_type === 'tool_call') tags.push(engineTag(e));
  if (e.cached) tags.push(h('span', { class: 'chip t-neutral', title: 'Answered from the in-memory cache' }, 'cached'));
  if (verdict) tags.push(h('span', { class: `chip ${good ? 't-ok' : 't-crit'}` }, good ? 'accepted' : 'rejected'), d.stage ? h('span', { class: 'chip t-neutral' }, d.stage) : null);
  if (e.event_type === 'llm_call' && d.model) tags.push(h('span', { class: 'chip t-neutral mono' }, d.model));
  if (e.latency_ms != null) tags.push(h('span', { class: 'chip t-neutral' }, fmtMs(e.latency_ms)));
  if (e.tokens) tags.push(h('span', { class: 'chip t-neutral' }, `${fmtN(e.tokens)} tok`));
  if (['limit', 'error', 'retry'].includes(e.event_type)) tags.push(h('span', { class: 'chip t-crit' }, e.event_type));
  return h('li', { class: `tl-item ${bad ? 'bad' : good ? 'good' : ''}` },
    h('span', { class: 't' }, secs ? `+${secs}s` : e.seq),
    h('span', null, e.summary || e.event_type, e.evidence_ids?.length ? h('span', { class: 'small muted' }, `  (${e.evidence_ids.length} evidence ids)`) : null),
    h('span', { class: 'tags' }, tags));
}

function timelineView(events, note) {
  if (!events.length) return empty('No audit events found for this run', note || 'The run may predate the audit table, or the rows are still being written.');
  const t0 = new Date(events[0].ts);
  const groups = new Map();
  for (const e of events) {
    if (!groups.has(e.agent)) groups.set(e.agent, []);
    groups.get(e.agent).push(e);
  }
  const wall = (new Date(events[events.length - 1].ts) - t0) / 1000;
  const tools = events.filter((e) => e.event_type === 'tool_call');
  const engines = [...new Set(tools.map((e) => e.engine).filter(Boolean))];
  const stats = h('div', { class: 'row small muted', style: 'padding:8px 16px;border-bottom:1px solid var(--border)' },
    h('span', null, `${events.length} events`), h('span', null, `${tools.length} tool calls (${tools.filter((e) => e.cached).length} cached)`),
    h('span', null, `engine: ${engines.join(', ') || 'n/a'}`),
    h('span', null, `${events.filter((e) => e.event_type === 'llm_call').length} model calls`),
    h('span', null, `${fmtN(events.filter((e) => e.event_type === 'llm_call').reduce((a, e) => a + (e.tokens || 0), 0))} tokens`),
    h('span', null, `${wall.toFixed(1)} s from first to last event`));
  const body = [...groups].map(([agent, list]) => h('details', { class: 'tl-group', open: agent !== 'analyst' ? true : null },
    h('summary', null, `${AGENT_LABEL[agent] || agent} (${list.length})`),
    h('ol', { class: 'timeline' }, list.map((e) => timelineItem(e, t0)))));
  return h('div', null, stats, body);
}

// ---------- evidence ----------

function evidenceChips(ids, chips, onPick) {
  const wrap = h('span');
  const show = (list) => list.forEach((id) => {
    const el = h('button', { type: 'button', class: 'ev-chip', 'aria-pressed': 'false', title: id, onclick: () => onPick(id, el) }, id);
    chips.push({ el, id });
    wrap.append(el);
  });
  show(ids.slice(0, 8));
  if (ids.length > 8) {
    const more = h('button', { type: 'button', class: 'btn small ghost', onclick: () => { more.remove(); show(ids.slice(8)); } }, `+${ids.length - 8} more`);
    wrap.append(more);
  }
  return wrap;
}

function summaryWithCites(text, claims, onCite) {
  const byId = Object.fromEntries(claims.map((c) => [c.claim_id, c]));
  const out = h('p', { class: 'summary' });
  for (const part of String(text || '').split(/(\[c\d+\])/g)) {
    const m = /^\[(c\d+)\]$/.exec(part);
    if (m && byId[m[1]]) {
      const b = h('button', { type: 'button', class: 'cite', 'aria-pressed': 'false', title: `Show ${m[1]} evidence on the graph`, onclick: () => onCite(m[1], b) }, m[1]);
      out.append(b);
    } else out.append(part);
  }
  return out;
}

// ---------- page ----------

export async function render(root, { args, keys }) {
  const alertId = args[0];
  const printMode = args[1] === 'print';
  const st = { graph: null, graphData: null, live: null, pressed: null, chips: [], detail: null, handle: store('ms-handle') || '' };
  let g = null;
  let abort = null;
  const cleanups = [() => { if (g) g.destroy(); if (abort) abort.abort(); }];
  const themeFn = () => g && g.restyle();
  window.addEventListener('themechange', themeFn);
  cleanups.push(() => window.removeEventListener('themechange', themeFn));

  const page = h('div', { class: 'stack' });
  root.append(page);
  page.append(loading());

  async function load(fresh) {
    try {
      st.detail = await get(`/cases/${enc(alertId)}/detail`, { fresh });
    } catch (e) {
      clear(page).append(h('div', { class: 'crumbs' }, h('a', { href: '#/' }, 'Command centre'), ` / ${alertId}`),
        e.status === 404 ? empty('Unknown alert', `There is no alert ${alertId}.`) : errorState(e, () => load(true)));
      return;
    }
    draw();
  }

  // ---- the page body ----
  function draw() {
    const { detail } = st;
    const alert = detail.alert;
    const live = st.live;
    const report = live ? live.report : detail.report;
    clear(page);
    st.chips = [];
    if (g) { g.destroy(); g = null; }

    const crumbs = h('div', { class: 'crumbs no-print' }, h('a', { href: '#/' }, 'Command centre'), ` / ${alert.alert_id}`);
    const badge = live ? sourceBadge(false) : sourceBadge(true, fmtTime(detail.case_created_ts));
    const lane = live ? report?.lane : detail.effective_lane;
    page.append(crumbs, h('div', { class: 'page-head' },
      h('div', { class: 'grow' },
        h('h1', null, h('span', { class: 'mono' }, alert.alert_id), ' ', badge),
        h('div', { class: 'row', style: 'margin-top:6px' }, sevChip(alert.severity), statusChip(alert.status), scenarioChip(alert.scenario_hint),
          h('span', { class: 'chip t-neutral' }, `ring ${alert.ring_id}`), h('span', { class: 'chip t-neutral' }, `score ${alert.score.toFixed(1)} / threshold ${alert.threshold}`),
          h('span', { class: 'chip t-neutral' }, `${alert.n_members} members`))),
      printMode ? printBar() : actionBar(report)),
    );
    if (printMode) page.append(h('div', { class: 'print-only small' }, `MarketSentinel case report, generated ${fmtTime(new Date().toISOString())}. Synthetic data; the agent recommends, an analyst decides.`));

    if (alert.injected || detail.source === 'eval_injected') {
      page.append(h('div', { class: 'notice warn', role: 'note' }, h('strong', null, `${INJECTED_LABEL}. `),
        'This alert was built for the evaluation from real accounts at their real detector scores; the detector did not raise it.'));
    }
    if (live) page.append(liveNotice());
    if (!report) {
      page.append(empty('No cached case yet', 'No case is stored for this alert. Use "Investigate live" to run one (limited).'));
      page.append(card('Alert evidence', evidenceOnly(alert)));
      return;
    }

    const left = h('div', { class: 'stack' });
    const right = h('div', { class: 'stack sticky' });
    const layout = h('div', { class: 'case-layout' }, left, right);
    page.append(layout);

    left.append(decisionCard(report, lane, detail, live));
    left.append(reportCard(report));
    left.append(card('Evidence', evidenceCard(report, alert)));
    if (report.removed_claims?.length) left.append(removedCard(report));
    left.append(card('Policy references', policyCard(report)));
    if (!printMode) left.append(trailCard());

    right.append(graphCard(report, alert));
    right.append(h('div', { class: 'card', id: 'node-panel', 'aria-live': 'polite' },
      h('header', null, h('h2', null, 'Selection')), h('div', { class: 'state small' }, 'Click an account, device or funding source in the graph to see its trades and links.')));

    const tlCard = h('section', { class: 'card', 'aria-label': 'Agent timeline' }, h('header', null, h('h2', null, 'Agent timeline'),
      live ? sourceBadge(false) : sourceBadge(true)), h('div', { id: 'timeline-body' }, loading(3)));
    page.append(tlCard);
    loadTimeline();
    loadGraph(report, alert);
    keys.set(shortcuts(report));
  }

  const card = (heading, body, extra) => h('section', { class: 'card' }, h('header', null, h('h2', null, heading), extra), h('div', { class: 'body' }, body));

  function printBar() {
    return h('div', { class: 'row no-print' },
      h('button', { class: 'btn primary', type: 'button', onclick: () => window.print() }, 'Print / save as PDF'),
      h('a', { class: 'btn', href: `#/case/${enc(alertId)}` }, 'Back to case'));
  }

  function actionBar(report) {
    const locked = !!st.live || !report;
    const escalated = (st.detail.effective_lane === 'escalated');
    const why = locked ? 'Decisions apply to the stored case; leave the live result first.' : '';
    return h('div', { class: 'row no-print', role: 'group', 'aria-label': 'Case actions' },
      h('button', { class: 'btn primary', type: 'button', disabled: locked, title: why || 'Approve the recommendation (a: asks for a reason)', onclick: () => decide('approve') }, 'Approve'),
      h('button', { class: 'btn', type: 'button', disabled: locked, title: why || 'Reject the recommendation (r)', onclick: () => decide('reject') }, 'Reject'),
      h('button', { class: 'btn danger', type: 'button', disabled: locked || escalated, title: escalated ? 'Already escalated' : (why || 'Escalate this case yourself (e)'), onclick: () => decide('escalate') }, 'Escalate'),
      h('span', { style: 'width:8px' }),
      h('button', { class: 'btn', type: 'button', onclick: confirmLive, title: 'Run the agents now (i). Limited per client and per hour.' }, 'Investigate live'),
      h('button', { class: 'btn', type: 'button', onclick: exportJson, title: 'Download the case as JSON (x)' }, 'Export JSON'),
      h('a', { class: 'btn', href: `#/case/${enc(alertId)}/print`, title: 'Print-friendly view (p)' }, 'Print view'));
  }

  function shortcuts(report) {
    const ok = !st.live && !!report;
    return {
      a: () => ok && decide('approve'), r: () => ok && decide('reject'),
      e: () => ok && st.detail.effective_lane !== 'escalated' && decide('escalate'),
      i: confirmLive, x: exportJson, p: () => { location.hash = `#/case/${enc(alertId)}/print`; },
    };
  }

  function decisionCard(report, lane, detail, live) {
    const conf = report.confidence != null ? `${Math.round(report.confidence * 100)}% confidence` : '';
    const meta = live
      ? [`${fmtN(live.tokens)} tokens`, `${live.tool_calls} tool calls`, fmtMs(live.latency_ms), `engine ${live.engines || 'n/a'}`]
      : [`${fmtN(detail.tokens)} tokens`, `${detail.tool_calls} tool calls`, fmtMs(detail.latency_ms), `engine ${detail.engines || 'n/a'}`, detail.source ? `source ${detail.source}` : null];
    const models = detail.models && Object.keys(detail.models).length ? [...new Set(Object.values(detail.models))].join(', ') : null;
    return h('section', { class: 'card' }, h('div', { class: 'decision-banner' },
      h('div', null, h('div', { class: 'small muted' }, 'Agent recommendation'),
        h('div', { class: 'row' }, h('strong', { style: 'font-size:1.1rem' }, report.decision), laneChip(lane))),
      h('div', { class: 'small muted' }, conf, report.recommended_action ? ` · ${report.recommended_action}` : ''),
      h('div', { class: 'small muted grow', style: 'flex:1;text-align:right' }, meta.filter(Boolean).join(' · '), models ? ` · ${models}` : '')),
    detail.analyst_override ? h('div', { class: 'notice', style: 'margin:0 16px 12px' }, `Analyst ${detail.analyst_override.decision.replaceAll('_', ' ')} on ${fmtTime(detail.analyst_override.ts)}.`) : null,
    report.decision === 'clear' ? h('div', { class: 'body', style: 'border-top:1px solid var(--border)' }, h('strong', null, 'Why the agent cleared this case'),
      h('p', null, report.clear_reason || 'No reason stored.'), evidenceChips(report.clear_evidence_ids || [], st.chips, pick)) : null);
  }

  function reportCard(report) {
    const sum = summaryWithCites(report.summary, report.evidence_table || [], (cid, el) => {
      const c = report.evidence_table.find((x) => x.claim_id === cid);
      pick(c.evidence_ids, el);
    });
    return card('Draft report', [
      sum,
      report.hint_note ? h('p', { class: 'small muted' }, `Detector hint (a hypothesis): ${report.hint_note}`) : null,
      report.unverified_note ? h('p', { class: 'notice warn small' }, report.unverified_note) : null,
      h('p', { class: 'small muted' }, `Members: ${(report.members_alert || []).length} in the alert${report.members_linked?.length ? `, ${report.members_linked.length} linked through devices or fills` : ''}. ${report.member_basis || ''}`),
    ]);
  }

  function evidenceOnly(alert) {
    return h('div', null, h('p', { class: 'small muted' }, 'Detector evidence ids:'), evidenceChips(alert.evidence_edge_ids || [], st.chips, pick));
  }

  function evidenceCard(report, alert) {
    const claims = report.evidence_table || [];
    if (!claims.length) return empty('No verified claims', 'Every claim was removed or none was produced.');
    return h('div', null, claims.map((c) => h('div', { class: 'claim' },
      h('div', { class: 'meta' }, h('span', { class: 'chip t-ok' }, 'verified'), ' ', h('span', { class: 'mono' }, c.claim_id), ` · ${c.kind}`),
      h('div', { class: 'text' }, c.claim), h('div', null, evidenceChips(c.evidence_ids || [], st.chips, pick)))),
    alert.evidence_edge_ids?.length ? h('details', { style: 'margin-top:8px' }, h('summary', { class: 'small' }, `Detector evidence (${alert.evidence_edge_ids.length} edge ids)`), evidenceChips(alert.evidence_edge_ids, st.chips, pick)) : null);
  }

  function removedCard(report) {
    return h('section', { class: 'card' }, h('header', null, h('h2', null, 'Removed claims'), h('span', { class: 'chip t-crit' }, `${report.removed_claims.length} not verified`)),
      h('div', { class: 'body' }, h('p', { class: 'small muted' }, 'The Critic or the evidence ledger rejected these. They are not part of the report.'),
        report.removed_claims.map((c) => h('div', { class: 'claim removed' }, h('div', { class: 'meta' }, h('span', { class: 'mono' }, c.claim_id), ' · reason: ', c.reason), h('div', { class: 'text' }, c.text)))));
  }

  function policyCard(report) {
    const refs = report.policy_references || [];
    if (!refs.length) return empty('No policy clause cited', '');
    return h('div', null, refs.map((p) => h('div', { class: 'policy' }, h('strong', null, `${p.clause_id} · ${p.title}`), h('div', { class: 'small muted' }, p.why))));
  }

  function trailCard() {
    const box = h('div', { id: 'trail' }, h('div', { class: 'state small' }, 'Loading analyst decisions…'));
    get(`/audit?alert_id=${enc(alertId)}&limit=100&hide_test=true`, { fresh: true }).then((res) => {
      const rows = res.events.filter((e) => ['approval', 'override', 'webhook_mock'].includes(e.event_type));
      clear(box).append(rows.length ? table(['When', 'Event', 'Detail'], rows.map((e) => [fmtTime(e.ts), e.event_type.replace('_', ' '), e.summary]), { dense: true, caption: 'Analyst decisions' })
        : h('div', { class: 'state small' }, 'No analyst decision yet.'));
    }).catch((e) => clear(box).append(h('div', { class: 'state small error' }, `Audit trail unavailable: ${e.message}`)));
    return card('Analyst decisions (audit)', box);
  }

  // ---- graph ----
  function graphCard(report, alert) {
    return h('section', { class: 'card', 'aria-label': 'Ring graph' },
      h('header', null, h('h2', null, 'Ring graph'), h('span', { id: 'graph-engine' }), h('button', { class: 'btn small ghost no-print', type: 'button', onclick: () => { if (g) { g.clearFocus(); g.fit(); st.pressed = null; $('[aria-pressed="true"]', page)?.setAttribute('aria-pressed', 'false'); } } }, 'Reset view')),
      h('div', { id: 'graph', role: 'img', 'aria-label': `Graph of ring ${alert.ring_id}: accounts, devices and funding sources` }, h('div', { class: 'state' }, 'Loading graph…')),
      h('div', { class: 'legend' },
        h('span', null, h('i', { style: 'border-radius:50%;color:var(--g-account)' }), 'Account (dashed: linked, not in alert)'),
        h('span', null, h('i', { style: 'transform:rotate(45deg) scale(.8);color:var(--g-device)' }), 'Device'),
        h('span', null, h('i', { style: 'border-radius:3px;color:var(--g-funding)' }), 'Funding source'),
        h('span', null, 'Edges: solid uses-device, dashed funded-by, dotted traded-with. Thick edges are the detector evidence.')),
      h('div', { class: 'body small muted', id: 'member-list' }));
  }

  async function loadGraph(report, alert) {
    const box = $('#graph', page);
    try {
      st.graphData = await get(`/cases/${enc(alertId)}/graph`);
    } catch (e) {
      clear(box).append(errorState(e, () => loadGraph(report, alert)));
      st.chips.forEach((c) => { c.el.title = `${c.id} (graph unavailable)`; });
      return;
    }
    const gd = st.graphData;
    $('#graph-engine', page).replaceWith(h('span', { id: 'graph-engine', class: 'chip t-med', title: gd.fallback_reason || 'Answered by the BigQuery graph' }, `${gd.engine === 'gql' ? 'GQL' : 'Fallback'}${gd.cached ? ' · cached' : ''}`));
    clear(box);
    try {
      g = createGraph(box, { ...gd, members_alert: report.members_alert || gd.members_alert }, { onSelectNode: showNode });
    } catch (e) {
      box.append(errorState(e));
      return;
    }
    const accts = gd.nodes.filter((n) => n.type === 'Account').map((n) => n.key).sort();
    const list = $('#member-list', page);
    list.append(h('div', null, `${accts.length} accounts${gd.truncated ? ' (truncated)' : ''}: `), h('div', { class: 'row', style: 'margin-top:4px' }, accts.slice(0, 40).map((a) => h('button', { type: 'button', class: 'ev-chip', onclick: () => g.selectAccount(a) }, a)), accts.length > 40 ? `+${accts.length - 40} more` : null));
    st.chips.forEach((c) => {
      const ids = ids2graph(c.id);
      if (!ids.some((x) => g.resolves(x))) { c.el.classList.add('none'); c.el.title = `${c.id}: no matching element in the graph (a policy clause, market, trade or score row)`; }
    });
    if (printMode) setTimeout(printSnapshot, 600);
  }

  function printSnapshot() {
    // a still image prints more reliably than a canvas
    const box = $('#graph', page);
    const canvasImg = box && box.querySelector('canvas');
    if (!canvasImg) return;
    try {
      const url = canvasImg.toDataURL('image/png');
      if (url.length > 1000) box.dataset.snapshot = '1';
    } catch { /* tainted or unavailable: print the canvas */ }
  }

  const ids2graph = (id) => {
    if (/^T\d+/.test(id) && st.graphData?.trade_accounts?.[id]) return [`Account:${st.graphData.trade_accounts[id]}`];
    return [id];
  };

  function pick(idOrList, el) {
    const list = Array.isArray(idOrList) ? idOrList : [idOrList];
    const wasOn = el.getAttribute('aria-pressed') === 'true';
    $$pressed().forEach((b) => b.setAttribute('aria-pressed', 'false'));
    if (wasOn) { if (g) { g.clearFocus(); g.fit(); } return; }
    el.setAttribute('aria-pressed', 'true');
    if (!g) { toast('The graph is not available yet.', 'err'); return; }
    const n = g.focus(list.flatMap(ids2graph));
    if (!n) toast('None of these ids has a node or edge in the graph (policy clauses, markets and score rows do not).');
  }
  const $$pressed = () => [...page.querySelectorAll('[aria-pressed="true"]')];

  // ---- node panel ----
  async function showNode(data) {
    const panel = $('#node-panel', page);
    if (!panel) return;
    if (!data) { clear(panel).append(h('header', null, h('h2', null, 'Selection')), h('div', { class: 'state small' }, 'Nothing selected.')); return; }
    clear(panel).append(h('header', null, h('h2', null, `${data.type === 'FundingSource' ? 'Funding source' : data.type} ${data.label}`)), h('div', { class: 'body' }, loading(3)));
    if (data.type !== 'Account') {
      const who = g.neighbours(data.id);
      clear(panel).append(h('header', null, h('h2', null, `${data.type === 'FundingSource' ? 'Funding source' : data.type} ${data.label}`)),
        h('div', { class: 'body' }, h('p', null, `Shared by ${who.length} accounts in this ring:`), h('div', null, who.map((a) => h('button', { type: 'button', class: 'ev-chip', onclick: () => g.selectAccount(a) }, a)))));
      return;
    }
    try {
      const t = await get(`/cases/${enc(alertId)}/trades?account=${enc(data.label)}`);
      const f = t.score?.contributions ? Object.entries(t.score.contributions).sort((a, b) => b[1] - a[1]).slice(0, 4) : [];
      clear(panel).append(h('header', null, h('h2', null, `Account ${t.account_id}`), h('span', { class: 'chip t-neutral' }, `engine ${t.engine}`)),
        h('div', { class: 'body' },
          t.score ? h('p', null, `Detector score ${t.score.score}`, f.length ? h('span', { class: 'small muted' }, ` (top: ${f.map(([k, v]) => `${k} ${v}`).join(', ')})`) : null) : null,
          t.summary ? h('p', null, `${t.summary.n_trades} trades, $${fmtN(Math.round(t.summary.total_usd))} in ${t.market_id}`) : h('p', { class: 'muted' }, t.market_id ? `No trades in ${t.market_id}.` : 'No trades.'),
          t.other_markets.length ? h('p', { class: 'small muted' }, `Markets: ${t.other_markets.map((m) => `${m.market_id} (${m.n_trades})`).join(', ')}`) : null,
          t.trades.length ? table(['Time', 'Side', 'Out', ['Price', 'num'], ['USD', 'num']], t.trades.slice(0, 40).map((x) => [String(x.ts).slice(0, 19), x.side, x.outcome, Number(x.price).toFixed(2), fmtN(Math.round(x.size_usd))]), { dense: true, caption: 'Trades' }) : null,
          t.truncated ? h('p', { class: 'small muted' }, 'More trades exist than are shown.') : null));
    } catch (e) {
      clear(panel).append(h('header', null, h('h2', null, `Account ${data.label}`)), errorState(e, () => showNode(data)));
    }
  }

  // ---- timeline ----
  async function loadTimeline() {
    const box = $('#timeline-body', page);
    if (st.live) { clear(box).append(timelineView(st.live.events, 'Live run events')); return; }
    try {
      const t = await get(`/cases/${enc(alertId)}/timeline`);
      clear(box).append(timelineView(t.events));
    } catch (e) {
      clear(box).append(errorState(e, loadTimeline));
    }
  }

  // ---- decisions ----
  function decide(kind) {
    const labels = { approve: 'Approve', reject: 'Reject', escalate: 'Escalate' };
    const help = {
      approve: 'Approving an escalation fires the mocked outbound webhook (nothing leaves this service). Approving a clear just records that you agree.',
      reject: 'Rejecting records that you disagree with the agent recommendation.',
      escalate: 'Moves the case to the escalated lane. It still needs an approval before anything is sent.',
    };
    const who = h('input', { id: 'f-who', value: st.handle, required: true, maxlength: 40, autocomplete: 'off', placeholder: 'analyst handle' });
    const why = h('textarea', { id: 'f-why', required: true, minlength: 3, maxlength: 500, placeholder: 'Why? This is written to the audit trail.' });
    const err = h('div', { class: 'notice err', hidden: true, role: 'alert' });
    const go = h('button', { class: 'btn primary', type: 'submit' }, labels[kind]);
    const dlg = dialog(`${labels[kind]} case ${alertId}`, h('div', { class: 'stack' }, h('p', { class: 'small muted' }, help[kind]),
      h('label', { for: 'f-who' }, 'Analyst handle (not a real name)', who), h('label', { for: 'f-why' }, 'Reason (required)', why), err),
    [h('button', { class: 'btn', type: 'button', onclick: closeDialog }, 'Cancel'), go]);
    dlg.querySelector('form').onsubmit = async (ev) => {
      ev.preventDefault();
      if (why.value.trim().length < 3 || !who.value.trim()) { err.hidden = false; err.textContent = 'A handle and a reason of at least 3 characters are required.'; return; }
      go.disabled = true;
      try {
        st.handle = who.value.trim();
        store('ms-handle', st.handle);
        const body = { analyst: st.handle, note: why.value.trim() };
        const res = kind === 'escalate'
          ? await post(`/cases/${enc(alertId)}/override`, { ...body, decision: 'override_to_escalate' })
          : await post(`/cases/${enc(alertId)}/approve`, { ...body, decision: kind });
        closeDialog();
        toast(kind === 'approve' && res.webhook_fired ? 'Approved. Mock webhook fired (nothing left the service).' : `${labels[kind]} recorded in the audit trail.`, 'ok');
        forget('/cases/'); forget('/summary'); forget('/queue'); forget('/audit');
        await load(true);
      } catch (e) {
        go.disabled = false;
        err.hidden = false;
        err.textContent = e.message;
      }
    };
    why.focus();
  }

  // ---- live investigation ----
  async function confirmLive() {
    let gov = null;
    try { gov = await get('/governance', { fresh: true }); } catch { /* shown without quota */ }
    const left = gov?.live;
    const go = h('button', { class: 'btn primary', type: 'submit' }, 'Start live investigation');
    const dlg = dialog('Investigate live', h('div', { class: 'stack' },
      h('p', null, 'This runs the agents now: tool calls on the graph, three model calls or more, a Critic pass and a draft. It takes 20 to 70 seconds and has a hard stop at 120 seconds.'),
      h('p', { class: 'small muted' }, 'It spends model tokens, so it is limited. The result is shown to you only; the stored (cached) case is not replaced.'),
      left ? h('div', { class: 'notice' }, `Left for you this hour: ${left.client_left} of ${left.client_per_hour}. Whole service this hour: ${left.service_hour_left} of ${left.service_per_hour}.`) : null),
    [h('button', { class: 'btn', type: 'button', onclick: closeDialog }, 'Cancel'), go]);
    dlg.querySelector('form').onsubmit = (ev) => { ev.preventDefault(); closeDialog(); runLive(); };
  }

  async function runLive() {
    const events = [];
    const t0 = Date.now();
    const state = { done: false };
    const prog = h('section', { class: 'card', id: 'live-progress', 'aria-live': 'polite' });
    const stepsEl = h('ol', { class: 'steps' }, STAGES.map(([n]) => h('li', { 'data-stage': n }, n)));
    const clock = h('span', { class: 'chip t-neutral mono' }, '0 s');
    const log = h('ol', { class: 'timeline', style: 'max-height:220px;overflow:auto' });
    const note = h('div', { class: 'small muted' }, 'Starting…');
    const cancel = h('button', { class: 'btn small', type: 'button', onclick: () => abort && abort.abort() }, 'Stop waiting');
    prog.append(h('header', null, h('h2', null, 'Live investigation'), sourceBadge(false), clock, cancel),
      h('div', { class: 'body' }, stepsEl, note), log);
    const old = $('#live-progress', page);
    if (old) old.remove();
    $('.page-head', page).after(prog);
    prog.scrollIntoView({ block: 'nearest' });

    abort = new AbortController();
    const timer = setInterval(() => {
      const s = Math.round((Date.now() - t0) / 1000);
      clock.textContent = `${s} s of ${LIVE_LIMIT_S} s`;
      if (s > LIVE_LIMIT_S + 15) abort.abort();
    }, 500);
    const mark = () => {
      const last = events[events.length - 1];
      const idx = STAGES.findIndex(([, fn]) => fn(last));
      [...stepsEl.children].forEach((li, i) => { li.className = i < idx ? 'done' : i === idx ? 'active' : ''; });
      note.textContent = last.summary || last.event_type;
    };
    let result = null;
    let failure = null;
    try {
      await stream(`/cases/${enc(alertId)}/investigate?force=true`, (name, data) => {
        if (name === 'step') {
          events.push(data);
          log.append(timelineItem(data, new Date(events[0].ts)));
          log.scrollTop = log.scrollHeight;
          mark();
        } else if (name === 'result') result = data;
        else if (name === 'error') failure = `${data.error}: ${data.message}`;
      }, abort.signal);
    } catch (e) {
      if (e.name === 'AbortError') failure = 'You stopped waiting, or the service did not answer in time. The run may still finish on the server; the cached case is unchanged.';
      else if (e instanceof ApiError && e.status === 429) failure = e.message;
      else failure = e.message || String(e);
    } finally {
      clearInterval(timer);
      abort = null;
      state.done = true;
    }

    if (result && result.status === 'ok' && result.report) {
      st.live = { ...result, events };
      forget('/audit');
      toast('Live result ready. It is marked LIVE and is not stored.', 'ok');
      draw();
      return;
    }
    const why = result?.status === 'timed_out'
      ? `The investigation did not finish within ${LIVE_LIMIT_S} seconds and was stopped. Nothing was stored; the cached case is unchanged. Try again later or review the cached case.`
      : result?.status === 'error'
        ? `The live run failed: ${result.error}. This usually means the model service is busy or rate limited. The cached case is unchanged; try again in a few minutes.`
        : (failure || 'The live run ended without a result.');
    clear(prog).append(h('header', null, h('h2', null, 'Live investigation'), sourceBadge(false)),
      h('div', { class: 'body' }, h('div', { class: `notice ${/limit|hour|day|switched/.test(why) ? 'warn' : 'err'}`, role: 'alert' }, why),
        h('div', { class: 'row', style: 'margin-top:8px' }, h('button', { class: 'btn small', type: 'button', onclick: () => prog.remove() }, 'Dismiss'))),
      events.length ? h('details', null, h('summary', { class: 'small', style: 'padding:8px 16px' }, `${events.length} events before it stopped`), h('ol', { class: 'timeline' }, events.map((e) => timelineItem(e, new Date(events[0].ts))))) : null);
  }

  function liveNotice() {
    return h('div', { class: 'notice warn no-print' }, h('strong', null, 'LIVE result. '),
      'This investigation was just run for you and is not stored. Approve, reject and escalate apply to the stored case. ',
      h('button', { class: 'btn small', type: 'button', onclick: () => { st.live = null; draw(); } }, 'Back to the cached case'));
  }

  // ---- export ----
  async function exportJson() {
    try {
      const res = await fetch(`/cases/${enc(alertId)}/export`);
      if (!res.ok) throw new ApiError(res.status, `Export failed (${res.status})`);
      download(`case_${alertId}.json`, await res.text());
      toast('Case exported as JSON.', 'ok');
    } catch (e) {
      toast(e.message, 'err');
    }
  }

  await load(false);
  return () => cleanups.forEach((f) => f());
}
