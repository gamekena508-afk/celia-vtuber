'use strict';
// Faixa única de voz: uma fala nunca pisa em outra, e nada some sem estado terminal.
//
// A bancada carrega a TaskQueue, o hook useAudioTask e o tratamento de
// "conversation-chain-start" REAIS do bundle servido (web/assets). Só a mídia
// (Audio) é simulada. Os cenários A/B/C reproduzem os traços medidos no log de
// 25/09 (var/diagnostics/fila/): o bundle chama audioTaskQueue.clearQueue() no
// início de todo turno, zerando `running` com o áudio anterior ainda tocando.
//   A = 20:29:14-20:29:20  6530 toca, 4791 na fila, chain-start, 153d entra por cima
//   B = 20:38:18-20:38:29  a49a toca, 73ae e a73a entram por cima (3 vozes)
//   C = 20:40:57-20:41:01  humano já falava quando a fala dela começou; o turno
//       dele foi aceito 1 s depois e cortou a fala com ~1 s tocado
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const bundlePath = process.env.AUDIO_BUNDLE || path.join(root, 'web/assets/main-nu7uwxNJ.js');
const bundle = fs.readFileSync(bundlePath, 'utf8');

const DUR = {LONG: 100, MED: 40};      // ms de "áudio" simulado
let concurrent = 0, peak = 0;
const wire = [];
class Socket extends EventTarget {
  static OPEN = 1;
  constructor() { super(); this.readyState = 1; }
  send(data) { wire.push(JSON.parse(data)); }
}
class Media extends EventTarget {
  constructor(src) { super(); this.src = src; this.playing = false; }
  load() { if (this.src) queueMicrotask(() => this.dispatchEvent(new Event('canplaythrough'))); }
  play() {
    if (!this.playing) {
      this.playing = true; concurrent++; peak = Math.max(peak, concurrent);
      const ms = DUR[(/(LONG|MED)\d*$/.exec(this.src) || [])[1]] || 8;   // src termina em LONG1, MED2...
      this.timer = setTimeout(() => { this.playing = false; concurrent--; this.dispatchEvent(new Event('ended')); }, ms);
    }
    return Promise.resolve();
  }
  pause() { if (this.playing) { concurrent--; this.playing = false; clearTimeout(this.timer); } }
  removeAttribute(name) { if (name === 'src') this.src = ''; }
}
const window = new EventTarget();
Object.assign(window, {WebSocket: Socket, Audio: Media, Event,
  CustomEvent: class extends Event { constructor(type, init) { super(type); this.detail = init.detail; } },
  getLive2DManager: () => ({getModel: () => ({startRandomMotion() {}})})});
let aiState = 'idle';
const audioManager = {current: null, setCurrentAudio(a) {this.current = a;},
  clearCurrentAudio(a) {if (this.current === a) this.current = null;},
  hasCurrentAudio() {return this.current !== null;}, stopCurrentAudioAndLipSync() {this.current?.pause(); this.current = null;}};
const ctx = vm.createContext({window, console: {log() {}, debug() {}, warn() {}, error() {}},
  Event, DOMException, queueMicrotask,
  setTimeout: (fn, ms) => { const t = setTimeout(fn, ms); if (ms > 1000) t.unref(); return t; },
  clearTimeout, setInterval: (fn, ms) => {const t = setInterval(fn, ms); t.unref(); return t;}, clearInterval,
  xt: (obj, key, val) => obj[key] = val, audioManager, LAppDefine: {}, PriorityNormal: 1,
  useTranslation: () => ({t: x => x}), useAiState: () => ({aiState, backendSynthComplete: false, setBackendSynthComplete() {}}),
  useSubtitle: () => ({setSubtitleText() {}}), useChatHistory: () => ({appendResponse() {}, appendAIMessage() {}}),
  useWebSocket: () => ({sendMessage() {}}), useLive2DExpression: () => ({setExpression() {}}),
  reactExports: {useRef: x => ({current: x}), useCallback: x => x, useEffect() {}}, toaster: {create() {}}});
vm.runInContext(fs.readFileSync(process.env.SPEECH_BRIDGE || path.join(root, 'frontend/external-playback-bridge.js'), 'utf8'), ctx);
ctx.Audio = window.Audio;
const qStart = bundle.indexOf('class TaskQueue{'), qEnd = bundle.indexOf(',ScreenCaptureContext=', qStart);
vm.runInContext(bundle.slice(qStart, qEnd) + ';', ctx);
const hookStart = bundle.indexOf('useAudioTask=()=>'), hookEnd = bundle.indexOf(',useInterrupt=', hookStart);
vm.runInContext('const ' + bundle.slice(hookStart, hookEnd) + ';globalThis.hook=useAudioTask();', ctx);
const audioCase = bundle.slice(bundle.indexOf('case"audio":', bundle.indexOf('case"background-files":')),
  bundle.indexOf('case"history-data":', bundle.indexOf('case"audio":', bundle.indexOf('case"background-files":'))));
const chainStart = bundle.indexOf('case"conversation-chain-start":');
const chainCase = bundle.slice(chainStart, bundle.indexOf('break;', chainStart) + 6);
assert.match(chainCase, /audioTaskQueue\.clearQueue\(\)/, 'o bundle mudou: reavaliar a bancada');
const socket = new window.WebSocket();
const emit = payload => { const e = new Event('message'); e.data = JSON.stringify(payload); socket.dispatchEvent(e); };
const sleep = ms => new Promise(r => setTimeout(r, ms));
const events = () => window.celiaPlayback.events;
const first = (event, id) => events().findIndex(r => r.event === event && r.speech_id === id);
const has = (event, id) => first(event, id) >= 0;
const reasonOf = (event, id) => events().find(r => r.event === event && r.speech_id === id)?.reason;

function setState(s) { aiState = s; window.celiaPlayback.setState(s); }
function receive(id, audio) {                       // igual ao receptor real: ponte primeiro, bundle depois
  const payload = {type: 'audio', speech_id: id, audio: audio || id};
  emit(payload);
  Object.assign(ctx, {Gt: payload, st: aiState, ct: ctx.hook.addAudioTask, Qt: null, tr: null});
  vm.runInContext('switch(Gt.type){' + audioCase + '}', ctx);
}
function newTurn() {                                // conversation-chain-start real do bundle
  emit({type: 'control', text: 'conversation-chain-start'});
  Object.assign(ctx, {Gt: 'conversation-chain-start', it: () => setState('thinking-speaking'), ot: () => {}});
  vm.runInContext('switch(Gt){' + chainCase + '}', ctx);
}
function scenario() { peak = concurrent; }          // mede o pico só do cenário corrente

let ran = 0, failed = 0;
async function run(name, fn) {
  scenario();
  setState('idle');
  try {
    await fn();
    ran += 1;
    console.log('ok   ' + name);
  } catch (e) {
    failed += 1;
    console.log('FAIL ' + name + '\n       -> ' + String(e.message).split('\n')[0] + ' (pico=' + peak + ')');
  }
  await sleep(260);                                 // esvazia o que sobrou antes do próximo cenário
}

(async () => {
  Object.assign(window.celiaPlayback.config || {}, {claimTimeoutMs: 60, humanHoldMs: 80});

  await run('A: turno novo com áudio tocando (20:29) - nada por cima, irmã não vira zumbi', async () => {
    receive('A-6530', 'LONG1'); await sleep(20);
    assert.ok(has('AUDIO_STARTED', 'A-6530'));
    receive('A-4791', 'LONG2');                     // na fila, atrás da que toca
    newTurn();                                      // o bundle zera a fila aqui
    receive('A-153d', 'LONG3');                     // chega do turno novo
    await sleep(320);
    assert.equal(peak, 1, 'duas vozes ao mesmo tempo');
    assert.ok(has('AUDIO_FINISHED', 'A-6530'), 'a fala que tocava termina inteira');
    assert.ok(!has('AUDIO_STARTED', 'A-4791'));
    assert.ok(has('AUDIO_CANCELLED', 'A-4791'), 'fala apagada da fila precisa de estado terminal');
    assert.equal(reasonOf('AUDIO_CANCELLED', 'A-4791'), 'new_turn');
    assert.ok(wire.some(m => m.type === 'frontend-playback-cancelled' && m.speech_id === 'A-4791'),
      'o servidor precisa ser avisado do cancelamento');
    assert.ok(has('AUDIO_FINISHED', 'A-153d'));
    assert.ok(first('AUDIO_FINISHED', 'A-6530') < first('AUDIO_STARTED', 'A-153d'), 'começou antes da outra acabar');
  });

  await run('B: dois turnos novos em sequência (20:38) - nunca três vozes', async () => {
    receive('B-a49a', 'LONG1'); await sleep(20);
    newTurn();
    receive('B-73ae', 'MED2');                      // espera a que toca
    await sleep(15);
    newTurn();                                      // o turno dele foi aceito: 73ae ficou velha
    receive('B-a73a', 'MED3'); receive('B-7c94', 'MED4');
    await sleep(420);
    assert.equal(peak, 1, 'mais de uma voz ao mesmo tempo');
    assert.ok(!has('AUDIO_STARTED', 'B-73ae') && has('AUDIO_CANCELLED', 'B-73ae'), 'a velha some com motivo');
    for (const id of ['B-a49a', 'B-a73a', 'B-7c94']) assert.ok(has('AUDIO_FINISHED', id), id + ' devia terminar');
    assert.ok(first('AUDIO_STARTED', 'B-a73a') < first('AUDIO_STARTED', 'B-7c94'), 'ordem de chegada (FIFO)');
    assert.ok(first('AUDIO_FINISHED', 'B-a49a') < first('AUDIO_STARTED', 'B-a73a'));
  });

  await run('C: humano em voo (20:41) - ela não começa e o turno dele a torna obsoleta', async () => {
    emit({type: 'speech.started', source: 'microphone', utterance_id: 'u1', profile: 'chat'});
    receive('C-d77f', 'MED1'); receive('C-fa5d', 'MED2');
    await sleep(30);
    assert.ok(!has('AUDIO_STARTED', 'C-d77f'), 'começou a falar por cima do humano');
    assert.equal(reasonOf('AUDIO_DEFERRED', 'C-d77f'), 'human_in_flight');
    newTurn();                                      // turno humano aceito
    await sleep(30);
    assert.ok(!has('AUDIO_STARTED', 'C-d77f') && !has('AUDIO_STARTED', 'C-fa5d'), 'fala obsoleta tocou');
    assert.ok(has('AUDIO_CANCELLED', 'C-d77f') && has('AUDIO_CANCELLED', 'C-fa5d'));
    receive('C-nova', 'MED3'); await sleep(90);
    assert.ok(has('AUDIO_FINISHED', 'C-nova'), 'a resposta do turno novo toca normalmente');
  });

  await run('C2: turno rejeitado (ruído) - a espera vence e ela fala', async () => {
    emit({type: 'speech.started', source: 'microphone', utterance_id: 'u2', profile: 'chat'});
    receive('C2-x', 'MED1'); await sleep(30);
    assert.ok(!has('AUDIO_STARTED', 'C2-x'));
    await sleep(120);                               // humanHoldMs = 80
    assert.ok(has('AUDIO_STARTED', 'C2-x'), 'a espera precisa ter fim');
  });

  await run('C3: roteiro e servidor antigo não seguram a fala', async () => {
    emit({type: 'speech.started', source: 'microphone', utterance_id: 'u3', profile: 'roteiro'});
    receive('C3-roteiro', 'MED1'); await sleep(30);
    assert.ok(has('AUDIO_STARTED', 'C3-roteiro'), 'roteiro nunca espera humano');
    await sleep(80);
    emit({type: 'speech.started', source: 'microphone', utterance_id: 'u4'});   // sem campo profile
    receive('C3-antigo', 'MED2'); await sleep(30);
    assert.ok(has('AUDIO_STARTED', 'C3-antigo'), 'sem o campo profile o comportamento é o de antes');
  });

  await run('D: quem recebeu a voz e nunca tocou não trava a faixa', async () => {
    assert.equal(await window.celiaPlayback.waitForPlayable('D-fantasma'), true);   // pega a voz e some
    receive('D-depois', 'MED1'); await sleep(30);
    assert.ok(!has('AUDIO_STARTED', 'D-depois'));
    await sleep(100);                               // claimTimeoutMs = 60
    assert.ok(has('AUDIO_STARTED', 'D-depois'));
    assert.equal((reasonOf('AUDIO_CANCELLED', 'D-fantasma') || reasonOf('AUDIO_DROPPED', 'D-fantasma')), 'lane_claim_timeout');
    assert.equal(await window.celiaPlayback.waitForPlayable('D-fantasma'), false, 'fala expirada não pode recuperar a voz e tocar tarde');
  });

  await run('G: dono segurado de propósito (humano em voo) não perde a faixa pelo timeout', async () => {
    window.celiaPlayback.config.humanHoldMs = 200;
    receive('G-dono', 'MED1');                      // recebe a voz...
    emit({type: 'speech.started', source: 'microphone', utterance_id: 'ug', profile: 'chat'});   // ...e o humano começa antes do play()
    receive('G-fila', 'MED2');
    await sleep(150);                               // claimTimeoutMs = 60 já passou
    assert.ok(!has('AUDIO_STARTED', 'G-dono') && !has('AUDIO_STARTED', 'G-fila'), 'tocou com o humano em voo');
    assert.ok(!has('AUDIO_DROPPED', 'G-dono'), 'segurado de propósito não é fantasma');
    await sleep(300);
    assert.ok(has('AUDIO_FINISHED', 'G-dono') && has('AUDIO_FINISHED', 'G-fila'));
    assert.ok(first('AUDIO_FINISHED', 'G-dono') < first('AUDIO_STARTED', 'G-fila'));
    assert.equal(peak, 1);
    window.celiaPlayback.config.humanHoldMs = 80;
  });

  await run('E: rajada em ordem de chegada, uma por vez', async () => {
    for (let i = 0; i < 5; i++) receive('E-' + i, 'MED' + i);
    await sleep(320);
    assert.equal(peak, 1);
    for (let i = 0; i < 4; i++) assert.ok(first('AUDIO_STARTED', 'E-' + i) < first('AUDIO_STARTED', 'E-' + (i + 1)));
    for (let i = 0; i < 5; i++) assert.ok(has('AUDIO_FINISHED', 'E-' + i));
  });

  await run('F: cancelar quem toca libera a próxima', async () => {
    receive('F-1', 'LONG1'); receive('F-2', 'MED2'); await sleep(20);
    emit({type: 'playback.cancel', speech_id: 'F-1'});
    await sleep(80);
    assert.ok(has('AUDIO_FINISHED', 'F-2'));
    assert.equal(peak, 1);
  });

  try {
    const snap = window.celiaPlayback.snapshot();
    const resto = JSON.stringify({owner: snap.owner, waiters: snap.waiters, hold: snap.hold});
    assert.equal(snap.waiters.length, 0, 'nada pendurado na faixa ao final: ' + resto);   // (array vem do vm: sem deepEqual)
    assert.equal(snap.owner, '', 'faixa presa: ' + resto);
  } catch (e) { failed += 1; console.log('FAIL fim: ' + String(e.message).split('\n')[0]); }
  console.log((failed ? 'REPROVADO' : 'PASS') + ': ' + ran + ' ok, ' + failed + ' falharam');
  process.exitCode = failed ? 1 : 0;
})().catch(e => { console.error(e); process.exitCode = 1; });
