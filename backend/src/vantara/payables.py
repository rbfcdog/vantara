"""Read-only review of invoice debits in the original bank and mailbox exports."""

import json
import os
import re
from collections import defaultdict

from openai import OpenAI, OpenAIError

from .reconcile import _br, _invoice
from .sources import INBOX_KEY


_MODEL = "gpt-6-luna"
_SECTION = re.compile(r"^--- (?:E-MAIL|TRECHO DO GRUPO)")
_AMOUNT = re.compile(r"R\$\s*([\d.,]+)")
_MISSING = [
    "Identificação do favorecido e comprovante bancário que vincule o débito à NF.",
    "Nota fiscal, boleto e anexos; mensagens que os mencionam não substituem os documentos.",
    "Comprovante de entrega e conferência dos volumes efetivamente recebidos.",
    "Aprovação formal válida após a conferência da entrega e do valor devido.",
    "Histórico de contas a pagar no ERP e eventual quitação/duplicidade nas demais contas.",
]
_RECOMMENDATION = ("Conferir os documentos e a autorização antes de repetir pagamento; "
                   "isto é uma recomendação de revisão, não um bloqueio aplicado nem confirmação de quitação.")
_INSTRUCTIONS = (
    "Você analisa contas a pagar SOMENTE para leitura, em português, a partir de linhas numeradas dos exports. "
    "Retorne APENAS JSON com objeto cases, um caso para cada invoice fornecida, sem casos inventados: "
    "{\"cases\":[{\"invoice\":\"NF 88231\",\"observations\":[{\"kind\":\"valor_divergente\","
    "\"text\":\"...\",\"evidence\":[\"arquivo:linha\"]}],\"missing_evidence\":[\"...\"],"
    "\"next_query\":\"...\",\"recommendation\":\"...\"}]}. "
    "Cada referência de evidence deve ser exatamente um ID arquivo:linha das linhas fornecidas para AQUELE caso. "
    "Distingua registro bancário, alegação em mensagem e comprovante ausente. Quando as linhas sustentarem, "
    "aponte separadamente a diferença entre valor inicial por e-mail e boleto atualizado, entrega parcial, "
    "aprovação incerta ('acho que sim' após dúvida sobre entrega completa) e contradição cronológica "
    "(mensagem de 19/07 diz que pagou dia 20). Solicite identificação de favorecido, NF e anexos, "
    "entrega, aprovação e histórico AP no ERP. Não afirme que fornecedor foi pago, favorecido foi "
    "verificado ou aprovação é válida. Nunca diga que um bloqueio já foi aplicado nem inicie pagamento; "
    "apenas recomende conferência antes de repetir pagamento. Esta revisão de débitos D é A PAGAR: "
    "não gere título/NF A RECEBER no Protheus ou Omie, não proponha baixa de crédito C e não use "
    "uma saída bancária como comprovação de recebimento de cliente. Não use ferramentas nem outras fontes."
)


def _sections(inbox: list[str]) -> list[list[int]]:
    starts = [index for index, text in enumerate(inbox) if _SECTION.match(text)]
    if starts:
        return [list(range(start, end)) for start, end in zip(starts, [*starts[1:], len(inbox)])]
    return [list(range(max(0, index - 6), min(len(inbox), index + 7)))
            for index, text in enumerate(inbox) if _invoice(text)]


def payables_review(report: dict, inbox_bytes: bytes) -> dict:
    """Collect source-grounded cases; optionally annotate in one read-only model call."""
    groups = defaultdict(list)
    for entry in report["bank"]:
        if entry["type"] == "D":
            number = _invoice(entry["history"])
            if number:
                groups[number].append(entry)
    if not groups:
        return {"method": "regras_sobre_exports", "status": "no_cases", "cases": []}

    inbox = inbox_bytes.decode("utf-8").splitlines()
    sections = _sections(inbox)
    cases, excerpts = [], []
    for number, entries in groups.items():
        lines = sorted({index + 1 for section in sections
                        if any(_invoice(inbox[index]) == number for index in section)
                        for index in section if inbox[index].strip()})
        evidence = [f"{entry['file']}:{entry['line']}" for entry in entries]
        evidence.extend(f"{INBOX_KEY}:{line}" for line in lines)
        text = [inbox[line - 1] for line in lines]
        amounts = [match.group(1) for value in text if (match := _AMOUNT.search(value))]
        originals = [match.group(1) for value in text
                     if (match := re.search(r"Valor original\s*R\$\s*([\d.,]+)", value, re.IGNORECASE))]
        updated = [match.group(1) for value in text
                   if (match := re.search(r"Valor atualizado\s*R\$\s*([\d.,]+)", value, re.IGNORECASE))]
        bank_amounts = {_br(entry["amount"]) for entry in entries}
        if originals and updated:
            amount_note = (f"Confrontar valor original R$ {originals[0]} com boleto atualizado R$ {updated[0]} "
                           f"e débito(s) no extrato (R$ {', R$ '.join(sorted(bank_amounts))}); ")
        elif amounts:
            amount_note = (f"Confrontar valores informados nas mensagens ({', '.join(dict.fromkeys(amounts))}) "
                           f"com débito(s) no extrato (R$ {', R$ '.join(sorted(bank_amounts))}); ")
        else:
            amount_note = ""
        volumes = next((match.group(0) for value in text
                        if (match := re.search(r"\d+ de \d+ volumes", value, re.IGNORECASE))), None)
        delivery_note = (f"conferir entrega parcial ({volumes}) e volumes pendentes; " if volumes else
                         "conferir comprovante de entrega; ")
        approval_note = "esclarecer aprovação após resposta incerta sobre entrega; " if any(
            "acho que sim" in value.casefold() for value in text) else "confirmar aprovação; "
        chronology_note = "conferir cronologia das mensagens e do débito; " if text else ""
        query = (amount_note + delivery_note + approval_note + chronology_note +
                 "solicitar favorecido e comprovante bancário, NF/boleto anexados e "
                 "histórico de contas a pagar no ERP.")
        query = query[0].upper() + query[1:]
        cases.append({"invoice": f"NF {number}", "bank_lines": [entry["line"] for entry in entries],
                      "inbox_lines": lines, "evidence": evidence,
                      "bank_entries": [{"line": entry["line"], "amount": entry["amount"], "date": entry["date"]}
                                       for entry in entries],
                      "observations": [], "missing_evidence": _MISSING.copy(),
                      "next_query": query,
                      "recommendation": _RECOMMENDATION})
        excerpts.append({"invoice": f"NF {number}",
                         "bank": [{"id": f"{entry['file']}:{entry['line']}", "date": entry["date"],
                                   "history": entry["history"], "amount": entry["amount"],
                                   "document": entry["document"], "type": entry["type"]} for entry in entries],
                         "inbox": [{"id": f"{INBOX_KEY}:{line}", "text": inbox[line - 1]} for line in lines]})

    result = {"method": "regras_sobre_exports", "status": "not_configured", "cases": cases}
    if not os.environ.get("OPENAI_API_KEY"):
        return result
    try:
        response = OpenAI().responses.create(model=_MODEL, instructions=_INSTRUCTIONS,
                                             input=json.dumps({"cases": excerpts}, ensure_ascii=False),
                                             tools=[], store=False)
        if response.status != "completed" or not response.output_text:
            raise ValueError("Resposta incompleta")
        parsed = json.loads(response.output_text)
        model_cases = parsed["cases"]
        if not isinstance(model_cases, list) or len(model_cases) != len(cases):
            raise ValueError("Quantidade de casos inválida")
        by_invoice = {case["invoice"]: case for case in cases}
        seen = set()
        pending = {}
        for item in model_cases:
            invoice = item["invoice"]
            if invoice not in by_invoice or invoice in seen:
                raise ValueError("NF não incluída nos exports")
            seen.add(invoice)
            observations = item["observations"]
            if not isinstance(observations, list) or not observations:
                raise ValueError("Observações ausentes")
            allowed = set(by_invoice[invoice]["evidence"])
            for observation in observations:
                if (not isinstance(observation, dict) or
                    not all(isinstance(observation.get(key), str) and observation[key].strip()
                            for key in ("kind", "text")) or
                    not isinstance(observation.get("evidence"), list) or
                    not observation["evidence"] or
                    any(not isinstance(ref, str) or ref not in allowed for ref in observation["evidence"])):
                    raise ValueError("Observação ou citação inválida")
            if (not isinstance(item["missing_evidence"], list) or
                any(not isinstance(gap, str) for gap in item["missing_evidence"]) or
                not isinstance(item["next_query"], str) or
                not isinstance(item["recommendation"], str)):
                raise ValueError("Estrutura de caso inválida")
            pending[invoice] = observations
        for case in cases:
            case["observations"] = pending[case["invoice"]]
        result.update(method="ia_read_only_sobre_exports", status="completed")
    except (OpenAIError, ValueError, TypeError, KeyError):
        # An API or untrusted-output failure cannot block the source-grounded publication.
        result["status"] = "failed"
    return result
