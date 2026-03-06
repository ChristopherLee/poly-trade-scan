import { NextResponse } from "next/server";

import { getLivePnlOverTime } from "@/dashboard-lib/dashboard-api";

export async function GET() {
  try {
    const points = await getLivePnlOverTime();
    return NextResponse.json(points);
  } catch (error) {
    return NextResponse.json(
      { error: error instanceof Error ? error.message : "Unknown error" },
      { status: 500 },
    );
  }
}
