import { backendRequest, backendResponse, connectionError } from "../../../backend";

export async function POST() {
  try {
    return backendResponse(await backendRequest("/api/demo/operations/run", { method: "POST" }));
  } catch (error) {
    return connectionError(error);
  }
}
