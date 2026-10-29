import { mockEnabled, mockHealth } from "@/lib/mock";

// Mock of GET /api/health.
export async function GET() {
  if (!mockEnabled()) return Response.json({ message: "Mock mode is off." }, { status: 404 });
  return Response.json(mockHealth);
}
