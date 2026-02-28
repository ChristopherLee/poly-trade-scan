// ── State ─────────────────────────────────────────────────────────
let tradesPage = 0;
const PAGE_SIZE = 50;
let pnlChart = null;
let latencyChart = null;
let categoryPnlChart = null;
let autoRefreshTimer = null;

// ── Init ──────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
    refreshAll();
    setupAutoRefresh();
});

function setupAutoRefresh() {
    const cb = document.getElementById('auto-refresh');
    cb.addEventListener('change', () => {
        if (cb.checked) {
            autoRefreshTimer = setInterval(refreshAll, 30000);
        } else {
            clearInterval(autoRefreshTimer);
        }
    });
    autoRefreshTimer = setInterval(refreshAll, 30000);
}

// ── API helpers ───────────────────────────────────────────────────
async function api(path) {
    try {
        const resp = await fetch(path);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        return await resp.json();
    } catch (e) {
        console.error(`API error: ${path}`, e);
        return null;
    }
}

// ── Formatters ────────────────────────────────────────────────────
function fmt$(v, decimals = 2) {
    if (v == null || isNaN(v)) return '—';
    const sign = v >= 0 ? '+' : '';
    return `${sign}$${v.toFixed(decimals)}`;
}

function fmtAddr(addr) {
    if (!addr) return '—';
    return `${addr.slice(0, 6)}…${addr.slice(-4)}`;
}

function fmtTime(epoch) {
    if (!epoch) return '—';
    const d = new Date(epoch * 1000);
    return d.toLocaleString('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function pnlClass(v) {
    if (v > 0.001) return 'text-green';
    if (v < -0.001) return 'text-red';
    return '';
}

function pnlStatClass(v) {
    if (v > 0.001) return 'positive';
    if (v < -0.001) return 'negative';
    return '';
}

// ── Refresh All ───────────────────────────────────────────────────
async function refreshAll() {
    await Promise.all([
        loadSummary(),
        loadWallets(),
        loadCategories(),
        loadTrades(),
        loadPositions(),
        loadCharts(),
    ]);
}

// ── Summary ───────────────────────────────────────────────────────
async function loadSummary() {
    const d = await api('/api/summary');
    if (!d) return;

    const totalPnlEl = document.getElementById('stat-total-pnl');
    totalPnlEl.textContent = fmt$(d.total_pnl);
    totalPnlEl.className = `stat-value ${pnlStatClass(d.total_pnl)}`;

    document.getElementById('stat-realized').textContent = fmt$(d.realized_pnl);
    document.getElementById('stat-unrealized').textContent = fmt$(d.unrealized_pnl);
    document.getElementById('stat-volume').textContent = `$${d.total_volume.toLocaleString()}`;
    document.getElementById('stat-paper-trades').textContent = d.total_paper_trades;
    document.getElementById('stat-slippage').textContent = `$${d.avg_slippage.toFixed(4)}`;
    document.getElementById('stat-latency').textContent = `${d.avg_latency_ms.toFixed(0)}ms`;
    document.getElementById('stat-wallets').textContent = d.total_wallets;
    document.getElementById('stat-target-trades').textContent = d.total_target_trades;
    document.getElementById('stat-positions').textContent = d.unresolved_positions + ' open';
    document.getElementById('stat-resolved').textContent = d.resolved_markets;
}

// ── Wallets ───────────────────────────────────────────────────────
async function loadWallets() {
    const data = await api('/api/wallets');
    if (!data) return;

    // Populate filter dropdown
    const select = document.getElementById('filter-wallet');
    const current = select.value;
    select.innerHTML = '<option value="">All Wallets</option>';
    data.forEach(w => {
        const opt = document.createElement('option');
        opt.value = w.address;
        opt.textContent = w.alias ? `${w.alias} (${fmtAddr(w.address)})` : fmtAddr(w.address);
        select.appendChild(opt);
    });
    select.value = current;

    // Table
    const tbody = document.getElementById('wallets-tbody');
    tbody.innerHTML = data.map(w => `
        <tr>
            <td class="mono">${fmtAddr(w.address)}</td>
            <td>${w.alias || '—'}</td>
            <td><span class="badge ${w.source === 'leaderboard' ? 'badge-resolved' : 'badge-open'}">${w.source}</span></td>
            <td class="${pnlClass(w.leaderboard_pnl)}">${fmt$(w.leaderboard_pnl)}</td>
            <td>$${(w.leaderboard_vol || 0).toLocaleString()}</td>
            <td>${w.trade_count}</td>
            <td>$${(w.paper_volume || 0).toFixed(2)}</td>
            <td><button class="btn btn-sm" onclick="filterByWallet('${w.address}')">View Trades</button></td>
        </tr>
    `).join('');
}

async function loadCategories() {
    const data = await api('/api/markets');
    if (!data) return;
    const categories = [...new Set(data.map(m => m.category).filter(Boolean))].sort();
    const select = document.getElementById('filter-category');
    const current = select.value;
    select.innerHTML = '<option value="">All Categories</option>';
    categories.forEach(cat => {
        const opt = document.createElement('option');
        opt.value = cat;
        opt.textContent = cat;
        select.appendChild(opt);
    });
    select.value = current;
}

function filterByWallet(addr) {
    document.getElementById('filter-wallet').value = addr;
    switchTab('trades');
    loadTrades();
}

// ── Trades ────────────────────────────────────────────────────────
async function loadTrades() {
    const wallet = document.getElementById('filter-wallet').value;
    const resolved = document.getElementById('filter-resolved').value;
    const category = document.getElementById('filter-category').value;
    let url = `/api/trades?limit=${PAGE_SIZE}&offset=${tradesPage * PAGE_SIZE}`;
    if (wallet) url += `&wallet=${wallet}`;
    if (category) url += `&category=${category}`; // Note: API doesn't support this yet, but we'll filter client-side for now or just show column


    const data = await api(url);
    if (!data) return;

    // Filter status and category client-side
    let filtered = data;
    if (resolved === 'resolved') filtered = filtered.filter(t => t.resolved);
    if (resolved === 'unresolved') filtered = filtered.filter(t => !t.resolved);
    if (category) filtered = filtered.filter(t => t.category === category);

    const tbody = document.getElementById('trades-tbody');
    tbody.innerHTML = filtered.map(t => {
        const outcomes = Array.isArray(t.outcomes) ? t.outcomes : [];
        const outcomeLabel = outcomes[t.outcome_idx] || '?';
        const question = t.question || '—';
        const groupItemTitle = (t.group_item_title || '').trim();
        const showGroupItem = groupItemTitle && groupItemTitle.toLowerCase() !== (t.category || '').toLowerCase();
        const statusBadge = t.resolved
            ? `<span class="badge badge-resolved">Resolved</span>`
            : `<span class="badge badge-open">Open</span>`;
        const sideBadge = t.side === 'BUY'
            ? '<span class="badge badge-buy">BUY</span>'
            : '<span class="badge badge-sell">SELL</span>';

        const marketLink = t.slug
            ? `<a href="https://polymarket.com/event/${t.slug}" target="_blank" class="market-link">${question.slice(0, 45)}${question.length > 45 ? '…' : ''}</a>`
            : `${question.slice(0, 45)}${question.length > 45 ? '…' : ''}`;

        return `
        <tr>
            <td><a href="https://polygonscan.com/tx/${t.tx_hash}" target="_blank" class="tx-link">${fmtTime(t.onchain_ts)}</a></td>
            <td class="mono"><a href="https://polygonscan.com/address/${t.wallet}" target="_blank" class="wallet-link">${fmtAddr(t.wallet)}</a></td>
            <td class="truncate" title="${question}">${marketLink}<br><small style="color:var(--text-muted)">${outcomeLabel}${showGroupItem ? ` • ${groupItemTitle}` : ''}</small></td>
            <td><span class="badge badge-resolved" style="background:var(--bg-card); border:1px solid var(--border)">${t.category || 'Other'}</span></td>
            <td>${sideBadge}</td>
            <td class="mono">$${(t.target_price || 0).toFixed(4)}</td>
            <td class="mono">$${(t.paper_price || 0).toFixed(4)}</td>
            <td class="mono ${pnlClass(-(t.slippage || 0))}">${t.slippage != null ? t.slippage.toFixed(4) : '—'}</td>
            <td class="mono">${(t.paper_size || 0).toFixed(1)}</td>
            <td class="mono">${t.total_delay_ms != null ? t.total_delay_ms.toFixed(0) + 'ms' : '—'}</td>
            <td>${statusBadge}</td>
            <td><button class="btn btn-sm btn-ghost" onclick="openOrderBook(${t.target_id})">📖</button></td>
        </tr>`;
    }).join('');

    document.getElementById('trades-page-info').textContent = `Page ${tradesPage + 1}`;
    document.getElementById('trades-prev').disabled = tradesPage === 0;
    document.getElementById('trades-next').disabled = data.length < PAGE_SIZE;
}

function tradesNext() { tradesPage++; loadTrades(); }
function tradesPrev() { if (tradesPage > 0) { tradesPage--; loadTrades(); } }

// ── Positions ─────────────────────────────────────────────────────
async function loadPositions() {
    const filter = document.getElementById('position-filter').value;
    let url = '/api/positions';
    if (filter) url += `?resolved=${filter}`;

    const data = await api(url);
    if (!data) return;

    const tbody = document.getElementById('positions-tbody');
    tbody.innerHTML = data.map(p => {
        const outcomes = Array.isArray(p.outcomes) ? p.outcomes : [];
        const outcomeLabel = outcomes[p.outcome_idx] || '?';
        const question = p.question || p.token_id.slice(0, 20) + '…';
        const statusBadge = p.resolved
            ? `<span class="badge badge-resolved">Resolved (${p.payout_value})</span>`
            : `<span class="badge badge-open">Open</span>`;

        const marketLink = p.slug
            ? `<a href="https://polymarket.com/event/${p.slug}" target="_blank" class="market-link">${question.slice(0, 55)}${question.length > 55 ? '…' : ''}</a>`
            : `${question.slice(0, 55)}${question.length > 55 ? '…' : ''}`;

        return `
        <tr>
            <td class="truncate" title="${question}">${marketLink}</td>
            <td><span class="badge" style="background:var(--bg-card); border:1px solid var(--border)">${p.category || 'Other'}</span></td>
            <td>${outcomeLabel}</td>
            <td class="mono">${p.size.toFixed(2)}</td>
            <td class="mono">$${p.cost_basis.toFixed(2)}</td>
            <td class="mono ${pnlClass(p.realized_pnl)}">${fmt$(p.realized_pnl)}</td>
            <td class="mono ${pnlClass(p.unrealized_pnl)}">${fmt$(p.unrealized_pnl)}</td>
            <td>${statusBadge}</td>
        </tr>`;
    }).join('');
}

// ── Charts ────────────────────────────────────────────────────────
async function loadCharts() {
    await Promise.all([loadPnlChart(), loadLatencyChart(), loadCategoryPnLChart()]);
}

async function loadPnlChart() {
    const data = await api('/api/pnl_over_time');
    if (!data || !data.length) return;

    const labels = data.map(d => {
        const dt = new Date(d.ts * 1000);
        return dt.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit' });
    });
    const values = data.map(d => d.cumulative_cost);

    const ctx = document.getElementById('pnl-chart').getContext('2d');
    if (pnlChart) pnlChart.destroy();

    pnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [{
                label: 'Cumulative Cost Flow ($)',
                data: values,
                borderColor: '#6366f1',
                backgroundColor: 'rgba(99,102,241,0.1)',
                fill: true,
                tension: 0.3,
                pointRadius: 0,
                borderWidth: 2,
            }]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } }
            },
            scales: {
                x: { ticks: { color: '#64748b', maxTicksLimit: 12 }, grid: { color: '#1e293b' } },
                y: { ticks: { color: '#64748b' }, grid: { color: '#1e293b' } }
            }
        }
    });
}

async function loadLatencyChart() {
    const data = await api('/api/latency_stats');
    if (!data || !data.length) return;

    const labels = data.map((_, i) => `#${data.length - i}`).reverse();
    const det = data.map(d => d.detection_delay_ms).reverse();
    const exe = data.map(d => d.execution_delay_ms).reverse();

    const ctx = document.getElementById('latency-chart').getContext('2d');
    if (latencyChart) latencyChart.destroy();

    latencyChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Detection (ms)',
                    data: det,
                    backgroundColor: 'rgba(6,182,212,0.6)',
                    borderRadius: 3,
                },
                {
                    label: 'Execution (ms)',
                    data: exe,
                    backgroundColor: 'rgba(99,102,241,0.6)',
                    borderRadius: 3,
                }
            ]
        },
        options: {
            responsive: true,
            plugins: {
                legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } }
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: { color: '#64748b', maxTicksLimit: 20, display: false },
                    grid: { display: false }
                },
                y: {
                    stacked: true,
                    ticks: { color: '#64748b' },
                    grid: { color: '#1e293b' }
                }
            }
        }
    });
}

async function loadCategoryPnLChart() {
    const data = await api('/api/pnl_by_category');
    if (!data || !data.length) return;

    // Sort by total PnL
    data.sort((a, b) => (b.realized + b.unrealized) - (a.realized + a.unrealized));

    const labels = data.map(d => d.category);
    const realized = data.map(d => d.realized);
    const unrealized = data.map(d => d.unrealized);

    const ctx = document.getElementById('category-pnl-chart').getContext('2d');
    if (categoryPnlChart) categoryPnlChart.destroy();

    categoryPnlChart = new Chart(ctx, {
        type: 'bar',
        data: {
            labels,
            datasets: [
                {
                    label: 'Realized PnL ($)',
                    data: realized,
                    backgroundColor: 'rgba(6,182,212,0.8)',
                    borderRadius: 4,
                },
                {
                    label: 'Unrealized PnL ($)',
                    data: unrealized,
                    backgroundColor: 'rgba(99,102,241,0.8)',
                    borderRadius: 4,
                }
            ]
        },
        options: {
            indexAxis: 'y',
            responsive: true,
            plugins: {
                legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } }
            },
            scales: {
                x: {
                    stacked: true,
                    ticks: { color: '#64748b' },
                    grid: { color: '#1e293b' }
                },
                y: {
                    stacked: true,
                    ticks: { color: '#64748b' },
                    grid: { display: false }
                }
            }
        }
    });
}

// ── Tab switching ─────────────────────────────────────────────────
function switchTab(name) {
    document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.tab-content').forEach(c => c.classList.toggle('active', c.id === `tab-${name}`));
    if (name === 'charts') loadCharts();
    if (name === 'positions') loadPositions();
}
async function openOrderBook(targetId) {
    const data = await api(`/api/orderbook?target_trade_id=${targetId}`);
    if (!data || data.error) {
        alert(data ? data.error : "Failed to load order book");
        return;
    }

    document.getElementById('ob-modal-info').innerHTML = `
        Target Trade ID: ${targetId} · Token: ${data.token_id} · 
        Best Bid: $${(data.best_bid || 0).toFixed(4)} · Best Ask: $${(data.best_ask || 0).toFixed(4)} <br>
        Total Liquidity (USD): <span style="color:#10b981">Bids $${(data.total_bid_liquidity_usd || 0).toLocaleString()}</span> / 
        <span style="color:#ef4444">Asks $${(data.total_ask_liquidity_usd || 0).toLocaleString()}</span>
    `;

    renderOBTable('ob-bids-tbody', data.bids || []);
    renderOBTable('ob-asks-tbody', data.asks || []);

    document.getElementById('ob-modal').style.display = 'block';
}

function renderOBTable(tbodyId, levels) {
    const tbody = document.getElementById(tbodyId);
    if (!levels.length) {
        tbody.innerHTML = '<tr><td colspan="3" style="text-align:center; opacity:0.5">No levels captured</td></tr>';
        return;
    }

    // Total liquidity up to this level
    let cumulative = 0;
    tbody.innerHTML = levels.slice(0, 50).map(l => {
        const usd = l.price * l.size;
        cumulative += usd;
        return `
            <tr>
                <td class="mono">$${Number(l.price).toFixed(4)}</td>
                <td class="mono">${Number(l.size).toLocaleString()}</td>
                <td class="mono">$${cumulative.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
            </tr>
        `;
    }).join('');
}

function closeModal(id) {
    document.getElementById(id).style.display = 'none';
}

// Close modal on outside click
window.onclick = function (event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
    }
}
