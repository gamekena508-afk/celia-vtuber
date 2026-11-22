from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_operational_profiles import behavior


@pytest.mark.parametrize("profile,preview", [("roteiro", False), ("chat", True)])
def test_bridge_process_keeps_response_but_never_previews_recording_subtitle(behavior, monkeypatch, profile, preview):
    import celia.bridge_routes as bridge
    from celia.agent.output_types import SentenceOutput, DisplayText, Actions
    behavior.select_profile(profile)
    monkeypatch.setattr(bridge, "continuous_behavior", behavior)
    monkeypatch.setattr(bridge, "_guardar_entrada", AsyncMock(return_value=(None, "accepted", "test")))
    monkeypatch.setattr(bridge, "_bridge_emotion_context", lambda text: ("", "neutral"))
    class Agent:
        async def chat(self, batch):
            yield SentenceOutput(DisplayText("Finalmente."), "Finalmente.", Actions())
    context = SimpleNamespace(agent_engine=Agent(), history_uid=None)
    singleton = SimpleNamespace(context=context, broadcast=AsyncMock())
    monkeypatch.setattr(bridge.CeliaSingleton, "get_instance", lambda: singleton)
    app = FastAPI()
    app.include_router(bridge.init_bridge_routes(context))
    result = TestClient(app).post("/bridge/process", json={"text": "Fala pessoal!", "fonte": "owner", "synthesize_audio": False})
    assert result.status_code == 200
    assert result.json()["response"] == "Finalmente."
    assert result.json()["error"] is None
    events = [call.args[0] for call in singleton.broadcast.await_args_list]
    assert any(event["type"] == "celia.text.approved" for event in events) is preview
