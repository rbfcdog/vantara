import json
import os

from openai import OpenAI

from .prompt import INSTRUCTIONS
from .reconcile import case_bundle, format_case, reconcile
from .sources import Sources
from .tools import TOOLS, execute


MAX_CALLS = 8


def draft_request(evidence: dict, model: str = "gpt-6-luna") -> str:
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Defina OPENAI_API_KEY antes de gerar o rascunho.")
    response = OpenAI().responses.create(
        model=model,
        instructions=("Você prepara, em português, um RASCUNHO NÃO ENVIADO de solicitação "
                      "de identificação de crédito bancário C sem vínculo com título a receber. "
                      "A entrada é um ou mais pares data/valor, e SOMENTE estes são fatos "
                      "disponíveis: não há pagador, CNPJ, referência, título, banco, contato "
                      "verificado ou comprovante. Para cada crédito, indique data e valor "
                      "separadamente e peça o número da fatura ou o nosso número do boleto, "
                      "quando houver, e o identificador/comprovante da transação para "
                      "permitir a conferência. Não atribua créditos iguais "
                      "a um mesmo título nem invente NF, beneficiário ou remetente. A entrada "
                      "no extrato não confirma quitação, saldo nem baixa no ERP; não solicite "
                      "novo pagamento, dados bancários sensíveis ou documentos além dos "
                      "necessários para identificar o crédito. Não afirme que o destinatário "
                      "recebeu o pedido, que houve cobrança, que o contato foi verificado ou "
                      "que algo foi enviado. Devolva apenas o corpo da mensagem curta "
                      "para uma pessoa externa, sem título, cabeçalho, assinatura, nomes "
                      "dos ERPs, termos de contabilidade nem comentário técnico sobre "
                      "quitação ou baixa."),
        input=json.dumps(evidence, ensure_ascii=False),
        tools=[],
        store=False,
    )
    if response.status != "completed" or not response.output_text or not response.output_text.strip():
        raise RuntimeError("Modelo não retornou um rascunho completo.")
    return response.output_text.strip()


def run(question: str, sources: Sources, model: str = "gpt-6-luna", include_dossier: bool = True,
        history: list[dict[str, str]] | None = None) -> str:
    if not question.strip() or len(question) > 1000:
        raise ValueError("pergunta deve ter de 1 a 1000 caracteres")
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("Defina OPENAI_API_KEY antes de executar o agente.")
    client = OpenAI()
    report = reconcile(sources)
    dossier = case_bundle(report, question)
    context = json.dumps(dossier if dossier else report["summary"], ensure_ascii=False)
    messages = [*history, {"role": "user", "content": question}] if history else [{"role": "user", "content": question}]
    calls = 0
    while True:
        response = client.responses.create(
            model=model, instructions=INSTRUCTIONS + (
                "\nDossiê dos títulos citados; explique a conclusão com linhas de evidência e lacunas:\n" if dossier else
                "\nResumo calculado da amostra; consulte registros para detalhes. Sem cobertura total dos quatro bancos:\n"
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


