# Testes

## Como rodar

```powershell
python -m pytest -q
```

Use o `pytest-asyncio` instalado normalmente (não force `-p pytest_asyncio`). Dois arquivos podem passar de 2 minutos: `test_roteiro_interruption_route.py` e `test_tools.py`.

## Resultado medido em 22/11/2026 (arquivo por arquivo, Windows)

- 210 arquivos de teste: **123 100% verdes**.
- **2.041 testes passam**, 338 falham, 24 com erro, 39 pulados.

## Por que ainda há falhas

As falhas restantes vêm de peças que **não fazem parte deste repositório público** ou de configuração que vem enxuta:

- Gate obrigatório de regressão de áudio: [Contrato de isolamento de áudio](CONTRATO-ISOLAMENTO-AUDIO.md). A versão pública deve provar que superfícies de apresentação não capturam microfone. Testes leves: `node tests/test_mic_physical_release.js`, `node tests/test_mic_physical_release.js --pet`, `node tests/test_mic_toggle_sync.js` e `python -m pytest -q tests/test_audio_source.py tests/test_microphone_gate.py tests/test_bridge_corpo_opcoes.py`. Testes simulados não equivalem à homologação física.
- Arquivos e módulos não publicados: roteiros de gravação, janela Pet em Electron (`tools/pet-electron`), jogos, integração Minecraft, bots de Discord/Twitch, scripts `.sh`.
- `config/image-identities.json` e a lista de palavras do filtro de fala (`integrations/tiktok_chat/*.json`) vêm sem conteúdo pessoal.
- `config/model_chains.yaml` vem sem a seção de cotas dos provedores.

Quem usar o repositório pode apagar os testes que não se aplicam ao seu uso.

## Atenção: filtro de fala

`integrations/tiktok_chat/bloqueio_saida.json` precisa existir. Se estiver ausente ou inválido, o filtro **bloqueia toda a fala** por segurança. O arquivo incluso vem vazio: adicione os termos que você quer bloquear.

## Testes não publicados (81 arquivos)

Foram retirados porque dependem de partes do projeto que não estão neste repositório ou de bibliotecas opcionais que não são instaladas por padrão.

### Dependem de: integração Minecraft (não publicada) (33)

- `tests/test_body_hold_persistence.py`
- `tests/test_celia_minecraft_mundos_cli.py`
- `tests/test_ep1_inventory_truth.py`
- `tests/test_jev_mission_retomada.py`
- `tests/test_jev_skill_registry_v3.py`
- `tests/test_minecraft_authorized_goals.py`
- `tests/test_minecraft_central_adapter.py`
- `tests/test_minecraft_companion.py`
- `tests/test_minecraft_companion_aproximacao.py`
- `tests/test_minecraft_companion_resgate.py`
- `tests/test_minecraft_failure_terminal.py`
- `tests/test_minecraft_gameplay_loop.py`
- `tests/test_minecraft_hud.py`
- `tests/test_minecraft_hud_feed.py`
- `tests/test_minecraft_jev.py`
- `tests/test_minecraft_jev_daemon.py`
- `tests/test_minecraft_jev_narration.py`
- `tests/test_minecraft_presence.py`
- `tests/test_minecraft_profile_v6.py`
- `tests/test_minecraft_progression_dispatch.py`
- `tests/test_minecraft_rcon_gateway.py`
- `tests/test_minecraft_recording_state.py`
- `tests/test_minecraft_serie_inteligencia.py`
- `tests/test_minecraft_state_fields.py`
- `tests/test_minecraft_world_manager.py`
- `tests/test_missao_ep1_r20.py`
- `tests/test_possession_central_to_daemon.py`
- `tests/test_possession_event_bridge.py`
- `tests/test_presence_production_gates.py`
- `tests/test_presence_rehearsals.py`
- `tests/test_scene_director.py`
- `tests/test_scene_wiring.py`
- `tests/test_series_give_pausa_missao.py`

### Dependem de: módulos locais não publicados (17)

- `tests/test_audio_lab.py`
- `tests/test_benchmark_celia_identidade_curta_v01.py`
- `tests/test_benchmark_celia_identidade_longa_hibridos.py`
- `tests/test_benchmark_p04_l1_misterio.py`
- `tests/test_benchmark_p04_operacional_compressao.py`
- `tests/test_benchmark_personagens_celia.py`
- `tests/test_benchmark_r15a_cinco_antialucinacao.py`
- `tests/test_direct_address.py`
- `tests/test_game_log_replay.py`
- `tests/test_local_llm_fallback_contract.py`
- `tests/test_minecraft_spectator_camera.py`
- `tests/test_omnivoice_voice_cache_benchmark.py`
- `tests/test_pet_sem_quadro_vazio.py`
- `tests/test_pet_zoom_roda.py`
- `tests/test_react_player_frontend.py`
- `tests/test_teste_vivo_episodio1.py`
- `tests/test_tiktok_celia_adapter.py`

### Dependem de: bots de Discord/Twitch (não publicados) (10)

- `tests/test_p8_platform_connectivity.py`
- `tests/test_platform_bridge_delivery.py`
- `tests/test_platform_chat_adapters.py`
- `tests/test_react_inicio.py`
- `tests/test_react_v2_p3.py`
- `tests/test_react_v2_p4.py`
- `tests/test_react_v2_p5.py`
- `tests/test_react_worker_process.py`
- `tests/test_v02_platform_runner_heartbeat.py`
- `tests/test_v02_retro_adapter.py`

### Dependem de: biblioteca opcional `selenium` (4)

- `tests/test_browser_pet_mode.py`
- `tests/test_desktop_pet_browser.py`
- `tests/test_live2d_body_controls.py`
- `tests/test_runtime_settings_surface.py`

### Dependem de: biblioteca opcional `playwright` (3)

- `tests/test_asr_chat_browser.py`
- `tests/test_checkers_chess_browser_contract.py`
- `tests/test_roteiro_reconnect_browser.py`

### Dependem de: bot de Discord (não publicado) (2)

- `tests/test_celia_discord_bot.py`
- `tests/test_discord_auto_local_vision.py`

### Dependem de: biblioteca opcional `torch` (2)

- `tests/test_omnivoice_tts.py`
- `tests/test_roteiro_prepared_audio.py`

### Dependem de: `bot_choice` (1)

- `tests/test_discord_bot_choice.py`

### Dependem de: `integrations` (1)

- `tests/test_discord_outbound_bot_choice.py`

### Dependem de: `conversation_window` (1)

- `tests/test_discord_peer_conversation.py`

### Dependem de: `voice_vad` (1)

- `tests/test_discord_voice_vad.py`

### Dependem de: `latency_report` (1)

- `tests/test_latency_report.py`

### Dependem de: `start_body` (1)

- `tests/test_minecraft_body_supervisor.py`

### Dependem de: biblioteca opcional `python-socketio` (1)

- `tests/test_minecraft_controller_safety.py`

### Dependem de: `spectator_camera` (1)

- `tests/test_minecraft_profile_camera_v6.py`

### Dependem de: `mcps` (1)

- `tests/test_v02_mcp_launcher_paths.py`

### Dependem de: `common` (1)

- `tests/test_video_memoria_pipeline.py`

