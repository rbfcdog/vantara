# Vantara

A demo lê os quatro arquivos sintéticos de `data_pack/` sem alterar os bytes. O Floci simula o armazenamento na infraestrutura da Vantara. Durante os primeiros 90 dias o Protheus continua restrito a leitura e exportação. Nenhuma tela ou API faz baixa, aprovação ou pagamento.

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

O arquivo `.env` na raiz deve conter `OPENAI_API_KEY=sua-chave` para a investigação com IA. A fila e os dossiês funcionam sem chave. O comando remove qualquer chave diferente que tenha sido exportada no terminal antes de carregar o arquivo.

Abra outro terminal para a interface

```sh
npm --prefix frontend run dev
```

Acesse `http://127.0.0.1:3000`. A conversa com o assistente é a entrada principal: digite uma pergunta ou escolha uma sugestão para consultar os exports de Protheus, Omie, extrato bancário e contas a pagar. As mensagens ficam neste navegador (até 30 trocas salvas); as últimas seis trocas concluídas acompanham a próxima pergunta como contexto, mas a IA consulta novamente as fontes em cada resposta. **Limpar conversa** remove o histórico local, e o envio de novos exports também inicia uma conversa limpa. Uma falha aparece na troca afetada, com opção para repetir a pergunta. Abaixo ficam a fila priorizada e o dossiê; **Perguntar sobre este registro** preenche a mensagem com a linha selecionada. A interface usa a API Python por meio do servidor Next.js; a chave da OpenAI e o acesso ao Floci ficam no backend. Só há dados da amostra de julho/2026, não acesso a todos os sistemas da empresa nem comandos de baixa. Enquanto a fila carrega, não há contadores nem consultas; após falhas, há quatro tentativas no total (imediata, depois 1 s, 3 s e 9 s) antes do diagnóstico e da opção de tentar novamente. Um roteiro de demonstração de quatro minutos está em [`docs/roteiro-video-4min.md`](docs/roteiro-video-4min.md).

Se a API confirmar uma fila vazia, a tela mostra o horário do último processamento e permite selecionar os quatro arquivos originais (`01` a `04` em `data_pack/`) com **Enviar arquivos**. O backend valida todos os formatos e concilia antes de gravar os quatro objetos no bucket da Vantara no Floci local; o envio não escreve no Protheus. Arquivos inválidos não substituem os anteriores. Se a API estiver fora do ar, a tela mostra erro em vez de fingir que a fila está vazia.

## CLI e API

```sh
uv run --project backend python -m vantara worklist > pendencias.csv
uv run --project backend python -m vantara case "NF 104560"
uv run --project backend python -m vantara reconcile
```

A API fornece a fila em JSON e CSV, o dossiê de cada registro e a investigação por pergunta. `POST /api/exports` aceita apenas os quatro exports sintéticos esperados em JSON com bytes codificados em base64 e grava no Floci local após validação; os demais endpoints não fazem baixa nem pagamentos. O PIX de R$ 3.400 da linha 17 do extrato e o título Omie da linha 4 aparecem no dossiê da NF 104560 como pistas não confirmadas. O saldo calculado a partir de créditos identificados não é saldo bancário ou baixa atualizada no ERP.

```sh
uv run --project backend python -m unittest discover -s backend/tests
npm --prefix frontend run build
docker compose down
```

O ambiente local não implementa login nem recebe dados de produção. A implantação real exige autorização e integração de leitura aprovadas pela TI da Vantara. Hospedagem na Barte é exceção sujeita à liderança. Os anexos mencionados na caixa de entrada não foram fornecidos. O histórico de orientações parafraseadas está em `docs/prompts.md`.
