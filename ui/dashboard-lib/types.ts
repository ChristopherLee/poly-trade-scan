export type WalletSummary = {
  address: string;
  alias: string;
  source: string;
  leaderboard_pnl: number;
  leaderboard_vol: number;
  trade_count: number;
  paper_volume: number;
  wallet_total_pnl: number;
};

export type WalletTimelinePoint = {
  ts: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  open_positions: number;
};

export type RealizedTradePoint = {
  token_id: string;
  ts?: number;
  closed_ts?: number | null;
  realized_pnl: number;
  question?: string;
};

export type WalletTradeRow = {
  entry_ts: number;
  closed_ts?: number | null;
  question: string;
  outcome?: string;
  category?: string;
  status: string;
  hold_seconds?: number | null;
  entry_size: number;
  entry_price?: number | null;
  entry_cost: number;
  avg_exit_value?: number | null;
  total_pnl: number;
  notes?: string;
  close_reason?: string;
  [key: string]: any;
};

export type WalletPosition = {
  token_id: string;
  question: string;
  category?: string;
  slug?: string;
  outcomes: string[];
  outcome_idx: number;
  status: "Open" | "Resolved" | "Closed" | string;
  open_size: number;
  avg_entry: number;
  cost_basis: number;
  realized_pnl: number;
  unrealized_pnl: number;
  total_pnl: number;
  roi_pct?: number | null;
  last_price?: number | null;
  [key: string]: any;
};

export type WalletDetail = {
  wallet: {
    address: string;
    alias: string;
    source: string;
    tracking_enabled: boolean;
    added_at?: number | null;
    leaderboard_vol: number;
    [key: string]: any;
  };
  summary: {
    total_pnl: number;
    realized_pnl: number;
    paper_volume: number;
    filled_trades: number;
    total_target_trades: number;
    avg_latency_ms?: number | null;
    no_fill_buy_entry_count: number;
    winning_trades: number;
    losing_trades: number;
    active_positions: number;
    buy_entry_count: number;
    filled_buy_entry_count: number;
    avg_slippage: number;
    [key: string]: any;
  };
  positions: WalletPosition[];
  pnl_timeline: WalletTimelinePoint[];
  realized_trade_points: RealizedTradePoint[];
};

export type WalletTradesResponse = {
  rows: WalletTradeRow[];
  total: number;
};

export type LiveTrade = {
  id: number;
  token_id: string;
  source_wallet: string;
  side: string;
  requested_size: number;
  filled_size: number;
  avg_price: number;
  notional_usd: number;
  status: string;
  risk_flags: string[];
  audit_ref?: string | null;
  tx_hash?: string | null;
  exchange_order_id?: string | null;
  execution_mode?: string | null;
  error_message?: string | null;
  question?: string | null;
  category?: string | null;
  created_at: number;
};

export type LivePnlPoint = {
  ts: number;
  cash_delta_cumulative: number;
};
