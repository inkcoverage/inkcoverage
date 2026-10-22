import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
import assert from 'node:assert/strict';

const source = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const helpers = source.slice(source.indexOf('function trackInk('), source.indexOf('function beginAnalysis('));
const single = source.slice(source.indexOf('async function analyzeCurrentPage()'), source.indexOf('btnAnalyze.addEventListener'));
const batch = source.slice(source.indexOf('async function runBatch('), source.indexOf('async function analyzeAllPages'));
function harness({ demo = false, denied = false, failPage = null, stale = false } = {}) {
  const sent = [], request = { id: 'private', page: 1 };
  const noop = () => {};
  const state = { isDemo: demo, pageCount: 2, pageResults: {}, resultScopes: {},
    pageDimensions: { 1: { widthPt: 100, heightPt: 100 }, 2: { widthPt: 100, heightPt: 100 } } };
  const env = { state, window: { siteAnalytics: { track: (name, params) => sent.push({ name, params }) } },
    beginAnalysis: () => denied ? null : request, analysisRequest: request, analysisIsCurrent: () => !stale,
    apiAnalyze: async (id, page) => { if (page === failPage) throw Object.assign(new Error('private.pdf'), { status: 422 }); return { privateResult: 123 }; },
    captureMeasuredGeometry: () => [0, 0, 100, 100], updateBatchResultsForPage: noop, finishAnalysis: noop,
    updateActionControls: noop, refreshAllowance: async () => ({ allowances: { analysis: { remaining: 30 }, preview: { remaining: 120 } } }),
    sourceTimeExpired: () => false, finishQueued: noop, renderBatch: noop, setActionError: noop, showToast: noop, t: x => x };
  vm.createContext(env); vm.runInContext(helpers + single + batch, env);
  return { env, sent };
}
test('single analysis emits request then completion only after a current result', async () => {
  const h = harness(); await h.env.analyzeCurrentPage();
  assert.deepEqual(h.sent.map(x => x.name), ['analysis_requested', 'analysis_completed']);
  assert.equal(JSON.stringify(h.sent).includes('private'), false);
});
test('rejected start and built-in demo never enter the funnel', async () => {
  for (const opts of [{ denied: true }, { demo: true }]) {
    const h = harness(opts); await h.env.analyzeCurrentPage(); assert.equal(h.sent.length, 0);
  }
});
test('failed or stale work cannot generate a completion event', async () => {
  const failed = harness({ failPage: 1 }); await failed.env.analyzeCurrentPage();
  assert.deepEqual(failed.sent.map(x => x.name), ['analysis_requested', 'analysis_failed']);
  assert.equal(failed.sent[1].params.failure_reason, 'processing');
  const stale = harness({ stale: true }); await stale.env.analyzeCurrentPage();
  assert.deepEqual(stale.sent.map(x => x.name), ['analysis_requested']);
});
test('two-page batch counts one request and one completed outcome', async () => {
  const h = harness(); await h.env.runBatch('all');
  assert.deepEqual(h.sent.map(x => x.name), ['analysis_requested', 'analysis_completed']);
  assert.equal(h.sent[1].params.result_status, 'complete');
});
test('partial batch preserves successful visual use and does not leak errors', async () => {
  const h = harness({ failPage: 2 }); await h.env.runBatch('all');
  assert.equal(h.sent.length, 2); assert.equal(h.sent[1].params.result_status, 'partial');
  assert.equal(JSON.stringify(h.sent).includes('private.pdf'), false);
});
