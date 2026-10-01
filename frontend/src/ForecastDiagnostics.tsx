import { useEffect, useId, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { RefreshCw } from 'lucide-react';
import { diagnosticsQuery, readPredictors } from './forecastVariableSettings';
import './forecastDiagnostics.css';

type Variable = {
  key: string; label: string; kind?: string; source?: string; description?: string;
  status: string; enabled: boolean; model_eligible?: boolean; default_enabled?: boolean;
  warning?: string; detail?: string; observed_days?: number; missing_days?: number;
};
type Matrix = { variables: Variable[]; matrix: (number | null)[][]; explanations: string[][] };
type Coefficient = { feature: string; term: string; coef: number; std_err: number; t: number | null; p_value: number | null; ci_low: number; ci_high: number; source: string };
type Model = { coefficients: Coefficient[]; r_squared: number; adjusted_r_squared: number; rmse: number; no_observations: number; f_statistic: number | null; f_p_value: number | null };
type Step = { key: string; label: string; order: number; round: number; before: number | null; after: number | null; gain: number | null; p_value: number | null; vif: number | null; sample_size: number; result: string; reason: string };
type Diagnostics = {
  scope: { date_start: string | null; date_end: string | null; observations: number };
  variables: Variable[]; declared: Matrix; expanded: Matrix; multivariate: Model | null;
  empty_reason: string; selection: { steps: Step[]; baseline_features: string[] };
};
type Props = {
  campaignId: string; adSetId: string; startDate: string; endDate: string; refreshKey: number;
  request: (path: string, init?: RequestInit) => Promise<any>;
};
const stat = (value: number | null | undefined, digits = 3) => typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : 'N/A';
const pValue = (value: number | null | undefined) => value != null && value < .001 ? '<0.001' : stat(value);
const statusClass = (status: string) => status.toLowerCase().replace(/ /g, '-');

export function ForecastDiagnostics({ campaignId, adSetId, startDate, endDate, refreshKey, request }: Props) {
  const id = useId();
  const enabled = readPredictors(null);
  const [result, setResult] = useState<{ query: string; data: Diagnostics } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [retry, setRetry] = useState(0);
  const [view, setView] = useState<'declared' | 'expanded'>('declared');
  const generation = useRef(0);
  const query = diagnosticsQuery(campaignId, adSetId, startDate, endDate, enabled);
  // Never present a stale model as current while a new toggle/scope request is in flight.
  const data = result?.query === query ? result.data : null;
  const busy = loading || !data;
  useEffect(() => {
    const controller = new AbortController();
    const current = ++generation.current;
    setLoading(true);
    setError('');
    request(`/forecast/diagnostics?${query}`, { signal: controller.signal })
      .then((value: Diagnostics) => {
        if (!controller.signal.aborted && current === generation.current) setResult({ query, data: value });
      })
      .catch((err: Error) => {
        if (!controller.signal.aborted && current === generation.current) setError(err.message || 'Unable to calculate diagnostics.');
      })
      .finally(() => { if (!controller.signal.aborted && current === generation.current) setLoading(false); });
    return () => controller.abort();
  }, [query, refreshKey, retry, request]);

  const matrix = data?.[view];
  return (
    <section className="forecast-diagnostics" aria-labelledby={`${id}-heading`}>
      {error ? <div className="fd-message" role="alert"><b>Diagnostics could not be updated</b><p>{error}</p><button onClick={() => setRetry(value => value + 1)}><RefreshCw size={14} />Retry</button></div>
      : busy ? <div className="fd-message" role="status" aria-live="polite">Recalculating the matrix and regression for your selection…</div>
      : <>
        <section className="fd-correlation" aria-labelledby={`${id}-correlation`}>
          <div className="fd-section-heading"><div><h4 id={`${id}-correlation`}>Correlation matrix</h4></div>
            <div className="fd-view" aria-label="Matrix view">{(['declared', 'expanded'] as const).map(option => <button key={option} aria-pressed={view === option} onClick={() => setView(option)}>{option === 'declared' ? 'Model' : 'Expanded'}</button>)}</div>
          </div>
          <div className="fd-scroll" tabIndex={0} role="region" aria-label="Correlation matrix">
            <table className="fd-matrix"><caption className="sr-only">{view} Pearson correlation matrix for CRM Leads and selected-scope variables</caption>
              <thead><tr><th scope="col">Variable</th>{matrix?.variables.map(variable => <th scope="col" key={variable.key} className={statusClass(variable.status)} title={variable.label}>{variable.label}</th>)}</tr></thead>
              <tbody>{matrix?.variables.map((row, ri) => <tr key={row.key}><th scope="row" className={statusClass(row.status)} title={row.label}>{row.label}</th>{matrix.variables.map((column, ci) => {
                const value = matrix.matrix[ri]?.[ci];
                const muted = [row, column].some(v => !v.enabled || v.status === 'Unavailable' || v.status === 'Excluded');
                const style: CSSProperties = value == null ? {} : { backgroundColor: `color-mix(in srgb, var(${value < 0 ? '--corr-cold' : '--corr-hot'}) ${Math.round(Math.abs(value) * (muted ? 13 : 42))}%, var(--surface))` };
                const detail = `${row.label} × ${column.label}: ${stat(value, 2)}. ${matrix.explanations[ri]?.[ci]}`;
                return <td key={column.key} style={style} className={muted ? 'fd-muted-cell' : ''}><span tabIndex={0} title={detail} aria-label={detail}>{stat(value, 2)}</span></td>;
              })}</tr>)}</tbody>
            </table>
          </div>
        </section>
      </>}
    </section>
  );
}
