# Vantara

A demo lê os quatro arquivos sintéticos de `data_pack/` sem alterar os bytes. O Floci simula o armazenamento na infraestrutura da Vantara; decisões humanas sobre casos ficam separadamente em SQLite local. Durante os primeiros 90 dias o Protheus continua restrito a leitura e exportação. Nenhuma tela ou API faz baixa, aprovação ou pagamento.

## Rodar localmente

Execute na raiz do projeto

```sh
docker compose up -d --wait
uv sync --project backend
npm --prefix frontend install
uv run --project backend python -m vantara seed
```

Abra outro terminal para a API

```sh
env -u OPENAI_API_KEY uv run --project backend --env-file .env uvicorn vantara.api:app --host 127.0.0.1 --port 8000
```

O arquivo `.env` na raiz deve conter `OPENAI_API_KEY=sua-chave` para investigação e rascunhos com IA; fila, dossiês, atribuições e decisões funcionam sem chave. O comando remove qualquer chave diferente que tenha sido exportada no terminal antes de carregar o arquivo. A IA usa uma API externa: contexto e trechos consultados saem do ambiente local nesta demonstração com dados sintéticos.

Abra outro terminal para a interface

```sh
npm --prefix frontend run dev
```

Acesse `http://127.0.0.1:3000`. A visão de gestão agrupa **casos pendentes**, não saldo de caixa, por situação, prioridade, responsável informado e tempo desde a primeira entrada no fluxo. Clique num grupo para filtrar a fila e abrir o dossiê. Cada caso mostra a próxima conferência derivada dos exports, referências às linhas e a investigação opcional com IA. Informe um responsável e uma nota humana para marcar **inconclusivo** ou **encerrado**; para reabrir, registre o motivo. Encerrar a revisão não atribui créditos nem comprova quitação. O histórico e o estado sobrevivem a recargas, mas o nome do responsável não é uma identidade autenticada.

A IA pode preparar um pedido **não enviado** de comprovante ou referência para crédito sem vínculo, usando somente data e valor; revise e copie o texto para tratar fora do sistema. Não há envio de mensagens. A conversa livre aparece acima da visão de gestão e da fila para investigações adicionais: até 30 trocas ficam neste navegador, e até seis trocas anteriores acompanham a próxima pergunta como contexto, nunca como prova. **Limpar conversa** remove esse histórico local; novos exports também o limpam. A interface usa a API Python pelo servidor Next.js, sem expor a chave da OpenAI ou acesso ao Floci no navegador. Enquanto a fila carrega, não há contadores; após falhas, há quatro tentativas (imediata, depois 1 s, 3 s e 9 s) e um diagnóstico. O roteiro de apresentação anterior está em [`docs/roteiro-video-4min.md`](docs/roteiro-video-4min.md).

Se a API confirmar uma fila vazia, a tela mostra o horário do último processamento e permite selecionar os quatro arquivos originais (`01` a `04` em `data_pack/`) com **Enviar arquivos**. O backend valida todos os formatos e concilia antes de gravar os quatro objetos no bucket da Vantara no Floci local; o envio não escreve no Protheus. Arquivos inválidos não substituem os anteriores. Se a API estiver fora do ar, a tela mostra erro em vez de fingir que a fila está vazia.

## CLI e API

```sh
uv run --project backend python -m vantara worklist > pendencias.csv
uv run --project backend python -m vantara case "NF 104560"
uv run --project backend python -m vantara reconcile
```

A API fornece fila em JSON e CSV, dossiês e investigação por pergunta. `GET /api/workflow` devolve os casos do conjunto atual; `PATCH /api/workflow?source=bank&line=17` aceita `{ "owner": "Tesouraria", "status": "inconclusive", "note": "Falta comprovante", "version": 0 }`, rejeita versões antigas com HTTP 409 e exige justificativa ao encerrar, marcar inconclusivo ou reabrir. `POST /api/workflow/draft` recebe `{ "source": "bank", "line": 17 }` e devolve somente um texto para revisão, sem enviar mensagens. A persistência é `~/.local/share/vantara/workflow.sqlite3` (ou `$XDG_DATA_HOME/vantara/workflow.sqlite3`); `VANTARA_WORKFLOW_DB` pode apontar para outro arquivo. O SHA-256 dos quatro exports separa as decisões de conjuntos de bytes diferentes; reenviar exatamente os mesmos bytes recupera as decisões anteriores. O CSV exportado continua sendo a fila dos exports, **não** o histórico do fluxo.

`POST /api/exports` aceita apenas os quatro exports sintéticos esperados em base64 e grava no Floci local depois de validar e conciliar; não escreve no Protheus. O PIX de R$ 3.400 da linha 17 do extrato e o título Omie da linha 4 são pistas não confirmadas para a NF 104560. O saldo calculado a partir de créditos identificados não é saldo bancário nem baixa atualizada no ERP.

```sh
uv run --project backend python -m unittest discover -s backend/tests
npm --prefix frontend run build
docker compose down
```

O ambiente local não implementa login nem recebe dados de produção: responsável e notas não são uma trilha de auditoria autenticada. Uso real exige autorização, autenticação, política de acesso ao SQLite e integração de leitura aprovadas pela TI da Vantara; manter todos os dados dentro da empresa exigiria inferência privada, pois esta IA usa a OpenAI externa. Hospedagem na Barte é exceção sujeita à liderança. Os anexos mencionados na caixa de entrada não foram fornecidos. O histórico de orientações parafraseadas está em `docs/prompts.md`.
