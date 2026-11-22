#!/usr/bin/env python3
"""R18 (2026-10-07) — Configuracoes: texto quebrado consertado, aba TTS com a voz atual, Runtime com acentos. Chrome real contra :12393.
So LE: nao altera opcao, nao clica em Save. Saidas em /tmp/r18/*.png."""
import json, os, sys
from playwright.sync_api import sync_playwright

BASE = os.environ.get("CELIA_URL", "http://127.0.0.1:12393")
OUT = "/tmp/r18"; os.makedirs(OUT, exist_ok=True)
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
res = []

def ok(nome, cond, extra=""):
    res.append((nome, bool(cond)))
    print(("PASS " if cond else "FAIL ") + nome + (("  :: " + str(extra)) if extra != "" else ""))

def aba(p, val):
    p.click(f'[role="dialog"] [role="tab"][data-value="{val}"]'); p.wait_for_timeout(1800)

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    p = b.new_page(viewport={"width": 1440, "height": 900}); erros = []; p.on("pageerror", lambda e: erros.append(str(e)))
    p.goto(BASE + "/?corpo=2d", wait_until="load"); p.wait_for_timeout(6000)
    p.mouse.click(35, 58); p.wait_for_selector('[role="dialog"] [role="tab"]'); p.wait_for_timeout(1500)
    antes = p.evaluate("""() => [...document.querySelectorAll('[role=dialog] input, [role=dialog] select')].map(e => e.type==='checkbox' ? e.checked : e.value)""")

    # General: opcao de idioma
    ops = p.evaluate("() => [...document.querySelectorAll('[role=dialog] select option')].map(o => o.textContent.trim()).filter(Boolean)")
    ok("G1 opcao de idioma chinesa legivel (中文), sem 'ä¸­æ–‡'", "中文" in ops and not any("ä" in o for o in ops), ops)
    p.screenshot(path=OUT + "/G_general.png")

    # TTS
    aba(p, "tts")
    t = p.evaluate("() => { const s=document.getElementById('celia-tts-settings'); return s ? s.innerText : null }")
    ok("T1 aba TTS deixou de ser vazia: 'Voz atual' com preset e seed", t and "Voz atual" in t and "Preset ativo" in t and "Seed" in t and "7314" in t and "Neutra" in t, (t or "")[:160].replace("\n", " | "))
    ok("T2 TTS diz que e somente leitura (nao finge trocar a voz)", t and "Somente leitura" in t)
    p.screenshot(path=OUT + "/T_tts_voz_atual.png")

    # Runtime
    aba(p, "agent")
    r = p.evaluate("() => document.getElementById('celia-runtime-settings').innerText")
    ok("R1 Runtime com acentos corretos", all(x in r for x in ["Histórico recente", "são somente leitura", "não exibem"]) and "Historico" not in r and " sao " not in r, r[:200].replace("\n", " | "))
    ok("R2 Runtime sem o revisor LLM do guardiao (removido 24/10)", "Revisor do guardião (LLM)" not in r, [l for l in r.split("\n") if "guardi" in l.lower()])
    ok("R3 valor booleano acentuado ('não') no fail-open", any(l.strip().startswith("Guardian fail-open") for l in r.split("\n")) and ("não" in r or "sim" in r))
    leg_oculto = p.evaluate("() => document.querySelector('[role=tabpanel][id$=content-agent] > :first-child').hidden")
    ok("R4 painel legado do Agent continua escondido (contrato antigo mantido)", leg_oculto is True)
    p.screenshot(path=OUT + "/R_runtime.png")

    # About
    aba(p, "about")
    ab = p.evaluate("() => document.querySelector('[role=dialog] [role=tabpanel][data-state=open]').innerText")
    ok("A1 copyright legivel '© 2026', sem 'Â'", "©" in ab and "Â" not in ab, [l for l in ab.split("\n") if "2026" in l])
    # repouso: sem mutacao de DOM escrita por esta camada (excluindo o fluxo ao vivo, que atualiza de 1 em 1 s em outra aba)
    n = p.evaluate("""() => new Promise(res => { let c=0; const mo=new MutationObserver(m => { for (const x of m) if (!(x.target.closest && x.target.closest('#celia-debug-flow'))) c++; }); mo.observe(document.querySelector('[role=dialog]'), {childList:true, subtree:true, characterData:true, attributes:true}); setTimeout(() => { mo.disconnect(); res(c) }, 2500) })""")
    ok("A2 em repouso (aba About) 0 mutacoes escritas no dialogo em 2,5 s", n == 0, n)
    p.screenshot(path=OUT + "/A_about.png")

    depois = p.evaluate("""() => [...document.querySelectorAll('[role=dialog] input, [role=dialog] select')].map(e => e.type==='checkbox' ? e.checked : e.value)""")
    ok("N1 NENHUMA opcao foi alterada (valores iguais antes e depois)", antes == depois)
    ok("X1 sem erros de pagina", not erros, erros[:2]); b.close()

bad = [n for n, c in res if not c]
print(f"\n{len(res) - len(bad)}/{len(res)} passaram")
sys.exit(1 if bad else 0)
