const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(
  path.join(__dirname, '..', 'extension', 'background.js'),
  'utf8',
);

const lifecycleListeners = { alarm: [], installed: [], startup: [] };
const sockets = [];
let storageReads = 0;

class FakeWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;

  constructor(url) {
    this.url = url;
    this.readyState = FakeWebSocket.CONNECTING;
    this.messages = [];
    sockets.push(this);
  }

  send(message) {
    this.messages.push(JSON.parse(message));
  }
}

function event(bucket) {
  return {
    addListener(listener) {
      if (bucket) bucket.push(listener);
    },
  };
}

const chrome = {
  action: { setBadgeBackgroundColor() {}, setBadgeText() {} },
  alarms: { clear() {}, create() {}, onAlarm: event(lifecycleListeners.alarm) },
  runtime: {
    onInstalled: event(lifecycleListeners.installed),
    onMessage: event(),
    onStartup: event(lifecycleListeners.startup),
    sendMessage: async () => {},
  },
  scripting: { executeScript: async () => {} },
  storage: {
    local: {
      async get() {
        storageReads += 1;
        return {
          callbackSecret: 'persisted-secret',
          flowKey: 'persisted-flow-key',
          metrics: { tokenCapturedAt: 1234 },
        };
      },
      async set() {},
    },
  },
  tabs: {
    create: async () => ({}),
    query: async () => [],
    sendMessage: async () => {},
    update: async () => {},
  },
  webRequest: { onBeforeSendHeaders: event() },
};

const context = vm.createContext({
  URL,
  WebSocket: FakeWebSocket,
  chrome,
  clearInterval() {},
  clearTimeout() {},
  console,
  fetch: async () => ({ ok: true }),
  navigator: { userAgent: 'FlowkitBootstrapTest/1.0' },
  setInterval() { return 1; },
  setTimeout() { return 1; },
});

vm.runInContext(source, context, { filename: 'background.js' });

setImmediate(async () => {
  await Promise.resolve();
  await Promise.resolve();

  assert.equal(storageReads, 1, 'cold worker start must hydrate storage exactly once');
  assert.equal(sockets.length, 1, 'cold worker start must connect after hydration');

  await lifecycleListeners.startup[0]();
  await lifecycleListeners.installed[0]();
  await lifecycleListeners.alarm[0]({ name: 'keepAlive' });
  assert.equal(storageReads, 1, 'lifecycle events must reuse initialization');
  assert.equal(sockets.length, 1, 'lifecycle events must not duplicate the socket');

  const socket = sockets[0];
  socket.readyState = FakeWebSocket.OPEN;
  socket.onopen();

  assert.equal(socket.messages[0].type, 'extension_ready');
  assert.equal(socket.messages[0].flowKeyPresent, true);
  assert.ok(socket.messages[0].tokenAge > 0);
  assert.deepEqual(socket.messages[1], {
    type: 'token_captured',
    flowKey: 'persisted-flow-key',
  });

  console.log('Flowkit MV3 cold-start bootstrap regression test passed');
});
