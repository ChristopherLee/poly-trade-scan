"use client";

import Link from "next/link";
import { useDeferredValue, useMemo, useState } from "react";

import type {
  RealizedTradePoint,
  WalletDetail,
  WalletSummary,
  WalletTimelinePoint,
  WalletTradeRow,
} from "@/dashboard-lib/types";
import {
  formatAddress,
  formatDuration,
  formatMoney,
  formatPercent,
  formatSignedMoney,
  formatTimestamp,
  pnlTone,
} from "@/dashboard-lib/format";

type SortField =
  | "wallet_total_pnl"
  | "leaderboard_pnl"
  | "paper_volume"
  | "trade_count"
  | "alias";

type PositionSortField = "total_pnl" | "realized_pnl" | "roi_pct" | "open_size" | "avg_entry" | "question";
type TradeSortField = "entry_ts" | "closed_ts" | "total_pnl" | "entry_cost" | "hold_seconds" | "question" | "status";

function compareText(left: string | null | undefined, right: string | null | undefined) {
  return (left || "").localeCompare(right || "", undefined, { sensitivity: "base" });
}

function compareNumber(left: number | null | undefined, right: number | null | undefined) {
  return (left ?? 0) - (right ?? 0);
}

function PaginationControls({
  page,
  totalPages,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  onPageChange: (page: number) => void;
}) {
  if (totalPages <= 1) {
    return null;
  }

  return (
    <div className="flex items-center justify-between gap-3 border-t border-slate-200 px-4 py-3 text-sm text-slate-500">
      <p>
        Page {page} of {totalPages}
      </p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          disabled={page <= 1}
          className="rounded-xl border border-slate-200 px-3 py-2 text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Previous
        </button>
        <button
          type="button"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          disabled={page >= totalPages}
          className="rounded-xl border border-slate-200 px-3 py-2 text-slate-700 transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-40"
        >
          Next
        </button>
      </div>
    </div>
  );
}

export function StatusPill({ status }: { status: string }) {
  const normalized = status.toLowerCase();
  const tone = normalized.includes("open")
    ? "bg-amber-100 text-amber-900"
    : normalized.includes("close")
      ? "bg-emerald-100 text-emerald-900"
      : "bg-slate-100 text-slate-700";

  return (
    <span className={`rounded-full px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.18em] ${tone}`}>
      {status}
    </span>
  );
}

export function MetricCard({
  label,
  value,
  tone,
  note,
}: {
  label: string;
  value: string;
  tone?: string;
  note?: string;
}) {
  return (
    <div className="metric-card">
      <p className="eyebrow">{label}</p>
      <p className={`mt-3 text-3xl font-semibold ${tone ?? "text-slate-950"}`}>{value}</p>
      {note ? <p className="mt-2 text-sm text-slate-500">{note}</p> : null}
    </div>
  );
}

export function PnlWave({ points }: { points: WalletTimelinePoint[] }) {
  if (!points.length) {
    return <div className="flex h-44 items-center justify-center text-sm text-slate-500">No PnL timeline yet.</div>;
  }

  const values = points.map((point) => point.total_pnl);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const positive = (values.at(-1) ?? 0) >= 0;

  const coordinates = points
    .map((point, index) => {
      const x = (index / Math.max(points.length - 1, 1)) * 100;
      const y = 100 - ((point.total_pnl - min) / range) * 100;
      return `${x},${y}`;
    })
    .join(" ");

  const area = `0,100 ${coordinates} 100,100`;

  return (
    <svg viewBox="0 0 100 100" className="h-44 w-full">
      <defs>
        <linearGradient id="wallet-pnl-fill" x1="0" x2="0" y1="0" y2="1">
          <stop offset="0%" stopColor={positive ? "#12715b" : "#b45309"} stopOpacity="0.35" />
          <stop offset="100%" stopColor={positive ? "#12715b" : "#b45309"} stopOpacity="0.04" />
        </linearGradient>
      </defs>
      <path d={`M ${area}`} fill="url(#wallet-pnl-fill)" />
      <polyline
        fill="none"
        points={coordinates}
        stroke={positive ? "#12715b" : "#b45309"}
        strokeWidth="2.5"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

export function RealizedBars({ points }: { points: RealizedTradePoint[] }) {
  if (!points.length) {
    return <div className="flex h-32 items-center justify-center text-sm text-slate-500">No closed trades yet.</div>;
  }

  const sortedPoints = [...points].sort(
    (left, right) => (left.closed_ts ?? left.ts ?? 0) - (right.closed_ts ?? right.ts ?? 0),
  );
  const bucketCount = Math.min(12, sortedPoints.length);
  const chunkSize = Math.max(1, Math.ceil(sortedPoints.length / bucketCount));
  const buckets = Array.from({ length: bucketCount }, (_, index) => {
    const chunk = sortedPoints.slice(index * chunkSize, (index + 1) * chunkSize);
    const first = chunk[0];
    const last = chunk.at(-1) ?? first;
    const realizedPnl = chunk.reduce((total, point) => total + point.realized_pnl, 0);
    const wins = chunk.filter((point) => point.realized_pnl > 0).length;
    const losses = chunk.filter((point) => point.realized_pnl < 0).length;

    return {
      key: `${first.token_id}-${first.closed_ts ?? first.ts}-${index}`,
      realizedPnl,
      tradeCount: chunk.length,
      wins,
      losses,
      firstClosedTs: first.closed_ts ?? first.ts,
      lastClosedTs: last.closed_ts ?? last.ts,
    };
  }).filter((bucket) => bucket.tradeCount > 0);

  const values = buckets.map((bucket) => bucket.realizedPnl);
  const min = Math.min(...values, 0);
  const max = Math.max(...values, 0);
  const range = max - min || 1;
  const zeroY = ((max - 0) / range) * 100;
  const barWidth = 100 / Math.max(buckets.length, 1);
  const yForValue = (value: number) => ((max - value) / range) * 100;
  const bestTrade = sortedPoints.reduce((best, point) =>
    point.realized_pnl > best.realized_pnl ? point : best,
  sortedPoints[0]);
  const worstTrade = sortedPoints.reduce((worst, point) =>
    point.realized_pnl < worst.realized_pnl ? point : worst,
  sortedPoints[0]);
  const latestClosedTs = sortedPoints.at(-1)?.closed_ts ?? sortedPoints.at(-1)?.ts ?? null;

  return (
    <div>
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="rounded-2xl bg-[#f5f1e8] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Best Close</p>
          <p className="mt-2 text-lg font-semibold text-emerald-800">{formatSignedMoney(bestTrade.realized_pnl)}</p>
          <p className="mt-1 truncate text-xs text-slate-500">{bestTrade.question}</p>
        </div>
        <div className="rounded-2xl bg-[#f5f1e8] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Worst Close</p>
          <p className="mt-2 text-lg font-semibold text-amber-800">{formatSignedMoney(worstTrade.realized_pnl)}</p>
          <p className="mt-1 truncate text-xs text-slate-500">{worstTrade.question}</p>
        </div>
        <div className="rounded-2xl bg-[#f5f1e8] px-4 py-3">
          <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">Closed Trades</p>
          <p className="mt-2 text-lg font-semibold text-slate-950">{points.length}</p>
          <p className="mt-1 text-xs text-slate-500">
            Latest close {latestClosedTs ? formatTimestamp(latestClosedTs) : "—"}
          </p>
        </div>
      </div>

      <svg viewBox="0 0 100 100" className="mt-6 h-44 w-full">
        <line
          x1="0"
          x2="100"
          y1={zeroY}
          y2={zeroY}
          stroke="#cbd5e1"
          strokeWidth="1"
          vectorEffect="non-scaling-stroke"
        />
        {buckets.map((bucket, index) => {
          const x = index * barWidth + barWidth * 0.12;
          const width = Math.max(barWidth * 0.76, 1.2);
          const valueY = yForValue(bucket.realizedPnl);
          const y = Math.min(valueY, zeroY);
          const height = Math.max(Math.abs(zeroY - valueY), 1.5);
          const radius = Math.min(width / 2, 3.2);
          const fill = bucket.realizedPnl >= 0 ? "#12715b" : "#b45309";
          const title = [
            `${formatSignedMoney(bucket.realizedPnl)} realized`,
            `${bucket.tradeCount} closes`,
            `${bucket.wins} wins / ${bucket.losses} losses`,
          ].join(" • ");

          return (
            <rect
              key={bucket.key}
              x={x}
              y={y}
              width={width}
              height={height}
              rx={radius}
              ry={radius}
              fill={fill}
            >
              <title>{title}</title>
            </rect>
          );
        })}
      </svg>

      <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {buckets.map((bucket) => {
          const positive = bucket.realizedPnl >= 0;
          const label =
            bucket.firstClosedTs && bucket.lastClosedTs && bucket.firstClosedTs !== bucket.lastClosedTs
              ? `${formatTimestamp(bucket.firstClosedTs)} to ${formatTimestamp(bucket.lastClosedTs)}`
              : formatTimestamp(bucket.lastClosedTs);

          return (
            <div key={`${bucket.key}-label`} className="rounded-2xl border border-slate-200 px-4 py-3">
              <p className="text-[11px] uppercase tracking-[0.18em] text-slate-500">{label}</p>
              <p className={`mt-2 text-lg font-semibold ${positive ? "text-emerald-800" : "text-amber-800"}`}>
                {formatSignedMoney(bucket.realizedPnl)}
              </p>
              <p className="mt-1 text-xs text-slate-500">
                {bucket.tradeCount} closes • {bucket.wins} wins / {bucket.losses} losses
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function PositionsTable({ detail }: { detail: WalletDetail }) {
  if (!detail.positions.length) {
    return <div className="rounded-[24px] border border-dashed border-slate-200 p-8 text-sm text-slate-500">No open copied positions for this wallet.</div>;
  }

  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"Open" | "Resolved" | "Closed" | "All">("Open");
  const [sortField, setSortField] = useState<PositionSortField>("total_pnl");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const deferredSearch = useDeferredValue(search);

  const filteredPositions = useMemo(() => {
    const term = deferredSearch.trim().toLowerCase();
    const direction = sortDirection === "desc" ? -1 : 1;
    const rows = detail.positions.filter((position) => {
      if (statusFilter !== "All" && position.status !== statusFilter) {
        return false;
      }
      if (!term) {
        return true;
      }
      return [
        position.question,
        position.category,
        position.slug,
        position.outcomes[position.outcome_idx] ?? "Outcome",
        position.status,
        position.token_id,
      ]
        .join(" ")
        .toLowerCase()
        .includes(term);
    });

    return [...rows].sort((left, right) => {
      if (sortField === "question") {
        return compareText(left.question, right.question) * direction;
      }
      return compareNumber(left[sortField], right[sortField]) * direction;
    });
  }, [deferredSearch, detail.positions, sortDirection, sortField, statusFilter]);

  const pageSize = 8;
  const totalPages = Math.max(1, Math.ceil(filteredPositions.length / pageSize));
  const currentPage = Math.min(page, totalPages);
  const pageRows = filteredPositions.slice((currentPage - 1) * pageSize, currentPage * pageSize);

  return (
    <div className="overflow-hidden rounded-[24px] border border-slate-200">
      <div className="grid gap-3 border-b border-slate-200 bg-slate-50/70 px-4 py-4 sm:grid-cols-[minmax(0,1fr)_160px_180px_auto]">
        <input
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            setPage(1);
          }}
          placeholder="Search positions, markets, outcomes..."
          className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-emerald-700"
        />
        <select
          value={statusFilter}
          onChange={(event) => {
            setStatusFilter(event.target.value as "Open" | "Resolved" | "Closed" | "All");
            setPage(1);
          }}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-emerald-700"
        >
          <option value="Open">Open only</option>
          <option value="Resolved">Resolved</option>
          <option value="Closed">Closed</option>
          <option value="All">All statuses</option>
        </select>
        <select
          value={sortField}
          onChange={(event) => setSortField(event.target.value as PositionSortField)}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-emerald-700"
        >
          <option value="total_pnl">Sort by Total PnL</option>
          <option value="realized_pnl">Sort by Realized PnL</option>
          <option value="roi_pct">Sort by ROI</option>
          <option value="open_size">Sort by Open Size</option>
          <option value="avg_entry">Sort by Avg Entry</option>
          <option value="question">Sort by Market</option>
        </select>
        <button
          type="button"
          onClick={() => setSortDirection((current) => (current === "desc" ? "asc" : "desc"))}
          className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm font-medium text-slate-950 transition hover:bg-slate-100"
        >
          {sortDirection === "desc" ? "High to low" : "Low to high"}
        </button>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-[#f3efe6] text-left text-[11px] uppercase tracking-[0.18em] text-slate-600">
            <tr>
              <th className="px-4 py-4">Outcome</th>
              <th className="px-4 py-4">Position</th>
              <th className="px-4 py-4">Avg Entry</th>
              <th className="px-4 py-4">Current Mark</th>
              <th className="px-4 py-4">Realized</th>
              <th className="px-4 py-4">Total PnL</th>
              <th className="px-4 py-4">ROI</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {pageRows.map((position) => (
              <tr key={position.token_id}>
                <td className="px-4 py-4">
                  <p className="font-semibold text-slate-950">{position.question}</p>
                  <p className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-500">
                    {position.category} / {position.outcomes[position.outcome_idx] ?? "Outcome"}
                  </p>
                </td>
                <td className="px-4 py-4 text-slate-800">
                  {position.open_size.toFixed(2)} shares
                  <div className="mt-2">
                    <StatusPill status={position.status} />
                  </div>
                </td>
                <td className="px-4 py-4 text-slate-700">{position.avg_entry.toFixed(3)}</td>
                <td className="px-4 py-4 text-slate-700">{position.last_price?.toFixed(3) ?? "—"}</td>
                <td className={`px-4 py-4 font-semibold ${pnlTone(position.realized_pnl)}`}>{formatSignedMoney(position.realized_pnl)}</td>
                <td className={`px-4 py-4 font-semibold ${pnlTone(position.total_pnl)}`}>{formatSignedMoney(position.total_pnl)}</td>
                <td className={`px-4 py-4 font-semibold ${pnlTone(position.roi_pct)}`}>{formatPercent(position.roi_pct)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {!pageRows.length ? (
        <div className="border-t border-slate-200 px-4 py-8 text-sm text-slate-500">No positions match the current filters.</div>
      ) : null}
      <PaginationControls page={currentPage} totalPages={totalPages} onPageChange={setPage} />
    </div>
  );
}

export function TradesTable({ trades }: { trades: WalletTradeRow[] }) {
  if (!trades.length) {
    return <div className="rounded-[24px] border border-dashed border-slate-200 p-8 text-sm text-slate-500">No copied buy outcomes yet.</div>;
  }

  return (
    <div className="overflow-hidden rounded-[24px] border border-slate-200">
      <div className="overflow-x-auto">
        <table className="min-w-full text-sm">
          <thead className="bg-slate-950 text-left text-[11px] uppercase tracking-[0.18em] text-white/60">
            <tr>
              <th className="px-4 py-4">Market</th>
              <th className="px-4 py-4">Status</th>
              <th className="px-4 py-4">Entry</th>
              <th className="px-4 py-4">Hold</th>
              <th className="px-4 py-4">Cost</th>
              <th className="px-4 py-4">Exit</th>
              <th className="px-4 py-4">PnL</th>
              <th className="px-4 py-4">Notes</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100 bg-white">
            {trades.map((trade) => (
              <tr key={`${trade.entry_target_id}-${trade.token_id}`}>
                <td className="px-4 py-4">
                  <div className="max-w-sm">
                    <p className="font-semibold text-slate-950">{trade.question}</p>
                    <p className="mt-1 text-xs uppercase tracking-[0.18em] text-slate-500">
                      {trade.category} / {trade.outcome}
                    </p>
                  </div>
                </td>
                <td className="px-4 py-4">
                  <StatusPill status={trade.status} />
                </td>
                <td className="px-4 py-4">
                  <p className="font-medium text-slate-950">{formatTimestamp(trade.entry_ts)}</p>
                  <p className="mt-1 text-xs text-slate-500">
                    {trade.entry_size.toFixed(2)} shares at {trade.entry_price?.toFixed(3) ?? "—"}
                  </p>
                </td>
                <td className="px-4 py-4 text-slate-500">{formatDuration(trade.hold_seconds)}</td>
                <td className="px-4 py-4 text-slate-700">{formatMoney(trade.entry_cost)}</td>
                <td className="px-4 py-4 text-slate-700">
                  {trade.avg_exit_value != null ? `${trade.avg_exit_value.toFixed(3)} avg` : "—"}
                </td>
                <td className={`px-4 py-4 font-semibold ${pnlTone(trade.total_pnl)}`}>{formatSignedMoney(trade.total_pnl)}</td>
                <td className="px-4 py-4 text-slate-500">{trade.notes || trade.close_reason || "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function TrackedWalletsDirectoryPage({
  wallets,
}: {
  wallets: WalletSummary[];
}) {
  const [search, setSearch] = useState("");
  const [sortField, setSortField] = useState<SortField>("wallet_total_pnl");
  const [sortDirection, setSortDirection] = useState<"asc" | "desc">("desc");

  const filteredWallets = useMemo(() => {
    const term = search.trim().toLowerCase();
    const filtered = term
      ? wallets.filter((wallet) =>
          [wallet.alias, wallet.address, wallet.source]
            .join(" ")
            .toLowerCase()
            .includes(term),
        )
      : wallets;

    const direction = sortDirection === "desc" ? -1 : 1;

    return [...filtered].sort((left, right) => {
      if (sortField === "alias") {
        const a = (left.alias || left.address).toLowerCase();
        const b = (right.alias || right.address).toLowerCase();
        return a.localeCompare(b) * direction;
      }

      return (((left[sortField] as number) ?? 0) - ((right[sortField] as number) ?? 0)) * direction;
    });
  }, [wallets, search, sortField, sortDirection]);

  const aggregate = wallets.reduce(
    (accumulator, wallet) => {
      accumulator.walletCount += 1;
      accumulator.tradeCount += wallet.trade_count || 0;
      accumulator.paperVolume += wallet.paper_volume || 0;
      accumulator.totalPnl += wallet.wallet_total_pnl || 0;
      return accumulator;
    },
    { walletCount: 0, tradeCount: 0, paperVolume: 0, totalPnl: 0 },
  );

  return (
    <main className="mx-auto min-h-screen max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
      <section className="panel-hero relative overflow-hidden p-8 sm:p-10">
        <div className="grid gap-8 lg:grid-cols-[1.35fr_0.9fr]">
          <div>
            <p className="eyebrow text-white/50">Poly Trade Scan / Tracked Wallets</p>
            <h1 className="mt-4 max-w-3xl text-4xl font-semibold leading-tight text-white sm:text-5xl">
              Wallet directory first, dedicated detail page second.
            </h1>
            <p className="mt-4 max-w-2xl text-base text-white/72 sm:text-lg">
              Use the table to scan and sort tracked wallets, then open a dedicated wallet page for the full copied-trade breakdown.
            </p>
          </div>

          <div className="grid gap-4 sm:grid-cols-2">
            <MetricCard label="Tracked Wallets" value={`${aggregate.walletCount}`} note={`${aggregate.tradeCount} target trades observed`} />
            <MetricCard label="Copied Volume" value={formatMoney(aggregate.paperVolume, true)} note="Paper cost routed through the simulator" />
            <MetricCard label="Wallet PnL" value={formatSignedMoney(aggregate.totalPnl)} tone={pnlTone(aggregate.totalPnl)} note="Aggregate copied performance" />
            <MetricCard label="Navigation" value="Dedicated" note="Each wallet opens on its own page" />
          </div>
        </div>
      </section>

      <section className="panel mt-6 p-5 sm:p-6">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="eyebrow">Tracked Wallets</p>
            <h2 className="mt-2 text-2xl font-semibold text-slate-950">Directory</h2>
            <p className="mt-2 text-sm text-slate-500">
              Search by alias, address, or source. Click a row to open the wallet page.
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_180px_auto]">
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search wallets, addresses, sources..."
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-emerald-700"
            />
            <select
              value={sortField}
              onChange={(event) => setSortField(event.target.value as SortField)}
              className="rounded-2xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-950 outline-none transition focus:border-emerald-700"
            >
              <option value="wallet_total_pnl">Sort by Copied PnL</option>
              <option value="leaderboard_pnl">Sort by Leaderboard PnL</option>
              <option value="paper_volume">Sort by Paper Volume</option>
              <option value="trade_count">Sort by Trades</option>
              <option value="alias">Sort by Name</option>
            </select>
            <button
              onClick={() => setSortDirection((current) => (current === "desc" ? "asc" : "desc"))}
              className="rounded-2xl border border-slate-200 bg-slate-950 px-4 py-3 text-sm font-medium text-white transition hover:bg-slate-800"
            >
              {sortDirection === "desc" ? "High to low" : "Low to high"}
            </button>
          </div>
        </div>

        <div className="mt-5 overflow-hidden rounded-[24px] border border-slate-200">
          <div className="overflow-x-auto">
            <table className="min-w-full text-sm">
              <thead className="bg-[#f3efe6] text-left text-[11px] uppercase tracking-[0.18em] text-slate-600">
                <tr>
                  <th className="px-4 py-4">Wallet</th>
                  <th className="px-4 py-4">Source</th>
                  <th className="px-4 py-4">Copied PnL</th>
                  <th className="px-4 py-4">Paper Volume</th>
                  <th className="px-4 py-4">Trades</th>
                  <th className="px-4 py-4">Leaderboard PnL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100 bg-white">
                {filteredWallets.map((wallet) => (
                  <tr key={wallet.address} className="transition hover:bg-emerald-50">
                    <td className="px-4 py-4">
                      <Link href={`/wallets/${wallet.address}`} className="block">
                        <p className="font-semibold text-slate-950">{wallet.alias || formatAddress(wallet.address)}</p>
                        <p className="mt-1 font-[var(--font-mono)] text-xs text-slate-500">{wallet.address}</p>
                      </Link>
                    </td>
                    <td className="px-4 py-4 uppercase tracking-[0.18em] text-slate-500">
                      {wallet.source.replace("leaderboard:", "")}
                    </td>
                    <td className={`px-4 py-4 font-semibold ${pnlTone(wallet.wallet_total_pnl)}`}>
                      {formatSignedMoney(wallet.wallet_total_pnl)}
                    </td>
                    <td className="px-4 py-4 text-slate-700">{formatMoney(wallet.paper_volume)}</td>
                    <td className="px-4 py-4 text-slate-700">{wallet.trade_count}</td>
                    <td className="px-4 py-4 text-slate-700">{formatMoney(wallet.leaderboard_pnl, true)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </main>
  );
}

export function WalletDetailPage({
  detail,
  trades,
}: {
  detail: WalletDetail;
  trades: WalletTradeRow[];
}) {
  return (
    <main className="mx-auto min-h-screen max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
      <section className="panel-hero relative overflow-hidden p-8 sm:p-10">
        <div className="flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <p className="eyebrow text-white/50">Wallet Detail</p>
            <h1 className="mt-4 text-4xl font-semibold leading-tight text-white sm:text-5xl">
              {detail.wallet.alias || formatAddress(detail.wallet.address)}
            </h1>
            <p className="mt-3 font-[var(--font-mono)] text-sm text-white/65">{detail.wallet.address}</p>
          </div>
          <div className="flex gap-3">
            <Link
              href="/"
              className="rounded-full border border-white/20 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/10"
            >
              Back to wallets
            </Link>
            <a
              href={`https://polymarket.com/profile/${detail.wallet.address}`}
              target="_blank"
              rel="noreferrer"
              className="rounded-full bg-white px-4 py-2 text-sm font-medium text-slate-950 transition hover:bg-slate-100"
            >
              Open Polymarket profile
            </a>
          </div>
        </div>
      </section>

      <section className="panel mt-6 overflow-hidden">
        <div className="grid gap-0 lg:grid-cols-[1.2fr_0.8fr]">
          <div className="border-b border-slate-200 p-6 sm:p-8 lg:border-b-0 lg:border-r">
            <p className="eyebrow">Summary</p>
            <div className="mt-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
              <MetricCard
                label="Total PnL"
                value={formatSignedMoney(detail.summary.total_pnl)}
                tone={pnlTone(detail.summary.total_pnl)}
                note={`${detail.summary.winning_trades} wins / ${detail.summary.losing_trades} losses`}
              />
              <MetricCard
                label="Realized"
                value={formatSignedMoney(detail.summary.realized_pnl)}
                tone={pnlTone(detail.summary.realized_pnl)}
                note={`${detail.summary.active_positions} active positions`}
              />
              <MetricCard
                label="Copied Volume"
                value={formatMoney(detail.summary.paper_volume)}
                note={`${detail.summary.filled_trades}/${detail.summary.total_target_trades} fills`}
              />
              <MetricCard
                label="Avg Latency"
                value={detail.summary.avg_latency_ms ? `${Math.round(detail.summary.avg_latency_ms)}ms` : "—"}
                note={`${detail.summary.no_fill_buy_entry_count} no-fill entries`}
              />
            </div>
          </div>

          <div className="bg-[#f5f1e8] p-6 sm:p-8">
            <p className="eyebrow">Context</p>
            <dl className="mt-4 space-y-4 text-sm">
              <div>
                <dt className="text-slate-500">Source</dt>
                <dd className="mt-1 text-lg font-semibold text-slate-950">{detail.wallet.source}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Tracking</dt>
                <dd className="mt-1 text-lg font-semibold text-slate-950">
                  {detail.wallet.tracking_enabled ? "Enabled" : "Disabled"}
                </dd>
              </div>
              <div>
                <dt className="text-slate-500">Added</dt>
                <dd className="mt-1 text-lg font-semibold text-slate-950">{formatTimestamp(detail.wallet.added_at)}</dd>
              </div>
              <div>
                <dt className="text-slate-500">Leaderboard Volume</dt>
                <dd className="mt-1 text-lg font-semibold text-slate-950">{formatMoney(detail.wallet.leaderboard_vol, true)}</dd>
              </div>
            </dl>
          </div>
        </div>
      </section>

      <section className="mt-6 grid gap-6 xl:grid-cols-[1.2fr_0.8fr]">
        <div className="panel p-6 sm:p-8">
          <div className="flex items-center justify-between gap-4">
            <div>
              <p className="eyebrow">Total PnL Timeline</p>
              <h3 className="mt-2 text-2xl font-semibold text-slate-950">Marked to current outcome state</h3>
            </div>
            <p className={`text-lg font-semibold ${pnlTone(detail.summary.total_pnl)}`}>
              {formatSignedMoney(detail.summary.total_pnl)}
            </p>
          </div>
          <div className="mt-8">
            <PnlWave points={detail.pnl_timeline} />
          </div>
        </div>

        <div className="panel p-6 sm:p-8">
          <p className="eyebrow">Realized Trade PnL</p>
          <h3 className="mt-2 text-2xl font-semibold text-slate-950">Realized PnL by close window</h3>
          <div className="mt-8">
            <RealizedBars points={detail.realized_trade_points} />
          </div>
          <p className="mt-5 text-sm text-slate-500">
            Latest close captured at{" "}
            {detail.realized_trade_points.length
              ? formatTimestamp(detail.realized_trade_points.at(-1)?.closed_ts ?? null)
              : "—"}
            .
          </p>
        </div>
      </section>

      <section className="panel mt-6 p-6 sm:p-8">
        <div className="mb-5 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Open Positions</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-950">Inventory still on the book</h3>
          </div>
          <p className="text-sm text-slate-500">{detail.positions.length} tracked outcomes</p>
        </div>
        <PositionsTable detail={detail} />
      </section>

      <section className="panel mt-6 p-6 sm:p-8">
        <div className="mb-5 flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Copied Trade History</p>
            <h3 className="mt-2 text-2xl font-semibold text-slate-950">Buy outcomes with close-state attribution</h3>
          </div>
          <p className="text-sm text-slate-500">Showing {trades.length} rows</p>
        </div>
        <TradesTable trades={trades} />
        <div className="mt-4 grid gap-4 text-sm text-slate-500 sm:grid-cols-3">
          <p>Buy entries: {detail.summary.buy_entry_count}</p>
          <p>Filled buy entries: {detail.summary.filled_buy_entry_count}</p>
          <p>Avg slippage: {detail.summary.avg_slippage.toFixed(4)}</p>
        </div>
      </section>
    </main>
  );
}
