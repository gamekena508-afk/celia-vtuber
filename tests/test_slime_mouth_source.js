'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('frontend/celia-corpo-config.js', 'utf8');
function extract(name) {
  const start = source.indexOf('  function ' + name + '(');
  const end = source.indexOf('\n  }', start) + 4;
  assert(start > 0 && end > start);
  return source.slice(start, end);
}
const ctx = {estado:{mic:{rms:.06, ativo:true, th:.022, capturando:true, em:100}, remoto:{boca:0}},
  PET:true, performance:{now:()=>100}, window:{__micToggle:{state:()=>true}}};
vm.createContext(ctx);
vm.runInContext(extract('alvoBoca') + '\n' + extract('alvoBocaSlime'),ctx);
const mouth = ()=>vm.runInContext('alvoBocaSlime()',ctx);
assert.equal(mouth(),1, 'Pet ASR microphone must beat silent ordinary browser');
assert.equal(ctx.estado.bocaFonte,'microfone_local');
ctx.estado.remoto.boca=.8; ctx.estado.mic.rms=0; ctx.estado.mic.ativo=false;
assert.equal(mouth(),0,'Local silence must beat stale remote speech');
ctx.estado.mic.capturando=false;
assert.equal(mouth(),.8,'Remote fallback when Pet has no stream');
ctx.estado.mic.capturando=true; ctx.estado.mic.em=-500;
assert.equal(mouth(),.8,'Expired local sample must not override remote');
ctx.window.__micToggle.state=()=>false;
assert.equal(mouth(),0,'Mute must close both local and remote mouth');
ctx.window.__micToggle.state=()=>true; ctx.PET=false; ctx.estado.mic.capturando=false;
assert.equal(mouth(),0,'Inactive browser remains silent');
console.log('PASS: 6 microphone ownership, silence, fallback, expiry and mute cases');
