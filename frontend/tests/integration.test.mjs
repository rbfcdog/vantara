import assert from "node:assert/strict";
import test from "node:test";

const base = process.env.FRONTEND_URL || "http://127.0.0.1:3000";

test("local proxy exposes the worklist, CSV and a real dossier", async () => {
  const worklist = await fetch(new URL("/api/worklist", base));
  assert.equal(worklist.status, 200);
  const { items } = await worklist.json();
  assert.ok(Array.isArray(items));
  const invoice = items.find((item) => item.documento_ou_historico === "NF 104560");
  assert.ok(invoice, "O export deve conter a NF 104560");
  assert.match(invoice.evidencias, /03_extrato_bancario_jul2026\.csv:17/);
  assert.deepEqual(Object.keys(items[0]), ["prioridade", "origem", "linha", "documento_ou_historico", "valor_brl", "situacao", "evidencias", "proxima_acao"]);

  const csv = await fetch(new URL("/api/worklist.csv", base));
  assert.equal(csv.status, 200);
  assert.match(csv.headers.get("content-disposition") || "", /attachment/);
  assert.match(await csv.text(), /prioridade;origem;linha/);

  const url = new URL("/api/case", base);
  url.searchParams.set("source", invoice.origem);
  url.searchParams.set("line", invoice.linha);
  const response = await fetch(url);
  assert.equal(response.status, 200);
  const dossier = await response.json();
  assert.equal(dossier.kind, "title");
  assert.deepEqual(dossier.linked_credits.map((row) => row.line), [8]);
  assert.deepEqual(dossier.candidate_credits.map((row) => row.line), [17]);
  assert.ok(dossier.related_titles.some((row) => row.source === "omie" && row.line === 4));

  const bankUrl = new URL("/api/case", base);
  bankUrl.searchParams.set("source", "bank");
  bankUrl.searchParams.set("line", "17");
  const bankResponse = await fetch(bankUrl);
  assert.equal(bankResponse.status, 200);
  const bankCase = await bankResponse.json();
  assert.equal(bankCase.kind, "bank");
  assert.ok(bankCase.candidates.some(({ title }) => title.source === "omie" && title.line === 4));
});
