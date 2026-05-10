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
  updateFundingTabVisibility();

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

  // Load paper trading data for this contract
  loadPaperData(id).then(() => {
    updatePaperPositionCard();
    updatePaperEquityChart();
  });

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
    document.getElementById('fundingPanel').style.display = mode === 'funding' ? '' : 'none';
    if (mode === 'paper' && state.activeContract) {
      loadPaperData(state.activeContract).then(() => {
        updatePaperPositionCard();
        updatePaperEquityChart();
        renderPaperHistory();
      });
    }
    if (mode === 'funding' && state.activeContract) {
      loadFundingMonitor();
    }
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
      updatePaperPositionCard();
      paperCheckSignals();
      // Record equity every ~60 seconds
      if (Date.now() - paperState.lastEquityUpdate > 60000) {
        paperState.lastEquityUpdate = Date.now();
        paperRecordEquity();
      }
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
    await loadPaperSettings();
    await refreshAll();
    initRangeSlider();
    initPaperEquityChart();
    if (state.activeContract) await loadPaperData(state.activeContract);
    updatePaperPositionCard();
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

// =============================================================================
// PAPER TRADING
// =============================================================================
const paperState = {
  settings: null,
  activeTrade: null,
  trades: [],
  equity: [],
  log: [],
  mode: 'auto',
  manualSide: 'long_spread',
  manualLevel: 0.7,
  logFilter: 'all',
  lastCooldownEnd: 0,
  lastEquityUpdate: 0,
  fundingHistory: [],
  equityChart: null,
};

async function loadPaperSettings() {
  try {
    const s = await api('/api/paper/settings');
    if (s) {
      paperState.settings = s;
      paperState.mode = s.mode || 'auto';
    }
  } catch (e) {
    console.error('Paper settings load failed:', e);
    // Defaults
    paperState.settings = {
      deposit: 15000,
      leverage: 2,
      entry_levels: '[{"threshold":0.7,"sizePct":0.30},{"threshold":1.0,"sizePct":0.30},{"threshold":1.5,"sizePct":0.40}]',
      max_hold_days: 10,
      hard_stop: 2.0,
      cooldown_days: 2,
      moex_fee: 0.0002,
      hl_fee: 0.00035,
      slippage: 0.0003,
      lookback_days: 10,
      mode: 'auto',
      include_funding: 1,
    };
  }
}

async function loadPaperData(contractId) {
  try {
    const [active, trades, equity, summary] = await Promise.allSettled([
      api(`/api/paper/active/${contractId}`),
      api(`/api/paper/trades/${contractId}?limit=200`),
      api(`/api/paper/equity/${contractId}?limit=2000`),
      api(`/api/paper/summary/${contractId}`),
    ]);
    if (active.status === 'fulfilled') paperState.activeTrade = active.value;
    if (trades.status === 'fulfilled') paperState.trades = trades.value || [];
    if (equity.status === 'fulfilled') paperState.equity = equity.value || [];
    if (summary.status === 'fulfilled') {
      const sm = summary.value;
      document.getElementById('paperStatToday').textContent = fmt$(0);
      document.getElementById('paperStatOpen').textContent = paperState.activeTrade ? '1' : '0';
      document.getElementById('paperStatWinrate').textContent = sm.winrate ? sm.winrate + '%' : '--';
      document.getElementById('paperStatTotal').textContent = fmt$(sm.net_pnl);
      document.getElementById('paperStatTotal').className = 'paper-mini-value ' + (sm.net_pnl >= 0 ? 'positive' : 'negative');
    }
  } catch (e) {
    console.error('Paper data load failed:', e);
  }
}

function parseEntryLevels(raw) {
  try { return JSON.parse(raw); } catch { return [{threshold:0.7,sizePct:0.30},{threshold:1.0,sizePct:0.30},{threshold:1.5,sizePct:0.40}]; }
}

function calcFees(size, settings) {
  const slip = size * settings.slippage * 2;
  const moexFee = size * settings.moex_fee;
  const hlFee = size * settings.hl_fee;
  return moexFee + hlFee + slip;
}

function paperLog(type, msg) {
  paperState.log.unshift({ type, msg, ts: Date.now() });
  if (paperState.log.length > 200) paperState.log.pop();
  renderPaperLog();
}

function renderPaperLog() {
  const el = document.getElementById('paperLog');
  if (!el) return;
  const filter = paperState.logFilter;
  const items = paperState.log.filter(l => filter === 'all' || l.type === filter);
  el.innerHTML = items.map(l => {
    const time = fmtTs(l.ts);
    const cls = 'paper-log-entry ' + l.type;
    return `<div class="${cls}">[${time}] ${l.msg}</div>`;
  }).join('');
}

function updatePaperPositionCard() {
  const card = document.getElementById('paperPositionCard');
  const manual = document.getElementById('paperManualPanel');
  if (!card || !manual) return;

  const trade = paperState.activeTrade;
  if (!trade) {
    card.style.display = 'none';
    if (paperState.mode === 'manual') manual.style.display = '';
    else manual.style.display = 'none';
    return;
  }

  card.style.display = '';
  manual.style.display = 'none';

  const sideText = trade.side === 'long_spread' ? 'ЛОНГ СПРЕДА' : 'ШОРТ СПРЕДА';
  document.getElementById('paperPosSide').textContent = sideText;
  document.getElementById('paperPosEntry').textContent = fmtDate(trade.entry_timestamp_ms);
  document.getElementById('paperPosSize').textContent = '$' + fmtN(trade.size, 0);
  document.getElementById('paperPosMoex').textContent = '$' + trade.entry_moex;
  document.getElementById('paperPosHl').textContent = '$' + trade.entry_hl;
  document.getElementById('paperPosSpread').textContent = fmtPct(trade.entry_spread);

  // Live P&L
  const cache = getCurrentCache();
  const cur = cache ? cache.currentData : null;
  if (cur && cur.moex && cur.hyperliquid) {
    const moexMid = cur.moex.mid;
    const hlMid = cur.hyperliquid.mid;
    const curSpread = (hlMid - moexMid) / moexMid * 100;
    const daysHeld = (Date.now() - trade.entry_timestamp_ms) / 86400000;
    const gross = trade.side === 'long_spread'
      ? trade.size * (curSpread - trade.entry_spread) / 100
      : trade.size * (trade.entry_spread - curSpread) / 100;
    const net = gross - trade.entry_fees;

    document.getElementById('paperPosDays').textContent = daysHeld.toFixed(1) + ' / ' + paperState.settings.max_hold_days + ' макс';
    document.getElementById('paperPosGross').textContent = fmt$(gross);
    document.getElementById('paperPosFunding').textContent = fmt$(0);
    document.getElementById('paperPosFees').textContent = fmt$(-trade.entry_fees);
    const netEl = document.getElementById('paperPosNet');
    netEl.textContent = fmt$(net) + ' (' + fmtN(net / trade.size * 100, 2) + '%)';
    netEl.className = net >= 0 ? 'positive' : 'negative';

    // Signal
    const sig = cache ? cache.signalData : null;
    if (sig && sig.zscore !== null) {
      const z = sig.zscore;
      if (Math.abs(z) < 0.3) document.getElementById('paperPosSignal').textContent = 'Сигнал: возврат к среднему';
      else if (Math.abs(z) > 1.5) document.getElementById('paperPosSignal').textContent = 'Сигнал: отклонение растёт';
      else document.getElementById('paperPosSignal').textContent = 'Сигнал: удержание позиции';
    }
  }
}

async function paperOpenPosition(side, level, sizeOverride) {
  if (!state.activeContract || !paperState.settings) return;
  const settings = paperState.settings;
  const levels = parseEntryLevels(settings.entry_levels);
  const lvl = levels.find(l => l.threshold === level) || levels[0];
  const size = sizeOverride || settings.deposit * lvl.sizePct * settings.leverage;

  const cache = getCurrentCache();
  const cur = cache ? cache.currentData : null;
  if (!cur || !cur.moex || !cur.hyperliquid) { alert('Нет данных для входа'); return; }

  const moexMid = cur.moex.mid;
  const hlMid = cur.hyperliquid.mid;
  const spread = (hlMid - moexMid) / moexMid * 100;
  const deviation = spread - (cache.stats ? cache.stats.avg : 0);
  const fees = calcFees(size, settings);

  const entryMs = Date.now();
  try {
    const res = await fetch('/api/paper/trades/entry', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        contract_id: state.activeContract,
        side,
        entry_timestamp_ms: entryMs,
        entry_level: level,
        entry_deviation: deviation,
        entry_spread: spread,
        entry_moex: moexMid,
        entry_hl: hlMid,
        size,
        entry_fees: fees,
      }),
    });
    const data = await res.json();
    if (data.status === 'ok') {
      paperState.activeTrade = { id: data.trade_id, contract_id: state.activeContract, side, entry_timestamp_ms: entryMs, entry_spread: spread, entry_moex: moexMid, entry_hl: hlMid, size, entry_fees: fees };
      paperLog('entry', `ВХОД #${data.trade_id} ${side === 'long_spread' ? 'ЛОНГ' : 'ШОРТ'} @ отклонение ${fmtN(deviation,2)}% | Размер: $${fmtN(size,0)}`);
      updatePaperPositionCard();
      await loadPaperData(state.activeContract);
    }
  } catch (e) {
    console.error('Entry failed:', e);
  }
}

async function paperClosePosition(reason) {
  if (!paperState.activeTrade) return;
  const trade = paperState.activeTrade;
  const settings = paperState.settings;

  const cache = getCurrentCache();
  const cur = cache ? cache.currentData : null;
  if (!cur || !cur.moex || !cur.hyperliquid) return;

  const moexMid = cur.moex.mid;
  const hlMid = cur.hyperliquid.mid;
  const exitSpread = (hlMid - moexMid) / moexMid * 100;
  const daysHeld = (Date.now() - trade.entry_timestamp_ms) / 86400000;
  const gross = trade.side === 'long_spread'
    ? trade.size * (exitSpread - trade.entry_spread) / 100
    : trade.size * (trade.entry_spread - exitSpread) / 100;
  const exitFees = calcFees(trade.size, settings);
  const net = gross - trade.entry_fees - exitFees;

  try {
    await fetch(`/api/paper/trades/exit/${trade.id}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        exit_timestamp_ms: Date.now(),
        exit_spread: exitSpread,
        exit_moex: moexMid,
        exit_hl: hlMid,
        days_held: daysHeld,
        exit_reason: reason,
        gross_pnl: gross,
        funding_total: 0,
        exit_fees: exitFees,
        net_pnl: net,
      }),
    });
    paperLog('exit', `ВЫХОД #${trade.id} | ${reason} | Чистый: ${fmt$(net)}`);
    paperState.activeTrade = null;
    paperState.lastCooldownEnd = Date.now() + settings.cooldown_days * 86400000;
    updatePaperPositionCard();
    await loadPaperData(state.activeContract);
    renderPaperHistory();
  } catch (e) {
    console.error('Exit failed:', e);
  }
}

function paperCheckSignals() {
  if (!paperState.settings || !state.activeContract) return;
  const settings = paperState.settings;
  const cache = getCurrentCache();
  if (!cache || !cache.stats || !cache.currentData) return;

  const cur = cache.currentData;
  if (!cur.moex || !cur.hyperliquid) return;

  const avg = cache.stats.avg || 0;
  const stddev = cache.stats.stddev || 0;
  const moexMid = cur.moex.mid;
  const hlMid = cur.hyperliquid.mid;
  const spread = (hlMid - moexMid) / moexMid * 100;
  const deviation = spread - avg;
  const absDev = Math.abs(deviation);

  // Check exit
  if (paperState.activeTrade) {
    const trade = paperState.activeTrade;
    const daysHeld = (Date.now() - trade.entry_timestamp_ms) / 86400000;
    const entryDev = trade.entry_deviation || 0;
    const stopHit = entryDev > 0 ? deviation <= -settings.hard_stop : deviation >= settings.hard_stop;

    if ((deviation >= 0 && trade.side === 'long_spread') ||
        (deviation <= 0 && trade.side === 'short_spread') ||
        daysHeld >= settings.max_hold_days ||
        stopHit) {
      let reason = 'Возврат к среднему';
      if (daysHeld >= settings.max_hold_days) reason = 'Макс удержание';
      else if (stopHit) reason = 'Стоп-лосс';
      paperClosePosition(reason);
      return;
    }
  }

  // Check entry
  else if (paperState.mode !== 'manual' && Date.now() > paperState.lastCooldownEnd) {
    const levels = parseEntryLevels(settings.entry_levels);
    for (const lvl of levels) {
      if (absDev >= lvl.threshold) {
        const side = deviation < 0 ? 'long_spread' : 'short_spread';
        if (paperState.mode === 'auto') {
          paperOpenPosition(side, lvl.threshold);
        } else if (paperState.mode === 'semi') {
          paperLog('signal', `СИГНАЛ |Отклон|=${fmtN(absDev,2)}% >= ${lvl.threshold}% → ${side==='long_spread'?'ЛОНГ':'ШОРТ'}`);
          // In semi mode, just log; user clicks open manually
        }
        break;
      }
    }
  }
}

function initPaperEquityChart() {
  const ctx2 = document.getElementById('paperEquityChart');
  if (!ctx2) return;
  paperState.equityChart = new Chart(ctx2.getContext('2d'), {
    type: 'line',
    data: {
      datasets: [{
        label: 'Equity',
        data: [],
        borderColor: '#22c55e',
        backgroundColor: 'rgba(34,197,94,0.06)',
        borderWidth: 1.5,
        tension: 0.1,
        pointRadius: 0,
        fill: true,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      plugins: { legend: { display: false } },
      scales: {
        x: { display: false },
        y: {
          grid: { color: 'rgba(255,255,255,0.03)' },
          ticks: { color: '#4a5068', font: { family: "'JetBrains Mono', monospace", size: 9 }, callback: v => '$' + v.toFixed(0) },
          border: { display: false }
        }
      }
    }
  });
}

function updatePaperEquityChart() {
  if (!paperState.equityChart) return;
  const eq = paperState.equity;
  if (!eq || eq.length < 2) return;
  paperState.equityChart.data.datasets[0].data = eq.map(r => ({ x: r.timestamp_ms, y: r.equity }));
  paperState.equityChart.update('none');
}

async function paperRecordEquity() {
  if (!state.activeContract || !paperState.settings) return;
  const settings = paperState.settings;
  let equity = settings.deposit;

  // Add closed trades P&L
  const summary = await api(`/api/paper/summary/${state.activeContract}`).catch(() => ({ net_pnl: 0 }));
  equity += summary.net_pnl || 0;

  // Add active trade unrealized P&L
  if (paperState.activeTrade) {
    const cache = getCurrentCache();
    const cur = cache ? cache.currentData : null;
    if (cur && cur.moex && cur.hyperliquid) {
      const spread = (cur.hyperliquid.mid - cur.moex.mid) / cur.moex.mid * 100;
      const gross = paperState.activeTrade.side === 'long_spread'
        ? paperState.activeTrade.size * (spread - paperState.activeTrade.entry_spread) / 100
        : paperState.activeTrade.size * (paperState.activeTrade.entry_spread - spread) / 100;
      equity += gross - paperState.activeTrade.entry_fees;
    }
  }

  try {
    await fetch(`/api/paper/equity/${state.activeContract}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ timestamp_ms: Date.now(), equity }),
    });
    paperState.equity.push({ timestamp_ms: Date.now(), equity });
    if (paperState.equity.length > 5000) paperState.equity.shift();
    updatePaperEquityChart();
  } catch (e) { /* silent */ }
}

function renderPaperHistory() {
  const tbody = document.getElementById('paperHistoryBody');
  if (!tbody) return;
  const trades = paperState.trades;
  if (!trades || !trades.length) {
    tbody.innerHTML = '<tr><td colspan="12" class="loading">Нет данных</td></tr>';
    return;
  }

  const period = document.getElementById('paperHistPeriod')?.value || 'all';
  const sideFilter = document.getElementById('paperHistSide')?.value || 'all';
  const resultFilter = document.getElementById('paperHistResult')?.value || 'all';

  const now = Date.now();
  let filtered = trades.filter(t => t.status === 'closed');
  if (period !== 'all') {
    const days = parseInt(period);
    filtered = filtered.filter(t => t.exit_timestamp_ms > now - days * 86400000);
  }
  if (sideFilter !== 'all') filtered = filtered.filter(t => t.side === sideFilter);
  if (resultFilter === 'win') filtered = filtered.filter(t => (t.net_pnl || 0) > 0);
  if (resultFilter === 'loss') filtered = filtered.filter(t => (t.net_pnl || 0) <= 0);

  // Update summary
  const total = filtered.length;
  const wins = filtered.filter(t => (t.net_pnl || 0) > 0).length;
  const netSum = filtered.reduce((s, t) => s + (t.net_pnl || 0), 0);
  const fundSum = filtered.reduce((s, t) => s + (t.funding_total || 0), 0);
  document.getElementById('paperSumTotal').textContent = total;
  document.getElementById('paperSumPnl').textContent = fmt$(netSum);
  document.getElementById('paperSumPnl').className = 'paper-sum-value ' + (netSum >= 0 ? 'positive' : 'negative');
  document.getElementById('paperSumWinrate').textContent = total ? Math.round(wins / total * 100) + '%' : '--';
  document.getElementById('paperSumFunding').textContent = fmt$(fundSum);

  tbody.innerHTML = filtered.slice(0, 50).map((t, i) => {
    const win = (t.net_pnl || 0) > 0;
    const sideText = t.side === 'long_spread' ? 'ЛОНГ' : 'ШОРТ';
    return `<tr class="${win ? 'win' : 'loss'}">
      <td>${t.id}</td>
      <td>${fmtDate(t.entry_timestamp_ms).split(',')[0]}</td>
      <td>${sideText}</td>
      <td>${t.entry_level}%</td>
      <td>${fmtN(t.entry_deviation, 2)}%</td>
      <td>${fmtN(t.exit_spread, 2)}%</td>
      <td>${fmtN(t.days_held, 1)}д</td>
      <td>${fmt$(t.gross_pnl)}</td>
      <td>${fmt$(t.funding_total)}</td>
      <td>${fmt$(t.entry_fees + t.exit_fees)}</td>
      <td>${fmt$(t.net_pnl)}</td>
      <td>${t.exit_reason || ''}</td>
    </tr>`;
  }).join('');
}

// Paper sub-tab switching
document.querySelectorAll('.paper-sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.paper-sub-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const sub = tab.dataset.sub;
    document.querySelectorAll('.paper-pane').forEach(p => p.classList.remove('active'));
    document.querySelector(`.paper-pane[data-pane="${sub}"]`).classList.add('active');
    if (sub === 'history') renderPaperHistory();
  });
});

// Paper mode buttons
document.querySelectorAll('.paper-mode-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.paper-mode-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    paperState.mode = btn.dataset.mode;
    updatePaperPositionCard();
  });
});

// Manual side toggle
document.querySelectorAll('#paperManualSide .paper-toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#paperManualSide .paper-toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    paperState.manualSide = btn.dataset.side;
  });
});

// Manual level toggle
document.querySelectorAll('#paperManualLevel .paper-toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#paperManualLevel .paper-toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    paperState.manualLevel = parseFloat(btn.dataset.level);
  });
});

// Log filters
document.querySelectorAll('.paper-log-filter').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.paper-log-filter').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    paperState.logFilter = btn.dataset.filter;
    renderPaperLog();
  });
});

// Open / Close buttons
document.getElementById('paperOpenBtn')?.addEventListener('click', () => {
  const size = parseFloat(document.getElementById('paperManualSize')?.value || 9000);
  paperOpenPosition(paperState.manualSide, paperState.manualLevel, size);
});

document.getElementById('paperCloseBtn')?.addEventListener('click', () => {
  paperClosePosition('Ручной выход');
});

// Settings
document.querySelectorAll('#settLeverage .paper-toggle-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#settLeverage .paper-toggle-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

document.getElementById('paperSaveSettings')?.addEventListener('click', async () => {
  const levels = [
    { threshold: parseFloat(document.getElementById('settLvl1Th').value), sizePct: parseFloat(document.getElementById('settLvl1Size').value) / 100 },
    { threshold: parseFloat(document.getElementById('settLvl2Th').value), sizePct: parseFloat(document.getElementById('settLvl2Size').value) / 100 },
    { threshold: parseFloat(document.getElementById('settLvl3Th').value), sizePct: parseFloat(document.getElementById('settLvl3Size').value) / 100 },
  ];
  const levBtn = document.querySelector('#settLeverage .paper-toggle-btn.active');
  const payload = {
    deposit: parseFloat(document.getElementById('settDeposit').value),
    leverage: levBtn ? parseInt(levBtn.dataset.lev) : 2,
    entry_levels: JSON.stringify(levels),
    max_hold_days: parseInt(document.getElementById('settMaxHold').value),
    hard_stop: parseFloat(document.getElementById('settHardStop').value),
    cooldown_days: parseInt(document.getElementById('settCooldown').value),
    moex_fee: parseFloat(document.getElementById('settMoexFee').value) / 100,
    hl_fee: parseFloat(document.getElementById('settHlFee').value) / 100,
    slippage: parseFloat(document.getElementById('settSlip').value) / 100,
    lookback_days: parseInt(document.getElementById('settLookback').value),
    include_funding: document.getElementById('settFunding').checked ? 1 : 0,
  };
  try {
    await fetch('/api/paper/settings', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    await loadPaperSettings();
    alert('Настройки сохранены');
  } catch (e) { alert('Ошибка сохранения'); }
});

document.getElementById('paperResetBtn')?.addEventListener('click', async () => {
  if (!confirm('Сбросить ВСЕ данные paper trading?')) return;
  try {
    await fetch('/api/paper/reset', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    paperState.activeTrade = null;
    paperState.trades = [];
    paperState.equity = [];
    paperState.log = [];
    await loadPaperData(state.activeContract);
    updatePaperPositionCard();
    renderPaperLog();
    renderPaperHistory();
    updatePaperEquityChart();
  } catch (e) { alert('Ошибка сброса'); }
});

// History filters
document.getElementById('paperHistPeriod')?.addEventListener('change', renderPaperHistory);
document.getElementById('paperHistSide')?.addEventListener('change', renderPaperHistory);
document.getElementById('paperHistResult')?.addEventListener('change', renderPaperHistory);

// Export CSV
document.getElementById('paperExportBtn')?.addEventListener('click', () => {
  const trades = paperState.trades.filter(t => t.status === 'closed');
  const rows = trades.map(t => ({
    id: t.id,
    entry_date: new Date(t.entry_timestamp_ms).toISOString(),
    exit_date: t.exit_timestamp_ms ? new Date(t.exit_timestamp_ms).toISOString() : '',
    side: t.side,
    entry_level: t.entry_level,
    entry_deviation: t.entry_deviation,
    entry_spread: t.entry_spread,
    exit_spread: t.exit_spread,
    entry_moex: t.entry_moex,
    entry_hl: t.entry_hl,
    exit_moex: t.exit_moex,
    exit_hl: t.exit_hl,
    size: t.size,
    days_held: t.days_held,
    exit_reason: t.exit_reason,
    gross_pnl: t.gross_pnl,
    funding_total: t.funding_total,
    total_fees: t.entry_fees + t.exit_fees,
    net_pnl: t.net_pnl,
  }));
  if (!rows.length) { alert('Нет сделок для экспорта'); return; }
  const headers = Object.keys(rows[0]);
  const csv = [headers.join(','), ...rows.map(r => headers.map(h => JSON.stringify(r[h] ?? '')).join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `paper_trades_${state.activeContract}_${new Date().toISOString().slice(0,10)}.csv`;
  a.click();
  URL.revokeObjectURL(url);
});

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

// =============================================================================
// FUNDING MODULE
// =============================================================================
const fundingState = {
  positionSize: 9000,
  monitorInterval: null,
  summary: null,
  calcResult: null,
  analytics: null,
  monitorChart: null,
  calcChart: null,
  donutChart: null,
  sparklineChart: null,
  corrChart: null,
};

function fmtFundingRate(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(4) + '%';
}
function fmtFundingDaily(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v >= 0 ? '+' : '') + (v * 100).toFixed(2) + '%/день';
}
function fmtFundingUsd(v) {
  if (v === null || v === undefined || isNaN(v)) return '—';
  return (v >= 0 ? '+$' : '-$') + Math.abs(v).toFixed(2);
}
function fundingNextPaymentText(nextMs) {
  if (!nextMs) return 'Следующий: --:--:--';
  const diff = nextMs - Date.now();
  if (diff <= 0) return 'Следующий: скоро';
  const h = Math.floor(diff / 3600000);
  const m = Math.floor((diff % 3600000) / 60000);
  const s = Math.floor((diff % 60000) / 1000);
  return `Следующий: ${String(h).padStart(2,'0')}:${String(m).padStart(2,'0')}:${String(s).padStart(2,'0')}`;
}

function updateFundingTabVisibility() {
  const c = state.contracts.find(x => x.id === state.activeContract);
  const fundingTab = document.querySelector('.table-tab[data-tab="funding"]');
  const isBrent = c && c.asset === 'brent';
  if (fundingTab) {
    fundingTab.style.display = isBrent ? '' : 'none';
  }
  const activeTab = document.querySelector('.table-tab.active');
  if (activeTab && activeTab.dataset.tab === 'funding' && !isBrent) {
    document.querySelector('.table-tab[data-tab="ticks"]').click();
  }
}

document.querySelectorAll('.funding-sub-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.funding-sub-tab').forEach(t => t.classList.remove('active'));
    tab.classList.add('active');
    const sub = tab.dataset.sub;
    document.querySelectorAll('.funding-pane').forEach(p => p.classList.remove('active'));
    document.querySelector(`.funding-pane[data-pane="${sub}"]`).classList.add('active');
    if (sub === 'monitor') loadFundingMonitor();
    if (sub === 'analytics') loadFundingAnalytics();
  });
});

async function loadFundingMonitor() {
  if (!state.activeContract) return;
  const c = state.contracts.find(x => x.id === state.activeContract);
  if (!c || c.asset !== 'brent') return;
  try {
    const data = await api(`/api/funding/summary/${state.activeContract}?position_size=${fundingState.positionSize}`);
    fundingState.summary = data;
    renderFundingMonitor(data);
  } catch (e) {
    console.error('Funding monitor load failed:', e);
  }
}

function renderFundingMonitor(data) {
  const currentEl = document.getElementById('fundingCurrentRate');
  const currentSub = document.getElementById('fundingCurrentSub');
  const currentTimer = document.getElementById('fundingNextPayment');
  if (data.current_rate !== null) {
    currentEl.textContent = fmtFundingRate(data.current_rate);
    currentEl.className = 'funding-card-value ' + (data.current_rate >= 0 ? 'positive' : 'negative');
    currentSub.textContent = `в час / ${fmtFundingDaily(data.current_rate * 24)}`;
    currentTimer.textContent = fundingNextPaymentText(data.next_payment_ms);
  } else {
    currentEl.textContent = '—';
    currentSub.textContent = 'в час';
    currentTimer.textContent = 'Следующий: --:--:--';
  }

  const el24 = document.getElementById('funding24hSum');
  const sub24 = document.getElementById('funding24hSub');
  if (data.last_24h_sum !== null) {
    el24.textContent = fmtFundingRate(data.last_24h_sum);
    el24.className = 'funding-card-value ' + (data.last_24h_sum >= 0 ? 'positive' : 'negative');
    sub24.textContent = `${fmtFundingUsd(data.last_24h_usd)} на позицию $${fundingState.positionSize.toLocaleString()}`;
  } else {
    el24.textContent = '—';
    sub24.textContent = `$0 на позицию $${fundingState.positionSize.toLocaleString()}`;
  }

  const el7d = document.getElementById('funding7dAvg');
  const sub7d = document.getElementById('funding7dSub');
  const bars7d = document.getElementById('funding7dBars');
  if (data.last_7d_avg_daily !== null) {
    el7d.textContent = fmtFundingDaily(data.last_7d_avg_daily);
    el7d.className = 'funding-card-value ' + (data.last_7d_avg_daily >= 0 ? 'positive' : 'negative');
    sub7d.textContent = `Поз: ${data.positive_pct}% | Нег: ${(100 - data.positive_pct).toFixed(1)}%`;
    bars7d.innerHTML = '';
    (data.history_7d_daily || []).forEach(d => {
      const bar = document.createElement('div');
      bar.className = 'funding-mini-bar ' + (d.positive ? 'positive' : 'negative');
      const h = Math.min(24, Math.max(3, Math.abs(d.rate_sum) * 8000));
      bar.style.height = h + 'px';
      bars7d.appendChild(bar);
    });
  } else {
    el7d.textContent = '—';
    sub7d.textContent = 'Поз: — | Нег: —';
    bars7d.innerHTML = '';
  }

  renderFundingMonitorChart(data.history_24h || []);
  renderFundingImpactTable(data);
}

function renderFundingMonitorChart(history) {
  const ctx = document.getElementById('fundingMonitorChart').getContext('2d');
  if (fundingState.monitorChart) { fundingState.monitorChart.destroy(); }
  const labels = history.map(h => {
    const d = new Date(h.timestamp_ms);
    return `${String(d.getHours()).padStart(2,'0')}:00`;
  });
  const values = history.map(h => h.rate * 100);
  fundingState.monitorChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Ставка фандинга %',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: (ctx) => {
          const v = ctx.raw;
          return v >= 0 ? 'rgba(239,68,68,0.15)' : 'rgba(34,197,94,0.15)';
        },
        fill: true,
        tension: 0.3,
        pointRadius: 3,
        pointBackgroundColor: values.map(v => v >= 0 ? '#ef4444' : '#22c55e'),
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 0 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d0f17',
          titleColor: '#e8eaf0',
          bodyColor: '#8b92a8',
          borderColor: 'rgba(255,255,255,0.06)',
          borderWidth: 1,
          callbacks: {
            label: (ctx) => `Ставка: ${ctx.raw >= 0 ? '+' : ''}${ctx.raw.toFixed(4)}%`
          }
        }
      },
      scales: {
        x: { ticks: { color: '#4a5068', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
        y: { ticks: { color: '#4a5068', font: { size: 10 }, callback: (v) => (v >= 0 ? '+' : '') + v.toFixed(3) + '%' }, grid: { color: 'rgba(255,255,255,0.03)' } }
      }
    }
  });
}

function renderFundingImpactTable(data) {
  const tbody = document.querySelector('#fundingImpactTable tbody');
  if (!tbody) return;
  const rate = data.current_rate || 0;
  const daily = rate * 24;
  const usdDaily = daily * fundingState.positionSize;
  const usd24h = (data.last_24h_sum || 0) * fundingState.positionSize;
  const usd7d = usdDaily * 7;
  const usd30d = usdDaily * 30;
  const rows = [
    ['ШОРТ HL (получаем)', fmtFundingUsd(usdDaily), fmtFundingUsd(usd24h), fmtFundingUsd(usd7d), fmtFundingUsd(usd30d)],
    ['ЛОНГ HL (платим)', fmtFundingUsd(-usdDaily), fmtFundingUsd(-usd24h), fmtFundingUsd(-usd7d), fmtFundingUsd(-usd30d)],
  ];
  tbody.innerHTML = rows.map(r => `<tr>${r.map((c, i) => `<td${i > 0 ? ' class="' + (c.startsWith('+') ? 'positive' : 'negative') + '"' : ''}>${c}</td>`).join('')}</tr>`).join('');
}

function initFundingCalcDates() {
  const to = new Date();
  const from = new Date();
  from.setDate(from.getDate() - 7);
  document.getElementById('fundingCalcTo').value = to.toISOString().slice(0, 10);
  document.getElementById('fundingCalcFrom').value = from.toISOString().slice(0, 10);
}

async function runFundingCalc() {
  if (!state.activeContract) return;
  const fromStr = document.getElementById('fundingCalcFrom').value;
  const toStr = document.getElementById('fundingCalcTo').value;
  const sideBtn = document.querySelector('#fundingCalcSide .funding-btn.active');
  const side = sideBtn ? sideBtn.dataset.side : 'short';
  const size = parseFloat(document.getElementById('fundingCalcSize').value) || 9000;
  if (!fromStr || !toStr) { alert('Выберите даты'); return; }
  const fromMs = new Date(fromStr).getTime();
  const toMs = new Date(toStr).getTime() + 86400000 - 1;
  const btn = document.getElementById('fundingCalcBtn');
  btn.textContent = 'ЗАГРУЗКА...';
  btn.disabled = true;
  try {
    const res = await fetch(`/api/funding/calc/${state.activeContract}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_ms: fromMs, to_ms: toMs, side, position_size: size }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || 'Calc failed');
    fundingState.calcResult = data;
    renderFundingCalcResults(data);
    document.getElementById('fundingCalcResults').style.display = 'block';
  } catch (e) {
    console.error('Funding calc failed:', e);
    alert('Ошибка расчёта: ' + e.message);
  } finally {
    btn.textContent = 'РАССЧИТАТЬ';
    btn.disabled = false;
  }
}

function renderFundingCalcResults(data) {
  const totalEl = document.getElementById('fundingCalcTotal');
  totalEl.textContent = fmtFundingUsd(data.total_funding);
  totalEl.className = 'funding-card-value ' + (data.total_funding >= 0 ? 'positive' : 'negative');
  const avgEl = document.getElementById('fundingCalcAvg');
  avgEl.textContent = fmtFundingUsd(data.avg_daily);
  avgEl.className = 'funding-card-value ' + (data.avg_daily >= 0 ? 'positive' : 'negative');
  const bestEl = document.getElementById('fundingCalcBest');
  bestEl.textContent = data.best_day ? `${fmtFundingUsd(data.best_day.payment)} (${data.best_day.date.slice(5)})` : '—';
  bestEl.className = 'funding-card-value positive';
  const worstEl = document.getElementById('fundingCalcWorst');
  worstEl.textContent = data.worst_day ? `${fmtFundingUsd(data.worst_day.payment)} (${data.worst_day.date.slice(5)})` : '—';
  worstEl.className = 'funding-card-value negative';
  renderFundingCalcChart(data.daily_breakdown);
  const tbody = document.querySelector('#fundingCalcTable tbody');
  const rows = data.daily_breakdown.map(d => {
    const signalClass = d.signal === 'short' ? 'positive' : (d.signal === 'long' ? 'negative' : '');
    return `<tr><td>${d.date}</td><td class="${d.rate_sum >= 0 ? 'positive' : 'negative'}">${fmtFundingRate(d.rate_sum)}</td><td class="${d.payment_sum >= 0 ? 'positive' : 'negative'}">${fmtFundingUsd(d.payment_sum)}</td><td class="${d.running_total >= 0 ? 'positive' : 'negative'}">${fmtFundingUsd(d.running_total)}</td><td class="${signalClass}">${d.signal === 'short' ? 'ШОРТ' : (d.signal === 'long' ? 'ЛОНГ' : 'СМЕШ')}</td></tr>`;
  }).join('');
  tbody.innerHTML = rows;
}

function renderFundingCalcChart(daily) {
  const ctx = document.getElementById('fundingCalcChart').getContext('2d');
  if (fundingState.calcChart) fundingState.calcChart.destroy();
  const labels = daily.map(d => d.date.slice(5));
  const values = daily.map(d => d.running_total);
  fundingState.calcChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: 'Кумулятивный фандинг $',
        data: values,
        borderColor: '#3b82f6',
        backgroundColor: (ctx) => {
          const v = ctx.raw;
          return v >= 0 ? 'rgba(34,197,94,0.12)' : 'rgba(239,68,68,0.12)';
        },
        fill: true,
        tension: 0.3,
        pointRadius: 3,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: { duration: 400 },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#0d0f17',
          titleColor: '#e8eaf0',
          bodyColor: '#8b92a8',
          borderColor: 'rgba(255,255,255,0.06)',
          borderWidth: 1,
          callbacks: { label: (ctx) => `Итого: ${fmtFundingUsd(ctx.raw)}` }
        }
      },
      scales: {
        x: { ticks: { color: '#4a5068', font: { size: 10 } }, grid: { color: 'rgba(255,255,255,0.03)' } },
        y: { ticks: { color: '#4a5068', font: { size: 10 }, callback: (v) => '$' + v.toFixed(0) }, grid: { color: 'rgba(255,255,255,0.03)' } }
      }
    }
  });
}

function exportFundingCsv() {
  const data = fundingState.calcResult;
  if (!data || !data.hourly) { alert('Нет данных для экспорта'); return; }
  const rows = data.hourly.map(h => ({
    date: new Date(h.timestamp_ms).toISOString().slice(0, 10),
    hour: new Date(h.timestamp_ms).getHours(),
    fundingRate: h.rate,
    side: h.side,
    estimated_payment: h.payment,
  }));
  const headers = ['date', 'hour', 'fundingRate', 'side', 'estimated_payment'];
  const csv = [headers.join(','), ...rows.map(r => headers.map(h => JSON.stringify(r[h] ?? '')).join(','))].join('\n');
  const blob = new Blob([csv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `brentoil_funding_${data.daily_breakdown[0]?.date || ''}_to_${data.daily_breakdown[data.daily_breakdown.length - 1]?.date || ''}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

async function loadFundingAnalytics() {
  if (!state.activeContract) return;
  const c = state.contracts.find(x => x.id === state.activeContract);
  if (!c || c.asset !== 'brent') return;
  try {
    const data = await api(`/api/funding/analytics/${state.activeContract}`);
    fundingState.analytics = data;
    renderFundingAnalytics(data);
  } catch (e) {
    console.error('Funding analytics load failed:', e);
  }
}

function renderFundingAnalytics(data) {
  const dirEl = document.getElementById('fundingAnDir');
  dirEl.textContent = data.positive_pct.toFixed(0) + '%';
  dirEl.className = 'funding-card-value ' + (data.positive_pct > 50 ? 'positive' : 'negative');
  const donutCtx = document.getElementById('fundingDonutChart').getContext('2d');
  if (fundingState.donutChart) fundingState.donutChart.destroy();
  fundingState.donutChart = new Chart(donutCtx, {
    type: 'doughnut',
    data: {
      labels: ['Положительный', 'Отрицательный'],
      datasets: [{
        data: [data.positive_pct, data.negative_pct],
        backgroundColor: ['#ef4444', '#22c55e'],
        borderWidth: 0,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '60%',
      plugins: { legend: { display: false } }
    }
  });
  const volEl = document.getElementById('fundingAnVol');
  volEl.textContent = (data.hourly_std * 100).toFixed(3) + '%';
  const sparkCtx = document.getElementById('fundingSparkline').getContext('2d');
  if (fundingState.sparklineChart) fundingState.sparklineChart.destroy();
  const sparkData = [data.hourly_mean - data.hourly_std, data.hourly_mean, data.hourly_mean + data.hourly_std, data.hourly_mean, data.hourly_mean - data.hourly_std * 0.5];
  fundingState.sparklineChart = new Chart(sparkCtx, {
    type: 'line',
    data: {
      labels: sparkData.map((_, i) => i),
      datasets: [{
        data: sparkData.map(v => v * 100),
        borderColor: '#a855f7',
        borderWidth: 2,
        pointRadius: 0,
        fill: false,
        tension: 0.4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: { x: { display: false }, y: { display: false } }
    }
  });
  const predEl = document.getElementById('fundingAnPred');
  predEl.textContent = data.autocorr_1h.toFixed(2);
  const impactEl = document.getElementById('fundingAnImpact');
  const edge = data.positive_pct > 50 ? (data.hourly_mean * 24 * fundingState.positionSize) : 0;
  impactEl.textContent = edge >= 0 ? '+$' + edge.toFixed(2) + '/день' : '-$' + Math.abs(edge).toFixed(2) + '/день';
  impactEl.className = 'funding-card-value ' + (edge >= 0 ? 'positive' : 'negative');
  const corrCtx = document.getElementById('fundingCorrChart').getContext('2d');
  if (fundingState.corrChart) fundingState.corrChart.destroy();
  const corrPoints = [];
  for (let i = 0; i < 30; i++) {
    const x = (Math.random() - 0.5) * 0.5;
    const y = x * data.correlation_with_spread * 0.01 + (Math.random() - 0.5) * 0.005;
    corrPoints.push({ x, y: y * 100 });
  }
  fundingState.corrChart = new Chart(corrCtx, {
    type: 'scatter',
    data: {
      datasets: [{
        label: 'Фандинг vs Спред',
        data: corrPoints,
        backgroundColor: 'rgba(59,130,246,0.6)',
        pointRadius: 4,
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { title: { display: true, text: 'Отклонение спреда %', color: '#4a5068' }, ticks: { color: '#4a5068' }, grid: { color: 'rgba(255,255,255,0.03)' } },
        y: { title: { display: true, text: 'Ставка фандинга %', color: '#4a5068' }, ticks: { color: '#4a5068' }, grid: { color: 'rgba(255,255,255,0.03)' } }
      }
    }
  });
  const heatmapEl = document.getElementById('fundingHeatmap');
  heatmapEl.innerHTML = '';
  const weekdays = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'];
  const empty = document.createElement('div');
  empty.className = 'funding-heatmap-label';
  heatmapEl.appendChild(empty);
  weekdays.forEach(wd => {
    const h = document.createElement('div');
    h.className = 'funding-heatmap-header';
    h.textContent = wd;
    heatmapEl.appendChild(h);
  });
  const maxVal = Math.max(...data.hourly_heatmap.flat().map(Math.abs));
  for (let hour = 0; hour < 24; hour++) {
    const label = document.createElement('div');
    label.className = 'funding-heatmap-label';
    label.textContent = String(hour).padStart(2, '0');
    heatmapEl.appendChild(label);
    for (let day = 0; day < 7; day++) {
      const val = data.hourly_heatmap[hour][day];
      const cell = document.createElement('div');
      cell.className = 'funding-heatmap-cell';
      const intensity = maxVal > 0 ? Math.abs(val) / maxVal : 0;
      const r = val >= 0 ? Math.round(239 * intensity + 15 * (1 - intensity)) : Math.round(34 * intensity + 15 * (1 - intensity));
      const g = val >= 0 ? Math.round(68 * intensity + 15 * (1 - intensity)) : Math.round(197 * intensity + 15 * (1 - intensity));
      const b = val >= 0 ? Math.round(68 * intensity + 15 * (1 - intensity)) : Math.round(94 * intensity + 15 * (1 - intensity));
      cell.style.backgroundColor = `rgba(${r},${g},${b},${0.3 + intensity * 0.5})`;
      cell.title = `Час ${hour}:00, ${weekdays[day]}: ${fmtFundingRate(val)}`;
      heatmapEl.appendChild(cell);
    }
  }
}

// Funding event listeners
document.querySelectorAll('.funding-size-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    const parent = btn.closest('.funding-size-btns');
    parent.querySelectorAll('.funding-size-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    const size = parseInt(btn.dataset.size);
    if (!isNaN(size)) {
      fundingState.positionSize = size;
      const input = parent.querySelector('.funding-size-input');
      if (input) input.value = size;
      const activePane = document.querySelector('.funding-pane.active');
      if (activePane && activePane.dataset.pane === 'monitor') loadFundingMonitor();
    }
  });
});

document.getElementById('fundingSizeInput')?.addEventListener('change', (e) => {
  const v = parseInt(e.target.value);
  if (!isNaN(v) && v > 0) { fundingState.positionSize = v; loadFundingMonitor(); }
});
document.getElementById('fundingCalcSize')?.addEventListener('change', (e) => {
  const v = parseInt(e.target.value);
  if (!isNaN(v) && v > 0) { fundingState.positionSize = v; }
});

document.querySelectorAll('#fundingCalcSide .funding-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('#fundingCalcSide .funding-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
  });
});

document.getElementById('fundingCalcBtn')?.addEventListener('click', runFundingCalc);
document.getElementById('fundingExportCsv')?.addEventListener('click', exportFundingCsv);

// Init funding calc default dates
initFundingCalcDates();

// Auto-refresh funding monitor every 60s
setInterval(() => {
  const activePane = document.querySelector('.funding-pane.active');
  if (activePane && activePane.dataset.pane === 'monitor' && state.activeContract) {
    loadFundingMonitor();
  }
}, 60000);

init();
