"""Deterministic voice/text intents for the Célia x Criador series (plan V2 §4). No language model is involved.

A spoken line is a COMMAND only when the WHOLE utterance is that command (plus a vocative, politeness or a time filler). A phrase that is
merely inside a longer sentence, quoted, hypothetical or negated is never a command, so "quando eu disser bora jogar..." and "não vamos
começar a jogar ainda" do nothing. A detected intent is only a PROPOSAL: ``TrustedOwnerControlGate`` (deny by default, outside any LLM)
decides whether it may run, and the central/daemon re-check their own contracts.

World-changing commands never carry a console string: they are typed intents with validated parameters (see ``minecraft_series``).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

PROFILE_ID = "conversation_autonomous"
MAX_COMMANDS_PER_UTTERANCE = 2
_NUMBER_WORDS = {"um": 1, "uma": 1, "1": 1, "01": 1, "dois": 2, "duas": 2, "2": 2, "02": 2, "tres": 3, "3": 3, "03": 3,
                 "quatro": 4, "4": 4, "04": 4, "cinco": 5, "5": 5, "05": 5, "seis": 6, "6": 6, "06": 6, "sete": 7, "7": 7, "07": 7,
                 "oito": 8, "8": 8, "08": 8, "nove": 9, "9": 9, "09": 9}
_NUM = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_SERIE_NUM = rf"(?:s ?0?({_NUM})|({_NUM}))"

VOCATIVE = r"(?:(?:celia|selia|cela|sylia|silia|cilia|celea)[ ]+)"
FILL_HEAD = r"(?:(?:por favor|entao|ai|ei|opa|oi|hum|hm|ah|tipo|bora la|olha|agora|ja|vai|pode|poderia|consegue|quero que (?:voce |tu |ela )?)(?: me| a gente)? ?)*"
END_HEAD = r"(?:(?:ai|serio|olha|bom|pois e|entao|po|cara|mano) )*(?:(?:eu )?(?:acho|achei) que )?"  # "ai, sério, eu acho que tá bom por hoje"
TAIL_NAME = r"(?: (?:celia|selia|cela|sylia|silia|cilia|celea))?"  # "tá, mas eu vou contigo, Célia"
FILL_TAIL = r"(?: (?:agora|por favor|ai|ja|ali|pra mim|para mim|pra gente|para a gente|um pouquinho|um pouco))*"

# folded text that signals "this is not an order": quotation, report, condition, wish, past or future talk
_QUOTE = re.compile(
    r"\b(?:quando|se|caso|sempre que|toda vez que|depois que|assim que) (?:eu|a gente|nos|ele|ela|voce|tu) (?:falar|disser|dizer|falo|digo|falei|disse|mandar|pedir|peco|pedi)\b|"
    r"\b(?:eu|ele|ela|voce|tu|a gente|nos|o criador|a celia|alguem) (?:falei|falou|disse|falo|digo|dissemos|falamos|mandei|mandou|pediu|pedi)\b|"
    r"\b(?:a frase|a palavra|o comando|o gatilho|o bora|escrito|dito)\b|\bvou (?:falar|dizer)\b|\bvai (?:falar|dizer)\b|"
    r"\bfalei bora\b|\bdisse bora\b|\bhoje eu (?:vou|vamos)\b|\bqueria\b|\bgostaria\b|\bseria (?:bom|legal)\b|\bimagina\b|\bsera que\b|\btalvez\b|"
    r"\bamanha\b|\bontem\b|\bdepois\b|\bmais tarde\b|\bnao precisa\b|\bsem\b"
)
_NEGATION = re.compile(r"(?:^|\b)(?:nao|nunca|jamais|ainda nao|calma|espera ai|peraí|pera ai|de jeito nenhum|nem)\b")


_HESITATIONS = frozenset({"e", "hum", "hm", "hmm", "ah", "eh", "tipo", "ne", "ei", "opa", "oi", "bom", "entao", "ai", "pois", "po"})


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    value = value.replace("–", " ").replace("—", " ")
    return value


def _clean(text: str) -> str:
    value = re.sub(r"[,;:\"'()\[\]{}<>«»…]+", " ", text)
    value = re.sub(r"[-_/]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


@dataclass(frozen=True)
class SeriesIntent:
    action: str  # "command" | "quoted" | "negated" | "mention" | "none"
    intent: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    matched_text: str = ""
    reason: str = ""
    # states in which the intent makes sense. NO_WORLD means "series not engaged": commands that only make sense inside a session are ignored
    # there, so that "bora jogar" in a normal conversation never becomes an order.
    valid_states: frozenset[str] = frozenset()

    @property
    def should_act(self) -> bool:
        return self.action == "command" and self.intent is not None


def _pat(body: str) -> re.Pattern[str]:
    return re.compile(rf"^{FILL_HEAD}{VOCATIVE}?{FILL_HEAD}{body}{FILL_TAIL}{TAIL_NAME}$")


GAME = frozenset({"GAMEPLAY"})
SESSION = frozenset({"INTRO_READY", "GAMEPLAY", "TALK_PAUSE"})
ANY_STATE = frozenset({"NO_WORLD", "WORLD_LOADING", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE", "EPISODE_END", "BLOCKED", "RECOVERY"})
# (intent, params-factory, states, compiled pattern list)
_RESOURCES = r"(ferro|carvao|madeira|lenha|tronco|troncos|pedra|cobre|ouro|diamante)"
_DET = r"(?:(?:esse|essa|aquele|aquela|esses|essas|aqueles|aquelas|o|a|os|as|um|uma) )?"
# spoken mob word -> Minecraft entity id ("hostile" = any hostile mob; the body picks the NEAREST one inside its range)
_MOB_WORDS = {"zumbi": "zombie", "zumbis": "zombie", "zombie": "zombie", "esqueleto": "skeleton", "esqueletos": "skeleton", "aranha": "spider",
              "aranhas": "spider", "creeper": "creeper", "criper": "creeper", "slime": "slime", "bruxa": "witch", "enderman": "enderman",
              "afogado": "drowned", "vaca": "cow", "porco": "pig", "galinha": "chicken", "ovelha": "sheep", "coelho": "rabbit",
              "mob": "hostile", "mobs": "hostile", "monstro": "hostile", "monstros": "hostile", "bicho": "hostile", "inimigo": "hostile"}
_MOBS = "(" + "|".join(sorted(_MOB_WORDS, key=len, reverse=True)) + ")"
# spoken item word -> canonical word the companion resolves against the REAL inventory (families like madeira/picareta)
_ITEM_WORDS = {"graveto": "graveto", "gravetos": "graveto", "pau": "graveto", "paus": "graveto", "tocha": "tocha", "tochas": "tocha", "madeira": "madeira", "tronco": "madeira",
               "troncos": "madeira", "tabua": "tabua", "tabuas": "tabua", "pedra": "pedra", "pedras": "pedra", "carvao": "carvao", "ferro": "ferro",
               "pao": "pao", "paes": "pao", "maca": "maca", "macas": "maca", "comida": "comida", "carne": "comida", "espada": "espada",
               "picareta": "picareta", "machado": "machado", "pa": "pa", "escudo": "escudo", "bancada": "bancada", "mesa": "bancada",
               "fornalha": "fornalha", "terra": "terra", "areia": "areia", "flecha": "flecha", "flechas": "flecha", "arco": "arco"}
_ITEMS = "(" + "|".join(sorted(_ITEM_WORDS, key=len, reverse=True)) + ")"
_QTY = rf"(?:(?:o|a|os|as|um pouco de|uns|umas|algum|alguma|algumas|alguns)? ?({_NUM}) |(?:o|a|os|as|um pouco de|uns|umas|algum|alguma|algumas|alguns) )?"


_SKILL_WORDS = {"ponte": "safe-bridge", "torre": "pillar-up", "pilar": "pillar-up", "escada": "stairs", "abrigo": "emergency-shelter",
                "casinha": "emergency-shelter", "cabana": "emergency-shelter", "comemora": "comemorar", "comemore": "comemorar",
                "faz a dancinha": "comemorar", "faz uma dancinha": "comemorar"}


def _mob(word: str) -> str:
    return _MOB_WORDS[word]


def _item_word(word: str) -> str:
    return _ITEM_WORDS[word]


def _count(word: str | None) -> int:
    return min(64, _NUMBER_WORDS.get(word or "", 1))
_RULES: list[tuple[str, Any, frozenset[str], list[re.Pattern[str]]]] = [
    ("control.emergency_stop", lambda m: {}, ANY_STATE, [
        _pat(r"para (?:agora|tudo|imediatamente|ja)"), _pat(r"cancela(?: tudo| isso| agora)?"), _pat(r"para tudo agora"),
        re.compile(rf"^{VOCATIVE}para$"), re.compile(rf"^{VOCATIVE}(?:para|cancela) (?:agora|tudo)$")]),
    ("companion.return", lambda m: {}, ANY_STATE, [
        _pat(r"(?:volta|vem|venha|volte) (?:aqui|pra ca|para ca|pra perto|para perto|pro meu lado|para o meu lado|pra mim|para mim|pro lado|aqui pra perto)"),
        _pat(r"(?:volta|vem|venha|volte) (?:pra|para) (?:perto|camera|a camera|junto|o meu lado|meu lado)(?: de mim)?"),
        _pat(r"fica perto(?: de mim)?")]),
    # 2026-10-20 (decision 4a): short movement orders never go through the language model ("pode se mexer" once became a malformed tool call,
    # parametros='forward 1', and the body stayed still). They only count INSIDE a series session, in the series profile: "anda" in an ordinary
    # conversation is just a word. They all mean "go on moving beside me".
    # 2026-10-21 presence controls (the local executor, deterministic, no model): "fica parada" holds her until an explicit release; "se mexe um pouquinho" is a
    # short visible demonstration (no mission, no permission); "pode se mexer" releases the hold. Explicit orders always beat her spontaneous circulation.
    ("companion.hold", lambda m: {}, SESSION, [
        _pat(r"(?:fica|fique|ficar) (?:ai |aqui |ali )?parad[ao]"), _pat(r"(?:para|pare) de (?:andar|se mexer|circular|caminhar)"), _pat(r"(?:fica|fique) (?:ai|aqui) parad[ao]")]),
    ("companion.release", lambda m: {}, SESSION, [
        re.compile(rf"^{FILL_HEAD}{VOCATIVE}?(?:pode )(?:se mexer|andar|circular|caminhar)(?: de novo| novamente)?{FILL_TAIL}$"),
        _pat(r"(?:volta|volte|voltar) a (?:andar|se mexer|circular|caminhar)"), _pat(r"(?:solta|solte|libera|libere) (?:o )?corpo(?: dela)?"),
        _pat(r"(?:solta|solte|libera|libere) (?:ela|a celia)")]),
    ("companion.wiggle", lambda m: {}, SESSION, [
        _pat(r"se mexe(?: um pouquinho| um pouco| um instante)?(?: pra eu ver| para eu ver| pra mim| para mim)?"), _pat(r"anda um pouquinho(?: pra eu ver| para eu ver)?"),
        _pat(r"anda um pouco(?: pra eu ver| para eu ver)?"), _pat(r"da uma andadinha(?: pra eu ver)?"), _pat(r"mostra que (?:voce )?(?:se )?mexe"),
        _pat(r"da uma mexida(?: pra eu ver)?")]),
    ("companion.jump", lambda m: {}, SESSION, [
        _pat(r"(?:pula|pule|pular|da um pulo|salta|salte)(?: uma vez| um pouquinho)?")]),
    ("companion.crouch", lambda m: {}, SESSION, [
        _pat(r"(?:agacha|agache|abaixa|abaixe)(?: ai| aqui)?")]),
    ("companion.stand", lambda m: {}, SESSION, [
        _pat(r"(?:levanta|levante|fica de pe|fique de pe|pode levantar)(?: ai| aqui)?")]),
    ("companion.follow", lambda m: {}, SESSION, [
        _pat(r"anda(?:r)?(?: comigo| junto| pra frente| para frente)?"), _pat(r"(?:me )?segue(?:-me)?"),
        _pat(r"vem comigo"), _pat(r"(?:vamos|bora|vamo) (?:andar|caminhar)")]),
    ("companion.look", lambda m: {}, SESSION, [
        _pat(r"me olh(?:a|e|ar)"), re.compile(rf"^{VOCATIVE}?olh(?:a|e|ar) (?:pra|para) (?:mim|aqui|ca)$")]),  # a bare "olha" is a filler, never an order
    ("series.talk_pause", lambda m: {"freeze_world": False}, GAME, [
        _pat(r"(?:para|pausa|segura)(?: ai| a)? (?:um pouquinho|um instante|um minuto|um segundo|um pouco)"),
        _pat(r"(?:vamos|bora) conversar(?: um pouquinho| um pouco)?"), _pat(r"(?:segura|pausa) ai"), _pat(r"pera ai um pouquinho")]),
    ("series.talk_pause", lambda m: {"freeze_world": True}, GAME, [
        _pat(r"congela o mundo(?: um pouquinho)?"), _pat(r"(?:para|pausa) (?:o mundo|tudo) (?:e |pra )?conversar")]),
    ("series.resume", lambda m: {}, frozenset({"TALK_PAUSE"}), [
        _pat(r"(?:pode )?continua(?:r)?"), _pat(r"vamos (?:voltar|continuar)(?: a jogar| ao jogo| o jogo)?"), _pat(r"(?:pode )?voltar (?:ao jogo|a jogar|a minerar)"),
        _pat(r"voltamos ao jogo"), _pat(r"solta(?: a|)? (?:ela|celia)")]),
    # fix / release the clock of the day INSIDE the series state machine (not a raw server command, not the body, not the tick rate)
    ("world.hold_day", lambda m: {"hold": True}, SESSION, [
        _pat(r"(?:fixa|fixe|deixa|deixe|segura|mantem|mantenha) (?:o )?(?:dia|de dia|o tempo de dia|a hora do dia|o dia fixo)"), _pat(r"(?:para|pare|congela|congele) (?:o )?(?:tempo|relogio)(?: do jogo)?"),
        _pat(r"nao deixa anoitecer"), _pat(r"(?:tira|tire) a noite")]),
    ("world.hold_day", lambda m: {"hold": False}, SESSION, [
        _pat(r"(?:solta|solte|libera|libere|deixa|deixe) (?:o )?tempo (?:correr|passar|andar|voltar)"), _pat(r"(?:solta|solte|libera|libere) (?:o )?(?:dia|relogio|tempo)"),
        _pat(r"(?:pode )?(?:deixar )?(?:a noite )?(?:vir|chegar)")]),
    ("series.start_gameplay", lambda m: {"goal": "build"}, SESSION, [
        _pat(r"(?:bora|vamos|vamo) construir(?: a| nossa| a nossa| uma)?(?: casa| base| nossa casa| nossa base)?"),
        _pat(r"(?:vamos|bora|vamo) comecar(?: a)? construir(?: a| nossa)?(?: casa| base)?")]),
    ("series.start_gameplay", lambda m: {"goal": "progression"}, SESSION, [
        _pat(r"(?:bora|vamos|vamo) (?:tentar )?zerar(?: o)?(?: minecraft| mine craft| jogo| o jogo)?"),
        _pat(r"(?:vamos|bora|vamo) comecar(?: a)? zerar(?: o)?(?: minecraft| jogo)?")]),
    ("series.start_gameplay", lambda m: {"goal": "progression"}, frozenset({"INTRO_READY"}), [
        _pat(r"(?:vamos|bora|vamo) (?:comecar|iniciar)(?: a)? jogar"), _pat(r"(?:bora|vamos|vamo) jogar"),
        _pat(r"(?:agora )?pode (?:comecar|iniciar)(?: a jogar| o jogo)?"), _pat(r"(?:vamos|bora) (?:comecar|iniciar) o jogo")]),
    # releasing the world is only that: time runs again and she stays beside him, no mission
    ("series.start_gameplay", lambda m: {"goal": None}, frozenset({"INTRO_READY"}), [
        _pat(r"(?:pode )?solt(?:a|ar|e) o mundo"), _pat(r"(?:pode )?libera(?:r)? o mundo")]),
    ("series.prepare_intro", lambda m: {}, frozenset({"NO_WORLD", "EPISODE_END", "INTRO_READY"}), [
        _pat(r"(?:vamos |bora )?prepar(?:a|e|ar)(?: a| o)? serie(?: nossa)?"), _pat(r"(?:vamos |bora )?prepar(?:a|e|ar)(?: a| o)? mundo da serie"),
        _pat(r"(?:vamos |bora )?prepar(?:a|e|ar)(?: a| o)? (?:introducao|intro)(?: da serie)?"), _pat(r"prepara a serie nova")]),
    ("world.select", lambda m: _world_param(m), frozenset({"NO_WORLD", "EPISODE_END", "INTRO_READY"}), [
        _pat(rf"(?:abre|abra|abrir|entra|entre|entrar|vamos|bora|vamo) (?:o |no |pro |para o |pra o |para |pra )?mundo (?:da|de|do) serie(?: {_SERIE_NUM})?"),
        _pat(rf"(?:abre|abra|abrir|entra|entre|entrar|vamos|bora|vamo) (?:o |no |pro |para o )?mundo celia serie(?: {_SERIE_NUM})?"),
        _pat(rf"(?:abre|abra|abrir) a serie(?: {_SERIE_NUM})?")]),
    ("world.list", lambda m: {}, ANY_STATE, [
        _pat(r"(?:mostra|mostre|lista|liste|exibe) (?:os |todos os )?mundos(?: que nos temos| que temos| da serie)?"),
        _pat(r"(?:quais|que) mundos (?:nos |a gente )?(?:temos|tem|existem|ha)"), _pat(r"quais sao os mundos")]),
    ("world.create", lambda m: _create_param(m), frozenset({"NO_WORLD", "EPISODE_END"}), [
        _pat(rf"cria(?:r)?(?: um)? (?:novo )?mundo (?:pra|para) (?:nossa|a nossa|a) serie(?: {_SERIE_NUM})?"),
        _pat(rf"cria(?:r)? a serie {_SERIE_NUM}"), _pat(rf"cria(?:r)? o mundo (?:da|de) serie {_SERIE_NUM}")]),
    ("world.checkpoint", lambda m: {"label": "voz"}, ANY_STATE - {"WORLD_LOADING"}, [
        _pat(r"salva(?:r)?(?: esse| este| o)? (?:mundo|progresso|jogo|tudo)"), _pat(r"salva o progresso"),
        _pat(r"faz um (?:checkpoint|ponto de retorno)")]),
    ("world.backup", lambda m: {"label": "voz"}, ANY_STATE - {"WORLD_LOADING"}, [
        _pat(r"faz(?:er)? (?:um )?backup(?: desse mundo| do mundo| da serie)?"), _pat(r"(?:cria|criar) (?:um )?backup")]),
    ("series.end_episode", lambda m: {"with_backup": True}, SESSION, [
        _pat(r"encerra(?:r)?(?: esse| este| o)? episodio"), _pat(r"(?:vamos |bora )?encerrar(?: o)?(?: episodio| por hoje)"),
        _pat(r"terminamos(?: por hoje)?"), _pat(r"por hoje (?:e isso|chega|acabou|terminamos)"), _pat(r"(?:vamos )?terminar por hoje"),
        _pat(END_HEAD + r"ta bom por hoje" + TAIL_NAME), _pat(END_HEAD + r"(?:e )?isso por hoje" + TAIL_NAME),
        _pat(END_HEAD + r"(?:ta bom por hoje )?(?:(?:eu )?(?:vou|vamos) (?:me )?)?(?:encerra|encerrar|fecha|fechar) (?:o|esse|este) video" + TAIL_NAME)]),
    # ---- candidata B (2026-10-23): everyday survival orders Criador gives a partner. Deterministic, whole-utterance only, like the rest. ----
    ("companion.task", lambda m: {"kind": "attack", "target": _mob(m.group(1))}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(rf"(?:mata|mate|matar|ataca|ataque|atacar|bate|bate n[oa]|acaba com) {_DET}{_MOBS}(?: ali| la| aqui| perto)?")]),
    ("companion.give", lambda m: {"item": _item_word(m.group(2)), "count": _count(m.group(1))}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(rf"me (?:da|de|passa|passe|joga|jogue|empresta|entrega|manda) {_QTY}{_ITEMS}"),
        _pat(rf"(?:da|passa|joga|entrega) {_QTY}{_ITEMS} (?:pra mim|para mim|aqui)")]),
    ("companion.report", lambda m: {"what": "hand"}, frozenset({"NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(r"o que (?:que )?(?:voce|tu) (?:tem|ta|esta|tas) (?:segurando|na mao|com na mao)"), _pat(r"o que (?:que )?tem na (?:tua|sua) mao"),
        _pat(r"o que (?:que )?(?:voce|tu) (?:tem|ta|esta) (?:na|com) (?:a )?mao"), _pat(r"(?:ta|esta) segurando o que")]),
    ("companion.report", lambda m: {"what": "inventory"}, frozenset({"NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(r"o que (?:que )?(?:voce|tu) (?:tem|tens)(?: ai)?(?: no (?:teu |seu )?inventario)?"), _pat(r"o que (?:que )?tem no (?:teu |seu )?inventario"),
        _pat(r"(?:mostra|mostre|fala|diz) (?:o )?(?:teu |seu )?inventario")]),
    ("companion.report", lambda m: {"what": "item", "item": _item_word(m.group(1))}, frozenset({"NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(rf"(?:tu|voce) (?:ja )?(?:tem|tens) (?:algum |alguma )?{_ITEMS}"),
        _pat(rf"quant[oa]s? {_ITEMS} (?:tu|voce) (?:tem|tens)")]),
    ("companion.report", lambda m: {"what": "inventory"}, frozenset({"NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(r"o que (?:tu|voce) (?:pegou|coletou|juntou) ate agora")]),
    ("companion.report", lambda m: {"what": "armor"}, frozenset({"NO_WORLD", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(r"(?:(?:voce|tu) )?(?:ta|esta|tas) de armadura"), _pat(r"(?:voce|tu) tem armadura")]),
    ("companion.armor", lambda m: {}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(r"se equip(?:a|e|ar)"), _pat(r"(?:veste|vista|coloca|coloque|bota|bote|poe|ponha|equipa|equipe) (?:a |tua |sua )?armadura")]),
    ("companion.equip", lambda m: {"item": m.group(1)}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(r"(?:pega|pegue|segura|segure|equipa|equipe|saca|saque|usa|use) (?:a |o |tua |teu |sua |seu )?(espada|picareta|machado|escudo|pa|arco)"),
        _pat(r"(?:coloca|coloque|bota|bote|poe|ponha|poe) (?:a |o |tua |teu |sua |seu )?(espada|picareta|machado|escudo|pa|arco) na mao")]),
    # V3: a capability that lives in Jev's versioned skill catalog. If the active catalog has no such skill, the daemon REGISTERS a skill-gap job
    # (Codex -> Claude Code) and the persona says so; nothing is improvised by the body.
    ("skill.use", lambda m: {"skill_id": _SKILL_WORDS[m.group(1)]}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(r"(?:constroi|construa|construir|faz|faca|fazer|monta|monte|levanta|ergue) (?:uma |um |a |o )?(ponte|torre|pilar|escada|abrigo|casinha|cabana)"
             r"(?: ali| aqui| pra gente| pra nos| pra gente passar| pra gente subir)?"),
        _pat(r"(comemora|comemore|faz a dancinha|faz uma dancinha)")]),
    # scene director (23/10): "hoje tu tá rebelde" arms the rebel night; "eu vou contigo" is only meaningful while she is outside
    ("scene.arm_rebel", lambda m: {}, frozenset({"INTRO_READY", "GAMEPLAY", "TALK_PAUSE"}), [
        _pat(r"hoje (?:tu|voce) (?:ta|esta|vai (?:ficar|estar|ser)) rebelde" + TAIL_NAME), _pat(r"(?:tu|voce) (?:ta|esta|vai ficar|vai estar) rebelde hoje" + TAIL_NAME),
        _pat(r"hoje (?:e|eh) dia de (?:ser )?rebelde" + TAIL_NAME), _pat(r"fica rebelde hoje" + TAIL_NAME), _pat(r"hoje (?:tu|voce) (?:e|eh) rebelde" + TAIL_NAME)]),
    # 24/10 (missão EP1, R20): a missão do Jev travada ("sem progresso") deixava ela parada o resto do dia; com a série gravando a persona não mexe
    # mais no corpo (R6), então retomar é um comando da série, inteiro, como os outros.
    ("series.resume_mission", lambda m: {}, frozenset({"GAMEPLAY"}), [
        _pat(r"(?:continua|continue|retoma|retome|segue|siga)(?: com)? (?:a )?(?:missao|tarefa|trabalho)" + TAIL_NAME),
        _pat(r"(?:volta|volte) (?:a|pra|para|pro) (?:missao|trabalhar|trabalho|tarefa)" + TAIL_NAME),
        _pat(r"(?:pode )?(?:voltar a trabalhar|voltar ao trabalho)" + TAIL_NAME), _pat(r"tenta (?:de novo|outra vez|novamente)" + TAIL_NAME),
        _pat(r"continua de onde (?:parou|tu parou|voce parou)" + TAIL_NAME)]),
    # R23 (24/10): resposta ao "me diz se posso me afastar mais" (a coleira do "bora jogar" é 12 blocos; ele libera até 24 nesta missão)
    ("series.widen_leash", lambda m: {}, frozenset({"GAMEPLAY"}), [
        _pat(r"(?:pode|podes) (?:se afastar|te afastar|ir mais longe|ir longe|se afastar mais|ir buscar(?: mais longe)?)(?: mais)?" + TAIL_NAME),
        _pat(r"(?:vai|va) (?:mais longe|buscar mais longe)" + TAIL_NAME), _pat(r"(?:tudo bem|ta bom|ok),? (?:pode )?(?:se afastar|ir mais longe)" + TAIL_NAME)]),
    ("scene.join", lambda m: {}, frozenset({"GAMEPLAY"}), [
        _pat(r"(?:ta(?: bom)?,? )?(?:mas )?eu vou (?:contigo|junto|com voce|com tu)" + TAIL_NAME), _pat(r"(?:entao )?vou contigo" + TAIL_NAME), _pat(r"(?:eu )?vou junto" + TAIL_NAME)]),
    ("companion.eat", lambda m: {}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(r"(?:vai )?(?:come|coma|comer)(?: alguma coisa| algo| um pouco)?")]),
    ("companion.task", lambda m: {"kind": "mine", "resource": m.group(1)}, frozenset({"NO_WORLD", "GAMEPLAY"}), [
        _pat(rf"(?:pega|pegue|pegar|minera|mine|minerar|coleta|coletar|junta|juntar) {_DET}{_RESOURCES}"),
        _pat(rf"(?:pega|pegue|minera|mine|coleta|junta) {_DET}{_RESOURCES} (?:ali|la|aqui|perto)"),
        _pat(rf"(?:vai |va )?(?:pegar|minerar|coletar) {_DET}{_RESOURCES}")]),
]


def _world_param(m: re.Match[str]) -> dict[str, Any]:
    number = next((g for g in m.groups() if g in _NUMBER_WORDS), None)
    return {"world_id": f"celia-serie-s{_NUMBER_WORDS[number]:02d}"} if number else {}


def _create_param(m: re.Match[str]) -> dict[str, Any]:
    number = next((g for g in m.groups() if g in _NUMBER_WORDS), None)
    series = f"s{_NUMBER_WORDS[number]:02d}" if number else None
    return {"series": series, "name": f"Célia e Criador — Série {_NUMBER_WORDS[number]:02d}" if number else ""}


# inside a longer utterance: used only to classify "mention" (never to act)
_LOOSE = re.compile(
    r"\b(?:bora jogar|vamos (?:comecar a )?jogar|bora construir|bora zerar|vamos zerar|prepar\w+ a serie|abr\w+ o mundo da serie|"
    r"salva\w* (?:esse|o) mundo|encerr\w+ (?:esse |o )?episodio|volta aqui|pega\w* (?:esse |aquele |o )?(?:ferro|madeira|carvao)|"
    r"para agora|cancela|pula|agacha|abaixa|levanta|fica de pe|mata (?:esse |aquele |o |a )?\w+|me da (?:um |uma )?\w+|se equip\w*|armadura)\b"
)


def _single(segment: str) -> SeriesIntent | None:
    for intent, make, states, patterns in _RULES:
        for pattern in patterns:
            match = pattern.match(segment)
            if match:
                return SeriesIntent("command", intent, make(match), segment, "explicit_command", states)
    return None


def detect_series_intent(text: str) -> list[SeriesIntent]:
    """Classify an utterance. Returns [] when it is ordinary talk, else one SeriesIntent per command (at most 2) or ONE non-command marker."""
    folded = _fold(text)
    if not folded.strip():
        return []
    # a hesitation segment ("é...", "hum.", "tipo,") is noise, not a second sentence that has to match
    segments = [s for s in (_clean(part) for part in re.split(r"[.!?;]+", folded)) if s and s not in _HESITATIONS]
    if not segments:
        return []
    whole = " ".join(segments)
    if _QUOTE.search(whole):
        return [SeriesIntent("quoted", None, {}, whole[:120], "quoted_or_reported_speech")] if _LOOSE.search(whole) else []
    if _NEGATION.search(whole):
        return [SeriesIntent("negated", None, {}, whole[:120], "negated_or_postponed")] if _LOOSE.search(whole) else []
    found: list[SeriesIntent] = []
    for segment in segments:
        intent = _single(segment)
        if intent is None:
            # EP1: ASR can join two imperatives with "e" instead of punctuation.
            # Accept this only when BOTH halves are complete commands and one is
            # an emergency stop. Comments/quotes/negations must never start work.
            halves = re.split(r"\s+e\s+", segment)
            pair = [_single(part) for part in halves] if len(halves) == 2 else []
            if pair and all(pair) and any(i.intent == "control.emergency_stop" for i in pair):
                found.extend(pair)
                continue
            found = []
            break
        found.append(intent)
    stops = [i for i in found if i.intent == "control.emergency_stop"]
    if stops:  # a stop always wins and always runs ALONE: nothing else in the same breath may start work
        return [stops[0]]
    if found and len(found) <= MAX_COMMANDS_PER_UTTERANCE:
        # the same command twice in a row is one command (an ASR echo), not two
        unique = [found[0]]
        for item in found[1:]:
            if (item.intent, item.params) != (unique[-1].intent, unique[-1].params):
                unique.append(item)
        return unique
    if _LOOSE.search(whole):
        return [SeriesIntent("mention", None, {}, whole[:120], "command_phrase_inside_a_longer_sentence")]
    return []


# ----------------------------------------------------------------------------------------------------- confirmation words ------------------
_CONFIRM = re.compile(rf"^{VOCATIVE}?(?:sim )?(?:eu )?(?:confirma|confirmo|confirmado|pode confirmar|pode fazer|pode criar|pode reiniciar(?: o servidor)?|pode abrir|pode trocar|isso mesmo confirma|manda ver)(?: celia)?$")
_CANCEL = re.compile(rf"^{VOCATIVE}?(?:cancela(?: isso)?|deixa pra la|deixa pra la mesmo|esquece|nao confirma|nao precisa|melhor nao)$")


def classify_confirmation(text: str) -> str | None:
    """"confirm" / "cancel" / None. Only a short utterance consisting of the confirmation itself counts."""
    value = _clean(_fold(re.sub(r"[.!?]+", " ", text or "")))
    if not value or len(value) > 48:
        return None
    if _CANCEL.match(value):
        return "cancel"
    if _CONFIRM.match(value):
        return "confirm"
    return None


# ----------------------------------------------------------------------------------------------------- the gate (deny by default) -------------
@dataclass(frozen=True)
class GateDecision:
    allowed: bool
    reason: str


class TrustedOwnerControlGate:
    """Privileges are granted HERE, outside the model. Everything not explicitly allowed is refused.

    Inputs come from the transport the central controls, never from the transcript: ``authenticated_owner`` is true only for the owner's own
    microphone path or the loopback browser; ``self_echo`` is true when the heard text is probably Célia's own voice coming back.
    """

    MUTATING = frozenset({"series.prepare_intro", "world.select", "world.create", "world.checkpoint", "world.backup", "series.start_gameplay",
                          "series.talk_pause", "series.resume", "series.end_episode", "companion.task", "companion.follow", "companion.look", "world.hold_day",
                          "companion.hold", "companion.release", "companion.wiggle", "companion.jump", "companion.crouch", "companion.stand",
                          # candidata B: gated like the other companion orders (series profile + valid state). report is read-only but still series-only,
                          # so "o que tu tem?" in an ordinary chat stays ordinary conversation.
                          "companion.give", "companion.armor", "companion.equip", "companion.eat", "companion.report", "skill.use", "scene.arm_rebel", "scene.join",
                          "series.resume_mission", "series.widen_leash"})
    ALWAYS = frozenset({"control.emergency_stop", "companion.return", "world.list"})

    def decide(self, intent: SeriesIntent, *, authenticated_owner: bool, self_echo: bool, profile: str | None, series_state: str | None,
               source: str) -> GateDecision:
        if not intent.should_act:
            return GateDecision(False, "not_a_command")
        if not authenticated_owner:
            return GateDecision(False, "not_the_authenticated_owner")
        if self_echo:
            return GateDecision(False, "probable_self_echo")
        if source not in {"microphone", "browser_text"}:
            return GateDecision(False, "unknown_source")
        if intent.intent in self.ALWAYS:
            return GateDecision(True, "always_allowed_for_the_owner")
        if intent.intent in self.MUTATING:
            if profile != PROFILE_ID:
                return GateDecision(False, "profile_is_not_conversation_autonomous")
            state = series_state or "NO_WORLD"
            if state not in intent.valid_states:
                return GateDecision(False, f"not_valid_in_state_{state}")
            return GateDecision(True, "owner_in_series_profile")
        return GateDecision(False, "unlisted_intent")


def ui_valid_states(intent: str) -> frozenset[str]:
    """States in which a UI button for ``intent`` makes sense (the union over its voice rules; the controller still validates)."""
    states: frozenset[str] = frozenset()
    for name, _make, valid, _patterns in _RULES:
        if name == intent:
            states |= valid
    return states
