import pytest

from celia.core.profile_switch_intent import detect_profile_switch_intent


@pytest.mark.parametrize(
    ("text", "target"),
    [
        ("ativa o co-host", "cohost"),
        ("entra no modo co-host", "cohost"),
        ("muda para roteiro", "roteiro"),
        ("entra no modo gravação", "gravacao"),
        ("volta para conversa", "chat"),
        ("quero usar o roteiro agora", "roteiro"),
        ("quero que tu entre no modo co-host", "cohost"),
        ("coloca a Célia no modo roteiro", "roteiro"),
        ("entra no modo autônoma", "autonomous"),
    ],
)
def test_explicit_profile_switches_execute(text, target):
    intent = detect_profile_switch_intent(text)
    assert intent.should_switch is True
    assert intent.target == target
    assert intent.reason == "explicit_switch"

@pytest.mark.parametrize(
    "text",
    [
        "co-host",
        "roteiro",
        "modo gravação",
        "modo autônoma",
        "o co-host ficou estranho ontem",
        "no roteiro tem esse trecho",
        "eu estava testando o modo conversa",
        "quando eu falar co-host, não quero que troque automaticamente",
        "o roteiro precisa melhorar",
    ],
)
def test_profile_mentions_never_execute(text):
    intent = detect_profile_switch_intent(text)
    assert intent.should_switch is False


@pytest.mark.parametrize(
    "text",
    [
        "não ativa o co-host",
        "não quero usar o roteiro",
        "não quero que tu entre no modo co-host",
        "nunca troca para roteiro",
    ],
)
def test_negated_switches_never_execute(text):
    intent = detect_profile_switch_intent(text)
    assert intent.should_switch is False
    assert intent.reason == "negated_switch"

def test_unrelated_conversation_has_no_profile_intent():
    intent = detect_profile_switch_intent("essa conversa está longa, mas continua")
    assert intent.should_switch is False
    assert intent.action == "none"
    assert intent.target is None


def test_profile_word_after_meta_instruction_does_not_switch():
    intent = detect_profile_switch_intent(
        "Quando eu disser roteiro, só estou falando sobre o nome do modo."
    )
    assert intent.should_switch is False
    assert intent.target == "roteiro"

def test_explicit_change_of_mode_phrase_is_supported():
    intent = detect_profile_switch_intent("troca de modo pro co-host")
    assert intent.should_switch is True
    assert intent.target == "cohost"


def test_apply_switch_uses_canonical_runtime_and_does_not_rebroadcast_noop(tmp_path, monkeypatch):
    import asyncio
    import importlib

    behavior_module = importlib.import_module("celia.core.continuous_behavior")
    runtime = behavior_module.ContinuousBehaviorRuntime(tmp_path / "profile.json")
    monkeypatch.setattr(behavior_module, "continuous_behavior", runtime)

    class FakeSingleton:
        def __init__(self):
            self.events = []

        async def broadcast(self, payload):
            self.events.append(payload)

    from celia.core.profile_switch_intent import apply_profile_switch

    singleton = FakeSingleton()
    first = asyncio.run(apply_profile_switch("cohost", singleton=singleton))
    assert first["profile"] == "cohost"
    assert [event["type"] for event in singleton.events] == ["profile.changed"]

    singleton.events.clear()
    second = asyncio.run(apply_profile_switch("cohost", singleton=singleton))
    assert second["profile"] == "cohost"
    assert singleton.events == []
