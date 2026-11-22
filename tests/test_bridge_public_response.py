from celia.bridge_routes import _bridge_approved_event, _public_bridge_response


def test_provider_failure_never_becomes_celia_dialogue():
    response = _public_bridge_response("Error calling the chat endpoint: Rate limit exceeded")
    assert "rate limit" not in response.lower()
    assert response == ""


def test_translated_connection_error_is_not_public_dialogue():
    assert _public_bridge_response("Minha conexão de conversa está ocupada agora. Tente de novo daqui a pouco.") == ""
    assert _public_bridge_response("[happy] Minha conexão de conversa está ocupada agora.") == ""
    assert _public_bridge_response("Olá, pessoal! Vamos conversar.") == "Olá, pessoal! Vamos conversar."


def test_bridge_process_approval_event_is_safe_for_roteiro():
    event = _bridge_approved_event("  Por enquanto.  ")

    assert event == {
        "type": "celia.text.approved",
        "name": "Célia",
        "text": "Por enquanto.",
        "source": "bridge.process",
    }


def test_public_bridge_response_removes_emotion_control_tag():
    assert _public_bridge_response("[happy] Por enquanto.") == "Por enquanto."


def test_discord_response_removes_mixed_script_artifact_before_output():
    assert _public_bridge_response("De nada, cara. Pra\u0632\u0627\u0631.", discord=True) == "De nada, cara."
    assert _public_bridge_response("Em japonês, ありがとう é obrigado.", discord=True) == "Em japonês, ありがとう é obrigado."
