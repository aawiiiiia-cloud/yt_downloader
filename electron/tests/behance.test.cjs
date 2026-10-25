'use strict';
const test = require('node:test'), assert = require('node:assert/strict'), vm = require('node:vm');
const { contentKey, siteForUrl, readCookies, cookieText, hasAuth } = require('../model.cjs');
const { SiteTabs } = require('../site-tabs.cjs');
const { assetUrl } = require('../assets.cjs');
const { snapshotScript } = require('../behance-page.cjs');
const project = require('../fixtures/behance.json');
test('Behance project identity ignores titles/tracking and excludes non-project pages', () => {
  assert.equal(siteForUrl('https://www.behance.net/'), 'behance');
  assert.equal(contentKey('https://www.behance.net/gallery/123/Title?tracking_source=search'), 'behance:123');
  assert.equal(contentKey('https://behance.net/gallery/123/New-title'), 'behance:123');
  for (const url of ['https://www.behance.net/artist','https://www.behance.net/collection/123','https://behance.net.evil.invalid/gallery/123/a']) assert.equal(contentKey(url), null);
  const tabs = new SiteTabs(); const tab = tabs.add('behance','https://www.behance.net/gallery/123/Title');tabs.select(tab.id);
  const restored = new SiteTabs(tabs.serialize());
  assert.equal(restored.find('https://behance.net/gallery/123/Other').id,tab.id);
  assert.equal(restored.switchGroup('behance').id,tab.id);
});
test('Behance page snapshot excludes account data and rejects stale project identities', () => {
  const run = (id, data) => vm.runInNewContext(snapshotScript, {location:{pathname:'/gallery/'+id+'/a'},document:{getElementById:()=>({textContent:JSON.stringify(data)})}});
  const data = {project:{project},user:{secretToken:'do-not-copy'}};
  const result = run(123,data);
  assert.equal(result.projectId,'123'); assert.equal(result.project.modules.length,3);
  assert.doesNotMatch(JSON.stringify(result),/secretToken|do-not-copy/);
  assert.equal(run(456,data),null);
  assert.equal(run(123,{project:{project:{...project,modules:null}}}),null);
});
test('Behance CDN previews are allowlisted and anonymous cookies never imply account login', () => {
  assert.equal(assetUrl('http://mir-s3-cdn-cf.behance.net/a.jpg','behance'),'https://mir-s3-cdn-cf.behance.net/a.jpg');
  for (const url of ['https://behance.net.evil.invalid/a','https://user:secret@mir-s3-cdn-cf.behance.net/a','http://127.0.0.1/private']) assert.throws(()=>assetUrl(url,'behance'));
  const cookies = [{domain:'.behance.net',name:'bcp',value:'anonymous',secure:true},{domain:'.youtube.com',name:'SID',value:'unrelated'}];
  assert.equal(hasAuth(cookies,'behance'),false);
  const rows=readCookies(cookieText(cookies,'behance'),'behance');
  assert.equal(rows.length,1);assert.equal(rows[0].name,'bcp');
});
