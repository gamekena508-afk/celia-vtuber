"""Rodada 8 (2026-10-06, Kurt) — EFEITO medido na bancada real, em navegador de verdade (Chrome headless, GPU).
Pulado (nao verde) se o servidor :12393, o Chrome ou o playwright nao existirem. Interpretador: o .venv da raiz.
Cada asserção repete, em ponto pequeno, uma regua de 3d/celia/v3/web/ (acervo do dono, fora do repositorio) com o NUMERO medido ao lado.
Controle: o proprio teste prova que o instrumento enxerga (quadril -5 cm TEM de cair ~5 cm)."""
import os
import shutil
import socket
import pytest

CHROME = "/usr/bin/google-chrome-stable"
# CELIA3D_QS: sabotagem de proposito (flags de controle A/B) para provar que o teste MORDE
URL = "http://127.0.0.1:12393/celia-3d-bancada.html?demo=0&pausado=1&prova=1" + os.environ.get("CELIA3D_QS", "")


def _servidor():
    try:
        with socket.create_connection(("127.0.0.1", 12393), timeout=1):
            return True
    except OSError:
        return False


pytestmark = pytest.mark.skipif(not (_servidor() and shutil.which(CHROME) or _servidor() and os.path.exists(CHROME)), reason="sem servidor :12393 ou sem Chrome")
sync_api = pytest.importorskip("playwright.sync_api")

SOLA = """() => { const T = celia3dBancada, h = T.vrm.humanoid, V = T.camera.position.constructor, mapa = new Map();
 for (const n of ["leftFoot", "rightFoot", "leftToes", "rightToes"]) mapa.set(h.getRawBoneNode(n), 1);
 const lista = []; T.vrm.scene.traverse((m) => { if (!m.isSkinnedMesh) return; const SI = m.geometry.attributes.skinIndex, SW = m.geometry.attributes.skinWeight;
   for (let i = 0; i < SI.count; i++) { let mx = 0, ok = false; for (let c = 0; c < 4; c++) { const w = SW.getComponent(i, c); if (w > mx) { mx = w; ok = mapa.has(m.skeleton.bones[SI.getComponent(i, c)]); } } if (ok && mx > 0.5) lista.push([m, i]); } });
 window.__sola = () => { const p = new V(); let mn = 9; for (const [m, i] of lista) { m.getVertexPosition(i, p).applyMatrix4(m.matrixWorld); if (p.y < mn) mn = p.y; } return mn; }; return lista.length; }"""


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


def test_camera_exposta_e_kaykit_carregou(pagina):
    assert pagina.evaluate("() => celia3dBancada.camera.isPerspectiveCamera") is True
    kk = pagina.evaluate("() => celia3dBancada.kaykit")
    assert kk["erro"] == {} and len(kk["ok"]) >= 40 and "kk_provocar" not in kk["ok"]
    # reprovados na triagem p130 (pe > 2,5 cm abaixo do chao ou fora do quadro) nao podem estar no catalogo
    assert not {"kk_magia_invocar", "kk_arco_soltar_cima", "kk_espada_pulo", "kk_montante_giro2", "kk_luta_guarda"} & set(kk["ok"])


def test_mao_doada_carregou_e_punho_curto(pagina):
    assert pagina.evaluate("() => celia3dBancada.maoNova.ok") is True


def test_sola_nos_gestos_nao_afunda_mais_de_1_5_cm(pagina):
    # p109: antes sim -4,0 / continencia -3,4 / charleston -4,2; depois -1,0 / -0,7. Limite do aceite do plano: -1,5 cm.
    assert pagina.evaluate(SOLA) > 100
    pagina.evaluate("() => celia3dBancada.avancar(0.5)")
    rep = pagina.evaluate("() => window.__sola()")
    piores = {}
    for acao in ("sim", "continencia", "cumprimentar", "kk_acenar"):
        pagina.evaluate("(n) => celia3dBancada.corpo.play(n, true)", acao)
        pior = 9
        for _ in range(25):
            pagina.evaluate("() => celia3dBancada.avancar(0.1)")
            pior = min(pior, pagina.evaluate("() => window.__sola()") - rep)
        piores[acao] = round(pior * 100, 1)
        pagina.evaluate("() => celia3dBancada.avancar(7)")
    assert all(v >= -1.5 for v in piores.values()), piores


def test_controle_positivo_quadril_5cm_derruba_a_sola(pagina):
    # o instrumento tem de enxergar: sem isto o teste acima poderia passar por cegueira
    pagina.evaluate("() => celia3dBancada.avancar(7)")
    rep = pagina.evaluate("() => window.__sola()")
    pagina.evaluate("() => { celia3dBancada.corpo.quadrilRepouso.y -= 0.05; }")
    pagina.evaluate("() => celia3dBancada.avancar(0.3)")
    queda = (pagina.evaluate("() => window.__sola()") - rep) * 100
    pagina.evaluate("() => { celia3dBancada.corpo.quadrilRepouso.y += 0.05; celia3dBancada.avancar(0.3); }")
    assert -5.6 < queda < -4.4, queda


def test_backflip_tem_teto_de_voo(pagina):
    # p113/p111: o clipe subia o quadril 1,84 m (pico absoluto ~2,7 m); com alt 0.45 fica ~1,7 m.
    pagina.evaluate("() => celia3dBancada.corpo.play('backflip', true)")
    topo = 0
    for _ in range(50):
        pagina.evaluate("() => celia3dBancada.avancar(0.05)")
        topo = max(topo, pagina.evaluate("() => celia3dBancada.mundo('hips')[1]"))
    pagina.evaluate("() => celia3dBancada.avancar(7)")
    assert topo < 2.0, topo


def test_boca_triste_cede_para_acao_de_boca(pagina):
    # p116/p117: triste (t62 .75) + paz (t105 .6) = 2 manchas de boca visiveis; com a regra t62 cai abaixo de 0,15.
    pagina.evaluate("() => { const c = celia3dBancada.corpo; c.acao = null; c.setEmotion('sad'); celia3dBancada.avancar(2.5); }")
    antes = pagina.evaluate("() => celia3dBancada.corpo.rostoAtual.target_62 || 0")
    pagina.evaluate("() => { celia3dBancada.corpo.play('paz', true); celia3dBancada.avancar(0.9); }")
    durante = pagina.evaluate("() => celia3dBancada.corpo.rostoAtual.target_62 || 0")
    pagina.evaluate("() => { celia3dBancada.corpo.setEmotion('neutral'); celia3dBancada.avancar(5); }")
    assert antes > 0.5 and durante < 0.15, (antes, durante)


def test_braco_nao_entra_na_coxa_no_andar_e_na_danca(pagina):
    # p104: andar -1,1 cm e danca do quadril -6,9 cm (mao/meio do antebraco contra o eixo da coxa, raio 8,5 cm); depois +0,6 e +0,3.
    medir = """() => { const T = celia3dBancada, h = T.vrm.humanoid, V = T.camera.position.constructor, raw = (n) => h.getRawBoneNode(n).getWorldPosition(new V()); let pior = 9;
      const dseg = (p, a, b) => { const ab = b.clone().sub(a), t = Math.max(0, Math.min(1, p.clone().sub(a).dot(ab) / ab.lengthSq())); return p.distanceTo(a.clone().addScaledVector(ab, t)); };
      for (const l of ["left", "right"]) { const mao = raw(l + "Hand"), ant = raw(l + "LowerArm").add(mao).multiplyScalar(0.5);
        for (const pt of [mao, ant]) for (const c of ["left", "right"]) pior = Math.min(pior, dseg(pt, raw(c + "UpperLeg"), raw(c + "LowerLeg")) - 0.085); } return pior; }"""
    for acao, dur in (("andar", 10.2), ("danca_quadril", 2.6)):
        pagina.evaluate("(n) => celia3dBancada.corpo.play(n, true)", acao)
        pior = 9
        for _ in range(int(dur / 0.1)):
            pagina.evaluate("() => celia3dBancada.avancar(0.1)")
            pior = min(pior, pagina.evaluate(medir))
        pagina.evaluate("() => celia3dBancada.avancar(7)")
        assert pior >= -0.002, (acao, round(pior * 100, 1))


def _espessura_ombro(pagina, qs_extra):
    """Espessura (p90 do raio ao eixo ombro->cotovelo) da 1a quinta parte do UpperArm, posado/repouso, no pior quadro do comemorar (0,2 a 0,6 s). p135 em ponto pequeno."""
    pagina.goto("http://127.0.0.1:12393/celia-3d-bancada.html?demo=0&pausado=1&prova=1" + qs_extra, wait_until="load")
    pagina.wait_for_function("() => window.celia3dBancada && celia3dBancada.estado === 'ok'", timeout=90000); pagina.wait_for_timeout(1200)
    return pagina.evaluate("""async () => { const T = celia3dBancada, vrm = T.vrm, h = vrm.humanoid, TH = await import("three"), V = TH.Vector3; let body = null; vrm.scene.traverse((o) => { if (o.isSkinnedMesh && o.name === "V3_corpo_normal") body = o; });
      h.resetNormalizedPose(); vrm.update(0); body.skeleton.update(); const SI = body.geometry.attributes.skinIndex, SW = body.geometry.attributes.skinWeight, N = SI.count, p = new V();
      const up = h.getRawBoneNode("leftUpperArm"), lo = h.getRawBoneNode("leftLowerArm"), desc = (b, r) => { for (let o = b; o; o = o.parent) if (o === r) return true; return false; }, ok = body.skeleton.bones.map((b) => desc(b, up) && !desc(b, lo)), ids = [];
      for (let i = 0; i < N; i++) { let mx = 0, dom = -1; for (let c = 0; c < 4; c++) { const w = SW.getComponent(i, c); if (w > mx) { mx = w; dom = SI.getComponent(i, c); } } if (dom >= 0 && ok[dom] && mx > 0.5) ids.push(i); }
      const eixo = () => [up.getWorldPosition(new V()), lo.getWorldPosition(new V())], raio = (A, B, sel) => { const ab = B.clone().sub(A), L2 = ab.lengthSq(), r = []; for (const [i, s0] of sel) { body.getVertexPosition(i, p); const t = Math.min(1, Math.max(0, p.clone().sub(A).dot(ab) / L2)); r.push(p.distanceTo(A.clone().addScaledVector(ab, t))); } r.sort((a, b) => a - b); return r[Math.floor(r.length * 0.9)]; };
      let [A, B] = eixo(), ab = B.clone().sub(A), L2 = ab.lengthSq(); const sel = ids.map((i) => { body.getVertexPosition(i, p); return [i, p.clone().sub(A).dot(ab) / L2]; }).filter(([, s]) => s < 0.2); const r0 = raio(A, B, sel);
      const c = T.corpo; c.acao = null; c.microEm = 1e9; c.play("comemorar", true); let pior = 9;
      for (let k = 0; k < 120; k++) { c.update(1 / 60); vrm.update(1 / 60); if (c.distribuirTorcao) c.distribuirTorcao(); c.aplicarRosto(); if (T.ombro) T.ombro.atualizar(); if (k > 12 && k % 3 === 0) { body.skeleton.update(); [A, B] = eixo(); pior = Math.min(pior, raio(A, B, sel) / r0); } }
      return +pior.toFixed(3); }""")


def test_comemorar_ombro_nao_afina_com_torcao_espalhada(pagina):
    # p135: ombro do comemorar 0,72 do repouso (padrao) -> 0,94-0,97 com ?torcao=1. Controle: o proprio padrao TEM de mostrar o aperto (instrumento enxerga).
    padrao = _espessura_ombro(pagina, ""); com = _espessura_ombro(pagina, "&torcao=1")
    assert padrao < 0.85, ("o instrumento nao enxergou o aperto do padrao", padrao)
    assert com > 0.90, ("a torcao espalhada nao tirou o aperto do ombro", com, padrao)


def test_torcao_espalhada_nao_move_antebraco_nem_mao(pagina):
    # a rotacao de mundo do antebraco e da mao fica igual (<= 0,05 grau) e o cotovelo <= 1 mm; so a torcao e espalhada
    js = """async (qs) => { const T = celia3dBancada, h = T.vrm.humanoid, out = {}; for (const n of ["leftLowerArm", "leftHand", "rightLowerArm", "rightHand"]) { const b = h.getRawBoneNode(n); out[n] = [...b.getWorldQuaternion(new T.camera.quaternion.constructor()).toArray(), ...b.getWorldPosition(new T.camera.position.constructor()).toArray()]; } return out; }"""
    def pose(qs):
        pagina.goto("http://127.0.0.1:12393/celia-3d-bancada.html?demo=0&pausado=1&prova=1" + qs, wait_until="load")
        pagina.wait_for_function("() => window.celia3dBancada && celia3dBancada.estado === 'ok'", timeout=90000); pagina.wait_for_timeout(1200)
        pagina.evaluate("() => { const c = celia3dBancada.corpo; c.acao = null; c.microEm = 1e9; c.play('comemorar', true); for (let k = 0; k < 60; k++) { c.update(1 / 60); celia3dBancada.vrm.update(1 / 60); if (c.distribuirTorcao) c.distribuirTorcao(); c.aplicarRosto(); } celia3dBancada.vrm.scene.updateMatrixWorld(true); }")
        return pagina.evaluate(js, qs)
    import math
    a, b = pose(""), pose("&torcao=1")
    for n in a:
        dq = abs(sum(x * y for x, y in zip(a[n][:4], b[n][:4]))); ang = 2 * math.degrees(math.acos(min(1.0, dq))); dist = math.dist(a[n][4:], b[n][4:]) * 1000
        assert ang < 0.05 and dist < 1.0, (n, ang, dist)
