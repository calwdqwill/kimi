(() => {
    // -----------------------------------------------------------------------
    // DOM refs
    // -----------------------------------------------------------------------
    const els = {
        moexBid: document.getElementById('moex-bid'),
        moexAsk: document.getElementById('moex-ask'),
        moexMid: document.getElementById('moex-mid'),
        moexFallback: document.getElementById('moex-fallback'),
        hlBid: document.getElementById('hl-bid'),
        hlAsk: document.getElementById('hl-ask'),
        hlMid: document.getElementById('hl-mid'),
        hlFallback: document.getElementById('hl-fallback'),
        currentSpread: document.getElementById('current-spread'),
        lastUpdated: document.getElementById('last-updated'),
        tfButtons: document.querySelectorAll('.tf-btn'),
    };

    // -----------------------------------------------------------------------
    // Time helpers (MSK)
    // -----------------------------------------------------------------------
    const MSK_OPTS = { timeZone: 'Europe/Moscow', hour12: false };

    function fmtDateTime(ts) {
        return new Date(ts).toLocaleString('ru-RU', {
            ...MSK_OPTS,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', second: '2-digit',
        });
    }

    function fmtAxis(ts) {
        return new Date(ts).toLocaleString('ru-RU', {
            ...MSK_OPTS,
            month: 'short', day: 'numeric',
            hour: '2-digit', minute: '2-digit',
        });
    }

    // -----------------------------------------------------------------------
    // Chart setup
    // -----------------------------------------------------------------------
    const zoomPlugin = window.ChartZoom || window['chartjs-plugin-zoom'];
    if (zoomPlugin) {
        Chart.register(zoomPlugin);
    } else {
        console.warn('chartjs-plugin-zoom not found; zoom disabled');
    }

    const commonOptions = {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
            legend: { display: false },
            tooltip: {
                callbacks: {
                    title: (items) => {
                        const x = items[0]?.parsed?.x;
                        return x ? fmtDateTime(x) : '';
                    },
                },
            },
            zoom: {
                pan: { enabled: true, mode: 'x' },
                zoom: {
                    wheel: { enabled: true },
                    pinch: { enabled: true },
                    mode: 'x',
                },
            },
        },
        scales: {
            x: {
                type: 'linear',
                ticks: {
                    callback: (value) => fmtAxis(value),
                    maxRotation: 0,
                    autoSkip: true,
                    maxTicksLimit: 10,
                },
                grid: { color: '#2a2e35' },
            },
            y: {
                grid: { color: '#2a2e35' },
                ticks: { color: '#8a919b' },
            },
        },
    };

    let spreadChart = new Chart(document.getElementById('spreadChart'), {
        type: 'line',
        data: {
            datasets: [{
                label: 'Spread %',
                data: [],
                borderColor: '#4fc3f7',
                backgroundColor: 'rgba(79,195,247,0.1)',
                borderWidth: 1.5,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: false,
                tension: 0,
            }],
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: {
                    ...commonOptions.scales.y,
                    title: { display: true, text: 'Spread %', color: '#8a919b' },
                },
            },
        },
    });

    let zscoreChart = new Chart(document.getElementById('zscoreChart'), {
        type: 'line',
        data: {
            datasets: [{
                label: 'Z-Score',
                data: [],
                borderColor: '#ba68c8',
                backgroundColor: 'rgba(186,104,200,0.1)',
                borderWidth: 1.5,
                pointRadius: 0,
                pointHoverRadius: 4,
                fill: false,
                tension: 0,
            }],
        },
        options: {
            ...commonOptions,
            scales: {
                ...commonOptions.scales,
                y: {
                    ...commonOptions.scales.y,
                    title: { display: true, text: 'Z-Score', color: '#8a919b' },
                },
            },
        },
    });

    // -----------------------------------------------------------------------
    // Data loading
    // -----------------------------------------------------------------------
    let currentTf = '5m';

    async function loadHistorical(tf) {
        try {
            const res = await fetch(`/api/historical/${tf}`);
            if (!res.ok) throw new Error(res.statusText);
            const data = await res.json();
            spreadChart.data.datasets[0].data = data.map((d) => ({ x: d.timestamp_ms, y: d.spread_pct }));
            spreadChart.update('none');
        } catch (err) {
            console.error('Historical load failed:', err);
        }
    }

    async function loadZscore(tf) {
        try {
            const res = await fetch(`/api/zscore/${tf}`);
            if (!res.ok) throw new Error(res.statusText);
            const data = await res.json();
            zscoreChart.data.datasets[0].data = data.map((d) => ({ x: d.timestamp_ms, y: d.zscore }));
            zscoreChart.update('none');
        } catch (err) {
            console.error('Z-Score load failed:', err);
        }
    }

    async function loadCurrent() {
        try {
            const res = await fetch('/api/current');
            if (!res.ok) throw new Error(res.statusText);
            const data = await res.json();

            // MOEX
            els.moexBid.textContent = data.moex.best_bid != null ? data.moex.best_bid.toFixed(2) : '—';
            els.moexAsk.textContent = data.moex.best_ask != null ? data.moex.best_ask.toFixed(2) : '—';
            els.moexMid.textContent = data.moex.mid != null ? data.moex.mid.toFixed(2) : '—';
            els.moexFallback.textContent = data.moex.is_orderbook
                ? ''
                : 'Using last price (not bid/ask)';

            // HL
            els.hlBid.textContent = data.hyperliquid.best_bid != null ? data.hyperliquid.best_bid.toFixed(2) : '—';
            els.hlAsk.textContent = data.hyperliquid.best_ask != null ? data.hyperliquid.best_ask.toFixed(2) : '—';
            els.hlMid.textContent = data.hyperliquid.mid != null ? data.hyperliquid.mid.toFixed(2) : '—';
            els.hlFallback.textContent = data.hyperliquid.is_l2
                ? ''
                : 'Using allMids (not L2 book)';

            // Spread
            els.currentSpread.textContent = data.current_spread_pct != null
                ? data.current_spread_pct.toFixed(3)
                : '—';
            els.currentSpread.style.color = (data.current_spread_pct || 0) >= 0 ? '#66bb6a' : '#ef5350';

            // Updated
            els.lastUpdated.textContent = data.updated_ms
                ? 'Updated: ' + fmtDateTime(data.updated_ms)
                : 'Updated: —';
        } catch (err) {
            console.error('Current prices load failed:', err);
        }
    }

    function loadCharts(tf) {
        currentTf = tf;
        loadHistorical(tf);
        loadZscore(tf);
    }

    // -----------------------------------------------------------------------
    // Event wiring
    // -----------------------------------------------------------------------
    els.tfButtons.forEach((btn) => {
        btn.addEventListener('click', () => {
            els.tfButtons.forEach((b) => b.classList.remove('active'));
            btn.classList.add('active');
            loadCharts(btn.dataset.tf);
        });
    });

    // -----------------------------------------------------------------------
    // Boot
    // -----------------------------------------------------------------------
    loadCharts('5m');
    loadCurrent();
    setInterval(loadCurrent, 2000);
})();
