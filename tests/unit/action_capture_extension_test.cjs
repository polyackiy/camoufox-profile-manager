const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const path = require('node:path');
const root = path.resolve(__dirname, '../../scripts/action_capture_extension');
const event = () => ({listeners: [], addListener(fn) { this.listeners.push(fn); }});
let nativeMessages = [], stopped = false, sent = [];
const native = {onDisconnect:event(),onMessage:event(),postMessage(v){nativeMessages.push(v)},disconnect(){stopped=true}};
const browser = {runtime:{id:'addon',connectNative(){return native},onMessage:event()},tabs:{onRemoved:event(),onUpdated:event(),sendMessage(tab,v){sent.push(v);return Promise.resolve()}}};
vm.runInNewContext(fs.readFileSync(path.join(root,'background.js'),'utf8'),{CPM_CAPTURE_CONFIG:{host:'host',generation:'g',origin:'https://site.test'},browser,URL});
const receive=browser.runtime.onMessage.listeners[0];
const sender={id:'addon',frameId:0,url:'https://site.test/?private',tab:{id:8}};
receive({kind:'ready',width:900,height:700},sender);
assert.equal(nativeMessages.length,1);
native.onMessage.listeners[0]({arm:true});
receive({kind:'click',generation:'g',width:900,height:700,x:10,y:20},sender);
assert.equal(nativeMessages.length,2);
native.onMessage.listeners[0]({accepted:true});
assert.equal(stopped,false,'Acknowledgement must not disconnect the capture stream');
receive({kind:'scroll',generation:'g',width:900,height:700,x:10,y:20,delta_x:0,delta_y:100},sender);
assert.equal(nativeMessages.length,3);
assert.equal(nativeMessages[2].sequence,2);
assert.equal(nativeMessages[2].origin,'https://site.test');
assert.equal(JSON.stringify(nativeMessages).includes('private'),false);
for(const other of [{...sender,id:'different'},{...sender,frameId:3}]) {
 receive({kind:'click',generation:'g'},other);
}
assert.equal(nativeMessages.length,3);
browser.tabs.onUpdated.listeners[0](8,{url:'https://other.test'});
assert.equal(stopped,true);
receive({kind:'click',generation:'g'},sender);
assert.equal(nativeMessages.length,3);
console.log('background acknowledgement, origin, sender/frame and stop contracts passed');

// Opaque shadow hosts can hide editable descendants from composedPath().
const shadowRoots = new WeakMap(), listeners = new Map(), contentMessages = [];
class Element { get openOrClosedShadowRoot() {return shadowRoots.get(this)||null;} constructor(tag='div') { this.tag=tag; this.isContentEditable=false; } matches(){return ['input','textarea','iframe'].includes(this.tag)} }
class HTMLLabelElement extends Element {}
const doc={addEventListener(type,fn){listeners.set(type,fn)},removeEventListener(type){listeners.delete(type)}};
const win={}; win.top=win;
const contentBrowser={runtime:{onMessage:event(),sendMessage(v){contentMessages.push(v);return Promise.resolve()}}};
vm.runInNewContext(fs.readFileSync(path.join(root,'content.js'),'utf8'),{browser:contentBrowser,document:doc,window:win,innerWidth:900,innerHeight:700,Element,HTMLLabelElement,addEventListener(){}});
contentBrowser.runtime.onMessage.listeners[0]({kind:'arm',generation:'g'});
const opaque=new Element(); shadowRoots.set(opaque, {mode:'closed'});
const input=new Element('input'), normal=new Element('button');
const click=(target, trusted=true)=>({isTrusted:trusted,button:0,detail:1,clientX:10,clientY:20,composedPath(){return [target]}});
const baseline=contentMessages.length;
listeners.get('click')(click(opaque));
listeners.get('click')(click(input));
listeners.get('click')(click(normal,false));
assert.equal(contentMessages.length,baseline,'Sensitive/closed-shadow/synthetic input must not be captured');
listeners.get('click')(click(normal));
assert.equal(contentMessages.length,baseline+1);
const wheel = {...click(normal),deltaMode:1,deltaX:0,deltaY:3};
listeners.get('wheel')(wheel);
assert.equal(contentMessages.at(-1).kind,'scroll');
assert.equal(contentMessages.at(-1).delta_mode,1);
assert.equal(contentMessages.at(-1).delta_y,3);
const wheelCount = contentMessages.length;
listeners.get('wheel')({...wheel,deltaMode:2});
listeners.get('wheel')({...wheel,deltaY:61});
assert.equal(contentMessages.length,wheelCount);
contentBrowser.runtime.onMessage.listeners[0]({kind:'stop'});
assert.equal(listeners.size,0,'Stop must detach listeners');
console.log('closed shadow, sensitive/synthetic input and listener cleanup contracts passed');
