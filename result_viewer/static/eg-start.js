/* The electro_gui tab while electro_gui starts: ask the viewer to start it, wait until it answers, then go to
 * the unit link in this page's fragment (only a link to the configured electro_gui is followed). */
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const target = () => { try { return decodeURIComponent(location.hash.slice(1)); } catch (e) { return ''; } };
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  async function api(path, opts = {}) {
    const r = await fetch(path, Object.assign({headers: {'Content-Type': 'application/json'}, cache: 'no-store'}, opts));
    let body = null;
    try { body = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((body && body.error) || `${r.status} ${r.statusText}`);
    return body;
  }

  function fail(text, detail) {
    $('head').textContent = 'electro_gui did not start';
    $('msg').textContent = text;
    $('wait').hidden = true;
    if (detail) $('detail').innerHTML = detail;
  }

  async function main() {
    let cfg;
    try { cfg = await api('/api/config'); } catch (e) { return fail(`The viewer did not answer (${e.message}).`); }
    const base = cfg.electro_gui.url;
    const sameOrigin = link => { try { return new URL(link).origin === new URL(base).origin; } catch (e) { return false; } };
    if (!sameOrigin(target())) return fail('This is not a link to electro_gui.');
    const esc = s => String(s).replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
    const byHand = `To start it by hand:<br><code>${esc(cfg.electro_gui.launch)}</code>`;
    $('msg').textContent = `Starting it from analysis_imitation. This tab opens the unit as soon as electro_gui answers at ${base}.`;
    let start;
    try { start = await api('/api/electro_gui/start', {method: 'POST', body: '{}'}); } catch (e) { return fail(e.message, byHand); }
    if (start.error) return fail(start.error, byHand);
    if (start.log) $('detail').innerHTML = `Its output goes to <code>${esc(start.log)}</code>.`;
    const t0 = Date.now();
    while (Date.now() - t0 < 120000) {
      try {
        const s = await api('/api/electro_gui');
        if (s.running) { location.replace(target()); return; }
        const l = s.launch || {};
        if (l.exit_code != null) return fail(`electro_gui exited (code ${l.exit_code}) before answering.`, `See <code>${esc(l.log)}</code>. ${byHand}`);
      } catch (e) { /* the viewer may be restarting; keep waiting */ }
      $('elapsed').textContent = `${Math.round((Date.now() - t0) / 1000)} s`;
      await sleep(700);
    }
    fail('electro_gui did not answer within 2 minutes.', `See <code>${esc(start.log || '~/Library/Logs/electro_gui.log')}</code>. ${byHand}`);
  }

  main();
})();
