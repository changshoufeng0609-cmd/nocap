import data from "../results.json";

export function GET() {
  return new Response(JSON.stringify(data, null, 2), {
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      "Content-Disposition": 'attachment; filename="nocap-financial-qa-records.json"',
      "Cache-Control": "public, max-age=300",
    },
  });
}
