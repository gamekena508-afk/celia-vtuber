import sys, pathlib
import time
# Deriva a raiz do proprio arquivo: o teste continua valendo se a arvore mudar
# de lugar. Cravar caminho absoluto foi exatamente o que quebrou estes testes
# quando Célia virou a raiz (2026-08-11).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from celia.core.emotion_bus import BarramentoEmocao, NEUTRA

falhas = []
def check(nome, cond, extra=""):
    print(("  OK  " if cond else "FALHOU") + f"  {nome} {extra}")
    if not cond: falhas.append(nome)

b = BarramentoEmocao()

# 1. prioridade maior atropela
b.publicar("happy", origem="conversa", prioridade=70, ttl_s=5)
r = b.publicar("angry", origem="interrupt", prioridade=90, ttl_s=5)
check("prioridade maior atropela", r["ok"] and b.valendo().emocao == "angry")

# 2. prioridade menor NAO atropela
r = b.publicar("sad", origem="minecraft", prioridade=50, ttl_s=5)
check("prioridade menor recusada", (not r["ok"]) and r["motivo"]=="prioridade_menor" and b.valendo().emocao=="angry")

# 3. trava barra ate prioridade IGUAL
b2 = BarramentoEmocao()
b2.publicar("in_love", origem="flor", prioridade=85, ttl_s=5, trava_s=3)
r = b2.publicar("happy", origem="outro", prioridade=85, ttl_s=5)
check("trava barra prioridade igual", (not r["ok"]) and r["motivo"]=="travado")
r = b2.publicar("scared", origem="urgente", prioridade=99, ttl_s=5)
check("trava cede pra prioridade maior", r["ok"] and b2.valendo().emocao=="scared")

# 4. TTL expira e volta pro neutro
b3 = BarramentoEmocao()
b3.publicar("surprised", origem="t", prioridade=50, ttl_s=0.3)
check("vale antes de expirar", b3.valendo() is not None)
time.sleep(0.4)
check("expira sozinho (valendo=None => neutro)", b3.valendo() is None)

# 5. expirou_agora consome uma vez so
b4 = BarramentoEmocao()
b4.publicar("teasing", origem="t", prioridade=50, ttl_s=0.2)
time.sleep(0.3)
check("expirou_agora avisa 1x", b4.expirou_agora() is not None)
check("expirou_agora nao repete", b4.expirou_agora() is None)

# 6. ttl absurdo e' limitado (nao segura o rosto pra sempre)
b5 = BarramentoEmocao()
b5.publicar("angry", origem="t", prioridade=50, ttl_s=99999)
check("ttl limitado ao teto", b5.valendo().ttl_s == 30.0, f"(ttl={b5.valendo().ttl_s})")

# 7. emocao desconhecida cai no neutro E e' contada (o caso do 'think')
mapa = {"neutral":0,"happy":3,"sad":1,"angry":2}
b6 = BarramentoEmocao()
idx = b6.indice_expressao("think", mapa)
check("desconhecida cai no neutro", idx == 0, f"(idx={idx})")
check("desconhecida e' contada", b6.diagnostico()["emocoes_desconhecidas"].get("think")==1)
check("conhecida nao e' contada como desconhecida", b6.indice_expressao("happy", mapa)==3 and len(b6.diagnostico()["emocoes_desconhecidas"])==1)

# 8. assinante que explode NAO derruba quem publicou
b7 = BarramentoEmocao(ao_mudar=lambda e: (_ for _ in ()).throw(RuntimeError("boom")))
try:
    r = b7.publicar("happy", origem="t", prioridade=50, ttl_s=2)
    check("assinante quebrado nao derruba publisher", r["ok"])
except Exception as e:
    check("assinante quebrado nao derruba publisher", False, f"({e})")

# 9. emocao vazia/None vira neutro em vez de quebrar
b8 = BarramentoEmocao()
check("emocao vazia vira neutro", b8.publicar("", origem="t", prioridade=1)["emocao"]==NEUTRA)

print()
print("FALHAS:", falhas if falhas else "nenhuma")
sys.exit(1 if falhas else 0)
