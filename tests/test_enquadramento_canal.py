"""Enquadramento do video: o canal e da Celia, o criador e o pai co-host/guia. Sem rede."""
import importlib.util
import sys
from pathlib import Path

from celia.core import continuous_behavior
from celia.core.minecraft_series import build_brief, facts_for, verified_report_reply

BACKUP = Path(__file__).resolve().parent.parent / ".contract-backup" / "20261111-enquadramento" / "minecraft_series.py"


def test_prompt_conversation_autonomous_carries_the_video_framing():
    prompt = continuous_behavior._PROFILE_PROMPTS["conversation_autonomous"]
    assert "FORMATO DO VIDEO" in prompt
    assert "canal e SEU" in prompt
    assert "pai" in prompt
    assert '"pai"' in prompt
    assert "FORMATO DO VIDEO" in prompt
    assert "MINECRAFT BASICO E HUMOR" in prompt
    assert "toda fala dirigida a ele" in prompt
    assert "palpite" in prompt


def test_prepare_intro_gives_the_persona_nothing_to_say_the_opening_is_the_directors_fixed_line():
    for frozen in (True, False):
        assert facts_for("series.prepare_intro", {}, {"ok": True, "frozen": frozen}) == []


def test_world_select_keeps_the_original_persona_fact_in_both_branches():
    frozen = facts_for("world.select", {}, {"ok": True, "frozen": True})
    day = facts_for("world.select", {}, {"ok": True, "frozen": False})
    assert frozen == ["FATO: a introdução está pronta: o mundo ficou congelado e eu espero o criador dizer 'bora jogar'"]
    assert day == ["FATO: a introdução está pronta: é de dia e eu fico por perto do criador até ele dizer 'bora jogar'"]
    assert "cumprimento o público" not in build_brief(frozen) + build_brief(day)


def test_end_episode_brief_is_a_warm_goodbye_without_checkpoint_jargon():
    brief = build_brief(facts_for("series.end_episode", {}, {"ok": True, "episode": 6}))
    assert "AÇÃO:" in brief
    assert "checkpoint" not in brief
    assert "FATO: o episódio 6 foi encerrado e o mundo ficou salvo" in brief


def test_build_brief_explains_action_lines_and_allows_30_words():
    brief = build_brief(["FATO: qualquer coisa"])
    assert "linhas AÇÃO" in brief
    assert "30 palavras" in brief
    assert "não comece com 'Que legal' nem 'Que bom'" in brief


def test_inventory_reply_unchanged_by_the_intro_edit():
    spec = importlib.util.spec_from_file_location("celia.core._mc_series_backup", BACKUP)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    raw = {"ok": True, "inventory": {"cobblestone": 55, "coal": 2}}
    brief = build_brief(facts_for("companion.report", {"what": "inventory"}, raw))
    new_reply = verified_report_reply(brief)
    old_reply = old.verified_report_reply(old.build_brief(old.facts_for("companion.report", {"what": "inventory"}, raw)))
    assert new_reply == old_reply
    assert "55 pedregulho" in new_reply["text"] and "2 carvão" in new_reply["text"]


def test_series_brief_forbids_the_que_legal_opener():
    brief = build_brief(facts_for("world.select", {}, {"ok": True, "frozen": True}))
    assert "não comece com 'Que legal' nem 'Que bom'" in brief


def test_jev_narration_forbids_the_que_legal_opener(tmp_path):
    ROOT = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(ROOT / "integrations/minecraft"))
    from celia.core import jev_narration
    from celia_minecraft.worldmap import JevStore

    jev_narration._last_spoken = 0.0
    store = JevStore(tmp_path / "jev.sqlite")
    store.create_mission("iron_age-t1", "iron_age", "w")
    event = store.add_event("iron_age-t1", 1, "succeeded", {"inventory": {"iron_pickaxe": 1, "oak_log": 3}, "held": "iron_pickaxe"})
    result = jev_narration.build_narration(store, event, "iron_age-t1", 1, now=100.0)
    assert result["spoken"]
    assert "não comece com 'Que legal' nem 'Que bom'" in result["text"]
