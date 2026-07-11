// BFF proxy: the MCP server registry settings (GET reads code + operator
// lists; PUT replaces the operator list — merged over code by name at team
// assembly, applied on restart). Mount at `app/api/admin/settings/mcp/route.ts`.

import { NextResponse } from "next/server";

import { adminGet, adminRequest } from "../../../lib/admin-api";

export async function GET() {
  try {
    return NextResponse.json(await adminGet("/admin/v1/settings/mcp"));
  } catch {
    return NextResponse.json({ error: "admin-api unreachable" }, { status: 503 });
  }
}

export async function PUT(req: Request) {
  const body = await req.text();
  const res = await adminRequest("/admin/v1/settings/mcp", { method: "PUT", body });
  return new NextResponse(await res.text(), {
    status: res.status,
    headers: { "Content-Type": "application/json" },
  });
}
