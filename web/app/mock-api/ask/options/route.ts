import { mockAskOptions, mockEnabled } from "@/lib/mock";

// Mock of GET /api/ask/options, shared by Ask and the Check coverage disclosure.
export async function GET() {
  if (!mockEnabled()) return Response.json({ message: "Mock mode is off." }, { status: 404 });
  return Response.json(mockAskOptions);
}
