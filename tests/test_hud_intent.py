"""One HUD, one visibility state: every noun is an alias of the WHOLE HUD; only explicit imperatives act."""
import pytest

from celia.core.hud_intent import detect_hud_intent

SHOW = [
    "Célia, ligar inventário", "Célia, ligar mapa", "Célia, ligar bússola", "Célia, ligar HUD",
    "Célia, ligar interface Minecraft", "Célia, liga o mapa", "Célia, liga a bússola", "Célia, mostra o inventário",
    "Célia, mostra a interface do Minecraft", "Célia, exibe o HUD", "celia liga o hud", "Selia, liga o mapa",
    "Célia, ative a bússola", "Célia, mostra o mapa e a bússola", "Célia, liga o mapa do Minecraft por favor",
    "Célia, pode ligar o inventário agora", "liga o mapa", "ligar HUD", "Célia, ligar a busola", "Célia, liga o minimapa",
]
HIDE = [
    "Célia, desligar inventário", "Célia, desligar mapa", "Célia, desligar bússola", "Célia, desligar HUD",
    "Célia, desligar interface Minecraft", "Célia, desliga o mapa", "Célia, esconde o inventário", "Célia, oculta a bússola",
    "Célia, oculta o HUD.", "Célia, fecha a interface do Minecraft", "celia desliga o hud", "Célia, esconda o mapa",
    "Célia, some com o mapa", "Célia, pode desligar a bússola", "desliga o inventário", "Célia, desliga o inventário e o mapa",
]
NOT_COMMANDS = [
    "me mostra um mapa", "o mapa está bonito hoje", "não liga o mapa", "nunca desliga a bússola", "o inventário está cheio",
    "como funciona o mapa do Minecraft", "Célia, quero um mapa novo da cidade", "esse HUD é legal",
    "liga a luz da sala", "desliga o computador", "Célia, mostra o seu vídeo", "o mapa da base está em outro lugar",
    "mapa", "inventário", "", "   ",
]


@pytest.mark.parametrize("text", SHOW)
def test_show_commands_show_the_whole_hud(text):
    intent = detect_hud_intent(text)
    assert intent.action == "show" and intent.enabled is True and intent.should_switch, text


@pytest.mark.parametrize("text", HIDE)
def test_hide_commands_hide_the_whole_hud(text):
    intent = detect_hud_intent(text)
    assert intent.action == "hide" and intent.enabled is False and intent.should_switch, text


@pytest.mark.parametrize("text", NOT_COMMANDS)
def test_mentions_and_generic_requests_do_not_act(text):
    intent = detect_hud_intent(text)
    assert not intent.should_switch and intent.enabled is None, text


def test_every_alias_produces_the_same_action_there_is_no_part_level_intent():
    """Positive and negative control for the central rule: the noun never changes WHAT is controlled, only the verb does."""
    nouns = ["inventário", "mapa", "bússola", "HUD", "interface Minecraft"]
    for verb, expected in (("ligar", "show"), ("desligar", "hide")):
        results = {noun: detect_hud_intent(f"Célia, {verb} {noun}") for noun in nouns}
        assert {r.action for r in results.values()} == {expected}
        assert all(not hasattr(r, "target") and not hasattr(r, "component") for r in results.values())


def test_hud_voice_never_touches_minecraft_profile_camera_or_permissions():
    """The intent module is pure text matching: it imports nothing that could act on the game, the profile or the permissions."""
    import ast

    import celia.core.hud_intent as module

    tree = ast.parse(open(module.__file__, encoding="utf-8").read())
    imported = {n.module if isinstance(n, ast.ImportFrom) else a.name for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom)) for a in (getattr(n, "names", None) or [None])}
    assert imported <= {"__future__", "re", "unicodedata", "dataclasses"}, imported
