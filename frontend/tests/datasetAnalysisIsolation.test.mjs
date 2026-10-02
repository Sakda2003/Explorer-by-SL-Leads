import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const source = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const start = source.indexOf('function DatasetPage(');
const end = source.indexOf('\nfunction FollowupPage(', start);
const datasetPage = source.slice(start, end > start ? end : source.length);

test('Dataset diagnostics call only the versioned dataset-analysis endpoints', () => {
  assert.ok(datasetPage.includes('/dataset-analysis/ols'));
  assert.ok(datasetPage.includes('/dataset-analysis/correlation'));
  assert.ok(datasetPage.includes('/dataset-analysis/scopes'));
  assert.ok(datasetPage.includes('/dataset-analysis/status'));
  assert.equal(datasetPage.includes("api(`/ols-summary"), false);
  assert.equal(datasetPage.includes("api('/dashboard/insights')"), false);
});

test('Dataset source UI exposes upload, freshness and validation facts without history chrome', () => {
  assert.ok(datasetPage.includes('/dataset-analysis/preview'));
  assert.ok(datasetPage.includes('/dataset-analysis/confirm'));
  assert.ok(datasetPage.includes('Dataset diagnostics source'));
  assert.ok(datasetPage.includes('Source hash'));
  assert.ok(datasetPage.includes('diagnosticStatus.age_days'));
  assert.ok(datasetPage.includes('diagnosticStatus.freshness'));
  assert.ok(datasetPage.includes('Validation'));
  assert.ok(datasetPage.includes('diagnosticPreview.blocking_errors'));
  assert.ok(datasetPage.includes('diagnosticPreview.duplicate_keys'));
  assert.ok(datasetPage.includes('No active diagnostic dataset'));
  assert.equal(datasetPage.includes('/dataset-analysis/imports'), false);
  assert.equal(datasetPage.includes('Import history'), false);
  assert.equal(datasetPage.includes('Reactivate'), false);
  assert.equal(datasetPage.includes('Version ${diagnosticStatus.active.id}'), false);
});

test('Dataset diagnostic selectors and requests use diagnostic campaign and ad-set scope', () => {
  assert.ok(datasetPage.includes('setDiagnosticScopes(scopes)'));
  assert.ok(datasetPage.includes('diagnosticScopes.campaigns'));
  assert.ok(datasetPage.includes('diagnosticScopes.ad_sets'));
  assert.ok(datasetPage.includes('campaign_name=${encodeURIComponent(selectedCampaignId)}'));
  assert.ok(datasetPage.includes('ad_set_id=${encodeURIComponent(selectedAdSetId)}'));
});

test('Dataset displays OLS and correlation from the same import version', () => {
  assert.ok(datasetPage.includes('olsData.import_id !== correlationData.import_id'));
  assert.equal(datasetPage.includes('Version {correlation.import_id}'), false);
  assert.equal(datasetPage.includes('Version {ols.import_id}'), false);
});

test('Dataset displays both univariate and multivariate diagnostic OLS cards', () => {
  assert.ok(datasetPage.includes('Univariate and multivariate OLS'));
  assert.ok(datasetPage.includes('<OlsResultCards ols={ols} className="dataset-ols"'));
  assert.ok(datasetPage.includes('notebookStyle'));
  assert.equal(datasetPage.includes('view="multivariate"'), false);
});
