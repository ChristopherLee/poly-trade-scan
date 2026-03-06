import { NextRequest, NextResponse } from "next/server";

import { getWalletTrades } from "@/dashboard-lib/dashboard-api";

export async function GET(request: NextRequest) {
  const wallet = request.nextUrl.searchParams.get("wallet")?.trim().toLowerCase();

  if (!wallet) {
    return NextResponse.json({ error: "missing wallet" }, { status: 400 });
  }

  try {
    const trades = await getWalletTrades(wallet);
    return NextResponse.json(trades);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 },
    );
  }
}
