const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = process.env.FLOW_PROXY_ROOT || path.join(process.env.CELIA_PROXIES_DIR || path.join(require('node:os').homedir(), 'Documents', 'Automacoes', 'Proxies'), 'FLOW-v1.5.3');
const source = fs.readFileSync(path.join(root, 'background/service-worker.js'), 'utf8');
function extract(name) {
  const start = source.indexOf(`function ${name}(`);
  assert(start >= 0);
  return source.slice(start, source.indexOf('\n}', start) + 2);
}
const sandbox = { chrome: { tabs: { sendMessage: async (_id, msg) => msg.type === 'FLOW_API_LIST_MEDIA'
  ? {ok:true,media:[]} : {ok:true,media:[{src:'https://flow-content.google/image/id?Signature=changed',key:'stable-id',alt:'Bloco mostrando a imagem de um usuário',name:'reference_1.png',width:1122}]} } } };
vm.createContext(sandbox);
vm.runInContext(extract('flowMediaKey') + '\n' + 'async ' + extract('getFlowMediaSnapshot') + '\n' + extract('isReferenceMedia'), sandbox);
(async () => {
  assert.equal(sandbox.isReferenceMedia({alt:'Bloco mostrando a imagem de um usuário',name:'reference_1.png'}), true);
  assert.equal(sandbox.isReferenceMedia({alt:'Bloco mostrando a imagem de um usuário',name:'Adding red apple to image'}), false);
  const media = await sandbox.getFlowMediaSnapshot(1);
  assert.equal(media[0].key, 'dom:stable-id');
  assert.equal(sandbox.isReferenceMedia(media[0]), true);
  console.log('PASS: reference tile excluded; generated tile retained; media identity ignores signed URL changes');
})();
