import re

from celia.agent.bridge_client import TOOL_TRIGGERS, needs_tool


def test_vision_phrase_has_word_boundary_regression():
    vision_patterns = TOOL_TRIGGERS["vision"]
    assert not any(re.search(p, "que estamos fazendo hoje") for p in vision_patterns)
    assert any(re.search(p, "o que esta acontecendo ai") for p in vision_patterns)
    assert any(re.search(p, "o que está acontecendo ai") for p in vision_patterns)


def test_conversation_with_fazendo_no_longer_routes_to_image_tool():
    assert needs_tool("que estamos fazendo hoje") is None
