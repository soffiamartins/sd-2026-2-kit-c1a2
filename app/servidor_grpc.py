from concurrent import futures
import logging
import time
import grpc

from app.modelo import carregar_modelo
from proto import inferencia_pb2, inferencia_pb2_grpc

logging.basicConfig(level=logging.INFO)


class ClassificadorServicer(inferencia_pb2_grpc.ClassificadorServicer):

    def __init__(self):
        # Corrigido de init para __init__
        self.modelo = carregar_modelo()

    def Prever(self, request, context):
        inicio = time.time()
        predicao = self.modelo.prever(request.texto)

        tempo_ms = round((time.time() - inicio) * 1000, 2)
        logging.info(f"[gRPC Prever] tempo_ms={tempo_ms}")

        return inferencia_pb2.RespostaClassificacao(
            sentimento=predicao.get("sentimento", predicao.get("classe", "")),
            confianca=float(
                predicao.get("confianca", predicao.get("probabilidade", 0.0))
            ),
        )

    def PreverLote(self, request, context):
        inicio = time.time()
        respostas = []

        for texto in request.textos:
            predicao = self.modelo.prever(texto)
            respostas.append(
                inferencia_pb2.RespostaClassificacao(
                    sentimento=predicao.get(
                        "sentimento", predicao.get("classe", "")
                    ),
                    confianca=float(
                        predicao.get(
                            "confianca", predicao.get("probabilidade", 0.0)
                        )
                    ),
                )
            )

        tempo_ms = round((time.time() - inicio) * 1000, 2)
        # TAREFA 6: Log de requisições gRPC
        logging.info(
            f"[gRPC PreverLote] itens={len(request.textos)} | tempo_ms={tempo_ms}"
        )

        return inferencia_pb2.RespostaLote(resultados=respostas)


def servir():
    servidor = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    inferencia_pb2_grpc.add_ClassificadorServicer_to_server(
        ClassificadorServicer(), servidor
    )
    servidor.add_insecure_port("[::]:50051")
    print("[gRPC] Servidor escutando na porta 50051...")
    servidor.start()
    servidor.wait_for_termination()


if __name__ == "__main__":
    servir()