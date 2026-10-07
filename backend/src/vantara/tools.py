import json

from .reconcile import overview, reconcile
from .sources import CSV_SOURCES, Sources


MAX_OUTPUT_CHARS = 6000
TOOLS = [
    {"type": "function", "name": "list_sources", "description": "Lista arquivos exportados no S3 simulado e colunas dos CSVs.",
     "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "search_csv", "description": "Busca termo em linhas de um CSV exportado; devolve arquivo, linha e colunas originais (até 12 resultados).",
     "parameters": {"type": "object", "properties": {
         "source": {"type": "string", "enum": list(CSV_SOURCES)},
         "query": {"type": "string", "description": "Texto ou CNPJ sem pontuação para buscar."}},
         "required": ["source", "query"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "search_text", "description": "Busca na caixa de entrada exportada; devolve linhas vizinhas numeradas (até 6 ocorrências).",
     "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                    "required": ["query"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "list_reconciliation", "description": "Percorre títulos e créditos sem vínculo, inclusive candidatos NÃO confirmados. Paginar até total.",
     "parameters": {"type": "object", "properties": {"offset": {"type": "integer", "minimum": 0}},
                    "required": ["offset"], "additionalProperties": False}, "strict": True},
    {"type": "function", "name": "get_record", "description": "Detalha título ou lançamento bancário pela linha do relatório antes de concluir.",
     "parameters": {"type": "object", "properties": {
         "source": {"type": "string", "enum": ["protheus", "omie", "bank"]},
         "line": {"type": "integer", "minimum": 2}},
         "required": ["source", "line"], "additionalProperties": False}, "strict": True},
]


def execute(sources: Sources, name: str, arguments: str, report=None) -> str:
    try:
        args = json.loads(arguments)
        if name == "list_sources" and args == {}:
            result = sources.list_sources()
        elif name == "search_csv" and isinstance(args, dict) and set(args) == {"source", "query"}:
            result = sources.search_csv(**args)
        elif name == "search_text" and isinstance(args, dict) and set(args) == {"query"}:
            result = sources.search_text(**args)
        elif name == "list_reconciliation" and isinstance(args, dict) and set(args) == {"offset"} and type(args["offset"]) is int and args["offset"] >= 0:
            summary = overview(report if report is not None else reconcile(sources))
            items = summary["titles"] + summary["unlinked_bank"]
            result = {"counts": summary["counts"], "total": len(items), "offset": args["offset"],
                      "items": items[args["offset"]:args["offset"] + 10]}
        elif name == "get_record" and isinstance(args, dict) and set(args) == {"source", "line"} and args["source"] in {"protheus", "omie", "bank"} and type(args["line"]) is int and args["line"] >= 2:
            snapshot = report if report is not None else reconcile(sources)
            records = snapshot["bank"] if args["source"] == "bank" else snapshot["titles"]
            result = next((row for row in records if row["line"] == args["line"] and
                           (args["source"] == "bank" or row["source"] == args["source"])),
                          {"error": "Registro não encontrado"})
        else:
            raise ValueError("Ferramenta ou argumentos não permitidos")
    except (ValueError, TypeError) as exc:
        result = {"error": str(exc)}
    output = json.dumps(result, ensure_ascii=False)
    if len(output) > MAX_OUTPUT_CHARS:
        output = json.dumps({"error": "Resultado extenso; refine a busca."})
    return output
