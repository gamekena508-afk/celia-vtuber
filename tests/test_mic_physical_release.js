"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const code = fs.readFileSync("frontend/mic_toggle.js", "utf8");
class Track extends EventTarget {
  constructor() { super(); this.enabled=true; this.readyState="live"; }
  stop() { this.readyState="ended"; }
}
function stream() {
  const t = new Track();
  return { getAudioTracks:()=>[t], getTracks:()=>[t] };
}
class AudioContext {
  constructor() { this.state="running"; }
  createMediaStreamDestination() { return {stream:stream()}; }
  createMediaStreamSource() { return {connect(){},disconnect(){}}; }
  resume() {return Promise.resolve();}
  close() {return Promise.resolve();}
}
class Socket extends EventTarget {
  constructor() {super();this.readyState=0;}
  send() {}
  receive(data) {const e=new Event("message"); e.data=JSON.stringify(data); this.dispatchEvent(e);}
}
const document=new EventTarget();
document.body={appendChild(){}};
document.createElement=()=>({style:{}});
const physical=[];
const navigator={mediaDevices:{getUserMedia:async()=>{const s=stream();physical.push(s);return s;}}};
const renderOnly = process.argv.includes("--pet");
const window={WebSocket:Socket,AudioContext,location:{search:renderOnly?"?pet=1":""},addEventListener(){}};
const ctx=vm.createContext({window,navigator,document,URLSearchParams,console:{log(){},warn(){}},setTimeout:()=>0,clearTimeout(){},setInterval:()=>0});
vm.runInContext(code,ctx);
const socket=new window.WebSocket("ws://localhost/client-ws");
socket.readyState=1;socket.dispatchEvent(new Event("open"));
const flush=()=>new Promise(resolve=>setImmediate(resolve));
(async()=>{
  const logical=await navigator.mediaDevices.getUserMedia({audio:true});
  assert.equal(physical.length,0,"no hardware while authorization unknown");
  socket.receive({type:"microphone-state",enabled:true,revision:1,window_allowed:true});
  await flush();
  if (renderOnly) {
    assert.equal(physical.length,0,"Pet must never access hardware, including legacy backend");
    assert.equal(window.__micToggle.captureStatus().renderOnly,true);
    assert.equal(window.__micToggle.captureStatus().physical,false);
    console.log("PASS: Pet remains display-only even with legacy server authorization");
    return;
  }
  assert.equal(physical.length,1);
  assert.equal(window.__micToggle.captureStatus().physical,true);
  socket.receive({type:"microphone-state",enabled:true,revision:2,window_allowed:false});
  assert.equal(physical[0].getTracks()[0].readyState,"ended");
  assert.equal(window.__micToggle.captureStatus().physical,false);
  assert.equal(logical.getTracks()[0].readyState,"live","VAD logical stream survives handoff");
  socket.receive({type:"microphone-state",enabled:true,revision:3,window_allowed:true});
  await flush();
  assert.equal(physical.length,2,"fallback reacquires hardware");
  socket.receive({type:"microphone-state",enabled:false,revision:4,window_allowed:true});
  assert.equal(physical[1].getTracks()[0].readyState,"ended");
  assert.equal(window.__micToggle.captureStatus().physical,false);
  console.log("PASS: no preauthorization capture, physical release on Discord/other owner, fallback reacquire, manual mute");
})().catch(e=>{console.error(e);process.exitCode=1;});
