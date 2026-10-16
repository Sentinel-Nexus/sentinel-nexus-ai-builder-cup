import { get } from '../api.js';
import { h, table } from '../dom.js';
import { css, errorState, fmtN, fmtPct } from '../ui.js';

export const title = 'Benchmark';

function chart(canvasLabel, build) {
  const box = h('div', { class: 'chart-box' });
  const canvas = h('canvas', { role: 'img', 'aria-label': canvasLabel });
  box.append(canvas);
  let inst = null;
  const draw = () => {
    if (!window.Chart) { box.replaceChildren(h('div', { class: 'state small' }, 'The chart library did not load; see the table.')); return; }
    if (inst) inst.destroy();
    window.Chart.defaults.color = css('text-2');
    window.Chart.defaults.font.family = css('font') || 'system-ui';
    inst = new window.Chart(canvas, build());
  };
  // Chart.js is loaded with `defer`; wait for it if needed
  if (window.Chart) setTimeout(draw, 0); else window.addEventListener('load', draw, { once: true });
  return { box, draw, destroy: () => inst && inst.destroy() };
}

const axes = () => ({ x: { grid: { color: css('border') } }, y: { min: 0, max: 100, grid: { color: css('border') }, ticks: { callback: (v) => `${v}%` } } });

export async function render(root) {
  let d;
  try { d = await get('/benchmark'); } catch (e) { root.append(errorState(e, () => location.reload())); return; }
  const charts = [];
  const sc = d.seed42.filter((r) => r.group !== 'all_scenarios');
  const groups = [...new Set(sc.map((r) => r.group))];
  const pick = (method, key) => groups.map((g) => sc.find((r) => r.group === g && r.method.startsWith(method))[key]);

  const c1 = chart('Recall by scenario, flat rule versus detector', () => ({
    type: 'bar',
    data: { labels: groups.map((g) => g.replaceAll('_', ' ')), datasets: [
      { label: 'Flat rule recall', data: pick('Flat', 'recall'), backgroundColor: css('g-edge') },
      { label: 'Detector recall', data: pick('Detector', 'recall'), backgroundColor: css('accent') },
      { label: 'Flat rule precision', data: pick('Flat', 'precision'), backgroundColor: css('g-dim') },
      { label: 'Detector precision', data: pick('Detector', 'precision'), backgroundColor: css('g-funding') },
    ] },
    options: { maintainAspectRatio: false, scales: axes(), plugins: { legend: { position: 'bottom' } } },
  }));
  const c2 = chart('Robustness F1 median by detector variant', () => ({
    type: 'bar',
    data: { labels: d.ablation.map((a) => a.variant), datasets: [{ label: 'Median F1 over 8 unseen seeds', data: d.ablation.map((a) => a.robustness_f1.median), backgroundColor: css('accent') }] },
    options: { indexAxis: 'y', maintainAspectRatio: false, scales: { x: { min: 0, max: 100, ticks: { callback: (v) => `${v}%` }, grid: { color: css('border') } }, y: { grid: { display: false } } }, plugins: { legend: { display: false } } },
  }));
  charts.push(c1, c2);
  const redraw = () => charts.forEach((c) => c.draw());
  window.addEventListener('themechange', redraw);

  const all = d.kpi;
  root.append(h('div', { class: 'stack' },
    h('div', { class: 'page-head' }, h('div', { class: 'grow' }, h('h1', null, 'Benchmark'),
      h('p', null, `Flat funding rule versus the detector, ablation, and the agent evaluation. Synthetic data, seed 42 held out, threshold ${d.threshold} frozen.`))),
    h('div', { class: 'notice warn' }, h('strong', null, 'Read the limits first. '), 'Every number below comes from generated scenarios whose labels the generator planted. They show the method is consistent on that world, not how it performs on real markets.'),

    h('section', { class: 'card', 'aria-label': 'Honest limits' }, h('header', null, h('h2', null, 'Limits we are not hiding')),
      d.limits.map((l) => h('div', { class: 'limit-item' }, h('h3', null, l.title), h('p', { class: 'small', style: 'margin:0' }, l.text)))),

    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Seed 42: baseline versus detector')),
      h('div', { class: 'body' }, h('div', { class: 'vs', style: 'margin-bottom:12px' },
        h('span', null, 'Detector ', h('strong', null, `${fmtPct(all.detector.precision)} precision, ${fmtPct(all.detector.recall)} recall`), ` (${all.detector.tp} of 39 attackers, ${all.detector.fp} false positives)`),
        h('span', { class: 'b' }, `Flat rule ${fmtPct(all.baseline.precision)} / ${fmtPct(all.baseline.recall)} (${all.baseline.tp} of 39, ${all.baseline.fp} false positives)`)), c1.box),
      table(['Group', 'Method', ['Flagged', 'num'], ['TP', 'num'], ['FP', 'num'], ['Precision', 'num'], ['Recall', 'num'], ['F1', 'num']],
        d.seed42.map((r) => [r.group.replaceAll('_', ' '), r.method, r.flagged, r.tp, r.fp, fmtPct(r.precision), fmtPct(r.recall), fmtPct(r.f1)]), { dense: true, caption: 'Per scenario' }),
      h('div', { class: 'body small muted' }, 'Per-scenario precision is one-vs-rest. Coordinated accumulation recall is 83%: one evaded account scored 42.99 against 43.0.')),

    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Innocent groups')),
      table(['Group', ['Accounts', 'num'], ['Flat rule flagged', 'num'], ['Detector flagged', 'num']], d.innocent_groups.map((r) => [r.group, r.accounts, r.flat_rule, r.detector]), { dense: true, caption: 'False positives' })),

    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Ablation and robustness')),
      h('div', { class: 'body' }, c2.box, h('p', { class: 'small muted' }, 'Median F1 on eight extra synthetic worlds with varied parameters. Each variant has its own cut-off chosen on the validation seeds; nothing was tuned on these.')),
      table(['Variant', 'Cut-off', ['Validation F1', 'num'], 'Seed 42 P / R / F1', 'Robustness F1 min / med / max', 'Robustness recall min / med / max'],
        d.ablation.map((a) => [a.variant, a.cutoff, fmtPct(a.validation_f1), `${fmtPct(a.seed42.precision)} / ${fmtPct(a.seed42.recall)} / ${fmtPct(a.seed42.f1)}`,
          `${fmtPct(a.robustness_f1.min)} / ${fmtPct(a.robustness_f1.median)} / ${fmtPct(a.robustness_f1.max)}`, `${fmtPct(a.robustness_recall.min)} / ${fmtPct(a.robustness_recall.median)} / ${fmtPct(a.robustness_recall.max)}`]), { dense: true, caption: 'Ablation' }),
      h('div', { class: 'body' }, h('details', null, h('summary', null, 'Detector caveats'), h('ul', null, d.benchmark_caveats.map((c) => h('li', { class: 'small' }, c)))))),

    h('section', { class: 'card' }, h('header', null, h('h2', null, d.rerun.title), h('span', { class: 't-crit chip' }, 'differs from the table below')),
      table(['Case', 'Evaluation run', 'Re-run', ['Tokens', 'num'], ['Latency', 'num'], ['Linked members added', 'num']],
        d.rerun.rows.map((r) => [h('span', { class: 'mono' }, r.alert), r.before, h('strong', null, r.now), fmtN(r.tokens), `${r.latency_s.toFixed(1)} s`, r.linked]), { dense: true, caption: 'Re-run of injected cases' })),

    h('section', { class: 'card' }, h('header', null, h('h2', null, 'Agent evaluation (run of 2026-10-16)'), h('span', { class: 'chip t-neutral' }, d.agent_run)),
      table(['Metric', 'Result'], d.agent_metrics.map((m) => [m.metric, m.result]), { dense: true, caption: 'Agent metrics' }),
      h('div', { class: 'body' }, h('h3', { style: 'margin-bottom:8px' }, 'Per case')),
      table(['Alert', 'Kind', 'Decision', ['Tokens', 'num'], ['Latency', 'num'], 'Engine'],
        d.agent_cases.map((c) => [h('span', { class: 'mono' }, c.alert), c.kind.replaceAll('_', ' '), c.decision, fmtN(c.tokens), `${(c.latency_ms / 1000).toFixed(1)} s`, c.engines]), { dense: true, caption: 'Per case' }),
      h('div', { class: 'body' }, h('h3', { style: 'margin-bottom:8px' }, 'Adversarial test: 3 injected claims, 3 rejected, 0 leaked')),
      table(['Injected claim', 'Stopped at', 'Verdict'], d.adversarial.map((a) => [a.claim, a.stage, a.verdict]), { dense: true, caption: 'Adversarial claims' }),
      h('div', { class: 'body' }, h('details', { open: true }, h('summary', null, 'Agent caveats'), h('ul', null, d.agent_caveats.map((c) => h('li', { class: 'small' }, c)))))),

    h('p', { class: 'small muted' }, `Sources: ${d.source.join(', ')}. ${d.data_note}`)));
  return () => { window.removeEventListener('themechange', redraw); charts.forEach((c) => c.destroy()); };
}
