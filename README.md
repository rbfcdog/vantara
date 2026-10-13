# Vantara

A solução de **recebíveis** parte do crédito no banco e procura um título **já criado pelo faturamento** no Protheus ou Omie; **o extrato não cria NF**. Neste repositório, quatro exports sintéticos são conciliados sem alterar `data_pack/`: `seed` e `POST /api/exports` publicam o fechamento sem pergunta humana, a IA revisa cada crédito e o executor registra baixas idempotentes num **livro local**. Não há consulta ao estado atual nem escrita nos ERPs reais. Em contas a pagar, a IA acrescenta observações citadas sobre débitos e mensagens da caixa `04`; sem chave ou se falhar, restam fontes e consultas por regras. O Floci simula armazenamento local; nenhuma tela/API escreve em ERP, aprova ou paga.

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
uv run --project backend --env-file .env uvicorn vantara.api:app --host 127.0.0.1 --port 8000
```

O arquivo `.env` na raiz pode conter `OPENAI_API_KEY=sua-chave` para a **revisão automática de todos os créditos e de contas a pagar** durante `seed`/upload/primeira publicação. Sem chave, conciliação e consultas por regras continuam disponíveis, mas nenhum crédito novo recebe baixa do executor; configure a chave e reenvie os arquivos ou rode `seed` para uma nova publicação. A IA usa uma API externa: trechos numerados dos exports e da caixa `04` saem do ambiente local nesta demo **com dados sintéticos**.

Abra outro terminal para a interface

```sh
npm --prefix frontend run dev
```

Acesse `http://127.0.0.1:3000`. A tela mostra as **decisões pendentes** e os 67 créditos do extrato em páginas de dez, com parecer individual da IA, vínculo documental quando houver, motivo de retenção ou recibo da baixa. Resolver / Inconclusivo / Reabrir gravam decisão humana no workflow local (responsável + nota obrigatórias; conflito de versão é rejeitado). Upload/seed, rastreio, rascunhos e CSV ficam na CLI/API. A tela exibe as baixas do **executor local** na cobertura; não são escritas no Protheus nem no Omie reais.

Todos os créditos `C`, inclusive os 55 vinculados a referências exatas, passam pela **IA em lotes de até dez** na publicação. O modelo retorna veredito e evidências para cada um; em vínculos exatos só pode confirmar o título já identificado pelas regras, não trocá-lo. Nos demais, escolhe entre os candidatos fornecidos ou deixa sem atribuição. Somente após validar os 67 pareceres, o executor aplica automaticamente baixas no **livro local**; duplicidade entre ERPs, valor excedente ou saldo já consumido ficam retidos. Cada baixa gera recibo idempotente `EXE-{dataset}-C{linha}`, confirmado por releitura. `erp_writeoffs_performed` continua **0** porque escrita no ERP real exige conector homologado. Sem chave ou diante de resposta inválida, não há **novas** baixas e permanecem os recibos já gravados.

## O que a demonstração comprova

Na publicação local expandida foram observados **67/67 pareceres da IA**, **63 baixas no livro do executor**, **3 créditos retidos** e **1 sem atribuição**. Dois retidos por saldo já consumido refletem recibos históricos para créditos sem referência nos mesmos títulos; os recibos não foram apagados. Uma execução nova impede que créditos sem referência consumam títulos com referência exata. A seção de CLI **Baixas simuladas** é uma demonstração separada sobre dois títulos do pack original, com recibos `SIM-*`; nenhum desses recibos é do Protheus/Omie. `erp_writeoffs_performed=0` no fechamento.

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

`POST /api/exports` recebe os quatro arquivos em base64, concilia, revisa os créditos por IA, analisa débitos da caixa `04` e publica o resultado depois da validação; arquivos inválidos não substituem a publicação anterior. `GET /api/worklist` devolve metadados, fila, `investigations`, `payables_review`, `receivables_suggestions`, `autonomous_execution` e `credits_review` com parecer e recibo ou motivo para cada crédito. O CSV contém a fila, não o histórico humano. `payables_review` inclui observações da IA apenas com `status=completed`. Snapshots anteriores podem não ter essas projeções; reprocessar para atualizar. O PIX sem documento da linha 17 recebeu atribuição e recibo no **livro local**, não baixa no ERP.

`GET /api/reconciliation` devolve o **último fechamento publicado**, com horário estável, resumo, títulos, movimentos e rastreamentos com arquivo/linha. O vínculo exato vem de `documento` bancário igual a referência única `nosso_numero` ou `invoice_ref`; não equivale a baixa no ERP real. A cobertura é de uma das quatro contas, e débitos com mesma NF são alerta, não prova de duplicidade. Os exports sintéticos em `data_pack/` incluem agora 67 créditos.

A [caixa sintética de recebíveis](feedback/amostra-expandida/05_caixa_de_entrada_contas_a_receber.txt) exemplifica seis mensagens ligadas a créditos, divergências e contestações; veja o [cruzamento com o extrato](feedback/amostra-expandida/README.md#caixa-de-entrada-de-recebíveis-exemplo-adicional-não-ingerido). **É material ilustrativo separado** da caixa `04` de contas a pagar e não é um quinto arquivo aceito pela API.

O vínculo documental de **recebíveis** continua determinístico; a IA revisa os 67 créditos desses exports e o executor grava no ledger local decisões validadas, sem procurar dados fora deles. A análise separada de contas a pagar por IA é condicional à chave e limitada aos trechos `03`/`04`: não lê anexos ausentes nem comprova favorecido ou quitação. A investigação interativa continua opcional por pergunta. Para consultar ERP atual, outras contas, comprovantes ou Open Finance por conta própria faltam conectores autorizados e consentimento.

O [guia de prompts](feedback/prompts-dominio-vantara.md) documenta investigação interativa e leitura de contas a pagar, além do fluxo de crédito sem título. Na aplicação atual, **a revisão dos 67 créditos e a leitura da caixa `04` usam IA automaticamente** quando há chave; nenhum prompt executa escrita no ERP ou envio de mensagem.

**Desenho operacional:** [`feedback/plano-recebiveis-slide8.md`](feedback/plano-recebiveis-slide8.md) detalha conectores bancários/ERP, consulta atual, idempotência, confirmação de baixa e prevenção de cobrança indevida. As janelas de 30/90 dias naquele plano descrevem restrições históricas do case; para esta narrativa, **considere-as transcorridas**. Mesmo assim, os quatro exports deste repositório não fornecem credenciais, interface de escrita homologada, estado atual dos títulos nem comprovantes para executar baixa real. O [`roteiro de vídeo`](docs/roteiro-video-4min.md) explica produto final versus publicação de arquivos sem insinuar baixa feita.

Para apresentar o trabalho ao CFO/CTO: [script falado e respostas a perguntas](feedback/narrativa-apresentacao-recebiveis.md), separando a operação final de baixa automática da amostra de exports sem escrita.

Roteiro falado detalhado da entrevista (executor autônomo, com recibos e perguntas duras): [feedback/roteiro-entrevista-autonoma.md](feedback/roteiro-entrevista-autonoma.md).
Descrição textual do projeto (problema, pipeline, decisões de design e limites): [feedback/descricao-projeto-vantara.md](feedback/descricao-projeto-vantara.md).

```sh
uv run --project backend python -m unittest discover -s backend/tests
npm --prefix frontend run build
docker compose down
```

O ambiente local não implementa login nem recebe dados de produção: responsável e notas não são uma trilha de auditoria autenticada. Uso real exige autorização, autenticação, política de acesso ao SQLite e integração de leitura aprovadas pela TI da Vantara; manter todos os dados dentro da empresa exigiria inferência privada, pois esta IA usa a OpenAI externa. Hospedagem na Barte é exceção sujeita à liderança. Os anexos mencionados na caixa de entrada não foram fornecidos. O histórico de orientações parafraseadas está em `docs/prompts.md`.
