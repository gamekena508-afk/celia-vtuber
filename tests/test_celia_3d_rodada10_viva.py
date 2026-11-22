"""Rodada 10 (2026-10-06, Kurt) — EFEITO medido na bancada real (Chrome headless, GPU). Pulado (nao verde) sem servidor :12393 ou Chrome.
Pedido do Pai: cabelo atravessa o corpo (levar golpe, arremessar), bracos dentro do corpo, 'duas maos'. Reguas de 3d/celia/v3/web/ (acervo do dono, fora do repositorio):
p107_ab (cabelo+), p138 (braco no tronco), p139/p140 (cabelo repouso), p142 (mao na pele do rosto), p144 (mao fantasma).
Cada teste leva CONTROLE POSITIVO (guarda desligado em tempo de execucao TEM de reproduzir o defeito) e NEGATIVO (acao sem defeito TEM de ficar quieta)."""
import os
import shutil
import socket
import pytest

CHROME = "/usr/bin/google-chrome-stable"
URL = "http://127.0.0.1:12393/celia-3d-bancada.html?demo=0&pausado=1&prova=1" + os.environ.get("CELIA3D_QS", "")


def _servidor():
    try:
        with socket.create_connection(("127.0.0.1", 12393), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not (_servidor() and os.path.exists(CHROME)), reason="sem servidor :12393 ou sem Chrome")
sync_api = pytest.importorskip("playwright.sync_api")

# mesmo RESET das reguas (p42_cabelo_repouso.py): semente do acaso + estado zerado + gesto aleatorio travado. Sem ele a medida do cabelo oscila 40-80 mm entre rodadas identicas.
RESET = """() => { const c = celia3dBancada, k = c.corpo; let s = 4242; Math.random = () => { s = (s * 16807) % 2147483647; return (s - 1) / 2147483646; };
  Object.assign(k, { t: 0, fase: 1, emocaoT: 99, presetT: 99, presetAte: 6, preset: 'Idle Ativo', presetAnterior: 'Idle Ativo', microEm: 1e9,
    olhar: { x: 0, y: 0, alvoX: 0, alvoY: 0, proxima: 1 }, piscar: { proxima: 2, t: -1, dupla: false }, olharCabeca: null, energiaCorpo: 0, acao: null, fila: [] });
  k.update(0); c.vrm.update(0); c.vrm.springBoneManager.reset(); }"""

# vertices de cabelo (amostra 1 a cada 8) a menos de 8,5 cm do eixo quadril->peito: dentro do tronco sem duvida (raio medido 10,9 a 13 cm)
CABELO_NO_TRONCO = """async ([acao, passo, n, RESETSRC]) => { const T = celia3dBancada, c = T.corpo, TH = await import("three"), V = TH.Vector3, h = T.vrm.humanoid;
 let cab = null; T.vrm.scene.traverse((o) => { if (o.isSkinnedMesh && /HEAD_SHELL/i.test(o.name)) cab = o; }); const A = new V(), B = new V(), p = new V(), ab = new V();
 const dentro = () => { cab.skeleton.update(); h.getNormalizedBoneNode("hips").getWorldPosition(A); h.getNormalizedBoneNode("chest").getWorldPosition(B); ab.copy(B).sub(A); const L2 = ab.lengthSq(); let k = 0;
   for (let i = 0; i < cab.geometry.attributes.position.count; i += 8) { cab.getVertexPosition(i, p).applyMatrix4(cab.matrixWorld); const t = Math.max(0, Math.min(1, p.clone().sub(A).dot(ab) / L2)); if (p.distanceTo(A.clone().addScaledVector(ab, t)) < 0.085) k++; } return k; };
 const sb = T.vrm.springBoneManager; (eval("(" + RESETSRC + ")"))(); c.play("acenar", true); sb.reset(); T.avancar(2.0); const base = dentro();   // repouso assentado: o cabelo parado ja tem vertices a 8,5 cm do eixo (sobre os ombros)
 c.play(acao, true); let pior = 0; for (let i = 0; i < n; i++) { T.avancar(passo); pior = Math.max(pior, dentro()); } return pior - base; }"""


@pytest.fixture(scope="module")
def pagina():
    with sync_api.sync_playwright() as pw:
        b = pw.chromium.launch(executable_path=CHROME, args=["--headless=new", "--use-angle=vulkan", "--enable-features=Vulkan", "--ignore-gpu-blocklist"])
        p = b.new_page(viewport={"width": 900, "height": 700})
        p.goto(URL, wait_until="load")
        p.wait_for_function("() => window.celia3dBancada && celia3dBancada.estado === 'ok'", timeout=90000)
        p.wait_for_timeout(1500)
        yield p
        b.close()


def _cabelo(pagina, acao, barriga):
    pagina.evaluate("(b) => celia3dBancada.corpo.ligarBarriga(b)", barriga)
    return pagina.evaluate(CABELO_NO_TRONCO, [acao, 0.1, 20, RESET])


def test_guarda_da_barriga_liga_por_padrao_e_desliga_em_execucao(pagina):
    assert pagina.evaluate("() => celia3dBancada.corpo.barrigaLigada") is True
    assert pagina.evaluate("() => celia3dBancada.corpo.grupoBarriga.colliders.length") == 4
    assert pagina.evaluate("() => celia3dBancada.corpo.ligarBarriga(false)") is False
    assert pagina.evaluate("() => celia3dBancada.corpo.ligarBarriga(true)") is True


def test_cabelo_nao_atravessa_a_barriga_em_arremessar_e_levar_golpe(pagina):
    # p107: levar golpe 1483 -> 416, arremessar 1593 -> 623, levar golpe 2 1028 -> 424 (vertices de cabelo dentro dos capsulos; +69% a +74% menos)
    for acao in ("kk_arremessar", "kk_golpe"):
        sem = _cabelo(pagina, acao, False)   # CONTROLE POSITIVO: sem o guarda o instrumento TEM de ver o defeito
        com = _cabelo(pagina, acao, True)
        assert sem >= 60, f"{acao}: instrumento nao enxerga o defeito sem guarda ({sem})"
        assert com <= sem * 0.7, f"{acao}: guarda nao reduziu o suficiente (sem={sem}, com={com})"


def test_guarda_da_barriga_nao_mexe_no_cabelo_parado(pagina):
    # p140: cabelo em repouso, ligado x desligado = 4,6 mm no pior ponto (37 juntas). CONTROLE NEGATIVO: ligado x ligado = 0.
    # CONTROLE POSITIVO: capsula gigante (r = 0,5 m) TEM de mover o cabelo (p140: 699 mm). Gesto aleatorio travado: sem ele a medida oscila.
    JS = """async ([modo, RESETSRC]) => { const T = celia3dBancada, c = T.corpo; (eval("(" + RESETSRC + ")"))(); const g = c.grupoBarriga, r0 = g.colliders.map((k) => k.shape.radius);
      if (modo === "gigante") g.colliders.forEach((k) => { k.shape.radius = 0.5; }); c.ligarBarriga(modo !== "desligado"); c.play("acenar", true); T.vrm.springBoneManager.reset(); c.acao = null; T.avancar(3.0);
      const P = [...T.vrm.springBoneManager.joints].map((j) => j.bone.getWorldPosition(new (T.camera.position.constructor)()).toArray()); g.colliders.forEach((k, i) => { k.shape.radius = r0[i]; }); c.ligarBarriga(true); return P; }"""
    d = lambda a, b: max(sum((x - y) ** 2 for x, y in zip(u, v)) ** 0.5 for u, v in zip(a, b)) * 1000
    a, a2 = pagina.evaluate(JS, ["ligado", RESET]), pagina.evaluate(JS, ["ligado", RESET])
    off, giant = pagina.evaluate(JS, ["desligado", RESET]), pagina.evaluate(JS, ["gigante", RESET])
    assert len(a) >= 30
    assert d(a, a2) < 2.0, f"controle negativo ligado x ligado nao e ~0 ({d(a, a2):.2f} mm): a medida oscila sozinha"
    assert d(a, off) < 15.0, f"o guarda mexeu no cabelo parado: {d(a, off):.1f} mm (p140: 4,6 mm)"
    assert d(a, giant) > 100.0, f"controle positivo: capsula gigante deveria mover o cabelo ({d(a, giant):.1f} mm)"


def test_mao_desenhada_e_os_ossos_raw_coincidem_e_o_solver_usa_os_raw(pagina):
    # p144: ossos raw dos dedos a 0,1-0,5 cm da mao desenhada; os NORMALIZADOS ficam 9-12 cm fora (mao fantasma do rig). O solver amostra a mao desenhada.
    r = pagina.evaluate("""async () => { const T = celia3dBancada, c = T.corpo, TH = await import("three"), V = TH.Vector3, h = T.vrm.humanoid; let m = null; T.vrm.scene.traverse((o) => { if (o.name === "MAOS_DOADAS") m = o; }); m.skeleton.update();
      const P = [], p = new V(); for (let i = 0; i < m.geometry.attributes.position.count; i++) { m.getVertexPosition(i, p).applyMatrix4(m.matrixWorld); P.push(p.clone()); }
      const prox = (q) => Math.min(...P.map((v) => v.distanceTo(q))); const q = h.getRawBoneNode("rightMiddleDistal").getWorldPosition(new V());
      return { raw: prox(q), off: c.maoOff.right[0].length() }; }""")
    assert r["raw"] < 0.015, f"osso raw do dedo nao esta na mao desenhada ({r['raw']})"
    assert r["off"] > 0.08, f"amostra da mao do solver nao alcanca a mao desenhada ({r['off']})"


def test_empurrar_bracos_age_onde_ha_penetracao_e_fica_quieto_onde_nao_ha(pagina):
    def angmax(acao, sem):
        return pagina.evaluate("""([acao, sem]) => { const T = celia3dBancada, c = T.corpo; c.semEmpurrar = sem; c.play(acao, true); let m = 0;
          for (let i = 0; i < 16; i++) { T.avancar(0.25); const a = c.estat.empurraAng; if (a) m = Math.max(m, a.left, a.right); } c.semEmpurrar = false; return m; }""", [acao, sem])
    assert angmax("confusa", False) > 0, "confusa: o guarda nao agiu (p138: mao dir 5,4 -> 0,4 cm)"
    assert angmax("confusa", True) == 0, "controle: desligado nao pode corrigir nada"
    assert angmax("acenar", False) == 0, "acenar nao tem penetracao: o guarda tem de ficar quieto"
    assert angmax("confusa", False) <= 21, "correcao acima do teto de 20 graus (+1 de passo)"
