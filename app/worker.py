import time
import redis
from app import fila
from app.modelo import carregar_modelo


def main():
    print("[worker] carregando modelo...")
    modelo = carregar_modelo()
    print("[worker] pronto. aguardando tarefas (Ctrl+C para sair)")

    while True:
        try:
            # Tenta buscar a próxima tarefa na fila
            tarefa = fila.proxima_tarefa(timeout=5)
            if tarefa is None:
                continue

            print(f"[worker] processando {tarefa['id']}")
            inicio = time.time()

            # Executa a inferência
            resultado = modelo.prever(tarefa["texto"])
            resultado["status"] = "pronto"
            resultado["tempo_ms"] = round((time.time() - inicio) * 1000, 2)

            # TAREFA 3: Salva o resultado para consulta posterior
            fila.guardar_resultado(tarefa["id"], resultado)
            print(f"[worker] concluído {tarefa['id']} em {resultado['tempo_ms']}ms")

        except (redis.exceptions.TimeoutError, TimeoutError):
            # Quando estoura o timeout de 5s sem mensagem, ignora e volta a escutar
            continue

        except Exception as erro:  # noqa: BLE001
            # TAREFA 5: Tratamento de erros gerais durante o processamento
            print(f"[worker] ERRO no processamento: {erro}")


if __name__ == "__main__":
    main()