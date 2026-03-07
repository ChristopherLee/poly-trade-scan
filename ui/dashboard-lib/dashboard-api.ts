import "server-only";

import type {
  LivePnlPoint,
  LiveTrade,
  WalletDetail,
  WalletSummary,
  WalletTradesResponse,
} from "@/dashboard-lib/types";

const DEFAULT_BASE_URL = "http://127.0.0.1:8050";

function getApiBase(): string {
  return (
    process.env.DASHBOARD_API_BASE ??
    process.env.DASHBOARD_API_BASE_URL ??
    process.env.NEXT_PUBLIC_DASHBOARD_API_BASE ??
    DEFAULT_BASE_URL
  );
}

export class DashboardApiError extends Error {
  status?: number;
  path: string;
  baseUrl: string;

  constructor(message: string, options: { path: string; baseUrl: string; status?: number }) {
    super(message);
    this.name = "DashboardApiError";
    this.status = options.status;
    this.path = options.path;
    this.baseUrl = options.baseUrl;
  }
}

async function fetchJson<T>(path: string): Promise<T> {
  const baseUrl = getApiBase();
  let response: Response;

  try {
    response = await fetch(`${baseUrl}${path}`, {
      cache: "no-store",
      next: { revalidate: 0 },
    });
  } catch {
    throw new DashboardApiError(
      `Dashboard backend unavailable at ${baseUrl}. Start dashboard.py or set DASHBOARD_API_BASE.`,
      { path, baseUrl },
    );
  }

  if (!response.ok) {
    const body = await response.text();
    throw new DashboardApiError(
      `Dashboard API ${path} failed (${response.status}): ${body || response.statusText}`,
      { path, baseUrl, status: response.status },
    );
  }

  return (await response.json()) as T;
}

export async function getTrackedWallets(): Promise<WalletSummary[]> {
  return fetchJson<WalletSummary[]>("/api/wallets");
}

export async function getWalletDetail(wallet: string): Promise<WalletDetail> {
  return fetchJson<WalletDetail>(`/api/wallet_detail?wallet=${encodeURIComponent(wallet)}`);
}

export async function getWalletTrades(wallet: string): Promise<WalletTradesResponse> {
  return fetchJson<WalletTradesResponse>(`/api/wallet_detail_trades?wallet=${encodeURIComponent(wallet)}`);
}

export async function getLiveTrades(limit = 100): Promise<LiveTrade[]> {
  return fetchJson<LiveTrade[]>(`/api/live_trades?limit=${limit}`);
}

export async function getLivePnlOverTime(): Promise<LivePnlPoint[]> {
  return fetchJson<LivePnlPoint[]>("/api/live_pnl_over_time");
}
