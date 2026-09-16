import time
import logging
from app import fila
from app.modelo import carregar_modelo

logging.basicConfig(level=logging.INFO)
MAX_TENTATIVAS = 3

def main():
    logging.info("[worker] carregando modelo...")
    modelo = carregar_modelo()
    logging.info("[worker] pronto. aguardando tarefas (Ctrl+C para sair)")

    while True:
        tarefa = fila.proxima_tarefa(timeout=5)
        if tarefa is None:
            continue

        tarefa_id = tarefa["id"]
        texto = tarefa["texto"]
        tentativas = tarefa.get("tentativas", 0) + 1
        
        logging.info(f"[worker] processando {tarefa_id} (tentativa {tentativas}/{MAX_TENTATIVAS})")
        inicio = time.time()

        try:
            resultado = modelo.prever(texto)
            resultado["status"] = "pronto"
            tempo_ms = round((time.time() - inicio) * 1000, 2)
            resultado["tempo_ms"] = tempo_ms

            # Salva o resultado no Redis
            fila.guardar_resultado(tarefa_id, resultado)
            
            # TAREFA 6: Log de requisições no worker
            logging.info(
                f"[worker] OK id={tarefa_id} | tamanho_texto={len(texto)} | tempo_ms={tempo_ms}"
            )

        except Exception as erro:  # noqa: BLE001
            tempo_ms = round((time.time() - inicio) * 1000, 2)
            logging.error(
                f"[worker] ERRO em {tarefa_id} | tentativa={tentativas} | erro={erro} | tempo_ms={tempo_ms}"
            )

            if tentativas < MAX_TENTATIVAS:
                # Reenfileira a tarefa incrementando o contador de tentativas
                tarefa["tentativas"] = tentativas
                fila.enfileirar_tarefa(tarefa)
            else:
                # Excedeu o limite de 3 tentativas -> Envia para Dead-Letter
                logging.warning(f"[worker] DEAD-LETTER enviada para id={tarefa_id}")
                fila.guardar_dead_letter(tarefa_id, {
                    "tarefa": tarefa,
                    "erro": str(erro),
                    "tentativas": tentativas
                })

if __name__ == "__main__":
    main()