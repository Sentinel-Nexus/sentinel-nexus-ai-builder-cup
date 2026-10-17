// Page-side helpers for scripts/graph_overlap_check.py (run through page.evaluate).
({
  rects() {
    const box = (e) => { const b = e.getBoundingClientRect(); return { x: b.x, y: b.y, r: b.right, b: b.bottom }; };
    const g = document.querySelector('#graph');
    const t = document.querySelector('.gx-tip');
    return { graph: box(g), tip: t && !t.hidden ? box(t) : null };
  },
  // elements other than the legend that paint over a legend item
  legendCovered() {
    const leg = document.querySelector('.gx-legend');
    if (!leg) return ['no legend'];
    const bad = [];
    leg.querySelectorAll('.gx-leg-item, .gx-leg-note, strong').forEach((it) => {
      const b = it.getBoundingClientRect();
      if (b.width === 0 || b.bottom < 0 || b.top > innerHeight) return;
      const x = Math.min(innerWidth - 1, Math.max(0, b.x + b.width / 2));
      const y = Math.min(innerHeight - 1, Math.max(0, b.y + b.height / 2));
      const top = document.elementFromPoint(x, y);
      if (top && !leg.contains(top)) bad.push(`${it.textContent.trim().slice(0, 24)} covered by ${top.tagName}.${top.className}`);
    });
    return bad;
  },
  gx() { return document.querySelector('#graph').gxDebug; },
})
