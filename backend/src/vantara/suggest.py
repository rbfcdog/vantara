"""Model-reviewed suggestions for every bank credit, grounded in reconciled exports."""

import json
import os

from openai import OpenAI, OpenAIError

from .reconcile import _br


_MODEL = "gpt-6-luna"
_INSTRUCTIONS = (
    "Você revisa TODOS os créditos bancários C (inclusive os vinculados por referência exata), a partir de "
    "fatos numerados dos exports. Retorne APENAS JSON com objeto items, um item para cada bank_line "
    "fornecida, sem itens inventados: {\"items\":[{\"bank_line\":17,"
    "\"verdict\":\"candidato_mais_provavel\"|\"sem_atribuicao\",\"candidate\":\"omie:4\" ou null,"
    "\"rationale\":\"...\",\"evidence\":[\"arquivo:linha\"],\"confirm_with\":\"...\"}]}. "
    "Regras: candidate DEVE ser exatamente um dos candidatos listados para aquele crédito, na forma "
    "'source:linha', ou null quando verdict=sem_atribuicao. Cada evidence deve ser exatamente um ID "
    "arquivo:linha fornecido para AQUELE crédito. "
    "Para crédito com referência exata única, escolha obrigatoriamente o único candidato: a prova "
    "documental não pode ser trocada nem descartada pela IA. Para os demais, seu veredito alimenta "
    "um executor automático com guardras próprias: duplicidade entre ERPs, valor excedente e candidato "
    "fora da lista são retidos pelo executor independentemente da sua decisão. "
    "Seu papel é decidir a atribuição mais provável: escolha candidato_mais_provavel sempre que houver UM "
    "título claramente favorecido — mesmo valor que o crédito (ainda que nomes difiram levemente), OU "
    "identificador forte no histórico (CNPJ completo) apontando para um único candidato, OU valor próximo "
    "com único candidato e nome compatível (baixa parcial: o executor registra o saldo restante). Reserve "
    "sem_atribuicao apenas para: nenhum candidato listado, fatos que não diferenciam dois candidatos, ou "
    "valor que não casa com NENHUM candidato sem identificador forte. Divergência de valor para MAIS não o "
    "impede de atribuir: o executor retém excesso com motivo. "
    "No rationale, diga o que sustenta a decisão e o que permanece sem prova; em confirm_with, a consulta "
    "que validaria a decisão depois (comprovante/ID da transação, consulta ao ERP atual). Não proponha "
    "criar NF/título a partir do extrato. Não use ferramentas nem outras fontes."
)


def _credits(report: dict) -> list[dict]:
    titles = {(title["source"], title["line"]): title for title in report["titles"]}
    credits = []
    for entry in report["bank"]:
        if entry["type"] != "C":
            continue
        candidates = []
        if entry["status"] == "credito_com_referencia":
            title = titles[(entry["linked_title"]["source"], entry["linked_title"]["line"])]
            candidates.append({
                "id": f"{title['source']}:{title['line']}", "reasons": ["referencia_exata_unica"],
                "documento": title["document"], "cliente": title["customer"],
                "valor": _br(title["amount"]), "vencimento": title["due"],
            })
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
            "exact": entry["status"] == "credito_com_referencia",
        })
    return credits


def unlinked_suggestions(report: dict) -> dict:
    """Review every bank credit with the model; reject fabricated or changed exact links.
    An invalid batch leaves the whole result rules-only, without autonomous writes.
    """
    credits = _credits(report)
    suggestions = [{"bank": credit["bank"], "candidates": credit["candidates"],
                    "next_action": credit["next_action"], "exact": credit["exact"]} for credit in credits]
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
            excerpts.append({"bank_line": credit["bank"]["line"], "vinculo_exato": credit["exact"],
                             "credito": {"id": credit["bank"]["id"], "data": credit["bank"]["date"],
                                         "historico": credit["bank"]["history"],
                                         "documento": credit["bank"]["document"],
                                         "valor": _br(credit["bank"]["amount"])},
                             "candidatos": titles,
                             "next_action": credit["next_action"]})
        items = []
        client = OpenAI()
        for start in range(0, len(excerpts), 10):
            response = client.responses.create(model=_MODEL, instructions=_INSTRUCTIONS,
                                                 input=json.dumps({"credits": excerpts[start:start + 10]}, ensure_ascii=False),
                                                 tools=[], store=False)
            if response.status != "completed" or not response.output_text:
                raise ValueError("Resposta incompleta")
            batch = json.loads(response.output_text)["items"]
            if not isinstance(batch, list) or len(batch) != len(excerpts[start:start + 10]):
                raise ValueError("Quantidade de itens inválida")
            items.extend(batch)
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
            if credit["exact"] and (item["verdict"] != "candidato_mais_provavel" or
                                     item["candidate"] != credit["candidates"][0]["id"]):
                raise ValueError("A IA não pode alterar vínculo exato")
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
