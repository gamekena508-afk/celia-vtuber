"""Contratos do corpo 3D (plano Celia 3D, 2026-10-06, Kurt).

1. Paridade 2D <-> 3D: os nomes E os valores de PRESETS, e os nomes dos perfis de emocao, sao os mesmos no
   diretor do 2D (celia-motion-director.js) e no corpo 3D (celia-3d-corpo.js). Se alguem mudar um lado, isto aponta.
2. O 3D nao mexe no 2D: nao emite eventos celia-*, nao toca no #canvas do Live2D.
3. Offline: o importmap da bancada so aponta para arquivos locais.
"""
import re
from pathlib import Path

FRONT = Path(__file__).resolve().parents[1] / "frontend"
DIRETOR_2D = FRONT / "celia-motion-director.js"
CORPO_3D = FRONT / "celia-3d-corpo.js"
BANCADA_JS = FRONT / "celia-3d-bancada.js"
OMBRO_3D = FRONT / "celia-3d-ombro.js"
CABELO_3D = FRONT / "celia-3d-cabelo.js"
MAOS_3D = FRONT / "celia-3d-maos.js"
CLIPES_3D = FRONT / "celia-3d-clipes.js"
MAO_DOADA_3D = FRONT / "celia-3d-mao-doada.js"
CAUDA_3D = FRONT / "celia-3d-cauda.js"
CATALOGO_3D = FRONT / "celia-3d-catalogo.js"
CURADORIA_3D = FRONT / "celia-3d-curadoria.js"
SAIA_3D = FRONT / "celia-3d-saia.js"
GITIGNORE = FRONT.parent / ".gitignore"
BANCADA_HTML = FRONT / "celia-3d-bancada.html"

PRESET = re.compile(r'^\s*"([^"]+)":\s*mp\((.*)\),\s*$', re.M)


def _bloco(texto: str, nome: str) -> str:
    inicio = texto.index(f"const {nome} = Object.freeze({{")
    return texto[inicio:texto.index("});", inicio)]


def presets(caminho: Path) -> dict:
    return {nome: re.sub(r"\s+", "", args) for nome, args in PRESET.findall(_bloco(caminho.read_text(encoding="utf-8"), "PRESETS"))}


def perfis(caminho: Path) -> list:
    bloco = _bloco(caminho.read_text(encoding="utf-8"), "PROFILES")
    return re.findall(r"^\s{2,4}([a-z_]+):\s*\{\s*amp:", bloco, re.M)


def test_paridade_presets_nomes_e_valores():
    p2d, p3d = presets(DIRETOR_2D), presets(CORPO_3D)
    assert len(p2d) >= 50, "regex nao achou os presets do 2D (instrumento quebrado)"
    assert p2d == p3d


def test_paridade_perfis_de_emocao():
    e2d, e3d = perfis(DIRETOR_2D), perfis(CORPO_3D)
    assert len(e2d) >= 10, "regex nao achou os perfis do 2D (instrumento quebrado)"
    assert e2d == e3d


def test_3d_nao_emite_nem_toca_no_2d():
    for arquivo in (CORPO_3D, BANCADA_JS, OMBRO_3D, CABELO_3D, MAOS_3D, CLIPES_3D, MAO_DOADA_3D, CAUDA_3D, SAIA_3D, CURADORIA_3D, CATALOGO_3D):
        texto = arquivo.read_text(encoding="utf-8")
        assert "dispatchEvent" not in texto, f"{arquivo.name} emite evento"
        assert 'getElementById("canvas")' not in texto and "getElementById('canvas')" not in texto
        assert "live2d-internal-wrapper" not in texto


def test_bancada_sem_cdn():
    html = BANCADA_HTML.read_text(encoding="utf-8")
    mapa = html[html.index('<script type="importmap">'):html.index("</script>", html.index('<script type="importmap">'))]
    assert "./libs/three/" in mapa and "./libs/three-vrm/" in mapa
    assert "http://" not in mapa and "https://" not in mapa


def test_corretor_de_ombro_tem_controle_ab():
    # p20_pontas.py: 34 pontas > 6 mm sem o corretor, 0 com ele. ?semombro e o controle; nao pode sumir.
    js = BANCADA_JS.read_text(encoding="utf-8")
    assert 'import { OmbroSuave } from "./celia-3d-ombro.js' in js
    assert 'Q.has("semombro")' in js
    assert js.count("api.ombro.atualizar()") == 2  # laco do quadro e avanco deterministico da prova


def test_procedural_sem_tremedeira():
    # p30/p31 (2026-10-06): misturar a FREQUENCIA do preset dentro de sin(T*freq) chacoalhava a cabeca a ~300 graus/s
    # em toda troca; e o 3D usava sin(T*2pi*freq*0.5), ~pi vezes mais rapido que o 2D. Agora mistura sinais, rad/s.
    js = CORPO_3D.read_text(encoding="utf-8")
    assert "_presetMisturado" not in js
    assert "Math.PI * 2 * pr." not in js
    assert "Math.sin(T * pr.nodFreq + f * 0.7)" in js  # mesma forma do presetSignals() do 2D
    assert "this.energiaCorpo" in js and "energia * 0.35" not in js  # corpo nao segue silaba a silaba


def test_boca_sem_lingua_pra_fora():
    # p27/p33: no unlit a lingua rosa enchia a boca aberta e passava do labio. Fala limitada e lingua escurecida.
    js = CORPO_3D.read_text(encoding="utf-8")
    boca = float(re.search(r"const BOCA_MAX = ([\d.]+);", js).group(1))
    tom = float(re.search(r"const LINGUA_TOM = ([\d.]+);", js).group(1))
    assert boca <= 0.5 and tom <= 0.35
    assert "g * BOCA_MAX" in js
    assert '_TONGUE' in js


def test_cabelo_com_colisores_e_controle_ab():
    # p32/p41/p43: o .vrm tem 0 colliders; o cabelo entrava no corpo no mortal e no casaco em repouso.
    js, banc = CORPO_3D.read_text(encoding="utf-8"), BANCADA_JS.read_text(encoding="utf-8")
    assert "VRMSpringBoneColliderShapeCapsule" in js and "_isSortedJointsDirty = true" in js
    assert "_firmarCabelo" in js and "cabelo: 1," in js  # mortal com cabelo firme
    assert 'Q.has("semcolisor")' in banc and 'Q.has("cabelosolto")' in banc


def test_uma_boca_so():
    # p48/p51 (rodada 5): target_101 desenha boquinha + curva = duas bocas; boca da emocao + boca da fala = duas bocas.
    js = CORPO_3D.read_text(encoding="utf-8")
    perfis = js[js.index("export const PROFILES"):js.index("export const EMOTION_ALIASES")]
    acoes = js[js.index("export const ACOES"):]
    assert "target_101" not in perfis and "target_101:" not in acoes
    assert "const BOCAS_EMOCAO" in js and "rosto[k] *= 1 - this.bocaFala" in js


def test_maos_e_cabelo_corrigidos_na_carga():
    # p57-p60: ossos dos dedos ~6 cm fora da mao e o medio fora do esqueleto -> reencaixe + pesos; p52/p53: pontinhos.
    banc, maos, corpo = BANCADA_JS.read_text(encoding="utf-8"), MAOS_3D.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert 'Q.has("maocrua")' in banc and 'Q.has("cabelocru")' in banc
    assert "function reencaixarDedos" in maos and "malha.bind(novo, malha.bindMatrix)" in maos
    assert "andar: {" in corpo and "espreguicar: {" in corpo and "ALFA = PASSO / 1.6" in corpo  # fallback procedural


def test_mao_transplantada_de_vrm_bem_rigada():
    # p72 (rodada 6): a mao do .vrm e sopa de triangulos, dedos fundidos, sem aneis nas juntas. A mao modelada em codigo
    # foi reprovada pelo Pai ("pa laranja grande"); vale a mao TRANSPLANTADA da VRM de exemplo da pixiv (VRM Public
    # License 1.0: modificar/redistribuir/comercial). ?maoantiga e o controle A/B.
    banc, doada, corpo = BANCADA_JS.read_text(encoding="utf-8"), MAO_DOADA_3D.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert 'import { transplantarMaos } from "./celia-3d-mao-doada.js' in banc and 'Q.has("maoantiga")' in banc
    assert "celia-3d-mao-nova.js" not in banc and not (FRONT / "celia-3d-mao-nova.js").exists()  # quarentena
    assert "VRM1_Constraint_Twist_Sample.vrm" in banc and "allowModificationRedistribution" in doada
    assert 'nova.name = "MAOS_DOADAS"' in doada and "setIndex(new THREE.BufferAttribute(" in doada  # TypedArray cru quebra o indice
    assert "FACE_VRM" in doada  # tom da pele do rosto, nao o laranja chapado do corpo
    limite = float(re.search(r"const DEDOS_LIMITE = ([\d.]+);", corpo).group(1))
    assert 0.55 < limite <= 1.6


def test_cauda_com_mola_e_controle_ab():
    # p81/p83 (rodada 6): a cauda ('other') vinha 100% presa na coluna; a perna a atravessava (andar 52 quadros, correr 44).
    # Com cadeia + mola + colisores, e repouso para tras: 0 nas cinco acoes medidas.
    banc, cauda = BANCADA_JS.read_text(encoding="utf-8"), CAUDA_3D.read_text(encoding="utf-8")
    assert 'Q.has("caudarigida")' in banc and banc.index("criarCauda(vrm") < banc.index("new Corpo3D(")
    assert "VRMSpringBoneJoint" in cauda and "sb.addJoint(j)" in cauda and "j.setInitState()" in cauda
    assert "new THREE.Vector3(-0.15, -0.7, -0.7)" in cauda and "opcoes.raio || 0.15" in cauda


def test_saia_lascas_e_acolchoamento():
    # p91-p95 (rodada 6): manchas da saia = lascas com UV em ilha de outra peca + borda puxando ilha vizinha.
    banc, saia = BANCADA_JS.read_text(encoding="utf-8"), SAIA_3D.read_text(encoding="utf-8")
    assert 'Q.has("saiacrua")' in banc and "export function consertarLascas" in saia
    # o acolchoamento global (fase 2) foi REMOVIDO de proposito (manchas pretas no peito, p98): so a saia e acolchoada
    assert "PAD_SAIA" in saia and "SEM fase 2" in saia


def test_clipes_cc0_por_padrao_e_mixamo_so_com_flag():
    # Mesh2Motion e CC0 (pode ir pra produto); Clawatar e Mixamo NON-COMMERCIAL: so com ?clawatar, fora do git.
    banc, clip = BANCADA_JS.read_text(encoding="utf-8"), CLIPES_3D.read_text(encoding="utf-8")
    assert "carregarM2M(\"./modelos-3d/anim/m2m/" in banc
    assert 'if (Q.has("clawatar"))' in banc and banc.index("./modelos-3d/anim/clawatar/") > banc.index('if (Q.has("clawatar"))')
    assert "frontend/modelos-3d/anim/" in GITIGNORE.read_text(encoding="utf-8")
    assert "createVRMAnimationClip" in clip and "./libs/three-vrm/three-vrm-animation.module.min.js" in clip


def test_andar_por_clipe_sem_escorregar():
    # p70 (rodada 6): sem trava o pe escorregava 3,55 mm/quadro (mediana) no andar e 7,7 na corrida; com trava, 0,0.
    banc, clip, corpo = BANCADA_JS.read_text(encoding="utf-8"), CLIPES_3D.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert "function rotaAndar" in corpo and "_travarPes(" in corpo and 'Q.has("semtrava")' in banc
    assert 'clipe: "Walk_Female"' in corpo and "Walk_Female" in banc[banc.index('"human-addon-animations.glb"'):]
    assert "velPe" in clip and "fase0" in clip and "pisos: piso" in clip
    assert "passos += s * dt;" in corpo  # virada parada nao avanca o clipe (o pe andava sem o corpo andar)


# ---------------------------------------------------------------------------------------------------------------------
# Rodada 8 (2026-10-06, Kurt) — plano em 04-CONTEXTO-E-PLANO-RODADA7.md (acervo do dono, fora do repositorio).
# Cada teste abaixo guarda UM defeito medido pelas reguas p102-p123 (3d/celia/v3/web/, acervo do dono, fora do repositorio).
# Os testes estaticos so provam que o mecanismo esta no codigo; o efeito e provado por test_celia_3d_rodada8_viva.py.
# ---------------------------------------------------------------------------------------------------------------------

def test_camera_exposta_so_para_leitura():
    # Fase 0: a regua do "mortal sai do quadro" nunca capturou a camera (cam: None x6) e media o vazio.
    js = BANCADA_JS.read_text(encoding="utf-8")
    trecho = js[js.index('Object.defineProperty(api, "camera"'):][:140]
    assert "get: () => camera" in trecho and "set:" not in trecho


def test_camera_acompanha_rapido_na_rota_e_backflip_tem_teto_de_voo():
    # p111: backflip saia do quadro em 29/151 quadros (quadril +1,84 m, pes +3,4 m); correr em janela 480x800 em 120/269.
    banc, corpo = BANCADA_JS.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert "ACOES[st.acao]._rota ? 12" in banc
    assert 'backflip: { clipe: "Backflip", tipo: "acao", cabelo: 1, alt: 0.45' in corpo
    assert "d[1] *= clipeAtivo.alt" in corpo


def test_supersampling_e_opt_in_e_cauda_tem_anisotropia():
    # p112: ?ss=2 reduz o erro de borda contra a referencia 4x de 7,04 para 2,40 (FPS 60,0 -> 49,9): por isso NAO e o padrao.
    js = BANCADA_JS.read_text(encoding="utf-8")
    assert 'get("ss")) || 1' in js and "renderer.setPixelRatio(dpr * SS)" in js
    assert 'Q.has("caudaaniso") ? Number(Q.get("caudaaniso")) : 8' in js


def test_apoio_no_chao_e_trava_de_pe_nos_gestos():
    # p109: nos gestos por clipe o quadril do clipe fica 2,5-4,7 cm abaixo do repouso e a sola afundava 2-4 cm (sim -4,0).
    banc, corpo = BANCADA_JS.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert "_pisarChao(dt)" in corpo and "_sondasPe()" in corpo and "this.chaoLift = alvo > this.chaoLift ? alvo" in corpo
    assert 'gesto: def.tipo === "gesto"' in corpo and "(clipeAtivo.rota || clipeAtivo.gesto) && !this.semTrava" in corpo
    assert "d[0] -= clipeAtivo.c.quadril[0]" in corpo  # deriva horizontal do quadril do mocap nos gestos
    assert 'Q.has("semchao")' in banc and 'Q.has("semderiva")' in banc  # controles A/B


def test_acao_manda_na_boca_e_boca_triste_cede():
    # p110/p116/p117: pares que DESENHAM duas/tres bocas (visual, regua de manchas): t105+t62 (2), t62+visema (3), t58+t91+t93 (2).
    corpo, banc = CORPO_3D.read_text(encoding="utf-8"), BANCADA_JS.read_text(encoding="utf-8")
    assert "A ACAO MANDA NA BOCA" in corpo and "rosto[k] *= 1 - clamp(ma, 0, 1)" in corpo
    assert "rosto.target_62 && !this.semCederBoca" in corpo and "rosto.target_62 *= 1 - u * u * (3 - 2 * u)" in corpo
    assert 'Q.has("semcederboca")' in banc


def test_toco_do_pulso_curto_nao_fura_a_manga():
    # p119/p121: com corte 0,3 o toco da mao doada furava a manga (pele laranja sobre o punho); 0,08 limpa.
    banc, doada = BANCADA_JS.read_text(encoding="utf-8"), MAO_DOADA_3D.read_text(encoding="utf-8")
    assert 'corte: Q.has("cortemao") ? Number(Q.get("cortemao")) : 0.08' in banc
    assert "opcoes.corte || 0.3" in doada  # o padrao do modulo continua; quem decide e a bancada


def test_kaykit_cc0_com_procedencia_e_so_o_que_passou_na_regua():
    # Fase F: KayKit Character Animations 1.1 (CC0). Skeletons_Taunt (pe -11 cm) e Jump_Full_Short (pe -9,8 cm) foram REPROVADOS.
    # Rodada 9: o catalogo saiu da bancada para celia-3d-catalogo.js (so dados).
    banc, clip = BANCADA_JS.read_text(encoding="utf-8"), CLIPES_3D.read_text(encoding="utf-8")
    cat = CATALOGO_3D.read_text(encoding="utf-8")
    ativo = cat[cat.index("export const KAYKIT = {"):cat.index("export const CLAWATAR")]
    for ok in ("Waving", "Cheering", "Hit_A", "Interact", "Throw", "Use_Item", "Ranged_Magic_Spellcasting", "Melee_1H_Attack_Slice_Horizontal", "Ranged_2H_Aiming"):
        assert f"{ok}:" in ativo
    codigo = "\n".join(l for l in ativo.splitlines() if not l.strip().startswith("//"))
    assert "Skeletons_Taunt:" not in codigo and "Jump_Full_Short:" not in codigo
    assert "const KAYKIT" not in banc and 'from "./celia-3d-catalogo.js' in banc  # uma fonte so
    assert "export const CFG_KAYKIT" in clip and "userData.name" in clip  # o GLTFLoader tira o ponto de 'foot.l'
    assert 'Q.has("semkaykit")' in banc
    assert "CC0" in (FRONT / "modelos-3d" / "anim" / "PROCEDENCIA.txt").read_text(encoding="utf-8")
    assert "Creative Commons Zero" in (FRONT / "modelos-3d" / "anim" / "kaykit" / "LICENSE-CC0.txt").read_text(encoding="utf-8")
    # o rosto das acoes novas nunca usa chave de boca (a regra "a acao manda na boca" e do corpo)
    boca = {"target_93", "target_105", "target_91", "target_62", "target_35", "target_17", "target_58", "target_2", "target_21", "target_11", "target_56"}
    usadas = set(re.findall(r"target_\d+", ativo))
    assert not (usadas - {"target_93", "target_105", "target_91"}) & boca, usadas  # as 3 do pacote antigo ja medidas; as novas so sobrancelha/olho


def test_clawatar_e_non_commercial_e_so_com_flag():
    banc, cat = BANCADA_JS.read_text(encoding="utf-8"), CATALOGO_3D.read_text(encoding="utf-8")
    assert "NON-COMMERCIAL" in cat and 'if (Q.has("clawatar"))' in banc
    assert "frontend/modelos-3d/anim/" in GITIGNORE.read_text(encoding="utf-8")


def test_braco_sai_da_coxa_com_controle_ab():
    # p104: BRACO NA PERNA em andar -1,1 cm, danca do quadril -6,9, recusar -2,3, tonta -1,4, backflip -3,0 (casaco/saia largos, clipe de ombro estreito).
    banc, corpo = BANCADA_JS.read_text(encoding="utf-8"), CORPO_3D.read_text(encoding="utf-8")
    assert "_afastarBracos(dt)" in corpo and "const R = 0.09, PASSO = 3 * DEG, MAXIMO = 26 * DEG" in corpo
    assert corpo.index("this._afastarBracos(dt);") < corpo.index("this._aplicarTransicao(dt);\n\n    // ---- piscar")
    assert 'Q.has("semafastar")' in banc


def test_curadoria_tres_estados_persiste_e_migra():
    # Pedido do Pai (rodada 9): V (fica), ? (fica e vai para a lista de consertar) e X (caixa de descartadas). Mini-app de decisao: CODEX.md secao 14.
    cur, banc = CURADORIA_3D.read_text(encoding="utf-8"), BANCADA_JS.read_text(encoding="utf-8")
    assert 'const ESTADOS = ["ok", "melhorar", "descartada"]' in cur and 'CHAVE = "celia3d-bancada:curadoria:v2"' in cur
    assert 'typeof e.marcas === "undefined"' in cur and "CHAVE_V1" in cur and "this.migrado = true" in cur  # migracao da v1 (a chave v1 nao e apagada)
    assert "localStorage.setItem(CHAVE" in cur and "NAO salvou" in cur and "ilegivel" in cur       # fail-visible
    assert "navigator.clipboard.writeText" in cur and 'a.download = "celia3d-curadoria.json"' in cur  # lista de ? copiavel e baixavel
    assert 'import { Curadoria } from "./celia-3d-curadoria.js' in banc and "curadoria.chip(" in banc and 'id="g-curadoria"' in BANCADA_HTML.read_text(encoding="utf-8")
    assert "if (!curadoria.descartada(arg)) api.corpo.play(arg);" in banc  # a demonstracao automatica pula as descartadas
    assert banc.count("botaoAcao(") >= 4  # acoes, clawatar, kaykit e a definicao


def test_nenhum_comentario_de_linha_engole_codigo_na_mesma_linha():
    # Armadilha repetida (plano da rodada 7 e rodada 9): um "// ..." no meio de uma linha com varios comandos engole o resto da linha.
    # `node --check` NAO pega (continua sintaxe valida): o erro so estoura em tempo de execucao (ReferenceError: alvo is not defined).
    import re as _re
    ruins = []
    for arq in sorted(FRONT.glob("celia-3d-*.js")):
        for n, linha in enumerate(arq.read_text(encoding="utf-8").splitlines(), 1):
            sem_texto = _re.sub(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`(?:[^`\\]|\\.)*`|/\*.*?\*/', '""', linha)  # tira strings e /* */
            m = _re.search(r"(?<!:)//(?!/)(.*)$", sem_texto)
            # comentario NO FIM de uma linha que ja tem codigo (so conta o que vem antes do //) e que contem uma declaracao: o codigo foi engolido
            if m and sem_texto[: m.start()].strip() and _re.search(r"\b(const|let|var)\s+[A-Za-z_$][\w$]*\s*=", m.group(1)):
                ruins.append(f"{arq.name}:{n}: {linha.strip()[:90]}")
    assert not ruins, ruins


def test_dividir_aproxima_modelo_e_ossos_pela_metade():
    # Pedido do Pai (rodada 9): no ?dividir=1 a distancia modelo<->ossos cai 50%. Medido em p134: 750,7 -> 375,6 px (1500 px) e 500,3 -> 250,4 px (1000 px); ?dist=1 = antigo.
    js = BANCADA_JS.read_text(encoding="utf-8")
    assert 'Math.min(1, Math.max(0.2, Number(Q.get("dist")) || 0.5))' in js
    assert "camera.setViewOffset(meio, H, -desloc, 0, meio, H)" in js and "camera.setViewOffset(meio, H, desloc, 0, meio, H)" in js and "camera.clearViewOffset();" in js
    assert js.index("if (!DIVIDIR) { renderer.render(cena, camera); return; }") < js.index("setViewOffset")  # o modo normal nunca chega no deslocamento


def test_torcao_do_braco_espalhada_e_opt_in():
    # Pedido do Pai (rodada 9): no comemorar o braco afina na ligacao com o tronco. Causa medida (p135/p136): o Cheering_Two_Hands gira o umero 118-122 graus em
    # torno do proprio eixo e toda a torcao fica no osso do ombro (espessura 0,72). O rig ja tem arm -> arm001 -> arm002 -> elbow; so estavam rigidos.
    corpo, banc = CORPO_3D.read_text(encoding="utf-8"), BANCADA_JS.read_text(encoding="utf-8")
    assert "distribuirTorcao() {" in corpo and "if (!this.torcao) return;" in corpo and "this.torcao = false;" in corpo   # opt-in: o padrao nao muda
    assert "A1.position.clone().add(A2.position).add(L.position).normalize()" in corpo   # eixo = reta ombro->cotovelo (4,4 graus fora do X do osso; em torno do X o cotovelo deslocava 14 mm)
    assert 'Q.has("torcao")' in banc and banc.count("api.corpo.distribuirTorcao();") == 2   # laco do quadro e avanco deterministico
    assert banc.index("api.corpo.distribuirTorcao();") < banc.index("api.ombro.atualizar()")  # antes do corretor de ombro, que le as matrizes dos ossos


def test_rodada10_cabelo_barriga_e_bracos_no_tronco_tem_controle_ab():
    """Rodada 10 (2026-10-06, Kurt). Pedido do Pai: cabelo atravessa o corpo em levar golpe/arremessar; bracos dentro do corpo; mao 'dupla'.
    Efeito medido nos testes vivos (test_celia_3d_rodada10_viva.py); aqui, que cada correcao existe, e DESLIGAVEL (controle A/B) e liga por padrao."""
    corpo, banc = CORPO_3D.read_text(encoding="utf-8"), BANCADA_JS.read_text(encoding="utf-8")
    assert "_colisoresBarriga()" in corpo and "ligarBarriga(ligado)" in corpo and 'name: "barriga-celia3d"' in corpo
    assert "this.ligarBarriga(true)" in corpo, "o guarda da barriga tem de ligar por padrao"
    assert 'Q.has("semcolisorbarriga")' in banc and "ligarBarriga(false)" in banc
    assert "_empurrarBracos(dt)" in corpo and "this._empurrarBracos(dt);" in corpo
    assert 'Q.has("semempurrar")' in banc and "api.corpo.semEmpurrar = true" in banc
    # a mao fantasma: as amostras da mao vem dos ossos RAW (mao desenhada), nunca dos dedos normalizados
    assert "this.maoOff" in corpo and "getRawBoneNode(lado + n)" in corpo
    # o guarda da cabeca/olhos foi medido PIOR que a base (223 x 36 vertices na faixa dos olhos) e removido: nao pode voltar sem nova medida
    assert "livreOlhos" not in corpo and "semOlhosLivres" not in corpo
