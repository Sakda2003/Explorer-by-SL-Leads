// Only predictor preferences are persisted. The outcome is not a selectable variable.
export const SETTINGS_KEY = 'leadlens.forecast.predictors.v1';
export const DECLARED_KEYS = ['spend', 'holiday_proximity', 'days_since_adset_started', 'frequency', 'ad_change_recency', 'ad_set_change_recency', 'day_of_week'];
export const META_KEYS = ['messaging_conversations_started', 'cost_per_messaging_conversation', 'reach', 'impressions', 'ctr_all', 'cpm', 'link_clicks', 'clicks_all', 'source_leads', 'cost_per_lead', 'meta_leads'];
const allowed = new Set([...DECLARED_KEYS, ...META_KEYS]);

export function readPredictors(serialized: string | null): string[] {
  try {
    const value: unknown = serialized === null ? null : JSON.parse(serialized);
    if (!Array.isArray(value)) return [...DECLARED_KEYS, ...META_KEYS];
    return [...new Set(value.filter((key): key is string => typeof key === 'string' && allowed.has(key)))];
  } catch { return [...DECLARED_KEYS, ...META_KEYS]; }
}

export function togglePredictor(enabled: string[], key: string): string[] {
  if (!allowed.has(key)) return enabled;
  return enabled.includes(key) ? enabled.filter(item => item !== key) : [...enabled, key];
}

export function predictorAction(enabled: string[], action: 'all' | 'clear' | 'defaults'): string[] {
  if (action === 'all') return [...DECLARED_KEYS, ...META_KEYS];
  if (action === 'clear') return enabled.filter(key => DECLARED_KEYS.includes(key));
  return [...DECLARED_KEYS, ...META_KEYS];
}

export function diagnosticsQuery(campaign: string, adSet: string, start: string, end: string, enabled: string[]): string {
  const query = new URLSearchParams({ enabled: enabled.filter(key => allowed.has(key)).join(',') });
  if (campaign) query.set('campaign_id', campaign);
  if (adSet) query.set('ad_set_id', adSet);
  if (start) query.set('start_date', start);
  if (end) query.set('end_date', end);
  return query.toString();
}
