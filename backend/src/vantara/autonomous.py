"""Autonomous executor: applies AI attribution decisions to a local idempotent ledger.

The model holds the pen on WHICH credit maps to WHICH title; deterministic
guardrails (candidate must be in the computed list, duplicate-between-ERPs
titles and excess amounts are held) keep the write robust. Writes land only
in the local ledger with receipts and reread confirmation — never in a real
ERP (erp_writeoffs_performed stays 0).
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
    row = connection.execute("""SELECT amount FROM executor_writeoffs
        WHERE dataset = ? AND title_source = ? AND title_line = ?""",
        (dataset, source, line)).fetchone()
    return Decimal(row["amount"]) if row else Decimal("0")


def run_autonomous(dataset: str, report: dict, suggestions: dict) -> dict:
    """Apply AI decisions the guardrails admit; hold the rest with a reason."""
    titles = {(title["source"], title["line"]): title for title in report["titles"]}
    credits = {item["bank"]["line"]: item for item in suggestions.get("suggestions", [])}
    result = {"mode": "executor_autonomo", "dataset": dataset, "erp_writeoffs_performed": 0,
              "status": suggestions.get("status"), "applied": [], "pending": []}

    def pending(line, reason, next_query):
        item = credits.get(line)
        result["pending"].append({
            "bank_line": line, "amount": item["bank"]["amount"] if item else None,
            "history": item["bank"]["history"] if item else None,
            "reason": reason, "next_query": next_query})

    if suggestions.get("status") != "completed":
        for line, item in credits.items():
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
            pending(line, "Decisão da IA: sem atribuição segura.",
                    item.get("confirm_with") or "Comprovante/ID da transação e consulta ao ERP atual.")
            continue
        source, _, line_text = item["candidate"].partition(":")
        title = titles.get((source, int(line_text)))
        if title is None:
            pending(line, "Candidato fora da lista determinística.", "Reprocessar a conciliação.")
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
                already = _title(connection, dataset, title["source"], title["line"])
                remaining = Decimal(title["amount"]) - already
                if amount > remaining:
                    continue  # Held: another credit already consumed the balance this run.
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
                     json.dumps(item.get("evidence", []), ensure_ascii=False),
                     item.get("rationale", "")))
        # Reread the ledger: confirmation is what was stored, not what was sent.
        result["applied"] = _applied_rows(connection, dataset)

    executed = {row["event_line"] for row in result["applied"]}
    for line, (item, title, amount) in plan.items():
        if line not in executed:
            pending(line, "Saldo do título já consumido por outro crédito nesta execução.",
                    "Conferir parcelas e histórico do título antes de nova baixa.")
    result["status"] = "completed"
    return result
