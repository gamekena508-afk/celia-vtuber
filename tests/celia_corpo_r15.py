#!/usr/bin/env python3
"""R15 (2026-10-07) — prova no navegador real contra a Célia viva (:12393).

Cobre: legenda do palco fora, painel de emojis dentro de Configuracoes, interruptor 2D/3D no lugar do selo, corpo publicado/lido pelo Pet,
Pet ligado => interface sem corpo (e volta ao desligar). As rotas /bridge/body, /bridge/desktop-pet/* sao SIMULADAS no navegador
(page.route): o teste nunca grava o corpo real nem abre o Pet real. Saidas em /tmp/r15/*.png.
Rodar:  python3 tests/celia_corpo_r15.py   (Chrome real, flags de GPU Vulkan, como o resto da bancada)
"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("CELIA_URL", "http://127.0.0.1:12393")
OUT = "/tmp/r15"; os.makedirs(OUT, exist_ok=True)
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
res = []

def ok(nome, cond, extra=""):
    res.append((nome, bool(cond)))
    print(("PASS " if cond else "FAIL ") + nome + (("  :: " + str(extra)) if extra != "" else ""))

class Simulado:
    """Estado simulado do servidor para as 3 rotas (nada chega ao servidor real)."""
    def __init__(self):
        self.corpo = None; self.posts = []; self.abertos = 0; self.pet_enabled = None; self.pet_state_404 = False
    def instalar(self, ctx):
        def body(route):
            r = route.request
            if r.method == "POST":
                j = json.loads(r.post_data or "{}"); self.posts.append(j); self.corpo = j.get("corpo")
                return route.fulfill(status=200, content_type="application/json", body=json.dumps({"corpo": self.corpo}))
            return route.fulfill(status=200, content_type="application/json", body=json.dumps({"corpo": self.corpo}))
        def abrir(route):
            self.abertos += 1
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"ok": True, "action": "desktop_pet_launch_requested"}))
        def estado(route):
            if self.pet_state_404: return route.fulfill(status=404, body="nope")
            route.fulfill(status=200, content_type="application/json", body=json.dumps({"enabled": self.pet_enabled}))
        ctx.route("**/bridge/body", body); ctx.route("**/bridge/desktop-pet/open", abrir); ctx.route("**/bridge/desktop-pet/state", estado)

def nova(pw, sim, w=1440, h=900):
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    ctx = b.new_context(viewport={"width": w, "height": h}); sim.instalar(ctx)
    return b, ctx, ctx.new_page()

def pronta3d(p):
    p.wait_for_function("() => window.celia3dInterface && celia3dInterface.pronta", timeout=120000); p.wait_for_timeout(3500)

with sync_playwright() as pw:
    # ---------- A: 3D pelo cartao, interruptor, legenda, painel ----------
    sim = Simulado(); b, ctx, p = nova(pw, sim)
    erros = []; p.on("pageerror", lambda e: erros.append(str(e)))
    p.goto(BASE + "/?prova3d=1&publicarcorpo=1", wait_until="load")
    p.wait_for_selector("#celia-escolha button[data-corpo='3d']"); p.click("#celia-escolha button[data-corpo='3d']")
    pronta3d(p)
    ok("A1 cartao 3D publicou {corpo:3d} (gesto humano simulado)", sim.posts == [{"corpo": "3d"}], sim.posts)
    p.wait_for_selector("#celia-corpo-interruptor", timeout=15000); p.wait_for_timeout(600)
    sw = p.evaluate("""() => { const b=document.getElementById('celia-corpo-interruptor'); const r=b.getBoundingClientRect(); const cs=getComputedStyle(b);
      const selo=[...document.querySelectorAll('body *')].find(e=>e.children.length===0 && /^(idle|thinking\\/speaking|listening|speaking)$/i.test(e.textContent.trim()));
      return {x:r.x,y:r.y,w:r.width,h:r.height,checked:b.getAttribute('aria-checked'),bg:cs.backgroundColor,role:b.getAttribute('role'),
              seloOculto: selo ? getComputedStyle(selo.closest('div')).visibility : 'sem-selo'} }""")
    ok("A2 interruptor no lugar do selo (x~456,y~804,110x30)", abs(sw["x"] - 456) <= 3 and abs(sw["y"] - 804) <= 3 and abs(sw["w"] - 110) <= 3 and abs(sw["h"] - 30) <= 3, sw)
    ok("A3 3D => aria-checked=true e VERDE", sw["checked"] == "true" and sw["bg"] == "rgb(47, 170, 84)", sw["bg"])
    ok("A4 selo antigo oculto (visibility:hidden), role=switch", sw["seloOculto"] == "hidden" and sw["role"] == "switch", sw)
    # legenda: cria texto 'Thinking...' como o bundle faz; precisa seguir invisivel
    leg = p.evaluate("""() => { const w=document.createTreeWalker(document.getElementById('root'),NodeFilter.SHOW_TEXT); const out=[];
      for(let n=w.nextNode();n;n=w.nextNode()){ const t=n.textContent.trim(); if(t==='Connection established'||t==='Thinking...'){ let el=n.parentElement; out.push([t,getComputedStyle(el).visibility, el.closest('[style*="visibility"]')?1:0]); } } return out; }""")
    ok("A5 legenda do palco (placeholder/Thinking...) invisivel", leg and all(v == "hidden" or f == 1 for _, v, f in leg), leg)
    ok("A6 #celia-debug-flow NAO esta solto no body", p.evaluate("() => !document.querySelector('body > #celia-debug-flow')"))
    knob = p.evaluate("() => getComputedStyle(document.querySelector('#celia-corpo-interruptor .botao'), '::after').content")
    ok("A9 o botao branco traz o modo atual escrito (3D)", knob == '"3D"', knob)
    p.screenshot(path=OUT + "/A_3d_interruptor.png")
    # Configuracoes > Runtime tem o fluxo
    p.mouse.click(35, 58); p.wait_for_selector('[role="dialog"] [role="tab"][data-value="agent"]', timeout=15000)
    p.click('[role="dialog"] [role="tab"][data-value="agent"]'); p.wait_for_timeout(2500)
    cfg = p.evaluate("""() => { const e=document.querySelector('[role=dialog] #celia-debug-flow'); const v=e && e.getBoundingClientRect(); return e?{texto:e.textContent.slice(0,120),w:v.width,h:v.height,visivel:v.width>0&&v.height>0}:null }""")
    ok("A7 Configuracoes > Runtime mostra o fluxo (cohost/brain...)", cfg and cfg["visivel"] and ("cohost" in cfg["texto"] or "Célia" in cfg["texto"] or "debug" in cfg["texto"].lower()), cfg)
    p.screenshot(path=OUT + "/A_config_runtime.png")
    p.keyboard.press("Escape"); p.wait_for_timeout(500)
    ok("A8 sem erros de pagina", not erros, erros[:2])

    # ---------- C: Pet ligado => interface sem corpo; Pet desligado => volta ----------
    sim.pet_enabled = None
    r = p.evaluate("() => celiaBrowserPet.setMode('pet')")
    p.wait_for_timeout(900)
    c1 = p.evaluate("() => ({q:getComputedStyle(document.getElementById('celia-3d-quadro')).display, m:getComputedStyle(document.getElementById('celia-3d-menu')).display, ativo:celia3dInterface.petAtivo})")
    ok("C1 Pet ligado: iframe 3D e menu da interface ocultos", r.get("ok") and c1["q"] == "none" and c1["m"] == "none" and c1["ativo"] is True, c1)
    p.screenshot(path=OUT + "/C_pet_ligado_interface_vazia.png")
    sim.pet_enabled = True; p.wait_for_timeout(2600)
    ok("C2 enabled=true visto: continua oculto", p.evaluate("() => getComputedStyle(document.getElementById('celia-3d-quadro')).display") == "none")
    sim.pet_enabled = False; p.wait_for_timeout(4800)
    c3 = p.evaluate("() => ({q:getComputedStyle(document.getElementById('celia-3d-quadro')).display, m:getComputedStyle(document.getElementById('celia-3d-menu')).display, modo:celiaBrowserPet.state().mode})")
    ok("C3 Pet fechado (enabled=false apos true): interface volta com o 3D", c3["q"] != "none" and c3["m"] != "none" and c3["modo"] == "window", c3)
    p.wait_for_timeout(1500); p.screenshot(path=OUT + "/C_pet_desligado_interface_volta.png")
    sim.pet_state_404 = True
    p.evaluate("() => celiaBrowserPet.setMode('pet')"); p.wait_for_timeout(5000)
    ok("C4 rota de estado ausente (404): nada muda sozinho (comportamento antigo)", p.evaluate("() => celia3dInterface.petAtivo") is True)
    p.evaluate("() => celiaBrowserPet.setMode('window')"); p.wait_for_timeout(900)
    ok("C5 desligar pelo menu volta a interface", p.evaluate("() => celia3dInterface.petAtivo") is False)
    sim.pet_state_404 = False

    # ---------- B: clicar no interruptor => grava 2d e recarrega em 2D ----------
    sim.posts.clear()
    p.click("#celia-corpo-interruptor"); p.wait_for_timeout(120)
    meio = p.evaluate("() => document.getElementById('celia-corpo-interruptor').getAttribute('aria-checked')")
    ok("B1 animacao: aria-checked vira false ja no clique", meio == "false", meio)
    p.wait_for_url("**corpo=2d**", timeout=15000); p.wait_for_load_state("load")
    p.wait_for_selector("#celia-corpo-interruptor", timeout=30000); p.wait_for_timeout(5000)
    ok("B2 clique publicou {corpo:2d}", {"corpo": "2d"} in sim.posts, sim.posts)
    s2 = p.evaluate("""() => { const b=document.getElementById('celia-corpo-interruptor'); return {checked:b.getAttribute('aria-checked'), bg:getComputedStyle(b).backgroundColor, corpo:window.__celiaCorpo, quadro3d:!!document.getElementById('celia-3d-quadro'),
        live2d:!!document.getElementById('live2d-internal-wrapper')} }""")
    ok("B3 2D => VERMELHO, sem iframe 3D, Live2D presente", s2["checked"] == "false" and s2["bg"] == "rgb(214, 64, 63)" and s2["corpo"] == "2d" and not s2["quadro3d"] and s2["live2d"], s2)
    p.screenshot(path=OUT + "/B_2d_interruptor_vermelho.png")
    ok("B4 sem painel solto nem legenda visivel no 2D tambem", p.evaluate("() => !document.querySelector('body > #celia-debug-flow')"))
    b.close()

    # ---------- guarda de automacao: sem ?publicarcorpo nao publica ----------
    sim = Simulado(); b, ctx, p = nova(pw, sim)
    p.goto(BASE + "/?prova3d=1", wait_until="load")
    p.wait_for_selector("#celia-escolha button[data-corpo='3d']"); p.click("#celia-escolha button[data-corpo='3d']"); pronta3d(p)
    ok("G1 automacao (webdriver) sem publicarcorpo NAO publica", sim.posts == [], sim.posts)
    b.close()

    # ---------- D: Pet segue o corpo publicado ----------
    for corpo in ("3d", "2d", None):
        sim = Simulado(); sim.corpo = corpo; b, ctx, p = nova(pw, sim, 520, 720)
        p.goto(BASE + "/?palco=1&pet=1&fundo=cena&prova3d=1", wait_until="load"); p.wait_for_timeout(9000)
        d = p.evaluate("""() => ({corpo:window.__celiaCorpo, quadro3d:!!document.getElementById('celia-3d-quadro'), larg3d:(document.getElementById('celia-3d-quadro')||{getBoundingClientRect(){return {width:0}}}).getBoundingClientRect().width, menu:!!document.getElementById('celia-3d-menu'), sw:!!document.getElementById('celia-corpo-interruptor'),
            live2d:!!document.getElementById('live2d-internal-wrapper')})""")
        esperado = corpo or "2d"
        ok(f"D1 Pet com corpo publicado={corpo}: roda {esperado}", d["corpo"] == esperado and d["quadro3d"] == (esperado == "3d") and not d["menu"] and not d["sw"], d)
        if esperado == "3d": ok("D3 Pet 3D: o quadro ocupa a janela (nao 0x0)", d["larg3d"] >= 500, d["larg3d"])
        p.screenshot(path=OUT + f"/D_pet_{corpo}.png")
        if corpo == "3d":
            sim.corpo = "2d"; p.wait_for_function("() => window.__celiaCorpo === '2d'", timeout=15000)
            ok("D2 Pet recarrega quando a escolha muda 3d->2d", p.evaluate("() => window.__celiaCorpo") == "2d")
        b.close()

bad = [n for n, c in res if not c]
print(f"\n{len(res) - len(bad)}/{len(res)} passaram")
sys.exit(1 if bad else 0)
