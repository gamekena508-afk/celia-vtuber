"""Memória episódica dos vídeos do canal (sem LLM no turno).

Ingestão: var/video-memoria/analise/*.json -> var/memoria/videos.json (determinístico, idempotente).
Estado mutável (força / último acesso): var/memoria/videos_estado.json (a reingestão não o zera).
Recuperação por turno: léxica PT-BR + pessoa + humor, com limiar e orçamento de caracteres.
Flag: CELIA_VIDEO_MEMORY (padrão ligado; 0/false/off desliga).
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
import unicodedata
from pathlib import Path
from typing import Any, Iterable

from .raizes import VAR

VERSAO = 1
ANALISE_DIR = VAR / "video-memoria" / "analise"
ARQ_MEMORIA = VAR / "memoria" / "videos.json"
ARQ_ESTADO = VAR / "memoria" / "videos_estado.json"

# --- parâmetros (documentados em docs/auditoria-2026-11-12/MEMORIA-VIDEOS.md) ---
BUDGET_CHARS = 450          # total por turno, incluindo a regra curta
MAX_LEMBRANCAS = 3
REL_MIN = 0.34              # cobertura mínima (soma de idf casados / soma de idf da pergunta)
IDF_MIN = 2.2               # evidência mínima casada (evita "um token fraco" virar lembrança)
GENERICO_DF = 0.20          # token em >20% dos documentos não discrimina
COOLDOWN_TURNOS = 6         # não repete a mesma lembrança logo em seguida
COOLDOWN_PESSOA = 12
FLOOR = 0.20                # esquecer enfraquece até 20% da base, nunca apaga
REFORCO = 0.15              # ao ser lembrada: força += 0.15*(1-força)
RESUMO_REGRA = "Regra: só cite lembrança se for relevante; não invente detalhes além do registro."

MEIA_VIDA_DIAS = {"episodio": 60.0, "comentario": 90.0, "personalidade": 180.0,
                  "piada": 120.0, "gosto": 365.0, "lore": 365.0, "dono": 3650.0}
PESO_TIPO = {"defesa_do_criador": 0.9, "hater": 0.8, "bitcoin": 0.8, "aprovacao": 0.8, "carinhoso": 0.7,
             "irritado": 0.7, "engracado": 0.6, "provocativo": 0.6, "dilema": 0.5, "espontaneo": 0.5,
             "bug": 0.4, "jogo": 0.4, "musica": 0.4, "tecnico": 0.2, "outro": 0.1}
CONFLITO_TIPOS = {"hater", "irritado", "defesa_do_criador"}
PESO_CERTEZA = {"alta": 1.0, "media": 0.85, "baixa": 0.5}
PESO_RANK = {"dono": 1.2, "episodio": 1.0, "gosto": 0.95, "lore": 0.95, "personalidade": 0.8,
             "piada": 0.8, "comentario": 0.7}

# --------------------------------------------------------------------------- texto


def enabled() -> bool:
    return os.environ.get("CELIA_VIDEO_MEMORY", "1").strip().lower() not in ("0", "false", "off", "no", "")


def norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


_STOP = set("""a o as os e em um uma uns umas de da do das dos para por pra pro com sem que se na no nas nos eu tu voce vc
ele ela eles elas te me meu minha teu tua seu sua nao sim ja foi ser ter tem tinha mais muito muita mas ou como quando
onde qual quem isso isto essa esse esta este aquele aquela aqui ali la oi ola eae opa tudo bem entao so ai tipo coisa
lembra lembro lembrar lembranca falou fala falar disse diz sobre vai vou era sao to ta tao nem ate la mesmo fez fazer
ainda depois antes tambem porque pq cade pode posso quer quero gosta gosto gostar gostou curte sabe sabia""".split())
_SUFIXOS = sorted(["amentos", "imentos", "amento", "imento", "acoes", "coes", "acao", "cao", "mente", "adores",
                   "ador", "ando", "endo", "indo", "aram", "eram", "iram", "adas", "ados", "ada", "ado", "idas",
                   "idos", "ida", "ido", "ou", "ar", "er", "ir", "ei", "as", "os", "es", "s", "a", "o", "e"],
                  key=len, reverse=True)


_GOSTO_Q = re.compile(r"\b(gosta|gosto|gostar|curte|curtir|favorit\w*|ama|adora|prefere)\b")


def stem(w: str) -> str:
    if w.isdigit():
        return w
    for suf in _SUFIXOS:
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            return w[: -len(suf)]
    return w


_SINONIMOS = [
    ["natal", "natalino", "ceia", "noel"],
    ["xadrez", "xeque", "mate", "tabuleiro", "peao"],
    ["bitcoin", "btc", "cripto", "criptomoeda", "minerar", "mineracao", "wallet"],
    ["hater", "haters", "hate", "odio", "troll"],
    ["censura", "censurar", "censurada", "censurou", "filtro"],
    ["discord", "dc"],
    ["defender", "defende", "defesa", "proteger"],
    ["tita", "titas", "titan", "aot", "shingeki", "attack", "ataque"],
    ["minecraft", "mine", "mc"],
    ["meme", "memes", "zoeira"],
    ["spoiler", "spoilers"],
    ["comida", "comer", "alimento", "lanche", "cookie", "cookies", "biscoito", "biscoitos"],
]
_CANON: dict[str, str] = {}
for _g in _SINONIMOS:
    _c = stem(_g[0])
    for _m in _g:
        _CANON[stem(_m)] = _c


def toks(text: str) -> list[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", norm(text)):
        if w in _STOP or len(w) < 2:
            continue
        s = stem(w)
        out.append(_CANON.get(s, s))
    return out


def chave_pessoa(nome: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(nome).lstrip("@"))


def _curto(txt: str, n: int) -> str:
    txt = " ".join(str(txt or "").split())
    if len(txt) <= n:
        return txt
    cut = txt[: max(1, n - 1)]
    if " " in cut[n // 2:]:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,.;:-") + "…"


def titulo_curto(titulo: str) -> str:
    t = re.sub(r"[#,]\S*", " ", str(titulo or ""))
    t = re.sub(r"[|!?.]+", " ", t)
    palavras = t.split()
    if sum(1 for p in palavras if p.isupper() and len(p) > 2) > len(palavras) / 2:
        palavras = [p if p in ("IA", "VS") or len(p) > 3 and not p.isupper() else (p.capitalize() if len(p) > 3 else p.lower())
                    for p in palavras]
    out: list[str] = []
    for p in palavras:
        if len(" ".join(out + [p])) > 24:
            break
        out.append(p)
    t = " ".join(out) or "vídeo"
    return t[:1].upper() + t[1:]


def _jac(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


def _mmss(t: str) -> str:
    return str(t or "").strip() or "?"


# ----------------------------------------------------------------------- ingestão


def _id(kind: str, video: str, texto: str) -> str:
    return hashlib.sha1(f"{kind}|{video}|{norm(texto)[:200]}".encode("utf-8")).hexdigest()[:12]


def _certeza(v: Any) -> str:
    v = norm(v)
    return v if v in PESO_CERTEZA else "media"


def importancia_episodio(ep: dict[str, Any]) -> float:
    """informação nova (30%) + destaque (45%: aprovação do criador pesa mais que o tipo) + emoção (30%)."""
    reacao = str(ep.get("reacao_celia") or "")
    sem_reacao = bool(re.match(r"\s*(sem|nenhuma|n[aã]o h[aá])\b", reacao.lower())) or not reacao.strip()
    nov = 0.4 + (0.0 if sem_reacao else 0.3) + (0.3 if ep.get("falas_chave") else 0.0)
    aprov = 1.0 if (ep.get("criador_aprovou") or {}).get("sim") or "aprovacao" in (ep.get("tipos") or []) else 0.0
    tipo_w = max([PESO_TIPO.get(t, 0.1) for t in ep.get("tipos") or []] or [0.1])
    dest = min(1.0, 0.65 * aprov + 0.35 * tipo_w)
    emo_txt = norm(ep.get("emocao_celia"))
    emo = 0.0 if (not emo_txt or emo_txt.startswith("neutr")) else 1.0
    try:
        q = (max(1, min(5, int(ep.get("qualidade_reacao") or 3))) - 1) / 4
    except (TypeError, ValueError):
        q = 0.5
    imp = 0.30 * nov + 0.45 * dest + 0.30 * (0.7 * emo + 0.3 * q)
    return round(max(0.05, min(1.0, imp * PESO_CERTEZA[_certeza(ep.get("certeza"))])), 4)


def _tipos_ep(ep: dict[str, Any]) -> list[str]:
    out = []
    for t in ep.get("tipos") or []:
        for p in str(t).split("|"):
            p = norm(p).strip()
            if p and p not in out:
                out.append(p)
    return out


def _lista(d: dict[str, Any], k: str) -> list[dict[str, Any]]:
    v = d.get(k)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def _indexa(it: dict[str, Any]) -> str:
    tipos = " ".join(t for t in it.get("tipos") or [] if t in ("bitcoin", "hater", "musica", "jogo"))
    return " ".join(str(it.get(k) or "") for k in ("texto", "reacao", "evidencia", "autor", "extra")) + " " + tipos


def _fatos_dono() -> list[dict[str, Any]]:
    base = [
        ("A Célia tem uma carteira de Bitcoin. É lore da personagem, nunca alegação de fundos reais.",
         "carteira bitcoin cripto wallet"),
        ("A Célia minera Bitcoin no PC do criador. É lore/brincadeira da personagem, não mineração real nem dinheiro de verdade.",
         "minera mineração bitcoin cripto PC do criador"),
        ("A Célia defende o criador dos haters.", "defende defesa hater haters criador"),
        ("A Célia curte os memes de hater.", "memes hater haters zoeira"),
    ]
    out = []
    for texto, extra in base:
        out.append({"id": _id("dono", "dono", texto), "kind": "dono", "video_id": "", "titulo": "",
                    "t": "", "texto": texto, "evidencia": "lore fixa dada pelo dono em 12/11/2026",
                    "extra": extra, "certeza": "alta", "origem": "dono", "tipos": ["bitcoin" if "Bitcoin" in texto else "hater"],
                    "importancia": 0.9, "confirmacoes": 0})
    return out


def _negacao(texto: str) -> bool:
    return bool(re.search(r"\b(nao|nunca|so leu|apenas leu)\b", norm(texto)))


def ingerir(analise_dir: Path | None = None, destino: Path | None = None) -> dict[str, Any]:
    """Lê todas as análises e grava videos.json. Reprocessável: mesma entrada -> mesmo arquivo."""
    analise_dir = Path(analise_dir or ANALISE_DIR)
    destino = Path(destino or ARQ_MEMORIA)
    itens: list[dict[str, Any]] = []
    fontes: list[dict[str, Any]] = []
    pessoas: dict[str, dict[str, Any]] = {}
    ignorados: list[str] = []
    for f in sorted(analise_dir.glob("*.json")):
        try:
            raw = f.read_bytes()
            d = json.loads(raw.decode("utf-8-sig"))
            if not isinstance(d, dict) or not d.get("video_id"):
                raise ValueError("sem video_id")
        except Exception as exc:  # noqa: BLE001 - arquivo em escrita/inválido: fica para a próxima rodada
            ignorados.append(f"{f.name}: {exc}")
            continue
        vid = str(d["video_id"])
        tit = titulo_curto(d.get("titulo") or vid)
        fontes.append({"video_id": vid, "titulo": str(d.get("titulo") or ""), "titulo_curto": tit,
                       "data": str(d.get("data") or ""), "url": str(d.get("url") or ""),
                       "sha256": hashlib.sha256(raw).hexdigest()[:16]})
        base = {"video_id": vid, "titulo": tit}
        for ep in _lista(d, "episodios"):
            ap = ep.get("criador_aprovou") if isinstance(ep.get("criador_aprovou"), dict) else {}
            texto = str(ep.get("o_que_aconteceu") or "").strip()
            if not texto:
                continue
            falas = " ".join(str(x.get("texto") or "") for x in ep.get("falas_chave") or [] if isinstance(x, dict))
            itens.append({**base, "id": _id("episodio", vid, str(ep.get("id") or "") + texto), "kind": "episodio",
                          "t": _mmss(ep.get("inicio")), "fim": _mmss(ep.get("fim")), "texto": texto,
                          "reacao": str(ep.get("reacao_celia") or "").strip(), "extra": falas,
                          "criador_aprovou": bool(ap.get("sim")), "criador_citacao": str(ap.get("citacao") or "").strip(),
                          "tipos": _tipos_ep(ep), "emocao": str(ep.get("emocao_celia") or ""),
                          "certeza": _certeza(ep.get("certeza")), "origem": "video",
                          "importancia": importancia_episodio(ep), "confirmacoes": 0})
        for kind, chave, campo in (("gosto", "gostos_da_celia", "coisa"), ("lore", "lore_da_celia", "fato"),
                                   ("personalidade", "personalidade_demonstrada", "traco"),
                                   ("piada", "piadas_que_funcionaram", "texto")):
            for x in _lista(d, chave):
                texto = str(x.get(campo) or "").strip()
                if not texto:
                    continue
                imp = {"gosto": 0.55, "lore": 0.6, "personalidade": 0.5, "piada": 0.45}[kind]
                itens.append({**base, "id": _id(kind, vid, texto), "kind": kind, "t": _mmss(x.get("t")),
                              "texto": texto, "reacao": "",
                              "evidencia": str(x.get("evidencia") or x.get("por_que") or "").strip(),
                              "certeza": "alta" if x.get("evidencia") or x.get("por_que") else "media",
                              "origem": "video", "tipos": [], "importancia": imp, "confirmacoes": 0})
        for c in _lista(d, "comentaristas"):
            autor = str(c.get("autor") or "").strip()
            texto = str(c.get("texto") or "").strip()
            k = chave_pessoa(autor)
            if not k or not texto:
                continue
            sent = norm(c.get("sentimento")) or "neutro"
            p = pessoas.setdefault(k, {"autor": autor.lstrip("@") and autor, "sentimento": sent, "hater": False,
                                       "videos": [], "mencoes": 0})
            if sent == "hater":
                p["hater"] = True
                p["sentimento"] = "hater"
            elif p["sentimento"] != "hater" and sent == "negativo":
                p["sentimento"] = "negativo"
            elif p["sentimento"] not in ("hater", "negativo") and sent == "positivo":
                p["sentimento"] = "positivo"
            if vid not in p["videos"]:
                p["videos"].append(vid)
            p["mencoes"] += 1
            if not c.get("vale_lembrar"):
                continue
            curt = int(c.get("curtidas") or 0) if str(c.get("curtidas") or "0").lstrip("-").isdigit() else 0
            imp = 0.35 + 0.25 * min(1.0, math.log1p(max(0, curt)) / math.log1p(20)) + (0.2 if sent in ("hater", "negativo") else 0.0)
            itens.append({**base, "id": _id("comentario", vid, autor + texto), "kind": "comentario", "t": "",
                          "texto": texto, "reacao": "", "autor": autor, "pessoa": k, "sentimento": sent,
                          "evidencia": str(c.get("por_que") or "").strip(), "certeza": "alta", "origem": "video",
                          "tipos": ["hater"] if sent == "hater" else [], "curtidas": curt,
                          "importancia": round(min(1.0, imp), 4), "confirmacoes": 0})
    itens = _dedup(itens) + _fatos_dono()
    contr = _contradicoes(itens)
    for it in itens:
        it["conflito"] = bool(set(it.get("tipos") or []) & CONFLITO_TIPOS or it.get("sentimento") in ("hater", "negativo"))
    out = {"versao": VERSAO, "fontes": fontes, "itens": itens, "pessoas": pessoas, "contradicoes": contr,
           "ignorados": ignorados}
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, destino)
    return out


def _dedup(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fatos de gosto/lore/personalidade quase idênticos entre vídeos viram um (confirmações sobem a importância)."""
    out: list[dict[str, Any]] = []
    vistos: dict[str, list[tuple[set[str], dict[str, Any]]]] = {}
    for it in itens:
        if it["kind"] in ("gosto", "lore", "personalidade", "piada"):
            s = set(toks(it["texto"]))
            alvo = next((o for st, o in vistos.get(it["kind"], []) if _jac(s, st) >= 0.7), None)
            if alvo is not None:
                alvo["confirmacoes"] += 1
                alvo["importancia"] = round(min(1.0, alvo["importancia"] + 0.05), 4)
                alvo.setdefault("tambem_em", [])
                if it["video_id"] not in alvo["tambem_em"] and it["video_id"] != alvo["video_id"]:
                    alvo["tambem_em"].append(it["video_id"])
                continue
            vistos.setdefault(it["kind"], []).append((s, it))
        out.append(it)
    return out


def _contradicoes(itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lore negativa dita pelo criador vs. gosto/afirmação da Célia sobre a mesma coisa: a versão do criador vence."""
    out = []
    for neg in itens:
        if neg["kind"] != "lore" or not _negacao(neg["texto"]):
            continue
        fonte = norm(neg["texto"] + " " + neg.get("evidencia", ""))
        if "criador" not in fonte and "criador" not in fonte:
            continue
        t_neg = set(toks(neg["texto"] + " " + neg.get("evidencia", "")))
        for g in itens:
            if g["kind"] != "gosto":
                continue
            chave = set(toks(re.sub(r"\(.*?\)", "", g["texto"])))
            if chave and len(chave & t_neg) / len(chave) >= 0.6:
                out.append({"id": _id("contradicao", g["id"], neg["id"]), "assunto": g["texto"],
                            "versao_celia": g["texto"], "id_celia": g["id"],
                            "versao_criador": neg["texto"], "id_criador": neg["id"], "vence": "criador"})
    return out


# ----------------------------------------------------------------------- estado


def _forca(base: float, imp: float, last: float, kind: str, agora: float) -> float:
    dias = max(0.0, (agora - last) / 86400.0)
    hl = MEIA_VIDA_DIAS.get(kind, 90.0)
    piso = FLOOR * imp
    return round(piso + max(0.0, base - piso) * 0.5 ** (dias / hl), 5)


class Memoria:
    def __init__(self, arq: Path | None = None, estado: Path | None = None) -> None:
        self.arq = Path(arq or ARQ_MEMORIA)
        self.arq_estado = Path(estado or ARQ_ESTADO)
        self._lock = threading.Lock()
        self._mtime = -1.0
        self.dados: dict[str, Any] = {}
        self.itens: list[dict[str, Any]] = []
        self.estado: dict[str, dict[str, float]] = {}
        self._tok: list[set[str]] = []
        self._ttok: list[set[str]] = []
        self._df: dict[str, int] = {}
        self._contr_celia: dict[str, dict[str, Any]] = {}
        self._turno = 0
        self._uso: dict[str, int] = {}

    # carga ---------------------------------------------------------------
    def carregar(self, forcar: bool = False) -> None:
        try:
            mt = self.arq.stat().st_mtime
        except OSError:
            self.dados, self.itens, self._tok, self._df = {}, [], [], {}
            self._mtime = -1.0
            return
        if not forcar and mt == self._mtime:
            return
        with self._lock:
            self.dados = json.loads(self.arq.read_text(encoding="utf-8"))
            self.itens = self.dados.get("itens", [])
            self._tok = [set(toks(_indexa(it))) for it in self.itens]
            self._ttok = [set(toks(self._titulo_de(it))) for it in self.itens]
            self._df = {}
            for s in self._tok:
                for t in s:
                    self._df[t] = self._df.get(t, 0) + 1
            self._contr_celia = {c["id_celia"]: c for c in self.dados.get("contradicoes", [])}
            self._mtime = mt
            try:
                self.estado = json.loads(self.arq_estado.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.estado = {}
            agora = time.time()
            for it in self.itens:
                self.estado.setdefault(it["id"], {"base": it["importancia"], "last": agora, "acessos": 0})

    def _titulo_de(self, it: dict[str, Any]) -> str:
        if it.get("titulo"):
            return it["titulo"]
        for f in self.dados.get("fontes", []):
            if f["video_id"] == it.get("video_id"):
                return f.get("titulo") or ""
        return ""

    def _salvar_estado(self) -> None:
        try:
            self.arq_estado.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.arq_estado.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.estado, ensure_ascii=False, sort_keys=True), encoding="utf-8", newline="\n")
            os.replace(tmp, self.arq_estado)
        except OSError:
            pass

    def forca(self, it: dict[str, Any], agora: float | None = None) -> float:
        e = self.estado.get(it["id"]) or {"base": it["importancia"], "last": time.time()}
        return _forca(float(e["base"]), it["importancia"], float(e["last"]), it["kind"], agora or time.time())

    def reforcar(self, it: dict[str, Any], agora: float | None = None) -> None:
        agora = agora or time.time()
        f = self.forca(it, agora)
        e = self.estado.setdefault(it["id"], {"base": it["importancia"], "last": agora, "acessos": 0})
        e["base"] = round(min(1.0, f + REFORCO * (1 - f)), 5)
        e["last"] = agora
        e["acessos"] = int(e.get("acessos", 0)) + 1

    # busca ---------------------------------------------------------------
    def _consulta(self, texto: str) -> dict[str, float]:
        n = max(1, len(self.itens))
        q: dict[str, float] = {}
        for t in set(toks(texto)):
            df = self._df.get(t, 0)
            if df == 0 or df / n > GENERICO_DF:
                continue
            q[t] = math.log(1 + max(n, 300) / df)  # corpus pequeno não infla a raridade
        return q

    def buscar(self, texto: str, *, pessoa: str = "", tensao: float = 0.0, owner: bool = False,
               agora: float | None = None, cooldown: bool = False) -> list[dict[str, Any]]:
        """Candidatos ordenados (não aplica orçamento). Cada resultado: {item, rel, score, motivo, texto_final}."""
        self.carregar()
        if not self.itens:
            return []
        normalizado = norm(texto)
        # "Não lembro o que falei ontem" relata um problema da pessoa que
        # fala; não é pedido para a Célia rememorar seus vídeos.
        if re.match(r"^(eu\s+)?nao\s+(me\s+)?lembro\b", normalizado):
            return []
        # Feitos recentes de outra pessoa nao sao episodios da Célia.
        if (re.match(r"^(eu\s+)?(organizei|mexi|terminei|acabei|consegui|configurei|instalei)\b", normalizado)
                and not re.search(r"\b(lembra|video|gravamos|gravei)\b", normalizado)):
            return []
        # Comentario sobre o proprio dia nao e automaticamente memoria de video.
        if (re.search(r"\b(meu|minha|meus|minhas)\b", normalizado)
                and "?" not in texto
                and not re.search(r"\b(lembra|lembro|lembranca|video|gravei|gravamos)\b", normalizado)):
            return []
        # Pergunta sobre pessoa exige nome ligado a um registro verificavel.
        pessoa_pergunta = bool(re.search(
            r"\b(quem e|quem eh|sabe sobre|conhece|quem foi)\b", normalizado))
        comida_preferencia = bool(
            re.search(r"\b(comer|comida|alimento|lanche)\b", normalizado)
            and _GOSTO_Q.search(normalizado))
        agora = agora or time.time()
        pks = set()
        if pessoa and not owner:
            pks.add(chave_pessoa(pessoa))
        for h in re.findall(r"@([\w.\-]+)", texto or ""):
            pks.add(chave_pessoa(h))
        pks = {k for k in pks if k in self.dados.get("pessoas", {})}
        # Não adicione recordações em perguntas gerais ou conversas correntes.
        # Ex.: "qual diferença CPU/GPU?" e "minha memória está bagunçada"
        # não são pedidos para recordar os vídeos da Célia.
        temas_fortes = set(toks(texto)) & {
            "xadrez", "bitcoin", "natal", "minecraft", "tita",
            "hater", "censura", "meme", "pokemon"
        }
        pedido_passado = bool(re.search(
            r"\b(lembra|lembro|lembrar|lembranca|esqueceu|esqueci|"
            r"naquele video|aquele video|no video|dos videos|gravamos|gravou)\b", normalizado
        ))
        pedido_gosto = bool(
            _GOSTO_Q.search(normalizado)
            and "?" in texto
            and re.search(r"\b(voce|tu|celia|ela)\b", normalizado)
        )
        # Uma pergunta sobre episódio específico com várias palavras-chave
        # AUSENTES do índice é provavelmente uma falsa memória. O match
        # parcial em "daquele"/"gente" não é evidência da cena.
        termos_pergunta = set(toks(texto))
        ausentes = {t for t in termos_pergunta if t not in self._df}
        if (pedido_passado and "?" in texto and not temas_fortes
                and len(termos_pergunta) >= 2
                and len(ausentes) / len(termos_pergunta) >= 0.34):
            return []
        q = self._consulta(texto)
        if not (pks or temas_fortes or pedido_passado or pedido_gosto):
            # Frases curtas retiradas dos vídeos, como "sorteio de pizza"
            # ou um comentário ofensivo, ainda podem ser buscadas se DOIS
            # termos raros coexistem na mesma lembrança. Perguntas novas
            # de conhecimento geral não disparam esse fallback.
            # Exceção para busca de um trecho literalmente dito no vídeo,
            # não para duas palavras desconexas do mesmo episódio.
            # "sorteio de pizza" e "essa IA é lixo..." são trechos reais;
            # "oi, gostei de você" NÃO deve puxar Natal.
            sequencia = toks(texto)
            ancora_frase = (
                "?" not in texto and 2 <= len(sequencia) <= 10
                and any(
                    " ".join(sequencia) in " ".join(toks(it.get("texto", "")))
                    for it in self.itens
                )
            )
            if not ancora_frase:
                return []
        tot = sum(q.values())
        hum = max(0.0, (float(tensao) - 30.0) / 70.0)
        res: list[dict[str, Any]] = []
        temas = set(toks(texto)) & {"xadrez", "bitcoin", "natal", "minecraft", "tita", "hater", "censura"}
        for it, tk, tt in zip(self.itens, self._tok, self._ttok):
            if comida_preferencia and it["kind"] != "gosto":
                continue
            # Nao inferir a identidade de pessoas conhecidas de videos de hardware.
            if pessoa_pergunta and not (
                it["kind"] == "comentario" and
                chave_pessoa(it.get("autor", "")) in pks
            ):
                continue
            # Se o assunto explicito e xadrez, nao trazer jogo da velha.
            if temas and not temas.intersection(tk) and not (
                it["kind"] == "comentario" and it.get("pessoa") in pks
            ):
                continue
            # O trecho sobre jogo da velha nao vira memoria de xadrez
            # apenas porque a fala-chave alude ao proximo jogo.
            if ("xadrez" in temas and it["kind"] == "episodio"
                    and "xadrez" not in set(toks(it.get("texto", "")))):
                continue
            if cooldown and self._turno - self._uso.get(it["id"], -10**9) <= (
                    COOLDOWN_PESSOA if it["kind"] == "comentario" else COOLDOWN_TURNOS):
                continue
            por_pessoa = it["kind"] == "comentario" and it.get("pessoa") in pks
            rel = 0.0
            if q:
                casado = sum(w for t, w in q.items() if t in tk) + 0.5 * sum(w for t, w in q.items() if t in tt and t not in tk)
                rel = casado / tot if tot else 0.0
                if casado < IDF_MIN or rel < REL_MIN:
                    rel = 0.0
            if rel == 0.0 and not por_pessoa:
                continue
            mult_humor = 1.0 + 0.8 * hum if it.get("conflito") else 1.0
            if por_pessoa and rel == 0.0:
                rel = 1.0
            rel_ef = rel * mult_humor
            f = self.forca(it, agora)
            ap = 1.35 if it.get("criador_aprovou") else 1.0
            gosto_q = 1.5 if (it["kind"] == "gosto" and _GOSTO_Q.search(norm(texto))) else 1.0
            score = gosto_q * rel_ef * (0.5 + 0.5 * f) * PESO_RANK.get(it["kind"], 0.8) * ap * PESO_CERTEZA[it.get("certeza", "media")]
            if por_pessoa:
                score += 10.0  # prioridade do comentarista conhecido
            res.append({"item": it, "rel": round(rel, 3), "score": round(score, 4), "forca": f,
                        "motivo": "pessoa" if por_pessoa else "texto"})
        res.sort(key=lambda r: -r["score"])
        donos = [r for r in res if r["item"]["kind"] == "dono"]
        if len(donos) > 1 and len(donos) < len(res):  # uma lore fixa por vez; o resto abre espaço p/ experiências vividas
            res = [r for r in res if r not in donos[1:]] + donos[1:]
        # contradição: a versão do criador substitui a da Célia; remove duplicados quase iguais
        ids = {r["item"]["id"] for r in res}
        criador_cobertos = {c["id_criador"] for cid, c in self._contr_celia.items() if cid in ids}
        final, vistos = [], []
        for r in res:
            it = r["item"]
            r["contradicao"] = self._contr_celia.get(it["id"])
            if it["id"] in criador_cobertos:
                continue
            s = set(toks(it["texto"]))
            if any(_jac(s, v) >= 0.6 for v in vistos):
                continue
            vistos.append(s)
            final.append(r)
        return final

    # linha ---------------------------------------------------------------
    def _linha(self, r: dict[str, Any], compacta: bool, limite_fato: int) -> str:
        it = r["item"]
        fontes = {f["video_id"]: f for f in self.dados.get("fontes", [])}
        tit = it.get("titulo") or (fontes.get(it["video_id"], {}).get("titulo_curto") or "")
        c = r.get("contradicao")
        if it["kind"] == "dono":
            return f"LEMBRANÇA (lore dada pelo criador em 12/11/2026, certeza alta): {_curto(it['texto'], limite_fato + 40)}"
        if it["kind"] == "comentario":
            alvo = {"hater": " (hater conhecido)", "negativo": " (crítico)"}.get(it.get("sentimento"), "")
            cab = f"LEMBRANÇA (vídeo «{tit}», comentário, certeza alta)"
            return f"{cab}: {it['autor']} já comentou que «{_curto(it['texto'], limite_fato)}»{alvo}"
        certeza = it.get("certeza", "media")
        cab = f"LEMBRANÇA (vídeo «{tit}», {it['t']}, certeza {certeza})"
        if certeza == "baixa":
            return f"{cab}: registro incerto, não afirme: {_curto(it['texto'], limite_fato)}"
        if c and c.get("vence") == "criador" and it["kind"] == "gosto":
            return (f"{cab}: o criador disse: {_curto(c['versao_criador'], limite_fato)} "
                    f"(ela já disse o contrário; vale a versão dele)")
        fato = _curto(it["texto"], limite_fato)
        if compacta:
            ju = " · Criador aprovou" if it.get("criador_aprovou") else ""
            return f"{cab}: {fato}{ju}"
        partes = [f"{cab}: {fato}"]
        if it.get("reacao"):
            partes.append(f"reação dela: {_curto(it['reacao'], 90)}")
        if it["kind"] == "episodio":
            if it.get("criador_aprovou"):
                cit = f" «{_curto(it['criador_citacao'], 40)}»" if it.get("criador_citacao") else ""
                partes.append(f"Criador: aprovou{cit}")
            else:
                partes.append("Criador: sem aprovação registrada")
        return " · ".join(partes)

    def compor(self, resultados: list[dict[str, Any]], budget: int = BUDGET_CHARS, maximo: int = MAX_LEMBRANCAS) -> list[str]:
        """Empacota linhas no orçamento (a regra curta conta no total)."""
        livre = budget - len(RESUMO_REGRA) - 1
        vistos_kind: set[str] = set()
        primeiros, resto = [], []
        for r in resultados:  # um de cada tipo primeiro (fato + experiência), depois o resto
            (resto if r["item"]["kind"] in vistos_kind else primeiros).append(r)
            vistos_kind.add(r["item"]["kind"])
        resultados = primeiros + resto
        linhas: list[str] = []
        for i, r in enumerate(resultados):
            if len(linhas) >= maximo:
                break
            restante = livre - sum(len(x) + 1 for x in linhas)
            if linhas and restante < 110:
                break  # sobra pequena demais para uma lembrança útil
            faltam = min(maximo - len(linhas), len(resultados) - i)
            # a mais relevante fica com ~60% do orçamento; o resto reparte entre as que ainda cabem
            alvo = int(restante * 0.6) if (not linhas and faltam >= 2) else restante // max(1, min(faltam, 2))
            escolhida = ""
            for compacta, lim in ((False, 150), (False, 100), (True, 100), (True, 60), (True, 40)):
                cand = self._linha(r, compacta, lim)
                if len(cand) <= alvo:
                    escolhida = cand
                    break
            if not escolhida:
                cand = self._linha(r, True, 40)
                if len(cand) <= restante:
                    escolhida = cand
                elif not linhas:
                    escolhida = _curto(cand, restante)
                else:
                    break
            linhas.append(escolhida)
            r["_usada"] = True
        return linhas

    _RECALL = re.compile(r"\b(lembra|lembro|lembrar|lembranca|esqueceu|esqueci|ja falou|aquele video|naquele video|no video)\b")

    def lembrar(self, texto: str, *, pessoa: str = "", tensao: float = 0.0, owner: bool = False,
                agora: float | None = None, atualizar: bool = True) -> dict[str, Any]:
        """Ponto de entrada por turno. Retorna {bloco, linhas, resultados}; bloco vazio = nada a injetar."""
        self.carregar()
        if atualizar:
            self._turno += 1
        res = self.buscar(texto, pessoa=pessoa, tensao=tensao, owner=owner, agora=agora, cooldown=atualizar)
        linhas = self.compor(res)
        usadas = [r for r in res if r.get("_usada")]
        if (not linhas and self.itens and self._RECALL.search(norm(texto)) and toks(texto)
                and not re.match(r"^(eu\s+)?nao\s+(me\s+)?lembro\b", norm(texto))):
            linhas = ["LEMBRANÇA AUSENTE NOS VÍDEOS: não há registro do fato pedido. Sem outra fonte, admita que não lembra; não invente episódio, cena nem participação."]
        if atualizar and usadas:
            agora = agora or time.time()
            for r in usadas:
                self.reforcar(r["item"], agora)
                self._uso[r["item"]["id"]] = self._turno
            self._salvar_estado()
        bloco = "\n".join(linhas + [RESUMO_REGRA]) if linhas else ""
        return {"bloco": bloco, "linhas": linhas, "resultados": res}

    def stats(self) -> dict[str, Any]:
        self.carregar()
        por: dict[str, int] = {}
        for it in self.itens:
            por[it["kind"]] = por.get(it["kind"], 0) + 1
        pess = self.dados.get("pessoas", {})
        return {"videos": len(self.dados.get("fontes", [])), "itens": len(self.itens), "por_tipo": por,
                "episodios_aprovados": sum(1 for i in self.itens if i.get("criador_aprovou")),
                "pessoas": len(pess), "haters_conhecidos": sorted(p["autor"] for p in pess.values() if p.get("hater")),
                "contradicoes": len(self.dados.get("contradicoes", [])),
                "ignorados": self.dados.get("ignorados", [])}


_memoria: Memoria | None = None


def instancia() -> Memoria:
    global _memoria
    if _memoria is None:
        _memoria = Memoria()
    return _memoria


def recall_block(texto: str, *, speaker: str = "", tension: float = 0.0, owner: bool = False) -> str:
    """Bloco pronto para o prompt (string vazia se desligado/sem relevância). Nunca levanta."""
    if not enabled():
        return ""
    try:
        return instancia().lembrar(texto, pessoa=speaker, tensao=tension, owner=owner)["bloco"]
    except Exception:  # noqa: BLE001 - sem memória de vídeo, o turno segue
        return ""
