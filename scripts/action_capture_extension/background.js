/* Only this privileged background talks to native messaging. No external listener. */
const config = CPM_CAPTURE_CONFIG;
const native = browser.runtime.connectNative(config.host);
let bound = null, sequence = 0, stopped = false;
function stop() {
  if (stopped) return;
  stopped = true;
  if (bound !== null) browser.tabs.sendMessage(bound, {kind: 'stop'}).catch(() => {});
  native.disconnect();
}
native.onDisconnect.addListener(stop);
native.onMessage.addListener((message) => {
  if (message.accepted === true && !stopped) return;
  if (message.arm === true && bound !== null && !stopped) {
    browser.tabs.sendMessage(bound, {kind: 'arm', generation: config.generation}).catch(stop);
  } else stop();
});
browser.runtime.onMessage.addListener((value, sender) => {
  if (stopped || sender.id !== browser.runtime.id || !sender.tab || sender.frameId !== 0) return;
  let origin;
  try { origin = new URL(sender.url).origin; } catch { stop(); return; }
  if (origin !== config.origin) { stop(); return; }
  if (value.kind === 'ready' && bound === null) {
    bound = sender.tab.id;
    native.postMessage({kind: 'ready', tab: bound, generation: config.generation,
      origin, width: value.width, height: value.height});
  } else if (sender.tab.id !== bound || value.kind === 'ready' || value.kind === 'invalidated') {
    stop();
  } else if (value.generation === config.generation && ['click', 'scroll'].includes(value.kind)) {
    native.postMessage({...value, tab: bound, origin, sequence: ++sequence});
  } else stop();
});
browser.tabs.onRemoved.addListener((tab) => { if (tab === bound) stop(); });
browser.tabs.onUpdated.addListener((tab, change) => {
  if (tab === bound && (change.url || change.status === 'loading')) stop();
});
