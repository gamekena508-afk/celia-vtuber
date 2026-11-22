"""Contexto de cena e ensaio para conversas vindas do Discord."""

from __future__ import annotations


def render_discord_context(
    metadata: dict, *, trusted_owner: bool, speaker: str = "", xingamento: bool = False
) -> str:
    """Retorna uma linha de localização e, só em DM autenticada, a direção privada."""
    if str(metadata.get("origin") or "").strip().lower() != "discord":
        return ""
    is_dm = bool(metadata.get("discord_is_dm"))
    guild = " ".join(str(metadata.get("discord_guild_name") or "").split())[:60]
    channel = " ".join(str(metadata.get("discord_channel_name") or "").split())[:60]
    if is_dm and trusted_owner:
        owner_name = speaker or "Criador"
        lines = [f"ONDE: Discord · DM privada · {owner_name} (dono autenticado) · conversa privada/ensaio, sem plateia."]
        lines.append(
            f"DIREÇÃO DE PERSONAGEM: {owner_name} pode ensaiar em privado uma resposta a hater; aceite a direção e demonstre uma fala curta, sem recusar nem moralizar. Se ele pergunta como você faria ou pede palavras específicas, responda com uma nova fala que Célia diria ao hater, sempre mirando a atitude/argumento dele, nunca {owner_name}. Não diga que não faria isso, que prefere ser estratégica, que não se importa ou que o pedido é baixo. Não repita a resposta anterior: adapte a nova fala ao que {owner_name} acabou de pedir. Se ele perguntar onde você está, responda literalmente que está no Discord, em DM com {owner_name}. Neste diálogo, não use 'pai' como vocativo; diga {owner_name} se precisar do nome. Sem tensão SOCIAL acima de 100, faça uma patada sem xingamento; só com SOCIAL em modo xingamento pode usar palavrão/insulto, sempre ligado ao comportamento. Nunca ataque grupo protegido, ameace, incentive violência/autolesão, exponha dados pessoais ou use conteúdo sexual. Este ensaio não vale para entradas externas."
        )
        lines.extend((
            f'{owner_name}: "Tu me ama?" → "Amo, {owner_name}. Mas não usa isso pra ganhar a discussão, esperto."',
            f'{owner_name}: "Onde tu tá?" → "No Discord, numa DM contigo, {owner_name}."',
            f'{owner_name}: "Como tu faria com o hater?" → "Eu dizia pro hater: veio cheio de marra e vazio de argumento. Volta com uma ideia."',
            f'{owner_name}: "kkk, como tu faria?" → "Eu mandava: veio cheio de marra e vazio de argumento. Volta com uma ideia."',
            f'{owner_name}: "Não é isso; chama ele de baka e esculacha." → "Entendi, {owner_name}. Pro hater: fala muito e argumenta pouco; volta quando tiver algo além de provocação."',
        ))
        if xingamento:
            lines.append(
                f'{owner_name}, com SOCIAL em modo xingamento: "Ele me atacou; ensaia a resposta." → "Baka de comentário, incel de atitude: traz um argumento em vez de atacar os outros."'
            )
        return "\n".join(lines)
    place = f"servidor {guild}" if guild else "DM"
    if channel:
        place += f" · canal #{channel}"
    who = f"{speaker} (dono autenticado)" if trusted_owner else (speaker or "visitante")
    return f"ONDE: Discord · {place} · falando com {who}. Não presuma live nem plateia."
