/**
 * Brent Spread Dashboard - Multi-Contract Frontend
 * Ocean Theme | Mission Control Layout
 */

// =============================================================================
// STATE
// =============================================================================
const state = {
  assets: {},           // config from /api/assets
  contracts: [],        // all contracts from /api/contracts
  activeAsset: 'brent', // current asset: brent | gold | silver
  activeContract: null, // current contract id
  activeTf: '15m',
  chartMode: 'spread%',
  // Per-asset+contract cache: key = "contractId|tf"
  cache: {},
  tickCount: 0,
  sessionStart: null,
  // Range slider state (per contract)
  rangeState: {},       // key = contractId, value = { rangeStart, rangeEnd }
  isDragging: null,
  pollInterval: null,
  isOnline: true,
  consecutiveFails: 0,
};

// Cache helpers
function cacheKey(contractId, tf) { return contractId + '|' + tf; }
function getCache(contractId, tf) {
  return state.cache[cacheKey(contractId, tf)] || null;
}
function setCache(contractId, tf, data) {
  state.cache[cacheKey(contractId, tf)] = data;
}
function getCurrentCache() {
  if (!state.activeContract) return null;
  return getCache(state.activeContract, state.activeTf);
}
function getCurrentData(field) {
  const c = getCurrentCache();
  return c ? c[field] : null;
}
function setCurrentData(data) {
  if (!state.activeContract) return;
  setCache(state.activeContract, state.activeTf, data);
}

// Range helpers
function getRangeState(contractId) {
  return state.rangeState[contractId] || { rangeStart: 0, rangeEnd: 0 };
}
function setRangeState(contractId, rs, re) {
  state.rangeState[contractId] = { rangeStart: rs, rangeEnd: re };
}
function getCurrentRange() {
  if (!state.activeContract) return { rangeStart: 0, rangeEnd: 0 };
  return getRangeState(state.activeContract);
}

// =============================================================================
// UTILS
// =============================================================================
function fmt$(v) {
  if (v === null || v === undefined || isNaN(v)) return '-';
  const s = v < 0 ? '-' : '';
  return s + '$' + Math.abs(v).toFixed(3);
}
function fmtPct(v) {
  if (v === null || v === undefined || isNaN(v)) return '-';
  return (v >= 0 ? '+' : '') + v.toFixed(3) + '%';
}
function fmtN(v, d = 2) {
  if (v === null || v === undefined || isNaN(v)) return '-';
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
async function api(path, retries = 2) {
  const timeoutMs = 8000;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), timeoutMs);
      const r = await fetch(path, {
        signal: controller.signal,
        cache: 'no-store',
      });
      clearTimeout(timer);
      if (!r.ok) throw new Error(`${path}: ${r.status}`);
      return r.json();
    } catch (err) {
      if (attempt === retries) throw err;
      // Exponential backoff: 300ms, 600ms
      await new Promise(res => setTimeout(res, 300 * (attempt + 1)));
    }
  }
}

async function loadAssets() {
  state.assets = await api('/api/assets');
}

function getContractsForAsset(assetId) {
  return state.contracts.filter(c => c.asset === assetId);
}

async function loadContracts() {
  state.contracts = await api('/api/contracts');
  renderAssetTabs();
  renderContractTabs();
  if (!state.activeContract) {
    const assetContracts = getContractsForAsset(state.activeAsset);
    const active = assetContracts.find(c => c.is_active) || assetContracts[0];
    if (active) setActiveContract(active.id);
  }
}

function setActiveAsset(assetId) {
  state.activeAsset = assetId;
  const asset = state.assets[assetId] || {};
  // Update logo text dynamically
  const logoAsset = document.getElementById('logoAsset');
  if (logoAsset) {
    logoAsset.textContent = (asset.name || assetId).toUpperCase() + ' SPREAD';
  }
  renderAssetTabs();
  renderContractTabs();
  // Pick first contract of this asset
  const assetContracts = getContractsForAsset(assetId);
  const active = assetContracts.find(c => c.is_active) || assetContracts[0];
  if (active) {
    setActiveContract(active.id);
  } else {
    // No contracts for this asset yet
    state.activeContract = null;
    document.getElementById('logoContract').textContent = '/ -';
    document.getElementById('kpiMoexName').textContent = '-';
  }
}

function setActiveContract(id) {
  state.activeContract = id;
  const c = state.contracts.find(x => x.id === id);
  const asset = state.assets[state.activeAsset] || {};
  document.getElementById('logoContract').textContent = '/ ' + (c?.name || id.toUpperCase());
  document.getElementById('kpiMoexName').textContent = c?.name || id.toUpperCase();
  document.getElementById('kpiMoexUnit').textContent = asset.unit || 'USD';
  document.getElementById('kpiHlName').textContent = 'Hyperliquid ' + (asset.name || '');
  renderContractTabs();

  // Restore cached range for this contract
  const rs = getRangeState(id);
  state.rangeStart = rs.rangeStart;
  state.rangeEnd = rs.rangeEnd;

  // If we have cached data for this contract+tf, use it immediately
  const cached = getCurrentCache();
  if (cached) {
    updateKPIs();
    updateStats();
    updateSignal();
    updateChart();
    updateSliderUI();
    updateTable();
  }

  refreshAll().then(() => {
    initRangeSlider();
    updateChart();
    updateSliderUI();
  });
}

// =============================================================================
// ASSET TABS
// =============================================================================
function renderAssetTabs() {
  const el = document.getElementById("assetTabs");
  if (!el) return;
  el.innerHTML = "";
  const icons = { brent: "\u26F3", gold: "\u25C6", silver: "\u25C8" };
  Object.entries(state.assets).forEach(([key, asset]) => {
    const btn = document.createElement("button");
    btn.className = "asset-tab" + (key === state.activeAsset ? " active" : "") + " " + key;
    btn.innerHTML = "<span class=\"asset-icon\">" + (icons[key] || "\u25C8") + "</span>" + (asset.name || key.toUpperCase());
    btn.onclick = () => setActiveAsset(key);
    el.appendChild(btn);
  });
}

// // CONTRACT TABS// CONTRACT TABS
// =============================================================================
function renderContractTabs() {
  const el = document.getElementById('contractTabs');
  el.innerHTML = '';
  const assetContracts = getContractsForAsset(state.activeAsset);
  assetContracts.forEach(c => {
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
  addBtn.textContent = '+ Add';
  addBtn.onclick = showAddContractModal;
  el.appendChild(addBtn);
}

function showAddContractModal() {
  // Simple prompt-based for now
  const id = prompt('ID contracta (example: bmm7):');
  if (!id) return;
  const name = prompt('Name (example: BMM7):') || id.toUpperCase();
  const moex = prompt('MOEX symbol (example: BMM7@RTSX):');
  if (!moex) return;
  const hl = prompt('HL ticker (example: xyz:BRENTOIL):') || 'xyz:BRENTOIL';

  fetch(`/api/contracts?contract_id=${encodeURIComponent(id)}&name=${encodeURIComponent(name)}&moex_symbol=${encodeURIComponent(moex)}&hl_coin=${encodeURIComponent(hl)}`, { method: 'POST' })
    .then(r => r.json())
    .then(() => loadContracts())
    .catch(e => alert('Error: ' + e.message));
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
// CHART.JS SETUP (no zoom plugin - using custom range slider)
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
            if (v === null || v === undefined) return ctx.dataset.label + ': -';
            if (state.chartMode === 'zscore') return ctx.dataset.label + ': ' + v.toFixed(4) + 'σ';
            if (state.chartMode === 'prices') return ctx.dataset.label + ': $' + v.toFixed(2);
            return ctx.dataset.label + ': ' + v.toFixed(4) + '%';
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
  const cache = getCurrentCache();
  if (!cache) return;
  const data = cache.historicalData;
  if (!data || !data.length) return;

  // Apply range slider filtering
  const start = Math.max(0, state.rangeStart);
  const end = Math.min(data.length - 1, state.rangeEnd);
  const slice = data.slice(start, end + 1);

  if (state.chartMode === 'zscore') {
    // Z-Score mode
    const zData = cache.zscoreData;
    if (!zData || !zData.length) return;
    const zStart = Math.floor(start * (zData.length / data.length));
    const zEnd = Math.floor(end * (zData.length / data.length));
    const zSlice = zData.slice(zStart, zEnd + 1);

    chart.data.datasets[0].data = zSlice.map(r => ({ x: r.timestamp_ms, y: r.zscore }));
    chart.data.datasets[0].label = 'Z-Score';
    chart.data.datasets[0].borderColor = '#a855f7';
    chart.data.datasets[0].backgroundColor = 'rgba(168,85,247,0.06)';
    chart.data.datasets[0].fill = true;
    chart.data.datasets[1].data = zSlice.map(r => ({ x: r.timestamp_ms, y: 0 }));
    chart.data.datasets[1].label = 'Mean';
    chart.data.datasets[1].borderColor = '#f59e0b';
    chart.data.datasets[1].borderDash = [4, 4];
    chart.data.datasets[1].fill = false;
    chart.data.datasets[2].data = zSlice.map(r => ({ x: r.timestamp_ms, y: 2 }));
    chart.data.datasets[2].label = '+2σ';
    chart.data.datasets[2].borderColor = 'rgba(239,68,68,0.4)';
    chart.data.datasets[2].borderDash = [3, 4];
    chart.data.datasets[2].fill = false;
    chart.data.datasets[3].data = zSlice.map(r => ({ x: r.timestamp_ms, y: -2 }));
    chart.data.datasets[3].label = '-2σ';
    chart.data.datasets[3].borderColor = 'rgba(239,68,68,0.4)';
    chart.data.datasets[3].borderDash = [3, 4];
    chart.data.datasets[3].fill = false;
    chart.options.scales.y.ticks.callback = (v) => v.toFixed(1) + 'σ';
  } else if (state.chartMode === 'prices') {
    // Prices mode - raw MOEX and HL close prices
    const pData = cache.pricesData;
    if (!pData || !pData.length) return;
    const pStart = Math.floor(start * (pData.length / data.length));
    const pEnd = Math.floor(end * (pData.length / data.length));
    const pSlice = pData.slice(pStart, pEnd + 1);

    chart.data.datasets[0].data = pSlice.map(r => ({ x: r.timestamp_ms, y: r.moex_close }));
    chart.data.datasets[0].label = 'MOEX Close';
    chart.data.datasets[0].borderColor = '#3b82f6';
    chart.data.datasets[0].backgroundColor = 'rgba(59,130,246,0.06)';
    chart.data.datasets[0].fill = true;
    chart.data.datasets[1].data = pSlice.map(r => ({ x: r.timestamp_ms, y: r.hl_close }));
    chart.data.datasets[1].label = 'HL Close';
    chart.data.datasets[1].borderColor = '#f97316';
    chart.data.datasets[1].backgroundColor = 'rgba(249,115,22,0.06)';
    chart.data.datasets[1].borderDash = [];
    chart.data.datasets[1].fill = true;
    chart.data.datasets[2].data = [];
    chart.data.datasets[2].label = '';
    chart.data.datasets[3].data = [];
    chart.data.datasets[3].label = '';
    chart.options.scales.y.ticks.callback = (v) => '$' + v.toFixed(2);
  } else {
    // Spread % mode (default)
    chart.data.datasets[0].data = slice.map(r => ({ x: r.timestamp_ms, y: r.spread_pct }));
    chart.data.datasets[0].label = 'Spread %';
    chart.data.datasets[0].borderColor = '#22c55e';
    chart.data.datasets[0].backgroundColor = 'rgba(34,197,94,0.06)';
    chart.data.datasets[0].fill = true;
    chart.data.datasets[1].data = slice.map(r => ({ x: r.timestamp_ms, y: r.mean }));
    chart.data.datasets[1].label = 'Mean';
    chart.data.datasets[1].borderColor = '#f59e0b';
    chart.data.datasets[1].borderDash = [4, 4];
    chart.data.datasets[1].fill = false;
    chart.data.datasets[2].data = slice.map(r => ({ x: r.timestamp_ms, y: r.plus_2sigma }));
    chart.data.datasets[2].label = '+2σ';
    chart.data.datasets[2].borderColor = 'rgba(239,68,68,0.4)';
    chart.data.datasets[2].borderDash = [3, 4];
    chart.data.datasets[2].fill = false;
    chart.data.datasets[3].data = slice.map(r => ({ x: r.timestamp_ms, y: r.minus_2sigma }));
    chart.data.datasets[3].label = '-2σ';
    chart.data.datasets[3].borderColor = 'rgba(239,68,68,0.4)';
    chart.data.datasets[3].borderDash = [3, 4];
    chart.data.datasets[3].fill = false;
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

const MIN_VISIBLE_POINTS = 96;  // ~1 day on 15m (96 candles)
const ZOOM_STEP_RATIO = 0.15;   // 15% per wheel tick

function initRangeSlider() {
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!data || !data.length) return;
  const rs = getRangeState(state.activeContract);
  // If range was never set for this contract, default to full view
  if (rs.rangeEnd === 0 && rs.rangeStart === 0) {
    state.rangeEnd = data.length - 1;
    state.rangeStart = 0;
    setRangeState(state.activeContract, 0, data.length - 1);
  } else {
    state.rangeStart = rs.rangeStart;
    state.rangeEnd = Math.min(data.length - 1, rs.rangeEnd);
    setRangeState(state.activeContract, state.rangeStart, state.rangeEnd);
  }
  updateSliderUI();
}

// =============================================================================
// CHART ZOOM (mouse wheel)
// =============================================================================
function onChartWheel(e) {
  e.preventDefault();
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!data || !data.length) return;

  const total = data.length;
  const currentVisible = state.rangeEnd - state.rangeStart + 1;

  // deltaY > 0 = scroll down (zoom out), deltaY < 0 = scroll up (zoom in)
  // User choice 1A: scroll UP = zoom IN (less days visible)
  const zoomIn = e.deltaY < 0;

  let newVisible;
  if (zoomIn) {
    newVisible = Math.max(MIN_VISIBLE_POINTS, Math.floor(currentVisible * (1 - ZOOM_STEP_RATIO)));
  } else {
    newVisible = Math.min(total, Math.ceil(currentVisible * (1 + ZOOM_STEP_RATIO)));
  }

  // Right edge fixed (user choice 2B)
  state.rangeEnd = total - 1;
  state.rangeStart = Math.max(0, state.rangeEnd - newVisible + 1);
  setRangeState(state.activeContract, state.rangeStart, state.rangeEnd);

  updateSliderUI();
  updateChart();
}

function updateSliderUI() {
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!data || !data.length) return;
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
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!data || !data.length) return;
  const rect = rangeTrack.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * data.length);
  const mid = (state.rangeStart + state.rangeEnd) / 2;
  if (idx < mid) {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(data.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  setRangeState(state.activeContract, state.rangeStart, state.rangeEnd);
  updateSliderUI();
  updateChart();
}

function onMouseMove(e) {
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!state.isDragging || !data || !data.length) return;
  const rect = rangeTrack.getBoundingClientRect();
  const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * data.length);

  if (state.isDragging === 'left') {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(data.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  setRangeState(state.activeContract, state.rangeStart, state.rangeEnd);
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

// Attach wheel zoom to chart canvas wrapper
document.querySelector('.chart-wrap').addEventListener('wheel', onChartWheel, { passive: false });

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
  const cache = getCurrentCache();
  const data = cache ? cache.historicalData : [];
  if (!state.isDragging || !data || !data.length) return;
  const rect = rangeTrack.getBoundingClientRect();
  const touch = e.touches[0];
  const pct = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));
  const idx = Math.round(pct * data.length);
  if (state.isDragging === 'left') {
    state.rangeStart = Math.max(0, Math.min(state.rangeEnd - 20, idx));
  } else {
    state.rangeEnd = Math.min(data.length - 1, Math.max(state.rangeStart + 20, idx));
  }
  setRangeState(state.activeContract, state.rangeStart, state.rangeEnd);
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
  const d = getCurrentData('currentData');
  if (!d) return;

  const moex = d.moex || {};
  const hl = d.hyperliquid || {};

  // MOEX
  document.getElementById('kpiMoexMid').textContent = moex.mid ? fmt$(moex.mid) : '-';
  document.getElementById('kpiMoexBid').textContent = moex.best_bid ? 'bid ' + moex.best_bid.toFixed(2) : 'bid -';
  document.getElementById('kpiMoexAsk').textContent = moex.best_ask ? 'ask ' + moex.best_ask.toFixed(2) : 'ask -';

  // HL
  document.getElementById('kpiHlMid').textContent = hl.mid ? fmt$(hl.mid) : '-';
  document.getElementById('kpiHlBid').textContent = hl.best_bid ? 'bid ' + hl.best_bid.toFixed(2) : 'bid -';
  document.getElementById('kpiHlAsk').textContent = hl.best_ask ? 'ask ' + hl.best_ask.toFixed(2) : 'ask -';

  // Spread
  const sp = d.current_spread_pct;
  const spVal = document.getElementById('kpiSpreadPct');
  spVal.textContent = sp !== null ? fmtPct(sp) : '-';
  spVal.className = 'kpi-value ' + (sp < 0 ? 'negative' : sp > 0 ? 'positive' : '');
  document.getElementById('kpiSpread$').textContent = d.arb_spread !== null ? fmt$(d.arb_spread) : '-';

  // Arb Spread
  document.getElementById('kpiArb').textContent = d.arb_spread !== null ? fmtN(d.arb_spread, 3) : '-';
  document.getElementById('kpiArbDir').textContent = d.arb_direction || '-';

  // Ticks
  state.tickCount++;
  document.getElementById('kpiTicks').textContent = state.tickCount;
  if (!state.sessionStart) state.sessionStart = Date.now();
  const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
  const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
  const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
  const s = (elapsed % 60).toString().padStart(2, '0');
  document.getElementById('kpiSession').textContent = `session ${h}:${m}:${s}`;
}

function updateStats() {
  const cache = getCurrentCache();
  if (!cache) return;
  const s = cache.stats;
  if (!s || s.avg === null) return;

  const currentData = cache.currentData;

  document.getElementById('kpiMedian').textContent = fmtPct(s.median);
  document.getElementById('kpiMedian').className = 'kpi-value ' + (s.median < 0 ? 'negative' : '');
  document.getElementById('kpiMedian$').textContent = '$ ' + fmtN(s.median / 100 * (currentData?.moex?.mid || 95), 3);

  document.getElementById('kpiMinMax').textContent = fmtPct(s.min) + ' / ' + fmtPct(s.max);
  document.getElementById('kpiMinMax$').textContent = '$' + fmtN(s.min / 100 * (currentData?.moex?.mid || 95), 2) + ' / $' + fmtN(s.max / 100 * (currentData?.moex?.mid || 95), 2);

  // Live Z-Score
  const z = cache.signalData;
  if (z && z.zscore !== null) {
    document.getElementById('kpiZscore').textContent = fmtN(z.zscore, 2) + 'σ';
    if (Math.abs(z.zscore) >= 2) {
      document.getElementById('kpiZscoreStatus').style.color = '#ef4444';
      document.getElementById('kpiZscoreText').textContent = 'SIGNAL!';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#ef4444';
    } else if (Math.abs(z.zscore) >= 1.5) {
      document.getElementById('kpiZscoreStatus').style.color = '#f59e0b';
      document.getElementById('kpiZscoreText').textContent = 'Attention: |Z| > 1.5';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#f59e0b';
    } else {
      document.getElementById('kpiZscoreStatus').style.color = '#22c55e';
      document.getElementById('kpiZscoreText').textContent = 'Within normal';
      document.getElementById('kpiZscoreStatus').querySelector('.status-dot').style.background = '#22c55e';
    }
  }
}

function updateSignal() {
  const cache = getCurrentCache();
  if (!cache) return;
  const sig = cache.signalData;
  if (!sig || !sig.signal) return;
  const badge = document.getElementById('kpiEntrySignal');

  badge.className = 'entry-badge ' + sig.signal;
  let text = 'NO SIGNAL';
  if (sig.signal === 'buy') text = 'BUY: Spread < -2σ';
  else if (sig.signal === 'sell') text = 'SELL: Spread > +2σ';
  else if (sig.signal === 'watch') text = 'WATCH: |Z| > 1.5';
  badge.innerHTML = `<span class="signal-dot"></span>${text}`;
}

// =============================================================================
// TABLE UPDATE
// =============================================================================
function updateTable() {
  const cache = getCurrentCache();
  const ticks = cache ? cache.ticks : [];
  const tbody = document.getElementById('tickTableBody');
  document.getElementById('tickCount').textContent = (ticks.length || 0) + ' records';

  if (!ticks.length) {
    tbody.innerHTML = '<tr><td colspan="6" class="loading">No data</td></tr>';
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
      <td ${zClass}>${t.zscore !== null ? fmtN(t.zscore, 2) + 'σ' : '-'}</td>
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
    // Fetch in parallel - use allSettled so one slow endpoint doesn't block everything
    const [histR, pricesR, curR, zscR, statR, sigR, ticksR] = await Promise.allSettled([
      api(`/api/historical/${cid}/${tf}`),
      api(`/api/prices/${cid}/${tf}`),
      api(`/api/current/${cid}`),
      api(`/api/zscore/${cid}/${tf}`),
      api(`/api/stats/${cid}/${tf}`),
      api(`/api/signal/${cid}`),
      api(`/api/ticks/${cid}?limit=50`),
    ]);

    // Build cache object for this contract+tf
    const cached = getCache(cid, tf) || {};
    const newCache = { ...cached };

    if (histR.status === 'fulfilled') newCache.historicalData = histR.value;
    if (pricesR.status === 'fulfilled') newCache.pricesData = pricesR.value;
    if (curR.status === 'fulfilled') newCache.currentData = curR.value;
    if (zscR.status === 'fulfilled') newCache.zscoreData = zscR.value;
    if (statR.status === 'fulfilled') newCache.stats = statR.value;
    if (sigR.status === 'fulfilled') newCache.signalData = sigR.value;
    if (ticksR.status === 'fulfilled') newCache.ticks = ticksR.value;

    setCache(cid, tf, newCache);

    // Only update UI if we're still on this contract+tf
    if (state.activeContract === cid && state.activeTf === tf) {
      updateKPIs();
      updateStats();
      updateSignal();
      updateChart();
      updateSliderUI();
      updateTable();
    }

    // Track connection health
    const failures = [histR, pricesR, curR, zscR, statR, sigR, ticksR].filter(r => r.status === 'rejected').length;
    if (failures === 7) {
      state.consecutiveFails++;
    } else {
      state.consecutiveFails = 0;
    }
    const wasOnline = state.isOnline;
    state.isOnline = state.consecutiveFails < 3;
    if (wasOnline !== state.isOnline) updateConnectionStatus();

    // Log any rejections for debugging
    [histR, pricesR, curR, zscR, statR, sigR, ticksR].forEach((r, i) => {
      if (r.status === 'rejected') console.error('Refresh partial fail:', i, r.reason);
    });
  } catch (e) {
    console.error('Refresh failed:', e);
    state.consecutiveFails++;
    const wasOnline = state.isOnline;
    state.isOnline = state.consecutiveFails < 3;
    if (wasOnline !== state.isOnline) updateConnectionStatus();
  }
}

function updateConnectionStatus() {
  const dot = document.querySelector('.live-dot');
  const text = document.querySelector('.live-text');
  if (!dot || !text) return;
  if (state.isOnline) {
    dot.style.background = '#22c55e';
    dot.style.boxShadow = '0 0 6px #22c55e';
    text.textContent = 'LIVE';
    text.style.color = '#22c55e';
  } else {
    dot.style.background = '#ef4444';
    dot.style.boxShadow = '0 0 6px #ef4444';
    text.textContent = 'OFFLINE';
    text.style.color = '#ef4444';
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

  const prices = [];
  let moexPrice = 96.0;
  let hlPrice = 90.5;
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
    // Random walk prices
    moexPrice += (Math.random() - 0.5) * 0.15;
    hlPrice += (Math.random() - 0.5) * 0.15;
    prices.push({
      timestamp_ms: ts,
      moex_close: Math.round(moexPrice * 1000) / 1000,
      hl_close: Math.round(hlPrice * 1000) / 1000,
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
    description: signal === 'buy' ? 'BUY: Spread < -2σ' : signal === 'sell' ? 'SELL: Spread > +2σ' : signal === 'watch' ? 'Attention: |Z| > 1.5' : 'Within normal',
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

  return { hist, prices, zData, stats, sig, cur, ticks };
}

// =============================================================================
// INIT
// =============================================================================
async function init() {
  if (!state.sessionStart) state.sessionStart = Date.now();

  // Try to load from API, fallback to demo data
  try {
    await loadAssets();
    await loadContracts();
    await refreshAll();
    initRangeSlider();
  } catch (e) {
    console.log('Backend not available, using demo data');
    useDemoData();
  }

  // Start polling every 5 seconds (will keep trying API)
  state.pollInterval = setInterval(async () => {
    try { await refreshAll(); }
    catch (e) { /* silent fail, keep showing last data */ }
  }, 5000);

  // Session timer
  setInterval(() => {
    if (state.sessionStart) {
      const elapsed = Math.floor((Date.now() - state.sessionStart) / 1000);
      const h = Math.floor(elapsed / 3600).toString().padStart(2, '0');
      const m = Math.floor((elapsed % 3600) / 60).toString().padStart(2, '0');
      const s = (elapsed % 60).toString().padStart(2, '0');
      const el = document.getElementById('kpiSession');
      if (el) el.textContent = `session ${h}:${m}:${s}`;
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

  // Store demo data in per-contract cache
  const cid = state.activeContract;
  const tf = state.activeTf;
  setCache(cid, tf, {
    historicalData: demo.hist,
    pricesData: demo.prices,
    zscoreData: demo.zData,
    stats: demo.stats,
    signalData: demo.sig,
    currentData: demo.cur,
    ticks: demo.ticks,
  });

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

// Alor history reload button
document.getElementById('reloadAlorBtn').addEventListener('click', async () => {
  const btn = document.getElementById('reloadAlorBtn');
  if (!state.activeContract) return;
  btn.classList.add('spin');
  btn.disabled = true;
  try {
    const res = await fetch(`/api/history/load/${state.activeContract}?timeframe=${state.activeTf}`, { method: 'POST' });
    const data = await res.json();
    console.log('Alor reload:', data);
    // Invalidate cache for this contract+tf and refresh
    setCache(state.activeContract, state.activeTf, null);
    await refreshAll();
    alert(`Loaded ${data.loaded} candles (${data.previous_candles} prev + ${data.current_candles} curr)`);
  } catch (e) {
    console.error('Alor reload failed:', e);
    alert('Reload failed: ' + e.message);
  } finally {
    btn.classList.remove('spin');
    btn.disabled = false;
  }
});

init();
