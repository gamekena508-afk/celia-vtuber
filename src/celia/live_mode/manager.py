"""Gerenciador do modo Live: estado, ingestão, roteamento, fila coordenada e eventos para o navegador.

Estados: off (padrão, ao iniciar) | test (eventos simulados, zero rede) | live (eventos reais do leitor).
Em test/live a entrada de conversa vem SÓ do chat da live; microfone e texto digitado do dono ficam isolados
(o ASR continua rodando; apenas a transcrição não é encaminhada). Nada aqui abre conexão com o TikTok.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import shutil
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from . import filters, llm, ranking as ranking_mod, voices
from .characters import AUXILIARES, PERSONAGENS
from .router import route
from .presentation import present_comment, gift_thanks
from .settings import LIVE_DIR, SettingsStore

MODES = ("off", "test", "live")
ORIGIN_FOR_MODE = {"test": "simulated", "live": "real"}
CELIA_FALLBACK_WAIT_S = 4.0


@dataclass
class Job:
    id: str
    character: str
    reason: str
    rank: int | None
    author: str
    user_id: str
    display_id: str
    text: str
    message_id: str
    origin: str
    created: float
    state: str = "queued"      # queued|generating|synthesizing|speaking|done|failed|cancelled|dropped
    detail: str = ""
    reply: str = ""
    speech_id: str = ""
    model: str = ""
    voice: str = ""
    audio_ms: int = 0
    timings: dict[str, int] = field(default_factory=dict)
    preparation: Any = field(default=None, repr=False)
    prepared_result: dict[str, Any] | None = field(default=None, repr=False)
    avatar_url: str = ""

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "character": self.character, "route": self.reason, "rank": self.rank,
                "author": self.author, "user_id": self.user_id, "display_id": self.display_id, "avatar_url": self.avatar_url,
                "text": self.text, "origin": self.origin, "state": self.state, "detail": self.detail,
                "reply": self.reply, "speech_id": self.speech_id, "model": self.model, "voice": self.voice,
                "age_s": round(time.time() - self.created, 1), "timings": self.timings}


CeliaReply = Callable[[Job], Awaitable[dict[str, Any]]]


class LiveModeManager:
    def __init__(self, settings: SettingsStore | None = None, *, celia_reply: CeliaReply | None = None,
                 llm_reply=llm.reply, synth=voices.synthesize, state_dir: Path | None = None,
                 clock: Callable[[], float] = time.time):
        self.settings = settings or SettingsStore()
        self.state_dir = Path(state_dir) if state_dir else LIVE_DIR
        self._celia_reply = celia_reply
        self._llm_reply = llm_reply
        self._synth = synth
        self._clock = clock
        self.mode = "off"
        self.room_id: str | None = None
        self.session_started: float | None = None
        self._ledgers: dict[str, ranking_mod.GiftLedger] = {}
        self._queue: deque[Job] = deque()
        self._wake = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._current: Job | None = None
        self._paired_reply_id: str | None = None
        self._last_voice_finished = self._clock()
        self._history: deque[Job] = deque(maxlen=40)
        self._seen_msgs: "OrderedDict[str, float]" = OrderedDict()
        self._user_times: dict[str, deque[float]] = {}
        self._last_text: dict[str, tuple[str, float]] = {}
        self._char_history: dict[str, deque[dict[str, str]]] = {k: deque(maxlen=6) for k in AUXILIARES}
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._seq = 0
        self._cond = asyncio.Condition()
        self._clients: dict[str, float] = {}
        self._first_seen: dict[str, float] = {}
        self._waiting: dict[str, int] = {}
        self._lease_id: str | None = None
        self._acks: dict[str, asyncio.Future] = {}
        self._active_audio_event: dict[str,Any] | None = None
        self._speaking: dict[str, Any] | None = None
        self._last_seats: tuple[str, ...] = ()
        self._avatars: dict[str,str] = {}
        self._presentation_turn = 0
        self._gift_turn = 0
        self._buffer_turn = 0
        self._reader_turn = 0
        self._idle_turn = 0
        self._gift_announced: OrderedDict[str, float] = OrderedDict()
        self.reader = None  # ReaderController (injetado pela rota); precisa de .stop()/.status()
        self.counters: dict[str, int] = {k: 0 for k in (
            "chat_accepted", "chat_rejected", "gifts", "jobs_done", "jobs_failed", "mic_blocked", "foreign_blocked")}
        self.last_error = ""

    # ---------------- ciclo de vida ----------------
    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.get_running_loop().create_task(self._run(), name="live-mode-worker")

    async def shutdown(self) -> None:
        await self.disable()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    @property
    def active(self) -> bool:
        return self.mode in ("test", "live")

    def ledger(self) -> ranking_mod.GiftLedger | None:
        origin = ORIGIN_FOR_MODE.get(self.mode)
        if not origin:
            return None
        if origin not in self._ledgers:
            self._ledgers[origin] = ranking_mod.GiftLedger(origin, window_minutes=self.settings.get()["ranking_window_minutes"])
        self._ledgers[origin].window_minutes = self.settings.get()["ranking_window_minutes"]
        return self._ledgers[origin]

    async def enable(self, mode: str, room_id: str | None = None) -> dict[str, Any]:
        if mode not in ("test", "live"):
            raise ValueError("mode deve ser test ou live")
        if self.mode == mode and (mode != "live" or room_id in (None, self.room_id)):
            return self.snapshot()  # idempotente: ativar de novo não duplica nada
        if self.active:
            await self.disable()
        self.mode = mode
        self.room_id = room_id if mode == "live" else None
        self.session_started = self._clock()
        self._last_voice_finished = self._clock()
        self._gift_announced.clear()
        self._ledgers.pop(ORIGIN_FOR_MODE[mode], None)  # sessão nova = ranking novo (regra documentada)
        for key in self.counters:  # contadores também são da sessão
            self.counters[key] = 0
        self.last_error = ""
        if mode == "live":
            self._restore_ledger(room_id)
        self.start()
        self._last_seats = ()
        self._publish_state()
        return self.snapshot()

    async def disable(self) -> dict[str, Any]:
        was = self.mode
        if self.reader is not None:
            try:
                await self.reader.stop()
            except Exception as exc:  # parar o leitor nunca impede desligar o modo
                self.last_error = "reader_stop:" + type(exc).__name__
        if was == "live":
            self._save_ledger()
        self.mode = "off"
        self._paired_reply_id = None
        while self._queue:
            job = self._queue.popleft()
            if job.preparation is not None and not job.preparation.done():
                job.preparation.cancel()
            job.state, job.detail = "cancelled", "live_disabled"
            self._history.append(job)
        for fut in self._acks.values():
            if not fut.done():
                fut.cancel()
        if self._speaking:
            self._emit({"type": "stop"})
        self._speaking = None
        self._publish_state()
        return self.snapshot()

    # ---------------- portões de isolamento ----------------
    def blocks_owner_input(self) -> bool:
        """True quando microfone/texto do dono NÃO podem entrar na conversa."""
        return self.active

    def note_mic_blocked(self) -> None:
        self.counters["mic_blocked"] += 1

    def source_allowed(self, fonte: str | None) -> bool:
        if not self.active:
            return True
        ok = (fonte or "").strip().lower() in self.settings.get()["allow_sources"]
        if not ok:
            self.counters["foreign_blocked"] += 1
        return ok

    # ---------------- ingestão ----------------
    async def ingest(self, event: dict[str, Any]) -> dict[str, Any]:
        if not self.active:
            return {"ok": False, "reason": "live_mode_off"}
        expected = ORIGIN_FOR_MODE[self.mode]
        if event.get("origin") != expected:
            self.counters["chat_rejected"] += 1
            return {"ok": False, "reason": f"origin_{event.get('origin')}_not_allowed_in_{self.mode}"}
        uid = str(event.get('user_id') or '')
        avatar = str(event.get('avatar_url') or '')
        if uid and avatar.startswith('https://') and len(avatar)<=2048:
            self._avatars[uid] = avatar
            if len(self._avatars)>5000:self._avatars.pop(next(iter(self._avatars)))
        kind = event.get("type")
        if kind == "gift":
            return self._ingest_gift(event)
        if kind == "chat":
            return self._ingest_chat(event)
        return {"ok": False, "reason": "unknown_event_type"}

    def _ingest_gift(self, event: dict[str, Any]) -> dict[str, Any]:
        ledger = self.ledger()
        result = ledger.apply(event, now=self._clock())
        self.counters["gifts"] += 1
        if result["credited"]:
            if self.mode == "live":
                self._save_ledger()
            self._publish_state()  # valor também muda sem troca de lugares
        self._gift_reaction(event, result)
        return {"ok": True, **result, "seats": self.seats()}

    def _gift_reaction(self, event: dict[str, Any], result: dict[str, Any]) -> None:
        cfg = self.settings.get()
        if not cfg["gift_reactions_enabled"] or result.get("reason") not in ("ok", "combo_already_counted"):
            return
        if event.get("streakable") and not event.get("repeat_end"):
            return  # agradecer apenas quando termina o combo
        user = str(event.get("user_id") or "")
        key = (f"{user}:{event.get('gift_id')}:{event.get('group_id')}" if event.get("streakable")
               else str(event.get("msg_id") or ""))
        if key in self._gift_announced:
            return
        author = str(event.get("nickname") or event.get("display_id") or "Visitante")[:60]
        gift = str(event.get("gift_name") or "presente")[:50]
        if filters.entrada_bloqueada(author, gift):
            return
        enabled = [name for name in AUXILIARES if cfg["characters"][name]["enabled"]]
        if not enabled:
            return
        ranked = route("", user, self._top_user_ids()).character
        character = ranked if ranked in enabled else enabled[self._reader_turn % len(enabled)]
        pending = next((j for j in self._queue if j.reason == 'gift' and j.user_id == user), None)
        if pending is not None:
            pending.text = gift_thanks(author,gift,self._gift_turn,pending.character,grouped=True)
            self._gift_announced[key] = self._clock()
            if self.mode == 'live':
                self._save_ledger()
            return
        if len(self._queue) >= cfg["queue_max"]:
            return  # presentes nunca expulsam leitura/resposta do chat
        self._gift_announced[key] = self._clock()
        while len(self._gift_announced) > 5000:
            self._gift_announced.popitem(last=False)
        text = gift_thanks(author,gift,self._gift_turn,character)
        self._gift_turn += 1
        reaction = Job(id=uuid.uuid4().hex[:10], character=character, reason="gift", rank=None,
            author=author, user_id=user, display_id=str(event.get("display_id") or ""), text=text,
            message_id=str(event.get("msg_id"))+'-thanks', origin=event["origin"], created=self._clock())
        self._queue.append(reaction)
        if self.mode == 'live':
            self._save_ledger()
        self._wake.set()

    def _drop_queued(self, victim: Job) -> None:
        base = victim.message_id.removesuffix('-read')
        # Leitura e resposta são uma unidade: não deixar um bichinho perguntar
        # à Célia sobre um comentário cuja resposta já foi descartada.
        for job in list(self._queue):
            paired_top1_slime = job.character == 'slime' and job.reason in ('rank', 'explicit')
            if job is victim or (job.message_id.removesuffix('-read') == base and
                                 (job.reason in ('default','reading','idle') or paired_top1_slime)):
                self._queue.remove(job)
                job.state, job.detail = 'dropped', 'queue_full_replaced'
                self._history.append(job)

    def _can_drop(self, job: Job) -> bool:
        if job.id == self._paired_reply_id:
            return False
        if job.character == 'celia' and any(
                queued.character == 'slime' and queued.reason in ('rank', 'explicit')
                and queued.message_id == job.message_id for queued in self._queue):
            return False  # manter junto a resposta da Célia ao turno do Slime
        if job.character == 'slime' and job.reason in ('rank', 'explicit') and any(
                queued.character == 'celia' and queued.message_id == job.message_id
                for queued in self._queue):
            return False
        if job.preparation is not None:
            return False
        if (self._current is not None and self._current.reason == 'reading'
                and job.message_id == self._current.message_id.removesuffix('-read')):
            return False
        return job.reason in ('default','reading','idle')

    def _ingest_chat(self, event: dict[str, Any]) -> dict[str, Any]:
        cfg = self.settings.get()
        user_id = str(event.get("user_id") or "").strip()
        msg_id = str(event.get("msg_id") or "").strip()
        author = (str(event.get("nickname") or event.get("display_id") or "Visitante").strip() or "Visitante")[:60]
        text = " ".join(str(event.get("text") or "").split())[: cfg["max_chars_read"]]
        if not text or not msg_id or not user_id:
            return self._reject("missing_fields")
        if msg_id in self._seen_msgs:
            return self._reject("duplicate")
        self._seen_msgs[msg_id] = self._clock()
        while len(self._seen_msgs) > 5000:
            self._seen_msgs.popitem(last=False)
        motivo = filters.entrada_bloqueada(author, text)
        if motivo:
            return self._reject("blocked:" + motivo)
        now = self._clock()
        times = self._user_times.setdefault(user_id, deque())
        while times and now - times[0] > 60:
            times.popleft()
        if times and now - times[-1] < cfg["per_user_interval_s"]:
            return self._reject("user_interval")
        if len(times) >= cfg["per_user_max_per_minute"]:
            return self._reject("user_rate")
        last = self._last_text.get(user_id)
        if last and last[0] == text.casefold() and now - last[1] < 60:
            return self._reject("repeated_text")
        self._last_text[user_id] = (text.casefold(), now)
        times.append(now)
        decision = route(text, user_id, self._top_user_ids(), ranking_enabled=cfg["ranking_characters_enabled"],
                         enabled={k: v["enabled"] for k, v in cfg["characters"].items()})
        job = Job(id=uuid.uuid4().hex[:10], character=decision.character, reason=decision.reason, rank=decision.rank,
                  author=author, user_id=user_id, display_id=str(event.get("display_id") or ""), text=text,
                  message_id=msg_id, origin=event["origin"], created=now, avatar_url=self._avatars.get(user_id,""))
        # Os lugares Top1/Top2 escolhem quem RESPONDE. Não silenciam a leitura
        # geral: os auxiliares revezam os comentários que a Célia vai responder,
        # inclusive quando os dois lugares já estão ocupados.
        readers = ["ursinho"] if cfg["characters"]["ursinho"]["enabled"] else []
        # Comentários reais têm prioridade sobre a próxima iniciativa local, ainda não iniciada.
        for pending in list(self._queue):
            if pending.reason == "idle" and (self._current is None or pending.message_id != self._current.message_id):
                self._queue.remove(pending)
                if pending.preparation is not None and not pending.preparation.done():pending.preparation.cancel()
                pending.state,pending.detail="cancelled","real_chat_priority"
                self._history.append(pending)
        presentation_turn = self._presentation_turn
        if decision.character == "celia": self._presentation_turn += 1
        wants_reading = decision.character == "celia" and bool(readers) and cfg["queue_max"] >= 2 and presentation_turn % 4 != 3 and any(c.isalnum() for c in text)
        pair_celia_after_slime = (decision.character == 'slime' and
                                  (decision.rank == 1 or decision.reason == 'explicit') and
                                  cfg.get('characters', {}).get('celia', {}).get('enabled', True))
        needed = 2 if wants_reading or pair_celia_after_slime else 1
        victims = sorted((j for j in self._queue if self._can_drop(j)),
                         key=lambda j: (j.reason != "idle", j.reason != "default", j.created))
        # Nunca expulsar uma resposta dirigida, por ranking ou um teste solicitado.
        if wants_reading and len(self._queue) + needed - len(victims) > cfg["queue_max"]:
            needed, wants_reading = 1, False
        if len(self._queue) + needed - len(victims) > cfg["queue_max"]:
            return self._reject("queue_full")
        while len(self._queue) + needed > cfg["queue_max"]:
            victim = victims.pop(0)
            if victim in self._queue:
                self._drop_queued(victim)
        if wants_reading:
            reader = readers[self._reader_turn % len(readers)]
            self._reader_turn += 1
            read_job = Job(id=uuid.uuid4().hex[:10], character=reader, reason="reading", rank=None,
                           author=author, user_id=user_id, display_id=str(event.get("display_id") or ""),
                           text=present_comment(author,text,presentation_turn), message_id=msg_id + "-read",
                           origin=event["origin"], created=now, avatar_url=self._avatars.get(user_id,""))
            self._queue.append(read_job)
        if decision.rank == 1:
            self._queue.appendleft(job)
            if pair_celia_after_slime:
                self._queue.append(Job(id=uuid.uuid4().hex[:10],character='celia',reason='default',rank=None,
                    author=author,user_id=user_id,display_id=str(event.get('display_id') or ''),text=text,
                    message_id=msg_id,origin=event['origin'],created=now,avatar_url=self._avatars.get(user_id,'')))
        else:
            self._queue.append(job)
            if pair_celia_after_slime:
                self._queue.append(Job(id=uuid.uuid4().hex[:10],character='celia',reason='default',rank=None,
                    author=author,user_id=user_id,display_id=str(event.get('display_id') or ''),text=text,
                    message_id=msg_id,origin=event['origin'],created=now,avatar_url=self._avatars.get(user_id,'')))
        self._prime_buffer()
        self.counters["chat_accepted"] += 1
        self._wake.set()
        return {"ok": True, "job": job.public()}

    def _reject(self, reason: str) -> dict[str, Any]:
        self.counters["chat_rejected"] += 1
        return {"ok": False, "reason": reason}

    async def speak_test(self, character: str, text: str, author: str = "Teste") -> dict[str, Any]:
        """Teste de personagem pelo painel: fala/boca/voz sem TikTok e sem mexer no ranking."""
        if character not in PERSONAGENS:
            return {"ok": False, "reason": "unknown_character"}
        job = Job(id=uuid.uuid4().hex[:10], character=character, reason="test", rank=None, author=author, user_id="test",
                  display_id="", text=text[:300], message_id="test-" + uuid.uuid4().hex[:8], origin="simulated",
                  created=self._clock())
        self.start()
        self._queue.append(job)
        self._wake.set()
        return {"ok": True, "job": job.public()}

    # ---------------- ranking ----------------
    def standings(self) -> list[ranking_mod.Donor]:
        ledger = self.ledger()
        return ledger.top(2, now=self._clock()) if ledger else []

    def _top_user_ids(self) -> list[str]:
        return [d.user_id for d in self.standings()]

    def _seat_ids(self) -> tuple[str, ...]:
        return tuple(self._top_user_ids())

    def seats(self) -> list[dict[str, Any]]:
        top = self.standings()
        out = []
        for index, character in enumerate(AUXILIARES):
            donor = top[index] if index < len(top) else None
            out.append({"seat": index + 1, "character": character,
                        "user_id": donor.user_id if donor else None,
                        "name": (donor.nickname or donor.display_id) if donor else None,
                        "display_id": donor.display_id if donor else None,
                        "avatar_url": (self._avatars.get(donor.user_id) or donor.avatar_url) if donor else None,
                        "total": donor.total if donor else 0})
        return out

    def _ledger_path(self) -> Path:
        return self.state_dir / "ledger-real.json"

    def _save_ledger(self) -> None:
        ledger = self._ledgers.get("real")
        if not ledger or not self.room_id:
            return
        self.state_dir.mkdir(parents=True, exist_ok=True)
        payload = {"room_id": self.room_id, "saved_at": self._clock(), "ledger": ledger.to_state(),
                   "announced_gifts": list(self._gift_announced)[-5000:]}
        fd, tmp = tempfile.mkstemp(dir=self.state_dir, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
        os.replace(tmp, self._ledger_path())

    def _restore_ledger(self, room_id: str | None) -> None:
        """Reinício no meio da MESMA live (mesmo room_id, < 6 h) recupera o ranking; sala diferente começa do zero."""
        try:
            data = json.loads(self._ledger_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if room_id and data.get("room_id") == room_id and self._clock() - float(data.get("saved_at", 0)) < 6 * 3600:
            self._ledgers["real"] = ranking_mod.GiftLedger.from_state(
                data["ledger"], window_minutes=self.settings.get()["ranking_window_minutes"])
            self._gift_announced = OrderedDict((str(k), self._clock()) for k in data.get('announced_gifts', [])[-5000:])

    def reset_ranking(self) -> dict[str, Any]:
        ledger = self.ledger()
        if ledger:
            ledger.reset()
            if self.mode == "live":
                self._save_ledger()
            self._publish_state()
        return {"ok": ledger is not None}

    # ---------------- fila / trabalhador ----------------
    def _enqueue_idle(self, *, anticipate: bool = False) -> None:
        cfg = self.settings.get()
        if not self.active or self._queue or (self._current and not anticipate) or not cfg['proactive_speech'] or not cfg['idle_host_enabled']:
            return
        # Iniciativa dos personagens é local: não depende de haver chat conectado.
        readers = [name for name in AUXILIARES if cfg['characters'][name]['enabled']]
        if not readers or cfg['queue_max'] < 2 or not self._lease_holder():
            return
        reader = 'slime' if 'slime' in readers else readers[0]
        topics = ('Qual foi a melhor parte do seu dia até agora?',
                  'Se você pudesse escolher um lanche agora, qual seria?',
                  'Que jogo combina com uma tarde tranquila?')
        topic = topics[self._idle_turn % len(topics)]
        self._idle_turn += 1
        ident = 'host-' + uuid.uuid4().hex[:10]
        for character, reason, text in ((reader,'idle',f'Célia, vamos conversar com o pessoal. {topic}'),
                                        ('celia','idle',topic)):
            self._queue.append(Job(id=uuid.uuid4().hex[:10],character=character,reason=reason,rank=None,
                author='Slime' if reader=='slime' else 'Ursinho',user_id='local-host',display_id='',text=text,
                message_id=ident,origin='local',created=self._clock()))
        # Preparar as duas vozes enquanto o turno anterior ainda está audível.
        prepare = getattr(self._celia_reply, 'prepare', None)
        for pending in self._queue:
            if pending.message_id != ident: continue
            if pending.character == 'celia' and prepare is not None:
                pending.preparation = asyncio.create_task(prepare(pending))
            elif pending.character != 'celia':
                pending.preparation = asyncio.create_task(self._synth(pending.character, pending.text))

    def _prime_buffer(self) -> None:
        prepare = getattr(self._celia_reply, 'prepare', None)
        if prepare is None: return
        pending = list(self._queue)
        occupied = sum(j.character=='celia' and j.preparation is not None for j in pending)
        candidates = [j for j in pending if j.character=='celia' and j.preparation is None]
        for job in sorted(candidates,key=lambda j:j.created,reverse=True)[:max(0,3-occupied)]:
            job.preparation=asyncio.create_task(prepare(job))
            reading=next((j for j in pending if j.reason=='reading' and j.message_id==job.message_id+'-read'),None)
            if reading is not None and reading.preparation is None:
                reading.preparation=asyncio.create_task(self._synth(reading.character,reading.text))

    def _take_next_job(self) -> Job:
        paired=next((j for j in self._queue if j.id==self._paired_reply_id),None)
        if paired is not None:
            self._queue.remove(paired)
            return paired
        priority=next((j for j in self._queue if j.rank==1),None)
        if priority is not None:
            self._queue.remove(priority)
            return priority
        gift = next((j for j in self._queue if j.reason=='gift'),None)
        if gift is not None and self._buffer_turn%3==2:
            self._buffer_turn+=1
            self._queue.remove(gift)
            return gift
        prepared=[j for j in self._queue if j.character=='celia' and j.preparation is not None]
        ready=[j for j in prepared if j.preparation.done()]
        if ready:
            ordered=sorted(ready,key=lambda j:j.created)
            chosen=ordered[-1] if self._buffer_turn%3==0 else ordered[0]
            self._buffer_turn+=1
            reading=next((j for j in self._queue if j.reason=='reading' and j.message_id==chosen.message_id+'-read'),None)
            job=reading or chosen
            self._queue.remove(job)
            return job
        return self._queue.popleft()

    async def _run(self) -> None:
        while True:
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=min(self.settings.get()['idle_after_s'], self.settings.get()['silence_limit_s']/2))
            except asyncio.TimeoutError:
                self._enqueue_idle()
            self._wake.clear()
            while self._queue:
                self._prime_buffer()
                job = self._take_next_job()
                if self._paired_reply_id == job.id:
                    self._paired_reply_id = None
                if job.origin != "simulated" and not self.active:
                    job.state, job.detail = "cancelled", "live_disabled"
                    self._history.append(job)
                    continue
                self._current = job
                try:
                    await self._process(job)
                    if job.state not in ("failed", "dropped"):
                        job.state = "done"
                        self.counters["jobs_done"] += 1
                        self._last_voice_finished = self._clock()
                except asyncio.CancelledError:
                    job.state, job.detail = "cancelled", "worker_cancelled"
                    raise
                except Exception as exc:  # um job ruim nunca derruba a fila
                    job.state, job.detail = "failed", type(exc).__name__ + ":" + str(exc)[:120]
                    self.counters["jobs_failed"] += 1
                    self.last_error = job.detail
                finally:
                    self._speaking = None
                    self._current = None
                    self._history.append(job)
                    if (job.reason in ('reading', 'idle') or
                            (job.character == 'slime' and job.reason in ('rank', 'explicit'))):
                        paired = next((j for j in self._queue if j.message_id == job.message_id.removesuffix('-read')), None)
                        if paired is not None:
                            self._queue.remove(paired)
                            self._queue.appendleft(paired)
                            self._paired_reply_id = paired.id
                await asyncio.sleep(self.settings.get()["gap_between_speeches_s"])

    def _fail(self, job: Job, detail: str) -> None:
        job.state, job.detail = "failed", detail
        self.counters["jobs_failed"] += 1
        self.last_error = detail

    async def _process(self, job: Job) -> None:
        cfg = self.settings.get()
        t0 = time.monotonic()
        if job.character == "celia":
            if self._celia_reply is None:
                return self._fail(job, "celia_pipeline_not_wired")
            prepare = getattr(self._celia_reply, 'prepare', None)
            if job.preparation is None and prepare is not None:
                job.preparation = asyncio.create_task(prepare(job))
            if job.preparation is not None:
                job.state = 'generating'
                job.prepared_result = await job.preparation
                if not job.prepared_result.get('ok'):
                    return self._fail(job, 'celia_prepare:' + str(job.prepared_result.get('error') or 'failed'))
            if job.prepared_result is not None and getattr(self._celia_reply,'shared_player',False):
                ready=job.prepared_result
                job.reply=str(ready['response'])
                from celia.core.dialogue_hygiene import is_infrastructure_text
                if is_infrastructure_text(job.reply):
                    job.reply=''
                    return self._fail(job,'celia:output_suppressed')
                voices.AUDIO_DIR.mkdir(parents=True,exist_ok=True)
                path=voices.AUDIO_DIR/('celia-'+job.id+'.wav')
                await asyncio.to_thread(shutil.copyfile,ready['audio_paths'][0],path)
                audio={'path':path,'voice':'celia-omnivoice-original',**voices.wav_info(path)}
                self._enqueue_idle(anticipate=True)
                self._prime_buffer()
                await self._play_audio(job,audio,t0)
                if job.state!='failed':
                    await self._celia_reply.record(job)
                return
            job.state = "speaking"
            self._speaking = {"character": "celia", "job_id": job.id, "since": self._clock(), "author": job.author, "avatar_url": job.avatar_url}
            self._publish_state()
            self._enqueue_idle(anticipate=True)
            result = await self._celia_reply(job)
            job.reply = str(result.get("text") or "")[:400]
            job.speech_id = str(result.get("speech_id") or "")
            from celia.core.dialogue_hygiene import is_infrastructure_text
            if is_infrastructure_text(job.reply):
                job.reply = ""
                return self._fail(job, "celia:output_suppressed")
            if not result.get("ok"):
                return self._fail(job, "celia:" + str(result.get("error") or "unknown"))
            job.timings["total_ms"] = int((time.monotonic() - t0) * 1000)
            return
        # Leitura usa o texto do chat; respostas dirigidas mantêm a persona própria.
        job.state = "generating"
        if job.reason in ("reading", "test", "gift", "idle"):
            reply = filters.limpar_resposta(job.text, cfg["max_chars_read"] + (140 if job.reason == 'reading' else 0))
        else:
            history = list(self._char_history.get(job.character, []))
            try:
                got = await self._llm_reply(job.character, job.author, job.text, max_chars=cfg["reply_max_chars"], history=history)
            except llm.LLMError as exc:
                return self._fail(job, "llm:" + str(exc)[:100])
            job.model = got.get("model", "")
            job.timings["llm_ms"] = int(got.get("latency_ms", 0))
            reply = filters.limpar_resposta(got["text"], cfg["reply_max_chars"])
        block = filters.saida_bloqueada(reply)
        if not reply or block:
            return self._fail(job, "output_blocked:" + (block or "empty"))
        job.reply = reply
        job.state = "synthesizing"
        try:
            audio = await job.preparation if job.preparation is not None else await self._synth(job.character, reply)
        except voices.SynthError as exc:
            return self._fail(job, "tts:" + str(exc)[:100])
        job.voice, job.audio_ms = audio["voice"], int(audio["duration_s"] * 1000)
        job.timings["tts_ms"] = int(audio.get("latency_ms", 0))
        if job.reason in ('reading', 'idle'):
            prepare = getattr(self._celia_reply, 'prepare', None)
            paired = next((j for j in self._queue if j.message_id == job.message_id.removesuffix('-read') and j.character == 'celia'), None)
            if prepare is not None and paired is not None and paired.preparation is None:
                paired.preparation = asyncio.create_task(prepare(paired))
        if job.reason not in ("reading", "test", "gift", "idle"):
            self._char_history[job.character].append({"role": "user", "content": job.author + ": " + job.text})
            self._char_history[job.character].append({"role": "assistant", "content": reply})
        await self._play_audio(job,audio,t0)

    async def _play_audio(self, job: Job, audio: dict[str, Any], t0: float) -> None:
        cfg=self.settings.get()
        reply=job.reply
        job.voice=audio['voice']
        job.audio_ms=int(audio['duration_s']*1000)
        has_client = any(self._client_active(cid, self._clock()) for cid in self._clients)
        if not self._lease_holder() and not has_client:
            return self._fail(job, "no_audio_client")  # sem uma página conectada não existe quem possa retomar
        job.state = "speaking"
        job.speech_id = "live-" + job.id
        self._speaking = {"character": job.character, "job_id": job.id, "since": self._clock(), "author": job.author, "avatar_url": job.avatar_url}
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._acks[job.speech_id] = fut
        self._active_audio_event={"type": "speak", "speech_id": job.speech_id, "character": job.character, "attempt":0,
                    "audio_url": "/bridge/live/audio/" + Path(audio["path"]).name,
                    "volume": cfg["volumes"][job.character], "text": reply, "author": job.author,
                    "duration_ms": job.audio_ms}
        self._emit(self._active_audio_event)
        self._publish_state()
        try:
            # Reserva uma janela curta para a página reclamar o áudio após um reload,
            # mais a duração real e uma margem pequena para o evento ended.
            ack = await asyncio.wait_for(
                fut,
                timeout=8.0 + job.audio_ms / 1000 + cfg["playback_timeout_s"],
            )
        except asyncio.TimeoutError:
            self._emit({"type": "stop"})
            return self._fail(job, "playback_ack_timeout")
        finally:
            self._acks.pop(job.speech_id, None)
            self._active_audio_event=None
        if ack.get("status") != "ended":
            return self._fail(job, "playback_" + str(ack.get("status")))
        job.timings["played_ms"] = int(ack.get("played_ms") or 0)
        job.timings["total_ms"] = int((time.monotonic() - t0) * 1000)

    # ---------------- eventos para o navegador ----------------
    def _emit(self, event: dict[str, Any]) -> None:
        self._seq += 1
        self._events.append({"seq": self._seq, "t": self._clock(), **event})
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self._notify())
        except RuntimeError:
            pass

    async def _notify(self) -> None:
        async with self._cond:
            self._cond.notify_all()

    def _publish_state(self) -> None:
        cfg = self.settings.get()
        self._emit({"type": "state", "mode": self.mode, "seats": self.seats(), "speaking": self._speaking,
                    "characters": cfg["characters"], "volumes": cfg["volumes"]})

    LEASE_ACTIVE_S = 6.0

    def _client_active(self, cid: str, now: float) -> bool:
        # long-poll ocioso conta: quem está ESPERANDO uma resposta do servidor está vivo, mesmo sem novo contato há > 6 s
        return self._waiting.get(cid, 0) > 0 or now - self._clients.get(cid, 0.0) < self.LEASE_ACTIVE_S

    def _lease_holder(self) -> str | None:
        """Retorna apenas um cliente que reivindicou áudio após gesto do navegador."""
        now = self._clock()
        if self._lease_id and self._client_active(self._lease_id, now):
            return self._lease_id
        self._lease_id = None
        return None

    def claim_audio(self, client: str) -> dict[str, Any]:
        """Claim playback only after the browser has unlocked its AudioContext."""
        if client not in self._clients:
            return {"ok": False, "error": "cliente_de_audio_desconhecido", "replayed": False}
        previous = self._lease_id
        self._lease_id = client
        self._clients[client] = self._clock()
        event = self._active_audio_event
        pending = self._acks.get(event["speech_id"]) if event else None
        replayed = False
        if previous != client and event is not None and pending is not None and not pending.done():
            event["attempt"] = event.get("attempt", 0) + 1
            self._emit({"type": "stop"})
            self._emit(event)
            replayed = True
        self._publish_state()
        return {"ok": True, "client": client, "replayed": replayed}

    async def wait_events(self, client_id: str, after: int, wait: float = 15.0) -> dict[str, Any]:
        self._clients[client_id] = self._clock()
        self._first_seen.setdefault(client_id, self._clock())
        for cid in [c for c, s in self._clients.items() if self._clock() - s > 60 and self._waiting.get(c, 0) == 0]:
            self._clients.pop(cid, None)
            self._first_seen.pop(cid, None)

        def pending():
            return [e for e in self._events if e["seq"] > after]
        if not pending() and wait > 0:
            self._waiting[client_id] = self._waiting.get(client_id, 0) + 1
            try:
                async with self._cond:
                    await asyncio.wait_for(self._cond.wait_for(lambda: bool(pending())), timeout=wait)
            except asyncio.TimeoutError:
                pass
            finally:
                self._waiting[client_id] -= 1
        self._clients[client_id] = self._clock()
        lease = self._lease_holder() == client_id
        evs = [e for e in pending() if lease or e["type"] != "speak"]  # só o dono do áudio recebe "speak"
        return {"seq": self._seq, "events": evs, "lease": lease, "mode": self.mode}

    def on_ack(self, data: dict[str, Any]) -> bool:
        event=self._active_audio_event
        if event is not None and data.get('speech_id')==event['speech_id']:
            if int(data.get('attempt') or 0)!=event.get('attempt',0):return False
            if data.get('client') and data['client']!=self._lease_id:return False
        fut = self._acks.get(str(data.get("speech_id") or ""))
        if fut and not fut.done():
            fut.set_result({"status": str(data.get("status") or "ended"), "played_ms": data.get("played_ms")})
            return True
        return False

    # ---------------- status ----------------
    def snapshot(self) -> dict[str, Any]:
        cfg = self.settings.get()
        ledger = self._ledgers.get(ORIGIN_FOR_MODE.get(self.mode, ""))
        return {
            "mode": self.mode, "active": self.active, "room_id": self.room_id,
            "session_started": self.session_started,
            "reader": self.reader.status() if self.reader is not None else {"state": "stopped"},
            "seats": self.seats() if self.active else [],
            "ranking": [{"user_id": d.user_id, "name": d.nickname or d.display_id, "display_id": d.display_id,
                         "avatar_url": d.avatar_url,
                         "total": d.total} for d in (ledger.top(10, now=self._clock()) if ledger else [])],
            "ranking_counters": ledger.counters if ledger else {},
            "speaking": self._speaking,
            "queue": [j.public() for j in self._queue],
            "recent": [j.public() for j in list(self._history)[-12:]],
            "counters": dict(self.counters), "last_error": self.last_error,
            "silence_s": 0.0 if self._speaking else round(max(0,self._clock()-self._last_voice_finished),2),
            "silence_limit_s": cfg['silence_limit_s'],
            "prepared_buffer": [{"id":j.id,"ready":j.preparation.done()} for j in self._queue if j.character=="celia" and j.preparation is not None],
            "audio_clients": sum(1 for cid in self._clients if self._client_active(cid, self._clock())),
            "audio_lease_claimed": self._lease_holder() is not None,
            "settings": cfg, "settings_load_error": self.settings.load_error,
            "isolation": {"mic_and_typed_input_blocked": self.active, "allowed_sources": cfg["allow_sources"]},
        }
