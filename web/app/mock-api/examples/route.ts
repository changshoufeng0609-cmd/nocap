import { mockEnabled, mockExamples } from "@/lib/mock";

// Mock of GET /api/examples.
export async function GET() {
  if (!mockEnabled()) return Response.json({ message: "Mock mode is off." }, { status: 404 });
  return Response.json(mockExamples);
}
