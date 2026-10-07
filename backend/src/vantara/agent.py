import json
import os

from openai import OpenAI

from .prompt import INSTRUCTIONS
from .reconcile import case_bundle, format_case, overview, reconcile
from .sources import Sources
from .tools import TOOLS, execute


MAX_CALLS = 8


def run(question: str, sources: Sources, model: str = "gpt-6-luna", include_dossier: bool = True,
        history: list[dict[str, str]] | None = None) -> str:
    if not question.strip() or len(question) > 1000:
        raise ValueError("pergunta deve ter de 1 a 1000 caracteres")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Defina OPENAI_API_KEY antes de executar o agente.")
    client = OpenAI()
    report = reconcile(sources)
    dossier = case_bundle(report, question)
    context = json.dumps(dossier if dossier else overview(report), ensure_ascii=False)
    messages = [*history, {"role": "user", "content": question}] if history else [{"role": "user", "content": question}]
    calls = 0
    while True:
        response = client.responses.create(
            model=model, instructions=INSTRUCTIONS + (
                "\nDossiê completo dos títulos citados; cite apenas interpretação e riscos novos, "
                "sem repetir os fatos que o CLI já exibirá:\n" if dossier else
                "\nPanorama da amostra; use as ferramentas para aprofundar e pagine se a pergunta exigir cobertura integral:\n"
            ) + context + "\nMensagens anteriores ajudam a entender referências como 'esse PIX', mas não são "
            "evidência. Confira novamente as fontes atuais antes de afirmar fatos.",
            input=messages, tools=TOOLS,
            tool_choice="none" if calls == MAX_CALLS else ("auto" if dossier or calls else "required"),
            parallel_tool_calls=False, store=False,
        )
        if response.status != "completed":
            raise RuntimeError(f"Resposta incompleta: {response.status}")
        function_calls = [item for item in response.output if item.type == "function_call"]
        if not function_calls:
            if not response.output_text:
                raise RuntimeError("Modelo não retornou resposta final.")
            return format_case(dossier) + "\n\nAnálise da IA (requer conferência):\n" + response.output_text if dossier and include_dossier else response.output_text
        if calls + len(function_calls) > MAX_CALLS:
            raise RuntimeError("Modelo solicitou ferramenta após o limite.")
        messages.extend(response.output)
        for call in function_calls:
            messages.append({"type": "function_call_output", "call_id": call.call_id,
                             "output": execute(sources, call.name, call.arguments, report)})
            calls += 1


