import sys, pathlib
import asyncio, time, os
# Deriva a raiz do proprio arquivo: o teste continua valendo se a arvore mudar
# de lugar. Cravar caminho absoluto foi exatamente o que quebrou estes testes
# quando Célia virou a raiz (2026-08-11).
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))
from celia.core.narrador import Narrador, familia, frase_imediata

falhas=[]
def ck(n,c,x=""):
    print(("  OK  " if c else "FALHOU")+f"  {n} {x}");  falhas.append(n) if not c else None

# roteamento de ferramenta -> familia de frase
for nome, esperado in [("ddg-search","busca"),("vision_extract_text","visao"),
                       ("edicao_de_imagem","imagem"),("memoria_mcp","memoria"),
                       ("minecraft_status","minecraft"),("coisa_nova_xyz","_generico")]:
    ck(f"'{nome}' -> {esperado}", familia(nome)==esperado, f"(deu {familia(nome)})")

ditas=[]
async def enviar(m): ditas.append(m)

async def main():
    n = Narrador(enviar)
    # inicio narra na hora
    t0=time.perf_counter()
    await n.evento({"status":"running","tool_id":"t1","tool_name":"ddg-search"})
    dt=(time.perf_counter()-t0)*1000
    ck("narra no inicio", len(ditas)==1 and ditas[0]["type"]=="celia-narracao")
    ck("camada imediata e' instantanea", dt < 50, f"({dt:.1f} ms)")
    ck("frase e' da familia certa", "pesquis" in ditas[0]["text"] or "procurar" in ditas[0]["text"] or "web" in ditas[0]["text"], f"({ditas[0]['text']!r})")

    # ferramenta rapida NAO diz 'pronto' (evita ruido)
    await n.evento({"status":"completed","tool_id":"t1","tool_name":"ddg-search"})
    ck("ferramenta rapida nao enche de 'pronto'", len(ditas)==1, f"({len(ditas)} falas)")

    # ferramenta lenta DIZ pronto
    await n.evento({"status":"running","tool_id":"t2","tool_name":"edicao_de_imagem"})
    n._rodando["t2"]["em"] -= 5   # finge que demorou 5s
    await n.evento({"status":"completed","tool_id":"t2","tool_name":"edicao_de_imagem"})
    ck("ferramenta lenta confirma no fim", ditas[-1]["text"]=="pronto!", f"({ditas[-1]['text']!r})")

    # erro e' narrado
    await n.evento({"status":"running","tool_id":"t3","tool_name":"x"})
    await n.evento({"status":"error","tool_id":"t3","tool_name":"x"})
    ck("erro e' narrado", "não deu certo" in ditas[-1]["text"])

    # evento malformado nao derruba
    try:
        await n.evento({}); await n.evento({"status":None}); ck("evento quebrado nao derruba", True)
    except Exception as e: ck("evento quebrado nao derruba", False, str(e))

    # a regra que a medicao imposse: frase tardia e' descartada
    n._rodando.pop("t9", None)
    antes=len(ditas)
    await n._enriquecer("t9_ja_terminou", "ddg-search")
    ck("frase do LLM tardia e' descartada", len(ditas)==antes)

    print("\ndiagnostico:", n.diagnostico())

asyncio.run(main())
print("\nFALHAS:", falhas if falhas else "nenhuma")
sys.exit(1 if falhas else 0)
