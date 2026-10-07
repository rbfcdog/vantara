import { backendRequest, backendResponse, connectionError } from "../backend";

export async function POST(request: Request) {
  let question: unknown;
  let history: unknown;
  try {
    ({ question, history } = await request.json());
  } catch {
    return Response.json({ error: "Envie uma pergunta em JSON." }, { status: 400 });
  }
  if (typeof question !== "string" || !question.trim()) {
    return Response.json({ error: "Escreva uma pergunta antes de investigar." }, { status: 400 });
  }
  try {
    return backendResponse(await backendRequest("/api/ask", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question: question.trim(), history: history ?? [] }),
    }));
  } catch (error) {
    return connectionError(error);
  }
}
