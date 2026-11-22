#!/usr/bin/env python3
"""Roteiro do teste REAL (Chrome) da curadoria V / ? / X da bancada 3D (rodada 9). Rodado por tests/test_celia_3d_curadoria_viva.py; origem: p131_curadoria_ui.py.
CODEX.md secao 14: muda cada controle, confere o export na hora, recarrega, baixa o JSON, copia a lista de ?, migra a v1 e confere a FALHA VISIVEL.
SABOTAGEM=1 faz o armazenamento gravar em silencio nada: os testes de persistencia TEM de falhar."""
import json, os, sys
from playwright.sync_api import sync_playwright
GPU = ["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"]
URL = "http://127.0.0.1:12393/celia-3d-bancada.html?demo=0&prova=1"; ok = []; falhas = []
def conf(nome, cond, extra=""):
    (ok if cond else falhas).append(nome); print(("PASSOU " if cond else "FALHOU ") + nome + ((" | " + str(extra)) if extra and not cond else ""))
with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path="/usr/bin/google-chrome-stable", args=GPU)
    ctx = b.new_context(viewport={"width": 1100, "height": 900}, accept_downloads=True); ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="http://127.0.0.1:12393")
    if os.environ.get("SABOTAGEM"): ctx.add_init_script("Storage.prototype.setItem = function () {};")   # grava em silencio NADA: os testes de persistencia TEM de falhar
    p = ctx.new_page(); er = []; p.on("pageerror", lambda e: er.append(str(e)[:200]))
    def abrir():
        p.goto(URL, wait_until="load"); p.wait_for_function("() => window.celia3dBancada && celia3dBancada.estado === 'ok'", timeout=90000); p.wait_for_timeout(600)
    abrir()
    st = lambda: p.evaluate("() => document.getElementById('curadoria-status').textContent")
    lista = lambda: p.evaluate("() => document.getElementById('curadoria-lista').value")
    chip = lambda k: p.locator(f'.chip[data-chave="{k}"]')
    nmarcas = lambda k: chip(k).locator(".marcas button").count()
    pai = lambda k: p.evaluate("(k) => document.querySelector('.chip[data-chave=\"' + k + '\"]').parentElement.id", k)
    # ---- controle negativo: nada marcado ----
    conf("negativo: sem marca, cada acao mostra 3 opcoes", nmarcas("acenar") == 3 and "nada marcado ainda" in st(), st())
    pai_acenar = pai("acenar"); chaves = p.evaluate("() => [...document.querySelectorAll('.chip')].map(c => c.dataset.chave)"); conf("ha dezenas de botoes", len(chaves) >= 30, len(chaves))
    # ---- V: fica no lugar, so a marca ----
    chip("acenar").locator('button[data-marca="ok"]').click()
    conf("V: fica no mesmo grupo", pai("acenar") == pai_acenar); conf("V: so a marca (1 botao)", nmarcas("acenar") == 1 and chip("acenar").locator(".marcas button").inner_text() == "✓")
    conf("V: export na hora", "APROVADAS (✓) (1):\nacenar" in lista(), lista()[:200])
    # ---- ?: fica no lugar, entra na lista de melhorar, copiar habilitado ----
    chip("sim").locator('button[data-marca="melhorar"]').click()
    conf("?: fica no grupo e mostra a marca ?", pai("sim") in ("g-gesto", "g-acao") and nmarcas("sim") == 1 and chip("sim").locator(".marcas button").inner_text() == "?")
    conf("?: aparece em PARA MELHORAR", "PARA MELHORAR (?) (1):\nsim" in lista(), lista()[:160]); conf("?: botao copiar habilitado", p.evaluate("() => !document.getElementById('curadoria-copiar').disabled"))
    # ---- X: vai para a caixa de descartadas ----
    chip("paz").locator('button[data-marca="descartada"]').click()
    conf("X: foi para a caixa Descartadas", pai("paz") == "g-descartadas"); conf("X: titulo da caixa conta 1", "Descartadas (1)" in p.evaluate("() => document.getElementById('g-descartadas').querySelector('b').textContent"))
    conf("X: ainda tem a marca X (para restaurar)", nmarcas("paz") == 1 and chip("paz").locator(".marcas button").inner_text() == "✗")
    conf("status conta tudo", "✓ 1" in st() and "? 1" in st() and "✗ 1" in st(), st())
    # ---- persistencia: recarregar ----
    salvo = p.evaluate("() => JSON.parse(localStorage.getItem('celia3d-bancada:curadoria:v2') || 'null')") or {}
    conf("storage v2 tem a estrutura certa", salvo.get("versao") == 2 and salvo["marcas"] == {"acenar": "ok", "sim": "melhorar", "paz": "descartada"} and isinstance(salvo.get("atualizado"), str), salvo)
    abrir()
    conf("recarregar: V restaurado", nmarcas("acenar") == 1 and chip("acenar").get_attribute("data-estado") == "ok")
    conf("recarregar: ? restaurado", chip("sim").get_attribute("data-estado") == "melhorar"); conf("recarregar: X na caixa de descartadas", pai("paz") == "g-descartadas")
    conf("recarregar: status diz restaurada/salvo", "lista restaurada" in st() or "salvo" in st(), st())
    # ---- desmarcar V -> 3 opcoes voltam; restaurar X -> volta ao lugar de origem, 3 opcoes ----
    chip("acenar").locator(".marcas button").click(); conf("desmarcar V: 3 opcoes voltam", nmarcas("acenar") == 3 and chip("acenar").get_attribute("data-estado") == "pendente")
    chip("paz").locator(".marcas button").click(); conf("restaurar X: volta ao grupo de origem", pai("paz") == "g-gesto" and nmarcas("paz") == 3, pai("paz"))
    ordem = p.evaluate("() => [...document.querySelectorAll('#g-gesto > .chip')].map(c => c.dataset.chave)"); orig = [k for k in chaves if k in ordem]
    conf("restaurar X: respeita a ordem original", ordem == orig, (ordem[:6], orig[:6]))
    # ---- baixar JSON ----
    with p.expect_download() as dl: p.click("#curadoria-baixar")
    d = json.load(open(dl.value.path())); conf("download: estrutura", d["versao"] == 2 and d["melhorar"] == ["sim"] and d["aprovadas"] == [] and d["descartadas"] == [] and isinstance(d["sem_marca"], list) and len(d["sem_marca"]) >= 28, {k: (v if k != "sem_marca" else len(v)) for k, v in d.items()})
    # ---- copiar lista de ? ----
    p.click("#curadoria-copiar"); p.wait_for_timeout(200); clip = p.evaluate("() => navigator.clipboard.readText()")
    conf("copiar: area de transferencia tem a lista de ?", "sim" in clip and "marcadas com ?" in clip, clip[:120])
    # ---- migracao v1 ----
    p.evaluate("() => { localStorage.removeItem('celia3d-bancada:curadoria:v2'); localStorage.setItem('celia3d-bancada:curadoria:v1', JSON.stringify({ versao: 1, descartadas: ['sim', 'andar'], atualizado: '2026-10-06T12:00:00.000Z' })); }"); abrir()
    conf("migracao v1: descartadas foram para a caixa", pai("sim") == "g-descartadas" and pai("andar") == "g-descartadas")
    conf("migracao v1: v2 gravada e v1 preservada", p.evaluate("() => JSON.parse(localStorage.getItem('celia3d-bancada:curadoria:v2')).marcas.andar === 'descartada' && localStorage.getItem('celia3d-bancada:curadoria:v1') !== null"))
    # ---- falha visivel: gravacao lanca erro ----
    p.evaluate("() => { Storage.prototype.setItem = function () { throw new Error('cota estourada'); }; }")
    chip("giro").locator('button[data-marca="ok"]').click()
    conf("falha de gravacao aparece na tela", p.evaluate("() => document.getElementById('curadoria-status').dataset.s") == "erro" and "NAO salvou" in st(), st())
    # ---- falha visivel: JSON salvo corrompido ----
    p2 = ctx.new_page(); p2.goto(URL, wait_until="load"); p2.evaluate("() => localStorage.setItem('celia3d-bancada:curadoria:v2', '{quebrado')"); p2.close()
    abrir(); conf("JSON corrompido: avisa e nao quebra", "ilegivel" in st() and p.evaluate("() => document.querySelectorAll('.chip').length") >= 30, st())
    conf("sem erro de pagina", not er, er[:2])
    b.close()
print(f"\n{len(ok)} passaram, {len(falhas)} falharam"); sys.exit(1 if falhas else 0)
