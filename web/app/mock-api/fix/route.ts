import { mockEnabled, SSE_HEADERS, sseFrame } from "@/lib/mock";

/** No invented correction results: this operation needs the verifier and Wonder on the real API. */
export async function POST() {
  if (!mockEnabled()) return Response.json({ message: "Mock mode is off." }, { status: 404 });
  return new Response(sseFrame({ event: "error", data: {
    message: "Fix with receipts needs the live Receipts API. Mock mode only replays draft checks.",
    retryable: false,
  } }), { headers: SSE_HEADERS });
}
