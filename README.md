# Serviço de Inferência Distribuído — C1.A2

Serviço de classificação de sentimento (positivo/negativo) em português, exposto por
**duas interfaces de comunicação independentes** (REST e gRPC) e com **processamento
assíncrono** via fila de mensagens.

**Sistemas Distribuídos e Computação em Nuvem · FAESA · 2026/2**

---

## Passo a passo rápido

Sequência mínima, do clone ao primeiro resultado. A explicação detalhada de cada
passo está em [Como executar do zero](#como-executar-do-zero).

```bash
# ── Terminal 1 ────────────────────────────────────────────────────────────────
# 1. Clonar e entrar na pasta
git clone https://github.com/soffiamartins/sd-2026-2-kit-c1a2.git
cd sd-2026-2-kit-c1a2

# 2. Ambiente virtual
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 3. Dependências
pip install -r requirements.txt

# 4. Subir o Redis (precisa do Docker rodando)
docker compose up -d
docker compose ps                  # aguarde o status "healthy"

# 5. Gerar os stubs gRPC (obrigatório em toda instalação nova)
touch proto/__init__.py            # Windows: New-Item proto/__init__.py -ItemType File
python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. proto/inferencia.proto

# 6. Subir a API REST (este terminal fica ocupado)
uvicorn app.api_rest:app --reload --port 8000
```

```bash
# ── Terminal 2 ────────────────────────────────────────────────────────────────
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 7. Subir o worker (este terminal fica ocupado)
python -m app.worker
```

```bash
# ── Terminal 3 ────────────────────────────────────────────────────────────────
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 8. Subir o servidor gRPC (este terminal fica ocupado)
python -m app.servidor_grpc
```

```bash
# ── Terminal 4 ────────────────────────────────────────────────────────────────
source .venv/bin/activate          # Windows: .venv\Scripts\Activate.ps1

# 9. Conferir se a API está no ar
curl http://localhost:8000/saude
# {"status":"ok","modelo_carregado":true}

# 10. Rodar o cliente de exemplo (síncrono + assíncrono)
python exemplos/cliente_rest.py "o atendimento foi otimo"
```

**Ordem importa:** o Redis (passo 4) precisa estar de pé antes da API e do worker,
senão os dois falham ao conectar. Os stubs (passo 5) precisam existir antes do
servidor gRPC, senão o import de `proto.inferencia_pb2` quebra. A API (6), o worker (7)
e o gRPC (8) são independentes entre si e podem subir em qualquer ordem.

**Na primeira execução** o modelo é treinado e salvo em `app/modelo.joblib` (~1 s);
nas seguintes ele só é carregado do disco.

**Para parar tudo:** `Ctrl+C` em cada terminal e `docker compose down` (use
`docker compose down -v` para apagar também as filas e resultados do Redis).

---

## Visão geral

O sistema recebe um texto, roda uma inferência de IA e devolve o rótulo
(`positivo` / `negativo`) com a confiança. A inferência em si é uma chamada de
biblioteca — o valor do trabalho está na engenharia distribuída em volta dela:

- **Duas portas de entrada** para o mesmo domínio: HTTP/JSON (REST) e RPC binário (gRPC).
- **Desacoplamento temporal**: o cliente REST pode submeter e ir embora; quem
  executa é um worker separado, que consome de uma fila.
- **Tolerância a falha**: retentativa com contador e fila de descarte (dead-letter).
- **Modelo carregado uma única vez** por processo, nunca por requisição.

Tudo roda offline. O modelo é treinado localmente na primeira execução e
persistido em `app/modelo.joblib`.

---

## Arquitetura

São **quatro processos independentes**, que se comunicam apenas por rede — nenhum
deles compartilha memória com outro.

```
                      ┌──────────────────────────┐
   HTTP/JSON          │  API REST (FastAPI)      │
   :8000  ───────────▶│  api_rest.py             │
                      │  • POST /predict-sync    │──┐ (inferência no próprio processo)
                      │  • POST /predict    202  │  │
                      │  • GET  /resultado/{id}  │  │
                      └───────┬──────────────────┘  │
                              │                     │
                 enfileirar   │   consultar         │
                              ▼                     │
                      ┌──────────────────────────┐  │
                      │  Redis (Docker)  :6379   │  │
                      │  • lista  tarefas        │  │
                      │  • chave  resultado:<id> │  │
                      │  • lista  dead_letter    │  │
                      └───────┬──────────────────┘  │
                              │ BLPOP (bloqueante)  │
                              ▼                     │
                      ┌──────────────────────────┐  │
                      │  Worker                  │  │
                      │  worker.py               │──┤
                      │  • retry (3x)            │  │
                      │  • dead-letter           │  │
                      └──────────────────────────┘  │
                                                    │
                      ┌──────────────────────────┐  │
   gRPC/HTTP2         │  Servidor gRPC           │  │
   :50051 ───────────▶│  servidor_grpc.py        │──┘
                      │  • Prever                │
                      │  • PreverLote            │   ┌─────────────────────┐
                      └──────────────────────────┘   │ modelo.py           │
                                                     │ carregar_modelo()   │
                    (cada processo carrega o modelo  │ TF-IDF + LogReg     │
                     uma vez, no startup) ──────────▶│ modelo.joblib       │
                                                     └─────────────────────┘
```

### Fluxo assíncrono (o caminho principal)

1. Cliente faz `POST /predict` com o texto.
2. A API gera um `uuid4`, empurra `{"id", "texto"}` na lista `tarefas` do Redis,
   grava `resultado:<id> = {"status": "na_fila"}` e responde **202 Accepted** com o id.
   A API **não** executa o modelo nesse caminho.
3. O worker está bloqueado em `BLPOP` na lista `tarefas`. Ao receber a tarefa, roda a
   inferência e sobrescreve `resultado:<id>` com `{"status": "pronto", ...}`.
4. O cliente consulta `GET /resultado/{id}` até o status virar `pronto` (polling).

### Fluxo síncrono

`POST /predict-sync` executa a inferência no próprio processo da API e devolve o
resultado na mesma resposta. Fica no projeto como **contraponto didático**: serve para
comparar latência percebida e para provar que REST e gRPC devolvem o mesmo rótulo
para o mesmo texto.

### Fluxo gRPC

O servidor gRPC é um processo à parte, com seu próprio `ThreadPoolExecutor`
(10 threads). Ele **não usa a fila** — é o caminho de baixa latência, pensado para
comunicação serviço-a-serviço, e oferece `PreverLote` para amortizar o custo de rede
em várias predições de uma vez.

---

## Decisões de projeto

| Decisão | Motivo |
|---|---|
| **Redis como fila** (`LPUSH`/`BLPOP`) em vez de RabbitMQ/Kafka | Já resolve fila + armazenamento de resultado em um único componente, sem broker adicional para configurar. `BLPOP` dá consumo bloqueante com entrega a um único consumidor — o suficiente para o cenário. |
| **Resultado no Redis, não em banco** | O resultado é efêmero e consultado por chave. Um SGBD relacional aqui só adicionaria latência e acoplamento. |
| **Worker em processo separado** (não `BackgroundTasks` do FastAPI) | `BackgroundTasks` roda dentro do mesmo processo da API: se a API cai, a tarefa some, e não é possível escalar o processamento sem escalar a API. Processos separados permitem `python -m app.worker` em N instâncias. |
| **Modelo carregado no startup** | `carregar_modelo()` é chamado no evento de startup da API e no `__init__` do servicer gRPC — uma vez por processo. Carregar por requisição multiplicaria a latência por ~100x. |
| **REST e gRPC como processos independentes** | Cada interface tem seu ciclo de vida e sua porta; uma pode cair sem derrubar a outra. Ambas consomem o mesmo módulo `modelo.py`, então o comportamento é idêntico. |
| **`202 Accepted` em vez de `200`** | Semanticamente correto: a requisição foi aceita, o processamento ainda não aconteceu. |
| **`REDIS_URL` por variável de ambiente** | Permite trocar `localhost` por um host de container/nuvem sem alterar código. |

---

## Contratos das interfaces

### REST — `http://localhost:8000`

Documentação interativa gerada automaticamente (OpenAPI): `http://localhost:8000/docs`

| Método | Rota | Status | Corpo / Resposta |
|---|---|---|---|
| `GET` | `/saude` | 200 | `{"status": "ok", "modelo_carregado": true}` |
| `POST` | `/predict-sync` | 200 / 400 | Entrada `{"texto": "..."}` → `{"texto", "sentimento", "confianca", "tempo_ms"}` |
| `POST` | `/predict` | **202** / 400 | Entrada `{"texto": "..."}` → `{"id": "<uuid>"}` |
| `GET` | `/resultado/{id}` | 200 / **404** | `{"status": "na_fila"}` \| `{"status": "pronto", ...}` \| `{"status": "falhou", "erro": "..."}` |

Texto vazio ou só com espaços é rejeitado com **400** antes de tocar na fila.
Id inexistente devolve **404**.

### gRPC — `localhost:50051`

Contrato em `proto/inferencia.proto`:

```protobuf
service Classificador {
    rpc Prever     (RequisicaoTexto) returns (RespostaClassificacao);
    rpc PreverLote (RequisicaoLote)  returns (RespostaLote);
}
```

- `Prever` — um texto, uma resposta (`sentimento`, `confianca`).
- `PreverLote` — `repeated string textos` → `repeated RespostaClassificacao resultados`,
  na mesma ordem da entrada.

---

## Resiliência e tratamento de falhas

**Retentativa com limite.** Cada tarefa carrega um campo `tentativas`. Se a inferência
lança exceção, o worker incrementa o contador e reenfileira a tarefa. O limite é
`MAX_TENTATIVAS = 3` (`app/worker.py`).

**Dead-letter.** Esgotadas as 3 tentativas, a tarefa vai para a lista `dead_letter` no
Redis, junto com a mensagem de erro e o número de tentativas, e `resultado:<id>` passa
a `{"status": "falhou"}` — o cliente para de fazer polling em vez de esperar para
sempre. A fila principal não fica travada por uma mensagem envenenada.

```bash
# inspecionar a dead-letter
docker compose exec redis redis-cli LRANGE dead_letter 0 -1
```

**Isolamento de falha.** O worker captura exceção por tarefa; um erro em uma mensagem
não derruba o laço de consumo. O `BLPOP` usa `timeout=5`, então o worker não fica
preso indefinidamente e responde a `Ctrl+C`.

**Degradação parcial.** Se o worker estiver fora do ar, `POST /predict` continua
aceitando e acumulando tarefas na fila (elas são processadas quando o worker voltar),
e `/predict-sync` e o gRPC seguem funcionando normalmente.

---

## Observabilidade

Todos os serviços usam `logging` no nível `INFO` e registram, por requisição,
o **id**, o **tamanho da entrada** e o **tempo de resposta**:

```
INFO:root:[REST POST /predict] id=3f2b… | tamanho_entrada=27
INFO:root:[worker] processando 3f2b… (tentativa 1/3)
INFO:root:[worker] OK id=3f2b… | tamanho_texto=27 | tempo_ms=1.84
INFO:root:[gRPC PreverLote] itens=3 | tempo_ms=4.12
ERROR:root:[worker] ERRO em 9a1c… | tentativa=2 | erro=… | tempo_ms=0.91
WARNING:root:[worker] DEAD-LETTER enviada para id=9a1c…
```

O campo `tempo_ms` também volta no corpo da resposta REST, o que permite medir a
latência da inferência sem precisar ler log.

---

## Como executar do zero

### Pré-requisitos

- Python **3.10+**
- Docker Desktop (ou Docker Engine + plugin Compose) — usado só para o Redis
- Git

### 1. Clonar e preparar o ambiente

```bash
git clone <URL-DO-SEU-REPOSITORIO>
cd sd-2026-2-kit-c1a2

python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Subir o Redis

```bash
docker compose up -d
docker compose ps        # deve aparecer "healthy"
```

Se o Redis estiver em outra máquina/porta, copie `.env.example` para `.env` e ajuste
`REDIS_URL`, ou exporte a variável no shell:

```bash
export REDIS_URL=redis://localhost:6379/0     # Linux/macOS
$env:REDIS_URL="redis://localhost:6379/0"     # PowerShell
```

### 3. Gerar os stubs gRPC

Os arquivos `*_pb2.py` são **gerados** e não versionados (estão no `.gitignore`), então
este passo é obrigatório em toda instalação nova:

```bash
# cria um pacote Python para os stubs (só na primeira vez)
touch proto/__init__.py          # Windows: New-Item proto/__init__.py -ItemType File

python -m grpc_tools.protoc -I . --python_out=. --grpc_python_out=. proto/inferencia.proto
```

> O `-I .` (raiz do projeto, não `-I proto`) é o que faz o stub gerado importar
> `from proto import inferencia_pb2`, casando com o import usado em
> `app/servidor_grpc.py`. Gerar com `-I proto` produz arquivos na raiz que **não**
> importam corretamente.

Confira que `proto/inferencia_pb2.py` e `proto/inferencia_pb2_grpc.py` existem.

### 4. Subir os serviços

Cada comando em um terminal próprio, com a venv ativada:

```bash
# Terminal 1 — API REST
uvicorn app.api_rest:app --reload --port 8000

# Terminal 2 — Worker (pode abrir vários para dividir a carga)
python -m app.worker

# Terminal 3 — Servidor gRPC
python -m app.servidor_grpc
```

Na primeira execução, o modelo é treinado e salvo em `app/modelo.joblib` (~1s). Nas
execuções seguintes ele é apenas carregado do disco.

### 5. Verificar

```bash
curl http://localhost:8000/saude
# {"status":"ok","modelo_carregado":true}
```

### Problemas comuns

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| `redis.exceptions.ConnectionError` na API ou no worker | Redis não está de pé, ou `REDIS_URL` aponta para o lugar errado | `docker compose up -d` e confira `docker compose ps` |
| `ModuleNotFoundError: No module named 'proto.inferencia_pb2'` | Stubs não gerados, ou gerados com `-I proto` (caem na raiz) | Refaça o passo 5 com `-I .` e garanta que `proto/__init__.py` existe |
| `ModuleNotFoundError: No module named 'app'` | Rodou o arquivo direto (`python app/worker.py`) | Rode como módulo, a partir da raiz: `python -m app.worker` |
| `POST /predict` responde, mas o resultado fica preso em `na_fila` | Nenhum worker rodando | Suba `python -m app.worker` em outro terminal |
| `Address already in use` (8000 ou 50051) | Outra instância ainda no ar | Encerre o processo anterior ou troque a porta (`--port 8001`) |
| Primeira requisição demora mais que as outras | Treino inicial do modelo | Normal; a partir da segunda execução ele vem do `modelo.joblib` |

---

## Testando cada caminho

### Cliente de exemplo (síncrono + assíncrono)

```bash
python exemplos/cliente_rest.py "o atendimento foi otimo"
```

### REST na unha

```bash
# síncrono
curl -X POST http://localhost:8000/predict-sync \
  -H "Content-Type: application/json" \
  -d '{"texto":"produto maravilhoso, recomendo"}'

# assíncrono
curl -X POST http://localhost:8000/predict \
  -H "Content-Type: application/json" \
  -d '{"texto":"produto maravilhoso, recomendo"}'
# → {"id":"3f2b..."}  (HTTP 202)

curl http://localhost:8000/resultado/3f2b...
# → {"status":"na_fila"}  e, um instante depois:
# → {"texto":"...","sentimento":"positivo","confianca":0.83,"status":"pronto","tempo_ms":1.84}

# id inexistente
curl -i http://localhost:8000/resultado/nao-existe     # → 404
```

### gRPC

Salve como `exemplos/cliente_grpc.py` e rode com o servidor no ar:

```python
import grpc
from proto import inferencia_pb2, inferencia_pb2_grpc

with grpc.insecure_channel("localhost:50051") as canal:
    stub = inferencia_pb2_grpc.ClassificadorStub(canal)

    print(stub.Prever(inferencia_pb2.RequisicaoTexto(texto="o atendimento foi otimo")))

    lote = inferencia_pb2.RequisicaoLote(textos=[
        "adorei o produto",
        "entrega atrasou muito",
        "funcionou como prometido",
    ])
    for r in stub.PreverLote(lote).resultados:
        print(r.sentimento, round(r.confianca, 4))
```

```bash
python -m exemplos.cliente_grpc
```

### Provar que as duas interfaces concordam

Mande o mesmo texto para `POST /predict-sync` e para `Prever`: `sentimento` e
`confianca` devem bater, porque ambos os processos usam o mesmo `modelo.joblib`.

### Provar a divisão de carga

Abra dois terminais com `python -m app.worker` e dispare várias chamadas a
`POST /predict`. Os logs mostram ids diferentes sendo processados em cada worker —
`BLPOP` entrega cada mensagem a um único consumidor.

---

## Estrutura do repositório

```
sd-2026-2-kit-c1a2/
├── app/
│   ├── modelo.py           # TF-IDF + Regressão Logística; carregar_modelo() -> .prever()
│   ├── fila.py             # abstração do Redis: enfileirar, consumir, resultado, dead-letter
│   ├── api_rest.py         # interface REST (FastAPI): síncrona + assíncrona
│   ├── worker.py           # consumidor da fila: inferência, retry, dead-letter
│   └── servidor_grpc.py    # interface gRPC: Prever e PreverLote
├── proto/
│   ├── inferencia.proto    # contrato gRPC (fonte da verdade)
│   └── __init__.py         # necessário para importar os stubs gerados
├── exemplos/
│   └── cliente_rest.py     # cliente de demonstração
├── scripts/
│   ├── gerar_stubs.sh
│   └── gerar_stubs.ps1
├── docker-compose.yml      # Redis 7 (alpine) com healthcheck
├── requirements.txt
└── .env.example            # REDIS_URL
```

Os stubs `proto/*_pb2*.py` e o artefato `app/modelo.joblib` são gerados em tempo de
execução e ficam fora do versionamento.

---

## Limitações conhecidas

- **Consulta por polling.** `GET /resultado/{id}` exige que o cliente pergunte
  repetidamente. Webhook ou WebSocket seria mais eficiente.
- **Resultados sem expiração.** As chaves `resultado:<id>` ficam no Redis
  indefinidamente; em produção usaria `SETEX` com TTL.
- **Sem reprocessamento automático da dead-letter.** A inspeção e o reenvio são
  manuais, via `redis-cli`.
- **Perda em caso de crash do worker.** `BLPOP` remove a mensagem antes do
  processamento; se o worker morrer no meio, a tarefa se perde. `BLMOVE` para uma
  fila de "em processamento" resolveria.
- **Sem autenticação nem limite de taxa.** Ambas as interfaces estão abertas.
- **Só a fila está conteinerizada.** API, worker e gRPC rodam no host; empacotá-los
  em imagens próprias no mesmo `docker-compose.yml` é o próximo passo natural.
