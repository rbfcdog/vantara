import { backendRequest, backendResponse, connectionError } from "../backend";

export async function GET() {
  try {
    return backendResponse(await backendRequest("/api/workflow"));
  } catch (error) {
    return connectionError(error);
  }
}

export async function PATCH(request: Request) {
  const { searchParams } = new URL(request.url);
  const source = searchParams.get("source");
  const line = searchParams.get("line");
  if (!source || !["protheus", "omie", "bank"].includes(source) || !line || !/^[1-9]\d*$/.test(line)) {
    return Response.json({ error: "Informe uma origem e linha válidas para atualizar o caso." }, { status: 400 });
  }
  let body: unknown;
  try { body = await request.json(); } catch {
    return Response.json({ error: "Envie a decisão em JSON." }, { status: 400 });
  }
  if (!body || typeof body !== "object" ||
    !("owner" in body) || typeof body.owner !== "string" ||
    !("status" in body) || !["open", "inconclusive", "closed"].includes(String(body.status)) ||
    !("note" in body) || typeof body.note !== "string" ||
    !("version" in body) || !Number.isInteger(body.version) || (body.version as number) < 0) {
    return Response.json({ error: "Informe responsável, situação, nota e versão válidos." }, { status: 400 });
  }
  if (body.status !== "open" && !body.note.trim()) {
    return Response.json({ error: "Registre uma nota humana antes de concluir ou marcar como inconclusivo." }, { status: 400 });
  }
  try {
    return backendResponse(await backendRequest("/api/workflow", {
      method: "PATCH", searchParams: new URLSearchParams({ source, line }),
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ owner: body.owner, status: body.status, note: body.note, version: body.version }),
    }));
  } catch (error) {
    return connectionError(error);
  }
}
