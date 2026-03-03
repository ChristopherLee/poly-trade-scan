// â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
let tradesPage = 0;
const PAGE_SIZE = 50;
let lastTradeFilterKey = '';
let pnlChart = null;
let latencyChart = null;
let categoryPnlChart = null;
let walletPnlChart = null;
let autoRefreshTimer = null;
let leaderboardWalletCache = [];
let activeWalletDetail = null;
const tableSortState = {};
let dashboardInitialized = false;
const TRADES_COLUMN_WIDTH_STORAGE_KEY = 'trades_column_widths_v1';
const TRADES_COLUMN_WIDTH_VARS = {
    time: '--trades-col-time',
    wallet: '--trades-col-wallet',
    market: '--trades-col-market',
    category: '--trades-col-category',
    side: '--trades-col-side',
    predicted: '--trades-col-predicted',
    targetPrice: '--trades-col-target-price',
    paperPrice: '--trades-col-paper-price',
    slippage: '--trades-col-slippage',
    size: '--trades-col-size',
    latency: '--trades-col-latency',
    status: '--trades-col-status',
    position: '--trades-col-position',
    book: '--trades-col-book',
};

// â”€â”€ Init â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function initializeDashboard() {
    if (dashboardInitialized) return;
    dashboardInitialized = true;
    applyTradesColumnWidths();
    setupSortableTables();
    refreshAll();
    setupAutoRefresh();
    loadLeaderboardWallets();
}

if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initializeDashboard);
} else {
    initializeDashboard();
}

function setupSortableTables() {
    document.querySelectorAll('table.data-table').forEach((table) => {
        if (!table.id || table.dataset.sortBound === '1') return;
        table.addEventListener('click', (event) => {
            const header = event.target.closest('th.sortable');
            if (!header || !table.contains(header)) return;
            const headers = Array.from(table.querySelectorAll('thead th.sortable'));
            toggleTableSort(table.id, headers.indexOf(header), header.dataset.sortType || 'string');
        });
        table.dataset.sortBound = '1';
    });
}

function toggleTableSort(tableId, columnIndex, type) {
    if (!tableId || columnIndex < 0) return;
    const current = tableSortState[tableId];
    const defaultDirection = (type === 'number' || type === 'time') ? 'desc' : 'asc';
    const direction = current && current.columnIndex === columnIndex
        ? (current.direction === 'asc' ? 'desc' : 'asc')
        : defaultDirection;

    tableSortState[tableId] = { columnIndex, direction, type };
    applyTableSort(tableId);
}

function updateSortIndicators(tableId) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const state = tableSortState[tableId];
    Array.from(table.querySelectorAll('thead th.sortable')).forEach((th, index) => {
        th.classList.toggle('sorted-asc', !!state && state.columnIndex === index && state.direction === 'asc');
        th.classList.toggle('sorted-desc', !!state && state.columnIndex === index && state.direction === 'desc');
    });
}

function parseSortValue(raw, type) {
    const value = String(raw || '').trim();
    if (!value || value === '-' || value === '—') {
        return type === 'string' ? '' : Number.NEGATIVE_INFINITY;
    }
    if (type === 'time') {
        const parsed = Date.parse(value);
        return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    }
    if (type === 'number') {
        const normalized = value
            .replace(/[,$]/g, '')
            .replace(/ms$/i, '')
            .replace(/[^\d.+-]/g, '');
        const parsed = Number(normalized);
        return Number.isNaN(parsed) ? Number.NEGATIVE_INFINITY : parsed;
    }
    return value.toLowerCase();
}

function applyTableSort(tableId) {
    const state = tableSortState[tableId];
    const table = document.getElementById(tableId);
    if (!state || !table || !table.tBodies.length) return;

    const tbody = table.tBodies[0];
    const rows = Array.from(tbody.rows);
    rows.sort((a, b) => {
        const aValue = parseSortValue(a.cells[state.columnIndex]?.textContent, state.type);
        const bValue = parseSortValue(b.cells[state.columnIndex]?.textContent, state.type);
        if (aValue === bValue) return 0;
        if (state.direction === 'asc') return aValue > bValue ? 1 : -1;
        return aValue < bValue ? 1 : -1;
    });
    rows.forEach((row) => tbody.appendChild(row));
    updateSortIndicators(tableId);
}

function reapplyActiveTableSort(tableId) {
    updateSortIndicators(tableId);
    if (tableSortState[tableId]) applyTableSort(tableId);
}

function normalizeWidthValue(value) {
    if (value == null) return null;
    const normalized = String(value).trim();
    if (!normalized) return null;
    if (/^\d+$/.test(normalized)) return `${normalized}px`;
    return normalized;
}

function readStoredTradesColumnWidths() {
    try {
        const raw = localStorage.getItem(TRADES_COLUMN_WIDTH_STORAGE_KEY);
        if (!raw) return {};
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
        return {};
    }
}

function applyTradesColumnWidths() {
    const rootStyle = document.documentElement.style;
    const widths = readStoredTradesColumnWidths();

    for (const [key, cssVarName] of Object.entries(TRADES_COLUMN_WIDTH_VARS)) {
        const normalized = normalizeWidthValue(widths[key]);
        if (normalized) {
            rootStyle.setProperty(cssVarName, normalized);
        }
    }
}

function setTradesColumnWidths(nextWidths) {
    if (!nextWidths || typeof nextWidths !== 'object') return;

    const current = readStoredTradesColumnWidths();
    const merged = { ...current };

    for (const key of Object.keys(TRADES_COLUMN_WIDTH_VARS)) {
        if (!(key in nextWidths)) continue;
        const normalized = normalizeWidthValue(nextWidths[key]);
        if (normalized) {
            merged[key] = normalized;
        } else {
            delete merged[key];
        }
    }

    localStorage.setItem(TRADES_COLUMN_WIDTH_STORAGE_KEY, JSON.stringify(merged));
    applyTradesColumnWidths();
}

function resetTradesColumnWidths() {
    localStorage.removeItem(TRADES_COLUMN_WIDTH_STORAGE_KEY);
    for (const cssVarName of Object.values(TRADES_COLUMN_WIDTH_VARS)) {
        document.documentElement.style.removeProperty(cssVarName);
    }
}

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

// â”€â”€ API helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

async function apiPost(path, payload) {
    try {
        const resp = await fetch(path, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload || {}),
        });
        const body = await resp.json();
        if (!resp.ok) throw new Error(body.error || `HTTP ${resp.status}`);
        return body;
    } catch (e) {
        console.error(`API error: ${path}`, e);
        alert(e.message || 'Request failed');
        return null;
    }
}

// â”€â”€ Formatters â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
function fmt$(v, decimals = 2) {
    if (v == null || isNaN(v)) return '-';
    const sign = v >= 0 ? '+' : '';
    return `${sign}$${v.toFixed(decimals)}`;
}

function fmtAddr(addr) {
    if (!addr) return '-';
    return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

function fmtTime(epoch) {
    if (!epoch) return '-';
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

function isResolvedValue(v) {
    return v === true || v === 1 || v === '1';
}

function escapeHtml(value) {
    return String(value ?? '')
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#39;');
}

// â”€â”€ Refresh All â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function refreshAll() {
    await Promise.all([
        loadSummary(),
        loadWallets(),
        loadCategories(),
        loadTrades(),
        loadPositions(),
    ]);

    // Only load chart endpoints when charts tab is visible.
    const chartsTab = document.getElementById('tab-charts');
    if (chartsTab && chartsTab.classList.contains('active')) {
        await loadCharts();
    }

    if (activeWalletDetail) {
        await openWalletDetail(activeWalletDetail, { preserveOpen: true });
    }
}

// â”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Wallets â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function loadWallets() {
    const data = await api('/api/wallets');
    if (!data) return;
    const trackedWallets = data.filter(w => w.tracking_enabled);

    // Populate filter dropdown with actively tracked wallets
    const select = document.getElementById('filter-wallet');
    const current = select.value;
    select.innerHTML = '<option value="">All Wallets</option>';
    trackedWallets.forEach(w => {
        const opt = document.createElement('option');
        opt.value = w.address;
        opt.textContent = w.alias ? `${w.alias} (${fmtAddr(w.address)})` : fmtAddr(w.address);
        select.appendChild(opt);
    });
    select.value = current;

    const tbody = document.getElementById('wallets-tbody');
    if (!trackedWallets.length) {
        tbody.innerHTML = '<tr><td colspan="11" style="text-align:center;">No tracked wallets yet.</td></tr>';
        return;
    }

    tbody.innerHTML = trackedWallets.map(w => {
        const trackingBadge = w.tracking_enabled
            ? `<span class="badge badge-buy">Enabled</span>`
            : `<span class="badge badge-sell">Disabled</span>`;
        const profileUrl = `https://polymarket.com/profile/${w.address}`;
        const aliasLabel = w.alias || fmtAddr(w.address);

        return `
        <tr>
            <td class="mono">${fmtAddr(w.address)}</td>
            <td><a href="${profileUrl}" target="_blank" rel="noopener noreferrer" class="wallet-link">${aliasLabel}</a></td>
            <td><span class="badge ${w.source && w.source.startsWith('leaderboard') ? 'badge-resolved' : 'badge-open'}">${w.source || 'manual'}</span></td>
            <td>${trackingBadge}</td>
            <td>${fmtTime(w.enabled_at)}</td>
            <td>${fmtTime(w.disabled_at)}</td>
            <td class="${pnlClass(w.realized_pnl || 0)}">${fmt$(Number(w.realized_pnl || 0))}</td>
            <td>$${(w.leaderboard_vol || 0).toLocaleString()}</td>
            <td>${w.trade_count}</td>
            <td>$${(w.paper_volume || 0).toFixed(2)}</td>
            <td>
                <div class="wallet-actions">
                    <button class="btn btn-sm btn-accent" onclick="openWalletDetail('${w.address}')">Details</button>
                    <button class="btn btn-sm" onclick="filterByWallet('${w.address}')">View Trades</button>
                    <button class="btn btn-sm ${w.tracking_enabled ? 'btn-ghost' : 'btn-accent'}" onclick="toggleWalletTracking('${w.address}', ${w.tracking_enabled ? 'false' : 'true'})">${w.tracking_enabled ? 'Disable' : 'Enable'}</button>
                </div>
            </td>
        </tr>`;
    }).join('');
    reapplyActiveTableSort('wallets-table');
}

async function addWallet() {
    const addrInput = document.getElementById('wallet-address-input');
    const aliasInput = document.getElementById('wallet-alias-input');
    const address = addrInput.value.trim();
    const alias = aliasInput.value.trim();

    if (!address) {
        alert('Wallet address is required');
        return;
    }

    const resp = await apiPost('/api/wallets', { address, alias });
    if (!resp) return;

    addrInput.value = '';
    aliasInput.value = '';
    await loadWallets();
    await loadSummary();
}

async function toggleWalletTracking(address, enabled) {
    const resp = await apiPost('/api/wallets/toggle', { address, enabled });
    if (!resp) return;
    await loadWallets();
    await loadSummary();
}

async function addOrEnableWallet(address, alias = '') {
    const resp = await apiPost('/api/wallets', { address, alias });
    if (!resp) return;
    await loadWallets();
    await loadSummary();
}

async function setLeaderboardTracking(address, alias = '', enabled = true) {
    const resp = enabled
        ? await apiPost('/api/wallets', { address, alias })
        : await apiPost('/api/wallets/toggle', { address, enabled: false });
    if (!resp) return;
    await Promise.all([
        loadWallets(),
        loadSummary(),
        loadLeaderboardWallets(),
    ]);
}

async function loadLeaderboardWallets() {
    const category = document.getElementById('leaderboard-category').value;
    const timePeriod = document.getElementById('leaderboard-time-period').value;
    const orderBy = document.getElementById('leaderboard-order-by').value;
    const limit = document.getElementById('leaderboard-limit').value || 20;

    const [data, wallets] = await Promise.all([
        api(`/api/leaderboard?category=${category}&time_period=${timePeriod}&order_by=${orderBy}&limit=${limit}`),
        api('/api/wallets'),
    ]);
    const tbody = document.getElementById('leaderboard-wallets-tbody');
    if (!data) {
        leaderboardWalletCache = [];
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">Failed to load leaderboard.</td></tr>';
        return;
    }
    if (!data.length) {
        leaderboardWalletCache = [];
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;">No wallets found.</td></tr>';
        return;
    }
    leaderboardWalletCache = data;

    const walletMap = new Map((wallets || []).map(w => [String(w.address || '').toLowerCase(), w]));
    tbody.innerHTML = data.map(w => {
        const addr = String(w.address || '').toLowerCase();
        const tracked = !!walletMap.get(addr)?.tracking_enabled;
        const actionLabel = tracked ? 'Tracking' : 'Track';
        const actionClass = tracked ? 'btn-success' : 'btn-accent';
        const targetEnabled = tracked ? 'false' : 'true';
        const encodedAlias = encodeURIComponent(w.alias || '').replace(/'/g, '%27');
        const profileUrl = `https://polymarket.com/profile/${w.address}`;
        const aliasLabel = w.alias || fmtAddr(w.address);
        return `
            <tr>
                <td class="mono">${fmtAddr(w.address)}</td>
                <td><a href="${profileUrl}" target="_blank" rel="noopener noreferrer" class="wallet-link">${aliasLabel}</a></td>
                <td class="${pnlClass(w.pnl || 0)}">${fmt$(Number(w.pnl || 0))}</td>
                <td>$${Number(w.vol || 0).toLocaleString()}</td>
                <td><button class="btn btn-sm ${actionClass}" onclick="setLeaderboardTracking('${w.address}', decodeURIComponent('${encodedAlias}'), ${targetEnabled})">${actionLabel}</button></td>
            </tr>
        `;
    }).join('');
    reapplyActiveTableSort('leaderboard-wallets-table');
}

async function enableAllLeaderboardWallets() {
    if (!leaderboardWalletCache.length) {
        alert('Load a leaderboard first.');
        return;
    }

    const requests = leaderboardWalletCache.map((w) =>
        apiPost('/api/wallets', { address: w.address, alias: w.alias || '' })
    );
    const results = await Promise.all(requests);
    const enabledCount = results.filter(Boolean).length;

    await Promise.all([
        loadWallets(),
        loadSummary(),
        loadLeaderboardWallets(),
    ]);

    alert(`Enabled ${enabledCount}/${leaderboardWalletCache.length} wallets.`);
}

async function loadCategories() {
    const data = await api('/api/markets');
    if (!data) return;
    const categories = [...new Set(data.map(m => m.category).filter(Boolean))].sort();

    ['filter-category', 'position-category'].forEach((id) => {
        const select = document.getElementById(id);
        if (!select) return;
        const current = select.value;
        select.innerHTML = '<option value="">All Categories</option>';
        categories.forEach(cat => {
            const opt = document.createElement('option');
            opt.value = cat;
            opt.textContent = cat;
            select.appendChild(opt);
        });
        select.value = current;
    });
}

function filterByWallet(addr) {
    document.getElementById('filter-wallet').value = addr;
    switchTab('trades');
    loadTrades();
}

function viewTradesForToken(tokenId) {
    if (!tokenId) return;
    tradesPage = 0;
    document.getElementById('filter-token').value = tokenId;
    switchTab('trades');
    loadTrades();
}

function goToPosition(tokenId) {
    if (!tokenId) return;
    document.getElementById('position-search').value = tokenId;
    switchTab('positions');
    loadPositions();
}

function predictedDirectionLabel(side, outcomes, outcomeIdx) {
    const selectedOutcome = outcomes?.[outcomeIdx] || '?';
    if (side === 'BUY') {
        return selectedOutcome;
    }
    if (Array.isArray(outcomes) && outcomes.length === 2 && Number.isInteger(outcomeIdx)) {
        return outcomes[1 - outcomeIdx] || `Not ${selectedOutcome}`;
    }
    return `Not ${selectedOutcome}`;
}

function walletEffectBadge(effect) {
    const value = effect || '-';
    const tone = {
        Opened: 'badge-buy',
        Added: 'badge-open',
        Reduced: 'badge-resolved',
        Closed: 'badge-resolved',
        'No Position': 'badge-sell',
        'No Fill': 'badge-sell',
    }[value] || 'badge-open';

    return `<span class="badge ${tone}">${escapeHtml(value)}</span>`;
}

function walletFillBadge(trade) {
    if (trade.no_fill_reason || (trade.paper_size || 0) <= 0) {
        const label = trade.no_fill_reason ? 'No Fill' : 'Skipped';
        return `<span class="badge badge-sell" title="${escapeHtml(trade.no_fill_reason || '')}">${label}</span>`;
    }
    return '<span class="badge badge-buy">Filled</span>';
}

function formatShareValue(value, decimals = 2) {
    if (value == null || value === '' || Number.isNaN(Number(value))) return '-';
    return Number(value).toFixed(decimals);
}

function formatPriceValue(value) {
    if (value == null || value === '' || Number.isNaN(Number(value))) return '-';
    return `$${Number(value).toFixed(4)}`;
}

function renderWalletTradeStatus(trade) {
    const parts = [walletFillBadge(trade)];
    if (trade.position_mismatch_reason) {
        parts.push(`<div class="wallet-trade-note">${escapeHtml(trade.position_mismatch_reason)}</div>`);
    } else if (trade.no_fill_reason) {
        parts.push(`<div class="wallet-trade-note">${escapeHtml(trade.no_fill_reason)}</div>`);
    }
    return parts.join('');
}

function walletTradeRowClass(trade) {
    if (trade.no_fill_reason || (trade.paper_size || 0) <= 0) return 'wallet-row-neutral';
    if ((trade.trade_pnl || 0) > 0.01) return 'wallet-row-positive';
    if ((trade.trade_pnl || 0) < -0.01) return 'wallet-row-negative';
    return 'wallet-row-neutral';
}

function walletPositionRowClass(position) {
    if ((position.total_pnl || 0) > 0.01) return 'wallet-row-positive';
    if ((position.total_pnl || 0) < -0.01) return 'wallet-row-negative';
    return 'wallet-row-neutral';
}

function fmtPercent(value) {
    if (value == null || Number.isNaN(Number(value))) return '-';
    const numeric = Number(value);
    const sign = numeric >= 0 ? '+' : '';
    return `${sign}${numeric.toFixed(1)}%`;
}

function renderWalletSummary(summary) {
    const items = [
        ['Total PnL', fmt$(summary.total_pnl), pnlStatClass(summary.total_pnl)],
        ['Realized', fmt$(summary.realized_pnl), pnlStatClass(summary.realized_pnl)],
        ['Unrealized', fmt$(summary.unrealized_pnl), pnlStatClass(summary.unrealized_pnl)],
        ['Paper Volume', `$${Number(summary.paper_volume || 0).toLocaleString()}`, ''],
        ['Avg Slippage', `${Number(summary.avg_slippage || 0).toFixed(4)}`, pnlClass(-(summary.avg_slippage || 0))],
        ['Avg Latency', `${Number(summary.avg_latency_ms || 0).toFixed(0)}ms`, ''],
        ['Trades', `${summary.filled_trades}/${summary.total_target_trades} filled`, ''],
        ['Wins / Losses', `${summary.winning_trades} / ${summary.losing_trades}`, ''],
        ['Positions', `${summary.active_positions} open`, ''],
    ];

    return items.map(([label, value, cls]) => `
        <div class="card stat-card wallet-stat-card">
            <div class="stat-label">${escapeHtml(label)}</div>
            <div class="stat-value ${cls || ''}">${escapeHtml(value)}</div>
        </div>
    `).join('');
}

function destroyWalletPnlChart() {
    if (walletPnlChart) {
        walletPnlChart.destroy();
        walletPnlChart = null;
    }
}

function renderWalletPnlTimeline(timeline) {
    const chartMeta = document.getElementById('wallet-pnl-meta');
    destroyWalletPnlChart();

    if (!Array.isArray(timeline) || !timeline.length) {
        chartMeta.textContent = 'No filled trade history yet';
        return;
    }

    chartMeta.textContent = `${timeline.length} points · ${timeline[timeline.length - 1].open_positions || 0} open positions`;

    const labels = timeline.map(point => fmtTime(point.ts));
    const realized = timeline.map(point => Number(point.realized_pnl || 0));
    const unrealized = timeline.map(point => Number(point.unrealized_pnl || 0));
    const total = timeline.map(point => Number(point.total_pnl || 0));

    const ctx = document.getElementById('wallet-pnl-chart').getContext('2d');
    walletPnlChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels,
            datasets: [
                {
                    label: 'Realized PnL ($)',
                    data: realized,
                    borderColor: 'rgba(6, 182, 212, 0.95)',
                    backgroundColor: 'rgba(6, 182, 212, 0.14)',
                    fill: false,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'Unrealized PnL ($)',
                    data: unrealized,
                    borderColor: 'rgba(245, 158, 11, 0.95)',
                    backgroundColor: 'rgba(245, 158, 11, 0.14)',
                    fill: false,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2,
                },
                {
                    label: 'Total PnL ($)',
                    data: total,
                    borderColor: 'rgba(99, 102, 241, 1)',
                    backgroundColor: 'rgba(99, 102, 241, 0.12)',
                    fill: true,
                    tension: 0.25,
                    pointRadius: 0,
                    borderWidth: 2.5,
                }
            ]
        },
        options: {
            responsive: true,
            interaction: {
                intersect: false,
                mode: 'index',
            },
            plugins: {
                legend: { labels: { color: '#94a3b8', font: { family: 'Inter' } } },
            },
            scales: {
                x: {
                    ticks: { color: '#64748b', maxTicksLimit: 10 },
                    grid: { color: '#1e293b' },
                },
                y: {
                    ticks: {
                        color: '#64748b',
                        callback: (value) => fmt$(Number(value || 0)),
                    },
                    grid: { color: '#1e293b' },
                }
            }
        }
    });
}

async function openWalletDetail(address, options = {}) {
    const wallet = String(address || '').trim().toLowerCase();
    if (!wallet) return;

    const data = await api(`/api/wallet_detail?wallet=${encodeURIComponent(wallet)}`);
    if (!data || data.error) {
        alert(data ? data.error : 'Failed to load wallet detail');
        return;
    }

    activeWalletDetail = wallet;

    const walletInfo = data.wallet || {};
    const summary = data.summary || {};
    const positions = Array.isArray(data.positions) ? data.positions : [];
    const openPositions = positions.filter(position => String(position.status || '').toLowerCase() === 'open');
    const trades = Array.isArray(data.trades) ? data.trades : [];
    const timeline = Array.isArray(data.pnl_timeline) ? data.pnl_timeline : [];
    const alias = walletInfo.alias || fmtAddr(walletInfo.address || wallet);
    const profileUrl = `https://polymarket.com/profile/${walletInfo.address || wallet}`;
    const statusLabel = walletInfo.tracking_enabled ? 'Tracking enabled' : 'Tracking disabled';

    document.getElementById('wallet-modal-title').innerHTML = `
        <a href="${profileUrl}" target="_blank" rel="noopener noreferrer" class="wallet-link">${escapeHtml(alias)}</a>
    `;
    document.getElementById('wallet-modal-subtitle').innerHTML = `
        <span class="mono">${escapeHtml(walletInfo.address || wallet)}</span>
        <span class="wallet-detail-divider">•</span>
        <span>${escapeHtml(statusLabel)}</span>
        <span class="wallet-detail-divider">•</span>
        <span>${escapeHtml(walletInfo.source || 'manual')}</span>
    `;
    document.getElementById('wallet-modal-summary').innerHTML = renderWalletSummary(summary);
    renderWalletPnlTimeline(timeline);

    document.getElementById('wallet-positions-meta').textContent =
        `${openPositions.length} open markets · ${positions.length} total derived positions`;
    document.getElementById('wallet-trades-meta').textContent =
        `${summary.total_target_trades || 0} tracked trades · ${summary.filled_trades || 0} filled · ${summary.no_fill_trades || 0} no fill`;

    const positionsTbody = document.getElementById('wallet-positions-tbody');
    if (!openPositions.length) {
        positionsTbody.innerHTML = '<tr><td colspan="15" style="text-align:center;">No open copied positions for this wallet.</td></tr>';
    } else {
        positionsTbody.innerHTML = openPositions.map(position => {
            const outcomes = Array.isArray(position.outcomes) ? position.outcomes : [];
            const outcomeLabel = outcomes[position.outcome_idx] || '?';
            const statusBadge = position.status === 'Open'
                ? '<span class="badge badge-open">Open</span>'
                : `<span class="badge badge-resolved">${escapeHtml(position.status)}</span>`;
            const question = position.question || position.token_id || '-';
            const marketLink = position.slug
                ? `<a href="https://polymarket.com/event/${position.slug}" target="_blank" class="market-link">${escapeHtml(question)}</a>`
                : escapeHtml(question);

            return `
                <tr class="${walletPositionRowClass(position)}">
                    <td class="truncate" title="${escapeHtml(question)}">${marketLink}</td>
                    <td>${escapeHtml(position.category || 'Other')}</td>
                    <td>${escapeHtml(outcomeLabel)}</td>
                    <td>${statusBadge}</td>
                    <td class="mono">${fmtTime(position.entry_ts)}</td>
                    <td class="mono">${position.filled_trades || 0}</td>
                    <td class="mono">${Number(position.open_size || 0).toFixed(2)}</td>
                    <td class="mono">$${Number(position.open_cost_basis || 0).toFixed(2)}</td>
                    <td class="mono">$${Number(position.total_cost || 0).toFixed(2)}</td>
                    <td class="mono">${position.avg_entry != null ? `$${Number(position.avg_entry).toFixed(4)}` : '-'}</td>
                    <td class="mono">${position.avg_exit_value != null ? `$${Number(position.avg_exit_value).toFixed(4)}` : '-'}</td>
                    <td class="mono ${pnlClass(position.realized_pnl || 0)}">${fmt$(Number(position.realized_pnl || 0))}</td>
                    <td class="mono ${pnlClass(position.unrealized_pnl || 0)}">${fmt$(Number(position.unrealized_pnl || 0))}</td>
                    <td class="mono ${pnlClass(position.total_pnl || 0)}">${fmt$(Number(position.total_pnl || 0))}</td>
                    <td class="mono ${pnlClass(position.total_pnl || 0)}">${fmtPercent(position.roi_pct)}</td>
                </tr>
            `;
        }).join('');
    }
    reapplyActiveTableSort('wallet-positions-table');

    const tradesTbody = document.getElementById('wallet-trades-tbody');
    if (!trades.length) {
        tradesTbody.innerHTML = '<tr><td colspan="19" style="text-align:center;">No tracked trades for this wallet yet.</td></tr>';
    } else {
        tradesTbody.innerHTML = trades.map(trade => {
            const outcomes = Array.isArray(trade.outcomes) ? trade.outcomes : [];
            const outcomeLabel = outcomes[trade.outcome_idx] || '?';
            const question = trade.question || '-';
            const marketLink = trade.slug
                ? `<a href="https://polymarket.com/event/${trade.slug}" target="_blank" class="market-link">${escapeHtml(question)}</a>`
                : escapeHtml(question);
            const sideBadge = trade.target_side === 'BUY'
                ? '<span class="badge badge-buy">BUY</span>'
                : '<span class="badge badge-sell">SELL</span>';
            const tradePnl = trade.trade_pnl == null ? '-' : fmt$(Number(trade.trade_pnl || 0));
            const tradePnlClass = trade.trade_pnl == null ? '' : pnlClass(Number(trade.trade_pnl || 0));
            const realizedPnl = trade.realized_pnl == null ? '-' : fmt$(Number(trade.realized_pnl || 0));
            const realizedPnlClass = trade.realized_pnl == null ? '' : pnlClass(Number(trade.realized_pnl || 0));
            const exitPercent = trade.source_position_fraction == null ? '-' : fmtPercent(Number(trade.source_position_fraction || 0) * 100);

            return `
                <tr class="${walletTradeRowClass(trade)}">
                    <td><a href="https://polygonscan.com/tx/${trade.tx_hash}" target="_blank" class="tx-link">${fmtTime(trade.onchain_ts)}</a></td>
                    <td class="truncate" title="${escapeHtml(question)}">${marketLink}</td>
                    <td>${escapeHtml(outcomeLabel)}</td>
                    <td>${sideBadge}</td>
                    <td>${walletEffectBadge(trade.position_effect)}</td>
                    <td class="mono">${formatShareValue(trade.target_size, 2)}</td>
                    <td class="mono">${formatPriceValue(trade.target_price)}</td>
                    <td class="mono">${fmt$(Number(trade.target_cost || 0))}</td>
                    <td class="mono">${formatShareValue(trade.requested_size, 2)}</td>
                    <td class="mono">${trade.paper_id ? formatShareValue(trade.paper_size, 2) : '-'}</td>
                    <td class="mono">${trade.paper_id ? formatPriceValue(trade.paper_price) : '-'}</td>
                    <td class="mono">${formatShareValue(trade.source_wallet_position_before, 2)}</td>
                    <td class="mono">${exitPercent}</td>
                    <td class="mono ${pnlClass(-(trade.slippage || 0))}">${trade.paper_id ? Number(trade.slippage || 0).toFixed(4) : '-'}</td>
                    <td class="mono ${tradePnlClass}">${tradePnl}</td>
                    <td class="mono ${realizedPnlClass}">${realizedPnl}</td>
                    <td class="mono">${trade.total_delay_ms != null ? `${Number(trade.total_delay_ms).toFixed(0)}ms` : '-'}</td>
                    <td>${renderWalletTradeStatus(trade)}</td>
                    <td>${trade.paper_id ? `<button class="btn btn-sm btn-ghost" onclick="openOrderBook(${trade.target_id})">Book</button>` : '-'}</td>
                </tr>
            `;
        }).join('');
    }
    reapplyActiveTableSort('wallet-trades-table');

    if (!options.preserveOpen) {
        document.getElementById('wallet-modal').style.display = 'block';
    } else {
        document.getElementById('wallet-modal').style.display = 'block';
    }
}

// â”€â”€ Trades â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function loadTrades() {
    const wallet = document.getElementById('filter-wallet').value;
    const resolved = document.getElementById('filter-resolved').value;
    const category = document.getElementById('filter-category').value;
    const side = document.getElementById('filter-side').value;
    const fillStatus = document.getElementById('filter-fill').value;
    const slippageFilter = document.getElementById('filter-slippage').value;
    const latencyFilter = document.getElementById('filter-latency').value;
    const search = document.getElementById('filter-search').value.trim().toLowerCase();
    const tokenFilter = document.getElementById('filter-token').value.trim();

    const currentTradeFilterKey = JSON.stringify({
        wallet,
        resolved,
        category,
        side,
        fillStatus,
        slippageFilter,
        latencyFilter,
        search,
        tokenFilter,
    });
    if (currentTradeFilterKey !== lastTradeFilterKey) {
        tradesPage = 0;
        lastTradeFilterKey = currentTradeFilterKey;
    }

    let url = `/api/trades?limit=${PAGE_SIZE}&offset=${tradesPage * PAGE_SIZE}`;
    if (wallet) url += `&wallet=${wallet}`;
    if (resolved) url += `&resolved=${resolved}`;
    if (category) url += `&category=${category}`;
    if (tokenFilter) url += `&token_id=${encodeURIComponent(tokenFilter)}`;

    const data = await api(url);
    if (!data) return;

    let filtered = data;
    if (resolved === 'resolved') filtered = filtered.filter(t => isResolvedValue(t.resolved));
    if (resolved === 'unresolved') filtered = filtered.filter(t => !isResolvedValue(t.resolved));
    if (category) filtered = filtered.filter(t => t.category === category);
    if (tokenFilter) filtered = filtered.filter(t => t.token_id === tokenFilter);
    if (side) filtered = filtered.filter(t => t.side === side);
    if (fillStatus === 'filled') filtered = filtered.filter(t => (t.paper_size || 0) > 0);
    if (fillStatus === 'nofill') filtered = filtered.filter(t => (t.paper_size || 0) <= 0 || Boolean(t.no_fill_reason));

    if (slippageFilter === 'adverse') filtered = filtered.filter(t => (t.slippage || 0) > 0);
    if (slippageFilter === 'favorable') filtered = filtered.filter(t => (t.slippage || 0) < 0);
    if (slippageFilter === 'high') filtered = filtered.filter(t => Math.abs(t.slippage || 0) >= 0.02);

    if (latencyFilter === 'fast') filtered = filtered.filter(t => (t.total_delay_ms || 0) < 500);
    if (latencyFilter === 'medium') filtered = filtered.filter(t => (t.total_delay_ms || 0) >= 500 && (t.total_delay_ms || 0) <= 2000);
    if (latencyFilter === 'slow') filtered = filtered.filter(t => (t.total_delay_ms || 0) > 2000);

    if (search) {
        filtered = filtered.filter(t => {
            const outcomes = Array.isArray(t.outcomes) ? t.outcomes : [];
            const outcomeLabel = outcomes[t.outcome_idx] || '';
            const predicted = predictedDirectionLabel(t.side, outcomes, t.outcome_idx);
            const haystack = [
                t.question || '',
                t.group_item_title || '',
                t.category || '',
                outcomeLabel,
                predicted,
                t.wallet || '',
                t.tx_hash || '',
            ].join(' ').toLowerCase();
            return haystack.includes(search);
        });
    }

    const tbody = document.getElementById('trades-tbody');
    tbody.innerHTML = filtered.map(t => {
        const outcomes = Array.isArray(t.outcomes) ? t.outcomes : [];
        const outcomeLabel = outcomes[t.outcome_idx] || '?';
        const predictedLabel = predictedDirectionLabel(t.side, outcomes, t.outcome_idx);
        const question = t.question || '-';
        const groupItemTitle = (t.group_item_title || '').trim();
        const showGroupItem = groupItemTitle && groupItemTitle.toLowerCase() !== (t.category || '').toLowerCase();
        const statusBadge = isResolvedValue(t.resolved)
            ? `<span class="badge badge-resolved">Resolved</span>`
            : `<span class="badge badge-open">Open</span>`;
        const sideBadge = t.side === 'BUY'
            ? '<span class="badge badge-buy">BUY</span>'
            : '<span class="badge badge-sell">SELL</span>';

        const marketLink = t.slug
            ? `<a href="https://polymarket.com/event/${t.slug}" target="_blank" class="market-link">${question.slice(0, 45)}${question.length > 45 ? '...' : ''}</a>`
            : `${question.slice(0, 45)}${question.length > 45 ? '...' : ''}`;

        return `
        <tr>
            <td><a href="https://polygonscan.com/tx/${t.tx_hash}" target="_blank" class="tx-link">${fmtTime(t.onchain_ts)}</a></td>
            <td class="mono"><a href="https://polygonscan.com/address/${t.wallet}" target="_blank" class="wallet-link">${fmtAddr(t.wallet)}</a></td>
            <td class="truncate" title="${question}">${marketLink}<br><small style="color:var(--text-muted)">${outcomeLabel}${showGroupItem ? ` | ${groupItemTitle}` : ''}</small></td>
            <td><span class="badge badge-resolved" style="background:var(--bg-card); border:1px solid var(--border)">${t.category || 'Other'}</span></td>
            <td>${sideBadge}</td>
            <td><span class="badge" style="background:var(--bg-card); border:1px solid var(--border)">${predictedLabel}</span></td>
            <td class="mono">$${(t.target_price || 0).toFixed(4)}</td>
            <td class="mono">$${(t.paper_price || 0).toFixed(4)}</td>
            <td class="mono ${pnlClass(-(t.slippage || 0))}">${t.slippage != null ? t.slippage.toFixed(4) : '-'}</td>
            <td class="mono">${(t.paper_size || 0).toFixed(1)}</td>
            <td class="mono">${t.total_delay_ms != null ? t.total_delay_ms.toFixed(0) + 'ms' : '-'}</td>
            <td>${statusBadge}</td>
            <td><button class="btn btn-sm" onclick="goToPosition('${t.token_id}')">Open</button></td>
            <td><button class="btn btn-sm btn-ghost" onclick="openOrderBook(${t.target_id})">Book</button></td>
        </tr>`;
    }).join('');

    document.getElementById('trades-page-info').textContent = `Page ${tradesPage + 1}`;
    document.getElementById('trades-prev').disabled = tradesPage === 0;
    document.getElementById('trades-next').disabled = filtered.length < PAGE_SIZE;
    reapplyActiveTableSort('trades-table');
}

function tradesNext() { tradesPage++; loadTrades(); }
function tradesPrev() { if (tradesPage > 0) { tradesPage--; loadTrades(); } }

// â”€â”€ Positions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
async function loadPositions() {
    const filter = document.getElementById('position-filter').value;
    const category = document.getElementById('position-category').value;
    const pnlFilter = document.getElementById('position-pnl').value;
    const search = document.getElementById('position-search').value.trim().toLowerCase();

    let url = '/api/positions';
    if (filter) url += `?resolved=${filter}`;

    const data = await api(url);
    if (!data) return;

    let filtered = data;
    if (category) filtered = filtered.filter(p => p.category === category);

    if (pnlFilter) {
        filtered = filtered.filter(p => {
            const totalPnl = (p.realized_pnl || 0) + (p.unrealized_pnl || 0);
            if (pnlFilter === 'winners') return totalPnl > 0.01;
            if (pnlFilter === 'losers') return totalPnl < -0.01;
            if (pnlFilter === 'flat') return Math.abs(totalPnl) <= 0.01;
            return true;
        });
    }

    if (search) {
        filtered = filtered.filter(p => {
            const outcomes = Array.isArray(p.outcomes) ? p.outcomes : [];
            const outcomeLabel = outcomes[p.outcome_idx] || '';
            const haystack = [
                p.question || '',
                p.group_item_title || '',
                p.category || '',
                outcomeLabel,
                p.token_id || '',
            ].join(' ').toLowerCase();
            return haystack.includes(search);
        });
    }

    const tbody = document.getElementById('positions-tbody');
    tbody.innerHTML = filtered.map(p => {
        const outcomes = Array.isArray(p.outcomes) ? p.outcomes : [];
        const outcomeLabel = outcomes[p.outcome_idx] || '?';
        const question = p.question || p.token_id.slice(0, 20) + '...';
        const statusBadge = isResolvedValue(p.resolved)
            ? `<span class="badge badge-resolved">Resolved (${p.payout_value})</span>`
            : `<span class="badge badge-open">Open</span>`;

        const marketLink = p.slug
            ? `<a href="https://polymarket.com/event/${p.slug}" target="_blank" class="market-link">${question.slice(0, 55)}${question.length > 55 ? '...' : ''}</a>`
            : `${question.slice(0, 55)}${question.length > 55 ? '...' : ''}`;

        const sourceWallets = (p.source_wallets || '').split(',').filter(Boolean);
        const walletBadges = sourceWallets.slice(0, 2)
            .map(addr => `<span class="badge" style="background:var(--bg-card); border:1px solid var(--border)">${fmtAddr(addr)}</span>`)
            .join(' ');
        const extraWallets = sourceWallets.length > 2 ? ` <small style="color:var(--text-muted)">+${sourceWallets.length - 2} more</small>` : '';

        return `
        <tr>
            <td class="truncate" title="${question}">${marketLink}</td>
            <td><span class="badge" style="background:var(--bg-card); border:1px solid var(--border)">${p.category || 'Other'}</span></td>
            <td>${outcomeLabel}</td>
            <td>${walletBadges || '-'}${extraWallets}</td>
            <td class="mono">${fmtTime(p.entry_ts)}</td>
            <td class="mono">${fmtTime(p.resolved_ts)}</td>
            <td class="mono">${p.size.toFixed(2)}</td>
            <td class="mono">$${p.cost_basis.toFixed(2)}</td>
            <td class="mono ${pnlClass(p.realized_pnl)}">${fmt$(p.realized_pnl)}</td>
            <td class="mono ${pnlClass(p.unrealized_pnl)}">${fmt$(p.unrealized_pnl)}</td>
            <td>${statusBadge}</td>
            <td><button class="btn btn-sm" onclick="viewTradesForToken('${p.token_id}')">View</button></td>
        </tr>`;
    }).join('');
    reapplyActiveTableSort('positions-table');
}

// â”€â”€ Charts â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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

// â”€â”€ Tab switching â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
        Target Trade ID: ${targetId} | Token: ${data.token_id} | 
        Best Bid: $${(data.best_bid || 0).toFixed(4)} | Best Ask: $${(data.best_ask || 0).toFixed(4)} <br>
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
    if (id === 'wallet-modal') {
        activeWalletDetail = null;
        destroyWalletPnlChart();
    }
}

// Close modal on outside click
window.onclick = function (event) {
    if (event.target.classList.contains('modal')) {
        event.target.style.display = 'none';
        if (event.target.id === 'wallet-modal') {
            activeWalletDetail = null;
            destroyWalletPnlChart();
        }
    }
}

// Ensure functions invoked via inline HTML handlers are available globally.
Object.assign(window, {
    addOrEnableWallet,
    addWallet,
    closeModal,
    filterByWallet,
    goToPosition,
    enableAllLeaderboardWallets,
    loadLeaderboardWallets,
    openWalletDetail,
    openOrderBook,
    refreshAll,
    setLeaderboardTracking,
    setupSortableTables,
    setTradesColumnWidths,
    switchTab,
    toggleTableSort,
    toggleWalletTracking,
    tradesNext,
    tradesPrev,
    viewTradesForToken,
    resetTradesColumnWidths,
});
