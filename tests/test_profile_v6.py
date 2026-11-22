"""V6 profile editor and persistence: no server or world modifications."""
import json

import pytest
from pydantic import ValidationError

from celia.config_manager.profile_v6 import all_presets, preset, validated_settings
from celia.core.continuous_behavior import ContinuousBehaviorRuntime
from celia.core.profile_switch_intent import detect_profile_switch_intent


def test_five_independent_profile_presets_and_minecraft_ranges():
    profiles = all_presets()
    assert len(profiles) == 6  # + conversation_autonomous (always in force, see ALWAYS_EFFECTIVE)
    assert [profiles[p].gameplay.ideal_distance for p in ("chat","roteiro","gravacao","cohost")] == [6,8,12,16]
    assert profiles["autonomous"].gameplay.mode == "free"
    assert profiles["autonomous"].gameplay.spectator_camera
    profiles["cohost"].social.participation = 95
    assert profiles["chat"].social.participation != 95


def test_migrate_old_profile_selection_and_save_only_one_profile(tmp_path):
    path = tmp_path / "profile.json"
    path.write_text('{"version":1,"selected_profile":"cohost"}',encoding="utf-8")
    runtime = ContinuousBehaviorRuntime(path)
    old = runtime.profile_settings("cohost")
    settings = old["settings"]
    settings["social"]["participation"] = 83
    settings["gameplay"]["max_distance"] = 40
    result = runtime.update_profile_settings("cohost", settings, expected_revision=old["revision"])
    assert result["revision"] == 1
    persisted = json.loads(path.read_text())
    assert persisted["version"] == 6
    assert persisted["selected_profile"] == "cohost"
    assert set(persisted["profiles"]) == {"cohost", "conversation_autonomous"}  # the always-in-force profile is persisted too
    assert path.stat().st_mode & 0o077 == 0
    new = ContinuousBehaviorRuntime(path)
    assert new.profile_settings("cohost")["settings"]["social"]["participation"] == 83
    assert new.profile_settings("chat")["customized"] is False
    assert new.policy["threshold"] < 0.65


def test_revision_conflict_and_invalid_settings_leave_disk_unchanged(tmp_path):
    path = tmp_path / "profile.json"
    runtime = ContinuousBehaviorRuntime(path)
    initial = runtime.profile_settings("chat")["settings"]
    runtime.update_profile_settings("chat", initial, expected_revision=0)
    data = path.read_text()
    with pytest.raises(RuntimeError,match="revision"):
        runtime.update_profile_settings("chat", initial, expected_revision=0)
    bad = dict(initial)
    bad["gameplay"] = {**bad["gameplay"], "max_distance": 2}
    with pytest.raises(ValidationError):
        runtime.update_profile_settings("chat", bad)
    assert path.read_text() == data


def test_reset_profile_and_preserve_other_profiles(tmp_path):
    runtime = ContinuousBehaviorRuntime(tmp_path / "state.json")
    for profile in ("cohost","roteiro"):
        initial = runtime.profile_settings(profile)["settings"]
        initial["social"]["participation"] = 91
        runtime.update_profile_settings(profile, initial)
    runtime.reset_profile_settings("cohost")
    assert runtime.profile_settings("cohost")["settings"] == preset("cohost").model_dump()
    assert runtime.profile_settings("roteiro")["settings"]["social"]["participation"] == 91


def test_trigger_cooldown_and_no_tool_authority(tmp_path):
    runtime = ContinuousBehaviorRuntime(tmp_path / "state.json")
    assert runtime.profile_trigger_allowed("danger","damage-1")
    assert not runtime.profile_trigger_allowed("danger","damage-1")
    assert runtime.profile_trigger_allowed("danger","damage-2")
    assert not runtime.profile_trigger_allowed("made_up_event")
    assert runtime.get_capabilities()["tool_authorization"] == "existing_authorization_unchanged"


@pytest.mark.parametrize("text,expected", [
    ("Célia, modo autônoma.", "autonomous"),
    ("Célia, modo co-host!", "cohost"),
    ("Célia, modo gravação", "gravacao"),
    ("Célia, modo roteiro", "roteiro"),
    ("Célia, modo conversa", "chat"),
])
def test_direct_owner_vocative_profile_change(text,expected):
    intent = detect_profile_switch_intent(text)
    assert intent.should_switch and intent.target == expected


@pytest.mark.parametrize("text", [
    "modo autônoma", "a Célia falou de modo autônoma",
    "Célia, modo autônoma seria legal", "não ativa o modo autônoma",
])
def test_profile_mentions_do_not_switch(text):
    assert not detect_profile_switch_intent(text).should_switch
