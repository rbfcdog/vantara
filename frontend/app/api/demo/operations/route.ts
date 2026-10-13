import { backendRequest, backendResponse, connectionError } from "../../backend";

export async function GET() {
  try {
    return backendResponse(await backendRequest("/api/demo/operations"));
  } catch (error) {
    return connectionError(error);
  }
}
