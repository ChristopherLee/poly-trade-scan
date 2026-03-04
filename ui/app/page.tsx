import { TrackedWalletsDirectoryPage } from "@/components/tracked-wallets-page";
import { getTrackedWallets } from "@/lib/dashboard-api";

export const dynamic = "force-dynamic";

export default async function Home() {
  const wallets = await getTrackedWallets();
  return <TrackedWalletsDirectoryPage wallets={wallets} />;
}
