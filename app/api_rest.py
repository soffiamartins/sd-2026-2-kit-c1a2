import logging
import time

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import fila
from app.modelo import carregar_modelo

# Configuração de logs para a Tarefa 6
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Serviço de Inferência - C1.A2", version="0.1.0")

modelo = None


class Entrada(BaseModel):
    texto: str


@app.on_event("startup")
def _subir():
    """Carrega o modelo UMA vez. Este é o ponto-chave da Aula 6."""
    global modelo
    inicio = time.time()
    modelo = carregar_modelo()
    print(f"[startup] modelo carregado em {time.time() - inicio:.3f}s")


@app.get("/saude")
def saude():
    return {"status": "ok", "modelo_carregado": modelo is not None}


@app.post("/predict-sync")
def predict_sync(entrada: Entrada):
    """Inferência SÍNCRONA: o cliente espera a resposta. Lab da Aula 6."""
    if not entrada.texto.strip():
        raise HTTPException(status_code=400, detail="texto vazio")
    inicio = time.time()
    resultado = modelo.prever(entrada.texto)
    tempo_ms = round((time.time() - inicio) * 1000, 2)
    resultado["tempo_ms"] = tempo_ms

    # TAREFA 6: Log da requisição síncrona
    logging.info(f"[REST /predict-sync] tamanho_entrada={len(entrada.texto)} | tempo_ms={tempo_ms}")

    return resultado


# ------------------------------------------------------------------
# TAREFA 1 - submissão assíncrona
# ------------------------------------------------------------------
@app.post("/predict", status_code=202)
def predict(entrada: Entrada):
    """Enfileira a tarefa e devolve {"id": ...} SEM esperar."""
    if not entrada.texto.strip():
        raise HTTPException(status_code=400, detail="O texto não pode estar vazio")

    tarefa_id = fila.enfileirar(entrada.texto)

    # TAREFA 6: Log de requisição assíncrona recebida
    logging.info(f"[REST POST /predict] id={tarefa_id} | tamanho_entrada={len(entrada.texto)}")

    return {"id": tarefa_id}


# ------------------------------------------------------------------
# TAREFA 2 - consulta do resultado
# ------------------------------------------------------------------
@app.get("/resultado/{tarefa_id}")
def resultado(tarefa_id: str):
    """Devolve o resultado; 404 se o id não existir."""
    res = fila.buscar_resultado(tarefa_id)

    if res is None:
        logging.warning(f"[REST GET /resultado] id={tarefa_id} NÃO ENCONTRADO")
        raise HTTPException(status_code=404, detail="tarefa não encontrada")

    # TAREFA 6: Log da consulta de resultado
    logging.info(f"[REST GET /resultado] id={tarefa_id} | status={res.get('status')}")

    return res