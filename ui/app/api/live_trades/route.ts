import { NextRequest, NextResponse } from "next/server";

import { getLiveTrades } from "@/dashboard-lib/dashboard-api";

export async function GET(request: NextRequest) {
  const limitParam = request.nextUrl.searchParams.get("limit") || "100";
  const limit = Math.max(1, Math.min(500, Number(limitParam) || 100));

  try {
    const rows = await getLiveTrades(limit);
    return NextResponse.json(rows);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 },
    );
  }
}
