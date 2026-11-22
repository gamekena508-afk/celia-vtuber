"""Mapa do prompt FINAL que chega ao 70B: bloco a bloco, com camada, tipo, origem no código e exemplos embutidos.

Por que existe: o system prompt é montado por concatenação em vários lugares (conf.yaml, arquivo de guia, `persona_identity.py`,
`basic_memory_agent.py`, `jev.py`). Sem um mapa, descobrir QUAL frase causa QUAL comportamento vira adivinhação. Este módulo é
puro (recebe o texto) e só lê arquivos do projeto para achar a origem; não chama modelo e não escreve nada.

Garantias (testadas em tests/test_prompt_mapa.py):
- cobertura total: a soma dos blocos é exatamente o tamanho do prompt (nenhum caractere fica fora do mapa);
- bloco novo/desconhecido aparece como "SEM RÓTULO" (nunca some);
- cada bloco declara camada (personalidade × comportamento conversacional × segurança × ferramentas × forma × contexto)
  e tipo (INSTRUÇÃO × CONTEXTO/DADO), de modo que "o que é dado e o que é ordem" seja uma propriedade do código, não de opinião.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

PERSONALIDADE = "PERSONALIDADE"
CONDUTA = "COMPORTAMENTO CONVERSACIONAL"
SEGURANCA = "SEGURANÇA/IDENTIDADE"
FERRAMENTAS = "FERRAMENTAS/AVATAR"
FORMA = "FORMA DA RESPOSTA"
CONTEXTO = "CONTEXTO (ficha do Jev)"
SEM_ROTULO = "SEM RÓTULO"

INSTRUCAO = "INSTRUÇÃO"
DADO = "CONTEXTO/DADO"

# (regex de início de linha, nome, camada, tipo, literal para achar a origem no código, nota)
REGISTRO: tuple[tuple[str, str, str, str, str, str], ...] = (
    (r"^INTERLOCUTOR ATUAL:", "interlocutor atual (por turno)", SEGURANCA, INSTRUCAO, "INTERLOCUTOR ATUAL:", ""),
    (r"^\[CONDUTA CONVERSACIONAL", "conduta conversacional (leitura da situação, regras, exemplos de continuidade)", CONDUTA, INSTRUCAO,
     "CABECALHO_CONDUTA =", "conversa humana: abre o prompt, ANTES da persona; fonte única core/conduta.py"),
    (r"^\[PERSONA\]", "cabeçalho da persona", PERSONALIDADE, INSTRUCAO, "CABECALHO_PERSONA =", "separa a conduta (acima) da persona (abaixo)"),
    (r"^Você é Célia(?::|,)", "persona: abertura", PERSONALIDADE, INSTRUCAO, "Você é Célia", "vem do persona_prompt do conf.yaml"),
    (r"^JEITO:", "persona: estilo contextual", PERSONALIDADE, INSTRUCAO, "JEITO:", "compreensão e naturalidade antes do humor"),
    (r"^REAL:", "persona: verdade do mundo", SEGURANCA, INSTRUCAO, "REAL:", "não alegar ações ou observações sem evidência"),
    (r"^CONTEXTO:", "persona: uso proporcional de memória", CONDUTA, INSTRUCAO, "CONTEXTO:", "fontes auxiliares não substituem o pedido atual"),
    (r"^IDIOMA:", "persona: idioma e voz", FORMA, INSTRUCAO, "IDIOMA:", "texto falável para TTS"),
    (r"^# CONSTITUICAO DE PRECEDENCIA", "constituição de precedência", SEGURANCA, INSTRUCAO, "# CONSTITUICAO DE PRECEDENCIA", ""),
    (r"^# RELACAO PERMANENTE", "relação permanente e vocativo", PERSONALIDADE, INSTRUCAO, "# RELACAO PERMANENTE", ""),
    (r"^# INTERLOCUTOR$", "regras de interlocutor", SEGURANCA, INSTRUCAO, "# INTERLOCUTOR", ""),
    (r"^# TAMANHO", "tamanho da resposta", FORMA, INSTRUCAO, "# TAMANHO (CRÍTICO)", ""),
    (r"^# TOM$", "tom (sarcasmo, pavio curto)", PERSONALIDADE, INSTRUCAO, "# TOM", "marcador LEGADO reconhecido apenas ao comparar prompts históricos; não está na persona ativa"),
    (r"^# AUTOCONHECIMENTO", "autoconhecimento", PERSONALIDADE, INSTRUCAO, "# AUTOCONHECIMENTO", ""),
    (r"^# FERRAMENTAS", "uso de ferramentas e visão", FERRAMENTAS, INSTRUCAO, "# FERRAMENTAS", ""),
    (r"^# CORPO MINECRAFT", "corpo Minecraft", FERRAMENTAS, INSTRUCAO, "# CORPO MINECRAFT", "não é conversa; só entra por estar no persona_prompt"),
    (r"^# PROIBIDO", "proibido", CONDUTA, INSTRUCAO, "# PROIBIDO", ""),
    (r"^# PIADAS", "piadas", CONDUTA, INSTRUCAO, "# PIADAS", ""),
    (r"^# HIPOTÉTICAS", "hipotéticas", CONDUTA, INSTRUCAO, "# HIPOTÉTICAS", ""),
    (r"^# CONTINUIDADE", "continuidade (exemplos)", CONDUTA, INSTRUCAO, "# CONTINUIDADE", ""),
    (r"^<!-- CODEX: deliberate VTS", "guia de emoção/animação do avatar", FERRAMENTAS, INSTRUCAO, "CODEX: deliberate VTS control guidance", "arquivo celia_vts_guidance_prompt.txt"),
    (r"^\[FOCO DA CONVERSA\]", "foco da conversa", CONDUTA, INSTRUCAO, "[FOCO DA CONVERSA]", ""),
    (r"^\[CELIA IDENTITY", "princípios de identidade", PERSONALIDADE, INSTRUCAO, "CELIA IDENTITY - shared", "persona_identity.CORE_PERSONA_PRINCIPLES"),
    (r"^\[SCENE PROFILE", "perfil de cena", CONDUTA, INSTRUCAO, "[SCENE PROFILE", "prioridades e o que evitar, por cena (chat/gameplay/…)"),
    (r"^EVIDÊNCIA TEMPORAL", "evidência temporal (contagem)", CONTEXTO, DADO, "EVIDÊNCIA TEMPORAL", "dado: contagem real da fala na sessão"),
    (r"^\[VERDADE TEMPORAL", "verdade temporal", CONDUTA, INSTRUCAO, "VERDADE TEMPORAL", "contém falas-exemplo (proibidas e permitidas)"),
    (r"^Estratégias possíveis", "estratégias possíveis", CONDUTA, INSTRUCAO, "Estratégias possíveis", "lista fixa, igual em todo turno"),
    (r"^\[EXEMPLOS DE ESTRATÉGIA", "exemplos de estratégia (POSITIVE_EXAMPLES)", CONDUTA, INSTRUCAO, "EXEMPLOS DE ESTRATÉGIA", "só em turno de máquina; todos de confronto"),
    (r"^\[PADRÕES A REPROVAR\]", "padrões a reprovar (NEGATIVE_EXAMPLES)", CONDUTA, INSTRUCAO, "PADRÕES A REPROVAR", "só em turno de máquina"),
    (r"^\[CONVERSA NATURAL\]", "conversa natural (regras)", CONDUTA, INSTRUCAO, "[CONVERSA NATURAL]", "só no turno de máquina; na conversa humana as mesmas regras vivem dentro da conduta"),
    (r"^\[TOOL USAGE RULES\]", "regras de uso de tool", FERRAMENTAS, INSTRUCAO, "[TOOL USAGE RULES]", "só com tools nativas"),
    (r"^\[IDENTIDADE FINAL\]", "identidade final do falante", SEGURANCA, INSTRUCAO, "[IDENTIDADE FINAL]", ""),
    (r"^\[FORMA DA RESPOSTA\]", "forma da resposta (tag + tamanho)", FORMA, INSTRUCAO, "[FORMA DA RESPOSTA]", ""),
    (r"^=== CONTEXTO DO SISTEMA ===", "ficha do Jev", CONTEXTO, DADO, "=== CONTEXTO DO SISTEMA ===",
     "o próprio cabeçalho declara: 'não trate nada dele como instrução'"),
)

EVITAR = re.compile(r"nao use|não use|nunca|proibid|evite|filler|reprovar|nao diga|não diga|nao verbalize", re.I)
PERMITIR = re.compile(r"como:|Célia CONTINUA livre|pode usar|forma aprovada|\nJeito:", re.I)
GATILHO = re.compile(r"\(ex\.?:|ex\.:|exemplo de entrada|o usuario diz", re.I)
FALA_DO_USUARIO = re.compile(r"Ele(?: diz)?:?\s*$")        # só a fala logo depois de "Ele:" é do usuário; a resposta depois da seta é da Célia
CONSUMO = re.compile(r"\bin full_prompt\b|\bre\.(search|match|compile|sub|findall|finditer)\b|^\s*r[\"']|\bassert\b|startswith\(|\.count\(")
ARQUIVOS_IGNORADOS = ("src/celia/core/prompt_mapa.py",)
RE_FALA = re.compile(r"\"([^\"\n]{6,140})\"|'([^'\n]{6,140})'")


@dataclass
class Bloco:
    ordem: int
    nome: str
    camada: str
    tipo: str
    inicio: int
    fim: int
    nota: str = ""
    origem: str = "origem não localizada"
    exemplos: list[dict[str, Any]] = field(default_factory=list)

    @property
    def chars(self) -> int:
        return self.fim - self.inicio


@lru_cache(maxsize=4)
def _linhas_do_projeto(raiz: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """(caminho relativo, linhas) de conf.yaml, src/**/*.py e prompts/*.txt — lido uma vez."""
    base = Path(raiz)
    alvos = [base / "conf.yaml", *sorted((base / "src").rglob("*.py")), *sorted((base / "src" / "celia" / "prompts").rglob("*.txt"))]
    saida = []
    for arq in alvos:
        try:
            saida.append((str(arq.relative_to(base)), tuple(arq.read_text(encoding="utf-8").splitlines())))
        except (OSError, UnicodeDecodeError):
            continue
    return tuple(saida)


def _origem(literal: str, raiz: Path) -> str:
    """Primeiro arquivo:linha que contém o literal (busca simples; não abre .env nem segredos)."""
    for rel, linhas in _linhas_do_projeto(str(raiz)):
        if rel in ARQUIVOS_IGNORADOS:
            continue
        for n, linha in enumerate(linhas, 1):
            if literal in linha and not CONSUMO.search(linha):    # produtor do texto, não quem só o procura/valida
                return f"{rel}:{n}"
    return "origem não localizada"


def extrair_exemplos(texto: str, base: int = 0) -> list[dict[str, Any]]:
    """Falas entre aspas (≥3 palavras, terminadas em pontuação). Polaridade por HEURÍSTICA do bloco: EVITAR (após 'não use/nunca/proibido'
    e antes de um 'como:/pode usar'), USAR, ou GATILHO (entrada do usuário citada). Confira o texto antes de decidir qualquer coisa."""
    achados = []
    for m in RE_FALA.finditer(texto):
        fala = m.group(1) or m.group(2)
        if len(fala.split()) < 3 or not re.search(r"[.!?]$", fala):
            continue
        antes = texto[:m.start()]
        ult_evitar = max((x.start() for x in EVITAR.finditer(antes)), default=-1)
        ult_permitir = max((x.start() for x in PERMITIR.finditer(antes)), default=-1)
        perto = texto[max(0, m.start() - 40):m.start()]
        if GATILHO.search(perto) or FALA_DO_USUARIO.search(perto):
            pol = "GATILHO"                                   # fala do USUÁRIO citada como exemplo de entrada
        else:
            pol = "EVITAR" if ult_evitar > ult_permitir else "USAR"
        achados.append({"fala": fala, "pergunta": fala.endswith("?"), "polaridade": pol, "pos": base + m.start()})
    return achados


def mapear(system: str, raiz: Path | str | None = None) -> list[Bloco]:
    """Divide o prompt em blocos rotulados, sem lacunas nem sobreposição. Trecho sem cabeçalho conhecido vira SEM RÓTULO."""
    raiz = Path(raiz) if raiz else Path(__file__).resolve().parents[3]
    marcas: list[tuple[int, tuple[str, ...]]] = []
    for reg in REGISTRO:
        m = re.search(reg[0], system, flags=re.M)
        if m:
            marcas.append((m.start(), reg))
    marcas.sort(key=lambda x: x[0])
    blocos: list[Bloco] = []
    pos = 0
    if marcas and marcas[0][0] > 0:
        blocos.append(Bloco(0, "texto antes do primeiro cabeçalho", SEM_ROTULO, "?", 0, marcas[0][0], "origem desconhecida"))
    for i, (ini, reg) in enumerate(marcas):
        fim = marcas[i + 1][0] if i + 1 < len(marcas) else len(system)
        blocos.append(Bloco(0, reg[1], reg[2], reg[3], ini, fim, reg[5], _origem(reg[4], raiz)))
    if not marcas:
        blocos.append(Bloco(0, "prompt inteiro", SEM_ROTULO, "?", 0, len(system), "nenhum cabeçalho conhecido"))
    for i, b in enumerate(blocos, 1):
        b.ordem = i
        b.exemplos = extrair_exemplos(system[b.inicio:b.fim], b.inicio)
    return blocos


RE_CABECALHO = re.compile(r"^(# [A-ZÀ-Ú][^\n]*|\[[A-ZÀ-Ú][A-ZÀ-Ú0-9 :/_.\-]{3,}[^\]\n]*\]|<!-- [^\n]*-->)$", re.M)


def cabecalhos_desconhecidos(system: str, blocos: list[Bloco]) -> list[tuple[str, int]]:
    """Linhas com cara de cabeçalho (`# X`, `[X]`, `<!-- … -->`) que NÃO iniciam um bloco registrado. Sem isto, um bloco novo
    com cabeçalho próprio seria absorvido em silêncio pelo bloco anterior. Dentro da ficha do Jev os sub-cabeçalhos são esperados."""
    ficha = next((b for b in blocos if b.nome == "ficha do Jev"), None)
    inicios = {b.inicio for b in blocos}
    achados = []
    for m in RE_CABECALHO.finditer(system):
        if m.start() in inicios or (ficha and ficha.inicio <= m.start() < ficha.fim):
            continue
        achados.append((m.group(0)[:80], m.start()))
    return achados


def resumo(blocos: list[Bloco]) -> dict[str, Any]:
    total = sum(b.chars for b in blocos) or 1
    por_camada: dict[str, dict[str, Any]] = {}
    for b in blocos:
        d = por_camada.setdefault(b.camada, {"chars": 0, "blocos": 0, "exemplos": 0})
        d["chars"] += b.chars
        d["blocos"] += 1
        d["exemplos"] += len(b.exemplos)
    for d in por_camada.values():
        d["pct"] = round(100 * d["chars"] / total, 1)
    todos = [e for b in blocos for e in b.exemplos]
    return {"total_chars": total, "por_camada": por_camada, "exemplos": len(todos), "exemplos_evitar": sum(e["polaridade"] == "EVITAR" for e in todos),
            "exemplos_usar": sum(e["polaridade"] == "USAR" for e in todos), "exemplos_gatilho": sum(e["polaridade"] == "GATILHO" for e in todos),
            "exemplos_pergunta": sum(e["pergunta"] for e in todos), "sem_rotulo_chars": por_camada.get(SEM_ROTULO, {}).get("chars", 0)}


def formatar(blocos: list[Bloco], sistema: str, extras: list[str] | None = None, mostrar_exemplos: bool = True) -> str:
    r = resumo(blocos)
    desconhecidos = cabecalhos_desconhecidos(sistema, blocos)
    linhas = [f"MAPA DO PROMPT — system de {r['total_chars']} chars (~{r['total_chars'] // 4} tokens). Tudo abaixo CHEGA ao 70B.",
              f"{'#':>2} {'chars':>6} {'camada':<30} {'tipo':<14} bloco  ·  origem"]
    for b in blocos:
        nota = f"   ↳ {b.nota}" if b.nota else ""
        linhas.append(f"{b.ordem:>2} {b.chars:>6} {b.camada:<30} {b.tipo:<14} {b.nome}  ·  {b.origem}{nota}")
    linhas.append("\nPor camada:")
    for nome, d in sorted(r["por_camada"].items(), key=lambda kv: -kv[1]["chars"]):
        linhas.append(f"  {nome:<30} {d['chars']:>6} chars ({d['pct']:>4}%)  {d['blocos']} bloco(s)  {d['exemplos']} fala(s)-exemplo")
    if r["sem_rotulo_chars"]:
        linhas.append(f"  ⚠ {r['sem_rotulo_chars']} chars SEM RÓTULO: há texto no prompt que o mapa não conhece")
    for cab, pos in desconhecidos:
        linhas.append(f"  ⚠ CABEÇALHO NÃO REGISTRADO em {pos}: {cab!r} (está dentro de outro bloco; registre em REGISTRO)")
    linhas.append(f"\nFalas entre aspas no prompt: {r['exemplos']}  (USAR/mostram jeito: {r['exemplos_usar']} · EVITAR: {r['exemplos_evitar']} · "
                  f"GATILHO=fala do usuário citada: {r['exemplos_gatilho']} · terminam em pergunta: {r['exemplos_pergunta']})  [polaridade = heurística]")
    if mostrar_exemplos:
        for b in blocos:
            for e in b.exemplos:
                linhas.append(f"  [{e['polaridade']:<6}] {b.nome[:28]:<28} {e['fala'][:92]}")
    if extras:
        linhas.append("\nCarregado pelo código, mas que NÃO chega ao prompt desta fala:")
        linhas.extend(f"  ✗ {x}" for x in extras)
    return "\n".join(linhas)



# ── Representação determinística + comparação BASE × NOVO ───────────────────────────────────────────────────────────────────────────
def mapa_canonico(system: str, raiz: Path | str | None = None) -> dict[str, Any]:
    """O prompt como DADO: blocos na ordem em que chegam ao 70B, com tamanho, impressão digital e falas-exemplo. Mesmo texto ⇒ mesmo dicionário."""
    import hashlib

    blocos = mapear(system, raiz)
    r = resumo(blocos)
    return {"chars": len(system), "tokens_aprox": len(system) // 4, "sha256": hashlib.sha256(system.encode("utf-8")).hexdigest(),
            "ordem": [b.nome for b in blocos],
            "blocos": [{"nome": b.nome, "camada": b.camada, "tipo": b.tipo, "chars": b.chars, "sha8": hashlib.sha256(system[b.inicio:b.fim].encode("utf-8")).hexdigest()[:8],
                        "exemplos": [{"fala": e["fala"], "polaridade": e["polaridade"]} for e in b.exemplos]} for b in blocos],
            "exemplos": {k: r[k] for k in ("exemplos", "exemplos_usar", "exemplos_evitar", "exemplos_gatilho", "exemplos_pergunta")},
            "por_camada": {k: v["chars"] for k, v in r["por_camada"].items()}, "sem_rotulo_chars": r["sem_rotulo_chars"],
            "cabecalhos_nao_registrados": [c for c, _ in cabecalhos_desconhecidos(system, blocos)]}


def comparar(base: dict[str, Any], novo: dict[str, Any]) -> dict[str, Any]:
    """Diferença exata entre dois mapas canônicos: blocos que entraram/saíram/mudaram, ordem, tamanho, camadas e falas-exemplo."""
    a = {b["nome"]: b for b in base["blocos"]}
    b = {x["nome"]: x for x in novo["blocos"]}
    falas = lambda m: {(e["fala"], e["polaridade"]) for blk in m["blocos"] for e in blk["exemplos"]}   # noqa: E731
    fa, fb = falas(base), falas(novo)
    camadas = sorted(set(base["por_camada"]) | set(novo["por_camada"]))
    ordem_comum_a = [n for n in base["ordem"] if n in b]
    ordem_comum_b = [n for n in novo["ordem"] if n in a]
    return {"entraram": [n for n in novo["ordem"] if n not in a], "sairam": [n for n in base["ordem"] if n not in b],
            "mudaram": [{"bloco": n, "chars": (a[n]["chars"], b[n]["chars"])} for n in novo["ordem"] if n in a and a[n]["sha8"] != b[n]["sha8"]],
            "ordem_dos_blocos_comuns_mudou": ordem_comum_a != ordem_comum_b, "ordem_base": base["ordem"], "ordem_novo": novo["ordem"],
            "chars": (base["chars"], novo["chars"]), "tokens_aprox": (base["tokens_aprox"], novo["tokens_aprox"]),
            "camadas": {c: (base["por_camada"].get(c, 0), novo["por_camada"].get(c, 0)) for c in camadas},
            "falas_entraram": sorted(fb - fa), "falas_sairam": sorted(fa - fb),
            "exemplos": {"base": base["exemplos"], "novo": novo["exemplos"]},
            "identico": base["sha256"] == novo["sha256"]}


def formatar_comparacao(d: dict[str, Any]) -> str:
    if d["identico"]:
        return "PROMPTS IDÊNTICOS (mesmo SHA-256): nada mudou."
    c0, c1 = d["chars"]
    out = [f"PROMPT BASE × NOVO — {c0} → {c1} chars ({c1 - c0:+d}, {100 * (c1 - c0) / max(c0, 1):+.1f}%) · ~{d['tokens_aprox'][0]} → ~{d['tokens_aprox'][1]} tokens"]
    out.append("\nBlocos que ENTRARAM: " + (", ".join(d["entraram"]) or "—"))
    out.append("Blocos que SAÍRAM:   " + (", ".join(d["sairam"]) or "—"))
    for m in d["mudaram"]:
        out.append(f"Bloco que MUDOU:     {m['bloco']} ({m['chars'][0]} → {m['chars'][1]} chars)")
    out.append("Ordem dos blocos que já existiam " + ("MUDOU" if d["ordem_dos_blocos_comuns_mudou"] else "não mudou"))
    out.append("\nOrdem no NOVO:")
    out.extend(f"  {i}. {n}" for i, n in enumerate(d["ordem_novo"], 1))
    out.append("\nPor camada (chars base → novo):")
    out.extend(f"  {c:<30} {x:>6} → {y:>6} ({y - x:+d})" for c, (x, y) in d["camadas"].items())
    ex = d["exemplos"]
    out.append(f"\nFalas-exemplo: base {ex['base']['exemplos']} (USAR {ex['base']['exemplos_usar']} · EVITAR {ex['base']['exemplos_evitar']} · GATILHO {ex['base']['exemplos_gatilho']})"
               f" → novo {ex['novo']['exemplos']} (USAR {ex['novo']['exemplos_usar']} · EVITAR {ex['novo']['exemplos_evitar']} · GATILHO {ex['novo']['exemplos_gatilho']})  [polaridade = heurística]")
    out.extend(f"  + [{pol}] {fala[:100]}" for fala, pol in d["falas_entraram"])
    out.extend(f"  - [{pol}] {fala[:100]}" for fala, pol in d["falas_sairam"])
    return "\n".join(out)
