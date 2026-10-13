"use client";

import { useEffect, useState } from "react";

type WorkItem = {
  origem: string;
  linha: string;
  documento_ou_historico: string;
  valor_brl: string;
  situacao: string;
  evidencias: string;
  proxima_acao: string;
};
type PayablesCase = {
  invoice: string;
  bank_entries: { line: number; amount: string }[];
  observations: { kind: string; text: string; evidence: string[] }[];
  next_query: string;
  recommendation: string;
};
type CreditSuggestion = {
  bank: { id: string; line: number; date: string; history: string; document: string; amount: string };
  candidates: { id: string; documento: string; cliente: string; valor: string; vencimento: string; reasons: string[] }[];
  next_action: string;
  verdict?: "candidato_mais_provavel" | "sem_atribuicao";
  candidate?: string | null;
  rationale?: string;
  evidence?: string[];
  confirm_with?: string;
};
type AppliedWriteoff = {
  event_line: number;
  document: string;
  amount: string;
  kind: string;
  balance_after: string;
  receipt: string;
  evidence: string[];
  rationale: string;
};
type AutonomousExecution = {
  mode: string;
  status: string;
  erp_writeoffs_performed: number;
  applied: AppliedWriteoff[];
  pending: { bank_line: number; reason: string; next_query: string }[];
};
type CaseState = {
  source: string;
  line: number;
  status: "open" | "inconclusive" | "closed";
  owner: string;
  note: string;
  updated_at: string;
  version: number;
};
type Closing = {
  processed_at: string;
  summary: {
    coverage: { bank_accounts_supplied: number; bank_accounts_in_case: number };
    receivables: { unlinked_credits: number };
    erp_writeoffs_performed: number;
  };
  items: WorkItem[];
  payables_review: { status: string; cases: PayablesCase[] } | null;
  receivables_suggestions: { status: string; suggestions: CreditSuggestion[] } | null;
  autonomous_execution: AutonomousExecution | null;
};
type Load = { state: "loading" } | { state: "error"; message: string } | { state: "ready"; data: Closing };

const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const dateTime = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

async function json<T>(response: Response): Promise<T> {
  const result = await response.json().catch(() => { throw new Error("O serviço não retornou JSON válido."); });
  if (!response.ok) throw new Error(result.detail ?? result.error ?? `Falha HTTP ${response.status}.`);
  return result as T;
}

function References({ values }: { values: string[] }) {
  return <div className="references" aria-label="Fontes consultadas">{values.map((value) => <code key={value}>{value}</code>)}</div>;
}

function Decision({ id, state, onDone }: { id: string; state: CaseState; onDone: (value: CaseState) => void }) {
  const [form, setForm] = useState<{ intent: "closed" | "inconclusive" | "open" } | null>(null);
  const [owner, setOwner] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => { setOwner(window.localStorage.getItem("vantara:owner") ?? ""); }, []);
  const chip = state.status === "closed" ? "Resolvido" : state.status === "inconclusive" ? "Inconclusivo" : "Pendente";
  const chipClass = state.status === "closed" ? "chip-done" : state.status === "inconclusive" ? "chip-warn" : "chip-open";

  async function submit(intent: "closed" | "inconclusive" | "open") {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const updated = await json<CaseState>(await fetch(`/api/workflow?source=${state.source}&line=${state.line}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner, status: intent, note, version: state.version }),
      }));
      window.localStorage.setItem("vantara:owner", owner);
      onDone(updated);
      setForm(null);
      setNote("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Não foi possível registrar a decisão.");
    } finally {
      setBusy(false);
    }
  }

  return <div className="decision">
    <span className={`chip ${chipClass}`}>{chip}</span>
    {state.status !== "open" && <p className="decision-meta">{state.owner || "—"} · <time dateTime={state.updated_at}>{dateTime.format(new Date(state.updated_at))}</time>{state.note ? ` · “${state.note}”` : ""}</p>}
    {!form && state.status === "open" && <div className="decision-actions">
      <button type="button" className="action" disabled={busy} onClick={() => { setForm({ intent: "closed" }); setError(""); }}>Resolver</button>
      <button type="button" className="action action-quiet" disabled={busy} onClick={() => { setForm({ intent: "inconclusive" }); setError(""); }}>Inconclusivo</button>
    </div>}
    {!form && state.status !== "open" && <div className="decision-actions">
      <button type="button" className="action action-quiet" disabled={busy} onClick={() => { setForm({ intent: "open" }); setError(""); }}>Reabrir</button>
    </div>}
    {form && <div className="decision-form">
      <label>Responsável<input value={owner} onChange={(event) => setOwner(event.target.value)} autoComplete="name" required /></label>
      <label>{form.intent === "open" ? "Motivo da reabertura" : "Nota da decisão"}<textarea value={note} onChange={(event) => setNote(event.target.value)} rows={2} required /></label>
      <div className="decision-form-actions">
        <button type="button" className="action" disabled={busy || !owner.trim() || !note.trim()} onClick={() => { void submit(form.intent); }}>{busy ? "Registrando…" : form.intent === "closed" ? "Confirmar resolução" : form.intent === "inconclusive" ? "Marcar inconclusivo" : "Reabrir caso"}</button>
        <button type="button" className="action action-quiet" disabled={busy} onClick={() => { setForm(null); setError(""); }}>Cancelar</button>
      </div>
    </div>}
    {error && <p className="decision-error" role="alert">{error} <button type="button" className="link" onClick={() => { void submit(form?.intent ?? "closed"); }}>Tentar de novo</button></p>}
  </div>;
}

export default function Home() {
  const [report, setReport] = useState<Load>({ state: "loading" });
  const [cases, setCases] = useState<Record<string, CaseState>>({});
  const [retry, setRetry] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setReport({ state: "loading" });
    Promise.all([
      fetch("/api/worklist", { cache: "no-store", signal: controller.signal }).then((response) => json<{ processed_at: string; summary: Closing["summary"]; items: WorkItem[]; payables_review: Closing["payables_review"]; receivables_suggestions: Closing["receivables_suggestions"]; autonomous_execution: Closing["autonomous_execution"]; dataset: string }>(response)),
      fetch("/api/workflow", { cache: "no-store", signal: controller.signal }).then((response) => json<{ dataset: string; cases: Record<string, CaseState> }>(response)),
    ]).then(([closing, workflow]) => {
      if (!closing?.summary?.coverage || !closing.summary.receivables ||
          typeof closing.summary.erp_writeoffs_performed !== "number" || !Array.isArray(closing.items) ||
          !Number.isFinite(Date.parse(closing.processed_at))) throw new Error("Publicação incompleta. Confira os quatro exports.");
      if (!controller.signal.aborted) { setReport({ state: "ready", data: closing }); setCases(workflow.cases); }
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setReport({ state: "error", message: error instanceof Error ? error.message : "Publicação indisponível." });
    });
    return () => controller.abort();
  }, [retry]);

  const data = report.state === "ready" ? report.data : null;
  const duplicates = data?.items.filter((item) => item.situacao === "possivel duplicidade entre sistemas") ?? [];
  const payableCases = data?.payables_review?.cases ?? [];
  const pending = [...duplicates.map((item) => `${item.origem}:${item.linha}`),
                   ...payableCases.map((item) => `bank:${item.bank_entries[0]?.line}`)]
    .filter((id) => cases[id]?.status === "open").length;
  const done = duplicates.length + payableCases.length - pending;
  const suggestions = data?.receivables_suggestions?.suggestions ?? [];
  const appliedByLine = new Map((data?.autonomous_execution?.applied ?? []).map((row) => [row.event_line, row]));
  const appliedCount = appliedByLine.size;

  return <main className="workspace">
    <header className="topbar"><span className="brand">VANTARA</span><span className="topbar-label">Financeiro</span></header>
    <div className="page-heading"><div><h1>Revisões financeiras</h1><p>Exceções que precisam de decisão</p></div>{data && <span className="timestamp">Atualizado em <time dateTime={data.processed_at}>{dateTime.format(new Date(data.processed_at))}</time></span>}</div>

    {report.state === "loading" && <p className="state" role="status">Carregando publicação…</p>}
    {report.state === "error" && <section className="state" role="alert"><h2>Publicação indisponível</h2><p>{report.message}</p><button type="button" onClick={() => setRetry((value) => value + 1)}>Tentar novamente</button><p>Nenhuma conciliação publicada ainda; ela aparece aqui assim que os exports chegarem.</p></section>}

    {data && <>
      <section className="coverage" aria-label="Cobertura da conciliação"><dl><div><dt>Contas recebidas</dt><dd>{data.summary.coverage.bank_accounts_supplied} / {data.summary.coverage.bank_accounts_in_case}</dd></div><div><dt>Créditos sem vínculo</dt><dd>{data.summary.receivables.unlinked_credits}</dd></div><div><dt>Baixas aplicadas pelo executor</dt><dd>{appliedCount}</dd></div><div><dt>Baixas no ERP</dt><dd>{data.summary.erp_writeoffs_performed}</dd></div></dl><p>{data.summary.coverage.bank_accounts_in_case - data.summary.coverage.bank_accounts_supplied} contas ainda sem extrato; a conciliação atualiza assim que chegam.</p></section>

      <section className="reviews" aria-labelledby="reviews-title"><div className="section-heading"><h2 id="reviews-title">Decisões pendentes</h2><span>{pending} {pending === 1 ? "pendente" : "pendentes"}{done > 0 ? ` · ${done} ${done === 1 ? "resolvida" : "resolvidas"}` : ""}</span></div>
        {duplicates.map((item) => {
          const id = `${item.origem}:${item.linha}`;
          return <article className={`review${cases[id]?.status !== "open" ? " review-done" : ""}`} key={id}><div className="review-top"><span>Recebíveis · {item.documento_ou_historico}</span><strong>R$ {item.valor_brl}</strong></div><h3>Possível duplicidade entre ERPs</h3><p>{item.proxima_acao}</p><details><summary>Ver evidências</summary><div className="detail-body"><p>Mesmo cliente, valor e vencimento em Protheus e Omie. Confirme qual título representa a obrigação antes de qualquer baixa.</p><References values={item.evidencias.split(", ")} /></div></details>{cases[id] && <Decision id={id} state={cases[id]} onDone={(value) => setCases((current) => ({ ...current, [id]: value }))} />}</article>;
        })}
        {payableCases.map((item) => {
          const id = `bank:${item.bank_entries[0]?.line}`;
          return <article className={`review${cases[id]?.status !== "open" ? " review-done" : ""}`} key={item.invoice}><div className="review-top"><span>Contas a pagar · {item.invoice}</span><strong>{item.bank_entries.map((entry) => currency.format(Number(entry.amount))).join(" · ")}</strong></div><h3>Conferir antes de outro pagamento</h3><p>{item.recommendation}</p><details><summary>Ver evidências</summary><div className="detail-body"><References values={item.bank_entries.map((entry) => `03_extrato_bancario_jul2026.csv:${entry.line}`)} />{item.observations.length ? <ul>{item.observations.map((observation, index) => <li key={`${observation.kind}-${index}`}><strong>{observation.kind.replaceAll("_", " ")}</strong><p>{observation.text}</p><References values={observation.evidence} /></li>)}</ul> : <p>Análise de mensagens indisponível nesta atualização; as evidências e a próxima consulta seguem abaixo.</p>}<p><strong>Próxima consulta:</strong> {item.next_query}</p></div></details>{cases[id] && <Decision id={id} state={cases[id]} onDone={(value) => setCases((current) => ({ ...current, [id]: value }))} />}</article>;
        })}
        {duplicates.length + payableCases.length === 0 && <p className="empty">Nenhuma exceção aguardando decisão.</p>}
      </section>
      {suggestions.length > 0 && <section className="reviews" aria-labelledby="credits-title"><div className="section-heading"><h2 id="credits-title">Créditos sem vínculo</h2><span>{suggestions.length} analisados pela IA · {appliedCount} baixas aplicadas · {suggestions.length - appliedCount} retidas</span></div>
        {suggestions.map((item) => {
          const applied = appliedByLine.get(item.bank.line) ?? null;
          const candidate = item.verdict === "candidato_mais_provavel" ? item.candidates.find((entry) => entry.id === item.candidate) ?? null : null;
          return <details className="credit" key={item.bank.line}><summary><span className="credit-row"><span>{item.bank.history}</span><strong>{currency.format(Number(item.bank.amount))}</strong></span>{applied ? <span className="chip chip-done">Baixa aplicada</span> : item.verdict && <span className={`chip ${item.verdict === "candidato_mais_provavel" ? "chip-warn" : "chip-open"}`}>{item.verdict === "candidato_mais_provavel" ? "Retida pela validação" : "Sem atribuição"}</span>}</summary>
          <div className="detail-body">
            {applied ? <>
              <p>Atribuída a <strong>{applied.document}</strong> pela decisão da IA; baixa {applied.kind === "total" ? "total" : "parcial"} de {currency.format(Number(applied.amount))} registrada no executor, saldo {currency.format(Number(applied.balance_after))}.</p>
              <p>{applied.rationale}</p>
              <References values={applied.evidence} />
              <p>Recibo: <code>{applied.receipt}</code></p>
            </> : item.verdict ? <>
              {candidate && <p>Título apontado: <strong>{candidate.documento} · {candidate.cliente} · R$ {candidate.valor} · vence {candidate.vencimento}</strong></p>}
              <p>{item.rationale}</p>
              <References values={item.evidence ?? []} />
              <p><strong>Próxima consulta:</strong> {item.confirm_with}</p>
            </> : <p>{item.next_action}</p>}
            {item.candidates.length > 0 && <details><summary>Ver candidatos pelas regras</summary><ul>{item.candidates.map((entry) => <li key={entry.id}><code>{entry.id}</code> {entry.documento} · {entry.cliente} · R$ {entry.valor} · pistas: {entry.reasons.join(", ")}</li>)}</ul></details>}
          </div></details>;
        })}
        <p className="scope-note">A IA decide a atribuição e o executor aplica automaticamente com recibo; o que falta prova fica retido com a próxima consulta.</p>
      </section>}
      <p className="scope-note">Baixas do executor ficam no livro local com recibo idempotente; escrita no ERP requer conector homologado.</p>
    </>}
  </main>;
}
