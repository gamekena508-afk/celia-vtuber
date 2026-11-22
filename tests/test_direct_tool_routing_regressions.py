from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.core.ingress_security import new_context
from celia.core.tool_authorization import authorize_tool, bind_direct_browser_command


def _agent():
    return BasicMemoryAgent.__new__(BasicMemoryAgent)


def _owner():
    return new_context("local_owner", external=False, authenticated=True, trusted_user_id="Criador", authorization_scope=("owner.full",))


def test_direct_google_command_gets_narrow_browser_grant():
    ctx = bind_direct_browser_command(_owner(), "Célia, abre o Google.")
    decision = authorize_tool("open_browser", {"url": "https://www.google.com", "navegador": "padrao"}, ctx)
    assert decision.allowed
    assert decision.reason == "owner_direct_command"


def test_retrospective_google_mention_is_not_a_command_or_grant():
    text = "Um novo erro aqui, quando eu mandei ela abrir o Google."
    assert _agent()._forced_system_tool_call(text) is None
    ctx = bind_direct_browser_command(_owner(), text)
    assert not authorize_tool("open_browser", {"url": "https://www.google.com", "navegador": "padrao"}, ctx).allowed


def test_board_games_use_their_own_direct_tool_not_minecraft():
    agent = _agent()
    assert agent._forced_system_tool_call("Célia, abre o jogo da velha.")["name"] == "abrir_jogo"
    assert agent._forced_system_tool_call("Célia, abre o da velha.")["input"]["jogo"] == "jogo da velha"
    assert agent._forced_system_tool_call("abre a dama")["input"]["jogo"] == "dama"
    assert agent._forced_system_tool_call("Célia, liga o jogo da dama.")["input"]["jogo"] == "dama"
    assert agent._forced_system_tool_call("Célia, eu quero que você liga o jogo da Dama.")["input"]["jogo"] == "dama"
    assert agent._forced_system_tool_call("Célia, eu quero que você ligue o jogo da Dama.")["input"]["jogo"] == "dama"
    assert agent._forced_system_tool_call("quero abrir o xadrez")["input"]["jogo"] == "xadrez"
    assert agent._forced_system_tool_call("bota o xadrez")["input"]["jogo"] == "xadrez"
    assert agent._forced_system_tool_call("Série, tira o jogo da tela.")["name"] == "fechar_jogo"
    assert agent._forced_system_tool_call("Célia, fecha o xadrez.")["name"] == "fechar_jogo"
    assert agent._forced_system_tool_call("quando eu mandei ela ligar o jogo da dama, deu erro") is None


def test_game_tool_filter_keeps_game_tools_and_drops_minecraft_noise():
    agent = _agent()
    tools = [
        {"type": "function", "function": {"name": "abrir_jogo"}},
        {"type": "function", "function": {"name": "listar_jogos"}},
        {"type": "function", "function": {"name": "fechar_jogo"}},
        {"type": "function", "function": {"name": "minecraft_acao"}},
        {"type": "function", "function": {"name": "open_browser"}},
        {"type": "function", "function": {"name": "get_current_time"}},
    ]
    filtered = agent._filter_tools_for_turn(tools, "Célia, abre o jogo da velha.", origin="owner")
    names = {agent._tool_name(tool) for tool in filtered}
    assert {"abrir_jogo", "fechar_jogo", "listar_jogos", "get_current_time"} <= names
    assert "minecraft_acao" not in names


def test_fixed_local_game_launch_is_low_risk_for_owner_but_not_public():
    owner = _owner()
    assert authorize_tool("abrir_jogo", {"jogo": "jogo da velha"}, owner).allowed
    assert authorize_tool("fechar_jogo", {}, owner).allowed
    public = new_context("youtube_chat", external=True, authenticated=True, authorization_scope=("poll.write",))
    assert not authorize_tool("abrir_jogo", {"jogo": "jogo da velha"}, public).allowed
def test_read_only_status_queries_are_deterministic():
    agent = _agent()
    cases = {
        "Célia, status do Discord.": "discord_status_bot",
        "Célia, status dos geradores de imagem.": "status_geradores_imagem",
        "Célia, qual o resultado da enquete?": "resultado_enquete",
        "Célia, quais os jogos disponíveis?": "listar_jogos",
        "Célia, tira um print da tela.": "take_screenshot",
    }
    for text, expected in cases.items():
        call = agent._forced_system_tool_call(text)
        assert call and call["name"] == expected


def test_public_can_read_poll_result_but_not_other_private_reads():
    public = new_context("youtube_chat", external=True, authenticated=True, authorization_scope=("poll.write",))
    assert authorize_tool("resultado_enquete", {}, public).allowed
    assert not authorize_tool("status_servico", {}, public).allowed
    assert not authorize_tool("take_screenshot", {}, public).allowed
def test_fast_owner_reads_route_without_tool_llm():
    agent = _agent()
    assert agent._forced_system_tool_call("Célia, lê a memória teste_mcp.") == {
        "id": "forced_read_memory_0", "name": "ler_memoria",
        "input": {"chave": "teste_mcp"}, "_direct_tool": True,
    }
    assert agent._forced_system_tool_call("Célia, pesquisa OpenAI no DuckDuckGo.")["name"] == "search"
    tz = agent._forced_system_tool_call("Célia, converte a hora de São Paulo pra Londres.")
    assert tz["name"] == "convert_timezone"
    assert tz["input"] == {"source_timezone": "America/Sao_Paulo", "target_timezone": "Europe/London"}


def test_search_signature_treats_default_region_variants_as_duplicate():
    agent = _agent()
    first = {"name": "search", "input": {"query": "OpenAI", "max_results": "5", "region": "wt-wt"}}
    second = {"name": "search", "input": {"query": "OpenAI", "max_results": 5, "region": ""}}
    assert agent._tool_signature(first) == agent._tool_signature(second)


def test_real_youtube_command_with_discourse_suffix_gets_grant():
    text = "Abre o Youtube então."
    call = _agent()._forced_system_tool_call(text)
    ctx = bind_direct_browser_command(_owner(), text)
    assert call == {
        "id": "forced_open_browser_0",
        "name": "open_browser",
        "input": {"url": "https://www.youtube.com", "navegador": "padrao"},
        "_direct_tool": True,
    }
    assert authorize_tool(call["name"], call["input"], ctx).allowed
