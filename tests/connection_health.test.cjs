const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('whatsapp_bridge.js', 'utf8');
const watchdog = source.slice(source.indexOf('async function checkConnectionHealth'), source.indexOf('const connectionWatchdog'));

function fixture() {
  const scheduled = [];
  const context = {
    stopping: false, AUTOMATION_PAUSED: false, watchdogBusy: false,
    connectionStatus: 'CONNECTED', connecting: false, reconnectTimer: null,
    lastDisconnectReason: null, DisconnectReason: { connectionReplaced: 440 },
    connectionChangedAt: Date.now(), socketGeneration: 1,
    waSocket: { sendPresenceUpdate: async () => {} },
    setTimeout, clearTimeout, addLog() {},
    scheduleReconnect: delay => scheduled.push(delay),
    setConnectionStatus: value => { context.connectionStatus = value; }
  };
  vm.createContext(context); vm.runInContext(watchdog, context);
  return { context, scheduled };
}
test('a connecting state that never finishes is retried', async () => {
  const { context, scheduled } = fixture();
  context.connectionStatus = 'CONNECTING'; context.connectionChangedAt = Date.now() - 121000;
  await context.checkConnectionHealth();
  assert.equal(context.connectionStatus, 'DISCONNECTED'); assert.equal(scheduled.length, 1);
});
test('an old heartbeat cannot disconnect the replacement socket', async () => {
  const { context, scheduled } = fixture();
  let reject;
  context.waSocket.sendPresenceUpdate = () => new Promise((_, fail) => { reject = fail; });
  const checking = context.checkConnectionHealth();
  context.socketGeneration++;
  reject(new Error('old socket closed'));
  await checking;
  assert.equal(context.connectionStatus, 'CONNECTED'); assert.equal(scheduled.length, 0);
});
test('failed heartbeat reconnects the active socket, but does not fight another instance', async () => {
  const { context, scheduled } = fixture();
  context.waSocket.sendPresenceUpdate = async () => { throw new Error('offline'); };
  await context.checkConnectionHealth();
  assert.equal(scheduled.length, 1);
  context.lastDisconnectReason = { code: 440 };
  await context.checkConnectionHealth();
  assert.equal(scheduled.length, 1);
});
test('waiting for QR and administrative pause never trigger forced reconnects', async () => {
  const { context, scheduled } = fixture();
  context.connectionChangedAt = Date.now() - 900000;
  context.connectionStatus = 'QR_READY'; await context.checkConnectionHealth();
  context.connectionStatus = 'DISCONNECTED'; context.AUTOMATION_PAUSED = true; await context.checkConnectionHealth();
  assert.equal(scheduled.length, 0);
});
