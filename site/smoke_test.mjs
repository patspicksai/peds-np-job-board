// Smoke test for site/index.html.
// Loads the page in jsdom with fetch mocked to the fixture files in site/data/, a minimal Leaflet stub,
// and IntersectionObserver / matchMedia / clipboard stubs, then exercises the feed, filters, hash state,
// hospital ledger, detail panels and search.
//
// Usage: npm install jsdom (anywhere on NODE_PATH), then: node site/smoke_test.mjs
//   Options: --data <dir>  use another data directory (default: site/data next to this file)
//            --verbose     print each passing check
// Exit code 0 when every check passes.
import { readFileSync, existsSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const here = dirname(fileURLToPath(import.meta.url));
const args = process.argv.slice(2);
const verbose = args.includes('--verbose');
const dataDir = args.includes('--data') ? resolve(args[args.indexOf('--data') + 1]) : join(here, 'data');
const require = createRequire(import.meta.url);
let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = require('jsdom'));
} catch (e) {
  console.error('jsdom is not installed. Run `npm install jsdom` (or set NODE_PATH to a node_modules that has it).');
  process.exit(2);
}

const html = readFileSync(join(here, 'index.html'), 'utf8');
const hospDoc = JSON.parse(readFileSync(join(dataDir, 'hospitals.json'), 'utf8'));
const jobsDoc = JSON.parse(readFileSync(join(dataDir, 'jobs.json'), 'utf8'));

// ---------- expectations computed independently from the data ----------
const activeHosp = new Map(hospDoc.hospitals.filter(h => h.active !== false).map(h => [h.slug, h]));
const inactiveSlugs = new Set(hospDoc.hospitals.filter(h => h.active === false).map(h => h.slug));
const boardJobs = jobsDoc.jobs.filter(j => j.status !== 'closed' && !(inactiveSlugs.has(j.hospital_slug) && !activeHosp.has(j.hospital_slug)));
const ts = v => { const t = Date.parse(v); return isNaN(t) ? 0 : t; };

let failures = 0, passes = 0;
function check(name, cond, detail) {
  if (cond) { passes++; if (verbose) console.log('  ok   ' + name); }
  else { failures++; console.log('  FAIL ' + name + (detail !== undefined ? '  ->  ' + detail : '')); }
}

// ---------- minimal Leaflet stub ----------
function leafletStub(win) {
  const events = () => {
    const h = {};
    return {
      on(names, fn) { String(names).split(/\s+/).forEach(n => (h[n] = h[n] || []).push(fn)); return this; },
      once(name, fn) { const w = (...a) => { this.off(name, w); fn.apply(this, a); }; return this.on(name, w); },
      off(name, fn) { if (h[name]) h[name] = h[name].filter(f => f !== fn); return this; },
      fire(name, e) { (h[name] || []).slice().forEach(f => f.call(this, e || {})); return this; },
    };
  };
  const calls = { flyTo: [], setView: [], fitBounds: [], popups: [], tileLayers: [] };
  const L = { _calls: calls };
  L.map = function (id) {
    const m = Object.assign(events(), {
      _zoom: 5, _panes: {}, _layers: new Set(),
      attributionControl: { setPrefix() {}, addAttribution() {} },
      createPane(n) { const el = win.document.createElement('div'); this._panes[n] = el; return el; },
      getPane(n) { return this._panes[n]; },
      fitBounds(b, o) { calls.fitBounds.push(b); return this; },
      setView(ll, z) { calls.setView.push([ll, z]); this._zoom = z; this.fire('moveend'); return this; },
      flyTo(ll, z) { calls.flyTo.push([ll, z]); this._zoom = z; this.fire('moveend'); return this; },
      getZoom() { return this._zoom; },
      invalidateSize() { return this; },
      addLayer(l) { this._layers.add(l); return this; },
      removeLayer(l) { this._layers.delete(l); return this; },
      hasLayer(l) { return this._layers.has(l); },
      closePopup() { this._popup = null; this.fire('popupclose'); return this; },
    });
    L._map = m;
    return m;
  };
  const layer = (extra) => Object.assign(events(), { addTo(m) { m.addLayer(this); this._map = m; return this; } }, extra || {});
  L.tileLayer = (url, o) => { const t = layer({ url, options: o }); calls.tileLayers.push(url); return t; };
  L.svg = () => ({});
  L.polygon = (pts, o) => layer({ pts, options: o });
  L.divIcon = (o) => ({ options: o });
  L.latLngBounds = (pts) => ({ pts });
  L.control = { zoom: () => layer() };
  L.marker = (ll, o) => {
    const mk = layer({ _ll: ll, options: Object.assign({}, o), _icon: null });
    mk.setIcon = function (ic) { this.options.icon = ic; if (this._icon) this._icon.innerHTML = ic.options.html; return this; };
    mk.getElement = function () { return this._icon; };
    return mk;
  };
  L.popup = () => {
    const p = layer({
      _content: '', _ll: null,
      setLatLng(ll) { this._ll = ll; return this; },
      setContent(c) { this._content = c; if (this._el) this._el.innerHTML = c; return this; },
      openOn(m) {
        m.addLayer(this); m._popup = this; calls.popups.push(this._ll);
        if (!this._el) { this._el = win.document.createElement('div'); this._el.className = 'leaflet-popup-content'; win.document.getElementById('map').appendChild(this._el); }
        this._el.innerHTML = this._content; return this;
      },
    });
    return p;
  };
  L.markerClusterGroup = (o) => {
    const g = layer({ _m: new Set(), options: o });
    const mapEl = () => win.document.getElementById('map');
    g.addLayer = function (m) { this._m.add(m); if (!m._icon) { m._icon = win.document.createElement('div'); m._icon.className = 'leaflet-marker-icon ' + (m.options.icon.options.className || ''); m._icon.innerHTML = m.options.icon.options.html; m._icon.addEventListener('click', () => m.fire('click')); } mapEl().appendChild(m._icon); return this; };
    g.addLayers = function (a) { a.forEach(m => this.addLayer(m)); return this; };
    g.clearLayers = function () { this._m.forEach(m => m._icon && m._icon.remove()); this._m.clear(); return this; };
    g.hasLayer = function (m) { return this._m.has(m); };
    g.getVisibleParent = function (m) { return m; };
    g.zoomToShowLayer = function (m, cb) { cb && cb(); };
    g.refreshClusters = function () { return this; };
    return g;
  };
  return L;
}

// ---------- page loader ----------
async function loadPage({ hash = '', search = '', fetchMode = 'ok', noLeaflet = false } = {}) {
  const errors = [];
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => errors.push('jsdomError: ' + (e && e.message)));
  vc.on('error', (...a) => errors.push('console.error: ' + a.join(' ')));
  const fetched = [];
  const url = 'https://patspicksai.github.io/peds-np-job-board/' + search + (hash ? '#' + hash : '');
  const dom = new JSDOM(html, {
    url, runScripts: 'dangerously', pretendToBeVisual: true, virtualConsole: vc,
    beforeParse(win) {
      win.L = noLeaflet ? undefined : leafletStub(win);
      // jsdom does not load external scripts here; make sure the page script still sees our stub
      win.matchMedia = (q) => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
      win.IntersectionObserver = class { constructor(cb) { this.cb = cb; this.els = new Set(); win.__ios = (win.__ios || []).concat(this); } observe(el) { this.els.add(el); } unobserve(el) { this.els.delete(el); } disconnect() { this.els.clear(); } trigger(entries) { this.cb(entries); } };
      win.scrollTo = () => {};
      win.HTMLElement.prototype.scrollIntoView = function () {};
      win.HTMLElement.prototype.focus = function () { try { win.document.__lastFocus = this; } catch (e) {} };
      Object.defineProperty(win.navigator, 'clipboard', { value: { writeText: (t) => { win.__copied = t; return Promise.resolve(); } }, configurable: true });
      win.fetch = async (input) => {
        const u = String(input);
        fetched.push(u);
        const respond = (obj) => ({ ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(obj)) });
        const fail = () => ({ ok: false, status: 404, json: async () => ({}) });
        if (fetchMode === 'fail-all') throw new TypeError('network down');
        const isRel = u.startsWith('./data/') || u.startsWith('https://patspicksai.github.io/peds-np-job-board/data/');
        const isRaw = u.startsWith('https://raw.githubusercontent.com/patspicksai/peds-np-job-board/main/data/');
        const isOverride = u.startsWith('https://example.org/alt/');
        if (fetchMode === 'fail-relative' && isRel) return fail();
        if (fetchMode === 'override-only' && !isOverride) return fail();
        if (u.includes('en.wikipedia.org')) return respond({ title: 'Test', extract: 'Wikipedia summary text.', thumbnail: { source: 'https://upload.wikimedia.org/x.jpg' } });
        if (isRel || isRaw || isOverride) {
          if (u.includes('hospitals.json')) return respond(hospDoc);
          if (u.includes('jobs.json')) return respond(jobsDoc);
        }
        return fail();
      };
    },
  });
  const win = dom.window;
  const ready = await new Promise((res) => {
    const t0 = Date.now();
    (function poll() {
      if (win.NPJobs && win.NPJobs.ready) return res(true);
      if (win.NPJobs && win.NPJobs.error) return res(false);
      if (Date.now() - t0 > 5000) return res(false);
      setTimeout(poll, 10);
    })();
  });
  return { dom, win, doc: win.document, ready, errors, fetched };
}
const tick = (ms = 0) => new Promise(r => setTimeout(r, ms));
const cards = (doc) => Array.from(doc.querySelectorAll('#feed .job'));
const hashParams = (win) => {
  const h = win.location.hash.replace(/^#/, ''); const o = {};
  h.split('&').filter(Boolean).forEach(kv => { const i = kv.indexOf('='); o[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1)); });
  return o;
};
const click = (win, el) => el.dispatchEvent(new win.MouseEvent('click', { bubbles: true, cancelable: true }));
const key = (win, el, k) => el.dispatchEvent(new win.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));

// ======================================================================
console.log('Smoke test: ' + join(here, 'index.html'));
console.log('Data: ' + dataDir + ' (' + activeHosp.size + ' active hospitals, ' + jobsDoc.jobs.length + ' jobs, ' + boardJobs.length + ' on the board)');

// ---------- 1. default load ----------
{
  console.log('\n[default load]');
  const { win, doc, ready, errors, fetched } = await loadPage();
  check('page reports ready', ready, errors.join(' | '));
  check('no script errors', errors.length === 0, errors.join(' | '));
  check('fetches relative ./data/ first', fetched[0] && fetched[0].startsWith('./data/hospitals.json?v='), fetched[0]);
  check('cache busting param on data requests', fetched.slice(0, 2).every(u => /[?&]v=\d+/.test(u)), fetched.slice(0, 2).join(', '));
  const cs = cards(doc);
  const expected = Math.min(boardJobs.length, 100);
  check('feed card count equals active board jobs (default filters)', cs.length === expected, cs.length + ' vs ' + expected);
  check('closed jobs are not in the feed', !jobsDoc.jobs.filter(j => j.status === 'closed').some(j => doc.querySelector('[data-job-id="' + j.id + '"]')));
  const order = cs.map(c => c.getAttribute('data-job-id'));
  const sorted = boardJobs.slice().sort((a, b) => ts(b.first_seen_at) - ts(a.first_seen_at)).map(j => j.id).slice(0, expected);
  const firstSeenOrder = order.map(id => ts(jobsDoc.jobs.find(j => j.id === id).first_seen_at));
  check('feed is newest first by first_seen_at', firstSeenOrder.every((t, i) => i === 0 || firstSeenOrder[i - 1] >= t), firstSeenOrder.join(','));
  check('newest job is first', order[0] === sorted[0], order[0] + ' vs ' + sorted[0]);
  const now = Date.now();
  const newIds = boardJobs.filter(j => now - ts(j.first_seen_at) <= 2 * 864e5).map(j => j.id);
  const badgeIds = cs.filter(c => c.querySelector('.badge-new')).map(c => c.getAttribute('data-job-id'));
  check('"New" badge exactly on jobs first seen within 48h', JSON.stringify(newIds.sort()) === JSON.stringify(badgeIds.sort()), badgeIds.join(',') + ' vs ' + newIds.join(','));
  const staleIds = boardJobs.filter(j => j.status === 'stale').map(j => j.id);
  check('stale jobs are muted with a label', staleIds.every(id => { const c = doc.querySelector('[data-job-id="' + id + '"]'); return c && c.classList.contains('is-stale') && c.querySelector('.badge-stale'); }));
  const withSalary = boardJobs.find(j => j.salary_text);
  if (withSalary) check('salary shows on card when present', doc.querySelector('[data-job-id="' + withSalary.id + '"] .job-meta').textContent.includes(withSalary.salary_text));
  const withPosted = boardJobs.find(j => j.posted_at);
  if (withPosted) check('posted date shows on card when present', /Posted /.test(doc.querySelector('[data-job-id="' + withPosted.id + '"] .job-meta').textContent));
  check('cards are focusable with aria-labels', cs.every(c => c.getAttribute('tabindex') === '0' && c.getAttribute('aria-label')));
  check('relevance indicator on every card', cs.every(c => c.querySelector('.rel')));
  check('setting badge on every card', cs.every(c => c.querySelector('.badge-setting')));
  const stats = doc.getElementById('stats').textContent;
  check('header shows "Updated" relative time', /Updated /.test(stats), stats);
  check('header shows new this week count', /new this week/.test(stats), stats);
  const markerEls = doc.querySelectorAll('#map .leaflet-marker-icon');
  const withCoords = [...activeHosp.values()].filter(h => typeof h.lat === 'number').length;
  check('one map marker per active hospital', markerEls.length === withCoords, markerEls.length + ' vs ' + withCoords);
  const L = win.L;
  check('light Voyager tiles requested', L._calls.tileLayers.some(u => u.includes('voyager')), L._calls.tileLayers.join(','));
  check('fixture notice shown for _fixture data', !doc.getElementById('notice').hidden === !!(hospDoc._fixture || jobsDoc._fixture));
  check('default view leaves the hash empty', win.location.hash === '' || win.location.hash === '#', win.location.hash);

  // ---------- 2. region filter ----------
  console.log('\n[region filter]');
  const regionCounts = {};
  boardJobs.forEach(j => { const h = activeHosp.get(j.hospital_slug); if (h) regionCounts[h.region] = (regionCounts[h.region] || 0) + 1; });
  const regions = Object.keys(regionCounts).sort((a, b) => regionCounts[b] - regionCounts[a]);
  const r1 = regions[0], r2 = regions[1];
  doc.getElementById('filtersBtn').click();
  await tick();
  const box = doc.querySelector('#filterPop input[data-f="region"][value="' + r1 + '"]');
  check('filter panel lists region ' + r1, !!box);
  box.checked = true; box.dispatchEvent(new win.Event('change', { bubbles: true }));
  await tick();
  check('region filter reduces feed to ' + r1 + ' jobs', cards(doc).length === regionCounts[r1], cards(doc).length + ' vs ' + regionCounts[r1]);
  check('region filter reflected in hash', hashParams(win).region === r1, win.location.hash);
  check('region chip shown', [...doc.querySelectorAll('#chips .chip')].some(c => c.textContent.includes(r1)));
  const visibleMarkers = doc.querySelectorAll('#map .leaflet-marker-icon').length;
  const r1Hosp = [...activeHosp.values()].filter(h => h.region === r1).length;
  check('map markers follow the region filter', visibleMarkers === r1Hosp, visibleMarkers + ' vs ' + r1Hosp);
  if (r2) {
    const box2 = doc.querySelector('#filterPop input[data-f="region"][value="' + r2 + '"]');
    box2.checked = true; box2.dispatchEvent(new win.Event('change', { bubbles: true }));
    await tick();
    check('two regions combine (OR)', cards(doc).length === regionCounts[r1] + regionCounts[r2], cards(doc).length);
    check('hash lists both regions', (hashParams(win).region || '').split(',').length === 2, win.location.hash);
  }
  const chipBtn = [...doc.querySelectorAll('#chips .chip')].find(c => c.textContent.includes(r1));
  click(win, chipBtn);
  await tick();
  check('removing a chip removes that filter', cards(doc).length === (r2 ? regionCounts[r2] : Math.min(boardJobs.length, 100)), cards(doc).length);
  if (doc.getElementById('filterPop').hidden) { doc.getElementById('filtersBtn').click(); await tick(); }
  const clr = doc.querySelector('#filterPop [data-act="clear-all"]');
  check('filter panel has a Clear all button', !!clr);
  click(win, clr);
  await tick();
  check('filter panel stays open after Clear all', !doc.getElementById('filterPop').hidden);
  doc.getElementById('filtersBtn').click();
  await tick();
  check('filters button closes the panel', doc.getElementById('filterPop').hidden);
  check('clear all restores full feed', cards(doc).length === Math.min(boardJobs.length, 100), cards(doc).length);

  // hotspots
  const hotJobs = boardJobs.filter(j => { const h = activeHosp.get(j.hospital_slug); return h && h.hotspot; }).length;
  doc.getElementById('hotBtn').click();
  await tick();
  check('hotspots only toggle filters to hotspot hospitals', cards(doc).length === hotJobs, cards(doc).length + ' vs ' + hotJobs);
  check('hotspots in hash', hashParams(win).hot === '1', win.location.hash);
  doc.getElementById('hotBtn').click();
  await tick();

  // relevance
  const strong = boardJobs.filter(j => j.relevance >= 3).length;
  doc.querySelector('#relSeg [data-rel="3"]').click();
  await tick();
  check('relevance "Strong only"', cards(doc).length === strong, cards(doc).length + ' vs ' + strong);
  check('rel=3 in hash', hashParams(win).rel === '3', win.location.hash);
  doc.querySelector('#relSeg [data-rel="1"]').click();
  await tick();

  // ---------- 3. search ----------
  console.log('\n[search]');
  const q = doc.getElementById('q');
  const terms = ['picu', 'neonatal', 'charleston'];
  for (const term of terms) {
    q.value = term; q.dispatchEvent(new win.Event('input', { bubbles: true }));
    await tick(220);
    const exp = boardJobs.filter(j => {
      const h = activeHosp.get(j.hospital_slug);
      const hay = [j.title, h ? h.name : j.hospital_slug, h ? h.parent_hospital : '', j.department, j.description, j.summary, j.location_text, j.city].map(v => v == null ? '' : String(v)).join(' ').toLowerCase();
      return hay.includes(term);
    }).length;
    check('search "' + term + '" matches ' + exp + ' jobs', cards(doc).length === exp, cards(doc).length);
  }
  q.value = 'zzqxnotarealterm'; q.dispatchEvent(new win.Event('input', { bubbles: true }));
  await tick(220);
  check('search with no results shows empty state', cards(doc).length === 0 && !doc.getElementById('empty').hidden && /No jobs match/.test(doc.getElementById('empty').textContent));
  check('search is in the hash', hashParams(win).q === 'zzqxnotarealterm', win.location.hash);
  key(win, q, 'Escape');
  await tick();
  check('Escape in search clears it', cards(doc).length === Math.min(boardJobs.length, 100) && !('q' in hashParams(win)), cards(doc).length);

  // ---------- 4. job selection ----------
  console.log('\n[job detail]');
  const first = cards(doc)[0];
  const jid = first.getAttribute('data-job-id');
  const job = jobsDoc.jobs.find(j => j.id === jid);
  L._calls.flyTo.length = 0; L._calls.setView.length = 0; L._calls.popups.length = 0;
  click(win, first.querySelector('.job-title'));
  await tick(20);
  check('selecting a job sets job= in the hash', hashParams(win).job === jid, win.location.hash);
  const panel = doc.getElementById('panel');
  check('job detail panel opens', !panel.hidden && panel.textContent.includes(job.title));
  const view = panel.querySelector('a.btn-primary');
  check('"View posting" button links to the employer URL in a new tab', view && view.textContent.includes('View posting') && view.getAttribute('href') === job.url && view.getAttribute('target') === '_blank', view && view.outerHTML.slice(0, 160));
  const paras = panel.querySelectorAll('.desc p').length;
  const expParas = String(job.description).trim().split(/\n\s*\n/).length;
  check('description keeps paragraphs', paras === expParas, paras + ' vs ' + expParas);
  check('requirements bullets rendered', panel.querySelectorAll('.bullets li').length === job.requirements.length);
  check('tags rendered', panel.querySelectorAll('.tags .tag').length === job.tags.length);
  check('hospital link present in job detail', !!panel.querySelector('[data-act="hosp"][data-slug="' + job.hospital_slug + '"]'));
  check('map flew to the hospital', L._calls.flyTo.length + L._calls.setView.length > 0);
  check('map popup opened for the hospital', L._calls.popups.length > 0);
  const copyBtn = panel.querySelector('[data-act="copy"]');
  click(win, copyBtn);
  await tick(5);
  check('copy link copies the posting URL', win.__copied === job.url, win.__copied);
  key(win, doc.body, 'Escape');
  await tick();
  check('Escape closes the panel and clears job from hash', panel.hidden && !('job' in hashParams(win)), win.location.hash);

  // star
  const starBtn = cards(doc)[1].querySelector('.star');
  click(win, starBtn);
  await tick();
  check('star toggles on', starBtn.getAttribute('aria-pressed') === 'true');
  doc.getElementById('starBtn').click();
  await tick();
  check('"Starred only" shows only starred jobs', cards(doc).length === 1, cards(doc).length);
  doc.getElementById('starBtn').click();
  await tick();

  // marker click filters to hospital (toggle)
  console.log('\n[map interactions]');
  const slug = job.hospital_slug;
  const markerFor = (sl) => [...doc.querySelectorAll('#map .leaflet-marker-icon')].find(el => el.querySelector('.hm-dot[data-slug="' + sl + '"]'));
  const mEl = markerFor(slug) && markerFor(slug).textContent.trim() === String(boardJobs.filter(j => j.hospital_slug === slug).length) ? markerFor(slug) : null;
  check('marker for the selected hospital exists with its job count', !!mEl);
  const atCount = boardJobs.filter(j => j.hospital_slug === slug).length;
  if (mEl) {
    click(win, mEl);
    await tick();
    check('marker click filters feed to that hospital', cards(doc).length === atCount && cards(doc).every(c => c.getAttribute('data-slug') === slug), cards(doc).length + ' vs ' + atCount);
    check('marker click puts at= in the hash', hashParams(win).at === slug, win.location.hash);
    const pop = doc.querySelector('#map .leaflet-popup-content');
    check('popup shows hospital name and job count', pop && pop.textContent.includes(activeHosp.get(slug).name) && /\d+ (open|matching) job/.test(pop.textContent), pop && pop.textContent);
    const mEl2 = markerFor(slug);
    check('selected hospital marker gets the "at" style', !!(mEl2 && mEl2.querySelector('.is-at')));
    click(win, mEl2);
    await tick();
    check('second marker click clears the hospital filter (toggle)', cards(doc).length === Math.min(boardJobs.length, 100) && !('at' in hashParams(win)), cards(doc).length);
  }
  // hover highlights marker
  const c0 = cards(doc)[0];
  c0.dispatchEvent(new win.MouseEvent('mouseover', { bubbles: true }));
  await tick();
  const lit = doc.querySelector('#map .leaflet-marker-icon.is-lit');
  check('hovering a card highlights its marker', !!lit && lit === markerFor(c0.getAttribute('data-slug')));
  // IntersectionObserver scroll sync
  const io = (win.__ios || [])[0];
  if (io) {
    const target = cards(doc).find(c => c.getAttribute('data-slug') !== c0.getAttribute('data-slug')) || cards(doc)[1];
    c0.dispatchEvent(new win.MouseEvent('mouseleave', { bubbles: false }));
    doc.getElementById('feed').dispatchEvent(new win.MouseEvent('mouseleave', { bubbles: false }));
    io.trigger([{ target, isIntersecting: true }]);
    await tick();
    const litNow = doc.querySelector('#map .leaflet-marker-icon.is-lit');
    const tSlug = target.getAttribute('data-slug');
    check('scroll observer highlights the topmost visible card\'s marker', !!litNow && litNow === markerFor(tSlug), litNow ? litNow.innerHTML.slice(0, 120) : 'none lit');
  }

  // ---------- 5. hospital ledger ----------
  console.log('\n[hospital ledger]');
  doc.querySelector('.view-seg [data-view="hospitals"]').click();
  await tick();
  const rows = doc.querySelectorAll('#ledger .hrow');
  check('ledger renders all active hospitals', rows.length === activeHosp.size, rows.length + ' vs ' + activeHosp.size);
  check('view=hospitals in hash', hashParams(win).view === 'hospitals', win.location.hash);
  const groups = [...doc.querySelectorAll('#ledger .lg-group')];
  const hotFlags = groups.map(g => !!g.querySelector('.hot-tag'));
  check('hotspot regions are listed first', hotFlags.every((f, i) => i === 0 || hotFlags[i - 1] || !f), hotFlags.join(','));
  const anyRow = [...rows].find(r => r.getAttribute('data-slug') === slug);
  const careersA = anyRow.querySelector('a.ext-link');
  check('ledger row has careers link', careersA && careersA.getAttribute('href') === activeHosp.get(slug).careers_url);
  check('ledger row shows open job count', anyRow.querySelector('.hrow-count b').textContent === String(atCount));
  click(win, anyRow);
  await tick(20);
  check('clicking a hospital opens hospital detail', !doc.getElementById('panel').hidden && doc.getElementById('panel').textContent.includes(activeHosp.get(slug).name));
  check('hospital selection sets hosp= in hash', hashParams(win).hosp === slug, win.location.hash);
  const h = activeHosp.get(slug);
  const pnl = doc.getElementById('panel');
  if (h.photos.length) check('photo gallery shows the first photo', pnl.querySelector('.gal-main img') && pnl.querySelector('.gal-main img').getAttribute('src') === h.photos[0].url);
  check('facts grid present', pnl.querySelectorAll('.facts > div').length >= 9);
  check('open jobs listed in hospital detail', pnl.querySelectorAll('.mini-job').length === atCount);
  check('Google Maps link at the coordinates', !!pnl.querySelector('a[href*="google.com/maps/search/?api=1&query=' + h.lat + ',' + h.lng + '"]'));
  if (h.wikipedia_title) check('Wikipedia button present', !!pnl.querySelector('a[href^="https://en.wikipedia.org/wiki/"]'));
  // open lightbox
  const opener = pnl.querySelector('[data-act="lightbox"]');
  if (opener) {
    click(win, opener); await tick();
    check('photo enlarges in a lightbox', !doc.getElementById('lightbox').hidden);
    key(win, doc.body, 'Escape'); await tick();
    check('Escape closes the lightbox first', doc.getElementById('lightbox').hidden && !doc.getElementById('panel').hidden);
  }
  // from hospital to job and back
  const mini = pnl.querySelector('.mini-job');
  if (mini) {
    click(win, mini); await tick(20);
    check('open job from hospital detail', hashParams(win).job === mini.getAttribute('data-id'));
    const back = doc.querySelector('#panel [data-act="panel-back"]');
    check('back button returns to the hospital', !!back);
    if (back) { click(win, back); await tick(20); check('back restores hospital detail', hashParams(win).hosp === slug, win.location.hash); }
  }
  // ledger filter by type
  const types = [...new Set([...activeHosp.values()].map(x => x.hospital_type))];
  doc.getElementById('filtersBtn').click(); await tick();
  const tBox = doc.querySelector('#filterPop input[data-f="type"][value="' + types[0] + '"]');
  tBox.checked = true; tBox.dispatchEvent(new win.Event('change', { bubbles: true })); await tick();
  const expT = [...activeHosp.values()].filter(x => x.hospital_type === types[0]).length;
  check('hospital type filter narrows ledger', doc.querySelectorAll('#ledger .hrow').length === expT, doc.querySelectorAll('#ledger .hrow').length + ' vs ' + expT);
  win.close();
}

// ---------- 6. hash restores state on load ----------
{
  console.log('\n[shareable hash]');
  const regionCounts = {};
  boardJobs.forEach(j => { const h = activeHosp.get(j.hospital_slug); if (h) regionCounts[h.region] = (regionCounts[h.region] || 0) + 1; });
  const regs = Object.keys(regionCounts).slice(0, 2);
  const target = boardJobs.find(j => j.relevance === 3 && regs.includes(activeHosp.get(j.hospital_slug).region)) || boardJobs[0];
  const hash = 'view=jobs&region=' + regs.map(encodeURIComponent).join(',') + '&rel=3&job=' + target.id;
  const { win, doc, ready, errors } = await loadPage({ hash });
  check('loads with a shared hash', ready && !errors.length, errors.join(' | '));
  const exp = boardJobs.filter(j => regs.includes(activeHosp.get(j.hospital_slug).region) && j.relevance >= 3).length;
  check('region + relevance from hash applied', cards(doc).length === exp, cards(doc).length + ' vs ' + exp);
  check('job from hash opens its detail', !doc.getElementById('panel').hidden && doc.getElementById('panel').textContent.includes(target.title));
  check('hash survives round trip', hashParams(win).job === target.id && hashParams(win).rel === '3', win.location.hash);
  win.close();
}

// ---------- 7. data fallbacks ----------
{
  console.log('\n[data loading fallbacks]');
  const a = await loadPage({ fetchMode: 'fail-relative' });
  check('falls back to raw.githubusercontent.com when ./data fails', a.ready && a.fetched.some(u => u.startsWith('https://raw.githubusercontent.com/patspicksai/peds-np-job-board/main/data/jobs.json')), a.fetched.join(', '));
  check('fallback renders the feed', cards(a.doc).length === Math.min(boardJobs.length, 100));
  a.win.close();
  const b = await loadPage({ search: '?data=https://example.org/alt', fetchMode: 'override-only' });
  check('?data= override is tried first and used', b.ready && b.fetched[0].startsWith('https://example.org/alt/hospitals.json'), b.fetched[0]);
  b.win.close();
  const c = await loadPage({ fetchMode: 'fail-all' });
  await tick(50);
  const empty = c.doc.getElementById('empty');
  check('clear error state when no data source loads', !empty.hidden && /did not load/.test(empty.textContent) && empty.querySelector('[data-act="retry"]'), empty.textContent.slice(0, 120));
  c.win.close();
  const d = await loadPage({ noLeaflet: true });
  check('page still works without Leaflet (map message shown)', d.ready && cards(d.doc).length > 0 && !d.doc.getElementById('mapMsg').hidden, d.errors.join(' | '));
  d.win.close();
}

// ---------- 7b. Wikipedia fallback when a hospital has no photos ----------
{
  console.log('\n[wikipedia fallback]');
  const target = hospDoc.hospitals.find(h => h.active !== false && h.wikipedia_title);
  if (target) {
    const savedPhotos = target.photos;
    target.photos = [];
    const { win, doc, ready, errors, fetched } = await loadPage({ hash: 'view=hospitals&hosp=' + target.slug });
    await tick(50);
    const pnl = doc.getElementById('panel');
    check('hospital from hash opens with no photos', ready && !pnl.hidden && pnl.textContent.includes(target.name), errors.join(' | '));
    check('fetches the Wikipedia REST summary for wikipedia_title', fetched.some(u => u.startsWith('https://en.wikipedia.org/api/rest_v1/page/summary/' + encodeURIComponent(target.wikipedia_title.replace(/ /g, '_')))), fetched.filter(u => u.includes('wikipedia')).join(', '));
    check('shows the Wikipedia thumbnail and extract', !!pnl.querySelector('#wikiSlot img') && /Wikipedia summary text/.test(pnl.textContent));
    win.close();
    target.photos = savedPhotos;
  }
  const bare = await loadPage({ hash: 'hospital-' + hospDoc.hospitals[0].slug });
  check('bare #hospital-<slug> token opens that hospital', bare.ready && !bare.doc.getElementById('panel').hidden && bare.doc.getElementById('panel').textContent.includes(hospDoc.hospitals[0].name));
  bare.win.close();
}

// ---------- 8. performance: 2,000 jobs, 300 hospitals ----------
{
  console.log('\n[scale]');
  const big = { hospitals: [], jobs: [] };
  const regionsList = ['Triangle', 'Charlotte', 'Boston metro', 'NYC metro', 'Portland ME', 'Charleston SC'];
  for (let i = 0; i < 300; i++) {
    const base = hospDoc.hospitals[i % hospDoc.hospitals.length];
    big.hospitals.push(Object.assign({}, base, { slug: base.slug + '-' + i, name: base.name + ' ' + i, lat: base.lat + (i % 17) * 0.01, lng: base.lng + (i % 13) * 0.01, region: regionsList[i % regionsList.length], hotspot: ['Triangle', 'Charlotte', 'Portland ME'].includes(regionsList[i % regionsList.length]) }));
  }
  for (let i = 0; i < 2000; i++) {
    const base = jobsDoc.jobs[i % jobsDoc.jobs.length];
    big.jobs.push(Object.assign({}, base, { id: 'big' + i, status: 'active', hospital_slug: big.hospitals[i % 300].slug, first_seen_at: new Date(Date.now() - i * 36e5).toISOString() }));
  }
  const saved = { h: hospDoc.hospitals, j: jobsDoc.jobs };
  hospDoc.hospitals = big.hospitals; jobsDoc.jobs = big.jobs;
  const t0 = Date.now();
  const { win, doc, ready, errors } = await loadPage();
  const ms = Date.now() - t0;
  check('2,000 jobs load', ready && !errors.length, errors.join(' | '));
  check('feed renders the first chunk of 100', cards(doc).length === 100, cards(doc).length);
  check('sentinel visible with more to load', !doc.getElementById('sentinel').hidden);
  const sio = (win.__ios || [])[1];
  if (sio) { sio.trigger([{ target: doc.getElementById('sentinel'), isIntersecting: true }]); await tick(); }
  check('sentinel loads the next 100', cards(doc).length === 200, cards(doc).length);
  doc.getElementById('moreBtn').click(); await tick();
  check('"Show more" button loads another chunk', cards(doc).length === 300, cards(doc).length);
  check('300 markers on the map', doc.querySelectorAll('#map .leaflet-marker-icon').length === 300);
  console.log('  info load + first render of 2,000 jobs in jsdom: ' + ms + ' ms');
  win.close();
  hospDoc.hospitals = saved.h; jobsDoc.jobs = saved.j;
}

console.log('\n' + (failures ? 'FAILED: ' + failures + ' of ' + (passes + failures) + ' checks' : 'PASSED: all ' + passes + ' checks'));
process.exit(failures ? 1 : 0);
