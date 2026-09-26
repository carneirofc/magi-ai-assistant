// BFF proxy: per-fact memory CRUD. All ids + the optimistic-concurrency version
// travel in the body; status (incl. 409 on stale version) is relayed verbatim.

import { NextResponse } from "next/server";
import { z } from "zod";

import { addFact, deleteFact, updateFact } from "../../lib/admin-api";
import { invalidBody, readJsonBody } from "../../lib/route-body";

const PayloadSchema = z.object({
  userId: z.string().optional(),
  factId: z.string().optional(),
  text: z.string().optional(),
  expectedVersion: z.string().optional(),
});

function relay(res: Response): Promise<NextResponse> {
  return res.text().then(
    (text) =>
      new NextResponse(text, {
        status: res.status,
        headers: { "Content-Type": "application/json" },
      }),
  );
}

export async function POST(req: Request) {
  const b = await readJsonBody(req, PayloadSchema);
  if (!b) return invalidBody();
  if (!b.userId || !b.text) {
    return NextResponse.json({ error: "userId and text required" }, { status: 400 });
  }
  return relay(await addFact(b.userId, b.text, b.expectedVersion));
}

export async function PATCH(req: Request) {
  const b = await readJsonBody(req, PayloadSchema);
  if (!b) return invalidBody();
  if (!b.userId || !b.factId || !b.text) {
    return NextResponse.json({ error: "userId, factId, text required" }, { status: 400 });
  }
  return relay(await updateFact(b.userId, b.factId, b.text, b.expectedVersion));
}

export async function DELETE(req: Request) {
  const b = await readJsonBody(req, PayloadSchema);
  if (!b) return invalidBody();
  if (!b.userId || !b.factId) {
    return NextResponse.json({ error: "userId and factId required" }, { status: 400 });
  }
  return relay(await deleteFact(b.userId, b.factId, b.expectedVersion));
}
