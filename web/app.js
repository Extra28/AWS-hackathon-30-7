'use strict';

// ---------------------------------------------------------------- helpers
const $ = (s) => document.querySelector(s);
const el = (t, cls, txt) => {
  const n = document.createElement(t);
  if (cls) n.className = cls;
  if (txt !== undefined) n.textContent = txt;
  return n;
};
const money = (v) => '$' + Math.round(v || 0).toLocaleString();
const pct = (v, d = 0) => (100 * (v || 0)).toFixed(d) + '%';
const pctRaw = (v, d = 1) => (v || 0).toFixed(d) + '%';

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(url + ' -> ' + r.status);
  return r.json();
}
async function postJSON(url, body) {
  const r = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  return r.json();
}

function bandOf(p, elig, floor) {
  if (p < floor) return 'below';
  if (p < elig) return 'inband';
  return 'above';
}

function table(cols, rows, rowFn) {
  const wrap = el('div', 'tablewrap');
  const t = el('table');
  const thead = el('thead');
  const tr = el('tr');
  cols.forEach((c) => {
    const th = el('th', c.num ? 'num' : null, c.label);
    tr.appendChild(th);
  });
  thead.appendChild(tr);
  t.appendChild(thead);
  const tb = el('tbody');
  rows.forEach((r) => tb.appendChild(rowFn(r)));
  t.appendChild(tb);
  wrap.appendChild(t);
  return wrap;
}

function emptyNote(msg) {
  return el('div', 'empty', msg);
}

// ---------------------------------------------------------------- state
const STATE = { summary: null, elig: 0.55, floor: 0.25 };

// ---------------------------------------------------------------- tabs
document.querySelectorAll('.tabs button').forEach((b) => {
  b.addEventListener('click', () => {
    document.querySelectorAll('.tabs button').forEach((x) => x.classList.remove('active'));
    document.querySelectorAll('.tab').forEach((x) => x.classList.remove('active'));
    b.classList.add('active');
    $('#tab-' + b.dataset.tab).classList.add('active');
    if (b.dataset.tab === 'fleet') loadFleet();
    if (b.dataset.tab === 'bench') loadBenchmark();
    if (b.dataset.tab === 'advice') loadAdvice();
  });
});

// ---------------------------------------------------------------- charts
function loadChart(perDay, cap) {
  const w = Math.max(perDay.length * 56, 320);
  const h = 190, padL = 40, padB = 32, padT = 14;
  const max = Math.max(cap * 1.15, ...perDay.map((d) => d.c), 1);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('width', w);
  svg.setAttribute('height', h);
  const NS = 'http://www.w3.org/2000/svg';
  const y = (v) => padT + (h - padT - padB) * (1 - v / max);

  [0, Math.round(max / 2), Math.round(max)].forEach((v) => {
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('x1', padL); ln.setAttribute('x2', w);
    ln.setAttribute('y1', y(v)); ln.setAttribute('y2', y(v));
    ln.setAttribute('class', 'axis');
    svg.appendChild(ln);
    const tx = document.createElementNS(NS, 'text');
    tx.setAttribute('x', 4); tx.setAttribute('y', y(v) + 3);
    tx.textContent = v;
    svg.appendChild(tx);
  });

  const capLine = document.createElementNS(NS, 'line');
  capLine.setAttribute('x1', padL); capLine.setAttribute('x2', w);
  capLine.setAttribute('y1', y(cap)); capLine.setAttribute('y2', y(cap));
  capLine.setAttribute('class', 'capline');
  svg.appendChild(capLine);

  const bw = Math.max(14, (w - padL - 10) / perDay.length - 12);
  perDay.forEach((d, i) => {
    const x = padL + 6 + i * ((w - padL - 10) / perDay.length);
    const r = document.createElementNS(NS, 'rect');
    r.setAttribute('x', x); r.setAttribute('width', bw);
    r.setAttribute('y', y(d.c)); r.setAttribute('height', y(0) - y(d.c));
    r.setAttribute('rx', 3);
    r.setAttribute('fill', d.c > cap ? '#f85149' : '#4a9eff');
    r.appendChild(document.createElementNS(NS, 'title')).textContent =
      d.plan_date + ': ' + d.c + ' visits';
    svg.appendChild(r);

    const lab = document.createElementNS(NS, 'text');
    lab.setAttribute('x', x + bw / 2); lab.setAttribute('y', h - 16);
    lab.setAttribute('text-anchor', 'middle');
    lab.textContent = d.plan_date.slice(5);
    svg.appendChild(lab);

    const val = document.createElementNS(NS, 'text');
    val.setAttribute('x', x + bw / 2); val.setAttribute('y', y(d.c) - 4);
    val.setAttribute('text-anchor', 'middle');
    val.textContent = d.c;
    svg.appendChild(val);
  });
  return svg;
}

function lineChart(series, colors) {
  const NS = 'http://www.w3.org/2000/svg';
  const keys = Object.keys(series);
  const len = Math.max(...keys.map((k) => series[k].length), 1);
  const w = Math.max(len * 26, 360), h = 220, padL = 42, padB = 30, padT = 12;
  let max = 1;
  keys.forEach((k) => series[k].forEach((p) => { max = Math.max(max, p.c); }));
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', w); svg.setAttribute('height', h);
  const y = (v) => padT + (h - padT - padB) * (1 - v / max);
  const x = (i) => padL + i * ((w - padL - 12) / Math.max(len - 1, 1));

  [0, Math.round(max / 2), max].forEach((v) => {
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('x1', padL); ln.setAttribute('x2', w);
    ln.setAttribute('y1', y(v)); ln.setAttribute('y2', y(v));
    ln.setAttribute('class', 'axis');
    svg.appendChild(ln);
    const tx = document.createElementNS(NS, 'text');
    tx.setAttribute('x', 4); tx.setAttribute('y', y(v) + 3);
    tx.textContent = v;
    svg.appendChild(tx);
  });

  keys.forEach((k) => {
    const pts = series[k].map((p, i) => x(i) + ',' + y(p.c)).join(' ');
    const pl = document.createElementNS(NS, 'polyline');
    pl.setAttribute('points', pts);
    pl.setAttribute('fill', 'none');
    pl.setAttribute('stroke', colors[k]);
    pl.setAttribute('stroke-width', '2');
    svg.appendChild(pl);

    const ah = series[k].map((p, i) => x(i) + ',' + y(p.adhoc || 0)).join(' ');
    const pl2 = document.createElementNS(NS, 'polyline');
    pl2.setAttribute('points', ah);
    pl2.setAttribute('fill', 'none');
    pl2.setAttribute('stroke', colors[k]);
    pl2.setAttribute('stroke-width', '1');
    pl2.setAttribute('stroke-dasharray', '3 3');
    pl2.setAttribute('opacity', '.75');
    svg.appendChild(pl2);
  });

  let lx = padL + 6;
  keys.forEach((k) => {
    const sw = document.createElementNS(NS, 'rect');
    sw.setAttribute('x', lx); sw.setAttribute('y', h - 12);
    sw.setAttribute('width', 9); sw.setAttribute('height', 9);
    sw.setAttribute('fill', colors[k]); sw.setAttribute('rx', 2);
    svg.appendChild(sw);
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', lx + 13); t.setAttribute('y', h - 4);
    t.textContent = k;
    svg.appendChild(t);
    lx += 22 + k.length * 6.4;
  });
  return svg;
}

// ---------------------------------------------------------------- summary
async function loadSummary() {
  const s = await getJSON('/api/summary');
  STATE.summary = s;
  if (!s.ready) {
    $('#subtitle').textContent = s.message;
    $('#banner').textContent = s.message;
    $('#banner').classList.remove('hidden');
    return;
  }
  STATE.elig = s.eligibility_pct;
  STATE.floor = s.threshold_pct;
  $('#subtitle').textContent =
    s.fleet_size + ' ATMs  |  planned as of ' + s.as_of + '  |  horizon ' +
    s.horizon + ' days  |  cap ' + s.daily_trip_cap + '/day (' + s.effective_cap +
    ' planned + ' + (s.daily_trip_cap - s.effective_cap) + ' ad hoc reserve)';

  const ai = s.policy_metrics.ai_planner || {};
  const re = s.policy_metrics.reactive || {};
  const k = $('#kpis');
  k.innerHTML = '';
  const band = s.band_counts || {};

  const add = (label, value, sub, cls) => {
    const d = el('div', 'kpi ' + (cls || ''));
    d.appendChild(el('div', 'label', label));
    d.appendChild(el('div', 'value', value));
    if (sub) d.appendChild(el('div', 'sub', sub));
    k.appendChild(d);
  };

  const peak = Math.max(0, ...(s.per_day || []).map((d) => d.c));
  add('Peak day load', peak, 'cap ' + s.effective_cap + ' planned',
    peak > s.effective_cap ? 'bad' : 'good');
  add('In refill band', band.in_band || 0, '25-55% of capacity', 'warn');
  add('Below floor', band.below_threshold || 0, 'under 25% now',
    (band.below_threshold || 0) > 0 ? 'bad' : 'good');

  if (ai.total_trips !== undefined) {
    add('Trips (sim)', Math.round(ai.total_trips),
      re.total_trips ? 'vs ' + Math.round(re.total_trips) + ' reactive' : '', 'good');
    add('Ad hoc rate', pctRaw(ai.adhoc_rate_pct),
      re.adhoc_rate_pct !== undefined ? 'vs ' + pctRaw(re.adhoc_rate_pct) + ' reactive' : '',
      ai.adhoc_rate_pct < (re.adhoc_rate_pct || 100) ? 'good' : 'warn');
    add('Stockouts', Math.round(ai.stockout_events || 0),
      'reactive ' + Math.round(re.stockout_events || 0),
      (ai.stockout_events || 0) === 0 ? 'good' : 'bad');
    add('Fill at refill', pctRaw(ai.mean_fill_at_refill_pct),
      pctRaw(ai.refills_inside_band_pct) + ' inside band', 'warn');
  }

  const fails = (s.validation || []).filter((c) => !c.passed);
  if (fails.length) {
    $('#banner').innerHTML = '<strong>' + fails.length +
      ' constraint check(s) not met:</strong> ' +
      fails.map((f) => f.check_name + ' (observed ' + Math.round(f.observed) +
        ', limit ' + Math.round(f.limit_val) + ')').join('; ') +
      '. Best-effort scheduling at prototype scale; see Model &amp; checks tab.';
    $('#banner').classList.remove('hidden');
  }

  $('#loadchart').innerHTML = '';
  $('#loadchart').appendChild(loadChart(s.per_day || [], s.effective_cap));
  renderChecks(s.validation || []);
  renderAccuracy(s.forecast_accuracy || []);
}

// ---------------------------------------------------------------- day plan
async function loadSchedule(date) {
  const d = await getJSON('/api/schedule' + (date ? '?date=' + encodeURIComponent(date) : ''));
  const sel = $('#dateSel');
  if (!sel.options.length) {
    (d.dates || []).forEach((x) => {
      const o = el('option', null, x);
      o.value = x;
      sel.appendChild(o);
    });
  }
  sel.value = d.date;
  $('#planDate').textContent = d.date || '-';

  const mix = $('#mixbar');
  mix.innerHTML = '';
  const total = d.rows.length || 1;
  mix.appendChild(el('span', null, total + ' visits'));
  ['P0', 'P1', 'P2'].forEach((b) => {
    const got = d.band_counts[b] || 0;
    const tgt = (d.band_target || {})[b] || 0;
    const seg = el('div', 'mixseg', b + ' ' + got);
    seg.style.width = Math.max(52, (got / total) * 380) + 'px';
    seg.style.background = b === 'P0' ? '#4a9eff' : (b === 'P1' ? '#a371f7' : '#56d4dd');
    seg.title = b + ': ' + got + ' scheduled, target ' + tgt;
    mix.appendChild(seg);
  });
  mix.appendChild(el('span', null,
    'target ' + ['P0', 'P1', 'P2'].map((b) => (d.band_target || {})[b] || 0).join(' / ')));

  const host = $('#planTables');
  host.innerHTML = '';
  if (!d.rows.length) { host.appendChild(emptyNote('No visits planned for this day.')); return; }

  ['P0', 'P1', 'P2'].forEach((b) => {
    const rows = d.rows.filter((r) => r.priority_window === b);
    if (!rows.length) return;
    const label = { P0: 'P0  0000-0800', P1: 'P1  0800-1200', P2: 'P2  1200-0000' }[b];
    host.appendChild(el('div', 'groupheading', label + '   (' + rows.length + ')'));
    host.appendChild(table(
      [{ label: 'ATM' }, { label: 'Location' }, { label: 'Now', num: true },
       { label: 'Band' }, { label: 'At visit', num: true }, { label: 'Load', num: true },
       { label: 'Window' }, { label: 'Driver' }],
      rows,
      (r) => {
        const tr = el('tr');
        const a = el('a', 'linkish', r.atm_id);
        a.href = '#';
        a.onclick = (e) => { e.preventDefault(); openDrawer(r.atm_id); };
        tr.appendChild(el('td')).appendChild(a);
        tr.appendChild(el('td', null, r.location_type.replace(/_/g, ' ')));
        tr.appendChild(el('td', 'num', pct(r.pct_of_capacity)));
        const bt = el('td');
        const bar = el('div', 'bar');
        const fill = el('i', bandOf(r.pct_of_capacity, STATE.elig, STATE.floor));
        fill.style.width = Math.min(100, 100 * r.pct_of_capacity) + '%';
        bar.appendChild(fill);
        bt.appendChild(bar);
        tr.appendChild(bt);
        tr.appendChild(el('td', 'num', pct(r.pct_at_visit)));
        tr.appendChild(el('td', 'num', money(r.load_total)));
        tr.appendChild(el('td', null,
          (r.opens_date || '?').slice(5) + ' to ' + (r.effective_latest || '?').slice(5)));
        const dv = el('td');
        const p = el('span', 'pill ' + (r.compliance_driven ? 'compliance' : 'demand'),
          r.compliance_driven ? 'compliance' : (r.driver || 'demand'));
        dv.appendChild(p);
        tr.appendChild(dv);
        return tr;
      }));
  });
}

$('#dateSel').addEventListener('change', (e) => loadSchedule(e.target.value));

// ---------------------------------------------------------------- fleet
let FLEET = [];
async function loadFleet() {
  if (!FLEET.length) {
    const d = await getJSON('/api/fleet');
    FLEET = d.rows;
  }
  renderFleet();
}
function renderFleet() {
  const q = ($('#fleetSearch').value || '').toLowerCase();
  const bandSel = $('#fleetBand').value;
  const rows = FLEET.filter((r) => {
    if (q && !(r.atm_id + ' ' + r.name + ' ' + r.region + ' ' + r.location_type)
      .toLowerCase().includes(q)) return false;
    if (bandSel && bandOf(r.pct_of_capacity, STATE.elig, STATE.floor) !== bandSel) return false;
    return true;
  });
  const host = $('#fleetTable');
  host.innerHTML = '';
  host.appendChild(el('div', 'hint',
    rows.length + ' of ' + FLEET.length + ' ATMs, most urgent first'));
  host.appendChild(table(
    [{ label: 'ATM' }, { label: 'Region' }, { label: 'Type' }, { label: 'Balance', num: true },
     { label: 'Now', num: true }, { label: 'Band' }, { label: 'Slack', num: true },
     { label: 'Latest' }, { label: 'Planned' }],
    rows,
    (r) => {
      const tr = el('tr');
      const a = el('a', 'linkish', r.atm_id);
      a.href = '#';
      a.onclick = (e) => { e.preventDefault(); openDrawer(r.atm_id); };
      tr.appendChild(el('td')).appendChild(a);
      tr.appendChild(el('td', null, r.region));
      tr.appendChild(el('td', null, (r.location_type || '').replace(/_/g, ' ')));
      tr.appendChild(el('td', 'num', money(r.total_balance)));
      tr.appendChild(el('td', 'num', pct(r.pct_of_capacity)));
      const bt = el('td');
      const bar = el('div', 'bar');
      const fill = el('i', bandOf(r.pct_of_capacity, STATE.elig, STATE.floor));
      fill.style.width = Math.min(100, 100 * r.pct_of_capacity) + '%';
      bar.appendChild(fill);
      bt.appendChild(bar);
      tr.appendChild(bt);
      tr.appendChild(el('td', 'num', r.slack_days === null ? '-' : r.slack_days));
      tr.appendChild(el('td', null, (r.effective_latest || '-').slice(5)));
      const pl = el('td');
      if (r.plan_date) {
        pl.appendChild(el('span', 'pill ' + r.priority_window,
          r.plan_date.slice(5) + ' ' + r.priority_window));
      } else {
        pl.textContent = '-';
      }
      tr.appendChild(pl);
      return tr;
    }));
}
$('#fleetSearch').addEventListener('input', renderFleet);
$('#fleetBand').addEventListener('change', renderFleet);

// ---------------------------------------------------------------- benchmark
const BENCH_ROWS = [
  ['total_trips', 'Total trips', 0],
  ['planned_trips', 'Planned trips', 0],
  ['adhoc_trips', 'Ad hoc trips', 0],
  ['adhoc_low_cash', '  - low cash', 0],
  ['adhoc_fault', '  - fault', 0],
  ['adhoc_rate_pct', 'Ad hoc rate', 1, '%'],
  ['mean_trips_per_day', 'Mean trips/day', 1],
  ['peak_trips_per_day', 'Peak trips/day', 0],
  ['stockout_events', 'Stockout events', 0],
  ['low_cash_breach_hours', 'Hours below 25%', 0],
  ['unmet_demand', 'Unmet demand', 0, '$'],
  ['mean_idle_cash', 'Mean cash in field', 0, '$'],
  ['mean_fill_at_refill_pct', 'Mean fill at refill', 1, '%'],
  ['refills_inside_band_pct', 'Refills inside band', 1, '%'],
  ['mean_refill_interval_days', 'Mean interval (days)', 1],
  ['max_refill_interval_days', 'Max interval (days)', 0],
  ['intervals_over_limit', 'Intervals over limit', 0],
  ['days_over_cap', 'Days over trip cap', 0],
];

async function loadBenchmark() {
  const d = await getJSON('/api/benchmark');
  const pols = Object.keys(d.metrics).filter((p) => Object.keys(d.metrics[p]).length);
  const host = $('#benchTable');
  host.innerHTML = '';
  if (!pols.length) {
    host.appendChild(emptyNote('No simulation results. Run: python run_pipeline.py'));
    return;
  }
  const t = el('table');
  const thead = el('thead');
  const hr = el('tr');
  hr.appendChild(el('th', null, 'Metric'));
  pols.forEach((p) => hr.appendChild(el('th', 'num', d.labels[p] || p)));
  thead.appendChild(hr);
  t.appendChild(thead);
  const tb = el('tbody');
  BENCH_ROWS.forEach(([key, label, dp, unit]) => {
    const tr = el('tr');
    tr.appendChild(el('td', null, label));
    pols.forEach((p) => {
      const v = d.metrics[p][key];
      let txt = '-';
      if (v !== undefined && v !== null) {
        txt = unit === '$' ? money(v) : Number(v).toFixed(dp) + (unit === '%' ? '%' : '');
      }
      tr.appendChild(el('td', 'num', txt));
    });
    tb.appendChild(tr);
  });
  t.appendChild(tb);
  host.appendChild(t);

  const ai = d.metrics.ai_planner || {};
  const notes = [];
  ['reactive', 'fixed_calendar'].forEach((b) => {
    const tr = ai['trips_reduction_pct_vs_' + b];
    const ad = ai['adhoc_reduction_pct_vs_' + b];
    if (tr !== undefined) {
      notes.push('vs ' + b + ': ' + Number(tr).toFixed(1) + '% fewer trips, ' +
        Number(ad || 0).toFixed(1) + '% fewer ad hoc');
    }
  });
  if (notes.length) host.appendChild(el('div', 'hint', notes.join('   |   ')));

  $('#benchChart').innerHTML = '';
  $('#benchChart').appendChild(lineChart(d.trend, {
    ai_planner: '#4a9eff', reactive: '#f85149', fixed_calendar: '#d29922',
  }));

  const ah = await getJSON('/api/adhoc');
  const host2 = $('#adhocTable');
  host2.innerHTML = '';
  if (!ah.rows.length) {
    host2.appendChild(emptyNote('No ad hoc trips were raised under the planner.'));
  } else {
    host2.appendChild(el('div', 'hint', ah.by_trigger
      .map((x) => x.trigger + ': ' + x.c).join('   |   ')));
    host2.appendChild(table(
      [{ label: 'Date' }, { label: 'ATM' }, { label: 'Trigger' },
       { label: 'Before', num: true }, { label: 'Loaded', num: true }],
      ah.rows,
      (r) => {
        const tr = el('tr');
        tr.appendChild(el('td', null, r.d));
        const a = el('a', 'linkish', r.atm_id);
        a.href = '#';
        a.onclick = (e) => { e.preventDefault(); openDrawer(r.atm_id); };
        tr.appendChild(el('td')).appendChild(a);
        tr.appendChild(el('td')).appendChild(el('span', 'pill ' + r.trigger, r.trigger));
        tr.appendChild(el('td', 'num', pct(r.pct_before)));
        tr.appendChild(el('td', 'num', money(r.cash_loaded)));
        return tr;
      }));
  }
}

// ---------------------------------------------------------------- model tab
function renderAccuracy(acc) {
  const host = $('#accTable');
  host.innerHTML = '';
  if (!acc.length) { host.appendChild(emptyNote('No model metrics yet.')); return; }
  host.appendChild(table(
    [{ label: 'Model' }, { label: 'Metric' }, { label: 'Value', num: true }],
    acc,
    (r) => {
      const tr = el('tr');
      tr.appendChild(el('td', null, r.model));
      tr.appendChild(el('td', null, r.metric));
      const v = r.metric.includes('coverage') ? r.value.toFixed(3)
        : (r.metric.includes('pct') ? r.value.toFixed(1) + '%'
          : Math.round(r.value).toLocaleString());
      tr.appendChild(el('td', 'num', v));
      return tr;
    }));
  host.appendChild(el('div', 'hint',
    'Coverage is the share of actuals at or below the predicted quantile; ' +
    'a well calibrated q90 sits near 0.90.'));
}

function renderChecks(checks) {
  const host = $('#checkTable');
  host.innerHTML = '';
  if (!checks.length) { host.appendChild(emptyNote('No validation results yet.')); return; }
  host.appendChild(table(
    [{ label: 'Check' }, { label: 'Result' }, { label: 'Observed', num: true },
     { label: 'Limit', num: true }, { label: 'Meaning' }],
    checks,
    (r) => {
      const tr = el('tr');
      tr.appendChild(el('td', null, r.check_name));
      const st = el('td');
      st.appendChild(el('span', 'pill ' + (r.passed ? 'demand' : 'fault'),
        r.passed ? 'pass' : 'fail'));
      tr.appendChild(st);
      tr.appendChild(el('td', 'num', Math.round(r.observed)));
      tr.appendChild(el('td', 'num', Math.round(r.limit_val)));
      tr.appendChild(el('td', null, r.detail));
      return tr;
    }));
}

// ---------------------------------------------------------------- advice
async function loadAdvice() {
  const d = await getJSON('/api/recommendations');
  const host = $('#adviceTable');
  host.innerHTML = '';
  if (!d.rows.length) {
    host.appendChild(emptyNote(
      'No cartridge remix candidates. No ATM is currently constrained by a single ' +
      'denomination running out ahead of its aggregate balance.'));
    return;
  }
  host.appendChild(table(
    [{ label: 'ATM' }, { label: 'Current mix' }, { label: 'Recommended' },
     { label: 'Cover now', num: true }, { label: 'Projected', num: true },
     { label: 'Rationale' }],
    d.rows,
    (r) => {
      const tr = el('tr');
      const a = el('a', 'linkish', r.atm_id);
      a.href = '#';
      a.onclick = (e) => { e.preventDefault(); openDrawer(r.atm_id); };
      tr.appendChild(el('td')).appendChild(a);
      tr.appendChild(el('td', null, JSON.parse(r.current_mix).map((x) => '$' + x).join(' ')));
      tr.appendChild(el('td', null, JSON.parse(r.recommended_mix).map((x) => '$' + x).join(' ')));
      tr.appendChild(el('td', 'num', r.current_interval_days + 'd'));
      tr.appendChild(el('td', 'num', r.projected_interval_days + 'd'));
      tr.appendChild(el('td', null, r.rationale));
      return tr;
    }));
}

// ---------------------------------------------------------------- replan
$('#replanBtn').addEventListener('click', async () => {
  const btn = $('#replanBtn');
  btn.disabled = true;
  const out = $('#replanOut');
  out.textContent = 'replanning (retraining the model, this takes a few seconds)...';
  const body = {
    safety_quantile: parseFloat($('#cfgQuantile').value),
    refill_eligibility_pct: parseFloat($('#cfgElig').value),
    low_cash_threshold_pct: parseFloat($('#cfgFloor').value),
    adhoc_reserve_trips: parseInt($('#cfgReserve').value, 10),
  };
  try {
    const r = await postJSON('/api/replan', body);
    if (r.error) {
      out.textContent = 'rejected: ' + r.error;
    } else {
      const peak = Math.max(...r.per_day.map((d) => d.c));
      const fails = (r.validation || []).filter((c) => !c.passed).length;
      out.textContent = 'run ' + r.run_id + ': ' + r.total_visits + ' visits, peak ' +
        peak + '/day, ' + r.compliance_driven + ' compliance-driven, mean fill at visit ' +
        r.mean_fill_at_visit_pct.toFixed(1) + '%, ' + fails + ' check(s) failing.';
      FLEET = [];
      $('#dateSel').innerHTML = '';
      await loadSummary();
      await loadSchedule();
    }
  } catch (e) {
    out.textContent = 'replan failed: ' + e.message;
  }
  btn.disabled = false;
});

// ---------------------------------------------------------------- ask
$('#askForm').addEventListener('submit', (e) => {
  e.preventDefault();
  ask($('#askInput').value);
});
document.querySelectorAll('.chip').forEach((c) => {
  c.addEventListener('click', () => { $('#askInput').value = c.dataset.q; ask(c.dataset.q); });
});
async function ask(q) {
  if (!q.trim()) return;
  const r = await postJSON('/api/ask', { question: q });
  const out = $('#askOut');
  const b = el('div', 'askbubble');
  b.appendChild(el('div', 'hint', q));
  b.appendChild(el('div', null, r.explanation || r.answer || 'no answer'));
  if (r.rows) {
    b.appendChild(table(
      [{ label: 'ATM' }, { label: 'Now', num: true }, { label: 'Floor date' },
       { label: 'Slack', num: true }],
      r.rows,
      (x) => {
        const tr = el('tr');
        tr.appendChild(el('td', null, x.atm_id));
        tr.appendChild(el('td', 'num', pct(x.pct_now)));
        tr.appendChild(el('td', null, x.closes_date || '-'));
        tr.appendChild(el('td', 'num', x.slack_days));
        return tr;
      }));
  }
  out.prepend(b);
}

// ---------------------------------------------------------------- drawer
async function openDrawer(atmId) {
  const d = await getJSON('/api/atm/' + atmId);
  $('#drawerTitle').textContent = atmId + '  ' + (d.atm.name || '');
  const body = $('#drawerBody');
  body.innerHTML = '';

  if (d.schedule) {
    body.appendChild(el('div', 'reason', d.schedule.reason));
  } else {
    const ex = await getJSON('/api/explain/' + atmId);
    body.appendChild(el('div', 'reason', ex.explanation));
  }

  const kv = el('div', 'kv');
  const pairs = [
    ['Location', (d.atm.location_type || '').replace(/_/g, ' ') + ', ' + d.atm.region],
    ['Capacity', money(d.atm.capacity)],
    ['Balance now', money(d.state.total_balance) + '  (' + pct(d.state.pct_of_capacity, 1) + ')'],
    ['55% level', money(d.atm.eligibility_level)],
    ['25% floor', money(d.atm.low_cash_level)],
    ['Last refill', d.state.last_refill_date],
    ['Cartridges', d.cartridges.map((c) => '$' + c.denomination).join('  ')],
  ];
  if (d.window) {
    pairs.push(['Window opens', d.window.opens_date || 'already eligible']);
    pairs.push(['Floor date', d.window.closes_date || 'beyond horizon']);
    pairs.push(['Interval deadline', d.window.regulatory_deadline]);
    pairs.push(['Effective latest', d.window.effective_latest + '  (' + d.window.driver + ')']);
    pairs.push(['Slack', d.window.slack_days + ' days']);
  }
  if (d.schedule) {
    pairs.push(['Scheduled', d.schedule.plan_date + '  ' + d.schedule.priority_window]);
    pairs.push(['Load', money(d.schedule.load_total)]);
  }
  pairs.forEach(([k, v]) => {
    kv.appendChild(el('div', 'k', k));
    kv.appendChild(el('div', 'v', String(v)));
  });
  body.appendChild(kv);

  body.appendChild(el('div', 'groupheading', 'Projected balance vs the 55% and 25% levels'));
  body.appendChild(trajChart(d));

  body.appendChild(el('div', 'groupheading', 'Hourly balance, recent telemetry'));
  body.appendChild(telChart(d));

  body.appendChild(el('div', 'groupheading', 'Forecast by horizon (q50 / q90 / q95)'));
  body.appendChild(table(
    [{ label: 'Date' }, { label: 'q50', num: true }, { label: 'q90', num: true },
     { label: 'q95', num: true }],
    d.forecast,
    (f) => {
      const tr = el('tr');
      tr.appendChild(el('td', null, f.target_date));
      tr.appendChild(el('td', 'num', money(f.q50)));
      tr.appendChild(el('td', 'num', money(f.q90)));
      tr.appendChild(el('td', 'num', money(f.q95)));
      return tr;
    }));

  if (d.denom_recommendation) {
    body.appendChild(el('div', 'groupheading', 'Cartridge advice'));
    body.appendChild(el('div', 'reason', d.denom_recommendation.rationale));
  }
  if (d.faults.length) {
    body.appendChild(el('div', 'groupheading', 'Recent faults'));
    body.appendChild(table(
      [{ label: 'Category' }, { label: 'Disables' }, { label: 'Start' }],
      d.faults,
      (f) => {
        const tr = el('tr');
        tr.appendChild(el('td', null, f.category));
        tr.appendChild(el('td', null, f.disables_dispensing ? 'yes' : 'no'));
        tr.appendChild(el('td', null, f.start_ts));
        return tr;
      }));
  }
  $('#drawer').classList.remove('hidden');
  $('#scrim').classList.remove('hidden');
}

function trajChart(d) {
  const NS = 'http://www.w3.org/2000/svg';
  const pts = d.trajectory || [];
  const w = 560, h = 170, padL = 46, padB = 26, padT = 10;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', w); svg.setAttribute('height', h);
  const cap = d.atm.capacity;
  const y = (v) => padT + (h - padT - padB) * (1 - Math.max(v, 0) / cap);
  const x = (i) => padL + i * ((w - padL - 12) / Math.max(pts.length - 1, 1));

  [[d.eligibility_pct, '#d29922', '55%'], [d.threshold_pct, '#f85149', '25%']]
    .forEach(([p, col, lab]) => {
      const ln = document.createElementNS(NS, 'line');
      ln.setAttribute('x1', padL); ln.setAttribute('x2', w);
      ln.setAttribute('y1', y(cap * p)); ln.setAttribute('y2', y(cap * p));
      ln.setAttribute('stroke', col); ln.setAttribute('stroke-dasharray', '4 3');
      svg.appendChild(ln);
      const t = document.createElementNS(NS, 'text');
      t.setAttribute('x', 4); t.setAttribute('y', y(cap * p) + 3);
      t.setAttribute('fill', col);
      t.textContent = lab;
      svg.appendChild(t);
    });

  const poly = document.createElementNS(NS, 'polyline');
  poly.setAttribute('points', pts.map((p, i) => x(i) + ',' + y(p.balance)).join(' '));
  poly.setAttribute('fill', 'none');
  poly.setAttribute('stroke', '#4a9eff');
  poly.setAttribute('stroke-width', '2');
  svg.appendChild(poly);

  pts.forEach((p, i) => {
    if (i % 3) return;
    const t = document.createElementNS(NS, 'text');
    t.setAttribute('x', x(i)); t.setAttribute('y', h - 8);
    t.setAttribute('text-anchor', 'middle');
    t.textContent = p.date.slice(5);
    svg.appendChild(t);
  });
  return svg;
}

function telChart(d) {
  const NS = 'http://www.w3.org/2000/svg';
  const pts = d.telemetry || [];
  const w = 560, h = 150, padL = 46, padB = 22, padT = 8;
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('width', w); svg.setAttribute('height', h);
  if (!pts.length) return svg;
  const cap = d.atm.capacity;
  const y = (v) => padT + (h - padT - padB) * (1 - Math.max(v, 0) / cap);
  const x = (i) => padL + i * ((w - padL - 12) / Math.max(pts.length - 1, 1));

  [[d.eligibility_pct, '#d29922'], [d.threshold_pct, '#f85149']].forEach(([p, col]) => {
    const ln = document.createElementNS(NS, 'line');
    ln.setAttribute('x1', padL); ln.setAttribute('x2', w);
    ln.setAttribute('y1', y(cap * p)); ln.setAttribute('y2', y(cap * p));
    ln.setAttribute('stroke', col); ln.setAttribute('stroke-dasharray', '4 3');
    svg.appendChild(ln);
  });

  const poly = document.createElementNS(NS, 'polyline');
  poly.setAttribute('points', pts.map((p, i) => x(i) + ',' + y(p.total_balance)).join(' '));
  poly.setAttribute('fill', 'none');
  poly.setAttribute('stroke', '#3fb950');
  poly.setAttribute('stroke-width', '1.4');
  svg.appendChild(poly);

  const first = document.createElementNS(NS, 'text');
  first.setAttribute('x', padL); first.setAttribute('y', h - 6);
  first.textContent = pts[0].ts.slice(0, 10);
  svg.appendChild(first);
  const last = document.createElementNS(NS, 'text');
  last.setAttribute('x', w - 12); last.setAttribute('y', h - 6);
  last.setAttribute('text-anchor', 'end');
  last.textContent = pts[pts.length - 1].ts.slice(0, 10);
  svg.appendChild(last);
  return svg;
}

function closeDrawer() {
  $('#drawer').classList.add('hidden');
  $('#scrim').classList.add('hidden');
}
$('#drawerClose').addEventListener('click', closeDrawer);
$('#scrim').addEventListener('click', closeDrawer);
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });

// ---------------------------------------------------------------- boot
(async function boot() {
  try {
    await loadSummary();
    if (STATE.summary && STATE.summary.ready) await loadSchedule();
  } catch (e) {
    $('#banner').textContent = 'Failed to load: ' + e.message +
      '. Has the pipeline been run? python run_pipeline.py';
    $('#banner').classList.remove('hidden');
  }
})();
