// Pure helpers for the case graph: plain-language caption, feature names, story steps, the
// reciprocal-trade merge and deterministic layouts. No DOM and no imports, so they can be tested
// on their own. Everything here is generated from the graph payload and the alert, never by an LLM.

export const DENSE_EDGES = 40; // above this, reciprocal trades are merged into one line per pair

export const FEATURE_NAMES = {
  fill_reciprocity: 'Accounts buying from and selling to each other',
  link_size: 'Size of the connected group',
  fresh_cluster: 'Newly created accounts funded together',
  link_density: 'How tightly accounts are connected',
  late_concentration: 'Heavy one-sided buying close to resolution',
  size_zscore: 'Unusually large trades',
  timing_zscore: 'Unusual trade timing',
  account_freshness: 'Recently created accounts',
};

/** Plain name of a detector feature; an unknown name is humanised ("some_new_x" -> "Some new x"). */
export function featureName(key) {
  if (FEATURE_NAMES[key]) return FEATURE_NAMES[key];
  const s = String(key || '').replace(/[_-]+/g, ' ').trim();
  return s ? s[0].toUpperCase() + s.slice(1) : 'Unnamed signal';
}

/** Top features as bars: [{key, name, value, pct}] sorted by value, pct relative to the largest. */
export function featureBars(topFeatures, limit = 4) {
  const rows = Object.entries(topFeatures || {}).map(([key, v]) => ({ key, name: featureName(key), value: Number(v) || 0 }))
    .sort((a, b) => b.value - a.value).slice(0, limit);
  const max = Math.max(...rows.map((r) => r.value), 0) || 1;
  return rows.map((r) => ({ ...r, pct: Math.round((r.value / max) * 100) }));
}

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** Facts counted from the payload. */
export function buildModel(gd) {
  const nodes = gd.nodes || [];
  const ids = new Set(nodes.map((n) => n.id));
  const edges = (gd.edges || []).filter((e) => ids.has(e.source) && ids.has(e.target));
  const accounts = nodes.filter((n) => n.type === 'Account').map((n) => n.key).sort();
  const hubs = nodes.filter((n) => n.type !== 'Account').sort((a, b) => (a.type + a.key).localeCompare(b.type + b.key));
  const byHub = new Map(hubs.map((n) => [n.id, []]));
  const hubsOf = new Map(accounts.map((a) => [a, []]));
  for (const e of edges) {
    if ((e.type === 'UsesDevice' || e.type === 'FundedBy') && byHub.has(e.target)) {
      const a = e.source.replace(/^Account:/, '');
      byHub.get(e.target).push(a);
      if (hubsOf.has(a)) hubsOf.get(a).push(e.target);
    }
  }
  const pairs = new Map(); // reciprocal trade pairs: "A|B" (sorted) -> directed links seen
  for (const e of edges.filter((x) => x.type === 'TradedWith')) {
    const a = e.source.replace(/^Account:/, '');
    const b = e.target.replace(/^Account:/, '');
    const k = [a, b].sort().join('|');
    const p = pairs.get(k) || { a: [a, b].sort()[0], b: [a, b].sort()[1], links: 0, ids: [] };
    p.links += 1;
    p.ids.push(e.id);
    pairs.set(k, p);
  }
  const shared = (type) => hubs.filter((h) => h.type === type && byHub.get(h.id).length >= 2);
  return {
    accounts, hubs, edges, byHub, hubsOf,
    members: new Set(gd.members_alert || []),
    tradePairs: [...pairs.values()].sort((x, y) => (x.a + x.b).localeCompare(y.a + y.b)),
    tradeEdges: edges.filter((e) => e.type === 'TradedWith').length,
    sharedDevices: shared('Device'),
    sharedFunding: shared('FundingSource'),
    dense: edges.length > DENSE_EDGES,
  };
}

/** One plain-English sentence for the case, from counts in the payload. */
export function caption(model, scenarioHint) {
  const n = model.accounts.length;
  const d = model.sharedDevices.length;
  const f = model.sharedFunding.length;
  const t = model.tradePairs.length;
  const dev = plural(d, 'shared device', 'shared devices');
  const fund = plural(f, 'shared funding source', 'shared funding sources');
  const acc = plural(n, 'account', 'accounts');
  if (t && d && f) return `${acc} trade back and forth with each other and connect through ${dev} and ${fund}. Real traders rarely do both.`;
  if (t && (d || f)) return `${acc} trade back and forth with each other and connect through ${d ? dev : fund}. Real traders rarely do both.`;
  if (t) return `${acc} trade back and forth with each other (${plural(t, 'pair', 'pairs')}), with no shared device or funding source in view.`;
  if (d && f) {
    const lead = scenarioHint === 'coordinated_accumulation' ? 'were flagged for buying the same market close to its end and' : 'are';
    return `${acc} ${lead} connected through ${dev} and ${fund}.`;
  }
  if (f) return `${acc} were funded from ${fund}${scenarioHint === 'mule_funding' ? ', a pattern seen when one source feeds many new accounts' : ''}.`;
  if (d) return `${acc} use ${dev}, so some of them may be run by the same person.`;
  return `${acc} were flagged together, but no shared device, funding source or back-and-forth trade shows in this view.`;
}

/** Four story steps, each revealing one more layer. `layers` says what is visible at that step. */
export function storySteps(model) {
  const n = model.accounts.length;
  const d = model.sharedDevices;
  const f = model.sharedFunding;
  const t = model.tradePairs.length;
  const covered = (hubs) => new Set(hubs.flatMap((h) => model.byHub.get(h.id))).size;
  return [
    { id: 'accounts', title: 'The accounts', layers: { devices: false, funding: false, trades: false },
      sentence: `Here are the ${plural(n, 'account', 'accounts')} in this group. On their own they are just names.` },
    { id: 'devices', title: 'Shared devices', layers: { devices: true, funding: false, trades: false },
      sentence: d.length
        ? `${plural(d.length, 'device is', 'devices are')} used by more than one account, covering ${covered(d)} of them. Accounts on the same device may be the same person.`
        : 'No device is shared by two or more of these accounts.' },
    { id: 'funding', title: 'Shared funding', layers: { devices: true, funding: true, trades: false },
      sentence: f.length
        ? `${plural(f.length, 'funding source feeds', 'funding sources feed')} ${covered(f)} of the accounts: their money came from the same place.`
        : 'No funding source is shared by two or more of these accounts.' },
    { id: 'trades', title: 'Back-and-forth trades', layers: { devices: true, funding: true, trades: true },
      sentence: t
        ? `${plural(t, 'pair', 'pairs')} of accounts bought from and sold to each other at matching times and prices.`
        : 'No back-and-forth trades between these accounts were found.' },
  ];
}

/** Merge reciprocal trade links into one line per pair; width grows with the links in the pair. */
export function mergePairs(model) {
  return model.tradePairs.map((p) => ({
    id: `Pair:${p.a}|${p.b}`, a: p.a, b: p.b, links: p.links, ids: p.ids,
    both: p.links > 1,
    title: p.links > 1 ? `${p.a} and ${p.b} traded both ways (${p.links} matched links)` : `${p.a} and ${p.b} traded one way (1 matched link)`,
    width: 1.5 + 1.5 * p.links,
  }));
}

/** Volume to node diameter, clamped to [18, 46]. */
export function nodeSize(usd, maxUsd) {
  if (!usd || !maxUsd) return 18;
  return Math.round(18 + 28 * Math.sqrt(Math.min(1, usd / maxUsd)));
}

/** Rows of the text-alternative table: one per account. */
export function listRows(model) {
  const key = (id) => id.replace(/^(Device|FundingSource):/, '');
  const partners = new Map(model.accounts.map((a) => [a, new Set()]));
  for (const p of model.tradePairs) { partners.get(p.a)?.add(p.b); partners.get(p.b)?.add(p.a); }
  return model.accounts.map((a) => {
    const hubs = model.hubsOf.get(a) || [];
    const dev = hubs.filter((h) => h.startsWith('Device:')).map(key);
    const fund = hubs.filter((h) => h.startsWith('FundingSource:')).map(key);
    return { account: a, role: model.members.has(a) ? 'Flagged member' : 'Linked, not flagged', devices: dev.join(', '), funding: fund.join(', '), tradesWith: [...partners.get(a)].sort().join(', ') };
  });
}

/** Plain facts about one account, from the graph alone. */
export function accountFacts(model, key) {
  const hubs = model.hubsOf.get(key) || [];
  const others = (id) => model.byHub.get(id).filter((x) => x !== key).length;
  const facts = [];
  for (const id of hubs) {
    const n = others(id);
    if (!n) continue;
    facts.push(id.startsWith('Device:') ? `Shares a device with ${plural(n, 'other account', 'other accounts')}.` : `Shares a funding source with ${plural(n, 'other account', 'other accounts')}.`);
  }
  const t = new Set(model.tradePairs.filter((p) => p.a === key || p.b === key).map((p) => (p.a === key ? p.b : p.a)));
  if (t.size) facts.push(`Traded back and forth with ${plural(t.size, 'other account', 'other accounts')} in this group.`);
  const role = model.members.has(key) ? 'A flagged member of this group.' : 'Linked to the group through a shared device or funding source, but not flagged itself.';
  return { role, facts };
}

export function hubFacts(model, hubId) {
  const n = (model.byHub.get(hubId) || []).length;
  return hubId.startsWith('Device:') ? `${plural(n, 'account uses', 'accounts use')} this device.` : `${plural(n, 'account was', 'accounts were')} funded from this source.`;
}

// ---------- layouts (deterministic; no randomness anywhere) ----------

const MIN_ACCOUNT = 46;
const MIN_HUB = 62;

function separate(pos, order, fixed, passes = 80) {
  for (let it = 0; it < passes; it += 1) {
    let moved = false;
    for (let i = 0; i < order.length; i += 1) {
      for (let j = i + 1; j < order.length; j += 1) {
        const a = order[i];
        const b = order[j];
        const need = (a.hub || b.hub) ? MIN_HUB : MIN_ACCOUNT;
        let dx = pos[b.id].x - pos[a.id].x;
        let dy = pos[b.id].y - pos[a.id].y;
        let dist = Math.hypot(dx, dy);
        if (dist >= need) continue;
        if (dist < 1e-6) { dx = Math.cos(i * 2.4); dy = Math.sin(i * 2.4); dist = 1; }
        const push = (need - dist) / 2 + 0.5;
        const ux = dx / dist;
        const uy = dy / dist;
        const fa = fixed.has(a.id) ? 0 : 1;
        const fb = fixed.has(b.id) ? 0 : 1;
        const w = fa + fb || 1;
        pos[a.id].x -= ux * push * 2 * (fa / w);
        pos[a.id].y -= uy * push * 2 * (fa / w);
        pos[b.id].x += ux * push * 2 * (fb / w);
        pos[b.id].y += uy * push * 2 * (fb / w);
        moved = true;
      }
    }
    if (!moved) break;
  }
}

/** "Clusters": every device or funding hub sits in the middle of its accounts. */
export function clusterLayout(model) {
  const pos = {};
  const hubs = model.hubs;
  const hr = hubs.length <= 1 ? 0 : 110 + 60 * hubs.length;
  hubs.forEach((h, i) => {
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, hubs.length);
    pos[h.id] = { x: Math.round(hr * Math.cos(ang)), y: Math.round(hr * Math.sin(ang)) };
  });
  const groups = new Map();
  for (const a of model.accounts) {
    const hs = [...(model.hubsOf.get(a) || [])].sort();
    const k = hs.join('|');
    if (!groups.has(k)) groups.set(k, { hs, list: [] });
    groups.get(k).list.push(a);
  }
  const loose = [];
  for (const { hs, list } of [...groups.values()].sort((x, y) => x.hs.join().localeCompare(y.hs.join()))) {
    if (!hs.length) { loose.push(...list); continue; }
    const cx = hs.reduce((s, id) => s + pos[id].x, 0) / hs.length;
    const cy = hs.reduce((s, id) => s + pos[id].y, 0) / hs.length;
    list.forEach((a, i) => {
      const r = list.length === 1 && hs.length > 1 ? 0 : 40 * Math.sqrt(i + 0.8);
      const ang = i * 2.39996 + (hs.length > 1 ? Math.PI / 2 : 0);
      pos[`Account:${a}`] = { x: Math.round(cx + r * Math.cos(ang)), y: Math.round(cy + r * Math.sin(ang)) };
    });
  }
  if (loose.length) {
    const ext = Math.max(160, ...Object.values(pos).map((p) => Math.hypot(p.x, p.y))) + 110;
    loose.forEach((a, i) => {
      const ang = (2 * Math.PI * i) / loose.length;
      pos[`Account:${a}`] = { x: Math.round(ext * Math.cos(ang)), y: Math.round(ext * Math.sin(ang)) };
    });
  }
  const order = [...hubs.map((h) => ({ id: h.id, hub: true })), ...model.accounts.map((a) => ({ id: `Account:${a}`, hub: false }))];
  separate(pos, order, new Set(hubs.map((h) => h.id)));
  for (const p of Object.values(pos)) { p.x = Math.round(p.x); p.y = Math.round(p.y); }
  return pos;
}

/** "Circle": accounts on a ring, hubs inside it. */
export function circleLayout(model) {
  const pos = {};
  const n = model.accounts.length;
  const R = Math.max(180, (n * 56) / (2 * Math.PI));
  model.accounts.forEach((a, i) => {
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, n);
    pos[`Account:${a}`] = { x: Math.round(R * Math.cos(ang)), y: Math.round(R * Math.sin(ang)) };
  });
  const hr = model.hubs.length <= 1 ? 0 : Math.min(R * 0.45, 40 + 36 * model.hubs.length);
  model.hubs.forEach((h, i) => {
    const ang = -Math.PI / 2 + (2 * Math.PI * i) / Math.max(1, model.hubs.length);
    pos[h.id] = { x: Math.round(hr * Math.cos(ang)), y: Math.round(hr * Math.sin(ang)) };
  });
  return pos;
}

export const LAYOUTS = { clusters: clusterLayout, circle: circleLayout };

/** Accounts a report cites: from account, link, trade-row and score ids (trade ids via the map). */
export function citedAccounts(evidenceIds, tradeAccounts, accountKeys) {
  const known = new Set(accountKeys);
  const out = new Set();
  const add = (k) => { if (known.has(k)) out.add(k); };
  for (const id of evidenceIds || []) {
    let m = /^Account:(.+)$/.exec(id);
    if (m) { add(m[1]); continue; }
    m = /^(?:UsesDevice|FundedBy|TradedWith):([^>]+)>(.+)$/.exec(id);
    if (m) { add(m[1]); if (id.startsWith('TradedWith')) add(m[2]); continue; }
    m = /^(?:TradedIn|Score):([^>]+)/.exec(id);
    if (m) { add(m[1]); continue; }
    if (tradeAccounts && tradeAccounts[id]) add(tradeAccounts[id]);
  }
  return [...out].sort();
}
