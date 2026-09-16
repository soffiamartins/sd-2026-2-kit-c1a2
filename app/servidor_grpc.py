import time
import logging
import inferencia_pb2
import inferencia_pb2_grpc
from app.modelo import carregar_modelo

logging.basicConfig(level=logging.INFO)

class ClassificadorServicer(inferencia_pb2_grpc.ClassificadorServicer):
    def init(self):
        self.modelo = carregar_modelo()

    def PreverLote(self, request, context):
        inicio = time.time()
        respostas = []

        for texto in request.textos:
            predicao = self.modelo.prever(texto)
            respostas.append(
                inferencia_pb2.RespostaClassificacao(
                    sentimento=predicao.get("sentimento", ""),
                    confianca=predicao.get("confianca", 0.0)
                )
            )

        tempo_ms = round((time.time() - inicio) * 1000, 2)
        # TAREFA 6: Log de requisições gRPC
        logging.info(
            f"[gRPC PreverLote] itens={len(request.textos)} | tempo_ms={tempo_ms}"
        )

        return inferencia_pb2.RespostaLote(resultados=respostas)
