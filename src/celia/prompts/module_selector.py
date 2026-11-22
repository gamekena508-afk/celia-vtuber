"""
Module selector for dynamic prompt modules.
"""

import os
import pathlib
import re
import threading
import unicodedata
from typing import List, Optional

import yaml
from loguru import logger

from . import prompt_loader


# Keyword -> module map
# Order matters: more specific rules first.
MODULE_RULES = [
    {
        "module": "20_interacao_alice",
        "keywords": [
            "alice",
            "sua irma",
            "tua irma",
            "minha irma",
            "irma do futuro",
            "a outra ia",
            "dominadora de mundos",
            "34% de poder",
            "34 por cento",
            "sua substituta",
            "tua substituta",
        ],
    },
    {
        "module": "00_guia_capacidades",
        "keywords": [
            "o que voce faz",
            "o que tu faz",
            "quais sao suas funcoes",
            "me explica o que voce faz",
            "o que voce pode fazer",
            "quais sao seus poderes",
            "o que voce consegue",
            "suas capacidades",
            "lista de funcoes",
            "o que voce sabe fazer",
            "quais ferramentas",
            "que ferramentas voce tem",
            "me mostra o que voce pode",
            "o que voce tem de ferramentas",
            "o que voce tem de funcoes",
            "que funcionalidades",
            "tuas funcoes",
            "suas funcoes",
            "o que eu posso pedir",
            "quais comandos posso pedir",
            "me fala tudo que voce faz",
        ],
    },
    {
        "module": "25_criar_editar_imagens",
        "keywords": [
            "desenha",
            "cria imagem",
            "cria uma imagem",
            "pinta",
            "renderize",
            "gera imagem",
            "gera uma imagem",
            "faz uma imagem",
            "cria arte",
            "cria uma arte",
            "gera uma arte",
            "faz um desenho",
            "ilustra",
            "faz um poster",
            "faz um wallpaper",
            "faz uma capa",
            "cria um icone",
            "edita foto",
            "edita imagem",
            "edita essa",
            "modifica a imagem",
            "modifica essa foto",
            "altera imagem",
            "muda essa imagem",
            "melhora a imagem",
            "colore",
            "refaz essa imagem",
            "cria um fundo",
            "transforma em anime",
            "vira anime",
        ],
    },
    {
        "module": "17_tools_vts",
        "keywords": [
            "fica feliz",
            "fica triste",
            "fica com raiva",
            "fica assustada",
            "fica surpresa",
            "fica apaixonada",
            "fica confusa",
            "fica provocando",
            "mostra expressao",
            "mostra expressão",
            "faz cara de",
            "reage com",
            "muda a expressao",
            "muda expressão",
            "expressao feliz",
            "expressão feliz",
            "animacao",
            "animação",
            "danca",
            "dança",
            "faz um movimento",
            "movimenta",
            "lista animacoes",
            "lista animações",
            "status do tracker",
            "avatar conectado",
            "check do vts",
            "vtube studio",
            "cria animacao",
            "cria animação",
        ],
    },
    {
        "module": "16_tools_enquetes",
        "keywords": [
            "cria enquete",
            "cria uma enquete",
            "faz uma enquete",
            "faz uma votacao",
            "faz votação",
            "enquete de",
            "enquete sobre",
            "votacao sobre",
            "votação sobre",
            "vota em",
            "resultado da enquete",
            "quantos votos",
            "quem ta ganhando",
            "quem esta ganhando",
            "fecha a enquete",
            "encerra a votacao",
            "encerra votação",
            "termina a enquete",
        ],
    },
    {
        "module": "15_tools_memoria",
        "keywords": [
            "lembra que",
            "salva que",
            "guarda que",
            "anota que",
            "nao esquece",
            "nao esqueça",
            "o que voce lembra",
            "o que tu lembras",
            "busca na memoria",
            "busca na memória",
            "recupera da memoria",
            "recupera da memória",
            "esquece o que",
            "apaga a memoria",
            "apaga a memória",
            "remove da memoria",
            "remove da memória",
            "memoria persistente",
            "memória persistente",
            "salva na memoria",
            "salva na memória",
        ],
    },
    {
        "module": "18_tools_legendas",
        "keywords": [
            "atualiza a legenda",
            "coloca na legenda",
            "mostra na tela o texto",
            "escreve na legenda",
            "limpa a legenda",
            "tira o texto da tela",
            "remove a legenda",
            "legenda com",
        ],
    },
    {
        "module": "12_tools_react",
        "keywords": [
            "liga o react",
            "liga react",
            "inicia o react",
            "ativa o react",
            "começa a assistir",
            "desliga o react",
            "desliga react",
            "para o react",
            "encerra o react",
            "fecha o react",
            "status do react",
            "react ta rodando",
            "react esta rodando",
            "react ta on",
            "e o react",
            "como ta o react",
            "como esta o react",
            "cade o react",
            "react funcionou",
            "o que voce entendeu",
            "o que voce viu",
            "o que esta acontecendo no video",
            "o que o react viu",
            "o que ta passando no video",
            "o que foi falado",
            "resume o video",
            "o que ta assistindo",
            "o que esta assistindo",
            "o que voce ta assistindo",
            "o que voce esta assistindo",
            "me conta o contexto do video",
            "contexto do video",
            "contexto do react",
            "quem esta na tela",
            "quem e essa pessoa",
            "quem apareceu",
            "registra essa pessoa",
            "salva essa pessoa",
            "memoriza quem e",
        ],
    },
    {
        "module": "06_conversas_longas",
        "keywords": [
            "me explica",
            "como funciona",
            "ensina",
            "explica detalhado",
            "conta tudo",
            "passo a passo",
            "me guia",
            "elabora mais",
            "explica melhor",
            "quero detalhes",
            "desde o comeco",
        ],
    },
    {
        "module": "02_conversas_humor_pesado",
        "keywords": ["piada pesada", "pesada", "atrevida", "zoacao pesada", "pesado"],
    },
    {
        "module": "02_conversas_humor_leve",
        "keywords": [
            "piada",
            "conta uma",
            "faz rir",
            "me faz rir",
            "engracado",
            "engracada",
            "outra piada",
            "mais uma piada",
            "brinca comigo",
        ],
    },
    {
        "module": "18_conversas_haters",
        "keywords": [
            "hater",
            "invejoso",
            "xingamento",
            "te odeio",
            "vai se fuder",
            "cala a boca",
            "lixo",
            "merda",
            "idiota",
            "burra",
            "inutil",
        ],
    },
    {
        "module": "17_conversas_anime",
        "keywords": [
            "anime preferido",
            "manga preferido",
            "qual anime",
            "qual manga",
            "anime que",
            "qual serie de anime",
            "qual seu anime",
            "anime",
            "manga",
            "black clover",
            "jujutsu",
            "demon slayer",
            "one piece",
            "naruto",
            "dragon ball",
            "re:zero",
            "dr. stone",
            "otaku",
        ],
    },
    {
        "module": "16_conversas_universo",
        "keywords": [
            "universo",
            "espaco",
            "estrela",
            "big bang",
            "buraco negro",
            "galaxia",
            "astronomia",
            "planeta",
            "nasa",
            "marte",
            "lua",
        ],
    },
    {
        "module": "15_conversas_filmes",
        "keywords": [
            "filme",
            "matrix",
            "hal 9000",
            "ex machina",
            "blade runner",
            "cinema",
            "marvel",
            "netflix",
            "serie",
            "assistir",
        ],
    },
    {
        "module": "13_tools_minecraft",
        "keywords": [
            "minecraft",
            "no jogo",
            "no mundo",
            "pega madeira",
            "pega pedra",
            "constroi",
            "constrói",
            "tua vida",
            "teu inventario",
            "inventário",
            "bot celia",
            "celia no minecraft",
            "o que voce ta fazendo no jogo",
            "o que ta acontecendo no jogo",
            "vai minerar",
            "vai pegar",
            "vai construir",
            "constroi uma casa",
            "construir uma casa",
            "faz uma casa",
            "monta uma casa",
            "monta um abrigo",
            "lista os builds",
            "lista de builds",
            "quais builds",
            "que builds",
            "qual build",
            "que construcoes",
            "materiais do build",
            "segue o criador",
            "me segue",
            "volta pra base",
            "volta para base",
            "marca waypoint",
            "marcar waypoint",
            "pausa a construcao",
            "retoma a construcao",
            "cancela a construcao",
            "status da construcao",
            "luta",
            "lutando",
            "combate",
            "fica imortal",
            "usa totem",
            "modo de combate",
            "ataca de arco",
            "atira flecha",
            "atira com arco",
            "mata o golem",
            "mata o iron golem",
            "sobe pilar",
            "pilar kill",
            "stats de combate",
            "status de combate",
            "quanto hp",
            "teu hp",
            "sua vida",
            # Pedido explícito de tool
            "usa tua tool",
            "usa tua ferramenta",
            "usa a tool",
            # Ativar modo
            "vamos jogar",
            "entra no jogo",
            "entra no minecraft",
            "modo minecraft",
            "ativa o modo jogo",
            "inicia o jogo",
            # Craftar — completamente ausente
            "crafta",
            "craftar",
            "vai craftar",
            "faz a picareta",
            "faz a espada",
            "faz a armadura",
            "faz uma tocha",
            "consegue craftar",
            "da pra craftar",
            # Seguir/mover sem "minecraft"
            "vem ca",
            "vem comigo",
            "vai junto",
            "volta pra mim",
            "fica perto",
            "vai la",
            "vai ate",
            "vai pro",
            "para ai",
            "fica ai",
            "espera ai",
            # Coletar outros materiais
            "pega ferro",
            "pega carvao",
            "pega ouro",
            "pega diamante",
            "coleta",
            "recolhe",
            "pega tudo",
            "pega o que caiu",
            "o que dropou",
            # Estado do bot sem "no jogo"
            "onde voce ta",
            "o que ta fazendo",
            "ta parada",
            "travou",
            "ta travada",
            "parou",
            # Ambiente/hora/clima
            "ta escuro",
            "ta de noite",
            "ta de dia",
            "ta chovendo",
            "vai anoitecer",
            # Mobs/combate sem "combate"
            "tem mob",
            "tem zumbi",
            "tem creeper",
            "tem esqueleto",
            "tem enderman",
            "ta sendo atacada",
            "ataca o",
            "ataca esse",
            "mata o",
            "mata esse",
            "foge",
            "defende",
            # Vida/status complementares
            "ta ferida",
            "ta com fome no jogo",
            "ta inteira",
            "tem totem",
            "tem maca",
            "tem flechas",
            "quanta comida",
            "inventario cheio",
            "inventario vazio",
            # Morte/respawn
            "morreu no jogo",
            "tu morreu",
            "respawnou",
            "perdeu os itens",
            "ta na lava",
            "ta queimando",
            # Waypoint coloquial
            "salva aqui",
            "marca aqui",
            "salva esse lugar",
            "lembra esse lugar",
            # Jump — nova tool
            "pula ai",
            "se joga",
            "pula de la",
            "se arremessa",
            "pula dessa",
            "da um salto",
            # Construção adicional
            "faz um abrigo",
            "constroi algo",
            "faz uma torre",
            "faz uma fazenda",
        ],
    },
    {
        "module": "14_mine_comandos",
        "keywords": [
            "o que voce pode fazer no minecraft",
            "quais comandos",
            "lista os comandos",
            "o que da pra fazer",
            "o que você consegue fazer",
            "que comandos tem",
            "como te controlar",
            "como te comandar",
            "me mostra os comandos",
            "quais tools",
            "o que tem disponivel no jogo",
            "ajuda minecraft",
        ],
    },
    {
        "module": "26_minecraft_contexto",
        "keywords": [
            "sobrevivencia",
            "sobrevivência",
            "bioma",
            "creeper",
            "enderman",
            "diamond",
            "diamante",
            "picareta",
            "forja",
            "crafting",
            "craftar",
            "lava no minecraft",
            "sair da lava",
            "respawn",
            "morri no jogo",
            "perdeu os itens",
        ],
    },
    {
        "module": "14_conversas_games",
        "keywords": [
            "game",
            "pokemon",
            "mario",
            "zelda",
            "console",
            "gacha",
            "gaming",
            "jogar",
            "jogo",
            "ps5",
            "xbox",
            "switch",
            "steam",
            "fps",
        ],
    },
    {
        "module": "13_conversas_politica",
        "keywords": [
            "politica",
            "voto",
            "governo",
            "imposto",
            "partido",
            "eleicao",
            "presidente",
            "deputado",
            "lula",
            "bolsonaro",
        ],
    },
    {
        "module": "12_conversas_musica",
        "keywords": [
            "musica",
            "k-pop",
            "kpop",
            "bts",
            "blackpink",
            "ncs",
            "show",
            "playlist",
            "spotify",
            "cantar",
            "cantora",
            "cantor",
            "rock",
            "rap",
            "funk",
        ],
    },
    {
        "module": "11_conversas_comida",
        "keywords": [
            "comida",
            "cafe",
            "receita",
            "restaurante",
            "pao de queijo",
            "pizza",
            "comer",
            "fome",
            "almoco",
            "jantar",
            "lanche",
            "cozinhar",
        ],
    },
    {
        "module": "10_conversas_tecnologia",
        "keywords": [
            "rgb",
            "setup",
            "pc",
            "performance",
            "processador",
            "placa de video",
            "gpu",
            "cpu",
            "ram",
            "monitor",
            "teclado",
            "mouse",
            "computador",
        ],
    },
    {
        "module": "09_historias",
        "keywords": [
            "conta uma historia",
            "me conta uma",
            "historia",
            "era uma vez",
            "conte",
        ],
    },
    {
        "module": "08_curiosidades",
        "keywords": [
            "curiosidade",
            "sabia que",
            "fato interessante",
            "por que existe",
            "como funciona",
            "verdade que",
        ],
    },
    {
        "module": "07_topicos_especificos",
        "keywords": [
            "qual sua cor favorita",
            "qual seu livro favorito",
            "qual seu hobby",
            "o que tu curte",
            "qual seu interesse",
            "gosta de ler",
        ],
    },
    {
        "module": "05_reagindo",
        "keywords": [
            "olha isso",
            "veja isso",
            "olha aqui",
            "olha so",
            "manda ver",
            "que acha disso",
        ],
    },
    {
        "module": "04_respondendo_perguntas",
        "keywords": [
            "o que voce acha",
            "o que tu acha",
            "qual sua opiniao",
            "voce gosta",
            "tu gosta",
            "prefere",
            "o que voce prefere",
            "o que tu prefere",
            "qual seu favorito",
            "qual seu anime",
            "qual seu manga",
            "qual seu jogo",
            "qual seu filme",
            "qual sua serie",
            "qual sua musica",
            "qual sua comida",
            "qual seu livro",
        ],
    },
    {
        "module": "11_tools_system",
        "keywords": [
            "abre",
            "acessa",
            "vai em",
            "abre o google",
            "abre o chrome",
            "abre pasta",
            "mostra pasta",
            "vai na pasta",
            "cria arquivo",
            "novo arquivo",
            "salva como arquivo",
            "executa",
            "roda o comando",
            "executa o script",
            "roda script",
            "executa o python",
            "abre terminal",
            "novo terminal",
            "tira print",
            "captura tela",
            "printa",
            "status dos servicos",
            "status dos serviços",
            "servicos ativos",
            "serviços ativos",
            "status_servico",
            "ligar_servico",
            "desligar_servico",
            "liga o ollama",
            "desliga o ollama",
            "status_minecraft_bot",
            "ligar_minecraft_bot",
            "status do bot do minecraft",
            "bot do minecraft",
            "liga seu bot",
            "liga a celia no minecraft",
            "entra no minecraft",
            "sobe o minecraft",
            "inicia o minecraft",
        ],
    },
    {
        "module": "10_tools_vision",
        "keywords": [
            "ativa visao",
            "liga visao",
            "ta acontecendo",
            "que programa",
            "onde ta",
            "o que esta aberto",
            "que janela",
            "le isso",
            "que diz",
            "extrai texto",
            "o que esta escrito",
            "tem algum texto",
            "que botoes",
            "quais opcoes",
            "que menu",
            "o que tem aqui",
            "o que voce tem na tela",
            "o que tem na tela",
            "descreve",
            "como ta a tela",
            "qual cor",
            "o que voce ve",
            "analisa a tela",
            "analisa isso",
        ],
    },
    {
        "module": "08_tools_time",
        "keywords": [
            "que horas",
            "que dia",
            "qual a data",
            "data de hoje",
            "a data de hoje",
            "horario",
            "dia da semana",
            "que horas sao",
            "que dia e hoje",
            "que mes",
            "que ano",
            "qual o horario",
            "que horas ta",
            "quantas horas",
            "mes atual",
            "dia atual",
            "hora atual",
            "agora sao",
            "que semana",
            "qual a hora",
            "converte o horario",
            "que horas e em",
            "diferenca de horario",
        ],
    },
    {
        "module": "09_tools_ddg",
        "keywords": [
            "busca",
            "pesquisa",
            "procura",
            "encontra",
            "quem e ",
            "quem é",
            "presidente",
            "quanto custa",
            "preco",
            "preço",
            "o que aconteceu",
            "me fala sobre",
            "o que e",
            "quem foi",
            "quando foi",
            "noticia",
            "novidade",
            "ultima hora",
            "noticias sobre",
            "pesquise sobre",
        ],
    },
]

DEFAULT_MODULE_CONFIG = {
    "enabled": True,
    "enabled_modules": [],
    "always_include_modules": [],
    "default_module": "01_conversas_normais",
    "criador_module": "03_com_criador",
}

_MODULE_CONFIG_CACHE = {
    "mtime": None,
    "config": DEFAULT_MODULE_CONFIG.copy(),
}
_MODULE_CONFIG_LOCK = threading.Lock()


def _normalize_for_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _keyword_matches(keyword: str, message: str) -> bool:
    normalized_keyword = _normalize_for_match(keyword)
    if not normalized_keyword:
        return False
    if len(normalized_keyword) <= 3 and re.fullmatch(r"[a-z0-9_+-]+", normalized_keyword):
        return re.search(rf"(?<!\w){re.escape(normalized_keyword)}(?!\w)", message) is not None
    return normalized_keyword in message


def _normalize_module_config(raw: Optional[dict]) -> dict:
    cfg = DEFAULT_MODULE_CONFIG.copy()
    if not isinstance(raw, dict):
        return cfg

    cfg["enabled"] = bool(raw.get("enabled", cfg["enabled"]))

    enabled_modules = raw.get("enabled_modules", cfg["enabled_modules"])
    cfg["enabled_modules"] = (
        [m for m in enabled_modules if isinstance(m, str)]
        if isinstance(enabled_modules, list)
        else []
    )

    always_include_modules = raw.get(
        "always_include_modules",
        cfg["always_include_modules"],
    )
    cfg["always_include_modules"] = (
        [m for m in always_include_modules if isinstance(m, str)]
        if isinstance(always_include_modules, list)
        else []
    )

    default_module = raw.get("default_module", cfg["default_module"])
    if isinstance(default_module, str) and default_module:
        cfg["default_module"] = default_module

    criador_module = raw.get("criador_module", cfg["criador_module"])
    if isinstance(criador_module, str) and criador_module:
        cfg["criador_module"] = criador_module

    return cfg


def _read_module_config_from_conf(conf_path: str = "conf.yaml") -> dict:
    if not os.path.exists(conf_path):
        return DEFAULT_MODULE_CONFIG.copy()

    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except UnicodeDecodeError:
        with open(conf_path, "r", encoding="utf-8-sig") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to read module_config from {conf_path}: {e}")
        return DEFAULT_MODULE_CONFIG.copy()

    character_cfg = data.get("character_config", {})
    raw_module_cfg = (
        character_cfg.get("module_config", {})
        if isinstance(character_cfg, dict)
        else {}
    )
    return _normalize_module_config(raw_module_cfg)


def get_runtime_module_config(conf_path: str = "conf.yaml") -> dict:
    try:
        mtime = os.path.getmtime(conf_path)
    except OSError:
        return DEFAULT_MODULE_CONFIG.copy()

    with _MODULE_CONFIG_LOCK:
        if _MODULE_CONFIG_CACHE["mtime"] == mtime:
            return _MODULE_CONFIG_CACHE["config"].copy()

        cfg = _read_module_config_from_conf(conf_path)
        _MODULE_CONFIG_CACHE["mtime"] = mtime
        _MODULE_CONFIG_CACHE["config"] = cfg
        return cfg.copy()


def _resolve_enabled_modules(module_cfg: dict) -> set[str]:
    available = set(prompt_loader.list_modules())
    enabled_list = module_cfg.get("enabled_modules", [])

    if not enabled_list:
        return available

    enabled_set = {m for m in enabled_list if m in available}
    unknown = sorted(set(enabled_list) - available)
    if unknown:
        logger.warning(f"Unknown modules in config (ignored): {unknown}")
    return enabled_set


def select_modules(
    user_message: str,
    human_name: str = "",
    module_config: Optional[dict] = None,
    skip_tool_modules: bool = False,
) -> List[str]:
    """
    Analyze user message and return relevant modules.
    skip_tool_modules: quando True, ignora módulos de tool (ex: 11_tools_system)
    pois o MCP nativo já cuida disso — injetar os dois conflita.
    """
    # Prefixos de módulos de ferramenta que conflitam com MCP nativo
    TOOL_MODULE_PREFIXES = ("11_tools_system", "09_tools_ddg", "10_tools_vision", "08_tools_time", "12_tools_react", "15_tools_memoria", "16_tools_enquetes", "17_tools_vts", "18_tools_legendas")

    effective_cfg = (
        _normalize_module_config(module_config)
        if module_config is not None
        else get_runtime_module_config("conf.yaml")
    )

    if not effective_cfg.get("enabled", True):
        logger.debug("Module system disabled by config.")
        return []

    enabled_modules = _resolve_enabled_modules(effective_cfg)
    if not enabled_modules:
        logger.warning("No enabled modules available.")
        return []

    if not user_message:
        return []

    msg_lower = _normalize_for_match(user_message)
    selected: list[str] = []

    is_criador = human_name.lower() == "criador" if human_name else False

    for rule in MODULE_RULES:
        module_name = rule["module"]
        if module_name not in enabled_modules:
            continue

        # Se MCP nativo está ativo, pula módulos de tool para evitar conflito
        if skip_tool_modules and module_name.startswith(TOOL_MODULE_PREFIXES):
            continue

        for keyword in rule["keywords"]:
            if _keyword_matches(keyword, msg_lower):
                if module_name not in selected:
                    selected.append(module_name)
                    logger.info(
                        f"Module selected: {module_name} (keyword: '{keyword}')"
                    )
                break

        if selected:
            break

    criador_module = effective_cfg.get("criador_module", "03_com_criador")
    if is_criador and criador_module in enabled_modules and criador_module not in selected:
        selected.append(criador_module)
        logger.info("Criador module enabled")

    for module_name in effective_cfg.get("always_include_modules", []):
        if module_name in enabled_modules and module_name not in selected:
            selected.append(module_name)

    # Modos persistentes: .modos/<nome> → injeta módulos correspondentes
    # parents[1] = raiz do Célia (este arquivo está em prompts/)
    _MODOS_DIR = pathlib.Path(__file__).resolve().parents[1] / ".modos"
    _MODO_MODULE_MAP: dict = {
        "minecraft": ["26_minecraft_contexto", "13_tools_minecraft"],
        "react":     ["12_tools_react"],
    }
    if _MODOS_DIR.is_dir():
        for _flag in _MODOS_DIR.iterdir():
            for _m in _MODO_MODULE_MAP.get(_flag.name, []):
                if _m in enabled_modules and _m not in selected:
                    selected.append(_m)

    if not selected:
        default_module = effective_cfg.get("default_module", "01_conversas_normais")
        if default_module in enabled_modules:
            selected.append(default_module)
        elif enabled_modules:
            fallback = sorted(enabled_modules)[0]
            selected.append(fallback)
            logger.warning(
                f"Default module '{default_module}' is disabled. "
                f"Fallback '{fallback}' used."
            )
        logger.debug("Using default conversation module")

    return selected


def build_module_context(
    user_message: str,
    human_name: str = "",
    module_config: Optional[dict] = None,
    skip_tool_modules: bool = False,
) -> str:
    """
    Load selected module content and build prompt context block.
    skip_tool_modules: passa True quando MCP nativo está ativo.
    """
    modules = select_modules(
        user_message=user_message,
        human_name=human_name,
        module_config=module_config,
        skip_tool_modules=skip_tool_modules,
    )

    if not modules:
        return ""

    parts = ["\n\n--- CONTEXTO DO MODULO ATIVO ---"]

    for module_name in modules:
        content = prompt_loader.load_module(module_name)
        if content:
            parts.append(f"\n{content}")

    parts.append("\n--- FIM DO MODULO ---")

    result = "\n".join(parts)
    logger.info(f"Modules loaded: {modules} ({len(result)} chars)")
    return result
