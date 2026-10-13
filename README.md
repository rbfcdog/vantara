# Vantara

A solução de **recebíveis** parte do crédito no banco, procura um título **já criado pelo faturamento** no Protheus ou Omie e, após confirmar valor, parcela e estado atual, registra e confirma a baixa no ERP. Sem título identificado, investiga origem e cadastro no faturamento/ERP; **o extrato não cria NF**. Este repositório demonstra a etapa de leitura sobre quatro exports sintéticos sem alterar `data_pack/`: `seed` e `POST /api/exports` conciliam movimentos por regras e publicam o fechamento em SQLite **sem pergunta humana**, mas não acessam ERP atual nem executam baixa. Em contas a pagar, agrupam débitos por NF e mensagens da caixa `04`; com `OPENAI_API_KEY`, a IA acrescenta observações citadas. Sem chave ou se falhar, restam fontes e consultas por regras. O Floci simula armazenamento local; nenhuma tela/API escreve em ERP, aprova ou paga.

## Rodar localmente

Execute na raiz do projeto

```sh
docker compose up -d --wait
uv sync --project backend
npm --prefix frontend install
uv run --project backend --env-file .env python -m vantara seed
```

Abra outro terminal para a API

```sh
env -u OPENAI_API_KEY uv run --project backend --env-file .env uvicorn vantara.api:app --host 127.0.0.1 --port 8000
```

O arquivo `.env` na raiz pode conter `OPENAI_API_KEY=sua-chave` para **análise automática de contas a pagar** durante `seed`/upload/primeira publicação. Sem chave, conciliação e consultas por regras continuam disponíveis, mas o painel informa que a IA não concluiu a leitura; configure a chave e reenvie os arquivos ou rode `seed` para uma nova publicação. Perguntas e rascunhos continuam disponíveis somente pela API, fora da tela MVP. A IA usa uma API externa: trechos numerados do extrato `03` e da caixa `04` saem do ambiente local nesta demo **com dados sintéticos**.

Abra outro terminal para a interface

```sh
npm --prefix frontend run dev
```

Acesse `http://127.0.0.1:3000`. A tela é **somente leitura sobre os dados e usa linguagem de produto**: cobertura (contas recebidas, créditos sem vínculo, baixas confirmadas no ERP) e as **decisões pendentes** — duplicidade provável entre Protheus/Omie e conferência de contas a pagar, cada uma com evidências `arquivo:linha` citáveis e ações **Resolver / Inconclusivo / Reabrir** gravadas no workflow local (responsável + nota obrigatórias, versionamento com detecção de conflito). Upload/seed, rastreio, rascunhos e CSV ficam na CLI/API; a interface não menciona mock/simulação e não executa escritas no ERP.

Créditos sem vínculo passam por um **executor autônomo** em duas etapas na publicação: a IA decide a atribuição mais provável entre os candidatos que as regras listaram (uma chamada read-only com citação `arquivo:linha` validada contra as linhas do próprio crédito) e o executor aplica a baixa no **livro local** — sem confirmação humana — com guardras determinísticas: candidato fora da lista, título com possível duplicidade entre ERPs ou crédito excedente ficam **retidos** com motivo. Cada baixa gera recibo idempotente `EXE-{dataset}-C{linha}` e o ledger é relido para confirmar o que ficou gravado; `erp_writeoffs_performed` continua **0** porque escrita no ERP real exige conector homologado. Sem chave de IA ou se a citação não conferir, nada é aplicado e os créditos permanecem com as pistas das regras.

## O que a demonstração comprova

A seção **Baixas simuladas** usa exclusivamente o pack original e dois títulos com estado/política **fictícios declarados**: NF 104540 (crédito integral `03:4`) e NF 104560 (crédito parcial `03:8`). Um comando processa ambos em lote num livro local, guarda recibos `SIM-*`, relê o saldo simulado e impede repetir a baixa do mesmo evento. Esses recibos não são do Protheus; `erp_writeoffs_performed` do fechamento real continua **zero**. Os demais créditos ficam pendentes sem inventar estado de ERP, identidade do Pix ou nota fiscal.

| Dor | O que a amostra exercita | O que falta para operar sem analista |
|---|---|---|
| Juntar banco, ERPs e mensagens | Processamento dos quatro exports, cobertura e trilhas citadas | Conectores atuais e extratos das outras três contas |
| Ligar crédito ao título | Correspondência exata entre C e referência única; pistas sem atribuição separadas | ID/retorno dos C sem referência e pesquisa em faturamento/ERP |
| Dar baixa com segurança | Escrita idempotente e releitura **apenas no livro local simulado** | Leitura e escrita homologadas nos ERPs responsáveis, autenticação e política |
| Investigar título ausente | Crédito continua não atribuído com próxima fonte indicada | História atual de Protheus/Omie e documento de origem |
| Evitar repetição | Mesma transação simulada não baixa duas vezes; possível título em dois ERPs é retido | Confirmação da identidade das obrigações e do favorecido nos débitos D |
| Interpretar mensagens | Caixa `04` de contas a pagar cotejada com débitos; IA opcional cita alegações | Anexos, beneficiário, aprovação e ingestão autorizada da caixa `05` de recebíveis |
| Fechar e medir tempo | Totais, cobertura e zero baixas reais expostos no fechamento | Recibos reais, cobertura completa e medição antes/depois do trabalho |

Não há como garantir eliminação de **100%** do trabalho analítico com fontes contraditórias ou incompletas: o fluxo normal pode ser automatizado quando há prova, e as exceções exigem novas fontes ou decisão de política, não uma baixa por palpite.

O envio dos quatro exports acontece pela API (`POST /api/exports`) ou por `seed`, com ou sem relatório prévio (inclusive quando faltam arquivos no Floci). Use os nomes originais `01` a `04` de `data_pack/` ou as cópias expandidas em `feedback/amostra-expandida/`. O backend valida os arquivos antes de atualizar o Floci e só publica fechamento e rastreamento **após** todas as gravações terem êxito. Se uma gravação falhar no meio, a publicação anterior permanece íntegra, mas os objetos brutos podem precisar de reenvio completo. Não há polling do bucket nem conectores de ERP/banco: novos exports precisam chegar por upload ou `seed`. Uma publicação anterior sem rastreamento exige reprocessamento, indicado no painel, e não é chamada de investigada.

## CLI e API

```sh
uv run --project backend python -m vantara worklist > pendencias.csv
uv run --project backend python -m vantara case "NF 104560"
uv run --project backend python -m vantara reconcile
```

A API fornece o fechamento publicado, a fila de exceções em JSON/CSV, dossiês e investigação opcional. `GET /api/workflow` devolve os casos do conjunto publicado; `PATCH /api/workflow?source=bank&line=17` aceita `{ "owner": "Tesouraria", "status": "inconclusive", "note": "Falta comprovante", "version": 0 }`, rejeita versões antigas com HTTP 409 e exige justificativa. `POST /api/workflow/draft` recebe `{ "source": "bank", "line": 17 }` e devolve apenas texto para revisão, sem enviar mensagens. Fechamento e decisões humanas ficam em `~/.local/share/vantara/workflow.sqlite3` (ou `$XDG_DATA_HOME/vantara/workflow.sqlite3`); `VANTARA_WORKFLOW_DB` pode apontar para outro arquivo. O SHA-256 dos quatro exports separa as decisões de conjuntos distintos; reenvio dos mesmos bytes recupera decisões anteriores.

`POST /api/exports` recebe os quatro arquivos em base64, concilia, investiga as NF de débitos da caixa `04` e devolve `processed_at`, `dataset`, `summary` e `items_count` após publicar; arquivos inválidos não substituem a publicação anterior. `GET /api/worklist` devolve esses metadados, `investigations`, `payables_review` e itens para investigação (inclusive débitos); o CSV contém a fila, não o histórico humano. `investigations` mantém `method=regras_sobre_exports`, os quatro `consulted`, `gaps` e `cases` com `source`, `line`, `status`, `finding`, `evidence` e `next_query`. `payables_review` contém `method`, `status` (`completed`, `not_configured`, `failed` ou `no_cases`) e casos por NF com linhas D, linhas de caixa, observações de IA **apenas em `completed`**, evidências faltantes e próxima consulta. Snapshots anteriores podem devolver `null` para essas investigações; reprocessar para atualizar. O PIX sem documento da linha 17 continua pista para dois títulos possíveis, não baixa.

`GET /api/reconciliation` devolve o **último fechamento publicado**, com horário estável, resumo, títulos, movimentos e rastreamentos com arquivo/linha; se não há publicação mas os quatro exports já estão no Floci, publica-os na primeira consulta. Identificação usa apenas `documento` bancário igual a referência única `nosso_numero` ou `invoice_ref`. A cobertura é de uma das quatro contas; crédito integral no extrato não confirma baixa no ERP. Débitos com mesma NF são alerta de possível repetição, não prova de duplicidade. O pack expandido em `feedback/amostra-expandida/` testa o mesmo fluxo sem substituir o pack original do repositório.

A [caixa sintética de recebíveis](feedback/amostra-expandida/05_caixa_de_entrada_contas_a_receber.txt) exemplifica seis mensagens ligadas a créditos, divergências e contestações; veja o [cruzamento com o extrato](feedback/amostra-expandida/README.md#caixa-de-entrada-de-recebíveis-exemplo-adicional-não-ingerido). **É material ilustrativo separado** da caixa `04` de contas a pagar e não é um quinto arquivo aceito pela API.

O rastreamento de **recebíveis** continua determinístico sobre os quatro exports recebidos; não procura dados fora deles. A **leitura automática de contas a pagar por IA** é separada, condicional à chave e limitada aos trechos `03`/`04` da NF citada: não lê os anexos ausentes nem comprova favorecido ou quitação. A investigação interativa com IA continua opcional ao receber uma pergunta; as instruções e os prompts ainda futuros estão em [`feedback/prompts-dominio-vantara.md`](feedback/prompts-dominio-vantara.md). Rastreamentos do agente executados com citações estão em [`feedback/rastreio-registros-2026-10-10.md`](feedback/rastreio-registros-2026-10-10.md). Para consultar ERP atual, outras contas, comprovantes ou anexos por conta própria faltam conectores e permissões aprovados pela TI. Não há escrita no ERP nem pagamento de fornecedor.

O [guia de prompts](feedback/prompts-dominio-vantara.md) reúne nove papéis: investigação interativa dos exports, investigação automática de exceção futura, pedido neutro de referência, relatório CFO, gatilho de recebimento, leitura da caixa `05` de recebíveis, diagnóstico de crédito sem título identificado, pré-checagem de cobrança e **revisão operacional da caixa `04` de contas a pagar**. O guia explicita a separação entre título criado pelo faturamento, crédito bancário, vínculo determinístico e baixa confirmada por executor autorizado. Só a revisão `04` usa IA automaticamente na publicação quando há chave; nenhum prompt executa escrita ou envio.

**Desenho operacional:** [`feedback/plano-recebiveis-slide8.md`](feedback/plano-recebiveis-slide8.md) detalha conectores bancários/ERP, consulta atual, idempotência, confirmação de baixa e prevenção de cobrança indevida. As janelas de 30/90 dias naquele plano descrevem restrições históricas do case; para esta narrativa, **considere-as transcorridas**. Mesmo assim, os quatro exports deste repositório não fornecem credenciais, interface de escrita homologada, estado atual dos títulos nem comprovantes para executar baixa real. O [`roteiro de vídeo`](docs/roteiro-video-4min.md) explica produto final versus publicação de arquivos sem insinuar baixa feita.

Para apresentar o trabalho ao CFO/CTO: [script falado e respostas a perguntas](feedback/narrativa-apresentacao-recebiveis.md), separando a operação final de baixa automática da amostra de exports sem escrita.

Roteiro falado detalhado da entrevista (executor autônomo, com recibos e perguntas duras): [feedback/roteiro-entrevista-autonoma.md](feedback/roteiro-entrevista-autonoma.md).

```sh
uv run --project backend python -m unittest discover -s backend/tests
npm --prefix frontend run build
docker compose down
```

O ambiente local não implementa login nem recebe dados de produção: responsável e notas não são uma trilha de auditoria autenticada. Uso real exige autorização, autenticação, política de acesso ao SQLite e integração de leitura aprovadas pela TI da Vantara; manter todos os dados dentro da empresa exigiria inferência privada, pois esta IA usa a OpenAI externa. Hospedagem na Barte é exceção sujeita à liderança. Os anexos mencionados na caixa de entrada não foram fornecidos. O histórico de orientações parafraseadas está em `docs/prompts.md`.
