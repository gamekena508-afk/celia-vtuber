"""Regua do F3-A. Escrita pelo JUIZ. O agente que implementa NAO pode edita-la.

Contrato: o ativo ganha REGRA DE SELECAO separada do texto; o roteador decide
com justificativa estruturada; e em SHADOW MODE ele NAO altera a fala.

Modulos esperados:
  src/celia/core/memoria.py    -> schema celia.banco.ativo.v2 com bloco 'selecao'
  src/celia/core/roteador.py   -> Roteador

    Roteador(memoria, personagem, modo="shadow")
      .modo                                   -> "shadow" | "ativo"
      .decidir(entrada, contexto=None) -> Decisao

    Decisao (objeto ou dict) com:
      .resultado    BANK_HIT | BANK_MISS | BANK_REJECTED_LOW_CONFIDENCE
                    | BANK_REJECTED_CONTEXT
      .ativo_id     id do ativo escolhido, ou None
      .candidatos   lista dos avaliados
      .regra        qual regra bateu
      .confianca    numero
      .restricoes   restricoes avaliadas
      .razao        texto curto do porque
      .duracao_ms   tempo gasto

Rodar: .venv/bin/python tests/prova_f3a_roteador.py
"""
import hashlib
import sys
import tempfile
import time
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))
FALHAS: list[str] = []


def checa(nome, cond, detalhe=""):
    if cond:
        print(f"  OK    {nome}")
    else:
        print(f"  FALHA {nome}  {detalhe}")
        FALHAS.append(f"{nome} {detalhe}")


def campo(d, nome):
    return d.get(nome) if isinstance(d, dict) else getattr(d, nome, None)


def _wav(tmp, nome):
    p = tmp / nome
    p.write_bytes(b"RIFF" + b"\0" * 200)
    return p, hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    print("REGUA F3-A - regra de selecao, roteador conservador e shadow mode\n")
    try:
        from celia.core.memoria import Memoria
        from celia.core.roteador import Roteador
    except Exception as exc:  # noqa: BLE001
        print(f"  FALHA import: {exc}")
        print("\nRESULTADO: REPROVADO")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="f3a_"))
    m = Memoria(tmp)

    def publica(texto, personagem="celia_001", **extra):
        w, h = _wav(tmp, f"{abs(hash(texto+personagem))}.wav")
        base = dict(texto=texto, camada="L1", categoria="saudacao",
                    personagem=personagem, motor="omnivoice", perfil_voz="neutra",
                    estado_validacao="aprovado", audio_path=str(w), audio_sha256=h)
        base.update(extra)
        return m.publicar_ativo(**base)

    # ------------------------------------------------------------------ 1
    print("[1] o ativo carrega REGRA DE SELECAO separada do texto")
    sel = {"intencao": "saudacao", "gatilhos": ["bom dia", "bom dia pai"],
           "contextos_permitidos": ["conversa"], "confianca_minima": 0.7}
    aid = publica("Bom dia, Pai.", selecao=sel)
    a = m.ativo(aid)
    checa("o schema do ativo subiu de versao",
          campo(a, "schema") == "celia.banco.ativo.v2", f"veio {campo(a,'schema')!r}")
    s = campo(a, "selecao")
    checa("existe bloco 'selecao'", isinstance(s, dict), f"veio {type(s).__name__}")
    if isinstance(s, dict):
        for c in ("intencao", "gatilhos", "contextos_permitidos"):
            checa(f"selecao carrega '{c}'", c in s)
    checa("o texto NAO virou depositario de regra",
          "gatilho" not in str(campo(a, "texto")).lower())

    print("\n[1b] a regra de selecao e validada, nao aceita qualquer coisa")
    try:
        publica("x1", selecao="bom dia")          # string solta
        checa("selecao invalida e recusada", False, "aceitou string solta")
    except Exception:
        checa("selecao invalida e recusada", True)

    print("\n[1c] migracao: ativo v1 sem selecao continua legivel")
    aid2 = publica("Oi.")                          # sem bloco selecao
    checa("ativo sem selecao ainda publica", m.ativo(aid2) is not None)

    # ------------------------------------------------------------------ 2
    print("\n[2] SHADOW MODE e explicito, nao implicito")
    r = Roteador(m, personagem="celia_001")
    checa("o modo padrao e shadow", campo(r, "modo") == "shadow",
          f"veio {campo(r,'modo')!r}")
    d = r.decidir("bom dia")
    checa("a decisao traz resultado", campo(d, "resultado") is not None)
    checa("shadow NAO devolve audio para tocar",
          campo(d, "audio_path") is None or campo(d, "aplicar") is False,
          "o shadow esta entregando audio para playback")

    # ------------------------------------------------------------------ 3
    print("\n[3] a decisao e justificada, nao opaca")
    d = r.decidir("bom dia")
    for c in ("candidatos", "regra", "confianca", "restricoes", "razao", "duracao_ms"):
        checa(f"decisao carrega '{c}'", campo(d, c) is not None, "veio None")
    checa("resultado e um dos quatro nomes",
          campo(d, "resultado") in {"BANK_HIT", "BANK_MISS",
                                    "BANK_REJECTED_LOW_CONFIDENCE",
                                    "BANK_REJECTED_CONTEXT"},
          f"veio {campo(d,'resultado')!r}")

    # ------------------------------------------------------------------ 4
    print("\n[4] entrada compativel bate; ambigua e incompativel nao")
    d = r.decidir("bom dia", contexto="conversa")
    checa("entrada clara gera BANK_HIT",
          campo(d, "resultado") == "BANK_HIT", f"veio {campo(d,'resultado')}")
    checa("o hit aponta o ativo certo", campo(d, "ativo_id") == aid)
    d = r.decidir("qual a capital da mongolia em 1920", contexto="conversa")
    checa("entrada sem relacao NAO da hit",
          campo(d, "resultado") != "BANK_HIT", f"veio {campo(d,'resultado')}")
    d = r.decidir("bom dia", contexto="combate")
    checa("contexto incompativel bloqueia o hit",
          campo(d, "resultado") == "BANK_REJECTED_CONTEXT",
          f"veio {campo(d,'resultado')}")

    print("\n[4b] na duvida, MISS - errar por lentidao, nunca por resposta errada")
    d = r.decidir("bo dai")                       # parecido, mas nao igual
    checa("entrada duvidosa nao vira hit",
          campo(d, "resultado") != "BANK_HIT",
          f"veio {campo(d,'resultado')} com confianca {campo(d,'confianca')}")

    # ------------------------------------------------------------------ 5
    print("\n[5] personagem e barreira - quebrando de proposito")
    publica("Bom dia, Pai.", personagem="mao_pro_001", selecao=sel)
    r_celia = Roteador(m, personagem="celia_001")
    d = r_celia.decidir("bom dia", contexto="conversa")
    ids_outros = [c for c in (campo(d, "candidatos") or [])
                  if str(c.get("personagem", "celia_001") if isinstance(c, dict) else "celia_001") != "celia_001"]
    checa("nenhum candidato de outro personagem foi sequer avaliado",
          not ids_outros, f"vazou: {ids_outros[:2]}")
    r_mao = Roteador(m, personagem="mao_pro_001")
    d2 = r_mao.decidir("bom dia", contexto="conversa")
    checa("o roteador do outro personagem nao pega o ativo da Celia",
          campo(d2, "ativo_id") != aid)

    # ------------------------------------------------------------------ 6
    print("\n[6] o roteador NAO chama LLM nem rede")
    fonte = (RAIZ / "src/celia/core/roteador.py").read_text(encoding="utf-8")
    for proibido in ("requests", "httpx", "urllib.request", "openai",
                     "chat_completion", "aiohttp", "socket"):
        checa(f"roteador nao usa '{proibido}'", proibido not in fonte)

    # ------------------------------------------------------------------ 7
    print("\n[7] velocidade - o router existe para economizar segundos")
    for n in (50, 500, 5000):
        alvo = n - len(m.buscar(personagem="celia_001", estado="publicado"))
        for i in range(max(0, alvo)):
            publica(f"frase de enchimento numero {i}",
                    selecao={"intencao": f"i{i%40}", "gatilhos": [f"gat{i}"],
                             "contextos_permitidos": ["conversa"]})
        rr = Roteador(m, personagem="celia_001")
        tempos = []
        for _ in range(120):
            t0 = time.perf_counter()
            rr.decidir("bom dia", contexto="conversa")
            tempos.append((time.perf_counter() - t0) * 1000)
        o = sorted(tempos)
        p50 = o[len(o)//2]
        p95 = o[min(len(o)-1, int(len(o)*0.95))]
        print(f"  medido com ~{n} ativos: p50={p50:.2f} ms  p95={p95:.2f} ms")
        checa(f"p95 abaixo de 25 ms com ~{n} ativos", p95 < 25, f"veio {p95:.1f} ms")

    # ------------------------------------------------------------------ 8
    print("\n[8] gravar memoria nao pode derrubar a conversa")
    import celia.core.roteador as mod
    checa("existe caminho que registra sem propagar excecao",
          "except" in (RAIZ / "src/celia/core/roteador.py").read_text(encoding="utf-8")
          or "registrar_turno_seguro" in (RAIZ / "src/celia/core/memoria.py").read_text(encoding="utf-8"),
          "nao achei protecao contra falha de persistencia")

    # ------------------------------------------------------------------ 9
    print("\n[9] o F3 nao encosta no TTS realtime")
    conf = (RAIZ / "conf.yaml").read_text(encoding="utf-8")
    checa("tts_model continua x_tts", "tts_model: x_tts" in conf)
    checa("roteador nao cita omnivoice", "omnivoice" not in fonte.lower())
    checa("roteador nao troca motor", "tts_model" not in fonte)

    # ----------------------------------------------------------------- 10
    print("\n[10] a flag de modo e explicita e configuravel")
    checa("existe o nome do modo em algum lugar declarado",
          "bank_router_mode" in conf or "bank_router_mode" in fonte
          or "shadow" in conf,
          "o modo shadow nao esta declarado em config nem no modulo")

    print()
    if FALHAS:
        print(f"RESULTADO: REPROVADO - {len(FALHAS)} falha(s)")
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("RESULTADO: APROVADO")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
