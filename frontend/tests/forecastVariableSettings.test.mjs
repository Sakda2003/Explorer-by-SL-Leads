import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readPredictors, togglePredictor, predictorAction, diagnosticsQuery, DECLARED_KEYS, META_KEYS } from '../src/forecastVariableSettings.ts';

test('default settings include declared and imported Meta predictors', () => {
  assert.deepEqual(readPredictors(null), [...DECLARED_KEYS, ...META_KEYS]);
  assert.deepEqual(readPredictors('bad json'), [...DECLARED_KEYS, ...META_KEYS]);
  assert.deepEqual(readPredictors('{}'), [...DECLARED_KEYS, ...META_KEYS]);
  assert.deepEqual(readPredictors('[]'), []);
});
test('Leads cannot be toggled or restored as a predictor; Meta leads is independent', () => {
  assert.deepEqual(readPredictors('["leads","meta_leads","cost_per_crm_lead","meta_leads","unknown"]'), ['meta_leads']);
  assert.deepEqual(togglePredictor([], 'leads'), []);
  assert.deepEqual(togglePredictor([], 'cost_per_crm_lead'), []);
  assert.deepEqual(togglePredictor([], 'meta_leads'), ['meta_leads']);
});
test('holiday off/on and persistent selections round trip', () => {
  const off = togglePredictor(DECLARED_KEYS, 'holiday_proximity');
  assert.equal(off.includes('holiday_proximity'), false);
  const on = togglePredictor(readPredictors(JSON.stringify(off)), 'holiday_proximity');
  assert.equal(on.includes('holiday_proximity'), true);
});
test('bulk actions preserve deliberate declared exclusions while clearing optional metrics', () => {
  assert.deepEqual(predictorAction([], 'all'), [...DECLARED_KEYS, ...META_KEYS]);
  assert.deepEqual(predictorAction(['spend', 'meta_leads'], 'clear'), ['spend']);
  assert.deepEqual(predictorAction([], 'defaults'), [...DECLARED_KEYS, ...META_KEYS]);
});
test('queries honor inclusive date window, multi-campaign scope, ad-set and empty model', () => {
  const params = new URLSearchParams(diagnosticsQuery('c1,c2', 'a1', '2026-06-01', '2026-06-15', ['spend', 'meta_leads', 'leads']));
  assert.equal(params.get('campaign_id'), 'c1,c2');
  assert.equal(params.get('ad_set_id'), 'a1');
  assert.equal(params.get('start_date'), '2026-06-01');
  assert.equal(params.get('end_date'), '2026-06-15');
  assert.equal(params.get('enabled'), 'spend,meta_leads');
  assert.equal(new URLSearchParams(diagnosticsQuery('', '', '', '', [])).get('enabled'), '');
});
