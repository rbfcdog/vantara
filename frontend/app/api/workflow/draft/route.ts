import { backendRequest, backendResponse, connectionError } from "../../backend";

export async function POST(request: Request) {
  let body: unknown;
  try { body = await request.json(); } catch {
    return Response.json({ error: "Informe o caso em JSON." }, { status: 400 });
  }
  if (!body || typeof body !== "object" || !("source" in body) ||
    !["protheus", "omie", "bank"].includes(String(body.source)) ||
    !("line" in body) || !Number.isInteger(body.line) || (body.line as number) < 1) {
    return Response.json({ error: "Informe uma origem e linha válidas para o rascunho." }, { status: 400 });
  }
  try {
    return backendResponse(await backendRequest("/api/workflow/draft", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: body.source, line: body.line }),
    }));
  } catch (error) {
    return connectionError(error);
  }
}
