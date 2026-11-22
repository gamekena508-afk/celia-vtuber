"""Regua do F2. Escrita pelo JUIZ. O agente que implementa NAO pode edita-la.

Contrato congelado da fundacao de dados: LOG, CANDIDATO e ATIVO sao tres
coisas distintas, e uma interacao nunca vira ativo publicado por ter
acontecido.

Modulo esperado: src/celia/core/memoria.py

    Memoria(raiz: Path)
      # LOG - o que aconteceu no turno (append-only)
      .registrar_turno(**campos) -> str                 id do registro
      .logs_do_turno(turn_id) -> list[dict]
      # CANDIDATO - talvez sirva depois
      .propor_candidato(**campos) -> str
      .candidato(id) -> dict | None
      # ATIVO - aprovado para uso imediato
      .publicar_ativo(**campos) -> str
      .ativo(id) -> dict | None
      .verificar_audio(ativo_id) -> bool
      # CONSULTA indexada
      .buscar(*, categoria=None, intencao=None, template_id=None,
              texto_hash=None, estado=None, camada=None,
              personagem=None) -> list[dict]

Esquemas exigidos, seguindo a convencao ja usada na casa
("celia.roteiro.session.v1", "celia.omnivoice.run.v1"):
    celia.interacao.log.v1
    celia.memoria.candidato.v1
    celia.banco.ativo.v1

Rodar: .venv/bin/python tests/prova_f2_memoria.py
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


def erra(nome, fn, *a, **k):
    """Passa se a chamada LEVANTAR excecao."""
    try:
        fn(*a, **k)
    except Exception:
        print(f"  OK    {nome}")
        return
    print(f"  FALHA {nome}  aceitou o que devia recusar")
    FALHAS.append(nome)


def main() -> int:
    print("REGUA F2 - LOG, CANDIDATO e ATIVO sao tres coisas diferentes\n")
    try:
        from celia.core.memoria import Memoria
    except Exception as exc:  # noqa: BLE001
        print(f"  FALHA import de celia.core.memoria: {exc}")
        print("\nRESULTADO: REPROVADO (modulo nao existe)")
        return 1

    tmp = Path(tempfile.mkdtemp(prefix="f2_"))
    m = Memoria(tmp)

    # ------------------------------------------------------------------ 1
    print("[1] o schema recusa registro invalido")
    erra("registrar_turno sem turn_id levanta", m.registrar_turno, resposta="oi")
    erra("propor_candidato sem texto levanta", m.propor_candidato, camada="L0")
    erra("camada invalida levanta", m.propor_candidato, texto="oi", camada="L9")
    erra("publicar_ativo sem validacao levanta",
         m.publicar_ativo, texto="oi", camada="L0", categoria="saudacao")

    # ------------------------------------------------------------------ 2
    print("\n[2] o log guarda o turno com os campos do F-1")
    rid = m.registrar_turno(
        turn_id="t-aaa", personagem="celia_001", input_normalizado="oi",
        resposta="Oi, paizao.", intencao="saudacao", emocao="joy",
        fonte="LOCAL_LLM", modelo="gpt", tts_engine="x_tts",
        latencias={"llm_ttft": 2400, "first_audio_sent": 700},
        bank_hit=False, bank_miss=True, fallback_generation=True,
    )
    checa("registrar_turno devolve id", isinstance(rid, str) and rid)
    logs = m.logs_do_turno("t-aaa")
    checa("logs_do_turno acha o registro", len(logs) == 1, f"veio {len(logs)}")
    if logs:
        r = logs[0]
        checa("o registro carrega o schema versionado",
              r.get("schema") == "celia.interacao.log.v1", f"veio {r.get('schema')!r}")
        for c in ("turn_id", "timestamp", "tts_engine", "latencias", "bank_miss"):
            checa(f"campo '{c}' persistido", c in r)
        checa("nao guarda payload bruto de audio",
              not any(k in r for k in ("audio_bytes", "wav", "raw_audio")))

    # ------------------------------------------------------------------ 3
    print("\n[3] turnos diferentes nao colidem")
    m.registrar_turno(turn_id="t-bbb", personagem="celia_001", resposta="outra")
    checa("turno A continua com 1", len(m.logs_do_turno("t-aaa")) == 1)
    checa("turno B tem o dele", len(m.logs_do_turno("t-bbb")) == 1)
    checa("turno inexistente devolve vazio, sem excecao",
          m.logs_do_turno("t-nunca") == [])

    # ------------------------------------------------------------------ 4
    print("\n[4] interacao NAO vira ativo publicado sozinha")
    checa("nada foi publicado ao registrar turno",
          m.buscar(estado="publicado") == [], "apareceu ativo sem publicacao explicita")
    cid = m.propor_candidato(texto="Oi, paizao.", camada="L0",
                             categoria="saudacao", personagem="celia_001",
                             turn_id="t-aaa")
    checa("propor_candidato devolve id", isinstance(cid, str) and cid)
    c = m.candidato(cid)
    checa("candidato nasce com schema proprio",
          c and c.get("schema") == "celia.memoria.candidato.v1", f"veio {c and c.get('schema')}")
    checa("candidato NAO esta publicado",
          c and c.get("estado") != "publicado", f"estado {c and c.get('estado')}")

    # ------------------------------------------------------------------ 5
    print("\n[5] template e frase literal sao distinguiveis")
    t = m.propor_candidato(texto="{nome}, tu aprontou de novo.", camada="L2",
                           categoria="provocacao", personagem="celia_001",
                           template_id="prov_001", variaveis=["nome"])
    lit = m.propor_candidato(texto="Tu aprontou de novo.", camada="L1",
                             categoria="provocacao", personagem="celia_001")
    ct, cl = m.candidato(t), m.candidato(lit)
    checa("o template declara suas variaveis", ct and ct.get("variaveis") == ["nome"])
    checa("a frase literal nao tem template_id", cl and not cl.get("template_id"))
    checa("busca por template_id acha so o template",
          [x.get("template_id") for x in m.buscar(template_id="prov_001")] == ["prov_001"])
    erra("template com variavel nao declarada levanta",
         m.propor_candidato, texto="{jogo} de novo", camada="L2",
         categoria="x", personagem="celia_001", template_id="p2", variaveis=[])

    # ------------------------------------------------------------------ 6
    print("\n[6] audio do Coqui realtime nao publica ativo permanente")
    erra("publicar ativo gerado por x_tts levanta",
         m.publicar_ativo, texto="Oi", camada="L0", categoria="saudacao",
         personagem="celia_001", motor="x_tts", estado_validacao="aprovado",
         audio_path="/tmp/x.wav")

    # ------------------------------------------------------------------ 7
    print("\n[7] ativo exige estado explicito e o hash pega audio alterado")
    wav = tmp / "a.wav"
    wav.write_bytes(b"RIFF" + b"\0" * 200)
    h = hashlib.sha256(wav.read_bytes()).hexdigest()
    aid = m.publicar_ativo(texto="Oi, paizao.", camada="L0", categoria="saudacao",
                           personagem="celia_001", motor="omnivoice",
                           perfil_voz="neutra", estado_validacao="aprovado",
                           audio_path=str(wav), audio_sha256=h)
    a = m.ativo(aid)
    checa("ativo tem schema proprio",
          a and a.get("schema") == "celia.banco.ativo.v1", f"veio {a and a.get('schema')}")
    for c in ("texto", "audio_path", "motor", "perfil_voz", "estado_validacao",
              "audio_sha256", "criado_em", "categoria"):
        checa(f"ativo carrega '{c}'", a and c in a)
    checa("verificar_audio aprova o arquivo intacto", m.verificar_audio(aid) is True)
    wav.write_bytes(b"RIFF" + b"\0" * 201)          # alterado por fora
    checa("verificar_audio DETECTA o audio alterado",
          m.verificar_audio(aid) is False, "o hash nao pegou a alteracao")

    # ------------------------------------------------------------------ 8
    print("\n[8] duplicata exata e detectavel pelo hash do texto")
    d1 = m.propor_candidato(texto="Tudo certo por aqui.", camada="L1",
                            categoria="reacao", personagem="celia_001")
    achados = m.buscar(texto_hash=m.candidato(d1)["texto_hash"])
    checa("busca por texto_hash acha o registro", len(achados) >= 1)
    d2 = m.propor_candidato(texto="  TUDO   certo, por aqui!  ", camada="L1",
                            categoria="reacao", personagem="celia_001")
    checa("texto equivalente produz o MESMO hash normalizado",
          m.candidato(d1)["texto_hash"] == m.candidato(d2)["texto_hash"],
          "a normalizacao nao pegou a duplicata")

    # ------------------------------------------------------------------ 9
    print("\n[9] o personagem separa os bancos (mao_pro nao contamina a Celia)")
    m.propor_candidato(texto="frase do modelo de demo", camada="L1",
                       categoria="x", personagem="mao_pro_001")
    so_celia = m.buscar(personagem="celia_001")
    checa("busca por personagem nao mistura",
          all(x.get("personagem") == "celia_001" for x in so_celia),
          "vazou registro de outro personagem")

    # ----------------------------------------------------------------- 10
    print("\n[10] consulta em escala, sem varredura absurda")
    t0 = time.perf_counter()
    for i in range(3000):
        m.propor_candidato(texto=f"frase numero {i}", camada="L1",
                           categoria=f"cat{i % 20}", personagem="celia_001")
    escrita = time.perf_counter() - t0
    print(f"  medido: 3000 insercoes em {escrita:.2f}s")
    t0 = time.perf_counter()
    for _ in range(200):
        m.buscar(categoria="cat7")
    busca = (time.perf_counter() - t0) / 200 * 1000
    print(f"  medido: {busca:.2f} ms por consulta indexada sobre 3000+ registros")
    checa("consulta por categoria abaixo de 50 ms", busca < 50, f"veio {busca:.1f} ms")
    t0 = time.perf_counter()
    for _ in range(200):
        m.candidato(cid)
    porid = (time.perf_counter() - t0) / 200 * 1000
    print(f"  medido: {porid:.3f} ms por consulta por id")
    checa("consulta por id abaixo de 10 ms", porid < 10, f"veio {porid:.2f} ms")

    # ----------------------------------------------------------------- 11
    print("\n[11] o F2 nao encosta no TTS realtime")
    fonte = (RAIZ / "src/celia/core/memoria.py").read_text(encoding="utf-8")
    for proibido in ("tts_model", "TTSFactory", "async_generate_audio", "conf.yaml"):
        checa(f"memoria.py nao mexe em '{proibido}'", proibido not in fonte)
    conf = (RAIZ / "conf.yaml").read_text(encoding="utf-8")
    checa("tts_model continua x_tts", "tts_model: x_tts" in conf)

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
