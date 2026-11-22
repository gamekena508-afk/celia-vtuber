'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const code = fs.readFileSync(process.env.MIC_SCRIPT || 'frontend/mic_toggle.js', 'utf8');
const clients = [];
let enabled = true, revision = 0;
const delivered = [];
class Socket extends EventTarget {
  static OPEN = 1;
  constructor() { super(); this.readyState = 0; clients.push(this); }
  send(raw) {
    const msg = JSON.parse(raw);
    if (msg.type === 'microphone-state-set') {
      if (enabled !== msg.enabled) { enabled = msg.enabled; revision++; }
      for (const s of clients.filter(s => s.readyState === 1)) s.receive({type:'microphone-state', enabled, revision});
    } else if (msg.type === 'microphone-state-get') {
      this.receive({type:'microphone-state', enabled, revision});
    } else delivered.push(msg);
  }
  receive(data) { const e = new Event('message'); e.data = JSON.stringify(data); this.dispatchEvent(e); }
  open() { this.readyState=1; this.dispatchEvent(new Event('open')); }
  close() { this.readyState=3; this.dispatchEvent(new Event('close')); }
}
function surface() {
  const document = new EventTarget();
  document.body={appendChild() {}};
  document.createElement=()=>({style:{},textContent:''});
  const tracks=[];
  const navigator={mediaDevices:{getUserMedia:async()=> {
    const track = new EventTarget(); track.enabled=true; tracks.push(track);
    return {getAudioTracks:()=>[track]};
  }}};
  const window={WebSocket:Socket};
  const ctx=vm.createContext({document,navigator,window,console:{log(){},warn(){}},
    setTimeout:()=>0,clearTimeout(){}});
  vm.runInContext(code,ctx);
  const socket=new window.WebSocket('ws://localhost/client-ws?role=browser');
  socket.open();
  const key=(repeat=false)=>{const e=new Event('keydown');Object.assign(e,{key:'m',ctrlKey:true,repeat});document.dispatchEvent(e);};
  return {window,navigator,tracks,socket,key};
}
(async()=>{
  const ui=surface(),pet=surface();
  await ui.navigator.mediaDevices.getUserMedia({audio:true});
  await pet.navigator.mediaDevices.getUserMedia({audio:true});
  assert.equal(pet.tracks[0].enabled,true);
  ui.key();
  assert.equal(ui.tracks[0].enabled,false);
  assert.equal(pet.tracks[0].enabled,false,'Ctrl+M na interface precisa mutar a track do Pet');
  pet.socket.send(JSON.stringify({type:'mic-audio-data',audio:[1]}));
  assert.equal(delivered.length,0,'áudio não deve sair enquanto mudo');
  pet.socket.send(JSON.stringify({type:'text-input',text:'continua permitido'}));
  assert.equal(delivered.length,1);
  const reopened=surface();
  await reopened.navigator.mediaDevices.getUserMedia({audio:true});
  assert.equal(reopened.tracks[0].enabled,false,'Pet novo herda mute');
  await pet.navigator.mediaDevices.getUserMedia({audio:true});
  assert.equal(pet.tracks[1].enabled,false,'nova track herda mute');
  pet.key();
  assert.equal(ui.tracks[0].enabled,true,'desmutar no Pet sincroniza interface');
  assert.equal(reopened.tracks[0].enabled,true);
  ui.key(true);
  assert.equal(ui.tracks[0].enabled,true,'key repeat não alterna');
  pet.socket.receive({type:'microphone-state',enabled:false,revision:0});
  assert.equal(pet.tracks[0].enabled,true,'snapshot atrasado ignorado');
  // Separate surface with no remaining open sockets.
  for (const s of clients) s.close();
  assert.equal(ui.tracks[0].enabled,false,'desconexão fecha mic local');
  console.log('PASS: mute UI -> Pet, reverse, reconnect, new track, no audio, text allowed, repeat, stale state, disconnect');
})().catch(e=>{console.error(e);process.exitCode=1;});
