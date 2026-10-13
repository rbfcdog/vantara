"""Local, explicitly simulated receivables writeoffs; never an ERP integration."""

import json
import re
from collections import Counter, defaultdict
from contextlib import closing
from decimal import Decimal, InvalidOperation

from .workflow import _connection


# SHA-256 dataset_id of the four ORIGINAL data_pack exports, in SOURCE_FILES order.
# Keep this pinned: recomputing it from uploaded/current files would silently enable new datasets.
ORIGINAL_DATASET = "68c2b2bc79beead40f7d97b3f4104f5331a78dd8ef5e5174de4ed3fd58beff8f"

# Synthetic current ERP state and authorization, NOT inferred from an exported "Aberto".
# The partial policy applies only to this one title/event; no other title has a fixture.
FIXTURES = {
    4: {"title_line": 5, "reference": "000012345701", "document": "NF 104540",
        "date": "2026-07-13", "amount": "9870.00", "current_balance": "9870.00", "allow_partial": False},
    8: {"title_line": 8, "reference": "000012345720", "document": "NF 104560",
        "date": "2026-07-16", "amount": "62500.00", "current_balance": "125000.00", "allow_partial": True},
}


def _amount(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d{2}", value):
        return None
    try:
        number = Decimal(value)
    except InvalidOperation:
        return None
    return number if number > 0 else None


def _decision(event, titles_by_reference, reference_counts):
    """Return (title, fixture, status, reason, next_query); only exact unique references qualify."""
    reference = event.get("document")
    amount = _amount(event.get("amount"))
    if not isinstance(event.get("line"), int) or event["line"] < 2 or amount is None:
        return None, None, "evento_invalido", "Lançamento bancário inválido; nenhuma baixa simulada.", "Conferir valor e linha no extrato original."
    if not isinstance(reference, str) or not reference or reference_counts[reference] > 1:
        if isinstance(reference, str) and reference and reference_counts[reference] > 1:
            return None, None, "referencia_repetida", "Referência aparece em mais de um crédito; evitar baixa duplicada.", "Consultar IDs das transações bancárias e histórico de liquidação no ERP."
        return None, None, "sem_vinculo", "Crédito sem referência exata única de título.", "Solicitar ID/comprovante da transação bancária e localizar título no faturamento e nos ERPs atuais."
    matches = titles_by_reference.get(reference, ())
    if len(matches) > 1:
        return None, None, "referencia_ambigua", "Referência compartilhada por títulos; obrigação ambígua.", "Confirmar nos dois ERPs qual título representa a obrigação e consultar histórico de baixas."
    if not matches:
        return None, None, "sem_vinculo", "Nenhum título com essa referência no pack.", "Solicitar ID/comprovante da transação bancária e localizar título no faturamento e nos ERPs atuais."
    title = matches[0]
    if (event.get("status") != "credito_com_referencia" or event.get("linked_title") !=
            {"source": title.get("source"), "line": title.get("line")}):
        return None, None, "vinculo_inconsistente", "Vínculo no relatório não confirma a referência única.", "Reconciliar a referência do extrato com o título exportado antes de qualquer operação."
    if title.get("tax_id") and any(
        other is not title and other.get("source") != title.get("source") and
        other.get("tax_id") == title["tax_id"] and other.get("due") == title.get("due") and
        other.get("amount") == title.get("amount")
        for group in titles_by_reference.values() for other in group
    ):
        return None, None, "duplicidade_entre_erps", "Possível mesma obrigação nos dois ERPs; nova baixa bloqueada.", "Consultar estado e histórico nos dois ERPs e confirmar qual título representa a obrigação."
    fixture = FIXTURES.get(event["line"])
    if fixture is None:
        return None, None, "sem_estado_erp", "Export não informa saldo nem histórico atual do ERP; não há fixture para este título.", "Consultar saldo atual e histórico de baixas no ERP antes de decidir pela baixa."
    if (title.get("source") != "protheus" or title.get("line") != fixture["title_line"] or
            title.get("reference") != fixture["reference"] or title.get("document") != fixture["document"] or
            title.get("amount") != fixture["current_balance"] or title.get("export_status") != "Aberto" or
            event.get("date") != fixture["date"] or reference != fixture["reference"] or
            event.get("amount") != fixture["amount"] or event.get("type") != "C" or
            event.get("file") is None or title.get("file") is None):
        return None, None, "fixture_divergente", "Evento/título não corresponde ao estado sintético fixado.", "Revisar export e consultar estado atual do título no ERP."
    balance = Decimal(fixture["current_balance"])
    if amount > balance or (amount != balance and not fixture["allow_partial"]):
        return None, None, "politica_nao_autorizada", "Valor incompatível com saldo ou política sintética de parcial.", "Consultar saldo e política de baixa parcial no ERP."
    return title, fixture, "apta_para_simulacao", "Estado e política adicionais existem somente na fixture local, sem consulta ao ERP real.", "Executar lote local simulado; antes de qualquer baixa real, consultar saldo e histórico no ERP."


def _table(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS demo_writeoffs (
        dataset TEXT NOT NULL, event_line INTEGER NOT NULL, title_source TEXT NOT NULL,
        title_line INTEGER NOT NULL, document TEXT NOT NULL, amount TEXT NOT NULL,
        kind TEXT NOT NULL, balance_after TEXT NOT NULL, receipt TEXT NOT NULL UNIQUE,
        evidence TEXT NOT NULL, PRIMARY KEY (dataset, event_line)
    )""")


def _processed(connection, dataset):
    rows = connection.execute("""SELECT event_line, title_source, title_line, document, amount,
        kind, balance_after, receipt, evidence FROM demo_writeoffs
        WHERE dataset = ? ORDER BY event_line""", (dataset,)).fetchall()
    return [{**dict(row), "evidence": json.loads(row["evidence"])} for row in rows]


def operations(report, run=False):
    """GET reads the local ledger; POST atomically writes and confirms by rereading it."""
    dataset = report["dataset"]
    result = {"mode": "local_simulation", "dataset": dataset,
              "supported": dataset == ORIGINAL_DATASET,
              "reason": None if dataset == ORIGINAL_DATASET else
                        "Dataset diferente dos quatro exports originais; nenhuma fixture de estado atual está disponível.",
              "eligible_count": 0, "simulated_writeoffs_performed": 0,
              "processed": [], "pending": []}
    if not result["supported"]:
        return result

    titles_by_reference = defaultdict(list)
    for title in report["titles"]:
        titles_by_reference[title["reference"]].append(title)
    credits = [event for event in report["bank"] if event.get("type") == "C"]
    reference_counts = Counter(event.get("document") for event in credits)
    decisions = [(event, _decision(event, titles_by_reference, reference_counts)) for event in credits]
    eligible = [(event, title, fixture) for event, (title, fixture, status, _, _) in decisions
                if status == "apta_para_simulacao"]
    result["eligible_count"] = len(eligible)

    with closing(_connection()) as connection:
        _table(connection)
        if run:
            with connection:
                connection.execute("BEGIN IMMEDIATE")
                for event, title, fixture in eligible:
                    amount = Decimal(fixture["amount"])
                    balance_after = Decimal(fixture["current_balance"]) - amount
                    evidence = [f"{event['file']}:{event['line']}", f"{title['file']}:{title['line']}",
                                "Estado ERP atual: fixture sintética local, não consultado no ERP real.",
                                "Política sintética de baixa parcial autorizada somente neste título." if fixture["allow_partial"]
                                else "Política sintética: somente quitação total."]
                    connection.execute("""INSERT INTO demo_writeoffs
                        (dataset, event_line, title_source, title_line, document, amount,
                         kind, balance_after, receipt, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(dataset, event_line) DO NOTHING""",
                        (dataset, event["line"], title["source"], title["line"], title["document"],
                         f"{amount:.2f}", "total" if balance_after == 0 else "partial",
                         f"{balance_after:.2f}", f"SIM-{dataset[:12]}-C{event['line']:04d}",
                         json.dumps(evidence, ensure_ascii=False)))
        result["processed"] = _processed(connection, dataset)
    completed = {item["event_line"] for item in result["processed"]}
    result["simulated_writeoffs_performed"] = len(result["processed"])
    result["pending"] = [{"event_line": event["line"], "amount": event["amount"],
                          "status": status, "reason": reason, "next_query": next_query}
                         for event, (_, _, status, reason, next_query) in decisions
                         if event["line"] not in completed]
    return result
