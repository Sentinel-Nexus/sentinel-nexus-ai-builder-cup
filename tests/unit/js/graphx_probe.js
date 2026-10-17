async () => {
  const X = await import('/static/js/graphx.js');
  const acc = (k) => ({ id: `Account:${k}`, type: 'Account', key: k });
  const hub = (t, k) => ({ id: `${t}:${k}`, type: t, key: k });
  const ring = (n, devices, trades) => {
    const keys = Array.from({ length: n }, (_, i) => `W${String(i + 1).padStart(3, '0')}`);
    const nodes = [...keys.map(acc), ...Array.from({ length: devices }, (_, i) => hub('Device', `D${i}`)), hub('FundingSource', 'F1')];
    const edges = [];
    keys.forEach((k, i) => {
      edges.push({ id: `UsesDevice:${k}>D${i % devices}`, type: 'UsesDevice', source: `Account:${k}`, target: `Device:D${i % devices}` });
      edges.push({ id: `FundedBy:${k}>F1`, type: 'FundedBy', source: `Account:${k}`, target: 'FundingSource:F1' });
    });
    if (trades) for (let i = 0; i < n; i++) for (let j = 0; j < n; j++) if (i !== j && (i + j) % 2 === 1) edges.push({ id: `TradedWith:${keys[i]}>${keys[j]}`, type: 'TradedWith', source: `Account:${keys[i]}`, target: `Account:${keys[j]}` });
    return { nodes, edges, members_alert: keys };
  };
  const minDist = (pos) => { const v = Object.values(pos); let m = 1e9; for (let i = 0; i < v.length; i++) for (let j = i + 1; j < v.length; j++) m = Math.min(m, Math.hypot(v[i].x - v[j].x, v[i].y - v[j].y)); return m; };
  const wash = X.buildModel(ring(8, 2, true));
  const mule = X.buildModel(ring(25, 3, false));
  const single = X.buildModel({ nodes: [acc('A'), acc('B'), hub('Device', 'D')], edges: [
    { id: 'UsesDevice:A>D', type: 'UsesDevice', source: 'Account:A', target: 'Device:D' },
    { id: 'UsesDevice:B>D', type: 'UsesDevice', source: 'Account:B', target: 'Device:D' }], members_alert: ['A'] });
  const l1 = X.clusterLayout(wash), l2 = X.clusterLayout(wash);
  return {
    wash: { caption: X.caption(wash, 'wash_trading'), n: wash.accounts.length, d: wash.sharedDevices.length, f: wash.sharedFunding.length, pairs: wash.tradePairs.length, dense: wash.dense, edges: wash.edges.length, merged: X.mergePairs(wash).length, steps: X.storySteps(wash).map((s) => s.sentence), layers: X.storySteps(wash).map((s) => s.layers) },
    mule: { caption: X.caption(mule, 'mule_funding'), steps: X.storySteps(mule).map((s) => s.sentence) },
    single: { caption: X.caption(single, 'x'), hub: X.hubFacts(single, 'Device:D'), acct: X.accountFacts(single, 'A'), linked: X.accountFacts(single, 'B').role },
    features: ['fill_reciprocity', 'link_size', 'fresh_cluster', 'link_density', 'some_new_signal', ''].map(X.featureName),
    bars: X.featureBars({ link_size: 12, fill_reciprocity: 13.81, x_y: 6 }, 2),
    sizes: [X.nodeSize(0, 100), X.nodeSize(100, 100), X.nodeSize(5000, 100), X.nodeSize(1, 100)],
    layout: { same: JSON.stringify(l1) === JSON.stringify(l2), washMin: minDist(l1), muleMin: minDist(X.clusterLayout(mule)), circleMin: minDist(X.circleLayout(mule)), circleWash: minDist(X.circleLayout(wash)), all: Object.keys(l1).length },
    list: X.listRows(wash)[0],
  };
}
