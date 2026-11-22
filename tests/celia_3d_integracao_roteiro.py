#!/usr/bin/env python3
"""Prova real (Chrome headless contra :12393) da integracao 3D (2026-10-07, ordem do Pai). Nao e coletado pelo pytest (nome sem test_).
Uso: python3 tests/celia_3d_integracao_roteiro.py   (servidor da Celia precisa estar no ar; NAO reinicia nada)
Cenarios: A sem escolha (nada renderiza) | B escolher 2D | C escolher 3D (menu so com aprovadas; nao aprovada recusada; sem Live2D) | D Pet | E ?corpo=3d."""
import json, os, re, sys
from playwright.sync_api import sync_playwright
RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = os.environ.get("CELIA_URL", "http://127.0.0.1:12393/")
APR = json.load(open(os.path.join(RAIZ, "frontend", "celia-3d-aprovadas.json")))["aprovadas"]
EXP = json.load(open(os.path.expanduser("~/Downloads/celia3d-curadoria.json"))) if os.path.exists(os.path.expanduser("~/Downloads/celia3d-curadoria.json")) else None
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
MODELO = re.compile(r"/live2d-models/")  # modelo real do Live2D (texturas, moc3, model3.json). O bundle tenta "undefined/undefined.model3.json" no boot em qualquer modo (404 inofensivo, nao e o modelo); BUNDLE = re.compile(r"/assets/main-[\w-]+\.js"); CUBISM = re.compile(r"live2dcubismcore")
BUNDLE = re.compile(r"/assets/main-[\w-]+\.js"); CUBISM = re.compile(r"live2dcubismcore")
falhas = []; log = []
def ok(cond, texto):
    log.append(("OK  " if cond else "FALHA ") + texto)
    if not cond: falhas.append(texto)
def abrir(b, qs=""):
    ctx = b.new_context(viewport={"width": 1440, "height": 900}); p = ctx.new_page(); reqs = []; erros = []
    p.on("request", lambda r: reqs.append(r.url)); p.on("pageerror", lambda e: erros.append(str(e)))
    p.goto(URL + qs, wait_until="load"); return ctx, p, reqs, erros
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    # ---- A: sem escolha
    ctx, p, reqs, erros = abrir(b); p.wait_for_timeout(4000)
    ok(p.locator("#celia-escolha").count() == 1, "A: a tela de escolha aparece")
    ok(p.locator("canvas").count() == 0, "A: zero <canvas> antes da escolha")
    ok(not any(BUNDLE.search(u) for u in reqs), "A: o bundle compilado NAO foi baixado antes da escolha")
    ok(not any(CUBISM.search(u) for u in reqs), "A: o core Cubism NAO foi baixado antes da escolha")
    ok(not any(MODELO.search(u) for u in reqs), "A: nenhum arquivo do modelo Live2D (/live2d-models/) pedido antes da escolha")
    ok(not any("celia-3d-bancada" in u or "celia-3d-interface" in u for u in reqs), "A: nada do 3D foi pedido antes da escolha")
    ctx.close()
    # ---- B: escolher 2D
    ctx, p, reqs, erros = abrir(b); p.wait_for_selector("#celia-escolha button[data-corpo='2d']"); p.click("#celia-escolha button[data-corpo='2d']"); p.wait_for_timeout(9000)
    ok(p.locator("#celia-escolha").count() == 0, "B: a tela some depois da escolha")
    ok(any(BUNDLE.search(u) for u in reqs), "B: 2D baixa o bundle")
    ok(any(CUBISM.search(u) for u in reqs), "B: 2D baixa o Cubism")
    ok(any(MODELO.search(u) for u in reqs), "B: 2D pede o modelo Live2D (/live2d-models/)")
    ok(p.locator("canvas").count() >= 1, "B: 2D cria canvas")
    ok(not any("celia-3d-bancada" in u for u in reqs), "B: 2D nao toca no 3D")
    ctx.close()
    # ---- C: escolher 3D
    ctx, p, reqs, erros = abrir(b); p.wait_for_selector("#celia-escolha button[data-corpo='3d']"); p.click("#celia-escolha button[data-corpo='3d']")
    p.wait_for_function("() => window.celia3dInterface && celia3dInterface.pronta", timeout=120000); p.wait_for_timeout(9000)
    ok(not any(MODELO.search(u) for u in reqs), "C: 3D NAO pede o modelo Live2D")
    botoes = p.evaluate("() => [...document.querySelectorAll('#celia-3d-menu .lista button')].map(b => b.dataset.k)")
    ok(sorted(botoes) == sorted(APR), f"C: o menu tem exatamente as {len(APR)} aprovadas (tem {len(botoes)})")
    if EXP:
        proibidas = set(EXP["melhorar"]) | set(EXP["descartadas"])
        ok(not (set(botoes) & proibidas), "C: nenhuma ? (melhorar) nem X (descartada) no menu")
    quadro = p.frame_locator("#celia-3d-quadro")
    fr = [f for f in p.frames if "celia-3d-bancada" in f.url][0]
    antes = fr.evaluate("() => celia3dBancada.corpo.acao && celia3dBancada.corpo.acao.nome")
    p.click("#celia-3d-menu > button"); p.click("#celia-3d-menu .lista button[data-k='acenar']"); p.wait_for_timeout(800)
    ok(fr.evaluate("() => celia3dBancada.corpo.acao && celia3dBancada.corpo.acao.nome") == "acenar", "C: clicar numa aprovada toca a acao")
    p.evaluate("() => { document.getElementById('celia-3d-quadro').contentWindow.postMessage({ celia3d: 1, tipo: 'play', k: 'mortal' }, location.origin); }"); p.wait_for_timeout(600)
    ok(fr.evaluate("() => celia3dBancada.corpo.acao && celia3dBancada.corpo.acao.nome") != "mortal", "C: pedido de acao NAO aprovada (mortal) por mensagem e recusado")
    ok(fr.evaluate("() => celia3dBancada.corpo.play('andar', true)") is False, "C: Corpo3D recusa 'andar' (marcada ?)")
    p.evaluate("() => window.dispatchEvent(new CustomEvent('celia-emotion', { detail: { emotion: 'happy' } }))"); p.wait_for_timeout(500)
    ok(fr.evaluate("() => celia3dBancada.corpo.estado().emocao") == "happy", "C: celia-emotion chega no corpo 3D")
    ok(p.locator("#celia-3d-quadro").count() == 1 and fr.evaluate("() => celia3dBancada.estado") == "ok", "C: o corpo 3D esta de pe")
    p.screenshot(path=os.path.join(RAIZ, "var", "integracao_3d_C.png")) if os.path.isdir(os.path.join(RAIZ, "var")) else None
    ctx.close()
    # ---- D: Pet
    ctx, p, reqs, erros = abrir(b, "?palco=1&pet=1&fundo=cena"); p.wait_for_timeout(6000)
    ok(p.locator("#celia-escolha").count() == 0, "D: Pet nao mostra a tela de escolha")
    ok(any(BUNDLE.search(u) for u in reqs), "D: Pet carrega o bundle na hora (2D como sempre)")
    ctx.close()
    # ---- E: atalho explicito
    ctx, p, reqs, erros = abrir(b, "?corpo=3d"); p.wait_for_function("() => window.celia3dInterface && celia3dInterface.pronta", timeout=120000)
    ok(p.locator("#celia-escolha").count() == 0 and not any(MODELO.search(u) for u in reqs), "E: ?corpo=3d vai direto ao 3D sem Live2D")
    ctx.close(); b.close()
print("\n".join(log)); print("\nRESULTADO:", "REPROVADO" if falhas else "APROVADO", f"({len(log) - len(falhas)}/{len(log)})"); sys.exit(1 if falhas else 0)
