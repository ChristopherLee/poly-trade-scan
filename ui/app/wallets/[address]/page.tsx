import { notFound } from "next/navigation";

import { DashboardBackendError } from "@/components/dashboard-backend-error";
import { WalletDetailPage } from "@/components/tracked-wallets-page";
import {
  DashboardApiError,
  getWalletDetail,
  getWalletTrades,
} from "@/dashboard-lib/dashboard-api";

export const dynamic = "force-dynamic";

export default async function WalletPage({
  params,
}: {
  params: Promise<{ address: string }>;
}) {
  const { address } = await params;
  const wallet = address.toLowerCase();

  try {
    const detail = await getWalletDetail(wallet);
    const trades = (await getWalletTrades(wallet)).rows;

    return <WalletDetailPage detail={detail} trades={trades} />;
  } catch (error) {
    if (error instanceof DashboardApiError) {
      if (error.status === 404) {
        notFound();
      }

      return (
        <DashboardBackendError
          title="Wallet detail could not be loaded"
          detail="This page needs wallet summary and trade history data from the Python dashboard API."
          error={error}
        />
      );
    }

    throw error;
  }
}
