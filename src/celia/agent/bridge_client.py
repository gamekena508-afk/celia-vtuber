"""
Bridge Client - Delega execução de tools para o servidor bridge (porta 12394)
==============================================================================
Detecta se a mensagem do usuário precisa de tools e chama o servidor de tools.
Retorna o resultado para ser injetado no contexto do LLM principal.
"""

import re
import httpx
from pathlib import Path
from typing import Optional, Tuple
from loguru import logger


# Diretório de modos persistentes (.modos/minecraft, .modos/react).
# Este arquivo está em src/celia/agent/ → parents[3] = raiz do Célia.
_MODOS_DIR = Path(__file__).resolve().parents[3] / ".modos"


# Triggers que indicam necessidade de ferramentas
TOOL_TRIGGERS = {
    "minecraft": [
        r"\bminecraft\b",
        r"\bmine\b",
        r"\bconstru(?:ir|a|cao|ção)\b",
        r"\bbuilds?\b",
        r"\bschematic\b",
        r"\besquem[aá]tic[oa]\b",
        r"\bblocos?\b",
        r"\bbot\b.*\b(?:jogo|servidor|lan)\b",
        # Ativar/encerrar sessão de jogo (liga o modo persistente)
        r"vamos jogar",
        r"entra no (?:jogo|minecraft|mundo)",
        r"modo (?:minecraft|jogo)",
        r"ativa o modo jogo",
        r"(?:fim de jogo|sai do minecraft|acabou o (?:jogo|minecraft)|para de jogar)",
        # Seguir / mover — a dor principal: ela precisa AGIR, não só falar
        r"\bme segue\b",
        r"\bsegue eu\b",
        r"\bme acompanha\b",
        r"vem (?:ca|cá|comigo)",
        r"vai junto",
        r"\bcola em mim\b",
        r"anda comigo",
        r"fica do meu lado",
        r"se mex[ae]",
        r"\bmexer\b",
    ],
    "search": [
        # Perguntas factuais / atualidades
        r"presidente",
        r"pesquis[ae]",
        r"busca[r]?",
        r"procura[r]?",
        r"not[ií]cia",
        r"atualidade",
        r"acontecendo",
        r"resultado (?:do|da|dos|das)",
        r"placar",
        r"preço (?:do|da|de)",
        r"cotação",
        r"dólar|bitcoin|crypto",
        r"eleição|eleicao",
        r"campeonato|copa|olimp[ií]ada",
        r"quem ganhou",
        r"lançamento",
        r"estreia|estreou",
        r"morreu|faleceu",
        r"guerra|conflito",
        r"temperatura|clima|previsão do tempo",
    ],
    "time": [
        r"que hora[s]?",
        r"hora atual",
        r"hor[aá]rio atual",
        r"que dia (?:é|e) hoje",
        r"data de hoje",
        r"agora são",
    ],
    "discord": [
        r"\bdiscord\b",
        r"@everyone",
    ],
    "system": [
        # Estado/runtime local da própria Célia é sistema, não pesquisa web.
        # "serviço" sem marcador explícito de site/internet também é sistema.
        r"\b(?:consulta|consultar|verifica|verificar|mostra|mostrar)\b.*\b(?:teu|seu) estado(?: atual)?\b",
        r"\bstatus(?: atual)? do servi[cç]o\b",
        # Comandos de sistema / automação
        r"abre[m]?",
        r"abra[m]?",
        r"abrir",
        r"abre",
        r"open",
        r"open\s+\w",  # "open de", "open o", "open a"
        r"fecha[m]?",
        r"fechar",
        r"fecha",
        r"close",
        r"executa[m]?",
        r"executar",
        r"executa",
        r"roda[r]?",
        r"rode",
        r"roda",
        r"run",
        r"corre[m]?",
        r"correr",
        r"cria[m]?",
        r"criar",
        r"cria",
        r"make",
        r"lista[m]?",
        r"listar",
        r"lista",
        r"list",
        r"mostre[m]?",
        r"mostrar",
        r"mostre",
        r"show",
        r"veja[m]?",
        r"ver",
        r"veja",
        r"see",
        r"olha[m]?",
        r"olhar",
        r"olha",
        r"look",
        r"tira[m]?",
        r"tirar",
        r"tira",
        r"screenshot",
        r"print",
        r"printar",
        r"captura[m]?",
        r"capturar",
        r"captura",
        r"terminal",
        r"cmd",
        r"prompt",
        r"bash",
        r"shell",
        r"pasta",
        r"folder",
        r"arquivo",
        r"file",
        r"documento",
        r"doc",
        r"browser",
        r"navegador",
        r"chrome",
        r"firefox",
        r"edge",
        r"youtube",
        r"google",
        r"site",
        r"página",
        r"pagina",
        r"page",
        r"python",
        r"py",
        r"script",
        r"programa",
        r"program",
        r"aplicativo",
        r"app",
        r"application",
        r"janela",
        r"window",
        r"desktop",
        r"área de trabalho",
        r"area de trabalho",
        r"explorador",
        r"explorer",
        r"arquivos",
        r"files",
        r"pastas",
        r"folders",
        r"diretório",
        r"diretorio",
        r"directory",
        r"dir",
        r"comando",
        r"command",
        r"digita[m]?",
        r"digitar",
        r"digita",
        r"type",
        r"escreve[m]?",
        r"escrever",
        r"escreve",
        r"write",
        r"salva[m]?",
        r"salvar",
        r"salva",
        r"save",
        r"deleta[m]?",
        r"deletar",
        r"deleta",
        r"delete",
        r"remove[m]?",
        r"remover",
        r"remove",
        r"move[m]?",
        r"mover",
        r"move",
        r"copi[a|ar]?",
        r"copy",
        r"cola[m]?",
        r"colar",
        r"cola",
        r"paste",
        r"clipboard",
        r"área de transferência",
        r"area de transferencia",
    ],
    "vision": [
        # Comandos de visão / tela
        r"tela",
        r"screen",
        r"monitor",
        r"vê[m]?",
        r"ver",
        r"enxerga[m]?",
        r"enxergar",
        r"analisa[m]?",
        r"analisar",
        r"descreve[m]?",
        r"descrever",
        r"o que tem",
        r"o que tá",
        r"\bo que est[aá]\b",
        r"o que há",
        r"o que tem na tela",
        r"o que aparece",
        r"botão",
        r"botao",
        r"menu",
        r"ícone",
        r"icone",
        r"imagem",
        r"foto",
        r"vídeo",
        r"video",
        r"lê[m]?",
        r"ler",
        r"texto na tela",
        r"ocr",
        r"reconhece[m]?",
        r"reconhecer",
        r"identifica[m]?",
        r"identificar",
        r"reconhece[m]?",
        r"reconhecer",
    ],
    "fal": [
        # Comandos de geração/edição de imagem com FAL
        r"desenha[m]?",
        r"desenhar",
        r"cria[m]?",
        r"criar",
        r"gera[m]?",
        r"gerar",
        r"\bfaz(?:e|ei)?\b",
        r"fazer",
        r"pinta[m]?",
        r"pintar",
        r"arte",
        r"arte digital",
        r"ilustra[m]?",
        r"ilustrar",
        r"renderiza[m]?",
        r"renderizar",
        r"visualiza[m]?",
        r"visualizar",
        r"edita[m]?",
        r"editar",
        r"modifica[m]?",
        r"modificar",
        r"altera[m]?",
        r"alterar",
        r"muda[m]?",
        r"mudar",
        r"ajusta[m]?",
        r"ajustar",
        r"melhora[m]?",
        r"melhorar",
        r"corrige[m]?",
        r"corrigir",
        r"remove[m]?",
        r"remover",
        r"adiciona[m]?",
        r"adicionar",
        r"imagem",
        r"foto",
        r"desenho",
        r"quadro",
        r"pintura",
        r"illustração",
        r"ilustracao",
    ],
}


def needs_tool(text: str) -> Optional[str]:
    """
    Detecta se o texto do usuário precisa de uma ferramenta.

    Tipos detectados:
    - search: Perguntas factuais, pesquisas, atualidades
    - time: Horas, data, tempo
    - system: Comandos de sistema (abrir, executar, criar, etc.)
    - vision: Análise de tela, OCR, descrição visual
    - fal: Geração/edição de imagens
    - discord: ações no Discord da Célia

    Returns:
        Tipo da tool ("search", "time", "system", "vision", "fal") ou None.
    """
    text_lower = text.lower().strip()

    # Gesto Meta+N ("Célia, olha isso"): nunca habilita tool. O texto do gesto e o
    # contexto visual contêm palavras ("tela", "agora") que os gatilhos abaixo leriam
    # como pedido de visão ao vivo, e isso gastaria GPU sem o dono ter pedido.
    try:
        from ..core.direct_address import is_look_gesture

        if is_look_gesture(text):
            return None
    except Exception:
        pass

    # MODO VIVA: se o modo minecraft persistente está ativo (.modos/minecraft existe,
    # criado pelo alert_watcher ao entrar no mundo ou por minecraft_ativar_modo),
    # habilita as tools em TODOS os turnos — ela pode agir a qualquer comando,
    # sem depender de keyword. Sai do modo com "fim de jogo" ou fim_jogo.bat.
    try:
        if (_MODOS_DIR / "minecraft").exists():
            logger.info("🔗 Bridge: modo minecraft ativo → tools habilitadas neste turno")
            return "minecraft"
    except Exception:
        pass

    for tool_type, patterns in TOOL_TRIGGERS.items():
        for pattern in patterns:
            if re.search(pattern, text_lower):
                logger.info(f"🔗 Bridge: Detectou necessidade de tool '{tool_type}' no texto: '{text_lower[:60]}...'")
                return tool_type

    return None


async def call_bridge(
    text: str,
    bridge_url: str = "http://localhost:12394/bridge/process",
    timeout: int = 30,
) -> Tuple[bool, str, str]:
    """
    Chama o servidor de tools (bridge) para executar ferramentas.
    
    Args:
        text: Texto do usuário
        bridge_url: URL do endpoint bridge/process
        timeout: Timeout em segundos
        
    Returns:
        Tuple de (sucesso, resultado_formatado, tool_name)
        - sucesso: True se a bridge executou com sucesso
        - resultado_formatado: Texto para injetar no contexto do LLM
        - tool_name: Nome da tool que foi usada
    """
    try:
        logger.info(f"🔗 Bridge: Chamando {bridge_url} com texto: '{text[:80]}...'")
        
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                bridge_url,
                json={"text": text},
            )
            
            if response.status_code != 200:
                logger.error(f"🔗 Bridge: HTTP {response.status_code}")
                return False, "", ""
            
            data = response.json()
            
            bridge_response = data.get("response", "")
            tool_used = data.get("tool_used", False)
            tools = data.get("tools", [])
            error = data.get("error")
            elapsed_ms = data.get("elapsed_ms", 0)
            
            if error:
                logger.error(f"🔗 Bridge: Erro retornado: {error}")
                return False, "", ""
            
            if not bridge_response:
                logger.warning("🔗 Bridge: Resposta vazia")
                return False, "", ""
            
            if str(bridge_response).strip() == "__API_NOT_SUPPORT_TOOLS__":
                logger.warning(
                    "Bridge: token interno __API_NOT_SUPPORT_TOOLS__ recebido; ignorando resultado."
                )
                return False, "", ""

            # Formata o resultado para injetar no contexto
            tool_name = ""
            if tools:
                tool_names = [t.get("name", "tool") for t in tools]
                tool_name = ", ".join(tool_names)
            
            # Detecta tipo de resultado
            tool_type = needs_tool(text)
            if tool_type == "time":
                formatted = f"[HORA ATUAL: {bridge_response}]"
            elif tool_type == "search":
                formatted = f"[RESULTADO DA PESQUISA: {bridge_response}]"
            elif tool_type == "system":
                formatted = f"[AÇÃO DE SISTEMA: {bridge_response}]"
            elif tool_type == "vision":
                formatted = f"[ANÁLISE DE TELA: {bridge_response}]"
            elif tool_type == "fal":
                formatted = f"[IMAGEM: {bridge_response}]"
            else:
                formatted = f"[RESULTADO DE FERRAMENTA: {bridge_response}]"
            
            logger.info(f"🔗 Bridge: Sucesso em {elapsed_ms}ms | tool_used={tool_used} | tools={tool_name}")
            logger.info(f"🔗 Bridge: Resultado: '{formatted[:120]}...'")
            
            return True, formatted, tool_name
            
    except httpx.TimeoutException:
        logger.error(f"🔗 Bridge: Timeout após {timeout}s")
        return False, "", ""
    except httpx.ConnectError:
        logger.error(f"🔗 Bridge: Não conseguiu conectar em {bridge_url}. Servidor de tools está rodando?")
        return False, "", ""
    except Exception as e:
        logger.error(f"🔗 Bridge: Erro inesperado: {type(e).__name__}: {e}")
        return False, "", ""


async def check_bridge_health(
    bridge_url: str = "http://localhost:12394/bridge/process",
) -> bool:
    """Verifica se o servidor de tools está disponível."""
    health_url = bridge_url.replace("/bridge/process", "/bridge/health")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get(health_url)
            if response.status_code == 200:
                data = response.json()
                ok = data.get("status") == "ok" and data.get("agent_loaded", False)
                logger.info(f"🔗 Bridge health: {'OK' if ok else 'NOT READY'}")
                return ok
    except Exception as e:
        logger.warning(f"🔗 Bridge health check falhou: {e}")
    return False
