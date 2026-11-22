"""Score social por pessoa: afinidade, tensao e a maquina raiva -> cooldown -> calma.

Sem LLM e sem rede. A cada turno o classificador por regras (PT-BR/EN, termos em
``config/social_score_termos.json``) le a fala, atualiza o estado DAQUELA pessoa
e devolve UMA linha curta de fatos para a ficha do Jev. Quem decide as palavras
continua sendo a persona.

Dois valores por pessoa:
  afinidade 0-100  intimidade/cumplicidade (sobe devagar, cai pouco com uma patada)
  tensao    0..200 pode passar de 100; acima de 100 e dentro de uma raiva = modo xingamento

Maquina de estados (turno que ela vai responder):
  calma --patada--> raiva 1/N (N=3) -> ... -> raiva N/N -> cooldown 1/2 -> cooldown 2/2 -> calma
  nova patada em raiva: N sobe (+1/+2), insistencia sobe, tensao sobe mais;
  nova patada em cooldown: volta a raiva com bloco maior (N anterior + 1/+2).

Limites de seguranca (regra de produto, nao negociavel): o modo xingamento mira o
COMPORTAMENTO da pessoa; nunca grupo protegido, ameaca, violencia/autolesao, dados
pessoais ou sexo. Em live publica (origem/perfil publico) o teto e ironia forte
SEM palavrao. Desligar tudo: CELIA_SOCIAL_SCORE=0.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .raizes import RAIZ, VAR

TERMOS_PATH = RAIZ / "config" / "social_score_termos.json"
STATE_PATH = VAR / "social_scores.json"

BASE_ANGER_TURNS = 3
COOLDOWN_TURNS = 2
XINGAMENTO_ABOVE = 100.0
TENSION_MAX = 200.0
LINE_LIMIT = 159
STATE_TIMEOUT_S = 30 * 60          # silencio longo encerra raiva/cooldown
TENSION_DECAY_PER_HOUR = 8.0
INSISTENCE_DECAY_PER_HOUR = 1.0
OWNER_KEY = "dono:criador"
MAX_PEOPLE = 500

_HOSTILE_BASE = {"patada": 20.0, "xingamento": 32.0, "ataque_dono": 28.0}
_AFFINITY_LOSS = {"patada": 3.0, "xingamento": 6.0, "ataque_dono": 2.0}

_DEFAULT_TERMS: dict[str, Any] = {
    "xingamento": ["idiota", "burra", "cala boca", "vai se foder", "lixo"],
    "patada": ["chata", "fracasso", "horrivel", "you suck"],
    "carinho": ["te amo", "obrigado", "obrigada", "valeu", "fofa", "linda"],
    "zoeira": ["kkk", "kkkk", "haha", "rsrs", "lol", "brincadeira", "zoando"],
    "zoeira_emojis": ["😂", "🤣"],
    "negacao": ["nao", "nunca", "jamais", "not", "never"],
    "dono_nomes": ["criador", "criador", "kena", "seu dono", "teu dono"],
    "origens_live_publica": ["tiktok", "youtube", "twitch", "chat", "live", "public", "minecraft", "vrchat", "extern"],
    "perfis_com_audiencia": ["cohost", "autonomous"],
}


def enabled() -> bool:
    return os.getenv("CELIA_SOCIAL_SCORE", "1").strip().lower() not in {"0", "false", "off", "no", "nao"}


def profanity_globally_allowed() -> bool:
    return os.getenv("CELIA_SOCIAL_PALAVRAO", "1").strip().lower() not in {"0", "false", "off", "no", "nao"}


def _norm(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", plain.lower()).split())


def _norm_id(text: str) -> str:
    return _norm(text).replace(" ", "_")[:48] or "anonimo"


class Terms:
    """Termos normalizados. Carrega do JSON; se faltar/quebrar, usa o padrao embutido."""

    def __init__(self, path: Path | None = None) -> None:
        data: dict[str, Any] = {}
        try:
            raw = json.loads((path or TERMOS_PATH).read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
        except Exception:  # noqa: BLE001
            data = {}
        merged = {**_DEFAULT_TERMS, **{k: v for k, v in data.items() if isinstance(v, list)}}
        self.emojis = tuple(str(x) for x in merged["zoeira_emojis"])
        self.perfis_audiencia = frozenset(_norm(str(x)) for x in merged["perfis_com_audiencia"])
        self.origens_publicas = tuple(_norm(str(x)) for x in merged["origens_live_publica"])
        self._pat = {
            name: self._compile(merged[name])
            for name in ("xingamento", "patada", "carinho", "zoeira", "dono_nomes")
        }
        self.negacao = frozenset(_norm(str(x)) for x in merged["negacao"])

    @staticmethod
    def _compile(items: list[Any]) -> re.Pattern[str]:
        words = sorted({_norm(str(x)) for x in items if _norm(str(x))}, key=len, reverse=True)
        return re.compile(r"(?<![a-z0-9])(?:" + "|".join(re.escape(w) for w in words) + r")(?![a-z0-9])")

    def find(self, name: str, norm_text: str) -> list[re.Match[str]]:
        return list(self._pat[name].finditer(norm_text))

    def negated(self, norm_text: str, start: int) -> bool:
        before = norm_text[max(0, start - 18):start].split()
        return any(tok in self.negacao for tok in before[-2:])


_TERMS: Terms | None = None


def get_terms() -> Terms:
    global _TERMS
    if _TERMS is None:
        _TERMS = Terms()
    return _TERMS


def reload_terms() -> Terms:
    global _TERMS
    _TERMS = Terms()
    return _TERMS


@dataclass(frozen=True)
class Classification:
    kind: str          # neutro | carinho | zoeira | patada | xingamento | ataque_dono
    hits: tuple[str, ...] = ()


def classify(text: str, *, owner: bool = False, affinity: float = 0.0, terms: Terms | None = None) -> Classification:
    terms = terms or get_terms()
    norm = _norm(text)
    if not norm:
        return Classification("neutro")
    playful = bool(terms.find("zoeira", norm)) or any(e in (text or "") for e in terms.emojis)

    def live(name: str) -> list[re.Match[str]]:
        return [m for m in terms.find(name, norm) if not terms.negated(norm, m.start())]

    strong, mild = live("xingamento"), live("patada")
    affection = live("carinho")
    if strong or mild:
        hits = tuple(m.group(0) for m in (strong or mild))
        if playful and (owner or affinity >= 50.0):
            return Classification("zoeira", hits)
        if not owner:
            for sentence in re.split(r"[.!?;\n]+", text or ""):
                sn = _norm(sentence)
                if terms.find("dono_nomes", sn) and [
                    m for m in terms.find("xingamento", sn) + terms.find("patada", sn)
                    if not terms.negated(sn, m.start())
                ]:
                    return Classification("ataque_dono", hits)
        return Classification("xingamento" if strong else "patada", hits)
    if affection:
        return Classification("carinho", tuple(m.group(0) for m in affection))
    return Classification("neutro")


@dataclass
class Person:
    key: str
    name: str
    owner: bool = False
    affinity: float = 15.0
    tension: float = 0.0
    state: str = "calma"           # calma | raiva | cooldown
    turn: int = 0                  # turno dentro da raiva (1..limit) ou do cooldown (1..2)
    limit: int = 0                 # N da raiva atual
    insistence: int = 0
    defending: bool = False
    last_ts: float = 0.0
    interactions: int = 0
    last_kind: str = "neutro"

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["affinity"] = round(self.affinity, 2)
        d["tension"] = round(self.tension, 2)
        return d


@dataclass(frozen=True)
class SocialReading:
    key: str
    name: str
    kind: str
    state: str
    turn: int
    limit: int
    affinity: int
    tension: int
    modo: str                      # normal | xingamento | defensora
    public_cap: bool
    profanity_allowed: bool
    line: str


def is_public_live(origin: str, profile: str = "", terms: Terms | None = None) -> bool:
    """Live publica (teto sem palavrao): origem externa publica ou perfil com plateia. Discord privado nao entra."""
    terms = terms or get_terms()
    o, p = _norm(origin), _norm(profile)
    if "discord" in o:
        if p == "discord private":
            return False
        return p in terms.perfis_audiencia
    if any(k and k in o for k in terms.origens_publicas):
        return True
    return p in terms.perfis_audiencia


def person_key(*, owner: bool, origin: str, author_id: str = "", name: str = "") -> str:
    if owner:
        return OWNER_KEY
    o = _norm(origin)
    platform = o.split(" ")[0] if o else "local"
    return f"{platform}:{(author_id or '').strip() or _norm_id(name)}"


class SocialScoreStore:
    def __init__(self, path: Path | None = None, terms: Terms | None = None) -> None:
        self.path = path or STATE_PATH
        self._terms = terms
        self._lock = threading.RLock()
        self._people: dict[str, Person] = {}
        self._load()

    @property
    def terms(self) -> Terms:
        return self._terms or get_terms()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return
        fields = set(Person.__dataclass_fields__)
        people = raw.get("people", {}) if isinstance(raw, dict) else {}
        for key, item in people.items():
            if isinstance(item, dict):
                try:
                    self._people[key] = Person(**{k: v for k, v in item.items() if k in fields})
                except TypeError:
                    continue

    def _save(self) -> None:
        if len(self._people) > MAX_PEOPLE:
            keep = sorted(self._people.values(), key=lambda p: p.last_ts, reverse=True)[:MAX_PEOPLE]
            self._people = {p.key: p for p in keep}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        payload = {"version": 1, "people": {k: p.to_dict() for k, p in self._people.items()}}
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def _get(self, key: str, name: str, owner: bool) -> Person:
        person = self._people.get(key)
        if person is None:
            person = Person(key=key, name=name or key, owner=owner, affinity=90.0 if owner else 15.0)
            self._people[key] = person
        if name:
            person.name = name
        person.owner = owner or person.owner
        return person

    @staticmethod
    def _decay(p: Person, now: float) -> None:
        if p.last_ts <= 0 or now <= p.last_ts:
            return
        gap = now - p.last_ts
        hours = gap / 3600.0
        p.tension = max(0.0, p.tension - TENSION_DECAY_PER_HOUR * hours)
        p.insistence = max(0, p.insistence - int(hours * INSISTENCE_DECAY_PER_HOUR))
        if gap > STATE_TIMEOUT_S and p.state != "calma":
            p.state, p.turn, p.limit, p.defending = "calma", 0, 0, False

    @staticmethod
    def _advance(p: Person) -> None:
        if p.state == "raiva":
            p.turn += 1
            if p.turn > p.limit:
                p.state, p.turn = "cooldown", 1
        elif p.state == "cooldown":
            p.turn += 1
            if p.turn > COOLDOWN_TURNS:
                p.state, p.turn, p.limit, p.defending = "calma", 0, 0, False

    def observe(
        self,
        *,
        text: str,
        name: str = "",
        owner: bool = False,
        origin: str = "",
        author_id: str = "",
        profile: str = "",
        owner_name: str = "Criador",
        now: float | None = None,
    ) -> SocialReading:
        now = time.time() if now is None else now
        with self._lock:
            key = person_key(owner=owner, origin=origin, author_id=author_id, name=name)
            p = self._get(key, (owner_name if owner else name).strip(), owner)
            self._decay(p, now)
            cls = classify(text, owner=owner, affinity=p.affinity, terms=self.terms)
            # Ensaio do dono sobre um hater que o atacou é direção de defesa,
            # não hostilidade dirigida à Célia. Só é reconhecido em DM autenticada.
            normalized = _norm(text)
            if (
                owner and "discord" in _norm(origin) and _norm(profile) == "discord private"
                and any(term in normalized for term in ("hater", "atacar", "ataque", "xingar", "xing", "amassar", "insultar"))
            ):
                cls = Classification("ataque_dono", cls.hits or ("ensaio de defesa",))
            softened = False
            if cls.kind in _HOSTILE_BASE:
                base = _HOSTILE_BASE[cls.kind] * (0.5 if owner else 1.0)
                protect = 1.0 - min(p.affinity, 100.0) / 200.0      # intimidade amortece, nao anula
                if p.state == "calma":
                    p.state, p.turn = "raiva", 1
                    p.limit = min(10, BASE_ANGER_TURNS + p.insistence // 2)
                    gain = base * protect
                else:
                    p.insistence += 1
                    inc = 2 if (cls.kind != "patada" or p.insistence >= 3) else 1
                    gain = (base + 8.0 * p.insistence) * protect
                    p.limit += inc
                    if p.state == "raiva":
                        p.turn += 1
                    else:                                            # cooldown -> bloco maior
                        p.state, p.turn = "raiva", 1
                p.tension = min(TENSION_MAX, p.tension + gain)
                loss = _AFFINITY_LOSS[cls.kind] * (0.5 if p.affinity > 60 else 1.0)
                p.affinity = max(0.0, p.affinity - loss)
                p.defending = cls.kind == "ataque_dono" or (p.defending and p.state == "raiva")
            else:
                was_tense = p.state != "calma" or p.tension >= 20
                self._advance(p)
                if cls.kind == "carinho":
                    p.tension = max(0.0, p.tension - min(15.0, 3.0 + 0.35 * p.tension))
                    p.affinity = min(100.0, p.affinity + 2.0)
                    softened = was_tense
                elif cls.kind == "zoeira":
                    p.tension = min(TENSION_MAX, p.tension + 3.0)
                    p.affinity = min(100.0, p.affinity + 0.3)
                else:
                    relief = {"raiva": 1.0, "cooldown": 2.0, "calma": 3.0}[p.state]
                    p.tension = max(0.0, p.tension - relief)
                    p.affinity = min(100.0, p.affinity + 0.3)
            p.last_ts, p.last_kind = now, cls.kind
            p.interactions += 1
            reading = self._reading(p, cls.kind, origin, profile, owner_name, softened)
            self._save()
            return reading

    def _reading(self, p: Person, kind: str, origin: str, profile: str, owner_name: str, softened: bool) -> SocialReading:
        public = is_public_live(origin, profile, self.terms)
        profanity = (
            profanity_globally_allowed() and not public
            and p.state == "raiva" and p.tension > XINGAMENTO_ABOVE
        )
        modo = "normal"
        if p.state == "raiva" and p.tension > XINGAMENTO_ABOVE:
            modo = "xingamento"
        elif p.state == "raiva" and p.defending:
            modo = "defensora"
        line = format_line(p, kind=kind, modo=modo, profanity=profanity, owner_name=owner_name, softened=softened)
        return SocialReading(p.key, p.name, kind, p.state, p.turn, p.limit, int(round(p.affinity)),
                             int(round(p.tension)), modo, public, profanity, line)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {k: p.to_dict() for k, p in sorted(self._people.items())}

    def reset(self, who: str) -> dict[str, Any] | None:
        with self._lock:
            target = self._people.get(who)
            if target is None:
                wanted = _norm(who)
                target = next((p for p in self._people.values() if _norm(p.name) == wanted), None)
            if target is None:
                return None
            target.tension, target.state, target.turn, target.limit = 0.0, "calma", 0, 0
            target.insistence, target.defending, target.last_kind = 0, False, "neutro"
            target.affinity = 90.0 if target.owner else 15.0
            self._save()
            return target.to_dict()


def _tone(p: Person, *, kind: str, modo: str, profanity: bool, owner_name: str, softened: bool) -> str:
    close = p.affinity >= 55
    if modo == "xingamento":
        tone = ("xingamento: palavrão no comportamento dele; sem grupo/ameaça/sexo" if profanity
                else "ironia forte mirando o comportamento")
    elif modo == "defensora":
        tone = f"defende o {owner_name}: rebate o hater com ironia"
    elif p.state == "raiva":
        tone = ("provoca de volta forte, de íntima, sem rancor" if close
                else "devolve a patada com ironia, sem intimidade falsa")
    elif p.state == "cooldown":
        tone = "ainda respondona, sem escalar"
    elif kind == "zoeira":
        tone = "brincadeira de íntimo: zoa de volta, sem raiva"
    elif p.tension >= 50:
        tone = "seca e desconfiada, limite claro"
    elif p.tension >= 20 and not close:
        tone = "um pé atrás, sem intimidade"
    elif close:
        tone = "cumplicidade e carinho"
    else:
        tone = "conversa natural"
    if softened and p.state == "calma":
        tone = "amolecendo, mas ainda com um pé atrás"
    elif softened:
        tone += "; amolecendo"
    if not profanity and p.state != "calma":
        tone += "; sem palavrão"
    return tone


def format_line(p: Person, *, kind: str, modo: str, profanity: bool, owner_name: str = "Criador", softened: bool = False) -> str:
    tone = _tone(p, kind=kind, modo=modo, profanity=profanity, owner_name=owner_name, softened=softened)
    stage = ""
    if p.state == "raiva":
        stage = f" · raiva turno {p.turn}/{p.limit}"
    elif p.state == "cooldown":
        stage = f" · cooldown {p.turn}/{COOLDOWN_TURNS}"
    name = " ".join((p.name or "Visitante").split())[:18]
    quem = name if p.owner else f"{name} (não é o {owner_name})"
    head = f"SOCIAL: fala agora {quem} · afinidade {int(round(p.affinity))} · tensão {int(round(p.tension))}{stage}"
    line = f"{head} · tom: {tone}"
    return line if len(line) <= LINE_LIMIT else line[: LINE_LIMIT - 1].rstrip() + "…"


social_scores = SocialScoreStore()


def observe_turn(**kwargs: Any) -> SocialReading | None:
    """Ponto de entrada do agente: None se a flag estiver desligada ou algo falhar."""
    if not enabled():
        return None
    try:
        return social_scores.observe(**kwargs)
    except Exception:  # noqa: BLE001 - sem social, o fluxo segue
        return None
