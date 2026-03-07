import Link from "next/link";

import { DashboardBackendError } from "@/components/dashboard-backend-error";
import { TrackedWalletsDirectoryPage } from "@/components/tracked-wallets-page";
import { DashboardApiError, getTrackedWallets } from "@/dashboard-lib/dashboard-api";

export const dynamic = "force-dynamic";

export default async function Home() {
  try {
    const wallets = await getTrackedWallets();

    return (
      <>
        <div className="mx-auto mt-4 flex max-w-[1600px] justify-end px-4 sm:px-6 lg:px-8">
          <Link
            href="/live"
            className="rounded-full border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-800 transition hover:bg-slate-50"
          >
            Open Live Trading View
          </Link>
        </div>
        <TrackedWalletsDirectoryPage wallets={wallets} />
      </>
    );
  } catch (error) {
    if (error instanceof DashboardApiError) {
      return (
        <DashboardBackendError
          title="Tracked wallets could not be loaded"
          detail="The Next.js UI reached its server-render path, but the Python dashboard API on port 8050 did not answer."
          error={error}
        />
      );
    }

    throw error;
  }
}
