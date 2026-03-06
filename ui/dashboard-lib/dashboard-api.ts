import type {
  LivePnlPoint,
  LiveTrade,
  WalletDetail,
  WalletSummary,
  WalletTradesResponse,
} from "@/dashboard-lib/types";

const BASE_URL = process.env.DASHBOARD_API_BASE_URL || "http://localhost:8050";

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE_URL}${path}`, {
    cache: "no-store",
    next: { revalidate: 0 },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`Dashboard API ${path} failed (${response.status}): ${body}`);
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
