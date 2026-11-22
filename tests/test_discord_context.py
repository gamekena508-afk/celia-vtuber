from celia.core.discord_context import render_discord_context


def test_dm_owner_has_location_roleplay_direction_and_examples():
    context = render_discord_context(
        {"origin": "discord", "discord_is_dm": True},
        trusted_owner=True,
        speaker="Criador",
    )
    assert "Discord · DM privada · Criador (dono autenticado)" in context
    assert "sem plateia" in context
    assert "Criador pode ensaiar" in context
    assert "SOCIAL em modo xingamento" in context
    assert "Tu me ama?" in context and "hater" in context
    assert 'Onde tu tá?' in context and 'No Discord, numa DM contigo, Criador.' in context
    assert "incel de atitude" not in context
    intense = render_discord_context(
        {"origin": "discord", "discord_is_dm": True},
        trusted_owner=True,
        speaker="Criador",
        xingamento=True,
    )
    assert "incel de atitude" in intense


def test_discord_servidor_identifica_local_sem_conceder_papel_privado():
    context = render_discord_context(
        {
            "origin": "discord",
            "discord_is_dm": False,
            "discord_guild_name": "Comunidade",
            "discord_channel_name": "geral",
        },
        trusted_owner=False,
        speaker="Visitante",
    )
    assert "servidor Comunidade · canal #geral" in context
    assert "Não presuma live nem plateia" in context
    assert "DIREÇÃO DE PERSONAGEM" not in context


def test_nao_discord_nao_recebe_contexto_especial():
    assert render_discord_context({"origin": "owner"}, trusted_owner=True) == ""
