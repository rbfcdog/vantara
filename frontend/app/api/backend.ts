const backendUrl = process.env.BACKEND_URL || "http://127.0.0.1:8000";

type BackendPath = "/api/worklist" | "/api/worklist.csv" | "/api/case" | "/api/ask" | "/api/exports";

export function backendTarget(path: BackendPath) {
  const url = new URL(path, backendUrl);
  return `${url.host}${url.pathname}`;
}

export async function backendRequest(path: BackendPath, options?: RequestInit & { searchParams?: URLSearchParams }) {
  const url = new URL(path, backendUrl);
  const { searchParams, ...init } = options ?? {};
  if (searchParams) url.search = searchParams.toString();
  return fetch(url, { ...init, cache: "no-store", signal: AbortSignal.timeout(path === "/api/ask" || path === "/api/exports" ? 120000 : 30000) });
}

export async function backendResponse(response: Response) {
  if (!response.ok) {
    let detail = `A API retornou ${response.status}. Confira os dados e tente novamente.`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (typeof body.error === "string") detail = body.error;
    } catch {
      detail = `A API retornou ${response.status}. Confira se o serviço local está funcionando.`;
    }
    return Response.json({ error: detail }, { status: response.status });
  }
  return new Response(response.body, { status: response.status, headers: { "Content-Type": "application/json; charset=utf-8" } });
}

export function connectionError(error: unknown) {
  const message = error instanceof Error && error.name === "TimeoutError"
    ? "A API demorou a responder. Confira se o serviço local está funcionando e tente novamente."
    : "Não foi possível conectar à API local. Inicie o backend e verifique BACKEND_URL.";
  return Response.json({ error: message }, { status: 502 });
}
