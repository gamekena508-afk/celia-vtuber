'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const bundlePath = process.env.AUDIO_BUNDLE || path.join(root, 'web/assets/main-nu7uwxNJ.js');
const bundle = fs.readFileSync(bundlePath, 'utf8');
const starts = [], finished = [], wire = [];
let state = 'idle', concurrent = 0, peak = 0;
class Socket extends EventTarget {
  static OPEN = 1;
  constructor() { super(); this.readyState = 1; }
  send(data) { wire.push(JSON.parse(data)); }
}
class Media extends EventTarget {
  constructor(src) { super(); this.src = src; this.playing = false; }
  load() { if (this.src) queueMicrotask(() => this.dispatchEvent(new Event('canplaythrough'))); }
  play() {
    if (this.src.includes('FAIL')) return Promise.reject(new Error('media failure'));
    if (!this.playing) {
      this.playing = true; concurrent++; peak = Math.max(peak, concurrent); starts.push(this.src);
      this.timer = setTimeout(() => {
        this.playing = false; concurrent--; finished.push(this.src); this.dispatchEvent(new Event('ended'));
      }, this.src.includes('LONG') ? 100 : 8);
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
const audioManager = {current: null, setCurrentAudio(a) {this.current = a;},
  clearCurrentAudio(a) {if(this.current === a) this.current = null;},
  hasCurrentAudio() {return this.current !== null;}, stopCurrentAudioAndLipSync() {this.current?.pause(); this.current=null;}};
const ctx = vm.createContext({window, console: {log(){}, debug(){}, warn(){}, error(){}},
  Event, DOMException, queueMicrotask, setTimeout: (fn, ms) => { const t=setTimeout(fn,ms); if(ms>1000)t.unref(); return t; },
  clearTimeout, setInterval: (fn,ms) => {const t=setInterval(fn,ms);t.unref();return t;}, clearInterval,
  xt: (obj,key,val) => obj[key]=val, audioManager, LAppDefine: {}, PriorityNormal: 1,
  useTranslation: () => ({t: x=>x}), useAiState: () => ({aiState:state, backendSynthComplete:false,setBackendSynthComplete(){}}),
  useSubtitle: () => ({setSubtitleText(){}}), useChatHistory: () => ({appendResponse(){},appendAIMessage(){}}),
  useWebSocket: () => ({sendMessage(){}}), useLive2DExpression: () => ({setExpression(){}}),
  reactExports: {useRef: x=>({current:x}), useCallback:x=>x, useEffect(){}}, toaster:{create(){}}});
vm.runInContext(fs.readFileSync(path.join(root,'frontend/external-playback-bridge.js'),'utf8'),ctx);
ctx.Audio = window.Audio;
const qStart=bundle.indexOf('class TaskQueue{'), qEnd=bundle.indexOf(',ScreenCaptureContext=',qStart);
vm.runInContext(bundle.slice(qStart,qEnd)+';',ctx);
const hookStart=bundle.indexOf('useAudioTask=()=>'), hookEnd=bundle.indexOf(',useInterrupt=',hookStart);
vm.runInContext('const '+bundle.slice(hookStart,hookEnd)+';globalThis.hook=useAudioTask();',ctx);
const handlerStart=bundle.indexOf('case"audio":', bundle.indexOf('case"background-files":'));
const handlerEnd=bundle.indexOf('case"history-data":',handlerStart);
const handler=bundle.slice(handlerStart,handlerEnd);
const socket = new window.WebSocket();
function setState(s) {state=s;window.celiaPlayback.setState(s);}
function receive(id, audio=id) {
  const payload={type:'audio',speech_id:id,audio};
  const event=new Event('message');event.data=JSON.stringify(payload);socket.dispatchEvent(event);
  Object.assign(ctx,{Gt:payload,st:state,ct:ctx.hook.addAudioTask,Qt:null,tr:null});
  vm.runInContext('switch(Gt.type){'+handler+'}',ctx);
}
function cancel(id) {const event=new Event('message'); event.data=JSON.stringify({type:'playback.cancel',speech_id:id});socket.dispatchEvent(event);}
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
async function drain() {await vm.runInContext('audioTaskQueue.waitForCompletion()',ctx);}
function count(event,id){return window.celiaPlayback.events.filter(r=>r.event===event&&r.speech_id===id).length;}
(async()=>{
 setState('idle'); receive('idle'); await drain(); assert.equal(count('AUDIO_FINISHED','idle'),1);
 setState('listening'); receive('during'); await sleep(30); assert.equal(count('AUDIO_STARTED','during'),0); assert.equal(count('AUDIO_DEFERRED','during'),1);
 setState('idle'); await drain(); assert.equal(count('AUDIO_FINISHED','during'),1);
 setState('listening'); setState('idle'); receive('after'); await drain(); assert.equal(count('AUDIO_FINISHED','after'),1);
 setState('listening'); receive('cancelled'); cancel('cancelled'); setState('idle'); await drain(); assert.equal(count('AUDIO_STARTED','cancelled'),0);
 cancel('stale'); receive('stale'); await drain(); assert.equal(count('AUDIO_STARTED','stale'),0); assert.equal(count('AUDIO_DROPPED','stale'),1);
 receive('repeat1','SAME'); receive('repeat2','SAME'); await drain(); assert.equal(count('AUDIO_FINISHED','repeat1'),1); assert.equal(count('AUDIO_FINISHED','repeat2'),1);
 receive('short','SHORT');receive('long','LONG');await drain();assert.equal(count('AUDIO_FINISHED','short'),1);assert.equal(count('AUDIO_FINISHED','long'),1);
 receive('dup');receive('dup');await drain();assert.equal(count('AUDIO_STARTED','dup'),1);
 setState('interrupted'); receive('fresh-after-interrupt');await drain();assert.equal(count('AUDIO_FINISHED','fresh-after-interrupt'),1);
 setState('idle');receive('cancel-active','LONG');await sleep(10);cancel('cancel-active');receive('after-cancel');await drain();assert.equal(count('AUDIO_FINISHED','cancel-active'),0);assert.equal(count('AUDIO_FINISHED','after-cancel'),1);
 receive('failed','FAIL');receive('after-failure');await drain();assert.equal(count('AUDIO_FINISHED','failed'),0);assert.equal(count('AUDIO_DROPPED','failed'),1);assert.equal(count('AUDIO_FINISHED','after-failure'),1);
 setState('idle');receive('late-listening');await Promise.resolve();setState('listening');await sleep(30);assert.equal(count('AUDIO_STARTED','late-listening'),0);setState('idle');await drain();assert.equal(count('AUDIO_FINISHED','late-listening'),1);
 setState('listening');receive('cancel-all1');receive('cancel-all2');window.celiaPlayback.cancelAll();setState('idle');receive('after-cancel-all');await drain();assert.equal(count('AUDIO_STARTED','cancel-all1'),0);assert.equal(count('AUDIO_STARTED','cancel-all2'),0);assert.equal(count('AUDIO_FINISHED','after-cancel-all'),1);
 setState('listening');for(let i=0;i<30;i++)receive('stress-'+i,'SAME');await sleep(20);setState('idle');await drain();
 for(let i=0;i<30;i++) assert.equal(count('AUDIO_FINISHED','stress-'+i),1);
 assert.equal(peak,1,'No overlapping playback');
 assert.ok(wire.some(r=>r.type==='audio-lifecycle'&&r.event==='AUDIO_FINISHED'));
 console.log(JSON.stringify({status:'PASS',proof:'automated; real bundle receiver + queue + hook, mocked media',cases:14,stress_finished:30,peak_concurrent:peak,events:window.celiaPlayback.events},null,2));
})().catch(e=>{console.error(e);process.exitCode=1;});
