"""Read-only AI suggestions for bank credits the deterministic engine could not link."""

import json
import os

from openai import OpenAI, OpenAIError

from .reconcile import _br


_MODEL = "gpt-6-luna"
_INSTRUCTIONS = (
    "Você decide, em português, a atribuição de créditos bancários C sem vínculo confirmado, a partir de "
    "fatos numerados dos exports. Retorne APENAS JSON com objeto items, um item para cada bank_line "
    "fornecida, sem itens inventados: {\"items\":[{\"bank_line\":17,"
    "\"verdict\":\"candidato_mais_provavel\"|\"sem_atribuicao\",\"candidate\":\"omie:4\" ou null,"
    "\"rationale\":\"...\",\"evidence\":[\"arquivo:linha\"],\"confirm_with\":\"...\"}]}. "
    "Regras: candidate DEVE ser exatamente um dos candidatos listados para aquele crédito, na forma "
    "'source:linha', ou null quando verdict=sem_atribuicao. Cada evidence deve ser exatamente um ID "
    "arquivo:linha fornecido para AQUELE crédito. "
    "Seu veredito alimenta um executor automático com guardras próprias: duplicidade entre ERPs, valor "
    "excedente e candidato fora da lista são retidos pelo executor independentemente da sua decisão. "
    "Seu papel é decidir a atribuição mais provável: escolha candidato_mais_provavel sempre que os fatos "
    "comparativos favorecerem claramente UM título sobre os demais (por exemplo mesmo valor E nome "
    "compatível, enquanto os outros candidatos divergem em valor); reserve sem_atribuicao para ambiguidade "
    "real: fatos que não diferenciam dois candidatos, nenhum candidato listado ou divergência de valor "
    "com TODOS os candidatos. No rationale, diga o que sustenta a decisão e o que permanece sem prova; em "
    "confirm_with, a consulta que validaria a decisão depois (comprovante/ID da transação, consulta ao ERP "
    "atual). Não proponha criar NF/título a partir do extrato. Não use ferramentas nem outras fontes."
)


def _credits(report: dict) -> list[dict]:
    titles = {(title["source"], title["line"]): title for title in report["titles"]}
    credits = []
    for entry in report["bank"]:
        if entry["type"] != "C" or entry["status"] != "credito_sem_vinculo_confirmado":
            continue
        candidates = []
        for clue in entry.get("candidates") or []:
            title = titles.get((clue["source"], clue["line"]))
            if not title:
                continue
            candidates.append({
                "id": f"{clue['source']}:{clue['line']}",
                "reasons": clue["reasons"],
                "documento": title["document"],
                "cliente": title["customer"],
                "valor": _br(title["amount"]),
                "vencimento": title["due"],
            })
        credits.append({
            "bank": {"id": f"{entry['file']}:{entry['line']}", "line": entry["line"], "date": entry["date"],
                     "history": entry["history"], "document": entry["document"], "amount": entry["amount"]},
            "candidates": candidates,
            "next_action": entry["next_action"],
        })
    return credits


def unlinked_suggestions(report: dict) -> dict:
    """Rules-first suggestions; optionally annotate in one read-only model call.
    Model output is accepted only when every item cites candidates and evidence
    lines that belong to that credit; any violation keeps the deterministic view.
    """
    credits = _credits(report)
    suggestions = [{"bank": credit["bank"], "candidates": credit["candidates"],
                    "next_action": credit["next_action"]} for credit in credits]
    result = {"method": "regras_sobre_exports", "status": "no_cases" if not credits else "not_configured",
              "suggestions": suggestions}
    if not credits:
        return result
    if not os.environ.get("OPENAI_API_KEY"):
        return result
    try:
        excerpts = []
        for credit in credits:
            titles = [{"id": c["id"], "documento": c["documento"], "cliente": c["cliente"],
                       "valor": c["valor"], "vencimento": c["vencimento"], "pistas": c["reasons"]}
                      for c in credit["candidates"]]
            excerpts.append({"bank_line": credit["bank"]["line"],
                             "credito": {"id": credit["bank"]["id"], "data": credit["bank"]["date"],
                                         "historico": credit["bank"]["history"],
                                         "documento": credit["bank"]["document"],
                                         "valor": _br(credit["bank"]["amount"])},
                             "candidatos": titles,
                             "next_action": credit["next_action"]})
        response = OpenAI().responses.create(model=_MODEL, instructions=_INSTRUCTIONS,
                                             input=json.dumps({"credits": excerpts}, ensure_ascii=False),
                                             tools=[], store=False)
        if response.status != "completed" or not response.output_text:
            raise ValueError("Resposta incompleta")
        items = json.loads(response.output_text)["items"]
        if not isinstance(items, list) or len(items) != len(credits):
            raise ValueError("Quantidade de itens inválida")
        by_line = {credit["bank"]["line"]: credit for credit in credits}
        seen = set()
        pending = {}
        for item in items:
            credit = by_line.get(item.get("bank_line"))
            if credit is None or item["bank_line"] in seen:
                raise ValueError("Crédito não incluído nos exports")
            seen.add(item["bank_line"])
            if item["verdict"] not in {"candidato_mais_provavel", "sem_atribuicao"}:
                raise ValueError("Veredito inválido")
            allowed_candidates = {candidate["id"] for candidate in credit["candidates"]}
            if item["verdict"] == "sem_atribuicao":
                if item["candidate"] is not None:
                    raise ValueError("Sem atribuição não pode ter candidato")
            elif item["candidate"] not in allowed_candidates:
                raise ValueError("Candidato fora da lista determinística")
            allowed_ids = {credit["bank"]["id"]} | allowed_candidates
            if (not isinstance(item["evidence"], list) or not item["evidence"] or
                any(ref not in allowed_ids for ref in item["evidence"]) or
                any(key not in item or not isinstance(item[key], str) or not item[key].strip()
                    for key in ("rationale", "confirm_with"))):
                raise ValueError("Citação ou estrutura inválida")
            pending[item["bank_line"]] = {key: item[key] for key in ("verdict", "candidate", "rationale",
                                                                    "evidence", "confirm_with")}
        for suggestion in suggestions:
            suggestion.update(pending[suggestion["bank"]["line"]])
        result.update(method="ia_read_only_sobre_exports", status="completed")
    except (OpenAIError, ValueError, TypeError, KeyError):
        # An API or untrusted-output failure cannot block the source-grounded publication.
        result["status"] = "failed"
    return result
