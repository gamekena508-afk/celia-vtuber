#!/usr/bin/env python3
"""
Instrumento de prova da visao da Celia — escrito pelo JUIZ, nao pelo executor.

CONTRATO CONGELADO (decisao 1a e 2a de docs/planos/PLANO-VISAO-CELIA.md:
"o navegador e a unica fonte" + "pytesseract sobre o tesseract 5.5.2"):

  POST /bridge/vision/frame   {"image": "data:image/jpeg;base64,..."}
       -> 200 {"ok": true, "bytes": N}
       -> 422 quando o data URL nao e JPEG ou estoura o limite
  GET  /bridge/vision/frame
       -> 200 {"image": "data:image/jpeg;base64,...", "age_ms": N}
       -> 404 {"detail": "sem_frame"} quando nada foi enviado ainda

  capture_screen() le esse GET. Nao usa mss. Sem frame -> None.
  As ferramentas, sem frame, devolvem a mensagem HONESTA
  "Sem frame do navegador" — que NAO e falha de captura, e sim
  o custo aceito na decisao 1a ("ela so enxerga com o Screen ligado").

Por que o teste INJETA um frame: se ele dependesse do Screen ligado no
navegador, reprovaria um conserto correto so porque ninguem estava com a
aba aberta. Injetando, a cadeia inteira e exercitada sem navegador.

Camadas:
  0  servidor vivo e a rota aceita o frame
  1  capture_screen() devolve imagem com CONTEUDO REAL
     (getextrema, nao size: no Wayland o mss devolve tamanho certo e
     pixels pretos — tamanho nunca provou conteudo)
  2  as 5 ferramentas vision_* por stdio real, nenhuma com falha de captura
  3  o OCR le o titulo grande E a linha normal que EU escrevi

Uso:
    python tests/prova_visao.py             # tudo
    python tests/prova_visao.py --rapido    # camadas 0 e 1 (sem rede/modelo)
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MCP_DIR = RAIZ / "mcp"
BASE = os.environ.get("CELIA_BRIDGE_URL", "http://127.0.0.1:12393").rstrip("/")
ROTA = f"{BASE}/bridge/vision/frame"

# Duas palavras, dois tamanhos. Motivo medido em 2026-08-27: um quadro com UMA
# palavra gigante e so isso faz o tesseract no modo automatico (psm 3) devolver
# vazio — e nenhuma tela real e assim. A primeira versao deste teste usava esse
# quadro irreal e REPROVOU um conserto correto. Medicao que derrubou a minha
# propria assercao:
#     so "CELIA" em 190px  -> psm 3 devolve ''  | psm 6 devolve 'CELIA'
#     titulo 150px + linha 40px -> psm 3 le AS DUAS | psm 6 devolve ''
# Ou seja: o padrao do produto e o certo para tela de verdade. O teste e que
# estava errado, e quem mudou foi o teste.
PALAVRA_TITULO = "CELIA"
PALAVRA_CORPO = "damas"

FALHAS_DE_CAPTURA = (
    "Erro ao capturar tela",
    "winocr nao instalado",
    "winocr não instalado",
    "mss nao instalado",
    "mss não instalado",
)
FALHAS_DE_MODELO = ("NVIDIA", "timeout", "Connection", "429", "401", "500")

FERRAMENTAS = [
    ("vision_extract_text", {}),
    ("vision_understand_context", {}),
    ("vision_list_options", {}),
    ("vision_describe_visual", {}),
    ("vision_custom_analysis", {"prompt": "Descreva em uma frase.", "max_tokens": 40}),
]


def p(txt: str = "") -> None:
    print(txt, flush=True)


def frame_de_teste() -> str:
    """JPEG 1280x720 parecido com uma TELA DE VERDADE: gradiente ao fundo,
    um titulo grande (tipo HUD de jogo) e uma linha de texto normal."""
    from PIL import Image, ImageDraw, ImageFont

    largura, altura = 1280, 720
    img = Image.new("RGB", (largura, altura), (22, 22, 34))
    d = ImageDraw.Draw(img)
    for y in range(0, altura, 4):  # gradiente => extrema nunca uniforme
        tom = int(22 + (y / altura) * 80)
        d.rectangle([0, y, largura, y + 4], fill=(tom, tom // 2, 120 - tom // 3))

    caminho_fonte = None
    for caminho in (
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ):
        if Path(caminho).exists():
            caminho_fonte = caminho
            break

    # fundo branco atras do texto: OCR odeia texto sobre gradiente
    d.rectangle([90, 150, 1190, 520], fill=(255, 255, 255))
    if caminho_fonte:
        d.text((150, 190), PALAVRA_TITULO, fill=(0, 0, 0),
               font=ImageFont.truetype(caminho_fonte, 150))
        d.text((150, 400), f"modo {PALAVRA_CORPO} ativo", fill=(0, 0, 0),
               font=ImageFont.truetype(caminho_fonte, 40))
    else:
        d.text((300, 300), f"{PALAVRA_TITULO} modo {PALAVRA_CORPO} ativo",
               fill=(0, 0, 0))

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


_FRAME_CACHE: str | None = None


def frame_fixo() -> str:
    """Mesmo frame em todas as injecoes: assim a unica variavel e o produto."""
    global _FRAME_CACHE
    if _FRAME_CACHE is None:
        _FRAME_CACHE = frame_de_teste()
    return _FRAME_CACHE


def _post(url: str, corpo: dict, timeout: int = 15):
    dados = json.dumps(corpo).encode()
    req = urllib.request.Request(
        url, data=dados, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]
    except Exception as e:  # noqa: BLE001
        return 0, repr(e)


# ---------------------------------------------------------------- camada 0
def camada0() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=5) as r:
            if r.status != 200:
                return False, f"servidor respondeu HTTP {r.status} em {BASE}"
    except Exception as e:  # noqa: BLE001
        return False, f"servidor da Celia nao responde em {BASE}: {e!r}"

    status, corpo = _post(ROTA, {"image": frame_fixo()})
    if status != 200:
        return False, f"POST {ROTA} -> HTTP {status} {corpo}"

    # a rota tem de RECUSAR lixo, senao aceitar nao prova nada
    ruim, _ = _post(ROTA, {"image": "data:text/plain;base64,QUJD"})
    if ruim == 200:
        return False, "POST aceitou data URL que nao e JPEG — validacao ausente"

    return True, f"POST 200 e recusa de nao-JPEG com HTTP {ruim}"


# ------------------------------------------------------- camada 1b validade
def camada_validade() -> tuple[bool, str]:
    """Frame parado tem de virar AUSENCIA, nunca visao confiante.

    Medido em 2026-08-27 com prova-visao-frame.html: desligado o Screen, o slot
    do servidor guarda o ultimo frame para SEMPRE e o age_ms so cresce. Sem
    teto, as ferramentas descreveriam com seguranca uma tela que ja nao existe.
    Aqui o teto vai a 1 ms de proposito: qualquer frame ja nasce velho.
    """
    import importlib

    sys.path.insert(0, str(MCP_DIR))
    antes = os.environ.get("CELIA_VISION_FRAME_MAX_AGE_MS")
    os.environ["CELIA_VISION_FRAME_MAX_AGE_MS"] = "1"
    try:
        _post(ROTA, {"image": frame_fixo()})
        import vision_mcp_realtime as vr  # type: ignore
        vr = importlib.reload(vr)
        img = vr.capture_screen()
        if img is not None:
            return False, "frame vencido foi aceito como visao — teto de idade nao aplicado"
        if not getattr(vr, "ultimo_get_sem_frame", False):
            return False, "frame vencido virou FALHA em vez de ausencia honesta"
        return True, f"frame vencido tratado como ausencia (teto {vr.IDADE_MAXIMA_MS} ms)"
    except Exception as e:  # noqa: BLE001
        return False, f"excecao ao testar validade: {e!r}"
    finally:
        if antes is None:
            os.environ.pop("CELIA_VISION_FRAME_MAX_AGE_MS", None)
        else:
            os.environ["CELIA_VISION_FRAME_MAX_AGE_MS"] = antes
        try:
            import vision_mcp_realtime as vr2  # type: ignore
            importlib.reload(vr2)
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- camada 1
def camada1() -> tuple[bool, str]:
    sys.path.insert(0, str(MCP_DIR))
    for mod in [m for m in sys.modules if m.startswith("vision_mcp")]:
        del sys.modules[mod]
    try:
        from vision_mcp_realtime import capture_screen  # type: ignore
    except Exception as e:  # noqa: BLE001
        return False, f"nao consegui importar capture_screen: {e!r}"

    try:
        img = capture_screen()
    except Exception as e:  # noqa: BLE001
        return False, f"capture_screen() levantou excecao: {e!r}"
    if img is None:
        return False, "capture_screen() devolveu None mesmo com frame injetado"

    try:
        tamanho, extrema = img.size, img.getextrema()
    except Exception as e:  # noqa: BLE001
        return False, f"objeto devolvido nao e imagem PIL utilizavel: {e!r}"
    if not tamanho or tamanho[0] < 100 or tamanho[1] < 100:
        return False, f"imagem pequena demais para ser uma tela: {tamanho}"
    if all(isinstance(c, tuple) and c[0] == c[1] for c in extrema):
        return False, (
            f"imagem UNIFORME {tamanho} extrema={extrema} — sintoma do mss no "
            "Wayland: tamanho certo, conteudo nenhum"
        )
    return True, f"imagem com conteudo real {tamanho} extrema={extrema}"


# ------------------------------------------------------------ camadas 2 e 3
async def camadas23() -> tuple[bool, bool, list[str]]:
    try:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
    except Exception as e:  # noqa: BLE001
        return False, False, [f"SDK mcp indisponivel: {e!r}"]

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP_DIR / "vision_mcp_server.py")],
        env={**os.environ, "PYTHONPATH": str(MCP_DIR), "CELIA_BRIDGE_URL": BASE},
    )
    notas: list[str] = []
    capturou_ok = True
    ocr_ok = False
    try:
        async with stdio_client(params) as (leitura, escrita):
            async with ClientSession(leitura, escrita) as sessao:
                await asyncio.wait_for(sessao.initialize(), timeout=60)
                listadas = {t.name for t in (await sessao.list_tools()).tools}
                notas.append(f"ferramentas expostas: {len(listadas)}")
                for nome, args in FERRAMENTAS:
                    if nome not in listadas:
                        notas.append(f"  [FALHA] {nome}: nao exposta")
                        capturou_ok = False
                        continue
                    # Reinjeta: uma chamada ao modelo leva dezenas de segundos
                    # e o frame venceria no meio da propria prova.
                    _post(ROTA, {"image": frame_fixo()})
                    try:
                        r = await asyncio.wait_for(sessao.call_tool(nome, args), timeout=120)
                        texto = " ".join(getattr(c, "text", "") or "" for c in (r.content or []))
                    except Exception as e:  # noqa: BLE001
                        notas.append(f"  [FALHA] {nome}: excecao {e!r}")
                        capturou_ok = False
                        continue
                    achou = next((f for f in FALHAS_DE_CAPTURA if f in texto), None)
                    if achou:
                        notas.append(f'  [FALHA] {nome}: devolveu "{achou}"')
                        capturou_ok = False
                    elif any(f in texto for f in FALHAS_DE_MODELO) and len(texto) < 300:
                        notas.append(f"  [outra classe] {nome}: erro de modelo/rede — {texto[:100]!r}")
                    else:
                        notas.append(f"  [OK] {nome}: {len(texto)} chars")
                    if nome == "vision_extract_text":
                        baixo = texto.lower()
                        viu_titulo = PALAVRA_TITULO.lower() in baixo
                        viu_corpo = PALAVRA_CORPO.lower() in baixo
                        ocr_ok = viu_titulo and viu_corpo
                        notas.append(
                            f"  [OCR] titulo {PALAVRA_TITULO!r}: "
                            f"{'SIM' if viu_titulo else 'NAO'} | corpo "
                            f"{PALAVRA_CORPO!r}: {'SIM' if viu_corpo else 'NAO'}"
                        )
                        if not ocr_ok:
                            notas.append(f"  [OCR] devolveu -> {texto[:200]!r}")
    except Exception as e:  # noqa: BLE001
        return False, False, notas + [f"stdio falhou: {e!r}"]
    return capturou_ok, ocr_ok, notas


def main() -> int:
    """Instrumento manual: simula o CLIQUE do dono ligando a tela e desliga ao fim.

    Desde 2026-10-05 o servidor recusa frame de fonte desligada, entao a prova
    precisa avisar `POST /bridge/vision/state` como o navegador faria.
    """
    estado = f"{BASE}/bridge/vision/state"
    _post(estado, {"source": "screen", "active": True})
    try:
        return _main()
    finally:
        _post(estado, {"source": "screen", "active": False})


def _main() -> int:
    rapido = "--rapido" in sys.argv
    p("=" * 68)
    p("PROVA DA VISAO DA CELIA — instrumento do juiz")
    p(f"sessao grafica: {os.environ.get('XDG_SESSION_TYPE', '?')} | bridge: {BASE}")
    p(f"interpretador: {sys.executable}")
    p("=" * 68)

    ok0, d0 = camada0()
    p(f"\nCAMADA 0 — rota de frame aceita e valida: {'PASSOU' if ok0 else 'REPROVOU'}")
    p(f"  {d0}")

    ok1, d1 = camada1()
    p(f"\nCAMADA 1 — captura com conteudo real: {'PASSOU' if ok1 else 'REPROVOU'}")
    p(f"  {d1}")

    okv, dv = camada_validade()
    p(f"\nCAMADA 1b — frame parado vira ausencia: {'PASSOU' if okv else 'REPROVOU'}")
    p(f"  {dv}")

    if rapido:
        v = ok0 and ok1 and okv
        p(f"\nVEREDITO (rapido): {'PASSOU' if v else 'REPROVOU'}")
        return 0 if v else 1

    ok2, ok3, notas = asyncio.run(camadas23())
    p(f"\nCAMADA 2 — 5 ferramentas por stdio: {'PASSOU' if ok2 else 'REPROVOU'}")
    for n in notas:
        p(f"  {n}")
    p(f"\nCAMADA 3 — OCR leu a palavra injetada: {'PASSOU' if ok3 else 'REPROVOU'}")

    v = ok0 and ok1 and okv and ok2 and ok3
    p("\n" + "=" * 68)
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 68)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
