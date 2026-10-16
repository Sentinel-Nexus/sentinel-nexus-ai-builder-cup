import { get } from '../api.js';
import { h, table } from '../dom.js';
import { errorState, fmtN, fmtTime, loading } from '../ui.js';

export const title = 'Governance';

const GUARD_TONE = { active: 't-ok', mock: 't-med', 'not deployed': 't-high' };

export async function render(root) {
  const page = h('div', { class: 'stack' });
  root.append(h('div', { class: 'page-head' }, h('div', { class: 'grow' }, h('h1', null, 'Governance'),
    h('p', null, 'The limits agents run under, the guards in force, and the audit trail of every step and analyst action.'))), page);
  page.append(loading());
  let g;
  try { g = await get('/governance', { fresh: true }); } catch (e) { page.replaceChildren(errorState(e, () => location.reload())); return; }
  const l = g.limits;
  const live = g.live;
  const auditBox = h('div', null, h('div', { class: 'state small' }, 'Loading audit trail…'));
  const filter = h('select', { id: 'a-type', 'aria-label': 'Filter by event type' },
    ['', 'approval', 'override', 'tool_call', 'verdict', 'llm_call', 'limit', 'error', 'retry', 'webhook_mock'].map((t) => h('option', { value: t }, t || 'All event types')));

  page.replaceChildren(
    h('div', { class: 'notice' }, h('strong', null, 'All data is synthetic. '), 'No customer, employer or personal data is stored or processed. Analyst handles are free text.'),
    h('div', { class: 'grid cols-2' },
      h('section', { class: 'card' }, h('header', null, h('h2', null, 'Caps in force')),
        table(['Limit', ['Value', 'num']], [
          ['Tool calls per investigation', l.max_tool_calls], ['Tokens per investigation (all agents)', fmtN(l.total_tokens)],
          ...Object.entries(l.agent_tokens).map(([k, v]) => [`Token ceiling, ${k}`, fmtN(v)]),
          ...Object.entries(l.max_output_tokens).map(([k, v]) => [`Max output tokens, ${k}`, fmtN(v)]),
          ['Wall-clock timeout per case', `${l.wall_clock_s} s`], ['Graph query budget', `${l.graph_budget_s} s`],
          ['Concurrent model calls', l.llm_concurrency], ['Concurrent investigations', l.run_concurrency],
        ], { dense: true, caption: 'Caps' })),
      h('section', { class: 'card' }, h('header', null, h('h2', null, 'Live investigations')),
        table(['Limit', ['Allowed', 'num'], ['Left now', 'num']], [
          ['Per client, per hour', live.client_per_hour, live.client_left],
          ['Whole service, per hour', live.service_per_hour, live.service_hour_left],
          ['Whole service, per day', live.service_per_day, live.service_day_left],
        ], { dense: true, caption: 'Live limits' }),
        h('div', { class: 'body small muted' }, 'Cached cases are always served. A live result is shown to the requester and does not replace the stored case. ', live.note,
          ` Models: ${[...new Set(Object.values(g.models))].join(', ')}. Policy retrieval: ${g.retrieval}.`))),
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Guards')),
      table(['Guard', 'Status', 'What it does'], g.guards.map((x) => [x.name, h('span', { class: `chip ${GUARD_TONE[x.status] || 't-neutral'}` }, x.status), x.detail]), { dense: true, caption: 'Guards' })),
    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Audit log'), filter), auditBox));

  async function loadAudit() {
    const t = filter.value;
    try {
      const res = await get(`/audit?limit=200${t ? `&event_type=${encodeURIComponent(t)}` : ''}`, { fresh: true });
      auditBox.replaceChildren(res.events.length
        ? table(['Time', 'Alert', 'Agent', 'Event', 'Summary'], res.events.map((e) => [fmtTime(e.ts), h('span', { class: 'mono' }, e.alert_id), e.agent, e.event_type, (e.summary || '').slice(0, 220)]), { dense: true, caption: 'Audit events, newest first' })
        : h('div', { class: 'state' }, h('strong', null, 'No events'), 'Nothing matches this filter.'));
    } catch (e) {
      auditBox.replaceChildren(errorState(e, loadAudit));
    }
  }
  filter.addEventListener('change', loadAudit);
  loadAudit();
}
