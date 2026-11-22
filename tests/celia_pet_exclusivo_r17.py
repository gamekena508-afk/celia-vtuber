#!/usr/bin/env python3
"""R17 (2026-10-07) — Pet e interface EXCLUSIVOS, provado no navegador real contra a Célia viva (:12393).

Regras provadas: Pet vivo => interface sem corpo (2D e 3D) e V em "Pet Mode"; "Live Mode" na interface => o Pet recebe o pedido de fechar e a
interface volta quando ele some; Pet fechado pela janela dele => interface volta; o Pet (?pet=1) so se fecha por um "janela" MAIS NOVO que a
sua carga. As rotas /bridge/body, /bridge/mode e /bridge/desktop-pet/* sao SIMULADAS no navegador (page.route): nada toca o servidor real nem o
seu Pet. Saidas em /tmp/r17/*.png.
"""
import json, os, sys, time
from playwright.sync_api import sync_playwright

BASE = os.environ.get("CELIA_URL", "http://127.0.0.1:12393")
OUT = "/tmp/r17"; os.makedirs(OUT, exist_ok=True)
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
res = []

def ok(nome, cond, extra=""):
    res.append((nome, bool(cond)))
    print(("PASS " if cond else "FAIL ") + nome + (("  :: " + str(extra)) if extra != "" else ""))

class Servidor:
    def __init__(self):
        self.alive = False; self.modo = None; self.em = None; self.posts_modo = []; self.abertos = 0; self.corpo = None
    def instalar(self, ctx):
        j = lambda r, o, st=200: r.fulfill(status=st, content_type="application/json", body=json.dumps(o))
        def estado(r): j(r, {"enabled": self.alive, "alive": self.alive})
        def modo(r):
            if r.request.method == "POST":
                m = json.loads(r.request.post_data or "{}").get("modo"); self.modo = m; self.em = int(time.time() * 1000); self.posts_modo.append(m)
                return j(r, {"modo": m, "em": self.em})
            j(r, {"modo": self.modo, "em": self.em})
        def abrir(r): self.abertos += 1; j(r, {"ok": True, "action": "desktop_pet_launch_requested"})
        def corpo(r): j(r, {"corpo": self.corpo})
        ctx.route("**/bridge/desktop-pet/state", estado); ctx.route("**/bridge/mode", modo)
        ctx.route("**/bridge/desktop-pet/open", abrir); ctx.route("**/bridge/body", corpo)

def nova(pw, srv, w=1440, h=900, init=None):
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    ctx = b.new_context(viewport={"width": w, "height": h}); srv.instalar(ctx)
    if init: ctx.add_init_script(init)
    return b, ctx, ctx.new_page()

SNAP = """() => { const q=document.getElementById('celia-3d-quadro'); const l2=document.getElementById('live2d-internal-wrapper');
  const vis = (e) => !!e && getComputedStyle(e).display !== 'none' && e.getBoundingClientRect().width > 0;
  return { modo: celiaBrowserPet.state().mode, launch: celiaBrowserPet.state().desktopPetLaunch, alive: celiaBrowserPet.state().petAlive,
           q3d: q ? vis(q) : null, menu3d: (document.getElementById('celia-3d-menu')||{}).style ? getComputedStyle(document.getElementById('celia-3d-menu')).display : null,
           live2d: l2 ? vis(l2) : null } }"""
MENU = """() => [...document.querySelectorAll('[role="menuitemradio"]')].filter(e=>/^(Live|Pet) Mode$/.test(e.textContent.trim())).map(e => ({t:e.textContent.trim(), checked:e.getAttribute('aria-checked'), v: !(e.querySelector('[data-part="item-indicator"]')||{hidden:true}).hidden}))"""

def abrir_menu(p):
    p.click('button[aria-label="Mode Menu"]'); p.wait_for_timeout(700)

with sync_playwright() as pw:
    # ---------- A: 3D, Pet aparece sozinho (nao lancado por esta pagina) ----------
    srv = Servidor(); b, ctx, p = nova(pw, srv)
    erros = []; p.on("pageerror", lambda e: erros.append(str(e)))
    p.goto(BASE + "/?corpo=3d&prova3d=1", wait_until="load"); p.wait_for_function("() => window.celia3dInterface && celia3dInterface.pronta", timeout=120000); p.wait_for_timeout(3000)
    a0 = p.evaluate(SNAP)
    ok("A1 sem Pet: interface mostra o corpo 3D e o menu de acoes", a0["modo"] == "window" and a0["q3d"] and a0["menu3d"] != "none", a0)
    abrir_menu(p); m0 = p.evaluate(MENU)
    ok("A2 sem Pet: V em 'Live Mode' e nao em 'Pet Mode'", {x["t"]: x["v"] for x in m0} == {"Live Mode": True, "Pet Mode": False}, m0)
    p.screenshot(path=OUT + "/A_sem_pet_V_em_live.png"); p.keyboard.press("Escape"); p.wait_for_timeout(400)
    srv.alive = True; p.wait_for_timeout(3800)       # Pet aparece (restaurado pelo ligar.sh / aberto a mao)
    a1 = p.evaluate(SNAP)
    ok("A3 Pet apareceu sozinho: interface ADOTA (modo pet) e esconde o 3D e o menu", a1["modo"] == "pet" and a1["q3d"] is False and a1["menu3d"] == "none" and a1["alive"] is True, a1)
    abrir_menu(p); m1 = p.evaluate(MENU)
    ok("A4 Pet vivo: V em 'Pet Mode' e NAO em 'Live Mode'", {x["t"]: x["v"] for x in m1} == {"Live Mode": False, "Pet Mode": True}, m1)
    p.screenshot(path=OUT + "/A_pet_vivo_V_em_pet.png")

    # ---------- B: 'Live Mode' => o Pet recebe o pedido de fechar; interface volta quando ele some ----------
    p.click('[role="menuitemradio"]:has-text("Live Mode")'); p.wait_for_timeout(600)
    ok("B1 'Live Mode' publicou modo=janela (o Pet le e se fecha)", srv.posts_modo[-1:] == ["janela"], srv.posts_modo)
    p.wait_for_timeout(2600)   # o Pet AINDA vivo: a interface nao pode readota-lo
    b1 = p.evaluate(SNAP)
    ok("B2 enquanto o Pet ainda fecha, a interface NAO o readota", b1["modo"] == "window", b1)
    srv.alive = False; p.wait_for_timeout(3000)
    b2 = p.evaluate(SNAP)
    ok("B3 Pet sumiu: interface renderiza o corpo e o menu de novo", b2["modo"] == "window" and b2["q3d"] and b2["menu3d"] != "none", b2)
    p.screenshot(path=OUT + "/B_live_mode_pet_fechado.png")

    # ---------- C: 'Pet Mode' pela interface; depois o Pet e fechado pela janela dele ----------
    abrir_menu(p); p.click('[role="menuitemradio"]:has-text("Pet Mode")'); p.wait_for_timeout(900)
    c0 = p.evaluate(SNAP)
    ok("C1 'Pet Mode': pediu modo=pet, abriu o launcher e escondeu o corpo", srv.posts_modo[-1] == "pet" and srv.abertos == 1 and c0["modo"] == "pet" and c0["q3d"] is False, [srv.posts_modo, srv.abertos, c0])
    srv.alive = True; p.wait_for_timeout(2600)   # o Pet subiu
    srv.alive = False; p.wait_for_timeout(3200)  # o Pai fechou no x
    c1 = p.evaluate(SNAP)
    ok("C2 Pet fechado pela janela dele (x): interface volta sozinha", c1["modo"] == "window" and c1["q3d"] is True, c1)
    ok("C3 sem erros de pagina", not erros, erros[:2]); b.close()

    # ---------- D: pagina ABRE com o Pet ja na tela (o caso do Pai agora), em 2D ----------
    srv = Servidor(); srv.alive = True; b, ctx, p = nova(pw, srv)
    p.goto(BASE + "/?corpo=2d", wait_until="load"); p.wait_for_timeout(7000)
    d0 = p.evaluate(SNAP)
    ok("D1 abre com Pet vivo: modo pet adotado e Live2D da interface ESCONDIDO", d0["modo"] == "pet" and d0["live2d"] is False, d0)
    abrir_menu(p); m = p.evaluate(MENU)
    ok("D2 abre com Pet vivo: V em 'Pet Mode'", {x["t"]: x["v"] for x in m}.get("Pet Mode") is True and {x["t"]: x["v"] for x in m}.get("Live Mode") is False, m)
    p.screenshot(path=OUT + "/D_abre_com_pet_vivo_2d.png"); b.close()

    # ---------- E: o Pet (?pet=1) fecha a si mesmo so por um 'janela' mais novo que a sua carga ----------
    STUB = "window.__fechou = 0; window.celiaPetNative = { close: () => { window.__fechou += 1; return Promise.resolve(); } };"
    srv = Servidor(); srv.corpo = "2d"; srv.modo = "janela"; srv.em = int(time.time() * 1000) - 600000   # 'janela' ANTIGO (10 min atras)
    b, ctx, p = nova(pw, srv, 520, 720, STUB)
    p.goto(BASE + "/?palco=1&pet=1&fundo=cena", wait_until="load"); p.wait_for_timeout(6500)
    ok("E1 Pet NAO se fecha por um 'janela' antigo (anterior a sua carga)", p.evaluate("() => window.__fechou") == 0)
    srv.modo = "janela"; srv.em = int(time.time() * 1000) + 500; p.wait_for_timeout(3500)
    ok("E2 Pet se fecha (celiaPetNative.close) por um 'janela' novo", p.evaluate("() => window.__fechou") >= 1, p.evaluate("() => window.__fechou"))
    srv.modo = "pet"; srv.em = int(time.time() * 1000) + 5000
    b.close()

bad = [n for n, c in res if not c]
print(f"\n{len(res) - len(bad)}/{len(res)} passaram")
sys.exit(1 if bad else 0)
