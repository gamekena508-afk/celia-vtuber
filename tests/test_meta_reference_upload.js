const fs = require('node:fs');
const assert = require('node:assert/strict');
const source = fs.readFileSync(process.env.META_PROXY_SOURCE || require('node:path').join(process.env.CELIA_PROXIES_DIR || require('node:path').join(require('node:os').homedir(), 'Documents', 'Automacoes', 'Proxies'), 'meta-proxy', 'proxy.mjs'), 'utf8');
assert(source.indexOf('await input.fill(prompt)') < source.indexOf('const paths = Array.isArray(imagePaths)'));
assert.equal(source.match(/await input\.fill\(prompt\)/g).length, 1);
const block = source.slice(source.indexOf('  const paths = Array.isArray(imagePaths)'), source.indexOf('\n  const baseline ='));
const AsyncFunction = Object.getPrototypeOf(async function(){}).constructor;
const run = new AsyncFunction('page', 'imagePaths', block);
function fixture(fail) {
  const events=[];
  const page = {
    locator: () => ({first: () => ({click: async () => events.push('open attachment dialog')})}),
    evaluate: async () => ['old-avatar'],
    waitForEvent: () => {events.push('listen for file chooser');return Promise.resolve({setFiles:async paths=>events.push('set '+paths.length+' file')});},
    getByText: () => ({first: () => ({click: async () => events.push('click live upload target')})}),
    waitForFunction: async (_fn, args) => {assert.deepEqual(args,{expected:1,before:['old-avatar']});events.push('confirm new loaded preview');if(fail)throw new Error('no attached preview');},
  };
  return {events,page};
}
(async () => {
  const ok=fixture(false);await run(ok.page,['fixture.png']);
  assert.deepEqual(ok.events,['open attachment dialog','listen for file chooser','click live upload target','set 1 file','confirm new loaded preview']);
  const bad=fixture(true);await assert.rejects(run(bad.page,['fixture.png']),/Falha no upload de referência/);
  console.log('PASS: prompt before attachment; live dialog file chooser; missing preview fails before submission');
})();
