"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";

type Source = "protheus" | "omie" | "bank";
type Priority = "alta" | "media" | "baixa";
type WorkItem = {
  prioridade: string;
  origem: Source;
  linha: string;
  documento_ou_historico: string;
  valor_brl: string;
  situacao: string;
  evidencias: string;
  proxima_acao: string;
};
type Title = {
  source: Source;
  file: string;
  line: number;
  document: string;
  reference: string;
  customer: string;
  tax_id: string;
  due: string;
  amount: string;
  export_status: string;
  status: string;
  next_action: string;
  identified_credit?: string;
  difference_before_unlinked_credits?: string;
};
type Bank = {
  file: string;
  line: number;
  date: string;
  history: string;
  document: string;
  amount: string;
  type: string;
  status: string;
  next_action: string;
};
type Case =
  | { kind: "title"; title: Title; linked_credits: Bank[]; candidate_credits: Bank[]; related_titles: Title[] }
  | { kind: "bank"; bank: Bank; candidates: { title: Title; reasons: string[] }[] };
type QueueState =
  | { kind: "loading"; attempt: number }
  | { kind: "ready"; items: WorkItem[]; processedAt: string }
  | { kind: "error"; failure: QueueFailure };
type QueueFailure = { message: string; status?: number; contentType?: string; backendUrl?: string; peek?: string };
type ChatTurn = { question: string; answer?: string; error?: string };
type WorkflowStatus = "open" | "inconclusive" | "closed";
type WorkflowCase = {
  source: Source; line: number; status: WorkflowStatus; owner: string; note: string;
  opened_at: string; updated_at: string; version: number;
  history: { status: WorkflowStatus; owner: string; note: string; at: string }[];
};
type WorkflowState =
  | { kind: "loading" }
  | { kind: "ready"; dataset: string; cases: Record<string, WorkflowCase> }
  | { kind: "error"; message: string };
type GroupKind = "situacao" | "prioridade" | "responsavel" | "idade";
type GroupFilter = { kind: GroupKind; value: string } | null;
type Editing = { key: string; owner: string; note: string; status: WorkflowStatus };


const exportFiles = [
  "01_contas_a_receber_protheus_jul2026.csv",
  "02_titulos_unidade_norte_omie_jul2026.csv",
  "03_extrato_bancario_jul2026.csv",
  "04_caixa_de_entrada_contas_a_pagar.txt",
] as const;
const maxFileBytes = 2 * 1024 * 1024;


const sourceNames: Record<Source, string> = { protheus: "Protheus", omie: "Omie", bank: "Banco" };
const priorityNames: Record<Priority, string> = { alta: "Alta", media: "Média", baixa: "Baixa" };
const priorityRank: Record<Priority, number> = { alta: 0, media: 1, baixa: 2 };
const reasonNames: Record<string, string> = {
  mesmo_valor: "Mesmo valor",
  cnpj_no_historico: "CNPJ no histórico",
  nome_parecido: "Nome semelhante",
};
const currency = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

function money(amount: string) {
  const value = Number(amount);
  return Number.isFinite(value) ? currency.format(value) : amount;
}

function evidence(file: string, line: number | string) {
  return `${file}:${line}`;
}

function displayDate(value: string) {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  return match ? `${match[3]}/${match[2]}/${match[1]}` : value;
}

async function readJson<T>(response: Response): Promise<T> {
  const body = await response.json().catch(() => {
    throw new Error("A API retornou uma resposta ilegível. Confira o serviço local.");
  });
  if (!response.ok) throw new Error(body && typeof (body.error ?? body.detail) === "string" ? body.error ?? body.detail : "A API não concluiu a solicitação. Tente novamente.");
  return body as T;
}
async function readQueue(response: Response): Promise<{ items: WorkItem[]; processedAt: string }> {
  const bytes = new Uint8Array(await response.arrayBuffer());
  const text = new TextDecoder().decode(bytes);
  const contentType = response.headers.get("content-type") ?? undefined;
  const backendUrl = response.headers.get("x-backend-url") ?? undefined;
  const peek = new TextDecoder().decode(bytes.subarray(0, 200));
  let body: unknown;
  if (contentType && /(?:^|\/|\+)json(?:\s*;|$)/i.test(contentType)) {
    try { body = JSON.parse(text); } catch { body = undefined; }
  }
  const detail = body && typeof body === "object" && "error" in body && typeof body.error === "string"
    ? body.error : body && typeof body === "object" && "detail" in body && typeof body.detail === "string"
      ? body.detail : undefined;
  const jsonResponse = !!contentType && /(?:^|\/|\+)json(?:\s*;|$)/i.test(contentType);
  const failure: QueueFailure = {
    message: !jsonResponse ? "O serviço respondeu algo que não é JSON. Verifique se o serviço local está de pé e na porta certa." :
      !response.ok ? detail ?? "O backend não conseguiu carregar a fila. Confira o serviço local." :
      "A API retornou uma fila inválida. Confira o backend.",
    status: response.status, contentType, backendUrl, peek,
  };
  if (!response.ok || !jsonResponse || !body || typeof body !== "object") throw failure;
  if (!("items" in body) || !Array.isArray(body.items) || !("processed_at" in body) ||
    typeof body.processed_at !== "string" || !Number.isFinite(Date.parse(body.processed_at)) ||
    !body.items.every((item: unknown) => item && typeof item === "object" &&
      ["prioridade", "origem", "linha", "documento_ou_historico", "valor_brl", "situacao", "evidencias", "proxima_acao"]
        .every((field) => field in item && typeof (item as Record<string, unknown>)[field] === "string") &&
      "origem" in item && (item.origem === "protheus" || item.origem === "omie" || item.origem === "bank"))) throw failure;
  return { items: body.items as WorkItem[], processedAt: body.processed_at };
}

function queueFailure(error: unknown): QueueFailure {
  if (error && typeof error === "object" && "status" in error && "message" in error && typeof error.message === "string") return error as QueueFailure;
  return { message: "Não foi possível carregar a fila. Confira a conexão com o serviço local." };
}

function processedTime(value: string) {
  return new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" }).format(new Date(value));
}
function openAge(openedAt: string) {
  const days = Math.max(0, Math.floor((Date.now() - Date.parse(openedAt)) / 86400000));
  return days === 0 ? "Hoje" : days < 3 ? "1–2 dias" : days < 7 ? "3–6 dias" : "7+ dias";
}

function workflowLabel(status: WorkflowStatus) {
  return { open: "Em aberto", inconclusive: "Inconclusivo", closed: "Encerrado" }[status];
}

function timestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
}

async function readWorkflow(response: Response, items: WorkItem[]) {
  const data = await readJson<{ dataset: string; cases: Record<string, WorkflowCase> }>(response);
  if (!data || typeof data.dataset !== "string" || !data.dataset || !data.cases || typeof data.cases !== "object" || Array.isArray(data.cases) ||
    items.some((item) => {
      const workflow = data.cases[`${item.origem}:${item.linha}`];
      return !workflow || workflow.source !== item.origem || workflow.line !== Number(item.linha) ||
        !["open", "inconclusive", "closed"].includes(workflow.status) || typeof workflow.owner !== "string" ||
        typeof workflow.note !== "string" || !Number.isFinite(Date.parse(workflow.opened_at)) ||
        !Number.isFinite(Date.parse(workflow.updated_at)) || !Number.isInteger(workflow.version) || workflow.version < 0 ||
        !Array.isArray(workflow.history) || workflow.history.some((entry) => !entry ||
          !["open", "inconclusive", "closed"].includes(entry.status) || typeof entry.owner !== "string" ||
          typeof entry.note !== "string" || !Number.isFinite(Date.parse(entry.at)));
    })) throw new Error("O serviço retornou um fluxo incompleto para a fila atual. Atualize os dados.");
  return data;
}

function encodeFile(file: File): Promise<string> {
  const { promise, resolve, reject } = Promise.withResolvers<string>();
  const reader = new FileReader();
  reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
  reader.onerror = () => reject(new Error(`Não foi possível ler ${file.name}. Selecione o arquivo novamente.`));
  reader.readAsDataURL(file);
  return promise;
}


function Evidence({ references }: { references: string[] }) {
  if (!references.length) return null;
  return <div className="evidence-list" aria-label="Referências das fontes">{references.map((reference, index) => <span className="evidence-ref" key={`${reference}-${index}`}>{reference}</span>)}</div>;
}

function CreditList({ credits, candidate }: { credits: Bank[]; candidate: boolean }) {
  return <section className={`evidence-section ${candidate ? "candidate-section" : "linked-section"}`}>
    <div className="section-heading"><div><p className="eyebrow">{candidate ? "Pistas sem atribuição" : "Vínculo por documento"}</p><h3>{candidate ? "Créditos candidatos" : "Créditos vinculados"}</h3></div><span className="section-count">{credits.length}</span></div>
    {credits.length ? <div className="record-list">{credits.map((credit) => <article className="record" key={evidence(credit.file, credit.line)}>
      <div className="record-top"><strong>{money(credit.amount)}</strong><span>{displayDate(credit.date)}</span></div>
      <p>{credit.history}</p>
      <Evidence references={[evidence(credit.file, credit.line)]} />
    </article>)}</div> : <p className="section-empty">{candidate ? "Nenhum crédito sem referência foi apontado para este título." : "Nenhum crédito foi vinculado pelo número do documento."}</p>}
    {candidate && credits.length > 0 && <p className="section-note">Coincidência de nome ou valor não comprova quitação.</p>}
  </section>;
}

function TitleCase({ dossier }: { dossier: Extract<Case, { kind: "title" }> }) {
  const { title, linked_credits, candidate_credits, related_titles } = dossier;
  return <>
    <div className="case-heading"><p className="eyebrow">Título · {sourceNames[title.source]}</p><h2>{title.document}</h2><p className="case-customer">{title.customer}</p></div>
    <div className="case-amount"><span>Valor exportado</span><strong>{money(title.amount)}</strong></div>
    <dl className="fact-grid">
      <div><dt>Situação</dt><dd>{title.status.replaceAll("_", " ")}</dd></div>
      <div><dt>Vencimento</dt><dd>{displayDate(title.due)}</dd></div>
      <div><dt>Status no ERP</dt><dd>{title.export_status}</dd></div>
      <div><dt>Referência</dt><dd className="mono">{title.reference}</dd></div>
      {title.identified_credit !== undefined && <div><dt>Crédito identificado</dt><dd>{money(title.identified_credit)}</dd></div>}
      {title.difference_before_unlinked_credits !== undefined && <div><dt>Diferença antes das pistas</dt><dd>{money(title.difference_before_unlinked_credits)}</dd></div>}
    </dl>
    {title.difference_before_unlinked_credits !== undefined && <p className="fact-warning">Esta diferença não é saldo confirmado.</p>}
    <Evidence references={[evidence(title.file, title.line)]} />
    <CreditList credits={linked_credits} candidate={false} />
    <CreditList credits={candidate_credits} candidate />
    <section className="evidence-section related-section"><div className="section-heading"><div><p className="eyebrow">Mesmo CNPJ · não necessariamente a mesma fatura</p><h3>Outros títulos</h3></div><span className="section-count">{related_titles.length}</span></div>
      {related_titles.length ? <div className="record-list">{related_titles.map((related) => <article className="record" key={evidence(related.file, related.line)}>
        <div className="record-top"><strong>{related.document}</strong><span>{money(related.amount)}</span></div>
        <p>{sourceNames[related.source]} · {related.export_status}</p>
        <Evidence references={[evidence(related.file, related.line)]} />
      </article>)}</div> : <p className="section-empty">Nenhum outro título do mesmo CNPJ neste export.</p>}
    </section>
    <div className="next-action"><p className="eyebrow">Próxima conferência</p><p>{title.next_action}</p></div>
  </>;
}

function BankCase({ dossier, references }: { dossier: Extract<Case, { kind: "bank" }>; references: string[] }) {
  const { bank, candidates } = dossier;
  return <>
    <div className="case-heading"><p className="eyebrow">Lançamento · banco</p><h2>{bank.history}</h2><p className="case-customer">{bank.type === "D" ? "Débito" : "Crédito"} no extrato</p></div>
    <div className="case-amount"><span>Valor no extrato</span><strong>{money(bank.amount)}</strong></div>
    <dl className="fact-grid">
      <div><dt>Situação</dt><dd>{bank.status.replaceAll("_", " ")}</dd></div>
      <div><dt>Data</dt><dd>{displayDate(bank.date)}</dd></div>
      {bank.document && <div><dt>Documento</dt><dd className="mono">{bank.document}</dd></div>}
    </dl>
    <Evidence references={references} />
    <section className="evidence-section candidate-section"><div className="section-heading"><div><p className="eyebrow">Pistas para investigação</p><h3>Títulos candidatos</h3></div><span className="section-count">{candidates.length}</span></div>
      {candidates.length ? <div className="record-list">{candidates.map(({ title, reasons }) => <article className="record" key={evidence(title.file, title.line)}>
        <div className="record-top"><strong>{title.document}</strong><span>{money(title.amount)}</span></div>
        <p>{title.customer} · {sourceNames[title.source]}</p>
        <div className="reason-list">{reasons.map((reason) => <span key={reason}>{reasonNames[reason] ?? reason.replaceAll("_", " ")}</span>)}</div>
        <Evidence references={[evidence(title.file, title.line)]} />
      </article>)}</div> : <p className="section-empty">Nenhum título candidato foi encontrado para este lançamento. Confira a origem antes de atribuir o valor.</p>}
      {candidates.length > 0 && <p className="section-note">Candidatos não representam crédito atribuído nem baixa confirmada.</p>}
      <div className="next-action"><p className="eyebrow">Próxima conferência</p><p>{bank.next_action}</p></div>
    </section>
  </>;
}

export default function Home() {
  const [queue, setQueue] = useState<QueueState>({ kind: "loading", attempt: 1 });
  const [reload, setReload] = useState(0);
  const [workflow, setWorkflow] = useState<WorkflowState>({ kind: "loading" });
  const [workflowReload, setWorkflowReload] = useState(0);
  const [groupFilter, setGroupFilter] = useState<GroupFilter>(null);
  const [showClosed, setShowClosed] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const [saveLoading, setSaveLoading] = useState(false);
  const [saveError, setSaveError] = useState("");
  const [saveNotice, setSaveNotice] = useState("");
  const [proofDraft, setProofDraft] = useState("");
  const [proofError, setProofError] = useState("");
  const [proofLoading, setProofLoading] = useState(false);
  const [analysis, setAnalysis] = useState("");
  const [analysisError, setAnalysisError] = useState("");
  const [analysisLoading, setAnalysisLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [priority, setPriority] = useState<Priority | "todas">("todas");
  const [source, setSource] = useState<Source | "todas">("todas");
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [dossier, setDossier] = useState<Case | null>(null);
  const [caseLoading, setCaseLoading] = useState(false);
  const [caseError, setCaseError] = useState("");
  const [caseReload, setCaseReload] = useState(0);
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [historyLoaded, setHistoryLoaded] = useState(false);
  const [askLoading, setAskLoading] = useState(false);
  const [exportLoading, setExportLoading] = useState(false);
  const [exportError, setExportError] = useState("");
  const [uploadState, setUploadState] = useState<"idle" | "reading" | "sending">("idle");
  const [uploadError, setUploadError] = useState("");
  const [uploadNotice, setUploadNotice] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const detailRef = useRef<HTMLElement>(null);
  const uploadController = useRef<AbortController | null>(null);
  const queueController = useRef<AbortController | null>(null);
  const askController = useRef<AbortController | null>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const transcriptRef = useRef<HTMLDivElement>(null);
  const proofController = useRef<AbortController | null>(null);
  const analysisController = useRef<AbortController | null>(null);
  const conflictKey = useRef<string | null>(null);

  useEffect(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem("vantara-chat-v1") ?? "[]");
      if (Array.isArray(saved)) setTurns(saved.filter((turn): turn is ChatTurn =>
        turn && typeof turn.question === "string" && turn.question.length <= 1000 &&
        ((typeof turn.answer === "string" && turn.answer.length <= 20000) ||
         (typeof turn.error === "string" && turn.error.length <= 1000))).slice(-30));
    } catch {}
    setHistoryLoaded(true);
  }, []);

  useEffect(() => {
    if (historyLoaded) {
      try { localStorage.setItem("vantara-chat-v1", JSON.stringify(turns.filter((turn) => turn.answer || turn.error).slice(-30))); }
      catch {}
    }
    if (transcriptRef.current) transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight;
  }, [turns, historyLoaded]);


  useEffect(() => {
    const controller = new AbortController();
    queueController.current = controller;
    let timeout: number | undefined;
    let resume: (() => void) | undefined;
    setQueue({ kind: "loading", attempt: 1 });
    async function load() {
      for (let index = 0; index < 4; index++) {
        if (index) {
          const { promise, resolve } = Promise.withResolvers<void>();
          resume = resolve;
          timeout = window.setTimeout(resolve, [1000, 3000, 9000][index - 1]);
          await promise;
          resume = undefined;
          if (controller.signal.aborted) return;
          setQueue({ kind: "loading", attempt: index + 1 });
        }
        try {
          const response = await fetch("/api/worklist", { cache: "no-store", signal: controller.signal });
          const data = await readQueue(response);
          if (!controller.signal.aborted) {
            setQueue({ kind: "ready", items: data.items, processedAt: data.processedAt });
            setUploadNotice("");
          }
          return;
        } catch (error) {
          if (controller.signal.aborted) return;
          if (index === 3) setQueue({ kind: "error", failure: queueFailure(error) });
        }
      }
    }
    void load();
    return () => { controller.abort(); clearTimeout(timeout); resume?.(); if (queueController.current === controller) queueController.current = null; };
  }, [reload]);

  const items = queue.kind === "ready" ? queue.items : [];
  useEffect(() => {
    if (queue.kind !== "ready") return;
    const controller = new AbortController();
    fetch("/api/workflow", { cache: "no-store", signal: controller.signal })
      .then((response) => readWorkflow(response, queue.items))
      .then((data) => {
        if (controller.signal.aborted) return;
        const conflict = conflictKey.current;
        if (conflict && data.cases[conflict]) {
          setShowClosed(data.cases[conflict].status === "closed");
          const [source, line] = conflict.split(":");
          setSelectedKey(`${source}-${line}`);
          setGroupFilter(null);
          setPriority("todas");
          setSource("todas");
          setSearch("");
          conflictKey.current = null;
        }
        setWorkflow({ kind: "ready", ...data });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) setWorkflow({ kind: "error", message: error instanceof Error ? error.message : "Não foi possível carregar o fluxo de decisões." });
      });
    return () => controller.abort();
  }, [queue, workflowReload]);

  const sorted = useMemo(() => [...items].sort((a, b) => (priorityRank[a.prioridade as Priority] ?? 3) - (priorityRank[b.prioridade as Priority] ?? 3)), [items]);
  const activeCases = workflow.kind === "ready" ? workflow.cases : null;
  const pending = useMemo(() => activeCases ? sorted.filter((item) => activeCases[`${item.origem}:${item.linha}`].status !== "closed") : [], [activeCases, sorted]);
  const groups = useMemo(() => {
    if (!activeCases) return [] as { kind: GroupKind; title: string; entries: { value: string; count: number }[] }[];
    const definitions: { kind: GroupKind; title: string; value: (item: WorkItem) => string }[] = [
      { kind: "situacao", title: "Situação nos exports", value: (item) => item.situacao.replaceAll("_", " ") },
      { kind: "prioridade", title: "Prioridade", value: (item) => priorityNames[item.prioridade as Priority] ?? item.prioridade },
      { kind: "responsavel", title: "Responsável informado", value: (item) => activeCases[`${item.origem}:${item.linha}`].owner.trim() || "Sem responsável" },
      { kind: "idade", title: "Tempo em aberto", value: (item) => openAge(activeCases[`${item.origem}:${item.linha}`].opened_at) },
    ];
    return definitions.map(({ kind, title, value }) => {
      const counts = new Map<string, number>();
      for (const item of pending) {
        const name = value(item);
        counts.set(name, (counts.get(name) ?? 0) + 1);
      }
      return { kind, title, entries: [...counts].map(([name, count]) => ({ value: name, count })) };
    });
  }, [activeCases, pending]);
  const visible = useMemo(() => {
    if (!activeCases) return [];
    const query = search.trim().toLocaleLowerCase("pt-BR");
    return sorted.filter((item) => {
      const entry = activeCases[`${item.origem}:${item.linha}`];
      const groupValue = groupFilter?.kind === "situacao" ? item.situacao.replaceAll("_", " ") :
        groupFilter?.kind === "prioridade" ? priorityNames[item.prioridade as Priority] ?? item.prioridade :
        groupFilter?.kind === "responsavel" ? entry.owner.trim() || "Sem responsável" :
        groupFilter?.kind === "idade" ? openAge(entry.opened_at) : "";
      return (showClosed ? entry.status === "closed" : entry.status !== "closed") &&
        (!groupFilter || groupValue === groupFilter.value) &&
        (priority === "todas" || item.prioridade === priority) &&
        (source === "todas" || item.origem === source) &&
        (!query || [item.documento_ou_historico, item.situacao, item.evidencias, item.proxima_acao, sourceNames[item.origem], entry.owner].some((value) => value.toLocaleLowerCase("pt-BR").includes(query)));
    });
  }, [sorted, activeCases, search, priority, source, groupFilter, showClosed]);
  const selected = visible.find((item) => `${item.origem}-${item.linha}` === selectedKey) ?? visible[0];
  const selectedWorkflow = selected && activeCases?.[`${selected.origem}:${selected.linha}`];
  const selectedEdit = selectedWorkflow && selected ? editing?.key === `${selected.origem}:${selected.linha}` ? editing :
    { key: `${selected.origem}:${selected.linha}`, owner: selectedWorkflow.owner, note: selectedWorkflow.note, status: selectedWorkflow.status } : null;
  function retryQueue() {
    queueController.current?.abort();
    askController.current?.abort();
    conflictKey.current = null;
    setWorkflow({ kind: "loading" });
    setAskLoading(false);
    setQueue({ kind: "loading", attempt: 1 });
    setReload((value) => value + 1);
  }

  useEffect(() => {
    setDossier(null);
    setCaseError("");
    if (!selected) { setCaseLoading(false); return; }
    const controller = new AbortController();
    setCaseLoading(true);
    const params = new URLSearchParams({ source: selected.origem, line: selected.linha });
    fetch(`/api/case?${params}`, { signal: controller.signal }).then((response) => readJson<Case>(response)).then((data) => {
      if (!data || (data.kind !== "title" && data.kind !== "bank")) throw new Error("A API retornou um caso inválido. Confira o backend.");
      setDossier(data);
    }).catch((error: unknown) => {
      if (!controller.signal.aborted) setCaseError(error instanceof Error ? error.message : "Não foi possível abrir o caso.");
    }).finally(() => { if (!controller.signal.aborted) setCaseLoading(false); });
    return () => controller.abort();
  }, [selected?.origem, selected?.linha, queue.kind === "ready" ? queue.processedAt : undefined, caseReload]);
  useEffect(() => {
    proofController.current?.abort();
    detailRef.current?.scrollTo(0, 0);
    analysisController.current?.abort();
    setProofDraft("");
    setProofError("");
    setProofLoading(false);
    setAnalysis("");
    setAnalysisError("");
    setAnalysisLoading(false);
    setEditing(null);
    setSaveError("");
    setSaveNotice("");
  }, [selected?.origem, selected?.linha, queue.kind === "ready" ? queue.processedAt : undefined]);

  function filterGroup(kind: GroupKind, value: string) {
    setGroupFilter((current) => current?.kind === kind && current.value === value ? null : { kind, value });
    setShowClosed(false);
    setSearch("");
    setPriority("todas");
    setSource("todas");
    detailRef.current?.scrollTo(0, 0);
    document.getElementById("queue-title")?.scrollIntoView({ block: "start" });
  }

  async function saveDecision(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selected || !selectedWorkflow || !selectedEdit || saveLoading || workflow.kind !== "ready") return;
    if ((selectedEdit.status !== "open" || selectedWorkflow.status === "closed") && !selectedEdit.note.trim()) {
      setSaveError("Escreva a justificativa humana para encerrar, reabrir ou marcar como inconclusivo.");
      return;
    }
    const key = selectedEdit.key;
    setSaveLoading(true);
    setSaveError("");
    setSaveNotice("");
    let conflict = false;
    try {
      const params = new URLSearchParams({ source: selected.origem, line: selected.linha });
      const response = await fetch(`/api/workflow?${params}`, {
        method: "PATCH", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner: selectedEdit.owner, status: selectedEdit.status, note: selectedEdit.note, version: selectedWorkflow.version }),
      });
      conflict = response.status === 409;
      await readJson<WorkflowCase>(response);
      if (selectedEdit.status === "closed") setShowClosed(true);
      else if (selectedWorkflow.status === "closed") setShowClosed(false);
      if (selectedEdit.status === "closed" || selectedWorkflow.status === "closed") {
        setGroupFilter(null);
        setPriority("todas");
        setSource("todas");
        setSearch("");
      }
      setSelectedKey(`${selected.origem}-${selected.linha}`);
      setEditing((current) => current?.key === key ? null : current);
      setSaveNotice("Decisão registrada. Atualizando os casos…");
      setWorkflowReload((value) => value + 1);
    } catch (error) {
      if (conflict) { conflictKey.current = key; setEditing({ ...selectedEdit }); }
      setSaveError(conflict ? "Este caso mudou desde sua última leitura. Confira a versão atual e salve novamente; seu texto foi mantido." :
        error instanceof Error ? error.message : "Não foi possível registrar a decisão.");
      if (conflict) setWorkflowReload((value) => value + 1);
    } finally {
      setSaveLoading(false);
    }
  }

  async function requestProof() {
    if (!selected || proofLoading) return;
    const controller = new AbortController();
    proofController.current = controller;
    setProofLoading(true);
    setProofError("");
    setProofDraft("");
    try {
      const response = await fetch("/api/workflow/draft", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: selected.origem, line: Number(selected.linha) }), signal: controller.signal,
      });
      const data = await readJson<{ draft: string }>(response);
      if (!data || typeof data.draft !== "string" || !data.draft.trim()) throw new Error("O serviço não retornou um rascunho legível.");
      if (!controller.signal.aborted) setProofDraft(data.draft);
    } catch (error) {
      if (!controller.signal.aborted) setProofError(error instanceof Error ? error.message : "Não foi possível preparar o pedido de comprovante.");
    } finally {
      if (!controller.signal.aborted) setProofLoading(false);
      if (proofController.current === controller) proofController.current = null;
    }
  }

  async function investigateCase() {
    if (!selected || analysisLoading) return;
    const controller = new AbortController();
    analysisController.current = controller;
    setAnalysis("");
    setAnalysisError("");
    setAnalysisLoading(true);
    try {
      const response = await fetch("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" }, signal: controller.signal,
        body: JSON.stringify({
          question: `Investigue o caso ${sourceNames[selected.origem]} linha ${selected.linha}, ${selected.documento_ou_historico}. Com base apenas nos exports, quais evidências e referências devo conferir antes de qualquer atribuição ou baixa? Não afirme pagamento sem comprovante.`,
          history: [],
        }),
      });
      const data = await readJson<{ answer: string }>(response);
      if (!data || typeof data.answer !== "string" || !data.answer.trim()) throw new Error("A API retornou uma análise inválida.");
      if (!controller.signal.aborted) setAnalysis(data.answer);
    } catch (error) {
      if (!controller.signal.aborted) setAnalysisError(error instanceof Error ? error.message : "Não foi possível investigar este caso.");
    } finally {
      if (!controller.signal.aborted) setAnalysisLoading(false);
      if (analysisController.current === controller) analysisController.current = null;
    }
  }


  async function investigate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const currentQuestion = question.trim();
    if (!currentQuestion || askLoading || queue.kind !== "ready") return;
    const history = turns.filter((turn) => turn.answer).slice(-6).flatMap((turn) => [
      { role: "user", content: turn.question },
      { role: "assistant", content: turn.answer!.slice(0, 4000) },
    ]);
    const controller = new AbortController();
    askController.current = controller;
    setAskLoading(true);
    setQuestion("");
    setTurns((previous) => [...previous, { question: currentQuestion }]);
    try {
      const response = await fetch("/api/ask", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: currentQuestion, history }), signal: controller.signal });
      const data = await readJson<{ answer: string }>(response);
      if (!data || typeof data.answer !== "string" || !data.answer.trim()) throw new Error("A API retornou uma análise inválida. Confira o backend.");
      if (!controller.signal.aborted) setTurns((previous) => [...previous.slice(0, -1), { question: currentQuestion, answer: data.answer }]);
    } catch (error) {
      if (!controller.signal.aborted) setTurns((previous) => [...previous.slice(0, -1), { question: currentQuestion, error: error instanceof Error ? error.message : "Não foi possível investigar o caso." }]);
    } finally {
      if (!controller.signal.aborted) setAskLoading(false);
      if (askController.current === controller) askController.current = null;
    }
  }

  async function exportCsv() {
    if (exportLoading) return;
    setExportLoading(true);
    setExportError("");
    try {
      const response = await fetch("/api/worklist.csv");
      if (!response.ok) await readJson(response);
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "vantara-pendencias.csv";
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setExportError(error instanceof Error ? error.message : "Não foi possível exportar a fila.");
    } finally {
      setExportLoading(false);
    }
  }

  useEffect(() => () => { uploadController.current?.abort(); askController.current?.abort(); }, []);

  async function submitExports(selectedFiles: FileList | null) {
    if (!selectedFiles || !selectedFiles.length || uploadState !== "idle") return;
    const files = Array.from(selectedFiles);
    setUploadError("");
    setUploadNotice("");
    if (files.length !== exportFiles.length || files.some((file) => !exportFiles.includes(file.name as typeof exportFiles[number])) ||
      new Set(files.map((file) => file.name)).size !== exportFiles.length) {
      setUploadError("Selecione os quatro arquivos originais do Protheus, Omie, banco e caixa de entrada, sem renomear nem adicionar arquivos.");
      return;
    }
    if (files.some((file) => file.size > maxFileBytes)) {
      setUploadError("Cada arquivo deve ter no máximo 2 MiB. Confira os exports selecionados.");
      return;
    }
    const controller = new AbortController();
    uploadController.current = controller;
    setUploadState("reading");
    try {
      const encoded = await Promise.all(files.map(async (file) => [file.name, await encodeFile(file)] as const));
      if (controller.signal.aborted) return;
      setUploadState("sending");
      const response = await fetch("/api/exports", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ files: Object.fromEntries(encoded) }), signal: controller.signal,
      });
      const result = await readJson<{ processed_at: string; items_count: number }>(response).catch((error: unknown) => {
        throw new Error(`HTTP ${response.status} · ${error instanceof Error ? error.message : "Confira os arquivos e tente novamente."}`);
      });
      if (!result || typeof result.processed_at !== "string" || !Number.isFinite(Date.parse(result.processed_at)) ||
        !Number.isInteger(result.items_count) || result.items_count < 0) throw new Error("O backend não confirmou o processamento. Confira o serviço local.");
      if (!controller.signal.aborted) {
        setUploadNotice("Arquivos recebidos. Atualizando a fila…");
        setTurns([]);
        retryQueue();
      }
    } catch (error) {
      if (!controller.signal.aborted) setUploadError(error instanceof Error ? error.message : "Não foi possível enviar os exports. Confira o serviço local e tente novamente.");
    } finally {
      if (!controller.signal.aborted) setUploadState("idle");
      if (uploadController.current === controller) uploadController.current = null;
    }
  }

  return <main className="workspace">
    <header className="masthead"><div className="brand"><span className="brand-mark" aria-hidden="true"><i /><i /><i /></span><strong>VANTARA</strong></div><span className="workspace-label">Financeiro / recebíveis</span></header>
    <section className="operations-hero" aria-labelledby="operations-title">
      <div><p className="eyebrow">Operações · julho/2026</p><h1 id="operations-title">Pendências com dono.<br />Decisões com rastro.</h1><p>Conciliação dos exports para conferência humana. Ações propostas e evidências não confirmam pagamento nem executam baixa.</p></div>
      <div className="hero-sources"><span>Fontes de trabalho</span><strong>Protheus / Omie / Banco / contas a pagar</strong><span>Responsável é texto informado manualmente, não identidade verificada.</span></div>
    </section>
    {queue.kind === "ready" && workflow.kind === "loading" && <section className="workspace-state" role="status"><p className="eyebrow">Decisões</p><h2>Carregando acompanhamento</h2><p>Consultando os estados persistentes desta fila.</p></section>}
    {queue.kind === "ready" && workflow.kind === "error" && <section className="workspace-state workspace-error" role="alert"><p className="eyebrow">Acompanhamento indisponível</p><h2>Não é possível apurar as pendências</h2><p>{workflow.message}</p><button type="button" className="export-button" onClick={() => setWorkflowReload((value) => value + 1)}>Tentar novamente</button></section>}
    {queue.kind === "ready" && workflow.kind === "ready" && <section className="oversight" aria-labelledby="oversight-title">
      <div className="oversight-heading"><div><p className="eyebrow">Visão de gestão · trabalho pendente</p><h2 id="oversight-title">{pending.length} casos aguardando decisão</h2><p>Em aberto e inconclusivos; {items.length - pending.length} encerrados fora do total. Idade desde a primeira entrada nesta fila, não desde o vencimento.</p></div>{items.length > 0 && <button type="button" className="export-button" onClick={exportCsv} disabled={exportLoading}>{exportLoading ? "Exportando…" : "Exportar CSV dos exports"} <span aria-hidden="true">↗</span></button>}</div>
      <div className="oversight-groups">{groups.map((group) => <section className="oversight-group" key={group.kind} aria-label={group.title}><h3>{group.title}</h3>
        {group.entries.length ? group.entries.map(({ value, count }) => <button type="button" key={value} aria-pressed={groupFilter?.kind === group.kind && groupFilter.value === value && !showClosed} onClick={() => filterGroup(group.kind, value)}><span>{value}</span><strong>{count}</strong></button>) : <p>Nenhum caso pendente.</p>}
      </section>)}</div>
    </section>}
    {queue.kind === "ready" && exportError && <p className="export-error" role="alert">{exportError}</p>}
    {queue.kind === "loading" && <section className="workspace-state" role="status" aria-live="polite"><p className="eyebrow">Fila de exceções</p><h2>Consultando o serviço local</h2><p>Carregando registros dos exports. Tentativa {queue.attempt} de 4.</p></section>}
    {queue.kind === "error" && <section className="workspace-state workspace-error" role="alert" aria-labelledby="queue-error-title"><p className="eyebrow">Ação necessária</p><h2 id="queue-error-title">Fila indisponível</h2><p className="diagnostic-line">{queue.failure.status ? `HTTP ${queue.failure.status}` : "Sem resposta HTTP"} · {queue.failure.contentType ?? "sem content-type"} em {queue.failure.backendUrl ?? "/api/worklist"}</p><p>{queue.failure.message}</p><button type="button" className="export-button" onClick={retryQueue}>Tentar novamente</button><details className="technical-details"><summary>Detalhes técnicos</summary><p>Primeiros 200 bytes da resposta</p><pre>{queue.failure.peek || "(resposta vazia)"}</pre></details></section>}
    {queue.kind === "ready" && workflow.kind === "ready" && items.length === 0 && <section className="workspace-state workspace-empty" aria-labelledby="empty-title"><p className="eyebrow">Fila atualizada · {processedTime(queue.processedAt)}</p><h2 id="empty-title">Nenhum caso nos exports</h2><p>Último processamento às {processedTime(queue.processedAt)}. Para revisar novos dados, selecione os quatro arquivos de Protheus, Omie, banco e contas a pagar.</p><input ref={fileInput} className="sr-only" type="file" multiple accept=".csv,.txt" aria-label="Selecionar os quatro exports originais" onChange={(event) => { void submitExports(event.currentTarget.files); event.currentTarget.value = ""; }} /><button type="button" className="export-button" onClick={() => fileInput.current?.click()} disabled={uploadState !== "idle"}>Enviar arquivos <span aria-hidden="true">↗</span></button>{uploadState !== "idle" && <p role="status">{uploadState === "reading" ? "Lendo os quatro arquivos…" : "Enviando arquivos e processando a fila…"}</p>}{uploadError && <p className="upload-error" role="alert">{uploadError}</p>}{uploadNotice && <p role="status">{uploadNotice}</p>}</section>}
    {queue.kind === "ready" && workflow.kind === "ready" && items.length > 0 && <div className="desk"><section className="queue-panel" aria-labelledby="queue-title"><div className="panel-top"><div><p className="eyebrow">Casos para decisão</p><h2 id="queue-title">Fila de trabalho</h2></div><span className="panel-total">{visible.length} exibidos</span></div>
      <div className="controls"><label className="search-field"><span className="search-icon" aria-hidden="true">⌕</span><span className="sr-only">Buscar na fila</span><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar documento, histórico ou evidência" /></label><label className="source-field"><span className="sr-only">Filtrar origem</span><select value={source} onChange={(event) => setSource(event.target.value as Source | "todas")}><option value="todas">Todas as origens</option><option value="protheus">Protheus</option><option value="omie">Omie</option><option value="bank">Banco</option></select></label></div>
      <div className="view-tabs" aria-label="Estado dos casos"><button type="button" aria-pressed={!showClosed} className={!showClosed ? "active" : ""} onClick={() => { setShowClosed(false); setGroupFilter(null); }}>Pendentes ({pending.length})</button><button type="button" aria-pressed={showClosed} className={showClosed ? "active" : ""} onClick={() => { setShowClosed(true); setGroupFilter(null); setPriority("todas"); }}>Encerrados ({items.length - pending.length})</button></div>
      {groupFilter && !showClosed && <button type="button" className="group-chip" onClick={() => setGroupFilter(null)}>Filtro: {groupFilter.value} <span aria-hidden="true">×</span><span className="sr-only">Limpar filtro de gestão</span></button>}
      <div className="priority-tabs" aria-label="Filtrar prioridade">{(["todas", "alta", "media", "baixa"] as const).map((option) => <button type="button" key={option} className={priority === option ? "active" : ""} aria-pressed={priority === option} onClick={() => setPriority(option)}>{option === "todas" ? "Todas" : priorityNames[option]}</button>)}</div>
      <div className="queue-list" aria-live="polite">{!visible.length ? <div className="state-block"><strong>{showClosed && items.length === pending.length ? "Nenhum caso encerrado" : !showClosed && !pending.length ? "Nenhum caso pendente" : "Nenhum caso encontrado com esses filtros"}</strong><p>{showClosed ? "Os casos encerrados aparecem aqui para consulta e reabertura." : "Ajuste os filtros para encontrar outro caso. Casos encerrados ficam em outra aba."}</p><button type="button" onClick={() => { setSearch(""); setPriority("todas"); setSource("todas"); setGroupFilter(null); }}>Limpar filtros</button></div> : visible.map((item) => {
        const key = `${item.origem}-${item.linha}`;
        return <button type="button" className={`queue-item priority-${item.prioridade} ${selected === item ? "selected" : ""}`} aria-pressed={selected === item} key={key} onClick={() => { setSelectedKey(key); if (window.matchMedia("(max-width: 800px)").matches) requestAnimationFrame(() => document.getElementById("detail-title")?.scrollIntoView({ block: "start" })); }}>
          <span className="queue-item-meta"><span className={`priority-label priority-${item.prioridade}`}>{priorityNames[item.prioridade as Priority] ?? item.prioridade}</span><span>{sourceNames[item.origem]} · linha {item.linha}</span></span>
          <span className="queue-item-main"><strong>{item.documento_ou_historico}</strong><b>R$ {item.valor_brl}</b></span>
          <span className="queue-item-status">{item.situacao.replaceAll("_", " ")} · {workflowLabel(activeCases![`${item.origem}:${item.linha}`].status)}</span>
          <span className="queue-owner">{activeCases![`${item.origem}:${item.linha}`].owner.trim() || "Sem responsável"} · {openAge(activeCases![`${item.origem}:${item.linha}`].opened_at)} na fila</span>
          <span className="queue-item-ref">{item.evidencias.split(", ")[0]}</span>
        </button>;
      })}</div>
    </section>
    <section ref={detailRef} className="detail-panel" aria-labelledby="detail-title">
      <div className="panel-top detail-top"><div><p className="eyebrow">Registro selecionado</p><h2 id="detail-title">Dossiê e decisão</h2></div>{selected && dossier && <button type="button" className="case-ask" onClick={() => { void investigateCase(); }} disabled={analysisLoading}>{analysisLoading ? "Investigando…" : "Investigar caso com IA ↗"}</button>}</div>
      <div className="detail-content">
        {!selected ? <div className="state-block">Selecione um caso na fila para examinar as fontes e registrar a decisão.</div> : caseLoading ? <div className="state-block" role="status">Buscando evidências do registro…</div> : caseError ? <div className="state-block state-error"><strong>Não foi possível abrir o dossiê</strong><p>{caseError}</p><button type="button" onClick={() => setCaseReload((value) => value + 1)}>Tentar novamente</button></div> : dossier?.kind === "title" ? <TitleCase dossier={dossier} /> : dossier?.kind === "bank" ? <BankCase dossier={dossier} references={selected.evidencias.split(", ")} /> : <div className="state-block">Nenhuma evidência retornada para esta linha.</div>}
        {selected && <section className="evidence-section ai-investigation" aria-label="Interpretação da IA"><p className="eyebrow">Interpretação opcional · não é decisão</p><h3>Investigação contextual</h3><p>Consulta aos exports; confira as referências acima antes de decidir. Não grava no histórico.</p>
          {analysisLoading && <p role="status">Consultando este caso com a IA…</p>}
          {analysisError && <p role="alert" className="form-error">{analysisError} <button type="button" onClick={() => { void investigateCase(); }}>Tentar novamente</button></p>}
          {analysis && <p className="analysis-answer">{analysis.replaceAll("**", "")}</p>}
          {!analysis && !analysisLoading && !analysisError && <button type="button" className="secondary-action" onClick={() => { void investigateCase(); }}>Investigar caso com IA</button>}
        </section>}
        {selected && selectedWorkflow && selectedEdit && <section className="evidence-section workflow-section" aria-labelledby="workflow-title">
          <p className="eyebrow">Decisão humana · persistente</p><h3 id="workflow-title">Acompanhamento do caso</h3>
          <p className="workflow-meta">{workflowLabel(selectedWorkflow.status)} · primeira entrada {timestamp(selectedWorkflow.opened_at)} · atualizado {timestamp(selectedWorkflow.updated_at)}</p>
          <p className="workflow-current"><strong>Responsável informado:</strong> {selectedWorkflow.owner || "Sem responsável"}<br /><strong>Última nota:</strong> {selectedWorkflow.note || "Nenhuma nota registrada."}</p>
          <form className="workflow-form" onSubmit={saveDecision}>
            <label>Responsável (texto informado manualmente)<input type="text" maxLength={120} value={selectedEdit.owner} onChange={(event) => setEditing({ ...selectedEdit, owner: event.target.value })} placeholder="Nome ou equipe" /></label>
            <label>Decisão<select value={selectedEdit.status} onChange={(event) => setEditing({ ...selectedEdit, status: event.target.value as WorkflowStatus, note: selectedWorkflow.status === "closed" && event.target.value === "open" ? "" : selectedEdit.note })}><option value="open">Em aberto / reabrir</option>{selectedWorkflow.status !== "closed" && <option value="inconclusive">Inconclusivo</option>}<option value="closed">Encerrar</option></select></label>
            <label>Nota humana {(selectedEdit.status !== "open" || selectedWorkflow.status === "closed") && <span>(obrigatória)</span>}<textarea rows={3} value={selectedEdit.note} required={selectedEdit.status !== "open" || selectedWorkflow.status === "closed"} onChange={(event) => setEditing({ ...selectedEdit, note: event.target.value })} placeholder="Registre o que foi conferido e o motivo da decisão." /></label>
            <p>Salvar uma decisão não altera Protheus, Omie ou banco. Encerrar o caso não confirma pagamento.</p>
            {saveError && <p className="form-error" role="alert">{saveError}</p>}{saveNotice && <p className="form-success" role="status">{saveNotice}</p>}
            <button type="submit" className="export-button" disabled={saveLoading || selectedWorkflow.status === "closed" && selectedEdit.status === "closed"}>{saveLoading ? "Salvando…" : selectedWorkflow.status === "closed" && selectedEdit.status === "open" ? "Reabrir caso" : "Salvar decisão"}</button>
          </form>
          <div className="workflow-history"><h4>Histórico de decisões</h4>{selectedWorkflow.history.length ? <ol>{selectedWorkflow.history.map((entry, index) => <li key={`${entry.at}-${index}`}><time dateTime={entry.at}>{timestamp(entry.at)}</time><strong>{workflowLabel(entry.status)}</strong><span>{entry.owner || "Sem responsável"}</span>{entry.note && <p>{entry.note}</p>}</li>)}</ol> : <p>Nenhuma decisão humana registrada ainda.</p>}</div>
        </section>}
        {selected && selectedWorkflow?.status !== "closed" && dossier && (dossier.kind === "bank" && dossier.bank.type === "C" || dossier.kind === "title" && dossier.candidate_credits.length > 0) && <section className="evidence-section proof-section" aria-label="Rascunho de pedido de comprovante">
          <p className="eyebrow">Rascunho opcional · não enviado</p><h3>Solicitar referência ou comprovante</h3><p>A IA prepara apenas texto para revisão. Você decide se, como e a quem enviar fora desta ferramenta.</p>
          <button type="button" className="secondary-action" disabled={proofLoading} onClick={() => { void requestProof(); }}>{proofLoading ? "Preparando rascunho…" : "Preparar pedido de comprovante"}</button>
          {proofError && <p className="form-error" role="alert">{proofError}</p>}
          {proofDraft && <div className="proof-edit"><label htmlFor="proof-draft">Edite antes de copiar</label><textarea id="proof-draft" rows={6} value={proofDraft} onChange={(event) => setProofDraft(event.target.value)} /><button type="button" className="secondary-action" onClick={() => { if (!navigator.clipboard) { setProofError("Selecione o texto e copie manualmente neste navegador."); return; } void navigator.clipboard.writeText(proofDraft).catch(() => setProofError("Não foi possível copiar. Selecione o texto e copie manualmente.")); }}>Copiar texto</button></div>}
        </section>}
      </div>
    </section></div>}
    <section className="assistant-panel" aria-labelledby="workspace-title">
      <div className="assistant-intro"><p className="eyebrow">Investigação adicional</p><h2 id="workspace-title">Pergunte aos registros.</h2><p>Conversa livre para explorar os exports. As respostas são auxiliares; a decisão humana fica no dossiê.</p><div className="assistant-sources">{queue.kind === "ready" ? "Fontes desta sessão" : "Fontes previstas"}<strong>Protheus · Omie · extrato bancário · contas a pagar</strong><span>Exports da amostra de julho/2026</span></div></div>
      <div className="assistant-console"><div className="console-heading"><span className="console-mark" aria-hidden="true">↗</span><div><p className="eyebrow">Assistente de IA</p><h2>Conversa com os registros</h2></div>{turns.length > 0 && <button type="button" className="clear-chat" disabled={askLoading} onClick={() => setTurns([])}>Limpar conversa</button>}</div>
        {turns.length > 0 && <div className="chat-transcript" ref={transcriptRef} role="log" aria-label="Histórico da conversa" aria-live="polite" aria-relevant="additions text">{turns.map((turn, index) => <div className="chat-turn" key={index}>
          <div className="chat-user"><span>Você</span><p>{turn.question}</p></div>
          <div className="chat-assistant"><span>Assistente · consulta aos exports</span>{turn.answer ? <p>{turn.answer.replaceAll("**", "")}</p> : turn.error ? <div className="chat-failure" role="alert"><p>{turn.error}</p><button type="button" onClick={() => { setQuestion(turn.question); questionRef.current?.focus(); }}>Repetir pergunta</button></div> : <p role="status">Consultando os registros…</p>}</div>
        </div>)}</div>}
        {!turns.length && <div className="chat-empty"><strong>Comece por uma pergunta</strong><p>Investigue uma pendência e acompanhe as respostas aqui, com as referências aos arquivos e linhas consultados.</p><div className="suggested-questions">{["O que falta conferir na NF 104560?", "Onde há divergências entre Protheus e Omie?", "Quais lançamentos do banco exigem revisão?"].map((suggestion) => <button type="button" key={suggestion} disabled={queue.kind !== "ready"} onClick={() => { setQuestion(suggestion); questionRef.current?.focus(); }}>{suggestion}</button>)}</div></div>}
        <form onSubmit={investigate}><label htmlFor="question">Sua mensagem</label><textarea id="question" ref={questionRef} rows={3} maxLength={1000} value={question} onChange={(event) => setQuestion(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={turns.length ? "Pergunte mais sobre os registros…" : "Ex.: O que falta conferir na NF 104560?"} disabled={askLoading || queue.kind !== "ready"} /><div className="composer-actions"><span>{queue.kind !== "ready" ? "Aguardando os dados para consultar" : "Histórico neste navegador · cada resposta confere os exports novamente."}</span><button type="submit" disabled={askLoading || queue.kind !== "ready" || !question.trim()}>{askLoading ? "Investigando…" : "Enviar"} <span aria-hidden="true">↗</span></button></div></form>
      </div>
    </section>
    <footer className="footer">Amostra de julho/2026 · Respostas para conferência humana; nenhuma baixa é executada.</footer>
  </main>;
}
