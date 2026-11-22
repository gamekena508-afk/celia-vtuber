from celia.agent.agents.basic_memory_agent import BasicMemoryAgent, _discord_owner_image_post_channel


def test_verified_owner_image_request_posts_to_originating_discord_channel():
    metadata = {
        "origin": "discord",
        "discord_interaction_kind": "discord_mention",
        "discord_is_dm": False,
        "discord_channel_id": "1492687428375281675",
    }

    assert _discord_owner_image_post_channel(metadata, trusted_owner=True) == "1492687428375281675"


def test_public_or_non_channel_turn_never_auto_posts():
    base = {
        "origin": "discord",
        "discord_interaction_kind": "discord_mention",
        "discord_is_dm": False,
        "discord_channel_id": "1492687428375281675",
    }

    assert _discord_owner_image_post_channel(base, trusted_owner=False) == ""
    assert _discord_owner_image_post_channel({**base, "discord_is_dm": True}, trusted_owner=True) == ""
    assert _discord_owner_image_post_channel({**base, "discord_channel_id": "not-a-channel"}, trusted_owner=True) == ""
    assert _discord_owner_image_post_channel({**base, "discord_interaction_kind": ""}, trusted_owner=True) == ""


def test_common_asr_misspelling_still_routes_image_generation():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)

    call = agent._forced_system_tool_call("gera uma iamgen", discord_channel_id="123456789012345678")

    assert call["name"] == "criar_ou_editar_imagem"
    assert call["_post_discord_after"]["enabled"] is False


def test_explicit_image_post_uses_authenticated_originating_channel():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)

    call = agent._forced_system_tool_call(
        "posta a ultima imagem no Discord", discord_channel_id="123456789012345678"
    )

    assert call["name"] == "discord_postar_imagem"
    assert call["input"]["canal_id"] == "123456789012345678"
