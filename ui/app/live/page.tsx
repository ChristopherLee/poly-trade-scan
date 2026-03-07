import { DashboardBackendError } from "@/components/dashboard-backend-error";
import { LiveTradingPage } from "@/components/live-trading-page";
import {
  DashboardApiError,
  getLivePnlOverTime,
  getLiveTrades,
} from "@/dashboard-lib/dashboard-api";

export const dynamic = "force-dynamic";

export default async function LivePage() {
  try {
    const [trades, pnlPoints] = await Promise.all([getLiveTrades(150), getLivePnlOverTime()]);
    return <LiveTradingPage trades={trades} pnlPoints={pnlPoints} />;
  } catch (error) {
    if (error instanceof DashboardApiError) {
      return (
        <DashboardBackendError
          title="Live trading data could not be loaded"
          detail="This view depends on the Python dashboard API for live trade rows and cash delta points."
          error={error}
        />
      );
    }

    throw error;
  }
}
