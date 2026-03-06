import { notFound } from "next/navigation";

import { WalletDetailPage } from "@/components/tracked-wallets-page";
import {
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
  let detail;
  try {
    detail = await getWalletDetail(wallet);
  } catch {
    notFound();
  }
  const trades = (await getWalletTrades(wallet)).rows;

  return <WalletDetailPage detail={detail} trades={trades} />;
}
