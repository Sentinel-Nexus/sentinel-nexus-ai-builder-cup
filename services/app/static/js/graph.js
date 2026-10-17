// Case graph (Cytoscape). Node and edge types differ by shape and line style, not only colour.
// Layout is deterministic (see graphx.js), nodes are sized by trading volume, hubs are always
// labelled and accounts when zoomed or hovered.
import { LAYOUTS, buildModel, mergePairs, nodeSize } from './graphx.js';
import { css } from './ui.js';

const cy = () => window.cytoscape;
const LABEL_ZOOM = 1.1;

function styleSheet() {
  const edge = css('g-edge');
  const hot = css('g-hot');
  const text = css('text');
  const surface = css('surface');
  return [
    { selector: 'node', style: { label: '', 'font-size': 12, 'min-zoomed-font-size': 0, color: text, 'text-valign': 'bottom', 'text-margin-y': 4, 'text-background-color': surface, 'text-background-opacity': 0.9, 'text-background-padding': 2, 'border-width': 2, width: 'data(size)', height: 'data(size)' } },
    { selector: 'node[type = "Account"]', style: { shape: 'ellipse', 'background-color': css('g-account'), 'border-color': css('g-account') } },
    { selector: 'node[type = "Device"]', style: { shape: 'diamond', 'background-color': surface, 'border-color': css('g-device'), 'border-width': 3, label: 'data(label)' } },
    { selector: 'node[type = "FundingSource"]', style: { shape: 'hexagon', 'background-color': surface, 'border-color': css('g-funding'), 'border-width': 3, label: 'data(label)' } },
    { selector: 'node.linked', style: { 'background-color': surface, 'border-style': 'dashed' } },
    { selector: 'node.lbl, node.hover, node:selected', style: { label: 'data(label)', 'z-index': 30 } },
    { selector: 'node.cited', style: { 'underlay-color': hot, 'underlay-padding': 6, 'underlay-opacity': 0.45, 'underlay-shape': 'ellipse' } },
    { selector: 'edge', style: { width: 1.5, 'line-color': edge, 'curve-style': 'bezier', 'target-arrow-color': edge, opacity: 0.8 } },
    { selector: 'edge[type = "UsesDevice"]', style: { 'line-style': 'solid', 'line-color': css('g-device'), 'target-arrow-color': css('g-device') } },
    { selector: 'edge[type = "FundedBy"]', style: { 'line-style': 'dashed', 'line-color': css('g-funding'), 'target-arrow-color': css('g-funding') } },
    { selector: 'edge[type = "TradedWith"]', style: { 'line-style': 'dotted', width: 2, 'target-arrow-shape': 'triangle', 'arrow-scale': 0.8 } },
    { selector: 'edge[type = "TradedPair"]', style: { 'line-style': 'dotted', width: 'data(width)', 'line-color': css('g-account'), opacity: 0.7 } },
    { selector: 'edge.evidence', style: { width: 3.5, opacity: 1 } },
    { selector: 'edge[type = "TradedPair"].evidence', style: { opacity: 1 } },
    { selector: '.dim', style: { opacity: 0.18 } },
    { selector: '.focus', style: { 'border-color': hot, 'line-color': hot, 'target-arrow-color': hot, 'border-width': 4, opacity: 1, 'z-index': 20 } },
    { selector: 'edge.focus', style: { width: 4.5 } },
    { selector: 'node.focus', style: { 'background-color': hot, label: 'data(label)' } },
    { selector: 'node:selected', style: { 'border-width': 4, 'border-color': hot } },
    { selector: '.gx-off', style: { display: 'none' } },
  ];
}

const KIND_OF = { UsesDevice: 'devices', FundedBy: 'funding', TradedWith: 'trades', TradedPair: 'trades' };

export function createGraph(container, graph, { onSelectNode, cited = [] } = {}) {
  if (!cy()) throw new Error('The graph library did not load (blocked or offline).');
  const model = buildModel(graph);
  const members = new Set(graph.members_alert);
  const evidence = new Set(graph.evidence_edge_ids);
  const citedSet = new Set(cited);
  const nodes = graph.nodes.map((n) => ({
    data: { id: n.id, type: n.type, label: n.key, size: n.type === 'Account' ? 20 : 30 },
    classes: [n.type === 'Account' && !members.has(n.key) ? 'linked' : '', n.type === 'Account' && citedSet.has(n.key) ? 'cited' : ''].join(' ').trim(),
  }));
  const ids = new Set(nodes.map((n) => n.data.id));
  const pairs = model.dense ? mergePairs(model) : [];
  const pairOf = new Map(pairs.flatMap((p) => p.ids.map((i) => [i, p.id])));
  const edges = model.edges.filter((e) => !(model.dense && e.type === 'TradedWith'))
    .map((e) => ({ data: { id: e.id, type: e.type, source: e.source, target: e.target }, classes: evidence.has(e.id) ? 'evidence' : '' }));
  for (const p of pairs) {
    edges.push({ data: { id: p.id, type: 'TradedPair', source: `Account:${p.a}`, target: `Account:${p.b}`, width: p.width, title: p.title }, classes: p.ids.some((i) => evidence.has(i)) ? 'evidence' : '' });
  }
  const pairTitle = new Map(pairs.map((p) => [p.id, p.title]));

  const inst = cy()({
    container,
    elements: [...nodes.filter((n) => ids.has(n.data.id)), ...edges],
    style: styleSheet(),
    layout: { name: 'grid' }, // replaced right below by the deterministic layout
    minZoom: 0.2,
    maxZoom: 3,
    wheelSensitivity: 0.3, // only reached with Ctrl or Cmd held (see wheelGuard)
    boxSelectionEnabled: false,
  });

  const state = { toggles: { devices: true, funding: true, trades: true }, layers: null, layout: 'clusters' };

  function applyVisibility() {
    const on = (kind) => state.toggles[kind] && (!state.layers || state.layers[kind]);
    inst.batch(() => {
      inst.edges().forEach((e) => e.toggleClass('gx-off', !on(KIND_OF[e.data('type')])));
      inst.nodes('[type = "Device"]').forEach((n) => n.toggleClass('gx-off', !!state.layers && !state.layers.devices));
      inst.nodes('[type = "FundingSource"]').forEach((n) => n.toggleClass('gx-off', !!state.layers && !state.layers.funding));
    });
  }

  function setLayout(name) {
    state.layout = LAYOUTS[name] ? name : 'clusters';
    const pos = LAYOUTS[state.layout](model);
    inst.layout({ name: 'preset', positions: (n) => pos[n.id()] || { x: 0, y: 0 }, fit: true, padding: 40, animate: false }).run();
  }
  setLayout('clusters');

  // tooltip for hover facts (counts only, never colours alone). It is measured and clamped so it
  // always stays inside the graph box: flipped above or left of the pointer near an edge.
  const tip = document.createElement('div');
  tip.className = 'gx-tip';
  tip.hidden = true;
  container.append(tip);
  const hideTip = () => { tip.hidden = true; };
  const showTip = (ev, text) => {
    const p = ev.renderedPosition || { x: 0, y: 0 };
    tip.textContent = text;
    tip.style.left = '0px';
    tip.style.top = '0px';
    tip.hidden = false;
    const w = tip.offsetWidth;
    const h = tip.offsetHeight;
    const W = container.clientWidth;
    const H = container.clientHeight;
    const clamp = (v, lo, hi) => Math.max(lo, Math.min(v, hi));
    let left = p.x + 12;
    let top = p.y + 12;
    if (left + w > W - 4) left = p.x - 12 - w;
    if (top + h > H - 4) top = p.y - 12 - h;
    tip.style.left = `${clamp(left, 4, Math.max(4, W - w - 4))}px`;
    tip.style.top = `${clamp(top, 4, Math.max(4, H - h - 4))}px`;
  };

  // A plain mouse wheel scrolls the page; only Ctrl or Cmd + wheel (and a touch pinch) zooms. The
  // zoom is done here, not by cytoscape, which ignores a wheel for 250 ms after the page scrolled
  // and would then let the browser zoom the whole page.
  const wheelGuard = (e) => {
    hideTip();
    if (!container.contains(e.target)) return;
    e.stopPropagation(); // cytoscape never sees a wheel event
    if (!(e.ctrlKey || e.metaKey)) return; // the page scrolls
    e.preventDefault();
    const rect = container.getBoundingClientRect();
    const dy = e.deltaY * (e.deltaMode === 1 ? 16 : 1);
    const level = Math.max(inst.minZoom(), Math.min(inst.maxZoom(), inst.zoom() * 1.002 ** -dy));
    inst.zoom({ level, renderedPosition: { x: e.clientX - rect.left, y: e.clientY - rect.top } });
  };
  const guardHost = container.parentElement || container;
  guardHost.addEventListener('wheel', wheelGuard, { capture: true, passive: false });
  const escHide = (e) => { if (e.key === 'Escape') hideTip(); };
  document.addEventListener('keydown', escHide);
  container.addEventListener('mouseleave', hideTip);
  // cytoscape caches the container's page offset; the walk-through bar or the page layout can move
  // the graph without a scroll or resize event, which would shift every hover and click
  container.addEventListener('mouseenter', () => inst.resize());
  inst.on('pan zoom', hideTip);

  // buttons and a hint inside the graph (keyboard reachable)
  const zoomBy = (f) => {
    const z = Math.max(inst.minZoom(), Math.min(inst.maxZoom(), inst.zoom() * f));
    inst.zoom({ level: z, renderedPosition: { x: container.clientWidth / 2, y: container.clientHeight / 2 } });
  };
  const mk = (label, text, id, fn) => {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'gx-zbtn';
    b.id = id;
    b.textContent = text;
    b.setAttribute('aria-label', label);
    b.title = label;
    b.addEventListener('click', fn);
    return b;
  };
  const tools = document.createElement('div');
  tools.className = 'gx-zoom no-print';
  tools.setAttribute('role', 'group');
  tools.setAttribute('aria-label', 'Zoom');
  tools.append(mk('Zoom in', '+', 'zoom-in-btn', () => zoomBy(1.25)), mk('Zoom out', '−', 'zoom-out-btn', () => zoomBy(0.8)),
    mk('Fit the whole graph in view', 'Fit', 'fit-btn', () => inst.fit(undefined, 40)));
  const hint = document.createElement('div');
  hint.className = 'gx-hint no-print';
  hint.textContent = 'Ctrl + scroll to zoom';
  container.append(tools, hint);

  // keep everything in view when the box changes size
  let raf = 0;
  const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => {
    cancelAnimationFrame(raf);
    raf = requestAnimationFrame(() => { inst.resize(); inst.fit(undefined, 40); hideTip(); });
  }) : null;
  if (ro) ro.observe(container);

  inst.on('tap', 'node', (ev) => onSelectNode && onSelectNode(ev.target.data()));
  inst.on('tap', (ev) => { if (ev.target === inst) { clearFocus(); onSelectNode && onSelectNode(null); } });
  inst.on('mouseover', 'node', (ev) => { ev.target.addClass('hover'); showTip(ev, `${ev.target.data('type') === 'FundingSource' ? 'Funding source' : ev.target.data('type') === 'Device' ? 'Shared device' : 'Account'} ${ev.target.data('label')}`); });
  inst.on('mouseout', 'node', (ev) => { ev.target.removeClass('hover'); hideTip(); });
  inst.on('mouseover', 'edge', (ev) => { const t = ev.target.data('title') || pairTitle.get(ev.target.id()); if (t) showTip(ev, t); });
  inst.on('mouseout', 'edge', hideTip);
  // a small group has room for every name; a big one labels accounts when zoomed in or hovered
  const small = model.accounts.length <= 12;
  const relabel = () => {
    inst.nodes('[type = "Account"]').toggleClass('lbl', small || inst.zoom() >= LABEL_ZOOM);
    // keep labels at least 11 px on screen whatever the zoom
    inst.nodes().style('font-size', Math.min(30, Math.max(12, 12 / inst.zoom())));
  };
  inst.on('zoom', relabel);
  relabel();

  function clearFocus() {
    inst.elements().removeClass('focus dim');
  }

  /** Map an evidence id from a report onto graph elements; null if it has no place in the graph. */
  function resolve(id) {
    if (!id) return null;
    if (inst.getElementById(id).nonempty()) return inst.getElementById(id);
    if (pairOf.has(id)) return inst.getElementById(pairOf.get(id));
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

  // read-only probe for the browser checks (scripts/graph_overlap_check.py)
  container.gxDebug = {
    zoom: () => inst.zoom(),
    positions: () => inst.nodes(':visible').map((n) => [n.renderedPosition('x'), n.renderedPosition('y')]),
    modelPositions: () => inst.nodes().map((n) => [n.position('x'), n.position('y')]),
    visibleEdges: () => inst.edges(':visible').length,
    focused: () => inst.elements('.focus').length,
    edgePoints: () => inst.edges(':visible').map((e) => [e.renderedMidpoint().x, e.renderedMidpoint().y]),
  };

  return {
    model,
    focus,
    clearFocus,
    resolves: (id) => !!resolve(id),
    selectAccount(key) {
      const n = inst.getElementById(`Account:${key}`);
      if (n.nonempty()) { inst.$(':selected').unselect(); n.select(); focus([`Account:${key}`]); }
    },
    selectNode(id) {
      const n = inst.getElementById(id);
      if (n.nonempty()) { inst.$(':selected').unselect(); n.select(); onSelectNode && onSelectNode(n.data()); }
    },
    neighbours(nodeId) {
      return inst.getElementById(nodeId).neighborhood('node[type = "Account"]').map((n) => n.data('label')).sort();
    },
    /** Edge kinds: 'devices' | 'funding' | 'trades'. */
    setEdgeVisible(kind, visible) { state.toggles[kind] = visible; applyVisibility(); },
    /** Story mode: pass {devices, funding, trades} to reveal layers, or null for the normal view. */
    setLayers(layers) { state.layers = layers; clearFocus(); applyVisibility(); },
    setLayout,
    get layout() { return state.layout; },
    /** Volumes in USD per account key; node diameter grows with the square root, clamped. */
    setVolumes(map) {
      const max = Math.max(0, ...Object.values(map));
      inst.batch(() => inst.nodes('[type = "Account"]').forEach((n) => n.data('size', nodeSize(map[n.data('label')] || 0, max))));
    },
    restyle() { inst.style(styleSheet()); },
    fit() { inst.fit(undefined, 40); },
    resize() { inst.resize(); inst.fit(undefined, 40); },
    zoomBy,
    destroy() {
      if (ro) ro.disconnect();
      cancelAnimationFrame(raf);
      guardHost.removeEventListener('wheel', wheelGuard, { capture: true });
      document.removeEventListener('keydown', escHide);
      inst.destroy();
    },
  };
}
