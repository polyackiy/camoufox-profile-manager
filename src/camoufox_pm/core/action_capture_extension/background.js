/* Only this privileged background talks to native messaging. No external listener. */
const config = CPM_CAPTURE_CONFIG;
let native = null;
let bound = null, documentId = null, sequence = 0, stopped = false;
function stop() {
  if (stopped) return;
  stopped = true;
  if (bound !== null) browser.tabs.sendMessage(bound, {kind: 'stop'}).catch(() => {});
  if (native) native.disconnect();
}
function connect() {
  native = browser.runtime.connectNative(config.host);
native.onDisconnect.addListener(stop);
native.onMessage.addListener((message) => {
  if (message.accepted === true && !stopped) return;
  if (message.arm === true && bound !== null && !stopped) {
    browser.tabs.sendMessage(bound, {
      kind: 'arm', generation: config.generation, document: documentId,
    }, {frameId: 0}).then((response) => {
      if (response?.armed !== true) stop();
    }).catch(stop);
  } else stop();
});
}
browser.runtime.onMessage.addListener((value, sender) => {
  if (stopped || sender.id !== browser.runtime.id || !sender.tab || sender.frameId !== 0) return;
  let origin;
  try { origin = new URL(sender.url).origin; } catch { stop(); return; }
  if (origin !== config.origin) { stop(); return; }
  if (value.kind === 'ready' && bound === null && sender.tab.active &&
      typeof value.document === 'string' && /^[0-9a-f]{32}$/.test(value.document)) {
    bound = sender.tab.id;
    documentId = value.document;
    connect();
    native.postMessage({kind: 'ready', tab: bound, generation: config.generation,
      origin, width: value.width, height: value.height});
  } else if (sender.tab.id !== bound || value.document !== documentId ||
      value.kind === 'ready' || value.kind === 'invalidated') {
    stop();
  } else if (value.generation === config.generation && ['click', 'scroll'].includes(value.kind)) {
    const {document: _document, ...input} = value;
    native.postMessage({...input, tab: bound, origin, sequence: ++sequence});
  } else stop();
});
browser.tabs.onRemoved.addListener((tab) => { if (tab === bound) stop(); });
browser.tabs.onCreated.addListener(() => { if (bound !== null) stop(); });
browser.tabs.onActivated.addListener(({tabId}) => {
  if (bound !== null && tabId !== bound) stop();
});
browser.tabs.onUpdated.addListener((tab, change) => {
  if (tab === bound && (change.url || change.status === 'loading')) stop();
});
