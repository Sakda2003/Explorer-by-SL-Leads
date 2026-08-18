import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { CSSProperties, FormEvent, MouseEvent as ReactMouseEvent } from 'react';
import explorerLogo from './assets/explorer-logo.png';
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, LabelList, Line, ReferenceArea, ReferenceLine, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from 'recharts';
import {
 Activity,
 AlertTriangle,
 ArrowDown,
 ArrowUp,
 ArrowUpDown,
 BarChart3,
 CalendarDays,
 Check,
 ChevronDown,
 Columns3,
 Copy,
 ChevronLeft,
 ChevronRight,
 CircleCheckBig,
 CircleX,
 Database,
 Download,
 Filter,
 Gauge,
 History,
 Info,
 Layers3,
 Lock,
 LogOut,
 Megaphone,
 Menu,
 Moon,
 Pencil,
 Plus,
 RefreshCw,
 Rows3,
 Search,
 Settings,
 SlidersHorizontal,
 Sun,
 Trash2,
 TrendingDown,
 TrendingUp,
 Upload,
 UserCheck,
 UserPlus,
 X,
} from 'lucide-react';

const API = '/api';
type Page = 'Forecast' | 'Optimization' | 'Upload Data' | 'Data History' | 'Dataset' | 'Settings';
const AUTH_STORAGE_KEY = 'leadlens-basic-auth';
let apiAuthHeader = '';

const setApiAuthHeader = (value: string) => {
 apiAuthHeader = value;
};

// Mirrors CHANGE_SCOPES in backend/core.py. Change TYPE was removed from the model on
// 2026-08-11, so a change is now just a scope and a date -- there is no category to pick.
const CHANGE_SCOPES: { key: 'ad_set' | 'ad'; label: string }[] = [
 { key: 'ad_set', label: 'Ad set change' },
 { key: 'ad', label: 'Ad change' },
];
// Drives the sliding tab indicator in ChangeEventButton -- position is this array's index,
// not a separately tracked number, so the indicator can never drift out of sync with which
// tab is actually showing.
const CHANGE_TAB_ORDER: Array<'ad_set' | 'ad' | 'start_date'> = ['ad_set', 'ad', 'start_date'];

// Grouped rather than one flat list of six: the pages fall into three genuinely
// different jobs -- reading the model's output, feeding it, and configuring it -- and
// the group labels make that split legible instead of implied by ordering.
const navGroups: { label: string; items: [Page, any][] }[] = [
 {
  label: 'Analyze',
  items: [
   ['Forecast', BarChart3],
   ['Optimization', TrendingUp],
  ],
 },
 {
  label: 'Data',
  items: [
   ['Upload Data', Upload],
   ['Data History', History],
   ['Dataset', Database],
  ],
 },
 {
  label: 'System',
  items: [['Settings', Settings]],
 },
];

type Theme = 'dark' | 'light';

const readTheme = (): Theme => {
 const stored = localStorage.getItem('leadlens-theme');
 if (stored === 'light' || stored === 'dark') return stored;
 return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
};

// Mirrors the pre-paint bootstrap in index.html -- that script owns the FIRST paint,
// this owns every change after it. Both write the same key and the same attribute.
function useTheme(): [Theme, () => void] {
 const [theme, setTheme] = useState<Theme>(() => readTheme());
 useEffect(() => {
  document.documentElement.setAttribute('data-theme', theme);
  localStorage.setItem('leadlens-theme', theme);
  document.querySelector('meta[name="theme-color"]')
   ?.setAttribute('content', theme === 'light' ? '#F4F5F7' : '#0C0D0F');
 }, [theme]);
 return [theme, () => setTheme((current) => (current === 'dark' ? 'light' : 'dark'))];
}

const fmt = (value: any) => value == null
 ? '-'
 : new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(value);

const money = (value: any) => value == null
 ? '-'
 : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value);

const plural = (count: number, noun: string, suffix = 's') => `${fmt(count)} ${noun}${count === 1 ? '' : suffix}`;

// The Dataset page's raw board shows the stored identifier verbatim -- `0_3_days`,
// `no_recent_change` -- rather than prose. Its headers are already the raw column names, so
// a value reading "No Recent Change" there can't be matched against the model's own bucket
// name by eye.
// The Dataset board shows the stored value verbatim -- `0_3_days`, `no_recent_change` --
// rather than prose, because its headers are already the raw column names and a value that
// reads "No Recent Change" there can't be matched against the model's category by eye.
// Null means nothing has ever been recorded for that ad set in that scope, and stays "-",
// which is a different thing from a recorded `no_recent_change`.
const rawCategory = (value: any) => value == null ? '-' : String(value);

const cplMoney = (value: any) => value == null || Number.isNaN(Number(value))
 ? '-'
 : new Intl.NumberFormat('en-US', {
 style: 'currency',
 currency: 'USD',
 minimumFractionDigits: 2,
 maximumFractionDigits: 2,
 }).format(Number(value));

const percent = (value: any) => value == null
 ? '-'
 : `${(Number(value) * 100).toFixed(1)}%`;

// Daily budget moves are small; rounding $7.25 and $6.71 both to "$7" makes
// two different reallocations look identical. Keep cents below $100.
const dayMoney = (value: any) => value == null || Number.isNaN(Number(value))
 ? '-'
 : Math.abs(Number(value)) < 100 ? cplMoney(value) : money(value);

const UPLOAD_COLUMN_WIDTHS: Record<string, number> = {
 'Created At': 170,
 'Customer Name': 190,
 'Status': 90,
 'UTM Campaign ID': 150,
 'UTM Ad Set ID': 150,
 'UTM Ad ID': 150,
};

const dateFmt = (value: any) => value
 ? new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric' }).format(new Date(value))
 : '-';

const compactDateRangeFmt = (start: any, end: any) => {
 if (!start || !end) return '-';
 const clean = (value: any) => {
  const date = new Date(`${String(value).slice(0, 10)}T12:00:00`);
  if (Number.isNaN(date.getTime())) return String(value);
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return { month, day, year: date.getFullYear() };
 };
 const from = clean(start);
 const to = clean(end);
 if (typeof from === 'string' || typeof to === 'string') return `${start} - ${end}`;
 return `${from.month}.${from.day}-${to.month}.${to.day}.${to.year}`;
};

const weekdayFmt = (value: any) => value
 ? new Intl.DateTimeFormat('en', { weekday: 'short' }).format(new Date(`${String(value).slice(0, 10)}T12:00:00`))
 : '';

const isoDate = (value: any) => {
 const date = new Date(value);
 if (Number.isNaN(date.getTime())) return '';
 const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
 return local.toISOString().slice(0, 10);
};

const rangeLabel = (iso: string) => iso
 ? new Intl.DateTimeFormat('en-US', { month: '2-digit', day: '2-digit', year: 'numeric' }).format(new Date(`${iso}T12:00:00`))
 : '--/--/----';

const dateTimeInputValue = (value: any) => {
 if (!value) return '';
 const date = new Date(value);
 if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
 const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
 return local.toISOString().slice(0, 16);
};

// Mirrors LEAD_QUALITY_OPTIONS in backend/core.py -- a hand-recorded CRM pipeline stage with
// no import source, so every lead starts at the first entry ("Intake") until moved. Order is
// the pipeline's natural progression, which is also the dropdown's option order.
const LEAD_QUALITY_OPTIONS = [
 'Intake', 'Not Qualified', 'Qualified', 'Converted', 'Lost', 'Awaiting Document and Payment',
];
// CSS class hook for a quality value's pill color (see `.lead-quality-select.quality-*` in
// styles.css) -- lowercased, spaces to hyphens, e.g. "Awaiting Document and Payment" ->
// "awaiting-document-and-payment".
const leadQualitySlug = (value: string) => String(value || 'intake').toLowerCase().replace(/\s+/g, '-');

const withAuth = (options?: RequestInit): RequestInit => {
 const headers = new Headers(options?.headers);
 if (apiAuthHeader && !headers.has('Authorization')) headers.set('Authorization', apiAuthHeader);
 return { ...options, headers };
};

const apiFetch = (path: string, options?: RequestInit) => fetch(API + path, withAuth(options));

const api = async (path: string, options?: RequestInit) => {
 const response = await apiFetch(path, options);
 if (!response.ok) {
 const body = await response.json().catch(() => ({ detail: 'Request failed' }));
 throw new Error(body.detail || 'Request failed');
 }
 return response.json();
};

const basicAuthHeader = (username: string, password: string) => {
 const bytes = new TextEncoder().encode(`${username}:${password}`);
 let binary = '';
 bytes.forEach((byte) => { binary += String.fromCharCode(byte); });
 return `Basic ${btoa(binary)}`;
};

const downloadApiFile = async (path: string, fallbackName: string) => {
 const response = await apiFetch(path);
 if (!response.ok) {
 const body = await response.json().catch(() => ({ detail: 'Download failed' }));
 throw new Error(body.detail || 'Download failed');
 }
 const blob = await response.blob();
 const disposition = response.headers.get('Content-Disposition') || '';
 const match = disposition.match(/filename="?([^"]+)"?/i);
 const link = document.createElement('a');
 link.href = URL.createObjectURL(blob);
 link.download = match?.[1] || fallbackName;
 document.body.appendChild(link);
 link.click();
 link.remove();
 URL.revokeObjectURL(link.href);
};

// Saving a change event or start date kicks off a background retrain (see `_request_retrain`
// in backend/app.py). The correlation matrix, OLS panel and raw dataset rows are all computed
// live from the recorded data, so they are already correct the moment the save returns --
// but anything reading a stored training run (the forecast chart, accuracy figures) stays
// stale until that run lands. This polls for the finish so the caller can refetch once.
//
// No race on startup: `_request_retrain` flips its running flag inside the request handler,
// so it is already true by the time the POST response reaches us.
function useRetrainWatcher(onComplete?: () => void) {
 const [retraining, setRetraining] = useState(false);
 const completeRef = useRef(onComplete);
 completeRef.current = onComplete;

 useEffect(() => {
  if (!retraining) return;
  let cancelled = false;
  const timer = setInterval(() => {
   void api('/models/retrain-status')
    .then((status: any) => {
     if (cancelled || status?.running) return;
     setRetraining(false);
     completeRef.current?.();
    })
    // A failed poll shouldn't leave the indicator spinning forever; the next save re-arms it.
    .catch(() => { if (!cancelled) setRetraining(false); });
  }, 2000);
  return () => { cancelled = true; clearInterval(timer); };
 }, [retraining]);

 return { retraining, watchRetrain: () => setRetraining(true) };
}

function AnimatedNumber({ value, suffix = '', format = fmt }: { value: number; suffix?: string; format?: (value: number) => string }) {
 const [display, setDisplay] = useState(0);
 useEffect(() => {
 if (!Number.isFinite(value)) return;
 if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
 setDisplay(value);
 return;
 }
 const started = performance.now();
 let frame = 0;
 const tick = (now: number) => {
 const progress = Math.min(1, (now - started) / 850);
 const eased = 1 - Math.pow(1 - progress, 3);
 setDisplay(value * eased);
 if (progress < 1) frame = requestAnimationFrame(tick);
 };
 frame = requestAnimationFrame(tick);
 return () => cancelAnimationFrame(frame);
 }, [value]);
 return <>{format(display)}{suffix}</>;
}

function ForecastTrackingTooltip({ active, payload }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 const hasActual = point.actual_leads != null;
 const hasForecast = point.forecast_leads != null;
 const hasScenario = point.scenario_leads != null;
 const comparison = hasActual && hasForecast;
 return (
 <div className="forecast-tooltip tracking-tooltip">
 <span>{point.phase_label ? `${String(point.phase_label).toUpperCase()} - ${comparison ? 'ACTUAL + FORECAST' : 'FORECAST SNAPSHOT'}` : 'HISTORICAL ACTUAL'}</span>
 <b>{weekdayFmt(point.date)} - {dateFmt(point.date)}</b>
 {hasActual && <p><i className="actual-dot" />Actual leads<strong>{fmt(point.actual_leads)}</strong></p>}
 {hasForecast && <p><i className="forecast-dot" />Forecast leads<strong>{fmt(point.forecast_leads)}</strong></p>}
 {hasScenario && <p><i className="scenario-dot" />Scenario forecast<strong>{fmt(point.scenario_leads)}</strong></p>}
 {comparison && <p><i />Difference<strong className={Number(point.difference) > 0 ? 'warm' : ''}>{Number(point.difference) > 0 ? '+' : ''}{fmt(point.difference)}</strong></p>}
 <small>{hasForecast ? `Likely range ${fmt(point.lower_estimate)}-${fmt(point.upper_estimate)} - ` : ''}{point.ad_set_count} ad sets{point.training_run_id ? ` - Run #${point.training_run_id}` : ''}</small>
 </div>
 );
}

function WeekdayAxisTick({ x, y, payload }: any) {
 const value = String(payload?.value || '');
 return (
 <g transform={`translate(${x},${y})`} aria-hidden="true">
 <text y={12} textAnchor="middle" fill="var(--text)" fontSize="12" fontWeight="700" letterSpacing=".04em">
 {weekdayFmt(value).toUpperCase()}
 </text>
 <text y={27} textAnchor="middle" fill="var(--muted)" fontSize="11" fontWeight="500">
 {value.slice(5)}
 </text>
 </g>
 );
}

function TrackingClickableDot({ cx, cy, payload, stroke = 'var(--series-actual)', fill = 'var(--bg-raised)', r = 3, onSelect }: any) {
 if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
 const select = (event: any) => {
 if (event.button != null && event.button !== 0) return;
 event.preventDefault();
 event.stopPropagation();
 onSelect?.(payload);
 };
 return (
 <g
 className="tracking-click-dot"
 role="button"
 tabIndex={0}
 aria-label={`Show leads for ${dateFmt(payload?.date)}`}
 onMouseDown={select}
 onClick={select}
 onKeyDown={(event) => {
 if (event.key === 'Enter' || event.key === ' ') {
 event.preventDefault();
 onSelect?.(payload);
 }
 }}
 >
 <circle className="tracking-dot-hit" cx={cx} cy={cy} r={12} />
 <circle cx={cx} cy={cy} r={r} fill={fill} stroke={stroke} strokeWidth={1.8} />
 </g>
 );
}

function PortfolioTooltip({ active, payload, measure = 'leads' }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 return (
 <div className="forecast-tooltip portfolio-tooltip">
 <b>{point.id}</b>
 <p><i className="forecast-dot" />Historical leads<strong>{fmt(point.leads)}</strong></p>
 <p><i className="actual-dot" />Portfolio share<strong>{Number(point.share || 0).toFixed(1)}%</strong></p>
 <small>Ranked by {measure === 'share' ? 'portfolio share' : 'lead volume'}</small>
 </div>
 );
}

function InteractivePortfolioBar({ x, y, width, height, fill, payload, onSelect }: any) {
 const select = () => onSelect?.(payload?.id);
 return (
 <g
 className="interactive-portfolio-bar"
 role="button"
 tabIndex={0}
 aria-label={`View ad set ${payload?.id}, ${fmt(payload?.leads)} historical leads`}
 onClick={select}
 onKeyDown={(event) => {
 if (event.key === 'Enter' || event.key === ' ') {
 event.preventDefault();
 select();
 }
 }}
 >
 <rect x={x} y={y} width={width} height={height} rx={7} fill={fill} />
 </g>
 );
}

function StatusMixTooltip({ active, payload }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 return (
 <div className="forecast-tooltip mix-tooltip">
 <b>{point.status} leads</b>
 <p><i style={{ background: point.color }} />Lead count<strong>{fmt(point.leads)}</strong></p>
 <p><i style={{ background: point.color }} />Share<strong>{Number(point.sharePercent || 0).toFixed(1)}%</strong></p>
 </div>
 );
}

function SpendTrendTooltip({ active, payload, label }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 const noLeadSpend = Number(point.spend || 0) > 0 && Number(point.actual_leads || 0) <= 0;
 return (
 <div className="forecast-tooltip spend-tooltip">
 <span>AD SPEND</span>
 <b>{weekdayFmt(label)} - {dateFmt(label)}</b>
 <p><i className="forecast-dot" />Amount spent<strong>{cplMoney(point.spend)}</strong></p>
 <p><i className="actual-dot" />Actual leads<strong>{fmt(point.actual_leads)}</strong></p>
 <p><i />Actual CPL<strong>{noLeadSpend ? 'No leads' : cplMoney(point.actual_cpl ?? point.cpl)}</strong></p>
 <small>{noLeadSpend ? 'Spend continued with 0 cleaned leads' : `${fmt(point.link_clicks)} clicks - ${percent(point.ctr)} CTR`}</small>
 </div>
 );
}

function SpendPerDayTooltip({ active, payload, label }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 const spend = Number(point.spend || 0);
 const noLeadSpend = spend > 0 && Number(point.actual_leads || 0) <= 0;
 const cpl = point.actual_cpl ?? point.cpl;
 const budget = point.budget != null ? Number(point.budget) : null;
 const delta = budget != null ? spend - budget : null;
 return (
 <div className="forecast-tooltip spend-tooltip">
 <span>AMOUNT SPENT</span>
 <b>{weekdayFmt(label)} - {dateFmt(label)}</b>
 <p><i className="forecast-dot" />Amount spent<strong>{cplMoney(spend)}</strong></p>
 {budget != null && (
 <>
 <p><i />Daily budget<strong>{cplMoney(budget)}</strong></p>
 <p><i />vs budget<strong>{delta == null ? '-' : `${delta > 0 ? '+' : delta < 0 ? '-' : ''}${cplMoney(Math.abs(delta))}`}</strong></p>
 </>
 )}
 <p><i className="actual-dot" />Leads<strong>{fmt(point.actual_leads)}</strong></p>
 <small>{noLeadSpend ? 'Spend continued with 0 cleaned leads' : `${cplMoney(cpl)} CPL`}</small>
 </div>
 );
}

function SpendCplDot(props: any) {
 const { cx, cy, payload } = props;
 if (cx == null || cy == null) return null;
 if (payload?.no_lead_spend) {
 return (
 <g>
 <line x1={cx} x2={cx} y1={cy - 9} y2={cy + 9} stroke="var(--yellow)" strokeWidth={1.4} strokeLinecap="round" opacity={0.9} />
 <circle cx={cx} cy={cy} r={4.8} fill="var(--bg-raised)" stroke="var(--yellow)" strokeWidth={2.2} />
 <circle cx={cx} cy={cy} r={1.4} fill="var(--yellow)" />
 </g>
 );
 }
 return <circle cx={cx} cy={cy} r={3.1} fill="var(--bg-raised)" stroke="var(--yellow)" strokeWidth={1.6} />;
}

function CampaignSpendTooltip({ active, payload }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 return (
 <div className="forecast-tooltip spend-tooltip">
 <span>CAMPAIGN SPEND</span>
 <b>{point.campaign_name}</b>
 <p><i className="forecast-dot" />Spend<strong>{money(point.spend)}</strong></p>
 <p><i className="actual-dot" />Actual leads<strong>{fmt(point.actual_leads)}</strong></p>
 <p><i />Actual CPL<strong>{cplMoney(point.actual_cpl ?? point.cpl)}</strong></p>
 <small>{fmt(point.ad_set_count)} ad sets - {fmt(point.impressions)} impressions</small>
 </div>
 );
}

// Dedicated tokens rather than --series-actual/--danger: the Forecast page is a hard-scoped dark
// surface in both themes, so the light-theme values of those tokens would land under 4.5:1 on it.
function SpendLeadsScatterTooltip({ active, payload }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 return (
 <div className="forecast-tooltip scatter-tooltip">
 <span>DAILY SPEND VS LEADS</span>
 <b>{weekdayFmt(point.day)} - {dateFmt(point.day)}</b>
 <p><i className="forecast-dot" />Daily spend<strong>{money(point.spend)}</strong></p>
 <p><i className="actual-dot" />Daily leads<strong>{fmt(point.actual_leads)}</strong></p>
 <p><i />Cost per lead<strong>{point.cpl == null ? 'No leads' : cplMoney(point.cpl)}</strong></p>
 </div>
 );
}

function SpendLeadsScatterDot({ cx, cy }: any) {
 if (cx == null || cy == null) return null;
 // Soft filled blob, no stroke, so overlapping days read as density. Fill opacity 0.62 rather
 // than the ~0.45 that suits this treatment on white: on the page's #0b0c0d ground a 0.45 fill
 // composites to ~2.4:1 and goes muddy, while 0.62 holds contrast and still blends.
 //
 // The trend line sits in a lower z-index layer than the dots (recharts assigns paint order
 // by component type, not JSX order), so it never draws over them — but a 0.62-opacity fill
 // still lets the grid/line show through wherever the fitted line runs under a dot, which is
 // worst exactly where the fit is best: the dense cluster it's drawn through. An opaque
 // page-color knockout circle underneath blocks that without flattening the dot-over-dot
 // density blending, which is a same-color blend unaffected by this second circle.
 // Grouped, with the hover-scale class on the <g>, not either circle — the knockout and the
 // color fill must grow together on hover, or the color circle outgrows the opaque one
 // underneath and exposes a ring of line/grid around its edge.
 return (
 <g className="scatter-dot">
 <circle cx={cx} cy={cy} r={7} fill="#0b0c0d" />
 <circle cx={cx} cy={cy} r={7} fill="var(--scatter-spend)" fillOpacity={0.62} />
 </g>
 );
}

function InteractiveEfficiencyBubble({ cx, cy, size, fill, payload, onSelect, selectedId }: any) {
 const selected = String(payload?.campaign_id) === String(selectedId);
 const radius = Math.max(6, Math.min(19, Math.sqrt(Number(size || 100) / Math.PI)));
 const select = () => onSelect?.(String(payload?.campaign_id || ''));
 return (
 <g
 className={`interactive-efficiency-bubble${selected ? ' selected' : ''}`}
 role="button"
 tabIndex={0}
 aria-label={`Select ${payload?.campaign_name}, ${money(payload?.spend)} spend, ${fmt(payload?.actual_leads)} leads, actual CPL ${cplMoney(payload?.actual_cpl)}`}
 onClick={select}
 onKeyDown={(event) => {
 if (event.key === 'Enter' || event.key === ' ') {
 event.preventDefault();
 select();
 }
 }}
 >
 <circle className="efficiency-bubble-hit-area" cx={cx} cy={cy} r={radius + 7} />
 <circle className="efficiency-bubble-mark" cx={cx} cy={cy} r={radius} fill={fill} />
 </g>
 );
}

function DistributionTooltip({ active, payload }: any) {
 if (!active || !payload?.length) return null;
 const point = payload[0]?.payload || {};
 return (
 <div className="forecast-tooltip distribution-tooltip">
 <span>Rank #{point.rank}</span>
 <b>{point.id}</b>
 <p><i className="forecast-dot" />Historical leads<strong>{fmt(point.leads)}</strong></p>
 <p><i className="actual-dot" />Portfolio share<strong>{point.share?.toFixed(1)}%</strong></p>
 <small>Click to open full ad set details</small>
 </div>
 );
}

function InteractiveDistributionDot({ cx, cy, payload, selectedId, onSelect }: any) {
 const selected = payload?.id === selectedId;
 const select = (event: any) => {
 onSelect?.(payload?.id);
 };
 return (
 <g
 className="distribution-point"
 role="button"
 tabIndex={0}
 aria-label={`Select rank ${payload?.rank}, ad set ${payload?.id}, ${fmt(payload?.leads)} historical leads`}
 onClick={select}
 onKeyDown={(event) => {
 if (event.key === 'Enter' || event.key === ' ') {
 event.preventDefault();
 select(event);
 }
 }}
 >
 <circle className="distribution-hit-area" cx={cx} cy={cy} r={11} />
 <circle className={`distribution-dot${selected ? ' selected' : ''}`} cx={cx} cy={cy} r={selected ? 6 : 3.5} />
 </g>
 );
}

function DateRangePicker({ startDate, endDate, minDate, maxDate, onApply, onReset }: any) {
 const [open, setOpen] = useState(false);
 const [draftStart, setDraftStart] = useState(startDate);
 const [draftEnd, setDraftEnd] = useState(endDate);
 const [viewDate, setViewDate] = useState(() => new Date(`${startDate || minDate || isoDate(new Date())}T12:00:00`));
 const wrapRef = useRef<HTMLDivElement>(null);

 useEffect(() => {
 if (!open) return;
 const handler = (event: MouseEvent) => {
 if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) setOpen(false);
 };
 const escHandler = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
 document.addEventListener('mousedown', handler);
 document.addEventListener('keydown', escHandler);
 return () => {
 document.removeEventListener('mousedown', handler);
 document.removeEventListener('keydown', escHandler);
 };
 }, [open]);

 const openPicker = () => {
 setDraftStart(startDate);
 setDraftEnd(endDate);
 setViewDate(new Date(`${startDate || minDate || isoDate(new Date())}T12:00:00`));
 setOpen(true);
 };

 const pickDay = (iso: string) => {
 if (!draftStart || (draftStart && draftEnd)) { setDraftStart(iso); setDraftEnd(''); return; }
 if (iso < draftStart) { setDraftStart(iso); setDraftEnd(''); return; }
 setDraftEnd(iso);
 };

 const apply = () => {
 if (draftStart && draftEnd) onApply(draftStart, draftEnd);
 setOpen(false);
 };

 const year = viewDate.getFullYear();
 const month = viewDate.getMonth();
 const firstOfMonth = new Date(year, month, 1);
 const startWeekday = (firstOfMonth.getDay() + 6) % 7;
 const daysInMonth = new Date(year, month + 1, 0).getDate();
 const daysInPrevMonth = new Date(year, month, 0).getDate();
 const cells: { iso: string; day: number; outside: boolean }[] = [];
 for (let i = startWeekday - 1; i >= 0; i--) {
 cells.push({ iso: isoDate(new Date(year, month - 1, daysInPrevMonth - i)), day: daysInPrevMonth - i, outside: true });
 }
 for (let day = 1; day <= daysInMonth; day++) {
 cells.push({ iso: isoDate(new Date(year, month, day)), day, outside: false });
 }
 let trailDay = 1;
 while (cells.length < 42) {
 cells.push({ iso: isoDate(new Date(year, month + 1, trailDay)), day: trailDay, outside: true });
 trailDay += 1;
 }

 return (
 <div className="date-range-picker" ref={wrapRef}>
 <button type="button" className="date-range-trigger" onClick={() => (open ? setOpen(false) : openPicker())} aria-expanded={open} aria-haspopup="dialog">
 <CalendarDays size={15} />
 <span>{rangeLabel(startDate)}</span>
 <i aria-hidden="true">-</i>
 <span>{rangeLabel(endDate)}</span>
 </button>
 {open && (
 <div className="date-range-popover" role="dialog" aria-label="Select date range">
 <div className="date-range-nav">
 <button type="button" aria-label="Previous month" onClick={() => setViewDate(new Date(year, month - 1, 1))}><ChevronLeft size={16} /></button>
 <span>{new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(viewDate)}</span>
 <button type="button" aria-label="Next month" onClick={() => setViewDate(new Date(year, month + 1, 1))}><ChevronRight size={16} /></button>
 </div>
 <div className="date-range-weekdays">{['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((w, i) => <span key={i}>{w}</span>)}</div>
 <div className="date-range-grid">
 {cells.map((cell) => {
 const disabled = (!!minDate && cell.iso < minDate) || (!!maxDate && cell.iso > maxDate);
 const isStart = cell.iso === draftStart;
 const isEnd = cell.iso === draftEnd;
 const inRange = !!draftStart && !!draftEnd && cell.iso > draftStart && cell.iso < draftEnd;
 const classes = ['date-cell'];
 if (cell.outside) classes.push('outside');
 if (isStart) classes.push('range-start');
 if (isEnd) classes.push('range-end');
 if (inRange) classes.push('in-range');
 return (
 <button type="button" key={cell.iso} className={classes.join(' ')} disabled={disabled} onClick={() => pickDay(cell.iso)}>
 <span>{cell.day}</span>
 </button>
 );
 })}
 </div>
 <div className="date-range-actions">
 <button type="button" className="ghost" onClick={() => { onReset(); setOpen(false); }}>Reset</button>
 <div className="date-range-actions-right">
 <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
 <button type="button" className="primary" disabled={!draftStart || !draftEnd} onClick={apply}>Apply</button>
 </div>
 </div>
 </div>
 )}
 </div>
 );
}

function SingleDatePicker({ value, onChange, min, ariaLabel }: { value: string; onChange: (next: string) => void; min?: string; ariaLabel: string }) {
 const [open, setOpen] = useState(false);
 const [viewDate, setViewDate] = useState(() => new Date(`${value || min || isoDate(new Date())}T12:00:00`));
 const [pos, setPos] = useState({ top: 0, left: 0 });
 const [darkScope, setDarkScope] = useState(false);
 const wrapRef = useRef<HTMLDivElement>(null);
 const triggerRef = useRef<HTMLButtonElement>(null);
 const popoverRef = useRef<HTMLDivElement>(null);

 const reposition = () => {
 const trigger = triggerRef.current;
 if (!trigger) return;
 const rect = trigger.getBoundingClientRect();
 const width = 250;
 const height = popoverRef.current?.getBoundingClientRect().height || 360;
 let left = rect.left;
 if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
 let top = rect.bottom + 5;
 if (top + height > window.innerHeight - 8) {
 const above = rect.top - height - 5;
 top = above > 8 ? above : Math.max(8, window.innerHeight - height - 8);
 }
 setPos({ top, left });
 };

 useLayoutEffect(() => {
 if (!open) return;
 reposition();
 }, [open]);

 useEffect(() => {
 if (!open) return;
 const handler = (event: MouseEvent) => {
 const target = event.target as Node;
 if (wrapRef.current?.contains(target)) return;
 if (popoverRef.current?.contains(target)) return;
 setOpen(false);
 };
 const escHandler = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
 const reflowHandler = () => reposition();
 document.addEventListener('mousedown', handler);
 document.addEventListener('keydown', escHandler);
 window.addEventListener('scroll', reflowHandler, true);
 window.addEventListener('resize', reflowHandler);
 return () => {
 document.removeEventListener('mousedown', handler);
 document.removeEventListener('keydown', escHandler);
 window.removeEventListener('scroll', reflowHandler, true);
 window.removeEventListener('resize', reflowHandler);
 };
 }, [open]);

 const openPicker = () => {
 setViewDate(new Date(`${value || min || isoDate(new Date())}T12:00:00`));
 setDarkScope(!!wrapRef.current?.closest('.forecast-v2-page, .dataset-page'));
 reposition();
 setOpen(true);
 };

 const pick = (iso: string) => { onChange(iso); setOpen(false); };

 const year = viewDate.getFullYear();
 const month = viewDate.getMonth();
 const firstOfMonth = new Date(year, month, 1);
 const startWeekday = (firstOfMonth.getDay() + 6) % 7;
 const daysInMonth = new Date(year, month + 1, 0).getDate();
 const daysInPrevMonth = new Date(year, month, 0).getDate();
 const cells: { iso: string; day: number; outside: boolean }[] = [];
 for (let i = startWeekday - 1; i >= 0; i--) {
 cells.push({ iso: isoDate(new Date(year, month - 1, daysInPrevMonth - i)), day: daysInPrevMonth - i, outside: true });
 }
 for (let day = 1; day <= daysInMonth; day++) {
 cells.push({ iso: isoDate(new Date(year, month, day)), day, outside: false });
 }
 let trailDay = 1;
 while (cells.length < 42) {
 cells.push({ iso: isoDate(new Date(year, month + 1, trailDay)), day: trailDay, outside: true });
 trailDay += 1;
 }
 const today = isoDate(new Date());
 const todayDisabled = !!min && today < min;

 return (
 <div className={`mini-date-field${open ? ' open' : ''}`} ref={wrapRef}>
 <button
 ref={triggerRef}
 type="button"
 className="mini-date-trigger"
 aria-haspopup="dialog"
 aria-expanded={open}
 aria-label={ariaLabel}
 onClick={() => (open ? setOpen(false) : openPicker())}
 >
 <span className={value ? undefined : 'placeholder'}>{value ? dateFmt(value) : 'Select'}</span>
 <CalendarDays size={13} className="mini-date-caret" />
 </button>
 {open && createPortal(
 <div className={`mini-date-popover${darkScope ? ' scoped-dark' : ''}`} role="dialog" aria-label={ariaLabel} ref={popoverRef} style={{ position: 'fixed', top: pos.top, left: pos.left }}>
 <div className="date-range-nav">
 <button type="button" aria-label="Previous month" onClick={() => setViewDate(new Date(year, month - 1, 1))}><ChevronLeft size={14} /></button>
 <span>{new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(viewDate)}</span>
 <button type="button" aria-label="Next month" onClick={() => setViewDate(new Date(year, month + 1, 1))}><ChevronRight size={14} /></button>
 </div>
 <div className="date-range-weekdays">{['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((w, i) => <span key={i}>{w}</span>)}</div>
 <div className="date-range-grid">
 {cells.map((cell) => {
 const disabled = !!min && cell.iso < min;
 const selected = cell.iso === value;
 const classes = ['date-cell'];
 if (cell.outside) classes.push('outside');
 if (selected) classes.push('range-start', 'range-end');
 return (
 <button type="button" key={cell.iso} className={classes.join(' ')} disabled={disabled} onClick={() => pick(cell.iso)}>
 <span>{cell.day}</span>
 </button>
 );
 })}
 </div>
 <div className="date-range-actions">
 <button type="button" className="ghost" onClick={() => { onChange(''); setOpen(false); }}>Clear</button>
 <button type="button" className="primary" disabled={todayDisabled} onClick={() => pick(today)}>Today</button>
 </div>
 </div>,
 document.body
 )}
 </div>
 );
}

const QUICK_RANGE_PRESETS: { key: string; label: string }[] = [
 { key: 'today', label: 'Today' },
 { key: 'yesterday', label: 'Yesterday' },
 { key: 'this_week', label: 'This week' },
 { key: 'last_week', label: 'Last week' },
 { key: 'this_month', label: 'This month' },
 { key: 'last_month', label: 'Last month' },
 { key: 'this_year', label: 'This year' },
 { key: 'last_year', label: 'Last year' },
 { key: 'all_time', label: 'All time' },
];

const quickRangeFor = (key: string): { from: string; to: string } | null => {
 const today = new Date();
 const startOfWeek = (date: Date) => { const d = new Date(date); d.setDate(date.getDate() - ((date.getDay() + 6) % 7)); return d; };
 switch (key) {
  case 'today': return { from: isoDate(today), to: isoDate(today) };
  case 'yesterday': { const d = new Date(today); d.setDate(today.getDate() - 1); return { from: isoDate(d), to: isoDate(d) }; }
  case 'this_week': return { from: isoDate(startOfWeek(today)), to: isoDate(today) };
  case 'last_week': { const end = new Date(startOfWeek(today)); end.setDate(end.getDate() - 1); const start = new Date(end); start.setDate(end.getDate() - 6); return { from: isoDate(start), to: isoDate(end) }; }
  case 'this_month': return { from: isoDate(new Date(today.getFullYear(), today.getMonth(), 1)), to: isoDate(today) };
  case 'last_month': return { from: isoDate(new Date(today.getFullYear(), today.getMonth() - 1, 1)), to: isoDate(new Date(today.getFullYear(), today.getMonth(), 0)) };
  case 'this_year': return { from: isoDate(new Date(today.getFullYear(), 0, 1)), to: isoDate(today) };
  case 'last_year': return { from: isoDate(new Date(today.getFullYear() - 1, 0, 1)), to: isoDate(new Date(today.getFullYear() - 1, 11, 31)) };
  default: return null;
 }
};

const monthCells = (year: number, month: number) => {
 const firstOfMonth = new Date(year, month, 1);
 const startWeekday = (firstOfMonth.getDay() + 6) % 7;
 const daysInMonth = new Date(year, month + 1, 0).getDate();
 const daysInPrevMonth = new Date(year, month, 0).getDate();
 const cells: { iso: string; day: number; outside: boolean }[] = [];
 for (let i = startWeekday - 1; i >= 0; i--) {
  cells.push({ iso: isoDate(new Date(year, month - 1, daysInPrevMonth - i)), day: daysInPrevMonth - i, outside: true });
 }
 for (let day = 1; day <= daysInMonth; day++) {
  cells.push({ iso: isoDate(new Date(year, month, day)), day, outside: false });
 }
 let trailDay = 1;
 while (cells.length < 42) {
  cells.push({ iso: isoDate(new Date(year, month + 1, trailDay)), day: trailDay, outside: true });
  trailDay += 1;
 }
 return cells;
};

// Quick date-range scope for the Dataset page's "Raw data" table -- sits next to the
// FilterBar and, unlike it, needs no "which column" step: presets + a two-month picker
// straight to a `{from, to}` range, portaled like SingleDatePicker so the wide popover
// isn't clipped by the scrolling rows controls row it lives in.
function PresetDateRangePicker({ value, onApply, onClear, minDate }: {
 value: { from: string; to: string } | null; onApply: (range: { from: string; to: string }) => void; onClear: () => void; minDate?: string;
}) {
 const [open, setOpen] = useState(false);
 const [activePreset, setActivePreset] = useState<string>(value ? 'custom' : 'all_time');
 const [draftFrom, setDraftFrom] = useState(value?.from || '');
 const [draftTo, setDraftTo] = useState(value?.to || '');
 const [leftView, setLeftView] = useState(() => new Date(`${value?.from || isoDate(new Date())}T12:00:00`));
 const [pos, setPos] = useState({ top: 0, left: 0 });
 const wrapRef = useRef<HTMLDivElement>(null);
 const triggerRef = useRef<HTMLButtonElement>(null);
 const popoverRef = useRef<HTMLDivElement>(null);

 const reposition = () => {
  const trigger = triggerRef.current;
  if (!trigger) return;
  const rect = trigger.getBoundingClientRect();
  const width = popoverRef.current?.getBoundingClientRect().width || 660;
  const height = popoverRef.current?.getBoundingClientRect().height || 400;
  let left = rect.right - width;
  if (left < 8) left = 8;
  if (left + width > window.innerWidth - 8) left = Math.max(8, window.innerWidth - width - 8);
  let top = rect.bottom + 6;
  if (top + height > window.innerHeight - 8) {
   const above = rect.top - height - 6;
   top = above > 8 ? above : Math.max(8, window.innerHeight - height - 8);
  }
  setPos({ top, left });
 };

 useLayoutEffect(() => { if (open) reposition(); }, [open]);

 useEffect(() => {
  if (!open) return;
  const handler = (event: MouseEvent) => {
   const target = event.target as Node;
   if (wrapRef.current?.contains(target)) return;
   if (popoverRef.current?.contains(target)) return;
   setOpen(false);
  };
  const escHandler = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
  const reflow = () => reposition();
  document.addEventListener('mousedown', handler);
  document.addEventListener('keydown', escHandler);
  window.addEventListener('scroll', reflow, true);
  window.addEventListener('resize', reflow);
  return () => {
   document.removeEventListener('mousedown', handler);
   document.removeEventListener('keydown', escHandler);
   window.removeEventListener('scroll', reflow, true);
   window.removeEventListener('resize', reflow);
  };
 }, [open]);

 const openPicker = () => {
  setDraftFrom(value?.from || '');
  setDraftTo(value?.to || '');
  setActivePreset(value ? 'custom' : 'all_time');
  setLeftView(new Date(`${value?.from || isoDate(new Date())}T12:00:00`));
  reposition();
  setOpen(true);
 };

 const pickPreset = (key: string) => {
  setActivePreset(key);
  const range = quickRangeFor(key);
  setDraftFrom(range?.from || '');
  setDraftTo(range?.to || '');
  if (range) setLeftView(new Date(`${range.from}T12:00:00`));
 };

 const pickDay = (iso: string) => {
  setActivePreset('custom');
  if (!draftFrom || (draftFrom && draftTo)) { setDraftFrom(iso); setDraftTo(''); return; }
  if (iso < draftFrom) { setDraftFrom(iso); setDraftTo(''); return; }
  setDraftTo(iso);
 };

 const apply = () => {
  if (activePreset === 'all_time' || (!draftFrom && !draftTo)) { onClear(); setOpen(false); return; }
  if (draftFrom && draftTo) { onApply({ from: draftFrom, to: draftTo }); setOpen(false); }
 };

 const leftYear = leftView.getFullYear();
 const leftMonth = leftView.getMonth();
 const rightRef = new Date(leftYear, leftMonth + 1, 1);

 const renderMonth = (year: number, month: number, side: 'left' | 'right') => (
  <div className="preset-date-month" key={side}>
   <div className="date-range-nav">
    <button type="button" aria-label="Previous month" className={side === 'right' ? 'is-spacer' : undefined} tabIndex={side === 'right' ? -1 : 0} onClick={() => setLeftView(new Date(leftYear, leftMonth - 1, 1))}><ChevronLeft size={15} /></button>
    <span>{new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric' }).format(new Date(year, month, 1))}</span>
    <button type="button" aria-label="Next month" className={side === 'left' ? 'is-spacer' : undefined} tabIndex={side === 'left' ? -1 : 0} onClick={() => setLeftView(new Date(leftYear, leftMonth + 1, 1))}><ChevronRight size={15} /></button>
   </div>
   <div className="date-range-weekdays">{['M', 'T', 'W', 'T', 'F', 'S', 'S'].map((w, i) => <span key={i}>{w}</span>)}</div>
   <div className="date-range-grid">
    {monthCells(year, month).map((cell) => {
     const disabled = !!minDate && cell.iso < minDate;
     const isStart = cell.iso === draftFrom;
     const isEnd = cell.iso === draftTo;
     const inRange = !!draftFrom && !!draftTo && cell.iso > draftFrom && cell.iso < draftTo;
     const classes = ['date-cell'];
     if (cell.outside) classes.push('outside');
     if (isStart) classes.push('range-start');
     if (isEnd) classes.push('range-end');
     if (inRange) classes.push('in-range');
     return <button type="button" key={cell.iso} className={classes.join(' ')} disabled={disabled} onClick={() => pickDay(cell.iso)}><span>{cell.day}</span></button>;
    })}
   </div>
  </div>
 );

 const label = value ? `${dateFmt(value.from)} – ${dateFmt(value.to)}` : 'All time';

 return (
  <div className="preset-date-picker" ref={wrapRef}>
   <button ref={triggerRef} type="button" className={`selector filter-selector date-range-trigger${value ? ' is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => (open ? setOpen(false) : openPicker())}>
    <CalendarDays size={15} />
    <span>{label}</span>
    <ChevronDown size={14} className="campaign-caret" />
   </button>
   {open && createPortal(
    <div className="preset-date-popover" role="dialog" aria-label="Select date range" ref={popoverRef} style={{ position: 'fixed', top: pos.top, left: pos.left }}>
     <div className="preset-date-body">
      <div className="preset-date-sidebar">
       {QUICK_RANGE_PRESETS.map((preset) => (
        <button type="button" key={preset.key} className={`preset-date-option${activePreset === preset.key ? ' active' : ''}`} onClick={() => pickPreset(preset.key)}>{preset.label}</button>
       ))}
       <button type="button" className={`preset-date-option${activePreset === 'custom' ? ' active' : ''}`} onClick={() => setActivePreset('custom')}>Custom</button>
      </div>
      <div className="preset-date-calendars">
       {renderMonth(leftYear, leftMonth, 'left')}
       {renderMonth(rightRef.getFullYear(), rightRef.getMonth(), 'right')}
      </div>
     </div>
     <div className="preset-date-footer">
      <div className="preset-date-inputs">
       <input type="date" value={draftFrom} max={draftTo || undefined} onChange={(event) => { setDraftFrom(event.target.value); setActivePreset('custom'); }} />
       <span>–</span>
       <input type="date" value={draftTo} min={draftFrom || undefined} onChange={(event) => { setDraftTo(event.target.value); setActivePreset('custom'); }} />
      </div>
      <div className="preset-date-actions">
       <button type="button" className="ghost" onClick={() => setOpen(false)}>Cancel</button>
       <button type="button" className="primary" onClick={apply}>Apply</button>
      </div>
     </div>
    </div>,
    document.body
   )}
  </div>
 );
}

function AmbientSystem() {
 return (
 <div className="ambient-system" aria-hidden="true">
 <div className="ambient-halo" />
 <div className="orbital-ring ring-one" />
 <div className="orbital-ring ring-two" />
 <i className="particle particle-one" />
 <i className="particle particle-two" />
 <i className="particle particle-three" />
 <i className="particle particle-four" />
 </div>
 );
}

function LoginPage({ checking = false, onSignedIn }: { checking?: boolean; onSignedIn: (token: string, user: string) => void }) {
 const [username, setUsername] = useState('admin');
 const [password, setPassword] = useState('');
 const [busy, setBusy] = useState(false);
 const [error, setError] = useState('');

 const submit = async (event: FormEvent<HTMLFormElement>) => {
  event.preventDefault();
  if (checking || busy) return;
  const token = basicAuthHeader(username.trim(), password);
  setBusy(true);
  setError('');
  try {
   const me = await api('/auth/me', { headers: { Authorization: token } });
   onSignedIn(token, me.user || username.trim());
  } catch (signInError: any) {
   setError(signInError.message || 'Sign-in failed');
  } finally {
   setBusy(false);
  }
 };

 return (
 <div className="login-screen">
 <div className="login-backdrop" aria-hidden="true" />
 <section className="login-card" aria-label="Sign in">
 <div className="login-visual" aria-hidden="true">
 <div className="login-visual-grid" />
 <div className="login-visual-shapes"><i /><i /><i /></div>
 <div className="login-visual-copy">
 <img src={explorerLogo} alt="" />
 <span>Explorer by SL</span>
 <b>Customer traffic forecasting</b>
 </div>
 </div>
 <form className="login-panel" onSubmit={submit}>
 <div className="login-form-inner">
 <div className="login-title">
 <span><Lock size={15} /> Private workspace</span>
 <h1>Welcome</h1>
 <p>Log in to your account to continue</p>
 </div>
 <label className="login-field">
 <span>Username</span>
 <div><UserCheck size={17} /><input value={username} autoComplete="username" disabled={checking || busy} onChange={(event) => setUsername(event.target.value)} /></div>
 </label>
 <label className="login-field">
 <span>Password</span>
 <div><Lock size={17} /><input type="password" value={password} autoComplete="current-password" disabled={checking || busy} onChange={(event) => setPassword(event.target.value)} autoFocus /></div>
 </label>
 {error && <p className="login-error" role="alert">{error}</p>}
 <button className="login-submit" type="submit" disabled={checking || busy || !username.trim() || !password}>
 {checking || busy ? <RefreshCw className="spin" size={16} /> : <Lock size={16} />}
 {checking ? 'Checking access' : busy ? 'Signing in' : 'Log In'}
 </button>
 <p className="login-note">Forecasts, uploads, and edit routes stay behind this private gate.</p>
 </div>
 </form>
 </section>
 </div>
 );
}

function Shell({ page, setPage, children, onSignOut }: { page: Page; setPage: (page: Page) => void; children: any; onSignOut?: () => void }) {
 const [open, setOpen] = useState(false);
 const [theme, toggleTheme] = useTheme();
 return (
 <div className="app-shell">
 <AmbientSystem />
 <button className="menu-btn floating-menu" aria-label="Open navigation" onClick={() => setOpen(true)}><Menu /></button>
 <aside className={open ? 'sidebar open' : 'sidebar'}>
 <div className="brand">
 <img className="brand-logo" src={explorerLogo} alt="Explorer by SL" />
 <button className="mobile-close" aria-label="Close navigation" onClick={() => setOpen(false)}><X /></button>
 </div>
 <nav>
 {navGroups.map((group) => (
 <div className="nav-group" key={group.label}>
 <div className="nav-label">{group.label}</div>
 {group.items.map(([name, Icon]) => (
 <button
 key={name}
 className={page === name ? 'active' : ''}
 aria-current={page === name ? 'page' : undefined}
 onClick={() => { setPage(name); setOpen(false); }}
 >
 <Icon size={18} /><span>{name}</span>
 </button>
 ))}
 </div>
 ))}
 </nav>
 <div className="sidebar-foot">
 <button
 type="button"
 className={`sidebar-theme-toggle is-${theme}`}
 onClick={toggleTheme}
 aria-label={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
 aria-pressed={theme === 'dark'}
 title={theme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
 >
 <span className="sidebar-theme-toggle-label">{theme === 'dark' ? 'Night mode' : 'Day mode'}</span>
 <span className="sidebar-theme-toggle-icon" aria-hidden="true">
 {theme === 'dark' ? <Moon size={27} strokeWidth={1.8} /> : <Sun size={27} strokeWidth={1.8} />}
 </span>
 </button>
 {onSignOut && (
 <button type="button" className="sidebar-logout" onClick={onSignOut}>
 <LogOut size={16} /><span>Sign out</span>
 </button>
 )}
 </div>
 </aside>
 {open && <button className="scrim" aria-label="Close navigation" onClick={() => setOpen(false)} />}
 <main>
 {children}
 </main>
 </div>
 );
}

function Metric({ label, value, suffix, sub, icon: Icon, index = 0, loading = false }: any) {
 const paths = [
 'M1 26 C12 21 16 28 25 18 S42 13 50 20 S66 5 80 11 S94 17 107 5',
 'M1 21 C10 28 19 11 28 18 S45 24 55 12 S72 6 81 17 S96 20 107 8',
 'M1 27 C15 24 18 16 31 20 S49 8 60 14 S74 4 86 10 S100 2 107 7',
 'M1 24 C12 18 20 25 29 15 S48 20 59 9 S73 17 84 7 S99 10 107 3',
 'M1 25 C14 27 19 17 31 19 S47 12 59 15 S76 5 87 9 S99 7 107 4',
 ];
 return (
 <article className="metric-card" style={{ '--delay': `${index * 70}ms` } as CSSProperties}>
 <div className="metric-top"><span>{label}</span><div className="metric-icon"><Icon size={17} /></div></div>
 {loading
 ? <div className="card-loading" aria-hidden="true"><div className="skeleton skeleton-line" style={{ width: '62%', height: 24 }} /><div className="skeleton skeleton-line" style={{ width: '40%' }} /></div>
 : <strong><AnimatedNumber value={Number(value) || 0} suffix={suffix} /></strong>}
 <div className="metric-bottom"><small>{sub}</small><svg viewBox="0 0 108 32" role="img" aria-label={`${label} trend`}><path d={paths[index % paths.length]} /></svg></div>
 </article>
 );
}

function ForecastSparkBars({ values, tone = 'neutral' }: { values: any[]; tone?: 'neutral' | 'good' | 'warm' }) {
 const nums = values.map((value) => Math.max(0, Number(value) || 0)).filter((value) => Number.isFinite(value));
 const bars = nums.length ? nums.slice(-18) : [4, 7, 5, 9, 8, 12, 10, 14, 13, 11, 15, 17];
 const max = Math.max(...bars, 1);
 return (
 <div className={`forecast-v2-spark ${tone}`} aria-hidden="true">
 {bars.map((value, index) => (
 <i
 key={`${value}-${index}`}
 style={{
 height: `${Math.max(4, Math.round((value / max) * 20))}px`,
 animationDelay: `${index * 28}ms`,
 } as CSSProperties}
 />
 ))}
 </div>
 );
}

const olsStat = (value: any, digits = 3) => value == null || !Number.isFinite(Number(value)) ? '-' : Number(value).toFixed(digits);
const olsPValue = (value: any) => value == null || !Number.isFinite(Number(value)) ? '-' : Number(value) < 0.001 ? '<0.001' : Number(value).toFixed(3);
// Cond. No. on the weekday block (see OLS-Declared-Ten-Variables.md) can land anywhere from
// single digits to 1e17+ depending on scope -- fixed-decimal would either truncate to "0" or
// print a 19-digit integer, so large values switch to scientific notation the way statsmodels'
// own summary table does.
const olsCondNo = (value: any) => value == null || !Number.isFinite(Number(value)) ? '-' : Number(value) >= 10000 ? Number(value).toExponential(2) : Number(value).toFixed(2);

// LOESS: locally weighted linear regression, sampled onto an evenly spaced grid across the
// observed x range. At each grid point, fits a weighted line using only the `k` nearest
// points (k = bandwidthFraction of n), weighted by the tricube kernel so nearby points count
// far more than distant ones — the shape bends where the local slope actually changes,
// unlike a single global OLS line. Falls back to no curve below 4 points (too few for a
// stable local neighborhood) or a zero-width x range.
function loessCurve(
 points: { x: number; y: number }[], bandwidthFraction = 0.6, gridSize = 60,
): { x: number; y: number }[] {
 if (points.length < 4) return [];
 const minX = Math.min(...points.map((p) => p.x));
 const maxX = Math.max(...points.map((p) => p.x));
 if (maxX <= minX) return [];
 const k = Math.max(3, Math.round(points.length * bandwidthFraction));
 const grid: { x: number; y: number }[] = [];
 for (let i = 0; i <= gridSize; i++) {
 const x0 = minX + ((maxX - minX) * i) / gridSize;
 const neighborhood = points
 .map((p) => ({ ...p, d: Math.abs(p.x - x0) }))
 .sort((a, b) => a.d - b.d)
 .slice(0, k);
 const maxD = neighborhood[neighborhood.length - 1].d || 1e-9;
 let sw = 0, swx = 0, swy = 0, swxx = 0, swxy = 0;
 for (const p of neighborhood) {
 const u = Math.min(1, p.d / maxD);
 const w = (1 - u ** 3) ** 3; // tricube kernel
 sw += w; swx += w * p.x; swy += w * p.y; swxx += w * p.x * p.x; swxy += w * p.x * p.y;
 }
 const denom = sw * swxx - swx * swx;
 const y0 = Math.abs(denom) < 1e-9
 ? (sw > 0 ? swy / sw : NaN)
 : (() => { const slope = (sw * swxy - swx * swy) / denom; const intercept = (swy - slope * swx) / sw; return slope * x0 + intercept; })();
 // Leads can't go negative — a local fit dipping below 0 near a low-spend cluster is a
 // fitting artifact, not a claim the model is making about actual lead volume.
 if (Number.isFinite(y0)) grid.push({ x: x0, y: Math.max(0, y0) });
 }
 return grid;
}

// Shared by the Forecast and Dataset pages so they cannot drift on the same regression.
// `coefficients` controls whether the coefficient table accompanies the fit quality;
// Forecast keeps the compact read, while Dataset presents the fuller diagnostic view.
// `view` narrows which of the two fits render (Dataset shows one at a time behind a tab),
// and `collapseTerms` truncates a long coefficient table to the top rows with a
// "show all" expander.
// The statsmodels-style "OLS Regression Results" printout -- summary block, full coefficient
// table (both CI bounds as separate columns, matching statsmodels' own [0.025, 0.975] layout
// rather than the compact "X to Y" single column the always-on coefficient table above uses),
// and the residual diagnostics row. Only ever rendered inside a "Show Detail" toggle, since it
// duplicates the fit-stat pills already visible above it.
function OlsDetailBlock({ summary }: { summary: any }) {
 const summaryRows: [string, string][] = [
  ['Dep. Variable', String(summary.dep_variable ?? '-')],
  ['Model', String(summary.model ?? '-')],
  ['Method', String(summary.method ?? '-')],
  ['Covariance Type', String(summary.covariance_type ?? '-')],
  ['No. Observations', fmt(summary.no_observations)],
  ['Df Residuals', fmt(summary.df_residuals)],
  ['Df Model', fmt(summary.df_model)],
  ['R-squared', olsStat(summary.r_squared, 3)],
  ['Adj. R-squared', olsStat(summary.adjusted_r_squared, 3)],
  ['F-statistic', olsStat(summary.f_statistic, 2)],
  ['Prob (F-statistic)', olsPValue(summary.f_p_value)],
  ['Log-Likelihood', olsStat(summary.log_likelihood, 2)],
  ['AIC', olsStat(summary.aic, 1)],
  ['BIC', olsStat(summary.bic, 1)],
 ];
 const diagnosticRows: [string, string][] = [
  ['Durbin-Watson', olsStat(summary.durbin_watson, 3)],
  ['Skew', olsStat(summary.skew, 3)],
  ['Kurtosis', olsStat(summary.kurtosis, 3)],
  ['Jarque-Bera (JB)', olsStat(summary.jarque_bera, 3)],
  ['Prob(JB)', olsPValue(summary.jarque_bera_p_value)],
  ['Cond. No.', olsCondNo(summary.cond_no)],
 ];
 return (
  <div className="model-gov-ols-detail">
   <div className="model-gov-ols-summary-grid">
    {summaryRows.map(([label, value]) => (
     <div className="model-gov-ols-summary-row" key={label}><span>{label}</span><b>{value}</b></div>
    ))}
   </div>
   <div className="model-gov-ols-detail-table">
    <div className="model-gov-ols-detail-table-head">
     <span>Term</span><span className="num">Coef</span><span className="num">Std err</span>
     <span className="num">t</span><span className="num">P&gt;|t|</span>
     <span className="num">[0.025</span><span className="num">0.975]</span>
    </div>
    {summary.coefficients.map((row: any) => (
     <div className="model-gov-ols-detail-table-row" key={row.feature}>
      <span>{row.term}</span>
      <span className="num">{olsStat(row.coef, 4)}</span>
      <span className="num">{olsStat(row.std_err, 4)}</span>
      <span className="num">{olsStat(row.t, 3)}</span>
      <span className="num">{olsPValue(row.p_value)}</span>
      <span className="num">{olsStat(row.ci_low, 3)}</span>
      <span className="num">{olsStat(row.ci_high, 3)}</span>
     </div>
    ))}
   </div>
   <div className="model-gov-ols-summary-grid model-gov-ols-diagnostics">
    {diagnosticRows.map(([label, value]) => (
     <div className="model-gov-ols-summary-row" key={label}><span>{label}</span><b>{value}</b></div>
    ))}
   </div>
  </div>
 );
}

// The forward-selection search that chose the Multivariate OLS card's variables, round by
// round. Every number here was computed during selection itself (backend
// `_forward_select_declared_features`) — without it the variable list reads as an unexplained
// verdict, and "why isn't spend in the model?" has no answer on the page.
// Each round lists every candidate that was tried, not just the winner: R2 for readability,
// adjusted R2 and its gain for the ranking, and the block F p-value for the entry gate. A
// variable enters only if it clears both gates, which is why a positive gain can still lose.
const OLS_SELECTION_STATUS: Record<string, string> = {
 eligible: 'eligible',
 no_gain: 'no gain',
 not_significant: 'not significant',
 rank: 'not estimable',
 observations: 'too few days',
};

function OlsSelectionPath({ selection }: { selection: any }) {
 const steps: any[] = selection?.steps || [];
 if (!steps.length) return null;
 const alpha = Number(selection?.alpha);
 return (
  <div className="model-gov-ols-detail model-gov-ols-selection">
   <p className="model-gov-ols-selection-rule">
    Greedy forward selection. Each round fits every remaining variable on top of the model so
    far and keeps the best one — it enters only if it raises adjusted R2 and its block F test
    clears p &lt; {Number.isFinite(alpha) ? alpha.toFixed(2) : '-'}. Whole variables enter
    together, so the seven day indicators and the four holiday buckets are one candidate each.
   </p>
   {steps.map((step: any) => (
    <div className="model-gov-ols-selection-step" key={step.round}>
     <div className="model-gov-ols-selection-head">
      <span>
       Round {step.round} · {step.action === 'drop' ? 'removed' : 'added'} <b>{step.winner_name}</b>
      </span>
      <span className="num">Adj R2 {olsStat(step.adjusted_r_squared, 4)}</span>
     </div>
     <div className="model-gov-ols-detail-table model-gov-ols-selection-table">
      <div className="model-gov-ols-detail-table-head">
       <span>Candidate</span><span className="num">R2</span><span className="num">Adj R2</span>
       <span className="num">Δ Adj R2</span><span className="num">P&gt;F</span><span>Outcome</span>
      </div>
      {(step.candidates || []).map((row: any) => (
       <div
        className={`model-gov-ols-detail-table-row${row.number === step.winner ? ' is-winner' : ''}`}
        key={`${step.round}-${row.number}`}
       >
        <span>{row.name}</span>
        <span className="num">{olsStat(row.r_squared, 4)}</span>
        <span className="num">{olsStat(row.adjusted_r_squared, 4)}</span>
        <span className="num">{row.gain == null ? '-' : `${row.gain >= 0 ? '+' : ''}${olsStat(row.gain, 4)}`}</span>
        <span className="num">{olsPValue(row.p_value)}</span>
        <span>{row.number === step.winner ? (step.action === 'drop' ? 'removed' : 'selected') : (OLS_SELECTION_STATUS[row.status] || row.status)}</span>
       </div>
      ))}
     </div>
    </div>
   ))}
  </div>
 );
}

function OlsResultCards(
 { ols, emptyCopy, className = '', coefficients = true, view, collapseTerms = false }:
 { ols: any; emptyCopy: string; className?: string; coefficients?: boolean; view?: 'univariate' | 'multivariate'; collapseTerms?: boolean },
) {
 const [expanded, setExpanded] = useState<Record<string, boolean>>({});
 const [detailOpen, setDetailOpen] = useState<Record<string, boolean>>({});
 const [pathOpen, setPathOpen] = useState(false);
 const selectionSteps: any[] = ols?.selection?.steps || [];
 const summaries = [
  { key: 'univariate', label: 'Spend-only OLS', summary: ols?.univariate },
  { key: 'multivariate', label: 'Multivariate OLS', summary: ols?.multivariate },
 ].filter((item) => item.summary && (!view || item.key === view));
 if (!summaries.length) return <div className={`table-empty ${className}`.trim()}>{emptyCopy}</div>;
 const fitRows = (summary: any) => [
  { label: 'R2', value: olsStat(summary?.r_squared, 3), warm: true },
  { label: 'Adj R2', value: olsStat(summary?.adjusted_r_squared, 3), warm: true },
  { label: 'F p-value', value: olsPValue(summary?.f_p_value), warm: Number(summary?.f_p_value) < 0.05 },
  { label: 'RMSE', value: olsStat(summary?.rmse, 2), warm: false },
 ];
 return (
  <section className={`model-gov-ols ${className}`.trim()} aria-label="OLS regression results">
   {summaries.map(({ key, label, summary }) => {
    const isCollapsed = collapseTerms && !expanded[key] && summary.coefficients.length > 6;
    const visibleTerms = isCollapsed ? summary.coefficients.slice(0, 6) : summary.coefficients;
    return (
     <article className="model-gov-ols-card" key={key}>
      <div className="model-gov-ols-card-head">
       <span>{label}</span>
       {coefficients && <b>{fmt(summary.features?.length || summary.df_model)} var{Number(summary.features?.length || summary.df_model) === 1 ? '' : 's'}</b>}
      </div>
      <div className={`model-gov-ols-fit${coefficients ? '' : ' is-only'}`}>
       {fitRows(summary).map((item) => <div key={`${key}-${item.label}`}><span>{item.label}</span><b className={item.warm ? 'warm' : ''}>{item.value}</b></div>)}
      </div>
      {coefficients && (
       <>
        <div className="model-gov-ols-table">
         <div className="model-gov-ols-table-head"><span>Term</span><span className="num">Coef</span><span className="num">Std err</span><span className="num">t</span><span className="num">P&gt;|t|</span><span className="num">95% CI</span></div>
         {visibleTerms.map((row: any) => (
          <div className="model-gov-ols-table-row" key={`${key}-${row.feature}`}>
           <span>{row.term}</span>
           <span className="num">{olsStat(row.coef, 4)}</span>
           <span className="num">{olsStat(row.std_err, 4)}</span>
           <span className="num">{olsStat(row.t, 3)}</span>
           <span className="num">{olsPValue(row.p_value)}</span>
           <span className="num">{olsStat(row.ci_low, 2)} to {olsStat(row.ci_high, 2)}</span>
          </div>
         ))}
        </div>
        {collapseTerms && summary.coefficients.length > 6 && (
         <button className="dataset-link-btn" onClick={() => setExpanded((s) => ({ ...s, [key]: !s[key] }))}>
          {isCollapsed ? `Show all ${summary.coefficients.length} terms` : 'Show fewer terms'}
         </button>
        )}
        <p className="model-gov-ols-features">{summary.features?.length ? `Variables: ${summary.features.join(', ')}` : 'No usable independent variables.'}</p>
       </>
      )}
      {!coefficients && (
       <>
        <button
         type="button"
         className="model-gov-ols-detail-toggle"
         aria-expanded={!!detailOpen[key]}
         onClick={() => setDetailOpen((s) => ({ ...s, [key]: !s[key] }))}
        >
         {detailOpen[key] ? 'Hide detail' : 'Show detail'}
         <ChevronDown size={13} className={detailOpen[key] ? 'is-open' : ''} />
        </button>
        {detailOpen[key] && <OlsDetailBlock summary={summary} />}
       </>
      )}
      {key === 'multivariate' && selectionSteps.length > 0 && (
       <>
        <button
         type="button"
         className="model-gov-ols-detail-toggle"
         aria-expanded={pathOpen}
         onClick={() => setPathOpen((open) => !open)}
        >
         {pathOpen ? 'Hide selection path' : `Show selection path (${selectionSteps.length} round${selectionSteps.length === 1 ? '' : 's'})`}
         <ChevronDown size={13} className={pathOpen ? 'is-open' : ''} />
        </button>
        {pathOpen && <OlsSelectionPath selection={ols.selection} />}
       </>
      )}
     </article>
    );
   })}
  </section>
 );
}

// Self-contained "Ad set change" recorder: button + popover, its own state and its own
// fetches keyed on whichever ad set the caller currently has scoped. Originally lived
// inline inside ForecastPage's toolbar; extracted 2026-08-06 so the Dataset page's scope
// bar can drop in the same recorder against its own selected ad set, without either page
// reaching into the other's state. See Vault/Data-Pipeline/Change-Event-UI-Recorder.md.
//
// `onChange` fires after every successful save/delete -- the caller's own correlation/OLS
// fetches don't know this popover exists, so without this they keep showing pre-edit
// numbers until an unrelated state change (or a full reload) happens to re-trigger them.
function ChangeEventButton({ adSetId, onChange, retraining = false }: { adSetId: string; onChange?: () => void; retraining?: boolean }) {
 const [changeEvents, setChangeEvents] = useState<any[]>([]);
 const [changeDraft, setChangeDraft] = useState({ scope: 'ad_set', start_date: '', end_date: '' });
 const [changeBusy, setChangeBusy] = useState(false);
 const [changeError, setChangeError] = useState('');
 const [changeRefreshKey, setChangeRefreshKey] = useState(0);
 const [changePanelOpen, setChangePanelOpen] = useState(false);
 // Third tab in the same popover: an ad set's true launch date (declared variable 4), not
 // a dated-range change event -- one confirmed fact per ad set, upserted, not appended.
 const [changeTab, setChangeTab] = useState<'ad_set' | 'ad' | 'start_date'>('ad_set');
 const [adSetStartDate, setAdSetStartDate] = useState<any>(null);
 const [startDateDraft, setStartDateDraft] = useState('');
 const [startDateBusy, setStartDateBusy] = useState(false);
 const [startDateError, setStartDateError] = useState('');
 const [startDateRefreshKey, setStartDateRefreshKey] = useState(0);
 // Whether the popover has flipped to open above the toggle instead of below it -- set by
 // the measure effect below, never toggled directly, so it can't fall out of sync with the
 // actual available space.
 const [opensUpward, setOpensUpward] = useState(false);
 const changePanelRef = useRef<HTMLDivElement>(null);
 const changeToggleRef = useRef<HTMLButtonElement>(null);
 const changePopoverBodyRef = useRef<HTMLDivElement>(null);

 // The toggle can sit anywhere on a long, scrollable page -- opening downward by default
 // (the CSS default) is fine near the top, but low on the page it pushes most of the
 // popover below the viewport, forcing the whole page to scroll to reach fields that are
 // otherwise fully rendered (verified: the popover's own content never overflows its box --
 // `scrollHeight === clientHeight` -- the box itself was just positioned off-screen).
 // Mirrors SingleDatePicker's `reposition()` above: measure real geometry, flip only when
 // there's more room the other way. A ResizeObserver re-measures as the popover's own
 // content changes height (switching tabs, a record appearing) without needing every state
 // that could affect that height listed as an effect dependency.
 useLayoutEffect(() => {
  if (!changePanelOpen) return;
  const toggle = changeToggleRef.current;
  const popover = changePopoverBodyRef.current;
  if (!toggle || !popover) return;
  const measure = () => {
   const toggleRect = toggle.getBoundingClientRect();
   const height = popover.getBoundingClientRect().height;
   const roomBelow = window.innerHeight - toggleRect.bottom - 8;
   const roomAbove = toggleRect.top - 8;
   setOpensUpward(height > roomBelow && roomAbove > roomBelow);
  };
  measure();
  const observer = new ResizeObserver(measure);
  observer.observe(popover);
  window.addEventListener('scroll', measure, true);
  window.addEventListener('resize', measure);
  return () => {
   observer.disconnect();
   window.removeEventListener('scroll', measure, true);
   window.removeEventListener('resize', measure);
  };
 }, [changePanelOpen]);

 // The type dropdown used to be a plain absolutely-positioned child of the popover -- fine
 // while the popover itself always fit the viewport, but the popover scrolls its own
 // overflow (`.budget-popover { overflow-y: auto }`), so a menu that opened low inside a
 // short popover got clipped by that scrollbar instead of the page's: exactly the "buried,
 // have to scroll to see it" report, and a different bug from the popover's own
 // off-viewport one above even though the symptom looks the same. Fixed the same way
 // SingleDatePicker's calendar already escapes this class of clipping: measured, `position:
 // fixed` coordinates, portaled to `document.body` (below) so the menu is never inside any
 // scrolling ancestor's box to begin with.


 useEffect(() => {
  if (!changePanelOpen) return;
  const closeOnPointer = (event: PointerEvent) => {
   const target = event.target as HTMLElement;
   if (changePanelRef.current?.contains(target)) return;
   // SingleDatePicker's calendar renders through a portal to document.body, so it is not
   // a DOM descendant of changePanelRef -- without this check, picking a date registers as
   // an outside click and closes the whole popover before onChange fires. The type dropdown
   // needed the same guard until it was removed with change type on 2026-08-11.
   if (target.closest?.('.mini-date-popover')) return;
   setChangePanelOpen(false);
  };
  const closeOnEscape = (event: KeyboardEvent) => {
   if (event.key === 'Escape') setChangePanelOpen(false);
  };
  document.addEventListener('pointerdown', closeOnPointer);
  document.addEventListener('keydown', closeOnEscape);
  return () => {
   document.removeEventListener('pointerdown', closeOnPointer);
   document.removeEventListener('keydown', closeOnEscape);
  };
 }, [changePanelOpen]);


 useEffect(() => {
  if (!adSetId) {
   setChangeEvents([]);
   return;
  }
  const controller = new AbortController();
  api(`/change-events?ad_set_id=${encodeURIComponent(String(adSetId))}`, { signal: controller.signal })
   .then((result: any) => {
    if (controller.signal.aborted) return;
    setChangeEvents(Array.isArray(result?.events) ? result.events : []);
   })
   .catch(() => {
    if (!controller.signal.aborted) setChangeEvents([]);
   });
  return () => controller.abort();
 }, [adSetId, changeRefreshKey]);

 useEffect(() => {
  if (!adSetId) {
   setAdSetStartDate(null);
   return;
  }
  const controller = new AbortController();
  api(`/ad-set-start-dates?ad_set_id=${encodeURIComponent(String(adSetId))}`, { signal: controller.signal })
   .then((result: any) => {
    if (controller.signal.aborted) return;
    setAdSetStartDate((Array.isArray(result?.dates) ? result.dates : [])[0] || null);
   })
   .catch(() => {
    if (!controller.signal.aborted) setAdSetStartDate(null);
   });
  return () => controller.abort();
 }, [adSetId, startDateRefreshKey]);

 const saveChangeEvent = async () => {
  if (!adSetId) return;
  setChangeError('');
  if (!changeDraft.start_date) {
   setChangeError('Pick a change type and the date it happened.');
   return;
  }
  setChangeBusy(true);
  try {
   await api('/change-events', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
     scope: changeDraft.scope,
     ad_set_id: String(adSetId),
     start_date: changeDraft.start_date,
    }),
   });
   setChangeDraft((current) => ({ ...current, start_date: '', end_date: '' }));
   setChangeRefreshKey((key) => key + 1);
   onChange?.();
  } catch (error: any) {
   setChangeError(error.message || 'Unable to record this change.');
  } finally {
   setChangeBusy(false);
  }
 };
 const deleteChangeEvent = async (id: number) => {
  setChangeError('');
  setChangeBusy(true);
  try {
   await api(`/change-events/${id}`, { method: 'DELETE' });
   setChangeRefreshKey((key) => key + 1);
   onChange?.();
  } catch (error: any) {
   setChangeError(error.message || 'Unable to delete this change.');
  } finally {
   setChangeBusy(false);
  }
 };
 const saveAdSetStartDate = async () => {
  if (!adSetId) return;
  setStartDateError('');
  if (!startDateDraft) {
   setStartDateError('Pick the ad set\'s true start date.');
   return;
  }
  setStartDateBusy(true);
  try {
   await api('/ad-set-start-dates', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ad_set_id: String(adSetId), start_date: startDateDraft }),
   });
   setStartDateDraft('');
   setStartDateRefreshKey((key) => key + 1);
   onChange?.();
  } catch (error: any) {
   setStartDateError(error.message || 'Unable to record this start date.');
  } finally {
   setStartDateBusy(false);
  }
 };
 const deleteAdSetStartDate = async () => {
  if (!adSetStartDate) return;
  setStartDateError('');
  setStartDateBusy(true);
  try {
   await api(`/ad-set-start-dates/${encodeURIComponent(String(adSetStartDate.ad_set_id))}`, { method: 'DELETE' });
   setStartDateRefreshKey((key) => key + 1);
   onChange?.();
  } catch (error: any) {
   setStartDateError(error.message || 'Unable to delete this start date.');
  } finally {
   setStartDateBusy(false);
  }
 };

 return (
  <div className="budget-toggle-wrap" ref={changePanelRef}>
   <button
    ref={changeToggleRef}
    type="button"
    className={`budget-toggle${changePanelOpen ? ' open' : ''}`}
    aria-expanded={changePanelOpen}
    aria-haspopup="dialog"
    onClick={() => setChangePanelOpen((wasOpen) => !wasOpen)}
   >
    <Pencil size={15} />
    <span>Ad set change</span>
    {changeEvents.length > 0 && <em className="change-count">{changeEvents.length}</em>}
    <ChevronDown size={15} className="budget-toggle-caret" />
   </button>
   {changePanelOpen && (
    <div
     ref={changePopoverBodyRef}
     className={`budget-popover change-popover${opensUpward ? ' opens-upward' : ''}`}
     role="dialog"
     aria-label="Ad set change"
    >
     <div className="budget-popover-head">
      <span><Pencil size={13} /> Change log</span>
      <button type="button" className="budget-popover-close" aria-label="Close" onClick={() => setChangePanelOpen(false)}><X size={14} /></button>
     </div>
     {retraining && (
      <p className="change-retrain-note" role="status">
       <RefreshCw size={12} className="change-retrain-spin" />
       Retraining forecasts with this change…
      </p>
     )}
     {!adSetId ? (
      <p className="budget-popover-empty">Select an ad set to record its changes.</p>
     ) : (
      <>
       <div
        className="metric-toggle change-scope-toggle"
        role="tablist"
        aria-label="Change scope"
        style={{ '--tab-index': CHANGE_TAB_ORDER.indexOf(changeTab) } as CSSProperties}
       >
        <span className="change-scope-indicator" aria-hidden="true" />
        {CHANGE_SCOPES.map((scope) => (
         <button
          key={scope.key}
          type="button"
          role="tab"
          aria-selected={changeTab === scope.key}
          className={changeTab === scope.key ? 'active' : ''}
          onClick={() => { setChangeTab(scope.key); setChangeDraft((current) => ({ ...current, scope: scope.key })); }}
         >
          {scope.label}
         </button>
        ))}
        <button
         type="button"
         role="tab"
         aria-selected={changeTab === 'start_date'}
         className={changeTab === 'start_date' ? 'active' : ''}
         onClick={() => setChangeTab('start_date')}
        >
         Start date
        </button>
       </div>
       {changeTab === 'start_date' ? (
        <>
         <div className="change-entry-row change-entry-row-solo">
          <label className="change-entry-field">
           <span>Started on</span>
           <SingleDatePicker value={startDateDraft} onChange={setStartDateDraft} ariaLabel="Ad set start date" />
          </label>
          <button type="button" className="budget-table-add" aria-label="Record start date" disabled={startDateBusy || !startDateDraft} onClick={() => void saveAdSetStartDate()}><Plus size={14} /></button>
         </div>
         {startDateError && <p className="budget-popover-error">{startDateError}</p>}
         {adSetStartDate && (
          <div className="budget-history-section">
           <div className="budget-history-head"><span>Recorded start date</span></div>
           <div className="budget-table">
            <div className="budget-table-row">
             <div className="budget-table-dates">
              <span>{dateFmt(adSetStartDate.start_date)}</span>
              <small>Ad set launch date</small>
             </div>
             <button type="button" className="budget-table-delete" aria-label="Delete recorded start date" disabled={startDateBusy} onClick={() => void deleteAdSetStartDate()}><X size={13} /></button>
            </div>
           </div>
          </div>
         )}
        </>
       ) : (
        <>
         <div className="change-entry-row">
          <label className="change-entry-field">
           <span>Date</span>
           <SingleDatePicker value={changeDraft.start_date} onChange={(next) => setChangeDraft((current) => ({ ...current, start_date: next }))} ariaLabel="Date of change" />
          </label>
          <button type="button" className="budget-table-add" aria-label="Record change" disabled={changeBusy || !changeDraft.start_date} onClick={() => void saveChangeEvent()}><Plus size={14} /></button>
         </div>
         {changeError && <p className="budget-popover-error">{changeError}</p>}
         {changeEvents.length > 0 && (
          <div className="budget-history-section">
           <div className="budget-history-head">
            <span>Recorded changes</span>
           </div>
           <div className="budget-table">
            {changeEvents.map((event: any, index: number) => (
             <div className="budget-table-row" key={event.id} style={{ '--stagger': Math.min(index, 6) } as CSSProperties}>
              <div className="budget-table-dates">
               <span>{dateFmt(event.start_date)}</span>
               <small>{event.scope === 'ad_set' ? 'Ad set' : 'Ad'}{event.from_upload ? ' · imported' : ''}</small>
              </div>
              <button type="button" className="budget-table-delete" aria-label="Delete recorded change" disabled={changeBusy} onClick={() => deleteChangeEvent(event.id)}><X size={13} /></button>
             </div>
            ))}
           </div>
          </div>
         )}
        </>
       )}
      </>
     )}
    </div>
   )}
  </div>
 );
}

function ForecastPage() {
 const [summary, setSummary] = useState<any>({});
 const [insights, setInsights] = useState<any>({ statuses: [], campaigns: [] });
 const [forecastTracking, setForecastTracking] = useState<any>({ summary: {}, timeline: [] });
 const [adSpend, setAdSpend] = useState<any>({ available: false, summary: {}, daily: [], campaigns: [], ad_sets: [] });
 const [sets, setSets] = useState<any[]>([]);
 const [query, setQuery] = useState('');
 const [selectedId, setSelectedId] = useState('');
 const [history, setHistory] = useState<any[]>([]);
 const [busy, setBusy] = useState(true);
 const [lookupError, setLookupError] = useState('');
 const [copiedField, setCopiedField] = useState<'adset' | 'campaign' | ''>('');
 const [showAllCampaigns, setShowAllCampaigns] = useState(false);
 const [selectedCampaignId, setSelectedCampaignId] = useState('');
 const [adSetMeasure, setAdSetMeasure] = useState<'leads' | 'share'>('leads');
 const [showForecast, setShowForecast] = useState(false);
 const [trackingStartDate, setTrackingStartDate] = useState('');
 const [trackingEndDate, setTrackingEndDate] = useState('');
 const [pendingLookupTerm, setPendingLookupTerm] = useState('');
 const [campaignPickerOpen, setCampaignPickerOpen] = useState(false);
 const [cplView, setCplView] = useState<'campaign' | 'adset'>('campaign');
 const [selectedLeadPoint, setSelectedLeadPoint] = useState<any>(null);
 const [leadDrilldownClosing, setLeadDrilldownClosing] = useState(false);
 const [leadDrilldownRows, setLeadDrilldownRows] = useState<any[]>([]);
 const [leadDrilldownBusy, setLeadDrilldownBusy] = useState(false);
 const [leadDrilldownError, setLeadDrilldownError] = useState('');
 const [leadActionBusy, setLeadActionBusy] = useState(false);
 const [leadActionError, setLeadActionError] = useState('');
 const [scenario, setScenario] = useState<any>(null);
 const [scenarioBusy, setScenarioBusy] = useState(false);
 const [scenarioError, setScenarioError] = useState('');
 const [baselineScenario, setBaselineScenario] = useState<any>(null);
 const [baselineBusy, setBaselineBusy] = useState(false);
 const [scenarioParams, setScenarioParams] = useState({
 future_spend_daily: '',
 });
 const [budgetPeriods, setBudgetPeriods] = useState<any[]>([]);
 const [allBudgetPeriods, setAllBudgetPeriods] = useState<any[]>([]);
 const [budgetDraft, setBudgetDraft] = useState({ start_date: '', end_date: '', daily_budget: '' });
 const [budgetBusy, setBudgetBusy] = useState(false);
 const [budgetError, setBudgetError] = useState('');
 const [budgetRefreshKey, setBudgetRefreshKey] = useState(0);
 const [budgetPanelOpen, setBudgetPanelOpen] = useState(false);
 const [ols, setOls] = useState<any>({ univariate: null, multivariate: null });
 const [olsBusy, setOlsBusy] = useState(false);
 // Bumped by ChangeEventButton's onChange, same reason as Dataset page's dataRefreshKey:
 // recording or deleting a change/start date changes what this fit computes, but nothing
 // else here re-runs the /ols-summary call when that happens.
 const [modelRefreshKey, setModelRefreshKey] = useState(0);
 const lookupInput = useRef<HTMLInputElement>(null);
 const selectedResultPanel = useRef<HTMLElement>(null);
 const leadDrilldownPanel = useRef<HTMLDivElement>(null);
 const campaignPickerRef = useRef<HTMLDivElement>(null);
 const budgetPanelRef = useRef<HTMLDivElement>(null);
 const trackingRequestId = useRef(0);
 const olsRequestId = useRef(0);

 const load = async () => {
 setBusy(true);
 try {
 const [summaryData, insightData, adSets, adSpendData] = await Promise.all([
 api('/dashboard/summary'),
 api('/dashboard/insights'),
 api('/ad-sets'),
 api('/dashboard/ad-spend').catch(() => ({ available: false, summary: {}, daily: [], campaigns: [], ad_sets: [] })),
 ]);
 const nextCampaignId = insightData.campaigns?.some((item: any) => item.campaign_id === selectedCampaignId)
 ? selectedCampaignId
 : insightData.campaigns?.[0]?.campaign_id || '';
 setSummary(summaryData);
 setInsights(insightData);
 setSets(adSets);
 setAdSpend(adSpendData);
 setSelectedCampaignId(String(nextCampaignId));
 setSelectedId((current) => {
 if (!current) return '';
 if (adSets.some((item: any) => String(item.utm_ad_set_id) === String(current))) return current;
 return '';
 });
 } finally {
 setBusy(false);
 }
 };

 useEffect(() => { void load(); }, []);

 // Unlike the Dataset page, this one renders stored forecasts, so it needs a second refresh
 // once the background retrain finishes -- the first (on save) only corrects the live OLS fit.
 const { retraining, watchRetrain } = useRetrainWatcher(() => {
 void load();
 setModelRefreshKey((key) => key + 1);
 });

 useEffect(() => {
 if (!selectedId) {
 setHistory([]);
 return;
 }
 setQuery((current) => current.trim() ? current : String(selectedId));
 setHistory([]);
 api(`/history?ad_set_id=${encodeURIComponent(selectedId)}`)
 .then(setHistory)
 .catch(() => setHistory([]));
 }, [selectedId]);

 const revealSelectedResult = () => {
 window.setTimeout(() => {
 selectedResultPanel.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
 }, 80);
 };

 const findAdSetLookupMatch = (rawTerm: string, sourceSets = sets) => {
 const term = rawTerm.toLowerCase();
 const numericTerm = rawTerm.replace(/\D/g, '');

 const exactMatch = sourceSets.find((item) => {
 const id = String(item.utm_ad_set_id);
 return id.toLowerCase() === term || (numericTerm && id === numericTerm);
 });
 const prefixMatches = exactMatch ? [] : sourceSets.filter((item) => (
 numericTerm.length >= 6 && String(item.utm_ad_set_id).startsWith(numericTerm)
 ));
 return exactMatch || (prefixMatches.length === 1 ? prefixMatches[0] : null);
 };

 const findSingleCampaignAdSetLookupMatch = (rawTerm: string, sourceSets = sets) => {
 const term = rawTerm.toLowerCase();
 const numericTerm = rawTerm.replace(/\D/g, '');
 const campaignMatches = sourceSets.filter((item) => {
 const campaignId = String(item.utm_campaign_id || '');
 return campaignId.toLowerCase() === term || (numericTerm && campaignId === numericTerm);
 });
 return campaignMatches.length === 1 ? campaignMatches[0] : null;
 };

 const fetchAdSetLookupMatches = async (rawTerm: string) => {
 const remoteSets = await api(`/ad-sets?q=${encodeURIComponent(rawTerm)}`);
 return Array.isArray(remoteSets) ? remoteSets : [];
 };

 const applyAdSetLookupMatch = (match: any, options: { reveal?: boolean } = {}) => {
 setLookupError('');
 setPendingLookupTerm('');
 setQuery(String(match.utm_ad_set_id));
 setSelectedId(match.utm_ad_set_id);
 lookupInput.current?.blur();
 if (options.reveal) revealSelectedResult();
 };

 const completeAdSetLookup = async (options: { reveal?: boolean } = {}, rawValue?: string) => {
 const rawTerm = (rawValue ?? lookupInput.current?.value ?? query).trim();
 const term = rawTerm.toLowerCase();
 if (!term) {
 setLookupError('Enter an Ad Set ID to view its history.');
 lookupInput.current?.focus();
 return;
 }

 if (!sets.length) {
 setLookupError('');
 setPendingLookupTerm(rawTerm);
 return;
 }

 let match = findAdSetLookupMatch(rawTerm) || findSingleCampaignAdSetLookupMatch(rawTerm);
 if (!match) {
 const remoteSets = await fetchAdSetLookupMatches(rawTerm).catch(() => []);
 match = findAdSetLookupMatch(rawTerm, remoteSets) || findSingleCampaignAdSetLookupMatch(rawTerm, remoteSets);
 if (match) {
 setSets((current) => current.some((item) => String(item.utm_ad_set_id) === String(match.utm_ad_set_id))
 ? current
 : [...current, match]);
 }
 }
 if (!match) {
 setLookupError('No exact Ad Set ID was found. Check the ID and try again.');
 lookupInput.current?.focus();
 return;
 }

 applyAdSetLookupMatch(match, options);
 };

 useEffect(() => {
 if (!pendingLookupTerm || !sets.length) return;
 const match = findAdSetLookupMatch(pendingLookupTerm, sets) || findSingleCampaignAdSetLookupMatch(pendingLookupTerm, sets);
 if (match) {
 applyAdSetLookupMatch(match, { reveal: true });
 return;
 }
 setPendingLookupTerm('');
 setLookupError('No exact Ad Set ID was found. Check the ID and try again.');
 lookupInput.current?.focus();
 }, [pendingLookupTerm, sets]);

 useEffect(() => {
 const selectedSetCampaign = sets.find((item) => String(item.utm_ad_set_id) === String(selectedId))?.utm_campaign_id;
 if (selectedSetCampaign && String(selectedSetCampaign) !== String(selectedCampaignId)) {
 setSelectedCampaignId(String(selectedSetCampaign));
 }
 }, [selectedId, sets, selectedCampaignId]);

 useEffect(() => {
 if (!selectedCampaignId) return;
 setSelectedLeadPoint(null);
 setLeadDrilldownRows([]);
 setLeadDrilldownError('');
 const requestedCampaignId = String(selectedCampaignId);
 const requestedAdSetId = String(selectedId || '');
 const requestId = ++trackingRequestId.current;
 const controller = new AbortController();
 const params = new URLSearchParams();
 params.set('campaign_id', requestedCampaignId);
 if (requestedAdSetId) params.set('ad_set_id', requestedAdSetId);
 api(`/dashboard/forecast-tracking?${params.toString()}`, { signal: controller.signal })
 .then((trackingData) => {
 if (controller.signal.aborted || requestId !== trackingRequestId.current) return;
 if (String(trackingData.summary?.campaign_id || '') !== requestedCampaignId) {
 throw new Error('The backend returned data for a different campaign.');
 }
 if (requestedAdSetId && String(trackingData.summary?.ad_set_id || '') !== requestedAdSetId) {
 throw new Error('The backend returned data for a different ad set.');
 }
 setForecastTracking(trackingData);
 setTrackingStartDate(trackingData.timeline?.[0]?.date || '');
 setTrackingEndDate(trackingData.timeline?.[trackingData.timeline.length - 1]?.date || '');
 })
 .catch(() => {
 if (!controller.signal.aborted && requestId === trackingRequestId.current) {
 setForecastTracking({ summary: {}, timeline: [] });
 }
 });
 return () => controller.abort();
 }, [selectedCampaignId, selectedId]);

 // Deliberately keyed on the same two values as the tracking chart above it, so the
 // regression always describes exactly the data that chart is drawing. Late responses are
 // dropped by request id -- switching campaigns quickly must not leave the previous
 // campaign's regression sitting under the new campaign's chart.
 useEffect(() => {
 if (!selectedCampaignId) return;
 const requestId = ++olsRequestId.current;
 const controller = new AbortController();
 const params = new URLSearchParams();
 params.set('campaign_id', String(selectedCampaignId));
 if (selectedId) params.set('ad_set_id', String(selectedId));
 setOlsBusy(true);
 api(`/ols-summary?${params.toString()}`, { signal: controller.signal })
 .then((data) => {
 if (controller.signal.aborted || requestId !== olsRequestId.current) return;
 setOls(data);
 setOlsBusy(false);
 })
 .catch(() => {
 if (!controller.signal.aborted && requestId === olsRequestId.current) {
 setOls({ univariate: null, multivariate: null });
 setOlsBusy(false);
 }
 });
 return () => controller.abort();
 }, [selectedCampaignId, selectedId, modelRefreshKey]);

 useEffect(() => {
 if (!selectedId) {
 setScenario(null);
 return;
 }
 const controller = new AbortController();
 const timeout = window.setTimeout(() => {
 setScenarioBusy(true);
 setScenarioError('');
 const params = new URLSearchParams();
 params.set('ad_set_id', String(selectedId));
 params.set('horizon', '14');
 Object.entries(scenarioParams).forEach(([key, value]) => {
 if (key === 'future_spend_daily' && String(value).trim() === '') return;
 params.set(key, String(value));
 });
 api(`/forecast-scenario?${params.toString()}`, { signal: controller.signal })
 .then((result) => {
 if (!controller.signal.aborted) setScenario(result);
 })
 .catch((error: any) => {
 if (!controller.signal.aborted) {
 setScenario(null);
 setScenarioError(error.message || 'Unable to generate scenario forecast.');
 }
 })
 .finally(() => {
 if (!controller.signal.aborted) setScenarioBusy(false);
 });
 }, 250);
 return () => {
 window.clearTimeout(timeout);
 controller.abort();
 };
 }, [selectedId, scenarioParams, budgetRefreshKey]);

 useEffect(() => {
 if (!selectedId) {
 setBaselineScenario(null);
 return;
 }
 const controller = new AbortController();
 setBaselineBusy(true);
 const params = new URLSearchParams();
 params.set('ad_set_id', String(selectedId));
 params.set('horizon', '14');
 api(`/forecast-scenario?${params.toString()}`, { signal: controller.signal })
 .then((result) => {
 if (!controller.signal.aborted) setBaselineScenario(result);
 })
 .catch(() => {
 if (!controller.signal.aborted) setBaselineScenario(null);
 })
 .finally(() => {
 if (!controller.signal.aborted) setBaselineBusy(false);
 });
 return () => controller.abort();
 }, [selectedId, budgetRefreshKey]);

 useEffect(() => {
 const controller = new AbortController();
 api('/budget-periods', { signal: controller.signal })
 .then((result) => {
 if (!controller.signal.aborted) setAllBudgetPeriods(Array.isArray(result) ? result : []);
 })
 .catch(() => {
 if (!controller.signal.aborted) setAllBudgetPeriods([]);
 });
 return () => controller.abort();
 }, [budgetRefreshKey]);

 useEffect(() => {
 if (!selectedId) {
 setBudgetPeriods([]);
 setBudgetError('');
 return;
 }
 const controller = new AbortController();
 api(`/budget-periods?ad_set_id=${encodeURIComponent(String(selectedId))}`, { signal: controller.signal })
 .then((result) => {
 if (!controller.signal.aborted) setBudgetPeriods(Array.isArray(result) ? result : []);
 })
 .catch(() => {
 if (!controller.signal.aborted) setBudgetPeriods([]);
 });
 return () => controller.abort();
 }, [selectedId, budgetRefreshKey]);

 useEffect(() => {
 if (!campaignPickerOpen) return;
 const closeOnPointer = (event: PointerEvent) => {
 if (!campaignPickerRef.current?.contains(event.target as Node)) setCampaignPickerOpen(false);
 };
 const closeOnEscape = (event: KeyboardEvent) => {
 if (event.key === 'Escape') setCampaignPickerOpen(false);
 };
 document.addEventListener('pointerdown', closeOnPointer);
 document.addEventListener('keydown', closeOnEscape);
 return () => {
 document.removeEventListener('pointerdown', closeOnPointer);
 document.removeEventListener('keydown', closeOnEscape);
 };
 }, [campaignPickerOpen]);

 useEffect(() => {
 if (!budgetPanelOpen) return;
 const closeOnPointer = (event: PointerEvent) => {
 const target = event.target as HTMLElement;
 if (budgetPanelRef.current?.contains(target)) return;
 if (target.closest?.('.mini-date-popover')) return;
 setBudgetPanelOpen(false);
 };
 const closeOnEscape = (event: KeyboardEvent) => {
 if (event.key === 'Escape') setBudgetPanelOpen(false);
 };
 document.addEventListener('pointerdown', closeOnPointer);
 document.addEventListener('keydown', closeOnEscape);
 return () => {
 document.removeEventListener('pointerdown', closeOnPointer);
 document.removeEventListener('keydown', closeOnEscape);
 };
 }, [budgetPanelOpen]);

 const selectPortfolioAdSet = (adSetId: string) => {
 if (!adSetId) return;
 setLookupError('');
 setCopiedField('');
 setQuery(adSetId);
 setSelectedId(adSetId);
 };

 const copyIdentifier = async (field: 'adset' | 'campaign', value: string) => {
 try {
 await navigator.clipboard.writeText(value);
 } catch {
 const input = document.createElement('textarea');
 input.value = value;
 input.style.position = 'fixed';
 input.style.opacity = '0';
 document.body.appendChild(input);
 input.select();
 document.execCommand('copy');
 input.remove();
 }
 setCopiedField(field);
 window.setTimeout(() => setCopiedField((current) => current === field ? '' : current), 1600);
 };

 const recentDelta = summary.previous_7_leads
 ? Math.round((summary.recent_7_leads - summary.previous_7_leads) / summary.previous_7_leads * 100)
 : 0;
 const selectedTotal = history.reduce((total, point) => total + Number(point.leads || 0), 0);
 const selectedRecent7 = history.slice(-7).reduce((total, point) => total + Number(point.leads || 0), 0);
 const statusMix = useMemo(() => (insights.statuses || []).map((item: any, index: number) => ({
 ...item,
 sharePercent: Number(item.share || 0) * 100,
 color: item.status === 'New' ? 'var(--yellow)' : item.status === 'Existing' ? 'var(--series-actual)' : 'var(--dim)',
 order: index,
 })), [insights]);
 const campaignMix = useMemo(() => (insights.campaigns || []).map((item: any, index: number) => ({
 ...item,
 rank: index + 1,
 sharePercent: Number(item.share || 0) * 100,
 })), [insights]);
 const campaignOptions = useMemo(() => campaignMix.map((campaign: any) => {
 const campaignSets = sets
 .filter((item) => String(item.utm_campaign_id) === String(campaign.campaign_id))
 .sort((a, b) => Number(b.total_leads || 0) - Number(a.total_leads || 0));
 return {
 ...campaign,
 primaryAdSetId: campaignSets[0]?.utm_ad_set_id ? String(campaignSets[0].utm_ad_set_id) : '',
 adSetCount: campaignSets.length,
 };
 }).filter((campaign: any) => campaign.primaryAdSetId), [campaignMix, sets]);
 const visibleCampaigns = showAllCampaigns ? campaignMix : campaignMix.slice(0, 10);
 const selectedCampaign = campaignMix.find((item: any) => item.campaign_id === selectedCampaignId) || campaignMix[0] || null;
 const selectedCampaignOption = campaignOptions.find((item: any) => String(item.campaign_id) === String(selectedCampaignId)) || campaignOptions[0] || null;
 const trackingScopeName = selectedId
 ? `${selectedCampaignOption?.campaign || 'Selected campaign'} - Ad set ${String(selectedId).slice(-6)}`
 : selectedCampaignOption?.campaign || 'Portfolio';
 const selectCampaignFromDropdown = (campaignId: string) => {
 setSelectedCampaignId(campaignId);
 setCampaignPickerOpen(false);
 setLookupError('');
 setCopiedField('');
 setQuery('');
 setSelectedId('');
 };
 const allTrackingTimeline = forecastTracking.timeline || [];
 const trackingPhases = forecastTracking.phases || [];
 const trackingMinDate = allTrackingTimeline[0]?.date || '';
 const trackingMaxDate = allTrackingTimeline[allTrackingTimeline.length - 1]?.date || '';
 const trackingTimeline = useMemo(() => allTrackingTimeline.filter((point: any) => (
 (!trackingStartDate || point.date >= trackingStartDate)
 && (!trackingEndDate || point.date <= trackingEndDate)
 )), [allTrackingTimeline, trackingStartDate, trackingEndDate]);
 const trackingStats = useMemo(() => {
 const actualPoints = trackingTimeline.filter((point: any) => point.actual_leads != null);
 const forecastPoints = trackingTimeline.filter((point: any) => point.forecast_leads != null);
 const comparisons = trackingTimeline.filter((point: any) => point.actual_leads != null && point.forecast_leads != null);
 const actualTotal = actualPoints.reduce((sum: number, point: any) => sum + Number(point.actual_leads || 0), 0);
 const forecastTotal = forecastPoints.reduce((sum: number, point: any) => sum + Number(point.forecast_leads || 0), 0);
 const mae = comparisons.length
 ? comparisons.reduce((sum: number, point: any) => sum + Math.abs(Number(point.forecast_leads) - Number(point.actual_leads)), 0) / comparisons.length
 : null;
 return {
 lastActualDate: actualPoints[actualPoints.length - 1]?.date || null,
 actualTotal,
 forecastTotal,
 mae,
 comparisonDays: comparisons.length,
 };
 }, [trackingTimeline]);
 const chartTrackingTimeline = useMemo(() => {
 if (showForecast || !trackingStats.lastActualDate) return trackingTimeline;
 return trackingTimeline.filter((point: any) => point.date <= trackingStats.lastActualDate);
 }, [showForecast, trackingTimeline, trackingStats.lastActualDate]);
 const trackingPhaseIds = new Set(trackingTimeline.map((point: any) => point.phase_id).filter(Boolean));
 const bestPhaseByStart = new Map<string, any>();
 trackingPhases.forEach((phase: any) => {
 const existing = bestPhaseByStart.get(phase.active_start);
 if (!existing) { bestPhaseByStart.set(phase.active_start, phase); return; }
 const existingHasData = trackingPhaseIds.has(existing.phase_id);
 const phaseHasData = trackingPhaseIds.has(phase.phase_id);
 if (phaseHasData && !existingHasData) bestPhaseByStart.set(phase.active_start, phase);
 else if (phaseHasData === existingHasData && phase.phase_number > existing.phase_number) bestPhaseByStart.set(phase.active_start, phase);
 });
 const visibleTrackingPhases = trackingPhases.filter((phase: any) => (
 (!trackingStartDate || phase.active_end >= trackingStartDate)
 && (!trackingEndDate || phase.active_start <= trackingEndDate)
 ) && bestPhaseByStart.get(phase.active_start) === phase);
 // The regression is fit on whatever the chart above is showing, which is why it renders
 // inside that chart's container with no heading of its own. The scope is therefore never
 // labelled here -- the only place a thin scope announces itself is the empty-state copy
 // below, so that copy has to stay specific about the day count.
 const olsScope = ols?.scope || {};
 const olsDays = olsScope.observations || ols?.univariate?.no_observations || 0;
 const olsEmptyCopy = !olsDays
 ? 'No leads recorded for this selection yet.'
 : olsDays < (olsScope.univariate_days_needed || 12)
 ? `Only ${plural(olsDays, 'day')} of data in this scope - a regression needs at least ${olsScope.univariate_days_needed || 12}. Pick a wider scope.`
 : 'Upload ad performance data with spend before OLS regression results are available.';
 // A multivariate card can vanish on its own while spend-only still fits, for two unrelated
 // reasons: the scope is too short for the terms it wants, or (since forward selection drives
 // this card) no declared variable beat an intercept-only model on adjusted R-squared. Those
 // read identically -- one missing card -- so the copy has to name which one happened.
 const olsMultivariateNote = !(ols?.univariate && !ols?.multivariate && olsScope.multivariate_days_needed)
 ? ''
 : olsScope.multivariate_terms_wanted
 ? `Multivariate fit needs ${olsScope.multivariate_days_needed} days for its ${olsScope.multivariate_terms_wanted} terms; this scope has ${olsDays}.`
 : 'Forward selection kept no declared variable here - none improved adjusted R-squared over predicting the average day. See the variable dictionary for the margin on each one.';
 // A phase marker needs roughly four days of x-axis to fit its label. Boundaries closer
 // than that still get their divider line, but only the first keeps the caption, so two
 // captions can never be painted over each other.
 const trackingDateIndex = new Map<string, number>(trackingTimeline.map((point: any, i: number) => [point.date, i]));
 const phaseLabelVisible = useMemo(() => {
 const out = new Set<string>();
 let lastLabelled: number | null = null;
 visibleTrackingPhases.forEach((phase: any) => {
 const at = trackingDateIndex.get(phase.active_start);
 if (at == null) return;
 if (lastLabelled == null || at - lastLabelled >= 4) { out.add(phase.phase_id); lastLabelled = at; }
 });
 return out;
 }, [visibleTrackingPhases, trackingTimeline]);
 const scenarioDaily = scenario?.daily || [];
 const scenarioByDate = useMemo(() => {
 const mapped: Record<string, any> = {};
 scenarioDaily.forEach((day: any) => { mapped[day.date] = day; });
 return mapped;
 }, [scenarioDaily]);
 const scenarioTrackingTimeline = useMemo(() => trackingTimeline.map((point: any) => {
 const day = scenarioByDate[point.date];
 return day ? {
 ...point,
 scenario_leads: day.predicted,
 scenario_lower: day.lower,
 scenario_upper: day.upper,
 scenario_model: day.model,
 } : point;
 }), [trackingTimeline, scenarioByDate]);
 const scenarioComponents = scenario?.components || {};
 const scenarioComparison = useMemo(() => {
 if (!scenario || !baselineScenario) return null;
 const baseTotal = Number(baselineScenario.predicted_total || 0);
 const newTotal = Number(scenario.predicted_total || 0);
 const delta = newTotal - baseTotal;
 return {
 baseTotal,
 newTotal,
 delta,
 deltaPct: baseTotal > 0 ? (delta / baseTotal) * 100 : null,
 multiplier: baseTotal > 0 ? newTotal / baseTotal : null,
 };
 }, [scenario, baselineScenario]);
 const setScenarioParam = (name: keyof typeof scenarioParams, value: string) => {
 setScenarioParams((current) => ({ ...current, [name]: value }));
 };
 const budgetInfo = scenario?.budget || baselineScenario?.budget || null;
 const saveBudgetPeriod = async () => {
 if (!selectedId) return;
 setBudgetError('');
 if (!budgetDraft.start_date || !budgetDraft.end_date || String(budgetDraft.daily_budget).trim() === '') {
 setBudgetError('Enter a start date, end date, and daily budget.');
 return;
 }
 setBudgetBusy(true);
 try {
 await api('/budget-periods', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({
 ad_set_id: String(selectedId),
 start_date: budgetDraft.start_date,
 end_date: budgetDraft.end_date,
 daily_budget: Number(budgetDraft.daily_budget),
 }),
 });
 setBudgetDraft({ start_date: '', end_date: '', daily_budget: '' });
 setBudgetRefreshKey((key) => key + 1);
 } catch (error: any) {
 setBudgetError(error.message || 'Unable to save this budget period.');
 } finally {
 setBudgetBusy(false);
 }
 };
 const deleteBudgetPeriod = async (id: number) => {
 setBudgetError('');
 setBudgetBusy(true);
 try {
 await api(`/budget-periods/${id}`, { method: 'DELETE' });
 setBudgetRefreshKey((key) => key + 1);
 } catch (error: any) {
 setBudgetError(error.message || 'Unable to delete this budget period.');
 } finally {
 setBudgetBusy(false);
 }
 };
 const resetTrackingDates = () => {
 setTrackingStartDate(trackingMinDate);
 setTrackingEndDate(trackingMaxDate);
 };
 const openLeadDrilldown = async (point: any) => {
 if (!point?.date) return;
 setLeadDrilldownClosing(false);
 setSelectedLeadPoint(point);
 setLeadDrilldownRows([]);
 setLeadDrilldownError('');
 setLeadActionError('');
 setLeadDrilldownBusy(true);
 window.setTimeout(() => {
 leadDrilldownPanel.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
 }, 80);
 try {
 const params = new URLSearchParams({ date: point.date });
 if (selectedCampaignId) params.set('campaign_id', String(selectedCampaignId));
 if (selectedId) params.set('ad_set_id', String(selectedId));
 const result = await api(`/leads?${params.toString()}`);
 setLeadDrilldownRows(result.rows || []);
 } catch (error: any) {
 setLeadDrilldownError(error.message || 'Unable to load leads for this point.');
 } finally {
 setLeadDrilldownBusy(false);
 }
 };
 const closeLeadDrilldown = () => {
 setLeadDrilldownClosing(true);
 window.setTimeout(() => {
 setSelectedLeadPoint(null);
 setLeadDrilldownRows([]);
 setLeadDrilldownError('');
 setLeadActionError('');
 setLeadDrilldownClosing(false);
 }, 220);
 };
 const reloadForecastTrackingForSelection = async () => {
 if (!selectedCampaignId) return;
 const params = new URLSearchParams();
 params.set('campaign_id', String(selectedCampaignId));
 if (selectedId) params.set('ad_set_id', String(selectedId));
 const trackingData = await api(`/dashboard/forecast-tracking?${params.toString()}`);
 setForecastTracking(trackingData);
 };
 // Monday.com-style inline cell editing: each field commits on its own, optimistically,
 // via a single-field PATCH (the backend's LeadUpdate ignores unset fields), instead of a
 // separate whole-row edit form the user had to open and save.
 const commitLeadField = async (lead: any, field: string, rawValue: string) => {
 let value: any = rawValue;
 if (field === 'amount_spent_usd') {
 value = String(rawValue).trim() === '' ? null : Number(rawValue);
 } else if (field === 'created_at') {
 value = rawValue ? `${rawValue}:00` : '';
 }
 const previousRows = leadDrilldownRows;
 setLeadDrilldownRows((rows) => rows.map((row) => (row.id === lead.id ? { ...row, [field]: value } : row)));
 setLeadActionError('');
 setLeadActionBusy(true);
 try {
 await api(`/leads/${lead.id}`, {
 method: 'PATCH',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ [field]: value }),
 });
 await reloadForecastTrackingForSelection();
 void load();
 } catch (error: any) {
 setLeadDrilldownRows(previousRows);
 setLeadActionError(error.message || 'Unable to update this lead.');
 } finally {
 setLeadActionBusy(false);
 }
 };
 const deleteLead = async (lead: any) => {
 if (!lead?.id) return;
 if (!confirm(`Delete lead #${lead.id}? This will update the lead totals and forecasts.`)) return;
 setLeadActionBusy(true);
 setLeadActionError('');
 try {
 await api(`/leads/${lead.id}`, { method: 'DELETE' });
 await reloadForecastTrackingForSelection();
 if (selectedLeadPoint) await openLeadDrilldown(selectedLeadPoint);
 void load();
 } catch (error: any) {
 setLeadActionError(error.message || 'Unable to delete this lead.');
 } finally {
 setLeadActionBusy(false);
 }
 };
 const portfolio = useMemo(() => {
 const ranked = [...sets].sort((a, b) => Number(b.total_leads || 0) - Number(a.total_leads || 0));
 const values = ranked.map((item) => Number(item.total_leads || 0)).sort((a, b) => a - b);
 const middle = Math.floor(values.length / 2);
 const median = values.length
 ? values.length % 2 ? values[middle] : (values[middle - 1] + values[middle]) / 2
 : 0;
 const total = ranked.reduce((sum, item) => sum + Number(item.total_leads || 0), 0);
 const allSets = ranked.map((item, index) => {
 const leads = Number(item.total_leads || 0);
 return {
 id: String(item.utm_ad_set_id),
 rank: index + 1,
 leads,
 share: total ? leads / total * 100 : 0,
 campaignId: String(item.utm_campaign_id || '-'),
 };
 });
 const selectedPoint = allSets.find((item) => item.id === selectedId);
 return {
 topSets: ranked.slice(0, 10).map((item, index) => ({
 id: String(item.utm_ad_set_id),
 label: `#${index + 1} - ${String(item.utm_ad_set_id).slice(-12, -6)}`,
 leads: Number(item.total_leads || 0),
 share: total ? Number(item.total_leads || 0) / total * 100 : 0,
 })),
 allSets,
 total,
 median,
 selectedPoint,
 };
 }, [sets, selectedId]);
 const spendSummary = adSpend.summary || {};
 const spendCampaignOptions = adSpend.campaigns || [];
 const budgetScopeAdSetIds = useMemo(() => {
 if (selectedId) return new Set([String(selectedId)]);
 const adSets = adSpend.ad_sets || [];
 if (selectedCampaignId) {
 return new Set(adSets
 .filter((item: any) => String(item.campaign_id || '') === String(selectedCampaignId))
 .map((item: any) => String(item.ad_set_id)));
 }
 return new Set(adSets.map((item: any) => String(item.ad_set_id)));
 }, [adSpend, selectedCampaignId, selectedId]);
 const scopedBudgetPeriods = useMemo(() => {
 const periods = allBudgetPeriods.length ? allBudgetPeriods : budgetPeriods;
 return periods.filter((period: any) => budgetScopeAdSetIds.has(String(period.ad_set_id)));
 }, [allBudgetPeriods, budgetPeriods, budgetScopeAdSetIds]);
 const spendDaily = useMemo(() => {
 const source = selectedId
 ? (adSpend.daily_ad_sets || [])
 : selectedCampaignId
 ? (adSpend.daily_campaigns || [])
 : (adSpend.daily || []);
 const filtered = source.filter((item: any) => {
 const campaignMatch = !selectedCampaignId || String(item.campaign_id) === String(selectedCampaignId);
 const adSetMatch = !selectedId || String(item.ad_set_id || '') === String(selectedId);
 const dateMatch = (!trackingStartDate || String(item.day) >= trackingStartDate) && (!trackingEndDate || String(item.day) <= trackingEndDate);
 return campaignMatch && adSetMatch && dateMatch;
 });
 const byDay = new Map<string, any>();
 filtered.forEach((item: any) => {
 const day = String(item.day);
 const current = byDay.get(day) || {
 day,
 spend: 0,
 actual_leads: 0,
 platform_leads: 0,
 link_clicks: 0,
 impressions: 0,
 };
 current.spend += Number(item.spend || 0);
 current.actual_leads += Number(item.actual_leads || 0);
 current.platform_leads += Number(item.platform_leads || 0);
 current.link_clicks += Number(item.link_clicks || 0);
 current.impressions += Number(item.impressions || 0);
 byDay.set(day, current);
 });
 const budgetForDay = (day: string) => scopedBudgetPeriods.reduce((sum: number, period: any) => {
 const start = String(period.start_date || '');
 const end = String(period.end_date || '');
 if (!start || !end || day < start || day > end) return sum;
 return sum + Number(period.daily_budget || 0);
 }, 0);
 const normalizedSpendDaily = Array.from(byDay.values()).sort((a, b) => String(a.day).localeCompare(String(b.day))).map((item) => ({
 ...item,
 budget: budgetForDay(String(item.day)) || null,
 cpl: item.actual_leads ? item.spend / item.actual_leads : null,
 actual_cpl: item.actual_leads ? item.spend / item.actual_leads : null,
 meta_cpl: item.platform_leads ? item.spend / item.platform_leads : null,
 ctr: item.impressions ? item.link_clicks / item.impressions : null,
 }));
 return normalizedSpendDaily.map((item) => {
 const noLeadSpend = Number(item.spend || 0) > 0 && Number(item.actual_leads || 0) <= 0;
 return {
 ...item,
 no_lead_spend: noLeadSpend,
 };
 });
 }, [adSpend, scopedBudgetPeriods, selectedCampaignId, selectedId, trackingStartDate, trackingEndDate]);
 const filteredSpendSummary = useMemo(() => {
 const spend = spendDaily.reduce((sum: number, item: any) => sum + Number(item.spend || 0), 0);
 const actualLeads = spendDaily.reduce((sum: number, item: any) => sum + Number(item.actual_leads || 0), 0);
 const metaLeads = spendDaily.reduce((sum: number, item: any) => sum + Number(item.platform_leads || 0), 0);
 const clicks = spendDaily.reduce((sum: number, item: any) => sum + Number(item.link_clicks || 0), 0);
 const impressions = spendDaily.reduce((sum: number, item: any) => sum + Number(item.impressions || 0), 0);
 return {
 spend,
 actual_leads: actualLeads,
 platform_leads: metaLeads,
 link_clicks: clicks,
 ctr: impressions ? clicks / impressions : null,
 cpl: actualLeads ? spend / actualLeads : null,
 actual_cpl: actualLeads ? spend / actualLeads : null,
 meta_cpl: metaLeads ? spend / metaLeads : null,
 };
 }, [spendDaily]);
 const allCampaignCplRows = [...spendCampaignOptions]
 .filter((item: any) => Number(item.actual_leads || 0) > 0 && (item.actual_cpl ?? item.cpl) != null)
 .map((item: any) => ({
 ...item,
 actual_cpl: Number(item.actual_cpl ?? item.cpl),
 spend: Number(item.spend || 0),
 actual_leads: Number(item.actual_leads || 0),
 shortName: String(item.campaign_name || item.campaign_id).replace(/^Leads\s*\|\s*/i, '').slice(0, 38),
 }))
 .sort((a: any, b: any) => a.actual_cpl - b.actual_cpl);
 const campaignCplRows = (allCampaignCplRows.length > 20
 ? [...allCampaignCplRows.slice(0, 10), ...allCampaignCplRows.slice(-10)]
 : allCampaignCplRows
 ).sort((a: any, b: any) => b.actual_cpl - a.actual_cpl);
 const campaignCplLeads = allCampaignCplRows.reduce((sum: number, item: any) => sum + item.actual_leads, 0);
 const campaignCplBenchmark = campaignCplLeads
 ? allCampaignCplRows.reduce((sum: number, item: any) => sum + item.spend, 0) / campaignCplLeads
 : null;
 const allAdSetCplRows = [...(adSpend.ad_sets || [])]
 .filter((item: any) => Number(item.actual_leads || 0) > 0 && (item.actual_cpl ?? item.cpl) != null)
 .map((item: any) => ({
 ...item,
 ad_set_id: String(item.ad_set_id),
 actual_cpl: Number(item.actual_cpl ?? item.cpl),
 spend: Number(item.spend || 0),
 actual_leads: Number(item.actual_leads || 0),
 shortName: `${String(item.campaign_name || item.campaign_id || '').replace(/^Leads\s*\|\s*/i, '').slice(0, 24)} · ${String(item.ad_set_id)}`,
 }))
 .sort((a: any, b: any) => b.actual_cpl - a.actual_cpl);
 const adSetCplLeads = allAdSetCplRows.reduce((sum: number, item: any) => sum + item.actual_leads, 0);
 const adSetCplBenchmark = adSetCplLeads
 ? allAdSetCplRows.reduce((sum: number, item: any) => sum + item.spend, 0) / adSetCplLeads
 : null;
 const spendScopeLabel = selectedId
 ? `Ad set ${String(selectedId).slice(-6)}`
 : selectedCampaignId
 ? selectedCampaignOption?.campaign || spendCampaignOptions.find((item: any) => String(item.campaign_id) === String(selectedCampaignId))?.campaign_name || 'Selected campaign'
 : 'All campaigns';
 const spendCampaigns = allCampaignCplRows.slice(0, 10);
 const cplBenchmark = cplView === 'campaign' ? campaignCplBenchmark : adSetCplBenchmark;
 const cplRows = (cplView === 'campaign' ? campaignCplRows : allAdSetCplRows)
 .slice(0, 16)
 .map((item: any) => {
 const value = Number(item.actual_cpl ?? item.cpl ?? 0);
 const label = cplView === 'campaign'
 ? String(item.campaign_name || item.campaign_id).replace(/^Leads\s*\|\s*/i, '')
 : `${String(item.campaign_name || item.campaign_id || '').replace(/^Leads\s*\|\s*/i, '')} - ${String(item.ad_set_id)}`;
 const id = cplView === 'campaign' ? String(item.campaign_id) : String(item.ad_set_id);
 const tone = cplBenchmark != null && value <= Number(cplBenchmark)
 ? 'good'
 : cplBenchmark != null && value >= Number(cplBenchmark) * 2.4
 ? 'bad'
 : 'warm';
 return { ...item, id, label, value, tone };
 });
 const cplMax = Math.max(1, Number(cplBenchmark || 0), ...cplRows.map((item: any) => Number(item.value || 0)));
 const cplBenchmarkPct = cplBenchmark == null ? null : Math.min(100, Math.max(0, Number(cplBenchmark) / cplMax * 100));
 const cplColumns = [
 cplRows.slice(0, Math.ceil(cplRows.length / 2)),
 cplRows.slice(Math.ceil(cplRows.length / 2)),
 ];
 const selectedSpendCampaign = selectedCampaignId
 ? (adSpend.campaigns || []).find((item: any) => String(item.campaign_id) === String(selectedCampaignId))
 : null;
 const selectedAdSetSpend = selectedId
 ? (adSpend.ad_sets || []).find((item: any) => String(item.ad_set_id) === String(selectedId))
 : null;
 const spendAdSetRows = (adSpend.ad_sets || [])
 .filter((item: any) => !selectedCampaignId || String(item.campaign_id) === String(selectedCampaignId))
 .sort((a: any, b: any) => {
 const aCpl = a.actual_cpl ?? a.cpl;
 const bCpl = b.actual_cpl ?? b.cpl;
 if (aCpl == null && bCpl == null) return Number(b.spend || 0) - Number(a.spend || 0);
 if (aCpl == null) return 1;
 if (bCpl == null) return -1;
 return Number(aCpl) - Number(bCpl);
 });

 const allocationRows = useMemo(() => {
 const campaigns = (adSpend.campaigns || []).filter((item: any) => Number(item.spend || 0) > 0 || Number(item.actual_leads || 0) > 0);
 const totalSpend = campaigns.reduce((sum: number, item: any) => sum + Number(item.spend || 0), 0);
 const totalLeads = campaigns.reduce((sum: number, item: any) => sum + Number(item.actual_leads || 0), 0);
 return campaigns
 .map((item: any) => {
 const spendShare = totalSpend ? (Number(item.spend || 0) / totalSpend) * 100 : 0;
 const leadShare = totalLeads ? (Number(item.actual_leads || 0) / totalLeads) * 100 : 0;
 return {
 campaign_id: String(item.campaign_id),
 shortName: String(item.campaign_name || item.campaign_id).replace(/^Leads\s*\|\s*/i, '').slice(0, 30),
 spend_share: spendShare,
 lead_share: leadShare,
 gap: leadShare - spendShare,
 spend: Number(item.spend || 0),
 actual_leads: Number(item.actual_leads || 0),
 };
 })
 .sort((a: any, b: any) => b.spend_share - a.spend_share)
 .slice(0, 10);
 }, [adSpend]);
 const allocationMaxShare = Math.max(1, ...allocationRows.flatMap((item: any) => [Number(item.spend_share || 0), Number(item.lead_share || 0)]));
 const allocationStatusFor = (gap: any) => Number(gap || 0) >= 3 ? 'Under-funded' : Number(gap || 0) <= -3 ? 'Over-funded' : 'Balanced';
 const selectedAllocationRow = useMemo(() => {
 return allocationRows.find((item: any) => String(item.campaign_id) === String(selectedCampaignId)) || allocationRows[0] || null;
 }, [allocationRows, selectedCampaignId]);
 const selectedAllocationStatus = selectedAllocationRow ? allocationStatusFor(selectedAllocationRow.gap) : '';

 // Spend-vs-leads scatter. The slope of a point from the origin IS its cost per lead, so a
 // benchmark ray at the blended portfolio CPL splits the cloud into cheaper-than-average
 // (above) and dearer-than-average (below). Everything else here serves reading that split.
 // Daily portfolio grain, not per-ad-set: one dot per day the portfolio spent money, so the
 // point count tracks the date range instead of being capped at ~20 ad sets.
 // Sourced from spendDaily, not raw adSpend.daily, so this chart honors the same campaign / ad
 // set / date-range selection as the tracking chart and "Total spent per day" chart above it —
 // one filter for every chart on this page, not a portfolio-wide chart sitting among scoped ones.
 const dailySpendLeadsScatter = useMemo(() => {
 const points = spendDaily
 .filter((item: any) => Number(item.spend || 0) > 0)
 .map((item: any) => {
 const spend = Number(item.spend || 0);
 const actual_leads = Number(item.actual_leads || 0);
 return { day: String(item.day), spend, actual_leads, cpl: actual_leads > 0 ? spend / actual_leads : null };
 });
 const maxSpend = Math.max(1, ...points.map((item: any) => item.spend));
 const maxLeads = Math.max(1, ...points.map((item: any) => item.actual_leads));
 // LOESS (locally weighted linear regression), not a single straight OLS line — a fixed
 // slope can't show a plateau or diminishing-returns bend, which is the actual question
 // ("where does more spend stop buying more leads") this line exists to answer. Sampled
 // across the observed spend range only, same reasoning as the old line never extrapolating
 // to $0: the curve has no business claiming a shape at spend levels never actually seen.
 const curve = loessCurve(points.map((item: any) => ({ x: item.spend, y: item.actual_leads })))
 .map((point) => ({ spend: point.x, actual_leads: point.y }));
 return { points, maxSpend, maxLeads, curve };
 }, [spendDaily]);

 const budgetPacing = useMemo(() => {
 const daily = (adSpend.daily || []).slice().sort((a: any, b: any) => String(a.day).localeCompare(String(b.day)));
 if (!daily.length) return null;
 const lastDay = String(daily[daily.length - 1].day);
 const monthStart = `${lastDay.slice(0, 8)}01`;
 const monthRows = daily.filter((row: any) => String(row.day) >= monthStart);
 const mtdSpend = monthRows.reduce((sum: number, row: any) => sum + Number(row.spend || 0), 0);
 const mtdLeads = monthRows.reduce((sum: number, row: any) => sum + Number(row.actual_leads || 0), 0);
 const last7 = daily.slice(-7);
 const runRate = last7.reduce((sum: number, row: any) => sum + Number(row.spend || 0), 0) / Math.max(1, last7.length);
 const [year, month] = lastDay.split('-').map(Number);
 const daysInMonth = new Date(year, month, 0).getDate();
 const dayOfMonth = Number(lastDay.slice(8, 10));
 const remainingDays = Math.max(0, daysInMonth - dayOfMonth);
 return {
 monthLabel: new Date(`${monthStart}T00:00:00`).toLocaleDateString('en-US', { month: 'long', year: 'numeric' }),
 lastDay, mtdSpend, mtdLeads, runRate,
 projected: mtdSpend + runRate * remainingDays,
 dayOfMonth, daysInMonth, remainingDays,
 mtdCpl: mtdLeads > 0 ? mtdSpend / mtdLeads : null,
 };
 }, [adSpend]);

 const campaignMovers = useMemo(() => {
 const daily = adSpend.daily_campaigns || [];
 if (!daily.length) return { risers: [], fallers: [] };
 const days: string[] = Array.from(new Set<string>(daily.map((row: any) => String(row.day)))).sort();
 const maxDay = days[days.length - 1];
 const shift = (offset: number) => {
 const date = new Date(`${maxDay}T00:00:00`);
 date.setDate(date.getDate() - offset);
 return date.toISOString().slice(0, 10);
 };
 const start7 = shift(6);
 const startPrev = shift(13);
 const endPrev = shift(7);
 const perCampaign = new Map<string, any>();
 daily.forEach((row: any) => {
 const day = String(row.day);
 const key = String(row.campaign_id);
 const entry = perCampaign.get(key) || { campaign_id: key, campaign_name: '', leads7: 0, leadsPrev7: 0 };
 if (day >= start7) entry.leads7 += Number(row.actual_leads || 0);
 else if (day >= startPrev && day <= endPrev) entry.leadsPrev7 += Number(row.actual_leads || 0);
 if (!entry.campaign_name && row.campaign_name) entry.campaign_name = String(row.campaign_name);
 perCampaign.set(key, entry);
 });
 const rows = Array.from(perCampaign.values())
 .filter((item) => item.leads7 + item.leadsPrev7 >= 5)
 .map((item) => ({
 ...item,
 delta: item.leads7 - item.leadsPrev7,
 shortName: String(item.campaign_name || item.campaign_id).replace(/^Leads\s*\|\s*/i, '').slice(0, 26),
 }));
 const risers = rows.filter((item) => item.delta > 0).sort((a, b) => b.delta - a.delta).slice(0, 3);
 const fallers = rows.filter((item) => item.delta < 0).sort((a, b) => a.delta - b.delta).slice(0, 3);
 return { risers, fallers };
 }, [adSpend]);
 const campaignShareMax = Math.max(1, ...visibleCampaigns.map((item: any) => Number(item.sharePercent || 0)));
 const forecastMetricCells = [
 {
 label: 'Total leads',
 value: fmt(insights.total_leads ?? summary.total_leads),
 // The 7-day move was buried mid-sentence in the note line. As a chip it reads at a
 // glance and carries direction in colour as well as sign.
 delta: recentDelta,
 deltaNote: 'vs prior 7 days',
 note: `${fmt(summary.recent_7_leads)} in last 7 days`,
 spark: trackingTimeline.map((point: any) => point.actual_leads ?? point.forecast_leads ?? 0),
 tone: 'neutral' as const,
 },
 {
 label: 'New leads',
 value: fmt(insights.new_leads),
 note: `${(Number(insights.new_share || 0) * 100).toFixed(1)}% of all customer traffic`,
 spark: statusMix.map((item: any) => item.status === 'New' ? item.leads : item.leads * 0.45),
 tone: 'good' as const,
 },
 {
 label: 'Existing leads',
 value: fmt(insights.existing_leads),
 note: `${(Number(insights.existing_share || 0) * 100).toFixed(1)}% of all customer traffic`,
 spark: statusMix.map((item: any) => item.status === 'Existing' ? item.leads : item.leads * 0.35),
 tone: 'neutral' as const,
 },
 {
 label: 'Ad sets',
 value: fmt(insights.unique_ad_sets ?? sets.length),
 note: `Measured through ${dateFmt(insights.date_end || summary.last_data_date)}`,
 spark: sets.slice(0, 18).map((item: any) => item.total_leads),
 tone: 'warm' as const,
 },
 {
 label: 'Campaigns',
 value: fmt(insights.unique_campaigns),
 note: 'Active campaign IDs in the dataset',
 spark: campaignMix.slice(0, 18).map((item: any) => item.leads),
 tone: 'neutral' as const,
 },
 ];

 return (
 <div className="page-content dashboard-page forecast-v2-page">
 <section className="forecast-v2-header hero-heading">
 <div>
 <h2>Forecast</h2>
 <p>Understand the past. Project what the next spend curve returns.</p>
 </div>
 </section>

 <section id="kpis" className="forecast-v2-kpis dashboard-kpis" aria-label="Forecast overview">
 {forecastMetricCells.map((metric: any) => (
 <article className="forecast-v2-kpi" key={metric.label}>
 <span>{metric.label}</span>
 <div className="forecast-v2-kpi-value">
 {busy ? <strong>-</strong> : <strong>{metric.value}</strong>}
 {!busy && typeof metric.delta === 'number' && Number.isFinite(metric.delta) && (
 <em className={`delta-chip${metric.delta > 0 ? ' is-up' : metric.delta < 0 ? ' is-down' : ' is-flat'}`}>
 {metric.delta > 0 ? <ArrowUp size={11} /> : metric.delta < 0 ? <ArrowDown size={11} /> : null}
 {Math.abs(metric.delta)}%
 </em>
 )}
 </div>
 <ForecastSparkBars values={metric.spark} tone={metric.tone} />
 <small>{metric.deltaNote ? `${metric.note} · ${metric.deltaNote}` : metric.note}</small>
 </article>
 ))}
 </section>

 <section id="tracking" className="forecast-tracking performance-tracking glass-panel" aria-label="Campaign performance, spend, and forecast tracking">
 <div className="history-control tracking-adset-control" aria-label="Search by Ad Set ID">
 <div className="card-head insight-head tracking-head tracking-head-top">
 <div>
 <h3>Actual vs forecast</h3>
 </div>
 </div>
 <div className="performance-filter-stack">
 <form
 className="lookup-area"
 onSubmit={(event) => {
 event.preventDefault();
 completeAdSetLookup({ reveal: true });
 }}
 >
 <div className="adset-lookup">
 <div className={`campaign-picker${campaignPickerOpen ? ' open' : ''}`} ref={campaignPickerRef}>
 <button
 type="button"
 className="selector campaign-selector"
 aria-haspopup="listbox"
 aria-expanded={campaignPickerOpen}
 onClick={() => setCampaignPickerOpen((open) => !open)}
 >
 <Megaphone size={16} />
 <span>Campaign</span>
 <strong title={selectedCampaignOption?.campaign || undefined}>{selectedCampaignOption?.campaign || 'Select campaign'}</strong>
 <ChevronDown size={15} className="campaign-caret" />
 </button>
 {campaignPickerOpen && (
 <div className="campaign-menu" role="listbox" aria-label="Campaigns">
 {campaignOptions.map((campaign: any) => {
 const isActive = String(campaign.campaign_id) === String(selectedCampaignId);
 return (
 <button
 type="button"
 key={campaign.campaign_id}
 role="option"
 aria-selected={isActive}
 className={`campaign-option${isActive ? ' active' : ''}`}
 data-campaign-id={campaign.campaign_id}
 onClick={() => selectCampaignFromDropdown(String(campaign.campaign_id))}
 >
 <span title={campaign.campaign}>{campaign.campaign}</span>
 <small>{campaign.adSetCount} ad sets - {fmt(campaign.leads)} leads</small>
 </button>
 );
 })}
 </div>
 )}
 </div>
 <div className={`selector adset-selector${lookupError ? ' invalid' : ''}`}>
 <Search size={17} />
 <label className="lookup-input-label" htmlFor="forecast-search">Ad Set ID</label>
 <input
 ref={lookupInput}
 id="forecast-search"
 value={query}
 onChange={(event) => { setQuery(event.target.value); setLookupError(''); }}
 onKeyDown={(event) => {
 if (event.key === 'Enter') {
 event.preventDefault();
 completeAdSetLookup({ reveal: true }, event.currentTarget.value);
 }
 }}
 placeholder="Enter or paste an Ad Set ID..."
 autoComplete="off"
 inputMode="numeric"
 spellCheck={false}
 aria-invalid={Boolean(lookupError)}
 aria-describedby={lookupError ? 'adset-lookup-error' : undefined}
 />
 {query && <button type="button" className="clear-search" aria-label="Clear Ad Set ID" onClick={() => { setQuery(''); setSelectedId(''); setLookupError(''); lookupInput.current?.focus(); }}><X size={15} /></button>}
 </div>
 </div>
 {lookupError && <div id="adset-lookup-error" className="lookup-error" aria-live="polite">{lookupError}</div>}
 </form>
 <div className="tracking-controls tracking-top-controls">
 <DateRangePicker
 startDate={trackingStartDate}
 endDate={trackingEndDate}
 minDate={trackingMinDate}
 maxDate={trackingMaxDate}
 onApply={(start: string, end: string) => { setTrackingStartDate(start); setTrackingEndDate(end); }}
 onReset={resetTrackingDates}
 />
 <div className="budget-toggle-wrap" ref={budgetPanelRef}>
 <button
 type="button"
 className={`budget-toggle${budgetPanelOpen ? ' open' : ''}`}
 aria-expanded={budgetPanelOpen}
 aria-haspopup="dialog"
 onClick={() => setBudgetPanelOpen((wasOpen) => !wasOpen)}
 >
 <SlidersHorizontal size={15} />
 <span>Budget scenario</span>
 <ChevronDown size={15} className="budget-toggle-caret" />
 </button>
 {budgetPanelOpen && (
 <div className="budget-popover" role="dialog" aria-label="Budget scenario">
 <div className="budget-popover-head">
 <span><SlidersHorizontal size={13} /> Budget scenario</span>
 <button type="button" className="budget-popover-close" aria-label="Close" onClick={() => setBudgetPanelOpen(false)}><X size={14} /></button>
 </div>
 {!selectedId ? (
 <p className="budget-popover-empty">Select an ad set to plan its budget.</p>
 ) : (
 <>
 <div className="budget-input-row">
 <div className="budget-current">
 <span>Current daily budget</span>
 <strong>{baselineBusy ? '···' : money(baselineScenario?.components?.future_spend_daily)}</strong>
 </div>
 <label className="budget-new">
 <span>New daily budget</span>
 <div className="budget-new-field">
 <i>$</i>
 <input
 type="number"
 min="0"
 step="1"
 inputMode="decimal"
 value={scenarioParams.future_spend_daily}
 placeholder={scenarioComponents.auto_spend_daily != null ? Number(scenarioComponents.auto_spend_daily).toFixed(0) : '0'}
 onChange={(event) => setScenarioParam('future_spend_daily', event.target.value)}
 />
 </div>
 </label>
 </div>
 {scenarioParams.future_spend_daily.trim() !== '' && scenarioComparison ? (
 <p className={`budget-impact${scenarioComparison.delta > 0 ? ' up' : scenarioComparison.delta < 0 ? ' down' : ''}`}>
 {scenarioComparison.delta > 0 ? <TrendingUp size={13} /> : scenarioComparison.delta < 0 ? <TrendingDown size={13} /> : null}
 {scenarioBusy || baselineBusy ? 'Recalculating…' : scenarioComparison.delta === 0 ? 'No change in projected leads' : `${scenarioComparison.delta > 0 ? '+' : ''}${fmt(scenarioComparison.delta)} leads (${(scenarioComparison.deltaPct ?? 0) > 0 ? '+' : ''}${scenarioComparison.deltaPct?.toFixed(0) ?? '0'}%) over 14 days`}
 </p>
 ) : (
 <p className="budget-impact muted">Enter a budget to preview its effect on leads</p>
 )}
 <div className="budget-history-section">
 <div className="budget-history-head">
 <span>Budget history</span>
 {budgetInfo?.fitted_elasticity != null && (
 <span className="budget-sensitivity" title="Fitted from your recorded budget periods">{Number(budgetInfo.fitted_elasticity).toFixed(2)} sensitivity</span>
 )}
 </div>
 <div className="budget-table">
 {budgetPeriods.map((period: any) => {
 const observed = (budgetInfo?.periods || []).find((row: any) => row.id === period.id);
 return (
 <div className={`budget-table-row${period.source === 'meta_export' ? ' derived' : ''}`} key={period.id}>
 <div className="budget-table-dates">
 <span>
 {dateFmt(period.start_date)} → {dateFmt(period.end_date)}
 {period.source === 'meta_export' && <em className="budget-source" title="Detected from the ad export. Editing it makes it yours, and later imports stop overwriting it.">from export</em>}
 </span>
 {period.spend_conflict ? (
 <small className="budget-conflict"><AlertTriangle size={11} />still over: {cplMoney(period.recent_mean_daily_spend ?? period.mean_daily_spend)}/day across the last {fmt(period.recent_days ?? period.observed_days)} days</small>
 ) : observed && observed.observed_leads_per_day != null ? (
 <small>{fmt(observed.observed_leads_per_day)} leads/day{observed.observed_cpl != null ? ` · ${cplMoney(observed.observed_cpl)} CPL` : ''}</small>
 ) : null}
 </div>
 <span className="budget-table-amount">{money(period.daily_budget)}</span>
 <button type="button" className="budget-table-delete" aria-label="Delete budget period" disabled={budgetBusy} onClick={() => deleteBudgetPeriod(period.id)}><X size={13} /></button>
 </div>
 );
 })}
 <div className="budget-table-draft">
 <SingleDatePicker value={budgetDraft.start_date} onChange={(next) => setBudgetDraft((current) => ({ ...current, start_date: next }))} ariaLabel="Budget start date" />
 <SingleDatePicker value={budgetDraft.end_date} min={budgetDraft.start_date || undefined} onChange={(next) => setBudgetDraft((current) => ({ ...current, end_date: next }))} ariaLabel="Budget end date" />
 <input type="number" min="0" step="1" placeholder="$/day" value={budgetDraft.daily_budget} onChange={(event) => setBudgetDraft((current) => ({ ...current, daily_budget: event.target.value }))} aria-label="Daily budget" />
 <button type="button" className="budget-table-add" aria-label="Add budget period" disabled={budgetBusy} onClick={() => void saveBudgetPeriod()}><Plus size={14} /></button>
 </div>
 </div>
 {budgetError && <p className="budget-popover-error">{budgetError}</p>}
 {(!budgetInfo?.fitted_elasticity) && (
 <p className="budget-hint">Add 2+ periods with different budgets to fit your own sensitivity.</p>
 )}
 </div>
 </>
 )}
 </div>
 )}
 </div>
 <ChangeEventButton adSetId={selectedId} retraining={retraining} onChange={() => { setModelRefreshKey((key) => key + 1); watchRetrain(); }} />
 </div>
 </div>
 </div>

 <div className="performance-summary-grid">
 <div className="summary-card-group">
 <div className="summary-row-label">
 <b>{spendScopeLabel || (selectedId ? 'Ad set view' : selectedCampaignId ? 'Campaign view' : `${sets.length} ad sets`)}</b>
 </div>
 <div className={`performance-kpi-strip${adSpend.available ? ' with-spend' : ''}`}>
 <article><span>Actual leads</span><strong>{fmt(trackingStats.actualTotal)}</strong><small>selected dates</small></article>
 <article><span>Forecast leads</span><strong>{fmt(trackingStats.forecastTotal)}</strong><small>selected dates</small></article>
 {adSpend.available && <article><span>Total spending</span><strong>{money(filteredSpendSummary.spend)}</strong><small>{dateFmt(spendSummary.date_start)} to {dateFmt(spendSummary.date_end)}</small></article>}
 {adSpend.available && <article><span>Actual CPL</span><strong>{cplMoney(filteredSpendSummary.actual_cpl ?? filteredSpendSummary.cpl)}</strong><small>spend / actual leads</small></article>}
 </div>
 </div>
 </div>

 {adSpend.available && (
 <div className="performance-subsection spend-performance-block" aria-label="Ad spend and CPL analytics">
 <article className="spend-chart-card spend-cpl-chart-card">
 <div className="card-head compact">
 <div><h3>Amount spent and lead volume over time</h3></div>
 </div>
 <div className="spend-line-chart">
 <ResponsiveContainer width="100%" height="100%">
 <ComposedChart data={spendDaily} margin={{ top: 18, right: 28, left: 0, bottom: 4 }} barCategoryGap="28%">
 <defs>
 <linearGradient id="leadVolumeFill" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stopColor="var(--series-actual)" stopOpacity={0.88} />
 <stop offset="1" stopColor="var(--series-actual)" stopOpacity={0.22} />
 </linearGradient>
 </defs>
 <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="var(--grid-line)" />
 <XAxis dataKey="day" interval="preserveStartEnd" minTickGap={32} height={40} tick={<WeekdayAxisTick />} axisLine={{ stroke: 'var(--axis-line)' }} tickLine={false} />
 <YAxis yAxisId="spend" tickFormatter={(value) => cplMoney(value)} tick={{ fontSize: 12, fontWeight: 600, fill: 'var(--yellow)' }} axisLine={false} tickLine={false} width={68} />
 <YAxis yAxisId="leads" orientation="right" allowDecimals={false} tickFormatter={(value) => fmt(value)} tick={{ fontSize: 12, fontWeight: 600, fill: 'var(--series-actual)' }} axisLine={false} tickLine={false} width={44} />
 <Tooltip content={<SpendTrendTooltip />} />
 <Bar yAxisId="leads" dataKey="actual_leads" name="Actual leads" fill="url(#leadVolumeFill)" radius={[7, 7, 0, 0]} maxBarSize={26} isAnimationActive animationDuration={650} />
 <Line yAxisId="spend" type="monotone" dataKey="spend" name="Amount spent" stroke="var(--yellow)" strokeWidth={2.9} dot={<SpendCplDot />} activeDot={<SpendCplDot />} connectNulls isAnimationActive animationDuration={850} />
 </ComposedChart>
 </ResponsiveContainer>
 </div>
 </article>
 </div>
 )}

 {adSpend.available && (
 <div className="performance-subsection cpl-performance-block" aria-label={`Total amount spent per day for ${spendScopeLabel}`}>
 <article className="spend-chart-card cpl-trend-card">
 <div className="card-head compact cpl-trend-head">
 <div><h3>Total spent per day</h3></div>
 <div className="cpl-trend-scope">
 <span className="cpl-trend-scope-label"><i />{spendScopeLabel}</span>
 {filteredSpendSummary.spend != null && <strong>{money(filteredSpendSummary.spend)} total</strong>}
 </div>
 </div>
 {spendDaily.some((item: any) => Number(item.spend || 0) > 0) ? (
 <div className="cpl-trend-chart">
 <ResponsiveContainer width="100%" height="100%">
 <ComposedChart data={spendDaily} margin={{ top: 18, right: 72, left: 0, bottom: 4 }}>
 <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="var(--grid-line)" />
 <XAxis dataKey="day" interval="preserveStartEnd" minTickGap={32} height={40} tick={<WeekdayAxisTick />} axisLine={{ stroke: 'var(--axis-line)' }} tickLine={false} />
 <YAxis tickFormatter={(value) => cplMoney(value)} tick={{ fontSize: 12, fontWeight: 600, fill: 'var(--yellow)' }} axisLine={false} tickLine={false} width={68} />
 <Tooltip content={<SpendPerDayTooltip />} cursor={{ stroke: 'var(--cursor-line)', strokeDasharray: '4 4' }} />
 {spendDaily.some((item: any) => item.budget != null) && (
 <Line type="stepAfter" dataKey="budget" name="Daily budget" stroke="var(--series-median)" strokeWidth={1.4} strokeDasharray="5 5" dot={false} activeDot={false} connectNulls isAnimationActive={false} />
 )}
 <Line type="monotone" dataKey="spend" name="Amount spent" stroke="var(--yellow)" strokeWidth={2.9} dot={<SpendCplDot />} activeDot={<SpendCplDot />} connectNulls isAnimationActive animationDuration={850} />
 </ComposedChart>
 </ResponsiveContainer>
 </div>
 ) : (
 <div className="card-empty-state cpl-trend-empty">
 <TrendingUp />
 <b>No spend in this range</b>
 <span>Widen the date range or clear the campaign filter to see daily spend.</span>
 </div>
 )}
 </article>
 </div>
 )}

 <div className="performance-subsection forecast-performance-block">
 {trackingTimeline.length ? (
 <div className="tracking-chart-block">
 {/* Inside the chart's own bordered container, so the fit statistics read as part of the
     chart rather than a separate panel that happens to sit near it. */}
 <div className={`forecast-ols-block${olsBusy ? ' is-busy' : ''}`}>
 <OlsResultCards ols={ols} emptyCopy={olsEmptyCopy} coefficients={false} />
 {olsMultivariateNote && <p className="forecast-ols-note">{olsMultivariateNote}</p>}
 </div>
 <div className="tracking-legend" aria-label="Chart legend">
 <div className="tracking-legend-keys">
 <span><i className="actual" />Actual</span>
 {showForecast && <><span><i className="predicted" />Forecast phases</span><small>Dashed = measured phase - Solid = current phase</small></>}
 </div>
 <button type="button" className={`tracking-forecast-toggle${showForecast ? ' is-on' : ''}`} onClick={() => setShowForecast((value) => !value)} aria-pressed={showForecast} title={showForecast ? 'Hide the 14-day forecast' : 'Show the 14-day forecast'}>
 <TrendingUp size={13} />Forecast
 </button>
 </div>
 <div className="tracking-chart">
 <ResponsiveContainer width="100%" height="100%">
 <AreaChart
 key={`${selectedCampaignId || 'portfolio'}-${showForecast ? 'fc' : 'act'}`}
 data={chartTrackingTimeline}
 margin={{ top: 24, right: 72, left: 0, bottom: 8 }}
 onClick={(state: any) => {
 const point = state?.activePayload?.[0]?.payload;
 if (point) void openLeadDrilldown(point);
 }}
 >
 <defs>
 <linearGradient id="trackingActualFill" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stopColor="var(--series-actual)" stopOpacity={0.24} />
 <stop offset="1" stopColor="var(--series-actual)" stopOpacity={0.01} />
 </linearGradient>
 {visibleTrackingPhases.map((phase: any, index: number) => (
 <linearGradient key={phase.phase_id} id={`trackingForecastFill-${phase.phase_number}`} x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stopColor={index % 2 ? 'var(--series-forecast)' : 'var(--series-forecast-alt)'} stopOpacity={0.16} />
 <stop offset="1" stopColor={index % 2 ? 'var(--series-forecast)' : 'var(--series-forecast-alt)'} stopOpacity={0.01} />
 </linearGradient>
 ))}
 <filter id="trackingYellowGlow"><feGaussianBlur stdDeviation="2" result="blur" /><feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge></filter>
 </defs>
 <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="var(--grid-line)" />
 {showForecast && visibleTrackingPhases.map((phase: any, index: number) => (
 <ReferenceArea key={`area-${phase.phase_id}`} x1={phase.active_start} x2={phase.active_end} fill={index % 2 ? 'var(--series-forecast)' : 'var(--series-forecast-alt)'} fillOpacity={0.035} strokeOpacity={0} />
 ))}
 <XAxis dataKey="date" interval="preserveStartEnd" minTickGap={35} height={42} tick={<WeekdayAxisTick />} axisLine={{ stroke: 'var(--axis-line)' }} tickLine={false} />
 <YAxis allowDecimals={false} tick={{ fontSize: 12, fontWeight: 600, fill: 'var(--muted)' }} axisLine={false} tickLine={false} width={68} />
 {showForecast && visibleTrackingPhases.map((phase: any, index: number) => (
 <ReferenceLine key={`line-${phase.phase_id}`} x={phase.active_start} stroke={index % 2 ? 'var(--series-forecast)' : 'var(--series-forecast-alt)'} strokeDasharray="3 5" label={phaseLabelVisible.has(phase.phase_id) ? { value: `PHASE ${index + 1}`, position: 'top', fill: index % 2 ? 'var(--series-forecast-strong)' : 'var(--series-forecast-alt)', fontSize: 14, fontWeight: 800 } : undefined} />
 ))}
 <Tooltip content={<ForecastTrackingTooltip />} cursor={{ stroke: 'var(--cursor-line)', strokeWidth: 1 }} />
 <Area
 type="monotone"
 dataKey="actual_leads"
 name="Actual"
 stroke="var(--series-actual)"
 strokeWidth={2.7}
 fill="url(#trackingActualFill)"
 dot={(props: any) => <TrackingClickableDot {...props} stroke="var(--series-actual-strong)" fill="var(--bg-raised)" r={3} onSelect={openLeadDrilldown} />}
 activeDot={(props: any) => <TrackingClickableDot {...props} stroke="var(--series-actual-strong)" fill="var(--bg-raised)" r={5} onSelect={openLeadDrilldown} />}
 connectNulls={false}
 isAnimationActive
 animationDuration={800}
 />
 {showForecast && visibleTrackingPhases.map((phase: any, index: number) => {
 const color = index % 2 ? 'var(--series-forecast)' : 'var(--series-forecast-alt)';
 const firstIdx = trackingTimeline.findIndex((point: any) => point.phase_id === phase.phase_id);
 let bridgePoint: any = null;
 if (index > 0) {
 for (let i = firstIdx - 1; i >= 0; i--) {
 const row = trackingTimeline[i];
 if (row.forecast_leads != null || row.actual_leads != null) { bridgePoint = row; break; }
 }
 }
 const bridgeValue = bridgePoint ? (bridgePoint.forecast_leads != null ? bridgePoint.forecast_leads : bridgePoint.actual_leads) : null;
 const phaseValue = (point: any) => {
 if (point.phase_id === phase.phase_id) return point.forecast_leads;
 if (bridgePoint && point === bridgePoint) return bridgeValue;
 return null;
 };
 return [
 <Area key={`forecast-area-${phase.phase_id}`} type="monotone" dataKey={phaseValue} name={`${phase.label} forecast`} stroke="none" fill={`url(#trackingForecastFill-${phase.phase_number})`} dot={false} connectNulls={false} isAnimationActive animationDuration={850} />,
 <Line
 key={`forecast-line-${phase.phase_id}`}
 type="monotone"
 dataKey={phaseValue}
 name={`${phase.label} forecast`}
 stroke={color}
 strokeWidth={index === visibleTrackingPhases.length - 1 ? 3 : 2.4}
 strokeDasharray={index === visibleTrackingPhases.length - 1 ? undefined : '6 4'}
 dot={(props: any) => <TrackingClickableDot {...props} stroke={color} fill="var(--bg-raised)" r={3} onSelect={openLeadDrilldown} />}
 activeDot={(props: any) => <TrackingClickableDot {...props} stroke="var(--series-forecast-strong)" fill="var(--bg-raised)" r={5} onSelect={openLeadDrilldown} />}
 connectNulls={false}
 filter="url(#trackingYellowGlow)"
 isAnimationActive
 animationDuration={950}
 />,
 ];
 })}
 {false && <Line
 type="monotone"
 dataKey="scenario_leads"
 name="Formula scenario"
 stroke="var(--series-neutral)"
 strokeWidth={2.2}
 strokeDasharray="4 5"
 dot={{ r: 2.5, stroke: 'var(--series-neutral)', fill: 'var(--bg-raised)', strokeWidth: 1.4 }}
 activeDot={{ r: 5, stroke: 'var(--series-neutral)', fill: 'var(--bg-raised)', strokeWidth: 2 }}
 connectNulls={false}
 isAnimationActive
 animationDuration={700}
 />}
 </AreaChart>
 </ResponsiveContainer>
 </div>
 </div>
 ) : <div className="empty tracking-empty"><TrendingUp /><b>No forecast record yet</b><span>Train the model to begin daily tracking.</span></div>}
 {selectedLeadPoint && (
 <div className={`lead-drilldown${leadDrilldownClosing ? ' is-closing' : ''}`} ref={leadDrilldownPanel}>
 <div className="card-head compact">
 <div>
 <h3>{weekdayFmt(selectedLeadPoint.date)} - {dateFmt(selectedLeadPoint.date)}</h3>
 <p>{trackingScopeName} - Actual {fmt(selectedLeadPoint.actual_leads)} - Forecast {selectedLeadPoint.forecast_leads == null ? '-' : fmt(selectedLeadPoint.forecast_leads)}</p>
 </div>
 <div className="lead-drilldown-actions">
 <span className="schema-count">{leadDrilldownBusy ? 'Loading...' : `${fmt(leadDrilldownRows.length)} leads loaded`}</span>
 <button type="button" className="icon-button" aria-label="Close lead verification table" onClick={closeLeadDrilldown}><X size={15} /></button>
 </div>
 </div>
 {leadDrilldownError ? (
 <div className="table-empty">{leadDrilldownError}</div>
 ) : (
 <>
 {leadActionError && <div className="lead-action-error">{leadActionError}</div>}
 <div className="table-scroll lead-drilldown-table">
 <table>
 <thead>
 <tr>
 <th>Lead ID</th>
 <th>Customer</th>
 <th>Status</th>
 <th>Lead Quality</th>
 <th>Date</th>
 <th>Time</th>
 <th>Campaign</th>
 <th>Campaign ID</th>
 <th>Ad Set ID</th>
 <th>Ad ID</th>
 <th>Ad title</th>
 <th className="num">Amount</th>
 <th className="action-col">Actions</th>
 </tr>
 </thead>
 <tbody key={selectedLeadPoint?.date}>
 {leadDrilldownRows.map((lead: any, rowIndex: number) => (
 <tr key={lead.id} className="lead-row-enter" style={{ animationDelay: `${Math.min(rowIndex, 14) * 22}ms` }}>
 <td><code>{lead.id}</code></td>
 <td>
 <LeadEditableCell
 value={lead.customer_name || ''}
 disabled={leadActionBusy}
 onCommit={(value) => commitLeadField(lead, 'customer_name', value)}
 formatDisplay={(value) => <b>{value || '-'}</b>}
 />
 </td>
 <td>
 <MenuSelect
 className={`lead-status-select status-${String(lead.status || 'unknown').toLowerCase()}`}
 ariaLabel={`Status for lead ${lead.id}`}
 value={lead.status || ''}
 options={[{ value: 'New', label: 'New' }, { value: 'Existing', label: 'Existing' }]}
 onChange={(value) => commitLeadField(lead, 'status', value)}
 />
 </td>
 <td>
 <MenuSelect
 className={`lead-quality-select quality-${leadQualitySlug(lead.lead_quality)}`}
 ariaLabel={`Lead quality for lead ${lead.id}`}
 value={lead.lead_quality || LEAD_QUALITY_OPTIONS[0]}
 options={LEAD_QUALITY_OPTIONS.map((option) => ({ value: option, label: option }))}
 onChange={(value) => commitLeadField(lead, 'lead_quality', value)}
 />
 </td>
 {(() => {
 // Split into separate Date/Time columns (was one cell, date over a small time
 // sub-line) per request. Both edit the same `created_at` field -- each column
 // commits the full datetime-local value, splicing its own edited part onto the
 // other column's current part, so editing the date can't clobber the time or
 // vice versa.
 const local = dateTimeInputValue(lead.created_at);
 const datePart = local.slice(0, 10);
 const timePart = local.slice(11, 16);
 return (
 <>
 <td>
 <LeadEditableCell
 type="date"
 value={datePart}
 disabled={leadActionBusy}
 onCommit={(value) => commitLeadField(lead, 'created_at', `${value}T${timePart}`)}
 formatDisplay={() => dateFmt(lead.created_at)}
 />
 </td>
 <td>
 <LeadEditableCell
 type="time"
 value={timePart}
 disabled={leadActionBusy}
 onCommit={(value) => commitLeadField(lead, 'created_at', `${datePart}T${value}`)}
 formatDisplay={() => timePart || '-'}
 />
 </td>
 </>
 );
 })()}
 <td>
 <LeadEditableCell value={lead.utm_campaign || ''} disabled={leadActionBusy} onCommit={(value) => commitLeadField(lead, 'utm_campaign', value)} />
 </td>
 <td>
 <LeadEditableCell value={lead.utm_campaign_id || ''} disabled={leadActionBusy} onCommit={(value) => commitLeadField(lead, 'utm_campaign_id', value)} formatDisplay={(value) => <code>{value || '-'}</code>} />
 </td>
 <td>
 <LeadEditableCell value={lead.utm_ad_set_id || ''} disabled={leadActionBusy} onCommit={(value) => commitLeadField(lead, 'utm_ad_set_id', value)} formatDisplay={(value) => <code>{value || '-'}</code>} />
 </td>
 <td>
 <LeadEditableCell value={lead.utm_ad_id || ''} disabled={leadActionBusy} onCommit={(value) => commitLeadField(lead, 'utm_ad_id', value)} formatDisplay={(value) => <code>{value || '-'}</code>} />
 </td>
 <td>
 <LeadEditableCell value={lead.fb_ad_title || ''} disabled={leadActionBusy} onCommit={(value) => commitLeadField(lead, 'fb_ad_title', value)} />
 </td>
 <td className="num">
 <LeadEditableCell
 type="number"
 align="num"
 value={lead.amount_spent_usd == null ? '' : String(lead.amount_spent_usd)}
 disabled={leadActionBusy}
 onCommit={(value) => commitLeadField(lead, 'amount_spent_usd', value)}
 formatDisplay={(value) => (value === '' ? '-' : money(Number(value)))}
 />
 </td>
 <td className="lead-actions-cell">
 <button type="button" className="lead-row-icon-btn danger" title="Delete lead" aria-label={`Delete lead ${lead.id}`} disabled={leadActionBusy} onClick={() => void deleteLead(lead)}><Trash2 size={13} /></button>
 </td>
 </tr>
 ))}
 </tbody>
 </table>
 {!leadDrilldownBusy && !leadDrilldownRows.length && <div className="table-empty">No raw leads found for this date and campaign.</div>}
 </div>
 </>
 )}
 </div>
 )}
 </div>
 </section>

 {false && adSpend.available && (
 <section className="spend-analytics glass-panel" aria-label="Ad spend analytics">
 <div className="card-head insight-head spend-head">
 <div>
 <span>AD SPEND ANALYTICS</span>
 <h3>Spend, actual leads, and true cost per lead</h3>
 <p>Spend is linked to the cleaned LeadLens lead dataset by campaign, ad set ID, and date.</p>
 </div>
 <span className="insight-chip">{dateFmt(spendSummary.date_start)} - {dateFmt(spendSummary.date_end)}</span>
 </div>
 <div className="spend-metrics">
 <article><span>Total spend</span><strong>{money(spendSummary.spend)}</strong><small>{fmt(spendSummary.campaigns)} campaigns</small></article>
 <article><span>Cleaned actual leads</span><strong>{fmt(spendSummary.actual_leads)}</strong><small>from LeadLens leads</small></article>
 <article><span>Actual cost / lead</span><strong>{cplMoney(spendSummary.actual_cpl ?? spendSummary.cpl)}</strong><small>spend / cleaned leads</small></article>
 <article><span>Meta-reported leads</span><strong>{fmt(spendSummary.platform_leads)}</strong><small>{fmt(spendSummary.lead_gap)} actual gap</small></article>
 </div>
 <div className="spend-grid">
 <article className="spend-chart-card">
 <div className="card-head compact"><div><span>DAILY TREND</span><h3>Spend and actual leads over time</h3></div></div>
 <div className="spend-line-chart">
 <ResponsiveContainer width="100%" height="100%">
 <AreaChart data={spendDaily} margin={{ top: 14, right: 12, left: -10, bottom: 4 }}>
 <defs>
 <linearGradient id="spendFill" x1="0" y1="0" x2="0" y2="1">
 <stop offset="0" stopColor="var(--yellow)" stopOpacity={0.26} />
 <stop offset="1" stopColor="var(--yellow)" stopOpacity={0.015} />
 </linearGradient>
 </defs>
 <CartesianGrid strokeDasharray="3 5" vertical={false} stroke="var(--grid-line)" />
 <XAxis dataKey="day" interval="preserveStartEnd" minTickGap={32} tick={<WeekdayAxisTick />} axisLine={{ stroke: 'var(--axis-line)' }} tickLine={false} />
 <YAxis yAxisId="spend" tickFormatter={(value) => `$${fmt(value)}`} tick={{ fontSize: 10, fill: 'var(--dim)' }} axisLine={false} tickLine={false} width={54} />
 <YAxis yAxisId="leads" orientation="right" allowDecimals={false} tick={{ fontSize: 10, fill: 'var(--series-actual)' }} axisLine={false} tickLine={false} width={38} />
 <Tooltip content={<SpendTrendTooltip />} />
 <Area yAxisId="spend" type="monotone" dataKey="spend" name="Spend" stroke="var(--yellow)" strokeWidth={2.6} fill="url(#spendFill)" dot={{ r: 2.3, fill: 'var(--bg-raised)', stroke: 'var(--yellow)', strokeWidth: 1.4 }} activeDot={{ r: 5, stroke: 'var(--yellow-strong)', fill: 'var(--bg-raised)' }} />
 <Line yAxisId="leads" type="monotone" dataKey="actual_leads" name="Actual leads" stroke="var(--series-actual)" strokeWidth={2.1} dot={false} />
 </AreaChart>
 </ResponsiveContainer>
 </div>
 </article>
 <article className="spend-chart-card">
 <div className="card-head compact"><div><span>CAMPAIGN EFFICIENCY</span><h3>Actual CPL by campaign</h3></div></div>
 <div className="spend-bar-chart">
 <ResponsiveContainer width="100%" height="100%">
 <BarChart data={spendCampaigns} layout="vertical" margin={{ top: 4, right: 44, left: 12, bottom: 4 }}>
 <CartesianGrid horizontal={false} stroke="var(--grid-line)" />
 <XAxis type="number" tickFormatter={(value) => `$${fmt(value)}`} tick={{ fontSize: 10, fill: 'var(--dim)' }} axisLine={false} tickLine={false} />
 <YAxis type="category" dataKey="shortName" width={160} tick={{ fontSize: 11, fill: 'var(--muted)' }} axisLine={false} tickLine={false} />
 <Tooltip content={<CampaignSpendTooltip />} cursor={{ fill: 'color-mix(in srgb, var(--yellow) 3.5%, transparent)' }} />
 <Bar dataKey="actual_cpl" radius={[0, 8, 8, 0]} isAnimationActive animationDuration={750}>
 {spendCampaigns.map((item: any) => <Cell key={item.campaign_id} fill={String(item.campaign_id) === String(selectedCampaignId) ? 'var(--series-actual)' : 'var(--yellow-muted)'} />)}
 <LabelList dataKey="actual_cpl" position="right" formatter={(value: any) => money(value)} fill="var(--text)" fontSize={11} fontWeight={700} />
 </Bar>
 </BarChart>
 </ResponsiveContainer>
 </div>
 </article>
 </div>
 <div className="spend-efficiency">
 <article>
 <span>Best actual cost / lead</span>
 {(adSpend.best_cpl || []).slice(0, 3).map((item: any) => (
 <div key={item.ad_set_id}><b>{item.ad_set_id}</b><strong>{cplMoney(item.actual_cpl ?? item.cpl)}</strong><small>{fmt(item.actual_leads)} actual leads</small></div>
 ))}
 </article>
 <article>
 <span>Needs review</span>
 {(adSpend.worst_cpl || []).slice(0, 3).map((item: any) => (
 <div key={item.ad_set_id}><b>{item.ad_set_id}</b><strong>{cplMoney(item.actual_cpl ?? item.cpl)}</strong><small>{fmt(item.actual_leads)} actual leads</small></div>
 ))}
 </article>
 {selectedSpendCampaign && (
 <article className="selected-spend-campaign">
 <span>Selected campaign</span>
 <div><b>{selectedSpendCampaign.campaign_name}</b><strong>{cplMoney(selectedSpendCampaign.actual_cpl ?? selectedSpendCampaign.cpl)}</strong><small>{money(selectedSpendCampaign.spend)} spend - {fmt(selectedSpendCampaign.actual_leads)} actual leads</small></div>
 </article>
 )}
 {selectedAdSetSpend && (
 <article className="selected-spend-campaign selected-adset-spend">
 <span>Selected ad set</span>
 <div><b>{selectedAdSetSpend.ad_set_id}</b><strong>{cplMoney(selectedAdSetSpend.actual_cpl ?? selectedAdSetSpend.cpl)}</strong><small>{money(selectedAdSetSpend.spend)} spend - {fmt(selectedAdSetSpend.actual_leads)} actual leads</small></div>
 </article>
 )}
 </div>
 <article className="spend-breakdown-table">
 <div className="card-head compact">
 <div>
 <span>CAMPAIGN + AD SET COST</span>
 <h3>{selectedSpendCampaign ? selectedSpendCampaign.campaign_name : 'Cost per lead by ad set'}</h3>
 </div>
 <span className="schema-count">{fmt(spendAdSetRows.length)} ad sets linked</span>
 </div>
 <div className="table-scroll">
 <table>
 <thead>
 <tr>
 <th>Campaign</th>
 <th>Ad Set ID</th>
 <th className="num">Amount spent</th>
 <th className="num">Actual leads</th>
 <th className="num">Actual CPL</th>
 <th className="num">Meta leads</th>
 <th className="num">Meta CPL</th>
 <th className="num">Clicks</th>
 </tr>
 </thead>
 <tbody>
 {spendAdSetRows.map((item: any) => (
 <tr
 key={`${item.campaign_id}-${item.ad_set_id}`}
 className={String(item.ad_set_id) === String(selectedId) ? 'selected-spend-row' : ''}
 onClick={() => selectPortfolioAdSet(String(item.ad_set_id))}
 >
 <td><b>{item.campaign_name}</b><small>{item.campaign_id}</small></td>
 <td><code>{item.ad_set_id}</code></td>
 <td className="num forecast-num">{money(item.spend)}</td>
 <td className="num">{fmt(item.actual_leads)}</td>
 <td className="num"><b>{cplMoney(item.actual_cpl ?? item.cpl)}</b></td>
 <td className="num">{fmt(item.platform_leads)}</td>
 <td className="num">{cplMoney(item.meta_cpl)}</td>
 <td className="num">{fmt(item.link_clicks)}</td>
 </tr>
 ))}
 </tbody>
 </table>
 {!spendAdSetRows.length && <div className="table-empty">No spend rows match this campaign yet.</div>}
 </div>
 </article>
 </section>
 )}


 {adSpend.available && (
 <section id="efficiency-scatter" className="portfolio-insights spend-leads-scatter" aria-label={`Daily spend against daily leads for ${spendScopeLabel}`}>
 <article className="portfolio-card glass-panel scatter-card">
 <div className="scatter-scope"><i />{spendScopeLabel}</div>
 <div className="scatter-kpi-row">
 <div className="scatter-kpi"><span>Total spend</span><strong><AnimatedNumber value={Number(filteredSpendSummary.spend) || 0} format={money} /></strong></div>
 <div className="scatter-kpi"><span>Total leads</span><strong><AnimatedNumber value={Number(filteredSpendSummary.actual_leads) || 0} format={fmt} /></strong></div>
 <div className="scatter-kpi"><span>Blended CPL</span><strong><AnimatedNumber value={Number(filteredSpendSummary.actual_cpl ?? filteredSpendSummary.cpl) || 0} format={cplMoney} /></strong></div>
 </div>
 {dailySpendLeadsScatter.points.length ? (
 <>
 {/* Keyed on scope so switching campaign / ad set remounts this whole plot — grid, axes,
 trend line, and dots fade/rise back in together as one moment, and Scatter's own
 entrance animation replays for the new point set, instead of dots silently jumping
 to new positions mid-frame. */}
 <div className="scatter-plot" key={spendScopeLabel}>
 <ResponsiveContainer width="100%" height="100%">
 <ScatterChart margin={{ top: 20, right: 26, left: 6, bottom: 26 }}>
 <CartesianGrid strokeDasharray="3 5" stroke="var(--grid-line)" />
 <XAxis
 type="number"
 dataKey="spend"
 name="Daily spend"
 domain={[0, Math.ceil(dailySpendLeadsScatter.maxSpend * 1.08)]}
 tickFormatter={(value) => `$${fmt(Math.round(value))}`}
 tick={{ fontSize: 12.5, fill: '#c2beb5' }}
 axisLine={{ stroke: 'var(--axis-line)' }}
 tickLine={false}
 height={42}
 label={{ value: 'Daily spend ($)', position: 'insideBottom', offset: -2, fill: '#9b978f', fontSize: 12 }}
 />
 <YAxis
 type="number"
 dataKey="actual_leads"
 name="Daily leads"
 domain={[0, Math.ceil(dailySpendLeadsScatter.maxLeads * 1.12)]}
 allowDecimals={false}
 tick={{ fontSize: 12.5, fill: '#c2beb5' }}
 axisLine={false}
 tickLine={false}
 width={48}
 // Non-rotated, corner-pinned label instead of a rotated title running the length of
 // the axis — that previous version sat directly on top of the tick numbers at this
 // chart's width. A short corner tag can't collide with them.
 label={{ value: 'Daily leads', position: 'insideTopLeft', offset: 14, fill: '#9b978f', fontSize: 11.5, fontWeight: 600 }}
 />
 <ZAxis range={[80, 80]} />
 <Tooltip content={<SpendLeadsScatterTooltip />} cursor={{ stroke: 'var(--cursor-line)', strokeDasharray: '4 4', strokeWidth: 1 }} />
 {dailySpendLeadsScatter.curve.length > 1 && (
 <Line
 data={dailySpendLeadsScatter.curve}
 dataKey="actual_leads"
 stroke="var(--series-median)"
 strokeWidth={1.6}
 strokeDasharray="6 5"
 dot={false}
 activeDot={false}
 isAnimationActive={false}
 type="monotone"
 legendType="none"
 />
 )}
 <Scatter
 data={dailySpendLeadsScatter.points}
 shape={(props: any) => <SpendLeadsScatterDot {...props} />}
 isAnimationActive={!window.matchMedia('(prefers-reduced-motion: reduce)').matches}
 animationDuration={620}
 />
 </ScatterChart>
 </ResponsiveContainer>
 </div>
 </>
 ) : (
 <div className="card-empty-state scatter-empty">
 <TrendingUp />
 <b>No spend in this range</b>
 <span>Widen the date range or import an ad performance export to compare spend against leads.</span>
 </div>
 )}
 </article>
 </section>
 )}

 {adSpend.available && (
 <section id="allocation" className="portfolio-insights budget-allocation" aria-label="Budget allocation and pacing">
 <article className="portfolio-card glass-panel allocation-card-v2">
 <div className="card-head insight-head allocation-head-v2">
 <div><h3>Spend share vs lead share</h3><p>Top {allocationRows.length} by spend</p></div>
 <div className="allocation-legend">
 <span><i className="spend" />Spend share</span>
 <span><i className="lead" />Lead share</span>
 </div>
 </div>
 <div className="allocation-list-v2" role="group" aria-label="Paired bars comparing each campaign's share of total spend against its share of total leads">
 {allocationRows.map((item: any) => {
 const gap = Number(item.gap || 0);
 const verdict = allocationStatusFor(gap);
 const isActive = String(item.campaign_id) === String(selectedAllocationRow?.campaign_id || '');
 return (
 <button
 type="button"
 className={`allocation-row-v2 ${gap >= 3 ? 'under' : gap <= -3 ? 'over' : 'balanced'}${isActive ? ' active' : ''}`}
 key={item.campaign_id}
 onClick={() => setSelectedCampaignId(String(item.campaign_id))}
 aria-pressed={isActive}
 aria-label={`${item.shortName}, spend share ${Number(item.spend_share || 0).toFixed(1)} percent, lead share ${Number(item.lead_share || 0).toFixed(1)} percent, ${verdict}`}
 >
 <span className="allocation-name-v2">{item.shortName}</span>
 <span className="allocation-bars-v2">
 <i className="allocation-track-v2"><em className="spend" style={{ width: `${Math.max(1, Number(item.spend_share || 0) / allocationMaxShare * 100)}%` }} /></i>
 <i className="allocation-track-v2"><em className="lead" style={{ width: `${Math.max(1, Number(item.lead_share || 0) / allocationMaxShare * 100)}%` }} /></i>
 </span>
 <span className="allocation-status-v2">{verdict}</span>
 </button>
 );
 })}
 </div>
 {selectedAllocationRow && (
 <div className={`allocation-detail-v2 ${Number(selectedAllocationRow.gap || 0) >= 3 ? 'under' : Number(selectedAllocationRow.gap || 0) <= -3 ? 'over' : 'balanced'}`} aria-live="polite">
 <div className="allocation-detail-name"><span>Selected campaign</span><strong>{selectedAllocationRow.shortName}</strong></div>
 <div><span>Spend share</span><strong>{Number(selectedAllocationRow.spend_share || 0).toFixed(1)}%</strong><small>{money(selectedAllocationRow.spend)} spent</small></div>
 <div><span>Lead share</span><strong>{Number(selectedAllocationRow.lead_share || 0).toFixed(1)}%</strong><small>{fmt(selectedAllocationRow.actual_leads)} leads</small></div>
 <div className="allocation-detail-status"><span>Funding status</span><strong>{selectedAllocationStatus}</strong></div>
 </div>
 )}
 </article>
 </section>
 )}

 <section id="composition" className="traffic-overview" aria-label="Customer traffic composition dashboard">
 <div className="traffic-grid">
 <article className="traffic-card campaign-mix-card glass-panel">
 <div className="card-head insight-head">
 <div><h3>Lead share by campaign</h3></div>
 <div className="metric-toggle" aria-label="Campaign chart range">
 <button type="button" className={!showAllCampaigns ? 'active' : ''} onClick={() => setShowAllCampaigns(false)}>Top 10</button>
 <button type="button" className={showAllCampaigns ? 'active' : ''} onClick={() => setShowAllCampaigns(true)}>All {campaignMix.length}</button>
 </div>
 </div>
 <div className={`campaign-chart campaign-share-list${showAllCampaigns ? ' expanded' : ''}`} role="group" aria-label="Interactive campaign lead share ranking">
 {visibleCampaigns.map((item: any) => {
 const isActive = String(item.campaign_id) === String(selectedCampaignId);
 const share = Number(item.sharePercent || 0);
 return (
 <button
 type="button"
 key={item.campaign_id}
 className={`campaign-share-row${isActive ? ' active' : ''}`}
 aria-pressed={isActive}
 aria-label={`${item.campaign}, ${share.toFixed(1)} percent of leads`}
 onClick={() => setSelectedCampaignId(String(item.campaign_id))}
 >
 <span className="campaign-share-name">{String(item.campaign || '').replace(/^Leads\s*\|\s*/i, '')}</span>
 <span className="campaign-share-track">
 <i style={{ width: `${Math.max(2, (share / campaignShareMax) * 100)}%` }} />
 </span>
 <strong>{share.toFixed(1)}%</strong>
 </button>
 );
 })}
 </div>
 {selectedCampaign && <div className="campaign-detail" aria-live="polite">
 <div><span>Selected campaign</span><strong>{selectedCampaign.campaign}</strong><code>{selectedCampaign.campaign_id}</code></div>
 <div><span>Total leads</span><strong>{fmt(selectedCampaign.leads)}</strong></div>
 <div><span>Traffic share</span><strong>{selectedCampaign.sharePercent.toFixed(1)}%</strong></div>
 <div><span>Ad sets</span><strong>{fmt(selectedCampaign.ad_set_count)}</strong></div>
 </div>}
 </article>
 </div>
 </section>

 {adSpend.available && (
 <section id="efficiency" className="campaign-cpl-visuals" aria-label="Campaign CPL ranking and efficiency charts">
 <article className="campaign-cpl-chart-card cpl-rank-card">
 <div className="card-head compact campaign-chart-head cpl-rank-head">
 <div>
 <h3>Actual cost per lead</h3>
 <p>{cplBenchmark == null ? 'Portfolio cost per lead unavailable' : `Line marks the ${cplMoney(cplBenchmark)} portfolio cost per lead`}</p>
 </div>
 <div className="metric-toggle cpl-rank-toggle" aria-label="Cost per lead chart type">
 <button type="button" className={cplView === 'campaign' ? 'active' : ''} onClick={() => setCplView('campaign')}>Campaign</button>
 <button type="button" className={cplView === 'adset' ? 'active' : ''} onClick={() => setCplView('adset')}>Ad set</button>
 </div>
 </div>
 <div className="cpl-rank-grid">
 {cplColumns.map((column, columnIndex) => (
 <div className="cpl-rank-column" key={`cpl-col-${columnIndex}`}>
 {column.map((item: any) => (
 <button
 type="button"
 key={item.id}
 className={`cpl-rank-row tone-${item.tone}`}
 onClick={() => {
 if (cplView === 'campaign') setSelectedCampaignId(String(item.id));
 else selectPortfolioAdSet(String(item.id));
 }}
 >
 <span className="cpl-rank-label">{item.label}</span>
 <span className="cpl-rank-value">{cplMoney(item.value)}</span>
 <span className="cpl-rank-track">
 {cplBenchmarkPct != null && <i className="cpl-benchmark-mark" style={{ left: `${cplBenchmarkPct}%` }} />}
 <i className="cpl-rank-fill" style={{ width: `${Math.min(100, Math.max(1, Number(item.value || 0) / cplMax * 100))}%` }} />
 </span>
 </button>
 ))}
 </div>
 ))}
 </div>
 </article>
 </section>
 )}
 </div>
 );
}

function UploadPage() {
 const [preview, setPreview] = useState<any>(null);
 const [busy, setBusy] = useState(false);
 const [dragging, setDragging] = useState(false);
 const [error, setError] = useState('');
 const [batchFiles, setBatchFiles] = useState<File[]>([]);
 const [batchIndex, setBatchIndex] = useState(0);
 const input = useRef<HTMLInputElement>(null);

 const clearFileInput = () => {
 if (input.current) input.current.value = '';
 };

 const inspect = async (file: File) => {
 setBusy(true);
 setError('');
 setPreview(null);
 const body = new FormData();
 body.append('file', file);
 try { setPreview(await api('/uploads/preview', { method: 'POST', body })); }
 catch (uploadError: any) { setError(uploadError.message); }
 finally { setBusy(false); }
 };

 const startImportBatch = (fileList: FileList | File[]) => {
 const files = Array.from(fileList).filter((file) => /\.(csv|xlsx)$/i.test(file.name));
 if (busy) return;
 if (!files.length) {
 setError('Choose CSV or XLSX files to import.');
 return;
 }
 setBatchFiles(files);
 setBatchIndex(0);
 void inspect(files[0]);
 };

 const resetPreview = () => {
 if (batchFiles.length > 1 && batchIndex + 1 < batchFiles.length) {
 const nextIndex = batchIndex + 1;
 setBatchIndex(nextIndex);
 void inspect(batchFiles[nextIndex]);
 return;
 }
 setBatchFiles([]);
 setBatchIndex(0);
 setPreview(null);
 setError('');
 clearFileInput();
 };

 const importMessage = (result: any) => result.file_type === 'model_dataset'
 ? [
 `Model dataset imported: ${fmt(result.imported)} new leads, ${fmt(result.duplicates)} already known.`,
 `${fmt(result.ad_set_days_inserted + result.ad_set_days_updated)} ad-set days of context stored${result.zero_lead_days ? `, including ${fmt(result.zero_lead_days)} that spent with no leads` : ''}.`,
 result.change_events_inserted || result.change_events_updated
 ? `${fmt(result.change_events_inserted + result.change_events_updated)} change events recorded — variables 6, 7, 9 and 10 now use your data.`
 : 'Change type columns were empty, so variables 6, 7, 9 and 10 still use inferred events.',
 ].filter(Boolean).join('\n')
 : result.file_type === 'change_log'
 ? [
 `Change log imported: ${fmt(result.inserted)} new events, ${fmt(result.updated)} updated.`,
 `${fmt(result.confirmed_rows)} confirmed events now drive variables 6, 7, 9 and 10.`,
 result.unconfirmed_rows ? `${fmt(result.unconfirmed_rows)} rows are still unconfirmed and were ignored by the model.` : '',
 ].filter(Boolean).join('\n')
 : result.file_type === 'ad_performance'
 ? [
 `Ad performance imported: ${fmt(result.inserted)} inserted, ${fmt(result.updated)} updated.`,
 result.budget_periods_written ? `${fmt(result.budget_periods_written)} budget periods recorded.` : '',
 result.budget_periods_kept_manual ? `${fmt(result.budget_periods_kept_manual)} kept your manual entry instead.` : '',
 ].filter(Boolean).join('\n')
 : 'Import complete. Forecasts have been retrained.';

 const previewUpload = async (file: File) => {
 const body = new FormData();
 body.append('file', file);
 return api('/uploads/preview', { method: 'POST', body });
 };

 const confirmPreview = (uploadPreview: any) => api('/uploads/confirm', {
 method: 'POST',
 headers: { 'Content-Type': 'application/json' },
 body: JSON.stringify({ token: uploadPreview.token, file_name: uploadPreview.file_name }),
 });

 const confirmImport = async () => {
 setBusy(true);
 try {
 if (batchFiles.length > 1) {
 const imported: string[] = [];
 for (let index = batchIndex; index < batchFiles.length; index += 1) {
 setBatchIndex(index);
 setPreview(null);
 const filePreview = index === batchIndex ? preview : await previewUpload(batchFiles[index]);
 setPreview(filePreview);
 const result = await confirmPreview(filePreview);
 imported.push(`${filePreview.file_name}: ${result.file_type_label || result.file_type || 'imported'}`);
 }
 setBatchFiles([]);
 setBatchIndex(0);
 setPreview(null);
 clearFileInput();
 alert([`Batch import complete: ${fmt(imported.length)} files imported.`, ...imported].join('\n'));
 return;
 }
 const result = await confirmPreview(preview);
 setPreview(null);
 setBatchFiles([]);
 setBatchIndex(0);
 clearFileInput();
 alert(importMessage(result));
 } catch (uploadError: any) { setError(uploadError.message); }
 finally { setBusy(false); }
 };

 const isAdPerformance = preview?.file_type === 'ad_performance';
 const isChangeLog = preview?.file_type === 'change_log';
 const isModelDataset = preview?.file_type === 'model_dataset';
 const changeScopes: any = preview?.by_scope || {};
 const budgetPeriods: any[] = preview?.budget_periods || [];
 const budgetAdSets = new Set(budgetPeriods.map((period) => period.ad_set_id)).size;
 const budgetChanges = budgetPeriods.length - budgetAdSets;
 const queuedCount = batchFiles.length;
 const queuePosition = queuedCount ? batchIndex + 1 : 0;
 const remainingCount = Math.max(0, queuedCount - queuePosition);
 const filesToImportCount = Math.max(1, queuedCount - batchIndex);

 return (
 <div className="page-content narrow upload-page upload-v2-page">
 <section className={`upload-v2-head${preview ? ' no-divider' : ''}`}>
 <div>
 <h2>Upload data</h2>
 <p>Raw export in, dashboard-ready data out.</p>
 </div>
 <aside>
 <span>CSV · XLSX</span>
 <strong>Up to 50 MB</strong>
 </aside>
 </section>
 {!preview ? (
 <>
 <div className="upload-v2-types">Model dataset <i /> Customer traffic <i /> Meta ad performance <i /> Change log</div>
 <section className="upload-workspace">
 <div
 className={`upload-panel upload-panel-v2 ${dragging ? 'dragging' : ''}${busy ? ' busy' : ''}`}
 role="button"
 tabIndex={0}
 aria-label="Upload CSV or XLSX files"
 onClick={() => { if (!busy) input.current?.click(); }}
 onKeyDown={(event) => { if ((event.key === 'Enter' || event.key === ' ') && !busy) { event.preventDefault(); input.current?.click(); } }}
 onDragOver={(event) => { event.preventDefault(); setDragging(true); }}
 onDragLeave={() => setDragging(false)}
 onDrop={(event) => { event.preventDefault(); setDragging(false); startImportBatch(event.dataTransfer.files); }}
 >
 <input ref={input} type="file" accept=".xlsx,.csv" multiple hidden onChange={(event) => event.target.files && startImportBatch(event.target.files)} />
 <Upload className="upload-v2-icon" aria-hidden="true" size={28} />
 <h3>{busy ? 'Cleaning your data…' : dragging ? 'Release to upload' : 'Drop your files here'}</h3>
 <p className="upload-sub">{busy ? 'This only takes a moment' : 'or click to browse — CSV or XLSX, one or many'}</p>
 <button type="button" className="button primary upload-cta" disabled={busy} onClick={(event) => { event.stopPropagation(); input.current?.click(); }}>{busy ? <RefreshCw className="spin" size={16} /> : <Upload size={16} />}{busy ? 'Preparing' : 'Choose files'}</button>
 </div>
 </section>
 <div className="upload-v2-footer">
 <span>Columns detected automatically.</span>
 <a href="data:text/csv;charset=utf-8,created_at%2Ccustomer_name%2Cstatus%2Cutm_campaign%2Cutm_campaign_id%2Cutm_ad_set_id%2Cutm_ad_id%2Camount_spent_usd%0A" download="customer-traffic-template.csv">Download a template</a>
 </div>
 </>
 ) : (
 <>
 <section className="import-summary glass-panel">
 <div className="file-line">
 <div className="file-line-top">
 <div className="file-line-name"><b>{preview.file_name}</b><span className="valid"><i />Cleaned</span></div>
 <button onClick={resetPreview} aria-label="Remove preview"><X /></button>
 </div>
 <span className="file-line-sub">{preview.file_type_label || 'Detected file'} · {preview.date_min} to {preview.date_max}</span>
 </div>
 {queuedCount > 1 && (
 <div className="upload-batch-note">
 <span>File {fmt(queuePosition)} of {fmt(queuedCount)}</span>
 <b>{remainingCount ? `${fmt(remainingCount)} queued after this` : 'Last file in this batch'}</b>
 </div>
 )}
 {isModelDataset ? (
 <div className="mini-metrics cleaning-metrics">
 <div><span>Source rows</span><b>{fmt(preview.source_rows)}</b></div>
 <div className="metric-ready"><span>Leads</span><b>{fmt(preview.lead_rows)}</b></div>
 <div><span>Ad-set days</span><b>{fmt(preview.ad_set_day_rows)}</b></div>
 <div className={preview.excluded_rows ? 'metric-warning' : ''}><span>Skipped</span><b>{fmt(preview.excluded_rows)}</b></div>
 </div>
 ) : isChangeLog ? (
 <div className="mini-metrics cleaning-metrics">
 <div><span>Events read</span><b>{fmt(preview.clean_rows)}</b></div>
 <div className="metric-ready"><span>Confirmed</span><b>{fmt(preview.confirmed_rows)}</b></div>
 <div className={preview.unconfirmed_rows ? 'metric-warning' : ''}><span>Unconfirmed</span><b>{fmt(preview.unconfirmed_rows)}</b></div>
 <div className={preview.excluded_rows ? 'metric-warning' : ''}><span>Skipped</span><b>{fmt(preview.excluded_rows)}</b></div>
 </div>
 ) : (
 <div className="mini-metrics cleaning-metrics">
 <div><span>Source rows</span><b>{fmt(preview.source_rows)}</b></div>
 <div className="metric-ready"><span>{isAdPerformance ? 'Valid rows' : 'Model-ready'}</span><b>{fmt(preview.clean_rows)}</b></div>
 {isAdPerformance && preview.ad_grain_input
 ? <div><span>Ad rows merged</span><b>{fmt(preview.ad_rows_collapsed)}</b></div>
 : <div><span>IDs repaired</span><b>{fmt(isAdPerformance ? preview.recovered_ad_set_ids : preview.scientific_id_values || preview.recovered_rows)}</b></div>}
 <div className={preview.excluded_rows || preview.rejected_rows ? 'metric-warning' : ''}><span>{isAdPerformance ? 'Spend' : 'Excluded'}</span><b>{isAdPerformance ? money(preview.total_spend) : fmt(preview.excluded_rows)}</b></div>
 </div>
 )}
 </section>
 {isModelDataset ? (
 <p className="upload-context-line">
 <span><strong>{plural(preview.unique_ad_sets || 0, 'ad set')}</strong></span>
 <span><strong>{money(preview.total_spend)}</strong> spend across {plural(preview.ad_set_day_rows || 0, 'ad-set day')}</span>
 <span className={preview.change_events ? 'tone-good' : 'tone-warn'}>{preview.change_events ? `${plural(preview.change_events, 'change event')} confirmed` : 'using inferred change events'}</span>
 </p>
 ) : isChangeLog ? (
 <p className="upload-context-line">
 <span><strong>{fmt(changeScopes.ad_set?.confirmed || 0)}</strong> ad set changes across {plural(changeScopes.ad_set?.ad_sets || 0, 'ad set')}</span>
 <span><strong>{fmt(changeScopes.ad?.confirmed || 0)}</strong> ad changes across {plural(changeScopes.ad?.ad_sets || 0, 'ad set')}</span>
 <span className={preview.unconfirmed_rows ? 'tone-warn' : 'tone-good'}>{preview.unconfirmed_rows ? `${plural(preview.unconfirmed_rows, 'unconfirmed row')} ignored` : 'every row confirmed'}</span>
 </p>
 ) : (
 <p className="upload-context-line">
 <span><strong>{plural(preview.unique_ad_sets || 0, 'ad set')}</strong></span>
 {isAdPerformance && preview.ad_grain_input ? (
 <span><strong>{fmt(preview.source_rows)}</strong> ad rows → <strong>{fmt(preview.ad_set_day_rows)}</strong> ad-set days</span>
 ) : (
 <span><strong>{fmt(isAdPerformance ? preview.recovered_ad_set_ids : preview.recovered_rows)}</strong> {isAdPerformance ? 'ad set IDs' : 'leads'} repaired</span>
 )}
 <span className={(preview.excluded_rows || preview.rejected_rows) ? 'tone-warn' : 'tone-good'}>{(preview.excluded_rows || preview.rejected_rows) ? `${fmt(isAdPerformance ? preview.rejected_rows || 0 : preview.excluded_rows || 0)} rows need attention` : 'nothing needs attention'}</span>
 </p>
 )}
 {(isChangeLog || isModelDataset) && (preview.warnings || []).length > 0 && (
 <section className="upload-warning"><Info size={14} /><div><b>Before you import</b><p>{(preview.warnings || []).join(' ')}</p></div></section>
 )}
 {isAdPerformance && budgetPeriods.length > 0 && (
 <section className="table-card glass-panel budget-detected">
 <div className="card-head">
 <div>
 <span>BUDGET HISTORY</span>
 <h3>{budgetChanges > 0 ? `${fmt(budgetChanges)} budget change${budgetChanges === 1 ? '' : 's'} across ${fmt(budgetAdSets)} ad sets` : `One flat budget per ad set — no changes in this file`}</h3>
 </div>
 </div>
 <div className="budget-detected-list">
 {budgetPeriods.map((period, index) => (
 <div className="budget-detected-row" key={`${period.ad_set_id}-${index}`}>
 <div className="budget-detected-name">
 <b>{period.campaign_name || period.ad_set_id}</b>
 <small>{dateFmt(period.start_date)} → {dateFmt(period.end_date)} · {fmt(period.observed_days)} days</small>
 </div>
 <span className="budget-detected-amount">{cplMoney(period.daily_budget)}<small>/day</small></span>
 </div>
 ))}
 </div>
 <div className="confirm-bar budget-detected-note">
 <div><Info /><p><b>These become dated budget periods for the Budget Scenario</b><span>Budgets you entered by hand are never overwritten. The declared budget is accepted as-is, with no comparison against actual spend.</span></p></div>
 </div>
 </section>
 )}
 <section className="table-card glass-panel">
 <div className="card-head"><div><span>CLEAN DATA PREVIEW</span><h3>{isChangeLog ? 'First 8 change events' : isAdPerformance ? 'First 8 cleaned ad performance rows' : 'First 8 model-ready leads'}</h3></div><span className="schema-count">{isChangeLog ? `${(preview.sheets_read || []).length} changelog sheets read` : isModelDataset ? `${plural(preview.ad_set_day_rows || 0, 'ad-set day')} of context` : `${preview.recognized_columns?.length || 0} source fields recognized`}</span></div>
 <div className="table-scroll"><table><thead><tr>{preview.columns.map((column: string) => <th key={column} style={UPLOAD_COLUMN_WIDTHS[column] ? { width: UPLOAD_COLUMN_WIDTHS[column] } : undefined}>{column}</th>)}</tr></thead><tbody>{preview.rows.map((row: any, rowIndex: number) => <tr key={rowIndex}>{preview.columns.map((column: string) => {
 const variant = column === 'Created At' ? 'time' : column === 'Customer Name' ? 'name' : column === 'Status' ? 'status' : /id$/i.test(column) ? 'id' : 'default';
 const value = row[column] ?? '-';
 return (
 <td key={column} className={variant !== 'default' ? `upload-cell-${variant}` : undefined} data-status={variant === 'status' ? String(value) : undefined} style={UPLOAD_COLUMN_WIDTHS[column] ? { width: UPLOAD_COLUMN_WIDTHS[column] } : undefined}>{String(value)}</td>
 );
 })}</tr>)}</tbody></table></div>
 {/* The import button used to live here, at the very bottom of the last section. With the
     stats, a scrollable 16-row budget list and the preview table above it, the only action
     on the page sat below the fold on a laptop -- and the budget list's own scrollbar
     swallows the wheel, so the page looked like it had nothing more to show. The note stays
     here where its context is; the action moved to the sticky bar below. */}
 <div className="confirm-bar"><div><Info /><p><b>Ready to import {fmt(preview.clean_rows)} {isModelDataset ? 'leads with their ad set context' : isChangeLog ? 'change events' : isAdPerformance ? 'ad spend rows' : 'leads'}</b><span>{isModelDataset ? 'Leads and ad-set-day context land from this one file. Leads are matched by content, so re-uploading an overlapping week adds nothing twice.' : isChangeLog ? 'An ad set with confirmed events stops using detected ones entirely; ad sets you have not recorded keep the detector. Re-uploading a corrected file updates events in place.' : isAdPerformance ? 'Only rows with Amount spent (USD) are stored. Optional metrics can be blank and filled later by analytics.' : 'The raw export stays preserved. Existing lead IDs are skipped and forecasts retrain after import.'}</span></p></div></div>
 </section>
 <div className="upload-commit-bar">
 <div className="upload-commit-facts">
 <b>{queuedCount > 1 ? `${fmt(filesToImportCount)} files queued` : `${fmt(preview.clean_rows)} ${isModelDataset ? 'leads' : isChangeLog ? 'change events' : isAdPerformance ? 'ad spend rows' : 'leads'} ready`}</b>
 <span>{queuedCount > 1 ? `Previewing ${fmt(queuePosition)} of ${fmt(queuedCount)} · ${preview.file_name}` : `${preview.date_min} → ${preview.date_max}${isAdPerformance && preview.total_spend != null ? ` · ${cplMoney(preview.total_spend)}` : ''}${preview.rejected_rows ? ` · ${fmt(preview.rejected_rows)} rejected` : ''}`}</span>
 </div>
 <div className="upload-commit-actions">
 <button className="button secondary" disabled={busy} onClick={resetPreview}>{queuedCount > 1 && remainingCount ? 'Skip file' : 'Discard'}</button>
 <button className="button primary" disabled={busy} onClick={confirmImport}>{busy ? <RefreshCw className="spin" /> : <Check />}{busy ? 'Importing' : queuedCount > 1 ? `Import ${fmt(filesToImportCount)} files` : isModelDataset ? 'Import model dataset' : isChangeLog ? 'Import change log' : isAdPerformance ? 'Import ad spend' : 'Import clean data'}</button>
 </div>
 </div>
 </>
 )}
 {error && <div className="error-banner">{error}</div>}
 </div>
 );
}

function HistoryPage() {
 const [rows, setRows] = useState<any[]>([]);
 const load = () => api('/uploads').then(setRows);
 useEffect(() => { void load(); }, []);
 const totals = {
  files: rows.length,
  rows: rows.reduce((sum, row) => sum + Number(row.row_count || 0), 0),
  duplicates: rows.reduce((sum, row) => sum + Number(row.duplicate_count || 0), 0),
 };
 const removeUpload = async (row: any, isSpend: boolean) => {
  if (confirm(isSpend ? 'Delete this ad performance upload?' : 'Delete this upload and retrain forecasts?')) {
   await api(`/uploads/${row.id}`, { method: 'DELETE' });
   load();
  }
 };
 return (
 <div className="page-content imports-page">
 <section className="imports-heading">
 <h2>Imports</h2>
 <p>Every file that's landed in this workspace, traced row by row.</p>
 </section>
 <section className="imports-summary" aria-label="Import summary">
 <div><span>Confirmed files</span><strong>{totals.files}</strong></div>
 <div><span>Rows processed</span><strong>{fmt(totals.rows)}</strong></div>
 <div><span>Duplicates prevented</span><strong>{fmt(totals.duplicates)}</strong></div>
 </section>
 <section className="imports-ledger" aria-label="Confirmed files">
 <div className="imports-ledger-head">
 <h3>{totals.files} confirmed files</h3>
 <span className="imports-status"><i />Storage healthy</span>
 </div>
 <div className="imports-scroll">
 <div className="imports-grid" role="table" aria-label="Upload history">
 <div className="imports-grid-header" role="row">
 <span>File</span><span>Type</span><span>Imported</span><span>Date range</span><span className="num">Rows</span><span className="num">New</span><span className="num">Updated</span><span className="num">Dupes</span><span className="num">Spend</span><span>Status</span><span />
 </div>
 {rows.map((row) => {
  const isSpend = row.file_type === 'ad_performance';
  const typeLabel = { ad_performance: 'Ad spend', change_log: 'Change log', model_dataset: 'Model dataset' }[row.file_type as string] || 'Traffic';
  return (
  <div className="imports-row" role="row" key={row.id}>
  <span className="imports-file" title={row.file_name}>{row.file_name}</span>
  <span className={isSpend ? 'imports-type spend' : 'imports-type'}>{typeLabel}</span>
  <span>{dateFmt(row.uploaded_at)}</span>
  <span className="imports-range">{compactDateRangeFmt(row.date_min, row.date_max)}</span>
  <span className="num">{fmt(row.row_count)}</span>
  <span className="num imports-new">{fmt(row.imported_count)}</span>
  <span className="num imports-muted-num">{fmt(row.updated_count || 0)}</span>
  <span className="num imports-muted-num">{fmt(row.duplicate_count)}</span>
  <span className="num">{isSpend ? money(row.total_spend_usd) : '-'}</span>
  <span className="imports-valid"><i />Imported</span>
  <button className="imports-delete" title={isSpend ? 'Delete ad performance upload' : 'Delete upload and retrain'} aria-label={isSpend ? 'Delete ad performance upload' : 'Delete upload and retrain'} onClick={() => void removeUpload(row, isSpend)}><Trash2 size={13} /></button>
  </div>
  );
 })}
 {!rows.length && <div className="table-empty">No upload history yet.</div>}
 </div>
 </div>
 </section>
 </div>
 );
}

// The backend's declared-variable names are a mix of casings (`Leads`, `Holiday_Proximity`,
// `days_since_adset_started`, "Days of the week") since they're identifiers first and a
// display label second. The 10-variable correlation matrix needs one consistent, humanized
// label per variable -- these mirror the wording `_feature_label()` already produces for
// the expanded feature-level matrix, so the two matrices read as the same vocabulary.
const DATASET_DECLARED_SHORT_LABEL: Record<number, string> = {
 1: 'Leads', 2: 'Spend', 3: 'Holiday proximity', 4: 'Ad set age', 5: 'Frequency',
 6: 'Ad change recency', 7: 'Ad set change recency', 8: 'Day of week',
};

type DatasetRowColumn = {
 key: string; label: string; render?: (row: any) => any;
 // Board-cell behaviour. `align: 'num'` right-aligns and tabular-numbers the column.
 // `edit` opts the column into inline editing (leads only -- it's the one table with a
 // PATCH endpoint); the value is the input type handed to DatasetCell.
 align?: 'num'; edit?: 'text' | 'number' | 'date' | 'datetime-local' | 'status'; width?: number;
};
// Every column always renders -- no "short"/"all" split, no toggle. Per feedback: the raw-row
// browser should just show everything up front.
const DATASET_ROW_COLUMNS: Record<'leads' | 'ad_performance' | 'ad_performance_export', DatasetRowColumn[]> = {
 leads: [
  { key: 'created_at', label: 'Created', width: 132, edit: 'datetime-local', render: (row) => dateFmt(row.created_at) },
  { key: 'customer_name', label: 'Customer', width: 176, edit: 'text' },
  { key: 'status', label: 'Status', width: 116, edit: 'status' },
  { key: 'utm_campaign', label: 'Campaign', width: 184, edit: 'text' },
  { key: 'utm_campaign_id', label: 'Campaign ID', width: 168, edit: 'text' },
  { key: 'utm_ad_set_id', label: 'Ad set ID', width: 168, edit: 'text' },
  { key: 'utm_ad_id', label: 'Ad ID', width: 168, edit: 'text' },
  { key: 'fb_ad_title', label: 'Ad title', width: 158, edit: 'text' },
  { key: 'amount_spent_usd', label: 'Amount', width: 108, align: 'num', edit: 'number', render: (row) => row.amount_spent_usd == null ? '-' : cplMoney(row.amount_spent_usd) },
 ],
 ad_performance: [
  { key: 'day', label: 'Day', width: 116, edit: 'date', render: (row) => dateFmt(row.day) },
  { key: 'campaign_name', label: 'Campaign', width: 196, edit: 'text' },
  { key: 'campaign_id', label: 'Campaign ID', width: 168, edit: 'text' },
  { key: 'ad_set_id', label: 'Ad set ID', width: 168, edit: 'text' },
  { key: 'amount_spent_usd', label: 'Spend', width: 100, align: 'num', edit: 'number', render: (row) => money(row.amount_spent_usd) },
  // Not editable: the board's Leads value is the CRM-attributed lead_count joined in from
  // daily_ad_set_aggregates, not daily_ad_performance.leads -- writing that column would
  // change nothing visible here. Same reasoning as the backend's update allowlist.
  { key: 'leads', label: 'Leads', width: 84, align: 'num', render: (row) => fmt(row.leads) },
  { key: 'cost_per_lead', label: 'CPL', width: 90, align: 'num', edit: 'number', render: (row) => cplMoney(row.cost_per_lead) },
  { key: 'reach', label: 'Reach', width: 96, align: 'num', edit: 'number', render: (row) => fmt(row.reach) },
  { key: 'impressions', label: 'Impressions', width: 108, align: 'num', edit: 'number', render: (row) => fmt(row.impressions) },
  { key: 'frequency', label: 'Frequency', width: 106, align: 'num', edit: 'number', render: (row) => row.frequency == null ? '-' : Number(row.frequency).toFixed(4) },
  // Displays budget and its type together but edits only the amount; the type has its own
  // editable column on the Combined export tab, which lists the two separately.
  { key: 'ad_set_budget', label: 'Budget', width: 132, align: 'num', edit: 'number', render: (row) => row.ad_set_budget == null ? '-' : `${money(row.ad_set_budget)} / ${row.ad_set_budget_type || '-'}` },
  { key: 'days_since_adset_started', label: 'days_since_adset_started', width: 210, align: 'num', render: (row) => row.days_since_adset_started ?? '-' },
  // Grouped by subject -- the two ad-set columns, then the two ad columns -- so the
  // recency/type pair for one subject reads together instead of interleaving.
  // Recency is a bucket string from the backend now, not a day count, so these are no longer
  // `align: 'num'` -- right-aligning `no_recent_change` against `0_3_days` reads as ragged.
  { key: 'ad_set_change_recency', label: 'ad_set_change_recency', width: 192, render: (row) => rawCategory(row.ad_set_change_recency) },
  { key: 'ad_change_recency', label: 'ad_change_recency', width: 168, render: (row) => rawCategory(row.ad_change_recency) },
 ],
 // Same rows as `ad_performance`, laid out in the column order/naming of the raw
 // Combined-Ad-Set-Dataset export the ad-performance importer accepts, for cross-checking
 // an upload against what actually landed. "Ad ID" and "Fb Ad Title" always render "-" --
 // the importer's ad-grain rollup discards per-ad identity once several ads' rows are
 // summed into one ad-set-day row (see Vault/Gotchas/Leadlens-Ad-Export-Grain-And-Budget.md),
 // so there's no real value to show, not a rendering gap.
 ad_performance_export: [
  { key: 'day', label: 'Day', width: 116, edit: 'date', render: (row) => dateFmt(row.day) },
  { key: 'campaign_name', label: 'Campaign Name', width: 196, edit: 'text' },
  { key: 'campaign_id', label: 'Campaign ID', width: 168, edit: 'text' },
  { key: 'ad_set_id', label: 'Ad set ID', width: 168, edit: 'text' },
  // No backend column behind either of these (the importer's ad-grain rollup discards per-ad
  // identity), so they stay read-only placeholders rather than accepting an edit that would
  // have nowhere to go.
  { key: 'ad_id', label: 'Ad ID', width: 84, render: () => '-' },
  { key: 'fb_ad_title', label: 'Fb Ad Title', width: 108, render: () => '-' },
  { key: 'reach', label: 'Reach', width: 96, align: 'num', edit: 'number', render: (row) => fmt(row.reach) },
  { key: 'impressions', label: 'Impression', width: 104, align: 'num', edit: 'number', render: (row) => fmt(row.impressions) },
  { key: 'frequency', label: 'Frequency', width: 104, align: 'num', edit: 'number', render: (row) => row.frequency == null ? '-' : Number(row.frequency).toFixed(4) },
  { key: 'messaging_conversations_started', label: 'Messaging Conversation', width: 178, align: 'num', edit: 'number', render: (row) => fmt(row.messaging_conversations_started) },
  { key: 'ad_set_budget', label: 'Ad set budget', width: 126, align: 'num', edit: 'number', render: (row) => row.ad_set_budget == null ? '-' : money(row.ad_set_budget) },
  { key: 'ad_set_budget_type', label: 'Ad set budget type', width: 144, edit: 'text' },
  { key: 'amount_spent_usd', label: 'Amount Spent (USD)', width: 156, align: 'num', edit: 'number', render: (row) => money(row.amount_spent_usd) },
  { key: 'leads', label: 'Leads', width: 84, align: 'num', render: (row) => fmt(row.leads) },
  { key: 'cost_per_lead', label: 'Cost Per Lead', width: 126, align: 'num', edit: 'number', render: (row) => cplMoney(row.cost_per_lead) },
  // Rendered as a COALESCE of the stored value and spend/messages, so a hand-entered figure
  // would be silently overridden whenever the stored one is NULL. Read-only by design.
  { key: 'cost_per_messaging_conversation_started', label: 'Cost Per Message', width: 142, align: 'num', render: (row) => row.cost_per_messaging_conversation_started == null ? '-' : cplMoney(row.cost_per_messaging_conversation_started) },
  { key: 'days_since_adset_started', label: 'days_since_adset_started', width: 210, align: 'num', render: (row) => row.days_since_adset_started ?? '-' },
  // Grouped by subject -- the two ad-set columns, then the two ad columns -- so the
  // recency/type pair for one subject reads together instead of interleaving.
  // Recency is a bucket string from the backend now, not a day count, so these are no longer
  // `align: 'num'` -- right-aligning `no_recent_change` against `0_3_days` reads as ragged.
  { key: 'ad_set_change_recency', label: 'ad_set_change_recency', width: 192, render: (row) => rawCategory(row.ad_set_change_recency) },
  { key: 'ad_change_recency', label: 'ad_change_recency', width: 168, render: (row) => rawCategory(row.ad_change_recency) },
 ],
};

// Which columns the backend can ORDER BY -- mirrors DATASET_ROW_TABLES[*]["sort_fields"] in
// backend/core.py. Columns absent here render an inert header: the declared-variable columns
// (ad set age, change recency/type) are attached in Python *after* the query by
// `_attach_declared_variables`, so no SQL ORDER BY can reach them, and the export tab's
// "Ad ID"/"Fb Ad Title" are hardcoded "-" placeholders with no column behind them at all.
const DATASET_SORT_FIELDS: Record<'leads' | 'ad_performance' | 'ad_performance_export', string[]> = {
 leads: ['created_at', 'customer_name', 'status', 'utm_campaign', 'utm_campaign_id',
         'utm_ad_set_id', 'utm_ad_id', 'fb_ad_title', 'amount_spent_usd'],
 ad_performance: ['day', 'campaign_name', 'campaign_id', 'ad_set_id', 'amount_spent_usd',
                  'leads', 'cost_per_lead', 'reach', 'impressions', 'frequency', 'ad_set_budget'],
 ad_performance_export: ['day', 'campaign_name', 'campaign_id', 'ad_set_id', 'reach',
                         'impressions', 'frequency', 'messaging_conversations_started',
                         'ad_set_budget', 'ad_set_budget_type', 'amount_spent_usd', 'leads',
                         'cost_per_lead', 'cost_per_messaging_conversation_started'],
};

// Advanced filters for the Dataset page's "Raw data" table, in the spirit of Monday.com's
// "Where [column] [is] [value]" filter bar. Keys and types here are a contract with the
// backend allowlist -- see DATASET_ROW_TABLES[*]["filter_fields"] in backend/core.py -- rename
// a key or change a type in only one place and filters silently stop matching.
type FilterFieldType = 'text' | 'number' | 'date' | 'enum';
type FilterField = { key: string; label: string; type: FilterFieldType; options?: string[] };
type FilterRow = { id: number; field: string; operator: string; value: any };

const DATASET_FILTER_FIELDS: Record<'leads' | 'ad_performance' | 'ad_performance_export', FilterField[]> = {
 leads: [
  { key: 'status', label: 'Status', type: 'enum', options: ['New', 'Existing'] },
  { key: 'customer_name', label: 'Customer', type: 'text' },
  { key: 'utm_campaign', label: 'Campaign', type: 'text' },
  { key: 'utm_campaign_id', label: 'Campaign ID', type: 'text' },
  { key: 'utm_ad_set_id', label: 'Ad set ID', type: 'text' },
  { key: 'utm_ad_id', label: 'Ad ID', type: 'text' },
  { key: 'fb_ad_title', label: 'Ad title', type: 'text' },
  { key: 'amount_spent_usd', label: 'Amount', type: 'number' },
  { key: 'created_at', label: 'Created', type: 'date' },
 ],
 // Same field set for both -- "ad_performance" and "ad_performance_export" are two views of
 // the same underlying rows (see DATASET_ROW_COLUMNS above), so a filter means the same thing
 // in either tab.
 ad_performance: [
  { key: 'day', label: 'Day', type: 'date' },
  { key: 'campaign_name', label: 'Campaign', type: 'text' },
  { key: 'campaign_id', label: 'Campaign ID', type: 'text' },
  { key: 'ad_set_id', label: 'Ad set ID', type: 'text' },
  { key: 'delivery_status', label: 'Delivery status', type: 'text' },
  { key: 'amount_spent_usd', label: 'Spend', type: 'number' },
  { key: 'cost_per_lead', label: 'CPL', type: 'number' },
  { key: 'reach', label: 'Reach', type: 'number' },
  { key: 'impressions', label: 'Impressions', type: 'number' },
  { key: 'frequency', label: 'Frequency', type: 'number' },
  { key: 'ad_set_budget', label: 'Budget', type: 'number' },
 ],
 ad_performance_export: [],
};
DATASET_FILTER_FIELDS.ad_performance_export = DATASET_FILTER_FIELDS.ad_performance;

const FILTER_OPERATORS: Record<FilterFieldType, { value: string; label: string }[]> = {
 text: [
  { value: 'contains', label: 'contains' },
  { value: 'not_contains', label: 'does not contain' },
  { value: 'is', label: 'is' },
  { value: 'is_not', label: 'is not' },
  { value: 'is_empty', label: 'is empty' },
  { value: 'is_not_empty', label: 'is not empty' },
 ],
 number: [
  { value: 'eq', label: '=' },
  { value: 'neq', label: '≠' },
  { value: 'gt', label: '>' },
  { value: 'gte', label: '≥' },
  { value: 'lt', label: '<' },
  { value: 'lte', label: '≤' },
  { value: 'is_empty', label: 'is empty' },
  { value: 'is_not_empty', label: 'is not empty' },
 ],
 date: [
  { value: 'on', label: 'is' },
  { value: 'before', label: 'is before' },
  { value: 'after', label: 'is after' },
  { value: 'between', label: 'is between' },
  { value: 'is_empty', label: 'is empty' },
  { value: 'is_not_empty', label: 'is not empty' },
 ],
 enum: [
  { value: 'is', label: 'is' },
  { value: 'is_not', label: 'is not' },
 ],
};

let filterRowSeq = 0;
const newFilterRow = (field: FilterField): FilterRow => ({
 id: ++filterRowSeq, field: field.key, operator: FILTER_OPERATORS[field.type][0].value, value: field.type === 'enum' ? [] : '',
});

// A filter row only ships to the backend once it has a usable value -- an empty text/number
// box or a date with nothing picked would otherwise round-trip as a no-op WHERE clause, or
// worse (an empty "contains" matches every row). is_empty/is_not_empty need no value at all.
const isFilterRowComplete = (row: FilterRow): boolean => {
 if (row.operator === 'is_empty' || row.operator === 'is_not_empty') return true;
 if (Array.isArray(row.value)) return row.value.length > 0;
 if (row.operator === 'between') return !!(row.value && row.value.from && row.value.to);
 return row.value !== '' && row.value != null;
};

// One icon per field *type* (not one per field) -- keeps the field-picker menu's icon
// column meaningful without needing a bespoke icon for every single column.
const FILTER_TYPE_ICON: Record<FilterFieldType, typeof Pencil> = {
 text: Pencil, number: Gauge, date: CalendarDays, enum: UserCheck,
};

// Custom trigger + menu standing in for a native <select> -- a native select's popup is
// OS-drawn and can't be themed or animated, which is why the field/operator pickers in the
// filter row previously looked like plain system dropdowns.
//
// Portaled to document.body with measured `position: fixed` coordinates, same fix already
// applied to SingleDatePicker's calendar (same file) --
// a menu rendered in place would get clipped by `.filter-menu`'s own `overflow: auto` box
// the moment a filter row sits low in that scrolling panel, which is exactly where a filter
// row usually is once you've added more than one.
function MenuSelect({ value, options, onChange, className, ariaLabel }: {
 value: string; options: { value: string; label: string; icon?: typeof Pencil }[];
 onChange: (value: string) => void; className?: string; ariaLabel: string;
}) {
 const [open, setOpen] = useState(false);
 const [menuPos, setMenuPos] = useState({ top: 0, left: 0, width: 0, openUp: false });
 const wrapRef = useRef<HTMLDivElement>(null);
 const triggerRef = useRef<HTMLButtonElement>(null);
 const menuRef = useRef<HTMLDivElement>(null);
 const current = options.find((item) => item.value === value) || options[0];

 // Matches SingleDatePicker's reposition() (same file):
 // measure on open plus window scroll/resize, flip upward when there's more room above than
 // below and the menu doesn't fit below.
 useLayoutEffect(() => {
  if (!open) return;
  const trigger = triggerRef.current;
  if (!trigger) return;
  const measure = () => {
   const rect = trigger.getBoundingClientRect();
   const menuHeight = menuRef.current?.getBoundingClientRect().height || 220;
   const roomBelow = window.innerHeight - rect.bottom - 8;
   const roomAbove = rect.top - 8;
   const openUp = menuHeight > roomBelow && roomAbove > roomBelow;
   setMenuPos({
    top: openUp ? rect.top - 6 : rect.bottom + 6,
    left: rect.left,
    width: Math.max(rect.width, 168),
    openUp,
   });
  };
  measure();
  window.addEventListener('scroll', measure, true);
  window.addEventListener('resize', measure);
  return () => {
   window.removeEventListener('scroll', measure, true);
   window.removeEventListener('resize', measure);
  };
 }, [open]);

 useEffect(() => {
  if (!open) return;
  const closeOnOutsideClick = (event: MouseEvent) => {
   const target = event.target as HTMLElement;
   if (wrapRef.current?.contains(target)) return;
   // The menu itself renders through a portal to document.body, so it's never a DOM
   // descendant of wrapRef -- without this, picking an option would register as an outside
   // click and close the menu before its own onClick fires.
   if (target.closest?.('.menu-select-menu')) return;
   setOpen(false);
  };
  const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
  document.addEventListener('mousedown', closeOnOutsideClick);
  document.addEventListener('keydown', closeOnEscape);
  return () => {
   document.removeEventListener('mousedown', closeOnOutsideClick);
   document.removeEventListener('keydown', closeOnEscape);
  };
 }, [open]);

 return (
  <div className={`menu-select${open ? ' open' : ''}${className ? ` ${className}` : ''}`} ref={wrapRef}>
   <button ref={triggerRef} type="button" className="menu-select-trigger" aria-haspopup="listbox" aria-expanded={open} aria-label={ariaLabel} onClick={() => setOpen((v) => !v)}>
    {current?.icon && <current.icon size={13} />}
    <span>{current?.label}</span>
    <ChevronDown size={13} className="menu-select-caret" />
   </button>
   {open && createPortal(
    <div
     ref={menuRef}
     className={`menu-select-menu${menuPos.openUp ? ' opens-upward' : ''}`}
     role="listbox"
     aria-label={ariaLabel}
     style={{ position: 'fixed', top: menuPos.top, left: menuPos.left, minWidth: menuPos.width }}
    >
     {options.map((option) => {
      const active = option.value === value;
      return (
       <button
        type="button"
        key={option.value}
        role="option"
        aria-selected={active}
        className={`menu-select-option${active ? ' active' : ''}`}
        onClick={() => { onChange(option.value); setOpen(false); }}
       >
        {option.icon && <option.icon size={14} />}
        <span>{option.label}</span>
        {active && <Check size={13} className="menu-select-check" />}
       </button>
      );
     })}
    </div>,
    document.body
   )}
  </div>
 );
}

// Monday.com-style click-to-edit table cell: shows a plain display state until clicked, then
// swaps in a live input in the same slot. Commits on blur/Enter, reverts on Escape without
// saving. Generic over the input type, and shared by the Forecast page's lead drilldown and
// the Dataset page's board (leads *and* both ad-performance tabs) -- the `lead-cell-*` class
// names it styles with predate that second caller and are now just the shared styling hook.
function LeadEditableCell({ value, type = 'text', placeholder = '-', align, disabled, onCommit, formatDisplay }: {
 value: string; type?: 'text' | 'number' | 'date' | 'time' | 'datetime-local'; placeholder?: string;
 align?: 'num'; disabled?: boolean; onCommit: (value: string) => void; formatDisplay?: (value: string) => any;
}) {
 const [editing, setEditing] = useState(false);
 const [draft, setDraft] = useState(value);
 const inputRef = useRef<HTMLInputElement>(null);

 useEffect(() => { if (!editing) setDraft(value); }, [value, editing]);
 useEffect(() => {
  if (!editing) return;
  inputRef.current?.focus();
  inputRef.current?.select();
 }, [editing]);

 const commit = () => {
  setEditing(false);
  if (draft !== value) onCommit(draft);
 };

 if (editing) {
  return (
   <input
    ref={inputRef}
    className="lead-cell-input"
    type={type}
    step={type === 'number' ? '0.01' : undefined}
    min={type === 'number' ? '0' : undefined}
    value={draft}
    onChange={(event) => setDraft(event.target.value)}
    onBlur={commit}
    onKeyDown={(event) => {
     if (event.key === 'Enter') { event.preventDefault(); commit(); }
     if (event.key === 'Escape') { event.preventDefault(); setDraft(value); setEditing(false); }
    }}
   />
  );
 }
 return (
  <button
   type="button"
   className={`lead-cell-display${align === 'num' ? ' num' : ''}${value ? '' : ' is-empty'}`}
   disabled={disabled}
   onClick={() => setEditing(true)}
  >
   {formatDisplay ? formatDisplay(value) : (value || placeholder)}
  </button>
 );
}

function FilterValueInput({ field, row, onChange }: { field: FilterField; row: FilterRow; onChange: (value: any) => void }) {
 if (row.operator === 'is_empty' || row.operator === 'is_not_empty') return null;
 if (field.type === 'enum') {
  return (
   <div className="filter-chip-picker">
    {(field.options || []).map((option) => {
     const active = (row.value || []).includes(option);
     return (
      <button
       type="button"
       key={option}
       className={`filter-chip${active ? ' active' : ''}`}
       onClick={() => onChange(active ? row.value.filter((v: string) => v !== option) : [...(row.value || []), option])}
      >
       {active && <Check size={11} />}{option}
      </button>
     );
    })}
   </div>
  );
 }
 if (field.type === 'number') {
  return <input className="filter-value-input" type="number" value={row.value ?? ''} onChange={(event) => onChange(event.target.value)} placeholder="Value" />;
 }
 if (field.type === 'date') {
  if (row.operator === 'between') {
   const from = row.value?.from || '';
   const to = row.value?.to || '';
   return (
    <div className="filter-date-range">
     <input type="date" value={from} onChange={(event) => onChange({ ...row.value, from: event.target.value })} />
     <span>to</span>
     <input type="date" value={to} onChange={(event) => onChange({ ...row.value, to: event.target.value })} />
    </div>
   );
  }
  return <input className="filter-value-input" type="date" value={row.value ?? ''} onChange={(event) => onChange(event.target.value)} />;
 }
 return <input className="filter-value-input" type="text" value={row.value ?? ''} onChange={(event) => onChange(event.target.value)} placeholder="Value" />;
}

// Monday.com-style "Where [column] [is] [value]" advanced filter bar, sitting above the
// Dataset page's "Raw data" table. All filters AND together (no groups/OR -- not needed yet
// for a handful of fields on one table).
function FilterBar({
 table, filters, appliedCount, resultLabel, onChange, onApply, onClearAll,
}: {
 table: 'leads' | 'ad_performance' | 'ad_performance_export'; filters: FilterRow[]; appliedCount: number;
 // Row count the current filter yields, echoed in the panel header the way Monday's
 // "Showing all of N items" does -- it turns the panel from "what am I asking for"
 // into "what am I getting".
 resultLabel?: string;
 onChange: (rows: FilterRow[]) => void; onApply: () => void; onClearAll: () => void;
}) {
 const [open, setOpen] = useState(false);
 const ref = useRef<HTMLDivElement>(null);
 const fields = DATASET_FILTER_FIELDS[table];

 useEffect(() => {
  const closeOnOutsideClick = (event: MouseEvent) => {
   const target = event.target as HTMLElement;
   if (ref.current?.contains(target)) return;
   // The field/operator MenuSelect menus render through a portal to document.body, so
   // they're never DOM descendants of this popover's own ref -- without this, picking a
   // field or operator would register as a click outside the whole "Advanced filters"
   // popover and close it before MenuSelect's own onChange fires.
   if (target.closest?.('.menu-select-menu')) return;
   setOpen(false);
  };
  document.addEventListener('mousedown', closeOnOutsideClick);
  return () => document.removeEventListener('mousedown', closeOnOutsideClick);
 }, []);

 // The badge on the closed button reflects what's actually applied to the table right now
 // (`appliedCount`), not the in-progress draft -- editing a filter without hitting Apply
 // shouldn't change what the button claims is active.
 const draftReady = filters.some(isFilterRowComplete);

 const updateRow = (id: number, patch: Partial<FilterRow>) => {
  onChange(filters.map((row) => (row.id === id ? { ...row, ...patch } : row)));
 };
 const setRowField = (id: number, fieldKey: string) => {
  const field = fields.find((item) => item.key === fieldKey) || fields[0];
  updateRow(id, { field: field.key, operator: FILTER_OPERATORS[field.type][0].value, value: field.type === 'enum' ? [] : '' });
 };
 const setRowOperator = (id: number, operator: string, field: FilterField) => {
  updateRow(id, { operator, value: field.type === 'enum' ? [] : '' });
 };
 const removeRow = (id: number) => onChange(filters.filter((row) => row.id !== id));
 const addRow = () => onChange([...filters, newFilterRow(fields[0])]);

 return (
  <div className={`filter-picker${open ? ' open' : ''}`} ref={ref}>
   <button type="button" className="selector filter-selector" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
    <Filter size={15} />
    <span>Filter{appliedCount ? ` / ${appliedCount}` : ''}</span>
    <ChevronDown size={14} className="campaign-caret" />
   </button>
   {open && (
    <div className="filter-menu" role="dialog" aria-label="Advanced filters">
     <div className="filter-menu-head">
      <strong>Advanced filters</strong>
      {resultLabel && <span className="filter-menu-count">{resultLabel}</span>}
      {/* Gated on the draft OR what's actually applied -- not just the draft. Removing every
          draft row (the row's own X) without hitting Apply leaves the table still filtered by
          whatever was last applied; hiding "Clear all" here stranded that state with no way
          back to unfiltered, since Apply is also disabled on an empty draft. */}
      {(filters.length > 0 || appliedCount > 0) && <button type="button" className="dataset-link-btn" onClick={onClearAll}>Clear all</button>}
     </div>
     {filters.length === 0 && (
      <p className="filter-menu-empty">
       {appliedCount > 0 ? 'All filter rows removed -- click "Clear all" to also clear the table filter.' : 'No filters applied to this table yet.'}
      </p>
     )}
     {filters.length > 0 && (
      <div className="filter-rows">
       {filters.map((row, index) => {
        const field = fields.find((item) => item.key === row.field) || fields[0];
        return (
         <div className="filter-row" key={row.id}>
          <span className="filter-row-prefix">{index === 0 ? 'Where' : 'and'}</span>
          <MenuSelect
           ariaLabel="Filter field"
           className="menu-select-field"
           value={field.key}
           options={fields.map((item) => ({ value: item.key, label: item.label, icon: FILTER_TYPE_ICON[item.type] }))}
           onChange={(value) => setRowField(row.id, value)}
          />
          <MenuSelect
           ariaLabel="Filter operator"
           className="menu-select-operator"
           value={row.operator}
           options={FILTER_OPERATORS[field.type]}
           onChange={(value) => setRowOperator(row.id, value, field)}
          />
          <FilterValueInput field={field} row={row} onChange={(value) => updateRow(row.id, { value })} />
          <button type="button" className="filter-row-remove" aria-label="Remove filter" onClick={() => removeRow(row.id)}><X size={14} /></button>
         </div>
        );
       })}
      </div>
     )}
     <div className="filter-menu-footer">
      <button type="button" className="dataset-link-btn filter-add-btn" onClick={addRow}><Plus size={13} /> New filter</button>
      <button type="button" className="button primary filter-apply-btn" disabled={!draftReady} onClick={onApply}>Apply</button>
     </div>
    </div>
   )}
  </div>
 );
}

// Shared close-on-outside-click/Escape for the board toolbar's popovers. `.menu-select-menu`
// is exempt for the same reason FilterBar exempts it: those menus portal to document.body,
// so a click on one is never a DOM descendant of the popover that opened it.
function useOutsideClose(open: boolean, close: () => void) {
 const ref = useRef<HTMLDivElement>(null);
 useEffect(() => {
  if (!open) return;
  const onDown = (event: MouseEvent) => {
   const target = event.target as HTMLElement;
   if (ref.current?.contains(target) || target.closest?.('.menu-select-menu')) return;
   close();
  };
  const onKey = (event: KeyboardEvent) => { if (event.key === 'Escape') close(); };
  document.addEventListener('mousedown', onDown);
  document.addEventListener('keydown', onKey);
  return () => {
   document.removeEventListener('mousedown', onDown);
   document.removeEventListener('keydown', onKey);
  };
 }, [open, close]);
 return ref;
}

// Monday's board search: an icon that expands into a field on click and collapses again when
// emptied and blurred, so the toolbar stays compact until you actually need to search.
function BoardSearch({ value, onChange }: { value: string; onChange: (value: string) => void }) {
 const [open, setOpen] = useState(false);
 const inputRef = useRef<HTMLInputElement>(null);
 useEffect(() => { if (open) inputRef.current?.focus(); }, [open]);
 const expanded = open || !!value;
 return (
  <div className={`board-search${expanded ? ' is-open' : ''}`}>
   <button type="button" className="board-search-icon" aria-label="Search rows" onClick={() => setOpen(true)}>
    <Search size={15} />
   </button>
   <input
    ref={inputRef}
    className="board-search-input"
    value={value}
    placeholder="Search this board"
    aria-label="Search rows"
    tabIndex={expanded ? 0 : -1}
    onChange={(event) => onChange(event.target.value)}
    onBlur={() => { if (!value) setOpen(false); }}
    onKeyDown={(event) => { if (event.key === 'Escape') { onChange(''); setOpen(false); } }}
   />
   {!!value && (
    <button type="button" className="board-search-clear" aria-label="Clear search" onClick={() => { onChange(''); setOpen(false); }}>
     <X size={13} />
    </button>
   )}
  </div>
 );
}

// Monday's "Sort" popover. Sorting is server-side (the board pages 50 rows at a time, so a
// client-side sort would only reorder the page you happen to be on, which is worse than none).
function BoardSortMenu({ columns, sortable, sort, onChange }: {
 columns: DatasetRowColumn[]; sortable: string[];
 sort: { field: string; direction: 'asc' | 'desc' } | null;
 onChange: (sort: { field: string; direction: 'asc' | 'desc' } | null) => void;
}) {
 const [open, setOpen] = useState(false);
 const ref = useOutsideClose(open, () => setOpen(false));
 const options = columns.filter((col) => sortable.includes(col.key));
 const active = sort ? options.find((col) => col.key === sort.field) : null;
 return (
  <div className={`board-popover${open ? ' open' : ''}`} ref={ref}>
   <button type="button" className={`selector board-tool-btn${sort ? ' is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
    <ArrowUpDown size={15} />
    <span>{active ? `Sort / ${active.label}` : 'Sort'}</span>
    <ChevronDown size={14} className="campaign-caret" />
   </button>
   {open && (
    <div className="board-menu" role="dialog" aria-label="Sort rows">
     <div className="board-menu-head">
      <strong>Sort by</strong>
      {sort && <button type="button" className="dataset-link-btn" onClick={() => { onChange(null); setOpen(false); }}>Clear</button>}
     </div>
     <div className="board-menu-list">
      {options.map((col) => {
       const isActive = sort?.field === col.key;
       return (
        <div key={col.key} className={`board-sort-row${isActive ? ' is-active' : ''}`}>
         <button type="button" className="board-sort-name" onClick={() => onChange({ field: col.key, direction: isActive ? sort!.direction : 'asc' })}>
          {isActive && <Check size={12} />}<span>{col.label}</span>
         </button>
         <div className="board-sort-dirs">
          <button
           type="button"
           className={isActive && sort!.direction === 'asc' ? 'is-on' : ''}
           aria-label={`Sort ${col.label} ascending`}
           onClick={() => onChange({ field: col.key, direction: 'asc' })}
          ><ArrowUp size={12} /></button>
          <button
           type="button"
           className={isActive && sort!.direction === 'desc' ? 'is-on' : ''}
           aria-label={`Sort ${col.label} descending`}
           onClick={() => onChange({ field: col.key, direction: 'desc' })}
          ><ArrowDown size={12} /></button>
         </div>
        </div>
       );
      })}
     </div>
    </div>
   )}
  </div>
 );
}

// Monday's "Hide" / column-manager popover.
function BoardColumnsMenu({ columns, hidden, onToggle, onShowAll }: {
 columns: DatasetRowColumn[]; hidden: string[];
 onToggle: (key: string) => void; onShowAll: () => void;
}) {
 const [open, setOpen] = useState(false);
 // A filter field, because these tables run to 20+ columns and scanning a flat switch
 // list to find one is the slow part.
 const [query, setQuery] = useState('');
 const ref = useOutsideClose(open, () => { setOpen(false); setQuery(''); });
 const needle = query.trim().toLowerCase();
 const shown = needle ? columns.filter((col) => col.label.toLowerCase().includes(needle)) : columns;
 return (
  <div className={`board-popover${open ? ' open' : ''}`} ref={ref}>
   <button type="button" className={`selector board-tool-btn${hidden.length ? ' is-active' : ''}`} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
    <Columns3 size={15} />
    <span>Columns{hidden.length ? ` / ${hidden.length} hidden` : ''}</span>
    <ChevronDown size={14} className="campaign-caret" />
   </button>
   {open && (
    <div className="board-menu board-menu-columns" role="dialog" aria-label="Show or hide columns">
     <div className="board-menu-head">
      <strong>Columns</strong>
      {!!hidden.length && <button type="button" className="dataset-link-btn" onClick={onShowAll}>Show all</button>}
     </div>
     <div className="board-menu-search">
      <Search size={13} aria-hidden="true" />
      <input
       type="text"
       value={query}
       autoFocus
       placeholder="Search columns"
       aria-label="Search columns"
       onChange={(event) => setQuery(event.target.value)}
      />
     </div>
     <div className="board-menu-list">
      {shown.map((col) => {
       const isVisible = !hidden.includes(col.key);
       // The last visible column can't be hidden -- an empty board has no header row to
       // bring anything back from.
       const isLast = isVisible && hidden.length === columns.length - 1;
       return (
        <button
         key={col.key}
         type="button"
         role="switch"
         aria-checked={isVisible}
         disabled={isLast}
         title={isLast ? 'At least one column has to stay visible' : undefined}
         className={`board-column-row${isVisible ? ' is-on' : ''}${isLast ? ' is-locked' : ''}`}
         onClick={() => onToggle(col.key)}
        >
         <i className="board-switch" aria-hidden="true" />
         <span>{col.label}</span>
         {isLast && <Lock size={11} className="board-column-lock" aria-hidden="true" />}
        </button>
       );
      })}
      {!shown.length && <p className="board-menu-empty">No column matches “{query}”.</p>}
     </div>
    </div>
   )}
  </div>
 );
}

const BOARD_DENSITIES = [
 { value: 'compact', label: 'Compact' },
 { value: 'default', label: 'Default' },
 { value: 'tall', label: 'Tall' },
] as const;
type BoardDensity = typeof BOARD_DENSITIES[number]['value'];

function BoardDensityMenu({ density, onChange }: { density: BoardDensity; onChange: (value: BoardDensity) => void }) {
 const [open, setOpen] = useState(false);
 const ref = useOutsideClose(open, () => setOpen(false));
 return (
  <div className={`board-popover${open ? ' open' : ''}`} ref={ref}>
   <button type="button" className="selector board-tool-btn board-tool-icon" aria-haspopup="dialog" aria-expanded={open} aria-label="Row height" title="Row height" onClick={() => setOpen((v) => !v)}>
    <Rows3 size={15} />
   </button>
   {open && (
    <div className="board-menu board-menu-narrow" role="dialog" aria-label="Row height">
     <div className="board-menu-head"><strong>Row height</strong></div>
     <div className="board-menu-list">
      {BOARD_DENSITIES.map((item) => (
       <button
        key={item.value}
        type="button"
        className={`board-column-row is-plain${density === item.value ? ' is-checked' : ''}`}
        onClick={() => { onChange(item.value); setOpen(false); }}
       >
        <span>{item.label}</span>
        {density === item.value && <Check size={13} />}
       </button>
      ))}
     </div>
    </div>
   )}
  </div>
 );
}

// Monday's board checkbox -- a real <input> underneath (keyboard + screen-reader behaviour
// for free) with the visual drawn by the sibling <i>.
function BoardCheckbox({ checked, indeterminate, onChange, label }: {
 checked: boolean; indeterminate?: boolean; onChange: () => void; label: string;
}) {
 const ref = useRef<HTMLInputElement>(null);
 useEffect(() => { if (ref.current) ref.current.indeterminate = !!indeterminate && !checked; }, [indeterminate, checked]);
 return (
  <label className="board-check">
   <input ref={ref} type="checkbox" checked={checked} aria-label={label} onChange={onChange} />
   <i aria-hidden="true">{indeterminate && !checked ? <span className="board-check-dash" /> : <Check size={11} strokeWidth={3.5} />}</i>
  </label>
 );
}

// A board header cell: click the label to cycle the sort, drag the right edge to resize.
// The drag listens on `window` (not the handle) so the pointer can leave the 5px hit area
// mid-drag -- which it always does -- without the column snapping back.
function BoardHeaderCell({ column, sortable, sortDirection, width, onSort, onResize }: {
 column: DatasetRowColumn; sortable: boolean; sortDirection: 'asc' | 'desc' | null;
 width: number; onSort: () => void; onResize: (width: number) => void;
}) {
 const [dragging, setDragging] = useState(false);

 const startResize = (event: ReactMouseEvent) => {
  event.preventDefault();
  event.stopPropagation();
  const startX = event.clientX;
  const startWidth = width;
  setDragging(true);
  const onMove = (move: MouseEvent) => onResize(Math.max(64, Math.round(startWidth + move.clientX - startX)));
  const onUp = () => {
   setDragging(false);
   window.removeEventListener('mousemove', onMove);
   window.removeEventListener('mouseup', onUp);
  };
  window.addEventListener('mousemove', onMove);
  window.addEventListener('mouseup', onUp);
 };

 return (
  <th
   className={`${column.align === 'num' ? 'num ' : ''}${sortDirection ? 'is-sorted' : ''}`}
   style={{ width }}
   aria-sort={sortDirection ? (sortDirection === 'asc' ? 'ascending' : 'descending') : 'none'}
  >
   <button
    type="button"
    className={`board-th-label${sortable ? '' : ' is-inert'}`}
    disabled={!sortable}
    title={sortable ? `Sort by ${column.label}` : `${column.label} is derived after the query, so it can't be sorted`}
    onClick={onSort}
   >
    <span>{column.label}</span>
    {sortable && (
     sortDirection === 'asc' ? <ArrowUp size={11} className="board-th-arrow is-on" />
     : sortDirection === 'desc' ? <ArrowDown size={11} className="board-th-arrow is-on" />
     : <ArrowUpDown size={11} className="board-th-arrow" />
    )}
   </button>
   <span
    role="separator"
    aria-orientation="vertical"
    aria-label={`Resize ${column.label}`}
    className={`board-th-resize${dragging ? ' is-dragging' : ''}`}
    onMouseDown={startResize}
    onDoubleClick={() => onResize(column.width ?? 140)}
   />
  </th>
 );
}

// A true diverging cold (negative) -> neutral -> hot (positive) scale, via the
// correlation-only --corr-cold/--corr-hot tokens, rather than reusing --yellow-strong/
// --danger's warm-positive/alert-negative meaning from the rest of the app. Mixing
// against --surface (not transparent) keeps near-zero cells legible on both themes,
// and the fixed-strength text color at higher magnitude keeps contrast readable
// instead of degrading to washed-out grey the closer a color gets to full saturation.
const correlationCellStyle = (value: number): CSSProperties => {
 const magnitude = Math.min(1, Math.abs(value));
 const token = value >= 0 ? '--corr-hot' : '--corr-cold';
 return {
  // Ramps to full saturation, not 90%, so the extremes read as extremes — the washed
  // ceiling was why the old matrix looked flat next to a matplotlib one.
  background: `color-mix(in srgb, var(${token}) ${Math.round(magnitude * 100)}%, var(--corr-zero))`,
  // Flip to light ink once the cell is dark enough to swallow body text. Fixed value,
  // not a token: it has to contrast with the CELL, which is the same colour in both themes.
  color: magnitude > 0.45 ? '#FFFFFF' : undefined,
  fontWeight: magnitude > 0.45 ? 600 : undefined,
 };
};

function DatasetPage() {
 const [correlation, setCorrelation] = useState<any>(null);
 const [ols, setOls] = useState<any>(null);
 const [error, setError] = useState('');

 const [hoverIdx, setHoverIdx] = useState(-1);
 const [declaredHoverIdx, setDeclaredHoverIdx] = useState(-1);
 const [correlationView, setCorrelationView] = useState<'declared' | 'expanded'>('declared');

 const [rowsTable, setRowsTable] = useState<'leads' | 'ad_performance' | 'ad_performance_export'>('leads');
 // Only what the server is authoritative about. The page position is client-owned state
 // (`rowsOffset` below) and is deliberately NOT read back off the response: the endpoint
 // echoes the offset it was given, so letting a late reply write it back re-applied a stale
 // page and kicked off another fetch -- the flicker loop described at the fetch effect.
 const [rowsData, setRowsData] = useState<{ rows: any[]; total: number; limit: number }>({ rows: [], total: 0, limit: 50 });
 const [rowsOffset, setRowsOffset] = useState(0);
 const [rowsBusy, setRowsBusy] = useState(false);
 // --- Board state (Monday-style raw-data board) -------------------------------------------
 // Sort and search are server-side: the board pages 50 rows at a time, so sorting or
 // searching only the loaded page would silently answer a different question than the one
 // the user asked ("the largest spend" vs "the largest spend on page 3").
 const [rowSort, setRowSort] = useState<{ field: string; direction: 'asc' | 'desc' } | null>(null);
 const [searchDraft, setSearchDraft] = useState('');
 const [rowSearch, setRowSearch] = useState('');
 const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
 // Per-column pixel widths from the header drag handles, keyed `${table}:${column}` so each
 // tab keeps its own layout (they share almost no columns).
 const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
 const [density, setDensity] = useState<BoardDensity>('default');
 const [selectedRowIds, setSelectedRowIds] = useState<string[]>([]);
 // True once the user has expanded a page-only selection to every row matching the current
 // filter/search/scope -- distinguishes "selected exactly this page" from "selected everything
 // that happens to total the page size", so the banner doesn't re-offer an already-taken action.
 const [selectedAllMatching, setSelectedAllMatching] = useState(false);
 const [selectAllMatchingBusy, setSelectAllMatchingBusy] = useState(false);
 const [boardBusy, setBoardBusy] = useState(false);
 const [boardError, setBoardError] = useState('');

 // Debounced so typing doesn't fire a request per keystroke.
 useEffect(() => {
  const timer = window.setTimeout(() => setRowSearch(searchDraft.trim()), 350);
  return () => window.clearTimeout(timer);
 }, [searchDraft]);
 // Advanced filters for the "Raw data" table (see FilterBar above). Field keys are specific
 // to one table's column set, so switching tabs clears them rather than carrying over a filter
 // that no longer means anything (or references a field the new table doesn't have).
 //
 // `rowFilters` is the draft the popover edits live; `appliedRowFilters` is what the table
 // actually fetches by. They're separate so editing a row (or adding/removing one) doesn't
 // hit the API until "Apply" is clicked -- per feedback, filters shouldn't fire on every
 // keystroke/selection. "Clear all" is the one exception: it applies immediately, since
 // there's nothing to preview before committing to "no filter".
 const [rowFilters, setRowFilters] = useState<FilterRow[]>([]);
 const [appliedRowFilters, setAppliedRowFilters] = useState<FilterRow[]>([]);
 const completeRowFilters = useMemo(() => appliedRowFilters.filter(isFilterRowComplete), [appliedRowFilters]);
 // A quick date-range scope, separate from the advanced FilterBar above -- it doesn't make
 // the user pick "which column" first. Applies immediately (no draft/Apply split) since it's
 // one popover-local decision, not a multi-row form. Table-specific ("day" vs "created_at")
 // so it rides shotgun with the rest of the filter pipeline as one more {field, operator,
 // value} row rather than a bespoke query param the backend would need to learn about.
 const [rowDateRange, setRowDateRange] = useState<{ from: string; to: string } | null>(null);
 const rowDateField = rowsTable === 'leads' ? 'created_at' : 'day';
 const allRowFilters = useMemo(() => (
  rowDateRange ? [...completeRowFilters, { id: -1, field: rowDateField, operator: 'between', value: rowDateRange }] : completeRowFilters
 ), [completeRowFilters, rowDateRange, rowDateField]);
 // Only the completed filters' serialized shape should trigger a refetch -- typing into a
 // half-filled filter row (or picking a field before a value exists) must not fire a request.
 const rowFiltersKey = useMemo(
  () => JSON.stringify(allRowFilters.map((row) => ({ field: row.field, operator: row.operator, value: row.value }))),
  [allRowFilters],
 );

 // Scope filter: which ad set or campaign the correlation/importance/raw-row sections are
 // narrowed to. An ad set wins over a campaign if both happen to be set, same convention as
 // /api/ols-summary. Portfolio-wide inventory counts (above) stay unscoped by design -- see
 // Vault/Features/Dataset-Page.md.
 const [campaigns, setCampaigns] = useState<any[]>([]);
 const [selectedCampaignId, setSelectedCampaignId] = useState('');
 const [selectedAdSetId, setSelectedAdSetId] = useState('');
 const [adSetQuery, setAdSetQuery] = useState('');
 const [adSetLookupError, setAdSetLookupError] = useState('');
 const [campaignPickerOpen, setCampaignPickerOpen] = useState(false);
 const campaignPickerRef = useRef<HTMLDivElement>(null);

 useEffect(() => {
  api('/dashboard/insights').then((data) => setCampaigns(data.campaigns || [])).catch(() => {});
 }, []);

 useEffect(() => {
  const closeOnOutsideClick = (event: MouseEvent) => {
   if (!campaignPickerRef.current?.contains(event.target as Node)) setCampaignPickerOpen(false);
  };
  document.addEventListener('mousedown', closeOnOutsideClick);
  return () => document.removeEventListener('mousedown', closeOnOutsideClick);
 }, []);

 const selectedCampaignName = campaigns.find((item: any) => String(item.campaign_id) === String(selectedCampaignId))?.campaign || '';

 const applyAdSetLookup = async () => {
  const term = adSetQuery.trim();
  if (!term) { setSelectedAdSetId(''); setAdSetLookupError(''); return; }
  setAdSetLookupError('');
  try {
   const matches = await api(`/ad-sets?q=${encodeURIComponent(term)}`);
   const exact = (matches || []).find((item: any) => String(item.utm_ad_set_id).toLowerCase() === term.toLowerCase());
   if (!exact) { setAdSetLookupError('No exact Ad Set ID was found. Check the ID and try again.'); return; }
   setSelectedAdSetId(String(exact.utm_ad_set_id));
   if (exact.utm_campaign_id) setSelectedCampaignId(String(exact.utm_campaign_id));
  } catch (err: any) {
   setAdSetLookupError(err.message || 'Ad set lookup failed.');
  }
 };

 const clearScope = () => {
  setSelectedCampaignId(''); setSelectedAdSetId(''); setAdSetQuery(''); setAdSetLookupError('');
 };

 const scopeParams = selectedAdSetId
  ? `ad_set_id=${encodeURIComponent(selectedAdSetId)}`
  : selectedCampaignId
  ? `campaign_id=${encodeURIComponent(selectedCampaignId)}`
  : '';

 // Bumped by ChangeEventButton's onChange -- recording or deleting a change/start date
 // changes what the correlation matrix and OLS fit compute, but neither endpoint call
 // above is otherwise triggered by that popover's own state, so without this the page
 // keeps showing pre-edit numbers until the scope filter happens to change too.
 const [dataRefreshKey, setDataRefreshKey] = useState(0);

 // Everything this page renders (correlation, OLS, raw rows) is computed live from the
 // recorded data, so `dataRefreshKey` alone already brings it current on save. The watcher
 // is here only to surface that the background retrain is still running -- no second refetch.
 const { retraining, watchRetrain } = useRetrainWatcher();

 useEffect(() => {
  const suffix = scopeParams ? `?${scopeParams}` : '';
  Promise.all([api(`/dataset/correlation${suffix}`), api(`/ols-summary${suffix}`)])
   .then(([correlationData, olsData]) => { setCorrelation(correlationData); setOls(olsData); })
   .catch((err) => setError(err.message || 'Failed to load dataset diagnostics'));
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [scopeParams, dataRefreshKey]);

 // Everything that defines *which rows* the board is showing, apart from which page of them.
 // Any change to this must send the pager back to page 1 -- a narrower filter should never
 // leave it stranded past the new end of the table.
 const rowsQueryKey = [
  rowsTable, scopeParams, rowFiltersKey, rowSort?.field ?? '', rowSort?.direction ?? '', rowSearch,
 ].join(' ');
 const lastRowsQueryKey = useRef(rowsQueryKey);
 // Adjusted during render, not in an effect. This is React's documented "adjust state when
 // props change" escape hatch: the re-render happens before any effect runs, so the fetch
 // effect below only ever sees the corrected offset.
 //
 // It used to be two follow-up effects, and that was the flicker: applying a filter while on
 // page 5 fired the fetch effect FIRST (still at offset 200, so the server returned an empty
 // page for a now-shorter result set -- the blank flash), then the reset effect set offset 0
 // and fired a SECOND fetch. With no ordering guard the two replies could land in either
 // order, and because the response also carried `offset`, a late offset-200 reply put the
 // pager back on page 5 and triggered yet another fetch. That loop is what was visibly
 // flickering and jumping the scroll position as the table's height changed under it.
 if (lastRowsQueryKey.current !== rowsQueryKey) {
  lastRowsQueryKey.current = rowsQueryKey;
  if (rowsOffset !== 0) setRowsOffset(0);
 }

 // Monotonic request id: only the newest in-flight request may write to state, so an earlier
 // reply that arrives late is dropped instead of overwriting newer rows.
 const rowsRequestId = useRef(0);

 useEffect(() => {
  setRowsBusy(true);
  const scoped = scopeParams ? `&${scopeParams}` : '';
  const filterQuery = allRowFilters.length
   ? `&filters=${encodeURIComponent(JSON.stringify(allRowFilters.map((row) => ({ field: row.field, operator: row.operator, value: row.value }))))}`
   : '';
  const sortQuery = rowSort ? `&sort=${encodeURIComponent(rowSort.field)}&direction=${rowSort.direction}` : '';
  const searchQuery = rowSearch ? `&search=${encodeURIComponent(rowSearch)}` : '';
  const requestId = ++rowsRequestId.current;
  api(`/dataset/rows?table=${rowsTable}&offset=${rowsOffset}&limit=${rowsData.limit}${scoped}${filterQuery}${sortQuery}${searchQuery}`)
   .then((data) => {
    if (requestId !== rowsRequestId.current) return;
    // `offset` from the response is intentionally discarded -- see the state declaration.
    setRowsData({ rows: data.rows, total: data.total, limit: data.limit });
   })
   .catch((err) => { if (requestId === rowsRequestId.current) setError(err.message || 'Failed to load rows'); })
   .finally(() => { if (requestId === rowsRequestId.current) setRowsBusy(false); });
  // `dataRefreshKey` for the same reason the correlation/OLS effect above needs it: the raw
  // table's declared-variable columns are derived from recorded changes, so a popover save
  // changes them without touching the scope or page. `rowsQueryKey` folds in table/scope/
  // filters/sort/search, so the effect re-fires on their actual shape rather than on every
  // render that produces a new-but-equal array.
  // eslint-disable-next-line react-hooks/exhaustive-deps
 }, [rowsQueryKey, rowsOffset, dataRefreshKey]);

 // --- Board behaviour ----------------------------------------------------------------------
 const boardColumns = DATASET_ROW_COLUMNS[rowsTable];
 const visibleColumns = boardColumns.filter((col) => !hiddenColumns.includes(col.key));
 const sortableKeys = DATASET_SORT_FIELDS[rowsTable];
 // Every tab is writable. Leads go through /api/leads/{id}; both ad-performance tabs are two
 // views over the same daily_ad_performance rows, so they share one endpoint pair. Which
 // individual cells accept an edit is decided per column by `edit` in DATASET_ROW_COLUMNS --
 // a few columns are joined, computed, or placeholder-only and stay read-only there.
 const boardRowEndpoint = (id: string) => (rowsTable === 'leads' ? `/leads/${id}` : `/dataset/ad-performance/${id}`);
 const pageRowIds: string[] = rowsData.rows.map((row: any) => String(row.id));
 const selectedOnPage = pageRowIds.filter((id) => selectedRowIds.includes(id));
 const allPageSelected = pageRowIds.length > 0 && selectedOnPage.length === pageRowIds.length;

 // Selection is by row id, and ids don't survive a table switch -- clear rather than carry a
 // selection that points at rows the board is no longer showing.
 const switchTable = (table: 'leads' | 'ad_performance' | 'ad_performance_export') => {
  setRowsTable(table);
  // No explicit offset reset needed -- `table` is part of `rowsQueryKey`, so the render-phase
  // adjustment above already sends the pager back to page 1.
  setRowFilters([]); setAppliedRowFilters([]);
  setRowSort(null); setHiddenColumns([]); setSelectedRowIds([]); setSelectedAllMatching(false);
  setSearchDraft(''); setRowSearch(''); setBoardError('');
 };

 // Click-to-sort on a header cycles asc -> desc -> unsorted, the same three-state cycle a
 // spreadsheet gives you, so a mis-click is always one more click from undone.
 const cycleSort = (field: string) => {
  if (!sortableKeys.includes(field)) return;
  setRowSort((current) => {
   if (current?.field !== field) return { field, direction: 'asc' };
   if (current.direction === 'asc') return { field, direction: 'desc' };
   return null;
  });
 };

 const toggleRowSelected = (id: string) => {
  setSelectedAllMatching(false);
  setSelectedRowIds((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
 };
 const toggleSelectAllOnPage = () => {
  setSelectedAllMatching(false);
  setSelectedRowIds((current) => (
   allPageSelected ? current.filter((id) => !pageRowIds.includes(id)) : Array.from(new Set([...current, ...pageRowIds]))
  ));
 };
 // "Select all N matching rows" -- expands a page-only selection to every row the current
 // filter/search/scope matches, via /dataset/row-ids (unbounded, unlike /dataset/rows' 500 cap).
 const selectAllMatchingRows = async () => {
  setSelectAllMatchingBusy(true);
  setBoardError('');
  try {
   const scoped = scopeParams ? `&${scopeParams}` : '';
   const filterQuery = allRowFilters.length
    ? `&filters=${encodeURIComponent(JSON.stringify(allRowFilters.map((row) => ({ field: row.field, operator: row.operator, value: row.value }))))}`
    : '';
   const searchQuery = rowSearch ? `&search=${encodeURIComponent(rowSearch)}` : '';
   const result = await api(`/dataset/row-ids?table=${rowsTable}${scoped}${filterQuery}${searchQuery}`);
   setSelectedRowIds(result.ids || []);
   setSelectedAllMatching(true);
   if (result.capped) {
    setBoardError(`Only the first ${fmt((result.ids || []).length)} of ${fmt(result.total)} matching rows were selected -- narrow the filter to select the rest.`);
   }
  } catch (err: any) {
   setBoardError(err.message || 'Failed to select all matching rows.');
  } finally {
   setSelectAllMatchingBusy(false);
  }
 };

 const setColumnWidth = (key: string, width: number) => {
  setColumnWidths((current) => ({ ...current, [`${rowsTable}:${key}`]: width }));
 };
 const columnWidthOf = (col: DatasetRowColumn) => columnWidths[`${rowsTable}:${col.key}`] ?? col.width ?? 140;
 // The board sizes to the sum of its columns and scrolls, rather than being squeezed into the
 // container: with `table-layout: fixed`, a `width: 100%` table redistributes any shortfall or
 // overflow across the columns proportionally, which silently undoes every resize drag. The
 // CSS `min-width: 100%` still lets a narrow column set fill the panel.
 const boardTableWidth = 44 + visibleColumns.reduce((total, col) => total + columnWidthOf(col), 0);

 // Same optimistic single-field PATCH the forecast page's lead drilldown uses, so editing a
 // row means the same thing everywhere. The value's shape is decided by the column's own
 // `edit` type rather than by field name, so a new editable column needs no change here.
 //
 // Deliberately does NOT set `boardBusy`. Both endpoints now write the row and return, leaving
 // rebuild_aggregates()/train_models() (~31s together) to the background retrain guard, so the
 // request is a few milliseconds -- and gating the board on it meant `.board-scroll.is-busy`
 // (opacity + `pointer-events: none`) blanked the entire table on every committed cell. The
 // optimistic update already shows the new value immediately; `boardBusy` is kept for bulk
 // delete, which really does need to lock the board while it walks N rows.
 const commitBoardField = async (row: any, column: DatasetRowColumn, rawValue: string) => {
  let value: any = rawValue;
  if (column.edit === 'number') value = String(rawValue).trim() === '' ? null : Number(rawValue);
  else if (column.edit === 'datetime-local') value = rawValue ? `${rawValue}:00` : '';
  // Roll back just this one cell rather than restoring a whole-page snapshot. Without the
  // busy lock, two edits can now be in flight at once, and a snapshot rollback would also
  // revert whatever the *other* one had already applied.
  const previousValue = row[column.key];
  setRowsData((current) => ({ ...current, rows: current.rows.map((item: any) => (item.id === row.id ? { ...item, [column.key]: value } : item)) }));
  setBoardError('');
  try {
   await api(boardRowEndpoint(String(row.id)), {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ [column.key]: value }),
   });
   watchRetrain();
  } catch (err: any) {
   setRowsData((current) => ({ ...current, rows: current.rows.map((item: any) => (item.id === row.id ? { ...item, [column.key]: previousValue } : item)) }));
   setBoardError(err.message || 'Unable to update this row.');
  }
 };

 const deleteSelectedRows = async () => {
  if (!selectedRowIds.length) return;
  const noun = rowsTable === 'leads' ? 'lead' : 'ad performance row';
  if (!confirm(`Delete ${selectedRowIds.length} ${noun}${selectedRowIds.length === 1 ? '' : 's'}? This updates the totals the forecast is built on.`)) return;
  setBoardBusy(true);
  setBoardError('');
  try {
   // Sequential rather than Promise.all: a burst of concurrent writes against SQLite trades a
   // tidy loop for lock contention. Each request is now just the row delete -- the aggregate
   // rebuild and retrain they all share collapses into one background pass behind the guard.
   for (const id of selectedRowIds) await api(boardRowEndpoint(id), { method: 'DELETE' });
   setSelectedRowIds([]);
   setSelectedAllMatching(false);
   setDataRefreshKey((key) => key + 1);
   watchRetrain();
  } catch (err: any) {
   setBoardError(err.message || 'Unable to delete the selected rows.');
  } finally {
   setBoardBusy(false);
  }
 };

 // Exports what's on screen: the visible columns, in their current order, with each column's
 // own renderer applied -- so the file reads the way the board does, not the way the DB does.
 // `rowsData.rows` only ever holds the current page, so a "select all N matching" selection
 // (which can span far more rows than one page) is re-fetched in full here rather than
 // silently exported as whatever page happened to be on screen when the button was clicked.
 const exportSelectedRows = async () => {
  let chosen: any[];
  if (selectedAllMatching && selectedRowIds.length) {
   setSelectAllMatchingBusy(true);
   setBoardError('');
   try {
    const scoped = scopeParams ? `&${scopeParams}` : '';
    const filterQuery = allRowFilters.length
     ? `&filters=${encodeURIComponent(JSON.stringify(allRowFilters.map((row) => ({ field: row.field, operator: row.operator, value: row.value }))))}`
     : '';
    const sortQuery = rowSort ? `&sort=${encodeURIComponent(rowSort.field)}&direction=${rowSort.direction}` : '';
    const searchQuery = rowSearch ? `&search=${encodeURIComponent(rowSearch)}` : '';
    const pages: any[][] = [];
    let offset = 0;
    const pageSize = 500;
    // Bounded by the same cap "select all matching" used to build this selection in the
    // first place -- can't export more rows than were ever actually selected.
    while (offset < selectedRowIds.length) {
     const page = await api(`/dataset/rows?table=${rowsTable}&offset=${offset}&limit=${pageSize}${scoped}${filterQuery}${sortQuery}${searchQuery}`);
     pages.push(page.rows || []);
     if (!page.rows?.length) break;
     offset += pageSize;
    }
    chosen = pages.flat();
   } catch (err: any) {
    setBoardError(err.message || 'Failed to export all matching rows.');
    return;
   } finally {
    setSelectAllMatchingBusy(false);
   }
  } else {
   chosen = selectedRowIds.length
    ? rowsData.rows.filter((row: any) => selectedRowIds.includes(String(row.id)))
    : rowsData.rows;
  }
  if (!chosen.length) return;
  const escape = (value: any) => {
   const text = value == null ? '' : String(value);
   return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  };
  const lines = [
   visibleColumns.map((col) => escape(col.label)).join(','),
   ...chosen.map((row: any) => visibleColumns.map((col) => escape(col.render ? col.render(row) : row[col.key] ?? '')).join(',')),
  ];
  // Leading BOM so Excel opens the Khmer customer names as UTF-8 rather than mojibake.
  // Built with fromCharCode rather than an inline U+FEFF, which is invisible in the source
  // and easy to delete by accident. Note when testing: Blob.text() strips a leading BOM per
  // the UTF-8 decode spec, so verifying it needs arrayBuffer(), not text().
  const bom = String.fromCharCode(0xFEFF);
  const blob = new Blob([`${bom}${lines.join('\r\n')}`], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = `leadlens-${rowsTable}-${isoDate(new Date())}.csv`;
  link.click();
  URL.revokeObjectURL(url);
 };

 const declaredVariables: any[] = ols?.declared_variables || [];

 // Collapses the feature-level matrix (some declared variables expand into several dummy
 // columns -- e.g. Holiday_Proximity, Days of the week) down to one row/column per declared
 // variable, so it reads as "the 10 things", not "the ~22 columns they expand into". A
 // variable-pair's collapsed value is its strongest sub-feature relationship (max |r|, sign
 // kept) rather than an average, since an average would wash out a single dominant pairing --
 // e.g. holiday proximity vs day of week is dominated by one sub-feature pair, not
 // a blend of all of them. Derived entirely client-side from the existing matrix -- no
 // backend change needed.
 const declaredCorrelation = (() => {
  if (!correlation?.variables?.length) return null;
  const groups = new Map<number, { number: number; name: string; indices: number[] }>();
  correlation.variables.forEach((v: any, idx: number) => {
   const group = groups.get(v.variable_number) || { number: v.variable_number, name: v.variable_name, indices: [] as number[] };
   group.indices.push(idx);
   groups.set(v.variable_number, group);
  });
  const ordered = Array.from(groups.values()).sort((a, b) => a.number - b.number);
  const matrix = ordered.map((rowGroup) => ordered.map((colGroup) => {
   if (rowGroup.number === colGroup.number) return 1;
   let strongest = 0;
   for (const ri of rowGroup.indices) {
    for (const ci of colGroup.indices) {
     const value = correlation.matrix[ri][ci];
     if (Math.abs(value) > Math.abs(strongest)) strongest = value;
    }
   }
   return Math.round(strongest * 100) / 100;
  }));
  return { variables: ordered, matrix };
 })();

 // A declared variable that's constant (backend status "flat") drops out of the matrix above
 // with no trace -- correct (a zero-variance column has no defined correlation), but silent.
 // Only surface the "flat_recorded" status (backend `_declared_variable_coverage`): something
 // WAS recorded for this variable and it's still constant (e.g. a change recorded on every
 // observed day), which is the non-obvious, actionable case. Plain "flat" -- nothing recorded
 // for this variable at all yet -- is the expected default and not worth a callout; per
 // feedback, showing it read as noise ("I already know it's empty").
 const missingDeclaredVariables = declaredVariables.filter(
  (v) => v.status === 'flat_recorded' && !declaredCorrelation?.variables?.some((present) => present.number === v.number)
 );

 const rowStart = rowsData.total ? rowsOffset + 1 : 0;
 const rowEnd = Math.min(rowsOffset + rowsData.limit, rowsData.total);

 return (
  <div className="page-content dataset-page">
   <section className="dataset-heading">
    <div>
     <span>Dataset</span>
     <h2>What's actually feeding the forecast</h2>
    </div>
   </section>

   {error && <div className="error-banner">{error}</div>}

   <section className="dataset-section">
    <OlsResultCards ols={ols} coefficients={false} className="dataset-ols" emptyCopy="Upload ad performance data with spend before OLS regression results are available." />
   </section>

   <section className="dataset-section dataset-scope-section" aria-label="Filter by campaign or ad set">
    <div className="dataset-scope-bar">
     <div className={`campaign-picker${campaignPickerOpen ? ' open' : ''}`} ref={campaignPickerRef}>
      <button
       type="button"
       className="selector campaign-selector"
       aria-haspopup="listbox"
       aria-expanded={campaignPickerOpen}
       onClick={() => setCampaignPickerOpen((open) => !open)}
      >
       <Megaphone size={16} />
       <span>Campaign</span>
       <strong title={selectedCampaignName || undefined}>{selectedCampaignId ? (selectedCampaignName || selectedCampaignId) : 'All campaigns'}</strong>
       <ChevronDown size={15} className="campaign-caret" />
      </button>
      {campaignPickerOpen && (
       <div className="campaign-menu" role="listbox" aria-label="Campaigns">
        <button
         type="button"
         role="option"
         aria-selected={!selectedCampaignId}
         className={`campaign-option${!selectedCampaignId ? ' active' : ''}`}
         onClick={() => { setSelectedCampaignId(''); setSelectedAdSetId(''); setAdSetQuery(''); setAdSetLookupError(''); setCampaignPickerOpen(false); }}
        >
         <span>All campaigns</span>
         <small>Portfolio-wide</small>
        </button>
        {campaigns.map((campaign: any) => {
         const isActive = String(campaign.campaign_id) === String(selectedCampaignId);
         return (
          <button
           type="button"
           key={campaign.campaign_id}
           role="option"
           aria-selected={isActive}
           className={`campaign-option${isActive ? ' active' : ''}`}
           onClick={() => { setSelectedCampaignId(String(campaign.campaign_id)); setSelectedAdSetId(''); setAdSetQuery(''); setAdSetLookupError(''); setCampaignPickerOpen(false); }}
          >
           <span title={campaign.campaign}>{campaign.campaign}</span>
           <small>{fmt(campaign.leads)} leads</small>
          </button>
         );
        })}
       </div>
      )}
     </div>
     <form
      className="lookup-area"
      onSubmit={(event) => { event.preventDefault(); applyAdSetLookup(); }}
     >
      <div className={`selector adset-selector${adSetLookupError ? ' invalid' : ''}`}>
       <Search size={17} />
       <label className="lookup-input-label" htmlFor="dataset-adset-search">Ad Set ID</label>
       <input
        id="dataset-adset-search"
        value={adSetQuery}
        onChange={(event) => { setAdSetQuery(event.target.value); setAdSetLookupError(''); }}
        placeholder="Enter or paste an Ad Set ID..."
        autoComplete="off"
        inputMode="numeric"
       />
       {(adSetQuery || selectedAdSetId) && (
        <button type="button" className="clear-search" aria-label="Clear Ad Set ID" onClick={clearScope}><X size={15} /></button>
       )}
      </div>
      {adSetLookupError && <small className="dataset-scope-error">{adSetLookupError}</small>}
     </form>
     <ChangeEventButton adSetId={selectedAdSetId} retraining={retraining} onChange={() => { setDataRefreshKey((key) => key + 1); watchRetrain(); }} />
    </div>
   </section>

   <section className="dataset-section">
    <div className="dataset-section-head">
     <div>
      <span>Correlation</span>
      <h3>{correlationView === 'declared' ? 'How the eight declared variables move together' : 'How the underlying feature columns move together'}</h3>
     </div>
     <div className="dataset-correlation-head-controls">
      <div className="dataset-tabs">
       <button className={correlationView === 'declared' ? 'is-active' : ''} onClick={() => setCorrelationView('declared')}>Declared</button>
       <button className={correlationView === 'expanded' ? 'is-active' : ''} onClick={() => setCorrelationView('expanded')}>Expanded</button>
      </div>
      {((correlationView === 'declared' && !!declaredCorrelation) || (correlationView === 'expanded' && !!correlation?.variables?.length)) && (
       /* A proper colourbar rather than a two-segment strip: a continuous ramp with
          labelled ticks at every quarter, so a cell's shade can actually be read back
          to a number instead of only "bluer" or "redder". */
       <div className="dataset-correlation-legend" aria-hidden="true">
        <div className="dataset-correlation-ramp" />
        <div className="dataset-correlation-ticks">
         {[-1, -0.5, 0, 0.5, 1].map((tick) => (
          <span key={tick}>{tick > 0 ? `+${tick.toFixed(2)}` : tick.toFixed(2)}</span>
         ))}
        </div>
       </div>
      )}
     </div>
    </div>
    {correlationView === 'declared' ? (
     !declaredCorrelation ? (
      <div className="table-empty">Not enough data yet for a correlation matrix.</div>
     ) : (
      <div className="dataset-correlation-scroll">
       <table className="dataset-correlation-table">
        <thead>
         <tr>
          <th />
          {declaredCorrelation.variables.map((v, ci: number) => (
           // Explicit calc() width, same reasoning as the expanded matrix's own columns just
           // below: the row-header column is a fixed 120px (see .dataset-correlation-table
           // CSS), so each data column gets an even share of what's left, however many
           // declared variables are actually present (5 here, up to 10 with everything
           // recorded) -- a bare 74px-per-column floor (the old default) doesn't shrink
           // below itself, so 5 short columns left the row-header looking like it was
           // eating the section while the other 4/5 of the table sat empty to the right.
           <th key={v.number} className={ci === declaredHoverIdx ? 'is-hover' : ''} onMouseEnter={() => setDeclaredHoverIdx(ci)} onMouseLeave={() => setDeclaredHoverIdx(-1)} title={`#${v.number} ${v.name}`} style={{ width: `calc((100% - 120px) / ${declaredCorrelation.variables.length})`, minWidth: 0, maxWidth: 'none' }}>{DATASET_DECLARED_SHORT_LABEL[v.number] || v.name}</th>
          ))}
         </tr>
        </thead>
        <tbody>
         {declaredCorrelation.variables.map((rowVar, ri: number) => (
          <tr key={rowVar.number}>
           <th className={ri === declaredHoverIdx ? 'is-hover' : ''} onMouseEnter={() => setDeclaredHoverIdx(ri)} onMouseLeave={() => setDeclaredHoverIdx(-1)} title={`#${rowVar.number} ${rowVar.name}`}>{DATASET_DECLARED_SHORT_LABEL[rowVar.number] || rowVar.name}</th>
           {declaredCorrelation.variables.map((colVar, ci: number) => {
            const value = declaredCorrelation.matrix[ri][ci];
            const rowLabel = DATASET_DECLARED_SHORT_LABEL[rowVar.number] || rowVar.name;
            const colLabel = DATASET_DECLARED_SHORT_LABEL[colVar.number] || colVar.name;
            return (
             <td key={colVar.number} className={ri === declaredHoverIdx || ci === declaredHoverIdx ? 'is-hover' : ''} style={correlationCellStyle(value)} title={`${rowLabel} vs ${colLabel}: ${value.toFixed(2)}`}>{value.toFixed(2)}</td>
            );
           })}
          </tr>
         ))}
        </tbody>
       </table>
      </div>
     )
    ) : null}
    {correlationView === 'declared' && !!missingDeclaredVariables.length && (
     <div className="dataset-correlation-missing" aria-label="Declared variables not in the matrix">
      <span className="dataset-correlation-missing-head"><Info size={13} /> Not shown above ({missingDeclaredVariables.length} of the ten):</span>
      {missingDeclaredVariables.map((v) => (
       <div className="dataset-correlation-missing-row" key={v.number}>
        <b>#{v.number} {DATASET_DECLARED_SHORT_LABEL[v.number] || v.name}</b>
        <span>{v.detail}</span>
       </div>
      ))}
     </div>
    )}
    {correlationView === 'expanded' && (
     !correlation?.variables?.length ? (
      <div className="table-empty">Not enough data yet for a correlation matrix.</div>
     ) : (
      <div className="dataset-correlation-scroll">
       <table className="dataset-correlation-table is-dense">
        <thead>
         <tr>
          <th />
          {correlation.variables.map((v: any, ci: number) => (
           // Explicit calc() width, not `auto`: a CSS `transform` is paint-only and never
           // shrinks a cell's layout box, so an "auto"-width column under table-layout:fixed
           // sized itself off each label's un-rotated text width (e.g. "during holiday" wider
           // than "spent") instead of dividing evenly -- the matrix kept overflowing. An
           // explicit width computed from the live column count divides the space actually
           // left after the fixed row-header column evenly, however many columns there are.
           <th key={v.key} className={ci === hoverIdx ? 'is-hover' : ''} onMouseEnter={() => setHoverIdx(ci)} onMouseLeave={() => setHoverIdx(-1)} title={`${v.variable_name}: ${v.label}`} style={{ width: `calc((100% - 130px) / ${correlation.variables.length})` }}><span>{v.label}</span></th>
          ))}
         </tr>
        </thead>
        <tbody>
         {correlation.variables.map((rowVar: any, ri: number) => (
          <tr key={rowVar.key}>
           <th className={ri === hoverIdx ? 'is-hover' : ''} onMouseEnter={() => setHoverIdx(ri)} onMouseLeave={() => setHoverIdx(-1)} title={`${rowVar.variable_name}: ${rowVar.label}`}>{rowVar.label}</th>
           {correlation.variables.map((colVar: any, ci: number) => {
            const value = correlation.matrix[ri][ci];
            return (
             <td key={colVar.key} className={ri === hoverIdx || ci === hoverIdx ? 'is-hover' : ''} style={correlationCellStyle(value)} title={`${rowVar.label} vs ${colVar.label}: ${value.toFixed(2)}`}>{value.toFixed(2)}</td>
            );
           })}
          </tr>
         ))}
        </tbody>
       </table>
      </div>
     )
    )}
   </section>

   <section className="dataset-section">
    <div className="dataset-section-head"><div><span>Calculation</span><h3>How the correlation matrix is calculated</h3></div></div>
    {!declaredCorrelation ? (
     <div className="table-empty">Not enough data yet to explain — no matrix above to describe.</div>
    ) : (
     <div className="dataset-formula-card">
      <div className="dataset-formula">
       <span className="dataset-formula-symbol">r</span>
       <span className="dataset-formula-eq">=</span>
       <span className="dataset-formula-frac">
        <span className="dataset-formula-num">cov(x, y)</span>
        <span className="dataset-formula-den">σx · σy</span>
       </span>
       <div className="dataset-formula-meta">
        <span><b>{fmt(correlation.sample_size)}</b> days</span>
        {correlation.date_start && correlation.date_end && <span>{dateFmt(correlation.date_start)} – {dateFmt(correlation.date_end)}</span>}
        <span>{selectedAdSetId ? 'Ad set scope' : selectedCampaignId ? 'Campaign scope' : 'Portfolio scope'}</span>
       </div>
      </div>
      <div className="dataset-formula-chips">
       {declaredCorrelation.variables.map((v) => (
        <div className="dataset-formula-chip" key={v.number} title={v.indices.map((index: number) => correlation.variables[index]?.label).filter(Boolean).join(', ')}>
         <b>{DATASET_DECLARED_SHORT_LABEL[v.number] || v.name}</b>
         <small>{v.indices.map((index: number) => correlation.variables[index]?.label).filter(Boolean).join(', ') || '—'}</small>
        </div>
       ))}
      </div>
     </div>
    )}
   </section>

   <section className="dataset-section">
    <div className="dataset-section-head">
     <div><span>Raw data</span><h3>Browse the imported rows</h3></div>
     {/* Editing an ad-performance row moves a model input (spend, frequency), so the backend
         schedules a background retrain. Without this the forecast would quietly be stale for
         ~18s after an edit with nothing on screen saying so. */}
     {retraining && <span className="board-retrain-chip"><RefreshCw size={12} />Retraining the model</span>}
    </div>
    <div className="dataset-rows-controls">
     <div className="dataset-tabs">
      <button className={rowsTable === 'leads' ? 'is-active' : ''} onClick={() => switchTable('leads')}>Leads</button>
      <button className={rowsTable === 'ad_performance' ? 'is-active' : ''} onClick={() => switchTable('ad_performance')}>Ad performance</button>
      <button className={rowsTable === 'ad_performance_export' ? 'is-active' : ''} onClick={() => switchTable('ad_performance_export')}>Combined export</button>
     </div>
     <div className="dataset-rows-controls-right">
      <BoardSearch value={searchDraft} onChange={setSearchDraft} />
      <PresetDateRangePicker
       value={rowDateRange}
       onApply={setRowDateRange}
       onClear={() => setRowDateRange(null)}
      />
      <FilterBar
       table={rowsTable}
       filters={rowFilters}
       appliedCount={completeRowFilters.length}
       resultLabel={rowsData.total
        ? `Showing ${completeRowFilters.length ? '' : 'all '}${fmt(rowsData.total)} rows`
        : ''}
       onChange={setRowFilters}
       onApply={() => setAppliedRowFilters(rowFilters)}
       onClearAll={() => { setRowFilters([]); setAppliedRowFilters([]); }}
      />
      <BoardSortMenu columns={boardColumns} sortable={sortableKeys} sort={rowSort} onChange={setRowSort} />
      <BoardColumnsMenu
       columns={boardColumns}
       hidden={hiddenColumns}
       onToggle={(key) => setHiddenColumns((current) => (current.includes(key) ? current.filter((item) => item !== key) : [...current, key]))}
       onShowAll={() => setHiddenColumns([])}
      />
      <BoardDensityMenu density={density} onChange={setDensity} />
     </div>
    </div>
    {boardError && <div className="lead-action-error board-error">{boardError}</div>}
    <div className={`dataset-rows-scroll board-scroll density-${density}${boardBusy ? ' is-busy' : ''}`}>
     {/* Column widths live on the header cells rather than a parallel <colgroup>, which
         under `table-layout: fixed` is equivalent and one less structure to keep in sync
         with the hidden-column set. */}
     <table className="dataset-rows-table board-table" style={{ width: boardTableWidth }}>
      <thead>
       <tr>
        <th className="board-check-col" style={{ width: 44 }}>
         <BoardCheckbox
          checked={allPageSelected}
          indeterminate={selectedOnPage.length > 0}
          label={allPageSelected ? 'Deselect all rows on this page' : 'Select all rows on this page'}
          onChange={toggleSelectAllOnPage}
         />
        </th>
        {visibleColumns.map((col) => (
         <BoardHeaderCell
          key={col.key}
          column={col}
          sortable={sortableKeys.includes(col.key)}
          sortDirection={rowSort?.field === col.key ? rowSort.direction : null}
          width={columnWidthOf(col)}
          onSort={() => cycleSort(col.key)}
          onResize={(width) => setColumnWidth(col.key, width)}
         />
        ))}
       </tr>
      </thead>
      <tbody>
       {rowsData.rows.map((row: any) => {
        const id = String(row.id);
        const isSelected = selectedRowIds.includes(id);
        return (
         <tr key={id} className={isSelected ? 'is-selected' : ''}>
          <td className="board-check-col">
           <BoardCheckbox checked={isSelected} label={`Select row ${id}`} onChange={() => toggleRowSelected(id)} />
          </td>
          {visibleColumns.map((col) => {
           if (col.edit === 'status') {
            return (
             <td key={col.key}>
              <MenuSelect
               className={`lead-status-select status-${String(row.status || 'unknown').toLowerCase()}`}
               ariaLabel={`Status for row ${id}`}
               value={row.status || ''}
               options={[{ value: 'New', label: 'New' }, { value: 'Existing', label: 'Existing' }]}
               onChange={(value) => commitBoardField(row, col, value)}
              />
             </td>
            );
           }
           if (col.edit) {
            const raw = col.edit === 'datetime-local'
             ? dateTimeInputValue(row[col.key])
             : row[col.key] == null ? '' : String(row[col.key]);
            return (
             <td key={col.key} className={col.align === 'num' ? 'num' : undefined}>
              <LeadEditableCell
               type={col.edit}
               align={col.align}
               value={raw}
               disabled={boardBusy}
               onCommit={(value) => commitBoardField(row, col, value)}
               formatDisplay={() => col.render ? col.render(row) : (row[col.key] ?? '-')}
              />
             </td>
            );
           }
           return (
            <td key={col.key} className={col.align === 'num' ? 'num' : undefined}>
             <span className="board-cell-static">{col.render ? col.render(row) : (row[col.key] ?? '-')}</span>
            </td>
           );
          })}
         </tr>
        );
       })}
      </tbody>
     </table>
     {!rowsBusy && !rowsData.rows.length && (
      <div className="table-empty">
       {rowSearch || completeRowFilters.length || rowDateRange
        ? 'No rows match the current search and filters.'
        : 'No rows in this table yet.'}
      </div>
     )}
    </div>
    <div className="dataset-rows-pager">
     <span>{rowsData.total ? `${fmt(rowStart)}-${fmt(rowEnd)} of ${fmt(rowsData.total)}` : ''}</span>
     <div>
      <button className="dataset-link-btn" disabled={rowsBusy || rowsOffset === 0} onClick={() => setRowsOffset((prev) => Math.max(0, prev - rowsData.limit))}><ChevronLeft size={13} /> Prev</button>
      <button className="dataset-link-btn" disabled={rowsBusy || rowEnd >= rowsData.total} onClick={() => setRowsOffset((prev) => prev + rowsData.limit)}>Next <ChevronRight size={13} /></button>
     </div>
    </div>
    {/* Monday's floating batch-action bar. Lives at the bottom of the viewport while any row
        is ticked, so the actions stay reachable however far down the board you've scrolled. */}
    {!!selectedRowIds.length && (
     <div className="board-bulk-bar" role="region" aria-label="Selected row actions">
      <div className="board-bulk-count"><strong>{fmt(selectedRowIds.length)}</strong><span>{selectedRowIds.length === 1 ? 'row' : 'rows'} selected</span></div>
      {/* Offered only once every row on the page is picked and more rows exist beyond it --
          the same "select all N matching" pattern as Gmail/Sheets, since ticking the header
          checkbox can only ever reach the page that happens to be loaded. */}
      {allPageSelected && !selectedAllMatching && rowsData.total > pageRowIds.length && (
       <button type="button" className="board-bulk-link" disabled={selectAllMatchingBusy} onClick={() => void selectAllMatchingRows()}>
        {selectAllMatchingBusy ? 'Selecting...' : `Select all ${fmt(rowsData.total)} rows matching this view`}
       </button>
      )}
      <div className="board-bulk-actions">
       <button type="button" className="board-bulk-btn" onClick={() => void exportSelectedRows()}><Download size={14} />Export CSV</button>
       <button type="button" className="board-bulk-btn danger" disabled={boardBusy} onClick={() => void deleteSelectedRows()}>
        <Trash2 size={14} />{boardBusy ? 'Deleting...' : 'Delete'}
       </button>
      </div>
      <button type="button" className="board-bulk-close" aria-label="Clear selection" onClick={() => { setSelectedRowIds([]); setSelectedAllMatching(false); }}><X size={15} /></button>
     </div>
    )}
   </section>
  </div>
 );
}

// One row, one action. The verdict against benchmark and the ad set's own budget-response curve
// are reconciled server-side, so the page never shows "boost" next to "cost is climbing".
const ACTIONS: Record<string, { label: string; tone: string }> = {
 cut: { label: 'Cut', tone: 'cut' },
 trim: { label: 'Trim', tone: 'trim' },
 scale: { label: 'Scale up', tone: 'scale' },
 watch: { label: 'Watch', tone: 'watch' },
 keep: { label: 'Keep', tone: 'keep' },
 paused: { label: 'Paused', tone: 'paused' },
};
const ACTION_ORDER = ['cut', 'trim', 'scale', 'watch', 'keep', 'paused'];

// The page's one bold element: the observed budget change and what it did to cost per lead, in
// real dollars. An elasticity number is the honest summary but nobody reads a slope at a glance,
// and two endpoints let the reader judge a thin sample themselves.
function BudgetResponse({ budget }: { budget: any }) {
 if (!budget || budget.cpl_from == null || budget.cpl_to == null) {
  return <span className="response-none" title="No budget change on record yet, so there is nothing to measure against.">Not measured</span>;
 }
 const rose = Number(budget.cpl_to) > Number(budget.cpl_from);
 return (
  <div className="response">
   <span className="response-budget">
    {cplMoney(budget.budget_from)}<i>-&gt;</i>{cplMoney(budget.budget_to)}<small>/day</small>
    {budget.budget_basis === 'detected' && (
     <em title="No budget change was recorded, so this is read from a sustained shift in daily spend.">est</em>
    )}
   </span>
   <span className={rose ? 'response-cpl worse' : 'response-cpl better'}>
    {rose ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
    {cplMoney(budget.cpl_from)}<i>-&gt;</i>{cplMoney(budget.cpl_to)}<small>per lead</small>
   </span>
  </div>
 );
}

const SPARKS = [
 [28, 74, 42, 32, 58, 25, 44, 70, 82, 90, 96, 78, 62, 45],
 [56, 70, 39, 78, 60, 88, 51, 76, 66, 62, 79, 44, 68, 35],
 [22, 46, 62, 78, 54, 90, 83, 66, 48, 72, 58, 80, 70, 55],
];

function MetricSpark({ values }: { values: number[] }) {
 return (
  <div className="om-spark" aria-hidden="true">
   {values.map((height, index) => <i key={`${height}-${index}`} style={{ height: `${height}%` }} />)}
  </div>
 );
}

function ReallocateMeter({ value, spend }: { value: any; spend: any }) {
 const amount = Math.max(0, Number(value) || 0);
 const total = Math.max(amount, Number(spend) || 0, 1);
 const width = Math.max(8, Math.min(100, (amount / total) * 100));
 return <div className="om-reallocate-meter" aria-hidden="true"><span style={{ width: `${width}%` }} /></div>;
}

function BenchmarkBar({ ratio, action }: { ratio: number | null; action: string }) {
 const safeRatio = ratio == null || !Number.isFinite(ratio) ? 4 : ratio;
 const width = Math.max(10, Math.min(100, (safeRatio / 4) * 100));
 return (
  <div className={`benchmark-bar action-${action}`}>
   <span style={{ width: `${width}%` }} />
   <i />
  </div>
 );
}

function OptimizationPage() {
 const [data, setData] = useState<any>(null);
 const [loading, setLoading] = useState(true);
 const [error, setError] = useState('');
 const [windowDays, setWindowDays] = useState(14);
 const [filter, setFilter] = useState<string | null>(null);
 const [expanded, setExpanded] = useState<string | null>(null);
 const [showAll, setShowAll] = useState(false);
 const [exporting, setExporting] = useState(false);

 useEffect(() => {
  let cancelled = false;
  setLoading(true);
  const query = `/dashboard/ad-decisions?window_days=${windowDays}`;
  api(query)
   .then((next) => { if (!cancelled) { setData(next); setError(''); } })
   .catch((err) => { if (!cancelled) setError(err.message || 'Could not load the optimization view'); })
   .finally(() => { if (!cancelled) setLoading(false); });
  return () => { cancelled = true; };
 }, [windowDays]);

 useEffect(() => {
  setShowAll(false);
  setExpanded(null);
 }, [filter, windowDays]);

 const summary = data?.summary || {};
 const realloc = data?.reallocation || {};
 const ads: any[] = data?.ads || [];
 const visible = filter ? ads.filter((ad) => ad.action === filter) : ads;
 const benchmark = Number(summary.benchmark_cpl) || 0;
 const pageSize = 8;
 const pagedVisible = showAll ? visible : visible.slice(0, pageSize);
 const shownCount = pagedVisible.length;
 const netLeads = Number(realloc.net_daily_leads) || 0;
 const leadStart = Number(summary.leads) || 0;
 const leadEnd = leadStart + Math.round(netLeads);
 const subtitle = `${fmt(ads.length)} ad sets measured against a ${cplMoney(summary.benchmark_cpl)} benchmark cost per lead.`;

 const counts: Record<string, number> = {
  cut: summary.cut_count || 0, trim: summary.trim_count || 0, scale: summary.scale_count || 0,
  watch: summary.watch_count || 0, keep: summary.keep_count || 0, paused: summary.paused_count || 0,
 };

 const toggle = (id: string) => setExpanded((current) => (current === id ? null : id));
 const exportDecisions = async () => {
  setExporting(true);
  try {
   await downloadApiFile(`/dashboard/ad-decisions.csv?window_days=${windowDays}`, 'ad-decisions.csv');
  } catch (downloadError: any) {
   setError(downloadError.message || 'Could not export decisions');
  } finally {
   setExporting(false);
  }
 };
 const metrics = [
  { label: 'Benchmark CPL', value: cplMoney(summary.benchmark_cpl), spark: SPARKS[0] },
  { label: 'Spent', value: money(summary.spend), spark: SPARKS[1] },
  { label: 'Leads', value: fmt(summary.leads), spark: SPARKS[2] },
  { label: 'Blended CPL', value: cplMoney(summary.blended_cpl), note: 'On benchmark' },
  { label: 'To reallocate', value: dayMoney(realloc.freed_daily), suffix: '/day', meter: true },
  { label: 'Net leads if applied', value: `${netLeads > 0 ? '+' : ''}${fmt(netLeads)}`, suffix: '/day', note: `${fmt(leadStart)} -> ${fmt(leadEnd)} leads`, positive: netLeads > 0 },
 ];

 return (
  <div className="page-content optimization-page optimization-design-clarity">
   <div className="page-heading">
    <div>
     <h2>Optimization</h2>
     <p>{subtitle}</p>
    </div>
    <div className="decision-controls">
     <div className="segmented" role="group" aria-label="Comparison window">
      {[7, 14, 30].map((days) => (
       <button
        key={days}
        type="button"
        className={windowDays === days ? 'active' : ''}
        aria-pressed={windowDays === days}
        onClick={() => setWindowDays(days)}
       >{days}d</button>
      ))}
     </div>
     <button type="button" className="ghost-button" disabled={exporting} onClick={exportDecisions}>{exporting ? 'Exporting' : 'Export'}</button>
    </div>
   </div>

   {error && <div className="decision-error" role="alert">{error}</div>}

   {loading && !data && (
    <section className="optimization-metrics optimization-skeleton" aria-hidden="true">
     {[0, 1, 2, 3, 4, 5].map((item) => (
      <div className="om-metric" key={item}>
       <div className="skeleton skeleton-line" style={{ width: '54%' }} />
       <div className="skeleton skeleton-line" style={{ width: '72%', height: 24 }} />
      </div>
     ))}
    </section>
   )}

   {!loading && data && !data.available && (
    <div className="card-empty-state glass-panel"><TrendingUp /><b>No ad spend yet</b>
     <span>Import a Meta ad set performance export to see which ad sets to cut, trim, and scale.</span></div>
   )}

   {data?.available && (
    <>
     <section className="optimization-metrics" aria-label="Optimization summary">
      {metrics.map((metric) => (
       <article className={metric.positive ? 'om-metric positive' : 'om-metric'} key={metric.label}>
        <span>{metric.label}</span>
        <strong>{metric.value}{metric.suffix && <small>{metric.suffix}</small>}</strong>
        {metric.spark && <MetricSpark values={metric.spark} />}
        {metric.note && <em>{metric.note}</em>}
        {metric.meter && <ReallocateMeter value={realloc.freed_daily} spend={summary.spend} />}
       </article>
      ))}
     </section>

     <div className="table-toolbar">
      <div className="verdict-filters" role="group" aria-label="Filter by action">
       <button type="button" className={filter === null ? 'active' : ''} aria-pressed={filter === null}
        onClick={() => setFilter(null)}>All <i>{ads.length}</i></button>
       {ACTION_ORDER.filter((key) => counts[key] > 0).map((key) => (
        <button
         key={key}
         type="button"
         className={`verdict-${ACTIONS[key].tone}${filter === key ? ' active' : ''}`}
         aria-pressed={filter === key}
         onClick={() => setFilter(filter === key ? null : key)}
        >{ACTIONS[key].label} <i>{counts[key]}</i></button>
       ))}
      </div>
      <div className="table-tools" aria-label="Table tools">
       <button type="button" title="Columns are fixed for this decision view">Columns</button>
       <button type="button" title="Use the action tabs to filter rows">Filters</button>
      </div>
     </div>

     <section className="table-card glass-panel decision-table">
      <div className="table-scroll">
       <table>
        <thead>
         <tr>
          <th>Ad set</th>
          <th>Action</th>
          <th>Vs benchmark</th>
          <th className="num">Cost / lead</th>
          <th className="num">Volume</th>
          <th className="num">Change</th>
          <th className="num">Ad set age</th>
          <th>Ad change</th>
          <th>Ad set change</th>
         </tr>
        </thead>
        <tbody>
         {pagedVisible.map((ad) => {
         const open = expanded === ad.ad_set_id;
         const action = ACTIONS[ad.action] || ACTIONS.keep;
         const ratio = benchmark && ad.cpl ? Number(ad.cpl) / benchmark : null;
         const spendSeries = (ad.series || []).map((point: any) => ({
          ...point,
          actual_leads: point.actual_leads ?? point.leads ?? 0,
          no_lead_spend: Number(point.spend || 0) > 0 && Number(point.actual_leads ?? point.leads ?? 0) <= 0,
         }));
         const totalSeriesSpend = spendSeries.reduce((sum: number, point: any) => sum + Number(point.spend || 0), 0);
         return [
           <tr
            key={ad.ad_set_id}
            className={open ? 'selected' : ''}
            tabIndex={0}
            role="button"
            aria-expanded={open}
            aria-label={`${ad.label}, ${action.label}. Show detail`}
            onClick={() => toggle(ad.ad_set_id)}
            onKeyDown={(event) => {
             if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); toggle(ad.ad_set_id); }
            }}
           >
            <td>
             <b className="row-label"><ChevronRight size={14} className={open ? 'row-caret open' : 'row-caret'} aria-hidden="true" />{ad.label}</b>
             <small className="row-id">{ad.ad_set_id}</small>
            </td>
            <td>
             <span className={`decision-action action-${ad.action}`}><i />{action.label}</span>
            </td>
            <td>
             <small className="row-reason">{ad.reason}</small>
             <BenchmarkBar ratio={ratio} action={ad.action} />
            </td>
            <td className="num">
             <span className="strong-num">{cplMoney(ad.cpl)}</span>
             {ratio && (
              <small className={ratio > 1 ? 'row-sub over' : 'row-sub under'}>
               {ratio > 1 ? `${ratio.toFixed(1)}× benchmark` : `${Math.round((1 - ratio) * 100)}% under`}
              </small>
            )}
           </td>
            <td className="num">
             {money(ad.spend)}
             <small className="row-sub">{fmt(ad.leads)} {ad.leads === 1 ? 'lead' : 'leads'}</small>
            </td>
            <td className="num">
             {ad.suggested_daily_delta
              ? <span className={ad.suggested_daily_delta > 0 ? 'budget-up' : 'budget-down'}>
                 {ad.suggested_daily_delta > 0 ? '+' : '-'}{dayMoney(Math.abs(ad.suggested_daily_delta))}<small>/day</small>
                </span>
              : <span className="delta-flat">hold</span>}
            </td>
            <td className="num">
             {ad.days_since_adset_started ?? '—'}
            </td>
            <td>
             {ad.ad_change_recency ?? '—'}
            </td>
            <td>
             {ad.ad_set_change_recency ?? '—'}
            </td>
           </tr>,
           open ? (
            <tr key={`${ad.ad_set_id}-detail`} className="detail-row">
             <td colSpan={11}>
              <div className="detail-panel">
               <div className="detail-copy">
                <div className="detail-head">
                 <h4>{ad.campaign_name}</h4>
                 <code className="detail-id">{ad.ad_set_id}</code>
                </div>
                <div className="detail-stats">
                 <div><span>Spend share</span><b>{percent(ad.spend_share)}</b></div>
                 <div><span>Lead share</span><b>{percent(ad.lead_share)}</b></div>
                 <div><span>Cost/lead before</span><b>{cplMoney(ad.cpl_prior)}</b></div>
                </div>
                {ad.budget?.periods?.length > 0 && (
                 <div className="budget-response-detail">
                  <h5>Budget history</h5>
                  <div className="budget-table">
                   {ad.budget.periods.map((period: any) => (
                    <div className="budget-table-row" key={period.id}>
                     <div className="budget-table-dates">
                      <span>{dateFmt(period.start_date)} -&gt; {dateFmt(period.end_date)}</span>
                      {period.observed_leads_per_day != null && (
                       <small>{fmt(period.observed_leads_per_day)}/day{period.observed_cpl != null ? ` - ${cplMoney(period.observed_cpl)}/lead` : ''}</small>
                      )}
                     </div>
                     <span className="budget-table-amount">{cplMoney(period.daily_budget)}</span>
                    </div>
                   ))}
                  </div>
                 </div>
                )}
               </div>
               <div className="detail-chart">
                <div className="detail-spend-chart-head">
                 <div><h5>Total spent per day</h5></div>
                 <span className="cpl-trend-scope-label"><i />{ad.label}</span>
                 <strong>{money(totalSeriesSpend)} total</strong>
                </div>
                <ResponsiveContainer width="100%" height={200}>
                 <ComposedChart data={spendSeries} margin={{ top: 12, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid stroke="var(--grid-line)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(value) => String(value).slice(5)}
                   stroke="var(--axis-line)" tick={{ fill: 'var(--muted)', fontSize: 12 }} />
                  <YAxis yAxisId="spend" tickFormatter={(value) => cplMoney(value)}
                   tick={{ fill: 'var(--yellow)', fontSize: 12, fontWeight: 600 }} axisLine={false} tickLine={false} width={68} />
                  <Tooltip content={<SpendPerDayTooltip />} />
                  <Line yAxisId="spend" type="monotone" dataKey="spend" name="Amount spent" stroke="var(--yellow)" strokeWidth={2.9} dot={<SpendCplDot />} activeDot={<SpendCplDot />} connectNulls isAnimationActive animationDuration={650} />
                 </ComposedChart>
                </ResponsiveContainer>
               </div>
              </div>
             </td>
            </tr>
           ) : null,
          ];
         })}
        </tbody>
       </table>
        {!visible.length && <div className="table-empty">No ad sets with this action.</div>}
       </div>
      </section>
      <div className="decision-footer">
       <span>Showing {shownCount} of {visible.length} ad sets</span>
       {!showAll && visible.length > pageSize && <button type="button" onClick={() => setShowAll(true)}>Show all</button>}
      </div>
     </>
   )}
  </div>
 );
}
function SettingsPage() {
 const rules = [
  ['Lead counting rule', 'Each validated row becomes one lead event.', '1 row = 1 lead'],
  ['Primary timestamp', 'Daily aggregation uses the lead creation time.', 'Created At'],
  ['Identifier storage', 'Campaign, ad set, and ad IDs stay text — never numeric.', 'Text only'],
  ['Prediction interval', 'Backtest residuals when available, variance fallback otherwise.', '80% calibrated'],
  ['Spend treatment', 'Repeated spend is context, never summed per lead.', 'Context only'],
 ];
 return (
 <div className="page-content data-contract-page">
 <section className="data-contract-heading">
 <span>Data contract</span>
 <h2>Forecast rules, clearly defined</h2>
 <p>Protected defaults that keep imports and training runs consistent.</p>
 </section>
 <section className="data-contract-rules" aria-label="Forecast rules">
 {rules.map(([title, desc, value]) => (
  <article key={title}>
  <div><b>{title}</b><p>{desc}</p></div>
  <strong>{value}</strong>
  </article>
 ))}
 </section>
 <div className="data-contract-notice"><span>i</span><div><b>Confidence is not probability.</b><p>A reliability score — data volume, volatility, error, and interval fit combined.</p></div></div>
 </div>
 );
}

export function App() {
 const [page, setPage] = useState<Page>('Forecast');
 const [auth, setAuth] = useState({ checking: true, required: false, signedIn: false, user: '' });

 useEffect(() => {
  let cancelled = false;
  const stored = sessionStorage.getItem(AUTH_STORAGE_KEY) || '';
  if (stored) setApiAuthHeader(stored);
  api('/auth/status')
   .then(async (status: any) => {
    if (cancelled) return;
    if (!status.required) {
     setAuth({ checking: false, required: false, signedIn: true, user: '' });
     return;
    }
    if (!stored) {
     setAuth({ checking: false, required: true, signedIn: false, user: '' });
     return;
    }
    try {
     const me = await api('/auth/me');
     if (!cancelled) setAuth({ checking: false, required: true, signedIn: true, user: me.user || '' });
    } catch {
     sessionStorage.removeItem(AUTH_STORAGE_KEY);
     setApiAuthHeader('');
     if (!cancelled) setAuth({ checking: false, required: true, signedIn: false, user: '' });
    }
   })
   .catch(() => {
    if (!cancelled) setAuth({ checking: false, required: true, signedIn: false, user: '' });
   });
  return () => { cancelled = true; };
 }, []);

 const signedIn = (token: string, user: string) => {
  setApiAuthHeader(token);
  sessionStorage.setItem(AUTH_STORAGE_KEY, token);
  setAuth({ checking: false, required: true, signedIn: true, user });
 };

 const signOut = () => {
  sessionStorage.removeItem(AUTH_STORAGE_KEY);
  setApiAuthHeader('');
  setPage('Forecast');
  setAuth((current) => ({ ...current, signedIn: false, user: '' }));
 };

 if (auth.checking) return <LoginPage checking onSignedIn={signedIn} />;
 if (auth.required && !auth.signedIn) return <LoginPage onSignedIn={signedIn} />;

 return (
 <Shell page={page} setPage={setPage} onSignOut={auth.required ? signOut : undefined}>
 {page === 'Forecast' ? <ForecastPage /> : page === 'Optimization' ? <OptimizationPage /> : page === 'Upload Data' ? <UploadPage /> : page === 'Data History' ? <HistoryPage /> : page === 'Dataset' ? <DatasetPage /> : <SettingsPage />}
 </Shell>
 );
}
