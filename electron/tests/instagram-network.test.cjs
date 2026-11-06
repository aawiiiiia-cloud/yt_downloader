'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { InstagramNetwork, publicContexts } = require('../instagram-network.cjs');
const post = require('../fixtures/instagram.json');
function fakeWeb(body = JSON.stringify({ data: post })) {
  const calls = [], debug = new EventEmitter();
  let attached = false;
  Object.assign(debug, {
    isAttached: () => attached, attach() { attached = true; }, detach() { attached = false; },
    async sendCommand(method) { calls.push(method); return { body, base64Encoded: false }; },
  });
  return { debugger: debug, isDestroyed: () => false, calls };
}
async function response(page, url = 'https://www.instagram.com/graphql/query', bytes = 4000, mimeType = 'application/json') {
  await page.message('Network.responseReceived', { requestId: 'fixture', response: { url, status: 200, mimeType } });
  await page.message('Network.loadingFinished', { requestId: 'fixture', encodedDataLength: bytes });
}

test('Instagram API payloads retain only identity-matched public media and strip account state', () => {
  const raw = structuredClone(post); raw.viewer = { password: 'secret-account' }; raw.user.sessionid = 'private-cookie';
  const contexts = publicContexts({ data: raw, account: { token: 'secret-token' } });
  assert.equal(contexts.length, 1);
  assert.equal(contexts[0].instagram.shortcode, post.code);
  assert.ok(!JSON.stringify(contexts).includes('secret') && !JSON.stringify(contexts).includes('private-cookie'));
  assert.equal(publicContexts({ ...raw, pk: '123' }).some(row => row.instagram.shortcode === post.code), false);
});

test('Instagram web GraphQL media and mixed carousels normalize to the same public schema', () => {
  const node = {
    id: post.pk, shortcode: post.code, owner: { username: 'fixture', sessionid: 'hidden' },
    edge_sidecar_to_children: { edges: [
      { node: { id: '201', display_url: 'https://fixture.cdninstagram.com/image.jpg', dimensions: { width: 640, height: 800 } } },
      { node: { id: '202', is_video: true, display_url: 'https://fixture.cdninstagram.com/cover.jpg',
        video_url: 'https://fixture.cdninstagram.com/video.mp4?signature=unchanged', video_duration: 12.5,
        dimensions: { width: 720, height: 1280 } } },
    ] },
  };
  const [context] = publicContexts({ data: { xdt_shortcode_media: node } });
  assert.equal(context.instagram.post.carousel_media.length, 2);
  assert.equal(context.instagram.post.carousel_media[1].video_duration, 12.5);
  assert.equal(context.instagram.post.carousel_media[1].video_versions[0].url, node.edge_sidecar_to_children.edges[1].node.video_url);
  assert.ok(!JSON.stringify(context).includes('hidden'));
});

test('Instagram observation accepts only bounded official API JSON and never changes existing debuggers', async () => {
  const web = fakeWeb(), page = new InstagramNetwork(web), url = `https://www.instagram.com/p/${post.code}/`;
  await response(page);
  assert.equal(page.get(url).instagram.shortcode, post.code);
  assert.equal(page.get('https://www.instagram.com/p/DfFPRM6n0u9/'), null);
  page.reset();
  for (const rejected of ['https://www.instagram.com.evil.test/graphql/query', 'https://www.instagram.com/accounts/login/',
    'http://www.instagram.com/graphql/query', 'https://user@www.instagram.com/graphql/query']) {
    await response(page, rejected);
  }
  await response(page, undefined, 5 * 1024 * 1024);
  assert.equal(page.get(url), null);
  assert.equal(web.calls.filter(method => method === 'Network.getResponseBody').length, 1);
  page.dispose(); assert.equal(web.debugger.isAttached(), false);
  const existing = fakeWeb(); existing.debugger.attach();
  const borrowed = new InstagramNetwork(existing); borrowed.dispose();
  assert.equal(existing.debugger.isAttached(), true); assert.deepEqual(existing.calls, []);
});

test('Instagram late API responses cannot refill a replaced document and expired contexts are discarded', async () => {
  const web = fakeWeb(), page = new InstagramNetwork(web);
  let resolve;
  web.debugger.sendCommand = async () => new Promise(done => { resolve = done; });
  await page.message('Network.responseReceived', { requestId: 'late', response: {
    url: 'https://www.instagram.com/api/graphql', status: 200, mimeType: 'application/json',
  } });
  const pending = page.message('Network.loadingFinished', { requestId: 'late', encodedDataLength: 4000 });
  page.reset(); resolve({ body: JSON.stringify(post), base64Encoded: false }); await pending;
  const url = `https://www.instagram.com/p/${post.code}/`;
  assert.equal(page.get(url), null);
  page.remember(post); page.cache.get(post.code).at -= 300001;
  assert.equal(page.get(url), null); page.dispose();
});

test('Instagram JavaScript-labelled API bodies are parsed as JSON and never executed', async () => {
  for (const mime of ['text/javascript', 'application/javascript', 'application/x-javascript']) {
    const web = fakeWeb('for (;;);' + JSON.stringify({ data: post })), page = new InstagramNetwork(web);
    await response(page, 'https://www.instagram.com/api/graphql', 4000, mime);
    assert.equal(page.get(`https://www.instagram.com/p/${post.code}/`).instagram.shortcode, post.code);
    page.reset();
    await response(page, undefined, 4000, 'text/html');
    assert.equal(page.cache.size, 0);
    page.dispose();
  }
  const web = fakeWeb('globalThis.__instagramNetworkExecuted = true'), page = new InstagramNetwork(web);
  await assert.rejects(() => response(page, undefined, 4000, 'text/javascript'), SyntaxError);
  assert.equal(globalThis.__instagramNetworkExecuted, undefined);
  assert.equal(page.cache.size, 0);
  page.dispose();
});
