import { SSE_HEADERS, delayBefore, mockEnabled, planCheck, sseFrame } from "@/lib/mock";

// Mock of POST /api/check (docs/api-contract.md): replays a fixture as text/event-stream.
export async function POST(request: Request) {
  if (!mockEnabled()) return Response.json({ message: "Mock mode is off." }, { status: 404 });

  let body: Record<string, unknown>;
  try {
    body = await request.json();
  } catch {
    return Response.json({ message: "Send a JSON body with a text field." }, { status: 400 });
  }
  const plan = planCheck(body ?? {});
  const encoder = new TextEncoder();
  let cancelled = false;
  request.signal?.addEventListener("abort", () => {
    cancelled = true;
  });
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

  const stream = new ReadableStream<Uint8Array>({
    async start(controller) {
      try {
        if (!plan.ok) {
          await sleep(250);
          controller.enqueue(encoder.encode(sseFrame({ event: "error", data: { message: plan.message, retryable: plan.retryable } })));
          return;
        }
        const events = plan.events;
        for (let i = 0; i < events.length; i++) {
          await sleep(delayBefore(events, i));
          if (cancelled) return;
          const bytes = encoder.encode(sseFrame(events[i]));
          if (events[i].event === "meta") {
            controller.enqueue(bytes);
            continue;
          }
          // Split every other frame mid-way (often inside a multi-byte character) so the client's
          // chunk handling is exercised the way a real network would.
          const cut = i % 2 ? Math.floor(bytes.length * 0.57) : bytes.length;
          controller.enqueue(bytes.slice(0, cut));
          if (cut < bytes.length) {
            await sleep(15);
            if (cancelled) return;
            controller.enqueue(bytes.slice(cut));
          }
        }
      } finally {
        try {
          controller.close();
        } catch {}
      }
    },
    cancel() {
      cancelled = true;
    },
  });

  return new Response(stream, { headers: SSE_HEADERS });
}
