import { backendRequest, backendResponse, connectionError } from "../backend";

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url);
  const source = searchParams.get("source");
  const line = searchParams.get("line");
  if (!source || !["protheus", "omie", "bank"].includes(source) || !line || !/^[1-9]\d*$/.test(line)) {
    return Response.json({ error: "Informe uma origem e linha válidas para abrir o caso." }, { status: 400 });
  }
  try {
    return backendResponse(await backendRequest("/api/case", { searchParams: new URLSearchParams({ source, line }) }));
  } catch (error) {
    return connectionError(error);
  }
}
