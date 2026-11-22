"""Read an already moderated TikTok input through Célia's existing TTS."""

async def read_live_comment(request, speak, speak_request):
    if not request.read_aloud_input:
        return
    if request.fonte != 'tiktok_live_chat':
        raise ValueError('readback_requires_tiktok_source')
    # The owner explicitly requests full spectator names/messages read aloud.
    # This runs only after the standard ingress Guardian and history write.
    result = await speak(speak_request(
        text=f'{request.autor or "Visitante"} disse: {request.text}',
        subtitle_text=f'{request.autor or "Visitante"} disse: {request.text}',
        wait_for_playback=True, record_context=False,
        speech_source='tiktok_comment',
        speech_event_key=f'tiktok:{request.message_id}' if request.message_id else None,
        speech_ttl_ms=120000,
    ))
    if (not result.ok or result.timed_out or result.clients < 1 or
            result.playback_status not in {'ended','finished','complete','completed','ok','success'}):
        raise RuntimeError('comment_readback_not_confirmed')
