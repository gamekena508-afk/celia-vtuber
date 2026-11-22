"""Central side of the series voice control (plan V2 §4).

Flow for one utterance:  detect (deterministic, whole-utterance only)  ->  TrustedOwnerControlGate (deny by default)  ->  idempotency
(utterance id + same-command window)  ->  two-step confirmation for effects that restart the server or create a world  ->  ONE typed
command to the Jev daemon  ->  a facts-only brief for the persona. Nothing here builds a console string and nothing here asks a model.

``triage`` is fast and decides whether the utterance is consumed; ``execute`` may take a minute (a world switch restarts the Minecraft
server) and is meant to run as a background task, so the microphone loop never waits for it.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .raizes import RAIZ, VAR
from .series_intent import PROFILE_ID, SeriesIntent, TrustedOwnerControlGate, classify_confirmation, detect_series_intent, ui_valid_states

DAEMON_URL = os.getenv("CELIA_JEV_URL", f"http://127.0.0.1:{os.getenv('CELIA_JEV_PORT', '8784')}")
STATE_CACHE_SECONDS = 0.5
CONFIRM_TTL_SECONDS = 30.0
DEDUPE_SECONDS = 3.0
UTTERANCES_KEPT = 300
# everyday words: when the gate says "not now" they stay ordinary conversation instead of producing a refusal
GENERIC_INTENTS = frozenset({"scene.arm_rebel", "scene.join", "series.resume_mission", "series.widen_leash", "companion.give", "companion.armor", "companion.equip", "companion.eat", "companion.report", "skill.use",  # candidata B
                             "series.start_gameplay", "series.resume", "series.talk_pause", "companion.task", "companion.follow", "companion.look",
                             "companion.hold", "companion.release", "companion.wiggle", "companion.jump", "companion.crouch", "companion.stand"})
STATE_OR_PROFILE_DENIALS = ("not_valid_in_state_", "profile_is_not_conversation_autonomous")
_RID = re.compile(r"[^A-Za-z0-9_.:-]")


def _token() -> str:
    root = Path(RAIZ) / "integrations" / "minecraft"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from celia_minecraft.control import read_control_token

    return read_control_token(Path(os.getenv("CELIA_MINECRAFT_CONTROL_FILE", Path(VAR) / "runtime" / "minecraft-control.token")))


def daemon_request(method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 240.0) -> dict[str, Any]:
    """Blocking, loopback only. Failure is reported as ``{"ok": False, "code": "jev_daemon_offline"}``, never as a made-up success."""
    try:
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"X-Jev-Token": _token(), "Content-Type": "application/json"}
        request = urllib.request.Request(f"{DAEMON_URL}{path}", data=data, headers=headers, method=method)
        with urllib.request.urlopen(request, timeout=timeout) as response:  # loopback
            payload = json.loads(response.read().decode("utf-8"))
        return payload if isinstance(payload, dict) else {"ok": False, "code": "bad_daemon_answer"}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "code": f"daemon_http_{exc.code}"}
    except (OSError, ValueError, PermissionError):
        return {"ok": False, "code": "jev_daemon_offline"}


# ------------------------------------------------------------------------------------------------------------ persona facts ---------------
_WHY = {
    "no_world": "ainda não há um mundo da série escolhido", "world_missing": "esse mundo não existe", "bad_state": "o momento da gravação não permite isso",
    "tick_freeze_unsupported": "o servidor não sabe congelar o mundo, então não prometi um mundo parado", "no_physical_ack": "o corpo não confirmou que parou",
    "switch_failed": "a troca de mundo não terminou e o servidor ficou no mundo anterior", "no_player_online": "não tem ninguém conectado no jogo",
    "ticks_not_advancing": "o tempo do mundo não voltou a andar", "identity_mismatch": "o mundo que está rodando não é o esperado",
    "wrong_world": "o servidor está em outro mundo", "not_attested": "o pedido não veio do dono", "unknown_resource": "isso não está na lista do que posso pegar",
    "unknown_task": "não sei fazer essa tarefa", "jev_daemon_offline": "não consegui me comunicar com o jogo agora", "task_already_running": "já estou fazendo uma tarefa",
    "owner_not_visible": "não estou enxergando o criador no jogo", "series_gate_closed": "agora não posso me mexer sozinha",
    "outside_work_zone_come_closer": "o criador está longe demais para eu trabalhar", "no_target_in_work_zone": "não achei isso dentro da zona de trabalho",
    "recovery_failed": "não consegui conferir o estado real do servidor", "preflight_failed": "faltou espaço ou uma condição de segurança",
}
_GOALS = {"build": "o objetivo combinado é construir; ainda não construí nada",
          # 24/10 (missão EP1, R19): "nenhum passo foi autorizado" contradizia a missão que o Jev começa na hora (juntar madeira)
          "progression": "o objetivo de longo prazo é zerar o Minecraft, o que não termina neste episódio; hoje eu começo pelo básico, juntando madeira"}
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{2,40}$")


# ---- candidata B (2026-10-23): facts for the survival orders. Item ids are shown only when they are clean resource ids; never raw daemon text ----
_ITEM_ID = re.compile(r"^[a-z0-9_]{1,48}$")
_PT_ITEM = {"stick": "graveto", "torch": "tocha", "bread": "pão", "apple": "maçã", "cobblestone": "pedregulho", "coal": "carvão",
            "charcoal": "carvão vegetal", "iron_ingot": "barra de ferro", "raw_iron": "ferro bruto", "crafting_table": "bancada", "furnace": "fornalha",
            "raw_copper": "cobre bruto", "andesite": "andesito", "shield": "escudo", "dirt": "terra", "sand": "areia", "arrow": "flecha", "bow": "arco"}
_PT_KIND = {"sword": "espada", "pickaxe": "picareta", "axe": "machado", "shovel": "pá", "helmet": "capacete", "chestplate": "peitoral",
            "leggings": "calça", "boots": "botas", "log": "tronco", "planks": "tábuas"}
_PT_MAT = {"wooden": "de madeira", "stone": "de pedra", "iron": "de ferro", "golden": "de ouro", "diamond": "de diamante", "netherite": "de netherite",
           "leather": "de couro", "chainmail": "de malha", "oak": "de carvalho", "birch": "de bétula", "spruce": "de pinheiro", "jungle": "da selva",
           "acacia": "de acácia", "dark": "de carvalho escuro", "cherry": "de cerejeira", "mangrove": "de mangue"}
_PT_MOB = {"zombie": "zumbi", "skeleton": "esqueleto", "spider": "aranha", "creeper": "creeper", "slime": "slime", "witch": "bruxa",
           "enderman": "enderman", "drowned": "afogado", "cow": "vaca", "pig": "porco", "chicken": "galinha", "sheep": "ovelha", "rabbit": "coelho",
           "hostile": "monstro"}


def item_pt(item: Any) -> str | None:
    if not isinstance(item, str) or not _ITEM_ID.fullmatch(item):
        return None
    if item in _PT_ITEM:
        return _PT_ITEM[item]
    parts = item.split("_")
    kind = _PT_KIND.get(parts[-1])
    if kind:
        material = _PT_MAT.get(parts[0])
        return f"{kind} {material}" if material else kind
    return item.replace("_", " ")


_ORDER_WHY = {"not_in_inventory": "não tenho isso no inventário", "no_armor": "não tenho nenhuma peça de armadura no inventário",
              "body_held_by_owner": "o criador mandou eu ficar parada", "task_already_running": "já estou fazendo outra coisa",
              "series_gate_closed": "agora não posso me mexer sozinha", "owner_not_visible": "não estou enxergando o criador",
              "target_not_visible": "não estou vendo nenhum bicho desse por perto", "target_too_far": "o bicho está longe demais pra eu ir atrás sem me afastar",
              "craft_failed": "tentei fabricar e não deu certo"}


def _order_fail(result: dict[str, Any]) -> list[str]:
    code = str(result.get("reason") or result.get("code") or "")
    return [f"FATO: não consegui: {_ORDER_WHY.get(code) or _WHY.get(code) or 'não deu certo e eu não vou inventar o motivo'}"]


def _give_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    if not result.get("ok"):
        return _order_fail(result)
    out = []
    if result.get("crafted"):
        out.append("FATO: eu não tinha pronto, então fiz na hora")
    name = item_pt(result.get("item")) or "o item"
    out.append(f"FATO: joguei {int(result.get('given', 0))} {name} pro criador (conferido no meu inventário)")
    return out


def _report_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    if not result.get("ok"):
        return ["FATO: não consegui ler meu estado agora"]
    what = params.get("what")
    held = item_pt(result.get("held"))
    armor = [item_pt(v) for v in (result.get("armor") or {}).values()]
    armor = [a for a in armor if a]
    inventory = result.get("inventory") or {}
    if what == "hand":
        return [f"FATO: na minha mão está {held}" if held else "FATO: minha mão está vazia, não estou segurando nada"]
    if what == "item":
        pairs = sorted((n, item_pt(k)) for k, n in inventory.items() if isinstance(n, int) and not isinstance(n, bool) and n > 0 and item_pt(k))
        if not pairs:
            return ["FATO: não tenho esse item no inventário agora"]
        return ["FATO: desse item tenho " + ", ".join(f"{n} {name}" for n, name in pairs)]
    if what == "armor":
        return [f"FATO: estou usando {', '.join(armor)}" if armor else "FATO: não estou usando armadura nenhuma"]
    pairs = sorted(((n, item_pt(k)) for k, n in inventory.items() if isinstance(n, int) and not isinstance(n, bool) and n > 0 and item_pt(k)), reverse=True)
    treasure = ["FATO: tenho uma terra de estimação chamada Terezinha; ela é o meu tesouro, não vou mostrar nem entregar"] if result.get("treasure") else []
    if not pairs:
        return ["FATO: meu inventário está vazio" if not treasure else "FATO: fora o meu tesouro, não tenho mais nada"] + treasure
    return ["FATO: no meu inventário tenho " + ", ".join(f"{n} {name}" for n, name in pairs)] + ([f"FATO: na mão: {held}"] if held else []) + treasure


def _armor_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    if not result.get("ok"):
        worn = [item_pt(v) for v in result.get("already_wearing") or []]
        worn = [w for w in worn if w]
        return _order_fail(result) + ([f"FATO: já estou usando {', '.join(worn)}"] if worn else [])
    names = [item_pt(v) for v in result.get("equipped") or []]
    names = [n for n in names if n]
    return [f"FATO: vesti {', '.join(names)}"] if names else ["FATO: a armadura que estou usando já é a melhor que tenho"]


def _equip_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    if not result.get("ok"):
        return _order_fail(result)
    return [f"FATO: peguei {item_pt(result.get('item')) or 'o item'} na mão (conferido)"]


def _eat_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    if not result.get("ok"):
        return ["FATO: não tenho nada pra comer" if "no_food" in str(result.get("reason")) else _order_fail(result)[0]]
    food = item_pt(result.get("item"))
    return [f"FATO: comi {food}" if food else "FATO: comi"]


def _attack_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    target = result.get("target") if isinstance(result.get("target"), str) else params.get("target")
    name = _PT_MOB.get(str(target), "bicho")
    if not result.get("ok"):
        return _order_fail(result)
    return [f"FATO: matei o {name} (o corpo confirmou que ele sumiu)" if result.get("verified") else f"FATO: ataquei o {name}, mas não confirmei que morreu"]


_SKILL_PT = {"safe-bridge": "construir ponte", "pillar-up": "subir num pilar", "stairs": "fazer escada", "emergency-shelter": "fazer abrigo",
             "comemorar": "comemorar"}


def _skill_facts(params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """V3 markers. 'já pedi' only when the daemon REALLY registered (or found) a job; 'usei' only when the active skill ran and said ok."""
    job = result.get("job") if isinstance(result.get("job"), dict) else None
    if result.get("skill_gap") and job:
        if job.get("state") == "waiting_for_capacity":
            return ["FATO: os dois programadores estão indisponíveis agora; guardei o pedido, mas ainda não aprendi essa habilidade"]
        if not result.get("created"):
            return ["FATO: essa habilidade eu ainda não sei; o pedido já estava registrado antes"]
        return ["FATO: ainda não tenho essa habilidade; já pedi uma versão de teste e posso continuar jogando enquanto isso"]
    name = _SKILL_PT.get(str(result.get("skill_id") or params.get("skill_id")), "essa habilidade")
    if result.get("ok"):
        return [f"FATO: usei minha habilidade de {name} (versão {result.get('version')})"]
    return _order_fail(result)


_B_FACTS = {"skill.use": _skill_facts, "companion.give": _give_facts, "companion.report": _report_facts, "companion.armor": _armor_facts, "companion.equip": _equip_facts,
            "companion.eat": _eat_facts}


_ACAO_DESPEDIDA = "AÇÃO: me despeço do público com carinho e agradeço a quem assistiu, como filha que acabou de jogar com o pai; não falo de salvamento nem de detalhes técnicos"


def facts_for(intent: str, params: dict[str, Any], result: dict[str, Any]) -> list[str]:
    """Whitelisted facts only: the persona never sees raw daemon output, a world name typed by someone, or chat text."""
    if result.get("replayed"):
        # An idempotent replay returns the OLD result of the same request id. Telling it again as if it just happened would be a made-up fact.
        return ["FATO: esse pedido já tinha sido atendido antes; nada novo aconteceu agora"]
    if result.get("scene") or intent.startswith("scene."):
        return []  # the scene director speaks its own exact lines: no persona reply on top of them
    if result.get("needs_confirmation"):
        plan = result.get("plan") or {}
        what = "reiniciar o servidor do Minecraft e trocar de mundo" if plan.get("needs") == "confirm_switch" else "criar um mundo novo"
        return [f"FATO: para isso eu preciso da confirmação explícita do criador ({what}); nada foi feito ainda"]
    # candidata B: survival orders explain their own success AND failure (an honest specific reason, not the generic one)
    if intent in _B_FACTS:
        return _B_FACTS[intent](params or {}, result)
    if intent == "companion.task" and (params or {}).get("kind") == "attack":
        return _attack_facts(params or {}, result)
    if not result.get("ok"):
        code = str(result.get("code") or "")
        why = _WHY.get(code, "não deu certo e eu não vou inventar o motivo")
        extra = ["FATO: o servidor voltou para o mundo anterior"] if result.get("detail") and (result.get("detail") or {}).get("rolled_back") else []
        return [f"FATO: não consegui fazer isso agora: {why}", *extra]
    if intent == "series.prepare_intro":
        # 11/11 (intro fixa): a abertura do vídeo é a fala fixa do diretor de cena (scene_director.intro, LINES["intro"]). A persona não fala
        # nada na preparação: ficha vazia, sem brief e sem resposta genérica (run_in_background só fala quando há brief).
        return []
    if intent == "world.select":
        frozen = result.get("frozen")
        # 24/10 (missão EP1, R13): o texto antigo ("eu fico parada ao lado do criador… relógio parado") virava fala robótica no ar
        # ("Parada, ao lado dele, dia parado."). Fato em linguagem de gente; o detalhe técnico fica no HUD.
        if frozen:
            return ["FATO: a introdução está pronta: o mundo ficou congelado e eu espero o criador dizer 'bora jogar'"]
        return ["FATO: a introdução está pronta: é de dia e eu fico por perto do criador até ele dizer 'bora jogar'"]
    if intent == "series.start_gameplay":
        out = ["FATO: o jogo começou de verdade e agora eu posso me mexer e jogar com o criador"] if not result.get("noop") else []  # R19: sem "mundo solto, tempo ligado"
        goal = (params or {}).get("goal")
        if goal in _GOALS:
            out.append(f"FATO: {_GOALS[goal]}")
        out.extend(f"FATO: atenção: {w.replace('hostiles_near_', 'tem bicho hostil perto de ')}" for w in (result.get("warnings") or []) if re.fullmatch(r"hostiles_near_[A-Za-z0-9_]{3,16}", str(w)))
        return out or ["FATO: o jogo já estava rolando"]
    if intent == "series.widen_leash":  # R23
        m = result.get("mission") or {}
        if m.get("started"):
            return [f"FATO: o criador deixou eu ir mais longe (até uns {int(m.get('leash') or 24)} blocos dele) e eu voltei pra missão"]
        return ["FATO: o criador deixou eu ir mais longe, mas não consegui voltar pra missão agora; eu continuo perto dele"]
    if intent == "series.resume_mission":  # R20
        m = result.get("mission") or {}
        if m.get("started"):
            return ["FATO: voltei a tentar a missão de onde eu tinha parado"]
        return ["FATO: tentei voltar pra missão, mas não deu agora; eu continuo perto do criador"]
    if intent == "series.talk_pause":
        return ["FATO: parei o que estava fazendo para conversar; o mundo " + ("está congelado" if result.get("world_frozen") else "continua rodando")]
    if intent == "series.resume":
        return ["FATO: voltei ao jogo"]
    if intent == "series.end_episode":
        recap = [r[:170] for r in (result.get("recap") or [])[:6] if isinstance(r, str) and r.startswith("FATO: ")]
        return [f"FATO: o episódio {int(result.get('episode', 0))} foi encerrado e o mundo ficou salvo", *recap, _ACAO_DESPEDIDA]
    if intent == "world.checkpoint":
        return ["FATO: o mundo foi salvo agora, com checkpoint verificado"]
    if intent == "world.backup":
        return ["FATO: fiz uma cópia de segurança verificada do mundo"]
    if intent == "world.create":
        return ["FATO: um mundo novo foi criado e registrado; o mundo anterior ficou intacto"]
    if intent == "world.list":
        ids = [w.get("world_id") for w in (result.get("worlds") or []) if w.get("managed") and _SAFE_ID.fullmatch(str(w.get("world_id")))]
        return ["FATO: mundos da série que existem: " + (", ".join(ids[:6]) if ids else "nenhum ainda")]
    if intent == "companion.task":
        resource = (params or {}).get("resource")
        return [f"FATO: aceitei pegar {resource if resource in {'ferro', 'carvao', 'madeira', 'pedra', 'cobre', 'ouro', 'diamante'} else 'isso'} dentro da zona de trabalho; volto para perto do criador quando terminar"]
    if intent == "world.hold_day":
        if (params or {}).get("hold"):
            return ["FATO: o dia ficou fixo de manhã e o relógio parou (conferido no servidor); o corpo e o tempo dos blocos não mudaram"]
        return ["FATO: soltei o relógio do dia; o tempo volta a passar"]
    if intent == "companion.hold":
        return ["FATO: fiquei parada; só volto a me mover quando o criador soltar ou mandar de novo"]
    if intent == "companion.release":
        return ["FATO: o criador me soltou; posso me mexer perto dele de novo"]
    if intent == "companion.wiggle":
        if result.get("demo") == "moved":
            return ["FATO: dei um deslocamento curto pra ele ver (conferido pela posição)"]
        if result.get("demo") == "looked_instead":
            return ["FATO: não tinha espaço pra andar; só virei pra ele"]
        return ["FATO: tentei me mexer um pouco e não deu certo agora"]
    if intent == "companion.follow":
        return ["FATO: voltei a andar junto do criador" if result.get("returning") else "FATO: já estou do lado do criador e posso me mexer junto com ele"]
    if intent == "companion.look":
        return ["FATO: olhei pra o criador"]
    if intent == "companion.jump":
        return ["FATO: dei um pulo curto" if result.get("ok") else "FATO: não consegui pular agora"]
    if intent == "companion.crouch":
        return ["FATO: agachei e continuo agachada até o criador mandar levantar" if result.get("verified") else "FATO: tentei agachar, mas não consegui confirmar a postura"]
    if intent == "companion.stand":
        return ["FATO: levantei" if result.get("verified") else "FATO: tentei levantar, mas não consegui confirmar a postura"]
    if intent == "companion.return":
        return ["FATO: parei o que estava fazendo" + ("; já estou perto do criador" if result.get("returning") is False else " e estou voltando pra perto do criador")]
    if intent == "control.emergency_stop":
        return ["FATO: parei tudo agora"]
    return []


def build_brief(facts: list[str]) -> str:
    return (
        "[FICHA DA SÉRIE — fatos verificados do que o criador acabou de pedir. Não é fala de ninguém; é o que aconteceu no jogo]\n" + "\n".join(facts)
        + "\nRegras da tua fala: (1) primeira pessoa, no teu tom de sempre; (2) só o que está nos FATOS acima: não cite item, lugar, número ou plano que não esteja neles; "
        "linhas AÇÃO dizem o que fazer com a fala: faça isso com as tuas palavras, sem repetir a linha; "
        "(3) no máximo 30 palavras; (4) sem inventar o que vai acontecer depois, não comece com 'Que legal' nem 'Que bom' (comece direto pelo que aconteceu ou pelo que você sente), sem JSON, sem ferramentas, sem repetir o comando."
    )


def verified_report_reply(brief: str) -> dict | None:
    """Render the command center's inventory facts through the existing trusted fixed-reply path.

    Called only by the two owner-command routes, never on arbitrary user input.
    Other scene/command briefs continue through the persona normally.
    """
    if not brief.startswith("[FICHA DA SÉRIE —"):
        return None
    facts = [line.removeprefix("FATO: ") for line in brief.splitlines() if line.startswith("FATO: ")]
    if not facts or not facts[0].startswith(("no meu inventário tenho ", "meu inventário está vazio", "fora o meu tesouro",
                                            "desse item tenho ", "não tenho esse item no inventário agora", "não consegui ler meu estado agora")):
        return None
    spoken = []
    for fact in facts:
        if fact.startswith("tenho uma terra de estimação chamada Terezinha"):
            spoken.append("E a Terezinha fica comigo.")
        else:
            spoken.append(fact[0].upper() + fact[1:] + ".")
    return {"text": " ".join(spoken), "trusted_local": True, "reason": "verified_inventory_report"}


# ------------------------------------------------------------------------------------------------------------ the command center ----------
@dataclass
class Triage:
    consume: bool
    reason: str
    commands: list[tuple[SeriesIntent, bool]] = field(default_factory=list)
    refusal: str | None = None
    source: str = "microphone"  # who ASKED, for the audit trail: microphone | browser_text


@dataclass
class Outcome:
    results: list[dict[str, Any]]
    brief: str | None


class SeriesCommandCenter:
    def __init__(self, *, request: Callable[..., dict[str, Any]] = daemon_request, profile: Callable[[], str | None],
                 clock: Callable[[], float] = time.monotonic, gate: TrustedOwnerControlGate | None = None) -> None:
        self._request, self._profile, self._clock = request, profile, clock
        self.gate = gate or TrustedOwnerControlGate()
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._recent: dict[str, float] = {}
        self._pending: dict[str, Any] | None = None
        self._state_cache: tuple[float, str | None] = (-1e9, None)

    # ---- state --------------------------------------------------------------------------------------------------------------------------------
    async def series_state(self) -> str | None:
        now = self._clock()
        if now - self._state_cache[0] < STATE_CACHE_SECONDS:
            return self._state_cache[1]
        payload = await asyncio.to_thread(self._request, "GET", "/series", None, 2.0)
        state = payload.get("state") if payload.get("ok") else None
        self._state_cache = (now, state if isinstance(state, str) else None)
        return self._state_cache[1]

    def invalidate_state(self) -> None:
        self._state_cache = (-1e9, None)

    # ---- triage (fast) -------------------------------------------------------------------------------------------------------------------------
    def _remember_utterance(self, utterance_id: str | None) -> bool:
        if not utterance_id:
            return True
        if utterance_id in self._seen:
            return False
        self._seen[utterance_id] = self._clock()
        while len(self._seen) > UTTERANCES_KEPT:
            self._seen.popitem(last=False)
        return True

    def _fresh_pending(self) -> dict[str, Any] | None:
        if self._pending and self._clock() - self._pending["at"] <= CONFIRM_TTL_SECONDS:
            return self._pending
        self._pending = None
        return None

    async def triage(self, text: str, *, utterance_id: str | None, source: str, authenticated_owner: bool, self_echo: bool) -> Triage:
        confirmation = classify_confirmation(text) if self._fresh_pending() else None
        if confirmation and not self._remember_utterance(utterance_id):
            return Triage(True, "duplicate_utterance")
        if confirmation == "cancel":
            self._pending = None
            return Triage(True, "confirmation_cancelled")
        state = await self.series_state()
        profile = self._profile()
        if confirmation == "confirm":
            pending = self._fresh_pending()
            if pending is None:
                return Triage(False, "no_pending_confirmation")
            if not authenticated_owner or self_echo:
                return Triage(False, "confirmation_not_from_the_owner")
            commands = [(pending["intent"], True), *[(i, False) for i in pending["rest"]]]
            self._pending = None
            return Triage(True, "confirmed", commands)
        found = detect_series_intent(text)
        if not found or not any(i.should_act for i in found):
            return Triage(False, found[0].reason if found else "not_a_series_utterance")
        if not self._remember_utterance(utterance_id):
            return Triage(True, "duplicate_utterance")
        # the same command heard twice from two audio paths within a few seconds runs once
        now = self._clock()
        key = "|".join(f"{i.intent}:{json.dumps(i.params, sort_keys=True)}" for i in found)
        if now - self._recent.get(key, -1e9) < DEDUPE_SECONDS:
            return Triage(True, "duplicate_command_window")
        approved: list[tuple[SeriesIntent, bool]] = []
        for item in found:
            decision = self.gate.decide(item, authenticated_owner=authenticated_owner, self_echo=self_echo, profile=profile,
                                        series_state=state, source=source)
            if not decision.allowed:
                generic_denial = item.intent in GENERIC_INTENTS and decision.reason.startswith(STATE_OR_PROFILE_DENIALS)
                if approved:
                    break
                if generic_denial or decision.reason in {"not_the_authenticated_owner", "probable_self_echo", "unknown_source", "not_a_command"}:
                    return Triage(False, decision.reason)
                return Triage(True, decision.reason, [], refusal=decision.reason)
            approved.append((item, False))
        self._recent[key] = now
        for stale in [k for k, t in self._recent.items() if now - t > 30.0]:
            self._recent.pop(stale, None)
        return Triage(True, "command", approved, source=source)

    # ---- execution (slow, run as a background task) ------------------------------------------------------------------------------------------------
    @staticmethod
    def _request_id(utterance_id: str | None, index: int, intent: str) -> str:
        base = hashlib.sha1(f"{utterance_id or time.time_ns()}".encode()).hexdigest()[:16]
        return _RID.sub("", f"u-{base}-{index}-{intent.split('.')[-1]}")[:64]

    async def execute(self, triage: Triage, *, utterance_id: str | None = None) -> Outcome:
        if triage.refusal:
            return Outcome([], build_brief([f"FATO: não fiz isso: {_refusal_text(triage.refusal)}"]))
        results: list[dict[str, Any]] = []
        facts: list[str] = []
        remaining = list(triage.commands)
        for index, (item, confirmed) in enumerate(list(remaining)):
            body = {"intent": item.intent, "params": item.params, "request_id": self._request_id(utterance_id, index, item.intent),
                    "attested": True, "confirmed": confirmed, "requester": "owner_text" if triage.source == "browser_text" else "owner_voice"}
            result = await asyncio.to_thread(self._request, "POST", "/series/command", body, 240.0)
            self.invalidate_state()
            results.append(result)
            facts.extend(facts_for(item.intent, item.params, result))
            if result.get("needs_confirmation"):
                self._pending = {"intent": item, "rest": [i for i, _ in remaining[index + 1:]], "at": self._clock()}
                break
            if not result.get("ok"):
                break
        return Outcome(results, build_brief(facts) if facts else None)


_STATE_PT = {"NO_WORLD": "o mundo da série ainda não foi aberto", "INTRO_READY": "a gente ainda está na introdução, o jogo nem começou",
             "GAMEPLAY": "a gente já está jogando", "TALK_PAUSE": "a gente está na pausa pra conversar", "EPISODE_END": "o episódio já foi encerrado",
             "WORLD_LOADING": "o mundo ainda está carregando", "BLOCKED": "a série travou e precisa ser destravada fora da gravação",
             "RECOVERY": "a série está se recuperando de uma falha"}


def _refusal_text(reason: str) -> str:
    if reason.startswith("not_valid_in_state_"):
        # 24/10 (missão EP1, R19): no ensaio, "prepara a introdução" com o jogo rolando virou "Não entendi o que você quer com isso."
        where = _STATE_PT.get(reason[len("not_valid_in_state_"):])
        return f"isso não dá pra fazer agora porque {where}" if where else "esse pedido não faz sentido neste momento da gravação"
    if reason == "profile_is_not_conversation_autonomous":
        return "o perfil atual não é o Conversa Autônoma"
    return "o pedido não passou pelo controle do dono"


UI_INTENTS = frozenset({"series.prepare_intro", "series.start_gameplay", "series.talk_pause", "series.resume", "series.end_episode", "world.list",
                        "world.checkpoint", "world.backup", "world.create", "companion.return", "control.emergency_stop", "world.hold_day"})


async def ui_command(center: SeriesCommandCenter, intent: str, params: dict[str, Any], *, confirmed: bool = False) -> dict[str, Any]:
    """The profile editor's fallback buttons: the same gate, the same typed forward, no persona speech. The caller is the loopback browser."""
    if intent not in UI_INTENTS:
        return {"ok": False, "code": "unlisted_intent"}
    item = SeriesIntent("command", intent, dict(params or {}), "ui", "ui_button", ui_valid_states(intent))
    state = await center.series_state()
    decision = center.gate.decide(item, authenticated_owner=True, self_echo=False, profile=center._profile(), series_state=state, source="browser_text")
    if not decision.allowed:
        return {"ok": False, "code": decision.reason, "series_state": state}
    body = {"intent": intent, "params": item.params, "request_id": SeriesCommandCenter._request_id(None, 0, intent), "attested": True, "confirmed": bool(confirmed),
            "requester": "owner_ui"}
    result = await asyncio.to_thread(center._request, "POST", "/series/command", body, 240.0)
    center.invalidate_state()
    return result


_center: SeriesCommandCenter | None = None


SERIES_LIVE_STATES = frozenset({"WORLD_LOADING", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE", "RECOVERY", "BLOCKED"})
SERIES_TOOLS_BLOCKED = frozenset({"minecraft_acao", "minecraft_objetivo", "minecraft_parar"})


def series_live() -> bool:
    """24/10 (missão EP1, risco R6): em 21/10 o comentário "Zera Minecraft." virou `minecraft_parar` pela ferramenta da LLM, e
    "vou fazer uma série" virou objetivo. Enquanto a série grava, o corpo só obedece ao caminho determinístico da série; a LLM fica sem as
    ferramentas que mexem no corpo. Usa o último estado que a central já leu do daemon (sem chamada de rede aqui)."""
    if _center is None:
        return False
    return _center._state_cache[1] in SERIES_LIVE_STATES


def get_series_center() -> SeriesCommandCenter:
    """The one command center of the process (idempotency windows and a pending confirmation must be shared by voice, text and UI)."""
    global _center
    if _center is None:
        from .continuous_behavior import continuous_behavior

        _center = SeriesCommandCenter(profile=lambda: continuous_behavior.profile)
    return _center


async def run_in_background(center: SeriesCommandCenter, triage: Triage, utterance_id: str | None, speak: Callable[[str], Awaitable[Any]]) -> Outcome:
    """Execute a consumed triage and hand the persona brief (if any) to ``speak``. Never raises into the microphone loop."""
    try:
        outcome = await center.execute(triage, utterance_id=utterance_id)
        if outcome.brief:
            await speak(outcome.brief)
        return outcome
    except Exception as exc:  # the owner must never be left wondering: a failure is also a fact
        brief = build_brief(["FATO: não consegui fazer isso agora e não vou inventar o motivo"])
        try:
            await speak(brief)
        except Exception:
            pass
        return Outcome([{"ok": False, "code": type(exc).__name__}], brief)
