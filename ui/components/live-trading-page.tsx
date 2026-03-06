import Link from "next/link";

import { formatAddress, formatMoney, formatTimestamp, pnlTone } from "@/dashboard-lib/format";
import type { LivePnlPoint, LiveTrade } from "@/dashboard-lib/types";

function CashDeltaChart({ points }: { points: LivePnlPoint[] }) {
  if (!points.length) {
    return <div className="rounded-2xl border border-dashed border-slate-200 p-6 text-sm text-slate-500">No live cash delta points yet.</div>;
  }

  const values = points.map((p) => p.cash_delta_cumulative);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min || 1;
  const coordinates = points
    .map((point, index) => {
      const x = (index / Math.max(points.length - 1, 1)) * 100;
      const y = 100 - ((point.cash_delta_cumulative - min) / range) * 100;
      return `${x},${y}`;
    })
    .join(" ");

  const last = values[values.length - 1] ?? 0;

  return (
    <div>
      <p className={`text-lg font-semibold ${pnlTone(last)}`}>Current cumulative cash delta: {formatMoney(last)}</p>
      <svg viewBox="0 0 100 100" className="mt-4 h-48 w-full rounded-2xl bg-slate-50 p-2">
        <polyline
          fill="none"
          points={coordinates}
          stroke="#7c3aed"
          strokeWidth="2.4"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
    </div>
  );
}

export function LiveTradingPage({ trades, pnlPoints }: { trades: LiveTrade[]; pnlPoints: LivePnlPoint[] }) {
  return (
    <main className="mx-auto min-h-screen max-w-[1600px] px-4 py-6 sm:px-6 lg:px-8">
      <section className="panel-hero relative overflow-hidden p-8 sm:p-10">
        <div className="flex items-end justify-between gap-4">
          <div>
            <p className="eyebrow text-white/50">Poly Trade Scan / React UI</p>
            <h1 className="mt-4 text-4xl font-semibold leading-tight text-white sm:text-5xl">Live Trading</h1>
            <p className="mt-4 text-white/75">Audit view for live trade attempts, execution mode, and cumulative cash impact.</p>
          </div>
          <Link href="/" className="rounded-full border border-white/20 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/10">
            Back to wallets
          </Link>
        </div>
      </section>

      <section className="panel mt-6 p-6 sm:p-8">
        <p className="eyebrow">Cash Usage</p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Cumulative cash delta over live trades</h2>
        <div className="mt-6">
          <CashDeltaChart points={pnlPoints} />
        </div>
      </section>

      <section className="panel mt-6 p-6 sm:p-8">
        <p className="eyebrow">Live trades</p>
        <h2 className="mt-2 text-2xl font-semibold text-slate-950">Recent executions and rejections</h2>

        <div className="mt-6 overflow-x-auto">
          <table className="min-w-full divide-y divide-slate-200 text-sm">
            <thead className="bg-slate-50 text-left text-xs uppercase tracking-[0.16em] text-slate-500">
              <tr>
                <th className="px-4 py-3">Time</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Side</th>
                <th className="px-4 py-3">Market</th>
                <th className="px-4 py-3">Source Wallet</th>
                <th className="px-4 py-3">Requested</th>
                <th className="px-4 py-3">Filled</th>
                <th className="px-4 py-3">Notional</th>
                <th className="px-4 py-3">Mode</th>
                <th className="px-4 py-3">Flags</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100 bg-white">
              {trades.length ? trades.map((trade) => (
                <tr key={trade.id}>
                  <td className="px-4 py-3 text-slate-600">{formatTimestamp(trade.created_at)}</td>
                  <td className="px-4 py-3 font-medium text-slate-900">{trade.status}</td>
                  <td className="px-4 py-3 text-slate-700">{trade.side}</td>
                  <td className="px-4 py-3 text-slate-700">{trade.question || trade.token_id}</td>
                  <td className="px-4 py-3 font-mono text-slate-500">{formatAddress(trade.source_wallet)}</td>
                  <td className="px-4 py-3 text-slate-700">{trade.requested_size.toFixed(4)}</td>
                  <td className="px-4 py-3 text-slate-700">{trade.filled_size.toFixed(4)}</td>
                  <td className="px-4 py-3 text-slate-700">{formatMoney(trade.notional_usd)}</td>
                  <td className="px-4 py-3 text-slate-700">{trade.execution_mode || "—"}</td>
                  <td className="px-4 py-3 text-xs text-slate-500">{(trade.risk_flags || []).join(", ") || "—"}</td>
                </tr>
              )) : (
                <tr>
                  <td className="px-4 py-6 text-center text-slate-500" colSpan={10}>No live trades found.</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}
