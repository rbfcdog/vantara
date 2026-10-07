import csv
import re
import unicodedata
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import StringIO

from .sources import CSV_SOURCES, INBOX_KEY


COLUMNS = {
    "protheus": {"cliente", "cnpj", "documento", "nosso_numero", "vencimento", "valor", "status"},
    "omie": {"customer_name", "tax_id", "invoice_ref", "due_date", "amount", "paid_flag"},
    "bank": {"data", "historico", "documento", "valor", "tipo"},
}
STOP_WORDS = {"RECEBIDO", "RECEBIDA", "LIQUIDACAO", "COBRANCA", "SIMPLES", "PAGAMENTO",
              "FORNECEDOR", "COMERCIO", "ALIMENTOS", "LTDA", "EIRELI", "FRANQUIAS"}
BR_SEPARATORS = str.maketrans(".,", ",.")


def _rows(sources, alias):
    filename, delimiter = CSV_SOURCES[alias]
    reader = csv.DictReader(StringIO(sources._read(filename).decode("utf-8-sig"), newline=""), delimiter=delimiter)
    if not reader.fieldnames or not COLUMNS[alias].issubset(reader.fieldnames):
        raise ValueError(f"{filename}: colunas obrigatórias ausentes: {sorted(COLUMNS[alias] - set(reader.fieldnames or []))}")
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError(f"{filename}:{reader.line_num}: colunas desalinhadas")
        yield filename, reader.line_num, row


def _money(value, source, line, brazilian):
    pattern = r"(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}" if brazilian else r"\d+\.\d{2}"
    if not re.fullmatch(pattern, value.strip()):
        raise ValueError(f"{source}:{line}: valor inválido: {value!r}")
    try:
        amount = Decimal(value.replace(".", "").replace(",", ".") if brazilian else value)
    except InvalidOperation as exc:
        raise ValueError(f"{source}:{line}: valor inválido") from exc
    if amount <= 0:
        raise ValueError(f"{source}:{line}: valor não positivo")
    return amount


def _date(value, source, line, brazilian):
    try:
        return datetime.strptime(value, "%d/%m/%Y" if brazilian else "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError(f"{source}:{line}: data inválida: {value!r}") from exc


def _digits(value):
    return "".join(char for char in value if char.isdecimal())


def _tokens(value):
    plain = unicodedata.normalize("NFKD", value.upper()).encode("ascii", "ignore").decode()
    return {word for word in re.findall(r"[A-Z]+", plain) if len(word) >= 4 and word not in STOP_WORDS}


def _hint(bank, title):
    reasons = []
    if bank["amount"] == title["amount"]:
        reasons.append("mesmo_valor")
    if title["tax_id"] and title["tax_id"] in _digits(bank["history"]):
        reasons.append("cnpj_no_historico")
    if len(_tokens(bank["history"]) & _tokens(title["customer"])) >= 2:
        reasons.append("nome_parecido")
    return reasons


def _invoice(value):
    match = re.search(r"\b(?:NF|NOTA FISCAL)\s*0*(\d+)\b", value, re.IGNORECASE)
    return match.group(1) if match else None


def reconcile(sources):
    titles = []
    protheus_by_key = {}
    omie_by_key = {}
    for alias in ("protheus", "omie"):
        for filename, line, row in _rows(sources, alias):
            is_protheus = alias == "protheus"
            key = row["nosso_numero"] if is_protheus else row["invoice_ref"]
            if not key:
                raise ValueError(f"{filename}:{line}: identificador vazio")
            registry = protheus_by_key if is_protheus else omie_by_key
            if key in registry:
                raise ValueError(f"{filename}:{line}: identificador duplicado {key} (linha {registry[key]['line']})")
            title = {"source": alias, "file": filename, "line": line,
                     "document": row["documento"] if is_protheus else key, "reference": key,
                     "customer": row["cliente"] if is_protheus else row["customer_name"],
                     "tax_id": _digits(row["cnpj"] if is_protheus else row["tax_id"]),
                     "due": _date(row["vencimento"] if is_protheus else row["due_date"], filename, line, is_protheus),
                     "amount": _money(row["valor"] if is_protheus else row["amount"], filename, line, is_protheus),
                     "export_status": row["status"] if is_protheus else row["paid_flag"]}
            registry[key] = title
            titles.append(title)

    bank = []
    exact = defaultdict(list)
    for filename, line, row in _rows(sources, "bank"):
        if row["tipo"] not in {"C", "D"}:
            raise ValueError(f"{filename}:{line}: tipo inválido")
        entry = {"file": filename, "line": line, "date": _date(row["data"], filename, line, True),
                 "history": row["historico"], "document": row["documento"].strip(),
                 "amount": _money(row["valor"], filename, line, True), "type": row["tipo"]}
        if entry["type"] == "C" and entry["document"] in protheus_by_key:
            exact[entry["document"]].append(entry)
            entry["status"] = "credito_com_referencia"
            entry["linked_title"] = {"source": "protheus", "line": protheus_by_key[entry["document"]]["line"]}
        else:
            entry["status"] = "debito_a_conferir" if entry["type"] == "D" else "credito_sem_vinculo_confirmado"
        bank.append(entry)

    inbox = sources._read(INBOX_KEY).decode("utf-8").splitlines()
    inbox_invoices = defaultdict(list)
    for line, text in enumerate(inbox, start=1):
        number = _invoice(text)
        if number:
            inbox_invoices[number].append(line)

    for title in titles:
        amount = title["amount"]
        if title["source"] == "protheus":
            linked = exact[title["reference"]]
            credited = sum((entry["amount"] for entry in linked), Decimal("0"))
            title["identified_credit"] = f"{credited:.2f}"
            title["difference_before_unlinked_credits"] = f"{amount - credited:.2f}"
            title["bank_lines"] = [entry["line"] for entry in linked]
            title["status"] = ("sem_credito_identificado" if not linked else
                               "credito_parcial" if credited < amount else
                               "credito_excedente" if credited > amount else "credito_integral_no_extrato")
        else:
            title["status"] = "sem_referencia_bancaria_confirmada"
            title["possible_duplicate_of"] = [
                {"source": "protheus", "line": other["line"]}
                for other in protheus_by_key.values()
                if other["tax_id"] == title["tax_id"] and other["amount"] == amount and other["due"] == title["due"]
            ]
            if title["possible_duplicate_of"]:
                title["status"] = "possivel_duplicidade_entre_sistemas"
        title["next_action"] = {
            "sem_credito_identificado": "Verificar recebimento e títulos ainda abertos antes de cobrar.",
            "credito_parcial": "Conferir saldo no ERP e investigar créditos sem vínculo antes de qualquer baixa.",
            "credito_excedente": "Investigar excedente e possíveis outras faturas antes de qualquer baixa.",
            "credito_integral_no_extrato": "Confirmar identificação e baixa no ERP; extrato não comprova quitação.",
            "sem_referencia_bancaria_confirmada": "Solicitar referência/comprovante; candidatos não confirmam pagamento.",
            "possivel_duplicidade_entre_sistemas": "Verificar se é o mesmo título nos dois ERPs antes de cobrar novamente.",
        }[title["status"]]
        title["candidate_bank_credits"] = []

    for entry in bank:
        if entry["status"] == "credito_sem_vinculo_confirmado":
            entry["candidates"] = []
            for title in titles:
                reasons = _hint(entry, title)
                if reasons:
                    ref = {"source": title["source"], "line": title["line"]}
                    entry["candidates"].append({**ref, "reasons": reasons})
                    title["candidate_bank_credits"].append({"line": entry["line"], "reasons": reasons})
        elif entry["type"] == "D":
            number = _invoice(entry["history"])
            entry["inbox_lines"] = inbox_invoices.get(number, []) if number else []
    for title in titles:
        title["amount"] = f"{title['amount']:.2f}"
    for entry in bank:
        entry["amount"] = f"{entry['amount']:.2f}"
        entry["next_action"] = ("Conferir a baixa do título no ERP." if entry["status"] == "credito_com_referencia" else
                                "Conferir nota, entrega, aprovação e pagamento antes de repetir o débito." if entry["type"] == "D" and entry.get("inbox_lines") else
                                "Conferir natureza do débito e classificação contábil." if entry["type"] == "D" else
                                "Solicitar referência/comprovante; não atribuir por nome ou valor.")

    return {"scope": "amostra_exportada; saldo e quitação exigem validação humana",
            "counts": {"protheus": len(protheus_by_key), "omie": len(omie_by_key), "bank": len(bank), "inbox_lines": len(inbox)},
            "titles": titles, "bank": bank,
            "inbox": {"file": INBOX_KEY, "note": "Anexos citados não foram fornecidos; mensagens não comprovam aprovação ou pagamento."}}


def overview(report):
    return {"scope": report["scope"], "counts": report["counts"],
            "titles": [{"source": t["source"], "line": t["line"], "document": t["document"],
                        "amount": t["amount"], "status": t["status"],
                        "identified_credit": t.get("identified_credit"), "bank_lines": t.get("bank_lines", []),
                        "candidate_bank_lines": [c["line"] for c in t["candidate_bank_credits"]],
                        "possible_duplicate_of": t.get("possible_duplicate_of", [])} for t in report["titles"]],
            "unlinked_bank": [{"line": b["line"], "amount": b["amount"], "status": b["status"],
                               "candidates": b.get("candidates", []), "inbox_lines": b.get("inbox_lines", [])}
                              for b in report["bank"] if b["status"] != "credito_com_referencia"],
            "inbox": report["inbox"]}


def title_case(report, title):
    bank_by_line = {row["line"]: row for row in report["bank"]}
    return {
        "title": title,
        "linked_credits": [bank_by_line[line] for line in title.get("bank_lines", [])],
        "candidate_credits": [bank_by_line[item["line"]] for item in title["candidate_bank_credits"]],
        "same_tax_id_titles": [other for other in report["titles"]
                               if title["tax_id"] and other is not title and other["tax_id"] == title["tax_id"]],
    }


def case_bundle(report, question):
    invoice_numbers = set(re.findall(r"\bNF\s*0*(\d+)\b", question, re.IGNORECASE))
    cases = []
    for title in report["titles"]:
        named_invoice = title["source"] == "protheus" and _invoice(title["document"]) in invoice_numbers
        named_reference = (len(title["reference"]) >= 8 and
                           re.search(rf"(?<!\w){re.escape(title['reference'])}(?!\w)", question, re.IGNORECASE))
        if not (named_invoice or named_reference):
            continue
        cases.append(title_case(report, title))
    return {"scope": report["scope"], "cases": cases} if cases else None


def _br(value):
    return f"{Decimal(value):,.2f}".translate(BR_SEPARATORS)


def format_case(bundle):
    lines = ["Dossiê verificado (pistas não são quitação):"]
    for case in bundle["cases"]:
        title = case["title"]
        lines.append(f"- {title['document']} ({title['file']}:{title['line']}): R$ {_br(title['amount'])}; "
                     f"status exportado {title['export_status']}; situação {title['status'].replace('_', ' ')}.")
        if "identified_credit" in title:
            lines.append(f"  Crédito com referência: R$ {_br(title['identified_credit'])}; diferença antes de créditos sem vínculo: "
                         f"R$ {_br(title['difference_before_unlinked_credits'])} (não é saldo confirmado).")
        for bank in case["linked_credits"]:
            lines.append(f"  Vinculado pelo número: {bank['file']}:{bank['line']}, R$ {_br(bank['amount'])}.")
        for bank in case["candidate_credits"]:
            others = ", ".join(f"{candidate['source']}:{candidate['line']}" for candidate in bank["candidates"]
                               if candidate["source"] != title["source"] or candidate["line"] != title["line"])
            lines.append(f"  Crédito NÃO atribuído: {bank['file']}:{bank['line']}, R$ {_br(bank['amount'])}, "
                         f"{bank['history']}; outros candidatos: {others or 'nenhum'}.")
        for other in case["same_tax_id_titles"]:
            lines.append(f"  Outro título do mesmo CNPJ (não necessariamente a mesma fatura): "
                         f"{other['document']} ({other['file']}:{other['line']}), R$ {_br(other['amount'])}; "
                         f"status exportado {other['export_status']}.")
        lines.append(f"  Próxima ação: {title['next_action']}")
    return "\n".join(lines)


def worklist(report):
    rows = []
    bank_file = CSV_SOURCES["bank"][0]
    for title in report["titles"]:
        status = title["status"]
        priority = ("alta" if status in {"credito_parcial", "credito_excedente", "possivel_duplicidade_entre_sistemas"}
                    else "baixa" if status == "credito_integral_no_extrato" else "media")
        evidence = [f"{title['file']}:{title['line']}"]
        evidence += [f"{bank_file}:{line}" for line in title.get("bank_lines", [])]
        evidence += [f"{bank_file}:{item['line']}" for item in title["candidate_bank_credits"]]
        evidence += [f"{CSV_SOURCES['protheus'][0]}:{item['line']}" for item in title.get("possible_duplicate_of", [])]
        rows.append([priority, title["source"], str(title["line"]), title["document"],
                     _br(title["amount"]), status.replace("_", " "), ", ".join(evidence), title["next_action"]])
    for bank in report["bank"]:
        if bank["status"] == "credito_com_referencia":
            continue
        priority = ("alta" if bank["type"] == "D" and bank.get("inbox_lines") else
                    "baixa" if bank["type"] == "D" else
                    "alta" if not bank.get("candidates") else "media")
        evidence = [f"{bank['file']}:{bank['line']}"]
        evidence += [f"{report['inbox']['file']}:{line}" for line in bank.get("inbox_lines", [])]
        for candidate in bank.get("candidates", []):
            evidence.append(f"{CSV_SOURCES[candidate['source']][0]}:{candidate['line']}")
        rows.append([priority, "bank", str(bank["line"]), bank["document"] or bank["history"],
                     _br(bank["amount"]), bank["status"].replace("_", " "), ", ".join(evidence), bank["next_action"]])
    rows.sort(key=lambda row: ({"alta": 0, "media": 1, "baixa": 2}[row[0]], row[1], int(row[2])))
    return [["prioridade", "origem", "linha", "documento_ou_historico", "valor_brl", "situacao",
             "evidencias", "proxima_acao"]] + [
                 ["'" + cell if cell.lstrip().startswith(("=", "+", "-", "@")) else cell for cell in row]
                 for row in rows
             ]
