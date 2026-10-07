import { backendRequest, backendResponse, connectionError } from "../backend";

const sourceFiles = [
  "01_contas_a_receber_protheus_jul2026.csv",
  "02_titulos_unidade_norte_omie_jul2026.csv",
  "03_extrato_bancario_jul2026.csv",
  "04_caixa_de_entrada_contas_a_pagar.txt",
];
const maxFileBytes = 2 * 1024 * 1024;
const maxRequestBytes = 11_200_000;

export async function POST(request: Request) {
  if (request.headers.get("content-type")?.split(";")[0].trim().toLowerCase() !== "application/json") {
    return Response.json({ error: "Envie os quatro exports em JSON." }, { status: 415 });
  }
  const reader = request.body?.getReader();
  if (!reader) return Response.json({ error: "Envie os quatro arquivos originais." }, { status: 400 });
  let size = 0;
  let text = "";
  const decoder = new TextDecoder("utf-8", { fatal: true });
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxRequestBytes) {
        await reader.cancel();
        return Response.json({ error: "Os exports excedem o limite de 2 MiB por arquivo." }, { status: 413 });
      }
      text += decoder.decode(value, { stream: true });
    }
    text += decoder.decode();
  } catch {
    return Response.json({ error: "Não foi possível ler os arquivos enviados. Selecione-os novamente." }, { status: 400 });
  }
  let payload: unknown;
  try { payload = JSON.parse(text); } catch {
    return Response.json({ error: "Envie os quatro exports em JSON válido." }, { status: 400 });
  }
  if (!payload || typeof payload !== "object" || !("files" in payload) || !payload.files ||
    typeof payload.files !== "object" || Array.isArray(payload.files) || Object.keys(payload).length !== 1) {
    return Response.json({ error: "Selecione exatamente os quatro arquivos originais." }, { status: 400 });
  }
  const files = payload.files as Record<string, unknown>;
  const names = Object.keys(files);
  if (names.length !== sourceFiles.length || names.some((name) => !sourceFiles.includes(name))) {
    return Response.json({ error: "Selecione os quatro exports originais sem renomear nem adicionar arquivos." }, { status: 400 });
  }
  for (const name of sourceFiles) {
    const encoded = files[name];
    if (typeof encoded !== "string" || encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]*={0,2}$/.test(encoded) ||
      (encoded.length / 4) * 3 - (encoded.endsWith("==") ? 2 : encoded.endsWith("=") ? 1 : 0) > maxFileBytes) {
      return Response.json({ error: `${name} está inválido ou excede 2 MiB.` }, { status: 400 });
    }
  }
  try {
    return backendResponse(await backendRequest("/api/exports", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: text,
    }));
  } catch (error) {
    return connectionError(error);
  }
}
