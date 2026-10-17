"""Idempotent GETs in the UI retry once after 400 ms on a network error or a 5xx."""

from __future__ import annotations

from tests.unit.jsharness import js_page  # noqa: F401

SCRIPT = """
async () => {
  const { fetchRetry, RETRY_DELAY_MS } = await import('/static/js/api.js');
  const run = async (responses) => {
    const sleeps = []; let calls = 0;
    const doFetch = async () => {
      const r = responses[Math.min(calls, responses.length - 1)]; calls += 1;
      if (r === 'net') throw new TypeError('Failed to fetch');
      return { status: r, ok: r < 400 };
    };
    let out;
    try { out = (await fetchRetry(doFetch, { sleep: async (ms) => { sleeps.push(ms); } })).status; }
    catch (e) { out = `error:${e.status}`; }
    return { out, calls, sleeps };
  };
  return {
    delay: RETRY_DELAY_MS,
    ok: await run([200]),
    s503then200: await run([503, 200]),
    netThen200: await run(['net', 200]),
    s503twice: await run([503, 503]),
    netTwice: await run(['net', 'net']),
    notFound: await run([404]),
  };
}
"""


def test_get_retries_once_on_network_error_or_5xx(js_page):  # noqa: F811
    r = js_page.evaluate(SCRIPT)
    assert r["delay"] == 400
    assert r["ok"] == {"out": 200, "calls": 1, "sleeps": []}
    assert r["s503then200"] == {"out": 200, "calls": 2, "sleeps": [400]}
    assert r["netThen200"] == {"out": 200, "calls": 2, "sleeps": [400]}
    assert r["s503twice"] == {"out": 503, "calls": 2, "sleeps": [400]}  # then the error state
    assert r["netTwice"] == {"out": "error:0", "calls": 2, "sleeps": [400]}
    assert r["notFound"] == {"out": 404, "calls": 1, "sleeps": []}  # a 4xx is not retried
