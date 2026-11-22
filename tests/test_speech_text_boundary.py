import pytest
import asyncio
import wave
from types import SimpleNamespace

from celia.bridge_routes import _generate_bridge_audio, _speech_text_error
from celia import bridge_routes
from celia.utils.sentence_divider import SentenceDivider


def test_speech_boundary_accepts_clean_human_facing_text():
    assert _speech_text_error("Essa peca ai ficou bem exposta.") is None


def test_speech_boundary_rejects_empty_text():
    assert _speech_text_error("   ") == "Texto de fala vazio ou inválido."


def test_speech_boundary_rejects_structured_json_object():
    payload = '{"action":"e2e4","emotion":"confident","commentary":"Heh."}'

    assert _speech_text_error(payload) == (
        "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
    )


def test_speech_boundary_rejects_structured_json_list():
    assert _speech_text_error('["action", "e2e4"]') == (
        "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
    )


def test_speech_boundary_rejects_python_dict_representation():
    assert _speech_text_error("{'action': 'e2e4', 'emotion': 'confident'}") == (
        "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
    )


def test_speech_boundary_rejects_malformed_machine_shaped_json():
    assert _speech_text_error('{"action":"e2e4","emotion":') == (
        "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."
    )


def test_speak_endpoint_rejects_structure_before_it_consumes_cooldown(monkeypatch):
    router = bridge_routes.init_bridge_routes(None)
    endpoint = next(
        route.endpoint for route in router.routes if route.path == "/bridge/speak"
    )

    def cooldown_must_not_run(_priority):
        raise AssertionError("structural speech must be rejected before cooldown")

    monkeypatch.setattr(bridge_routes, "_speak_allowed", cooldown_must_not_run)
    response = asyncio.run(
        endpoint(bridge_routes.SpeakRequest(text='{"action":"e2e4"}'))
    )

    assert response.ok is False
    assert response.error == "Texto estrutural não pode ser sintetizado. Envie somente fala limpa."


def test_bridge_speak_tts_receives_semantic_emotion():
    calls = []

    class Engine:
        async def async_generate_audio(self, **kwargs):
            calls.append(kwargs)
            return "/tmp/celia.wav"

    result = asyncio.run(
        _generate_bridge_audio(Engine(), "Pai, calma.", "speak-test", "nervous")
    )

    assert result == "/tmp/celia.wav"
    expected = {
        "text": "Pai, calma.",
        "file_name_no_ext": "speak-test",
        "emotion": "nervous",
    }
    if bridge_routes.continuous_behavior.profile == "roteiro":
        expected["fast"] = True
    assert calls == [expected]


def test_bridge_speak_legacy_tts_fallback_does_not_hide_other_type_errors():
    calls = []

    class LegacyEngine:
        async def async_generate_audio(self, text, file_name_no_ext):
            calls.append((text, file_name_no_ext))
            return "/tmp/celia.wav"

    result = asyncio.run(
        _generate_bridge_audio(LegacyEngine(), "Oi.", "speak-test", "happy")
    )

    assert result == "/tmp/celia.wav"
    assert calls == [("Oi.", "speak-test")]


def test_bridge_speak_keeps_nervous_for_lived2d_while_tts_receives_it(tmp_path, monkeypatch):
    wav_path = tmp_path / "generated.wav"
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 240)

    tts_calls = []

    class Engine:
        async def async_generate_audio(self, **kwargs):
            tts_calls.append(kwargs)
            return str(wav_path)

    broadcasts = []
    published = []

    class Singleton:
        context = SimpleNamespace(tts_engine=Engine(), agent_engine=None, history_uid=None)
        active_connections = 0
        _connections = []

        async def broadcast(self, payload):
            broadcasts.append(payload)

        def set_playback_status(self, _speech_id, _status):
            return None

    monkeypatch.setattr(bridge_routes.CeliaSingleton, "get_instance", lambda: Singleton())
    monkeypatch.setattr(bridge_routes, "_speak_allowed", lambda _priority: (True, 0.0))
    monkeypatch.setattr(
        bridge_routes,
        "prepare_audio_payload",
        lambda **kwargs: {"volumes": [], "slice_length": 20},
    )
    monkeypatch.setattr(bridge_routes, "_play_audio_to_device", lambda *_args: None)
    monkeypatch.setattr(bridge_routes.obs_subtitles, "start_subtitle", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bridge_routes,
        "publish_emotion",
        lambda emotion, **kwargs: published.append((emotion, kwargs)),
    )

    endpoint = next(
        route.endpoint
        for route in bridge_routes.init_bridge_routes(None).routes
        if route.path == "/bridge/speak"
    )
    response = asyncio.run(
        endpoint(bridge_routes.SpeakRequest(text="Pai, eu estou nervosa.", emotion="nervous"))
    )

    assert response.ok is True
    assert tts_calls[0]["emotion"] == "nervous"
    assert published == [(
        "nervous",
        {
            "source": "bridge_speak",
            "priority": 50,
            "ttl_sec": None,
            "persistent": True,
        },
    )]
    assert len(broadcasts) == 1


def test_celia_sentence_mode_does_not_synthesize_first_comma_fragment():
    """O que este teste protege: nunca sintetizar o fragmento "Criador,".

    A asserção era por lista exata de pedaços. Em 2026-10-01 o divisor passou a
    agrupar pedaços abaixo de `min_tts_chars`, porque uma resposta de 61
    caracteres virava duas chamadas ao OmniVoice (3 s + 2 s de custo fixo).
    Com o agrupamento ligado as duas frases saem juntas — o que NÃO viola esta
    regra, só muda a contagem.

    Então agora o teste separa as duas coisas: o fatiamento por frase é medido
    com o agrupamento desligado, e a regra do fragmento é medida nos dois modos.
    """
    async def source():
        for chunk in ["Criador, ", "a culpa é sua que ", "me programou assim. ", "Agora vai."]:
            yield chunk

    async def collect(min_tts_chars):
        divider = SentenceDivider(
            faster_first_response=False,
            segment_method="pysbd",
            min_tts_chars=min_tts_chars,
        )
        return [item.text async for item in divider.process_stream(source())]

    # Comportamento de fatiamento por frase, isolado do agrupamento.
    assert asyncio.run(collect(0)) == [
        "Criador, a culpa é sua que me programou assim.",
        "Agora vai.",
    ]

    # A regra de verdade, válida com e sem agrupamento: o fragmento da primeira
    # vírgula nunca pode virar uma síntese sozinha.
    for min_tts_chars in (0, 60):
        pedacos = asyncio.run(collect(min_tts_chars))
        assert "Criador," not in pedacos, (
            f"fragmento de vírgula sintetizado sozinho com min_tts_chars={min_tts_chars}"
        )
        assert all(len(p.strip()) > len("Criador,") for p in pedacos)
        # Nada de texto perdido ou duplicado em nenhum dos modos.
        assert " ".join(pedacos) == "Criador, a culpa é sua que me programou assim. Agora vai."


def test_control_tags_are_removed_from_human_facing_display_after_emotion_extraction():
    from celia.agent.output_types import Actions
    from celia.agent.transformers import display_processor
    from celia.utils.sentence_divider import SentenceWithTags, TagInfo, TagState

    async def source():
        yield (
            SentenceWithTags(
                text="[happy] Oi Pai. <emotion> [angry]",
                tags=[TagInfo("", TagState.NONE)],
            ),
            Actions(),
        )

    @display_processor()
    async def wrapped():
        async for item in source():
            yield item

    async def collect():
        return [item async for item in wrapped()]

    items = asyncio.run(collect())
    _sentence, display, _actions = items[0]
    assert display.text == "Oi Pai."


def test_bare_emotion_control_tag_at_display_edge_is_not_exposed():
    from celia.agent.output_types import Actions
    from celia.agent.transformers import display_processor
    from celia.utils.sentence_divider import SentenceWithTags, TagInfo, TagState

    async def source():
        yield (SentenceWithTags(text="happy Oi Pai.", tags=[TagInfo("", TagState.NONE)]), Actions())

    @display_processor()
    async def wrapped():
        async for item in source():
            yield item

    async def collect():
        return [item async for item in wrapped()]

    items = asyncio.run(collect())
    assert items[0][1].text == "Oi Pai."


@pytest.mark.parametrize("text,expected", [("[happy] Finalmente.", "Finalmente."), ("[angry] Oi.", "Oi."), ("[calm] Uma frase. [excited] Segunda frase.", "Uma frase. Segunda frase.")])
def test_fixed_voice_boundary_preserves_all_sentences(text, expected):
    from celia.utils.tts_preprocessor import sanitize_production_tts_text
    assert sanitize_production_tts_text(text) == expected


def test_bridge_fixed_voice_is_clean_and_never_retries_type_errors():
    calls = []
    class Engine:
        fixed_voice_profile = True
        async def async_generate_audio(self, **kwargs):
            calls.append(kwargs)
            raise TypeError("internal synthesis error")
    with pytest.raises(TypeError, match="internal synthesis error"):
        asyncio.run(_generate_bridge_audio(Engine(), "[happy] Finalmente.", "test", "happy"))
    assert calls == [{"text":"Finalmente.", "file_name_no_ext":"test"}]


@pytest.mark.parametrize("text,expected", [
    ("100? Tu tá tentando me comprar um pastel ou me convencer?", "Cem? Tu tá tentando me comprar um pastel ou me convencer?"),
    ("Tenho 3 jogos.", "Tenho três jogos."),
    ("Ficou 2 a 1.", "Ficou dois a um."),
    ("Estamos em 2026.", "Estamos em dois mil e vinte e seis."),
    ("3.14 é um decimal.", "Três vírgula um quatro é um decimal."),
    ("No dia 23.", "No dia vinte e três."),
    ("R$ 50.", "Cinquenta reais."),
    ("R$ 1.234,56.", "Mil, duzentos e trinta e quatro reais e cinquenta e seis centavos."),
    ("R$ 0,01.", "Zero reais e um centavo."),
    ("R$ 2,5.", "Dois reais e cinquenta centavos."),
    ("R$ 50.25.", "Cinquenta reais e vinte e cinco centavos."),
    ("50% pronto.", "Cinquenta por cento pronto."),
])
def test_production_tts_normalizes_spoken_numbers_anywhere(text, expected):
    from celia.utils.tts_preprocessor import sanitize_production_tts_text
    assert sanitize_production_tts_text(text) == expected
