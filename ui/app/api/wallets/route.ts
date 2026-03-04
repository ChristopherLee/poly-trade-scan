import { NextResponse } from "next/server";

import { getTrackedWallets } from "@/lib/dashboard-api";

export async function GET() {
  try {
    const wallets = await getTrackedWallets();
    return NextResponse.json(wallets);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 },
    );
  }
}
