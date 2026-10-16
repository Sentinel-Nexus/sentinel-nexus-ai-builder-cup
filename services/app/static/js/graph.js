// Ring graph (Cytoscape). Node and edge types differ by shape and line style, not only colour.
import { css } from './ui.js';

const cy = () => window.cytoscape;

function styleSheet() {
  const edge = css('g-edge');
  const hot = css('g-hot');
  const text = css('text');
  const surface = css('surface');
  return [
    { selector: 'node', style: { label: 'data(label)', 'font-size': 10, color: text, 'text-valign': 'bottom', 'text-margin-y': 3, 'text-background-color': surface, 'text-background-opacity': 0.85, 'text-background-padding': 1, 'border-width': 2, width: 20, height: 20 } },
    { selector: 'node[type = "Account"]', style: { shape: 'ellipse', 'background-color': css('g-account'), 'border-color': css('g-account') } },
    { selector: 'node[type = "Device"]', style: { shape: 'diamond', 'background-color': surface, 'border-color': css('g-device'), width: 24, height: 24 } },
    { selector: 'node[type = "FundingSource"]', style: { shape: 'hexagon', 'background-color': surface, 'border-color': css('g-funding'), width: 26, height: 26 } },
    { selector: 'node.linked', style: { 'background-color': surface, 'border-style': 'dashed' } },
    { selector: 'edge', style: { width: 1.5, 'line-color': edge, 'curve-style': 'bezier', 'target-arrow-color': edge, opacity: 0.8 } },
    { selector: 'edge[type = "UsesDevice"]', style: { 'line-style': 'solid', 'line-color': css('g-device'), 'target-arrow-color': css('g-device') } },
    { selector: 'edge[type = "FundedBy"]', style: { 'line-style': 'dashed', 'line-color': css('g-funding'), 'target-arrow-color': css('g-funding') } },
    { selector: 'edge[type = "TradedWith"]', style: { 'line-style': 'dotted', width: 2, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8 } },
    { selector: 'edge.evidence', style: { width: 3.5, opacity: 1 } },
    { selector: '.dim', style: { opacity: 0.18 } },
    { selector: '.focus', style: { 'border-color': hot, 'line-color': hot, 'target-arrow-color': hot, 'border-width': 4, width: 4.5, opacity: 1, 'z-index': 20 } },
    { selector: 'node.focus', style: { 'background-color': hot, width: 28, height: 28 } },
    { selector: 'node:selected', style: { 'border-width': 4, 'border-color': hot } },
  ];
}

export function createGraph(container, graph, { onSelectNode } = {}) {
  if (!cy()) throw new Error('The graph library did not load (blocked or offline).');
  const members = new Set(graph.members_alert);
  const evidence = new Set(graph.evidence_edge_ids);
  const nodes = graph.nodes.map((n) => ({ data: { id: n.id, type: n.type, label: n.key }, classes: n.type === 'Account' && !members.has(n.key) ? 'linked' : '' }));
  const ids = new Set(nodes.map((n) => n.data.id));
  const edges = graph.edges.filter((e) => ids.has(e.source) && ids.has(e.target))
    .map((e) => ({ data: { id: e.id, type: e.type, source: e.source, target: e.target }, classes: evidence.has(e.id) ? 'evidence' : '' }));

  const inst = cy()({
    container,
    elements: [...nodes, ...edges],
    style: styleSheet(),
    layout: { name: 'cose', animate: false, nodeRepulsion: () => 12000, idealEdgeLength: () => 70, padding: 24, randomize: true },
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.3,
  });

  inst.on('tap', 'node', (ev) => onSelectNode && onSelectNode(ev.target.data()));
  inst.on('tap', (ev) => { if (ev.target === inst) { clearFocus(); onSelectNode && onSelectNode(null); } });

  function clearFocus() {
    inst.elements().removeClass('focus dim');
  }

  /** Map an evidence id from a report onto graph elements; null if it has no place in the graph. */
  function resolve(id) {
    if (!id) return null;
    if (inst.getElementById(id).nonempty()) return inst.getElementById(id);
    if (id.startsWith('Account:') || id.startsWith('Device:') || id.startsWith('FundingSource:')) return null;
    const m = /^(?:TradedIn|Score):([^>]+)/.exec(id);
    if (m) {
      const n = inst.getElementById(`Account:${m[1]}`);
      return n.nonempty() ? n : null;
    }
    if (id.startsWith('R_')) return inst.elements();
    return null;
  }

  function focus(idList) {
    let hit = inst.collection();
    for (const id of idList) {
      const el = resolve(id);
      if (el) hit = hit.union(el);
    }
    clearFocus();
    if (hit.empty()) return 0;
    // keep the endpoints of a focused edge visible too
    const keep = hit.union(hit.connectedNodes());
    inst.elements().not(keep).addClass('dim');
    hit.addClass('focus');
    inst.animate({ fit: { eles: keep, padding: 50 }, duration: 250 });
    return hit.length;
  }

  return {
    focus,
    clearFocus,
    resolves: (id) => !!resolve(id),
    selectAccount(key) {
      const n = inst.getElementById(`Account:${key}`);
      if (n.nonempty()) { inst.$(':selected').unselect(); n.select(); focus([`Account:${key}`]); }
    },
    neighbours(nodeId) {
      return inst.getElementById(nodeId).neighborhood('node[type = "Account"]').map((n) => n.data('label')).sort();
    },
    restyle() { inst.style(styleSheet()); },
    fit() { inst.fit(undefined, 30); },
    resize() { inst.resize(); inst.fit(undefined, 30); },
    destroy() { inst.destroy(); },
  };
}
