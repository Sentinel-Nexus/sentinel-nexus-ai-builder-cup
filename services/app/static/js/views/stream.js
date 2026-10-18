// "Stream a scenario": publishes a fixed synthetic burst to Pub/Sub and shows the pipeline.
// A near-real-time micro-batch path: the page states the measured delay, it promises none.
import { ApiError, get, post } from '../api.js';
import { h } from '../dom.js';
import { fmtN, toast } from '../ui.js';

const POLL_MS = 2000;
const GIVE_UP_MS = 5 * 60 * 1000;

const STATS = [
  ['published', 'Published', 'Messages sent to Pub/Sub'],
  ['ingested', 'Ingested', 'Rows new to BigQuery'],
  ['duplicates_dropped', 'Duplicates dropped', 'Seen before, dropped without a second row'],
  ['retry', 'Retried / malformed', 'Bad messages are retried, never written'],
  ['dead_letter', 'In the dead-letter queue', 'Failed 5 delivery attempts (trades-dlq)'],
  ['alerts_raised', 'Alerts raised', 'By the frozen detector'],
];

export const streamedChip = () => h('span', { class: 't-med chip', title: 'Raised by the frozen detector over a streamed synthetic burst. Not part of the detector counts.' }, 'Streamed');

function caption(st) {
  const k = st.counters;
  const t = st.timings || {};
  if (st.kind === 'poison') {
    if (st.state === 'finished') return `The malformed message failed ${k.retried + k.dead_letter} delivery attempts and was moved to the dead-letter queue. Nothing was written to BigQuery.`;
    return 'A deliberately malformed message was published. It is rejected, retried by Pub/Sub, and after 5 attempts moved to the dead-letter queue.';
  }
  if (st.state === 'publishing') return 'Publishing the synthetic burst to Pub/Sub.';
  if (st.state === 'ingesting') return 'Messages are being checked and written to BigQuery in micro-batches of up to 3 seconds or 200 messages.';
  if (st.state === 'scoring') return 'Every message is in BigQuery. The frozen detector is now scoring the streamed accounts (one query; this is the slow step).';
  if (st.state === 'finished') {
    const secs = t.last_publish_to_alert_s;
    const dup = k.ingested === 0 && k.duplicates_dropped > 0
      ? ' This burst is already in BigQuery from an earlier run, so every message was dropped as a duplicate; scoring still ran and no second alert or row was created.' : '';
    return `Done${secs != null ? `: ${secs} s from the last publish to the alert` : ''}. ${k.alerts_raised} alert(s) now in the queue with a Streamed chip.${dup}`;
  }
  if (st.state === 'stalled') return `The run did not finish in time (${st.error || 'no completion'}). Counters show where it stopped.`;
  return st.error ? `The run failed: ${st.error}` : '';
}

export async function mountStream({ page, refreshQueue }) {
  let cfg;
  try { cfg = await get('/stream/config', { fresh: true }); } catch { return () => {}; }
  if (!cfg || !cfg.enabled) return () => {};

  const secs = cfg.measured_seconds;
  const btn = h('button', { class: 'btn primary', type: 'button', id: 'stream-btn', 'aria-describedby': 'stream-note' }, 'Stream a scenario');
  const bad = h('button', { class: 'btn small', type: 'button', id: 'stream-bad-btn', 'aria-describedby': 'stream-bad-note' }, 'Send a bad message');
  const note = h('span', { id: 'stream-note', class: 'small muted' },
    `Publishes a synthetic burst to Pub/Sub; alerts appear within about ${secs != null ? `${secs} seconds` : 'a measured number of seconds (not measured yet)'} (micro-batches).`);
  const badNote = h('span', { id: 'stream-bad-note', class: 'small muted' }, 'Publishes one malformed message to show retries and the dead-letter queue.');
  const replay = document.getElementById('replay-btn');
  if (replay) replay.parentElement.append(btn, note, bad, badNote);

  const stats = Object.fromEntries(STATS.map(([k, label, hint]) => [k, h('div', { class: 'value', 'data-k': k }, '0')]));
  const bar = h('div', { class: 'fill' });
  const barBox = h('div', { class: 'stream-bar', role: 'progressbar', 'aria-label': 'Stream run progress', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': '0' }, bar);
  const text = h('p', { class: 'small', id: 'stream-caption', 'aria-live': 'polite' }, 'Idle. Stream a scenario to watch messages move through Pub/Sub into BigQuery and the detector.');
  const badge = h('span', { class: 'chip t-neutral', id: 'stream-state' }, 'Idle');
  const msgBox = h('div');
  const card = h('section', { class: 'card', id: 'stream-card', 'aria-label': 'Pipeline' },
    h('header', null, h('h2', null, 'Pipeline'), badge,
      h('span', { class: 'small muted' }, 'Near-real-time micro-batches: Pub/Sub, BigQuery, frozen detector. Synthetic burst.')),
    h('div', { class: 'body stack' },
      h('div', { class: 'stream-grid' }, STATS.map(([k, label, hint]) => h('div', { class: 'stream-stat', title: hint }, h('div', { class: 'label' }, label), stats[k]))),
      barBox, text, msgBox));
  page.insertBefore(card, page.firstChild);

  let timer = null;
  let runId = null;
  let t0 = 0;
  let errors = 0;

  const setBadge = (label, tone) => { badge.textContent = label; badge.className = `chip ${tone}`; };
  const paint = (st) => {
    const k = st.counters;
    stats.published.textContent = `${fmtN(k.published)} / ${fmtN(st.expected)}`;
    stats.ingested.textContent = fmtN(k.ingested);
    stats.duplicates_dropped.textContent = fmtN(k.duplicates_dropped);
    stats.retry.textContent = `${fmtN(k.retried)} / ${fmtN(k.malformed)}`;
    stats.dead_letter.textContent = fmtN(k.dead_letter);
    stats.alerts_raised.textContent = fmtN(k.alerts_raised);
    const pct = Math.round(st.progress * 100);
    bar.style.width = `${pct}%`;
    barBox.setAttribute('aria-valuenow', String(pct));
    text.textContent = caption(st);
    const tone = { finished: 't-ok', error: 't-crit', stalled: 't-high' }[st.state] || 't-med';
    setBadge({ finished: 'Finished', error: 'Error', stalled: 'Stalled', scoring: 'Scoring', ingesting: 'Ingesting', publishing: 'Publishing' }[st.state] || st.state, tone);
    msgBox.replaceChildren();
    if (st.state === 'error' || st.state === 'stalled') {
      msgBox.append(h('div', { class: 'state error', role: 'alert' }, h('strong', null, st.state === 'error' ? 'The stream run failed' : 'The stream run stalled'), st.error || ''));
    }
  };

  const stop = () => { clearInterval(timer); timer = null; btn.disabled = false; bad.disabled = false; };

  async function poll() {
    try {
      const st = await get(`/stream/status?run_id=${encodeURIComponent(runId)}`, { fresh: true });
      errors = 0;
      paint(st);
      if (!st.active) { stop(); await refreshQueue(); return; }
    } catch (e) {
      errors += 1;
      if (e instanceof ApiError && e.status === 404) {
        stop();
        setBadge('Error', 't-crit');
        msgBox.replaceChildren(h('div', { class: 'state error', role: 'alert' }, h('strong', null, 'The run was lost'), 'The service no longer knows this run (it may have restarted). Start another.'));
        return;
      }
      if (errors >= 3) {
        stop();
        setBadge('Error', 't-crit');
        msgBox.replaceChildren(h('div', { class: 'state error', role: 'alert' }, h('strong', null, 'Cannot read the run status'), e.message));
        return;
      }
    }
    if (Date.now() - t0 > GIVE_UP_MS) {
      stop();
      setBadge('Stalled', 't-high');
      msgBox.replaceChildren(h('div', { class: 'state error', role: 'alert' }, h('strong', null, 'Still running after 5 minutes'), 'The page stopped polling. Reload to check the status.'));
    }
  }

  function begin(id) {
    runId = id;
    t0 = Date.now();
    errors = 0;
    btn.disabled = true;
    bad.disabled = true;
    clearInterval(timer);
    timer = setInterval(poll, POLL_MS);
    poll();
  }

  async function start(path, label) {
    msgBox.replaceChildren();
    try {
      const r = await post(path);
      setBadge(label, 't-med');
      text.textContent = 'Starting…';
      await refreshQueue(); // a newer run replaces the streamed alerts
      begin(r.run_id);
    } catch (e) {
      if (e instanceof ApiError && e.status === 429) {
        setBadge('Rate limited', 't-high');
        text.textContent = 'Streaming is limited so the public service stays affordable. Nothing was published.';
        msgBox.replaceChildren(h('div', { class: 'state', role: 'alert', id: 'stream-limit' }, h('strong', null, 'Rate limited'), e.message));
      } else if (e instanceof ApiError && e.status === 409) {
        setBadge('Busy', 't-high');
        msgBox.replaceChildren(h('div', { class: 'state', role: 'alert' }, h('strong', null, 'A run is already in progress'), e.message));
      } else {
        setBadge('Error', 't-crit');
        msgBox.replaceChildren(h('div', { class: 'state error', role: 'alert' }, h('strong', null, 'Could not start the stream run'), e.message,
          h('div', { style: 'margin-top:8px' }, h('button', { class: 'btn small', type: 'button', onclick: () => start(path, label) }, 'Retry'))));
        toast('Could not start the stream run', 'err');
      }
    }
  }

  btn.addEventListener('click', () => start('/stream/replay', 'Publishing'));
  bad.addEventListener('click', () => start('/stream/poison', 'Publishing'));

  // show the last run, and resume it if it is still going (no request when nothing has run)
  const active = cfg.active_run || cfg.latest_run;
  if (active) {
    try {
      const st = await get(`/stream/status?run_id=${encodeURIComponent(active)}`, { fresh: true });
      paint(st);
      if (st.active) begin(st.run_id);
    } catch { /* the run is gone after a restart: idle */ }
  }

  return () => clearInterval(timer);
}
