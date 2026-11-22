"""Revisao de concorrencia do editor de perfil e POR PERFIL.

Bug medido no servidor vivo (2026-10-20): a revisao era UMA so para todos os perfis. Um editor aberto em 'Autonoma' ficava com
revisao velha quando outro perfil era salvo, e o Salvar dava HTTP 409 'profile_revision_conflict' (mostrado como 'Falha ao salvar').
Controle positivo: o mesmo perfil com revisao velha TEM de dar conflito. Controle negativo: outro perfil NAO pode invalidar o editor.
"""
import json

import pytest

from celia.core.continuous_behavior import ContinuousBehaviorRuntime


@pytest.fixture
def runtime(tmp_path):
    return ContinuousBehaviorRuntime(tmp_path / "profile.json")


def _save(runtime, profile, expected):
    current = runtime.profile_settings(profile)
    settings = current["settings"]
    settings["social"]["silence_seconds"] = 4 if settings["social"]["silence_seconds"] != 4 else 5
    return runtime.update_profile_settings(profile, settings, expected_revision=expected)


def test_saving_another_profile_does_not_invalidate_an_editor_open_on_this_one(runtime):
    autonomous = runtime.profile_settings("autonomous")["revision"]
    cohost = runtime.profile_settings("cohost")["revision"]
    _save(runtime, "cohost", cohost)  # another editor saved in between
    result = _save(runtime, "autonomous", autonomous)  # this editor still holds its own, unchanged revision
    assert result["revision"] == autonomous + 1


def test_the_same_profile_with_a_stale_revision_still_conflicts(runtime):
    stale = runtime.profile_settings("autonomous")["revision"]
    _save(runtime, "autonomous", stale)
    with pytest.raises(RuntimeError, match="profile_revision_conflict"):
        _save(runtime, "autonomous", stale)


def test_reset_bumps_only_that_profile(runtime):
    before = runtime.profile_settings("chat")["revision"]
    runtime.reset_profile_settings("cohost")
    assert runtime.profile_settings("chat")["revision"] == before


def test_revisions_survive_a_restart_and_a_legacy_file_still_loads(tmp_path):
    path = tmp_path / "profile.json"
    first = ContinuousBehaviorRuntime(path)
    _save(first, "autonomous", first.profile_settings("autonomous")["revision"])
    second = ContinuousBehaviorRuntime(path)
    assert second.profile_settings("autonomous")["revision"] == 1
    assert second.profile_settings("chat")["revision"] == 0
    # a file written before the per-profile revision (only the global "revision") must still load without error
    legacy = json.loads(path.read_text(encoding="utf-8"))
    legacy.pop("profile_revisions", None)
    legacy["revision"] = 7
    path.write_text(json.dumps(legacy), encoding="utf-8")
    third = ContinuousBehaviorRuntime(path)
    assert third.profile_settings("chat")["revision"] == 7
