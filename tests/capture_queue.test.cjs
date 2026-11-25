const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');

async function fixture(t, deliver) {
  const { CaptureQueue } = await import('../capture_queue.js');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'buscabusca-capture-'));
  let time = 100000;
  const options = { directory, deliver, now: () => time };
  const queue = new CaptureQueue(options);
  const queues = [queue];
  // Drive retries explicitly without real-time background processing.
  queue.wake = () => {};
  t.after(() => { queues.forEach(q => q.stop()); fs.rmSync(directory, { recursive: true, force: true }); });
  return { queue, directory, advance: ms => { time += ms; }, restart() {
    queue.stop();
    const resumed = new CaptureQueue(options);
    resumed.wake = () => {};
    queues.push(resumed);
    return resumed;
  } };
}
const payload = id => ({ remoteJid: 'group@g.us', participantJid: 'supplier@s.whatsapp.net', messageId: id, timestamp: 1000, text: 'IPHONE 16 128GB PRETO R$ 5000' });

test('a failed panel request survives a process restart and resumes once', async t => {
  let calls = 0;
  const f = await fixture(t, async () => { if (++calls === 1) throw new Error('offline'); });
  f.queue.enqueue(payload('A'));
  await f.queue.run();
  assert.equal(f.queue.status().pending, 1);
  const resumed = f.restart();
  await resumed.run();
  assert.equal(calls, 1);
  f.advance(3001);
  await resumed.run();
  assert.equal(calls, 2);
  assert.equal(resumed.status().pending, 0);
  assert.ok(resumed.status().lastProcessedAt);
});

test('duplicate live and history messages stay deduplicated after restart', async t => {
  let calls = 0;
  const f = await fixture(t, async () => { calls++; });
  assert.equal(f.queue.enqueue(payload('A')), true);
  assert.equal(f.queue.enqueue(payload('A')), false);
  await Promise.all([f.queue.run(), f.queue.run()]);
  const resumed = f.restart();
  assert.equal(resumed.enqueue(payload('A')), false);
  await resumed.run();
  assert.equal(calls, 1);
  const saved = JSON.parse(fs.readFileSync(path.join(f.directory, fs.readdirSync(f.directory)[0])));
  assert.equal(saved.payload, undefined);
});

test('one rejected list remains available for review without blocking the next', async t => {
  const f = await fixture(t, async item => {
    if (item.messageId === 'A') throw Object.assign(new Error('HTTP 413'), { retryable: false });
  });
  f.queue.enqueue(payload('A')); f.queue.enqueue(payload('B'));
  await f.queue.run(); await f.queue.run();
  assert.equal(f.queue.status().failed, 1);
  assert.equal(f.queue.status().pending, 0);
  assert.equal([...f.queue.items.values()].find(i => i.status === 'FAILED').payload.messageId, 'A');
});

test('unavailable panel retains messages beyond five retries', async t => {
  const f = await fixture(t, async () => { throw new Error('offline'); });
  f.queue.enqueue(payload('A'));
  for (let i = 0; i < 8; i++) { await f.queue.run(); f.advance(60001); }
  assert.equal(f.queue.status().pending, 1);
  assert.equal([...f.queue.items.values()][0].attempts, 8);
});

test('corrupt pending records are preserved and reported without stopping healthy work', async t => {
  const f = await fixture(t, async () => {});
  f.queue.enqueue(payload('A'));
  const name = fs.readdirSync(f.directory)[0];
  fs.writeFileSync(path.join(f.directory, name), '{broken');
  const resumed = f.restart();
  assert.equal(resumed.status().failed, 1);
  assert.throws(() => resumed.enqueue(payload('A')), /ilegível/);
  resumed.enqueue(payload('B'));
  await resumed.run();
  assert.equal(resumed.status().pending, 0);
  assert.equal(fs.readFileSync(path.join(f.directory, name), 'utf8'), '{broken');
});

test('delivery times out against a real HTTP server that does not respond', async t => {
  const { deliverCapture } = await import('../capture_queue.js');
  const server = http.createServer(() => {});
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); server.close(); });
  await assert.rejects(deliverCapture(`http://127.0.0.1:${server.address().port}`, payload('A'), { timeoutMs: 50 }), /timeout|abort/i);
});

test('delivery requires actual parse confirmation and classifies HTTP failures', async () => {
  const { deliverCapture } = await import('../capture_queue.js');
  for (const status of [400, 413, 503]) {
    await assert.rejects(deliverCapture('http://localhost', {}, {
      fetchImpl: async () => ({ ok: false, status })
    }), error => error.retryable === (status === 503));
  }
  await assert.rejects(deliverCapture('http://localhost', {}, { fetchImpl: async () => ({ ok: true, json: async () => ({}) }) }), /não confirmou/);
});

test('image download timeout releases the queue to try other lists', async t => {
  const { withTimeout } = await import('../capture_queue.js');
  const f = await fixture(t, item => item.messageId === 'A' ? withTimeout(new Promise(() => {}), 10, 'imagem indisponível') : Promise.resolve());
  f.queue.enqueue(payload('A')); f.queue.enqueue(payload('B'));
  await f.queue.run(); await f.queue.run();
  assert.equal(f.queue.status().pending, 1);
  assert.ok(f.queue.status().lastProcessedAt);
});

test('edited messages with distinct editKey re-enqueue while identical edits deduplicate', async t => {
  const texts = [];
  const f = await fixture(t, async item => { texts.push(item.text); });
  assert.equal(f.queue.enqueue(payload('A')), true);
  assert.equal(f.queue.enqueue(payload('A')), false);
  assert.equal(f.queue.enqueue({ ...payload('A'), text: 'IPHONE 16 128GB PRETO R$ 4850', editKey: 'ed1' }), true);
  assert.equal(f.queue.enqueue({ ...payload('A'), text: 'IPHONE 16 128GB PRETO R$ 4850', editKey: 'ed1' }), false);
  await f.queue.run();
  await f.queue.run();
  assert.deepEqual(texts, [
    'IPHONE 16 128GB PRETO R$ 5000',
    'IPHONE 16 128GB PRETO R$ 4850',
  ]);
});

test('history keeps the original edit time and allows a later return to an earlier price', async t => {
  const { supplierEventTime } = await import('../capture_queue.js');
  const morning = 1795435200, noon = morning + 10800;
  assert.equal(supplierEventTime({protocolMessage: {timestampMs: noon * 1000}}, morning), noon);
  assert.equal(supplierEventTime({editedMessage: {}}, noon), noon);
  assert.equal(supplierEventTime({}, undefined), 0);
  const f = await fixture(t, async () => {});
  const offer = { ...payload('A'), editKey: 'same-content', timestamp: noon };
  assert.equal(f.queue.enqueue(offer), true);
  assert.equal(f.queue.enqueue(offer), false);
  assert.equal(f.queue.enqueue({ ...offer, timestamp: noon + 3600 }), true);
});
