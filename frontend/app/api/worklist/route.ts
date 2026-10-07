import { backendRequest, backendTarget, connectionError } from "../backend";

export async function GET() {
  const target = backendTarget("/api/worklist");
  try {
    const response = await backendRequest("/api/worklist");
    const headers = new Headers({
      "X-Backend-URL": target,
      "Cache-Control": "no-store",
      "Content-Security-Policy": "sandbox; default-src 'none'",
      "X-Content-Type-Options": "nosniff",
    });
    const contentType = response.headers.get("content-type");
    if (contentType) headers.set("Content-Type", contentType);
    return new Response(response.body, { status: response.status, headers });
  } catch (error) {
    const response = connectionError(error);
    response.headers.set("X-Backend-URL", target);
    return response;
  }
}
