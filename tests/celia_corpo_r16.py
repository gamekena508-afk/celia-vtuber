#!/usr/bin/env python3
"""R16 (2026-10-07) — prova no navegador real contra a Célia viva (:12393).

Cobre: tela de escolha sem FPS nem caixas de debug; botao do interruptor escuro; microfone azul escuro (desligado) / azul claro (ligado);
2a linha de emocao na caixa THINKING. O estado "ligado" do microfone e SIMULADO trocando o icone (nunca liga o microfone de verdade).
Saidas em /tmp/r16/*.png.
"""
import json, os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("CELIA_URL", "http://127.0.0.1:12393")
OUT = "/tmp/r16"; os.makedirs(OUT, exist_ok=True)
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
res = []

def ok(nome, cond, extra=""):
    res.append((nome, bool(cond)))
    print(("PASS " if cond else "FAIL ") + nome + (("  :: " + str(extra)) if extra != "" else ""))

# icone do microfone LIGADO (BsMicFill): sem a barra diagonal
MIC_LIGADO = '<svg stroke="currentColor" fill="currentColor" stroke-width="0" viewBox="0 0 16 16" height="1em" width="1em" xmlns="http://www.w3.org/2000/svg"><path d="M5 3a3 3 0 0 1 6 0v5a3 3 0 0 1-6 0z"></path><path d="M3.5 6.5A.5.5 0 0 1 4 7v1a4 4 0 0 0 8 0V7a.5.5 0 0 1 1 0v1a5 5 0 0 1-4.5 4.975V15h3a.5.5 0 0 1 0 1h-7a.5.5 0 0 1 0-1h3v-2.025A5 5 0 0 1 3 8V7a.5.5 0 0 1 .5-.5"></path></svg>'

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    ctx = b.new_context(viewport={"width": 1440, "height": 900})
    ctx.route("**/bridge/body", lambda r: r.fulfill(status=200, content_type="application/json", body=json.dumps({"corpo": None})))
    p = ctx.new_page(); erros = []; p.on("pageerror", lambda e: erros.append(str(e)))

    # ---------- tela de escolha ----------
    p.goto(BASE + "/?prova3d=1", wait_until="load")
    p.wait_for_selector("#celia-escolha button[data-corpo='3d']"); p.wait_for_timeout(5000)
    esc = p.evaluate("""() => { const vis = (sel) => { const e=document.querySelector(sel); if(!e) return 'ausente'; const cs=getComputedStyle(e); const r=e.getBoundingClientRect(); return (cs.display==='none'||r.width===0)?'oculto':'VISIVEL'; };
        return { boxes: vis('#celia-debug-boxes'), hud: vis('#celia-body-hud'), fps: vis('#celia-live-fps'), flow: vis('#celia-debug-flow'), corpo: document.documentElement.dataset.celiaCorpo || null } }""")
    ok("E1 tela de escolha: sem caixas THINKING/PROCESSOS", esc["boxes"] in ("oculto", "ausente"), esc)
    ok("E2 tela de escolha: sem FPS", esc["hud"] in ("oculto", "ausente") and esc["fps"] in ("oculto", "ausente"), esc)
    ok("E3 tela de escolha: nenhum painel de fluxo solto", esc["flow"] in ("oculto", "ausente"), esc)
    p.screenshot(path=OUT + "/E_tela_de_escolha.png")

    # ---------- depois de escolher 3D ----------
    p.click("#celia-escolha button[data-corpo='3d']")
    p.wait_for_function("() => window.celia3dInterface && celia3dInterface.pronta", timeout=120000); p.wait_for_timeout(4000)
    dep = p.evaluate("""() => { const vis = (sel) => { const e=document.querySelector(sel); if(!e) return 'ausente'; const cs=getComputedStyle(e); const r=e.getBoundingClientRect(); return (cs.display==='none'||r.width===0)?'oculto':'VISIVEL'; };
        return { boxes: vis('#celia-debug-boxes'), fps: vis('#celia-live-fps'), corpo: document.documentElement.dataset.celiaCorpo } }""")
    ok("E4 depois de escolher: caixas e FPS voltam", dep["boxes"] == "VISIVEL" and dep["fps"] == "VISIVEL" and dep["corpo"] == "3d", dep)

    # FPS nao pode cobrir o botao Fechar (o clique na escolha demorou >3 s: a barra de titulo monta depois das remedidas antigas)
    p.wait_for_timeout(1500)
    h = p.evaluate("""() => { const r=document.getElementById('celia-body-hud').getBoundingClientRect(); return {top:Math.round(r.top), bottom:Math.round(r.bottom), left:Math.round(r.left), right:Math.round(r.right)} }""")
    ok("H1 FPS abaixo da barra de titulo (nao cobre Fechar: y>=33)", h["top"] >= 33, h)

    # ---------- interruptor com botao escuro ----------
    p.wait_for_selector("#celia-corpo-interruptor", timeout=15000); p.wait_for_timeout(600)
    kn = p.evaluate("""() => { const k=document.querySelector('#celia-corpo-interruptor .botao'); const cs=getComputedStyle(k); return {bg:cs.backgroundColor, cor:cs.color} }""")
    ok("S1 botao do interruptor ESCURO (nao branco) com texto claro", kn["bg"] == "rgb(23, 24, 29)" and kn["cor"] == "rgb(255, 255, 255)", kn)

    # ---------- microfone ----------
    m = p.evaluate("""() => { const b=document.querySelector('button[data-celia-mic]'); if(!b) return null; const r=b.getBoundingClientRect(); return {estado:b.dataset.celiaMic, bg:getComputedStyle(b).backgroundColor, x:Math.round(r.x), y:Math.round(r.y)} }""")
    ok("M1 microfone desligado = azul ESCURO (e nao e mais vermelho)", m and m["estado"] == "off" and m["bg"] == "rgb(18, 48, 110)", m)
    ok("M2 so o microfone muda: 'Raise hand' continua amarelo", p.evaluate("() => getComputedStyle(document.querySelector('button[aria-label=\"Raise hand\"]')).backgroundColor") == "rgb(234, 179, 8)")
    p.screenshot(path=OUT + "/M_mic_desligado_azul_escuro.png")
    p.evaluate("(svg) => { const b=document.querySelector('button[data-celia-mic]'); b.innerHTML = svg; }", MIC_LIGADO); p.wait_for_timeout(900)
    m2 = p.evaluate("""() => { const b=document.querySelector('button[data-celia-mic]'); return {estado:b.dataset.celiaMic, bg:getComputedStyle(b).backgroundColor} }""")
    ok("M3 microfone LIGADO (icone simulado) = azul CLARO", m2["estado"] == "on" and m2["bg"] == "rgb(88, 183, 255)", m2)
    p.screenshot(path=OUT + "/M_mic_ligado_azul_claro.png")

    # ---------- emocao na caixa THINKING ----------
    p.evaluate("() => window.dispatchEvent(new CustomEvent('celia-emotion', {detail:{emotion:'happy'}}))"); p.wait_for_timeout(300)
    linhas = p.evaluate("""() => { const cx=document.getElementById('celia-debug-boxes'); const a=cx.children[0]; return [...a.children].map(c=>c.textContent) }""")
    ok("T1 caixa THINKING tem 2a linha com a emocao atual (happy)", len(linhas) == 3 and "parada" in linhas[1] + "".join(linhas) and any("emoção: happy" in l for l in linhas), linhas)
    p.evaluate("() => window.dispatchEvent(new CustomEvent('celia-emotion', {detail:{emotion:'sad'}}))"); p.wait_for_timeout(300)
    ok("T2 a linha acompanha a emocao seguinte (sad)", p.evaluate("() => document.getElementById('celia-debug-boxes').children[0].textContent").count("emoção: sad") == 1)
    p.screenshot(path=OUT + "/T_thinking_com_emocao.png")
    ok("X1 sem erros de pagina", not erros, erros[:2])
    b.close()

bad = [n for n, c in res if not c]
print(f"\n{len(res) - len(bad)}/{len(res)} passaram")
sys.exit(1 if bad else 0)
