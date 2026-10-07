import { backendRequest, backendResponse, connectionError } from "../backend";

export async function GET() {
  try {
    const response = await backendRequest("/api/worklist.csv");
    if (!response.ok) return backendResponse(response);
    return new Response(response.body, {
      headers: {
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": "attachment; filename=vantara-pendencias.csv",
      },
    });
  } catch (error) {
    return connectionError(error);
  }
}
