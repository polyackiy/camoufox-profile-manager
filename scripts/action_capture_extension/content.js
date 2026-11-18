/* Developer capture gate. No page bridge, DOM selectors, text or stored state. */
(() => {
  if (window.top !== window) return;
  let generation = null;
  let stopped = false;
  const sensitive = (event) => event.composedPath().some((node) => {
    if (!(node instanceof Element)) return false;
    // Extension-only API also sees closed roots; do not inspect their values.
    return browser.dom.openOrClosedShadowRoot(node) !== null ||
      node.matches('input,textarea,select,option,iframe,frame,object,embed') ||
      node.isContentEditable || (node instanceof HTMLLabelElement && node.control);
  });
  const send = (event, kind) => {
    if (!generation || stopped || !event.isTrusted || event.ctrlKey || event.altKey ||
        event.metaKey || event.shiftKey || sensitive(event)) return;
    if (kind === 'click' && (event.button !== 0 || event.detail !== 1)) return;
    if (kind === 'scroll' && (event.deltaMode !== 0 ||
        Math.abs(event.deltaX) > 2000 || Math.abs(event.deltaY) > 2000)) return;
    const x = Math.floor(event.clientX), y = Math.floor(event.clientY);
    if (x < 0 || y < 0 || x >= innerWidth || y >= innerHeight) return;
    const value = {kind, generation, width: innerWidth, height: innerHeight, x, y};
    if (kind === 'scroll') Object.assign(value, {
      delta_x: Math.trunc(event.deltaX), delta_y: Math.trunc(event.deltaY),
    });
    browser.runtime.sendMessage(value).catch(stop);
  };
  const click = (event) => send(event, 'click');
  const wheel = (event) => send(event, 'scroll');
  function stop() {
    stopped = true;
    generation = null;
    document.removeEventListener('click', click, true);
    document.removeEventListener('wheel', wheel, true);
  }
  browser.runtime.onMessage.addListener((message) => {
    if (message.kind === 'arm' && !stopped && generation === null) {
      generation = message.generation;
      document.addEventListener('click', click, true);
      document.addEventListener('wheel', wheel, {capture: true, passive: true});
    } else stop();
  });
  addEventListener('pagehide', () => {
    stop();
    browser.runtime.sendMessage({kind: 'invalidated'}).catch(() => {});
  }, {once: true});
  browser.runtime.sendMessage({kind: 'ready', width: innerWidth, height: innerHeight}).catch(stop);
})();
