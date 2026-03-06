export function formatAddress(value?: string | null, chars = 6) {
  if (!value) return "—";
  if (value.length <= chars * 2) return value;
  return `${value.slice(0, chars)}…${value.slice(-chars)}`;
}

export function formatMoney(value?: number | null, compact = false) {
  const amount = Number(value ?? 0);
  if (compact) {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      notation: "compact",
      maximumFractionDigits: 2,
    }).format(amount);
  }
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(amount);
}

export function formatSignedMoney(value?: number | null) {
  const amount = Number(value ?? 0);
  const sign = amount > 0 ? "+" : "";
  return `${sign}${formatMoney(amount)}`;
}

export function formatPercent(value?: number | null) {
  const amount = Number(value ?? 0);
  return `${amount.toFixed(2)}%`;
}

export function formatTimestamp(value?: number | null) {
  if (!value) return "—";
  return new Date(value * 1000).toLocaleString();
}

export function formatDuration(seconds?: number | null) {
  const s = Number(seconds ?? 0);
  if (!s) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

export function pnlTone(value?: number | null) {
  const amount = Number(value ?? 0);
  if (amount > 0.001) return "text-emerald-700";
  if (amount < -0.001) return "text-amber-700";
  return "text-slate-700";
}
