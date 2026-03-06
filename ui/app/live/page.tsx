import { LiveTradingPage } from "@/components/live-trading-page";
import { getLivePnlOverTime, getLiveTrades } from "@/dashboard-lib/dashboard-api";

export const dynamic = "force-dynamic";

export default async function LivePage() {
  const [trades, pnlPoints] = await Promise.all([getLiveTrades(150), getLivePnlOverTime()]);
  return <LiveTradingPage trades={trades} pnlPoints={pnlPoints} />;
}
