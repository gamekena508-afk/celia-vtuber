"""Regua do F-1. Escrita pelo JUIZ, nao pelo agente que implementa.

Contrato congelado que a implementacao precisa satisfazer. O agente nao pode
editar este arquivo - se ele editar, o julgamento e teatro.

Modulo esperado: src/celia/core/latencia.py

    Cronometro(janela: int = ...)
        .novo_turno() -> str                       id unico do turno
        .marcar(turno_id, estagio, ms) -> None     registra um estagio
        .estagios_do_turno(turno_id) -> dict       {estagio: ms} daquele turno
        .snapshot() -> dict                        {estagio: {count,min,media,
                                                    p50,p90,p95,max}}
        .janela                                    int, teto de amostras/estagio

Rodar:  .venv/bin/python tests/prova_latencia_f1.py
"""
import statistics
import sys
import threading
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "src"))

FALHAS: list[str] = []


def checa(nome: str, condicao: bool, detalhe: str = "") -> None:
    if condicao:
        print(f"  OK    {nome}")
    else:
        print(f"  FALHA {nome}  {detalhe}")
        FALHAS.append(f"{nome} {detalhe}")


def main() -> int:
    print("REGUA F-1 - correlacao por turno, distribuicao e estagios ausentes\n")

    try:
        from celia.core.latencia import Cronometro
    except Exception as exc:  # noqa: BLE001
        print(f"  FALHA import de celia.core.latencia: {exc}")
        print("\nRESULTADO: REPROVADO (modulo nao existe ou nao importa)")
        return 1

    # ---------------------------------------------------------------- 1
    print("[1] eventos do mesmo turno recebem o mesmo ID")
    c = Cronometro()
    t = c.novo_turno()
    for estagio, ms in (("asr", 100), ("router", 5), ("llm_ttft", 800), ("tts", 400)):
        c.marcar(t, estagio, ms)
    obtido = c.estagios_do_turno(t)
    checa("os 4 estagios voltam sob o mesmo turno",
          set(obtido) == {"asr", "router", "llm_ttft", "tts"}, f"veio {sorted(obtido)}")
    checa("os valores sao os que entraram", obtido.get("asr") == 100 and obtido.get("tts") == 400,
          f"veio {obtido}")

    # ---------------------------------------------------------------- 2
    print("\n[2] turnos simultaneos nao se misturam")
    c = Cronometro()
    a, b = c.novo_turno(), c.novo_turno()
    checa("dois turnos tem ids diferentes", a != b, f"a={a} b={b}")
    c.marcar(a, "asr", 111)
    c.marcar(b, "asr", 222)
    checa("turno A guarda so o dele", c.estagios_do_turno(a).get("asr") == 111,
          f"veio {c.estagios_do_turno(a)}")
    checa("turno B guarda so o dele", c.estagios_do_turno(b).get("asr") == 222,
          f"veio {c.estagios_do_turno(b)}")

    # concorrencia real, nao simulada
    c2 = Cronometro()
    ids: list[str] = []
    trava = threading.Lock()

    def trabalha(valor: int) -> None:
        tid = c2.novo_turno()
        with trava:
            ids.append(tid)
        for _ in range(20):
            c2.marcar(tid, "asr", valor)

    fios = [threading.Thread(target=trabalha, args=(i,)) for i in range(12)]
    for f in fios:
        f.start()
    for f in fios:
        f.join()
    checa("12 threads geraram 12 ids distintos", len(set(ids)) == 12, f"veio {len(set(ids))}")
    sujos = [i for i, tid in enumerate(ids) if set(_valores(c2, tid, "asr")) - {_esperado(ids, tid)}]
    checa("nenhum turno recebeu valor de outro", not sujos, f"turnos sujos: {sujos[:3]}")

    # ---------------------------------------------------------------- 3
    print("\n[3] percentis calculados corretamente")
    c = Cronometro()
    amostras = list(range(1, 101))  # 1..100
    for ms in amostras:
        c.marcar(c.novo_turno(), "llm", ms)
    s = c.snapshot().get("llm", {})
    checa("count = 100", s.get("count") == 100, f"veio {s.get('count')}")
    checa("min = 1", s.get("min") == 1, f"veio {s.get('min')}")
    checa("max = 100", s.get("max") == 100, f"veio {s.get('max')}")
    checa("media = 50.5", abs((s.get("media") or 0) - 50.5) < 0.6, f"veio {s.get('media')}")
    p50_ok = abs((s.get("p50") or 0) - statistics.median(amostras)) <= 1
    checa("p50 bate com a mediana", p50_ok, f"veio {s.get('p50')} esperado ~50.5")
    checa("p90 entre 89 e 91", 89 <= (s.get("p90") or 0) <= 91, f"veio {s.get('p90')}")
    checa("p95 entre 94 e 96", 94 <= (s.get("p95") or 0) <= 96, f"veio {s.get('p95')}")
    checa("p50 <= p90 <= p95 <= max",
          (s.get("p50") or 0) <= (s.get("p90") or 0) <= (s.get("p95") or 0) <= (s.get("max") or 0),
          f"veio {s}")

    # um unico valor nao pode explodir
    c = Cronometro()
    c.marcar(c.novo_turno(), "solo", 42)
    s = c.snapshot().get("solo", {})
    checa("uma amostra so nao quebra o percentil",
          s.get("count") == 1 and s.get("p95") == 42, f"veio {s}")

    # ---------------------------------------------------------------- 4
    print("\n[4] a janela nao cresce infinitamente")
    c = Cronometro(janela=50)
    checa("a janela e declarada", getattr(c, "janela", None) == 50, f"veio {getattr(c, 'janela', None)}")
    for i in range(5000):
        c.marcar(c.novo_turno(), "spam", i)
    s = c.snapshot().get("spam", {})
    checa("count nao passa da janela", (s.get("count") or 0) <= 50, f"veio {s.get('count')}")
    interno = _tamanho_interno(c)
    checa("nenhuma estrutura interna passa de 5000 itens", interno <= 5000,
          f"maior estrutura tem {interno}")

    # ---------------------------------------------------------------- 5
    print("\n[5] estagio ausente nao quebra o snapshot")
    c = Cronometro()
    t = c.novo_turno()
    c.marcar(t, "asr", 10)   # so um estagio; os outros nunca chegaram
    try:
        s = c.snapshot()
        ok = isinstance(s, dict) and "asr" in s
    except Exception as exc:  # noqa: BLE001
        ok = False
        s = f"levantou {exc!r}"
    checa("snapshot devolve dict mesmo com estagios faltando", ok, f"veio {s}")
    try:
        vazio = Cronometro().snapshot()
        ok2 = isinstance(vazio, dict)
    except Exception as exc:  # noqa: BLE001
        ok2 = False
        vazio = f"levantou {exc!r}"
    checa("cronometro vazio devolve dict, nao excecao", ok2, f"veio {vazio}")
    try:
        c.estagios_do_turno("turno-que-nunca-existiu")
        ok3 = True
    except Exception as exc:  # noqa: BLE001
        ok3 = False
    checa("turno inexistente nao levanta excecao", ok3)

    # ---------------------------------------------------------------- 6
    print("\n[6] estagios do plano estao nomeados")
    from celia.core import latencia as mod
    nomes = " ".join(str(getattr(mod, n, "")) for n in dir(mod) if not n.startswith("_"))
    fonte = (RAIZ / "src/celia/core/latencia.py").read_text(encoding="utf-8")
    for exigido in ("texto_utilizavel", "first_audio_generated", "first_audio_playback_started"):
        checa(f"o estagio '{exigido}' esta declarado no modulo",
              exigido in fonte or exigido in nomes,
              "nao achei o nome no arquivo")

    # ---------------------------------------------------------------- 7
    print("\n[7] overhead da medicao")
    import time
    c = Cronometro()
    t0 = time.perf_counter()
    for _ in range(10000):
        c.marcar(c.novo_turno(), "x", 1)
    gasto_us = (time.perf_counter() - t0) / 10000 * 1e6
    print(f"  medido: {gasto_us:.1f} us por marcacao")
    checa("custo por marcacao abaixo de 200 us", gasto_us < 200, f"veio {gasto_us:.1f} us")

    print()
    if FALHAS:
        print(f"RESULTADO: REPROVADO - {len(FALHAS)} falha(s)")
        for f in FALHAS:
            print(f"  - {f}")
        return 1
    print("RESULTADO: APROVADO")
    return 0


def _valores(c, turno_id, estagio):
    v = c.estagios_do_turno(turno_id).get(estagio)
    return [v] if v is not None else []


def _esperado(ids, tid):
    return ids.index(tid)


def _tamanho_interno(obj) -> int:
    maior = 0
    for nome in dir(obj):
        if nome.startswith("__"):
            continue
        try:
            v = getattr(obj, nome)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(v, (list, dict, set, tuple)):
            maior = max(maior, len(v))
            if isinstance(v, dict):
                for sub in v.values():
                    if isinstance(sub, (list, dict, set, tuple)):
                        maior = max(maior, len(sub))
    return maior


if __name__ == "__main__":
    raise SystemExit(main())
