// Page-side audit used by scripts/browser_check.py: inside the viewport and uncovered.
(el) => {
  const out = [];
  const prev = el.style.pointerEvents;
  el.style.pointerEvents = 'auto'; // tooltips ignore the pointer; elementFromPoint must see them
  const b = el.getBoundingClientRect();
  const vw = document.documentElement.clientWidth, vh = innerHeight;
  if (b.width === 0 || b.height === 0) out.push('has no size');
  if (b.left < -0.5 || b.top < -0.5 || b.right > vw + 0.5 || b.bottom > vh + 0.5) {
    out.push(`outside the viewport (${Math.round(b.left)},${Math.round(b.top)},${Math.round(b.right)},${Math.round(b.bottom)} in ${vw}x${vh})`);
  }
  const pts = [['centre', b.left + b.width / 2, b.top + b.height / 2], ['top-left', b.left + 4, b.top + 4],
    ['top-right', b.right - 4, b.top + 4], ['bottom-left', b.left + 4, b.bottom - 4], ['bottom-right', b.right - 4, b.bottom - 4]];
  for (const [name, x, y] of pts) {
    if (x < 0 || y < 0 || x >= vw || y >= vh) continue; // already reported as outside
    const top = document.elementFromPoint(x, y);
    if (!top || !(el === top || el.contains(top))) out.push(`covered at ${name} by ${top ? top.tagName + '.' + top.className : 'nothing'}`);
  }
  el.style.pointerEvents = prev;
  return out;
}
