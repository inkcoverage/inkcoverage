import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/analytics.js', import.meta.url), 'utf8');
function browser({ site = 'labelestimate', host = site + '.app', search = '', choice, hash = '', savedVisit } = {}) {
  const elements = [];
  class Element {
    children = []; hidden = false;
    constructor(tag) { this.tag = tag; elements.push(this); }
    append(...items) { this.children.push(...items); }
    setAttribute() {} addEventListener() {} focus() {}
  }
  const local = new Map(), session = new Map(), listeners = {};
  if (choice) local.set(site + '_analytics_consent_v1', JSON.stringify(choice));
  if (savedVisit) session.set(site + '_analytics_visit_v1', JSON.stringify(savedVisit));
  const store = map => ({ getItem: key => map.get(key) || null, setItem: (key, value) => map.set(key, value) });
  const document = { currentScript: { dataset: { site } }, documentElement: { lang: 'en' }, readyState: 'complete',
    createElement: tag => new Element(tag), body: new Element('body'), head: new Element('head'),
    referrer: 'https://google.com/search?q=customer-secret', cookie: '_ga=old; _ga_ABC=old', activeElement: null };
  const context = { document, localStorage: store(local), sessionStorage: store(session), Date, URL, URLSearchParams,
    location: { hostname: host, origin: 'https://' + host, pathname: '/', hash, search, reload() { context.reloaded = true; } },
    MutationObserver: class { observe() {} }, addEventListener(name, fn) { listeners[name] = fn; } };
  context.window = context;
  vm.runInNewContext(source, context);
  const events = () => Array.from(context.dataLayer || []).filter(row => row[0] === 'event').map(row => [row[1], row[2]]);
  return { context, document, elements, events, local, session, listeners,
    accept() { elements.find(el => el.id === 'analytics-choice').children[2].children[0].onclick(); },
    reject() { elements.find(el => el.id === 'analytics-choice').children[2].children[1].onclick(); } };
}
test('no Google loader, cookies or queued activity before consent or after rejection', () => {
  const b = browser();
  assert.equal(b.document.head.children.length, 0);
  assert.equal(b.context.siteAnalytics.once('estimate_started'), false);
  b.reject();
  assert.equal(b.document.head.children.length, 0);
  assert.equal(b.events().length, 0);
  b.accept();
  assert.equal(b.document.head.children.length, 1);
  assert.deepEqual(b.events().map(row => row[0]), ['page_view']); // No retrospective edit.
});
test('one loader/page view, explicit welcome entry and one first edit per visit', () => {
  const b = browser(); b.accept(); b.accept(); b.context.siteAnalytics.page();
  b.context.location.hash = '#estimate'; b.listeners.hashchange(); b.listeners.hashchange();
  b.context.siteAnalytics.once('estimate_started', { press_mode: 'flexo' });
  b.context.siteAnalytics.once('estimate_started', { press_mode: 'digital' });
  assert.equal(b.document.head.children.length, 1);
  assert.deepEqual(b.events().map(row => row[0]), ['page_view', 'estimator_open', 'estimate_started']);
  assert.equal(b.events()[1][1].entry_type, 'welcome');
});
test('direct entry differs from welcome and sample/local/beta never load GA', () => {
  const direct = browser({ hash: '#estimate', choice: { value: 'granted', at: Date.now() } });
  assert.equal(direct.events()[1][1].entry_type, 'direct');
  for (const options of [{ search: '?example=print-repeat' }, { host: '127.0.0.1' }, { site: 'inkcoverage', host: 'beta.inkcoverage.app' }]) {
    const b = browser(options); b.accept(); b.context.siteAnalytics.track('generate_pdf');
    assert.equal(b.document.head.children.length, 0); assert.equal(b.events().length, 0);
  }
});
test('only agreed events and fixed metadata leave the privacy boundary', () => {
  const b = browser({ search: '?email=secret@example.com&job=confidential' }); b.accept();
  b.context.siteAnalytics.track('generate_pdf', { press_mode: 'flexo', customer: 'secret', template: 'private-job', page_location: 'evil', failure_reason: 'user-entered' });
  b.context.siteAnalytics.track('mode_switched');
  const params = b.events().at(-1)[1];
  assert.equal(params.press_mode, 'flexo'); assert.equal(params.customer, undefined); assert.equal(params.template, undefined);
  assert.equal(params.failure_reason, undefined); assert.equal(params.page_location, 'https://labelestimate.app/');
  assert.equal(params.page_referrer, 'https://google.com/');
  assert.equal(b.events().length, 2);
});
test('withdrawal disables measurement, deletes GA cookies and reloads', () => {
  const b = browser(); b.accept(); b.reject();
  assert.equal(b.context['ga-disable-G-CXH8S10TXP'], true);
  assert.equal(b.context.siteAnalytics.track('generate_pdf'), false);
  assert.equal(b.context.reloaded, true); assert.match(b.document.cookie, /Max-Age=0/);
});
test('expired consent asks again; accepted consent and visit dedup survive navigation', () => {
  const expired = browser({ choice: { value: 'granted', at: 0 } });
  assert.equal(expired.document.head.children.length, 0);
  const b = browser({ choice: { value: 'granted', at: Date.now() }, savedVisit: { at: Date.now(), events: ['estimate_started'] } });
  b.context.siteAnalytics.once('estimate_started');
  assert.deepEqual(b.events().map(row => row[0]), ['page_view']);
});
test('InkCoverage request and result events allow no PDF-derived parameters', () => {
  const b = browser({ site: 'inkcoverage' }); b.accept();
  b.context.siteAnalytics.track('analysis_requested', { analysis_mode: 'batch', request_kind: 'new', page_count: 20, grand_total: 98, filename: 'private.pdf' });
  b.context.siteAnalytics.track('ink_limit_completed', { result_status: 'complete', limit: 300, channels: ['Private spot'] });
  assert.equal(b.events()[1][1].analysis_mode, 'batch');
  assert.equal(b.events()[1][1].page_count, undefined); assert.equal(b.events()[2][1].channels, undefined);
});
