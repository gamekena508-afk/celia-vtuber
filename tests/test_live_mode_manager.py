"""Gerenciador do modo Live com dublês (LLM/voz/navegador falsos). Offline, sem TikTok, sem rede."""
import asyncio
import functools
import inspect
import wave
from pathlib import Path

import pytest

from celia.live_mode import filters, llm, voices
from celia.live_mode.manager import LiveModeManager
from celia.live_mode.settings import SettingsStore

pytestmark = pytest.mark.asyncio


def chat(msg, user="u1", text="oi gente", origin="simulated", nick=None):
    return {"type": "chat", "origin": origin, "msg_id": msg, "user_id": user, "display_id": user,
            "nickname": nick or user.upper(), "text": text}


def gift(msg, user, value, origin="simulated", units=1):
    return {"type": "gift", "origin": origin, "msg_id": msg, "user_id": user, "display_id": user,
            "nickname": user.upper(), "gift_id": "rose", "gift_name": "Rose", "diamond_count": value,
            "repeat_count": units, "repeat_end": True, "streakable": False, "group_id": "g"}


class Harness:
    """Liga o gerenciador a dublês e registra o que cada etapa viu."""

    def __init__(self, tmp_path, **settings):
        base = {"gap_between_speeches_s": 0.0, "per_user_interval_s": 0.0, "playback_timeout_s": 5.0,
                "gift_reactions_enabled": False, "idle_host_enabled": False}
        base.update(settings)
        self.store = SettingsStore(tmp_path / "settings.json")
        self.store.update(base)
        self.tmp = tmp_path
        self.log: list[tuple[str, str]] = []
        self.active_speech = 0
        self.max_active = 0
        self.celia_calls: list[str] = []
        self.reply_text = "Que legal isso, obrigado por escrever!"
        self.mgr = LiveModeManager(self.store, celia_reply=self._celia, llm_reply=self._llm, synth=self._synth,
                                   state_dir=tmp_path)
        self.client_task = None
        self.ack_status = "ended"
        self.ack_delay = 0.01

    async def _celia(self, job):
        self.active_speech += 1
        self.max_active = max(self.max_active, self.active_speech)
        self.celia_calls.append(job.text)
        self.log.append(("celia", job.text))
        await asyncio.sleep(0.02)
        self.active_speech -= 1
        return {"ok": True, "text": "resposta da celia a: " + job.text, "speech_id": "c-" + job.id}

    async def _llm(self, character, author, text, **kw):
        self.log.append((character + ":llm", text))
        return {"text": self.reply_text, "model": "fake/model", "latency_ms": 5}

    async def _synth(self, character, text):
        path = self.tmp / f"{character}.wav"
        with wave.open(str(path), "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x10\x10" * 1600)
        return {"id": character, "path": path, "voice": "fake-" + character, "duration_s": 0.1, "latency_ms": 3}

    async def browser(self, stop: asyncio.Event, *, claim=True):
        """Navegador falso: pega 'speak' e responde ACK depois de um tempo (conta sobreposição)."""
        after = 0
        await self.mgr.wait_events("tab1", after, wait=0)
        if claim:
            self.mgr.claim_audio("tab1")
        while not stop.is_set():
            got = await self.mgr.wait_events("tab1", after, wait=0.05)
            for ev in got["events"]:
                after = max(after, ev["seq"])
                if ev["type"] == "speak" and got["lease"]:
                    self.active_speech += 1
                    self.max_active = max(self.max_active, self.active_speech)
                    self.log.append((ev["character"] + ":play", ev["text"]))
                    await asyncio.sleep(self.ack_delay)
                    self.active_speech -= 1
                    self.mgr.on_ack({"speech_id": ev["speech_id"], "status": self.ack_status,
                                     "played_ms": 100, "attempt": ev.get("attempt", 0), "client": "tab1"})
            after = max(after, got["seq"]) if not got["events"] else after

    async def run_browser(self):
        self.stop = asyncio.Event()
        self.client_task = asyncio.create_task(self.browser(self.stop))
        await asyncio.sleep(0.05)

    async def drain(self, timeout=3.0):
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            if not self.mgr._queue and self.mgr._current is None:
                return
            await asyncio.sleep(0.02)
        raise AssertionError("fila não esvaziou: " + str(self.mgr.snapshot()["queue"]))

    async def close(self):
        if self.client_task:
            self.stop.set()
            await asyncio.gather(self.client_task, return_exceptions=True)
        await self.mgr.shutdown()


def uses_h(fn):
    """Cria o Harness, roda o teste e SEMPRE fecha (sem fixture assíncrona)."""
    @functools.wraps(fn)
    async def wrapper(tmp_path):
        harness = Harness(tmp_path)
        try:
            await fn(harness)
        finally:
            await harness.close()
    wrapper.__signature__ = inspect.Signature([inspect.Parameter("tmp_path", inspect.Parameter.POSITIONAL_OR_KEYWORD)])
    return wrapper


@uses_h
async def test_reading_names_viewer_and_hands_message_to_celia(h):
    await h.run_browser()
    await h.mgr.enable('test')
    await h.mgr.ingest(gift('d1','donor1',20))
    await h.mgr.ingest(gift('d2','donor2',10))
    await h.mgr.ingest(chat('presented','visitor','Tudo bem?',nick='Ana'))
    await h.drain()
    played=[text for kind,text in h.log if kind.endswith(':play')]
    assert played == ['Ana: Tudo bem?']
    assert h.celia_calls == ['Tudo bem?']
    assert any(j['character']=='ursinho' and j['route']=='reading' for j in h.mgr.snapshot()['recent'])
    assert h.max_active == 1


async def test_connected_stream_does_not_take_audio_lease_until_explicit_claim(tmp_path):
    h = Harness(tmp_path)
    try:
        await h.mgr.enable('test')
        stop = asyncio.Event()
        h.stop = stop
        h.client_task = asyncio.create_task(h.browser(stop, claim=False))
        await asyncio.sleep(0.05)
        assert h.mgr.snapshot()["audio_clients"] == 1
        assert h.mgr.snapshot()["audio_lease_claimed"] is False
        assert h.mgr._lease_holder() is None

        await h.mgr.ingest(chat('resume-after-reload', 'visitor', 'Olá, Célia?'))
        deadline = asyncio.get_running_loop().time() + 2
        while h.mgr._active_audio_event is None and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.01)
        assert h.mgr._active_audio_event is not None
        assert not any(kind.endswith(':play') for kind, _ in h.log)

        result = h.mgr.claim_audio('tab1')
        assert result == {"ok": True, "client": "tab1", "replayed": True}
        await h.drain()
        assert any(kind.endswith(':play') for kind, _ in h.log)
        assert h.mgr.snapshot()["counters"]["jobs_done"] >= 1
    finally:
        await h.close()


async def test_gift_burst_is_coalesced_without_displacing_chat(tmp_path):
    h=Harness(tmp_path,gift_reactions_enabled=True)
    try:
        await h.run_browser()
        await h.mgr.enable('test')
        for n in range(20):
            await h.mgr.ingest(gift('burst-'+str(n),'donor',1))
        await h.mgr.ingest(chat('visitor-message','visitor','Tudo bem?'))
        for n in range(20,40):
            await h.mgr.ingest(gift('burst-'+str(n),'donor',1))
        assert sum(j.reason=='gift' for j in h.mgr._queue)==1
        assert sum(j.reason=='reading' for j in h.mgr._queue)==1
        assert len(h.mgr._queue)==3
        assert h.mgr.standings()[0].total==40
        await h.drain()
        assert h.celia_calls==['Tudo bem?']
        assert h.max_active==1
    finally:
        await h.close()


async def test_gift_acknowledgement_once_at_combo_end(tmp_path):
    h=Harness(tmp_path,gift_reactions_enabled=True)
    try:
        await h.run_browser()
        await h.mgr.enable('test')
        for i in (1,2,3):
            event=gift('combo'+str(i),'ana',1,units=i)
            event.update(streakable=True,repeat_end=False,group_id='same-combo')
            await h.mgr.ingest(event)
        assert not h.mgr._queue
        for ident in ('end','end-again'):
            event=gift(ident,'ana',1,units=3)
            event.update(streakable=True,repeat_end=True,group_id='same-combo')
            await h.mgr.ingest(event)
        await h.drain()
        reactions=[j for j in h.mgr.snapshot()['recent'] if j['route']=='gift']
        assert len(reactions)==1 and reactions[0]['state']=='done'
        assert 'ANA' in reactions[0]['reply'] and any(word in reactions[0]['reply'] for word in ('obrigado','valeu'))
        assert h.mgr.seats()[0]['total']==3
        assert not filters.pede_presente_ou_acao(reactions[0]['reply'])
    finally:
        await h.close()


async def test_idle_host_uses_same_serial_queue_and_no_fake_viewer(tmp_path):
    h=Harness(tmp_path,idle_host_enabled=True)
    try:
        await h.run_browser()
        await h.mgr.enable('test')
        h.mgr._enqueue_idle()
        assert [(j.character,j.reason,j.origin) for j in h.mgr._queue] == [('slime','idle','local'),('celia','idle','local')]
        h.mgr._wake.set()
        # O programa agora prepara o próximo par durante a fala atual;
        # primeiro observar a continuação, depois desligar a iniciativa para drenar.
        for _ in range(150):
            if h.mgr.counters['jobs_done'] >= 2:
                break
            await asyncio.sleep(.02)
        assert h.mgr.counters['jobs_done'] >= 2
        h.mgr.settings.update({'idle_host_enabled':False})
        await h.drain()
        assert h.max_active==1 and h.mgr.counters['jobs_done']>=2
        assert h.mgr.counters['chat_accepted']==0
    finally:
        await h.close()


async def test_celia_preparation_overlaps_reading_but_playback_waits_for_reading_ack(tmp_path):
    h=Harness(tmp_path)
    marks=[]
    async def prepare(job):
        marks.append('prepare-start')
        await asyncio.sleep(.04)
        assert h.active_speech<=1  # preparação pode começar antes da leitura, sem outra voz
        marks.append('prepare-ready')
        return {'ok':True,'response':'resposta preparada','audio_paths':['prepared.wav']}
    async def reply(job):
        assert job.prepared_result['audio_paths']==['prepared.wav']
        assert h.active_speech==0  # a resposta nunca toca sobre a leitura
        marks.append('celia-play')
        return await h._celia(job)
    reply.prepare=prepare
    h.mgr._celia_reply=reply
    h.ack_delay=.12
    try:
        await h.run_browser()
        await h.mgr.enable('test')
        await h.mgr.ingest(chat('paired','viewer','Oi, tudo bem?',nick='Ana'))
        await h.drain()
        assert marks==['prepare-start','prepare-ready','celia-play']
        assert h.max_active==1 and h.celia_calls==['Oi, tudo bem?']
    finally:
        await h.close()


@uses_h
async def test_off_by_default_and_ingest_rejected(h):
    assert h.mgr.mode == "off" and not h.mgr.blocks_owner_input()
    assert (await h.mgr.ingest(chat("m1")))["reason"] == "live_mode_off"
    assert h.mgr.source_allowed("owner") is True            # modo normal intacto


@uses_h
async def test_enable_isolates_owner_input_and_disable_restores(h):
    await h.mgr.enable("test")
    assert h.mgr.blocks_owner_input() is True
    assert h.mgr.source_allowed("owner") is False and h.mgr.source_allowed("microphone") is False
    assert h.mgr.source_allowed("tiktok_live_chat") is True
    snap = await h.mgr.enable("test")                       # idempotente
    assert snap["mode"] == "test"
    await h.mgr.disable()
    assert h.mgr.blocks_owner_input() is False and h.mgr.source_allowed("owner") is True


@uses_h
async def test_mode_and_origin_must_match(h):
    await h.mgr.enable("test")
    assert "not_allowed_in_test" in (await h.mgr.ingest(chat("r1", origin="real")))["reason"]
    await h.mgr.enable("live", room_id="123")
    assert "not_allowed_in_live" in (await h.mgr.ingest(chat("s1", origin="simulated")))["reason"]
    assert h.mgr.ledger().origin == "real"


@uses_h
async def test_no_donors_celia_answers_with_message_content(h):
    await h.mgr.enable("test")
    assert (await h.mgr.ingest(chat("m1", "u1", "qual sua comida favorita?")))["job"]["character"] == "celia"
    await h.drain()
    assert h.celia_calls == ["qual sua comida favorita?"]
    assert h.mgr.snapshot()["recent"][-1]["state"] == "done"


@uses_h
async def test_only_top1_owns_slime_other_viewers_use_general_chat(h):
    await h.run_browser()
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    await h.mgr.ingest(gift("g2", "bia", 50))
    seats = h.mgr.seats()
    assert (seats[0]["user_id"], seats[1]["user_id"]) == ("ana", "bia")
    jobs = {}
    for user in ("ana", "bia", "carlos"):
        jobs[user] = (await h.mgr.ingest(chat("c-" + user, user, "fala de " + user)))["job"]
    assert (jobs["ana"]["character"], jobs["bia"]["character"], jobs["carlos"]["character"]) == ("slime", "celia", "celia")
    assert jobs["ana"]["author"] == "ANA" and jobs["ana"]["user_id"] == "ana"   # identidade acompanha o job
    await h.drain()
    assert "fala de ana" in h.celia_calls  # Célia responde ao mesmo comentário depois do Slime Top 1
    # mudança de posição: bia passa ana
    await h.mgr.ingest(gift("g3", "bia", 80))
    assert [s["user_id"] for s in h.mgr.seats()] == ["bia", "ana"]
    assert (await h.mgr.ingest(chat("c2", "bia", "oi de novo")))["job"]["character"] == "slime"
    assert (await h.mgr.ingest(chat("c3", "ana", "e eu?")))["job"]["character"] == "celia"
    await h.drain()


@uses_h
async def test_explicit_address_beats_rank(h):
    await h.run_browser()
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    job = (await h.mgr.ingest(chat("c1", "ana", "ursinho, você gosta de mel?")))["job"]
    assert (job["character"], job["route"]) == ("ursinho", "explicit")
    job2 = (await h.mgr.ingest(chat("c2", "zeca", "slime, conta uma piada")))["job"]
    assert (job2["character"], job2["route"]) == ("slime", "explicit")
    await h.drain()
    assert "slime, conta uma piada" in h.celia_calls


@uses_h
async def test_serial_queue_never_overlaps_voices(h):
    await h.run_browser()
    h.ack_delay = 0.05
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100)); await h.mgr.ingest(gift("g2", "bia", 50))
    for i, user in enumerate(("ana", "bia", "carlos", "ana", "bia", "carlos")):
        await h.mgr.ingest(chat(f"m{i}", user + str(i % 1), f"mensagem numero {i} da live"))
    await h.drain(6.0)
    assert h.max_active == 1
    assert h.mgr.counters["jobs_done"] >= 3


@uses_h
async def test_auxiliary_flow_reaches_browser_with_own_voice_and_volume(h):
    await h.run_browser()
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    await h.mgr.ingest(chat("c1", "ana", "o que você faz o dia todo?"))
    await h.drain()
    rec = next(j for j in h.mgr.snapshot()["recent"] if j["character"] == "slime")
    assert rec["character"] == "slime" and rec["voice"] == "fake-slime" and rec["state"] == "done"
    assert ("slime:play", h.reply_text) in h.log
    assert "o que você faz o dia todo?" in h.celia_calls     # Célia responde ao mesmo comentário depois do Slime


@uses_h
async def test_no_audio_client_fails_visibly_and_queue_recovers(h):
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    await h.mgr.ingest(chat("c1", "ana", "alguém aí?"))
    await h.drain()
    assert any(j["detail"] == "no_audio_client" for j in h.mgr.snapshot()["recent"])
    await h.mgr.ingest(chat("c2", "carlos", "e comigo?"))   # a Célia continua respondendo
    await h.drain()
    assert h.mgr.snapshot()["recent"][-1]["state"] == "done"


@uses_h
async def test_ack_timeout_and_llm_failure_do_not_kill_the_worker(h):
    await h.run_browser()
    h.store.update({"playback_timeout_s": 5.0})
    h.ack_status = "error"
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    await h.mgr.ingest(chat("c1", "ana", "teste de erro"))
    await h.drain()
    assert any(j["detail"] == "playback_error" for j in h.mgr.snapshot()["recent"])

    async def boom(*a, **k):
        raise llm.LLMError("all_links_failed")
    h.mgr._llm_reply = boom
    await h.mgr.ingest(chat("c2", "ana", "falha no llm"))
    await h.drain()
    assert any(j["detail"].startswith("llm:") for j in h.mgr.snapshot()["recent"])
    await h.mgr.ingest(chat("c3", "carlos", "ainda funciona?"))
    await h.drain()
    assert h.mgr.snapshot()["recent"][-1]["state"] == "done"


@uses_h
async def test_reply_asking_for_gifts_is_blocked_before_voice(h):
    await h.run_browser()
    h.reply_text = "Manda uma rosa pra mim, por favor!"
    await h.mgr.enable("test")
    await h.mgr.ingest(gift("g1", "ana", 100))
    await h.mgr.ingest(chat("c1", "ana", "oi slime"))
    await h.drain()
    rec = next(j for j in h.mgr.snapshot()["recent"] if j["character"] == "slime")
    assert rec["state"] == "failed" and rec["detail"].startswith("output_blocked")
    # A leitura de entrada pelo outro auxiliar pode ocorrer; a resposta que pede
    # presentes nunca pode chegar à síntese/reprodução.
    assert not any(k.endswith(":play") and text == h.reply_text for k, text in h.log)


async def test_per_user_limits_duplicates_and_filter(tmp_path):
    h = Harness(tmp_path, per_user_interval_s=30.0)
    try:
        await h.mgr.enable("test")
        assert (await h.mgr.ingest(chat("a1", "u1", "primeira")))["ok"]
        assert (await h.mgr.ingest(chat("a2", "u1", "segunda logo")))["reason"] == "user_interval"
        assert (await h.mgr.ingest(chat("a1", "u2", "mesmo id")))["reason"] == "duplicate"
        assert (await h.mgr.ingest(chat("a3", "u3", "")))["reason"] == "missing_fields"
        await h.drain()
    finally:
        await h.close()


async def test_disable_cancels_queue_stops_reader_and_keeps_normal_mode(tmp_path):
    h = Harness(tmp_path)
    stopped = []

    class FakeReader:
        async def stop(self): stopped.append(1)
        def status(self): return {"state": "stopped"}
    h.mgr.reader = FakeReader()
    try:
        await h.mgr.enable("test")
        h.mgr._worker.cancel()                              # congela o trabalhador para a fila ficar cheia
        await asyncio.gather(h.mgr._worker, return_exceptions=True)
        for i in range(3):
            await h.mgr.ingest(chat(f"q{i}", f"u{i}", f"msg {i}"))
        snap = await h.mgr.disable()
        assert stopped == [1] and snap["mode"] == "off" and snap["queue"] == []
        assert all(j["state"] == "cancelled" for j in snap["recent"][-3:])
        assert h.mgr.source_allowed("owner") is True
    finally:
        await h.close()


async def test_live_ledger_survives_restart_same_room_only(tmp_path):
    h = Harness(tmp_path)
    try:
        await h.mgr.enable("live", room_id="777")
        await h.mgr.ingest(gift("g1", "ana", 40, origin="real"))
        await h.mgr.disable()
        mgr2 = LiveModeManager(h.store, celia_reply=h._celia, llm_reply=h._llm, synth=h._synth, state_dir=tmp_path)
        await mgr2.enable("live", room_id="777")
        assert [d.user_id for d in mgr2.standings()] == ["ana"]
        await mgr2.enable("live", room_id="888")            # outra sala: ranking começa limpo
        assert mgr2.standings() == []
        await mgr2.shutdown()
    finally:
        await h.close()


async def test_simulated_gifts_never_touch_real_ledger(tmp_path):
    h = Harness(tmp_path)
    try:
        await h.mgr.enable("test")
        await h.mgr.ingest(gift("g1", "ana", 500))
        await h.mgr.enable("live", room_id="9")
        assert h.mgr.standings() == []
    finally:
        await h.close()


async def test_filter_unit_cases():
    assert filters.pede_presente_ou_acao("Manda uma rosa pra mim!")
    assert filters.pede_presente_ou_acao("Segue a live, gente")
    assert filters.pede_presente_ou_acao("Dá uma rosa pro slime?")
    assert filters.pede_presente_ou_acao("Eu adoro jardim, as flores são lindas") is None
    assert filters.pede_presente_ou_acao("Que presente de aniversário bonito") is None
    assert filters.limpar_resposta("*sorri* Oi!! (rindo) Tudo bem? 😀").startswith("Oi!!")


@uses_h
async def test_idle_long_poll_keeps_the_audio_lease(h):
    """Regressão do E2E: cliente esperando no long-poll (sem novo contato há mais que a janela) continua dono do áudio."""
    h.mgr.LEASE_ACTIVE_S = 0.05
    poll = asyncio.create_task(h.mgr.wait_events("tab1", 0, wait=0.6))
    await asyncio.sleep(0.01)                                # registra a aba primeiro
    assert h.mgr.claim_audio("tab1")["ok"]                 # claim explícito após gesto
    await asyncio.wait_for(poll, 2)                          # o estado do lease acorda o poll atual
    poll = asyncio.create_task(h.mgr.wait_events("tab1", h.mgr._seq, wait=0.6))
    await asyncio.sleep(0.3)                                # > LEASE_ACTIVE_S sem novo contato, mas ainda esperando
    assert h.mgr._lease_holder() == "tab1"
    await h.mgr.enable("test")                              # o state event acorda o poll
    await asyncio.wait_for(poll, 2)
    await asyncio.sleep(0.2)                                # agora o cliente parou de consultar
    assert h.mgr._lease_holder() is None                    # controle negativo: sem poll e sem contato, não há dono


@uses_h
async def test_counters_reset_on_new_session_but_not_on_idempotent_enable(h):
    await h.mgr.enable("test")
    await h.mgr.ingest(chat("k1", "u1", "uma mensagem"))
    await h.drain()
    assert h.mgr.counters["chat_accepted"] == 1
    await h.mgr.enable("test")                      # idempotente: não zera
    assert h.mgr.counters["chat_accepted"] == 1
    await h.mgr.disable()
    await h.mgr.enable("test")                      # sessão nova: zera
    assert h.mgr.counters["chat_accepted"] == 0 and h.mgr.last_error == ""


async def test_celia_uses_shared_player_and_prepares_three_ahead(tmp_path,monkeypatch):
    h=Harness(tmp_path,characters={'ursinho':{'enabled':False}})
    monkeypatch.setattr(voices,'AUDIO_DIR',tmp_path/'shared-audio')
    source=await h._synth('celia','voz original')
    records=[]
    async def prepare(job):
        await asyncio.sleep(.03)
        return {'ok':True,'response':'Resposta a '+job.text,'audio_paths':[str(source['path'])]}
    async def legacy(job):
        raise AssertionError('segundo player não deve ser chamado')
    async def record(job):
        assert h.active_speech==0
        records.append(job.text)
    legacy.prepare=prepare
    legacy.shared_player=True
    legacy.record=record
    h.mgr._celia_reply=legacy
    try:
        await h.run_browser()
        await h.mgr.enable('test')
        for n in range(4):await h.mgr.ingest(chat('buffer'+str(n),'user'+str(n),'Mensagem '+str(n)))
        assert len(h.mgr.snapshot()['prepared_buffer'])==3
        await h.drain()
        assert set(records)=={'Mensagem '+str(n) for n in range(4)}
        assert len(records)==4 and h.max_active==1
        assert all(j['state']=='done' for j in h.mgr.snapshot()['recent'])
    finally:
        await h.close()
