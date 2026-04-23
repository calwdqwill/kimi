/**
 * Brent Spread Dashboard — Multi-Contract Frontend
 * Ocean Theme | Mission Control Layout
 */

// =============================================================================
// STATE
// =============================================================================
const state = {
  contracts: [],
  activeContract: null,
  activeTf: '15m',
  chartMode: 'spread%',
  historicalData: [],
  zscoreData: [],
  stats: {},
  currentData: null,
  tickCount: 0,
  sessionStart: null,
  // Range slider state
  rangeStart: 0,    // index into historicalData
  rangeEnd: 0,      // index into historicalData
  isDragging: null, // 'left' | 'right' | null
  pollInterval: null,
};

// =============================================================================
// UTILS
// =============================================================================
function fmt$(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  const s = v < 0 ? '—' : '';
  return s + '$' + Math.abs(v).toFixed(3);
}
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v >= 0 ? '+' : '') + v.toFixed(3) + '%';
}
function fmtN(v, d = 2) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v >= 0 ? '' : '') + v.toFixed(d);
}
function fmtTs(ms) {
  const d = new Date(ms);
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}
function fmtDate(ms) {
  const d = new Date(ms);
  return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' }) + ', ' +
         d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
}

// =============================================================================
// API
// =============================================================================
async function api(path) {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`${path}: ${r.status}`);
  return r.json();
}

async function loadContracts() {
  state.contracts = await api('/api/contracts');
  renderContractTabs();
  if (!state.activeContract && state.contracts.length > 0) {
    const active = state.contracts.find(c => c.is_active) || state.contracts[0];
    setActiveContract(active.id);
  }
}

function setActiveContract(id) {
  state.activeContract = id;
  const c = state.contracts.find(x => x.id === id);
  document.getElementById('logoContract').textContent = '/ ' + (c?.name || id.toUpperCase());
  document.getElementById('kpiMoexName').textContent = c?.name || id.toUpperCase();
  renderContractTabs();
  refreshAll().then(() => {
    initRangeSlider();
    updateChart();
    updateSliderUI();
  });
}

// =============================================================================
// CONTRACT TABS
// =============================================================================
function renderContractTabs() {
  const el = document.getElementById('contractTabs');
  el.innerHTML = '';
  state.contracts.forEach(c => {
    const btn = document.createElement('button');
    btn.className = 'contract-tab' + (c.id === state.activeContract ? ' active' : '');
    if (c.is_active && c.id === state.activeContract) {
      btn.innerHTML = `${c.name}<span class="tab-dot"></span>`;
    } else {
      btn.textContent = c.name;
    }
    btn.onclick = () => setActiveContract(c.id);
    el.appendChild(btn);
  });
  // Add new button
  const addBtn = document.createElement('button');
  addBtn.className = 'contract-tab add-new';
  addBtn.textContent = '+ Добавить';
  addBtn.onclick = showAddContractModal;
  el.appendChild(addBtn);
}

function showAddContractModal() {
  // Simple prompt-based for now
  const id = prompt('ID контракта (например: bmm7):');
  if (!id) return;
  const name = prompt('Название (например: BMM7):') || id.toUpperCase();
  const moex = prompt('MOEX символ (например: BMM7@RTSX):');
  if (!moex) return;
  const hl = prompt('HL тикер (например: xyz:BRENTOIL):') || 'xyz:BRENTOIL';

  fetch(`/api/contracts?contract_id=${encodeURIComponent(id)}&name=${encodeURIComponent(name)}&moex_symbol=${encodeURIComponent(moex)}&hl_coin=${encodeURIComponent(hl)}`, { method: 'POST' })
    .then(r => r.json())
    .then(() => loadContracts())
    .catch(e => alert('Ошибка: ' + e.message));
}

// =============================================================================
// TIMEFRAME
// =============================================================================
document.querySelectorAll('.tf-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.activeTf = btn.dataset.tf;
    refreshAll();
  });
});

// =============================================================================
// CHART MODE
// =============================================================================
document.querySelectorAll('.chart-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.chart-mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    state.chartMode = btn.dataset.mode;
    updateChart();
  });
});

// =============================================================================
// TABLE TABS
// =============================================================================
document.querySelectorAll('.table-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.table-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const mode = tab.dataset.tab;
    document.getElementById('tickTable').style.display = mode === 'ticks' ? '' : 'none';
    document.getElementById('paperTrade').style.display = mode === 'paper' ? '' : 'none';
  });
});

// =============================================================================
// CHART.JS SETUP (no zoom plugin — using custom range slider)
// =============================================================================
const ctx = document.getElementById('chart').getContext('2d');
const chart = new Chart(ctx, {
  type: 'line',
  data: {
    datasets: [
      { label: 'Spread %', data: [], borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.06)', borderWidth: 1.5, tension: 0.1, pointRadius: 0, pointHitRadius: 4, fill: true, order: 2 },
      { label: 'Mean', data: [], borderColor: '#f59e0b', borderWidth: 1, borderDash: [4, 4], pointRadius: 0, fill: false, order: 3 },
      { label: '+2σ', data: [], borderColor: 'rgba(239,68,68,0.4)', borderWidth: 1, borderDash: [3, 4], pointRadius: 0, fill: false, order: 4 },
      { label: '-2σ', data: [], borderColor: 'rgba(239,68,68,0.4)', borderWidth: 1, borderDash: [3, 4], pointRadius: 0, fill: false, order: 4 },
    ]
  },
  options: {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: 'index', intersect: false },
    animation: { duration: 0 },
    plugins: {
      legend: {
        display: true,
        position: 'top',
        align: 'end',
        labels: {
          boxWidth: 10,
          boxHeight: 2,
          padding: 12,
          color: '#4a5068',
          font: { family: "'JetBrains Mono', monospace", size: 10 }
        }
      },
      tooltip: {
        backgroundColor: '#0d0f17',
        borderColor: 'rgba(255,255,255,0.08)',
        borderWidth: 1,
        titleColor: '#8b92a8',
        bodyColor: '#e8eaf0',
        titleFont: { family: "'JetBrains Mono', monospace", size: 10 },
        bodyFont: { family: "'JetBrains Mono', monospace", size: 11 },
        padding: { top: 8, bottom: 8, left: 12, right: 12 },
        displayColors: true,
        callbacks: {
          title: (items) => {
            if (!items.length) return '';
            const t = items[0].parsed.x;
            return fmtDate(t);
          },
          label: (ctx) => {
            const v = ctx.parsed.y;
            if (v === null || v === undefined) return ctx.dataset.label + ': —';
            return ctx.dataset.label + ': ' + v.toFixed(4) + (state.chartMode === 'zscore' ? 'σ' : '%');
          }
        }
      }
    },
    scales: {
      x: {
        type: 'linear',
        grid: { color: 'rgba(255,255,255,0.03)' },
        ticks: {
          color: '#4a5068',
          font: { family: "'JetBrains Mono', monospace", size: 9 },
          maxRotation: 0,
          autoSkip: true,
          maxTicksLimit: 8,
          callback: (v) => {
            const d = new Date(v);
            return d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' });
          }
        },
        border: { display: false }
      },
      y: {
        grid: { color: 'rgba(255,255,255,0.03)' },
        ticks: {
          color: '#4a5068',
          font: { family: "'JetBrains Mono', monospace", size: 9 },
          callback: (v) => v.toFixed(2)
        },
        border: { display: false }
      }
    }
  }
});

// =============================================================================
// CHART UPDATE
// =============================================================================
function updateChart() {
  const data = state.historicalData;
  if (!data.length) return;

  // Apply range slider filtering
  const start = Math.max(0, state.rangeStart);
  const end = Math.min(data.length - 1, state.rangeEnd);
  const slice = data.slice(start, end + 1);

  if (state.chartMode === 'zscore') {
    // Z-Score mode
    const zData = state.zscoreData;
    if (!zData.length) return;
    const zStart = Math.floor(start * (zData.length / data.length));
    const zEnd = Math.floor(end * (zData.length / data.length));
    const zSlice = zData.slice(zStart, zEnd + 1);

    chart.data.datasets[0].data = zSlice.map(r => ({ x: r.timestamp_ms, y: r.zscore }));
    chart.data.datasets[0].label = 'Z-Score';
    chart.data.datasets[0].borderColor = '#a855f7';
    chart.data.datasets[0].backgroundColor = 'rgba(168,85,247,0.06)';
    chart.data.datasets[1].data = zSlice.map(r => ({ x: r.timestamp_ms, y: 0 }));
    chart.data.datasets[1].label = 'Mean';
    chart.data.datasets[1].borderColor = '#f59e0b';
    chart.data.datasets[2].data = zSlice.map(r => ({ x: r.timestamp_ms, y: 2 }));
    chart.data.datasets[2].label = '+2σ';
    chart.data.datasets[3].data = zSlice.map(r => ({ x: r.timestamp_ms, y: -2 }));
    chart.data.datasets[3].label = '-2σ';
    chart.options.scales.y.ticks.callback = (v) => v.toFixed(1) + 'σ';
  } else {
    // Spread % mode (default)
    chart.data.datasets[0].data = slice.map(r => ({ x: r.timestamp_ms, y: r.spread_pct }));
    chart.data.datasets[0].label = 'Spread %';
    chart.data.datasets[0].borderColor = '#22c55e';
    chart.data.datasets[0].backgroundColor = 'rgba(34,197,94,0.06)';
    chart.data.datasets[1].data = slice.map(r => ({ x: r.timestamp_ms, y: r.mean }));
    chart.data.datasets[1].label = 'Mean';
    chart.data.datasets[1].borderColor = '#f59e0b';
    chart.data.datasets[2].data = slice.map(r => ({ x: r.timestamp_ms, y: r.plus_2sigma }));
    chart.data.datasets[2].label = '+2σ';
    chart.data.datasets[3].data = slice.map(r => ({ x: r.timestamp_ms, y: r.minus_2sigma }));
    chart.options.scales.y.ticks.callback = (v) => v.toFixed(2) + '%';
  }

  chart.update('none');
}

// =============================================================================
// RANGE SLIDER
// =============================================================================
const rangeTrack = document.getElementById('rangeTrack');
const rangeFill = document.getElementById('rangeFill');
const rangeHandleL = document.getElementById('rangeHandleL');
const rangeHandleR = document.getElementById('rangeHandleR');
const rangeCount = document.getElementById('rangeCount');
const rangeStartLabel = document.getElementById('rangeStart');
const rangeEndLabel = document.getElementById('rangeEnd');

const VISIBLE_DEFAULT = 200; // default visible points

function initRangeSlider() {
  const data = state.historicalData;
  if (!data.length) return;
  state.rangeEnd = data.length - 1;
  state.rangeStart = 0;  // show full chart
  updateSliderUI();
}

function updateSliderUI() {
  const data = state.historicalData;
  if (!data.length) return;
  const total = data.length;
  const leftPct = (state.rangeStart / total) * 100;
  const rightPct = (state.rangeEnd / total) * 100;

  rangeFill.style.left = leftPct + '%';
  rangeFill.style.width = (rightPct - leftPct) + '%';
  rangeHandleL.style.left = leftPct + '%';
  rangeHandleR.style.left = rightPct + '%';

  const visible = state.rangeEnd - state.rangeStart + 1;
  rangeCount.textContent = `${visible} / ${total}`;

  if (data[state.rangeStart]) rangeStartLabel.textContent = fmtDate(data[state.rangeStart].timestamp_ms);
  if (data[state.rangeEnd]) rangeEndLabel.textContent = fmtDate(data[state.rangeEnd].timestamp_ms);
}

function onTrackClick(e) {
  const rect = rangeTrack.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * state.historicalData.length);
  const mid = (state.rangeStart + state.rangeEnd) / 2;
  if (idx < mid) {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(state.historicalData.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  updateSliderUI();
  updateChart();
}

function onMouseMove(e) {
  if (!state.isDragging || !state.historicalData.length) return;
  const rect = rangeTrack.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * state.historicalData.length);

  if (state.isDragging === 'left') {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(state.historicalData.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  updateSliderUI();
  updateChart();
}

function onMouseUp() {
  state.isDragging = null;
  rangeHandleL.classList.remove('dragging');
  rangeHandleR.classList.remove('dragging');
  document.removeEventListener('mousemove', onMouseMove);
  document.removeEventListener('mouseup', onMouseUp);
}

rangeHandleL.addEventListener('mousedown', (e) => {
  e.stopPropagation();
  state.isDragging = 'left';
  rangeHandleL.classList.add('dragging');
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
});

rangeHandleR.addEventListener('mousedown', (e) => {
  e.stopPropagation();
  state.isDragging = 'right';
  rangeHandleR.classList.add('dragging');
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', onMouseUp);
});

rangeTrack.addEventListener('click', onTrackClick);

// Touch support
rangeHandleL.addEventListener('touchstart', (e) => {
  e.stopPropagation();
  state.isDragging = 'left';
  document.addEventListener('touchmove', onTouchMove, { passive: false });
  document.addEventListener('touchend', onTouchEnd);
}, { passive: false });

rangeHandleR.addEventListener('touchstart', (e) => {
  e.stopPropagation();
  state.isDragging = 'right';
  document.addEventListener('touchmove', onTouchMove, { passive: false });
  document.addEventListener('touchend', onTouchEnd);
}, { passive: false });

function onTouchMove(e) {
  e.preventDefault();
  if (!state.isDragging || !state.historicalData.length) return;
  const rect = rangeTrack.getBoundingClientRect();
  const touch = e.touches[0];
  const pct = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * state.historicalData.length);
  if (state.isDragging === 'left') {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(state.historicalData.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  updateSliderUI();
  updateChart();
}

function onTouchEnd() {
  state.isDragging = null;
  document.removeEventListener('touchmove', onTouchMove);
  document.removeEventListener('touchend', onTouchEnd);
}

// =============================================================================
// KPI UPDATE
// =============================================================================
function updateKPIs() {
  const d = state.currentData;
  if (!d) return;

  const moex = d.moex || {};
  const hl = d.hyperliquid || {};

  // MOEX
  document.getElementById('kpiMoexMid').textContent = moex.mid ? fmt$(moex.mid) : '—';
  document.getElementById('kpiMoexBid').textContent = moex.best_bid ? 'bid ' + moex.best_bid.toFixed(2) : 'bid —';
  document.getElementById('kpiMoexAsk').textContent = moex.best_ask ? 'ask ' + moex.best_ask.toFixed(2) : 'ask —';

  // HL
  document.getElementById('kpiHlMid').textContent = hl.mid ? fmt$(hl.mid) : '—';
  document.getElementById('kpiHlBid').textContent = hl.best_bid ? 'bid ' + hl.best_bid.toFixed(2) : 'bid —';
  document.getElementById('kpiHlAsk').textContent = hl.best_ask ? 'ask ' + hl.best_ask.toFixed(2) : 'ask —';

  // Spread
  const sp = d.current_spread_pct;
  const spVal = document.getElementById('kpiSpreadPct');
  spVal.textContent = sp !== null ? fmtPct(sp) : '—';
  spVal.className = 'kpi-value ' + (sp < 0 ? 'negative' : sp > 0 ? 'positive' : '');
  document.getElementById('kpiSpread$').textContent = d.arb_spread !== null ? fmt$(d.arb_spread) : '—';

  // Arb Spread
  document.getElementById('kpiArb').textContent = d.arb_spread !== null ? fmtN(d.arb_spread, 3) : '—';
  document.getElementById('kpiArbDir').textContent = d.arb_direction || '—';

  // Ticks
  state.tickCount++;
  document.getElementById('kpiTicks').textContent = state.tickCount;
  if (!state.sessionStart) state.sessionStart = Date.now();
  const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
  const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
  const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
  const s = (elapsed % 60).toString().padStart(2, '0');
  document.getElementById('kpiSession').textContent = `сессия ${h}:${m}:${s}`;
}

function updateStats() {
  const s = state.stats;
  if (!s || s.avg === null) return;

  document.getElementById('kpiMedian').textContent = fmtPct(s.median);
  document.getElementById('kpiMedian').className = 'kpi-value ' + (s.median < 0 ? 'negative' : '');
  document.getElementById('kpiMedian$').textContent = '$ ' + fmtN(s.median / 100 * (state.currentData?.moex?.mid || 95), 3);

  document.getElementById('kpiMinMax').textContent = fmtPct(s.min) + ' / ' + fmtPct(s.max);
  document.getElementById('kpiMinMax$').textContent = '$' + fmtN(s.min / 100 * (state.currentData?.moex?.mid || 95), 2) + ' / $' + fmtN(s.max / 100 * (state.currentData?.moex?.mid || 95), 2);

  // Live Z-Score
  const z = state.signalData;
  if (z && z.zscore !== null) {
    document.getElementById('kpiZscore').textContent = fmtN(z.zscore, 2) + 'σ';
    if (Math.abs(z.zscore) >= 2) {
      document.getElementById('kpiZscoreStatus').style.color = '#ef4444';
      document.getElementById('kpiZscoreText').textContent = 'СИГНАЛ!';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#ef4444';
    } else if (Math.abs(z.zscore) >= 1.5) {
      document.getElementById('kpiZscoreStatus').style.color = '#f59e0b';
      document.getElementById('kpiZscoreText').textContent = 'Внимание: |Z| > 1.5';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#f59e0b';
    } else {
      document.getElementById('kpiZscoreStatus').style.color = '#22c55e';
      document.getElementById('kpiZscoreText').textContent = 'В пределах нормы';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#22c55e';
    }
  }
}

function updateSignal() {
  const sig = state.signalData;
  if (!sig || !sig.signal) return;
  const badge = document.getElementById('kpiEntrySignal');

  badge.className = 'entry-badge ' + sig.signal;
  let text = 'НЕТ СИГНАЛА';
  if (sig.signal === 'buy') text = 'BUY: Spread < -2σ';
  else if (sig.signal === 'sell') text = 'SELL: Spread > +2σ';
  else if (sig.signal === 'watch') text = 'ВНИМАНИЕ: |Z| > 1.5';
  badge.innerHTML = `<span class="signal-dot"></span>${text}`;
}

// =============================================================================
// TABLE UPDATE
// =============================================================================
function updateTable() {
  const ticks = state.ticks;
  const tbody = document.getElementById('tickTableBody');
  document.getElementById('tickCount').textContent = (ticks.length || 0) + ' записей';

  if (!ticks.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading">Нет данных</td></tr>';
    return;
  }

  tbody.innerHTML = ticks.slice(0, 20).map(t => {
    const zClass = t.zscore === null ? '' : Math.abs(t.zscore) >= 2 ? 'style="color:#ef4444;font-weight:600;"' : Math.abs(t.zscore) >= 1.5 ? 'style="color:#f59e0b;"' : '';
    return `<tr>
      <td>${fmtTs(t.timestamp_ms)}</td>
      <td>${fmtN(t.moex_mid, 2)}</td>
      <td>${fmtN(t.hl_mid, 2)}</td>
      <td class="${t.spread < 0 ? 'negative' : ''}">${fmtN(t.spread, 3)}</td>
      <td class="${t.spread_pct < 0 ? 'negative' : ''}">${fmtPct(t.spread_pct)}</td>
      <td ${zClass}>${t.zscore !== null ? fmtN(t.zscore, 2) + 'σ' : '—'}</td>
    </tr>`;
  }).join('');
}

// =============================================================================
// REFRESH
// =============================================================================
async function refreshAll() {
  if (!state.activeContract) return;
  const cid = state.activeContract;
  const tf = state.activeTf;

  try {
    // Fetch in parallel — use allSettled so one slow endpoint doesn't block everything
    const [histR, curR, zscR, statR, sigR, ticksR] = await Promise.allSettled([
      api(`/api/historical/${cid}/${tf}`),
      api(`/api/current/${cid}`),
      api(`/api/zscore/${cid}/${tf}`),
      api(`/api/stats/${cid}/${tf}`),
      api(`/api/signal/${cid}`),
      api(`/api/ticks/${cid}?limit=50`),
    ]);

    if (histR.status === 'fulfilled') state.historicalData = histR.value;
    if (curR.status === 'fulfilled') state.currentData = curR.value;
    if (zscR.status === 'fulfilled') state.zscoreData = zscR.value;
    if (statR.status === 'fulfilled') state.stats = statR.value;
    if (sigR.status === 'fulfilled') state.signalData = sigR.value;
    if (ticksR.status === 'fulfilled') state.ticks = ticksR.value;

    updateKPIs();
    updateStats();
    updateSignal();
    updateChart();
    updateSliderUI();
    updateTable();

    // Log any rejections for debugging
    [histR, curR, zscR, statR, sigR, ticksR].forEach((r, i) => {
      if (r.status === 'rejected') console.error('Refresh partial fail:', i, r.reason);
    });
  } catch (e) {
    console.error('Refresh failed:', e);
  }
}

// =============================================================================
// DEMO DATA (fallback when backend is not running)
// =============================================================================
function generateDemoData() {
  const now = Date.now();
  const HIST_SIZE = 611;
  const hist = [];
  let spreadBase = -5.5;
  const avg = -4.5;
  const sd = 1.5;

  for (let i = 0; i < HIST_SIZE; i++) {
    const ts = now - (HIST_SIZE - i) * 120000; // 2 min intervals
    // Random walk spread
    spreadBase += (Math.random() - 0.5) * 0.3;
    spreadBase = Math.max(-12, Math.min(2, spreadBase)); // clamp
    const sp = spreadBase;
    hist.push({
      timestamp_ms: ts,
      spread_pct: Math.round(sp * 1000) / 1000,
      mean: Math.round(avg * 1000) / 1000,
      plus_2sigma: Math.round((avg + 2 * sd) * 1000) / 1000,
      minus_2sigma: Math.round((avg - 2 * sd) * 1000) / 1000,
    });
  }

  // Z-Score data
  const zData = [];
  for (let i = 0; i < HIST_SIZE; i++) {
    const ts = now - (HIST_SIZE - i) * 120000;
    const z = (hist[i].spread_pct - avg) / sd;
    zData.push({ timestamp_ms: ts, zscore: Math.round(z * 1000) / 1000 });
  }

  // Stats
  const stats = {
    avg: avg,
    median: -4.55,
    stddev: sd,
    min: -12.70,
    max: 1.74,
    entry_low: Math.round((avg - 2 * sd) * 1000) / 1000,
    entry_high: Math.round((avg + 2 * sd) * 1000) / 1000,
  };

  // Signal
  const currentZ = zData[zData.length - 1].zscore;
  let signal = 'neutral';
  if (currentZ <= -2) signal = 'buy';
  else if (currentZ >= 2) signal = 'sell';
  else if (Math.abs(currentZ) >= 1.5) signal = 'watch';

  const sig = {
    signal: signal,
    zscore: currentZ,
    description: signal === 'buy' ? 'BUY: Spread < -2σ' : signal === 'sell' ? 'SELL: Spread > +2σ' : signal === 'watch' ? 'Внимание: |Z| > 1.5' : 'В пределах нормы',
    current_spread_pct: hist[hist.length - 1].spread_pct,
    avg: avg,
    entry_low: stats.entry_low,
    entry_high: stats.entry_high,
  };

  // Current prices
  const cur = {
    moex: { best_bid: 96.218, best_ask: 96.240, last_price: 96.225, mid: 96.229, is_orderbook: false },
    hyperliquid: { best_bid: 90.916, best_ask: 90.929, last_price: 90.922, mid: 90.922, is_l2: true },
    current_spread_pct: hist[hist.length - 1].spread_pct,
    arb_spread: -5.324,
    arb_direction: 'Sell HL → Buy MOEX',
    updated_ms: now,
  };

  // Ticks
  const ticks = [];
  for (let i = 0; i < 50; i++) {
    ticks.push({
      timestamp_ms: now - i * 2000,
      moex_mid: 96.22 + (Math.random() - 0.5) * 0.1,
      hl_mid: 90.92 + (Math.random() - 0.5) * 0.1,
      spread: -5.30 + (Math.random() - 0.5) * 0.1,
      spread_pct: -5.51 + (Math.random() - 0.5) * 0.1,
      zscore: currentZ + (Math.random() - 0.5) * 0.1,
    });
  }

  return { hist, zData, stats, sig, cur, ticks };
}

// =============================================================================
// INIT
// =============================================================================
async function init() {
  if (!state.sessionStart) state.sessionStart = Date.now();

  // Try to load from API, fallback to demo data
  try {
    await loadContracts();
    await refreshAll();
    initRangeSlider();
  } catch (e) {
    console.log('Backend not available, using demo data');
    useDemoData();
  }

  // Start polling every 2 seconds (will keep trying API)
  state.pollInterval = setInterval(async () => {
    try { await refreshAll(); }
    catch (e) { /* silent fail, keep showing last data */ }
  }, 2000);

  // Session timer
  setInterval(() => {
    if (state.sessionStart) {
      const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
      const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
      const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
      const s = (elapsed % 60).toString().padStart(2, '0');
      const el = document.getElementById('kpiSession');
      if (el) el.textContent = `сессия ${h}:${m}:${s}`;
    }
  }, 1000);
}

function useDemoData() {
  const demo = generateDemoData();
  state.contracts = [
    { id: 'bmm6', name: 'BMM6', moex_symbol: 'BMM6@RTSX', hl_coin: 'xyz:BRENTOIL', is_active: 1 },
    { id: 'bmk6', name: 'BMK6', moex_symbol: 'BMK6@RTSX', hl_coin: 'xyz:BRENTOIL', is_active: 1 },
  ];
  if (!state.activeContract) state.activeContract = 'bmm6';
  state.historicalData = demo.hist;
  state.zscoreData = demo.zData;
  state.stats = demo.stats;
  state.signalData = demo.sig;
  state.currentData = demo.cur;
  state.ticks = demo.ticks;

  renderContractTabs();
  const c = state.contracts.find(x => x.id === state.activeContract);
  document.getElementById('logoContract').textContent = '/ ' + (c?.name || 'BMM6');
  document.getElementById('kpiMoexName').textContent = c?.name || 'BMM6';

  updateKPIs();
  updateStats();
  updateSignal();
  initRangeSlider();
  updateChart();
  updateSliderUI();
  updateTable();
}

init();
