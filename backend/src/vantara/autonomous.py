"""Autonomous executor: applies writeoffs to a local idempotent ledger.

Every credit is reviewed by the model. The exact unique reference computed
by the rules constrains the model to the documented title; it cannot override
that link. For ambiguous credits the model chooses among rule-derived
candidates. Duplicate-between-ERPs titles and excess amounts are held.
Writes land only in the local ledger with receipts and reread confirmation —
never in a real ERP (erp_writeoffs_performed stays 0).
"""

import json
from contextlib import closing
from decimal import Decimal

from .workflow import _connection


def _table(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS executor_writeoffs (
        dataset TEXT NOT NULL, event_line INTEGER NOT NULL, title_source TEXT NOT NULL,
        title_line INTEGER NOT NULL, document TEXT NOT NULL, amount TEXT NOT NULL,
        kind TEXT NOT NULL, balance_after TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE,
        evidence TEXT NOT NULL, rationale TEXT NOT NULL,
        PRIMARY KEY (dataset, event_line)
    )""")


def _applied_rows(connection, dataset):
    rows = connection.execute("""SELECT event_line, title_source, title_line, document, amount,
        kind, balance_after, receipt, evidence, rationale FROM executor_writeoffs
        WHERE dataset = ? ORDER BY event_line""", (dataset,)).fetchall()
    return [{**dict(row), "evidence": json.loads(row["evidence"])} for row in rows]


def _title(connection, dataset, source, line):
    rows = connection.execute("""SELECT amount FROM executor_writeoffs
        WHERE dataset = ? AND title_source = ? AND title_line = ?""",
        (dataset, source, line)).fetchall()
    return sum((Decimal(row["amount"]) for row in rows), Decimal("0"))

def _apply(connection, dataset: str, line: int, title: dict, amount: Decimal,
           evidence: list, rationale: str) -> bool:
    """Idempotent write; False when the title balance is already consumed."""
    row = connection.execute("""SELECT title_source, title_line, amount FROM executor_writeoffs
        WHERE dataset = ? AND event_line = ?""", (dataset, line)).fetchone()
    if row is not None:
        return row["title_source"] == title["source"] and row["title_line"] == title["line"] and Decimal(row["amount"]) == amount
    already = _title(connection, dataset, title["source"], title["line"])
    remaining = Decimal(title["amount"]) - already
    if amount > remaining:
        return False
    balance_after = remaining - amount
    receipt = f"EXE-{dataset[:12]}-C{line:04d}"
    connection.execute("""INSERT INTO executor_writeoffs
        (dataset, event_line, title_source, title_line, document, amount, kind,
         balance_after, receipt, evidence, rationale)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(dataset, event_line) DO NOTHING""",
        (dataset, line, title["source"], title["line"], title["document"],
         f"{amount:.2f}", "total" if balance_after == 0 else "partial",
         f"{balance_after:.2f}", receipt,
         json.dumps(evidence, ensure_ascii=False), rationale))
    return True


def run_autonomous(dataset: str, report: dict, suggestions: dict) -> dict:
    """Apply validated AI decisions for every bank credit; hold the rest."""
    titles = {(title["source"], title["line"]): title for title in report["titles"]}
    credits = {item["bank"]["line"]: item for item in suggestions.get("suggestions", [])}
    bank = {entry["line"]: entry for entry in report["bank"] if entry["type"] == "C"}
    histories = {entry["line"]: entry["history"] for entry in report["bank"]}
    referenced_titles = {(entry["linked_title"]["source"], entry["linked_title"]["line"])
                         for entry in bank.values() if entry["status"] == "credito_com_referencia"}
    result = {"mode": "executor_autonomo", "dataset": dataset, "erp_writeoffs_performed": 0,
              "status": suggestions.get("status"), "applied": [], "pending": []}

    def pending(line, reason, next_query):
        item = credits.get(line)
        result["pending"].append({
            "bank_line": line, "amount": item["bank"]["amount"] if item else None,
            "history": item["bank"]["history"] if item else histories.get(line),
            "reason": reason, "next_query": next_query})


    if suggestions.get("status") != "completed":
        for line in credits:
            pending(line, "Sem decisão de IA disponível nesta publicação.",
                    "Reprocessar quando a análise estiver disponível.")
        result["status"] = suggestions.get("status") or "no_cases"
        with closing(_connection()) as connection:
            _table(connection)
            result["applied"] = _applied_rows(connection, dataset)
        return result

    plan = {}
    for line, item in credits.items():
        if item.get("verdict") != "candidato_mais_provavel" or not item.get("candidate"):
            if bank[line]["status"] == "credito_com_referencia":
                pending(line, "Vínculo exato sem confirmação válida da IA; baixa retida.",
                        "Reprocessar e conferir referência do título.")
                continue
            pending(line, "Decisão da IA: sem atribuição segura.",
                    item.get("confirm_with") or "Comprovante/ID da transação e consulta ao ERP atual.")
            continue
        source, _, line_text = item["candidate"].partition(":")
        title = titles.get((source, int(line_text)))
        if (bank[line]["status"] == "credito_com_referencia" and
            (source, int(line_text)) != (bank[line]["linked_title"]["source"],
                                         bank[line]["linked_title"]["line"])):
            pending(line, "A IA divergiu do vínculo documental exato; baixa retida.",
                    "Conferir referência do título antes de baixar.")
            continue
        if title is None:
            pending(line, "Candidato fora da lista determinística.", "Reprocessar a conciliação.")
            continue
        if bank[line]["status"] != "credito_com_referencia" and (source, int(line_text)) in referenced_titles:
            pending(line, "Título já tem crédito com referência exata no extrato; baixa retida.",
                    "Conferir se o crédito sem referência pertence a outro título.")
            continue
        if title["status"] == "possivel_duplicidade_entre_sistemas":
            pending(line, "Título candidato com possível duplicidade entre ERPs; baixa retida.",
                    "Confirmar qual título representa a obrigação nos dois ERPs antes de baixar.")
            continue
        amount = Decimal(item["bank"]["amount"])
        if amount > Decimal(title["amount"]):
            pending(line, "Crédito excede o valor do título; baixa retida.",
                    "Conferir parcelas e possíveis créditos complementares antes de baixar.")
            continue
        plan[line] = (item, title, amount)

    with closing(_connection()) as connection:
        _table(connection)
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            for line, (item, title, amount) in sorted(plan.items()):
                _apply(connection, dataset, line, title, amount,
                       item.get("evidence", []), item.get("rationale", ""))
        # Reread the ledger: confirmation is what was stored, not what was sent.
        result["applied"] = _applied_rows(connection, dataset)

    executed = {row["event_line"] for row in result["applied"]}
    for line, (item, title, amount) in plan.items():
        if line not in executed:
            pending(line, "Saldo do título já consumido por outro crédito nesta execução.",
                    "Conferir parcelas e histórico do título antes de nova baixa.")
    result["status"] = "completed"
    return result
