"""Modo Live TikTok da Célia — módulo isolado, DESLIGADO por padrão.

Nada aqui conecta ao TikTok/Euler. O leitor real é um processo separado
(integrations/tiktok_live_reader) que só sobe quando o dono manda, por
POST /bridge/live/reader/start com a autorização explícita da sessão.

Documentação: docs/LIVE-MODE.md
"""

CHARACTER_KEYS = ("celia", "slime", "ursinho")
