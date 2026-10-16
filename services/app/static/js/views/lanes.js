import { forget, get, post } from '../api.js';
import { h } from '../dom.js';
import { closeDialog, dialog, empty, errorState, injectedChip, loading, statusChip, store, toast } from '../ui.js';

export const title = 'Lanes';
const enc = encodeURIComponent;

const LANES = [
  ['escalated', 'Escalated', 'The agent escalated, or an analyst overrode a clear. Needs approval before anything is sent.'],
  ['agent_cleared', 'Agent-cleared', 'The agent closed these with a reason and evidence. An analyst can override.'],
  ['needs_analyst', 'Needs analyst', 'The agent was uncertain or the evidence did not meet the escalation standard.'],
];

function overrideDialog(item, kind, reload) {
  const who = h('input', { id: 'o-who', value: store('ms-handle') || '', required: true, maxlength: 40, autocomplete: 'off' });
  const why = h('textarea', { id: 'o-why', required: true, minlength: 3, maxlength: 500, placeholder: 'Why? Written to the audit trail.' });
  const err = h('div', { class: 'notice err', hidden: true, role: 'alert' });
  const go = h('button', { class: 'btn primary', type: 'submit' }, kind === 'override_to_escalate' ? 'Override and escalate' : 'Confirm clear');
  const dlg = dialog(`${kind === 'override_to_escalate' ? 'Override' : 'Confirm'} ${item.alert_id}`,
    h('div', { class: 'stack' },
      h('p', { class: 'small muted' }, kind === 'override_to_escalate'
        ? 'Moves the case to the escalated lane. It still needs an approval before anything is sent.'
        : 'Records that you agree with the clear.'),
      h('label', { for: 'o-who' }, 'Analyst handle (not a real name)', who), h('label', { for: 'o-why' }, 'Reason (required)', why), err),
    [h('button', { class: 'btn', type: 'button', onclick: closeDialog }, 'Cancel'), go]);
  dlg.querySelector('form').onsubmit = async (ev) => {
    ev.preventDefault();
    if (why.value.trim().length < 3 || !who.value.trim()) { err.hidden = false; err.textContent = 'A handle and a reason of at least 3 characters are required.'; return; }
    go.disabled = true;
    try {
      store('ms-handle', who.value.trim());
      await post(`/cases/${enc(item.alert_id)}/override`, { analyst: who.value.trim(), decision: kind, note: why.value.trim() });
      closeDialog();
      toast('Recorded in the audit trail.', 'ok');
      forget('/lanes'); forget('/summary'); forget('/queue'); forget('/cases/');
      reload();
    } catch (e) {
      go.disabled = false; err.hidden = false; err.textContent = e.message;
    }
  };
  why.focus();
}

function laneItem(lane, item, reload) {
  const body = [
    h('div', { class: 'row' }, h('a', { class: 'mono', href: `#/case/${enc(item.alert_id)}` }, item.alert_id), h('span', { class: 'chip t-neutral mono' }, item.ring_id), item.injected ? injectedChip() : null,
      statusChip(item.alert_status), item.analyst_changed ? h('span', { class: 'chip t-high' }, `analyst: ${(item.analyst_decision || '').replaceAll('_', ' ')}`) : null,
      h('span', { class: 'small muted' }, `${Math.round(item.confidence * 100)}% confidence`)),
    h('p', { class: 'small', style: 'margin:6px 0' }, item.summary),
  ];
  if (lane === 'agent_cleared') {
    body.push(h('div', { class: 'notice' }, h('strong', null, 'Clear reason: '), item.clear_reason || 'none stored'),
      h('div', { class: 'row small muted', style: 'margin:6px 0;gap:4px' }, 'Evidence:', (item.clear_evidence_ids || []).slice(0, 12).map((e) => h('code', { class: 'ev-chip none' }, e)),
        (item.clear_evidence_ids || []).length > 12 ? `+${item.clear_evidence_ids.length - 12} more (see the case)` : null),
      h('div', { class: 'row' }, h('button', { class: 'btn small danger', type: 'button', onclick: () => overrideDialog(item, 'override_to_escalate', reload) }, 'Override'),
        h('button', { class: 'btn small', type: 'button', onclick: () => overrideDialog(item, 'confirm_clear', reload) }, 'Confirm clear')));
  }
  return h('div', { class: 'lane-card' }, body);
}

export async function render(root) {
  const page = h('div', { class: 'stack' });
  root.append(h('div', { class: 'page-head' }, h('div', { class: 'grow' }, h('h1', null, 'Lanes'),
    h('p', null, 'Where each investigated alert ended up. Agents never act on accounts; every move here needs an analyst reason.'))), page);
  async function load() {
    page.replaceChildren(loading());
    let data;
    try { data = await get('/lanes', { fresh: true }); } catch (e) { page.replaceChildren(errorState(e, load)); return; }
    page.replaceChildren(h('div', { class: 'lanes' }, LANES.map(([key, name, hint]) => {
      const items = data[key] || [];
      return h('section', { class: 'card', 'aria-label': name },
        h('header', null, h('h2', null, name), h('span', { class: 'chip t-neutral' }, items.length)),
        h('div', { class: 'body small muted' }, hint),
        items.length ? items.map((i) => laneItem(key, i, load)) : empty('Nothing here', 'No case is in this lane.'));
    })));
  }
  await load();
}
