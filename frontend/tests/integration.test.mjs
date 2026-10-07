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

test("workflow proxy returns persistent decisions and rejects ineligible drafts", async () => {
  const worklist = await fetch(new URL("/api/worklist", base)).then((response) => response.json());
  const invoice = worklist.items.find((item) => item.documento_ou_historico === "NF 104560");
  assert.ok(invoice);
  const key = `${invoice.origem}:${invoice.linha}`;
  const url = new URL("/api/workflow", base);
  url.searchParams.set("source", invoice.origem);
  url.searchParams.set("line", invoice.linha);
  const response = await fetch(new URL("/api/workflow", base));
  assert.equal(response.status, 200);
  const workflow = await response.json();
  assert.equal(typeof workflow.dataset, "string");
  assert.equal(workflow.cases[key].source, invoice.origem);
  assert.equal(workflow.cases[key].line, Number(invoice.linha));
  assert.ok(Number.isFinite(Date.parse(workflow.cases[key].opened_at)));

  const draft = await fetch(new URL("/api/workflow/draft", base), {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ source: "bank", line: 7 }),
  });
  assert.equal(draft.status, 422);
  assert.match((await draft.json()).error, /crédito sem vínculo/);

  if (!process.env.VANTARA_WORKFLOW_DB || process.env.VANTARA_TEST_WORKFLOW_MUTATIONS !== "1") return;
  const initial = workflow.cases[key];
  const update = async (status, note, version) => fetch(url, {
    method: "PATCH", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner: "Equipe de conferência", status, note, version }),
  });
  const savedResponse = await update("inconclusive", "Conferir comprovante antes de atribuir o crédito.", initial.version);
  assert.equal(savedResponse.status, 200);
  const saved = await savedResponse.json();
  assert.equal(saved.owner, "Equipe de conferência");
  assert.equal(saved.status, "inconclusive");
  assert.equal(saved.version, initial.version + 1);
  assert.equal((await fetch(new URL("/api/workflow", base)).then((result) => result.json())).cases[key].note, saved.note);
  assert.equal((await update("closed", "Conferido", initial.version)).status, 409);
  const closedResponse = await update("closed", "Decisão humana após conferência.", saved.version);
  assert.equal(closedResponse.status, 200);
  const closed = await closedResponse.json();
  const reopenedResponse = await update("open", "Reaberto para revisão da referência.", closed.version);
  assert.equal(reopenedResponse.status, 200);
  const reopened = await reopenedResponse.json();
  assert.equal(reopened.status, "open");
  assert.ok(reopened.history.length >= 3);
});
