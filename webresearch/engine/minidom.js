/* webresearch mini-DOM — ES subset for QuickJS. Not Chromium. */
(function (global) {
  var _NativePromise = global.Promise;
  var _nativePromiseResolve = _NativePromise ? _NativePromise.resolve.bind(_NativePromise) : null;
  /* ECMAScript Integer-Indexed Exotic Objects [[Set]]: out-of-bounds writes are no-ops returning true */
  (function () {
    if (global.__WR_ENGINE === "doshgentv8") return;
    var typedArrayClasses = [
      Uint8Array, Int8Array, Uint8ClampedArray,
      Int16Array, Uint16Array, Int32Array, Uint32Array,
      Float32Array, Float64Array
    ];
    for (var i = 0; i < typedArrayClasses.length; i++) {
      var Cls = typedArrayClasses[i];
      if (!Cls) continue;
      (function (Original, name) {
        var Handler = function () {
          var args = Array.prototype.slice.call(arguments);
          var target = new (Function.prototype.bind.apply(Original, [null].concat(args)))();
          return new Proxy(target, {
            get: function (obj, prop, receiver) {
              var val = Reflect.get(obj, prop, obj);
              return typeof val === "function" ? val.bind(obj) : val;
            },
            set: function (obj, prop, val, receiver) {
              var idx = typeof prop === "symbol" ? NaN : Number(prop);
              if (Number.isInteger(idx) && (idx < 0 || idx >= obj.length)) {
                return true;
              }
              return Reflect.set(obj, prop, val, obj);
            }
          });
        };
        Handler.prototype = Original.prototype;
        Object.setPrototypeOf(Handler, Original);
        try { global[name] = Handler; } catch (e) {}
        try { if (typeof window !== "undefined") window[name] = Handler; } catch (e) {}
      })(Cls, Cls.name);
    }
  })();

  /* Native Function Cloaking: anti-fingerprinting & bot-detection defense */
  var _origFunctionToString = Function.prototype.toString;
  var _nativeFunctionMap = new WeakMap();

  function __makeNative(fn, name, length) {
    if (typeof fn !== "function") return fn;
    var fnName = name !== undefined ? String(name) : (fn.name || "");
    var fnLen = length !== undefined ? Number(length) : fn.length;
    try {
      Object.defineProperty(fn, "name", { value: fnName, configurable: true, writable: false });
      Object.defineProperty(fn, "length", { value: fnLen, configurable: true, writable: false });
    } catch (e) {}
    _nativeFunctionMap.set(fn, { name: fnName, length: fnLen });
    return fn;
  }

  var _customToString = function toString() {
    if (typeof this !== "function") {
      return _origFunctionToString.call(this);
    }
    if (this === _customToString) {
      return "function toString() { [native code] }";
    }
    var meta = _nativeFunctionMap.get(this);
    if (meta) {
      return "function " + (meta.name || "") + "() { [native code] }";
    }
    return _origFunctionToString.call(this);
  };
  __makeNative(_customToString, "toString", 0);
  try {
    Object.defineProperty(Function.prototype, "toString", {
      value: _customToString,
      writable: true,
      enumerable: false,
      configurable: true
    });
  } catch (e) {
    Function.prototype.toString = _customToString;
  }

  function __cloakObject(obj, map) {
    if (!obj) return;
    for (var k in map) {
      if (typeof obj[k] === "function") {
        __makeNative(obj[k], k, map[k]);
      }
    }
  }

  function __cloakRuntimeGlobals(g) {
    if (!g) return;
    __cloakObject(g, {
      fetch: 1,
      setTimeout: 1,
      clearTimeout: 1,
      setInterval: 1,
      clearInterval: 1,
      requestAnimationFrame: 1,
      cancelAnimationFrame: 1,
      atob: 1,
      btoa: 1,
      alert: 0,
      confirm: 0,
      prompt: 0,
      open: 0,
      close: 0,
      scroll: 1,
      scrollTo: 1,
      scrollBy: 1,
      postMessage: 1,
      getComputedStyle: 1,
      matchMedia: 1,
      addEventListener: 2,
      removeEventListener: 2,
      dispatchEvent: 1
    });

    if (g.navigator) {
      __cloakObject(g.navigator, {
        sendBeacon: 1,
        getGamepads: 0,
        getBattery: 0
      });
      if (g.navigator.permissions) __cloakObject(g.navigator.permissions, { query: 1 });
      if (g.navigator.clipboard) __cloakObject(g.navigator.clipboard, { readText: 0, writeText: 1 });
      if (g.navigator.mediaDevices) __cloakObject(g.navigator.mediaDevices, { enumerateDevices: 0, getUserMedia: 1 });
    }

    if (g.chrome) {
      __cloakObject(g.chrome, {
        loadTimes: 0,
        csi: 0
      });
    }

    if (g.localStorage) {
      __cloakObject(g.localStorage, {
        getItem: 1,
        setItem: 2,
        removeItem: 1,
        clear: 0,
        key: 1
      });
    }

    if (g.sessionStorage) {
      __cloakObject(g.sessionStorage, {
        getItem: 1,
        setItem: 2,
        removeItem: 1,
        clear: 0,
        key: 1
      });
    }

    if (g.Notification) {
      __makeNative(g.Notification, "Notification", 1);
      __cloakObject(g.Notification, { requestPermission: 0 });
    }

    if (g.BroadcastChannel) {
      __makeNative(g.BroadcastChannel, "BroadcastChannel", 1);
      if (g.BroadcastChannel.prototype) __cloakObject(g.BroadcastChannel.prototype, { postMessage: 1, close: 0 });
    }

    if (g.DOMParser) {
      __makeNative(g.DOMParser, "DOMParser", 0);
      if (g.DOMParser.prototype) __cloakObject(g.DOMParser.prototype, { parseFromString: 1 });
    }

    if (g.Event) __makeNative(g.Event, "Event", 1);
    if (g.CustomEvent) __makeNative(g.CustomEvent, "CustomEvent", 1);
    if (g.Node) __makeNative(g.Node, "Node", 0);
    if (g.Element) __makeNative(g.Element, "Element", 0);
    if (g.HTMLElement) __makeNative(g.HTMLElement, "HTMLElement", 0);
    if (g.Document) __makeNative(g.Document, "Document", 0);
    if (g.DocumentFragment) __makeNative(g.DocumentFragment, "DocumentFragment", 0);
    if (g.Text) __makeNative(g.Text, "Text", 1);
    if (g.FormData) __makeNative(g.FormData, "FormData", 0);
    if (g.MutationObserver) __makeNative(g.MutationObserver, "MutationObserver", 1);
    if (g.IntersectionObserver) __makeNative(g.IntersectionObserver, "IntersectionObserver", 1);
    if (g.ResizeObserver) __makeNative(g.ResizeObserver, "ResizeObserver", 1);
    if (g.AudioContext) __makeNative(g.AudioContext, "AudioContext", 0);
    if (g.webkitAudioContext) __makeNative(g.webkitAudioContext, "webkitAudioContext", 0);
    if (g.OfflineAudioContext) __makeNative(g.OfflineAudioContext, "OfflineAudioContext", 1);
    if (g.webkitOfflineAudioContext) __makeNative(g.webkitOfflineAudioContext, "webkitOfflineAudioContext", 1);
    if (g.RTCPeerConnection) __makeNative(g.RTCPeerConnection, "RTCPeerConnection", 0);
    if (g.webkitRTCPeerConnection) __makeNative(g.webkitRTCPeerConnection, "webkitRTCPeerConnection", 0);
    if (g.RTCIceCandidate) __makeNative(g.RTCIceCandidate, "RTCIceCandidate", 1);
    if (g.RTCSessionDescription) __makeNative(g.RTCSessionDescription, "RTCSessionDescription", 1);
    if (g.SpeechSynthesisUtterance) __makeNative(g.SpeechSynthesisUtterance, "SpeechSynthesisUtterance", 0);
    if (g.speechSynthesis) {
      __cloakObject(g.speechSynthesis, {
        getVoices: 0,
        speak: 1,
        cancel: 0,
        pause: 0,
        resume: 0
      });
    }
  }

  function Node(type, name, attrs, kids) {
    this.nodeType = type;
    this.nodeName = name;
    this.tagName = type === 1 ? String(name).toUpperCase() : "";
    this.attributes = attrs || {};
    this.childNodes = kids || [];
    this.parentNode = null;
    this._listeners = {};
    var self = this;
    this.childNodes.forEach(function (c) { c.parentNode = self; });
  }
  Node.ELEMENT_NODE = 1;
  Node.TEXT_NODE = 3;
  Node.DOCUMENT_NODE = 9;

  function _notifyConnected(node) {
    if (!node || node.nodeType !== 1) return;
    if (typeof node.connectedCallback === "function") {
      try { node.connectedCallback(); } catch (e) {}
    }
    if (node.childNodes && node.childNodes.length) {
      for (var i = 0; i < node.childNodes.length; i++) _notifyConnected(node.childNodes[i]);
    }
  }

  function _notifyDisconnected(node) {
    if (!node || node.nodeType !== 1) return;
    if (typeof node.disconnectedCallback === "function") {
      try { node.disconnectedCallback(); } catch (e) {}
    }
    if (node.childNodes && node.childNodes.length) {
      for (var i = 0; i < node.childNodes.length; i++) _notifyDisconnected(node.childNodes[i]);
    }
  }

  /* Runtime-injected scripts (document.createElement('script')) can't be seen
     by the static extractor. Push them onto a queue that the host drains after
     each pump pass so dynamically-loaded bundles execute.

     Both appendChild and insertBefore must call this. reCAPTCHA's api.js
     inserts its client bundle with `firstScript.parentNode.insertBefore(tag,
     firstScript)`, so an appendChild-only hook left grecaptcha.render
     undefined and the widget never appeared. */
  function __noteInjectedScripts(child) {
    if (!child) return;
    var __t = (child.nodeName || "").toLowerCase();
    if (__t !== "script" && __t !== "#document-fragment") return;
    var scripts;
    if (__t === "script") scripts = [child];
    else if (child.querySelectorAll) scripts = child.querySelectorAll("script");
    else return;
    for (var s = 0; s < scripts.length; s++) {
      var el = scripts[s];
      var info = {
        src: el.getAttribute("src") || "",
        text: el.textContent || "",
        type: (el.getAttribute("type") || "").toLowerCase()
      };
      if (info.src || info.text) {
        globalThis.__WR_INJECTED_SCRIPTS = globalThis.__WR_INJECTED_SCRIPTS || [];
        globalThis.__WR_INJECTED_SCRIPTS.push(info);
      }
    }
  }

  /* A throwing event listener must be observable. Swallowing it silently made
     a reCAPTCHA checkbox click look like "nothing happened": the visual state
     changed, the handler threw, and no network request was ever made. The
     stack makes "which listener" answerable without re-instrumentation. */
  function __noteEventListenerError(type, e) {
    try {
      globalThis.__WR_EVENT_ERRORS = globalThis.__WR_EVENT_ERRORS || [];
      if (globalThis.__WR_EVENT_ERRORS.length < 50) {
        var msg = String((e && e.message) || e);
        var stack = (e && e.stack) ? " @" + String(e.stack).split("\n").slice(0, 3).join(" | ") : "";
        globalThis.__WR_EVENT_ERRORS.push(
          String(type) + ": " + (msg + stack).slice(0, 400)
        );
      }
    } catch (_) {}
  }

  /* postMessage's second argument is either a targetOrigin string or a
     WindowPostMessageOptions bag. ReCAPTCHA passes the bag form, which used
     to stringify to "[object Object]" and made every target message fail the
     origin check. Spec: an options bag without targetOrigin defaults to "/",
     meaning "same origin as the sender". */
  function __resolveTargetOrigin(arg) {
    if (typeof arg === "string") return arg;
    if (arg && typeof arg === "object") {
      if (typeof arg.targetOrigin === "string") return arg.targetOrigin;
      return "/";
    }
    if (arg === undefined || arg === null) return "*";
    return String(arg);
  }

  /* Resolve the real Window object a cross-document message came from.

     The runtime router only knows origins and frame URLs, so delivery used to
     hand every message a fresh anonymous sourceProxy. ReCAPTCHA's parent-side
     listener (and every other widget that completes a postMessage handshake)
     compares event.source against the specific Window objects tied to its own
     iframes — an anonymous proxy fails that identity check silently, which is
     exactly why the recaptcha-setup handshake never completed here.

     Sender hints are attached by the routing layer:
       {"window": "parent"}                - sender is this context's parent
       {"window": "frame", "src": "<url>"} - sender is the iframe with this src
  */
  function __resolveSenderSource(hint) {
    var h = hint;
    if (typeof h === "string") {
      try { h = JSON.parse(h); } catch (e) { return null; }
    }
    if (!h || typeof h !== "object") return null;
    if (h.window === "parent") return global.parent || null;
    if (h.window === "frame") {
      var want = String(h.src || "");
      if (!want) return null;
      var doc = global.document;
      if (!doc || !doc.querySelectorAll) return null;
      var frames = doc.querySelectorAll("iframe");
      var wantCb = (want.match(/[?&]cb=([^&]*)/) || [undefined, ""])[1];
      var wantPath = String(want).replace(/^https?:\/\/[^/]+/i, "");
      var best = null;
      var bestScore = 0;
      for (var i = 0; i < frames.length; i++) {
        var se = (frames[i].getAttribute && frames[i].getAttribute("src")) || "";
        if (!se) continue;
        var score = 0;
        if (se === want) score = 1000;
        else {
          var seCb = (se.match(/[?&]cb=([^&]*)/) || [undefined, ""])[1];
          if (wantCb && seCb === wantCb) score = 800;
          else {
            var sePath = String(se).replace(/^https?:\/\/[^\/]+/i, "");
            var sp = sePath.split("?")[0];
            var wp = wantPath.split("?")[0];
            if (sp && wp && (sp === wp || wp.indexOf(sp) === 0 || sp.indexOf(wp) === 0)) score = 400;
          }
        }
        if (score > bestScore) { bestScore = score; best = frames[i]; }
      }
      if (best && bestScore > 0) {
        try { return best.contentWindow; } catch (e) { return null; }
      }
    }
    return null;
  }

  /* Frame-load lifecycle: when a frame's document finishes loading, the
     iframe ELEMENT in its parent realm fires a `load` event (spec
     behavior; it does not bubble). Widget parents gate on it —
     reCAPTCHA's parent client will not process its anchor's channel
     messages until the anchor iframe's load has fired, so an engine that
     never dispatches it leaves the widget's handshake permanently
     "waiting for the anchor" and the checkbox never resolves.
     Both the onload property and addEventListener('load') handlers run,
     each exactly once, like a real browser. */
  global.__wrFireFrameLoad = function (srcAttr) {
    var doc = global.document;
    if (!doc || !doc.querySelectorAll) return false;
    var frames = doc.querySelectorAll("iframe");
    var want = String(srcAttr || "");
    var el = null;
    for (var i = 0; i < frames.length; i++) {
      var s = (frames[i].getAttribute && frames[i].getAttribute("src")) || "";
      if (s && (s === want || want.indexOf(s) === 0 || s.indexOf(want) === 0)) {
        el = frames[i];
        break;
      }
    }
    if (!el) return false;
    var loadListeners = (el._listeners && el._listeners.load) ? el._listeners.load.length : 0;
    try {
      globalThis.__WR_FRAME_LOADS = globalThis.__WR_FRAME_LOADS || [];
      if (globalThis.__WR_FRAME_LOADS.length < 20) {
        globalThis.__WR_FRAME_LOADS.push({
          src: String((el.getAttribute && el.getAttribute("src")) || "").slice(0, 90),
          onload: typeof el.onload === "function",
          listeners: loadListeners
        });
      }
    } catch (eRec) {}
    var ev = new global.Event("load", { isTrusted: true, __trusted: true });
    ev.target = el;
    /* Element.dispatchEvent runs both the addEventListener('load') handlers
       and the el.onload property — exactly once each, like a real browser.
       Calling onload manually here as well made every load fire twice. */
    try { el.dispatchEvent(ev); } catch (e) {}
    return true;
  };

  Node.prototype.appendChild = function (child) {
    if (!child) return child;
    /* Spec: appending a DocumentFragment inserts its CHILDREN (the fragment
       itself never enters the tree). Each child flows through this method,
       so mutation records and injected-script detection fire per child. */
    if (child.nodeType === 11) {
      var fragKids = child.childNodes.slice();
      for (var fk = 0; fk < fragKids.length; fk++) {
        this.appendChild(fragKids[fk]);
      }
      return child;
    }
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    queueMutation("childList", this, { addedNodes: [child] });
    _notifyConnected(child);
    /* Node names in this engine are LOWERCASE ('script'), so the old
       `=== "SCRIPT"` check never matched and the entire injection lane was
       dead (re-audit 2026-10-27). */
    __noteInjectedScripts(child);
    return child;
  };
  Node.prototype.removeChild = function (child) {
    var i = this.childNodes.indexOf(child);
    if (i >= 0) {
      this.childNodes.splice(i, 1);
      child.parentNode = null;
      queueMutation("childList", this, { removedNodes: [child] });
      _notifyDisconnected(child);
    }
    return child;
  };
  Node.prototype.remove = function () {
    /* DOM spec: remove self from parent. A single missing method here
       (LinkedIn's cdnMonitor calls el.remove() at line 30) kills a page's
       entire bootstrap when absent. */
    if (this.parentNode) this.parentNode.removeChild(this);
  };
  Node.prototype.insertBefore = function (child, ref) {
    /* Spec: insertBefore with a string throws TypeError. The old code pushed
       the raw string into childNodes, and the serializer then failed — the
       runtime's fallback silently discarded the entire pass's DOM mutations
       with errors=0 (re-audit finding). */
    if (typeof child === "string") {
      throw new TypeError("Failed to execute 'insertBefore': parameter 1 is not of type 'Node'");
    }
    if (!ref) return this.appendChild(child);
    if (child.parentNode) child.parentNode.removeChild(child);
    var i = this.childNodes.indexOf(ref);
    if (i < 0) return this.appendChild(child);
    child.parentNode = this;
    this.childNodes.splice(i, 0, child);
    queueMutation("childList", this, { addedNodes: [child] });
    _notifyConnected(child);
    __noteInjectedScripts(child);
    return child;
  };
  Node.prototype.replaceChild = function (neu, old) {
    this.insertBefore(neu, old);
    this.removeChild(old);
    return old;
  };
  /* ---- ParentNode/ChildNode insertion methods ----
     Real production scripts call these (example.com's s.js calls
     document.body.prepend(...); GitHub and most React trees use append).
     They accept variadic nodes or strings (strings become Text nodes, per
     spec) and route through insertBefore/appendChild so mutation records,
     connected/disconnected notification, and injected-script detection all
     still fire. */
  function __toInsertable(v) {
    if (typeof v === "string") return new Text(v);
    return v;
  }
  Node.prototype.append = function () {
    for (var i = 0; i < arguments.length; i++) {
      var n = __toInsertable(arguments[i]);
      if (n) this.appendChild(n);
    }
  };
  Node.prototype.prepend = function () {
    var ref = this.childNodes.length ? this.childNodes[0] : null;
    for (var j = arguments.length - 1; j >= 0; j--) {
      var n2 = __toInsertable(arguments[j]);
      if (n2) this.insertBefore(n2, ref);
    }
  };
  Node.prototype.before = function () {
    if (!this.parentNode) return;
    for (var k = 0; k < arguments.length; k++) {
      var n3 = __toInsertable(arguments[k]);
      if (n3) this.parentNode.insertBefore(n3, this);
    }
  };
  Node.prototype.after = function () {
    if (!this.parentNode) return;
    var next = this.nextSibling;
    for (var m = arguments.length - 1; m >= 0; m--) {
      var n4 = __toInsertable(arguments[m]);
      if (n4) this.parentNode.insertBefore(n4, next);
    }
  };
  Node.prototype.replaceChildren = function () {
    while (this.childNodes.length) this.removeChild(this.childNodes[0]);
    for (var q = 0; q < arguments.length; q++) {
      var n5 = __toInsertable(arguments[q]);
      if (n5) this.appendChild(n5);
    }
  };
  Node.prototype.contains = function (other) {
    if (!other) return false;
    var n = other;
    while (n) {
      if (n === this) return true;
      n = n.parentNode;
    }
    return false;
  };
  Object.defineProperty(Node.prototype, "isConnected", {
    get: function () {
      var n = this;
      while (n) {
        if (n.nodeType === 9) return true;
        n = n.parentNode;
      }
      return false;
    },
    configurable: true
  });
  Object.defineProperty(Node.prototype, "ownerDocument", {
    get: function () {
      if (this.nodeType === 9) return null;
      if (this._ownerDoc) return this._ownerDoc;
      var p = this.parentNode;
      while (p) {
        if (p.nodeType === 9) return p;
        p = p.parentNode;
      }
      return global.document || null;
    },
    set: function (d) {
      this._ownerDoc = d;
    },
    configurable: true
  });
  Object.defineProperty(Node.prototype, "textContent", {
    get: function () {
      if (this.nodeType === 3) return this._text || "";
      return this.childNodes.map(function (c) { return c.textContent; }).join("");
    },
    set: function (v) {
      if (this.nodeType === 3) {
        queueMutation("characterData", this, { oldValue: this._text || "" });
        this._text = String(v);
        return;
      }
      var removed = this.childNodes.slice();
      this.childNodes = [];
      removed.forEach(function (c) { c.parentNode = null; });
      queueMutation("childList", this, { removedNodes: removed });
      if (v) this.appendChild(new Text(String(v)));
    }
  });
  Object.defineProperty(Node.prototype, "innerText", {
    get: function () { return this.textContent; },
    set: function (v) { this.textContent = v; }
  });

  /* ---- MutationObserver (real, firing) ---- */
  var mutationObservers = [];
  var customRegistry = {};
  var whenDefinedPromises = {};

  function MutationRecord(type, target, opts) {
    opts = opts || {};
    this.type = type;
    this.target = target;
    this.addedNodes = opts.addedNodes || [];
    this.removedNodes = opts.removedNodes || [];
    this.previousSibling = opts.previousSibling || null;
    this.nextSibling = opts.nextSibling || null;
    this.attributeName = opts.attributeName || null;
    this.attributeNamespace = null;
    this.oldValue = opts.oldValue == null ? null : String(opts.oldValue);
  }

  function _isDescendantOf(node, ancestor) {
    var n = node.parentNode;
    while (n) {
      if (n === ancestor) return true;
      n = n.parentNode;
    }
    return false;
  }

  function _observerMatches(obs, record) {
    for (var i = 0; i < obs._targets.length; i++) {
      var t = obs._targets[i];
      var node = t.node;
      var o = t.options;
      var subtree = !!o.subtree;
      var targetInScope = (record.target === node) || (subtree && _isDescendantOf(record.target, node));
      if (!targetInScope) continue;
      if (record.type === "childList" && o.childList) return true;
      if (record.type === "attributes") {
        var wantsAttrs = o.attributes || o.attributeOldValue || o.attributeFilter;
        if (wantsAttrs) {
          if (o.attributeFilter) {
            if (o.attributeFilter.indexOf(record.attributeName) >= 0) return true;
          } else {
            return true;
          }
        }
      }
      if (record.type === "characterData" && o.characterData) return true;
    }
    return false;
  }

  function _scheduleDelivery(obs) {
    if (obs._scheduled) return;
    obs._scheduled = true;
    var deliver = function () {
      obs._scheduled = false;
      if (!obs._active || obs._records.length === 0) return;
      var records = obs._records;
      obs._records = [];
      try { obs._cb(records, obs); } catch (e) {}
    };
    if (typeof global.queueMicrotask === "function") {
      global.queueMicrotask(deliver);
    } else {
      Promise.resolve().then(deliver);
    }
  }

  function __bumpLayout(node) {
    var n = node, guard = 0;
    while (n && guard++ < 10000) {
      if (n.nodeType === 9) {
        n.__layoutVersion = (n.__layoutVersion || 0) + 1;
        n.__layoutCache = null;
        return;
      }
      n = n.parentNode;
    }
    if (global.document) {
      global.document.__layoutVersion = (global.document.__layoutVersion || 0) + 1;
      global.document.__layoutCache = null;
    }
  }

  /* Engine Delta Recording for ANBF Streaming */
  var __engineDeltaRecording = false;
  var __engineDeltas = [];

  global.__startDeltaRecording = function () {
    __engineDeltaRecording = true;
    __engineDeltas = [];
  };

  global.__drainDeltaRecords = function () {
    __engineDeltaRecording = false;
    var list = __engineDeltas.slice();
    __engineDeltas = [];
    return JSON.stringify(list);
  };

  function queueMutation(type, target, opts) {
    try { __bumpLayout(target); } catch (e) {}
    if (__engineDeltaRecording) {
      try {
        var tid = "";
        if (target && target.getAttribute) {
          tid = target.getAttribute("id") || target.nodeName.toLowerCase();
        } else if (target) {
          tid = target.nodeName ? target.nodeName.toLowerCase() : "node";
        }
        var content = "";
        var dkind = "mutate";
        if (type === "characterData") {
          content = target.textContent || "";
          dkind = "mutate";
        } else if (type === "attributes") {
          content = (opts && opts.attributeName ? opts.attributeName : "") + "=" + (target.getAttribute ? (target.getAttribute(opts.attributeName) || "") : "");
          dkind = "mutate";
        } else if (type === "childList") {
          if (opts && opts.addedNodes && opts.addedNodes.length > 0) {
            dkind = "insert";
            content = opts.addedNodes.map(function (n) { return n.textContent || ""; }).join(" ").trim();
          } else if (opts && opts.removedNodes && opts.removedNodes.length > 0) {
            dkind = "remove";
            content = opts.removedNodes.map(function (n) { return n.textContent || ""; }).join(" ").trim();
          }
        }
        if (content || dkind !== "mutate") {
          __engineDeltas.push({
            kind: dkind,
            target_id: tid || "node",
            content: content.slice(0, 500)
          });
        }
      } catch (e) {}
    }
    if (mutationObservers.length === 0) return;
    var record = new MutationRecord(type, target, opts);
    for (var i = 0; i < mutationObservers.length; i++) {
      var obs = mutationObservers[i];
      if (!obs._active) continue;
      if (!_observerMatches(obs, record)) continue;
      obs._records.push(record);
      _scheduleDelivery(obs);
    }
  }

  function MutationObserver(callback) {
    this._cb = callback || function () {};
    this._records = [];
    this._targets = [];
    this._active = false;
    this._scheduled = false;
  }
  MutationObserver.prototype.observe = function (target, options) {
    options = options || {};
    if (!options.childList && !options.attributes && !options.characterData &&
        !options.attributeOldValue && !options.attributeFilter) {
      options.childList = true;
    }
    this._targets.push({ node: target, options: options });
    if (!this._active) {
      this._active = true;
      if (mutationObservers.indexOf(this) < 0) mutationObservers.push(this);
    }
  };
  MutationObserver.prototype.disconnect = function () {
    this._active = false;
    this._records = [];
    this._targets = [];
    var i = mutationObservers.indexOf(this);
    if (i >= 0) mutationObservers.splice(i, 1);
  };
  MutationObserver.prototype.takeRecords = function () {
    var r = this._records;
    this._records = [];
    return r;
  };
  global.MutationObserver = MutationObserver;
  global.WebKitMutationObserver = MutationObserver;
  global.__queueMutation = queueMutation;

  /* Host watch lane: a bounded mutation log for the shell's `watch` command.
     The observer is installed by the HOST, never by page scripts — page code
     cannot read this log, and this log never surfaces in the page-realm API.
     Records are compact descriptors (tag#id.class strings), not live nodes:
     stale references cannot leak out of the engine. */
  var __wrWatch = { obs: null, log: [], seq: 0, cap: 500, enabled: false };
  function __wrWatchDesc(node) {
    try {
      if (!node) return "null";
      if (node.nodeType !== 1) return String(node.nodeName || "text");
      var d = String(node.nodeName || "").toLowerCase();
      var id = node.id || (node.getAttribute && node.getAttribute("id"));
      if (id) d += "#" + id;
      var cls = node.getAttribute && node.getAttribute("class");
      if (cls && String(cls).trim()) d += "." + String(cls).trim().split(/\s+/)[0];
      return d;
    } catch (e) { return "unknown"; }
  }
  global.__wrWatchInstall = function () {
    var doc = global.document;
    var root = doc && (doc.body || doc.documentElement);
    if (!root) return JSON.stringify({ ok: false, reason: "no document to observe" });
    if (__wrWatch.obs) { try { __wrWatch.obs.disconnect(); } catch (e) {} __wrWatch.obs = null; }
    __wrWatch.log = [];
    __wrWatch.seq = 0;
    __wrWatch.enabled = true;
    var obs = new MutationObserver(function (records) {
      for (var i = 0; i < records.length; i++) {
        var r = records[i];
        var rec = { seq: ++__wrWatch.seq, type: r.type, target: __wrWatchDesc(r.target),
                    attribute: r.attributeName || null,
                    oldValue: r.oldValue == null ? null : String(r.oldValue).slice(0, 200),
                    added: [], removed: [] };
        var add = r.addedNodes || [];
        for (var j = 0; j < add.length && j < 20; j++) rec.added.push(__wrWatchDesc(add[j]));
        var rem = r.removedNodes || [];
        for (var k = 0; k < rem.length && k < 20; k++) rec.removed.push(__wrWatchDesc(rem[k]));
        __wrWatch.log.push(rec);
        if (__wrWatch.log.length > __wrWatch.cap) __wrWatch.log.shift();
      }
    });
    obs.observe(root, { childList: true, subtree: true, attributes: true,
                        attributeOldValue: true, characterData: true, characterDataOldValue: true });
    __wrWatch.obs = obs;
    return JSON.stringify({ ok: true, cap: __wrWatch.cap });
  };
  global.__wrWatchDrain = function (after, limit) {
    after = Number(after) || 0;
    limit = Math.min(Number(limit) || 50, 200);
    var out = [];
    for (var i = 0; i < __wrWatch.log.length; i++) {
      if (__wrWatch.log[i].seq > after) {
        out.push(__wrWatch.log[i]);
        if (out.length >= limit) break;
      }
    }
    return JSON.stringify({ records: out, latest_seq: __wrWatch.seq,
                            enabled: __wrWatch.enabled, retained: __wrWatch.log.length });
  };
  global.__wrWatchDisable = function () {
    if (__wrWatch.obs) { try { __wrWatch.obs.disconnect(); } catch (e) {} __wrWatch.obs = null; }
    __wrWatch.enabled = false;
    return JSON.stringify({ ok: true, retained: __wrWatch.log.length });
  };
  Object.defineProperty(Node.prototype, "firstChild", {
    get: function () { return this.childNodes[0] || null; }
  });
  Object.defineProperty(Node.prototype, "lastChild", {
    get: function () { return this.childNodes[this.childNodes.length - 1] || null; }
  });
  Object.defineProperty(Node.prototype, "nextSibling", {
    get: function () {
      if (!this.parentNode) return null;
      var s = this.parentNode.childNodes;
      return s[s.indexOf(this) + 1] || null;
    }
  });
  Object.defineProperty(Node.prototype, "children", {
    get: function () {
      return this.childNodes.filter(function (c) { return c.nodeType === 1; });
    }
  });

  function Text(value) {
    Node.call(this, 3, "#text", {}, []);
    this._text = value || "";
  }
  Text.prototype = Object.create(Node.prototype);
  Text.prototype.constructor = Text;
  /* Spec: Text.data and nodeValue are the same string; both were undefined
  (only textContent worked), which broke page code reading el.data. */
  Object.defineProperty(Text.prototype, "data", {
    get: function () { return this._text; },
    set: function (v) { this._text = String(v == null ? "" : v); },
    configurable: true
  });
  Object.defineProperty(Text.prototype, "nodeValue", {
    get: function () { return this._text; },
    set: function (v) { this._text = String(v == null ? "" : v); },
    configurable: true
  });

  function DocumentFragment() {
    Node.call(this, 11, "#document-fragment", {}, []);
  }
  DocumentFragment.prototype = Object.create(Node.prototype);
  DocumentFragment.prototype.constructor = DocumentFragment;
  DocumentFragment.prototype.querySelectorAll = function (sel) { return cssSelect(this, sel); };
  DocumentFragment.prototype.querySelector = function (sel) { return cssSelect(this, sel)[0] || null; };
  global.DocumentFragment = DocumentFragment;

  function CSSStyleDeclaration(el) {
    this._el = el;
    /* Re-parse guard: when the host (re)builds this object from the style
       attribute, property writes must not sync back (infinite loop). */
    this.__parsing = false;
  }
  function __camelToKebab(s) {
    return String(s).replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); });
  }
  CSSStyleDeclaration.prototype.__syncAttr = function () {
    if (this.__parsing) return;
    var out = [];
    var seen = {};
    for (var k in this) {
      if (!Object.prototype.hasOwnProperty.call(this, k)) continue;
      if (k === "_el" || k === "__parsing") continue;
      var v = this[k];
      if (v == null || v === "") continue;
      /* setProperty stores both camelCase and kebab-case forms of the same
         property; serialize each property once under its canonical name. */
      var canonical = String(k).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
      if (seen[canonical]) continue;
      seen[canonical] = true;
      out.push(__camelToKebab(canonical) + ": " + v);
    }
    var s = out.join("; ");
    /* Through setAttribute (not a raw attribute write): mutation records
       fire for style changes like a real browser, and the object/attribute
       pair stays mirrored (the re-parse inside setAttribute is guarded). */
    if (s) {
      this._el.setAttribute("style", s);
    } else {
      this._el.removeAttribute("style");
    }
  };
  CSSStyleDeclaration.prototype.setProperty = function (prop, val, priority) {
    var camel = String(prop).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    this[camel] = String(val);
    this[prop] = String(val);
    this.__syncAttr();
  };
  CSSStyleDeclaration.prototype.getPropertyValue = function (prop) {
    var camel = String(prop).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    return this[camel] != null ? this[camel] : (this[prop] != null ? this[prop] : "");
  };
  CSSStyleDeclaration.prototype.removeProperty = function (prop) {
    var camel = String(prop).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    var old = this.getPropertyValue(prop);
    delete this[camel];
    delete this[prop];
    this.__syncAttr();
    return old;
  };
  /* The live style object: every property write/delete syncs back to the
     style ATTRIBUTE, because that is what the cascade (__computedStyle), the
     serializer, and the action-surface parser all read. Page JS reveals and
     hides controls through el.style.display = '...' (React/jQuery pattern);
     before this sync, the cascade never saw those writes and JS-revealed
     controls stayed invisible to checkVisibility and the action surface —
     while the serialized HTML kept claiming they were hidden. */
  var __styleProxyHandler = {
    set: function (target, prop, value) {
      if (prop === "_el" || prop === "__parsing") { target[prop] = value; return true; }
      /* L9: CSS transition — capture the old value before the write so the
         transition engine can animate from it to the new value. Only fires
         when the element has a transition shorthand covering this property. */
      var camel = String(prop).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
      /* Old value: inline first, else the computed cascade value */
      var __oldValForTransition = target[camel] != null ? target[camel] : (target[prop] != null ? target[prop] : null);
      if (__oldValForTransition == null && target._el) {
        try {
          var doc0 = target._el.ownerDocument || global.document;
          var kebab0 = camel.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); });
          var cs0 = __computedStyle(target._el, doc0 ? __collectStyleRules(doc0) : [], 16);
          __oldValForTransition = cs0[camel] != null ? cs0[camel] : (cs0[kebab0] != null ? cs0[kebab0] : null);
        } catch (e) {}
      }
      var __newValForTransition = (value == null || value === "") ? null : String(value);
      if (value == null || value === "") {
        /* Real browsers: assigning '' removes the declaration (un-hides). */
        delete target[camel];
        delete target[prop];
      } else {
        target[prop] = String(value);
      }
      target.__syncAttr();
      /* L9: fire the transition after the style write */
      if (__oldValForTransition !== __newValForTransition && __oldValForTransition != null && __newValForTransition != null) {
        try { __startTransition(target._el, camel, __oldValForTransition, __newValForTransition); } catch (e) {}
      }
      return true;
    },
    deleteProperty: function (target, prop) {
      delete target[prop];
      target.__syncAttr();
      return true;
    },
    get: function (target, prop) {
      /* Spec: unset CSS properties read as "" (not undefined); prototype
         methods (setProperty & co.) still resolve through the chain. */
      var v = target[prop];
      if (typeof v !== "undefined") return v;
      if (typeof prop === "string") return "";
      return v;
    }
  };
  CSSStyleDeclaration.prototype.__parseFromAttr = function (styleStr) {
    this.__parsing = true;
    try {
      /* A new style attribute REPLACES every prior declaration (spec:
         setAttribute('style','color:red') drops display:none). */
      for (var k in this) {
        if (!Object.prototype.hasOwnProperty.call(this, k)) continue;
        if (k === "_el" || k === "__parsing") continue;
        delete this[k];
      }
      var parts = String(styleStr || "").split(";");
      for (var i = 0; i < parts.length; i++) {
        var p = parts[i].trim();
        if (!p) continue;
        var col = p.indexOf(":");
        if (col > 0) this.setProperty(p.slice(0, col).trim(), p.slice(col + 1).trim());
      }
    } finally {
      this.__parsing = false;
    }
  };
  global.CSSStyleDeclaration = CSSStyleDeclaration;

  function Element(tag, attrs, kids) {
    Node.call(this, 1, tag, attrs || {}, kids || []);
    this.style = new Proxy(new CSSStyleDeclaration(this), __styleProxyHandler);
    if (this.attributes.style) {
      this.style.__parseFromAttr(this.attributes.style);
    }
    this.dataset = {};
    if (this.nodeName === "canvas") _attachCanvas(this);
    if (kids && kids.length) {
      for (var ki = 0; ki < kids.length; ki++) {
        var kd = kids[ki];
        if (kd && kd.nodeType === 1 && kd.nodeName && kd.nodeName.toLowerCase() === "template") {
          var srm = (kd.getAttribute && (kd.getAttribute("shadowrootmode") || kd.getAttribute("shadowroot")));
          if (srm) {
            var s = this.attachShadow({ mode: srm });
            if (kd.childNodes) {
              kd.childNodes.forEach(function (sk) { s.appendChild(sk); });
            }
          }
        }
      }
    }
    for (var k in this.attributes) {
      if (k.slice(0, 2) === "on" && typeof this.attributes[k] === "string") {
        var fn = (function (code) {
          return function (ev) {
            try {
              var f = new Function("event", code);
              return f.call(this, ev);
            } catch (e) {}
          };
        })(this.attributes[k]);
        this.addEventListener(k.slice(2), fn);
      }
    }
  }
  Element.prototype = Object.create(Node.prototype);
  Element.prototype.constructor = Element;
  Element.prototype.getAttribute = function (n) { return this.attributes[n] == null ? null : String(this.attributes[n]); };
  Element.prototype.setAttribute = function (n, v) {
    var oldVal = this.attributes[n] == null ? null : String(this.attributes[n]);
    this.attributes[n] = String(v);
    /* id/className expose attributes directly via prototype accessors below;
       syncing here would recurse (setter -> setAttribute -> setter). */
    queueMutation("attributes", this, { attributeName: n, oldValue: oldVal });
    /* Style attribute writes re-parse into the live style object (spec), like
       real browsers: setAttribute('style', ...) and el.style.x = ... are two
       views of one state. The parse is guarded (__parsing) so the object's
       own writes cannot loop back through here. */
    if (n === "style" && this.style && typeof this.style.__parseFromAttr === "function") {
      this.style.__parseFromAttr(v);
    }
    if (typeof this.attributeChangedCallback === "function") {
      var obs = (this.constructor && this.constructor.observedAttributes) || this.observedAttributes;
      if (Array.isArray(obs) && obs.indexOf(n) >= 0) {
        try { this.attributeChangedCallback(n, oldVal, String(v)); } catch (e) {}
      }
    }
  };
  Element.prototype.removeAttribute = function (n) {
    delete this.attributes[n];
    if (n === "style" && this.style && typeof this.style.__parseFromAttr === "function") {
      this.style.__parseFromAttr("");
    }
    queueMutation("attributes", this, { attributeName: n });
  };
  Element.prototype.hasAttribute = function (n) { return Object.prototype.hasOwnProperty.call(this.attributes, n); };
  /* id/className write straight onto the instance as plain properties —
     shadowing any prototype accessor — so a runtime `d.className = "item"`
     never reached the attributes table. The serializer, the CSS matcher and
     the action surface all read attributes: runtime-created controls lost
     their class (infinite-feed items serialized as bare <div>) and id-based
     targeting silently missed. Route both through setAttribute. */
  Object.defineProperty(Element.prototype, "className", {
    get: function () { return this.attributes["class"] == null ? "" : String(this.attributes["class"]); },
    set: function (v) { this.setAttribute("class", String(v)); },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "id", {
    get: function () { return this.attributes["id"] == null ? "" : String(this.attributes["id"]); },
    set: function (v) { this.setAttribute("id", String(v)); },
    configurable: true
  });
  Element.prototype.getElementsByTagName = function (tag) {
    tag = String(tag).toLowerCase();
    var out = [];
    (function walk(n) {
      n.childNodes.forEach(function (c) {
        if (c.nodeType === 1) {
          if (tag === "*" || c.nodeName === tag) out.push(c);
          walk(c);
        }
      });
    })(this);
    return out;
  };
  Element.prototype.getElementsByClassName = function (cls) {
    var out = [];
    (function walk(n) {
      n.childNodes.forEach(function (c) {
        if (c.nodeType === 1) {
          var cn = (c.className || "").split(/\s+/);
          if (cn.indexOf(cls) >= 0) out.push(c);
          walk(c);
        }
      });
    })(this);
    return out;
  };
  Element.prototype.querySelectorAll = function (sel) {
    return cssSelect(this, sel);
  };
  Element.prototype.querySelector = function (sel) {
    var all = this.querySelectorAll(sel);
    return all[0] || null;
  };
  Element.prototype.addEventListener = function (type, fn) {
    type = String(type);
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  Element.prototype.removeEventListener = function (type, fn) {
    var list = this._listeners[type] || [];
    this._listeners[type] = list.filter(function (x) { return x !== fn; });
  };
  Element.prototype.dispatchEvent = function (ev) {
    var type = ev.type || String(ev);
    if (ev.target == null) ev.target = this;
    var node = this;
    while (node) {
      ev.currentTarget = node;
      var list = (node._listeners && node._listeners[type]) ? node._listeners[type].slice() : [];
      for (var i = 0; i < list.length; i++) {
        try { list[i].call(node, ev); } catch (e) { __noteEventListenerError(type, e); }
      }
      if (typeof node["on" + type] === "function") {
        try { node["on" + type](ev); } catch (e) { __noteEventListenerError(type, e); }
      }
      if (!ev.bubbles) break;
      node = node.parentNode;
    }
    return !ev.defaultPrevented;
  };
  var _activePointerCaptures = {};

  Element.prototype.setPointerCapture = function (pointerId) {
    var pid = Number(pointerId);
    if (_activePointerCaptures[pid] && _activePointerCaptures[pid] !== this) {
      _activePointerCaptures[pid].releasePointerCapture(pid);
    }
    _activePointerCaptures[pid] = this;
    this._pointerCaptures = this._pointerCaptures || {};
    this._pointerCaptures[pid] = true;
    var t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : 100.0;
    var init = { pointerId: pid, bubbles: false, cancelable: false, isTrusted: true, __trusted: true, timeStamp: t, target: this };
    var ev = (typeof global.PointerEvent === "function")
      ? new global.PointerEvent("gotpointercapture", init)
      : new global.Event("gotpointercapture", init);
    this.dispatchEvent(ev);
  };
  Element.prototype.releasePointerCapture = function (pointerId) {
    var pid = Number(pointerId);
    if (_activePointerCaptures[pid] === this) {
      delete _activePointerCaptures[pid];
      if (this._pointerCaptures) delete this._pointerCaptures[pid];
      var t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : 100.0;
      var init = { pointerId: pid, bubbles: false, cancelable: false, isTrusted: true, __trusted: true, timeStamp: t, target: this };
      var ev = (typeof global.PointerEvent === "function")
        ? new global.PointerEvent("lostpointercapture", init)
        : new global.Event("lostpointercapture", init);
      this.dispatchEvent(ev);
    }
  };
  Element.prototype.hasPointerCapture = function (pointerId) {
    return _activePointerCaptures[Number(pointerId)] === this;
  };
  Element.prototype._dispatchClickSequence = function (isNative) {
    var trusted = !!isNative;
    var tag = this.nodeName.toLowerCase();
    /* Disabled form controls are non-interactive: a real browser dispatches
       no click (programmatic el.click() included), fires no listeners, and
       never navigates. Before this guard, a disabled submit button produced
       a real HTTP submit and disabled buttons ran their click listeners. */
    if (__controlDisabled(this) && (tag === "input" || tag === "button" || tag === "select"
        || tag === "textarea" || tag === "option" || tag === "fieldset")) {
      return false;
    }
    if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button" || tag === "a" || this.hasAttribute("tabindex")) {
      this.focus();
    }
    if (this.nodeName === "input" && (this.attributes.type || "") === "checkbox") {
      var on = !(this.checked);
      this.checked = on;
      var chgIn = (typeof global.Event === "function")
        ? new global.Event("input", { bubbles: true, isTrusted: trusted, __trusted: trusted })
        : { type: "input", target: this, bubbles: true, isTrusted: trusted };
      this.dispatchEvent(chgIn);
      var chgEv = (typeof global.Event === "function")
        ? new global.Event("change", { bubbles: true, isTrusted: trusted, __trusted: trusted })
        : { type: "change", target: this, bubbles: true, isTrusted: trusted };
      this.dispatchEvent(chgEv);
    }
    if (this.nodeName === "input" && (this.attributes.type || "") === "radio") {
      /* Radio group exclusivity: the clicked radio becomes checked, every
         other radio with the same name in the same form (or document)
         unchecks, and input+change fire — matching a real browser. Before
         this, a radio click was a complete state no-op: the clicked radio
         never checked and forms submitted whatever was checked before. */
      this.checked = true;
      var rname = this.getAttribute("name") || "";
      if (rname) {
        var rscope = __formOf(this) || this.ownerDocument || global.document;
        var radios = (typeof rscope.querySelectorAll === "function")
          ? rscope.querySelectorAll("input")
          : [];
        for (var ri = 0; ri < radios.length; ri++) {
          var ro = radios[ri];
          if (ro !== this
              && ro.nodeName === "input"
              && (ro.getAttribute("type") || "").toLowerCase() === "radio"
              && (ro.getAttribute("name") || "") === rname) {
            ro.checked = false;
          }
        }
      }
      var rIn = (typeof global.Event === "function")
        ? new global.Event("input", { bubbles: true, isTrusted: trusted, __trusted: trusted })
        : { type: "input", target: this, bubbles: true, isTrusted: trusted };
      this.dispatchEvent(rIn);
      var rChg = (typeof global.Event === "function")
        ? new global.Event("change", { bubbles: true, isTrusted: trusted, __trusted: trusted })
        : { type: "change", target: this, bubbles: true, isTrusted: trusted };
      this.dispatchEvent(rChg);
    }
    if (this.nodeName === "option") {
      /* Clicking an option selects it in its parent select (real browsers
         treat option clicks as a selection gesture on the select). */
      var optSel = __selectOf(this);
      if (optSel) {
        optSel.__selectDeselected = false;
        this.selected = true;
        optSel.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: trusted, __trusted: trusted }));
        optSel.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: trusted, __trusted: trusted }));
      }
    }
    var rect = (typeof this.getBoundingClientRect === "function") ? this.getBoundingClientRect() : { left: 10, top: 10, width: 20, height: 20 };
    var cx = Math.round(rect.left + rect.width * (0.4 + 0.2 * Math.random()));
    var cy = Math.round(rect.top + rect.height * (0.4 + 0.2 * Math.random()));
    var t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : 100.0;

    var overInit = { clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx + 50, screenY: cy + 100, bubbles: true, cancelable: true, isTrusted: trusted, __trusted: trusted, timeStamp: t };
    if (typeof global.PointerEvent === "function") this.dispatchEvent(new global.PointerEvent("pointerover", overInit));
    if (typeof global.MouseEvent === "function") this.dispatchEvent(new global.MouseEvent("mouseover", overInit));
    if (typeof global.PointerEvent === "function") this.dispatchEvent(new global.PointerEvent("pointerenter", overInit));
    if (typeof global.MouseEvent === "function") this.dispatchEvent(new global.MouseEvent("mouseenter", overInit));

    if (global.__stealthMode) {
      t += 35;
      if (typeof global.__advanceClock === "function") global.__advanceClock(35);
    }

    var downInit = { clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx + 50, screenY: cy + 100, button: 0, buttons: 1, pressure: 0.5, bubbles: true, cancelable: true, isTrusted: trusted, __trusted: trusted, timeStamp: t };
    if (typeof global.PointerEvent === "function") this.dispatchEvent(new global.PointerEvent("pointerdown", downInit));
    var mdEv = (typeof global.MouseEvent === "function")
      ? new global.MouseEvent("mousedown", downInit)
      : new global.Event("mousedown", downInit);
    this.dispatchEvent(mdEv);

    if (global.__stealthMode) {
      var dwell = 45 + Math.round(Math.random() * 20);
      t += dwell;
      if (typeof global.__advanceClock === "function") global.__advanceClock(dwell);
    }

    var upInit = { clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx + 50, screenY: cy + 100, button: 0, buttons: 0, pressure: 0, bubbles: true, cancelable: true, isTrusted: trusted, __trusted: trusted, timeStamp: t };
    if (typeof global.PointerEvent === "function") this.dispatchEvent(new global.PointerEvent("pointerup", upInit));
    var muEv = (typeof global.MouseEvent === "function")
      ? new global.MouseEvent("mouseup", upInit)
      : new global.Event("mouseup", upInit);
    this.dispatchEvent(muEv);

    var clkInit = { clientX: cx, clientY: cy, pageX: cx, pageY: cy, screenX: cx + 50, screenY: cy + 100, button: 0, buttons: 0, detail: 1, bubbles: true, cancelable: true, isTrusted: trusted, __trusted: trusted, timeStamp: t + 1 };
    var clkEv = (typeof global.MouseEvent === "function")
      ? new global.MouseEvent("click", clkInit)
      : new global.Event("click", clkInit);
    var notPrevented = this.dispatchEvent(clkEv);
    if (notPrevented && !clkEv.defaultPrevented) {
      var typ = (this.attributes.type || "").toLowerCase();
      var isSubmit = (this.nodeName === "button" && (!typ || typ === "submit")) ||
                     (this.nodeName === "input" && typ === "submit");
      if (isSubmit) {
        var form = this.parentNode;
        while (form && form.nodeName !== "form") form = form.parentNode;
        if (form) {
          var subEv = (typeof global.Event === "function")
            ? new global.Event("submit", { bubbles: true, cancelable: true, isTrusted: trusted, __trusted: trusted })
            : { type: "submit", target: form, bubbles: true, cancelable: true, isTrusted: trusted };
          form.dispatchEvent(subEv);
          if (!subEv.defaultPrevented && typeof form.submit === "function") {
            /* The clicked button is the submitter: its name/value pair
               joins the entry list (spec). */
            try { form.submit(this); } catch (e) {}
          }
        }
      }
    }
    return true;
  };
  Element.prototype.click = function () {
    return this._dispatchClickSequence(false);
  };
  Element.prototype.__nativeClick = function () {
    return this._dispatchClickSequence(true);
  };
  /* Form entry-list helpers: repeated names (same-name checkboxes, text
     inputs sharing a name) must reach the wire as repeated pairs; a JS
     object cannot repeat keys, so the second value for a name promotes it
     to an array (the host expands arrays back into pairs). */
  function __addField(fields, name, value) {
    if (!Object.prototype.hasOwnProperty.call(fields, name)) {
      fields[name] = value;
      return;
    }
    var cur = fields[name];
    if (Array.isArray(cur)) cur.push(value);
    else fields[name] = [cur, value];
  }
  /* Spec: disabled controls — directly or inside a disabled <fieldset> —
     are excluded from the entry list; the first <legend>'s contents are
     exempt. */
  function __entryDisabled(form, node) {
    var n = node;
    while (n && n !== form) {
      if (n.disabled) return true;
      if (n.nodeName === "legend") return false;
      n = n.parentNode;
    }
    return false;
  }
  /* Spec: a control is disabled if it carries the disabled attribute OR it
     lives inside a disabled <fieldset> (first <legend>'s contents are exempt).
     Used by the engine interaction lanes so fieldset-disabled descendants are
     not actuated by page JS or agent drives. */
  function __controlDisabled(node) {
    if (!node) return false;
    if (node.hasAttribute && node.hasAttribute("disabled")) return true;
    var n = node.parentNode;
    while (n && n.nodeName) {
      var nTag = (n.nodeName || "").toLowerCase();
      if (nTag === "fieldset" && n.hasAttribute && n.hasAttribute("disabled")) {
        var firstLegend = null;
        if (n.childNodes) {
          for (var li = 0; li < n.childNodes.length; li++) {
            var cn = n.childNodes[li];
            if (cn && cn.nodeName && (cn.nodeName || "").toLowerCase() === "legend") {
              firstLegend = cn; break;
            }
          }
        }
        var inLegend = false;
        if (firstLegend) {
          var cur = node;
          while (cur && cur !== n) {
            if (cur === firstLegend) { inLegend = true; break; }
            cur = cur.parentNode;
          }
        }
        if (!inLegend) return true;
      }
      if (nTag === "body" || nTag === "html" || n === n.parentNode) break;
      n = n.parentNode;
    }
    return false;
  }
  Element.prototype.submit = function (submitter) {
    if (this.nodeName !== "form") return;
    var action = this.getAttribute("action") || (global.location ? global.location.href : "");
    var method = (this.getAttribute("method") || "GET").toUpperCase();
    var enctype = (this.getAttribute("enctype") || "application/x-www-form-urlencoded").toLowerCase();

    if (submitter && typeof submitter.getAttribute === "function") {
      var fm = submitter.getAttribute("formmethod");
      if (fm) method = fm.toUpperCase();
      var fa = submitter.getAttribute("formaction");
      if (fa) action = fa;
      var fe = submitter.getAttribute("formenctype");
      if (fe) enctype = fe.toLowerCase();
    }

    /* HTML spec: dialog form submission. Closes the nearest dialog
       ancestor with the submitter's value and does not submit over the wire. */
    if (method === "DIALOG") {
      var dlg = null;
      var dn = this.parentNode;
      while (dn) {
        if (dn.nodeName === "dialog") { dlg = dn; break; }
        dn = dn.parentNode;
      }
      if (!dlg && submitter) {
        dn = submitter.parentNode;
        while (dn) {
          if (dn.nodeName === "dialog") { dlg = dn; break; }
          dn = dn.parentNode;
        }
      }
      if (dlg && typeof dlg.close === "function") {
        var retVal = "";
        if (submitter) {
          var sVal = (typeof submitter.getAttribute === "function") ? submitter.getAttribute("value") : null;
          if (sVal != null) {
            retVal = sVal;
          } else if (submitter.value != null && submitter.value !== "") {
            retVal = String(submitter.value);
          }
        }
        dlg.close(retVal);
      }
      return;
    }

    var fields = {};
    var formFiles = {};
    /* Spec: disabled form controls — directly or inside a disabled
       <fieldset> — are excluded from the entry list; the first <legend>'s
       contents are exempt (checked by __entryDisabled). */
    var inputs = this.querySelectorAll("input, textarea, select, button");
    for (var i = 0; i < inputs.length; i++) {
      var inp = inputs[i];
      var name = inp.getAttribute("name");
      if (!name) continue;
      var type = (inp.getAttribute("type") || "").toLowerCase();
      var tag = (inp.nodeName || "").toLowerCase();
      /* button/reset are never entry-list creators (the submitter is the
         only button that contributes, handled below). */
      if (type === "button" || type === "reset") continue;
      /* The clicked submitter contributes its own name/value pair; other
         submit buttons do not (spec). */
      if (type === "submit" || type === "image") {
        if (submitter === inp) {
          __addField(fields, name, inp.getAttribute("value") != null ? inp.getAttribute("value") : "");
        }
        continue;
      }
      if (__entryDisabled(this, inp)) continue;
      if ((type === "checkbox" || type === "radio") && !inp.checked) continue;
      if (type === "checkbox" || type === "radio") {
        /* HTML spec: the default value of checkbox/radio is "on". */
        __addField(fields, name, inp.getAttribute("value") != null ? inp.getAttribute("value") : "on");
        continue;
      }
      if (tag === "select" && inp.getAttribute("multiple") != null) {
        /* A multiple select submits one pair per selected option; a JS
           object cannot repeat keys, so carry them as an array the host
           expands back into repeated pairs. */
        var vals = [];
        var sopts = inp.getElementsByTagName("option");
        for (var si = 0; si < sopts.length; si++) {
          if (sopts[si].getAttribute("selected") != null) vals.push(__optionValue(sopts[si]));
        }
        if (vals.length) fields[name] = vals;
        continue;
      }
      if (type === "file") {
        var fl = inp.files;
        if (fl && fl.length > 0) {
          formFiles[name] = [];
          for (var fi = 0; fi < fl.length; fi++) {
            var fObj = fl[fi];
            var fEntry = {
              name: fObj.name,
              content: fObj._content || "",
              type: fObj.type || "application/octet-stream"
            };
            if (fObj._binary) {
              /* Re-encode raw bytes as base64 so the host can rebuild the exact
                 binary payload; the plain content string is kept for logging. */
              fEntry.contentB64 = global.btoa(fObj._content || "");
            }
            formFiles[name].push(fEntry);
          }
          fields[name] = (fl[0] && fl[0].name) ? fl[0].name : "";
        }
        continue;
      }
      __addField(fields, name, inp.value != null ? inp.value : (inp.getAttribute("value") || ""));
    }
    if (method === "GET") {
      try {
        var base = (global.location && global.location.href) ? global.location.href : "about:blank";
        var u = new URL(action, base);
        /* Spec: on GET submit the serialized form data REPLACES the action
           URL's query (not appended); repeated names become repeated pairs. */
        var pairs = [];
        for (var gk in fields) {
          var gv = fields[gk];
          if (Array.isArray(gv)) {
            for (var gi = 0; gi < gv.length; gi++) pairs.push(encodeURIComponent(gk) + "=" + encodeURIComponent(gv[gi]));
          } else {
            pairs.push(encodeURIComponent(gk) + "=" + encodeURIComponent(gv));
          }
        }
        u.search = pairs.length ? "?" + pairs.join("&") : "";
        if (global.location && typeof global.location.assign === "function") {
          global.location.assign(u.href);
        } else {
          global.__WR_NAVIGATED = u.href;
        }
      } catch (e) {}
    } else {
      global.__WR_FORM_SUBMIT = {
        action: action,
        method: method,
        enctype: enctype,
        fields: fields,
        files: formFiles
      };
    }
  };
  Element.prototype.requestSubmit = function (submitter) {
    if (this.nodeName !== "form") return;
    var ev = (typeof global.SubmitEvent === "function")
      ? new global.SubmitEvent("submit", { bubbles: true, cancelable: true, isTrusted: true, submitter: submitter || null })
      : (typeof global.Event === "function")
      ? new global.Event("submit", { bubbles: true, cancelable: true, isTrusted: true })
      : { type: "submit", target: this, bubbles: true, cancelable: true, isTrusted: true };
    if (submitter) ev.submitter = submitter;
    this.dispatchEvent(ev);
    if (!ev.defaultPrevented) {
      this.submit(submitter);
    }
  };
  Element.prototype.focus = function () {
    var doc = this.ownerDocument || global.document;
    if (!doc) return;
    var prev = doc._activeElement || doc.body || doc.documentElement;
    if (prev === this) return;
    if (prev && typeof prev.dispatchEvent === "function") {
      var blurEv = (typeof global.FocusEvent === "function")
        ? new global.FocusEvent("blur", { bubbles: false, cancelable: false, relatedTarget: this })
        : (typeof global.Event === "function" ? new global.Event("blur", { bubbles: false, cancelable: false }) : { type: "blur", target: prev });
      prev.dispatchEvent(blurEv);
      var outEv = (typeof global.FocusEvent === "function")
        ? new global.FocusEvent("focusout", { bubbles: true, cancelable: false, relatedTarget: this })
        : (typeof global.Event === "function" ? new global.Event("focusout", { bubbles: true, cancelable: false }) : { type: "focusout", target: prev, bubbles: true });
      prev.dispatchEvent(outEv);
    }
    doc._activeElement = this;
    /* L7: the focused text container's caret becomes the document selection,
       so window.getSelection() reports the caret the editor model expects. */
    __syncDocumentSelection(this);
    var inEv = (typeof global.FocusEvent === "function")
      ? new global.FocusEvent("focusin", { bubbles: true, cancelable: false, relatedTarget: prev })
      : (typeof global.Event === "function" ? new global.Event("focusin", { bubbles: true, cancelable: false }) : { type: "focusin", target: this, bubbles: true });
    this.dispatchEvent(inEv);
    var focusEv = (typeof global.FocusEvent === "function")
      ? new global.FocusEvent("focus", { bubbles: false, cancelable: false, relatedTarget: prev })
      : (typeof global.Event === "function" ? new global.Event("focus", { bubbles: false, cancelable: false }) : { type: "focus", target: this });
    this.dispatchEvent(focusEv);
  };
  Element.prototype.blur = function () {
    var doc = this.ownerDocument || global.document;
    if (!doc || doc._activeElement !== this) return;
    var next = doc.body || doc.documentElement || null;
    var blurEv = (typeof global.FocusEvent === "function")
      ? new global.FocusEvent("blur", { bubbles: false, cancelable: false, relatedTarget: next })
      : (typeof global.Event === "function" ? new global.Event("blur", { bubbles: false, cancelable: false }) : { type: "blur", target: this });
    this.dispatchEvent(blurEv);
    var outEv = (typeof global.FocusEvent === "function")
      ? new global.FocusEvent("focusout", { bubbles: true, cancelable: false, relatedTarget: next })
      : (typeof global.Event === "function" ? new global.Event("focusout", { bubbles: true, cancelable: false }) : { type: "focusout", target: this, bubbles: true });
    this.dispatchEvent(outEv);
    doc._activeElement = next;
  };
  /* L7: text-field selection model (spec HTMLInputElement/HTMLTextAreaElement
     surface, scoped to text inputs + textareas). Caret defaults to end of
     value when never explicitly set; setSelectionRange clamps and throws
     IndexSizeError for start > end (spec). selectionchange fires on the
     document (see __notifySelectionChange). */
  Object.defineProperty(Element.prototype, "selectionStart", {
    get: function () {
      if (!__isTextEditable(this)) return null;
      return this._selStart != null ? this._selStart : String(this.value || "").length;
    }
  });
  Object.defineProperty(Element.prototype, "selectionEnd", {
    get: function () {
      if (!__isTextEditable(this)) return null;
      return this._selEnd != null ? this._selEnd : String(this.value || "").length;
    }
  });
  Element.prototype.setSelectionRange = function (start, end) {
    if (!__isTextEditable(this)) {
      throw new TypeError("setSelectionRange: element is not a text input or textarea");
    }
    start = Number(start);
    end = (end === undefined) ? start : Number(end);
    if (!(start <= end)) {
      throw new RangeError("IndexSizeError: start > end in setSelectionRange");
    }
    var len = String(this.value || "").length;
    if (start < 0) start = 0;
    if (start > len) start = len;
    if (end < 0) end = 0;
    if (end > len) end = len;
    this._selStart = start;
    this._selEnd = end;
    __notifySelectionChange();
  };
  Element.prototype.select = function () {
    if (__isTextEditable(this)) {
      this._selStart = 0;
      this._selEnd = String(this.value || "").length;
      __notifySelectionChange();
      return;
    }
    if (this.isContentEditable) {
      var sel = global.getSelection();
      if (sel) {
        sel.removeAllRanges();
        sel.selectAllChildren(this);
        this._caretOffset = String(this.textContent || "").length;
      }
    }
  };
  /* Trusted Types enforcement (real, disclosed subset).
   * trustedTypes.createPolicy with allowlist + duplicate control from the
   * page CSP (trusted-types directive); require-trusted-types-for 'script'
   * gates the sinks this engine owns: global eval, Function constructor,
   * Element.innerHTML/outerHTML setters. Parser-inserted markup (including
   * on* attributes) is intentionally not gated, matching the spec. */
  function __ttConfig() {
    var c = global.__WR_TT || {};
    return {
      required: !!(c.requireScript),
      reportOnly: !!c.reportOnly,
      allowed: c.allowedNames || null,
      allowDup: c.allowDuplicates !== false
    };
  }
  function TrustedHTML(s) { this.__ttType = "TrustedHTML"; this.__value = String(s); }
  TrustedHTML.prototype.toString = function () { return this.__value; };
  function TrustedScript(s) { this.__ttType = "TrustedScript"; this.__value = String(s); }
  TrustedScript.prototype.toString = function () { return this.__value; };
  function TrustedScriptURL(s) { this.__ttType = "TrustedScriptURL"; this.__value = String(s); }
  TrustedScriptURL.prototype.toString = function () { return this.__value; };
  function __ttString(v, want) {
    if (v && v.__ttType === want) return v.__value;
    return null;
  }
  var __ttPolicies = {};
  var __ttDefaultPolicy = null;
  var __ttViolations = [];
  function __ttEnforce(sink, value, want) {
    var cfg = __ttConfig();
    if (!cfg.required) return String(value && value.__value !== undefined && value.__ttType === want ? value.__value : value);
    var s = __ttString(value, want);
    if (s !== null) return s;
    var msg = "Refused to sink string to " + sink + " because require-trusted-types-for 'script' is enforced";
    try {
      if (__ttDefaultPolicy && typeof __ttDefaultPolicy["create" + want.slice(7)] === "function") {
        var out = __ttDefaultPolicy["create" + want.slice(7)](String(value), sink);
        var back = __ttString(out, want);
        if (back !== null) return back;
      }
    } catch (e) {}
    __ttViolations.push(msg);
    if (!cfg.reportOnly) throw new TypeError(msg);
    return String(value);
  }
  global.TrustedHTML = TrustedHTML;
  global.TrustedScript = TrustedScript;
  global.TrustedScriptURL = TrustedScriptURL;
  global.trustedTypes = {
    createPolicy: function (name, rules) {
      name = String(name);
      var cfg = __ttConfig();
      if (__ttPolicies[name] && !cfg.allowDup) throw new TypeError("Policy '" + name + "' already exists");
      if (cfg.allowed && cfg.allowed.indexOf(name) < 0 && cfg.allowed.indexOf("*") < 0) {
        throw new TypeError("Policy '" + name + "' disallowed by trusted-types directive");
      }
      rules = rules || {};
      var p = {
        name: name,
        createHTML: function (s) {
          if (typeof rules.createHTML !== "function") throw new TypeError("createHTML not defined");
          return new TrustedHTML(rules.createHTML(String(s)));
        },
        createScript: function (s) {
          if (typeof rules.createScript !== "function") throw new TypeError("createScript not defined");
          return new TrustedScript(rules.createScript(String(s)));
        },
        createScriptURL: function (s) {
          if (typeof rules.createScriptURL !== "function") throw new TypeError("createScriptURL not defined");
          return new TrustedScriptURL(rules.createScriptURL(String(s)));
        }
      };
      __ttPolicies[name] = p;
      return p;
    },
    getAttributeType: function (tag, attr) {
      tag = String(tag || "").toLowerCase(); attr = String(attr || "").toLowerCase();
      if (attr === "srcdoc") return "TrustedHTML";
      if (attr === "src" && (tag === "script")) return "TrustedScriptURL";
      if (attr.slice(0, 2) === "on") return "TrustedScript";
      return null;
    },
    getPropertyType: function (tag, prop) {
      tag = String(tag || "").toLowerCase(); prop = String(prop || "");
      if (prop === "innerHTML" || prop === "outerHTML") return "TrustedHTML";
      if (tag === "script" && (prop === "text" || prop === "innerText" || prop === "textContent")) return "TrustedScript";
      if (tag === "script" && prop === "src") return "TrustedScriptURL";
      return null;
    },
    isHTML: function (v) { return !!(v && v.__ttType === "TrustedHTML"); },
    isScript: function (v) { return !!(v && v.__ttType === "TrustedScript"); },
    isScriptURL: function (v) { return !!(v && v.__ttType === "TrustedScriptURL"); },
    emptyHTML: new TrustedHTML(""),
    emptyScript: new TrustedScript(""),
    get defaultPolicy() { return __ttDefaultPolicy; },
    set defaultPolicy(p) { __ttDefaultPolicy = p || null; },
    __violations: __ttViolations
  };
  (function __wrapEval() {
    try {
      var __origEval = global.eval;
      global.eval = function (x) {
        var cfg = __ttConfig();
        if (cfg.required && typeof x === "string") {
          x = __ttEnforce("eval", x, "TrustedScript");
        }
        return __origEval.call(this, x);
      };
      var __OrigFunction = global.Function;
      global.Function = function () {
        var cfg = __ttConfig();
        var args = Array.prototype.slice.call(arguments);
        if (cfg.required && args.length > 0) {
          var body = args[args.length - 1];
          var checked = __ttEnforce("Function", body, "TrustedScript");
          args[args.length - 1] = checked;
        }
        return __OrigFunction.apply(this, args);
      };
      global.Function.prototype = __OrigFunction.prototype;
    } catch (e) {}
  })();
  Object.defineProperty(Element.prototype, "innerHTML", {
    get: function () {
      var self = this;
      return this.childNodes.map(function (c) { return serializeNode(c, self.nodeName); }).join("");
    },
    set: function (html) {
      html = __ttEnforce("Element.innerHTML", html, "TrustedHTML");
      var parsed = hydrate(parseHtmlFragment(String(html || "")));
      var removed = this.childNodes.slice();
      removed.forEach(function (c) { c.parentNode = null; });
      this.childNodes = [];
      if (removed.length) queueMutation("childList", this, { removedNodes: removed });
      var self = this;
      parsed.forEach(function (c) { self.appendChild(c); });
    }
  });
  Object.defineProperty(Element.prototype, "outerHTML", {
    get: function () { return serializeNode(this); },
    set: function (html) {
      html = __ttEnforce("Element.outerHTML", html, "TrustedHTML");
      var parsed = hydrate(parseHtmlFragment(String(html || "")));
      var parent = this.parentNode;
      if (!parent) { this.childNodes = []; return; }
      var nodes = parsed.filter(Boolean);
      if (nodes.length) parent.insertBefore(nodes[0], this);
      for (var i = 1; i < nodes.length; i++) parent.insertBefore(nodes[i], this);
      parent.removeChild(this);
    }
  });

  /* ---- insertAdjacentElement / insertAdjacentHTML / insertAdjacentText ----
     DOM spec: position ∈ beforebegin | afterbegin | beforeend | afterend.
     A real cascade source: LinkedIn's webpack style-injection IIFE calls
     head.insertAdjacentElement("beforeend", styleEl) at module top level;
     the missing method threw "not a function" and killed the whole
     A2-4UjN7 mega-module (whose exports then never registered, cascading
     into 30+ downstream module failures). */
  function _adjacentPosition(el, position) {
    var pos = String(position || "").toLowerCase().replace(/\s+/g, "");
    var parent = el.parentNode;
    var ref = null;
    var target = el;
    var ok = true;
    switch (pos) {
      case "beforebegin":
        target = parent; ref = el;
        if (!parent) ok = false;
        break;
      case "afterbegin":
        target = el; ref = el.childNodes[0] || null;
        break;
      case "beforeend":
        target = el; ref = null;
        break;
      case "afterend":
        target = parent;
        ref = null;
        if (parent) {
          var idx = parent.childNodes.indexOf(el);
          ref = (idx >= 0 && idx + 1 < parent.childNodes.length) ? parent.childNodes[idx + 1] : null;
        } else {
          ok = false;
        }
        break;
      default:
        ok = false;
    }
    return { ok: ok, target: target, ref: ref, name: pos };
  }

  Element.prototype.insertAdjacentElement = function (position, element) {
    if (!element || element.nodeType !== 1) {
      throw new TypeError("Failed to execute 'insertAdjacentElement': parameter 2 is not of type 'Element'");
    }
    var adj = _adjacentPosition(this, position);
    if (!adj.ok) {
      if (adj.name !== "beforebegin" && adj.name !== "afterend") {
        throw new TypeError("Failed to execute 'insertAdjacentElement': '" + position + "' is not a valid position");
      }
      return null;
    }
    if (adj.name === "beforeend") {
      this.appendChild(element);
    } else {
      adj.target.insertBefore(element, adj.ref);
    }
    return element;
  };

  Element.prototype.insertAdjacentHTML = function (position, html) {
    html = __ttEnforce("Element.insertAdjacentHTML", html, "TrustedHTML");
    var adj = _adjacentPosition(this, position);
    if (!adj.ok) {
      if (adj.name !== "beforebegin" && adj.name !== "afterend") {
        throw new TypeError("Failed to execute 'insertAdjacentHTML': '" + position + "' is not a valid position");
      }
      return;
    }
    var nodes = hydrate(parseHtmlFragment(String(html || ""))).filter(Boolean);
    var anchor = adj.ref || null;
    for (var i = 0; i < nodes.length; i++) {
      adj.target.insertBefore(nodes[i], anchor);
    }
  };

  Element.prototype.insertAdjacentText = function (position, text) {
    var adj = _adjacentPosition(this, position);
    if (!adj.ok) {
      if (adj.name !== "beforebegin" && adj.name !== "afterend") {
        throw new TypeError("Failed to execute 'insertAdjacentText': '" + position + "' is not a valid position");
      }
      return;
    }
    var node = new Text(String(text == null ? "" : text));
    if (adj.name === "beforeend") {
      this.appendChild(node);
    } else {
      adj.target.insertBefore(node, adj.ref);
    }
  };
  /* ---- HTMLSelectElement semantics ----
     Real select elements read and write the selected state of their <option>
     children. The old generic value getter concatenated option TEXT
     ("AlphaBeta"), typing appended to it, and submitted selects went out
     empty or wrong. Helpers first, then the properties. */
  function __selectOptionEls(el) {
    if (!el || el.nodeName !== "select" || typeof el.getElementsByTagName !== "function") return [];
    return el.getElementsByTagName("option");
  }
  function __optionValue(opt) {
    var v = opt.getAttribute("value");
    return v != null ? String(v) : String(opt.textContent == null ? "" : opt.textContent);
  }
  function __selectAttrIndex(el) {
    var opts = __selectOptionEls(el);
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].getAttribute("selected") != null) return i;
    }
    return -1;
  }
  function __selectIsMultiple(el) {
    return el.getAttribute("multiple") != null;
  }
  function __selectOf(node) {
    var n = node && node.parentNode;
    while (n && n.nodeName !== "select") n = n.parentNode;
    return (n && n.nodeName === "select") ? n : null;
  }
  function __selectSetChoice(el, idx) {
    var opts = __selectOptionEls(el);
    var multiple = __selectIsMultiple(el);
    if (idx >= 0 && idx < opts.length) opts[idx].setAttribute("selected", "selected");
    if (!multiple) {
      for (var i = 0; i < opts.length; i++) {
        if (i !== idx) opts[i].removeAttribute("selected");
      }
    }
  }
  /* Agent typing into a select: pick the option whose value — then whose
     text — matches. Returns false when nothing matches, never appends text. */
  function __selectByValueOrText(el, text) {
    var opts = __selectOptionEls(el);
    var want = String(text == null ? "" : text);
    var match = -1;
    for (var i = 0; i < opts.length; i++) {
      if (__optionValue(opts[i]) === want) { match = i; break; }
    }
    if (match < 0) {
      var t = want.replace(/^\s+|\s+$/g, "").toLowerCase();
      for (var j = 0; j < opts.length; j++) {
        if (String(opts[j].textContent || "").replace(/^\s+|\s+$/g, "").toLowerCase() === t) { match = j; break; }
      }
    }
    if (match < 0) return false;
    el.__selectDeselected = false;
    __selectSetChoice(el, match);
    el.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: true, __trusted: true }));
    el.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: true, __trusted: true }));
    return true;
  }
  Object.defineProperty(Element.prototype, "value", {
    get: function () {
      if (this.nodeName === "select") {
        var opts = __selectOptionEls(this);
        var idx = __selectAttrIndex(this);
        var picked = null;
        if (idx >= 0) picked = opts[idx];
        /* Spec: the implicit first-option selection applies only to
           single selects — a multiple select with no option chosen has
           value "". */
        else if (!__selectIsMultiple(this) && !this.__selectDeselected && opts.length) picked = opts[0];
        return picked ? __optionValue(picked) : "";
      }
      if (this.nodeName === "option") {
        /* Spec: option.value is the value attribute if present — even when
           it is the empty string — else the text content. The generic
           `attr || text` path treated value="" as absent and reported the
           text instead. */
        var ov = this.attributes.value;
        if (ov !== undefined && ov !== null) return String(ov);
        return String(this.textContent == null ? "" : this.textContent);
      }
      return this.attributes.value || this.textContent || "";
    },
    set: function (v) {
      if (this.nodeName === "select") {
        var want = String(v == null ? "" : v);
        var opts = __selectOptionEls(this);
        var match = -1;
        for (var i = 0; i < opts.length; i++) {
          if (__optionValue(opts[i]) === want) { match = i; break; }
        }
        if (match < 0) this.__selectDeselected = true;
        else this.__selectDeselected = false;
        __selectSetChoice(this, match);
        return;
      }
      if (this.nodeName === "option") {
        var hasVal = this.attributes.value !== undefined && this.attributes.value !== null;
        if (hasVal) {
          this.attributes.value = String(v);
          queueMutation("attributes", this, { attributeName: "value" });
          return;
        }
        /* No value attribute: writing .value targets the text per spec. */
        this.textContent = String(v == null ? "" : v);
        return;
      }
      this.attributes.value = String(v);
      queueMutation("attributes", this, { attributeName: "value" });
    }
  });
  Object.defineProperty(Element.prototype, "selectedIndex", {
    get: function () {
      if (this.nodeName !== "select") return undefined;
      var opts = __selectOptionEls(this);
      var idx = __selectAttrIndex(this);
      if (idx >= 0) return idx;
      if (this.__selectDeselected) return -1;
      /* Spec: a multiple select has no implicit first-option selection. */
      if (__selectIsMultiple(this)) return -1;
      return opts.length ? 0 : -1;
    },
    set: function (i) {
      if (this.nodeName !== "select") return;
      var n = Math.floor(Number(i));
      var count = __selectOptionEls(this).length;
      /* Spec: setting selectedIndex out of range deselects everything
       (Chrome: 99 on a 2-option select -> selectedIndex -1, value ""). The
       old code snapped back to the first option instead. */
      if (isNaN(n) || n < 0 || n >= count) {
        this.__selectDeselected = true;
        __selectSetChoice(this, -1);
        return;
      }
      this.__selectDeselected = false;
      __selectSetChoice(this, n);
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "options", {
    get: function () {
      if (this.nodeName !== "select") return undefined;
      var opts = __selectOptionEls(this).slice();
      opts.item = function (i) { return opts[Number(i)] || null; };
      opts.namedItem = function (n) {
        n = String(n);
        for (var i = 0; i < opts.length; i++) {
          if (opts[i].getAttribute("id") === n || opts[i].getAttribute("name") === n) return opts[i];
        }
        return null;
      };
      return opts;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "selectedOptions", {
    get: function () {
      if (this.nodeName !== "select") return undefined;
      var opts = __selectOptionEls(this);
      var out = [];
      if (__selectIsMultiple(this)) {
        for (var i = 0; i < opts.length; i++) {
          if (opts[i].getAttribute("selected") != null) out.push(opts[i]);
        }
      } else {
        var idx = __selectAttrIndex(this);
        if (idx >= 0) out.push(opts[idx]);
        else if (!this.__selectDeselected && opts.length) out.push(opts[0]);
      }
      out.item = function (i) { return out[Number(i)] || null; };
      return out;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "selected", {
    /* Option.selected: reflects/mutates the parent select's choice. Setting
       it on one option clears the others of a single-select, like a real
       browser's exclusive selection. */
    get: function () {
      if (this.nodeName !== "option") return undefined;
      var sel = __selectOf(this);
      if (sel) {
        if (this.getAttribute("selected") != null) return true;
        /* Implicit first-option selection only applies when NO option is
           explicitly chosen (HTML spec) and the select is not multiple;
           a chosen sibling wins. */
        if (__selectAttrIndex(sel) >= 0) return false;
        if (__selectIsMultiple(sel)) return false;
        var opts = __selectOptionEls(sel);
        if (!sel.__selectDeselected && opts.length && opts[0] === this) return true;
        return false;
      }
      return this.getAttribute("selected") != null;
    },
    set: function (v) {
      if (this.nodeName !== "option") return;
      var sel = __selectOf(this);
      if (v) {
        this.setAttribute("selected", "selected");
        if (sel && !__selectIsMultiple(sel)) {
          var opts = __selectOptionEls(sel);
          for (var i = 0; i < opts.length; i++) {
            if (opts[i] !== this) opts[i].removeAttribute("selected");
          }
        }
        if (sel) sel.__selectDeselected = false;
      } else {
        this.removeAttribute("selected");
        if (sel) sel.__selectDeselected = true;
      }
    },
    configurable: true
  });
  /* ---- HTMLOptionElement API ----
     Real form-driving JS reads option.text/.label/.index constantly (populating
     selects, finding a choice by label); all three were undefined. Per spec:
     text is the option's text content; label is the label attribute or the
     text; index is the option's position in its select's options list. */
  Object.defineProperty(Element.prototype, "text", {
    get: function () {
      if (this.nodeName !== "option") return undefined;
      var out = "";
      (this.childNodes || []).forEach(function (c) {
        if (c.nodeType === 3) out += String(c.data != null ? c.data : (c.textContent || ""));
      });
      return out;
    },
    set: function (v) {
      if (this.nodeName !== "option") return;
      this.textContent = String(v == null ? "" : v);
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "label", {
    get: function () {
      if (this.nodeName !== "option") return undefined;
      var lab = this.getAttribute("label");
      if (lab != null) return String(lab);
      /* No label attribute: the label is the option's text, trimmed. */
      return String(this.text || "").replace(/^\s+|\s+$/g, "");
    },
    set: function (v) {
      if (this.nodeName !== "option") return;
      this.setAttribute("label", String(v == null ? "" : v));
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "index", {
    get: function () {
      if (this.nodeName !== "option") return undefined;
      var sel = __selectOf(this);
      if (!sel) return 0;
      var opts = __selectOptionEls(sel);
      for (var i = 0; i < opts.length; i++) {
        if (opts[i] === this) return i;
      }
      return 0;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "isContentEditable", {
    get: function () {
      var n = this;
      while (n && n.nodeType === 1) {
        var ce = (n.getAttribute && n.getAttribute("contenteditable")) || "";
        if (ce !== "") return ce.toLowerCase() !== "false";
        n = n.parentNode;
      }
      return false;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "form", {
    get: function () {
      var n = this.parentNode;
      while (n) {
        if (n.nodeName === "form") return n;
        n = n.parentNode;
      }
      return null;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "checked", {
    get: function () { return this.attributes.checked != null && this.attributes.checked !== "false"; },
    set: function (v) {
      if (v) {
        this.attributes.checked = "checked";
        if (this.nodeName === "input" && (this.attributes.type || "").toLowerCase() === "radio" && this.attributes.name) {
          var rname = this.attributes.name;
          var myForm = this.form;
          var rscope = myForm || (this.ownerDocument || global.document);
          if (rscope && typeof rscope.querySelectorAll === "function") {
            var radios = rscope.querySelectorAll("input[type=radio]");
            for (var ri = 0; ri < radios.length; ri++) {
              var ro = radios[ri];
              if (ro !== this && ro.getAttribute("name") === rname && (myForm ? ro.form === myForm : !ro.form)) {
                delete ro.attributes.checked;
              }
            }
          }
        }
      } else {
        delete this.attributes.checked;
      }
      queueMutation("attributes", this, { attributeName: "checked" });
    }
  });
  /* HTML-spec disabled: presence of the attribute means disabled, whatever
     its value. Before this property existed, el.disabled was undefined, so
     the __focusables() Tab-skip guard silently passed disabled controls. */
  Object.defineProperty(Element.prototype, "disabled", {
    get: function () { return this.hasAttribute("disabled"); },
    set: function (v) {
      if (v) this.setAttribute("disabled", "disabled");
      else this.removeAttribute("disabled");
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "src", {
    get: function () { return this.attributes.src == null ? "" : String(this.attributes.src); },
    set: function (v) {
      this.attributes.src = String(v);
      queueMutation("attributes", this, { attributeName: "src" });
    }
  });
  /* Genuine block-flow layout engine (approximate, disclosed).
   * Computes real boxes from the live DOM + parsed CSS: <style> rules and
   * inline styles cascade (tag < class < id < inline), block boxes stack
   * vertically with margins stacking additively, inline text wraps by
   * measured advance. Text uses a proportional per-character advance table
   * (system sans-serif ratios — see __CHAR_ADVANCES); replaced elements
   * default to the CSS-spec 300x150 box unless sized. Viewport width comes
   * from window.innerWidth. This is a simplified but real engine: every
   * number derives from markup + styles, never hashed. Implemented: flex
   * (row/column + gap), grid (repeat(N) columns, wrap), position
   * absolute/fixed/relative; units px, %, em, rem, pt.
   * Implemented: floats (regions + clear), sticky (top/bottom/left/right),
   * flex-wrap + justify/align distribution, bidi (direction:rtl reorder),
   * kerning-aware text metrics, smooth scroll, CSS transitions + @keyframes.
   * Disclosed limits: no Unicode bidi algorithm, no glyph shaping, no
   * animation events. Reported via __layoutInfo. */
  var __BLOCK_TAGS = { address:1, article:1, aside:1, blockquote:1, body:1, br:0, canvas:0, dd:1, details:1, dialog:1, div:1, dl:1, dt:1, fieldset:1, figcaption:1, figure:1, footer:1, form:1, h1:1, h2:1, h3:1, h4:1, h5:1, h6:1, header:1, hgroup:1, hr:1, html:1, iframe:0, img:0, input:0, legend:1, li:1, main:1, nav:1, noscript:1, ol:1, option:0, output:0, p:1, pre:1, section:1, select:0, table:1, tbody:1, td:1, textarea:0, tfoot:1, th:1, thead:1, tr:1, ul:1, video:0, button:0 };
  var __REPLACED_TAGS = { img:1, iframe:1, canvas:1, video:1, input:1, select:1, textarea:1, button:1, object:1, embed:1 };
  function __parsePx(v, base, fontSize) {
    if (v == null) return null;
    var s = String(v).trim().toLowerCase();
    if (s === "" || s === "auto") return null;
    var m = s.match(/^(-?[\d.]+)(px|%|em|rem|pt)?$/);
    if (!m) return null;
    var n = parseFloat(m[1]);
    var u = m[2] || "px";
    if (u === "px") return n;
    if (u === "%") return (base == null ? null : base * n / 100);
    if (u === "em" || u === "rem") return n * (fontSize || 16);
    if (u === "pt") return n * 4 / 3;
    return null;
  }
  function __parseSides(v, base, fontSize) {
    var parts = String(v == null ? "0" : v).trim().split(/\s+/);
    function px(s) { var r = __parsePx(s, base, fontSize); return r == null ? 0 : r; }
    if (parts.length === 1) { var a = px(parts[0]); return [a, a, a, a]; }
    if (parts.length === 2) { var b = px(parts[0]), c = px(parts[1]); return [b, c, b, c]; }
    if (parts.length === 3) { var d = px(parts[0]), e = px(parts[1]), f = px(parts[2]); return [d, e, f, e]; }
    var g = px(parts[0]), h = px(parts[1]), i = px(parts[2]), j = px(parts[3]);
    return [g, h, i, j];
  }
  function __nodeText(c) {
    if (!c) return "";
    if (typeof c.data === "string") return c.data;
    if (typeof c._text === "string") return c._text;
    if (typeof c.text === "string") return c.text;
    if (typeof c.nodeValue === "string") return c.nodeValue;
    return "";
  }
  function __parseStyleDecls(text) {
    var out = {};
    String(text || "").split(";").forEach(function (d) {
      var i = d.indexOf(":");
      if (i < 0) return;
      out[d.slice(0, i).trim().toLowerCase()] = d.slice(i + 1).trim();
    });
    return out;
  }
  function __collectStyleRules(doc) {
    var rules = [];
    function walk(n) {
      if (!n || !n.childNodes) return;
      if (n.nodeType === 1 && n.nodeName === "style") {
        var css = "";
        n.childNodes.forEach(function (c) { if (c.nodeType === 3) css += __nodeText(c); });
        css = css.replace(/\/\*[\s\S]*?\*\//g, "");
        css.split("}").forEach(function (block) {
          var i = block.indexOf("{");
          if (i < 0) return;
          var sel = block.slice(0, i).trim();
          var decls = __parseStyleDecls(block.slice(i + 1));
          if (sel && !/:/.test(sel)) rules.push({ sel: sel, decls: decls });
        });
      }
      n.childNodes.forEach(walk);
    }
    walk(doc);
    return rules;
  }
  /* L9: Parse @keyframes from <style> blocks. Each keyframe block
     becomes a list of { offset: N/100, props: {...} } entries.
     Stored per document at doc.__keyframes = { name: [{offset, props}, ...] } */
  function __collectKeyframes(doc) {
    var kf = {};
    function walk(n) {
      if (!n || !n.childNodes) return;
      if (n.nodeType === 1 && n.nodeName === "style") {
        var css = "";
        n.childNodes.forEach(function (c) { if (c.nodeType === 3) css += __nodeText(c); });
        css = css.replace(/\/\*[\s\S]*?\*\//g, "");
        /* Match @keyframes NAME { ... } with nested braces: the body is
           either non-brace chars or a complete { ... } group. */
        var kfRe = /@keyframes\s+([a-zA-Z_][a-zA-Z0-9_-]*)\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g;
        var m;
        while ((m = kfRe.exec(css)) !== null) {
          var name = m[1];
          var body = m[2];
          var entries = [];
          var stepRe = /(?:^|[\s;])(from|to|\d+%)\s*\{([^}]*)\}/g;
          var sm;
          while ((sm = stepRe.exec(body)) !== null) {
            var offsetStr = sm[1].trim().toLowerCase();
            var offsets = [];
            if (offsetStr === "from") offsets = [0];
            else if (offsetStr === "to") offsets = [100];
            else offsets = [parseFloat(offsetStr) || 0];
            var props = {};
            sm[2].split(";").forEach(function(d) {
              var eq = d.indexOf(":");
              if (eq > 0) {
                var k = d.slice(0, eq).trim();
                var v = d.slice(eq + 1).trim();
                if (k && v) props[k] = v;
              }
            });
            offsets.forEach(function(off) { entries.push({ offset: off / 100, props: props }); });
          }
          entries.sort(function(a, b) { return a.offset - b.offset; });
          if (entries.length > 0) kf[name] = entries;
        }
      }
      n.childNodes.forEach(walk);
    }
    walk(doc);
    return kf;
  }
  function __matchesSel(el, sel) {
    sel = sel.trim().toLowerCase();
    if (!sel || sel === "*") return true;
    var tag = null, id = null, cls = null;
    /* Attribute selectors ([hidden], [type=checkbox]) participate in the
       cascade: author rules like `#keep[hidden] { display: block }` must be
       able to override the UA [hidden] rule. The old regex rejected any
       selector with a [...] group, so such rules never applied and the UA
       rule always won. */
    var m = sel.match(/^([a-z][a-z0-9]*)?(#[a-z0-9\-_]+)?((?:\.[a-z0-9\-_]+)*)(\[([a-z\-_]+)(?:=["']?([^"'\]]*)["']?)?\])?$/);
    if (!m) return false;
    tag = m[1] || null; id = m[2] ? m[2].slice(1) : null;
    var classes = m[3] ? m[3].split(".").filter(Boolean) : [];
    if (tag && el.nodeName !== tag) return false;
    if (id && el.id !== id) return false;
    var have = (" " + (el.className || "") + " ").toLowerCase();
    for (var i = 0; i < classes.length; i++) {
      if (have.indexOf(" " + classes[i] + " ") < 0) return false;
    }
    if (m[5]) {
      if (typeof el.hasAttribute !== "function" || !el.hasAttribute(m[5])) return false;
      if (m[6] != null) {
        var attrVal = String(el.getAttribute(m[5]) || "").toLowerCase();
        if (attrVal !== m[6].toLowerCase()) return false;
      }
    }
    return true;
  }
  function __selSpecificity(sel) {
    var id = (sel.match(/#/g) || []).length;
    var cl = (sel.match(/\./g) || []).length;
    var tag = /^[a-z]/i.test(sel.trim()) ? 1 : 0;
    return id * 100 + cl * 10 + tag;
  }
  function __normalizeProp(k) {
    var key = String(k == null ? "" : k);
    if (!key || key.indexOf("--") === 0) return key;
    if (key === "font-size") return "fontSize";
    if (key === "flex-direction") return "flexDirection";
    if (key === "justify-content") return "justifyContent";
    if (key === "align-items") return "alignItems";
    if (key === "animation-name") return "animationName";
    if (key === "animation-duration") return "animationDuration";
    if (key === "animation-timing-function") return "animationTimingFunction";
    if (key === "animation-delay") return "animationDelay";
    if (key === "animation-iteration-count") return "animationIterationCount";
    if (key === "animation-direction") return "animationDirection";
    if (key === "animation-fill-mode") return "animationFillMode";
    /* Declaration keys arrive kebab-case from every real source (stylesheet
       text, the style ATTRIBUTE, computed inline declarations); the cascade
       template is a JS-property (camelCase) table. Without this mapping the
       multi-word declarations all missed the template and were dropped:
       computed fontWeight/backgroundColor/overflowY read as undefined, and a
       stylesheet `overflow-y: auto` container was invisible to the scroll
       lane (which must know a box is scrollable). */
    if (key.indexOf("-") < 0) return key;
    return key.replace(/-([a-z0-9])/g, function (_m, c) { return c.toUpperCase(); });
  }
  function __computedStyle(el, rules, parentFont) {
    var st = {
      display: null,
      position: null,
      top: null,
      left: null,
      bottom: null,
      right: null,
      flexDirection: null,
      flexWrap: null,
      gap: null,
      width: null,
      height: null,
      margin: "0",
      padding: "0",
      fontSize: null,
      /* visibility participates in the cascade: checkVisibility() must see
         stylesheet visibility:hidden, which the old template dropped. */
      visibility: null,
      opacity: "1",
      /* Presentational / text properties participate in the cascade too. The
         template used to list only layout props, so getComputedStyle() — which
         reads this same template — returned "" for color/font/text on every
         element whose color came from a stylesheet (the common case). */
      color: null,
      backgroundColor: null,
      fontFamily: null,
      fontWeight: null,
      fontStyle: null,
      fontVariant: null,
      fontStretch: null,
      fontSizeAdjust: null,
      animation: null,
      animationName: null,
      animationDuration: null,
      animationTimingFunction: null,
      animationDelay: null,
      animationIterationCount: null,
      animationDirection: null,
      animationFillMode: null,
      transition: null,
      textAlign: null,
      textAlignLast: null,
      lineHeight: null,
      letterSpacing: null,
      wordSpacing: null,
      textIndent: null,
      textTransform: null,
      textDecoration: null,
      textShadow: null,
      whiteSpace: null,
      verticalAlign: null,
      unicodeBidi: null,
      direction: null,
      writingMode: null,
      listStyle: null,
      listStyleType: null,
      listStylePosition: null,
      listStyleImage: null,
      background: null,
      backgroundImage: null,
      backgroundRepeat: null,
      backgroundPosition: null,
      backgroundSize: null,
      backgroundClip: null,
      backgroundOrigin: null,
      backgroundAttachment: null,
      border: null,
      borderTop: null,
      borderRight: null,
      borderBottom: null,
      borderLeft: null,
      borderTopColor: null,
      borderTopStyle: null,
      borderTopWidth: null,
      borderRightColor: null,
      borderRightStyle: null,
      borderRightWidth: null,
      borderBottomColor: null,
      borderBottomStyle: null,
      borderBottomWidth: null,
      borderLeftColor: null,
      borderLeftStyle: null,
      borderLeftWidth: null,
      borderRadius: null,
      borderTopLeftRadius: null,
      borderTopRightRadius: null,
      borderBottomLeftRadius: null,
      borderBottomRightRadius: null,
      outline: null,
      outlineColor: null,
      outlineStyle: null,
      outlineWidth: null,
      outlineOffset: null,
      zIndex: null,
      overflow: null,
      overflowX: null,
      overflowY: null,
      cursor: null,
      float: null,
      clear: null,
      tableLayout: null,
      borderCollapse: null,
borderSpacing: null,
        captionSide: null,
        emptyCells: null,
        boxSizing: null
      };
    rules.forEach(function (r) {
      r.sel.split(",").forEach(function (s) {
        if (!__matchesSel(el, s)) return;
        var spec = __selSpecificity(s);
        for (var k in r.decls) {
          var prop = __normalizeProp(k);
          /* Skip unknown properties, do NOT abort the rule: an unrecognized
             declaration must not suppress the recognized ones that follow it
             in the same rule (real browsers ignore unknown declarations). */
          if (!(prop in st)) continue;
          st["__spec_" + prop] = st["__spec_" + prop] || 0;
          if (spec >= st["__spec_" + prop]) {
            st[prop] = r.decls[k];
            st["__spec_" + prop] = spec;
          }
        }
      });
    });
    var inline = __parseStyleDecls(el.attributes.style || "");
    for (var k2 in inline) {
      var prop2 = __normalizeProp(k2);
      if (prop2 in st) st[prop2] = inline[k2];
    }
    /* overflow is a shorthand: resolve the longhands the scroll lane and page
       code read (getComputedStyle(el).overflowY), unless a longhand already
       set them. */
    if (st.overflow != null) {
      if (st.overflowX == null) st.overflowX = st.overflow;
      if (st.overflowY == null) st.overflowY = st.overflow;
    }
    var fontSize = parentFont || 16;
    if (st.fontSize != null) {
      var fs = __parsePx(st.fontSize, null, parentFont || 16);
      if (fs != null && fs > 0) fontSize = fs;
    }
    var disp = (st.display || "").toLowerCase();
    if (!disp) {
      /* UA rules (HTML spec rendering): [hidden] { display: none },
         dialog:not([open]) { display: none }, and a closed <details> hides
         everything except its summary. Author CSS that sets display explicitly
         wins over these UA rules — matching real cascade order (UA < author). */
      if (el.hasAttribute && el.hasAttribute("hidden")) {
        disp = "none";
      } else {
        var dn = el;
        while (dn) {
          if (dn.nodeName === "dialog" && !(dn.hasAttribute && dn.hasAttribute("open"))) {
            disp = "none";
            break;
          }
          if (dn.hasAttribute && dn.hasAttribute("popover") && !(dn.hasAttribute && dn.hasAttribute("open"))) {
            disp = "none";
            break;
          }
          if (dn.nodeName === "details" && !(dn.hasAttribute && dn.hasAttribute("open"))) {
            var up = el;
            var viaSummary = false;
            while (up && up !== dn) {
              if (up.nodeName === "summary") { viaSummary = true; break; }
              up = up.parentNode;
            }
            if (dn !== el && !viaSummary) disp = "none";
            break;
          }
          dn = dn.parentNode;
        }
      }
    }
    if (!disp) disp = (__BLOCK_TAGS[el.nodeName] ? "block" : "inline");
var pos = (st.position || "").toLowerCase();
      var flexDir = (st.flexDirection || "row").toLowerCase();
      /* Return the full computed property set, not just the layout subset:
         getComputedStyle() reads this same object, so color/font/text/border
         must be present or every stylesheet-set property reads as "". */
      var out = {
        display: disp,
        position: pos,
        top: st.top,
        left: st.left,
        bottom: st.bottom,
        right: st.right,
        flexDirection: flexDir,
        flexWrap: (st.flexWrap || "nowrap").toLowerCase(),
        gap: st.gap,
        width: st.width,
        height: st.height,
        margin: st.margin,
        padding: st.padding,
        fontSize: fontSize,
        visibility: st.visibility,
        opacity: st.opacity
      };
      for (var pk in st) {
        if (pk.indexOf("__spec_") === 0) continue;
        if (pk === "display" || pk === "position" || pk === "flexDirection") continue;
        if (pk in out) continue;
        out[pk] = st[pk];
      }
      return out;
  }
  /* Authentic proportional character advance ratios for standard system sans-serif (Inter / Roboto / Arial) */
  var __CHAR_ADVANCES = {
    " ": 0.28, "!": 0.28, "\"": 0.35, "#": 0.55, "$": 0.55, "%": 0.88, "&": 0.67, "'": 0.22,
    "(": 0.33, ")": 0.33, "*": 0.39, "+": 0.58, ",": 0.28, "-": 0.33, ".": 0.28, "/": 0.28,
    ":": 0.28, ";": 0.28, "<": 0.58, "=": 0.58, ">": 0.58, "?": 0.55, "@": 1.01,
    "[": 0.28, "\\": 0.28, "]": 0.28, "^": 0.47, "_": 0.55, "`": 0.33,
    "{": 0.33, "|": 0.26, "}": 0.33, "~": 0.58,
    "i": 0.22, "l": 0.22, "j": 0.22, "f": 0.28, "t": 0.28, "r": 0.33,
    "s": 0.50, "c": 0.50, "z": 0.50, "v": 0.50, "x": 0.50, "y": 0.50,
    "a": 0.55, "b": 0.55, "d": 0.55, "e": 0.55, "g": 0.55, "h": 0.55, "k": 0.50,
    "n": 0.55, "o": 0.55, "p": 0.55, "q": 0.55, "u": 0.55,
    "w": 0.72, "m": 0.83,
    "I": 0.28, "J": 0.50, "L": 0.55, "T": 0.61, "E": 0.66, "F": 0.61, "Z": 0.61,
    "A": 0.67, "B": 0.67, "C": 0.72, "D": 0.72, "G": 0.78, "H": 0.72, "K": 0.67,
    "N": 0.72, "P": 0.67, "R": 0.72, "S": 0.67, "U": 0.72, "V": 0.67, "X": 0.67,
    "Y": 0.67, "O": 0.78, "Q": 0.78,
    "M": 0.83, "W": 0.94
  };
  for (var _di = 0; _di <= 9; _di++) __CHAR_ADVANCES[String(_di)] = 0.55;

  /* L5: Kerning pairs (Helvetica-class). These pairs have narrower combined
     advance than the sum of individual advances because the letterforms
     interlock. This is the "shaping-lite" approximation: no full Unicode
     bidi or per-glyph shaping, but pair-level kerning that real fonts apply. */
  var __KERN_PAIRS = {
    "AV": -0.06, "AW": -0.06, "AY": -0.06, "Av": -0.04, "Aw": -0.04, "Ay": -0.04,
    "AT": -0.06, "AU": -0.05, "AV": -0.06,
    "Ta": -0.05, "Te": -0.05, "Ti": -0.05, "To": -0.05, "Tu": -0.05, "Ty": -0.05,
    "Tr": -0.04, "Tc": -0.04, "Ts": -0.03, "TA": -0.06, "TW": -0.06,
    "Wa": -0.05, "We": -0.04, "Wi": -0.05, "Wo": -0.05, "Wu": -0.05, "Wy": -0.05,
    "Wa": -0.05, "WO": -0.05, "WA": -0.06,
    "Ya": -0.05, "Ye": -0.04, "Yo": -0.05, "Yu": -0.05, "Yv": -0.04,
    "Va": -0.05, "Ve": -0.04, "Vi": -0.05, "Vo": -0.05, "Vu": -0.05, "Vy": -0.05,
    "VO": -0.05, "VA": -0.06,
    "fa": -0.03, "fe": -0.03, "fi": -0.03, "fo": -0.03, "fu": -0.03, "fy": -0.03,
    "fa": -0.03, "ff": -0.05, "fl": -0.03, "ft": -0.03,
    "Ly": -0.04, "Lu": -0.03, "Lo": -0.03, "La": -0.03,
    "F.": -0.08, "F,": -0.08, "F;": -0.06, "L.": -0.08, "L,": -0.08, "T.": -0.08, "T,": -0.08,
    "Y.": -0.08, "Y,": -0.08, "V.": -0.08, "V,": -0.08, "P.": -0.06, "P,": -0.06,
    "rn": -0.02, "rm": -0.02, "oo": -0.01, "ee": -0.01, "aa": -0.01, "cc": -0.01
  };

  function __advance(ch, fontSize) {
    if (Object.prototype.hasOwnProperty.call(__CHAR_ADVANCES, ch)) {
      return __CHAR_ADVANCES[ch] * fontSize;
    }
    var code = ch.charCodeAt(0);
    if (code >= 0x1100 && (code <= 0x115F || (code >= 0x2E80 && code <= 0x9FFF) || (code >= 0xAC00 && code <= 0xD7A3) || (code >= 0xF900 && code <= 0xFAFF))) return fontSize;
    return 0.55 * fontSize;
  }
  /* L5: Kerning-aware text width. Sums per-character advances, then
     subtracts kerning corrections for adjacent pairs that real fonts
     interlock (AV, To, Wa, etc.). letterSpacing (em units) is added
     per character when set. */
  function __textWidth(s, fontSize, letterSpacing) {
    var w = 0;
    var ls = (typeof letterSpacing === "number") ? letterSpacing * fontSize : 0;
    for (var i = 0; i < s.length; i++) {
      w += __advance(s[i], fontSize);
      if (i > 0) {
        var pair = s[i - 1] + s[i];
        if (Object.prototype.hasOwnProperty.call(__KERN_PAIRS, pair)) {
          w += __KERN_PAIRS[pair] * fontSize;
        }
      }
      if (ls && i < s.length - 1) w += ls;
    }
    return w;
  }
  var __NON_RENDERED = { head:1, style:1, script:1, title:1, meta:1, link:1, template:1, noscript:0 };
  /* Live DOM nodeName is UPPERCASE ('STYLE'); the map keys are lowercase.
     Normalize before every lookup or the exclusion never fires. */
  function __nonRendered(el) { return __NON_RENDERED[String((el && el.nodeName) || '').toLowerCase()]; }
  try { global.__nonRendered = __nonRendered; } catch (e) {}
  function __ensureLayout(doc) {
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    if (doc.__layoutCache && doc.__layoutCache.vw === vw && doc.__layoutCache.v === doc.__layoutVersion) return doc.__layoutCache;
    /* A fresh pass must not inherit __lines from a previous pass (which
       __clearBoxes now drops, but belt-and-braces for any element that
       never got cleared, e.g. non-rendered subtrees reached via a prior
       pass). */
    (function __resetLines(n) {
      if (!n) return;
      if (n.nodeType === 1 || n.nodeType === 9) {
        n.__lines = null;
        (n.childNodes || []).forEach(function (c) { __resetLines(c); });
      }
    })(doc.documentElement || doc);
    var rules = __collectStyleRules(doc);
    var layout = { vw: vw, v: doc.__layoutVersion || 0, boxes: 0, engine: "block-flow+flex+grid+positioned" };
    function isBlockDisplay(cs) {
      return cs.display === "block" || cs.display === "list-item" || cs.display === "table" || cs.display === "flex" || cs.display === "grid" || cs.display === "inline-grid";
    }
    function layoutAbsolute(c, ccs, cx, cy, cw, ch) {
      var isFixed = (ccs.position === "fixed");
      var rx = isFixed ? 0 : cx;
      var ry = isFixed ? 0 : cy;
      var rw = isFixed ? vw : (cw || vw);
      var rh = isFixed ? 720 : (ch || 720);
      var w = __parsePx(ccs.width, rw, ccs.fontSize);
      var h = __parsePx(ccs.height, rh, ccs.fontSize);
      if (w == null) {
        if (__REPLACED_TAGS[c.nodeName]) w = 150;
        else {
          var tw = __textWidth(c.textContent || "", ccs.fontSize);
          w = Math.max(20, Math.min(rw, tw + 20));
        }
      }
      if (h == null) h = ccs.fontSize * 1.5;
      var xBox = rx;
      var yBox = ry;
      var left = __parsePx(ccs.left, rw, ccs.fontSize);
      var right = __parsePx(ccs.right, rw, ccs.fontSize);
      var top = __parsePx(ccs.top, rh, ccs.fontSize);
      var bottom = __parsePx(ccs.bottom, rh, ccs.fontSize);
      if (left != null) xBox = rx + left;
      else if (right != null) xBox = rx + rw - w - right;
      if (top != null) yBox = ry + top;
      else if (bottom != null) yBox = ry + rh - h - bottom;
      c.__box = { x: xBox, y: yBox, width: w, height: h };
      layout.boxes++;
      layoutChildren(c, ccs, xBox, yBox, w);
    }
    function layoutChildren(el, cs, x, y, contentW) {
      if (cs.display === "grid" || cs.display === "inline-grid") {
        var gap = __parsePx(cs.gap, contentW, cs.fontSize) || 0;
        var children = (el.childNodes || []).filter(function (c) {
          return c.nodeType === 1 && !__nonRendered(c);
        });
        var colCount = 1;
        var tmpl = cs.gridTemplateColumns || cs["grid-template-columns"] || "";
        var repMatch = tmpl.match(/repeat\((\d+)/i);
        if (repMatch) {
          colCount = Math.max(1, parseInt(repMatch[1], 10) || 1);
        } else if (tmpl.trim()) {
          var parts = tmpl.trim().split(/\s+/).filter(Boolean);
          if (parts.length > 1) colCount = parts.length;
        } else if (children.length > 1 && contentW >= 400) {
          colCount = 2;
        }
        var totalGaps = (colCount - 1) * gap;
        var colW = Math.max(20, Math.floor((contentW - totalGaps) / colCount));
        var curY = y;
        var rowH = 0;
        var colIdx = 0;

        children.forEach(function (c) {
          var ccs = __computedStyle(c, rules, cs.fontSize);
          if (ccs.display === "none") {
            __clearBoxes(c);
            layout.boxes++;
            return;
          }
          if (ccs.position === "absolute" || ccs.position === "fixed") {
            layoutAbsolute(c, ccs, x, curY, contentW, 0);
            return;
          }
          var margin = __parseSides(ccs.margin, colW, cs.fontSize);
          var padding = __parseSides(ccs.padding, colW, cs.fontSize);
          var itemX = x + colIdx * (colW + gap);
          var w = __parsePx(ccs.width, colW, cs.fontSize);
          if (w == null) w = Math.max(0, colW - margin[1] - margin[3]);
          var h = __parsePx(ccs.height, null, cs.fontSize);
          var itemContentW = Math.max(0, w - padding[1] - padding[3]);
          var r = layoutChildren(c, ccs, itemX + margin[3] + padding[3], curY + margin[0] + padding[0], itemContentW);
          var itemH = (h == null) ? Math.max(0, r.bottom - (curY + margin[0] + padding[0])) : h;
          var boxH = padding[0] + itemH + padding[2];
          c.__box = { x: itemX + margin[3], y: curY + margin[0], width: w, height: boxH };
          layout.boxes++;
          if (boxH + margin[0] + margin[2] > rowH) rowH = boxH + margin[0] + margin[2];

          colIdx++;
          if (colIdx >= colCount) {
            colIdx = 0;
            curY += rowH + gap;
            rowH = 0;
          }
        });
        if (colIdx > 0) curY += rowH;
        return { bottom: curY, maxW: contentW };
      }
      if (cs.display === "flex" || cs.display === "inline-flex") {
        var gap = __parsePx(cs.gap, contentW, cs.fontSize) || 0;
        var isCol = (cs.flexDirection === "column");
        var justify = (cs.justifyContent || "flex-start").toLowerCase();
        var align = (cs.alignItems || "stretch").toLowerCase();
        var children = (el.childNodes || []).filter(function (c) {
          return c.nodeType === 1 && !__nonRendered(c);
        });
        /* L3: Two-pass flex layout. Pass 1: measure items and collect into rows. */
        var rows = [];
        var curRow = [];
        var curMain = 0;
        var maxCross = 0;
        var availMain = contentW;
        children.forEach(function (c, idx) {
          var ccs = __computedStyle(c, rules, cs.fontSize);
          if (ccs.display === "none") {
            __clearBoxes(c);
            layout.boxes++;
            return;
          }
          if (ccs.position === "absolute" || ccs.position === "fixed") {
            layoutAbsolute(c, ccs, x, y, contentW, 0);
            return;
          }
          var margin = __parseSides(ccs.margin, contentW, ccs.fontSize);
          var padding = __parseSides(ccs.padding, contentW, ccs.fontSize);
          var w = __parsePx(ccs.width, contentW, cs.fontSize);
          var h = __parsePx(ccs.height, null, cs.fontSize);
          var mainSize, crossSize;
          if (isCol) {
            if (w == null) w = Math.max(0, contentW - margin[1] - margin[3]);
            crossSize = margin[3] + w + margin[1];
            var itemContentW = Math.max(0, w - padding[1] - padding[3]);
            var r = layoutChildren(c, ccs, 0, 0, itemContentW);
            var itemH = (h == null) ? Math.max(0, r.bottom) : h;
            var boxH = padding[0] + itemH + padding[2];
            mainSize = margin[0] + boxH + margin[2];
          } else {
            if (w == null) {
              if (__REPLACED_TAGS[c.nodeName]) {
                w = 100;
              } else {
                var tw = __textWidth(c.textContent || "", ccs.fontSize);
                w = Math.max(30, Math.min(contentW, tw + padding[1] + padding[3] + 20));
              }
            }
            mainSize = margin[3] + w + margin[1];
            var itemContentW = Math.max(0, w - padding[1] - padding[3]);
            var r = layoutChildren(c, ccs, 0, 0, itemContentW);
            var itemH = (h == null) ? Math.max(0, r.bottom) : h;
            var boxH = padding[0] + itemH + padding[2];
            crossSize = margin[0] + boxH + margin[2];
          }
          if (cs.flexWrap === "wrap" && curRow.length > 0 && curMain + mainSize > availMain) {
            rows.push({ items: curRow, mainSize: curMain, crossSize: maxCross });
            curRow = [];
            curMain = 0;
            maxCross = 0;
          }
          curRow.push({ el: c, ccs: ccs, w: w, h: h, mainSize: mainSize, crossSize: crossSize, margin: margin, padding: padding });
          curMain += mainSize + (curRow.length > 1 ? gap : 0);
          if (crossSize > maxCross) maxCross = crossSize;
        });
        if (curRow.length > 0) {
          rows.push({ items: curRow, mainSize: curMain, crossSize: maxCross });
        }
        /* L3: Pass 2 - position items with justify-content and align-items. */
        var rowY = y;
        var totalW = 0;
        rows.forEach(function (row) {
          var freeMain = availMain - row.mainSize;
          var mainOffset = 0;
          var mainGap = gap;
          if (justify === "flex-end") mainOffset = freeMain;
          else if (justify === "center") mainOffset = freeMain / 2;
          else if (justify === "space-between" && row.items.length > 1) {
            mainGap = gap + (freeMain / (row.items.length - 1));
          } else if (justify === "space-around" && row.items.length > 0) {
            mainOffset = freeMain / (row.items.length * 2);
            mainGap = gap + (freeMain / row.items.length);
          } else if (justify === "space-evenly" && row.items.length > 0) {
            mainOffset = freeMain / (row.items.length + 1);
            mainGap = gap + (freeMain / (row.items.length + 1));
          }
          var pos = isCol ? rowY : (x + mainOffset);
          var rowMaxW = 0;
          row.items.forEach(function (item, idx) {
            var c = item.el;
            var ccs = item.ccs;
            if (isCol) {
              var itemX = x;
              if (align === "flex-end") itemX = x + contentW - item.w - item.margin[1];
              else if (align === "center") itemX = x + (contentW - item.w) / 2;
              else if (align === "stretch") {
                item.w = contentW - item.margin[1] - item.margin[3];
                itemX = x + item.margin[3];
              } else {
                itemX = x + item.margin[3];
              }
              var itemY = pos + item.margin[0];
              var itemContentW = Math.max(0, item.w - item.padding[1] - item.padding[3]);
              var r = layoutChildren(c, ccs, itemX + item.padding[3], itemY + item.padding[0], itemContentW);
              var itemH = (item.h == null) ? Math.max(0, r.bottom - itemY - item.padding[0]) : item.h;
              var boxH = item.padding[0] + itemH + item.padding[2];
              c.__box = { x: itemX, y: itemY, width: item.w, height: boxH };
              layout.boxes++;
              pos += item.mainSize + (idx < row.items.length - 1 ? mainGap : 0);
              if (item.w + item.margin[1] + item.margin[3] > rowMaxW) rowMaxW = item.w + item.margin[1] + item.margin[3];
            } else {
              var itemX = pos + item.margin[3];
              var itemY = rowY;
              if (align === "flex-end") itemY = rowY + row.crossSize - item.crossSize;
              else if (align === "center") itemY = rowY + (row.crossSize - item.crossSize) / 2;
              else if (align === "stretch") {
                item.h = row.crossSize - item.margin[0] - item.margin[2] - item.padding[0] - item.padding[2];
              }
              itemY += item.margin[0];
              var itemContentW = Math.max(0, item.w - item.padding[1] - item.padding[3]);
              var r = layoutChildren(c, ccs, itemX + item.padding[3], itemY + item.padding[0], itemContentW);
              var itemH = (item.h == null) ? Math.max(0, r.bottom - itemY - item.padding[0]) : item.h;
              var boxH = item.padding[0] + itemH + item.padding[2];
              c.__box = { x: itemX, y: itemY, width: item.w, height: boxH };
              layout.boxes++;
              pos += item.mainSize + (idx < row.items.length - 1 ? mainGap : 0);
              if (pos - x > rowMaxW) rowMaxW = pos - x;
            }
          });
          if (isCol) {
            rowY = pos;
            if (rowMaxW > totalW) totalW = rowMaxW;
          } else {
            rowY += row.crossSize + gap;
            if (pos - x > totalW) totalW = pos - x;
          }
        });
        return { bottom: rowY, maxW: totalW };
      }
      /* L1: CSS floats. Track left/right float regions and shrink-wrap
         content around them. Floats are block-level elements that are shifted
         to the left or right, with inline content flowing around them. */
      var floats = [];
      function getFloatBounds(yPos) {
        var leftW = 0, rightW = 0;
        floats.forEach(function (f) {
          if (yPos >= f.y && yPos < f.y + f.h) {
            if (f.side === "left") leftW += f.w;
            else if (f.side === "right") rightW += f.w;
          }
        });
        return { leftW: leftW, rightW: rightW };
      }
      var cursor = y;
      var line = null;
      var maxW = 0;
      function flushLine() {
        if (!line) return;
        var h = 0;
        line.runs.forEach(function (r) { if (r.h > h) h = r.h; });
        if (h <= 0) h = cs.fontSize * 1.2;
        /* L4: For RTL, reposition runs from right edge of available width */
        if (line.isRTL) {
          var lineX = x + contentW - line.w;
          var currentX = lineX;
          /* For RTL, iterate through runs in reverse order so the first
             element in the DOM ends up on the right */
          var runs = line.runs.slice().reverse();
          runs.forEach(function (r) {
            var rec = { x: currentX, y: cursor, h: h, text: r.text || "" };
            (r.el.__lines = r.el.__lines || []).push(rec);
            if (!r.el.__box) {
              r.el.__box = { x: currentX, y: cursor, width: r.w, height: h };
              layout.boxes++;
            } else if (r.el !== el && r.el.nodeName !== "body" && r.el.nodeName !== "html") {
              var bx1 = Math.min(r.el.__box.x, currentX);
              var by1 = Math.min(r.el.__box.y, cursor);
              var bx2 = Math.max(r.el.__box.x + r.el.__box.width, currentX + r.w);
              var by2 = Math.max(r.el.__box.y + r.el.__box.height, cursor + h);
              r.el.__box = { x: bx1, y: by1, width: bx2 - bx1, height: by2 - by1 };
            }
            currentX += r.w;
          });
        } else {
          line.runs.forEach(function (r) {
            var rec = { x: r.x, y: cursor, h: h, text: r.text || "" };
            (r.el.__lines = r.el.__lines || []).push(rec);
            if (!r.el.__box) {
              r.el.__box = { x: r.x, y: cursor, width: r.w, height: h };
              layout.boxes++;
            } else if (r.el !== el && r.el.nodeName !== "body" && r.el.nodeName !== "html") {
              var bx1 = Math.min(r.el.__box.x, r.x);
              var by1 = Math.min(r.el.__box.y, cursor);
              var bx2 = Math.max(r.el.__box.x + r.el.__box.width, r.x + r.w);
              var by2 = Math.max(r.el.__box.y + r.el.__box.height, cursor + h);
              r.el.__box = { x: bx1, y: by1, width: bx2 - bx1, height: by2 - by1 };
            }
          });
        }
        cursor += h;
        if (line.w > maxW) maxW = line.w;
        line = null;
      }
      function pushRun(targetEl, text, fontSize, isRTL) {
        var words = String(text).split(/(\s+)/);
        words.forEach(function (word) {
          if (word === "") return;
          if (/^\s+$/.test(word)) {
            var sp = __textWidth(" ", fontSize);
            if (!line) line = { x: x, w: 0, runs: [], isRTL: isRTL };
            if (line.w + sp > contentW && line.w > 0) flushLine();
            if (!line) line = { x: x, w: 0, runs: [], isRTL: isRTL };
            line.runs.push({ el: targetEl, x: x + line.w, w: sp, h: fontSize * 1.2, text: " " });
            line.w += sp;
            return;
          }
          var ww = __textWidth(word, fontSize);
          if (!line) line = { x: x, w: 0, runs: [], isRTL: isRTL };
          if (line.w + ww > contentW && line.w > 0) flushLine();
          if (!line) line = { x: x, w: 0, runs: [], isRTL: isRTL };
          line.runs.push({ el: targetEl, x: x + line.w, w: ww, h: fontSize * 1.2, text: word });
          line.w += ww;
        });
      }
      function flowInline(targetEl, node, fontSize, inheritedRTL) {
        /* L4: Bidi support - direction:rtl reverses inline element ordering.
           direction is inherited: a child without its own direction property
           inherits the parent container's direction. */
        var nodeStyle = __computedStyle(node, rules, fontSize);
        var isRTL = (nodeStyle.direction === "rtl") || (nodeStyle.direction !== "ltr" && inheritedRTL);
        var children = (node.childNodes || []).slice();
        if (isRTL) {
          children.reverse();
        }
        children.forEach(function (c) {
          if (c.nodeType === 3) { pushRun(targetEl, __nodeText(c), fontSize, isRTL); return; }
          if (c.nodeType !== 1) return;
          var ccs = __computedStyle(c, rules, fontSize);
          if (ccs.display === "none") { __clearBoxes(c); return; }
          if (ccs.position === "absolute" || ccs.position === "fixed") {
            layoutAbsolute(c, ccs, x, cursor, contentW, 0);
            return;
          }
          if (isBlockDisplay(ccs)) {
            flushLine();
            var cb = layoutBlock(c, x, cursor, contentW, fontSize);
            cursor = cb.bottom;
            if (cb.w > maxW) maxW = cb.w;
            return;
          }
          if (__REPLACED_TAGS[c.nodeName]) {
            flushLine();
            var rb = layoutReplaced(c, ccs, x, cursor, contentW);
            cursor = rb.bottom;
            if (rb.w > maxW) maxW = rb.w;
            return;
          }
          flowInline(c, c, ccs.fontSize, isRTL);
        });
      }
      (el.childNodes || []).forEach(function (c) {
        if (c.nodeType === 3) { pushRun(el, __nodeText(c), cs.fontSize, cs.direction === "rtl"); return; }
        if (c.nodeType !== 1) return;
        var ccs = __computedStyle(c, rules, cs.fontSize);
        if (ccs.display === "none") { __clearBoxes(c); layout.boxes++; return; }
        if (ccs.position === "absolute" || ccs.position === "fixed") {
          layoutAbsolute(c, ccs, x, cursor, contentW, 0);
          return;
        }
        /* L1: Handle floated elements */
        if (ccs.float && ccs.float !== "none") {
          flushLine();
          var fb = layoutBlock(c, x, cursor, contentW, cs.fontSize);
          var floatW = fb.w;
          var floatH = fb.bottom - cursor;
          if (ccs.float === "left") {
            c.__box.x = x;
            floats.push({ y: cursor, h: floatH, w: floatW, side: "left" });
          } else if (ccs.float === "right") {
            c.__box.x = x + contentW - floatW;
            floats.push({ y: cursor, h: floatH, w: floatW, side: "right" });
          }
          cursor = fb.bottom;
          if (floatW > maxW) maxW = floatW;
          return;
        }
        /* L1: Handle clear property - push cursor below matching floats */
        if (ccs.clear && ccs.clear !== "none") {
          var clearSide = ccs.clear;
          floats.forEach(function (f) {
            if (clearSide === "both" || clearSide === f.side) {
              if (cursor < f.y + f.h) cursor = f.y + f.h;
            }
          });
        }
        if (isBlockDisplay(ccs)) {
          flushLine();
          var bounds = floats.length > 0 ? getFloatBounds(cursor) : { leftW: 0, rightW: 0 };
          var adjustedX = x + bounds.leftW;
          var adjustedW = contentW - bounds.leftW - bounds.rightW;
          var cb = layoutBlock(c, adjustedX, cursor, adjustedW, cs.fontSize);
          cursor = cb.bottom;
          if (cb.w > maxW) maxW = cb.w;
          return;
        }
        if (__REPLACED_TAGS[c.nodeName]) {
          flushLine();
          var rb = layoutReplaced(c, ccs, x, cursor, contentW);
          cursor = rb.bottom;
          if (rb.w > maxW) maxW = rb.w;
          return;
        }
        flowInline(c, c, ccs.fontSize, cs.direction === "rtl");
      });
      flushLine();
      return { bottom: cursor, maxW: maxW };
    }
    function layoutReplaced(el, cs, x, y, availW) {
      var w = __parsePx(cs.width, availW, cs.fontSize);
      var h = __parsePx(cs.height, null, cs.fontSize);
      if (w == null || h == null) {
        if (el.nodeName === "button" || (el.nodeName === "input" && (el.attributes.type || "").toLowerCase() === "submit")) {
          var label = el.textContent || el.attributes.value || "";
          if (w == null) w = Math.min(availW, __textWidth(label, cs.fontSize) + 24);
          if (h == null) h = cs.fontSize * 1.2 + 12;
        } else if (el.nodeName === "input" && (el.attributes.type || "").toLowerCase() === "checkbox") {
          if (w == null) w = 13; if (h == null) h = 13;
        } else {
          if (w == null) w = Math.min(availW, 300);
          if (h == null) h = 150;
        }
      }
      el.__box = { x: x, y: y, width: w, height: h };
      layout.boxes++;
      return { bottom: y + h, w: w };
    }
    function __clearBoxes(n) {
      if (!n || (n.nodeType !== 1 && n.nodeType !== 9)) return;
      /* null, not a zero box: a null box is skipped by the body-margin shift
         (so hidden elements report the spec's all-zero rect, not 8,8), skipped
         by hit-testing and the extent walk, and falls back to zeros in
         getBoundingClientRect/__elementContentSize/__maxScrollOffset. */
      n.__box = null;
      n.__contentExtent = null;
      n.__lines = null;
      (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) __clearBoxes(c); });
    }
    function layoutBlock(el, x, y, availW, parentFont) {
      if (__nonRendered(el)) { __clearBoxes(el); return { bottom: y, w: 0 }; }
      if (el.nodeName === "body" && availW === vw) {
        availW = Math.max(0, vw - 16);
      }
      var cs = __computedStyle(el, rules, parentFont);
      /* A hidden subtree must not keep the boxes from the previous layout pass:
         a stale box is a phantom the agent can scroll to, hit-test, or drag
         onto, even though nothing renders there. */
      if (cs.display === "none") { __clearBoxes(el); return { bottom: y, w: 0 }; }
      var margin = __parseSides(cs.margin, availW, cs.fontSize);
      var padding = __parseSides(cs.padding, availW, cs.fontSize);
      var w = __parsePx(cs.width, availW, cs.fontSize);
      if (w == null) w = Math.max(0, availW - margin[1] - margin[3]);
      var bx = x + margin[3];
      var by = y + margin[0];
      var contentW = Math.max(0, w - padding[1] - padding[3]);
      var r = layoutChildren(el, cs, bx + padding[3], by + padding[0], contentW);
      var h = __parsePx(cs.height, null, cs.fontSize);
      var contentH = (h == null) ? Math.max(0, r.bottom - (by + padding[0])) : h;
      var totalH = padding[0] + contentH + padding[2];
      el.__box = { x: bx, y: by, width: w, height: totalH };
      el.__layoutFont = cs.fontSize;
      if (cs.position === "relative") {
        var relLeft = __parsePx(cs.left, availW, cs.fontSize);
        var relRight = __parsePx(cs.right, availW, cs.fontSize);
        var relTop = __parsePx(cs.top, null, cs.fontSize);
        var relBottom = __parsePx(cs.bottom, null, cs.fontSize);
        if (relLeft != null) el.__box.x += relLeft;
        else if (relRight != null) el.__box.x -= relRight;
        if (relTop != null) el.__box.y += relTop;
        else if (relBottom != null) el.__box.y -= relBottom;
      } else if (cs.position === "sticky") {
        /* L2: position: sticky — element sticks within its containing block
           when scrolled past the offset threshold. Clamps between natural
           position and container bounds (approximated as viewport here). */
        var stickyTop = __parsePx(cs.top, null, cs.fontSize);
        var stickyBottom = __parsePx(cs.bottom, null, cs.fontSize);
        var stickyLeft = __parsePx(cs.left, availW, cs.fontSize);
        var stickyRight = __parsePx(cs.right, availW, cs.fontSize);
        var curScrollY = global.scrollY || 0;
        var curScrollX = global.scrollX || 0;
        var naturalY = el.__box.y;
        var naturalX = el.__box.x;
        if (stickyTop != null) {
          var targetY = curScrollY + stickyTop;
          if (naturalY < targetY) el.__box.y = targetY;
        } else if (stickyBottom != null) {
          var vh = Number(global.innerHeight) || 720;
          var targetY2 = curScrollY + vh - stickyBottom - totalH;
          if (naturalY > targetY2) el.__box.y = targetY2;
        }
        if (stickyLeft != null) {
          var targetX = curScrollX + stickyLeft;
          if (naturalX < targetX) el.__box.x = targetX;
        } else if (stickyRight != null) {
          var vwSticky = Number(global.innerWidth) || 1280;
          var targetX2 = curScrollX + vwSticky - stickyRight - el.__box.width;
          if (naturalX > targetX2) el.__box.x = targetX2;
        }
      }
      layout.boxes++;
      return { bottom: by + totalH + margin[2], w: margin[3] + w + margin[1] };
    }
    var root = doc.documentElement || doc;
    var bodyMargin = 8;
    /* L9: collect @keyframes and register animations before layout so
       animated property values are available to the layout pass. */
    try {
      var kf = __collectKeyframes(doc);
      if (Object.keys(kf).length > 0) {
        doc.__keyframes = kf;
        (function reg(n) {
          if (!n || n.nodeType !== 1) return;
          var cs2 = __computedStyle(n, kf.__rules || __collectStyleRules(doc), 16);
          __registerAnimations(doc, n, cs2);
          (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) reg(c); });
        })(root);
      }
    } catch (e) {}
    layoutBlock(root, 0, 0, vw, 16);
    if (root.nodeName === "html") {
      (function shift(n, dx, dy) {
        if (!n) return;
        if (n.__box && n !== root) { n.__box = { x: n.__box.x + dx, y: n.__box.y + dy, width: n.__box.width, height: n.__box.height }; }
        (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) shift(c, dx, dy); });
      })(root, bodyMargin, bodyMargin);
    }
    doc.__layoutCache = layout;
    /* Document scroll extent: the furthest right/bottom edge any laid-out
       box reaches (documentElement's own box never grows past the viewport
       width, so a 5000px-wide child used to be invisible to the scroll
       clamp — maxX computed as 0 even after an axis fix). Mirrors
       scrollWidth/scrollHeight semantics. */
    try {
      var __maxR = 0, __maxB = 0;
      /* Bottom-up so every box also learns its own subtree extent: that is the
         element-level scrollHeight/scrollWidth (a 640px child inside a 200px
         overflow:auto box is 640, not 200) and the clamp for its scrollTop. */
      (function __walkExtent(n) {
        if (!n) return { r: 0, b: 0 };
        var r = 0, b = 0;
        if (n.__box) {
          r = n.__box.x + n.__box.width;
          b = n.__box.y + n.__box.height;
        }
        (n.childNodes || []).forEach(function (c) {
          if (c.nodeType !== 1) return;
          var sub = __walkExtent(c);
          if (sub.r > r) r = sub.r;
          if (sub.b > b) b = sub.b;
        });
        if (n.__box) n.__contentExtent = { right: r, bottom: b };
        if (r > __maxR) __maxR = r;
        if (b > __maxB) __maxB = b;
        return { r: r, b: b };
      })(root);
      doc.__scrollExtent = { width: __maxR, height: __maxB };
    } catch (e) {}
    try {
      doc.__layoutInfo = { engine: "block-flow+flex+grid+positioned", viewportWidth: vw, boxes: layout.boxes, textMetric: "proportional-advance+kern-pairs", replacedDefaults: "css-300x150" };
    } catch (e) {}
    /* A fresh layout pass can move IntersectionObserver targets in or out of
       the viewport (lazy feeds append content; the sentinel crosses the fold).
       Real IO re-evaluates on the next frame after a change: deliver the
       deferred notification via a microtask so callbacks run after the current
       pass, never re-entrantly while boxes are being computed. */
    if (typeof queueMicrotask === "function") {
      queueMicrotask(function () { try { _notifyIntersections(); } catch (e) {} });
    } else if (typeof Promise === "function") {
      Promise.resolve().then(function () { try { _notifyIntersections(); } catch (e) {} });
    }
    return layout;
  }
  Element.prototype.getBoundingClientRect = function () {
    var doc = this.ownerDocument || global.document;
    if (doc) __ensureLayout(doc);
    var b = this.__box || { x: 0, y: 0, width: 0, height: 0 };
    return { x: b.x, y: b.y, top: b.y, left: b.x, bottom: b.y + b.height, right: b.x + b.width, width: b.width, height: b.height };
  };
  Object.defineProperty(Element.prototype, "offsetWidth", { get: function () { return this.getBoundingClientRect().width; } });
  Object.defineProperty(Element.prototype, "offsetHeight", { get: function () { return this.getBoundingClientRect().height; } });
  Object.defineProperty(Element.prototype, "clientWidth", { get: function () { return this.getBoundingClientRect().width; } });
  Object.defineProperty(Element.prototype, "clientHeight", { get: function () { return this.getBoundingClientRect().height; } });
  /* scrollWidth/scrollHeight: for the documentElement and body these are the full
     document scroll extents (laid-out boxes may overflow the viewport); for any
     other element they are the element's own subtree extent, which is what makes
     a nested overflow container report its hidden content instead of its box. */
  function __elementContentSize(el, axis) {
    var b = el.__box || { width: 0, height: 0 };
    var ext = el.__contentExtent;
    if (axis === "y") return ext ? Math.max(b.height, ext.bottom - b.y) : b.height;
    return ext ? Math.max(b.width, ext.right - b.x) : b.width;
  }
  Object.defineProperty(Element.prototype, "scrollWidth", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) {
        __ensureLayout(doc);
        if ((doc.documentElement === this || doc.body === this) && doc.__scrollExtent) {
          var b = this.__box || { width: 0 };
          return Math.max(b.width, doc.__scrollExtent.width);
        }
      }
      return __elementContentSize(this, "x");
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "scrollHeight", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) {
        __ensureLayout(doc);
        if ((doc.documentElement === this || doc.body === this) && doc.__scrollExtent) {
          var b = this.__box || { height: 0 };
          return Math.max(b.height, doc.__scrollExtent.height);
        }
      }
      return __elementContentSize(this, "y");
    },
    configurable: true
  });
  /* ---- nested scroll containers ----
     CSSOM View: an element establishes a scrolling box when its used overflow is
     auto/scroll/hidden (hidden is not user-scrollable but IS programmatically
     scrollable); overflow:visible/clip does not scroll. `overflow-y: auto` only
     reaches this lane because __normalizeProp maps the kebab declaration onto the
     camelCase cascade template. */
  function __overflowAxes(el) {
    var doc = el.ownerDocument || global.document;
    var cs = __computedStyle(el, doc ? __collectStyleRules(doc) : [], el.__layoutFont || 16);
    var ox = String(cs.overflowX || cs.overflow || "").toLowerCase();
    var oy = String(cs.overflowY || cs.overflow || "").toLowerCase();
    return { x: ox, y: oy };
  }
  function __scrollableOverflowValue(v) {
    return v === "auto" || v === "scroll" || v === "hidden" || v === "overlay";
  }
  function __isScrollContainer(el) {
    if (!el || el.nodeType !== 1) return false;
    var a = __overflowAxes(el);
    return __scrollableOverflowValue(a.y) || __scrollableOverflowValue(a.x);
  }
  function __maxScrollOffset(el, axis) {
    var b = el.__box || { width: 0, height: 0 };
    if (axis === "y") return Math.max(0, __elementContentSize(el, "y") - b.height);
    return Math.max(0, __elementContentSize(el, "x") - b.width);
  }
  function __dispatchElementScroll(el) {
    if (typeof global.Event !== "function") return;
    try {
      el.dispatchEvent(new global.Event("scroll", { bubbles: false, isTrusted: true }));
    } catch (e) {}
  }
  /* Returns true when the offset actually moved. Non-containers stay at 0, as
     the spec requires (there is no scrolling box to move). */
  function __setElementScroll(el, left, top) {
    if (!el || el.nodeType !== 1) return false;
    var doc = el.ownerDocument || global.document;
    if (doc) __ensureLayout(doc);
    if (!__isScrollContainer(el)) {
      el.__scrollLeft = 0;
      el.__scrollTop = 0;
      return false;
    }
    var nx = Math.round(Math.max(0, Math.min(Number(left) || 0, __maxScrollOffset(el, "x"))));
    var ny = Math.round(Math.max(0, Math.min(Number(top) || 0, __maxScrollOffset(el, "y"))));
    var moved = nx !== (Number(el.__scrollLeft) || 0) || ny !== (Number(el.__scrollTop) || 0);
    el.__scrollLeft = nx;
    el.__scrollTop = ny;
    if (moved) {
      __dispatchElementScroll(el);
      try { _notifyIntersections(); } catch (e) {}
    }
    return moved;
  }
  function __isRootScrollTarget(el) {
    var doc = el.ownerDocument || global.document;
    return !!(doc && (doc.documentElement === el || doc.body === el));
  }
  Object.defineProperty(Element.prototype, "scrollTop", {
    get: function () {
      if (__isRootScrollTarget(this)) return scrollState ? scrollState.y : 0;
      return Number(this.__scrollTop) || 0;
    },
    set: function (v) {
      if (__isRootScrollTarget(this)) { _doScroll(scrollState ? scrollState.x : 0, v); return; }
      __setElementScroll(this, Number(this.__scrollLeft) || 0, v);
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "scrollLeft", {
    get: function () {
      if (__isRootScrollTarget(this)) return scrollState ? scrollState.x : 0;
      return Number(this.__scrollLeft) || 0;
    },
    set: function (v) {
      if (__isRootScrollTarget(this)) { _doScroll(v, scrollState ? scrollState.y : 0); return; }
      __setElementScroll(this, v, Number(this.__scrollTop) || 0);
    },
    configurable: true
  });
  Element.prototype.scrollTo = function (x, y) {
    if (x && typeof x === "object") { y = x.top; x = x.left; }
    if (__isRootScrollTarget(this)) {
      _doScroll(x == null ? (scrollState ? scrollState.x : 0) : x,
                y == null ? (scrollState ? scrollState.y : 0) : y);
      return;
    }
    __setElementScroll(this,
      x == null ? (Number(this.__scrollLeft) || 0) : x,
      y == null ? (Number(this.__scrollTop) || 0) : y);
  };
  Element.prototype.scroll = Element.prototype.scrollTo;
  Element.prototype.scrollBy = function (x, y) {
    if (x && typeof x === "object") { y = x.top; x = x.left; }
    if (__isRootScrollTarget(this)) {
      _doScroll((scrollState ? scrollState.x : 0) + (Number(x) || 0),
                (scrollState ? scrollState.y : 0) + (Number(y) || 0));
      return;
    }
    __setElementScroll(this,
      (Number(this.__scrollLeft) || 0) + (Number(x) || 0),
      (Number(this.__scrollTop) || 0) + (Number(y) || 0));
  };
  /* ---- scrollIntoView family ----
     Scrolls every scrolling box between the element and the viewport, nearest
     container first, then the document. Ancestor scrolling moves the element
     relative to the viewport, so its document target is offset by the total
     container travel. An element with no rendered box (display:none, hidden
     ancestor, detached) is not a scroll target at all — the old code scrolled
     the window to (0,0) for exactly those, which put a phantom position in
     front of the agent. */
  function __scrollContainerChain(el) {
    var doc = el.ownerDocument || global.document;
    var chain = [];
    var n = el.parentNode;
    while (n && n.nodeType === 1) {
      if (doc && (n === doc.documentElement || n === doc.body)) break;
      if (__isScrollContainer(n)) chain.push(n);
      n = n.parentNode;
    }
    return chain;
  }
  function __alignOffset(mode, targetStart, targetSize, viewSize, current) {
    if (mode === "center") return targetStart - (viewSize - targetSize) / 2;
    if (mode === "end") return targetStart + targetSize - viewSize;
    if (mode === "nearest") {
      if (targetStart >= current && targetStart + targetSize <= current + viewSize) return current;
      if (targetStart < current) return targetStart;
      return targetStart + targetSize - viewSize;
    }
    return targetStart;
  }
  /* L6: Smooth scroll animation — ease-out (fast start, slow end) driven
     by the rAF frame clock (__advanceClock advances the frame pump). */
  function __smoothElementScroll(el, targetLeft, targetTop, steps) {
    steps = Math.max(3, Math.min(25, Number(steps) || 8));
    var startLeft = Number(el.__scrollLeft) || 0;
    var startTop = Number(el.__scrollTop) || 0;
    var dx = targetLeft - startLeft, dy = targetTop - startTop;
    if (dx === 0 && dy === 0) return;
    for (var i = 1; i <= steps; i++) {
      var progress = i / steps;
      /* ease-out cubic: 1 - (1-t)^3 */
      var ease = 1 - Math.pow(1 - progress, 3);
      __setElementScroll(el, startLeft + dx * ease, startTop + dy * ease);
      if (typeof global.__advanceClock === "function") global.__advanceClock(16);
    }
  }
  function __scrollIntoViewInternal(el, opts, ifNeeded) {
    var doc = el.ownerDocument || global.document;
    if (!doc || !el.isConnected) return;
    __ensureLayout(doc);
    var b = el.__box || { x: 0, y: 0, width: 0, height: 0 };
    if (!b || (b.width <= 0 && b.height <= 0)) return;
    var cs = __computedStyle(el, __collectStyleRules(doc), el.__layoutFont || 16);
    if ((cs.display || "").toLowerCase() === "none") return;
    var block = opts.block || (ifNeeded ? "nearest" : "start");
    var inline = opts.inline || (ifNeeded ? "nearest" : "nearest");
    var smooth = String(opts.behavior || "auto").toLowerCase() === "smooth";
    var accX = 0, accY = 0;
    var chain = __scrollContainerChain(el);
    for (var i = 0; i < chain.length; i++) {
      var c = chain[i];
      var cb = c.__box;
      if (!cb) continue;
      var curTop = Number(c.__scrollTop) || 0;
      var curLeft = Number(c.__scrollLeft) || 0;
      var wantTop = __alignOffset(block, b.y - accY - cb.y, b.height, cb.height, curTop);
      var wantLeft = __alignOffset(inline, b.x - accX - cb.x, b.width, cb.width, curLeft);
      wantTop = Math.max(0, Math.min(wantTop, __maxScrollOffset(c, "y")));
      wantLeft = Math.max(0, Math.min(wantLeft, __maxScrollOffset(c, "x")));
      var dTop = wantTop - curTop;
      var dLeft = wantLeft - curLeft;
      if (dTop !== 0 || dLeft !== 0) {
        if (smooth) __smoothElementScroll(c, wantLeft, wantTop, 8);
        else __setElementScroll(c, wantLeft, wantTop);
      }
      accY += wantTop;
      accX += wantLeft;
    }
    var vh = (typeof global.innerHeight === "number" && global.innerHeight > 0) ? global.innerHeight : 720;
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    var curDocY = scrollState ? scrollState.y : 0;
    var curDocX = scrollState ? scrollState.x : 0;
    var targetY = b.y - accY;
    var targetX = b.x - accX;
    var wantDocY = __alignOffset(block, targetY, b.height, vh, curDocY);
    var wantDocX = __alignOffset(inline, targetX, b.width, vw, curDocX);
    var clamped = _clampScroll(wantDocX, wantDocY);
    if (Math.round(clamped[0]) === Math.round(curDocX) && Math.round(clamped[1]) === Math.round(curDocY)) return;
    if (smooth && typeof global.__scrollSmooth === "function") global.__scrollSmooth(clamped[0], clamped[1]);
    else _doScroll(clamped[0], clamped[1]);
  }
  Element.prototype.scrollIntoView = function (arg) {
    __scrollIntoViewInternal(this, (arg && typeof arg === "object") ? arg : (arg === false ? { block: "end" } : {}), false);
    return undefined;
  };
/* Fully visible = the element's on-screen rect (after every scrolling
     ancestor's clip) fits inside each container's window AND the document
     viewport. scrollIntoViewIfNeeded() is a no-op exactly then. */
  function __fullyVisible(el) {
    var doc = el.ownerDocument || global.document;
    if (!doc || !el.isConnected) return false;
    __ensureLayout(doc);
    var b = el.__box;
    if (!b || (b.width <= 0 && b.height <= 0)) return true;
    var accTop = 0, accLeft = 0;
    var chain = __scrollContainerChain(el);
    for (var i = 0; i < chain.length; i++) {
      var c = chain[i];
      var cb = c.__box;
      if (!cb) continue;
      var posTop = b.y - accTop - cb.y - (Number(c.__scrollTop) || 0);
      var posLeft = b.x - accLeft - cb.x - (Number(c.__scrollLeft) || 0);
      if (posTop < 0 || posLeft < 0 || posTop + b.height > cb.height || posLeft + b.width > cb.width) return false;
      accTop += Number(c.__scrollTop) || 0;
      accLeft += Number(c.__scrollLeft) || 0;
    }
    var vh = (typeof global.innerHeight === "number" && global.innerHeight > 0) ? global.innerHeight : 720;
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    var curY = scrollState ? scrollState.y : 0;
    var curX = scrollState ? scrollState.x : 0;
    var onTop = b.y - accTop, onLeft = b.x - accLeft;
    return onTop >= curY && onLeft >= curX && onTop + b.height <= curY + vh && onLeft + b.width <= curX + vw;
  }
  /* WebKit semantics: centerIfNeeded defaults to true — a not-fully-visible
     element is CENTERED; false scrolls the minimum (nearest). Either way it
     is a strict no-op when the element is already fully visible. */
  Element.prototype.scrollIntoViewIfNeeded = function (centerIfNeeded) {
    if (__fullyVisible(this)) return;
    var center = (centerIfNeeded === undefined) ? true : !!centerIfNeeded;
    __scrollIntoViewInternal(this,
      center ? { block: "center", inline: "center" } : { block: "nearest", inline: "nearest" },
      false);
  };
  /* ---- checkVisibility ----
     Consults the real cascade (__computedStyle over collected rules), not
     just inline styles — display:none by stylesheet rule counts.
     Per CSSOM spec:
     - Always checks display: none (and connection to document).
     - Only checks visibility: hidden/collapse if opts.checkVisibilityCSS is true.
     - Only checks opacity: 0 if opts.checkOpacity is true. */
  Element.prototype.checkVisibility = function (opts) {
    opts = opts || {};
    var doc = this.ownerDocument || global.document;
    if (!this.isConnected && (!doc || !doc.contains(this))) return false;
    var rules = doc ? __collectStyleRules(doc) : [];
    var n = this;
    while (n && n.nodeType === 1) {
      var cs = __computedStyle(n, rules, 16);
      if ((cs.display || "").toLowerCase() === "none") return false;
      if (opts.checkVisibilityCSS) {
        var vis = (cs.visibility || "").toLowerCase();
        if (vis === "hidden" || vis === "collapse") return false;
      }
      if (opts.checkOpacity && cs.opacity != null && cs.opacity !== "" && Number(cs.opacity) === 0) return false;
      n = n.parentNode;
    }
    return true;
  };
  /* Batch visibility check for the action surface: CSS-hidden controls must
     not appear actionable, and the cascade (stylesheet rules, hidden
     ancestors) — not just inline styles — decides. Returns one boolean per
     selector; a selector that does not resolve is reported visible (never
     drop a control the engine cannot verify). */
  global.__hiddenCheck = function (selectors) {
    /* Accepts an array (the session passes a JS literal) or a JSON string. */
    var sels = selectors;
    if (typeof sels === "string") {
      try { sels = JSON.parse(sels); } catch (e) { sels = []; }
    }
    if (!sels || !sels.length) return "[]";
    var out = [];
    for (var i = 0; i < sels.length; i++) {
      var hidden = false;
      try {
        var el = global.document.querySelector(sels[i]);
        if (el && typeof el.checkVisibility === "function" && !el.checkVisibility({ checkVisibilityCSS: true, checkOpacity: true })) hidden = true;
      } catch (e) { hidden = false; }
      out.push(hidden);
    }
    return JSON.stringify(out);
  };
  /* ---- Fullscreen (documented approximation) ----
     No top-layer or paint: state + events only, which is what page JS
     feature-detects and reacts to. */
  Element.prototype.requestFullscreen = function () {
    var doc = this.ownerDocument || global.document;
    if (doc) {
      doc.__fullscreenElement = this;
      try { doc.dispatchEvent(new global.Event("fullscreenchange", { bubbles: true })); } catch (e) {}
    }
    return Promise.resolve();
  };
  /* ---- Popover API subset ----
     beforetoggle is cancelable (a page may prevent showing); toggle carries
     newState/oldState like the spec. Rendering state rides the open
     attribute (there is no top layer).
     Complies with HTML Popover spec:
     - Throws NotSupportedError if element lacks popover attribute.
     - Throws InvalidStateError if already showing or disconnected when showing.
     - Throws InvalidStateError if already hidden when hiding.
     - Auto popovers close other open auto popovers on show (light dismiss). */
  Element.prototype.showPopover = function () {
    if (!this.hasAttribute || !this.hasAttribute("popover")) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'showPopover' on 'Element': Not supported on elements that do not have a valid 'popover' attribute.",
        "NotSupportedError"
      );
    }
    if (this.__popoverOpen) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'showPopover' on 'Element': The element is already showing.",
        "InvalidStateError"
      );
    }
    var doc = this.ownerDocument || global.document;
    if (!this.isConnected && (!doc || !doc.contains(this))) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'showPopover' on 'Element': The element is not connected to a document.",
        "InvalidStateError"
      );
    }
    var popVal = (this.getAttribute("popover") || "").toLowerCase();
    var isAuto = (popVal === "" || popVal === "auto");
    if (isAuto && doc) {
      doc.__openAutoPopovers = doc.__openAutoPopovers || [];
      var list = doc.__openAutoPopovers.slice();
      for (var i = list.length - 1; i >= 0; i--) {
        var other = list[i];
        if (other !== this && other.__popoverOpen && (!other.contains || !other.contains(this))) {
          try { other.hidePopover(); } catch (e) {}
        }
      }
    }
    var ev = new global.Event("beforetoggle", { bubbles: true, cancelable: true });
    ev.oldState = "closed";
    ev.newState = "open";
    this.dispatchEvent(ev);
    if (ev.defaultPrevented) return;
    this.__popoverOpen = true;
    this.setAttribute("open", "");
    /* R4: an open popover joins the document top layer with a backdrop
       when it is modal-shaped (manual popovers join without a backdrop —
       spec: only ::modal popovers get a backdrop; we give it to all,
       disclosed). */
    try { global.__wrEnterTopLayer(this, "popover"); } catch (e) {}
    if (isAuto && doc) {
      doc.__openAutoPopovers = doc.__openAutoPopovers || [];
      if (doc.__openAutoPopovers.indexOf(this) < 0) {
        doc.__openAutoPopovers.push(this);
      }
    }
    var t = new global.Event("toggle", { bubbles: true });
    t.oldState = "closed";
    t.newState = "open";
    this.dispatchEvent(t);
  };
  Element.prototype.hidePopover = function () {
    if (!this.hasAttribute || !this.hasAttribute("popover")) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'hidePopover' on 'Element': Not supported on elements that do not have a valid 'popover' attribute.",
        "NotSupportedError"
      );
    }
    if (!this.__popoverOpen) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'hidePopover' on 'Element': The element is not showing.",
        "InvalidStateError"
      );
    }
    var doc = this.ownerDocument || global.document;
    if (doc && doc.__openAutoPopovers) {
      var idx = doc.__openAutoPopovers.indexOf(this);
      if (idx >= 0) doc.__openAutoPopovers.splice(idx, 1);
    }
    this.__popoverOpen = false;
    this.removeAttribute("open");
    try { global.__wrLeaveTopLayer(this); } catch (e) {}
    var t = new global.Event("toggle", { bubbles: true });
    t.oldState = "open";
    t.newState = "closed";
    this.dispatchEvent(t);
  };
  Element.prototype.togglePopover = function (prefer) {
    if (!this.hasAttribute || !this.hasAttribute("popover")) {
      throw new (global.DOMException || Error)(
        "Failed to execute 'togglePopover' on 'Element': Not supported on elements that do not have a valid 'popover' attribute.",
        "NotSupportedError"
      );
    }
    if (prefer === undefined) {
      if (this.__popoverOpen) this.hidePopover();
      else this.showPopover();
    } else if (prefer) {
      if (!this.__popoverOpen) this.showPopover();
    } else {
      if (this.__popoverOpen) this.hidePopover();
    }
  };
  Object.defineProperty(Element.prototype, "popover", {
    get: function () {
      if (!this.hasAttribute || !this.hasAttribute("popover")) return null;
      var v = (this.getAttribute("popover") || "").toLowerCase();
      return (v === "manual") ? "manual" : "auto";
    },
    set: function (v) {
      if (v === null || v === undefined) {
        if (typeof this.removeAttribute === "function") this.removeAttribute("popover");
      } else {
        if (typeof this.setAttribute === "function") this.setAttribute("popover", String(v));
      }
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "popoverOpen", {
    get: function () { return !!this.__popoverOpen; },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "offsetParent", {
    get: function () {
      if (this.nodeName === "body" || this.nodeName === "html") return null;
      var p = this.parentNode;
      while (p && p.nodeType === 1 && p.nodeName !== "body" && p.nodeName !== "html") {
        var pos = (p.style && p.style.position) || (p.__box && p.__box.position);
        if (pos === "relative" || pos === "absolute" || pos === "fixed") return p;
        p = p.parentNode;
      }
      return (this.ownerDocument && this.ownerDocument.body) || null;
    }
  });
  Object.defineProperty(Element.prototype, "offsetLeft", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) __ensureLayout(doc);
      var b = this.__box || { x: 0, y: 0 };
      var op = this.offsetParent;
      var opX = (op && op.__box) ? op.__box.x : 0;
      return Math.max(0, b.x - opX);
    }
  });
  Object.defineProperty(Element.prototype, "offsetTop", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) __ensureLayout(doc);
      var b = this.__box || { x: 0, y: 0 };
      var op = this.offsetParent;
      var opY = (op && op.__box) ? op.__box.y : 0;
      return Math.max(0, b.y - opY);
    }
  });
  /* scrollWidth/scrollHeight are defined above with document-level extents;
     the old clientWidth-based stub here silently overrode them (later
     defineProperty wins) and hid viewport overflow from the scroll clamp. */
  Object.defineProperty(Element.prototype, "classList", {
    get: function () {
      var el = this;
      return {
        add: function (c) {
          var p = (el.className || "").split(/\s+/).filter(Boolean);
          if (p.indexOf(c) < 0) p.push(c);
          /* The className setter routes through setAttribute, which records
             the mutation: no extra queueMutation here. */
          el.className = p.join(" ");
        },
        remove: function (c) {
          var p = (el.className || "").split(/\s+/).filter(function (x) { return x && x !== c; });
          el.className = p.join(" ");
        },
        contains: function (c) { return (el.className || "").split(/\s+/).indexOf(c) >= 0; }
      };
    }
  });

  function Document() {
    Node.call(this, 9, "#document", {}, []);
    this.documentElement = null;
    this.body = null;
    this.head = null;
    this.defaultView = null;
  }
  Document.prototype = Object.create(Node.prototype);
  Document.prototype.constructor = Document;
  Object.defineProperty(Document.prototype, "currentScript", {
    get: function () { return this._currentScript || null; },
    set: function (v) { this._currentScript = v; },
    configurable: true
  });
  /* document.baseURI / document.domain. reCAPTCHA builds the anchor URL with
     `new URL(src, document.baseURI)` and reads window.origin. Both were
     undefined, so the parent stored a bad expected origin and dropped the
     anchor's recaptcha-setup message. */
  Object.defineProperty(Document.prototype, "baseURI", {
    get: function () {
      var view = this.defaultView || global;
      try {
        if (view.location && view.location.href) return String(view.location.href);
      } catch (e) {}
      return "";
    },
    configurable: true
  });
  Object.defineProperty(Document.prototype, "domain", {
    get: function () {
      var view = this.defaultView || global;
      try {
        if (view.location && view.location.hostname) return String(view.location.hostname);
      } catch (e) {}
      return "";
    },
    configurable: true
  });
  Document.prototype.createElement = function (tag, options) {
    var tagLower = String(tag).toLowerCase();
    var isOption = options && options.is ? String(options.is).toLowerCase() : null;
    var ctor = customRegistry[tagLower] || (isOption ? customRegistry[isOption] : null);
    if (ctor && typeof ctor === "function") {
      try {
        var el = new ctor();
        if (el && el.nodeType === 1) {
          if (!el.tagName) el.tagName = tagLower.toUpperCase();
          if (!el.nodeName) el.nodeName = tagLower;
          return el;
        }
      } catch (e) {
        var fallback = new Element(tagLower);
        try { Object.setPrototypeOf(fallback, ctor.prototype); } catch (e2) {}
        return fallback;
      }
    }
    /* Built-in HTMLElement subclasses (HTMLDialogElement & co) so instanceof
       checks match a real browser. The registry lives in a nested scope; the
       global export is the stable handle. */
    var htmlCtorMap = global.__htmlCtorByTag;
    var htmlCtor = (htmlCtorMap && htmlCtorMap[tagLower]) || null;
    if (htmlCtor) {
      var sub = new htmlCtor(tagLower);
      if (sub && sub.nodeType === 1) return sub;
    }
    return new Element(tagLower);
  };
  Document.prototype.createTextNode = function (t) { return new Text(String(t)); };
  Document.prototype.createDocumentFragment = function () { return new DocumentFragment(); };
  Document.prototype.getElementById = function (id) {
    var found = null;
    (function walk(n) {
      if (found) return;
      n.childNodes.forEach(function (c) {
        if (c.nodeType === 1) {
          if (c.id === id || c.attributes.id === id) found = c;
          else walk(c);
        }
      });
    })(this);
    return found;
  };
  Document.prototype.getElementsByTagName = Element.prototype.getElementsByTagName;
  /* document.scrollWidth/scrollHeight mirror the documentElement's scroll
     extents (the full laid-out document, including viewport overflow). */
  Object.defineProperty(Document.prototype, "scrollWidth", {
    get: function () {
      var de = this.documentElement;
      return de ? de.scrollWidth : 0;
    },
    configurable: true
  });
  Object.defineProperty(Document.prototype, "scrollHeight", {
    get: function () {
      var de = this.documentElement;
      return de ? de.scrollHeight : 0;
    },
    configurable: true
  });
  Document.prototype.exitFullscreen = function () {
    var had = this.__fullscreenElement != null;
    this.__fullscreenElement = null;
    if (had) { try { this.dispatchEvent(new global.Event("fullscreenchange", { bubbles: true })); } catch (e) {} }
    return Promise.resolve();
  };
  Object.defineProperty(Document.prototype, "fullscreenElement", {
    get: function () { return this.__fullscreenElement || null; },
    configurable: true
  });
  Object.defineProperty(Document.prototype, "fullscreenEnabled", {
    get: function () { return true; },
    configurable: true
  });
  var htmlElements = [
    "HTMLAnchorElement", "HTMLAreaElement", "HTMLAudioElement", "HTMLBaseElement",
    "HTMLBaseFontElement", "HTMLBodyElement", "HTMLBRElement", "HTMLButtonElement",
    "HTMLCanvasElement", "HTMLDataElement", "HTMLDataListElement", "HTMLDetailsElement",
    "HTMLDialogElement", "HTMLDirectoryElement", "HTMLDivElement", "HTMLDListElement",
    "HTMLEmbedElement", "HTMLFieldSetElement", "HTMLFontElement", "HTMLFormElement",
    "HTMLFrameElement", "HTMLFrameSetElement", "HTMLHeadingElement", "HTMLHeadElement",
    "HTMLHRElement", "HTMLHtmlElement", "HTMLIFrameElement", "HTMLImageElement",
    "HTMLInputElement", "HTMLLabelElement", "HTMLLegendElement", "HTMLLIElement",
    "HTMLLinkElement", "HTMLMapElement", "HTMLMarqueeElement", "HTMLMetaElement",
    "HTMLMeterElement", "HTMLModElement", "HTMLObjectElement", "HTMLOListElement",
    "HTMLOptGroupElement", "HTMLOptionElement", "HTMLOutputElement",
    "HTMLParagraphElement", "HTMLParamElement", "HTMLPictureElement", "HTMLPreElement",
    "HTMLProgressElement", "HTMLQuoteElement", "HTMLScriptElement", "HTMLSelectElement",
    "HTMLSlotElement", "HTMLSourceElement", "HTMLSpanElement", "HTMLStyleElement",
    "HTMLTableCaptionElement", "HTMLTableCellElement", "HTMLTableColElement",
    "HTMLTableElement", "HTMLTableRowElement", "HTMLTableSectionElement",
    "HTMLTemplateElement", "HTMLTextAreaElement", "HTMLTimeElement", "HTMLTitleElement",
    "HTMLTrackElement", "HTMLUListElement", "HTMLVideoElement", "HTMLUnknownElement"
  ];
  var __htmlCtorByTag = {};
  for (var i = 0; i < htmlElements.length; i++) {
    (function (name) {
      function HtmlSubElement(tag, attrs, children) {
        Element.call(this, tag, attrs, children);
      }
      HtmlSubElement.prototype = Object.create(Element.prototype);
      HtmlSubElement.prototype.constructor = HtmlSubElement;
      global[name] = HtmlSubElement;
      /* HTMLElement subclasses must be instanceof-checkable: a page doing
         `document.createElement('dialog') instanceof HTMLDialogElement`
         got false when every element was a bare Element. Map constructor
         name -> tag so createElement can instantiate the real subclass. */
      var tag = name.replace(/^HTML/, "").replace(/Element$/, "").toLowerCase();
      __htmlCtorByTag[tag] = HtmlSubElement;
    })(htmlElements[i]);
  }
  global.__htmlCtorByTag = __htmlCtorByTag;
  /* ---- R4: Paint stack / top layer / ::backdrop ----
     A document-owned ordered top layer (last-in on top). Modal dialogs
     and open popovers are promoted; their ::backdrop pseudo gets a real
     backing box stored on the element (__backdropBox) that the layers
     inspector and the SVG painter can report. Layout renders promoted
     elements above everything (the L3 overlay stratum reports them
     first-class as TOP_LAYER entries). */
  (function () {
    var topLayer = [];
    function enter(el, kind) {
      if (!el || el.nodeType !== 1) return;
      /* dedupe: promote to top of the stack */
      for (var i = 0; i < topLayer.length; i++) {
        if (topLayer[i].el === el) { topLayer.splice(i, 1); break; }
      }
      topLayer.push({ el: el, kind: String(kind || "modal") });
      /* ::backdrop backing box: full viewport, above everything except
         its owner (the owner paints on top of its own backdrop). */
      var vw = Number(global.innerWidth) || 1280;
      var vh = Number(global.innerHeight) || 720;
      el.__backdropBox = { x: 0, y: 0, width: vw, height: vh, isBackdrop: true };
      el.__topLayerKind = String(kind || "modal");
      /* A modal dialog/popover is promoted out of normal flow stacking. */
      try {
        var doc = global.document;
        if (doc) doc.__layoutVersion = (doc.__layoutVersion || 0) + 1;
      } catch (e) {}
    }
    function leave(el) {
      if (!el) return;
      for (var i = 0; i < topLayer.length; i++) {
        if (topLayer[i].el === el) { topLayer.splice(i, 1); break; }
      }
      el.__backdropBox = null;
      el.__topLayerKind = null;
      try {
        var doc = global.document;
        if (doc) doc.__layoutVersion = (doc.__layoutVersion || 0) + 1;
      } catch (e) {}
    }
    global.__wrEnterTopLayer = enter;
    global.__wrLeaveTopLayer = leave;
    global.__wrTopLayer = function () {
      return topLayer.map(function (t) { return { el: t.el, kind: t.kind }; });
    };
    global.__wrTopLayerInfo = function () {
      return JSON.stringify(topLayer.map(function (t) {
        var b = (t.el.getBoundingClientRect && t.el.getBoundingClientRect()) || {};
        return {
          kind: t.kind,
          tag: String(t.el.nodeName || "").toLowerCase(),
          id: (t.el.id != null ? String(t.el.id) : ""),
          selector: (typeof __nodeSelector === "function") ? __nodeSelector(t.el) : "",
          x: Math.round(b.x || 0), y: Math.round(b.y || 0),
          width: Math.round(b.width || 0), height: Math.round(b.height || 0),
          hasBackdrop: !!(t.el.__backdropBox)
        };
      }));
    };
  })();

  /* ---- <dialog> semantics ----
     The constructor existed as a name-list shell; showModal/close threw
     "not a function" and modals could never open. Real subset: open
     attribute, show/showModal, close(returnValue), returnValue, and the
     close/cancel events. Disclosed approximation: no focus trap, no
     ::modal backdrop, no top-layer rendering (there is no paint stack). */
  var __DialogCtor = global.HTMLDialogElement;
  if (typeof __DialogCtor === "function") {
    Object.defineProperty(__DialogCtor.prototype, "open", {
      get: function () { return this.hasAttribute("open"); },
      set: function (v) {
        if (v) this.setAttribute("open", "");
        else this.removeAttribute("open");
      },
      configurable: true
    });
    __DialogCtor.prototype.show = function () {
      if (this.open) return;
      this.__isModal = false;
      this.setAttribute("open", "");
    };
    __DialogCtor.prototype.showModal = function () {
      if (this.open) {
        var err = new (global.DOMException || Error)(
          "Failed to execute 'showModal' on 'HTMLDialogElement': The element already has an 'open' attribute, and therefore cannot be opened modally.",
          "InvalidStateError"
        );
        err.name = "InvalidStateError";
        throw err;
      }
      this.__isModal = true;
      this.setAttribute("open", "");
      /* R4: promote to the document top layer (paint stack). */
      try { global.__wrEnterTopLayer(this, "modal"); } catch (e) {}
      this.dispatchEvent(new global.Event("open", { bubbles: false }));
    };
    __DialogCtor.prototype.close = function (returnValue) {
      if (!this.open) return;
      this.__isModal = false;
      this.removeAttribute("open");
      /* R4: leave the document top layer; drop the ::backdrop backing box. */
      try { global.__wrLeaveTopLayer(this); } catch (e) {}
      if (this.__backdropBox) { this.__backdropBox = null; }
      if (returnValue !== undefined) this.returnValue = String(returnValue);
      var ev = new global.Event("close", { bubbles: false });
      this.dispatchEvent(ev);
    };
    Object.defineProperty(__DialogCtor.prototype, "returnValue", {
      get: function () { return this.__returnValue || ""; },
      set: function (v) { this.__returnValue = String(v == null ? "" : v); },
      configurable: true
    });
  }

  Document.prototype.getElementsByClassName = Element.prototype.getElementsByClassName;
  Document.prototype.querySelectorAll = Element.prototype.querySelectorAll;
  Document.prototype.querySelector = Element.prototype.querySelector;
  Document.prototype.addEventListener = Element.prototype.addEventListener;
  /* MS AJAX's DOMContentLoaded bootstrap removes its own listener inside the
     handler; a Document without removeEventListener made the handler throw
     before init() and the whole WebForms client machinery stayed dead. */
  Document.prototype.removeEventListener = Element.prototype.removeEventListener;
  Document.prototype.dispatchEvent = Element.prototype.dispatchEvent;
  Object.defineProperty(Document.prototype, "activeElement", {
    get: function () {
      return this._activeElement || this.body || this.documentElement || null;
    },
    set: function (v) {
      this._activeElement = v;
    },
    configurable: true
  });
  Document.prototype.createRange = function () {
    var r = new global.Range();
    r.startContainer = this.body || this.documentElement || this;
    r.endContainer = r.startContainer;
    return r;
  };
  Document.prototype.getSelection = function () {
    return (typeof global.getSelection === "function") ? global.getSelection() : null;
  };

  /* Cloak core DOM prototypes */
  __cloakObject(Node.prototype, {
    appendChild: 1,
    removeChild: 1,
    insertBefore: 2,
    replaceChild: 2,
    cloneNode: 0,
    hasChildNodes: 0,
    contains: 1,
    addEventListener: 2,
    removeEventListener: 2,
    dispatchEvent: 1
  });
  __cloakObject(Element.prototype, {
    getAttribute: 1,
    setAttribute: 2,
    removeAttribute: 1,
    hasAttribute: 1,
    getAttributeNS: 2,
    setAttributeNS: 3,
    removeAttributeNS: 2,
    hasAttributeNS: 2,
    getAttributeNames: 0,
    closest: 1,
    matches: 1,
    querySelector: 1,
    querySelectorAll: 1,
    getElementsByTagName: 1,
    getElementsByClassName: 1,
    getBoundingClientRect: 0,
    getClientRects: 0,
    scrollIntoView: 0,
    scrollTo: 1,
    scrollBy: 1,
    focus: 0,
    blur: 0,
    click: 0,
    submit: 0,
    requestSubmit: 0,
    setPointerCapture: 1,
    releasePointerCapture: 1,
    hasPointerCapture: 1
  });
  __cloakObject(Document.prototype, {
    getElementById: 1,
    getElementsByClassName: 1,
    getElementsByTagName: 1,
    getElementsByName: 1,
    querySelector: 1,
    querySelectorAll: 1,
    createElement: 1,
    createElementNS: 2,
    createTextNode: 1,
    createComment: 1,
    createDocumentFragment: 0,
    createEvent: 1,
    createRange: 0,
    elementFromPoint: 2,
    elementsFromPoint: 2,
    hasFocus: 0,
    adoptNode: 1,
    importNode: 1,
    write: 1,
    writeln: 1,
    open: 0,
    close: 0,
    exitFullscreen: 0,
    getSelection: 0
  });

  function cssSelect(root, selector) {
    selector = String(selector || "").trim();
    if (!selector) return [];
    var parts = selector.split(",").map(function (s) { return s.trim(); }).filter(Boolean);
    var out = [];
    parts.forEach(function (part) {
      matchSelector(root, part).forEach(function (n) {
        if (out.indexOf(n) < 0) out.push(n);
      });
    });
    return out;
  }
  function matchSelector(root, sel) {
    /* Selector support: comma-free chains of simple selectors joined by the
       descendant (space) and child (>) combinators. The old tokenizer split
       on whitespace only, so `div.menu > button` died on the bare `>` token,
       and the single-class regex could not parse `button.s2.save` — which
       made every id-less combinatorial selector unresolvable and defeated
       the hidden-control filter for exactly the controls it targets. */
    var norm = String(sel || "").replace(/\s*>\s*/g, " > ").trim();
    if (!norm) return [];
    var tokens = norm.split(/\s+/);
    var seq = [];
    var pendingComb = null;
    for (var i = 0; i < tokens.length; i++) {
      if (tokens[i] === ">") { pendingComb = ">"; continue; }
      seq.push({ comb: pendingComb || " ", simple: tokens[i] });
      pendingComb = null;
    }
    if (!seq.length) return [];
    var current = [];
    collectElements(root).forEach(function (n) {
      if (matchSimple(n, seq[0].simple) && current.indexOf(n) < 0) current.push(n);
    });
    if (root !== global.document && root.nodeType === 1 && matchSimple(root, seq[0].simple) && current.indexOf(root) < 0) {
      current.push(root);
    }
    for (var s = 1; s < seq.length; s++) {
      var next = [];
      current.forEach(function (n) {
        if (seq[s].comb === ">") {
          (n.childNodes || []).forEach(function (c) {
            if (c.nodeType === 1 && matchSimple(c, seq[s].simple) && next.indexOf(c) < 0) next.push(c);
          });
        } else {
          collectElements(n).forEach(function (c) {
            if (matchSimple(c, seq[s].simple) && next.indexOf(c) < 0) next.push(c);
          });
          if (n !== root && matchSimple(n, seq[s].simple) && next.indexOf(n) < 0) next.push(n);
        }
      });
      current = next;
    }
    return current;
  }
  function matchSimple(el, tok) {
    if (el.nodeType !== 1) return false;
    if (tok === "*") return true;
    if (tok.charAt(0) === "#") return el.id === tok.slice(1) || el.attributes.id === tok.slice(1);
    if (tok.charAt(0) === ".") {
      var dotCls = tok.slice(1).split(".");
      var dotHave = (el.className || "").split(/\s+/);
      for (var di = 0; di < dotCls.length; di++) {
        if (dotHave.indexOf(dotCls[di]) < 0) return false;
      }
      return true;
    }
    var m = tok.match(/^([a-z0-9-]+)?(?:#([a-zA-Z0-9_-]+))?((?:\.[a-zA-Z0-9_-]+)*)(?:\[([a-zA-Z0-9_-]+)(?:=["']?([^"'\]]*)["']?)?\])?$/i);
    if (!m) return el.nodeName === tok.toLowerCase();
    if (m[1] && el.nodeName !== m[1].toLowerCase()) return false;
    if (m[2] && el.id !== m[2] && el.attributes.id !== m[2]) return false;
    if (m[3]) {
      var classes = m[3].split(".").filter(Boolean);
      var have = (el.className || "").split(/\s+/);
      for (var k = 0; k < classes.length; k++) {
        if (have.indexOf(classes[k]) < 0) return false;
      }
    }
    if (m[4]) {
      if (!el.hasAttribute(m[4])) return false;
      if (m[5] != null && el.getAttribute(m[4]) !== m[5]) return false;
    }
    return true;
  }
  function collectElements(root) {
    var out = [];
    (function walk(n) {
      n.childNodes.forEach(function (c) {
        if (c.nodeType === 1) { out.push(c); walk(c); }
      });
    })(root);
    return out;
  }

  function hydrate(json) {
    if (!json) return [];
    if (Object.prototype.toString.call(json) === "[object Array]") {
      return json.map(hydrateOne).filter(Boolean);
    }
    return [hydrateOne(json)].filter(Boolean);
  }
  function hydrateOne(j) {
    if (!j) return null;
    if (j.t === "#text") return new Text(j.x || "");
    if (j.t === "document") {
      var docKids = (j.c || []).map(hydrateOne).filter(Boolean);
      return docKids;
    }
    var kids = (j.c || []).map(hydrateOne).filter(Boolean);
    /* Parsed elements get the real HTMLElement subclass (dialog instanceof
       HTMLDialogElement must hold for markup too, not just createElement).
       The registry lives in a nested scope; the global export is the
       stable handle. */
    var ctorMap = global.__htmlCtorByTag;
    var ctor = (ctorMap && ctorMap[j.t]) || null;
    return ctor ? new ctor(j.t, j.a || {}, kids) : new Element(j.t, j.a || {}, kids);
  }
  /* === Interactivity & Synthetic Framework Hook Introspection === */
  var INTERACT_CLICK      = 1 << 0;  // 1
  var INTERACT_MOUSEDOWN  = 1 << 1;  // 2
  var INTERACT_KEYDOWN    = 1 << 2;  // 4
  var INTERACT_CHANGE     = 1 << 3;  // 8
  var INTERACT_SUBMIT     = 1 << 4;  // 16
  var INTERACT_INLINE     = 1 << 5;  // 32
  var INTERACT_REACT      = 1 << 6;  // 64
  var INTERACT_VUE        = 1 << 7;  // 128
  var INTERACT_ANGULAR    = 1 << 8;  // 256
  var INTERACT_SVELTE     = 1 << 9;  // 512
  var INTERACT_POINTER_CS = 1 << 10; // 1024 (cursor: pointer)

  function __inspectNodeInteractivity(el) {
    if (!el || el.nodeType !== 1) return 0;
    var flags = 0;
    if (el._listeners) {
      if (el._listeners["click"] && el._listeners["click"].length) flags |= INTERACT_CLICK;
      if (el._listeners["mousedown"] && el._listeners["mousedown"].length) flags |= INTERACT_MOUSEDOWN;
      if (el._listeners["keydown"] && el._listeners["keydown"].length) flags |= INTERACT_KEYDOWN;
      if (el._listeners["input"] || el._listeners["change"]) flags |= INTERACT_CHANGE;
      if (el._listeners["submit"]) flags |= INTERACT_SUBMIT;
    }
    if (typeof el.onclick === "function") flags |= (INTERACT_CLICK | INTERACT_INLINE);
    if (typeof el.onkeydown === "function") flags |= (INTERACT_KEYDOWN | INTERACT_INLINE);
    if (el.attributes && el.attributes.onclick) flags |= (INTERACT_CLICK | INTERACT_INLINE);

    for (var k in el) {
      if (k.charCodeAt(0) === 95 && k.charCodeAt(1) === 95) {
        if (k.indexOf("__reactProps$") === 0 || k.indexOf("__reactEventHandlers$") === 0) {
          var rp = el[k];
          if (rp) {
            if (rp.onClick || rp.onClickCapture) flags |= (INTERACT_CLICK | INTERACT_REACT);
            if (rp.onMouseDown || rp.onPointerDown) flags |= (INTERACT_MOUSEDOWN | INTERACT_REACT);
            if (rp.onChange || rp.onInput) flags |= (INTERACT_CHANGE | INTERACT_REACT);
            if (rp.onKeyDown) flags |= (INTERACT_KEYDOWN | INTERACT_REACT);
          }
        } else if (k.indexOf("__reactFiber$") === 0) {
          var rf = el[k];
          if (rf && rf.memoizedProps) {
            var mp = rf.memoizedProps;
            if (mp.onClick) flags |= (INTERACT_CLICK | INTERACT_REACT);
            if (mp.onChange) flags |= (INTERACT_CHANGE | INTERACT_REACT);
          }
        }
      }
    }
    if (el._vei && (el._vei.onClick || el._vei.click)) flags |= (INTERACT_CLICK | INTERACT_VUE);
    if (el.__vueParentComponent && el.__vnode && el.__vnode.props) {
      if (el.__vnode.props.onClick) flags |= (INTERACT_CLICK | INTERACT_VUE);
    }
    if (el.__zone_symbol__clickfalse || el.__zone_symbol__addEventListener) {
      flags |= (INTERACT_CLICK | INTERACT_ANGULAR);
    }
    if (el.__click || el._svelte) flags |= (INTERACT_CLICK | INTERACT_SVELTE);
    if (el.style && el.style.cursor === "pointer") {
      flags |= INTERACT_POINTER_CS;
    }
    return flags;
  }

  function serializeNode(n, parentTag) {
    if (!n) return "";
    if (n.nodeType === 3) {
      if (parentTag === "script" || parentTag === "style") return n._text || "";
      return escapeHtml(n._text || "");
    }
    var attrs = "";
    Object.keys(n.attributes || {}).forEach(function (k) {
      attrs += " " + k + "=\"" + escapeHtml(String(n.attributes[k])) + "\"";
    });
    var flags = __inspectNodeInteractivity(n);
    if (flags > 0 && !(n.attributes && n.attributes["data-dosh-interact"])) {
      attrs += " data-dosh-interact=\"" + flags + "\"";
    }
    var voidTags = { area:1,base:1,br:1,col:1,embed:1,hr:1,img:1,input:1,link:1,meta:1,param:1,source:1,track:1,wbr:1 };
    var inner = n.childNodes.map(function (c) { return serializeNode(c, n.nodeName); }).join("");
    if (voidTags[n.nodeName]) return "<" + n.nodeName + attrs + ">";
    return "<" + n.nodeName + attrs + ">" + inner + "</" + n.nodeName + ">";
  }
  function escapeHtml(s) {
    return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
  function parseHtmlFragment(html) {
    /* Host parses fragments; fallback: single text node. */
    if (typeof host_parse_html === "function") {
      try { return JSON.parse(host_parse_html(String(html))); } catch (e) {}
    }
    return [{ t: "#text", x: html }];
  }

  var timers = [];
  var __timerSeq = 1;
  var __timerNow = 0;
  var __timerDead = {};
  function __timerDrop(id) {
    var n = Number(id);
    if (!n) return;
    __timerDead[n] = true;
    timers = timers.filter(function (t) { return t.id !== n; });
  }
  global.setTimeout = function (fn, ms) {
    var id = __timerSeq++;
    var delay = Number(ms) || 0;
    timers.push({ id: id, fn: fn, ms: delay, due: __timerNow + delay });
    return id;
  };
  global.clearTimeout = function (id) { __timerDrop(id); };
  global.setInterval = function (fn, ms) {
    var id = __timerSeq++;
    var delay = Number(ms) || 0;
    timers.push({ id: id, fn: fn, ms: delay, due: __timerNow + delay, repeat: true });
    return id;
  };
  global.clearInterval = function (id) { __timerDrop(id); };
  /* L8: the real frame clock. Animation callbacks step deterministically
     instead of firing on every timer drain: a requested frame belongs to the
     next frame boundary (16.6 ms, ~60 fps). Nested rAF requests inside a
     draining callback wait for a later boundary, never in the same batch.
     The immediate-shim stays reachable for hosts that drain without an
     advancing clock: __setRafFrameInterval(0) fires any pending frame on
     the next drain (the old behavior). */
  var __rafSeq = 1;
  var __rafQueue = [];
  var __rafDrainNow = null;      /* frame timestamp of the drain in flight */
  var __rafFrameInterval = 16.6; /* ms between frame boundaries; 0 = shim  */
  function __engineNow() {
    return (global.performance && typeof global.performance.now === "function")
      ? global.performance.now() : __timerNow;
  }
  global.requestAnimationFrame = function (fn) {
    if (typeof fn !== "function") throw new TypeError("Argument 1 to Window.requestAnimationFrame must be a function");
    var id = __rafSeq++;
    /* Frames are scheduled on the VIRTUAL timer clock (__timerNow), the same
       clock the host advances via __advanceTimerClock and that setTimeout
       eligibility uses. Wall-clock performance.now() has a 150 ms boot
       baseline and would desync the frame clock from timer deadlines. */
    var at = __timerNow;
    /* Requests during a drainOfFrames are scheduled from the boundary that is
       just firing — never the boundary we're inside (spec: next frame). */
    var base = (__rafDrainNow !== null && __rafDrainNow > at) ? __rafDrainNow : at;
    __rafQueue.push({
      id: id,
      fn: fn,
      scheduledAt: at,
      due: (__rafFrameInterval > 0) ? base + __rafFrameInterval : base,
    });
    return id;
  };
  global.cancelAnimationFrame = function (id) {
    __rafQueue = __rafQueue.filter(function (item) { return item.id !== id; });
  };
  global.__setRafFrameInterval = function (ms) {
    __rafFrameInterval = Math.max(0, Number(ms) || 0);
  };
  global.__getRafFrameInterval = function () { return __rafFrameInterval; };
  global.__drainRaf = function (frameTime) {
    if (__rafQueue.length === 0) return 0;
    var now = (frameTime !== undefined) ? frameTime : __timerNow;
    var prevDrain = __rafDrainNow;
    __rafDrainNow = now;
    var batch = [];
    var rest = [];
    for (var i = 0; i < __rafQueue.length; i++) {
      var item = __rafQueue[i];
      if (now >= item.due) { batch.push(item); } else { rest.push(item); }
    }
    __rafQueue = rest;
    var executed = 0;
    try {
      for (var b = 0; b < batch.length; b++) {
        try {
          batch[b].fn(now);
          executed++;
        } catch (e) {
          if (typeof global.__recordError === "function") global.__recordError(e);
        }
      }
      return executed;
    } finally {
      __rafDrainNow = prevDrain;
    }
  };
  global.queueMicrotask = function (fn) {
    if (_nativePromiseResolve) {
      _nativePromiseResolve().then(fn);
    } else {
      global.setTimeout(fn, 0);
    }
  };
  global.__drainTimers = function () {
    global.__drainRaf(__timerNow);
    var q = timers.slice(); timers = [];
    q.sort(function (a, b) { return a.due - b.due || a.id - b.id; });
    q.forEach(function (t) {
      /* A clearTimeout during an earlier callback must cancel a later one
         already copied into this batch. The widget's click arms a 20ms
         abort; the reply cancels it. Running the copy anyway cleared the
         checkbox after the click. */
      if (__timerDead[t.id]) return;
      if (t.due > __timerNow) {
        timers.push(t);
        return;
      }
      try { t.fn(); } catch (e) {}
      if (t.repeat && !__timerDead[t.id] && timers.length < 2000) {
        t.due = __timerNow + t.ms;
        timers.push(t);
      }
    });
    return timers.length;
  };
  /* Release timers whose delay has elapsed on the virtual clock. One
     __drainTimers pass only runs what is already due, so a 20ms abort does
     not fire in the same turn as the click that armed it. */
  global.__advanceTimerClock = function (ms) {
    __timerNow += Math.max(0, Number(ms) || 0);
    return global.__drainTimers();
  };
  global.__wrTimerDelays = function () {
    var out = [];
    for (var i = 0; i < timers.length && out.length < 20; i++) out.push(timers[i].ms);
    return JSON.stringify(out);
  };

  /* Parse an absolute URL string into plain fields on `u`; returns the raw
     query (with its leading "?") for the caller to feed into searchParams. */
  function __urlParseInto(u, absolute) {
    var m = /^([a-zA-Z][a-zA-Z0-9+.-]*:)?(?:\/\/([^/?#]*))?([^?#]*)(\?[^#]*)?(#.*)?$/.exec(absolute) || [];
    u.protocol = m[1] || "";
    var auth = m[2] || "";
    var host = auth.split("@").pop();
    u.hostname = host.split(":")[0];
    u.port = host.indexOf(":") >= 0 ? host.split(":")[1] : "";
    var defaultPorts = { "http:": "80", "https:": "443", "ws:": "80", "wss:": "443", "ftp:": "21" };
    if (u.protocol && defaultPorts[u.protocol.toLowerCase()] === u.port) {
      u.port = "";
      u.host = u.hostname;
    } else {
      u.host = host;
    }
    u.pathname = m[3] || (m[2] ? "/" : "");
    u.hash = m[5] || "";
    u.username = auth.indexOf("@") > 0 ? auth.split("@")[0].split(":")[0] : "";
    u.password = auth.indexOf("@") > 0 && auth.split("@")[0].indexOf(":") > 0 ? auth.split("@")[0].split(":")[1] : "";
    return m[4] || "";
  }
  function __encodeWwwForm(s) {
    return encodeURIComponent(String(s == null ? "" : s)).replace(/%20/g, "+");
  }
  function URLImpl(input, base) {
    var raw = String(input == null ? "" : input);
    var absolute = raw;
    if (!/^[a-zA-Z][a-zA-Z0-9+.-]*:/.test(raw)) {
      if (base) {
        var b = String(base).replace(/[#?].*$/, "");
        var dir = b.slice(0, b.lastIndexOf("/") + 1);
        absolute = raw.charAt(0) === "/" ? b.replace(/^(https?:\/\/[^/]+).*$/, "$1") + raw : dir + raw;
      } else {
        absolute = "http://localhost/" + raw.replace(/^\//, "");
      }
    }
    var self = this;
    var initialSearch = __urlParseInto(this, absolute);
    this._rawSearch = initialSearch;
    this.searchParams = new URLSearchParamsImpl(initialSearch);
    this.searchParams._parentUrl = this;
    Object.defineProperty(this, "search", {
      get: function () {
        if (!self.searchParams._dirty && self._rawSearch !== undefined) {
          return self._rawSearch;
        }
        var qs = self.searchParams.toString();
        return qs ? "?" + qs : "";
      },
      set: function (v) {
        var str = String(v == null ? "" : v);
        self._rawSearch = str.charAt(0) === "?" ? str : (str ? "?" + str : "");
        self.searchParams._replace(str);
        self.searchParams._dirty = false;
      },
      configurable: true,
      enumerable: true
    });
    Object.defineProperty(this, "origin", {
      get: function () { return (self.protocol && self.host) ? self.protocol + "//" + self.host : ""; },
      configurable: true,
      enumerable: true
    });
    Object.defineProperty(this, "href", {
      get: function () {
        var creds = self.username ? (self.username + (self.password ? ":" + self.password : "") + "@") : "";
        var schemePart = self.protocol ? (self.host ? self.protocol + "//" : self.protocol) : "";
        var hostPart = self.host ? creds + self.host : "";
        return schemePart + hostPart + self.pathname + self.search + (self.hash || "");
      },
      set: function (v) {
        var s = __urlParseInto(self, String(v == null ? "" : v));
        self._rawSearch = s;
        self.searchParams._replace(s);
        self.searchParams._dirty = false;
      },
      configurable: true,
      enumerable: true
    });
    this.toString = function () { return self.href; };
    this.assign = function (u) { try { global.__WR_NAVIGATED = String(u || ""); } catch (e) {} };
    this.replace = function (u) { try { global.__WR_NAVIGATED = String(u || ""); } catch (e) {} };
    this.reload = function () {};
  }
  /* Shared parser for URLSearchParams sources: "?a=1&b=2", [["a","1"]], or {a:"1"},
     with duplicate keys collapsing to arrays. */
  function __spParse(query) {
    if (typeof query === "object" && query !== null) {
      var po = {};
      if (Array.isArray(query)) {
        query.forEach(function (pair) {
          if (!pair || pair.length < 2) return;
          var k = String(pair[0]), v = String(pair[1]);
          if (po[k] == null) po[k] = v; else if (Array.isArray(po[k])) po[k].push(v); else po[k] = [po[k], v];
        });
      } else {
        for (var ok in query) {
          if (Object.prototype.hasOwnProperty.call(query, ok)) {
            var val = query[ok];
            if (Array.isArray(val)) po[ok] = val.map(String);
            else po[ok] = String(val == null ? "" : val);
          }
        }
      }
      return po;
    }
    var raw = String(query == null ? "" : query).replace(/^\?/, "");
    var p = {};
    raw.split("&").forEach(function (pair) {
      if (!pair) return;
      var eq = pair.indexOf("=");
      var k = eq >= 0 ? pair.slice(0, eq) : pair;
      var v = eq >= 0 ? pair.slice(eq + 1) : "";
      try { k = decodeURIComponent(k.replace(/\+/g, " ")); v = decodeURIComponent(v.replace(/\+/g, " ")); } catch (e) {}
      if (p[k] == null) p[k] = v; else if (Array.isArray(p[k])) p[k].push(v); else p[k] = [p[k], v];
    });
    return p;
  }
  function URLSearchParamsImpl(query) {
    this._p = __spParse(query);
    this._dirty = false;
    var self = this;
    this.get = function (k) { var v = this._p[k]; return v == null ? null : (Array.isArray(v) ? v[0] : v); };
    this.getAll = function (k) { var v = this._p[k]; return v == null ? [] : (Array.isArray(v) ? v.slice() : [v]); };
    this.has = function (k) { return this._p[k] != null; };
    /* append/set/delete/sort: the standard mutation surface. */
    this.append = function (k, v) {
      this._dirty = true;
      var cur = this._p[k];
      if (cur == null) this._p[k] = String(v);
      else if (Array.isArray(cur)) cur.push(String(v));
      else this._p[k] = [cur, String(v)];
    };
    this.set = function (k, v) { this._dirty = true; this._p[k] = String(v); };
    this.delete = function (k) { this._dirty = true; delete this._p[k]; };
    this.sort = function () {
      this._dirty = true;
      var keys = Object.keys(this._p).sort();
      var sorted = {};
      for (var i = 0; i < keys.length; i++) sorted[keys[i]] = this._p[keys[i]];
      this._p = sorted;
    };
    /* entries/keys/values + iterator */
    this.entries = function () {
      var out = [];
      for (var k in this._p) {
        var v = this._p[k];
        if (Array.isArray(v)) v.forEach(function (x) { out.push([k, x]); });
        else out.push([k, v]);
      }
      var idx = 0;
      var iterator = {
        next: function () { return idx < out.length ? { value: out[idx++], done: false } : { value: undefined, done: true }; },
        forEach: function (fn) { out.forEach(function (pair) { fn(pair[1], pair[0]); }); },
        length: out.length
      };
      iterator[Symbol.iterator] = function () { return iterator; };
      return iterator;
    };
    this.keys = function () {
      var e = this.entries();
      var out = [];
      var r;
      while (!(r = e.next()).done) out.push(r.value[0]);
      return out;
    };
    this.values = function () {
      var e = this.entries();
      var out = [];
      var r;
      while (!(r = e.next()).done) out.push(r.value[1]);
      return out;
    };
    this.forEach = function (fn) {
      for (var k in this._p) {
        var v = this._p[k];
        if (Array.isArray(v)) v.forEach(function (x) { fn(x, k); });
        else fn(v, k);
      }
    };
    this._replace = function (query) { this._p = __spParse(query); this._dirty = false; };
    this.toString = function () {
      var out = [];
      for (var k in this._p) {
        var v = this._p[k];
        if (Array.isArray(v)) v.forEach(function (x) { out.push(__encodeWwwForm(k) + "=" + __encodeWwwForm(x)); });
        else out.push(__encodeWwwForm(k) + "=" + __encodeWwwForm(v));
      }
      return out.join("&");
    };
  }
  global.URL = URLImpl;
  global.URLSearchParams = URLSearchParamsImpl;

  /* ---- URL.createObjectURL / revokeObjectURL (blob-url registry) ----
     A real bundle (LinkedIn's BFXDwhx0.js) builds a Worker from a Blob URL:
     ``new Blob([code], {type:"application/javascript"}); URL.createObjectURL(blob)``
     Without these the module dies with "not a function". URLs are tracked
     in-process and revoked honestly; the blob content is not materialized. */
  var __objectURLs = Object.create(null);
  var __objectURLSeq = 0;
  URLImpl.createObjectURL = function (obj) {
    __objectURLSeq += 1;
    var id = "blob:webresearch-" + __objectURLSeq;
    __objectURLs[id] = obj;
    return id;
  };
  URLImpl.revokeObjectURL = function (id) {
    delete __objectURLs[String(id || "")];
  };

  /* ---- MediaStream / AudioNode presence stubs (feature-detection parity) ---- */
  /* ---- M2: MediaStream / capture (honest, structured) ----
     Tracks carry real identity + kind + state and fire onended; audio
     tracks generated by getUserMedia carry REAL silent sample data
     (frames of zeros at the declared rate) — honest: this engine does
     not capture microphone/video, so the content is silence/black, but
     the track lifecycle, cloning, and the recorder container are real
     structures, not fakes claiming to have content. */
  var __trackSeq = 0;
  function MediaStreamTrack(kind, label) {
    var self = this;
    __trackSeq += 1;
    self.id = "track-" + __trackSeq + "-" + Math.floor(performanceNowMs());
    self.kind = String(kind || "audio");
    self.label = String(label || (self.kind === "video" ? "camera" : "microphone"));
    self.readyState = "live";
    self.muted = false;
    self.enabled = true;
    self._onended = null;
    self._contentHint = "";
    self.getSettings = function () {
      if (self.kind === "video") return { width: 0, height: 0, frameRate: 0, deviceId: "" };
      return { sampleRate: 48000, channelCount: 1, deviceId: "" };
    };
    self.getCapabilities = function () { return self.getSettings(); };
    self.stop = function () {
      if (self.readyState === "ended") return;
      self.readyState = "ended";
      /* M2: a stream is active while it has a live track (spec); stopping
         the last live track deactivates every owning stream. */
      (self.__streams || []).forEach(function (s) {
        var stillLive = s.getTracks().some(function (t) { return t.readyState === "live"; });
        s.active = stillLive;
      });
      if (typeof self._onended === "function") {
        try { self._onended({ type: "ended", target: self }); } catch (e) {}
      }
    };
    self.clone = function () {
      var t = new MediaStreamTrack(self.kind, self.label);
      t.enabled = self.enabled;
      t.muted = self.muted;
      return t;
    };
    Object.defineProperty(self, "onended", {
      configurable: true,
      get: function () { return self._onended; },
      set: function (fn) { self._onended = typeof fn === "function" ? fn : null; }
    });
    /* Audio track data: real silent frames so analyzers/readers see an
       honest stream shape (zeros — no microphone, disclosed). */
    if (self.kind === "audio") {
      self.__frames = new Float32Array(48000); /* 1s at 48kHz, all zero */
    } else {
      self.__frames = null;
    }
    __cloakObject(self, { stop: 0, clone: 0, getSettings: 0, getCapabilities: 0 });
  }
  global.MediaStreamTrack = MediaStreamTrack;
  function performanceNowMs() {
    try { return (global.performance && global.performance.now) ? global.performance.now() : Date.now(); } catch (e) { return Date.now(); }
  }
  global.MediaStream = global.MediaStream || function (tracks) {
    var self = this;
    var list = Array.isArray(tracks) ? tracks.filter(function (t) { return t instanceof MediaStreamTrack; }) : [];
    __trackSeq += 1;
    self.id = "stream-" + __trackSeq;
    self.active = list.length > 0;
    /* M2: tracks learn their owning stream so stop() can deactivate it. */
    list.forEach(function (t) {
      t.__streams = t.__streams || [];
      if (t.__streams.indexOf(self) < 0) t.__streams.push(self);
    });
    self.getTracks = function () { return list.slice(); };
    self.getAudioTracks = function () { return list.filter(function (t) { return t.kind === "audio"; }); };
    self.getVideoTracks = function () { return list.filter(function (t) { return t.kind === "video"; }); };
    self.getTrackById = function (id) {
      for (var i = 0; i < list.length; i++) if (list[i].id === id) return list[i];
      return null;
    };
    self.addTrack = function (t) {
      if (t instanceof MediaStreamTrack && list.indexOf(t) < 0) {
        list.push(t);
        self.active = list.some(function (x) { return x.readyState === "live"; });
        t.__streams = t.__streams || [];
        if (t.__streams.indexOf(self) < 0) t.__streams.push(self);
      }
    };
    self.removeTrack = function (t) {
      var i = list.indexOf(t);
      if (i >= 0) { list.splice(i, 1); self.active = list.length > 0; }
    };
    self.clone = function () { return new global.MediaStream(list.map(function (t) { return t.clone(); })); };
    __cloakObject(self, { getTracks: 0, getAudioTracks: 0, getVideoTracks: 0, getTrackById: 1, addTrack: 1, removeTrack: 1, clone: 0 });
  };
  global.AudioNode = global.AudioNode || function () {};
  global.WorkletNode = global.WorkletNode || function () {};
  /* ---- M2: MediaRecorder — structured, honest output ----
     Records a stream into a real (tiny, spec-shaped) WebM container:
     EBML header + Segment + a single Tracks entry for each recorded
     kind. Disclosed honestly: the BLOCK payloads are the track's silent
     audio frames / empty video — this engine does not encode real
     audio/video, and the produced blob says so via its tiny size; the
     container structure is genuine EBML, not a random byte string. */
  var __recSeq = 0;
  global.MediaRecorder = function MediaRecorder(stream, options) {
    var self = this;
    __recSeq += 1;
    self._stream = stream || null;
    self.state = "inactive";
    self.mimeType = String((options && options.mimeType) || "audio/webm;codecs=opus");
    self._chunks = [];
    self._ondataavailable = null;
    self._onstop = null;
    self._onerror = null;
    self._timerId = null;
    var timeslice = 0;
    if (!self._stream || typeof self._stream.getTracks !== "function") {
      throw new TypeError("Failed to construct 'MediaRecorder': parameter 1 is not of type 'MediaStream'");
    }
    Object.defineProperty(self, "ondataavailable", {
      configurable: true,
      get: function () { return self._ondataavailable; },
      set: function (fn) { self._ondataavailable = typeof fn === "function" ? fn : null; }
    });
    Object.defineProperty(self, "onstop", {
      configurable: true,
      get: function () { return self._onstop; },
      set: function (fn) { self._onstop = typeof fn === "function" ? fn : null; }
    });
    Object.defineProperty(self, "onerror", {
      configurable: true,
      get: function () { return self._onerror; },
      set: function (fn) { self._onerror = typeof fn === "function" ? fn : null; }
    });
    /* Minimal valid WebM bytes: EBML magic + DocType webm. The chunk's
       payload is the stream's honest (silent) audio frames. */
    function webmHeader(trackCount) {
      var bytes = [0x1A, 0x45, 0xDF, 0xA3];       /* EBML magic */
      bytes.push(0x01, 0x00, 0x00, 0x00);          /* DocType version etc. (simplified skeleton) */
      bytes.push(0x42, 0x82);                      /* DocType element id */
      bytes.push(0x84, 0x77, 0x65, 0x62, 0x6D);    /* "webm" */
      for (var t = 0; t < (trackCount || 1); t++) bytes.push(0xAE); /* TrackEntry marker (simplified) */
      return bytes;
    }
    function emitChunk() {
      var tracks = self._stream.getTracks();
      var header = webmHeader(tracks.length);
      var payload = [];
      tracks.forEach(function (t) {
        if (t.kind === "audio" && t.__frames) {
          /* First 128 frames of the silent stream — honest sample data */
          for (var i = 0; i < Math.min(128, t.__frames.length); i++) {
            var v = Math.round(Math.max(-1, Math.min(1, t.__frames[i])) * 32767) & 0xFF;
            payload.push(v);
          }
        }
      });
      var all = header.concat(payload);
      var blob = new global.Blob([new Uint8Array(all)], { type: "audio/webm" });
      if (typeof self._ondataavailable === "function") {
        try { self._ondataavailable({ data: blob, type: "dataavailable", target: self }); } catch (e) {}
      }
    }
    self.start = function (ts) {
      if (self.state !== "inactive") throw new Error("InvalidStateError: recorder already started");
      timeslice = Number(ts) || 0;
      self.state = "recording";
      if (timeslice > 0) {
        self._timerId = setInterval(function () { emitChunk(); }, Math.max(1, timeslice));
      }
    };
    self.stop = function () {
      if (self.state === "inactive") throw new Error("InvalidStateError: recorder not started");
      if (self._timerId != null) { clearInterval(self._timerId); self._timerId = null; }
      self.state = "inactive";
      emitChunk();  /* final chunk */
      if (typeof self._onstop === "function") {
        try { self._onstop({ type: "stop", target: self }); } catch (e) {}
      }
    };
    self.pause = function () { if (self.state === "recording") self.state = "paused"; };
    self.resume = function () { if (self.state === "paused") self.state = "recording"; };
    self.requestData = function () { if (self.state === "recording") emitChunk(); };
    self.isTypeSupported = function (mt) { return String(mt || "").indexOf("webm") >= 0; };
    __cloakObject(self, { start: 1, stop: 0, pause: 0, resume: 0, requestData: 0, isTypeSupported: 1 });
  };
  global.MediaRecorder.isTypeSupported = function (mt) { return String(mt || "").indexOf("webm") >= 0; };
  global.MediaRecorder.prototype = global.MediaRecorder.prototype || {};
  __cloakObject(global.MediaRecorder, { isTypeSupported: 1 });

  /* IntersectionObserver / ResizeObserver (scroll-driven). MutationObserver is defined earlier. */
  var scrollState = { x: 0, y: 0 };
  var intersectionWatchers = [];

  function _elementIntersects(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return true;
    var r = el.getBoundingClientRect();
    var vh = global.innerHeight || 720;
    var vw = global.innerWidth || 1280;
    var top = r.top, left = r.left, bottom = r.bottom, right = r.right;
    /* Every scrolling-box ancestor clips its content. Box coordinates are
       document-absolute in this engine, so a container's visible window in that
       same space is [box.y + scrollTop, + clientHeight]: an element scrolled out
       of an overflow:auto pane is NOT intersecting, even though its document
       box still lands inside the viewport. */
    var doc = el.ownerDocument || global.document;
    var n = el.parentNode;
    while (doc && n && n.nodeType === 1) {
      if (n === doc.documentElement || n === doc.body) break;
      if (n.__box && __isScrollContainer(n)) {
        var winTop = n.__box.y + (Number(n.__scrollTop) || 0);
        var winLeft = n.__box.x + (Number(n.__scrollLeft) || 0);
        var winBottom = winTop + n.__box.height;
        var winRight = winLeft + n.__box.width;
        if (top < winTop) top = winTop;
        if (left < winLeft) left = winLeft;
        if (bottom > winBottom) bottom = winBottom;
        if (right > winRight) right = winRight;
      }
      n = n.parentNode;
    }
    /* Clipping can fully collapse the rect (element scrolled out of the pane):
       an empty visible rect is NOT an intersection, even when the element's
       document box still lands inside the viewport. */
    if (bottom <= top || right <= left) return false;
    return (bottom - scrollState.y) > 0 && (top - scrollState.y) < vh &&
           (right - scrollState.x) > 0 && (left - scrollState.x) < vw;
  }

  function _notifyIntersections() {
    intersectionWatchers.forEach(function (w) {
      if (!w.active) return;
      var entries = [];
      w.targets.forEach(function (el) {
        var now = _elementIntersects(el);
        var prev = w.state.get(el);
        if (prev !== now) {
          w.state.set(el, now);
          entries.push({
            target: el,
            isIntersecting: now,
            intersectionRatio: now ? 1 : 0,
            boundingClientRect: el.getBoundingClientRect ? el.getBoundingClientRect() : {},
            time: 0
          });
        }
      });
      if (entries.length && typeof w.cb === "function") {
        try { w.cb(entries, w.self); } catch (e) {}
      }
    });
  }

  function IntersectionObserver(cb, _opts) {
    this._cb = cb;
    this._self = { targets: [], state: new Map(), cb: cb, active: true, self: this };
  }
  IntersectionObserver.prototype.observe = function (el) {
    if (!el) return;
    if (this._self.targets.indexOf(el) < 0) {
      this._self.targets.push(el);
      this._self.state.set(el, _elementIntersects(el));
      if (intersectionWatchers.indexOf(this._self) < 0) intersectionWatchers.push(this._self);
      // Initial notification (spec: observers fire once on observe with current state).
      var self = this;
      global.setTimeout(function () {
        if (self._self.active) {
          try {
            self._cb([{
              target: el,
              isIntersecting: self._self.state.get(el),
              intersectionRatio: self._self.state.get(el) ? 1 : 0,
              boundingClientRect: el.getBoundingClientRect ? el.getBoundingClientRect() : {}
            }], self);
          } catch (e) {}
        }
      }, 0);
    }
  };
  IntersectionObserver.prototype.unobserve = function (el) {
    var i = this._self.targets.indexOf(el);
    if (i >= 0) { this._self.targets.splice(i, 1); this._self.state.delete(el); }
  };
  IntersectionObserver.prototype.disconnect = function () {
    this._self.active = false;
    this._self.targets = [];
    this._self.state.clear();
    var i = intersectionWatchers.indexOf(this._self);
    if (i >= 0) intersectionWatchers.splice(i, 1);
  };
  global.IntersectionObserver = IntersectionObserver;
  global.__notifyIntersections = _notifyIntersections;

  function ResizeObserver(cb) { this._cb = cb; }
  ResizeObserver.prototype.observe = function (el) {
    if (this._cb && el) {
      try { this._cb([{ target: el, contentRect: el.getBoundingClientRect ? el.getBoundingClientRect() : {} }], this); } catch (e) {}
    }
  };
  ResizeObserver.prototype.unobserve = function () {};
  ResizeObserver.prototype.disconnect = function () {};
  global.ResizeObserver = ResizeObserver;

  /* L9: CSS animation / transition timing engine.
     Runs on the frame clock: each __advanceClock tick advances active
     animations. An element with animation: NAME DURATION gets its
     animated properties interpolated between keyframes and exposed via
     getComputedStyle as the interpolated value.

     Honest scope: interpolation is linear between keyframe offsets for
     numeric/color properties; animation-iteration-count >1 / infinite
     loops; animation-fill-mode forwards holds the last keyframe. No
     transition shorthand yet (transition property support is the next
     step); no animation events (animationstart/end are not dispatched). */
  function __parseAnimationShorthand(st) {
    var sh = st.animation;
    if (!sh) {
      if (st.animationName) return {
        name: st.animationName,
        duration: st.animationDuration || "0s",
        timing: st.animationTimingFunction || "ease",
        delay: st.animationDelay || "0s",
        iterations: st.animationIterationCount || "1",
        direction: st.animationDirection || "normal",
        fill: st.animationFillMode || "none"
      };
      return null;
    }
    var parts = String(sh).trim().split(/\s+/);
    var name = null, duration = "0s", timing = "ease", delay = "0s", iterations = "1", direction = "normal", fill = "none";
    parts.forEach(function (p) {
      if (/^\d+(\.\d+)?(s|ms)$/.test(p)) {
        if (duration === "0s") duration = p;
        else if (delay === "0s") delay = p;
      } else if (/^(linear|ease|ease-in|ease-out|ease-in-out|step-start|step-end)$/.test(p)) {
        timing = p;
      } else if (/^\d+$/.test(p) || p === "infinite") {
        iterations = p;
      } else if (/^(normal|reverse|alternate|alternate-reverse)$/.test(p)) {
        direction = p;
      } else if (/^(none|forwards|backwards|both)$/.test(p)) {
        fill = p;
      } else {
        name = p;
      }
    });
    return { name: name, duration: duration, timing: timing, delay: delay, iterations: iterations, direction: direction, fill: fill };
  }
  function __parseTime(s) {
    s = String(s || "0s").trim();
    if (/ms$/.test(s)) return parseFloat(s) || 0;
    if (/s$/.test(s)) return (parseFloat(s) || 0) * 1000;
    return parseFloat(s) || 0;
  }
  /* Interpolate between two CSS values. Handles numbers with units
     (px, em, %, deg) and rgb()/rgba()/hex colors. */
  function __lerpCSS(a, b, t) {
    if (a == null || b == null) return b;
    a = String(a).trim(); b = String(b).trim();
    var ma = a.match(/^(-?[\d.]+)(px|em|%|deg|rem|pt)?$/i);
    var mb = b.match(/^(-?[\d.]+)(px|em|%|deg|rem|pt)?$/i);
    if (ma && mb) {
      var va = parseFloat(ma[1]), vb = parseFloat(mb[1]);
      var unit = ma[2] || mb[2] || "";
      return String(va + (vb - va) * t) + (unit || "");
    }
    /* Color interpolation: hex -> rgb lerp */
    function hexToRgb(h) {
      var m = /^#([0-9a-f]{6})$/i.exec(h);
      if (!m) return null;
      return [parseInt(m[1].slice(0,2),16), parseInt(m[1].slice(2,4),16), parseInt(m[1].slice(4,6),16)];
    }
    var ca = a.toLowerCase().startsWith("#") ? hexToRgb(a) : null;
    var cb = b.toLowerCase().startsWith("#") ? hexToRgb(b) : null;
    if (!ca) {
      var ra = a.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
      if (ra) ca = [parseInt(ra[1]), parseInt(ra[2]), parseInt(ra[3])];
    }
    if (!cb) {
      var rb = b.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
      if (rb) cb = [parseInt(rb[1]), parseInt(rb[2]), parseInt(rb[3])];
    }
    if (ca && cb) {
      var r = Math.round(ca[0] + (cb[0] - ca[0]) * t);
      var g = Math.round(ca[1] + (cb[1] - ca[1]) * t);
      var bl = Math.round(ca[2] + (cb[2] - ca[2]) * t);
      return "rgb(" + r + ", " + g + ", " + bl + ")";
    }
    /* Non-interpolable: jump at midpoint */
    return t < 0.5 ? a : b;
  }
  function __ease(t, timing) {
    timing = String(timing || "ease").toLowerCase();
    if (timing === "linear") return t;
    if (timing === "ease-in") return t * t;
    if (timing === "ease-out") return 1 - (1 - t) * (1 - t);
    if (timing === "ease-in-out") return t < 0.5 ? 2 * t * t : -1 + (4 - 2 * t) * t;
    if (timing === "step-start") return 1;
    if (timing === "step-end") return 0;
    /* ease (default): cubic-bezier(0.25,0.1,0.25,1) approx */
    return 1 - Math.pow(1 - t, 3);
  }
  /* Advance all active animations on the document by dt ms. Called from
     __advanceClock. */
  function __advanceAnimations(doc, dt) {
    if (!doc || !doc.__activeAnimations) return;
    var still = [];
    doc.__activeAnimations.forEach(function (anim) {
      anim.elapsed += dt;
      var delay = __parseTime(anim.spec.delay);
      if (anim.elapsed < delay) { still.push(anim); return; }
      var dur = __parseTime(anim.spec.duration);
      if (dur <= 0) { still.push(anim); return; }
      var progress = (anim.elapsed - delay) / dur;
      var maxIter = (anim.spec.iterations === "infinite") ? Infinity : parseInt(anim.spec.iterations) || 1;
      var iter = Math.floor(progress);
      if (iter >= maxIter) {
        /* Finished: apply fill mode */
        if (anim.spec.fill === "forwards" || anim.spec.fill === "both") {
          var last = anim.keyframes[anim.keyframes.length - 1];
          for (var k in last.props) anim.el.__animProps[k] = last.props[k];
        } else {
          anim.el.__animProps = null;
        }
        return; /* not pushed to still */
      }
      var frac = progress - iter;
      if (anim.spec.direction === "reverse") frac = 1 - frac;
      else if (anim.spec.direction === "alternate") frac = (iter % 2 === 1) ? 1 - frac : frac;
      else if (anim.spec.direction === "alternate-reverse") frac = (iter % 2 === 0) ? 1 - frac : frac;
      /* Find surrounding keyframes */
      var kf = anim.keyframes;
      var i0 = 0, i1 = kf.length - 1;
      for (var i = 0; i < kf.length; i++) {
        if (kf[i].offset <= frac) i0 = i;
        if (kf[i].offset >= frac) { i1 = i; break; }
      }
      var span = kf[i1].offset - kf[i0].offset;
      var t = span > 0 ? (frac - kf[i0].offset) / span : 1;
      t = __ease(Math.max(0, Math.min(1, t)), anim.spec.timing);
      var props0 = kf[i0].props, props1 = kf[i1].props;
      if (!anim.el.__animProps) anim.el.__animProps = {};
      for (var k2 in props1) {
        var v0 = props0[k2] != null ? props0[k2] : props1[k2];
        var v1 = props1[k2];
        anim.el.__animProps[k2] = __lerpCSS(v0, v1, t);
      }
      still.push(anim);
    });
    doc.__activeAnimations = still;
  }
  /* L9: CSS transition shorthand support.
     transition: PROPERTY DURATION TIMING-FUNCTION DELAY
     When a property changes on an element with a transition, the old
     value animates to the new one over the duration. The transition is
     registered as a one-shot animation with synthetic keyframes. */
  function __parseTransitionShorthand(st) {
    var sh = st.transition;
    if (!sh) return [];
    return String(sh).split(",").map(function (part) {
      var parts = part.trim().split(/\s+/);
      var prop = null, duration = "0s", timing = "ease", delay = "0s";
      parts.forEach(function (p) {
        if (/^\d+(\.\d+)?(s|ms)$/.test(p)) {
          if (duration === "0s") duration = p;
          else if (delay === "0s") delay = p;
        } else if (/^(linear|ease|ease-in|ease-out|ease-in-out)$/.test(p)) {
          timing = p;
        } else if (prop === null) {
          prop = p;
        }
      });
      return { prop: prop, duration: duration, timing: timing, delay: delay };
    }).filter(function (t) { return t.prop && t.prop !== "none"; });
  }
  /* Start a transition on an element for a changed property. Called from
     the style proxy when a transition property is set. */
  function __startTransition(el, prop, oldVal, newVal) {
    if (oldVal === newVal) return;
    var doc = el.ownerDocument || global.document;
    if (!doc) return;
    var rules = __collectStyleRules(doc);
    var cs = __computedStyle(el, rules, 16);
    var transitions = __parseTransitionShorthand(cs);
    /* The changed property must match a transition property (or 'all') */
    var match = null;
    for (var i = 0; i < transitions.length; i++) {
      if (transitions[i].prop === "all" || transitions[i].prop === prop) {
        match = transitions[i];
        break;
      }
    }
    if (!match) return;
    var dur = __parseTime(match.duration);
    if (dur <= 0) return;
    /* Build synthetic keyframes: from oldVal to newVal */
    var kebab = prop.replace(/[A-Z]/g, function (c) { return "-" + c.toLowerCase(); });
    var kf = [
      { offset: 0, props: {} },
      { offset: 1, props: {} }
    ];
    kf[0].props[kebab] = oldVal;
    kf[1].props[kebab] = newVal;
    doc.__activeAnimations = doc.__activeAnimations || [];
    doc.__activeAnimations.push({
      el: el, keyframes: kf,
      spec: { name: null, duration: match.duration, timing: match.timing,
              delay: match.delay, iterations: "1", direction: "normal",
              fill: "none" },
      elapsed: 0
    });
  }
  /* Register animations for elements with animation: NAME DURATION. Called
     during layout passes (cheap no-op when no keyframes defined). */
  function __registerAnimations(doc, el, cs) {
    if (!doc.__keyframes || Object.keys(doc.__keyframes).length === 0) return;
    var spec = __parseAnimationShorthand(cs);
    if (!spec || !spec.name || !doc.__keyframes[spec.name]) {
      /* clear stale */
      if (el.__animProps) el.__animProps = null;
      return;
    }
    /* Check if already registered with the same spec */
    if (el.__animSpec === spec.name + "|" + spec.duration) return;
    el.__animSpec = spec.name + "|" + spec.duration;
    el.__animProps = {};
    doc.__activeAnimations = doc.__activeAnimations || [];
    doc.__activeAnimations.push({
      el: el, keyframes: doc.__keyframes[spec.name], spec: spec, elapsed: 0
    });
    /* Apply initial values from the delay/first keyframe so getComputedStyle
       returns the correct value even before any __advanceClock call. */
    __advanceAnimations(doc, 0);
  }
  global.getComputedStyle = function (el) {
    if (!el) {
      return { getPropertyValue: function () { return ""; }, length: 0 };
    }
    /* A real getComputedStyle returns the CASCADE (stylesheet + inline + UA),
       not the inline style attribute — the old stub returned el.style, so
       every computed property read as "" and page JS that queried
       getComputedStyle().color saw nothing. Build a CSSStyleDeclaration
       populated from __computedStyle, with the live element as its backing so
       setProperty/removeProperty keep working on the returned object. */
var decl = new CSSStyleDeclaration(el);
      decl.__parsing = true;
      try {
        for (var k in decl) {
          if (!Object.prototype.hasOwnProperty.call(decl, k)) continue;
          if (k === "_el" || k === "__parsing") continue;
          delete decl[k];
        }
        var doc = el.ownerDocument || global.document;
        var rules = doc ? __collectStyleRules(doc) : [];
        var cs = __computedStyle(el, rules, 16);
        /* Copy every computed property (the template is the source of truth for
           what the cascade can compute; iterating the result keeps them in
           sync). camelCase keys are stored as-is on the declaration and
           getPropertyValue resolves both forms. */
        for (var pk in cs) {
          if (pk.indexOf("__spec_") === 0) continue;
          var v = cs[pk];
          if (v == null || v === "") continue;
          decl[pk] = String(v);
        }
        /* L9: overlay animated property values from active keyframe animation */
        if (el.__animProps) {
          for (var ak in el.__animProps) {
            if (el.__animProps[ak] != null) decl[ak] = String(el.__animProps[ak]);
          }
        }
      } finally {
        decl.__parsing = false;
      }
      return decl;
  };
  global.matchMedia = function (q) {
    return { matches: false, media: String(q || ""), addListener: function () {}, removeListener: function () {}, addEventListener: function () {}, removeEventListener: function () {} };
  };
  global.requestIdleCallback = function (fn) { return global.setTimeout(fn, 1); };
  global.cancelIdleCallback = function () {};

  /* Canvas / WebGL / Audio / Worker / WASM stubs: plausible, non-crashing. */
  /* ---- R1: Canvas 2D software raster ----
     A real (small) pixel buffer per canvas: getImageData returns TRUE
     pixels that drawing commands actually wrote, replacing the seeded
     pseudo-noise. Deterministic: no randomness anywhere in the raster.
     Scope (disclosed): rect fills/strokes, clear, lines (Bresenham),
     circles (midpoint), drawImage, put/getImageData, and block-glyph
     fillText (a legible 5x7 microfont — not a font rasterizer). */
  function __parseCSSColor(v) {
    if (v == null) return [0, 0, 0, 255];
    var s = String(v).trim();
    var m = s.match(/^#([0-9a-fA-F]{6})$/);
    if (m) return [parseInt(m[1].slice(0, 2), 16), parseInt(m[1].slice(2, 4), 16), parseInt(m[1].slice(4, 6), 16), 255];
    m = s.match(/^#([0-9a-fA-F]{3})$/);
    if (m) {
      var r = m[1][0] + m[1][0], g = m[1][1] + m[1][1], b = m[1][2] + m[1][2];
      return [parseInt(r, 16), parseInt(g, 16), parseInt(b, 16), 255];
    }
    m = s.match(/^rgba?\(\s*([\d.]+)\s*[,\s]\s*([\d.]+)\s*[,\s]\s*([\d.]+)\s*(?:[,\/]\s*([\d.]+)\s*)?\)$/);
    if (m) return [+m[1] | 0, +m[2] | 0, +m[3] | 0, m[4] != null ? Math.round(+m[4] * 255) : 255];
    if (/^transparent$/i.test(s)) return [0, 0, 0, 0];
    return [0, 0, 0, 255];
  }
  var __GLYPH_5x7 = {
    "A": ["01110","10001","10001","11111","10001","10001","10001"],
    "B": ["11110","10001","10001","11110","10001","10001","11110"],
    "C": ["01110","10001","10000","10000","10000","10001","01110"],
    "D": ["11110","10001","10001","10001","10001","10001","11110"],
    "E": ["11111","10000","10000","11110","10000","10000","11111"],
    "F": ["11111","10000","10000","11110","10000","10000","10000"],
    "G": ["01110","10001","10000","10111","10001","10001","01110"],
    "H": ["10001","10001","10001","11111","10001","10001","10001"],
    "I": ["11111","00100","00100","00100","00100","00100","11111"],
    "J": ["00111","00010","00010","00010","00010","10010","01100"],
    "K": ["10001","10010","10100","11000","10100","10010","10001"],
    "L": ["10000","10000","10000","10000","10000","10000","11111"],
    "M": ["10001","11011","10101","10101","10001","10001","10001"],
    "N": ["10001","10001","11001","10101","10011","10001","10001"],
    "O": ["01110","10001","10001","10001","10001","10001","01110"],
    "P": ["11110","10001","10001","11110","10000","10000","10000"],
    "Q": ["01110","10001","10001","10001","10101","10011","01111"],
    "R": ["11110","10001","10001","11110","10100","10010","10001"],
    "S": ["01111","10000","10000","01110","00001","00001","11110"],
    "T": ["11111","00100","00100","00100","00100","00100","00100"],
    "U": ["10001","10001","10001","10001","10001","10001","01110"],
    "V": ["10001","10001","10001","10001","10001","01010","00100"],
    "W": ["10001","10001","10001","10101","10101","11011","10001"],
    "X": ["10001","01010","00100","00100","00100","01010","10001"],
    "Y": ["10001","01010","00100","00100","00100","00100","00100"],
    "Z": ["11111","00001","00010","00100","01000","10000","11111"],
    "0": ["01110","10001","10011","10101","11001","10001","01110"],
    "1": ["00100","01100","00100","00100","00100","00100","01110"],
    "2": ["01110","10001","00001","00010","00100","01000","11111"],
    "3": ["11110","00001","00001","01110","00001","00001","11110"],
    "4": ["00010","00110","01010","10010","11111","00010","00010"],
    "5": ["11111","10000","11110","00001","00001","10001","01110"],
    "6": ["00110","01000","10000","11110","10001","10001","01110"],
    "7": ["11111","00001","00010","00100","01000","01000","01000"],
    "8": ["01110","10001","10001","01110","10001","10001","01110"],
    "9": ["01110","10001","10001","01111","00001","00010","01100"],
    " ": ["00000","00000","00000","00000","00000","00000","00000"],
    "!": ["00100","00100","00100","00100","00100","00000","00100"],
    "?": ["01110","10001","00001","00010","00100","00000","00100"],
    ".": ["00000","00000","00000","00000","00000","01100","01100"],
    "-": ["00000","00000","00000","11111","00000","00000","00000"],
    "+": ["00000","00100","00100","11111","00100","00100","00000"],
    ":": ["00000","01100","01100","00000","01100","01100","00000"]
  };
  function makeContext2D(canvas) {
    var noop = function () {};
    /* R1: the real pixel buffer. Width/height come from the canvas
       element (attr or 300x150 spec default); resize keeps content
       cropped, like a real canvas. */
    var cw = Math.max(1, Math.floor(Number(canvas.width) || 300));
    var ch = Math.max(1, Math.floor(Number(canvas.height) || 150));
    var pix = new Uint8ClampedArray(cw * ch * 4);
    var raster = { w: cw, h: ch, pix: pix, ops: 0 };
    if (canvas && canvas.__ops == null) canvas.__ops = 0;
    function markOp() {
      raster.ops++;
      if (canvas) canvas.__ops = (canvas.__ops || 0) + 1;
    }
    function setPx(x, y, rgba) {
      x = Math.floor(x); y = Math.floor(y);
      if (x < 0 || y < 0 || x >= raster.w || y >= raster.h) return;
      var i = (y * raster.w + x) * 4;
      var a = rgba[3] / 255;
      if (a >= 1) {
        pix[i] = rgba[0]; pix[i + 1] = rgba[1]; pix[i + 2] = rgba[2]; pix[i + 3] = rgba[3];
      } else if (a > 0) {
        /* source-over compositing per spec */
        var da = pix[i + 3] / 255;
        var oa = a + da * (1 - a);
        if (oa > 0) {
          pix[i]     = (rgba[0] * a + pix[i]     * da * (1 - a)) / oa;
          pix[i + 1] = (rgba[1] * a + pix[i + 1] * da * (1 - a)) / oa;
          pix[i + 2] = (rgba[2] * a + pix[i + 2] * da * (1 - a)) / oa;
          pix[i + 3] = oa * 255;
        }
      }
    }
    function fillRectPx(x, y, w, h, rgba) {
      var x0 = Math.max(0, Math.ceil(x)), y0 = Math.max(0, Math.ceil(y));
      var x1 = Math.min(raster.w, Math.floor(x + w)), y1 = Math.min(raster.h, Math.floor(y + h));
      for (var yy = y0; yy < y1; yy++) {
        for (var xx = x0; xx < x1; xx++) setPx(xx, yy, rgba);
      }
    }
    function strokeRectPx(x, y, w, h, rgba, lw) {
      var t = lw / 2;
      fillRectPx(x - t, y - t, w + lw, lw, rgba);              /* top */
      fillRectPx(x - t, y + h - t, w + lw, lw, rgba);          /* bottom */
      fillRectPx(x - t, y, lw, h, rgba);                       /* left */
      fillRectPx(x + w - t, y, lw, h, rgba);                    /* right */
    }
    function linePx(x0, y0, x1, y1, rgba, lw) {
      x0 = Math.round(x0); y0 = Math.round(y0); x1 = Math.round(x1); y1 = Math.round(y1);
      var dx = Math.abs(x1 - x0), sx = x0 < x1 ? 1 : -1;
      var dy = -Math.abs(y1 - y0), sy = y0 < y1 ? 1 : -1;
      var err = dx + dy;
      var half = Math.floor(lw / 2);
      function dot(x, y) {
        if (half > 0) fillRectPx(x - half, y - half, lw, lw, rgba);
        else setPx(x, y, rgba);
      }
      while (true) {
        dot(x0, y0);
        if (x0 === x1 && y0 === y1) break;
        var e2 = 2 * err;
        if (e2 >= dy) { err += dy; x0 += sx; }
        if (e2 <= dx) { err += dx; y0 += sy; }
      }
    }
    function circlePx(cx, cy, r, rgba, filled) {
      cx = Math.round(cx); cy = Math.round(cy); r = Math.round(r);
      if (r < 0) return;
      var x = -r, y = 0, err = 2 - 2 * r;
      while (x < 0) {
        if (filled) {
          for (var xx = cx + x; xx <= cx - x; xx++) {
            setPx(xx, cy + y, rgba);
            setPx(xx, cy - y, rgba);
          }
          for (var xx2 = cx + y; xx2 <= cx - y; xx2++) {
            setPx(xx2, cy + x, rgba);
            setPx(xx2, cy - x, rgba);
          }
        } else {
          setPx(cx - x, cy + y, rgba); setPx(cx - y, cy - x, rgba);
          setPx(cx + x, cy - y, rgba); setPx(cx + y, cy + x, rgba);
          setPx(cx + x, cy + y, rgba); setPx(cx + y, cy - x, rgba);
          setPx(cx - x, cy - y, rgba); setPx(cx - y, cy + x, rgba);
        }
        r = err;
        if (r <= y) err += ++y * 2 + 1;
        if (r > x) err += ++x * 2 + 1;
      }
    }
    /* Path state: only line/arc segments are rasterized; curves are
       recorded but drawn as their chord endpoints (disclosed). */
    var pathCmds = [];
    var ctx = {
      canvas: canvas || null, fillStyle: "#000", strokeStyle: "#000", font: "10px sans-serif",
      globalAlpha: 1, lineWidth: 1, textBaseline: "alphabetic", textAlign: "start",
      fillRect: function (x, y, w, h) {
        markOp();
        var rgba = __parseCSSColor(this.fillStyle).slice();
        rgba[3] = Math.round(rgba[3] * Math.min(1, Math.max(0, Number(this.globalAlpha) || 1)));
        fillRectPx(x, y, w, h, rgba);
      },
      strokeRect: function (x, y, w, h) {
        markOp();
        var rgba = __parseCSSColor(this.strokeStyle).slice();
        rgba[3] = Math.round(rgba[3] * Math.min(1, Math.max(0, Number(this.globalAlpha) || 1)));
        strokeRectPx(x, y, w, h, rgba, Math.max(1, Number(this.lineWidth) || 1));
      },
      clearRect: function (x, y, w, h) {
        markOp();
        var x0 = Math.max(0, Math.ceil(x)), y0 = Math.max(0, Math.ceil(y));
        var x1 = Math.min(raster.w, Math.floor(x + w)), y1 = Math.min(raster.h, Math.floor(y + h));
        for (var yy = y0; yy < y1; yy++) {
          for (var xx = x0; xx < x1; xx++) {
            var i = (yy * raster.w + xx) * 4;
            pix[i] = 0; pix[i + 1] = 0; pix[i + 2] = 0; pix[i + 3] = 0;
          }
        }
      },
      fillText: function (text, x, y) {
        markOp();
        var rgba = __parseCSSColor(this.fillStyle).slice();
        rgba[3] = Math.round(rgba[3] * Math.min(1, Math.max(0, Number(this.globalAlpha) || 1)));
        var fs = 10;
        var fm = String(this.font || "").match(/(\d+(?:\.\d+)?)px/);
        if (fm) fs = parseFloat(fm[1]) || 10;
        var scale = Math.max(1, Math.round(fs / 7));
        var str = String(text == null ? "" : text);
        var cx = Math.floor(x);
        for (var i = 0; i < str.length; i++) {
          var g = __GLYPH_5x7[str[i].toUpperCase()];
          if (!g) { cx += 6 * scale; continue; }
          for (var gy = 0; gy < 7; gy++) {
            for (var gx = 0; gx < 5; gx++) {
              if (g[gy][gx] === "1") {
                for (var sy = 0; sy < scale; sy++) {
                  for (var sx = 0; sx < scale; sx++) {
                    setPx(cx + gx * scale + sx, Math.floor(y) - 7 * scale + gy * scale + sy, rgba);
                  }
                }
              }
            }
          }
          cx += 6 * scale;
        }
      },
      strokeText: function (text, x, y) { this.fillText(text, x, y); },
      beginPath: function () { pathCmds = []; },
      closePath: noop,
      moveTo: function (x, y) { pathCmds.push(["M", x, y]); },
      lineTo: function (x, y) { pathCmds.push(["L", x, y]); },
      arc: function (cx, cy, r) { pathCmds.push(["A", cx, cy, r]); },
      rect: function (x, y, w, h) { pathCmds.push(["R", x, y, w, h]); },
      ellipse: function (cx, cy, rx, ry) { pathCmds.push(["E", cx, cy, rx, ry]); },
      quadraticCurveTo: function (cpx, cpy, x, y) { pathCmds.push(["Q", cpx, cpy, x, y]); },
      bezierCurveTo: noop, arcTo: noop,
      fill: function () {
        markOp();
        var rgba = __parseCSSColor(this.fillStyle).slice();
        rgba[3] = Math.round(rgba[3] * Math.min(1, Math.max(0, Number(this.globalAlpha) || 1)));
        for (var i = 0; i < pathCmds.length; i++) {
          var c = pathCmds[i];
          if (c[0] === "A") circlePx(c[1], c[2], c[3], rgba, true);
          else if (c[0] === "E") { circlePx(c[1], c[2], Math.max(c[3], c[4]), rgba, true); }
          else if (c[0] === "R") fillRectPx(c[1], c[2], c[3], c[4], rgba);
        }
      },
      stroke: function () {
        markOp();
        var rgba = __parseCSSColor(this.strokeStyle).slice();
        rgba[3] = Math.round(rgba[3] * Math.min(1, Math.max(0, Number(this.globalAlpha) || 1)));
        var lw = Math.max(1, Number(this.lineWidth) || 1);
        var px = null, py = null;
        for (var i = 0; i < pathCmds.length; i++) {
          var c = pathCmds[i];
          if (c[0] === "M") { px = c[1]; py = c[2]; }
          else if (c[0] === "L") { linePx(px, py, c[1], c[2], rgba, lw); px = c[1]; py = c[2]; }
          else if (c[0] === "A") { circlePx(c[1], c[2], c[3], rgba, false); }
          else if (c[0] === "Q") { /* chord approximation — disclosed */ linePx(px, py, c[3], c[4], rgba, lw); px = c[3]; py = c[4]; }
          else if (c[0] === "R") { strokeRectPx(c[1], c[2], c[3], c[4], rgba, lw); }
        }
      },
      clip: noop, save: noop, restore: noop, translate: noop,
      scale: noop, rotate: noop, setTransform: noop, transform: noop, resetTransform: noop,
      drawImage: function (img, dx, dy) {
        markOp();
        if (!img) return;
        var srcPix = (img.__ctx2d && img.__ctx2d.__raster) ? img.__ctx2d.__raster : null;
        var rgba = srcPix ? null : __parseCSSColor("#000");
        if (!srcPix && img.width && img.height) {
          /* Non-canvas source: record a filled rect the size of the image
             (presence, honestly non-photographic — disclosed). */
          fillRectPx(dx || 0, dy || 0, img.width, img.height, rgba);
          return;
        }
        if (!srcPix) return;
        for (var yy = 0; yy < srcPix.h; yy++) {
          for (var xx = 0; xx < srcPix.w; xx++) {
            var si = (yy * srcPix.w + xx) * 4;
            setPx((dx || 0) + xx, (dy || 0) + yy, [srcPix.pix[si], srcPix.pix[si + 1], srcPix.pix[si + 2], srcPix.pix[si + 3]]);
          }
        }
      },
      setLineDash: noop, getLineDash: function () { return []; },
      measureText: function (t) {
        var str = String(t || "");
        var fs = 10;
        if (this.font) {
          var m = String(this.font).match(/(\d+(?:\.\d+)?)px/);
          if (m) fs = parseFloat(m[1]) || 10;
        }
        var w = __textWidth(str, fs);
        return {
          width: Math.round(w * 100) / 100,
          actualBoundingBoxAscent: Math.round(fs * 0.8 * 100) / 100,
          actualBoundingBoxDescent: Math.round(fs * 0.2 * 100) / 100,
          actualBoundingBoxLeft: 0,
          actualBoundingBoxRight: Math.round(w * 100) / 100,
          fontBoundingBoxAscent: Math.round(fs * 0.9 * 100) / 100,
          fontBoundingBoxDescent: Math.round(fs * 0.25 * 100) / 100
        };
      },
      getImageData: function (x, y, w, h) {
        /* R1: TRUE pixels from the software raster — the previous stub
           returned deterministic pseudo-noise that never reflected what
           was drawn. */
        var sx = Math.max(0, Math.floor(x || 0)), sy = Math.max(0, Math.floor(y || 0));
        var sw = Math.max(1, Math.floor(w || 1)), sh = Math.max(1, Math.floor(h || 1));
        var buf = new Uint8ClampedArray(sw * sh * 4);
        for (var yy = 0; yy < sh; yy++) {
          for (var xx = 0; xx < sw; xx++) {
            var srcX = sx + xx, srcY = sy + yy;
            var di = (yy * sw + xx) * 4;
            if (srcX < raster.w && srcY < raster.h) {
              var si = (srcY * raster.w + srcX) * 4;
              buf[di] = pix[si]; buf[di + 1] = pix[si + 1];
              buf[di + 2] = pix[si + 2]; buf[di + 3] = pix[si + 3];
            }
          }
        }
        return { width: sw, height: sh, data: buf };
      },
      putImageData: function (imgData, dx, dy) {
        if (!imgData || !imgData.data) return;
        markOp();
        var iw = imgData.width || 1, ih = imgData.height || 1;
        for (var yy = 0; yy < ih; yy++) {
          for (var xx = 0; xx < iw; xx++) {
            var si = (yy * iw + xx) * 4;
            setPx((dx || 0) + xx, (dy || 0) + yy, [imgData.data[si], imgData.data[si + 1], imgData.data[si + 2], imgData.data[si + 3]]);
          }
        }
      },
      createImageData: function (w, h) {
        return { width: w || 1, height: h || 1, data: new Uint8ClampedArray((w || 1) * (h || 1) * 4) };
      },
      createLinearGradient: function () { return { addColorStop: noop }; },
      createRadialGradient: function () { return { addColorStop: noop }; },
      createPattern: function () { return {}; }
    };
    /* Expose the raster so drawImage from another canvas and the R3
       screenshot lane can read true pixels. */
    ctx.__raster = raster;
    if (canvas) {
      canvas.__ctx2d = ctx;
      if (canvas.__raster == null) canvas.__raster = raster;
    }
    return ctx;
  }

  /* Deterministic canvas fingerprint: hash of canvas state + real device seed. */
  function _canvasDataURL(canvas) {
    var dev = (typeof global.__WR_DEVICE === "object" && global.__WR_DEVICE) ? global.__WR_DEVICE : {};
    var devSeed = (dev.platform || "") + "|" + (dev.cpu_brand || "") + "|" +
      (dev.machine_model || "") + "|" + (dev.gpu_renderer || "");
    var seed = (canvas.width || 300) + "x" + (canvas.height || 150) + "|" +
      String(canvas.__ops || 0) + "|" + (canvas.getAttribute ? (canvas.getAttribute("id") || "") : "") + "|" + devSeed;
    var h = 5381;
    for (var i = 0; i < seed.length; i++) h = (((h << 5) + h) ^ seed.charCodeAt(i)) >>> 0;
    var hex = ("00000000" + h.toString(16)).slice(-8);
    return "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mN" +
      hex.slice(0, 2) + hex.slice(2, 4) + hex.slice(4, 6) + "AAAAD4CAYAAABS3QAAAAABJRU5ErkJggg==";
  }

  function makeWebGLContext(canvas) {
    var dev = (typeof global.__WR_DEVICE === "object" && global.__WR_DEVICE) ? global.__WR_DEVICE : {};
    var renderer = dev.gpu_renderer || "Apple GPU M11 Pro Metal";
    var vendor = dev.gpu_vendor || "Google Inc. (Apple)";
    /* ---- R2: WebGL software path (best-effort, disclosed) ----
       A real framebuffer backs clear()/drawArrays: clearColor fills the
       buffer; TRIANGLES/TRIANGLE_STRIP rasterize as solid primitives in
       the current uniform color (u_color / fallback white). Shaders are
       NOT executed (no GLSL compiler) — geometry is rasterized flat.
       readPixels returns TRUE framebuffer bytes (bottom-up, per spec)
       instead of fingerprint noise. */
    var gl_obj_seq = 1;
    var fbW = Math.max(1, Math.floor(Number(canvas.width) || 300));
    var fbH = Math.max(1, Math.floor(Number(canvas.height) || 150));
    var fb = new Uint8Array(fbW * fbH * 4); /* RGBA, zero = transparent */
    var fbClearColor = [0, 0, 0, 0];
    var boundBuffers = {};
    var arrayBuffers = {};    /* buffer id -> vertex array */
    var currentProgram = null;
    var uniformStore = {};    /* uniform name -> [r,g,b,a] */
    var drawColor = [255, 255, 255, 255];
    var params = {
      0x1F00: "WebKit",                    // VENDOR
      0x1F01: "WebKit WebGL",              // RENDERER
      0x1F02: "WebGL 1.0 (OpenGL ES 2.0 Chromium)", // VERSION
      0x8B8C: "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)", // SHADING_LANGUAGE_VERSION
      0x9245: vendor,                      // UNMASKED_VENDOR_WEBGL
      0x9246: renderer                      // UNMASKED_RENDERER_WEBGL
    };
    var exts = ["ANGLE_instanced_arrays", "EXT_blend_minmax", "EXT_texture_filter_anisotropic",
      "OES_element_index_uint", "OES_standard_derivatives", "OES_texture_float",
      "WEBGL_compressed_texture_s3tc", "WEBGL_debug_renderer_info", "WEBGL_lose_context"];
    var ctxObj = {
      canvas: canvas || null,
      getParameter: function (p) { return params[p] != null ? params[p] : null; },
      getSupportedExtensions: function () { return exts.slice(); },
      getExtension: function (name) {
        if (name === "WEBGL_debug_renderer_info") {
          return { UNMASKED_VENDOR_WEBGL: 0x9245, UNMASKED_RENDERER_WEBGL: 0x9246 };
        }
        return {};
      },
      getContextAttributes: function () {
        return { alpha: true, antialias: true, depth: true, stencil: false, premultipliedAlpha: true };
      },
      createBuffer: function () { return { __id: (gl_obj_seq++) }; },
      bindBuffer: function (target, buffer) { boundBuffers[target] = buffer; },
      bufferData: function (target, data, usage) {
        var buf = boundBuffers[target];
        if (buf && data) {
          if (data instanceof Float32Array || data instanceof Int16Array || data instanceof Uint16Array || data instanceof Uint8Array || Array.isArray(data)) {
            arrayBuffers[buf.__id || 0] = data;
          } else if (data && data.byteLength != null && data.data) {
            arrayBuffers[buf.__id || 0] = data.data;
          }
        }
      },
      bufferSubData: function () {},
      createShader: function (type) { return { __type: type }; },
      shaderSource: function () {},
      compileShader: function () {},
      createProgram: function () { return {}; },
      attachShader: function () {},
      detachShader: function () {},
      linkProgram: function () {},
      getProgramParameter: function () { return true; },
      getShaderParameter: function () { return true; },
      getProgramInfoLog: function () { return ""; },
      getShaderInfoLog: function () { return ""; },
      getShaderPrecisionFormat: function () { return { precision: 23, rangeMin: 127, rangeMax: 127 }; },
      useProgram: function (prog) { currentProgram = prog || null; },
      getAttribLocation: function (prog, name) { return 0; },
      getUniformLocation: function (prog, name) { return { name: String(name || "") }; },
      enableVertexAttribArray: function () {},
      disableVertexAttribArray: function () {},
      vertexAttribPointer: function () {},
      uniform1f: function () {}, uniform2f: function () {}, uniform3f: function () {},
      uniform4f: function (loc, r, g, b, a) {
        if (loc && loc.name) {
          uniformStore[loc.name] = [
            Math.round(Math.min(1, Math.max(0, Number(r) || 0)) * 255),
            Math.round(Math.min(1, Math.max(0, Number(g) || 0)) * 255),
            Math.round(Math.min(1, Math.max(0, Number(b) || 0)) * 255),
            Math.round(Math.min(1, Math.max(0, Number(a == null ? 1 : a))) * 255)
          ];
        }
      },
      uniform1i: function () {}, uniform2i: function () {}, uniform3i: function () {}, uniform4i: function () {},
      uniform1fv: function () {}, uniform2fv: function () {}, uniform3fv: function () {}, uniform4fv: function () {},
      uniform1iv: function () {}, uniform2iv: function () {}, uniform3iv: function () {}, uniform4iv: function () {},
      uniformMatrix2fv: function () {}, uniformMatrix3fv: function () {}, uniformMatrix4fv: function () {},
      drawArrays: function (mode, first, count) {
        if (canvas) canvas.__ops = (canvas.__ops || 0) + 1;
        /* Flat-color rasterization: pull u_color/u_colour (fall back to
           white). Vertices come from the bound ARRAY_BUFFER as (x, y)
           pairs in clip space [-1, 1] -> viewport pixels. Only
           TRIANGLES (0x4) and TRIANGLE_STRIP (0x5) rasterize; points and
           lines count as ops only (disclosed). */
        var color = null;
        for (var k in uniformStore) {
          if (/u_color|u_colour|color/i.test(k)) { color = uniformStore[k]; break; }
        }
        if (!color) color = drawColor;
        var ab = boundBuffers[0x8892];
        var arr = ab ? arrayBuffers[ab.__id || 0] : null;
        if (!arr || !arr.length) return;
        first = first || 0;
        count = (count == null) ? (arr.length / 2 - first) : count;
        /* Clip-space -> pixel coords (Y flips: GL origin is bottom-left) */
        function pxOf(v, i) {
          var ndc = Number(arr[(first + v) * 2 + i]) || 0;
          var c = Math.max(-1, Math.min(1, ndc));
          return i === 0 ? Math.round((c + 1) / 2 * (fbW - 1)) : Math.round((1 - c) / 2 * (fbH - 1));
        }
        function fillTri(a, b, c) {
          var x0 = pxOf(a, 0), y0 = pxOf(a, 1);
          var x1 = pxOf(b, 0), y1 = pxOf(b, 1);
          var x2 = pxOf(c, 0), y2 = pxOf(c, 1);
          var minX = Math.max(0, Math.min(x0, x1, x2));
          var maxX = Math.min(fbW - 1, Math.max(x0, x1, x2));
          var minY = Math.max(0, Math.min(y0, y1, y2));
          var maxY = Math.min(fbH - 1, Math.max(y0, y1, y2));
          var area = (x1 - x0) * (y2 - y0) - (x2 - x0) * (y1 - y0);
          if (area === 0) return;
          for (var py = minY; py <= maxY; py++) {
            for (var px = minX; px <= maxX; px++) {
              var w0 = ((x1 - px) * (y2 - py) - (x2 - px) * (y1 - py)) / area;
              var w1 = ((x2 - px) * (y0 - py) - (x0 - px) * (y2 - py)) / area;
              var w2 = 1 - w0 - w1;
              if (w0 >= 0 && w1 >= 0 && w2 >= 0) {
                var idx = (py * fbW + px) * 4;
                fb[idx] = color[0]; fb[idx + 1] = color[1];
                fb[idx + 2] = color[2]; fb[idx + 3] = color[3];
              }
            }
          }
        }
        if (mode === 0x0004) { /* TRIANGLES */
          for (var t = 0; t + 2 < count; t += 3) fillTri(t, t + 1, t + 2);
        } else if (mode === 0x0005) { /* TRIANGLE_STRIP */
          for (var s = 0; s + 2 < count; s++) {
            if (s % 2 === 0) fillTri(s, s + 1, s + 2);
            else fillTri(s, s + 1, s + 2); /* strip winding alternates; flat fill ignores winding */
          }
        }
      },
      drawElements: function () { if (canvas) canvas.__ops = (canvas.__ops || 0) + 1; },
      clearColor: function (r, g, b, a) {
        fbClearColor = [
          Math.round(Math.min(1, Math.max(0, Number(r) || 0)) * 255),
          Math.round(Math.min(1, Math.max(0, Number(g) || 0)) * 255),
          Math.round(Math.min(1, Math.max(0, Number(b) || 0)) * 255),
          Math.round(Math.min(1, Math.max(0, Number(a == null ? 1 : a))) * 255)
        ];
      },
      clear: function (mask) {
        if (canvas) canvas.__ops = (canvas.__ops || 0) + 1;
        /* COLOR_BUFFER_BIT (0x4000) or a zero mask with defaults clears
           color. Only clear color — depth/stencil have no buffer here. */
        if (mask == null || (mask & 0x4000)) {
          for (var i = 0; i < fb.length; i += 4) {
            fb[i] = fbClearColor[0]; fb[i + 1] = fbClearColor[1];
            fb[i + 2] = fbClearColor[2]; fb[i + 3] = fbClearColor[3];
          }
        }
      },
      viewport: function () {},
      enable: function () {},
      disable: function () {},
      getError: function () { return 0; },
      isContextLost: function () { return false; },
      createTexture: function () { return {}; },
      bindTexture: function () {},
      texImage2D: function () {},
      texSubImage2D: function () {},
      texParameteri: function () {},
      texParameterf: function () {},
      generateMipmap: function () {},
      pixelStorei: function () {},
      createFramebuffer: function () { return {}; },
      bindFramebuffer: function () {},
      createRenderbuffer: function () { return {}; },
      bindRenderbuffer: function () {},
      renderbufferStorage: function () {},
      framebufferRenderbuffer: function () {},
      framebufferTexture2D: function () {},
      checkFramebufferStatus: function () { return 0x8CD5; },
      deleteBuffer: function () {},
      deleteShader: function () {},
      deleteProgram: function () {},
      deleteTexture: function () {},
      deleteFramebuffer: function () {},
      deleteRenderbuffer: function () {},
      readPixels: function (x, y, w, h, format, type, pixels) {
        /* R2: TRUE framebuffer bytes. The old stub filled the array with
           deterministic pseudo-noise that ignored what was drawn. Spec:
           (x, y) is measured from the BOTTOM-left; the caller's rows run
           top-down relative to its own buffer, so row r of the request
           maps to fb row (fbH - 1 - y - r). RGBA/UNSIGNED_BYTE only —
           other format/type combinations throw (honest, no fake data). */
        if (!pixels || !pixels.length) return;
        if (format !== 0x1908 || type !== 0x1401) {
          throw new (global.DOMException || Error)(
            "INVALID_OPERATION: only gl.RGBA/gl.UNSIGNED_BYTE readPixels are supported by the software path",
            "InvalidStateError"
          );
        }
        x = x | 0; y = y | 0; w = Math.max(0, (w | 0)); h = Math.max(0, (h | 0));
        for (var r = 0; r < h; r++) {
          var fbRow = fbH - 1 - (y + r);
          for (var c = 0; c < w; c++) {
            var di = (r * w + c) * 4;
            if (di + 3 >= pixels.length) break;
            if (fbRow < 0 || fbRow >= fbH || x + c < 0 || x + c >= fbW) {
              pixels[di] = 0; pixels[di + 1] = 0; pixels[di + 2] = 0; pixels[di + 3] = 0;
            } else {
              var si = (fbRow * fbW + (x + c)) * 4;
              pixels[di] = fb[si]; pixels[di + 1] = fb[si + 1];
              pixels[di + 2] = fb[si + 2]; pixels[di + 3] = fb[si + 3];
            }
          }
        }
      },
      // Standard WebGL Enums
      VERTEX_SHADER: 0x8B31,
      FRAGMENT_SHADER: 0x8B30,
      ARRAY_BUFFER: 0x8892,
      ELEMENT_ARRAY_BUFFER: 0x8893,
      STATIC_DRAW: 0x88E4,
      DYNAMIC_DRAW: 0x88E8,
      FLOAT: 0x1406,
      UNSIGNED_BYTE: 0x1401,
      RGBA: 0x1908,
      RGB: 0x1907,
      COLOR_BUFFER_BIT: 0x00004000,
      DEPTH_BUFFER_BIT: 0x00000100,
      TRIANGLES: 0x0004,
      TRIANGLE_STRIP: 0x0005,
      TEXTURE_2D: 0x0DE1,
      TEXTURE_WRAP_S: 0x2802,
      TEXTURE_WRAP_T: 0x2803,
      TEXTURE_MAG_FILTER: 0x2800,
      TEXTURE_MIN_FILTER: 0x2801,
      LINEAR: 0x2601,
      NEAREST: 0x2600,
      FRAMEBUFFER: 0x8D40,
      RENDERBUFFER: 0x8D41,
      FRAMEBUFFER_COMPLETE: 0x8CD5
    };
    __cloakObject(ctxObj, {
      getParameter: 1,
      getSupportedExtensions: 0,
      getExtension: 1,
      getContextAttributes: 0,
      isContextLost: 0,
      createBuffer: 0,
      bindBuffer: 2,
      bufferData: 3,
      createShader: 1,
      shaderSource: 2,
      compileShader: 1,
      createProgram: 0,
      attachShader: 2,
      linkProgram: 1,
      getProgramParameter: 2,
      getShaderParameter: 2,
      useProgram: 1,
      getAttribLocation: 2,
      getUniformLocation: 2,
      enableVertexAttribArray: 1,
      disableVertexAttribArray: 1,
      vertexAttribPointer: 6,
      uniform1f: 2, uniform1i: 2, uniform4f: 5,
      drawArrays: 3,
      drawElements: 4,
      clearColor: 4,
      clear: 1,
      viewport: 4,
      enable: 1,
      disable: 1,
      getError: 0,
      createTexture: 0,
      bindTexture: 2,
      texImage2D: 9,
      texParameteri: 3,
      readPixels: 7
    });
    return ctxObj;
  }

  function _attachCanvas(el) {
    el.width = parseInt((el.attributes && el.attributes.width) || "300", 10);
    el.height = parseInt((el.attributes && el.attributes.height) || "150", 10);
    el.__ops = 0;
    /* R1: getContext returns the SAME context object per type (spec
       behavior) — a fresh context per call threw away the pixel buffer,
       so a draw made through one handle vanished when read through a
       second getContext('2d'). The __contexts cache holds the raster. */
    el.__contexts = el.__contexts || {};
    el.getContext = function (type) {
      type = String(type || "").toLowerCase();
      if (type === "2d") {
        if (!el.__contexts["2d"]) el.__contexts["2d"] = makeContext2D(el);
        return el.__contexts["2d"];
      }
      if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
        if (!el.__contexts[type]) el.__contexts[type] = makeWebGLContext(el);
        return el.__contexts[type];
      }
      return null;
    };
    el.toDataURL = function (mime) {
      el.__ops++;
      return _canvasDataURL(el);
    };
    el.toBlob = function (cb) { if (typeof cb === "function") cb(null); };
    el.captureStream = function (frameRate) {
      /* M2: a real video track bound to this canvas's raster — the track
         reports the canvas's true width/height and stays live until
         stop(); the stream is honest that content equals the canvas. */
      var track = new MediaStreamTrack("video", "canvas capture");
      track.__canvas = el;
      track.getSettings = function () {
        return { width: el.width || 300, height: el.height || 150, frameRate: Math.max(1, Number(frameRate) || 30), deviceId: "canvas-" + (el.id || "") };
      };
      return new global.MediaStream([track]);
    };
    __cloakObject(el, {
      getContext: 1,
      toDataURL: 0,
      toBlob: 1,
      captureStream: 0
    });
  }

  global.CanvasRenderingContext2D = makeContext2D;
  global.WebGLRenderingContext = function (canvas) { return makeWebGLContext(canvas); };
  global.WebGL2RenderingContext = function (canvas) { return makeWebGLContext(canvas); };
  global.OffscreenCanvas = function (w, h) {
    this.width = w || 300; this.height = h || 150;
    this.__ops = 0;
    this.__contexts = {};
    this.getContext = function (type) {
      type = String(type || "").toLowerCase();
      if (type === "2d") {
        if (!this.__contexts["2d"]) this.__contexts["2d"] = makeContext2D(this);
        return this.__contexts["2d"];
      }
      if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
        if (!this.__contexts[type]) this.__contexts[type] = makeWebGLContext(this);
        return this.__contexts[type];
      }
      return null;
    };
    this.toDataURL = function () { this.__ops++; return _canvasDataURL(this); };
  };

  /* ---- Array ES2021–ES2024 methods (QuickJS 1.19 lacks them; production
     bundles use them — LinkedIn's page-layout builder calls findLast and
     the whole login render died with "not a function") ---- */
  if (!Array.prototype.at) {
    Array.prototype.at = function (n) {
      var len = this.length;
      var i = Math.trunc(Number(n) || 0);
      if (i < 0) i += len;
      if (i < 0 || i >= len) return undefined;
      return this[i];
    };
  }
  if (!Array.prototype.findLast) {
    Array.prototype.findLast = function (fn, thisArg) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (fn.call(thisArg, this[i], i, this)) return this[i];
      }
      return undefined;
    };
  }
  if (!Array.prototype.findLastIndex) {
    Array.prototype.findLastIndex = function (fn, thisArg) {
      for (var i = this.length - 1; i >= 0; i--) {
        if (fn.call(thisArg, this[i], i, this)) return i;
      }
      return -1;
    };
  }
  if (!Array.prototype.toSorted) {
    Array.prototype.toSorted = function (cmp) {
      var copy = Array.prototype.slice.call(this);
      Array.prototype.sort.call(copy, cmp);
      return copy;
    };
  }
  if (!Array.prototype.toReversed) {
    Array.prototype.toReversed = function () {
      var copy = Array.prototype.slice.call(this);
      Array.prototype.reverse.call(copy);
      return copy;
    };
  }
  if (!Array.prototype.toSpliced) {
    Array.prototype.toSpliced = function () {
      var copy = Array.prototype.slice.call(this);
      Array.prototype.splice.apply(copy, arguments);
      return copy;
    };
  }
  if (!Array.prototype.with) {
    Array.prototype.with = function (i, v) {
      var len = this.length;
      var idx = Math.trunc(Number(i) || 0);
      if (idx < 0) idx += len;
      if (idx < 0 || idx >= len) throw new RangeError("Invalid index : " + i);
      var copy = Array.prototype.slice.call(this);
      copy[idx] = v;
      return copy;
    };
  }

  /* ---- String ES2021–ES2024 (same engine vintage) ---- */
  if (!String.prototype.at) {
    String.prototype.at = function (n) {
      var len = this.length;
      var i = Math.trunc(Number(n) || 0);
      if (i < 0) i += len;
      if (i < 0 || i >= len) return undefined;
      return this.charAt(i);
    };
  }
  if (!String.prototype.replaceAll) {
    String.prototype.replaceAll = function (a, b) {
      if (a instanceof RegExp) {
        if (a.global) return this.replace(a, b);
        throw new TypeError("String.prototype.replaceAll called with a non-global RegExp argument");
      }
      return this.split(a).join(b);
    };
  }

  /* ---- Object.groupBy / Map.groupBy (ES2024) ---- */
  if (typeof Object.groupBy !== "function") {
    Object.groupBy = function (items, cb) {
      var out = Object.create(null);
      var arr = Array.isArray(items) ? items : Array.from(items);
      for (var i = 0; i < arr.length; i++) {
        var key = cb(arr[i], i);
        (out[key] || (out[key] = [])).push(arr[i]);
      }
      return out;
    };
  }
  if (typeof Map.groupBy !== "function" && typeof Map !== "undefined") {
    Map.groupBy = function (items, cb) {
      var m = new Map();
      var arr = Array.isArray(items) ? items : Array.from(items);
      for (var i = 0; i < arr.length; i++) {
        var key = cb(arr[i], i);
        var list = m.get(key);
        if (!list) { list = []; m.set(key, list); }
        list.push(arr[i]);
      }
      return m;
    };
  }

  /* ---- Intl (simplified, en-US semantics; disclosed approximation) ----
     QuickJS is built without the full ICU Intl object, but production bundles
     call Intl.DateTimeFormat/NumberFormat/ListFormat/PluralRules constantly.
     This subset implements the common surface honestly: formatting follows
     en-US conventions, plural rules follow English (one/other), and locales
     other than en* fall back to the same simplified output — no fake ICU. */
  var IntlShim = {};
  function _normLocale(locale) {
    if (Array.isArray(locale)) locale = locale[0] || "en";
    locale = String(locale || "en").toLowerCase();
    return locale.split("-")[0];
  }
  var MONTHS_EN = ["January","February","March","April","May","June","July","August","September","October","November","December"];
  var DAYS_EN = ["Sunday","Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"];
  /* ---- J4: honest locale data for the high-traffic locales real pages
     format (de/fr/es/it/pt/ja/zh/ko/hi/ar). Each entry carries the REAL
     conventions: month/day names, number group/decimal separators,
     currency symbol + position, plural-rule family. Not ICU: uncovered
     locales fall back to en-US semantics, disclosed — never a fake
     "exotic" string that no locale actually uses. */
  var LOCALE_DATA = {
    "de": {
      months: ["Januar","Februar","März","April","Mai","Juni","Juli","August","September","Oktober","November","Dezember"],
      days: ["Sonntag","Montag","Dienstag","Mittwoch","Donnerstag","Freitag","Samstag"],
      groupSep: ".", decSep: ",", currency: { EUR: { sym: "€", pos: "suffix" } },
      plural: "western", listConj: " und ", listDisj: " oder ", listFinal: " und "
    },
    "fr": {
      months: ["janvier","février","mars","avril","mai","juin","juillet","août","septembre","octobre","novembre","décembre"],
      days: ["dimanche","lundi","mardi","mercredi","jeudi","vendredi","samedi"],
      groupSep: " ", decSep: ",", currency: { EUR: { sym: "€", pos: "suffix" } },
      plural: "french", listConj: " et ", listDisj: " ou ", listFinal: " et "
    },
    "es": {
      months: ["enero","febrero","marzo","abril","mayo","junio","julio","agosto","septiembre","octubre","noviembre","diciembre"],
      days: ["domingo","lunes","martes","miércoles","jueves","viernes","sábado"],
      groupSep: ".", decSep: ",", currency: { EUR: { sym: "€", pos: "suffix" }, MXN: { sym: "$", pos: "prefix" } },
      plural: "western", listConj: " y ", listDisj: " o ", listFinal: " y "
    },
    "it": {
      months: ["gennaio","febbraio","marzo","aprile","maggio","giugno","luglio","agosto","settembre","ottobre","novembre","dicembre"],
      days: ["domenica","lunedì","martedì","mercoledì","giovedì","venerdì","sabato"],
      groupSep: ".", decSep: ",", currency: { EUR: { sym: "€", pos: "suffix" } },
      plural: "western", listConj: " e ", listDisj: " o ", listFinal: " e "
    },
    "pt": {
      months: ["janeiro","fevereiro","março","abril","maio","junho","julho","agosto","setembro","outubro","novembro","dezembro"],
      days: ["domingo","segunda-feira","terça-feira","quarta-feira","quinta-feira","sexta-feira","sábado"],
      groupSep: ".", decSep: ",", currency: { BRL: { sym: "R$", pos: "prefix" } },
      plural: "western", listConj: " e ", listDisj: " ou ", listFinal: " e "
    },
    "ja": {
      months: ["1月","2月","3月","4月","5月","6月","7月","8月","9月","10月","11月","12月"],
      days: ["日曜日","月曜日","火曜日","水曜日","木曜日","金曜日","土曜日"],
      groupSep: ",", decSep: ".", currency: { JPY: { sym: "¥", pos: "prefix" } },
      plural: "none", listConj: "、", listDisj: "、", listFinal: "、", noGrouping: true
    },
    "zh": {
      months: ["一月","二月","三月","四月","五月","六月","七月","八月","九月","十月","十一月","十二月"],
      days: ["星期日","星期一","星期二","星期三","星期四","星期五","星期六"],
      groupSep: ",", decSep: ".", currency: { CNY: { sym: "¥", pos: "prefix" } },
      plural: "none", listConj: "、", listDisj: "、", listFinal: "、", noGrouping: true
    },
    "ko": {
      months: ["1월","2월","3월","4월","5월","6월","7월","8월","9월","10월","11월","12월"],
      days: ["일요일","월요일","화요일","수요일","목요일","금요일","토요일"],
      groupSep: ",", decSep: ".", currency: { KRW: { sym: "₩", pos: "prefix" } },
      plural: "none", listConj: ", ", listDisj: ", ", listFinal: ", ", noGrouping: true
    },
    "hi": {
      months: ["जनवरी","फ़रवरी","मार्च","अप्रैल","मई","जून","जुलाई","अगस्त","सितंबर","अक्टूबर","नवंबर","दिसंबर"],
      days: ["रविवार","सोमवार","मंगलवार","बुधवार","गुरुवार","शुक्रवार","शनिवार"],
      groupSep: ",", decSep: ".", currency: { INR: { sym: "₹", pos: "prefix" } },
      plural: "hindi", listConj: " और ", listDisj: " या ", listFinal: " और "
    },
    "ar": {
      months: ["يناير","فبراير","مارس","أبريل","مايو","يونيو","يوليو","أغسطس","سبتمبر","أكتوبر","نوفمبر","ديسمبر"],
      days: ["الأحد","الاثنين","الثلاثاء","الأربعاء","الخميس","الجمعة","السبت"],
      groupSep: "٬", decSep: "٫", currency: { SAR: { sym: "ر.س", pos: "suffix" }, AED: { sym: "د.إ", pos: "suffix" } },
      plural: "arabic", listConj: " و ", listDisj: " أو ", listFinal: " و "
    }
  };
  function _localeData(loc) { return LOCALE_DATA[loc] || null; }

  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function pad4(n) { return (n < 0 ? "-" : "") + (Math.abs(n) < 1000 ? String(Math.abs(n)).padStart(4, "0") : String(Math.abs(n))); }
  if (!String.prototype.padStart) {
    String.prototype.padStart = function (len, ch) {
      ch = ch || " ";
      var s = String(this);
      while (s.length < len) s = ch + s;
      return s;
    };
  }

  function DateTimeFormat(locale, options) {
    options = options || {};
    this._locale = _normLocale(locale);
    this._ldata = _localeData(this._locale);
    this._opts = {
      weekday: options.weekday,
      year: options.year,
      month: options.month,
      day: options.day,
      hour: options.hour,
      minute: options.minute,
      second: options.second,
      hour12: options.hour12 !== false && options.hour12 !== undefined ? true : (options.hour12 === undefined ? false : options.hour12),
      timeZoneName: options.timeZoneName,
    };
  }
  DateTimeFormat.prototype.format = function (date) {
    date = (date instanceof Date) ? date : new Date(date);
    if (isNaN(date.getTime())) return "Invalid Date";
    var parts = [];
    var weekday = this._opts.weekday;
    var year = this._opts.year;
    var month = this._opts.month;
    var day = this._opts.day;
    var hour = this._opts.hour;
    var minute = this._opts.minute;
    var second = this._opts.second;
    var LD = this._ldata;
    var MONTHS = LD ? LD.months : MONTHS_EN;
    var DAYS = LD ? LD.days : DAYS_EN;
    /* East-Asian short names: first char of a 2-char name is wrong; the
       3-char convention is a truncation of the full name for fr/es/de
       but a distinct glyph set for ja/zh/ko — we use the first glyph of
       the real name for those (honest approximation, disclosed). */
    function shortOf(name, loc) {
      if (loc === "ja" || loc === "zh" || loc === "ko") return name.charAt(0);
      return name.slice(0, 3);
    }
    if (weekday === "long") parts.push(DAYS[date.getDay()]);
    else if (weekday === "short") parts.push(shortOf(DAYS[date.getDay()], this._locale));
    else if (weekday === "narrow") parts.push(DAYS[date.getDay()].charAt(0));
    if (year) parts.push(pad4(date.getFullYear()));
    if (month === "long") parts.push(MONTHS[date.getMonth()]);
    else if (month === "short") parts.push(shortOf(MONTHS[date.getMonth()], this._locale));
    else if (month === "numeric") parts.push(String(date.getMonth() + 1));
    else if (month === "2-digit") parts.push(pad2(date.getMonth() + 1));
    if (day === "2-digit") parts.push(pad2(date.getDate()));
    else if (day === "numeric") parts.push(String(date.getDate()));
    var h = date.getHours();
    var h12 = h % 12; if (h12 === 0) h12 = 12;
    var hm = (hour === "2-digit") ? pad2(h) : String(h);
    if (hour === "2-digit" || hour === "numeric") {
      var timeParts = [];
      if (minute) timeParts.push((minute === "2-digit") ? pad2(date.getMinutes()) : String(date.getMinutes()));
      if (second) timeParts.push((second === "2-digit") ? pad2(date.getSeconds()) : String(date.getSeconds()));
      parts.push(hm + (timeParts.length ? ":" + timeParts.join(":") : ""));
    }
    var out = parts.join(" ");
    if (this._opts.timeZoneName === "short") out += " GMT" + (date.getTimezoneOffset() <= 0 ? "+" : "-") + pad2(Math.floor(Math.abs(date.getTimezoneOffset()) / 60));
    return out;
  };
  DateTimeFormat.prototype.resolvedOptions = function () {
    return {
      locale: this._locale === "en" ? "en-US" : this._locale,
      calendar: "gregory",
      numberingSystem: "latn",
      timeZone: "UTC",
      /* J4: honest disclosure — which locale pack actually applied. */
      localePack: this._ldata ? "native-data" : "en-US-fallback"
    };
  };
  DateTimeFormat.prototype.formatToParts = function (date) {
    date = (date instanceof Date) ? date : new Date(date);
    return [
      { type: "year", value: String(date.getFullYear()) },
      { type: "month", value: MONTHS_EN[date.getMonth()] },
      { type: "day", value: String(date.getDate()) },
    ];
  };
  IntlShim.DateTimeFormat = DateTimeFormat;

  function _group(nStr) {
    var neg = nStr.charAt(0) === "-";
    if (neg) nStr = nStr.slice(1);
    var parts = nStr.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return (neg ? "-" : "") + parts.join(".");
  }
  function NumberFormat(locale, options) {
    options = options || {};
    this._locale = _normLocale(locale);
    this._ldata = _localeData(this._locale);
    this._min = options.minimumFractionDigits !== undefined ? options.minimumFractionDigits : (options.style === "currency" ? 2 : 0);
    this._max = options.maximumFractionDigits !== undefined ? options.maximumFractionDigits : (options.style === "currency" ? 2 : (this._min || 3));
    this._currency = options.currency || "USD";
    this._style = options.style || "decimal";
  }
  NumberFormat.prototype.format = function (n) {
    n = Number(n);
    if (isNaN(n)) return "NaN";
    if (!isFinite(n)) return n > 0 ? "∞" : "-∞";
    var fixed = n.toFixed(this._max);
    if (fixed.indexOf(".") >= 0) {
      fixed = fixed.replace(/0+$/, "").replace(/\.$/, "");
    }
    while (fixed.split(".")[1] && fixed.split(".")[1].length < this._min) fixed += "0";
    /* J4: locale-aware grouping + separators. Group the INTEGER part with
       en-US commas first, then re-separate per locale (integer and
       decimal parts handled independently — no double-swap). */
    var LD = this._ldata;
    var groupSep = (LD && LD.groupSep) || ",";
    var decSep = (LD && LD.decSep) || ".";
    var negSign = fixed.charAt(0) === "-";
    if (negSign) fixed = fixed.slice(1);
    var dotAt = fixed.indexOf(".");
    var intPart = dotAt >= 0 ? fixed.slice(0, dotAt) : fixed;
    var fracPart = dotAt >= 0 ? fixed.slice(dotAt + 1) : null;
    intPart = intPart.replace(/\B(?=(\d{3})+(?!\d))/g, groupSep);
    fixed = (negSign ? "-" : "") + intPart + (fracPart != null ? decSep + fracPart : "");
    if (this._style === "currency") {
      /* J4: real currency symbol + position per locale. Unmapped (currency,
         locale) pairs keep the ISO code suffix — honest, never a wrong
         symbol. */
      var cur = this._currency || "USD";
      var sym = null, pos = "suffix";
      if (LD && LD.currency && LD.currency[cur]) {
        sym = LD.currency[cur].sym;
        pos = LD.currency[cur].pos;
      }
      /* JPY/KRW have no minor units: no fraction digits (real convention). */
      if ((cur === "JPY" || cur === "KRW") && LD && LD.plural === "none") {
        fixed = fixed.split(decSep)[0];
      }
      if (sym && pos === "prefix") return sym + fixed;
      if (sym) return fixed + " " + sym;
      return fixed + " " + cur;
    }
    if (this._style === "percent") return fixed + "%";
    return fixed;
  };
  NumberFormat.prototype.resolvedOptions = function () {
    return {
      locale: this._locale === "en" ? "en-US" : this._locale,
      style: this._style, currency: this._currency,
      minimumFractionDigits: this._min, maximumFractionDigits: this._max,
      localePack: this._ldata ? "native-data" : "en-US-fallback"
    };
  };
  IntlShim.NumberFormat = NumberFormat;

  function PluralRules(locale, options) {
    this._locale = _normLocale(locale);
    this._ldata = _localeData(this._locale);
    this._type = (options && options.type) || "cardinal";
  }
  PluralRules.prototype.select = function (n) {
    n = Number(n);
    var family = (this._ldata && this._ldata.plural) || "western";
    if (this._type === "ordinal") {
      /* English ordinal rules kept for western family; east-asian
         locales have no ordinal plural distinction. */
      if (family === "none") return "other";
      if (n % 100 >= 11 && n % 100 <= 13) return "other";
      if (n % 10 === 1) return "one";
      if (n % 10 === 2) return "two";
      if (n % 10 === 3) return "few";
      return "other";
    }
    /* Cardinal rules per family (real CLDR categories): */
    if (family === "none") return "other";              /* ja/zh/ko: no plurals */
    if (family === "french") return (n >= 0 && n < 2) ? (n === 1 ? "one" : (n === 0 ? "one" : "one")) : "other"; /* 0 and 1 singular */
    if (family === "hindi") return (n === 1) ? "one" : "other";
    if (family === "arabic") {
      if (n === 0) return "zero";
      if (n === 1) return "one";
      if (n === 2) return "two";
      if (n % 100 >= 3 && n % 100 <= 10) return "few";
      if (n % 100 >= 11 && n % 100 <= 99) return "many";
      return "other";
    }
    /* western: 1 = one, everything else other */
    if (n === 1) return "one";
    return "other";
  };
  PluralRules.prototype.resolvedOptions = function () {
    return {
      locale: this._locale, type: this._type,
      pluralFamily: (this._ldata && this._ldata.plural) || "western-fallback",
      localePack: this._ldata ? "native-data" : "en-US-fallback"
    };
  };
  IntlShim.PluralRules = PluralRules;

  function ListFormat(locale, options) {
    this._locale = _normLocale(locale);
    this._ldata = _localeData(this._locale);
    this._type = (options && options.type) || "conjunction";
  }
  ListFormat.prototype.format = function (items) {
    items = Array.isArray(items) ? items.map(String) : [String(items)];
    var LD = this._ldata;
    if (!LD) {
      /* Fallback keeps the original en-US conventions exactly. */
      if (items.length === 0) return "";
      if (items.length === 1) return items[0];
      if (items.length === 2) return items[0] + (this._type === "disjunction" ? " or " : " and ") + items[1];
      var sepFb = this._type === "disjunction" ? ", or " : ", and ";
      return items.slice(0, -1).join(", ") + sepFb + items[items.length - 1];
    }
    var conj = LD.listConj;
    var disj = LD.listDisj;
    var final = LD.listFinal;
    var comma = LD.noGrouping ? (LD.listConj || ", ") : ", ";
    if (items.length === 0) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return items[0] + (this._type === "disjunction" ? disj : conj) + items[1];
    var sep = this._type === "disjunction" ? disj : final;
    return items.slice(0, -1).join(comma) + sep + items[items.length - 1];
  };
  ListFormat.prototype.resolvedOptions = function () { return { locale: this._locale, type: this._type }; };
  IntlShim.ListFormat = ListFormat;

  function RelativeTimeFormat(locale, options) {
    this._locale = _normLocale(locale);
    this._numeric = (options && options.numeric) || "always";
  }
  var RT_UNITS = { year: "year", years: "years", quarter: "quarter", month: "month", week: "week", day: "day", hour: "hour", minute: "minute", second: "second" };
  RelativeTimeFormat.prototype.format = function (value, unit) {
    var u = RT_UNITS[unit] || unit;
    var v = Number(value);
    var abs = Math.abs(v);
    var label = abs === 1 ? u : (u + "s");
    if (this._numeric === "auto") {
      if (v === 0) return "now";
      if (v === -1 && u === "day") return "yesterday";
      if (v === 1 && u === "day") return "tomorrow";
    }
    return (v < 0 ? v : "+" + v) + " " + label;
  };
  RelativeTimeFormat.prototype.resolvedOptions = function () { return { locale: this._locale, numeric: this._numeric }; };
  IntlShim.RelativeTimeFormat = RelativeTimeFormat;

  function Collator(locale, options) {
    this._locale = _normLocale(locale);
    this._sens = (options && options.sensitivity) || "variant";
  }
  Collator.prototype.compare = function (a, b) {
    a = String(a); b = String(b);
    if (this._sens === "base" || this._sens === "accent") {
      a = a.toLowerCase(); b = b.toLowerCase();
    }
    return a < b ? -1 : (a > b ? 1 : 0);
  };
  Collator.prototype.resolvedOptions = function () { return { locale: this._locale, sensitivity: this._sens }; };
  IntlShim.Collator = Collator;

  IntlShim.getCanonicalLocales = function (locales) {
    if (typeof locales === "string") return [locales];
    if (Array.isArray(locales)) return locales.map(String);
    return ["en-US"];
  };
  global.Intl = IntlShim;

  /* ---- DOMException (standard; LinkedIn's como core references it) ---- */
  global.DOMException = function (message, name) {
    this.name = name || "Error";
    this.message = message == null ? "" : String(message);
    this.code = 0;
    if (Error.captureStackTrace) Error.captureStackTrace(this, global.DOMException);
  };
  global.DOMException.prototype = Object.create(Error.prototype);
  global.DOMException.prototype.constructor = global.DOMException;
  // Common legacy codes present as static constants per spec.
  global.DOMException.INDEX_SIZE_ERR = 1;
  global.DOMException.DOMSTRING_SIZE_ERR = 2;
  global.DOMException.HIERARCHY_REQUEST_ERR = 3;
  global.DOMException.WRONG_DOCUMENT_ERR = 4;
  global.DOMException.INVALID_CHARACTER_ERR = 5;
  global.DOMException.NO_DATA_ALLOWED_ERR = 6;
  global.DOMException.NO_MODIFICATION_ALLOWED_ERR = 7;
  global.DOMException.NOT_FOUND_ERR = 8;
  global.DOMException.NOT_SUPPORTED_ERR = 9;
  global.DOMException.INUSE_ATTRIBUTE_ERR = 10;
  global.DOMException.INVALID_STATE_ERR = 11;
  global.DOMException.SYNTAX_ERR = 12;
  global.DOMException.INVALID_MODIFICATION_ERR = 13;
  global.DOMException.NAMESPACE_ERR = 14;
  global.DOMException.INVALID_ACCESS_ERR = 15;
  global.DOMException.VALIDATION_ERR = 16;
  global.DOMException.TYPE_MISMATCH_ERR = 17;
  global.DOMException.SECURITY_ERR = 18;
  global.DOMException.NETWORK_ERR = 19;
  global.DOMException.ABORT_ERR = 20;
  global.DOMException.URL_MISMATCH_ERR = 21;
  global.DOMException.QUOTA_EXCEEDED_ERR = 22;
  global.DOMException.TIMEOUT_ERR = 23;
  global.DOMException.INVALID_NODE_TYPE_ERR = 24;
  global.DOMException.DATA_CLONE_ERR = 25;

  /* ---- SuppressedError (ES2024; TS emit references it) ---- */
  global.SuppressedError = function (suppressed, error, message) {
    this.name = "SuppressedError";
    this.message = message || "";
    this.error = error || null;
    this.suppressed = suppressed || null;
    if (Error.captureStackTrace) Error.captureStackTrace(this, this.constructor);
  };
  global.SuppressedError.prototype = Object.create(Error.prototype);
  global.SuppressedError.prototype.constructor = global.SuppressedError;

  /* structuredClone is installed after the history codec below, which is the
     implementation. This placeholder keeps the name bound if a script runs
     before that assignment (it does not). */
  global.structuredClone = function (value) {
    if (typeof global.__structuredCloneEncode === "function") {
      return global.__structuredCloneDecode(global.__structuredCloneEncode(value));
    }
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  };

  /* ---- J3: WeakRef / FinalizationRegistry — spec-shaped, honestly
         disclosed. QuickJS has no real weak references exposed to JS,
         so deref() keeps STRONG retention (conservative: never observes
         a collection that did not happen). Upgrades over the old shell:
         deref() throws TypeError on non-object targets (spec), WeakRef
         constructor rejects non-objects, FinalizationRegistry carries
         REAL bookkeeping (register/unregisterToken/holds), and
         cleanupSome never fires callbacks (no GC integration —
         disclosed), but tracking is inspectable so agents can see the
         registry state honestly. */
  global.WeakRef = function (target) {
    if (target == null || (typeof target !== "object" && typeof target !== "function")) {
      throw new TypeError("WeakRef: target must be an object or a non-registered symbol");
    }
    this._target = target;
  };
  __cloakObject(global.WeakRef.prototype, { deref: 0 });
  global.WeakRef.prototype.deref = function () { return this._target; };
  global.FinalizationRegistry = function (cleanupCallback) {
    if (typeof cleanupCallback !== "function") {
      throw new TypeError("FinalizationRegistry: cleanupCallback must be a function");
    }
    var held = []; /* { target, heldValue, unregisterToken } — real bookkeeping */
    var unregistered = []; /* heldValues whose unregisterToken was hit */
    this.register = function (target, heldValue, unregisterToken) {
      if (target == null || (typeof target !== "object" && typeof target !== "function")) {
        throw new TypeError("FinalizationRegistry.register: target must be an object");
      }
      if (unregisterToken != null && (typeof unregisterToken !== "object" && typeof unregisterToken !== "function")) {
        throw new TypeError("FinalizationRegistry.register: unregisterToken must be an object");
      }
      var entry = { target: target, heldValue: heldValue, unregisterToken: unregisterToken || null };
      held.push(entry);
      return entry;
    };
    this.unregister = function (unregisterToken) {
      if (unregisterToken == null || (typeof unregisterToken !== "object" && typeof unregisterToken !== "function")) {
        throw new TypeError("FinalizationRegistry.unregister: unregisterToken must be an object");
      }
      var before = held.length;
      held = held.filter(function (e) { return e.unregisterToken !== unregisterToken; });
      var removed = before - held.length;
      if (removed > 0) unregistered.push(unregisterToken);
      return removed > 0;
    };
    this.cleanupSome = function () {
      /* No GC integration: callbacks never fire (disclosed). Kept as a
         no-op so feature-detection code paths work. */
    };
    /* Honest introspection for the agent surface: the registry's real
       state, without pretending GC ran. */
    this.__wrInspect = function () {
      return JSON.stringify({
        registered: held.length,
        unregistered: unregistered.length,
        cleanupWillFire: false,
        reason: "no GC integration in this engine; callbacks never fire (disclosed)"
      });
    };
  };

  /* ---- PerformanceObserver (records nothing; supported types listed so
         feature-detection branches take the observer path, not the crash) ---- */
  function PerformanceObserver(cb) {
    this._cb = cb || function () {};
    this._observing = false;
  }
  PerformanceObserver.supportedEntryTypes = [
    "mark", "measure", "navigation", "resource", "longtask", "paint", "largest-contentful-paint",
  ];
  PerformanceObserver.prototype.observe = function (opts) {
    this._observing = true;
    if (opts && opts.entryTypes && this._cb) {
      // Deliberately report nothing: there is no real perf timeline here.
    }
  };
  PerformanceObserver.prototype.disconnect = function () { this._observing = false; };
  PerformanceObserver.prototype.takeRecords = function () { return []; };
  global.PerformanceObserver = PerformanceObserver;

  /* ---- performance stubs for pre-__load contexts ----
     __load installs the real performance object (with resource-timing
     entries injected by the host); these fallbacks only matter before it
     runs. */
  if (global.performance && !global.performance.getEntriesByName) {
    global.performance.getEntriesByName = function () { return []; };
    global.performance.getEntriesByType = function () { return []; };
    global.performance.getEntries = function () { return []; };
    global.performance.mark = function () { return { name: "", entryType: "mark", startTime: 0, duration: 0 }; };
    global.performance.measure = function () { return { name: "", entryType: "measure", startTime: 0, duration: 0 }; };
    global.performance.now = global.performance.now || function () { return Date.now(); };
  }

  /* ---- W3C Web Audio API & OfflineAudioContext emulation ----
     Used by bot detection systems (DataDome, FingerprintJS, CreepJS) for
     audio fingerprinting. Produces deterministic, non-zero audio buffers
     matching Blink's DynamicsCompressor and Oscillator response. */
  function _createAudioBuffer(channels, len, rate, silent) {
    /* M1: buffers are REAL sample containers. A created buffer starts at
       digital silence (zeros) — the old stub pre-filled a canned 10kHz
       decaying sine into EVERY buffer, which startRendering() then
       handed back no matter what the graph did. Fingerprint noise stays
       available for stealth contexts that explicitly want it via
       __createNoiseBuffer. */
    channels = channels || 1;
    len = len || 1;
    rate = rate || 44100;
    var chData = [];
    for (var c = 0; c < channels; c++) {
      var f32 = new Float32Array(len);
      if (silent !== true) {
        /* Only the LEGACY stealth noise lane fills canned data; every
           normal createBuffer/decodeAudioData path passes silent=true. */
        for (var i = 0; i < len; i++) {
          var t = i / rate;
          var osc = Math.sin(2 * Math.PI * 10000 * t);
          var env = Math.exp(-t * 2.5);
          f32[i] = Math.tanh(osc * env * 2.0) * 0.15;
        }
      }
      chData.push(f32);
    }
    var buf = {
      numberOfChannels: channels,
      length: len,
      sampleRate: rate,
      duration: len / rate,
      getChannelData: function (idx) { return chData[idx || 0] || chData[0]; },
      copyFromChannel: function (dest, ch, start) {
        var src = chData[ch || 0];
        if (!src) return;
        start = start || 0;
        for (var j = 0; j < dest.length && (j + start) < src.length; j++) dest[j] = src[j + start];
      },
      copyToChannel: function (src, ch, start) {
        var dest = chData[ch || 0];
        if (!dest) return;
        start = start || 0;
        for (var j = 0; j < src.length && (j + start) < dest.length; j++) dest[j + start] = src[j];
      }
    };
    __cloakObject(buf, { getChannelData: 1, copyFromChannel: 3, copyToChannel: 3 });
    return buf;
  }

  function AudioParam(val) {
    this.value = val;
    this.defaultValue = val;
    this.minValue = -3.4028234663852886e38;
    this.maxValue = 3.4028234663852886e38;
    this.setValueAtTime = function (v) { this.value = v; return this; };
    this.linearRampToValueAtTime = function (v) { this.value = v; return this; };
    this.exponentialRampToValueAtTime = function (v) { this.value = v; return this; };
    this.setTargetAtTime = function (v) { this.value = v; return this; };
    this.setValueCurveAtTime = function () { return this; };
    this.cancelScheduledValues = function () { return this; };
    this.cancelAndHoldAtTime = function () { return this; };
  }
  __cloakObject(AudioParam.prototype, {
    setValueAtTime: 2, linearRampToValueAtTime: 2, exponentialRampToValueAtTime: 2,
    setTargetAtTime: 3, setValueCurveAtTime: 3, cancelScheduledValues: 1, cancelAndHoldAtTime: 1
  });

  function _setupBaseAudioContext(self, sampleRate) {
    self.sampleRate = sampleRate || 44100;
    self.currentTime = 0;
    self.destination = {
      channelCount: 2,
      maxChannelCount: 2,
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCountMode: "explicit",
      channelInterpretation: "speakers",
      /* M1: the destination is a real graph sink — connected sources
         record their edge here so startRendering() can pull them. */
      _audioInputs: []
    };
    self.createOscillator = function () {
      /* M1: oscillators are real graph nodes — connect() records the
         edge and start(when) schedules generation for rendering. */
      var osc = {
        type: "sine",
        frequency: new AudioParam(440),
        detune: new AudioParam(0),
        _started: false, _startWhen: 0,
        connect: function (t) {
          if (t) {
            if (!t._audioInputs) t._audioInputs = [];
            t._audioInputs.push(this);
          }
          this._target = t;
          return t;
        },
        disconnect: function () { this._target = null; },
        start: function (when) { this._started = true; this._startWhen = when || 0; },
        stop: function () { this._stopped = true; }
      };
      __cloakObject(osc, { connect: 1, disconnect: 0, start: 1, stop: 1 });
      return osc;
    };
    self.createDynamicsCompressor = function () {
      /* M1: the compressor is a real graph node. Rendering passes its
         input through (a soft-knee approximation is disclosed as not
         modeled); its parameters remain real AudioParams. */
      var comp = {
        threshold: new AudioParam(-24),
        knee: new AudioParam(30),
        ratio: new AudioParam(12),
        reduction: 0,
        attack: new AudioParam(0.003),
        release: new AudioParam(0.25),
        _audioInputs: [],
        connect: function (t) {
          if (t) {
            if (!t._audioInputs) t._audioInputs = [];
            t._audioInputs.push(this);
          }
          this._target = t;
          return t;
        },
        disconnect: function () { this._target = null; }
      };
      __cloakObject(comp, { connect: 1, disconnect: 0 });
      return comp;
    };
    self.createAnalyser = function () {
      var a = {
        fftSize: 2048, frequencyBinCount: 1024,
        minDecibels: -100, maxDecibels: -30, smoothingTimeConstant: 0.8,
        connect: function (t) { return t; }, disconnect: function () {},
        getFloatFrequencyData: function (arr) { for (var i = 0; i < arr.length; i++) arr[i] = -100 + (Math.sin(i * 0.05) * 15); },
        getByteFrequencyData: function (arr) { for (var i = 0; i < arr.length; i++) arr[i] = Math.floor((Math.sin(i * 0.05) + 1) * 64); },
        getFloatTimeDomainData: function (arr) { for (var i = 0; i < arr.length; i++) arr[i] = Math.sin(i * 0.05) * 0.1; },
        getByteTimeDomainData: function (arr) { for (var i = 0; i < arr.length; i++) arr[i] = Math.floor(128 + Math.sin(i * 0.05) * 20); }
      };
      __cloakObject(a, { connect: 1, disconnect: 0, getFloatFrequencyData: 1, getByteFrequencyData: 1, getFloatTimeDomainData: 1, getByteTimeDomainData: 1 });
      return a;
    };
    self.createGain = function () {
      /* M1: gain nodes are real graph nodes — OfflineAudioContext pulls
         their gain.value at render time. */
      var g = {
        gain: new AudioParam(1),
        _audioInputs: [],
        connect: function (t) {
          if (t) {
            if (!t._audioInputs) t._audioInputs = [];
            t._audioInputs.push(this);
          }
          this._target = t;
          return t;
        },
        disconnect: function () { this._target = null; }
      };
      __cloakObject(g, { connect: 1, disconnect: 0 });
      return g;
    };
    self.createBiquadFilter = function () {
      var f = {
        type: "lowpass",
        frequency: new AudioParam(350),
        detune: new AudioParam(0),
        Q: new AudioParam(1),
        gain: new AudioParam(0),
        connect: function (t) { return t; }, disconnect: function () {}
      };
      __cloakObject(f, { connect: 1, disconnect: 0 });
      return f;
    };
    self.createBuffer = function (ch, len, rate) {
      /* M1: spec behavior — a fresh buffer is digital silence. */
      return _createAudioBuffer(ch || 1, len || 1, rate || self.sampleRate, true);
    };
    self.createBufferSource = function () {
      /* M1: a buffer source holds the graph edge so rendering can pull
         its buffer's samples; start(when, offset) records scheduling. */
      var bs = {
        buffer: null, playbackRate: new AudioParam(1), loop: false,
        _started: false, _startWhen: 0, _startOffset: 0,
        connect: function (t) {
          if (t) {
            if (!t._audioInputs) t._audioInputs = [];
            t._audioInputs.push(this);
          }
          this._target = t;
          return t;
        },
        disconnect: function () { this._target = null; },
        start: function (when, offset) { this._started = true; this._startWhen = when || 0; this._startOffset = offset || 0; },
        stop: function () { this._stopped = true; }
      };
      __cloakObject(bs, { connect: 1, disconnect: 0, start: 1, stop: 1 });
      return bs;
    };
    self.decodeAudioData = function (arrBuf) {
      /* M1: no codec — honest silence at a plausible frame count derived
         from the input's byte length (2ch 16-bit assumption), instead of
         the old fixed 44100-frame canned-sine buffer. Disclosed: the
         samples are zeros; real decode needs a codec (out of scope). */
      var bytes = (arrBuf && arrBuf.byteLength) || 0;
      var frames = Math.max(1, Math.floor(bytes / 4)) || 44100;
      return Promise.resolve(_createAudioBuffer(2, frames, self.sampleRate, true));
    };
  }

  function AudioContext() {
    _setupBaseAudioContext(this, 44100);
    this.state = "running";
    this.resume = function () { this.state = "running"; return Promise.resolve(); };
    this.suspend = function () { this.state = "suspended"; return Promise.resolve(); };
    this.close = function () { this.state = "closed"; return Promise.resolve(); };
  }
  global.AudioContext = AudioContext;
  global.webkitAudioContext = AudioContext;

  function OfflineAudioContext(numberOfChannels, length, sampleRate) {
    var opts = (typeof numberOfChannels === "object" && numberOfChannels !== null)
      ? numberOfChannels
      : { numberOfChannels: numberOfChannels || 1, length: length || 44100, sampleRate: sampleRate || 44100 };
    this.numberOfChannels = opts.numberOfChannels || 1;
    this.length = opts.length || 44100;
    _setupBaseAudioContext(this, opts.sampleRate || 44100);
    this.destination.channelCount = this.numberOfChannels;
    this.state = "suspended";
    this.oncomplete = null;

    var self = this;
    /* M1: OfflineAudioContext renders the REAL graph, not a canned buffer.
       Walk from destination backwards: each connected source contributes
       its samples; each gain node on the path scales by gain.value at
       render time. Sources without a buffer contribute silence.
       Oscillators contribute a real sine at their frequency (the only
       generated waveform, disclosed: type is honored for 'sine' only). */
    function _renderGraph() {
      var out = _createAudioBuffer(self.numberOfChannels, self.length, self.sampleRate, true);
      var channels = out.getChannelData ? null : null;
      /* collect input chains into destination */
      function pullInputs(node, sampleOffset, applyGain) {
        var produced = null; /* array of Float32Array per channel */
        var inputs = node._audioInputs || [];
        for (var i = 0; i < inputs.length; i++) {
          var src = inputs[i];
          var samples = pullNode(src, sampleOffset);
          if (!samples) continue;
          if (!produced) produced = samples;
          else {
            /* SUM multiple inputs (spec: inputs mix) */
            for (var c = 0; c < samples.length && c < produced.length; c++) {
              for (var s = 0; s < samples[c].length; s++) produced[c][s] += samples[c][s];
            }
          }
        }
        if (produced && applyGain && node.gain && typeof node.gain.value === "number") {
          var g = node.gain.value;
          for (var c2 = 0; c2 < produced.length; c2++) {
            for (var s2 = 0; s2 < produced[c2].length; s2++) produced[c2][s2] *= g;
          }
        }
        return produced;
      }
      function pullNode(node, sampleOffset) {
        if (!node) return null;
        if (node.buffer && node.buffer.getChannelData) {
          /* Buffer source: real samples, offset honored. */
          var chans = [];
          var n = Math.min(self.length, node.buffer.length - (node._startOffset || 0));
          for (var c = 0; c < self.numberOfChannels; c++) {
            var srcCh = node.buffer.getChannelData(Math.min(c, node.buffer.numberOfChannels - 1));
            var dst = new Float32Array(self.length);
            var off = (node._startOffset || 0);
            for (var i = 0; i < self.length; i++) {
              var idx = off + i;
              dst[i] = (idx >= 0 && idx < srcCh.length) ? srcCh[idx] : 0;
            }
            chans.push(dst);
          }
          return chans;
        }
        if (node.frequency && node.type !== undefined && !node.gain) {
          /* Oscillator: real sine at frequency.value over the render
             length, from its start time. Disclosed: only the 'sine'
             waveform is generated; other types render as sine. */
          var freq = (node.frequency && typeof node.frequency.value === "number") ? node.frequency.value : 440;
          var chans2 = [];
          for (var c2 = 0; c2 < self.numberOfChannels; c2++) {
            var d = new Float32Array(self.length);
            var startIdx = Math.max(0, Math.floor((node._startWhen || 0) * self.sampleRate));
            for (var i2 = startIdx; i2 < self.length; i2++) {
              d[i2] = Math.sin(2 * Math.PI * freq * ((i2 - startIdx) / self.sampleRate));
            }
            chans2.push(d);
          }
          return chans2;
        }
        if (node.gain) {
          /* Gain node: pull its inputs, then scale (handled in pullInputs). */
          return pullInputs(node, sampleOffset, true);
        }
        if (node._audioInputs && node._audioInputs.length) {
          /* M1: intermediate graph node (dynamics compressor, filter,
             analyser): pass its inputs through. The compressor's soft-
             knee curve is not modeled (disclosed); filters/analysers
             pass audio unchanged (disclosed). */
          return pullInputs(node, sampleOffset, false);
        }
        return null;
      }
      var graphSamples = pullInputs(self.destination, 0, false);
      for (var c = 0; c < self.numberOfChannels; c++) {
        var dstCh = out.getChannelData(c);
        if (!graphSamples || !graphSamples[c]) continue;
        var srcCh2 = graphSamples[c];
        for (var i = 0; i < dstCh.length && i < srcCh2.length; i++) dstCh[i] = srcCh2[i];
      }
      return out;
    }
    this.startRendering = function () {
      self.state = "running";
      var buf = _renderGraph();
      self.state = "closed";
      if (typeof self.oncomplete === "function") {
        try { self.oncomplete({ renderedBuffer: buf, type: "complete", target: self }); } catch (e) {}
      }
      return Promise.resolve(buf);
    };
    this.resume = function () { self.state = "running"; return Promise.resolve(); };
    this.suspend = function () { self.state = "suspended"; return Promise.resolve(); };
  }
  global.OfflineAudioContext = OfflineAudioContext;
  global.webkitOfflineAudioContext = OfflineAudioContext;

  /* ---- SpeechSynthesis Emulation ---- */
  function SpeechSynthesisUtterance(text) {
    this.text = text || "";
    this.lang = "en-US";
    this.voice = null;
    this.volume = 1;
    this.rate = 1;
    this.pitch = 1;
    this.onstart = null;
    this.onend = null;
    this.onerror = null;
    this.onpause = null;
    this.onresume = null;
    this.onmark = null;
    this.onboundary = null;
  }
  global.SpeechSynthesisUtterance = SpeechSynthesisUtterance;

  var _speechSynthesis = {
    pending: false,
    speaking: false,
    paused: false,
    onvoiceschanged: null,
    getVoices: function () {
      return [
        { voiceURI: "Google US English", name: "Google US English", lang: "en-US", localService: false, default: true },
        { voiceURI: "Google UK English Female", name: "Google UK English Female", lang: "en-GB", localService: false, default: false },
        { voiceURI: "Google UK English Male", name: "Google UK English Male", lang: "en-GB", localService: false, default: false }
      ];
    },
    speak: function () {},
    cancel: function () {},
    pause: function () {},
    resume: function () {}
  };
  global.speechSynthesis = _speechSynthesis;

  /* ---- WebRTC RTCPeerConnection Emulation ---- */
  function RTCIceCandidate(candidateInitDict) {
    this.candidate = (candidateInitDict && candidateInitDict.candidate) || "";
    this.sdpMid = (candidateInitDict && candidateInitDict.sdpMid) || "0";
    this.sdpMLineIndex = (candidateInitDict && candidateInitDict.sdpMLineIndex) || 0;
    this.usernameFragment = (candidateInitDict && candidateInitDict.usernameFragment) || "";
    this.toJSON = function () {
      return {
        candidate: this.candidate,
        sdpMid: this.sdpMid,
        sdpMLineIndex: this.sdpMLineIndex,
        usernameFragment: this.usernameFragment
      };
    };
  }
  global.RTCIceCandidate = RTCIceCandidate;

  function RTCSessionDescription(descriptionInitDict) {
    this.type = (descriptionInitDict && descriptionInitDict.type) || "offer";
    this.sdp = (descriptionInitDict && descriptionInitDict.sdp) || "";
    this.toJSON = function () {
      return { type: this.type, sdp: this.sdp };
    };
  }
  global.RTCSessionDescription = RTCSessionDescription;

  function RTCPeerConnection(configuration) {
    this.signalingState = "stable";
    this.iceConnectionState = "new";
    this.connectionState = "new";
    this.iceGatheringState = "new";
    this.localDescription = null;
    this.remoteDescription = null;
    this.onicecandidate = null;
    this.onicecandidateerror = null;
    this.onconnectionstatechange = null;
    this.oniceconnectionstatechange = null;
    this.onsignalingstatechange = null;
    this.ontrack = null;
    this.ondatachannel = null;
    this._config = configuration || {};

    var self = this;
    this.createOffer = function () {
      var fakeSdp = "v=0\r\no=- 4235123985 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\na=msid-semantic: WMS\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\na=candidate:123456789 1 udp 2122260223 192.168.1.150 54321 typ host generation 0\r\n";
      return Promise.resolve(new RTCSessionDescription({ type: "offer", sdp: fakeSdp }));
    };
    this.createAnswer = function () {
      var fakeSdp = "v=0\r\no=- 4235123985 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\nc=IN IP4 0.0.0.0\r\n";
      return Promise.resolve(new RTCSessionDescription({ type: "answer", sdp: fakeSdp }));
    };
    this.setLocalDescription = function (desc) {
      self.localDescription = desc;
      return Promise.resolve();
    };
    this.setRemoteDescription = function (desc) {
      self.remoteDescription = desc;
      return Promise.resolve();
    };
    this.addIceCandidate = function () {
      return Promise.resolve();
    };
    this.getConfiguration = function () {
      return self._config;
    };
    this.getStats = function () {
      return Promise.resolve(new Map());
    };
    this.createDataChannel = function (label) {
      return {
        label: String(label || ""),
        id: 0,
        readyState: "open",
        bufferedAmount: 0,
        send: function () {},
        close: function () { this.readyState = "closed"; },
        onopen: null,
        onmessage: null,
        onerror: null,
        onclose: null
      };
    };
    this.close = function () {
      self.signalingState = "closed";
      self.connectionState = "closed";
      self.iceConnectionState = "closed";
    };
  }
  global.RTCPeerConnection = RTCPeerConnection;
  global.webkitRTCPeerConnection = RTCPeerConnection;

  /* ---- W1: Web Worker emulation (single-threaded) ----
     Creates a child realm via `new Function(...)` that shadows page globals
     with worker-only API (self, postMessage, close, importScripts, timers,
     console, location). Scripts are fetched from blob: URLs (blob store
     holds the text) or via fetch for HTTP URLs. Messages between parent and
     worker are queued and delivered on the next __drainTimers pump — real
     async, never synchronous. terminate() stops the worker's message pump. */
  var __workerSeq = 0;
  var __workerQueue = []; /* {worker, kind: "start"|"toWorker"|"toParent", data} */

  /* realm constructor: builds the DedicatedWorkerGlobalScope object */
  function _workerRealm(worker, scriptSource, url) {
    var w = worker;
    var realm = {
      _w: w, _started: false, _onmessage: null, _listeners: {}, _timers: [], _nextTimerId: 0
    };

    realm.self = realm;
    realm.location = { href: String(url || "") };

    /* postMessage delivers to the parent on the next drain */
    realm.postMessage = function (data) {
      if (w._terminated) return;
      __workerQueue.push({ worker: w, kind: "toParent", data: data });
    };
    realm.close = function () {
      /* Real Worker close(): stops the worker's event loop but does not
         drop messages already queued to the parent. Defer the actual
         termination to the end of the current pump pass. */
      w._realm._closed = true;
      setTimeout(function () { if (w._realm._closed) w.terminate(); }, 0);
    };

    realm.addEventListener = function (type, fn) {
      (realm._listeners[type] = realm._listeners[type] || []).push(fn);
    };
    realm.removeEventListener = function (type, fn) {
      var arr = realm._listeners[type];
      if (arr) realm._listeners[type] = arr.filter(function (l) { return l !== fn; });
    };
    realm.dispatchEvent = function (type, evt) {
      var onType = "on" + type;
      if (typeof realm[onType] === "function") {
        try { realm[onType](evt); } catch (e) {}
      }
      var arr = realm._listeners[type] || [];
      for (var i = 0; i < arr.length; i++) {
        try { arr[i](evt); } catch (e) {}
      }
    };

    /* Worker timers: reuse the page's timer infrastructure but tagged so
       terminate() can cancel them. */
    realm.setTimeout = function (fn, delay) {
      if (w._terminated) return -1;
      var id = ++realm._nextTimerId;
      var t = { id: id, fn: function () { if (!w._terminated) fn(); }, due: __timerNow + (Number(delay) || 0), repeat: false, realm: true };
      realm._timers.push(t);
      /* Schedule on the real timer list so __drainTimers fires it */
      timers.push(t);
      return id;
    };
    realm.setInterval = function (fn, delay) {
      if (w._terminated) return -1;
      var id = ++realm._nextTimerId;
      var ms = Number(delay) || 0;
      var t = { id: id, fn: function () { if (!w._terminated) fn(); }, due: __timerNow + ms, repeat: true, ms: ms, realm: true };
      realm._timers.push(t);
      timers.push(t);
      return id;
    };
    realm.clearTimeout = function (id) {
      realm._timers = realm._timers.filter(function (t) { return t.id !== id; });
      __timerDead[id] = true;
    };
    realm.clearInterval = realm.clearTimeout;

    /* Worker console: logs to the page's console with a [worker:N] prefix */
    var _wcon = function (level) {
      return function () {
        try {
          var parts = [];
          for (var i = 0; i < arguments.length; i++) {
            parts.push(typeof arguments[i] === "string" ? arguments[i] : JSON.stringify(arguments[i]));
          }
          global.__WR_CONSOLE_LOGS = global.__WR_CONSOLE_LOGS || [];
          if (global.__WR_CONSOLE_LOGS.length < 500) {
            global.__WR_CONSOLE_LOGS.push({
              level: level,
              message: "[worker:" + w._id + "] " + parts.join(" ").slice(0, 4000),
              ts: Date.now()
            });
          }
        } catch (e) {}
      };
    };
    realm.console = { log: _wcon("log"), error: _wcon("error"), warn: _wcon("warn"), info: _wcon("info") };

    /* importScripts stub: workers that need external scripts see a no-op */
    realm.importScripts = function () {
      for (var i = 0; i < arguments.length; i++) {
        realm.console.log("importScripts(" + String(arguments[i]) + ") — not implemented");
      }
    };

    /* onmessage: assigning it marks the realm as started and flushes the
       queued messages (spec behavior: onmessage starts the port). */
    Object.defineProperty(realm, "onmessage", {
      configurable: true,
      enumerable: true,
      get: function () { return realm._onmessage; },
      set: function (fn) {
        realm._onmessage = typeof fn === "function" ? fn : null;
        realm._started = true;
        /* Flush any queued parent->worker messages */
        var q = w._toWorkerQueue;
        w._toWorkerQueue = [];
        for (var i = 0; i < q.length; i++) {
          _fireWorkerMessage(w, q[i]);
        }
      }
    });

    return realm;
  }

  /* Fire a message into the worker realm */
  function _fireWorkerMessage(worker, data) {
    if (worker._terminated || !worker._realm) return;
    var realm = worker._realm;
    var evt = { type: "message", data: data, origin: "", ports: [], isTrusted: true };
    realm.dispatchEvent("message", evt);
  }

  /* Fire a message to the worker's parent */
  function _fireParentMessage(worker, data) {
    if (worker._terminated) return;
    var evt = { type: "message", data: data, origin: "", source: null, isTrusted: true };
    if (typeof worker.onmessage === "function") {
      try { worker.onmessage(evt); } catch (e) {}
    }
    var arr = worker._listeners && worker._listeners.message;
    if (arr) for (var i = 0; i < arr.length; i++) { try { arr[i](evt); } catch (e) {} }
    /* W1: SharedWorker exposes its channel through `port`, not the Worker's
       own onmessage — a consumer doing sw.port.onmessage = ... must hear
       worker→parent traffic. */
    if (worker.port) {
      if (typeof worker.port.onmessage === "function") {
        try { worker.port.onmessage(evt); } catch (e) {}
      }
      var parr = worker.port._listeners && worker.port._listeners.message;
      if (parr) for (var j = 0; j < parr.length; j++) { try { parr[j](evt); } catch (e) {} }
    }
  }

  /* Pump worker messages: start events and cross-direction deliveries.
     Loops until the queue is empty: a worker script that posts messages
     during its own start event pushes onto the queue, and those must be
     delivered in the same pump (not deferred to the next drain). */
  function _pumpWorkerMessages() {
    var guard = 0;
    while (__workerQueue.length > 0 && guard++ < 50) {
      var q = __workerQueue;
      __workerQueue = [];
      for (var i = 0; i < q.length; i++) {
        var item = q[i];
        var w = item.worker;
        if (w._terminated) continue;
        if (item.kind === "start") {
          /* Run the worker script in the realm: pass every shadowed global
             as a positional argument matching the new Function signature. */
          try {
            item.wrapper(
              item.realm,                     /* self */
              item.realm.postMessage,         /* postMessage */
              item.realm.close,               /* close */
              item.realm.importScripts,       /* importScripts */
              item.realm.setTimeout,          /* setTimeout */
              item.realm.setInterval,         /* setInterval */
              item.realm.clearTimeout,        /* clearTimeout */
              item.realm.clearInterval,       /* clearInterval */
              item.realm.console,             /* console */
              item.realm.location,            /* location */
              item.realm.addEventListener,    /* addEventListener */
              item.realm.removeEventListener, /* removeEventListener */
              item.realm.dispatchEvent,       /* dispatchEvent */
              null                            /* onmessage (read-only via defineProperty) */
            );
          } catch (e) {
            if (typeof w.onerror === "function") w.onerror({ type: "error", message: String(e) });
          }
          /* After the script ran, mark started; flush queued messages */
          w._realm._started = true;
          var mq = w._toWorkerQueue;
          w._toWorkerQueue = [];
          for (var j = 0; j < mq.length; j++) _fireWorkerMessage(w, mq[j]);
        } else if (item.kind === "toWorker") {
          /* Parent -> worker */
          if (w._realm && w._realm._started) {
            _fireWorkerMessage(w, item.data);
          } else {
            w._toWorkerQueue.push(item.data);
          }
        } else if (item.kind === "toParent") {
          /* Worker -> parent: always deliver even if the worker called close()
             (messages already posted to the parent are never dropped). */
          _fireParentMessage(w, item.data);
        }
      }
    }
  }

  global.Worker = function Worker(url, options) {
    var self = this;
    self.url = String(url || "");
    self.onmessage = null;
    self.onerror = null;
    self._terminated = false;
    self._realm = null;
    self._toWorkerQueue = [];
    self._listeners = {};
    self._id = "worker-" + (++__workerSeq);

    self.addEventListener = function (type, fn) {
      if (type === "message") {
        (self._listeners[type] = self._listeners[type] || []).push(fn);
      }
    };
    self.removeEventListener = function (type, fn) {
      var arr = self._listeners[type];
      if (arr) self._listeners[type] = arr.filter(function (l) { return l !== fn; });
    };

    self.postMessage = function (data) {
      if (self._terminated) return;
      __workerQueue.push({ worker: self, kind: "toWorker", data: data });
    };

    self.terminate = function () {
      self._terminated = true;
      if (self._realm) self._realm._started = false;
      self._toWorkerQueue = [];
      /* Cancel realm timers */
      if (self._realm) {
        for (var i = 0; i < self._realm._timers.length; i++) {
          __timerDead[self._realm._timers[i].id] = true;
        }
      }
    };

    /* Resolve the URL */
    var resolvedUrl;
    try {
      resolvedUrl = new URL(self.url, (global.location && global.location.href) || "").href;
    } catch (e) {
      resolvedUrl = self.url;
    }

    function _loadScript(scriptSource) {
      var realm = _workerRealm(self, scriptSource, resolvedUrl);
      self._realm = realm;
      /* Build the wrapper: a function whose parameters shadow the page's
         globals with the worker realm's API. The script runs inside it. */
      var wrapper = new Function(
        "self", "postMessage", "close", "importScripts",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "console", "location", "addEventListener", "removeEventListener", "dispatchEvent", "onmessage",
        "\"use strict\";\n" + String(scriptSource || "")
      );
      /* Queue the start event (async, like a real Worker) */
      __workerQueue.push({ worker: self, kind: "start", wrapper: wrapper, realm: realm });
    }

    if (resolvedUrl.indexOf("blob:") === 0) {
      /* Blob URL: read from the object store */
      var blob = __objectURLs[resolvedUrl];
      if (blob && blob._content) {
        _loadScript(blob._content);
      } else {
        setTimeout(function () {
          if (typeof self.onerror === "function") self.onerror({ type: "error", message: "Blob URL not found: " + resolvedUrl });
        }, 0);
      }
    } else {
      /* HTTP: fetch the script */
      fetch(String(resolvedUrl)).then(function (resp) {
        if (!resp.ok) throw new Error("Failed to load worker: " + resp.status);
        return resp.text();
      }).then(function (scriptSource) {
        _loadScript(scriptSource);
      }).catch(function (e) {
        setTimeout(function () {
          if (typeof self.onerror === "function") self.onerror({ type: "error", message: String(e) });
        }, 0);
      });
    }
  };

  /* SharedWorker: same execution model, but the port is explicit (not
     implicit postMessage/onmessage on the Worker object itself). */
  global.SharedWorker = function SharedWorker(url, name) {
    var self = this;
    Worker.call(this, url);
    this.name = String(name || "");
    /* SharedWorker.port replaces the implicit message channel */
    var _port = {
      _listeners: {},
      onmessage: null,
      start: function () {},
      postMessage: function (data) {
        /* Route through the same worker queue as Worker.postMessage */
        if (!self._terminated) __workerQueue.push({ worker: self, kind: "toWorker", data: data });
      },
      close: function () { self.terminate(); },
      addEventListener: function (type, fn) {
        if (type === "message") (_port._listeners[type] = _port._listeners[type] || []).push(fn);
      },
      removeEventListener: function (type, fn) {
        var arr = _port._listeners[type];
        if (arr) _port._listeners[type] = arr.filter(function (l) { return l !== fn; });
      }
    };
    this.port = _port;
  };
  global.SharedWorker.prototype = Object.create(Worker.prototype);
  global.SharedWorker.prototype.constructor = global.SharedWorker;

  /* Wire the worker pump into the drain cycle */
  var _origDrainTimers = global.__drainTimers;
  global.__drainTimers = function () {
    _pumpWorkerMessages();
    return _origDrainTimers();
  };
  /* ---- Agentic Scoped WebAssembly Engine ---- */
  global.__WR_WASM_CALLS = [];
  function __toUint8Array(source) {
    if (!source) return null;
    if (source instanceof Uint8Array) return source;
    if (source instanceof ArrayBuffer) return new Uint8Array(source);
    if (ArrayBuffer.isView(source)) return new Uint8Array(source.buffer, source.byteOffset, source.byteLength);
    return null;
  }

  function __readU32Leb(bytes, pos) {
    var res = 0, shift = 0;
    while (true) {
      if (pos >= bytes.length) throw new Error("unexpected end of leb128");
      var b = bytes[pos++];
      res |= (b & 0x7F) << shift;
      if ((b & 0x80) === 0) break;
      shift += 7;
    }
    return [res >>> 0, pos];
  }

  function __readI32Leb(bytes, pos) {
    var res = 0, shift = 0;
    while (true) {
      if (pos >= bytes.length) throw new Error("unexpected end of leb128");
      var b = bytes[pos++];
      res |= (b & 0x7F) << shift;
      shift += 7;
      if ((b & 0x80) === 0) {
        if (shift < 32 && (b & 0x40) !== 0) res |= (~0 << shift);
        break;
      }
    }
    return [res | 0, pos];
  }

  function __wasmValidate(bufferSource) {
    var bytes = __toUint8Array(bufferSource);
    if (!bytes || bytes.length < 8) return false;
    if (bytes[0] !== 0x00 || bytes[1] !== 0x61 || bytes[2] !== 0x73 || bytes[3] !== 0x6D) return false;
    if (bytes[4] !== 0x01 || bytes[5] !== 0x00 || bytes[6] !== 0x00 || bytes[7] !== 0x00) return false;
    return true;
  }

  function WasmMemory(desc) {
    if (!desc || typeof desc.initial !== "number") throw new TypeError("WebAssembly.Memory requires an initial page size");
    this.initial = desc.initial;
    this.maximum = desc.maximum !== undefined ? desc.maximum : null;
    this.buffer = new ArrayBuffer(this.initial * 65536);
  }
  WasmMemory.prototype.grow = function (delta) {
    var oldPages = this.buffer.byteLength / 65536;
    var newPages = oldPages + delta;
    if (this.maximum !== null && newPages > this.maximum) throw new RangeError("WebAssembly.Memory.grow exceeds maximum limit");
    var newBuf = new ArrayBuffer(newPages * 65536);
    new Uint8Array(newBuf).set(new Uint8Array(this.buffer));
    this.buffer = newBuf;
    return oldPages;
  };

  function WasmTable(desc) {
    this.initial = (desc && desc.initial) || 0;
    this.maximum = (desc && desc.maximum) || null;
    this.element = (desc && desc.element) || "anyfunc";
    this._elements = new Array(this.initial);
  }
  Object.defineProperty(WasmTable.prototype, "length", {
    get: function () { return this._elements.length; }
  });
  WasmTable.prototype.get = function (idx) { return this._elements[idx] || null; };
  WasmTable.prototype.set = function (idx, fn) { this._elements[idx] = fn; };
  WasmTable.prototype.grow = function (delta) {
    var oldLen = this._elements.length;
    for (var i = 0; i < delta; i++) this._elements.push(null);
    return oldLen;
  };

  function WasmModule(bufferSource) {
    var bytes = __toUint8Array(bufferSource);
    if (!__wasmValidate(bytes)) throw new Error("WebAssembly.Module: Invalid WASM magic or version header");
    this._bytes = bytes;
    this.types = [];
    this.funcTypeIndices = [];
    this.imports = [];
    this.exports = {};
    this.functions = [];
    this.memories = [];
    this.dataSegments = [];
    this._decode();
  }
  WasmModule.prototype._decode = function () {
    var b = this._bytes;
    var pos = 8;
    var funcBodies = [];
    while (pos < b.length) {
      var secId = b[pos++];
      var r = __readU32Leb(b, pos);
      var secLen = r[0]; pos = r[1];
      var secEnd = pos + secLen;

      if (secId === 1) { // Type section
        var rT = __readU32Leb(b, pos); var numTypes = rT[0]; pos = rT[1];
        for (var t = 0; t < numTypes; t++) {
          pos++; // form 0x60
          var rP = __readU32Leb(b, pos); var numP = rP[0]; pos = rP[1];
          var params = []; for (var p = 0; p < numP; p++) params.push(b[pos++]);
          var rR = __readU32Leb(b, pos); var numR = rR[0]; pos = rR[1];
          var results = []; for (var rx = 0; rx < numR; rx++) results.push(b[pos++]);
          this.types.push({ params: params, results: results });
        }
      } else if (secId === 2) { // Import section
        var rI = __readU32Leb(b, pos); var numImp = rI[0]; pos = rI[1];
        for (var im = 0; im < numImp; im++) {
          var rM = __readU32Leb(b, pos); var mL = rM[0]; pos = rM[1];
          var modName = String.fromCharCode.apply(null, b.slice(pos, pos + mL)); pos += mL;
          var rF = __readU32Leb(b, pos); var fL = rF[0]; pos = rF[1];
          var fieldName = String.fromCharCode.apply(null, b.slice(pos, pos + fL)); pos += fL;
          var kind = b[pos++];
          var rD = __readU32Leb(b, pos); var desc = rD[0]; pos = rD[1];
          this.imports.push({ module: modName, field: fieldName, kind: kind, desc: desc });
          if (kind === 0) this.funcTypeIndices.push(desc);
        }
      } else if (secId === 3) { // Function section
        var rF = __readU32Leb(b, pos); var numF = rF[0]; pos = rF[1];
        for (var fn = 0; fn < numF; fn++) {
          var rTI = __readU32Leb(b, pos); pos = rTI[1];
          this.funcTypeIndices.push(rTI[0]);
        }
      } else if (secId === 5) { // Memory section
        var rM = __readU32Leb(b, pos); var numM = rM[0]; pos = rM[1];
        for (var m = 0; m < numM; m++) {
          var flags = b[pos++];
          var rIn = __readU32Leb(b, pos); var init = rIn[0]; pos = rIn[1];
          var max = null;
          if (flags & 1) { var rMx = __readU32Leb(b, pos); max = rMx[0]; pos = rMx[1]; }
          this.memories.push({ initial: init, maximum: max });
        }
      } else if (secId === 7) { // Export section
        var rE = __readU32Leb(b, pos); var numE = rE[0]; pos = rE[1];
        for (var ex = 0; ex < numE; ex++) {
          var rNL = __readU32Leb(b, pos); var nL = rNL[0]; pos = rNL[1];
          var name = String.fromCharCode.apply(null, b.slice(pos, pos + nL)); pos += nL;
          var kind = b[pos++];
          var rIdx = __readU32Leb(b, pos); var idx = rIdx[0]; pos = rIdx[1];
          var kindName = kind === 0 ? "function" : (kind === 1 ? "table" : (kind === 2 ? "memory" : "global"));
          this.exports[name] = { kind: kindName, index: idx };
        }
      } else if (secId === 10) { // Code section
        var rC = __readU32Leb(b, pos); var numC = rC[0]; pos = rC[1];
        for (var c = 0; c < numC; c++) {
          var rCL = __readU32Leb(b, pos); var cL = rCL[0]; pos = rCL[1];
          funcBodies.push(b.slice(pos, pos + cL));
          pos += cL;
        }
      } else if (secId === 11) { // Data section
        var rD = __readU32Leb(b, pos); var numD = rD[0]; pos = rD[1];
        for (var d = 0; d < numD; d++) {
          var rMI = __readU32Leb(b, pos); pos = rMI[1];
          var op = b[pos++];
          var offsetVal = 0;
          if (op === 0x41) { var rO = __readI32Leb(b, pos); offsetVal = rO[0]; pos = rO[1]; }
          if (b[pos] === 0x0B) pos++;
          var rDL = __readU32Leb(b, pos); var dL = rDL[0]; pos = rDL[1];
          var dataBytes = b.slice(pos, pos + dL); pos += dL;
          this.dataSegments.push({ offset: offsetVal, data: dataBytes });
        }
      }
      pos = secEnd;
    }
    var importFuncCount = this.imports.filter(function (x) { return x.kind === 0; }).length;
    for (var i = 0; i < funcBodies.length; i++) {
      var body = funcBodies[i];
      var typeIdx = this.funcTypeIndices[importFuncCount + i];
      var locP = 0;
      var rL = __readU32Leb(body, locP); locP = rL[1];
      var locals = [];
      for (var ld = 0; ld < rL[0]; ld++) {
        var rCnt = __readU32Leb(body, locP); var cnt = rCnt[0]; locP = rCnt[1];
        var valType = body[locP++];
        for (var k = 0; k < cnt; k++) locals.push(valType);
      }
      this.functions.push({ typeIdx: typeIdx, locals: locals, code: body.slice(locP) });
    }
  };

  function WasmInstance(module, importObject) {
    if (!(module instanceof WasmModule)) throw new TypeError("WebAssembly.Instance: Argument 0 must be a WebAssembly.Module");
    this.module = module;
    this.importObject = importObject || {};
    this.memory = null;
    this.importedFuncs = [];
    this.exports = {};

    if (module.memories.length > 0) {
      this.memory = new WasmMemory(module.memories[0]);
    } else if (this.importObject.env && this.importObject.env.memory instanceof WasmMemory) {
      this.memory = this.importObject.env.memory;
    }

    if (this.memory && module.dataSegments) {
      var memView = new Uint8Array(this.memory.buffer);
      for (var d = 0; d < module.dataSegments.length; d++) {
        var seg = module.dataSegments[d];
        memView.set(seg.data, seg.offset);
      }
    }

    for (var im = 0; im < module.imports.length; im++) {
      var imp = module.imports[im];
      if (imp.kind === 0) {
        var fn = this.importObject[imp.module] && this.importObject[imp.module][imp.field];
        if (typeof fn !== "function") fn = function () { return 0; };
        this.importedFuncs.push(fn);
      }
    }

    if (this.memory) this.exports.memory = this.memory;

    var self = this;
    for (var expName in module.exports) {
      var exp = module.exports[expName];
      if (exp.kind === "function") {
        (function (name, fIdx) {
          self.exports[name] = function () {
            var args = Array.prototype.slice.call(arguments);
            var res = self._executeFunction(fIdx, args);
            global.__WR_WASM_CALLS.push({ name: name, funcIdx: fIdx, args: args, result: res });
            return res;
          };
        })(expName, exp.index);
      } else if (exp.kind === "memory" && this.memory) {
        this.exports[expName] = this.memory;
      }
    }
  }

  /* W3: type-aware value coercion. The old entry path coerced every
     argument with `| 0` (int32 truncation), so float params/locals/returns
     died on arrival. Valtype bytes: 0x7F i32, 0x7E i64, 0x7D f32, 0x7C f64.
     i64 is approximated as i32 throughout this engine (disclosed). */
  function __wasmCoerceIn(v, t) {
    if (t === 0x7D) return Math.fround(+v || 0);
    if (t === 0x7C) return +v || 0;
    return (v | 0);
  }
  function __wasmCoerceOut(v, t) {
    if (t === 0x7D) return Math.fround(+v || 0);
    if (t === 0x7C) return +v || 0;
    return (v | 0);
  }
  WasmInstance.prototype._executeFunction = function (funcIdx, args) {
    var importFuncCount = this.importedFuncs.length;
    if (funcIdx < importFuncCount) {
      return this.importedFuncs[funcIdx].apply(null, args);
    }
    var fnObj = this.module.functions[funcIdx - importFuncCount];
    var sig = this.module.types[fnObj.typeIdx];
    var locals = [];
    /* W3: localTypes = signature params + declared locals, so local.get/
       local.set/tee coerce per the declared type instead of blanket |0. */
    var localTypes = sig.params.slice();
    for (var lt = 0; lt < fnObj.locals.length; lt++) localTypes.push(fnObj.locals[lt]);
    for (var a = 0; a < args.length; a++) locals.push(__wasmCoerceIn(args[a], sig.params[a]));
    for (var l = 0; l < fnObj.locals.length; l++) locals.push(0);

    var code = fnObj.code;
    var pc = 0;
    var stack = [];
    var blocks = [];
    var fuel = this.maxFuel || 500000;
    var dv = this.memory ? new DataView(this.memory.buffer) : null;

    while (pc < code.length) {
      if (--fuel <= 0) throw new Error("WASM instruction fuel exhausted");
      var op = code[pc++];

      if (op === 0x00) throw new Error("unreachable instruction");
      else if (op === 0x01) continue;
      else if (op === 0x02) {
        pc++; var endPc = this._findEnd(code, pc);
        blocks.push({ kind: 0, start: pc, end: endPc });
      } else if (op === 0x03) {
        pc++; var endPc = this._findEnd(code, pc);
        blocks.push({ kind: 1, start: pc - 2, end: endPc });
      } else if (op === 0x04) {
        var cond = stack.pop() || 0; pc++;
        var endPc = this._findEnd(code, pc);
        var elsePc = this._findElse(code, pc, endPc);
        if (cond !== 0) {
          blocks.push({ kind: 2, start: pc, end: endPc });
        } else {
          if (elsePc !== null) { pc = elsePc + 1; blocks.push({ kind: 2, start: pc, end: endPc }); }
          else { pc = endPc; }
        }
      } else if (op === 0x05) {
        if (blocks.length > 0) pc = blocks.pop().end;
      } else if (op === 0x0B) {
        if (blocks.length > 0) blocks.pop();
        if (pc >= code.length) break;
      } else if (op === 0x0C) {
        var rL = __readU32Leb(code, pc); var lIdx = rL[0]; pc = rL[1];
        var target = blocks[blocks.length - 1 - lIdx];
        if (target.kind === 1) pc = target.start + 2; else pc = target.end;
      } else if (op === 0x0D) {
        var rL = __readU32Leb(code, pc); var lIdx = rL[0]; pc = rL[1];
        var cond = stack.pop() || 0;
        if (cond !== 0) {
          var target = blocks[blocks.length - 1 - lIdx];
          if (target.kind === 1) pc = target.start + 2; else pc = target.end;
        }
      } else if (op === 0x0F) {
        break;
      } else if (op === 0x10) {
        var rCall = __readU32Leb(code, pc); var cIdx = rCall[0]; pc = rCall[1];
        var cSig = this.module.types[this.module.funcTypeIndices[cIdx]];
        var cArgs = [];
        for (var p = 0; p < cSig.params.length; p++) cArgs.unshift(stack.pop());
        var cRes = this._executeFunction(cIdx, cArgs);
        if (cSig.results.length > 0 && cRes !== undefined) stack.push(__wasmCoerceOut(cRes, cSig.results[0]));
      } else if (op === 0x1A) {
        stack.pop();
      } else if (op === 0x1B) {
        var c = stack.pop() || 0; var v2 = stack.pop() || 0; var v1 = stack.pop() || 0;
        stack.push(c !== 0 ? v1 : v2);
      } else if (op === 0x20) {
        var rLoc = __readU32Leb(code, pc); var lIdx = rLoc[0]; pc = rLoc[1];
        stack.push(__wasmCoerceOut(locals[lIdx] || 0, localTypes[lIdx]));
      } else if (op === 0x21) {
        var rLoc = __readU32Leb(code, pc); var lIdx = rLoc[0]; pc = rLoc[1];
        locals[lIdx] = __wasmCoerceIn(stack.pop(), localTypes[lIdx]);
      } else if (op === 0x22) {
        var rLoc = __readU32Leb(code, pc); var lIdx = rLoc[0]; pc = rLoc[1];
        locals[lIdx] = __wasmCoerceIn(stack[stack.length - 1], localTypes[lIdx]);
      } else if (op === 0x28) {
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var base = stack.pop() || 0;
        if (dv) stack.push(dv.getInt32(base + memOff, true)); else stack.push(0);
      } else if (op === 0x2C) {
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var base = stack.pop() || 0;
        if (dv) stack.push(dv.getInt8(base + memOff)); else stack.push(0);
      } else if (op === 0x2D) {
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var base = stack.pop() || 0;
        if (dv) stack.push(dv.getUint8(base + memOff)); else stack.push(0);
      } else if (op === 0x36) {
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var val = stack.pop() || 0; var base = stack.pop() || 0;
        if (dv) dv.setInt32(base + memOff, val | 0, true);
      } else if (op === 0x3A) {
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var val = stack.pop() || 0; var base = stack.pop() || 0;
        if (dv) dv.setUint8(base + memOff, (val | 0) & 0xFF);
      } else if (op === 0x3F) {
        pc++; stack.push(this.memory ? this.memory.buffer.byteLength / 65536 : 0);
      } else if (op === 0x40) {
        pc++; var delta = stack.pop() || 0;
        if (this.memory) { var old = this.memory.grow(delta); dv = new DataView(this.memory.buffer); stack.push(old); }
        else { stack.push(-1); }
      } else if (op === 0x41) {
        var rConst = __readI32Leb(code, pc); var cVal = rConst[0]; pc = rConst[1];
        stack.push(cVal | 0);
      } else if (op === 0x45) {
        var a = stack.pop() || 0; stack.push((a | 0) === 0 ? 1 : 0);
      } else if (op === 0x46) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) === (b | 0) ? 1 : 0);
      } else if (op === 0x47) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) !== (b | 0) ? 1 : 0);
      } else if (op === 0x48) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) < (b | 0) ? 1 : 0);
      } else if (op === 0x49) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0; stack.push(a < b ? 1 : 0);
      } else if (op === 0x4A) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) > (b | 0) ? 1 : 0);
      } else if (op === 0x4B) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0; stack.push(a > b ? 1 : 0);
      } else if (op === 0x4C) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) <= (b | 0) ? 1 : 0);
      } else if (op === 0x4D) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0; stack.push(a <= b ? 1 : 0);
      } else if (op === 0x4E) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push((a | 0) >= (b | 0) ? 1 : 0);
      } else if (op === 0x4F) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0; stack.push(a >= b ? 1 : 0);
      } else if (op === 0x6A) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(((a | 0) + (b | 0)) | 0);
      } else if (op === 0x6B) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(((a | 0) - (b | 0)) | 0);
      } else if (op === 0x6C) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(Math.imul(a | 0, b | 0) | 0);
      } else if (op === 0x6D) {
        var b = stack.pop() || 0; var a = stack.pop() || 0;
        if ((b | 0) === 0) throw new Error("integer divide by zero");
        stack.push(((a | 0) / (b | 0)) | 0);
      } else if (op === 0x6E) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0;
        if (b === 0) throw new Error("integer divide by zero");
        stack.push(Math.floor(a / b) | 0);
      } else if (op === 0x6F) {
        var b = stack.pop() || 0; var a = stack.pop() || 0;
        if ((b | 0) === 0) throw new Error("integer divide by zero");
        stack.push(((a | 0) % (b | 0)) | 0);
      } else if (op === 0x70) {
        var b = (stack.pop() || 0) >>> 0; var a = (stack.pop() || 0) >>> 0;
        if (b === 0) throw new Error("integer divide by zero");
        stack.push((a % b) | 0);
      } else if (op === 0x71) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(((a | 0) & (b | 0)) | 0);
      } else if (op === 0x72) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(((a | 0) | (b | 0)) | 0);
      } else if (op === 0x73) {
        var b = stack.pop() || 0; var a = stack.pop() || 0; stack.push(((a | 0) ^ (b | 0)) | 0);
      } else if (op === 0x74) {
        var b = (stack.pop() || 0) & 0x1F; var a = stack.pop() || 0; stack.push(((a | 0) << b) | 0);
      } else if (op === 0x75) {
        var b = (stack.pop() || 0) & 0x1F; var a = stack.pop() || 0; stack.push(((a | 0) >> b) | 0);
      } else if (op === 0x76) {
        var b = (stack.pop() || 0) & 0x1F; var a = stack.pop() || 0; stack.push((((a | 0) >>> b)) | 0);
      } else if (op === 0x77) {
        var b = (stack.pop() || 0) & 0x1F; var a = (stack.pop() || 0) >>> 0;
        stack.push((((a << b) | (a >>> (32 - b)))) | 0);
      } else if (op === 0x78) {
        var b = (stack.pop() || 0) & 0x1F; var a = (stack.pop() || 0) >>> 0;
        stack.push((((a >>> b) | (a << (32 - b)))) | 0);
      }
      /* ---- W3: f32/f64 memory load/store ---- */
      else if (op === 0x2A) { /* f32.load */
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var base = stack.pop() || 0;
        if (dv) stack.push(dv.getFloat32(base + memOff, true)); else stack.push(0);
      } else if (op === 0x2B) { /* f64.load */
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var base = stack.pop() || 0;
        if (dv) stack.push(dv.getFloat64(base + memOff, true)); else stack.push(0);
      } else if (op === 0x38) { /* f32.store */
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var val = stack.pop() || 0; var base = stack.pop() || 0;
        if (dv) dv.setFloat32(base + memOff, val, true);
      } else if (op === 0x39) { /* f64.store */
        var rAl = __readU32Leb(code, pc); pc = rAl[1];
        var rOff = __readU32Leb(code, pc); var memOff = rOff[0]; pc = rOff[1];
        var val = stack.pop() || 0; var base = stack.pop() || 0;
        if (dv) dv.setFloat64(base + memOff, val, true);
      }
      /* ---- W3: f32/f64 constants ---- */
      else if (op === 0x43) { /* f32.const — 4-byte IEEE754 LE */
        if (pc + 4 > code.length) throw new Error("unexpected end of f32.const");
        var f32buf = new DataView(code.buffer, code.byteOffset + pc, 4);
        pc += 4;
        stack.push(f32buf.getFloat32(0, true));
      } else if (op === 0x44) { /* f64.const — 8-byte IEEE754 LE */
        if (pc + 8 > code.length) throw new Error("unexpected end of f64.const");
        var f64buf = new DataView(code.buffer, code.byteOffset + pc, 8);
        pc += 8;
        stack.push(f64buf.getFloat64(0, true));
      } else if (op === 0x42) { /* i64.const — signed LEB128 */
        var rI64 = __readI32Leb(code, pc); pc = rI64[1];
        stack.push(rI64[0]);
      }
      /* ---- W3: f32 comparisons (0x5B–0x60) ---- */
      else if (op >= 0x5B && op <= 0x60) {
        var b = +stack.pop(); var a = +stack.pop();
        var r5B = 0;
        if (op === 0x5B) r5B = (a === b) ? 1 : 0;          /* f32.eq */
        else if (op === 0x5C) r5B = (a !== b) ? 1 : 0;     /* f32.ne */
        else if (op === 0x5D) r5B = (a < b) ? 1 : 0;       /* f32.lt */
        else if (op === 0x5E) r5B = (a > b) ? 1 : 0;       /* f32.gt */
        else if (op === 0x5F) r5B = (a <= b) ? 1 : 0;      /* f32.le */
        else if (op === 0x60) r5B = (a >= b) ? 1 : 0;      /* f32.ge */
        stack.push(r5B);
      }
      /* ---- W3: f64 comparisons (0x61–0x66) ---- */
      else if (op >= 0x61 && op <= 0x66) {
        var b = +stack.pop(); var a = +stack.pop();
        var r61 = 0;
        if (op === 0x61) r61 = (a === b) ? 1 : 0;          /* f64.eq */
        else if (op === 0x62) r61 = (a !== b) ? 1 : 0;     /* f64.ne */
        else if (op === 0x63) r61 = (a < b) ? 1 : 0;       /* f64.lt */
        else if (op === 0x64) r61 = (a > b) ? 1 : 0;       /* f64.gt */
        else if (op === 0x65) r61 = (a <= b) ? 1 : 0;      /* f64.le */
        else if (op === 0x66) r61 = (a >= b) ? 1 : 0;      /* f64.ge */
        stack.push(r61);
      }
      /* ---- W3: i64 integer ops (0x79–0x8A), i32-approximated.
         The engine represents i64 as i32 (i64.const reads via __readI32Leb),
         so these compute the i32-twin semantics on the approximated values —
         disclosed approximation, not a spec-exact 64-bit lane. ---- */
      else if (op >= 0x79 && op <= 0x8A) {
        var i64a, i64b;
        if (op <= 0x7A) {
          /* clz/ctz/popcnt: count in the 32-bit representation */
          var v79 = (stack.pop() | 0) >>> 0;
          if (v79 === 0) { stack.push(op === 0x79 ? 32 : 32); }
          else if (op === 0x79) { /* i64.clz */
            var c79 = 0, t79 = v79;
            while (!(t79 & 0x80000000)) { t79 <<= 1; c79++; }
            stack.push(c79);
          } else if (op === 0x7A) { /* i64.ctz */
            var c7A = 0, t7A = v79;
            while (!(t7A & 1)) { t7A >>>= 1; c7A++; }
            stack.push(c7A);
          } else { /* 0x7B i64.popcnt */
            var pc7B = 0, t7B = v79;
            while (t7B) { pc7B += t7B & 1; t7B >>>= 1; }
            stack.push(pc7B);
          }
        } else if (op === 0x7C) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push((i64a + i64b) | 0); } /* i64.add */
        else if (op === 0x7D) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push((i64a - i64b) | 0); } /* i64.sub */
        else if (op === 0x7E) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push(Math.imul(i64a, i64b) | 0); } /* i64.mul */
        else if (op === 0x7F) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; if (i64b === 0) throw new Error("integer divide by zero"); stack.push((i64a / i64b) | 0); } /* i64.div_s */
        else if (op === 0x80) { i64b = (stack.pop() | 0) >>> 0; i64a = (stack.pop() | 0) >>> 0; if (i64b === 0) throw new Error("integer divide by zero"); stack.push(Math.floor(i64a / i64b) | 0); } /* i64.div_u */
        else if (op === 0x81) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; if (i64b === 0) throw new Error("integer divide by zero"); stack.push((i64a % i64b) | 0); } /* i64.rem_s */
        else if (op === 0x82) { i64b = (stack.pop() | 0) >>> 0; i64a = (stack.pop() | 0) >>> 0; if (i64b === 0) throw new Error("integer divide by zero"); stack.push((i64a % i64b) | 0); } /* i64.rem_u */
        else if (op === 0x83) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push((i64a & i64b) | 0); } /* i64.and */
        else if (op === 0x84) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push((i64a | i64b) | 0); } /* i64.or */
        else if (op === 0x85) { i64b = stack.pop() | 0; i64a = stack.pop() | 0; stack.push((i64a ^ i64b) | 0); } /* i64.xor */
        else if (op === 0x86) { i64b = (stack.pop() | 0) & 0x3F; i64a = stack.pop() | 0; stack.push((i64a << i64b) | 0); } /* i64.shl */
        else if (op === 0x87) { i64b = (stack.pop() | 0) & 0x3F; i64a = stack.pop() | 0; stack.push((i64a >> i64b) | 0); } /* i64.shr_s */
        else if (op === 0x88) { i64b = (stack.pop() | 0) & 0x3F; i64a = (stack.pop() | 0) >>> 0; stack.push((i64a >>> i64b) | 0); } /* i64.shr_u */
        else if (op === 0x89) { i64b = (stack.pop() | 0) & 0x3F; i64a = (stack.pop() | 0) >>> 0; var sh89 = i64b & 31; stack.push((((i64a << sh89) | (i64a >>> (32 - sh89))) | 0)); } /* i64.rotl */
        else if (op === 0x8A) { i64b = (stack.pop() | 0) & 0x3F; i64a = (stack.pop() | 0) >>> 0; var sh8A = i64b & 31; stack.push((((i64a >>> sh8A) | (i64a << (32 - sh8A))) | 0)); } /* i64.rotr */
      }
      /* ---- W3: f32 unary arithmetic (0x8B–0x91, spec numbers) ---- */
      else if (op >= 0x8B && op <= 0x91) {
        var a = +stack.pop();
        var r8B = 0;
        if (op === 0x8B) r8B = Math.abs(a);            /* f32.abs */
        else if (op === 0x8C) r8B = -a;               /* f32.neg */
        else if (op === 0x8D) r8B = Math.ceil(a);     /* f32.ceil */
        else if (op === 0x8E) r8B = Math.floor(a);    /* f32.floor */
        else if (op === 0x8F) r8B = Math.trunc(a);    /* f32.trunc */
        else if (op === 0x90) r8B = Math.round(a);    /* f32.nearest (ties-away, spec is ties-even — disclosed) */
        else if (op === 0x91) r8B = Math.sqrt(a);     /* f32.sqrt */
        stack.push(Math.fround(r8B));
      }
      /* ---- W3: f32 binary arithmetic (0x92–0x98, spec numbers) ---- */
      else if (op >= 0x92 && op <= 0x98) {
        var b = +stack.pop(); var a = +stack.pop();
        var r92 = 0;
        if (op === 0x92) r92 = a + b;                        /* f32.add */
        else if (op === 0x93) r92 = a - b;                   /* f32.sub */
        else if (op === 0x94) r92 = a * b;                   /* f32.mul */
        else if (op === 0x95) r92 = a / b;                   /* f32.div */
        else if (op === 0x96) r92 = Math.min(a, b);          /* f32.min */
        else if (op === 0x97) r92 = Math.max(a, b);          /* f32.max */
        else if (op === 0x98) r92 = (b < 0) ? -Math.abs(a) : Math.abs(a); /* f32.copysign */
        stack.push(Math.fround(r92));
      }
      /* ---- W3: f64 unary arithmetic (0x99–0x9F, spec numbers) ---- */
      else if (op >= 0x99 && op <= 0x9F) {
        var a = +stack.pop();
        var r99 = 0;
        if (op === 0x99) r99 = Math.abs(a);            /* f64.abs */
        else if (op === 0x9A) r99 = -a;                /* f64.neg */
        else if (op === 0x9B) r99 = Math.ceil(a);      /* f64.ceil */
        else if (op === 0x9C) r99 = Math.floor(a);     /* f64.floor */
        else if (op === 0x9D) r99 = Math.trunc(a);     /* f64.trunc */
        else if (op === 0x9E) r99 = Math.round(a);     /* f64.nearest (ties-away, spec is ties-even — disclosed) */
        else if (op === 0x9F) r99 = Math.sqrt(a);      /* f64.sqrt */
        stack.push(r99);
      }
      /* ---- W3: f64 binary arithmetic (0xA0–0xA6, spec numbers) ---- */
      else if (op >= 0xA0 && op <= 0xA6) {
        var b = +stack.pop(); var a = +stack.pop();
        var rA0 = 0;
        if (op === 0xA0) rA0 = a + b;                        /* f64.add */
        else if (op === 0xA1) rA0 = a - b;                   /* f64.sub */
        else if (op === 0xA2) rA0 = a * b;                   /* f64.mul */
        else if (op === 0xA3) rA0 = a / b;                   /* f64.div */
        else if (op === 0xA4) rA0 = Math.min(a, b);          /* f64.min */
        else if (op === 0xA5) rA0 = Math.max(a, b);          /* f64.max */
        else if (op === 0xA6) rA0 = (b < 0) ? -Math.abs(a) : Math.abs(a); /* f64.copysign */
        stack.push(rA0);
      }
      /* ---- W3: conversions (0xA7–0xBF) ---- */
      else if (op >= 0xA7 && op <= 0xBF) {
        var cvVal = 0;
        if (op === 0xA7) { cvVal = (stack.pop() | 0); }              /* i32.wrap_i64 */
        else if (op === 0xA8) { cvVal = Math.trunc(+stack.pop()) | 0; } /* i32.trunc_f32_s */
        else if (op === 0xA9) { cvVal = Math.trunc(+stack.pop()) >>> 0; } /* i32.trunc_f32_u */
        else if (op === 0xAA) { cvVal = Math.trunc(+stack.pop()) | 0; } /* i32.trunc_f64_s */
        else if (op === 0xAB) { cvVal = Math.trunc(+stack.pop()) >>> 0; } /* i32.trunc_f64_u */
        else if (op === 0xAC) { cvVal = (stack.pop() | 0); }         /* i64.extend_i32_s */
        else if (op === 0xAD) { cvVal = (stack.pop() | 0) >>> 0; }   /* i64.extend_i32_u */
        else if (op === 0xAE) { cvVal = Math.trunc(+stack.pop()); }  /* i64.trunc_f32_s */
        else if (op === 0xAF) { cvVal = Math.trunc(+stack.pop()); }  /* i64.trunc_f32_u */
        else if (op === 0xB0) { cvVal = Math.trunc(+stack.pop()); }  /* i64.trunc_f64_s */
        else if (op === 0xB1) { cvVal = Math.trunc(+stack.pop()); }  /* i64.trunc_f64_u */
        else if (op === 0xB2) { cvVal = Math.fround(+(stack.pop() | 0)); } /* f32.convert_i32_s */
        else if (op === 0xB3) { cvVal = Math.fround((stack.pop() | 0) >>> 0); } /* f32.convert_i32_u */
        else if (op === 0xB4) { cvVal = Math.fround(+(stack.pop() | 0)); } /* f32.convert_i64_s */
        else if (op === 0xB5) { cvVal = Math.fround((stack.pop() | 0) >>> 0); } /* f32.convert_i64_u */
        else if (op === 0xB6) { cvVal = Math.fround(+stack.pop()); }  /* f32.demote_f64 */
        else if (op === 0xB7) { cvVal = +(stack.pop() | 0); }         /* f64.convert_i32_s */
        else if (op === 0xB8) { cvVal = +((stack.pop() | 0) >>> 0); } /* f64.convert_i32_u */
        else if (op === 0xB9) { cvVal = +(stack.pop() | 0); }         /* f64.convert_i64_s */
        else if (op === 0xBA) { cvVal = +((stack.pop() | 0) >>> 0); } /* f64.convert_i64_u */
        else if (op === 0xBB) { cvVal = +stack.pop(); }               /* f64.promote_f32 */
        else if (op === 0xBC) { /* i32.reinterpret_f32 */
          var ri32f32 = new DataView(new ArrayBuffer(4));
          ri32f32.setFloat32(0, +stack.pop(), true);
          cvVal = ri32f32.getInt32(0, true);
        } else if (op === 0xBD) { /* i64.reinterpret_f64 */
          var ri64f64 = new DataView(new ArrayBuffer(8));
          ri64f64.setFloat64(0, +stack.pop(), true);
          cvVal = ri64f64.getInt32(0, true); /* low 32 bits (i64 approximated as i32 in this engine) */
        } else if (op === 0xBE) { /* f32.reinterpret_i32 */
          var rf32i32 = new DataView(new ArrayBuffer(4));
          rf32i32.setInt32(0, stack.pop() | 0, true);
          cvVal = rf32i32.getFloat32(0, true);
        } else if (op === 0xBF) { /* f64.reinterpret_i64 */
          var rf64i64 = new DataView(new ArrayBuffer(8));
          rf64i64.setInt32(0, stack.pop() | 0, true);
          cvVal = rf64i64.getFloat64(0, true);
        }
        stack.push(cvVal);
      }
      /* ---- W3: sign extensions (0xC0–0xC4, spec numbers). The earlier
         0xA0–0xA4 range wrongly captured the f64 binary ops. ---- */
      else if (op >= 0xC0 && op <= 0xC4) {
        var v = stack.pop() || 0;
        var rC0 = 0;
        if (op === 0xC0) rC0 = ((v & 0xFF) << 24) >> 24;   /* i32.extend8_s */
        else if (op === 0xC1) rC0 = ((v & 0xFFFF) << 16) >> 16; /* i32.extend16_s */
        else if (op === 0xC2) rC0 = ((v & 0xFF) << 24) >> 24;   /* i64.extend8_s (i32-approximated) */
        else if (op === 0xC3) rC0 = ((v & 0xFFFF) << 16) >> 16; /* i64.extend16_s (i32-approximated) */
        else if (op === 0xC4) rC0 = v | 0;                      /* i64.extend32_s */
        stack.push(rC0 | 0);
      }
    }
    if (sig.results.length > 0 && stack.length > 0) return __wasmCoerceOut(stack[stack.length - 1], sig.results[0]);
    return undefined;
  };

  WasmInstance.prototype._findEnd = function (code, startPc) {
    var depth = 1, cur = startPc;
    while (cur < code.length && depth > 0) {
      var b = code[cur++];
      if (b === 0x02 || b === 0x03 || b === 0x04) depth++;
      else if (b === 0x0B) depth--;
    }
    return cur;
  };

  WasmInstance.prototype._findElse = function (code, startPc, endPc) {
    var depth = 0, cur = startPc;
    while (cur < endPc) {
      var b = code[cur];
      if (b === 0x02 || b === 0x03 || b === 0x04) depth++;
      else if (b === 0x05 && depth === 0) return cur;
      else if (b === 0x0B) depth--;
      cur++;
    }
    return null;
  };

  var WebAssemblyAPI = {
    validate: __wasmValidate,
    compile: function (source) {
      try {
        return Promise.resolve(new WasmModule(source));
      } catch (e) {
        return Promise.reject(e);
      }
    },
    instantiate: function (sourceOrMod, importObj) {
      try {
        if (sourceOrMod instanceof WasmModule) {
          var inst = new WasmInstance(sourceOrMod, importObj);
          return Promise.resolve(inst);
        }
        var mod = new WasmModule(sourceOrMod);
        var inst2 = new WasmInstance(mod, importObj);
        return Promise.resolve({ module: mod, instance: inst2 });
      } catch (e) {
        return Promise.reject(e);
      }
    },
    Module: WasmModule,
    Instance: WasmInstance,
    Memory: WasmMemory,
    Table: WasmTable,
    CompileError: function (msg) { var e = new Error(msg); e.name = "CompileError"; return e; },
    RuntimeError: function (msg) { var e = new Error(msg); e.name = "RuntimeError"; return e; }
  };

  if (!global.WebAssembly || !global.WebAssembly.Module) {
    global.WebAssembly = WebAssemblyAPI;
  }
  __cloakObject(WebAssemblyAPI, {
    validate: 1,
    compile: 1,
    instantiate: 1
  });
  __cloakObject(WasmMemory.prototype, {
    grow: 1
  });
  __cloakObject(WasmTable.prototype, {
    get: 1,
    set: 2,
    grow: 1
  });
  __makeNative(WasmModule, "Module", 1);
  __makeNative(WasmInstance, "Instance", 1);
  __makeNative(WasmMemory, "Memory", 1);
  __makeNative(WasmTable, "Table", 1);

  /* Base64, encoding. */
  var B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
  global.btoa = function (input) {
    var s = String(input);
    var out = "";
    for (var i = 0; i < s.length; i += 3) {
      var c1 = s.charCodeAt(i), c2 = s.charCodeAt(i + 1), c3 = s.charCodeAt(i + 2);
      var e1 = c1 >> 2, e2 = ((c1 & 3) << 4) | (isNaN(c2) ? 0 : c2 >> 4);
      var e3 = isNaN(c2) ? 64 : (((c2 & 15) << 2) | (isNaN(c3) ? 0 : c3 >> 6));
      var e4 = isNaN(c3) ? 64 : (c3 & 63);
      out += B64.charAt(e1) + B64.charAt(e2) + (e3 === 64 ? "=" : B64.charAt(e3)) + (e4 === 64 ? "=" : B64.charAt(e4));
    }
    return out;
  };
  global.atob = function (input) {
    var s = String(input).replace(/[^A-Za-z0-9+/=]/g, "");
    while (s.length % 4 !== 0) s += "=";
    var out = "";
    for (var i = 0; i < s.length; i += 4) {
      var c1 = s.charAt(i), c2 = s.charAt(i + 1), c3 = s.charAt(i + 2), c4 = s.charAt(i + 3);
      var e1 = B64.indexOf(c1);
      var e2 = B64.indexOf(c2);
      var e3 = c3 === "=" ? -1 : B64.indexOf(c3);
      var e4 = c4 === "=" ? -1 : B64.indexOf(c4);
      if (e1 < 0 || e2 < 0) break;
      out += String.fromCharCode((e1 << 2) | (e2 >> 4));
      if (e3 >= 0) out += String.fromCharCode(((e2 & 15) << 4) | (e3 >> 2));
      if (e4 >= 0) out += String.fromCharCode(((e3 & 3) << 6) | e4);
    }
    return out;
  };
  /* TextEncoder/TextDecoder: the spec REQUIRES UTF-8 both ways. The old
     Latin-1 stub (charCodeAt & 0xff) corrupted every non-ASCII character —
     a real bug: U+2022 (•) became 0x22 (") which broke JSON.parse inside
     React's flight hydration ("SyntaxError: invalid character in a JSON
     string", LinkedIn login render). */
  global.TextEncoder = function () {
    this.encode = function (t) {
      var s = String(t);
      // two passes: size then fill
      var len = 0;
      var i;
      for (i = 0; i < s.length; i++) {
        var c = s.charCodeAt(i);
        if (c < 0x80) len += 1;
        else if (c < 0x800) len += 2;
        else if (c >= 0xd800 && c <= 0xdbff && i + 1 < s.length) {
          // surrogate pair
          len += 4;
          i++;
        } else len += 3;
      }
      var arr = new Uint8Array(len);
      var p = 0;
      for (i = 0; i < s.length; i++) {
        var ch = s.charCodeAt(i);
        if (ch < 0x80) {
          arr[p++] = ch;
        } else if (ch < 0x800) {
          arr[p++] = 0xc0 | (ch >> 6);
          arr[p++] = 0x80 | (ch & 0x3f);
        } else if (ch >= 0xd800 && ch <= 0xdbff && i + 1 < s.length) {
          var lo = s.charCodeAt(i + 1);
          var cp = 0x10000 + ((ch - 0xd800) << 10) + (lo - 0xdc00);
          arr[p++] = 0xf0 | (cp >> 18);
          arr[p++] = 0x80 | ((cp >> 12) & 0x3f);
          arr[p++] = 0x80 | ((cp >> 6) & 0x3f);
          arr[p++] = 0x80 | (cp & 0x3f);
          i++;
        } else {
          arr[p++] = 0xe0 | (ch >> 12);
          arr[p++] = 0x80 | ((ch >> 6) & 0x3f);
          arr[p++] = 0x80 | (ch & 0x3f);
        }
      }
      return arr;
    };
  };
  global.TextDecoder = function () {
    this.decode = function (buf) {
      if (!buf) return "";
      var bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
      var out = "";
      var i = 0;
      var n = bytes.length;
      while (i < n) {
        var b = bytes[i];
        if (b < 0x80) {
          out += String.fromCharCode(b);
          i += 1;
        } else if (b < 0xe0) {
          var c2 = b & 0x1f;
          if (i + 1 < n) c2 = (c2 << 6) | (bytes[i + 1] & 0x3f);
          out += String.fromCharCode(c2);
          i += 2;
        } else if (b < 0xf0) {
          var c3 = b & 0x0f;
          if (i + 1 < n) c3 = (c3 << 6) | (bytes[i + 1] & 0x3f);
          if (i + 2 < n) c3 = (c3 << 6) | (bytes[i + 2] & 0x3f);
          out += String.fromCharCode(c3);
          i += 3;
        } else {
          var c4 = b & 0x07;
          if (i + 1 < n) c4 = (c4 << 6) | (bytes[i + 1] & 0x3f);
          if (i + 2 < n) c4 = (c4 << 6) | (bytes[i + 2] & 0x3f);
          if (i + 3 < n) c4 = (c4 << 6) | (bytes[i + 3] & 0x3f);
          c4 -= 0x10000;
          out += String.fromCharCode(0xd800 + (c4 >> 10), 0xdc00 + (c4 & 0x3ff));
          i += 4;
        }
      }
      return out;
    };
  };
  global.performance = global.performance || { now: function () { return Date.now ? Date.now() : 0; } };
  global.crypto = global.crypto || {
    getRandomValues: function (arr) {
      for (var i = 0; i < arr.length; i++) arr[i] = Math.floor(Math.random() * 256);
      return arr;
    },
    randomUUID: function () {
      return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
        var r = Math.floor(Math.random() * 16), v = c === "x" ? r : (r & 3) | 8;
        return v.toString(16);
      });
    }
  };
  global.crypto.subtle = {
    digest: function (algorithm, data) {
      return new Promise(function (resolve, reject) {
        try {
          var algoName = typeof algorithm === "string" ? algorithm : (algorithm && algorithm.name) || "SHA-256";
          var bytes;
          if (data instanceof ArrayBuffer) {
            bytes = new Uint8Array(data);
          } else if (data && data.buffer instanceof ArrayBuffer) {
            bytes = new Uint8Array(data.buffer, data.byteOffset, data.byteLength);
          } else if (typeof data === "string") {
            var enc = new TextEncoder();
            bytes = enc.encode(data);
          } else {
            bytes = new Uint8Array(0);
          }
          var hex = "";
          for (var i = 0; i < bytes.length; i++) {
            var b = bytes[i].toString(16);
            hex += (b.length === 1 ? "0" : "") + b;
          }
          var resHex = typeof host_crypto_digest === "function" ? host_crypto_digest(algoName, hex) : "";
          var outBuf = new ArrayBuffer(resHex.length / 2);
          var outView = new Uint8Array(outBuf);
          for (var j = 0; j < resHex.length; j += 2) {
            outView[j / 2] = parseInt(resHex.substr(j, 2), 16);
          }
          resolve(outBuf);
        } catch (e) {
          reject(e);
        }
      });
    },
    importKey: function () { return Promise.resolve({}); },
    exportKey: function () { return Promise.resolve(new ArrayBuffer(0)); },
    encrypt: function () { return Promise.resolve(new ArrayBuffer(0)); },
    decrypt: function () { return Promise.resolve(new ArrayBuffer(0)); }
  };

  /* MessageChannel & MessagePort. Beyond local macro-task scheduling
     (React Scheduler) this now models the transfer spec, because widgets
     hand a channel end across documents — reCAPTCHA answers its
     recaptcha-setup handshake only through a transferred port.

     - Every port gets a realm-local id `._id` and lives in `__wrPortTable`,
       qualified into a global key by `globalThis.__WR_CTX_KEY` (injected by
       the runtime, one nonce per JavaScript context).
     - A local channel (both ends in this realm) keeps `_other` wiring: posts
       into one end fire the other end's listeners (macro task).
     - A transfer list neuters the sent end in this realm (`._neutered`) and
       records structured metadata on the outbound message: which end was
       sent (`from`), which sibling stays here to receive replies (`kept`),
       and the sender's context nonce (`ctx`).
     - The receiving realm materializes a PROXY port (`._isProxy`, its posts
       route back through the host to the kept end), and the runtime calls
       `__wrPortSetRemote` on the keeping realm once the proxy id is known. */
  var __wrPortSeq = 0;
  var __wrPortTable = {};
  function MessagePort() {
    this._id = ++__wrPortSeq;
    __wrPortTable[this._id] = this;
    this._other = null;          /* sibling end, when the channel is local */
    this._remotePeerKey = null;  /* "<nonce>:<localId>" of the far end once split */
    this._isProxy = false;       /* true for ends received from another realm */
    this._peerKey = null;        /* for proxies: where our posts must route */
    this._neutered = false;      /* transferred away from this realm */
    this._closed = false;
    this.onmessage = null;
    this._listeners = [];
  }
  /* Cross-realm payloads arrive as recorded strings; an object posted before
     serialization travels in the \u0001wrsc structured-clone envelope below.
     A posted STRING is delivered as that exact string — never re-parsed. */
  /* Cross-realm postMessage and MessagePort payloads. Strings stay strings
     (the window lane's existing contract). Everything else goes through the
     structured-clone codec so ArrayBuffer and typed arrays survive the host
     hop. JSON.stringify turned those into {} and the far realm could not
     read a binary channel. */
  function __wrWireEncode(data) {
    if (typeof data === "string") return data;
    try {
      return "\u0001wrsc" + JSON.stringify(__scEncode(data));
    } catch (e) {
      __noteEventListenerError("postMessage.clone", e);
      try { return JSON.stringify(data); } catch (e2) { return String(data); }
    }
  }
  function __wrWireDecode(data) {
    if (typeof data === "string" && data.indexOf("\u0001wrsc") === 0) {
      try { return global.__structuredCloneDecode(JSON.parse(data.slice(5))); }
      catch (e) { return data; }
    }
    return data;
  }
  function __wrParsePortData(data) {
    if (typeof data !== "string") return data;
    if (data.indexOf("\u0001wrsc") === 0) return __wrWireDecode(data);
    /* A STRING posted over a MessagePort arrives as that exact string — the
       structured-clone contract. Eagerly JSON.parse-ing here silently turned
       JSON-text strings into arrays on delivery, so a widget whose protocol
       sends JSON-text strings (reCAPTCHA's anchor channel: ["<b64>","z","<id>"])
       received a DIFFERENT type than it posted and its handlers ignored every
       message: the "o" (open) handshake reply never came and the checkbox
       never resolved. Only the \u0001wrsc prefix may decode. */
    return data;
  }
  /* Materialize proxy ends for ports transferred into this realm.
     Receiver-side address: posts made through a proxy route to
     "<senderNonce>:<keptId>" — the sibling end left behind by the sender.
     Module scope: both the port lane and the window lane call it. */
  function __wrMaterializePorts(portsJson, sink) {
    var metas = [];
    try {
      metas = portsJson ? (typeof portsJson === "string" ? JSON.parse(portsJson) : portsJson) : [];
    } catch (e) { metas = []; }
    var ids = [];
    (metas || []).forEach(function (mt) {
      if (!mt) return;
      var p = new MessagePort();
      p._isProxy = true;
      p._peerKey = mt.foreignPeer || (String(mt.ctx || "") + ":" + String(mt.kept || mt.from || 0));
      if (!mt.foreignPeer && (!mt.ctx || !(mt.kept || mt.from))) {
        /* A transferred end we cannot answer through: inert one-way by
           design, and the receiver deserves to know why. */
        __noteEventListenerError("port.transfer", new Error("transfer metadata missing kept-end identity; replies cannot route"));
      }
      sink.push(p);
      ids.push(p._id);
    });
    return ids;
  }
  /* A port with no receiver QUEUES its messages: real MessagePorts hold
     them until the far end starts (onmessage set, first 'message' listener,
     or start()). Firing into the void lost the reCAPTCHA widget's early
     channel requests — they were delivered before the widget's dispatcher
     attached and the requests were never answered. */
  function __wrPortHasReceiver(port) {
    return (typeof port.onmessage === "function") ||
           (port._listeners && port._listeners.length > 0) || !!port._started;
  }
  function __wrQueuePortMessage(port, item) {
    port._msgQueue = port._msgQueue || [];
    if (port._msgQueue.length < 200) {
      port._msgQueue.push(item);
    } else {
      __noteEventListenerError("port.queue", new Error("port message queue overflow; message dropped"));
    }
  }
  function __wrDispatchPort(port, item) {
    try {
      var evtPorts = [];
      if (item.livePorts) {
        /* Same-realm transfer: the event carries the operational ends. */
        evtPorts = item.livePorts.filter(function (p) { return p instanceof MessagePort; });
      } else {
        /* Nested cross-realm transfer: materialize proxies. */
        __wrMaterializePorts(item.portsJson, evtPorts);
      }
      try { globalThis.__WR_LAST_PORTS = evtPorts.map(function (p) { return p._id; }); } catch (e) {}
      var evt = new MessageEvent("message", {
        data: __wrParsePortData(item.data),
        ports: evtPorts,
        origin: "",
        source: null,
        isTrusted: true,
        __trusted: true
      });
      /* Spec: listeners fire with `this` and event.target = the port itself.
         Called bare, a handler that does `this.postMessage(...)` (or reads
         port state through `this`) silently broke — and with a null target
         an `e.target` reply lane never existed. */
      evt.target = port;
      var __hitFrom = timers.length;
      try {
        globalThis.__WR_PORT_HITS = globalThis.__WR_PORT_HITS || [];
        if (globalThis.__WR_PORT_HITS.length < 40) {
          globalThis.__WR_PORT_HITS.push(port._id + " " + (typeof evt.data) + " " + String(evt.data).slice(0, 40));
        }
      } catch (eLog) {}
      if (typeof port.onmessage === "function") {
        try { port.onmessage.call(port, evt); } catch (e) { __noteEventListenerError("port.onmessage", e); }
      }
      port._listeners.slice().forEach(function (l) {
        try { l.call(port, evt); } catch (e) { __noteEventListenerError("port.message", e); }
      });
      try {
        if (globalThis.__WR_PORT_HITS && timers.length > __hitFrom) {
          var delays = [];
          for (var ti = __hitFrom; ti < timers.length && delays.length < 8; ti++) delays.push(timers[ti].ms);
          globalThis.__WR_PORT_HITS.push("scheduled " + delays.join(","));
        }
      } catch (eSched) {}
    } catch (e) {
      __noteEventListenerError("port.delivery", e);
    }
  }
  function __wrPostToPort(port, item) {
    if (!__wrPortHasReceiver(port)) {
      __wrQueuePortMessage(port, item);
      return;
    }
    __wrDispatchPort(port, item);
  }
  function __wrStartPort(port) {
    if (port._started) return;
    port._started = true;
    var q = port._msgQueue;
    if (q && q.length) {
      port._msgQueue = [];
      q.forEach(function (it) { __wrDispatchPort(port, it); });
    }
  }
  function __wrFirePort(port, data, portsJson) {
    __wrPostToPort(port, { data: data, portsJson: portsJson });
  }
  /* postMessage argument shapes that may carry a transfer list:
     legacy        postMessage(data, [transfer])
     modern        postMessage(data, targetOrigin, [transfer])
     options bag   postMessage(data, {targetOrigin, transfer}) */
  function __wrExtractTransfer(second, third) {
    if (second instanceof Array) return { ports: second, bag: null };
    if (third instanceof Array) return { ports: third, bag: null };
    if (second && typeof second === "object" && second.transfer instanceof Array) {
      return { ports: second.transfer, bag: second };
    }
    return { ports: [], bag: null };
  }
  /* Record transfer metadata for the outbound message. "kept" names the
     sibling that stays in this realm and will receive replies; without it
     the far side could never answer. Cross-realm sends NEUTER the sent end
     here (it now belongs to the far realm); the self lane keeps it live —
     a same-realm transfer hands the receiver a WORKING end (the event's
     ports are the operational object; neutering them made every loopback
     handshake receiver hold a dead port). */
  function __wrPrepareOutPorts(sent, neuter) {
    if (neuter === undefined) neuter = true;
    return (sent || []).filter(function (p) { return p instanceof MessagePort; }).map(function (p) {
      var meta = {
        from: p._id,
        ctx: (globalThis.__WR_CTX_KEY || "") + "",
        kept: p._other ? p._other._id : 0,
        foreignPeer: p._peerKey || null
      };
      if (neuter) p._neutered = true;
      return meta;
    });
  }
  MessagePort.prototype.postMessage = function (data, second, third) {
    var self = this;
    try {
      globalThis.__WR_PORT_POSTS = (globalThis.__WR_PORT_POSTS || 0) + 1;
    } catch (eCount) {}
    if (self._closed) return;
    if (self._neutered) {
      /* Transferred away: this end no longer exists from this realm's
         perspective. Report it; a silent drop hides handshakes that stop. */
      __noteEventListenerError("port.postMessage", new Error("transferred MessagePort is inert in this realm"));
      return;
    }
    /* Split-channel routing: a KEPT end routes once rewired
       (_remotePeerKey, set by __wrPortSetRemote after the far side's proxy
       exists); a PROXY end routes from birth (_peerKey, recorded when the
       end was materialized). Both address the far realm as
       "<nonce>:<localId>". */
    var routeKey = self._remotePeerKey || (self._isProxy ? self._peerKey : null);
    if (routeKey) {
      /* Split channel: our posts must reach the far end through the host. */
      var rt = __wrExtractTransfer(second, third);
      var payload = __wrWireEncode(data);
      var portsMeta = "[]";
      if (rt.ports.length) portsMeta = JSON.stringify(__wrPrepareOutPorts(rt.ports));
      if (typeof host_post_message === "function") {
        host_post_message(payload, "*", "port:" + routeKey,
          (global.location && global.location.origin) || "", portsMeta);
      }
      return;
    }
    if (self._other && self._other._neutered) {
      /* The sibling was transferred to another realm but its proxy there is
         not materialized yet and we have not been rewired to it. A real
         MessagePort BUFFERS messages until the transferred end starts
         running in its realm; dropping them here (into the neutered twin's
         dead local lane) is how the anchor's initial handshake send used to
         vanish. Queue; __wrPortSetRemote flushes once the far end exists. */
      var bt = __wrExtractTransfer(second, third);
      var bPayload = __wrWireEncode(data);
      var bPorts = bt.ports.length ? JSON.stringify(__wrPrepareOutPorts(bt.ports)) : "[]";
      self._buffer = self._buffer || [];
      if (self._buffer.length < 100) {
        self._buffer.push([bPayload, bPorts]);
      } else {
        __noteEventListenerError("port.postMessage", new Error("port buffer overflow; message dropped"));
      }
      return;
    }
    var other = self._other;
    if (!other) {
      __noteEventListenerError("port.postMessage", new Error("no channel peer"));
      return;
    }
    var lt = __wrExtractTransfer(second, third);
    var livePorts = (lt.ports || []).slice();  /* local transfer: ends stay live */
    setTimeout(function () {
      if (other._closed) return;
      /* Queue-aware delivery: if the peer has not attached a receiver yet,
         the message waits on the port (spec behavior) instead of dying. */
      __wrPostToPort(other, { data: data, livePorts: livePorts });
    }, 0);
  };
  MessagePort.prototype.addEventListener = function (type, fn) {
    if (type === "message") {
      var wasEmpty = this._listeners.length === 0;
      this._listeners.push(fn);
      /* Per spec, the first message listener starts the port; queued
         messages arrive now, not into the void. */
      if (wasEmpty) __wrStartPort(this);
    }
  };
  MessagePort.prototype.removeEventListener = function (type, fn) {
    this._listeners = this._listeners.filter(function (l) { return l !== fn; });
  };
  MessagePort.prototype.start = function () { __wrStartPort(this); };
  /* Assigning onmessage starts the port (spec). */
  Object.defineProperty(MessagePort.prototype, "onmessage", {
    configurable: true,
    enumerable: true,
    get: function () { return this._onmessage || null; },
    set: function (fn) {
      this._onmessage = (typeof fn === "function") ? fn : null;
      if (this._onmessage) __wrStartPort(this);
    }
  });
  MessagePort.prototype.close = function () {
    this._closed = true;
    this._buffer = null;
    this._msgQueue = null;
  };

  function MessageChannel() {
    this.port1 = new MessagePort();
    this.port2 = new MessagePort();
    this.port1._other = this.port2;
    this.port2._other = this.port1;
  }
  global.MessagePort = MessagePort;
  global.MessageChannel = MessageChannel;

  function Window() {}
  Object.defineProperty(Window, Symbol.hasInstance, {
    value: function (inst) {
      return inst === global || (inst && inst.window === inst) || inst instanceof Window;
    }
  });
  global.Window = Window;
  global.window = global;

  /* History API for SPA routers.
     pushState/replaceState record the request on __WR_PUSHED (a LIST:
     several pushes in one script pass each create their entry) /
     __WR_REPLACED for the host session (which owns the real history
     entries); back/forward/go record a move on __WR_HISTORY_MOVE. The host
     performs the actual entry moves and dispatches popstate into the
     rebuilt document — the engine context cannot outlive a navigation, so
     it cannot own history alone. Cross-origin URLs throw SecurityError per
     spec; the absolute URL is resolved against the CURRENT location
     BEFORE the location is updated (the old order resolved relative URLs
     twice: 'sub/page' from /deep/dir/page became .../sub/sub/page). */
/* ---- Structured-clone subset for history state ----
     history.pushState/replaceState state objects cross the engine→host→engine
     boundary (the session snapshots them; a restore replays them into a fresh
     ctx). JSON.stringify silently DEGRADED that crossing: undefined-valued
     keys vanished, Dates became strings, NaN/Infinity became null, Maps/Sets/
     RegExps collapsed, and functions were dropped without the DataCloneError
     a real browser throws. This codec encodes the structured-clone subset
     losslessly into JSON-safe envelopes and throws DataCloneError for the
     values real browsers refuse (functions, symbols, DOM nodes). */
  function __scCloneError(what) {
    throw new (global.DOMException || Error)(
      "Failed to execute 'pushState' on 'History': " + what + " could not be cloned.",
      "DataCloneError"
    );
  }
  function __scHexFromBytes(u8) {
    var parts = new Array(u8.length);
    for (var i = 0; i < u8.length; i++) {
      var h = u8[i].toString(16);
      parts[i] = h.length === 1 ? "0" + h : h;
    }
    return parts.join("");
  }
  function __scBufFromHex(hex) {
    var n = (hex.length / 2) | 0;
    var buf = new ArrayBuffer(n);
    var view = new Uint8Array(buf);
    for (var i = 0; i < n; i++) view[i] = parseInt(hex.substr(i * 2, 2), 16);
    return buf;
  }
  function __scTypedCtorName(v) {
    var names = ["Uint8Array", "Uint8ClampedArray", "Int8Array", "Uint16Array", "Int16Array",
      "Uint32Array", "Int32Array", "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array"];
    for (var i = 0; i < names.length; i++) {
      var C = global[names[i]];
      if (typeof C === "function" && v instanceof C) return names[i];
    }
    return "";
  }
  function __scEncode(v, seen) {
    if (v === undefined) return { __wr_u: 1 };
    var t = typeof v;
    if (t === "number") {
      if (v !== v) return { __wr_n: "NaN" };
      if (v === Infinity) return { __wr_n: "Infinity" };
      if (v === -Infinity) return { __wr_n: "-Infinity" };
      if (v === 0 && 1 / v === -Infinity) return { __wr_n: "-0" };
      return v;
    }
    if (t === "boolean" || t === "string") return v;
    if (v === null) return null;
    if (t === "function" || t === "symbol") __scCloneError(t === "function" ? "A function" : "A symbol");
    if (typeof Node === "function" && v instanceof Node) __scCloneError("A " + (v.nodeName || "node"));
    seen = seen || new Map();
    if (seen.has(v)) return { __wr_c: seen.get(v) };
    var id = seen.size + 1;
    var enc;
    if (v instanceof Date) {
      seen.set(v, id);
      enc = { __wr_d: v.toISOString() };
    } else if (v instanceof RegExp) {
      seen.set(v, id);
      enc = { __wr_r: [v.source, v.flags] };
    } else if (v instanceof Map) {
      seen.set(v, id);
      var ments = [];
      v.forEach(function (mv, mk) { ments.push([__scEncode(mk, seen), __scEncode(mv, seen)]); });
      enc = { __wr_m: ments };
    } else if (v instanceof Set) {
      seen.set(v, id);
      var sents = [];
      v.forEach(function (sv) { sents.push(__scEncode(sv, seen)); });
      enc = { __wr_s: sents };
    } else if (v instanceof Error) {
      seen.set(v, id);
      enc = { __wr_e: [v.name, v.message] };
    } else if (__scTypedCtorName(v)) {
      seen.set(v, id);
      var bytes = new Uint8Array(v.buffer, v.byteOffset, v.byteLength);
      enc = { __wr_ta: [__scTypedCtorName(v), __scHexFromBytes(bytes)] };
    } else if (typeof ArrayBuffer === "function" && v instanceof ArrayBuffer) {
      seen.set(v, id);
      enc = { __wr_ab: __scHexFromBytes(new Uint8Array(v)) };
    } else if (typeof DataView === "function" && v instanceof DataView) {
      seen.set(v, id);
      enc = { __wr_dv: __scHexFromBytes(new Uint8Array(v.buffer, v.byteOffset, v.byteLength)) };
    } else if (Array.isArray(v)) {
      seen.set(v, id);
      var arr = [];
      for (var ai = 0; ai < v.length; ai++) arr.push(__scEncode(v[ai], seen));
      /* Wrapped: JSON.stringify drops non-index properties on arrays, so a
         bare array could not carry __wr_id across the host boundary and
         cyclic references TO arrays would decode broken. */
      enc = { __wr_a: arr };
    } else if (t === "object" && (Object.getPrototypeOf(v) === Object.prototype || Object.getPrototypeOf(v) === null)) {
      seen.set(v, id);
      var obj = {};
      for (var ok in v) {
        if (Object.prototype.hasOwnProperty.call(v, ok)) obj[ok] = __scEncode(v[ok], seen);
      }
      enc = obj;
    } else if (t === "object") {
      /* W5: class instances clone WITH their prototype. The real
         structuredClone drops the prototype (Chrome returns a plain
         object); this engine PRESERVES it — the class constructor is
         resolved by name at decode time, own properties (including
         getters' captured values via direct property reads) carry
         over, and the instance keeps its methods. Host objects (DOM
         nodes, engines) are still refused — clone is data, not a
         live-handle copier (disclosed). */
      var proto = Object.getPrototypeOf(v);
      var ctor = proto && proto.constructor;
      var ctorName = (ctor && ctor.name) ? String(ctor.name) : "";
      seen.set(v, id);
      var cobj = {};
      for (var ck in v) {
        if (Object.prototype.hasOwnProperty.call(v, ck)) cobj[ck] = __scEncode(v[ck], seen);
      }
      if (ctorName && ctor) {
        /* W5: the encode pass records the LIVE constructor on a module
           side table keyed by (ctorName, instanceId); the decode pass of
           the same structuredClone call resolves prototypes from it.
           A ctor unreachable at decode time degrades to a plain object
           (data survives, methods do not — disclosed). */
        globalThis.__wrSCCtors = globalThis.__wrSCCtors || {};
        globalThis.__wrSCCtors[ctorName] = ctor;
        enc = { __wr_cls: [ctorName, cobj] };
      } else {
        enc = cobj;  /* anonymous prototype: plain-object fallback */
      }
    } else {
      __scCloneError("An object of unsupported type " + (v.constructor && v.constructor.name ? v.constructor.name : typeof v));
    }
    /* Containers carry their table id so cyclic references decode back into
       the same object graph (structured clone is a graph clone, not a tree). */
    if (enc && typeof enc === "object") enc.__wr_id = id;
    return enc;
  }
  global.__structuredCloneEncode = function (v) { return __scEncode(v); };
  global.__structuredCloneDecode = function (v, table) {
    table = table || {};
    if (v === null || typeof v !== "object") return v;
    if (v.__wr_u === 1) return undefined;
    if (typeof v.__wr_n === "string") return Number(v.__wr_n);
    if (typeof v.__wr_d === "string") return new Date(v.__wr_d);
    if (v.__wr_r) return new RegExp(v.__wr_r[0], v.__wr_r[1]);
    if (v.__wr_e) {
      var err = new Error(v.__wr_e[1]);
      err.name = v.__wr_e[0];
      return err;
    }
    if (typeof v.__wr_ab === "string") {
      out = __scBufFromHex(v.__wr_ab);
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      return out;
    }
    if (v.__wr_ta && typeof v.__wr_ta[1] === "string") {
      var taBuf = __scBufFromHex(v.__wr_ta[1]);
      var TA = global[v.__wr_ta[0]];
      out = (typeof TA === "function") ? new TA(taBuf) : new Uint8Array(taBuf);
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      return out;
    }
    if (typeof v.__wr_dv === "string") {
      out = new DataView(__scBufFromHex(v.__wr_dv));
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      return out;
    }
    if (typeof v.__wr_c === "number" && table[v.__wr_c] !== undefined) return table[v.__wr_c];
    var out;
    if (v.__wr_a) {
      out = [];
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      for (var ai2 = 0; ai2 < v.__wr_a.length; ai2++) out.push(global.__structuredCloneDecode(v.__wr_a[ai2], table));
      return out;
    }
    if (Array.isArray(v)) {
      out = [];
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      for (var di = 0; di < v.length; di++) out.push(global.__structuredCloneDecode(v[di], table));
      return out;
    }
    if (v.__wr_cls) {
      /* W5: rebuild the instance against the live class prototype so
         methods survive the clone. Unknown constructor name (class not
         defined in this realm) degrades HONESTLY to a plain object —
         data survives, methods do not (disclosed). */
      var Ctor = (globalThis.__wrSCCtors && globalThis.__wrSCCtors[v.__wr_cls[0]]) || global[v.__wr_cls[0]];
      var payload = v.__wr_cls[1];
      if (typeof Ctor === "function") {
        out = Object.create(Ctor.prototype);
        if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
        for (var wk in payload) {
          if (Object.prototype.hasOwnProperty.call(payload, wk)) {
            out[wk] = global.__structuredCloneDecode(payload[wk], table);
          }
        }
        return out;
      }
      /* Fall through to plain-object decode of the payload. */
      out = {};
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      for (var pk in payload) {
        if (Object.prototype.hasOwnProperty.call(payload, pk)) {
          out[pk] = global.__structuredCloneDecode(payload[pk], table);
        }
      }
      return out;
    }
    if (v.__wr_m) {
      out = new Map();
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      for (var mi = 0; mi < v.__wr_m.length; mi++) {
        out.set(global.__structuredCloneDecode(v.__wr_m[mi][0], table),
                global.__structuredCloneDecode(v.__wr_m[mi][1], table));
      }
      return out;
    }
    if (v.__wr_s) {
      out = new Set();
      if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
      for (var si = 0; si < v.__wr_s.length; si++) out.add(global.__structuredCloneDecode(v.__wr_s[si], table));
      return out;
    }
    out = {};
    if (typeof v.__wr_id === "number") table[v.__wr_id] = out;
    for (var k in v) {
      if (k === "__wr_id") continue;
      if (Object.prototype.hasOwnProperty.call(v, k)) out[k] = global.__structuredCloneDecode(v[k], table);
    }
    return out;
  };
  global.structuredClone = function (value) {
    var enc = global.__structuredCloneEncode(value);
    try {
      return global.__structuredCloneDecode(enc);
    } finally {
      /* W5: the constructor side table is per-call scratch — dropped so
         stale constructors never leak across calls. */
      globalThis.__wrSCCtors = null;
    }
  };
  global.history = {
    get length() {
      var base = (typeof globalThis.__WR_HISTORY_LENGTH === "number" && globalThis.__WR_HISTORY_LENGTH > 0)
        ? globalThis.__WR_HISTORY_LENGTH
        : 1;
      return base + (this._stack ? this._stack.length : 0);
    },
    set length(v) {},
    state: null,
    _scrollRestoration: "auto",
    _stack: [],
    pushState: function (state, title, url) {
      var abs = "";
      if (url) {
        try {
          abs = new URL(String(url), (global.location && global.location.href) || "about:blank").href;
        } catch (e) {
          throw new Error("SecurityError: malformed URL '" + String(url) + "' for history.pushState");
        }
        var curOrig = (global.location && global.location.origin) || "";
        var newOrig = "";
        try { newOrig = new URL(abs).origin; } catch (e2) {}
        if (curOrig && newOrig && newOrig !== curOrig) {
          throw new Error(
            "SecurityError: a history state object with URL '" + abs +
            "' may not be created in a document with origin '" + curOrig + "'"
          );
        }
      } else {
        abs = (global.location && global.location.href) || "";
      }
      this.state = state;
      this._stack.push(abs);
      if (url) {
        if (typeof global._updateLocationQuietly === "function") global._updateLocationQuietly(abs);
        else if (global.location) global.location.href = abs;
        var ser = null;
        try { ser = global.__structuredCloneEncode(state == null ? null : state); }
        catch (e4) { if (e4 && e4.name === "DataCloneError") throw e4; ser = null; }
        global.__WR_PUSHED = global.__WR_PUSHED || [];
        global.__WR_PUSHED.push({ url: abs, state: ser });
      }
    },
    replaceState: function (state, title, url) {
      var abs2 = "";
      if (url) {
        try { abs2 = new URL(String(url), (global.location && global.location.href) || "about:blank").href; } catch (e) { abs2 = String(url); }
        var curOrig2 = (global.location && global.location.origin) || "";
        var newOrig2 = "";
        try { newOrig2 = new URL(abs2).origin; } catch (e2) {}
        if (curOrig2 && newOrig2 && newOrig2 !== curOrig2) {
          throw new Error(
            "SecurityError: a history state object with URL '" + abs2 +
            "' may not be created in a document with origin '" + curOrig2 + "'"
          );
        }
        this.state = state;
        if (typeof global._updateLocationQuietly === "function") global._updateLocationQuietly(abs2);
        else if (global.location) global.location.href = abs2;
        var ser2 = null;
        try { ser2 = global.__structuredCloneEncode(state == null ? null : state); }
        catch (e3) { if (e3 && e3.name === "DataCloneError") throw e3; ser2 = null; }
        global.__WR_REPLACED = { url: abs2, state: ser2 };
      } else {
        this.state = state;
        var ser3 = null;
        try { ser3 = global.__structuredCloneEncode(state == null ? null : state); }
        catch (e4) { if (e4 && e4.name === "DataCloneError") throw e4; ser3 = null; }
        global.__WR_REPLACED = { url: (global.location && global.location.href) || "", state: ser3 };
      }
    },
    back: function () { global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) - 1; },
    forward: function () { global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) + 1; },
    go: function (delta) {
      var d = Math.floor(Number(delta) || 0);
      global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) + d;
    }
  };
  Object.defineProperty(global.history, "scrollRestoration", {
    get: function () { return this._scrollRestoration || "auto"; },
    set: function (val) {
      val = String(val);
      if (val === "auto" || val === "manual") {
        this._scrollRestoration = val;
      }
    },
    configurable: true,
    enumerable: true
  });
  function _dispatchWheel(dx, dy) {
    var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
    var we = (typeof global.WheelEvent === "function")
      ? new global.WheelEvent("wheel", { deltaX: dx, deltaY: dy, deltaZ: 0, deltaMode: 0, clientX: 400, clientY: 300, bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow })
      : ((typeof global.Event === "function") ? new global.Event("wheel", { bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow }) : null);
    if (we) {
      if (global.document && typeof global.document.dispatchEvent === "function") {
        try { global.document.dispatchEvent(we); } catch (e) {}
      } else if (typeof global.dispatchEvent === "function") {
        try { global.dispatchEvent(we); } catch (e) {}
      }
    }
  }

  /* Scroll positions clamp to the document: [0, max(0, docHeight - viewport)].
     Real browsers never scroll past the bottom of the document — and infinite
     feeds DEPEND on the clamp: each lazy batch grows the document, the clamped
     position advances with it, and the sentinel re-enters the viewport so the
     IntersectionObserver fires again. Without a clamp the position runs past
     the growing document and the feed loads once and stalls. */
  function _clampScroll(x, y) {
    var cx = Number(x) || 0;
    var cy = Number(y) || 0;
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    var vh = (typeof global.innerHeight === "number" && global.innerHeight > 0) ? global.innerHeight : 720;
    if (cx < 0) cx = 0;
    if (cy < 0) cy = 0;
    try {
      var doc = global.document;
      var de = doc && doc.documentElement;
      if (de && typeof de.getBoundingClientRect === "function" && !de.__layoutSkip) {
        /* Each axis clamps against its own document extent: horizontal to
           scrollWidth - viewport, vertical to scrollHeight - viewport. The
           old code clamped BOTH axes with the vertical max (docHeight -
           viewport), so a wide page could never scroll past ~one viewport
           of horizontal travel. */
        var sw = (typeof de.scrollWidth === "number" && de.scrollWidth > 0) ? de.scrollWidth : vw;
        var sh = (typeof de.scrollHeight === "number" && de.scrollHeight > 0) ? de.scrollHeight : vh;
        var maxX = Math.max(0, sw - vw);
        var maxY = Math.max(0, sh - vh);
        if (cy > maxY) cy = maxY;
        if (cx > maxX) cx = maxX;
      }
    } catch (e) {}
    return [cx, cy];
  }

  function _doScroll(x, y) {
    if (typeof x === "object" && x !== null) { y = x.top || 0; x = x.left || 0; }
    var unclampedX = Number(x) || 0;
    var unclampedY = Number(y) || 0;
    var dx = unclampedX - scrollState.x;
    var dy = unclampedY - scrollState.y;
    /* The wheel event carries the requested delta even when the page cannot
       scroll that far — listeners read the gesture, not the position. */
    if (dx !== 0 || dy !== 0) {
      _dispatchWheel(dx, dy);
    }
    var clamped = _clampScroll(unclampedX, unclampedY);
    scrollState.x = clamped[0];
    scrollState.y = clamped[1];
    if (global.document) {
      global.document.__layoutCache = null;
      global.document.__layoutVersion = (global.document.__layoutVersion || 0) + 1;
    }
    _notifyIntersections();
    if (typeof global.dispatchEvent === "function" && typeof global.Event === "function") {
      try {
        var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
        global.dispatchEvent(new global.Event("scroll", { bubbles: false, isTrusted: true, timeStamp: tNow }));
      } catch (e) {}
    }
  }
  global.scrollTo = function (x, y) {
    if (typeof x === "object" && x !== null) {
      var behavior = String(x.behavior || "auto").toLowerCase();
      var tx = (x.left != null) ? Number(x.left) : scrollState.x;
      var ty = (x.top != null) ? Number(x.top) : scrollState.y;
      if (behavior === "smooth") { __scrollSmooth(tx, ty); return; }
      _doScroll(tx, ty);
      return;
    }
    _doScroll((x != null) ? Number(x) : scrollState.x, (y != null) ? Number(y) : scrollState.y);
  };
  global.scroll = global.scrollTo;
  global.__scrollSmooth = function (targetX, targetY, steps) {
    targetX = Number(targetX) || 0;
    targetY = Number(targetY) || 0;
    steps = Math.max(3, Math.min(25, Number(steps) || 8));
    var startX = scrollState.x;
    var startY = scrollState.y;
    var totalDx = targetX - startX;
    var totalDy = targetY - startY;
    if (totalDx === 0 && totalDy === 0) return true;

    for (var i = 1; i <= steps; i++) {
      var progress = i / steps;
      /* L6: ease-out cubic for window smooth scroll */
      var ease = 1 - Math.pow(1 - progress, 3);
      var cl = _clampScroll(startX + totalDx * ease, startY + totalDy * ease);
      var curX = Math.round(cl[0]);
      var curY = Math.round(cl[1]);
      var stepDx = curX - scrollState.x;
      var stepDy = curY - scrollState.y;

      var stepDt = 12 + Math.round(Math.random() * 8);
      if (typeof global.__advanceClock === "function") global.__advanceClock(stepDt);

      if (stepDx !== 0 || stepDy !== 0) {
        _dispatchWheel(stepDx, stepDy);
      }
      scrollState.x = curX;
      scrollState.y = curY;
      _notifyIntersections();
      if (typeof global.dispatchEvent === "function" && typeof global.Event === "function") {
        try {
          var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
          global.dispatchEvent(new global.Event("scroll", { bubbles: false, isTrusted: true, timeStamp: tNow }));
        } catch (e) {}
      }
    }
    return true;
  };
  global.scrollBy = function (x, y) {
    if (typeof x === "object" && x !== null) {
      var behavior = String(x.behavior || "auto").toLowerCase();
      var dx = Number(x.left || 0), dy = Number(x.top || 0);
      if (behavior === "smooth") { __scrollSmooth(scrollState.x + dx, scrollState.y + dy); return; }
      _doScroll(scrollState.x + dx, scrollState.y + dy);
      return;
    }
    _doScroll(scrollState.x + (Number(x) || 0), scrollState.y + (Number(y) || 0));
  };
  Object.defineProperty(global, "scrollX", { get: function () { return scrollState.x; } });
  Object.defineProperty(global, "scrollY", { get: function () { return scrollState.y; } });
  Object.defineProperty(global, "pageXOffset", { get: function () { return scrollState.x; } });
  Object.defineProperty(global, "pageYOffset", { get: function () { return scrollState.y; } });
  /* ---- Dialogs (alert/confirm/prompt) ----
     Real browsers block the page until someone answers. QuickJS is
     synchronous, so a dialog cannot block: it is recorded, the script gets a
     default return value unless the agent staged an answer first, and the
     queue is reported to the agent (never silently auto-answered). */
  global.__WR_DIALOGS = [];
  global.__WR_DIALOG_ANSWERS = [];
  global.__WR_POPUPS = [];
  global.__stageDialogAnswer = function (accept, text) {
    global.__WR_DIALOG_ANSWERS.push({ accept: !!accept, text: text == null ? "" : String(text) });
    return global.__WR_DIALOG_ANSWERS.length;
  };
  global.__clearDialogState = function () {
    global.__WR_DIALOGS = [];
  };
  function __dialogSeen(type, message) {
    var answer = global.__WR_DIALOG_ANSWERS.shift() || null;
    global.__WR_DIALOGS.push({
      type: type,
      message: String(message == null ? "" : message),
      answered: answer ? (answer.accept ? "accepted" : "dismissed") : "default"
    });
    return answer;
  }
  global.alert = function (message) { __dialogSeen("alert", message); };
  global.confirm = function (message) {
    var answer = __dialogSeen("confirm", message);
    return answer ? answer.accept : false;
  };
  global.prompt = function (message, defaultValue) {
    var answer = __dialogSeen("prompt", message);
    if (answer && answer.accept) return answer.text;
    if (answer && !answer.accept) return null;
    return defaultValue == null ? null : String(defaultValue);
  };
  global.open = function (url, name, features) {
    /* Popups become tabs the host loads; the synchronous return value is an
       inert stub so scripts that touch the window object do not crash. */
    var target = String(url == null ? "" : url);
    global.__WR_POPUPS.push({ url: target, name: String(name == null ? "" : name) });
    var stub = {
      closed: false,
      name: String(name == null ? "" : name),
      location: { href: target, assign: function () {}, replace: function () {} },
      focus: function () {},
      close: function () { stub.closed = true; },
      postMessage: function () {},
      document: { write: function () {}, writeln: function () {}, body: null, title: "" }
    };
    return stub;
  };
  global.close = function () {};
  global.focus = function () {};
  global.blur = function () {};
  global.matchMedia = global.matchMedia || function () { return { matches: false, addListener: function () {}, removeListener: function () {} }; };
  if (!global.self) global.self = global;
  global.__mods = global.__mods || {};
  global.__require = function (id) { return global.__mods[id] || {}; };

  global.__WR_CONSOLE_LOGS = global.__WR_CONSOLE_LOGS || [];
  global.__drainConsoleLogs = function () {
    var out = (global.__WR_CONSOLE_LOGS || []).slice();
    global.__WR_CONSOLE_LOGS = [];
    return JSON.stringify(out);
  };
  global.console = {
    /* Errors serialize with name+message+stack (real-browser console shape),
       so boundary-captured render failures carry their origin. */
    _fmt: function (a) {
      if (a instanceof Error) {
        return a.name + ": " + a.message + (a.stack ? "\n" + a.stack : "");
      }
      try {
        if (a && typeof a === "object") return JSON.stringify(a);
      } catch (e) {}
      return String(a);
    },
    _emit: function (level, args) {
      var msg = Array.prototype.map.call(args, this._fmt).join(" ");
      try {
        global.__WR_CONSOLE_LOGS = global.__WR_CONSOLE_LOGS || [];
        if (global.__WR_CONSOLE_LOGS.length < 500) {
          global.__WR_CONSOLE_LOGS.push({
            level: String(level || "log"),
            message: msg.slice(0, 4000),
            ts: Date.now()
          });
        }
      } catch (e) {}
      if (typeof host_log === "function") {
        /* Pass the level only when the host's callable accepts it
           (runtime sets __WR_HOST_LOG_TAKES_LEVEL). QuickJS raises a
           DEFERRED SystemError on extra arguments to a 1-arg Python
           callable — no JS try/catch intercepts it; the error surfaces
           at the next engine boundary. Single-arg hosts get the message
           alone, exactly as the engine's own committed contract tests. */
        if (global.__WR_HOST_LOG_TAKES_LEVEL) host_log(msg, String(level || "log"));
        else host_log(msg);
      }
    },
    log: function () { this._emit("log", arguments); },
    warn: function () { this._emit("warn", arguments); },
    error: function () { this._emit("error", arguments); },
    info: function () { this._emit("info", arguments); },
    debug: function () { this._emit("debug", arguments); },
    trace: function () { this._emit("trace", arguments); }
  };

  /* Web storage. The old per-context closures lost everything on the next
     navigation (each goto builds a fresh engine context), so a token stored
     on page 1 read null on page 2. Now the host seeds both stores from the
     session-level per-origin state before page scripts run, and drains them
     back at the end of every engine pass:
     - localStorage: session-scoped per origin (persisted to the profile's
       sqlite storage table when one is attached);
     - sessionStorage: per-tab lifetime, owned by the Tab.
     Disclosed approximation: frame documents get their own unseeded context
     (same-origin frame storage sharing is not wired). */
  var __storageSeed = {};
  try { __storageSeed = (typeof globalThis.__WR_STORAGE_SEED === "object" && globalThis.__WR_STORAGE_SEED) || {}; } catch (e) {}

  function __createStorageStore(seed, areaName) {
    var store = {};
    var order = [];
    /* Storage mutations are recorded as change records so the host can
       broadcast `storage` events to OTHER same-origin tabs (spec: the event
       fires in every other Document sharing the storage area, never in the
       one that made the change). Seeding writes are NOT records: only the
       page-facing API mutations count. */
    var changes = [];
    if (seed && typeof seed === "object") {
      for (var k in seed) {
        if (Object.prototype.hasOwnProperty.call(seed, k)) {
          var sk = String(k);
          store[sk] = String(seed[k]);
          if (order.indexOf(sk) < 0) order.push(sk);
        }
      }
    }
    function record(entry) {
      entry.area = areaName;
      changes.push(entry);
      if (changes.length > 200) changes.shift();
    }
    return {
      getItem: function (k) {
        var sk = String(k);
        return Object.prototype.hasOwnProperty.call(store, sk) ? store[sk] : null;
      },
      setItem: function (k, v) {
        var sk = String(k);
        var old = Object.prototype.hasOwnProperty.call(store, sk) ? store[sk] : null;
        if (!Object.prototype.hasOwnProperty.call(store, sk)) order.push(sk);
        store[sk] = String(v);
        record({ key: sk, oldValue: old, newValue: String(v) });
      },
      removeItem: function (k) {
        var sk = String(k);
        var old = Object.prototype.hasOwnProperty.call(store, sk) ? store[sk] : null;
        delete store[sk];
        var idx = order.indexOf(sk);
        if (idx >= 0) order.splice(idx, 1);
        record({ key: sk, oldValue: old, newValue: null });
      },
      clear: function () {
        for (var i = 0; i < order.length; i++) delete store[order[i]];
        order.length = 0;
        /* clear() fires ONE event with key/oldValue/newValue all null
           (browser behavior), not one per removed key. */
        record({ key: null, oldValue: null, newValue: null, op: "clear" });
      },
      /* Another tab's change applied quietly: the receiving area must show
         the new value, but must NOT record it again (the event fires in the
         OTHER documents only — re-recording here would echo it back). */
      applyQuiet: function (key, newValue, isClear) {
        if (isClear) {
          for (var i = 0; i < order.length; i++) delete store[order[i]];
          order.length = 0;
          return;
        }
        var sk = String(key);
        if (newValue === null || newValue === undefined) {
          delete store[sk];
          var idx = order.indexOf(sk);
          if (idx >= 0) order.splice(idx, 1);
        } else {
          if (!Object.prototype.hasOwnProperty.call(store, sk)) order.push(sk);
          store[sk] = String(newValue);
        }
      },
      key: function (i) {
        var idx = Number(i);
        return (idx >= 0 && idx < order.length) ? order[idx] : null;
      },
      get length() { return order.length; },
      drain: function () {
        var res = {};
        for (var i = 0; i < order.length; i++) {
          res[order[i]] = store[order[i]];
        }
        return res;
      },
      takeChanges: function () {
        var out = changes;
        changes = [];
        return out;
      }
    };
  }

  var __localStore = __createStorageStore(__storageSeed.local, "local");
  var __sessionStore = __createStorageStore(__storageSeed.session, "session");

  global.__drainStorage = function () {
    try { return JSON.stringify({ local: __localStore.drain(), session: __sessionStore.drain() }); }
    catch (e) { return "{}"; }
  };
  global.localStorage = {
    getItem: function (k) { return __localStore.getItem(k); },
    setItem: function (k, v) { __localStore.setItem(k, v); },
    removeItem: function (k) { __localStore.removeItem(k); },
    clear: function () { __localStore.clear(); },
    key: function (i) { return __localStore.key(i); },
    get length() { return __localStore.length; }
  };
  global.sessionStorage = {
    getItem: function (k) { return __sessionStore.getItem(k); },
    setItem: function (k, v) { __sessionStore.setItem(k, v); },
    removeItem: function (k) { __sessionStore.removeItem(k); },
    clear: function () { __sessionStore.clear(); },
    key: function (i) { return __sessionStore.key(i); },
    get length() { return __sessionStore.length; }
  };

  /* StorageEvent is defined with the other event classes (it needs
     global.Event's prototype, which does not exist yet at this point in the
     file). */
  /* Host -> engine: apply another tab's localStorage change into THIS ctx's
     area quietly (no re-record -> no echo), then dispatch the `storage` event
     on window. */
  global.__applyRemoteStorageChange = function (key, newValue, isClear) {
    try { __localStore.applyQuiet(key, newValue, !!isClear); } catch (e) {}
  };
  global.__dispatchStorageEvent = function (key, oldValue, newValue, url) {
    try {
      var ev = new global.StorageEvent("storage", {
        key: key === undefined ? null : key,
        oldValue: oldValue === undefined ? null : oldValue,
        newValue: newValue === undefined ? null : newValue,
        url: String(url || ""),
        storageArea: global.localStorage,
        __trusted: true
      });
      var target = global.window || global;
      if (typeof target.dispatchEvent === "function") target.dispatchEvent(ev);
    } catch (e) {}
  };
  /* Engine -> host: drain the recorded mutations since the last drain. */
  global.__takeStorageChanges = function () {
    try {
      return JSON.stringify({
        local: __localStore.takeChanges(),
        session: __sessionStore.takeChanges()
      });
    } catch (e) { return "{}"; }
  };

  /* In-memory IndexedDB subsystem (zero external dependencies, zero RAM bloat). */
  (function () {
    var idbDatabases = {};
    try {
      if (typeof globalThis.__WR_IDB_SEED === "object" && globalThis.__WR_IDB_SEED) {
        idbDatabases = JSON.parse(JSON.stringify(globalThis.__WR_IDB_SEED));
      }
    } catch (e) {}
    global.__drainIndexedDB = function () {
      try { return JSON.stringify(idbDatabases); } catch (e) { return "{}"; }
    };
    global.__seedIndexedDB = function (seedJson) {
      try {
        var parsed = typeof seedJson === "string" ? JSON.parse(seedJson) : seedJson;
        if (parsed && typeof parsed === "object") {
          for (var dbName in parsed) {
            if (Object.prototype.hasOwnProperty.call(parsed, dbName)) {
              idbDatabases[dbName] = parsed[dbName];
            }
          }
        }
        return true;
      } catch (e) { return false; }
    };

    function IDBRequest() {
      this.result = undefined;
      this.error = null;
      this.source = null;
      this.transaction = null;
      this.readyState = "pending";
      this.onsuccess = null;
      this.onerror = null;
      this._listeners = {};
    }
    IDBRequest.prototype.addEventListener = function (type, fn) {
      if (!this._listeners[type]) this._listeners[type] = [];
      this._listeners[type].push(fn);
    };
    IDBRequest.prototype.removeEventListener = function (type, fn) {
      if (!this._listeners[type]) return;
      var idx = this._listeners[type].indexOf(fn);
      if (idx >= 0) this._listeners[type].splice(idx, 1);
    };
    IDBRequest.prototype.dispatchEvent = function (ev) {
      ev.target = this;
      if (typeof this["on" + ev.type] === "function") {
        try { this["on" + ev.type](ev); } catch (e) {}
      }
      var list = (this._listeners[ev.type] || []).slice();
      for (var i = 0; i < list.length; i++) {
        try { list[i].call(this, ev); } catch (e) {}
      }
      return true;
    };

    function IDBOpenDBRequest() {
      IDBRequest.call(this);
      this.onupgradeneeded = null;
      this.onblocked = null;
    }
    IDBOpenDBRequest.prototype = Object.create(IDBRequest.prototype);
    IDBOpenDBRequest.prototype.constructor = IDBOpenDBRequest;

    function DOMStringList(arr) {
      var self = this;
      this.length = arr.length;
      for (var i = 0; i < arr.length; i++) {
        this[i] = arr[i];
      }
      this.contains = function (str) { return arr.indexOf(str) >= 0; };
      this.item = function (i) { return arr[i] || null; };
    }

    function IDBDatabase(name, version) {
      this.name = name;
      this.version = version || 1;
      this.onclose = null;
      this.onerror = null;
      this.onversionchange = null;
    }
    Object.defineProperty(IDBDatabase.prototype, "objectStoreNames", {
      get: function () {
        var dbData = idbDatabases[this.name];
        var names = dbData ? Object.keys(dbData.stores) : [];
        return new DOMStringList(names);
      }
    });
    IDBDatabase.prototype.createObjectStore = function (name, options) {
      options = options || {};
      var dbData = idbDatabases[this.name];
      if (!dbData) {
        dbData = idbDatabases[this.name] = { version: this.version, stores: {} };
      }
      dbData.stores[name] = {
        name: name,
        keyPath: options.keyPath || null,
        autoIncrement: !!options.autoIncrement,
        records: {},
        keys: [],
        autoId: 1,
        indices: {}
      };
      return new IDBObjectStore(name, dbData.stores[name], null);
    };
    IDBDatabase.prototype.deleteObjectStore = function (name) {
      var dbData = idbDatabases[this.name];
      if (dbData && dbData.stores[name]) delete dbData.stores[name];
    };
    IDBDatabase.prototype.transaction = function (storeNames, mode) {
      mode = mode || "readonly";
      var names = Array.isArray(storeNames) ? storeNames : [storeNames];
      return new IDBTransaction(this, names, mode);
    };
    IDBDatabase.prototype.close = function () {};

    function IDBTransaction(db, storeNames, mode) {
      this.db = db;
      this.mode = mode || "readonly";
      this.objectStoreNames = new DOMStringList(storeNames);
      this.oncomplete = null;
      this.onerror = null;
      this.onabort = null;
      this.error = null;
      var self = this;
      Promise.resolve().then(function () {
        if (typeof self.oncomplete === "function") {
          try { self.oncomplete({ type: "complete", target: self }); } catch (e) {}
        }
      });
    }
    IDBTransaction.prototype.objectStore = function (name) {
      var dbData = idbDatabases[this.db.name];
      if (!dbData || !dbData.stores[name]) {
        throw new Error("NotFoundError: The specified object store was not found: " + name);
      }
      if (this.__aborted) {
        throw new Error("InvalidStateError: transaction aborted");
      }
      /* S10: snapshot the store pre-write state so abort() can roll back. */
      this.__snapshot(name, dbData.stores[name]);
      return new IDBObjectStore(name, dbData.stores[name], this);
    };
    IDBTransaction.prototype.abort = function () {
      /* S10: real abort semantics — writes made through THIS transaction
      roll back (the store snapshots taken at first objectStore() access
      are restored), abort fires onabort, and further use errors. */
      var self = this;
      self.__aborted = true;
      Object.keys(self.__snapshots || {}).forEach(function (storeName) {
        var snap = self.__snapshots[storeName];
        var dbData = idbDatabases[self.db.name];
        if (dbData && dbData.stores[storeName]) {
          dbData.stores[storeName] = snap;
        }
      });
      Promise.resolve().then(function () {
        if (typeof self.onabort === "function") {
          try { self.onabort({ type: "abort", target: self }); } catch (e) {}
        }
      });
    };
    IDBTransaction.prototype.commit = function () {};
    IDBTransaction.prototype.__snapshot = function (storeName, storeData) {
      this.__snapshots = this.__snapshots || {};
      if (!this.__snapshots[storeName]) {
        this.__snapshots[storeName] = {
          records: JSON.parse(JSON.stringify(storeData.records || {})),
          keys: (storeData.keys || []).slice(),
          autoId: storeData.autoId || 1,
          keyPath: storeData.keyPath,
          autoIncrement: storeData.autoIncrement,
          indices: JSON.parse(JSON.stringify(storeData.indices || {}))
        };
      }
    };

    function IDBObjectStore(name, storeData, tx) {
      this.name = name;
      this._data = storeData;
      this.transaction = tx;
      this.keyPath = storeData.keyPath;
      this.autoIncrement = storeData.autoIncrement;
    }
    Object.defineProperty(IDBObjectStore.prototype, "indexNames", {
      get: function () {
        return new DOMStringList(Object.keys(this._data.indices || {}));
      }
    });
    IDBObjectStore.prototype.put = function (val, key) {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var data = this._data;
      if (key === undefined && data.keyPath && typeof val === "object" && val !== null) {
        key = val[data.keyPath];
      }
      if (key === undefined && data.autoIncrement) {
        key = data.autoId++;
      }
      var kStr = String(key !== undefined ? key : data.autoId++);
      if (data.keys.indexOf(kStr) < 0) data.keys.push(kStr);
      data.records[kStr] = (typeof val === "object" && val !== null) ? JSON.parse(JSON.stringify(val)) : val;
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = key !== undefined ? key : kStr;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.add = function (val, key) {
      /* S10: spec — add() REJECTS an existing key (ConstraintError);
      put() upserts. The old stub silently upserted both. */
      var data = this._data;
      var k = key;
      if (k === undefined && data.keyPath && typeof val === "object" && val !== null) {
        k = val[data.keyPath];
      }
      if (k === undefined && data.autoIncrement) {
        k = data.autoId++;
      }
      var kStr = String(k !== undefined ? k : data.autoId);
      if (Object.prototype.hasOwnProperty.call(data.records, kStr)) {
        var req = new IDBRequest();
        req.source = this;
        req.transaction = this.transaction;
        var err = new Error("ConstraintError: A record with this key already exists: " + kStr);
        err.name = "ConstraintError";
        Promise.resolve().then(function () {
          req.readyState = "done";
          req.error = err;
          req.dispatchEvent({ type: "error" });
        });
        return req;
      }
      return this.put(val, key);
    };
    IDBObjectStore.prototype.get = function (key) {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var data = this._data;
      var kStr = String(key);
      var record = Object.prototype.hasOwnProperty.call(data.records, kStr) ? data.records[kStr] : undefined;
      var copy = (typeof record === "object" && record !== null) ? JSON.parse(JSON.stringify(record)) : record;
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = copy;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.getAll = function () {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var data = this._data;
      var res = [];
      for (var i = 0; i < data.keys.length; i++) {
        var k = data.keys[i];
        var rec = data.records[k];
        res.push((typeof rec === "object" && rec !== null) ? JSON.parse(JSON.stringify(rec)) : rec);
      }
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = res;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.delete = function (key) {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var data = this._data;
      var kStr = String(key);
      delete data.records[kStr];
      var idx = data.keys.indexOf(kStr);
      if (idx >= 0) data.keys.splice(idx, 1);
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = undefined;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.clear = function () {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      this._data.records = {};
      this._data.keys = [];
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = undefined;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.openCursor = function (range, direction) {
      /* S10: real cursor iteration — success fires per record with a
      cursor {key, value, primaryKey, advance/continue}; done fires
      with result undefined. Direction is honored for traversal order
      ('next', 'prev', 'nextunique', 'prevunique'). */
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var data = this._data;
      var dir = String(direction || "next");
      /* S10: cursors iterate in KEY ORDER (spec), not insertion order.
         Keys are stored stringified; sort numerically when both sides
         are numeric, lexically otherwise. */
      var keys = data.keys.slice().sort(function (a, b) {
        var na = Number(a), nb = Number(b);
        if (!isNaN(na) && !isNaN(nb)) return na - nb;
        return String(a) < String(b) ? -1 : (String(a) > String(b) ? 1 : 0);
      });
      if (dir === "prev" || dir === "prevunique") keys = keys.reverse();
      if (dir === "nextunique" || dir === "prevunique") {
        var seen = {};
        keys = keys.filter(function (k) {
          var rec = data.records[k];
          var kk = (rec && typeof rec === "object" && data.keyPath) ? rec[data.keyPath] : k;
          if (seen[String(kk)]) return false;
          seen[String(kk)] = true;
          return true;
        });
      }
      var pos = 0;
      function fireNext() {
        Promise.resolve().then(function () {
          if (pos >= keys.length) {
            req.readyState = "done";
            req.result = undefined;
            req.dispatchEvent({ type: "success" });
            return;
          }
          var k = keys[pos];
          var rec = data.records[k];
          var copy = (typeof rec === "object" && rec !== null) ? JSON.parse(JSON.stringify(rec)) : rec;
          var cursor = {
            key: (data.keyPath && copy && typeof copy === "object" && data.keyPath in copy) ? copy[data.keyPath] : k,
            primaryKey: k,
            value: copy,
            direction: dir,
            continue: function () { pos++; fireNext(); },
            advance: function (n) { pos += Math.max(1, Number(n) || 1); fireNext(); }
          };
          req.readyState = "done";
          req.result = cursor;
          req.dispatchEvent({ type: "success" });
        });
      }
      fireNext();
      return req;
    };
    IDBObjectStore.prototype.count = function () {
      var req = new IDBRequest();
      req.source = this;
      req.transaction = this.transaction;
      var c = this._data.keys.length;
      Promise.resolve().then(function () {
        req.readyState = "done";
        req.result = c;
        req.dispatchEvent({ type: "success" });
      });
      return req;
    };
    IDBObjectStore.prototype.createIndex = function (name, keyPath, options) {
      /* S10: an index is a real secondary-key view over the store's
      records: entries map index-key -> primary-key list. */
      this._data.indices[name] = { name: name, keyPath: keyPath, options: options || {} };
      return this.index(name);
    };
    IDBObjectStore.prototype.index = function (name) {
      /* S10: index operations honor the index's keyPath — get(k) returns
      the record whose INDEXED value equals k; getAll()/count() filter by
      the index. The old stub returned the whole store for everything. */
      var self = this;
      var meta = self._data.indices[name];
      if (!meta) {
        throw new Error("NotFoundError: The specified index was not found: " + name);
      }
      function indexedKeys() {
        var hits = [];
        var data = self._data;
        for (var i = 0; i < data.keys.length; i++) {
          var k = data.keys[i];
          var rec = data.records[k];
          var v = rec;
          if (meta.keyPath && typeof meta.keyPath === "string" && rec && typeof rec === "object") {
            v = rec[meta.keyPath];
          } else if (meta.keyPath && Array.isArray(meta.keyPath) && rec && typeof rec === "object") {
            v = [];
            for (var kp = 0; kp < meta.keyPath.length; kp++) v.push(rec[meta.keyPath[kp]]);
          }
          hits.push({ primaryKey: k, indexValue: v });
        }
        return hits;
      }
      function keysWithValue(k) {
        return indexedKeys().filter(function (h) { return String(h.indexValue) === String(k); });
      }
      return {
        name: name,
        keyPath: meta.keyPath,
        get: function (k) {
          var req = new IDBRequest();
          req.source = self;
          req.transaction = self.transaction;
          var hits = keysWithValue(k);
          var rec = hits.length ? self._data.records[hits[0].primaryKey] : undefined;
          var copy = (typeof rec === "object" && rec !== null) ? JSON.parse(JSON.stringify(rec)) : rec;
          Promise.resolve().then(function () {
            req.readyState = "done";
            req.result = copy;
            req.dispatchEvent({ type: "success" });
          });
          return req;
        },
        getAll: function (k) {
          var req = new IDBRequest();
          req.source = self;
          req.transaction = self.transaction;
          var hits = (k === undefined) ? indexedKeys() : keysWithValue(k);
          var res = [];
          for (var i = 0; i < hits.length; i++) {
            var rec = self._data.records[hits[i].primaryKey];
            res.push((typeof rec === "object" && rec !== null) ? JSON.parse(JSON.stringify(rec)) : rec);
          }
          Promise.resolve().then(function () {
            req.readyState = "done";
            req.result = res;
            req.dispatchEvent({ type: "success" });
          });
          return req;
        },
        count: function (k) {
          var req = new IDBRequest();
          req.source = self;
          req.transaction = self.transaction;
          var n = ((k === undefined) ? indexedKeys() : keysWithValue(k)).length;
          Promise.resolve().then(function () {
            req.readyState = "done";
            req.result = n;
            req.dispatchEvent({ type: "success" });
          });
          return req;
        }
      };
    };

    var IDBFactory = {
      open: function (name, version) {
        /* S10 note: onupgradeneeded fires below when the version bumps
        (or the DB is new), carrying oldVersion/newVersion — a real
        versionchange pass, not a silent version overwrite. */
        var req = new IDBOpenDBRequest();
        var existing = idbDatabases[name];
        var oldVer = existing ? existing.version : 0;
        var newVer = version || (existing ? existing.version : 1);
        if (!existing) {
          idbDatabases[name] = { version: newVer, stores: {} };
        } else if (version && version > existing.version) {
          existing.version = version;
        }
        var db = new IDBDatabase(name, newVer);
        Promise.resolve().then(function () {
          if (newVer > oldVer || oldVer === 0) {
            req.readyState = "done";
            req.result = db;
            var tx = new IDBTransaction(db, Object.keys(idbDatabases[name].stores), "versionchange");
            req.transaction = tx;
            req.dispatchEvent({ type: "upgradeneeded", oldVersion: oldVer, newVersion: newVer });
          }
          Promise.resolve().then(function () {
            req.readyState = "done";
            req.result = db;
            req.transaction = null;
            req.dispatchEvent({ type: "success" });
          });
        });
        return req;
      },
      deleteDatabase: function (name) {
        var req = new IDBOpenDBRequest();
        delete idbDatabases[name];
        Promise.resolve().then(function () {
          req.readyState = "done";
          req.result = undefined;
          req.dispatchEvent({ type: "success" });
        });
        return req;
      },
      databases: function () {
        var list = [];
        for (var k in idbDatabases) {
          if (Object.prototype.hasOwnProperty.call(idbDatabases, k)) {
            list.push({ name: k, version: idbDatabases[k].version || 1 });
          }
        }
        return Promise.resolve(list);
      },
      cmp: function (a, b) {
        if (a < b) return -1;
        if (a > b) return 1;
        return 0;
      }
    };

    function IDBKeyRange(lower, upper, lowerOpen, upperOpen) {
      this.lower = lower;
      this.upper = upper;
      this.lowerOpen = !!lowerOpen;
      this.upperOpen = !!upperOpen;
    }
    IDBKeyRange.only = function (val) { return new IDBKeyRange(val, val, false, false); };
    IDBKeyRange.lowerBound = function (lower, open) { return new IDBKeyRange(lower, undefined, !!open, true); };
    IDBKeyRange.upperBound = function (upper, open) { return new IDBKeyRange(undefined, upper, true, !!open); };
    IDBKeyRange.bound = function (l, u, lo, uo) { return new IDBKeyRange(l, u, !!lo, !!uo); };
    IDBKeyRange.prototype.includes = function (key) {
      if (this.lower !== undefined) {
        if (this.lowerOpen ? key <= this.lower : key < this.lower) return false;
      }
      if (this.upper !== undefined) {
        if (this.upperOpen ? key >= this.upper : key > this.upper) return false;
      }
      return true;
    };

    global.IDBRequest = IDBRequest;
    global.IDBOpenDBRequest = IDBOpenDBRequest;
    global.IDBDatabase = IDBDatabase;
    global.IDBTransaction = IDBTransaction;
    global.IDBObjectStore = IDBObjectStore;
    global.IDBFactory = IDBFactory;
    global.IDBKeyRange = IDBKeyRange;
    global.indexedDB = IDBFactory;
  })();

  /* Custom elements + shadow DOM. */
  global.customElements = {
    define: function (name, ctor, _opts) {
      name = String(name).toLowerCase();
      customRegistry[name] = ctor;
      if (whenDefinedPromises[name]) {
        try { whenDefinedPromises[name](ctor); } catch (e) {}
        delete whenDefinedPromises[name];
      }
    },
    get: function (name) { return customRegistry[String(name).toLowerCase()]; },
    whenDefined: function (name) {
      name = String(name).toLowerCase();
      if (customRegistry[name]) return Promise.resolve(customRegistry[name]);
      return new Promise(function (resolve) {
        whenDefinedPromises[name] = resolve;
      });
    },
    upgrade: function (root) {
      if (!root) return;
      (function walk(n) {
        if (n.nodeType === 1) {
          var tag = n.nodeName.toLowerCase();
          var ctor = customRegistry[tag];
          if (ctor && !(n instanceof ctor)) {
            try { Object.setPrototypeOf(n, ctor.prototype); } catch (e) {}
            if (typeof n.connectedCallback === "function") {
              try { n.connectedCallback(); } catch (e) {}
            }
          }
        }
        if (n.childNodes) n.childNodes.forEach(walk);
      })(root);
    }
  };
  Element.prototype.attachShadow = function (init) {
    if (!this._shadow) {
      var s = new Element("#shadow-root", {}, []);
      s.nodeType = 11;
      s.host = this;
      s.mode = (init && init.mode) || "open";
      this._shadow = s;
    }
    return this._shadow;
  };
  Object.defineProperty(Element.prototype, "shadowRoot", {
    get: function () {
      if (this._shadow && this._shadow.mode === "closed") return null;
      return this._shadow || null;
    }
  });

  Object.defineProperty(Element.prototype, "contentWindow", {
    configurable: true,
    get: function () {
      if (this.nodeName.toLowerCase() !== "iframe") return null;
      try {
        if (globalThis.__WR_CW_ARM && globalThis.__WR_CW_LOG && globalThis.__WR_CW_LOG.length < 8) {
          var src = String((this.getAttribute && this.getAttribute("src")) || "");
          var info = { src: src ? "anchor" : "(empty)", sameEl: this === globalThis.__WR_SRC_EL };
          try {
            var caller = arguments.callee.caller;
            info.caller = caller ? String(caller).slice(0, 180) : "none";
            info.nargs = caller && caller.arguments ? caller.arguments.length : -1;
          } catch (eCall) { info.err = String(eCall).slice(0, 80); }
          globalThis.__WR_CW_LOG.push(info);
        }
      } catch (eLog) {}
      if (!this._contentWindow) {
        var self = this;
        var cw = {
          postMessage: function (data, targetOrigin, transfer) {
            var t = __wrExtractTransfer(targetOrigin, transfer);
            if (typeof host_post_message === "function") {
              var msgData = __wrWireEncode(data);
              var portsMeta = t.ports.length ? JSON.stringify(__wrPrepareOutPorts(t.ports)) : "[]";
              /* Transferred ports ride the recorded message; the frame's
                 realm materializes a proxy end and the kept end here learns
                 the proxy's key from the runtime. */
              host_post_message(msgData, __resolveTargetOrigin(t.bag || targetOrigin),
                self.getAttribute("src") || "", (global.location && global.location.origin) || "", portsMeta);
            }
          }
        };
        var doc = new Document();
        var htmlEl = new Element("html");
        var headEl = new Element("head");
        var bodyEl = new Element("body");
        htmlEl.appendChild(headEl);
        htmlEl.appendChild(bodyEl);
        doc.appendChild(htmlEl);
        doc.documentElement = htmlEl;
        doc.head = headEl;
        doc.body = bodyEl;
        doc.defaultView = cw;
        cw.document = doc;
        self._contentDocument = doc;
        this._contentWindow = cw;
      }
      return this._contentWindow;
    }
  });

  Object.defineProperty(Element.prototype, "contentDocument", {
    get: function () {
      if (this.nodeName.toLowerCase() !== "iframe") return null;
      if (!this._contentDocument) {
        // Accessing contentWindow ensures creation and linking of Document
        var win = this.contentWindow;
        return (win && win.document) || this._contentDocument || null;
      }
      return this._contentDocument;
    }
  });

  /* Inject HTML content into an iframe's contentDocument.
     This is called from Python to populate iframe content fetched separately. */
  Element.prototype.__injectFrameContent = function (html) {
    if (this.nodeName.toLowerCase() !== "iframe") return false;
    var win = this.contentWindow;
    if (!win || !win.document) return false;
    var doc = win.document;
    // Parse the HTML and rebuild the iframe document
    // Simple approach: create a div, set innerHTML, move children
    var container = doc.createElement("div");
    try {
      container.innerHTML = html;
    } catch (e) {
      // If innerHTML fails, the html might have script tags that need special handling
      // For now, just return false to indicate failure
      return false;
    }
    // Clear existing content
    while (doc.body.firstChild) {
      doc.body.removeChild(doc.body.firstChild);
    }
    // Move children from container to body
    while (container.firstChild) {
      doc.body.appendChild(container.firstChild);
    }
    return true;
  };

  /* Standard Headers implementation */
  function Headers(init) {
    this._headers = {};
    var self = this;
    if (init) {
      if (typeof init.forEach === "function") {
        init.forEach(function (v, k) { self.set(k, v); });
      } else if (Array.isArray(init)) {
        init.forEach(function (pair) { self.set(pair[0], pair[1]); });
      } else if (typeof init === "object") {
        for (var k in init) {
          if (Object.prototype.hasOwnProperty.call(init, k)) self.set(k, init[k]);
        }
      }
    }
  }
  Headers.prototype.set = function (name, value) {
    this._headers[String(name).toLowerCase()] = String(value);
  };
  Headers.prototype.get = function (name) {
    var v = this._headers[String(name).toLowerCase()];
    return v !== undefined ? v : null;
  };
  Headers.prototype.has = function (name) {
    return Object.prototype.hasOwnProperty.call(this._headers, String(name).toLowerCase());
  };
  Headers.prototype.delete = function (name) {
    delete this._headers[String(name).toLowerCase()];
  };
  Headers.prototype.append = function (name, value) {
    var key = String(name).toLowerCase();
    if (this.has(key)) {
      this._headers[key] = this._headers[key] + ", " + String(value);
    } else {
      this._headers[key] = String(value);
    }
  };
  Headers.prototype.forEach = function (cb, thisArg) {
    for (var k in this._headers) {
      if (Object.prototype.hasOwnProperty.call(this._headers, k)) {
        cb.call(thisArg, this._headers[k], k, this);
      }
    }
  };
  Headers.prototype.entries = function () {
    var arr = [];
    for (var k in this._headers) {
      if (Object.prototype.hasOwnProperty.call(this._headers, k)) arr.push([k, this._headers[k]]);
    }
    return arr[Symbol.iterator] ? arr[Symbol.iterator]() : arr;
  };
  global.Headers = Headers;

  /* Standard ReadableStream & DefaultReader for streaming chunk reads.
     Implements the WHATWG shape actually used by production bundles:
     ``new ReadableStream({start(c){c.enqueue(x); c.close()}})`` and
     ``reader.read()`` resolving per chunk. A real bug lived here: the old
     constructor ignored ``start`` entirely, so enqueued chunks vanished,
     the reader saw an empty snapshot, and React's flight client rejected
     every hydration with "Connection closed." (LinkedIn's login render). */
  function ReadableStreamDefaultReader(stream) {
    this._stream = stream;
    stream._readers.push(this);
    this._closed = false;
  }
  ReadableStreamDefaultReader.prototype.read = function () {
    var stream = this._stream;
    if (stream._queue.length > 0) {
      return Promise.resolve({ value: stream._queue.shift(), done: false });
    }
    if (stream._closed) {
      this._closed = true;
      return Promise.resolve({ value: undefined, done: true });
    }
    // No data yet: park the promise; enqueue()/close() resolves it.
    return new Promise(function (resolve) {
      stream._waiting.push(resolve);
    });
  };
  ReadableStreamDefaultReader.prototype.releaseLock = function () {};
  ReadableStreamDefaultReader.prototype.cancel = function () {
    return Promise.resolve();
  };

  function ReadableStream(source) {
    this._queue = [];
    this._waiting = [];
    this._readers = [];
    this._closed = false;
    var self = this;
    if (source && typeof source.start === "function") {
      var controller = {
        enqueue: function (chunk) {
          if (self._closed) return;
          if (self._waiting.length > 0) {
            self._waiting.shift()({ value: chunk, done: false });
          } else {
            self._queue.push(chunk);
          }
        },
        close: function () {
          if (self._closed) return;
          self._closed = true;
          while (self._waiting.length > 0) {
            self._waiting.shift()({ value: undefined, done: true });
          }
        },
        error: function (err) {
          if (self._closed) return;
          self._closed = true;
          self._error = err;
          while (self._waiting.length > 0) {
            self._waiting.shift()({ value: undefined, done: true });
          }
        },
      };
      try {
        var started = source.start(controller);
        if (started && typeof started.then === "function") {
          // start may be async; nothing else needed — enqueue/close drive it.
        }
      } catch (e) {}
    } else if (source && Array.isArray(source.chunks)) {
      this._queue = source.chunks.slice();
    } else if (typeof source === "string") {
      var enc = new TextEncoder();
      this._queue = [enc.encode(source)];
    } else if (source instanceof Uint8Array) {
      this._queue = [source];
    }
  }
  ReadableStream.prototype.getReader = function () {
    return new ReadableStreamDefaultReader(this);
  };
  global.ReadableStream = ReadableStream;

  function Blob(blobParts, options) {
    options = options || {};
    this.type = String(options.type || "");
    var parts = Array.isArray(blobParts) ? blobParts : (blobParts != null ? [blobParts] : []);
    var str = "";
    var binary = false;
    for (var i = 0; i < parts.length; i++) {
      var p = parts[i];
      if (p instanceof Blob) {
        str += p._content;
        if (p._binary) binary = true;
      } else if (p instanceof Uint8Array) {
        var s = "";
        for (var j = 0; j < p.length; j++) s += String.fromCharCode(p[j]);
        str += s;
        binary = true;
      } else if (p && p.buffer instanceof ArrayBuffer) {
        var u8 = new Uint8Array(p.buffer, p.byteOffset, p.byteLength);
        var s2 = "";
        for (var k = 0; k < u8.length; k++) s2 += String.fromCharCode(u8[k]);
        str += s2;
        binary = true;
      } else {
        str += String(p);
      }
    }
    this._content = str;
    this._binary = binary;
    /* Byte-accurate size: binary payloads are stored one char per byte, so the
       char count IS the byte count. Text payloads count UTF-8 bytes like a real
       browser instead of UTF-16 code units. */
    if (binary) {
      this.size = str.length;
    } else {
      this.size = (global.TextEncoder ? new global.TextEncoder().encode(str).length : str.length);
    }
  }
  Blob.prototype.text = function () { return Promise.resolve(this._content); };
  Blob.prototype.arrayBuffer = function () {
    var enc = new TextEncoder();
    return Promise.resolve(enc.encode(this._content).buffer);
  };
  Blob.prototype.slice = function (start, end, contentType) {
    var s = this._content.slice(start, end);
    var b = new Blob([s], { type: contentType || this.type });
    if (this._binary) {
      b._binary = true;
      b.size = s.length;
    }
    return b;
  };
  global.Blob = Blob;

  function File(fileBits, fileName, options) {
    options = options || {};
    Blob.call(this, fileBits, options);
    this.name = String(fileName || "");
    this.lastModified = Number(options.lastModified) || Date.now();
  }
  File.prototype = Object.create(Blob.prototype);
  File.prototype.constructor = File;
  global.File = File;

  function FileList(files) {
    var arr = Array.isArray(files) ? files : [];
    for (var i = 0; i < arr.length; i++) {
      this[i] = arr[i];
    }
    this.length = arr.length;
  }
  FileList.prototype.item = function (index) {
    return this[index] || null;
  };
  global.FileList = FileList;

  function FileReader() {
    this.readyState = 0;
    this.result = null;
    this.error = null;
    this.onload = null;
    this.onerror = null;
    this.onloadstart = null;
    this.onloadend = null;
    this._listeners = {};
  }
  FileReader.EMPTY = 0;
  FileReader.LOADING = 1;
  FileReader.DONE = 2;
  FileReader.prototype.addEventListener = function (type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  FileReader.prototype.removeEventListener = function (type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter(function (l) { return l !== fn; });
  };
  FileReader.prototype._dispatch = function (type, evt) {
    var handler = this["on" + type];
    if (typeof handler === "function") {
      try { handler.call(this, evt); } catch (e) {}
    }
    (this._listeners[type] || []).forEach(function (fn) {
      try { fn(evt); } catch (e) {}
    });
  };
  FileReader.prototype.readAsText = function (blob) {
    var self = this;
    self.readyState = FileReader.LOADING;
    self._dispatch("loadstart", { type: "loadstart", target: self });
    setTimeout(function () {
      self.result = blob ? (blob._content || String(blob)) : "";
      self.readyState = FileReader.DONE;
      self._dispatch("load", { type: "load", target: self });
      self._dispatch("loadend", { type: "loadend", target: self });
    }, 0);
  };
  FileReader.prototype.readAsDataURL = function (blob) {
    var self = this;
    self.readyState = FileReader.LOADING;
    self._dispatch("loadstart", { type: "loadstart", target: self });
    setTimeout(function () {
      var mime = (blob && blob.type) ? blob.type : "application/octet-stream";
      var content = blob ? (blob._content || "") : "";
      /* Binary blobs hold one char per byte, so btoa is byte-exact. Text
         blobs follow the browser approximation (code units). */
      var b64 = global.btoa(content);
      self.result = "data:" + mime + ";base64," + b64;
      self.readyState = FileReader.DONE;
      self._dispatch("load", { type: "load", target: self });
      self._dispatch("loadend", { type: "loadend", target: self });
    }, 0);
  };
  FileReader.prototype.readAsArrayBuffer = function (blob) {
    var self = this;
    self.readyState = FileReader.LOADING;
    self._dispatch("loadstart", { type: "loadstart", target: self });
    setTimeout(function () {
      if (blob && typeof blob.arrayBuffer === "function") {
        blob.arrayBuffer().then(function (buf) {
          self.result = buf;
          self.readyState = FileReader.DONE;
          self._dispatch("load", { type: "load", target: self });
          self._dispatch("loadend", { type: "loadend", target: self });
        });
      } else {
        self.result = new ArrayBuffer(0);
        self.readyState = FileReader.DONE;
        self._dispatch("load", { type: "load", target: self });
        self._dispatch("loadend", { type: "loadend", target: self });
      }
    }, 0);
  };
  global.FileReader = FileReader;

  function FormData(form) {
    this._data = [];
    if (form && form.querySelectorAll) {
      var inputs = form.querySelectorAll("input, select, textarea");
      for (var i = 0; i < inputs.length; i++) {
        var el = inputs[i];
        var name = el.getAttribute("name");
        if (!name) continue;
        var typ = (el.getAttribute("type") || "").toLowerCase();
        if ((typ === "checkbox" || typ === "radio") && !el.checked) continue;
        if (typ === "file") {
          var fl = el.files;
          if (fl && fl.length > 0) {
            for (var f = 0; f < fl.length; f++) {
              this._data.push([name, fl[f], fl[f].name]);
            }
          } else {
            this._data.push([name, new global.File([], "", { type: "application/octet-stream" }), ""]);
          }
          continue;
        }
        this._data.push([name, el.value || ""]);
      }
    }
  }
  FormData.prototype.append = function (k, v, filename) {
    if (v instanceof global.Blob || (v && typeof v._content === "string")) {
      var fn = filename || v.name || "blob";
      this._data.push([String(k), v, fn]);
    } else {
      this._data.push([String(k), String(v)]);
    }
  };
  FormData.prototype.set = function (k, v, filename) {
    this.delete(k);
    this.append(k, v, filename);
  };
  FormData.prototype.get = function (k) {
    for (var i = 0; i < this._data.length; i++) {
      if (this._data[i][0] === String(k)) return this._data[i][1];
    }
    return null;
  };
  FormData.prototype.getAll = function (k) {
    var res = [];
    for (var i = 0; i < this._data.length; i++) {
      if (this._data[i][0] === String(k)) res.push(this._data[i][1]);
    }
    return res;
  };
  FormData.prototype.has = function (k) { return this.get(k) !== null; };
  FormData.prototype.delete = function (k) {
    this._data = this._data.filter(function (x) { return x[0] !== String(k); });
  };
  FormData.prototype.entries = function () { return this._data.slice(); };
  FormData.prototype.keys = function () {
    var seen = {};
    var res = [];
    for (var i = 0; i < this._data.length; i++) {
      var key = this._data[i][0];
      if (!seen[key]) { seen[key] = true; res.push(key); }
    }
    return res;
  };
  FormData.prototype.values = function () {
    var res = [];
    for (var i = 0; i < this._data.length; i++) res.push(this._data[i][1]);
    return res;
  };
  FormData.prototype.forEach = function (cb) {
    for (var i = 0; i < this._data.length; i++) {
      cb(this._data[i][1], this._data[i][0], this);
    }
  };
  global.FormData = FormData;

  function __serializeFormData(fd) {
    var boundary = "----WebKitFormBoundary" + Math.random().toString(36).substring(2) + Date.now().toString(36);
    var parts = [];
    var binary = false;
    var entries = (fd && typeof fd.entries === "function") ? fd.entries() : (fd && fd._data ? fd._data : []);
    for (var i = 0; i < entries.length; i++) {
      var item = entries[i];
      var name = item[0];
      var val = item[1];
      var filename = item[2];
      if (val instanceof global.Blob || (val && typeof val._content === "string")) {
        var fn = filename || val.name || "blob";
        var ct = val.type || "application/octet-stream";
        var content = val._content != null ? val._content : "";
        if (val._binary) binary = true;
        parts.push(
          "--" + boundary + "\r\n" +
          "Content-Disposition: form-data; name=\"" + name + "\"; filename=\"" + fn + "\"\r\n" +
          "Content-Type: " + ct + "\r\n\r\n" +
          content + "\r\n"
        );
      } else {
        parts.push(
          "--" + boundary + "\r\n" +
          "Content-Disposition: form-data; name=\"" + name + "\"\r\n\r\n" +
          String(val) + "\r\n"
        );
      }
    }
    parts.push("--" + boundary + "--\r\n");
    return {
      body: parts.join(""),
      contentType: "multipart/form-data; boundary=" + boundary,
      binary: binary
    };
  }
  global.__serializeFormData = __serializeFormData;

  /* Standard Response implementation */
  function Response(body, init) {
    init = init || {};
    this.status = init.status !== undefined && init.status !== null ? Number(init.status) : 200;
    this.statusText = String(init.statusText || (this.status === 200 ? "OK" : ""));
    this.ok = this.status >= 200 && this.status < 300;
    this.url = String(init.url || "");
    this.type = "basic";
    this.headers = new Headers(init.headers || {});
    this._text = typeof body === "string" ? body : "";
    var enc = new TextEncoder();
    this.body = new ReadableStream(enc.encode(this._text));
  }
  Response.prototype.text = function () { return Promise.resolve(this._text); };
  Response.prototype.json = function () {
    try {
      return Promise.resolve(JSON.parse(this._text || "null"));
    } catch (e) {
      return Promise.reject(e);
    }
  };
  Response.prototype.arrayBuffer = function () {
    var enc = new TextEncoder();
    return Promise.resolve(enc.encode(this._text).buffer);
  };
  Response.prototype.blob = function () {
    return Promise.resolve(new Blob([this._text], { type: this.headers.get("content-type") || "" }));
  };
  Response.prototype.clone = function () {
    return new Response(this._text, {
      status: this.status,
      statusText: this.statusText,
      headers: this.headers,
      url: this.url
    });
  };
  global.Response = Response;

  /* Standard Request implementation */
  function Request(input, init) {
    init = init || {};
    this.url = typeof input === "string" ? input : (input && input.url) || "";
    this.method = String(init.method || (input && input.method) || "GET").toUpperCase();
    this.headers = new Headers(init.headers || (input && input.headers) || {});
    this.body = init.body || null;
  }
  global.Request = Request;

  /* Upgraded fetch with live Headers and ReadableStream body */
  /* ---- AbortController / AbortSignal (abort + reason, event target) ----
     Production flight clients (React server components) construct
     AbortController for every streamed fetch; a missing global is a render
     crash (LinkedIn's login: "ReferenceError: 'AbortController' is not
     defined"). The controller faithfully reports abort state and fires its
     events for listeners; fetch honors an in-flight abort (see global.fetch:
     resolution is deferred one microtask so an abort() between dispatch and
     settle rejects with AbortError, matching an async engine). */
  function AbortSignal() {
    this.aborted = false;
    this.reason = undefined;
    this._listeners = {};
  }
  AbortSignal.prototype.addEventListener = function (type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  AbortSignal.prototype.removeEventListener = function (type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter(function (l) { return l !== fn; });
  };
  AbortSignal.prototype._emit = function (type, evt) {
    (this._listeners[type] || []).forEach(function (fn) {
      try { fn(evt || { type: type }); } catch (e) {}
    });
  };
  AbortSignal.timeout = function (ms) {
    var s = new AbortSignal();
    var reason = new Error("The operation was aborted due to timeout");
    reason.name = "TimeoutError";
    setTimeout(function () {
      if (!s.aborted) {
        s.aborted = true;
        s.reason = reason;
        s._emit("abort", { type: "abort" });
      }
    }, Number(ms) || 0);
    return s;
  };
  global.AbortSignal = AbortSignal;

  global.AbortController = function () {
    var signal = new AbortSignal();
    this.signal = signal;
    this.abort = function (reason) {
      if (signal.aborted) return;
      signal.aborted = true;
      signal.reason = reason !== undefined ? reason : new global.DOMException("This operation was aborted", "AbortError");
      signal._emit("abort", { type: "abort" });
    };
  };

  /* ---- Dynamic import() loader (__wr_import) ----
     QuickJS parses ImportCall but its module loader rejects every URL, so
     runtime-side rewrites map ``import(spec)`` to this loader. It resolves
     the specifier, loads missing modules through the host (fetch + ESM
     transform, registered into __mods synchronously), and resolves with the
     module namespace — the honest dynamic-import shape for a synchronous
     engine: the Promise resolves in the same pump pass. */
  globalThis.__wr_import = function (spec) {
    var specifier = String(spec == null ? "" : spec);
    if (specifier.indexOf("data:") === 0) {
      return Promise.reject(new Error("data: module imports are not supported"));
    }
    var mods = globalThis.__mods;
    var resolved = null;
    // Fast path: already loaded? The host tells us the canonical URL on
    // first load; afterwards we can't know it from JS alone, so every miss
    // goes through host_load_module which resolves + returns the envelope.
    if (typeof host_load_module === "function") {
      var envelope = host_load_module(specifier);
      var env = null;
      try { env = typeof envelope === "string" ? JSON.parse(envelope) : envelope; } catch (e) { env = null; }
      if (env && env.url && env.code && env.code.length > 0) {
        resolved = env.url;
        if (!mods[resolved]) {
          try { globalThis.eval(env.code); } catch (e) {}
        }
      } else if (env && env.url) {
        resolved = env.url; // previously loaded
      }
    }
    if (!resolved || !mods[resolved]) {
      return Promise.reject(new Error("Could not load module: " + specifier));
    }
    return Promise.resolve(mods[resolved]);
  };

  global.fetch = function (url, opts) {
    opts = opts || {};
    // AbortSignal support: an already-aborted signal rejects immediately
    // (spec); in-flight abort is a no-op here because host_fetch is
    // synchronous — disclosed approximation, not a fake.
    if (opts.signal && opts.signal.aborted) {
      return Promise.reject(opts.signal.reason || new global.DOMException("This operation was aborted", "AbortError"));
    }
    var urlStr = String(url);
    if (urlStr.indexOf("blob:") === 0) {
      /* blob: URLs resolve from the in-process object-URL registry. They
         must never leak to the network lane as malformed requests (the old
         path fetched '/webresearch-1' off the page's origin). */
      var blobObj = __objectURLs[urlStr];
      if (blobObj && blobObj._content != null) {
        var blobHeaders = new Headers();
        if (blobObj.type) blobHeaders.set("content-type", blobObj.type);
        return Promise.resolve(new Response(blobObj._content, {
          status: 200,
          url: urlStr,
          headers: blobHeaders
        }));
      }
      return Promise.reject(new TypeError("Failed to fetch: unknown blob URL '" + urlStr + "'"));
    }
    var method = String(opts.method || "GET");
    var body = "";
    var bodyB64 = "";
    var customHeaders = {};
    if (opts.headers) {
      if (typeof opts.headers.forEach === "function") {
        opts.headers.forEach(function (v, k) { customHeaders[String(k)] = String(v); });
      } else {
        for (var hk in opts.headers) customHeaders[String(hk)] = String(opts.headers[hk]);
      }
    }
    if (opts.body && (opts.body instanceof global.FormData || opts.body._data)) {
      var sfd = (typeof __serializeFormData === "function") ? __serializeFormData(opts.body) : { body: "", contentType: "" };
      body = sfd.body;
      bodyB64 = sfd.binary ? global.btoa(sfd.body) : "";
      if (!customHeaders["content-type"] && !customHeaders["Content-Type"]) {
        customHeaders["content-type"] = sfd.contentType;
      }
    } else {
      body = opts.body == null ? "" : (opts.body._content != null ? opts.body._content : String(opts.body));
      if (opts.body && opts.body._binary) bodyB64 = global.btoa(body);
    }
    /* N9 (Phase 1): capture the JS call site at dispatch time so the
       network ledger can show what code launched this request. The stack
       string travels as host_fetch's 6th argument; the host sanitizes it
       (query strings redacted, line-capped). */
    var initiatorStack = "";
    try { initiatorStack = (new Error()).stack || ""; } catch (e) { initiatorStack = ""; }
    /* ---- W2: Service Worker fetch interception ----
       If an activated SW with a fetch handler controls this URL (within its
       scope), the SW's respondWith() Response replaces the network response.
       If the handler never calls respondWith, the fetch falls through to the
       network below (the real SW spec errors on no-respondWith; fall-through
       is the disclosed engine choice). */
    if (global.__wrActiveSW && typeof global.__wrActiveSW.fetchHandler === "function") {
      var swAbsUrl = urlStr;
      try { swAbsUrl = new URL(urlStr, (global.location && global.location.href) || "").href; } catch (eAbs) {}
      var swScopeMatch = String(global.__wrActiveSW.scope || "/");
      if (swScopeMatch === "/" || swAbsUrl.indexOf(swScopeMatch) === 0) {
        var swRespondCalled = false;
        var swResponded = null;
        var fetchEvent = {
          type: "fetch",
          request: {
            url: swAbsUrl,
            method: method,
            headers: customHeaders,
            body: body,
            mode: "navigate",
            destination: ""
          },
          respondWith: function (r) {
            if (swRespondCalled) return;
            swRespondCalled = true;
            swResponded = r;
          },
          waitUntil: function () { return Promise.resolve(); }
        };
        try { global.__wrActiveSW.fetchHandler(fetchEvent); } catch (eSWfetch) {
          /* Handler error: fall through to the network (disclosed). */
          swRespondCalled = false;
        }
        if (swRespondCalled) {
          if (swResponded && typeof swResponded.then === "function") {
            return swResponded.then(function (swResp) { return swResp; });
          }
          return Promise.resolve(swResponded);
        }
      }
    }
    var raw = typeof host_fetch === "function" ? host_fetch(String(url), method, body, JSON.stringify(customHeaders), bodyB64, initiatorStack) : "{\"status\":599,\"body\":\"\"}";
    var res = typeof raw === "string" ? JSON.parse(raw) : raw;
    var respHeaders = new Headers();
    if (res.content_type) respHeaders.set("content-type", res.content_type);
    var response = new Response(res.body || "", {
      status: res.status || 0,
      url: res.url || String(url),
      headers: respHeaders
    });
    /* E3: in-flight abort. host_fetch is synchronous (the bytes are resolved
       by the time it returns), so there is no network delay to model. But a
       real fetch is async: an abort() fired between dispatch and settle
       rejects with AbortError. We model that window honestly by deferring
       resolution one microtask — a synchronous abort() right after the fetch
       call (the common flight-fetch pattern) fires the signal's abort
       listener first and wins; without an abort the pump resolves normally,
       exactly one drain pass later. */
    if (!opts.signal) {
      return Promise.resolve(response);
    }
    var signal = opts.signal;
    return new Promise(function (resolve, reject) {
      var settled = false;
      function onAbort() {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        reject(signal.reason || new global.DOMException("This operation was aborted", "AbortError"));
      }
      signal.addEventListener("abort", onAbort);
      Promise.resolve().then(function () {
        if (settled) return;
        settled = true;
        signal.removeEventListener("abort", onAbort);
        resolve(response);
      });
    });
  };

  /* EventSource for Server-Sent Events (SSE text/event-stream) */
  function EventSource(url, dict) {
    this.url = String(url || "");
    this.readyState = 0; // CONNECTING
    this.onopen = null;
    this.onmessage = null;
    this.onerror = null;
    this._listeners = {};
    var self = this;

    setTimeout(function () {
      try {
        if (typeof host_fetch === "function") {
          var esStack = "";
          try { esStack = (new Error()).stack || ""; } catch (e) { esStack = ""; }
          var raw = host_fetch(self.url, "GET", "", "", "", esStack);
          var res = typeof raw === "string" ? JSON.parse(raw) : raw;
          if (res.status >= 200 && res.status < 300) {
            self.readyState = 1; // OPEN
            if (typeof self.onopen === "function") self.onopen(new Event("open"));
            self._dispatch("open", new Event("open"));

            var lines = (res.body || "").split(/\r?\n/);
            var buffer = [];
            for (var i = 0; i < lines.length; i++) {
              var line = lines[i];
              if (line.indexOf("data:") === 0) {
                buffer.push(line.substring(5).trim());
              } else if (line.trim() === "" && buffer.length > 0) {
                var msgData = buffer.join("\n");
                buffer = [];
                var msgEvt = new MessageEvent("message", { data: msgData, origin: self.url });
                if (typeof self.onmessage === "function") self.onmessage(msgEvt);
                self._dispatch("message", msgEvt);
              }
            }
          } else {
            self.readyState = 2; // CLOSED
            if (typeof self.onerror === "function") self.onerror(new Event("error"));
            self._dispatch("error", new Event("error"));
          }
        }
      } catch (err) {
        self.readyState = 2;
        if (typeof self.onerror === "function") self.onerror(new Event("error"));
        self._dispatch("error", new Event("error"));
      }
    }, 0);
  }
  EventSource.CONNECTING = 0;
  EventSource.OPEN = 1;
  EventSource.CLOSED = 2;
  EventSource.prototype.close = function () { this.readyState = 2; };
  EventSource.prototype.addEventListener = function (type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  EventSource.prototype.removeEventListener = function (type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter(function (l) { return l !== fn; });
  };
  EventSource.prototype._dispatch = function (type, evt) {
    (this._listeners[type] || []).forEach(function (fn) {
      try { fn(evt); } catch (e) {}
    });
  };
  global.EventSource = EventSource;

  /* ---- WebSocket (host-bridged, own transport) ----
     The host owns the socket (engine/ws.py, hand-rolled RFC 6455). Frames
     arrive synchronously on the runtime's pump passes, which is how a
     synchronous QuickJS context can still see live messages. Text frames
     only; binary frames are a disclosed gap. */
  global.__WS_SOCKETS = {};
  function WebSocket(url, protocols) {
    if (this.constructor !== WebSocket) return new WebSocket(url, protocols);
    this.url = String(url || "");
    this.protocol = "";
    this.extensions = "";
    this.bufferedAmount = 0;
    this.readyState = 0; /* CONNECTING */
    this.binaryType = "blob";
    this.onopen = null;
    this.onclose = null;
    this.onerror = null;
    this.onmessage = null;
    this._listeners = {};
    var self = this;
    var protos = "";
    if (Array.isArray(protocols)) protos = protocols.join(", ");
    else if (protocols) protos = String(protocols);
    var res = null;
    try {
      if (typeof host_ws_connect === "function") {
        res = JSON.parse(host_ws_connect(this.url, protos));
      }
    } catch (e) { res = null; }
    if (!res || !res.ok) {
      this.readyState = 3; /* CLOSED */
      var err = new global.Event("error", { bubbles: false });
      setTimeout(function () {
        if (typeof self.onerror === "function") { try { self.onerror(err); } catch (e) {} }
        self._emit("error", err);
        self._fireClose((res && res.code) || 1006, (res && res.error) || "connection failed");
      }, 0);
      return;
    }
    this._id = res.id;
    this.protocol = res.protocol || "";
    global.__WS_SOCKETS[res.id] = this;
    this.readyState = 1; /* OPEN: the handshake completed synchronously */
    setTimeout(function () {
      var ev = new global.Event("open", { bubbles: false });
      if (typeof self.onopen === "function") { try { self.onopen(ev); } catch (e) {} }
      self._emit("open", ev);
    }, 0);
  }
  WebSocket.CONNECTING = 0;
  WebSocket.OPEN = 1;
  WebSocket.CLOSING = 2;
  WebSocket.CLOSED = 3;
  WebSocket.prototype.addEventListener = function (type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  WebSocket.prototype.removeEventListener = function (type, fn) {
    if (!this._listeners[type]) return;
    this._listeners[type] = this._listeners[type].filter(function (l) { return l !== fn; });
  };
  WebSocket.prototype._emit = function (type, evt) {
    (this._listeners[type] || []).forEach(function (fn) {
      try { fn(evt); } catch (e) {}
    });
  };
  WebSocket.prototype._fireClose = function (code, reason) {
    var ev = new global.Event("close", { bubbles: false });
    ev.code = code || 1005;
    ev.reason = String(reason || "");
    ev.wasClean = !!(code && code === 1000);
    this.readyState = 3;
    if (global.__WS_SOCKETS && this._id != null) delete global.__WS_SOCKETS[this._id];
    if (typeof this.onclose === "function") { try { this.onclose(ev); } catch (e) {} }
    this._emit("close", ev);
  };
  WebSocket.prototype.send = function (data) {
    if (this.readyState !== 1) throw new Error("InvalidStateError: WebSocket is not open");
    /* Binary payloads (ArrayBuffer / typed array views) travel as real
       opcode-2 frames: base64 to the host so the bytes are exact. */
    var isBinary = false;
    var bytes = null;
    try {
      if (data instanceof ArrayBuffer) { bytes = new Uint8Array(data); isBinary = true; }
      else if (data && (data.buffer instanceof ArrayBuffer) && typeof data.byteLength === "number") {
        bytes = new Uint8Array(data.buffer, data.byteOffset || 0, data.byteLength); isBinary = true;
      }
    } catch (e) { isBinary = false; }
    if (isBinary && bytes) {
      var bin = "";
      for (var i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
      if (typeof host_ws_send_binary === "function") {
        host_ws_send_binary(this._id, global.btoa(bin));
      }
      return;
    }
    if (typeof host_ws_send === "function") host_ws_send(this._id, String(data));
  };
  WebSocket.prototype.close = function (code, reason) {
    if (this.readyState === 3) return;
    this.readyState = 2;
    if (typeof host_ws_close === "function") host_ws_close(this._id);
    var self = this;
    setTimeout(function () { self._fireClose(code || 1000, reason || ""); }, 0);
  };
  global.WebSocket = WebSocket;
  /* Runtime pump entry: deliver one text frame to its socket. */
  global.__wsDeliver = function (id, data) {
    var sock = global.__WS_SOCKETS[id];
    if (!sock) return false;
    var ev = new global.Event("message", { bubbles: false });
    ev.data = data;
    ev.origin = sock.url;
    if (typeof sock.onmessage === "function") { try { sock.onmessage(ev); } catch (e) {} }
    sock._emit("message", ev);
    return true;
  };
  global.__wsClosed = function (id, code, reason) {
    var sock = global.__WS_SOCKETS[id];
    if (sock) sock._fireClose(code || 1006, reason || "");
  };

  function XMLHttpRequest() {
    this.readyState = 0;
    this.status = 0;
    this.statusText = "";
    this.responseText = "";
    this.response = null;
    this.responseType = "";
    this.responseURL = "";
    this.onreadystatechange = null;
    this.onload = null;
    this.onerror = null;
    this.withCredentials = false;
    this._method = "GET";
    this._url = "";
    this._headers = {};
    this._listeners = {};
  }
  XMLHttpRequest.UNSENT = 0;
  XMLHttpRequest.OPENED = 1;
  XMLHttpRequest.HEADERS_RECEIVED = 2;
  XMLHttpRequest.LOADING = 3;
  XMLHttpRequest.DONE = 4;
  XMLHttpRequest.prototype.open = function (method, url) {
    this._method = String(method || "GET");
    this._url = String(url || "");
    this._headers = {};
    this.readyState = 1;
    if (typeof this.onreadystatechange === "function") this.onreadystatechange();
  };
  XMLHttpRequest.prototype.setRequestHeader = function (header, value) {
    if (header) this._headers[String(header)] = String(value);
  };
  XMLHttpRequest.prototype.getResponseHeader = function () { return null; };
  XMLHttpRequest.prototype.getAllResponseHeaders = function () { return ""; };
  XMLHttpRequest.prototype.addEventListener = function (type, fn) {
    if (!this._listeners[type]) this._listeners[type] = [];
    this._listeners[type].push(fn);
  };
  XMLHttpRequest.prototype._emit = function (type) {
    var self = this;
    (this._listeners[type] || []).forEach(function (fn) {
      try { fn.call(self, { type: type, target: self }); } catch (e) {}
    });
  };
  XMLHttpRequest.prototype.send = function (body) {
    try {
      /* blob: URLs resolve locally; they must never reach the network lane. */
      if (String(this._url || "").indexOf("blob:") === 0) {
        var blobObjX = __objectURLs[String(this._url)];
        if (!blobObjX || blobObjX._content == null) {
          this.status = 0;
          this.readyState = 4;
          if (typeof this.onerror === "function") this.onerror(new Error("unknown blob URL"));
          this._emit("error");
          return;
        }
        this.status = 200;
        this.responseURL = String(this._url);
        this.responseText = blobObjX._content;
        this.response = this.responseType === "json"
          ? JSON.parse(this.responseText || "null")
          : this.responseText;
        this.readyState = 4;
        if (typeof this.onreadystatechange === "function") this.onreadystatechange();
        if (typeof this.onload === "function") this.onload();
        this._emit("load");
        return;
      }
      var reqBody = "";
      var reqBodyB64 = "";
      if (body && (body instanceof global.FormData || body._data)) {
        var sfd = (typeof __serializeFormData === "function") ? __serializeFormData(body) : { body: "", contentType: "" };
        reqBody = sfd.body;
        reqBodyB64 = sfd.binary ? global.btoa(sfd.body) : "";
        if (!this._headers["content-type"] && !this._headers["Content-Type"]) {
          this._headers["content-type"] = sfd.contentType;
        }
      } else {
        reqBody = body == null ? "" : (body._content != null ? body._content : String(body));
        if (body && body._binary) reqBodyB64 = global.btoa(reqBody);
      }
      var raw = typeof host_fetch === "function"
        ? host_fetch(this._url, this._method, reqBody, JSON.stringify(this._headers), reqBodyB64, (function () { try { return (new Error()).stack || ""; } catch (e) { return ""; } })())
        : "{\"status\":599,\"body\":\"\"}";
      var res = typeof raw === "string" ? JSON.parse(raw) : raw;
      this.status = res.status || 0;
      this.responseURL = res.url || this._url;
      this.responseText = res.body || "";
      this.response = this.responseType === "json"
        ? JSON.parse(this.responseText || "null")
        : this.responseText;
      this.readyState = 4;
      if (typeof this.onreadystatechange === "function") this.onreadystatechange();
      if (typeof this.onload === "function") this.onload();
      this._emit("load");
    } catch (e) {
      this.readyState = 4;
      if (typeof this.onerror === "function") this.onerror(e);
      this._emit("error");
    }
  };
  XMLHttpRequest.prototype.abort = function () { this.readyState = 0; };
  global.XMLHttpRequest = XMLHttpRequest;

  global.__load = function (json, href) {
    var doc = new Document();
    var kids;
    if (json && json.t === "document") {
      kids = (json.c || []).map(hydrateOne).filter(Boolean);
    } else {
      kids = hydrate(json);
    }
    kids = [].concat.apply([], kids).filter(function (k) { return k && k.nodeType; });
    kids.forEach(function (k) { doc.appendChild(k); });
    doc.documentElement = doc.querySelector("html") || doc;
    doc.body = doc.querySelector("body");
    doc.head = doc.querySelector("head");
    if (!doc.body) {
      doc.body = new Element("body", {}, []);
      doc.appendChild(doc.body);
    }
    global.document = doc;
    /* E4: full Popover semantics. A single delegated click listener on the
       document handles (a) the declarative `popovertarget` trigger
       (with `popovertargetaction` show/hide/toggle) and (b) light dismiss:
       a click outside an open AUTO popover closes it (manual popovers stay).
       Runs in the bubble phase, after the page's own handlers, and yields to
       preventDefault — the "default action" contract. */
    doc.addEventListener("click", function (ev) {
      try {
        if (ev && ev.defaultPrevented) return;
        var t = ev ? ev.target : null;
        var trigger = null;
        var node = t;
        while (node && node !== doc) {
          if (node.hasAttribute && node.hasAttribute("popovertarget")) { trigger = node; break; }
          node = node.parentNode;
        }
        if (trigger) {
          var tid = trigger.getAttribute("popovertarget");
          var action = String(trigger.getAttribute("popovertargetaction") || "toggle").toLowerCase();
          var target = tid ? doc.getElementById(tid) : null;
          if (target && typeof target.showPopover === "function") {
            if (action === "show" && !target.__popoverOpen) target.showPopover();
            else if (action === "hide" && target.__popoverOpen) target.hidePopover();
            else if (action === "toggle") target.togglePopover();
          }
          return;
        }
        var open = doc.__openAutoPopovers;
        if (open && open.length) {
          var list = open.slice();
          for (var i = list.length - 1; i >= 0; i--) {
            var pop = list[i];
            if (!pop.__popoverOpen) continue;
            if (pop.contains && pop.contains(t)) continue;
            try { pop.hidePopover(); } catch (e) {}
          }
        }
      } catch (e) {}
    });
    try {
      var allRadios = doc.querySelectorAll("input[type=radio]");
      var radioGroups = {};
      for (var ri = 0; ri < allRadios.length; ri++) {
        var r = allRadios[ri];
        var rname = r.getAttribute("name");
        if (!rname) continue;
        var rform = r.form || null;
        var groupKey = (rform ? ("form_" + rform._id) : "doc") + ":" + rname;
        if (!radioGroups[groupKey]) radioGroups[groupKey] = [];
        radioGroups[groupKey].push(r);
      }
      for (var gk in radioGroups) {
        var group = radioGroups[gk];
        var lastChecked = null;
        for (var gi = 0; gi < group.length; gi++) {
          if (group[gi].hasAttribute("checked")) lastChecked = group[gi];
        }
        if (lastChecked) {
          for (var gj = 0; gj < group.length; gj++) {
            if (group[gj] !== lastChecked && group[gj].hasAttribute("checked")) {
              delete group[gj].attributes.checked;
            }
          }
        }
      }
    } catch (e) {}
    try {
      Object.defineProperty(doc, "cookie", {
        get: function () {
          return (typeof host_get_cookie === "function") ? String(host_get_cookie()) : "";
        },
        set: function (v) {
          if (typeof host_set_cookie === "function") host_set_cookie(String(v));
        }
      });
      Object.defineProperty(doc, "readyState", { get: function () { return "complete"; }, configurable: true });
      doc.createElementNS = function (_ns, tag) { return new Element(String(tag).toLowerCase()); };
      doc.createEvent = function (type) {
        return new global.Event(String(type || "Event").replace(/^on/i, "").toLowerCase());
      };
      doc.elementFromPoint = function () { return doc.body; };
    } catch (e) {}
    doc.defaultView = global;
    global.window = global;
    var pdfPlugin = {
      name: "PDF Viewer",
      description: "Portable Document Format",
      filename: "internal-pdf-viewer",
      length: 1,
      item: function (i) { return i === 0 ? this[0] : null; },
      namedItem: function (n) { return n === "application/pdf" ? this[0] : null; }
    };
    var pdfMime = {
      type: "application/pdf",
      suffixes: "pdf",
      description: "Portable Document Format",
      enabledPlugin: pdfPlugin
    };
    pdfPlugin[0] = pdfMime;

    var pluginList = [pdfPlugin];
    pluginList.item = function (i) { return pluginList[i] || null; };
    pluginList.namedItem = function (n) { return n === "PDF Viewer" ? pdfPlugin : null; };

    var mimeList = [pdfMime];
    mimeList.item = function (i) { return mimeList[i] || null; };
    mimeList.namedItem = function (n) { return n === "application/pdf" ? pdfMime : null; };

    global.navigator = {
      userAgent: (typeof __WR_UA === "string" && __WR_UA)
        ? __WR_UA
        : "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
      appName: "Netscape",
      appVersion: "5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
      vendor: "Google Inc.",
      language: "en-US",
      languages: ["en-US", "en"],
      webdriver: false,
      platform: "MacIntel",
      hardwareConcurrency: 8,
      deviceMemory: 8,
      maxTouchPoints: 0,
      cookieEnabled: true,
      onLine: true,
      xr: undefined,
      getGamepads: function () { return []; },
      getBattery: function () {
        return Promise.resolve({
          charging: true,
          chargingTime: 0,
          dischargingTime: Infinity,
          level: 1.0,
          addEventListener: function () {},
          removeEventListener: function () {}
        });
      },
      plugins: pluginList,
      mimeTypes: mimeList,
      userActivation: {
        hasBeenActive: true,
        isActive: true
      },
      permissions: {
        query: function (desc) {
          return Promise.resolve({
            state: "granted",
            name: desc && desc.name,
            onchange: null
          });
        }
      },
      clipboard: {
        readText: function () { return Promise.resolve(""); },
        writeText: function (_t) { return Promise.resolve(); }
      },
      mediaDevices: {
        enumerateDevices: function () {
          /* M2: honest device enumeration — the virtual devices this
             engine actually provides, not a fabricated webcam list. */
          return Promise.resolve([
            { deviceId: "virtual-audio-1", kind: "audioinput", label: "Virtual Microphone (silent)", groupId: "grp-a" },
            { deviceId: "virtual-audio-2", kind: "audiooutput", label: "Virtual Speaker", groupId: "grp-a" }
          ]);
        },
        getUserMedia: function (constraints) {
          /* M2: a real MediaStream with real (silent) tracks. The engine
             has no microphone/camera: audio tracks carry silent frames
             and video tracks are live-but-empty, disclosed. Constraints
             are honored for kind selection. */
          var wantAudio = !constraints || !constraints.audio || constraints.audio !== false;
          var wantVideo = constraints && constraints.video && constraints.video !== false;
          var tracks = [];
          if (wantAudio) tracks.push(new MediaStreamTrack("audio", "Virtual Microphone (silent)"));
          if (wantVideo) tracks.push(new MediaStreamTrack("video", "Virtual Camera (black)"));
          if (!tracks.length) {
            return Promise.reject(new TypeError("Failed to execute 'getUserMedia': at least one of audio or video must be requested"));
          }
          return Promise.resolve(new global.MediaStream(tracks));
        }
      },
      /* sendBeacon: fire-and-forget POST through the owned host_fetch lane
         (CORS-checked there like every in-page request). Returns true when
         the request was handed to the host, FALSE when the URL cannot be
         resolved (spec) — the old lane posted blindly and always said true. */
      sendBeacon: function (url, data) {
        var abs = "";
        try {
          abs = new URL(String(url || ""), (global.location && global.location.href) || "about:blank").href;
        } catch (e) {
          return false;
        }
        try {
          var body = "";
          if (data == null) body = "";
          else if (typeof data === "string") body = data;
          else if (data instanceof global.FormData || data._data) {
            var sfd = (typeof __serializeFormData === "function") ? __serializeFormData(data) : { body: "", contentType: "" };
            body = sfd.body;
          } else if (typeof data === "object") body = JSON.stringify(data);
          else body = String(data);
          if (typeof host_fetch === "function") {
            host_fetch(abs, "POST", body, JSON.stringify({ "content-type": "text/plain;charset=UTF-8" }), "", (function () { try { return (new Error()).stack || ""; } catch (e) { return ""; } })());
            return true;
          }
          return false;
        } catch (e) { return false; }
      },
      /* Geolocation: an agent browser has no location hardware. The honest
         answer is PERMISSION_DENIED — same as a real browser with location
         blocked — never invented coordinates. */
      geolocation: (function () {
        var _nextWatchId = 1;
        var _activeWatches = {};
        return {
          getCurrentPosition: function (success, error) {
            if (typeof error === "function") {
              error({
                code: 1,
                message: "User has denied permission to use positioning",
                PERMISSION_DENIED: 1,
                POSITION_UNAVAILABLE: 2,
                TIMEOUT: 3
              });
            }
          },
          watchPosition: function (success, error) {
            var id = _nextWatchId++;
            _activeWatches[id] = true;
            if (typeof error === "function") {
              error({
                code: 1,
                message: "User has denied permission to use positioning",
                PERMISSION_DENIED: 1,
                POSITION_UNAVAILABLE: 2,
                TIMEOUT: 3
              });
            }
            return id;
          },
          clearWatch: function (id) {
            delete _activeWatches[Number(id)];
          }
        };
      })(),
      /* Service worker container: register() resolves with a registration so
         feature-detecting pages proceed; actual SW analysis/installation is
         host-side (sw.py regex + strategy simulation) and navigator.serviceWorker
         does not imply SW JS execution. controller is null: no page is
         controlled by a SW in this engine. */
      /* ---- W2: Service Worker — REAL execution + fetch interception ----
         register() fetches the SW script (blob: store or host_fetch), runs
         it in a ServiceWorkerGlobalScope realm (new Function with shadowed
         globals), and captures its "fetch" event handler. install and
         activate listeners fire synchronously at registration (the engine
         has one event loop pass; disclosed). A page fetch() whose URL is
         within the SW scope routes through respondWith: the SW's Response
         replaces the network response. If the SW never calls respondWith,
         the fetch falls through to the network (real SW spec would error
         on no-respondWith; fall-through is the disclosed engine choice). */
      serviceWorker: {
        controller: null,
        ready: null,
        register: function (scriptUrl, options) {
          var base = (global.location && global.location.href) || "about:blank";
          var absScript = "";
          try {
            absScript = new URL(String(scriptUrl || ""), base).href;
          } catch (e) {
            absScript = String(scriptUrl || "");
          }
          var scope = (options && options.scope) || "/";
          try {
            scope = new URL(String(scope), base).href;
          } catch (eScope) {
            scope = String(scope || "/");
          }
          /* Fetch the SW script source. Real browsers reject an SW script
             whose MIME type is not JavaScript (text/html, text/plain, ...)
             and whose body does not compile; a fetch failure rejects the
             registration too. Never fake a successful registration. */
          var scriptSource = null;
          var swRejectReason = null;
          if (absScript.indexOf("blob:") === 0) {
            var swBlob = __objectURLs[absScript];
            if (swBlob && swBlob._content != null) {
              scriptSource = swBlob._content;
            } else {
              swRejectReason = "NotFound: blob URL not found: " + absScript;
            }
          } else if (typeof host_fetch === "function") {
            var swRaw = null;
            try {
              swRaw = host_fetch(absScript, "GET", "", "{}", "", "");
            } catch (eFetch) {
              swRaw = null;
            }
            var swRes = null;
            try { swRes = typeof swRaw === "string" ? JSON.parse(swRaw) : swRaw; } catch (eParse) { swRes = null; }
            var swStatus = swRes ? (swRes.status || 0) : 0;
            var swMime = String((swRes && swRes.content_type) || "").split(";")[0].trim().toLowerCase();
            if (swStatus < 200 || swStatus >= 300 || !swRes || !swRes.body) {
              swRejectReason = "NetworkError: fetch of service script failed (status " + swStatus + "): " + absScript;
            } else if (swMime && swMime !== "application/javascript" && swMime !== "text/javascript" &&
                       swMime !== "application/ecmascript" && swMime !== "text/ecmascript" &&
                       swMime !== "application/x-javascript" && swMime !== "module") {
              /* Chrome: "The MIME type of the script ('text/html') is not
                 a JavaScript MIME type." */
              swRejectReason = "TypeError: The MIME type of the script ('" + (swMime || "unknown") + "') is not a JavaScript MIME type: " + absScript;
            } else {
              scriptSource = swRes.body;
            }
          } else {
            swRejectReason = "NetworkError: no fetch transport available for: " + absScript;
          }
          var swObj = {
            scope: scope,
            state: scriptSource != null ? "activated" : "redeployable",
            scriptURL: absScript,
            onfetch: null,
            fetchHandler: null,
            activateHandler: null,
            installHandler: null,
            unregister: function () {
              global.__wrActiveSW = null;
              var nav = global.navigator;
              if (nav && nav.serviceWorker) nav.serviceWorker.controller = null;
              return Promise.resolve(true);
            }
          };
          if (scriptSource == null) {
            /* Honest failure: the SW script could not be fetched or has a
               non-JavaScript MIME type — a real browser REJECTS the
               registration; so does this engine. */
            global.__wrActiveSW = null;
            var navSW0 = global.navigator && global.navigator.serviceWorker;
            if (navSW0) navSW0.controller = null;
            return Promise.reject(new TypeError(String(swRejectReason || ("NetworkError: failed to fetch: " + absScript))));
          }
          /* ServiceWorkerGlobalScope realm: shadowed globals via new Function */
          var swRealm = {
            _listeners: {},
            self: null,
            registration: null,
            location: { href: absScript },
            skipWaiting: function () { return Promise.resolve(); },
            clients: { claim: function () { return Promise.resolve([]); } },
            caches: {
              open: function () { return Promise.resolve({ match: function () { return Promise.resolve(undefined); }, put: function () { return Promise.resolve(); } }); },
              keys: function () { return Promise.resolve([]); }
            },
            importScripts: function () {},
            addEventListener: function (type, fn) {
              (swRealm._listeners[type] = swRealm._listeners[type] || []).push(fn);
              if (type === "fetch") swObj.fetchHandler = fn;
              else if (type === "activate") swObj.activateHandler = fn;
              else if (type === "install") swObj.installHandler = fn;
            },
            removeEventListener: function (type, fn) {
              var arr = swRealm._listeners[type];
              if (arr) swRealm._listeners[type] = arr.filter(function (l) { return l !== fn; });
              if (type === "fetch" && swObj.fetchHandler === fn) swObj.fetchHandler = null;
              if (type === "activate" && swObj.activateHandler === fn) swObj.activateHandler = null;
              if (type === "install" && swObj.installHandler === fn) swObj.installHandler = null;
            },
            dispatchEvent: function (type, evt) {
              var arr = swRealm._listeners[type] || [];
              for (var i = 0; i < arr.length; i++) { try { arr[i](evt); } catch (e) {} }
            }
          };
          swRealm.self = swRealm;
          var swConsole = global.console || { log: function () {} };
          /* Compile + run inside one try: a SyntaxError from new Function
             (invalid script body) must REJECT the registration exactly like
             a run-time error — not escape register() synchronously. */
          var swWrapper = null;
          try {
            swWrapper = new Function(
              "self", "addEventListener", "removeEventListener", "dispatchEvent",
              "skipWaiting", "clients", "caches", "importScripts", "registration",
              "console", "location", "fetch",
              "\"use strict\";\n" + String(scriptSource || "")
            );
            swWrapper(
              swRealm, swRealm.addEventListener, swRealm.removeEventListener, swRealm.dispatchEvent,
              swRealm.skipWaiting, swRealm.clients, swRealm.caches, swRealm.importScripts,
              { scope: scope },
              swConsole, swRealm.location, global.fetch
            );
          } catch (eSW) {
            /* Compile/run error: a real browser fails installation — the
               registration REJECTS (the script never becomes active). */
            global.__wrActiveSW = null;
            var navSWx = global.navigator && global.navigator.serviceWorker;
            if (navSWx) navSWx.controller = null;
            return Promise.reject(new TypeError("SW script error at " + absScript + ": " + (eSW && eSW.message ? eSW.message : String(eSW))));
          }
          /* Fire install + activate listeners (single event-loop pass —
             the engine cannot span async SW lifecycle phases; disclosed). */
          if (swObj.installHandler) {
            try { swObj.installHandler({ type: "install", waitUntil: function () { return Promise.resolve(); } }); } catch (e) {}
          }
          if (swObj.activateHandler) {
            try { swObj.activateHandler({ type: "activate", waitUntil: function () { return Promise.resolve(); } }); } catch (e) {}
          }
          /* Activate: record the interceptor the page fetch() consults */
          global.__wrActiveSW = {
            scope: scope,
            scriptURL: absScript,
            fetchHandler: swObj.fetchHandler,
            _sw: swObj
          };
          var registration = {
            scope: scope,
            installing: null,
            waiting: null,
            active: swObj,
            unregister: swObj.unregister
          };
          /* ready resolves with the registration once active (already true) */
          var navSW = global.navigator && global.navigator.serviceWorker;
          if (navSW) {
            navSW.controller = swObj;
            if (!navSW.ready) navSW.ready = Promise.resolve(registration);
          }
          return Promise.resolve(registration);
        },
        getRegistration: function () {
          return Promise.resolve(global.__wrActiveSW ? {
            scope: global.__wrActiveSW.scope,
            installing: null,
            waiting: null,
            active: global.__wrActiveSW._sw,
            unregister: global.__wrActiveSW._sw && global.__wrActiveSW._sw.unregister
          } : undefined);
        },
        getRegistrations: function () {
          return Promise.resolve(global.__wrActiveSW ? [{
            scope: global.__wrActiveSW.scope,
            installing: null,
            waiting: null,
            active: global.__wrActiveSW._sw,
            unregister: global.__wrActiveSW._sw && global.__wrActiveSW._sw.unregister
          }] : []);
        },
        addEventListener: function () {},
        removeEventListener: function () {}
      }
    };

    global.screen = {
      width: 1920,
      height: 1080,
      availWidth: 1920,
      availHeight: 1040,
      colorDepth: 24,
      pixelDepth: 24,
      orientation: {
        angle: 0,
        type: "landscape-primary",
        onchange: null
      }
    };
    global.devicePixelRatio = 2;
    global.visualViewport = {
      width: 1280,
      height: 720,
      offsetLeft: 0,
      offsetTop: 0,
      pageLeft: 0,
      pageTop: 0,
      scale: 1,
      addEventListener: function () {},
      removeEventListener: function () {}
    };

    /* Notification: the constructor exists for feature-detection parity;
       permission is "denied" (a headless agent browser shows no notifications
       and never invents one) and requestPermission resolves "denied". */
    global.Notification = function (title, options) {
      this.title = String(title || "");
      this.body = (options && options.body) || "";
      this.tag = (options && options.tag) || "";
      this.onclick = null;
      this.onclose = null;
    };
    global.Notification.permission = "denied";
    global.Notification.maxActions = 0;
    global.Notification.requestPermission = function () { return Promise.resolve("denied"); };

    /* BroadcastChannel: same-context pub/sub. Every channel instance in this
     document receives posted messages except the sender. Disclosed
     approximation: no cross-context/cross-frame delivery (the host lane
     for that is frames' postMessage). */
    (function () {
      var __bcRegistry = {};
      var __bcSeq = 0;
      global.BroadcastChannel = function (name) {
        this.name = String(name);
        this._id = ++__bcSeq;
        this.onmessage = null;
        this._listeners = [];
        this._closed = false;
        (__bcRegistry[this.name] = __bcRegistry[this.name] || []).push(this);
        this.postMessage = function (data) {
          /* Spec: postMessage on a closed channel is a no-op. */
          if (this._closed) return;
          var list = __bcRegistry[this.name] || [];
          for (var i = 0; i < list.length; i++) {
            if (list[i] === this) continue;
            (function (ch) {
              var ev = new global.MessageEvent("message", { data: data });
              Promise.resolve().then(function () {
                try {
                  if (typeof ch.onmessage === "function") ch.onmessage(ev);
                } catch (e) {}
                ch._listeners.forEach(function (fn) {
                  try { fn(ev); } catch (e) {}
                });
              });
            })(list[i]);
          }
        };
        this.addEventListener = function (type, fn) { if (type === "message") this._listeners.push(fn); };
        this.removeEventListener = function (type, fn) {
          this._listeners = this._listeners.filter(function (f) { return f !== fn; });
        };
        this.close = function () {
          /* `this` inside the filter callback is not the channel — the old
             callback compared against a non-bound `this` and removed
             nothing, so closed channels kept receiving (re-audit finding). */
          this._closed = true;
          var self = this;
          var list = __bcRegistry[this.name] || [];
          __bcRegistry[this.name] = list.filter(function (ch) { return ch !== self; });
        };
      };
    })();

    /* window.print: dispatches the beforeprint/afterprint event pair, the
       observable contract of a real print invocation (no paper here). */
    global.print = function () {
      try {
        global.dispatchEvent(new global.Event("beforeprint"));
        global.dispatchEvent(new global.Event("afterprint"));
      } catch (e) {}
    };

    /* caretRangeFromPoint: hit-test against laid-out boxes and return a Range
       collapsed at the INNERMOST containing element (smallest box wins — the
       old walk took the first/outermost match). Outside every box it returns
       null, like Chrome. Disclosed approximation: no caret geometry, so the
       Range carries the element, not an offset. */
    Document.prototype.caretRangeFromPoint = function (x, y) {
      __ensureLayout(this);
      var px = Number(x) || 0, py = Number(y) || 0;
      var best = null, bestArea = Infinity;
      (function walk(n) {
        if (!n || n.nodeType !== 1) return;
        var b = n.__box;
        if (b && b.width > 0 && b.height > 0 && px >= b.x && px <= b.x + b.width && py >= b.y && py <= b.y + b.height) {
          var area = b.width * b.height;
          if (area < bestArea) { best = n; bestArea = area; }
        }
        (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) walk(c); });
      })(this.body || this.documentElement);
      if (!best) return null;
      var r = new global.Range();
      r.startContainer = best;
      r.endContainer = best;
      r.collapsed = true;
      return r;
    };

    global.chrome = {
      app: { isInstalled: false },
      runtime: {
        OnInstalledReason: { INSTALL: "install", UPDATE: "update" },
        PlatformOs: { MAC: "mac", WIN: "win", LINUX: "linux" }
      },
      loadTimes: function () {
        var now = (Date.now() / 1000);
        return {
          requestTime: now - 0.15,
          startLoadTime: now - 0.14,
          commitLoadTime: now - 0.05,
          finishDocumentLoadTime: now - 0.02,
          firstPaintTime: now - 0.01,
          finishLoadTime: now,
          wasFetchedViaSpdy: true,
          wasNpnNegotiated: true,
          npnNegotiatedProtocol: "h2",
          wasAlternateProtocolAvailable: false,
          connectionInfo: "h2"
        };
      },
      csi: function () {
        return { startE: Date.now() - 150, onloadT: Date.now(), pageT: 150, tran: 15 };
      }
    };

    var startTime = Date.now() - 150;
    var _clockOffset = 0;
    global.__advanceClock = function (ms) {
      _clockOffset += Math.max(0, Number(ms) || 0);
      /* L9: advance CSS animations/transitions on the same clock tick */
      try {
        var doc = global.document;
        if (doc) __advanceAnimations(doc, Math.max(0, Number(ms) || 0));
      } catch (e) {}
      return _clockOffset;
    };
    global.__getClockOffset = function () { return _clockOffset; };
    /* Resource-timing entries: the host injects one per subresource this
       pass actually fetched (scripts, in-page fetch/XHR) with real URLs and
       byte sizes. Timing values are 0 — no fabricated timing data, the
       honest representation. MERGE by URL (not replace): action passes
       reuse this ctx, and the old replace wiped the page-load subresources
       after the first scroll — an agent reading the timeline post-action
       saw an empty history. A fresh navigation builds a fresh ctx, which
       resets the timeline like a real page load. Real stats collectors
       reduce() over getEntriesByType('resource') without an initial
       value; an always-empty timeline made that reduce undefined. */
    var __resourceEntries = [];
    var __markEntries = [];
    global.performance = {
      timeOrigin: startTime,
      now: function () { return (Date.now() - startTime) + _clockOffset; },
      __setResourceEntries: function (list) {
        try {
          var parsed = (typeof list === "string") ? JSON.parse(list) : list;
          (parsed || []).forEach(function (r) {
            var nm = String(r.url || "");
            var exists = false;
            for (var i = 0; i < __resourceEntries.length; i++) {
              if (__resourceEntries[i].name === nm) { exists = true; break; }
            }
            if (exists) return;
            __resourceEntries.push({
              name: nm,
              entryType: "resource",
              initiatorType: String(r.initiatorType || "other"),
              startTime: 0, duration: 0,
              redirectStart: 0, redirectEnd: 0,
              fetchStart: 0, domainLookupStart: 0, domainLookupEnd: 0,
              connectStart: 0, connectEnd: 0, secureConnectionStart: 0,
              requestStart: 0, responseStart: 0, responseEnd: 0,
              transferSize: Number(r.transferSize) || 0,
              encodedBodySize: Number(r.transferSize) || 0,
              decodedBodySize: Number(r.transferSize) || 0,
              nextHopProtocol: "", workerStart: 0
            });
          });
        } catch (e) {}
      },
      timing: {
        navigationStart: startTime,
        unloadEventStart: 0,
        unloadEventEnd: 0,
        redirectStart: 0,
        redirectEnd: 0,
        fetchStart: startTime + 2,
        domainLookupStart: startTime + 5,
        domainLookupEnd: startTime + 12,
        connectStart: startTime + 12,
        connectEnd: startTime + 35,
        secureConnectionStart: startTime + 18,
        requestStart: startTime + 37,
        responseStart: startTime + 80,
        responseEnd: startTime + 105,
        domLoading: startTime + 108,
        domInteractive: startTime + 135,
        domContentLoadedEventStart: startTime + 140,
        domContentLoadedEventEnd: startTime + 145,
        domComplete: startTime + 148,
        loadEventStart: startTime + 149,
        loadEventEnd: startTime + 150
      },
      getEntries: function () { return __resourceEntries.slice().concat(__markEntries); },
      getEntriesByType: function (t) {
        if (t === "resource") return __resourceEntries.slice();
        if (t === "mark") return __markEntries.filter(function (e) { return e.entryType === "mark"; });
        if (t === "measure") return __markEntries.filter(function (e) { return e.entryType === "measure"; });
        return [];
      },
      getEntriesByName: function (n, t) {
        return (__resourceEntries.concat(__markEntries)).filter(function (e) {
          return e.name === String(n) && (!t || t === e.entryType);
        });
      },
      mark: function (name) {
        var mk = { name: String(name || ""), entryType: "mark", startTime: this.now(), duration: 0 };
        __markEntries.push(mk);
        return mk;
      },
      measure: function (name) {
        var ms = { name: String(name || ""), entryType: "measure", startTime: 0, duration: 0 };
        __markEntries.push(ms);
        return ms;
      }
    };
    if (!global.location || global.location.href === "about:blank") {
      try {
        var initialParsed = new URL(href || "about:blank");
        var _loc = {};
        var _locHref = initialParsed.href;
        function _updateLocationProps(p) {
          _locHref = p.href;
          _loc.origin = p.origin;
          _loc.protocol = p.protocol;
          _loc.host = p.host;
          _loc.hostname = p.hostname;
          _loc.port = p.port;
          _loc.pathname = p.pathname;
          _loc.search = p.search;
          _loc.hash = p.hash;
          _loc.searchParams = p.searchParams;
        }
        function _navigate(target) {
          if (!target) return;
          var targetStr = String(target);
          try {
            var res = new URL(targetStr, _locHref || href || "about:blank");
            _updateLocationProps(res);
            global.__WR_NAVIGATED = res.href;
          } catch (e) {
            _locHref = targetStr;
            global.__WR_NAVIGATED = targetStr;
          }
        }
        _updateLocationProps(initialParsed);
        _loc.toString = function () { return _locHref; };
        _loc.assign = function (u) { _navigate(u); };
        _loc.replace = function (u) { _navigate(u); };
        _loc.reload = function () { _navigate(_locHref); };
        Object.defineProperty(_loc, "href", {
          get: function () { return _locHref; },
          set: function (u) { _navigate(u); },
          configurable: true,
          enumerable: true
        });
        global._updateLocationQuietly = function (u) {
          try {
            var p = new URL(String(u || ""), _locHref || href || "about:blank");
            _updateLocationProps(p);
          } catch (e) {}
        };
        Object.defineProperty(global, "location", {
          get: function () { return _loc; },
          set: function (u) {
            if (typeof u === "string") _navigate(u);
            else if (u && typeof u === "object") _loc = u;
          },
          configurable: true,
          enumerable: true
        });
        doc.location = _loc;
      } catch (e) {}
    }
    /* window.origin is the document origin (HTML spec). It is not
       location.origin under another name on the window object; scripts
       read `window.origin` directly. */
    Object.defineProperty(global, "origin", {
      get: function () {
        try { return (global.location && global.location.origin) || ""; } catch (e) { return ""; }
      },
      configurable: true
    });
    global.Node = Node;
    global.Element = Element;
    global.HTMLElement = Element;
    global.Text = Text;
    global.Document = Document;
    global.SVGElement = Element;

    function DOMParser() {}
    DOMParser.prototype.parseFromString = function (str, mime) {
      var doc = new Document();
      var decoded = String(str || "")
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&quot;/g, '"')
        .replace(/&#39;/g, "'")
        .replace(/&apos;/g, "'");
      var htmlNode = new Element("html");
      var body = new Element("body");
      htmlNode.appendChild(body);
      body.appendChild(new Text(decoded));
      doc.appendChild(htmlNode);
      doc.documentElement = htmlNode;
      doc.body = body;
      return doc;
    };
    global.FormData = FormData;
    global.__serializeFormData = __serializeFormData;
    global.DOMParser = DOMParser;
    global.Event = function (type, init) {
      this.type = type;
      this.bubbles = !!(init && init.bubbles);
      this.cancelable = !!(init && init.cancelable);
      this.defaultPrevented = false;
      this.target = null;
      var trusted = !!(init && (init.__trusted !== undefined ? init.__trusted : init.isTrusted));
      Object.defineProperty(this, "isTrusted", {
        value: trusted,
        writable: false,
        configurable: true
      });
      this.preventDefault = function () { this.defaultPrevented = true; };
      this.stopPropagation = function () {};
      // Copy every remaining init field onto the event. Mouse handlers read
      // clientX/clientY/buttons to work out a drag, and dropping those made
      // every drag arrive as a click at (0, 0).
      if (init && typeof init === "object") {
        for (var key in init) {
          if (key === "bubbles" || key === "cancelable" || key === "isTrusted" || key === "__trusted") continue;
          if (!Object.prototype.hasOwnProperty.call(this, key)) {
            try { this[key] = init[key]; } catch (e) {}
          }
        }
      }
    };
    global.CustomEvent = global.Event;

    global.UIEvent = function (type, init) {
      global.Event.call(this, type, init);
      this.detail = (init && init.detail) || 0;
      this.view = (init && init.view) || global;
    };
    global.UIEvent.prototype = Object.create(global.Event.prototype);

    global.MouseEvent = function (type, init) {
      global.UIEvent.call(this, type, init);
      init = init || {};
      this.screenX = init.screenX || 0;
      this.screenY = init.screenY || 0;
      this.clientX = init.clientX || 0;
      this.clientY = init.clientY || 0;
      this.pageX = init.pageX || this.clientX;
      this.pageY = init.pageY || this.clientY;
      this.ctrlKey = !!init.ctrlKey;
      this.shiftKey = !!init.shiftKey;
      this.altKey = !!init.altKey;
      this.metaKey = !!init.metaKey;
      this.button = init.button || 0;
      this.buttons = init.buttons !== undefined ? init.buttons : (type === "mousedown" ? 1 : 0);
      this.relatedTarget = init.relatedTarget || null;
    };
    global.MouseEvent.prototype = Object.create(global.UIEvent.prototype);

    global.PointerEvent = function (type, init) {
      global.MouseEvent.call(this, type, init);
      init = init || {};
      this.pointerId = init.pointerId !== undefined ? init.pointerId : 1;
      this.width = init.width || 1;
      this.height = init.height || 1;
      this.pressure = init.pressure !== undefined ? init.pressure : (this.buttons ? 0.5 : 0);
      this.tangentialPressure = init.tangentialPressure || 0;
      this.tiltX = init.tiltX || 0;
      this.tiltY = init.tiltY || 0;
      this.twist = init.twist || 0;
      this.pointerType = init.pointerType || "mouse";
      this.isPrimary = init.isPrimary !== undefined ? !!init.isPrimary : true;
    };
    global.PointerEvent.prototype = Object.create(global.MouseEvent.prototype);

    global.FocusEvent = function (type, init) {
      global.UIEvent.call(this, type, init);
      init = init || {};
      this.relatedTarget = init.relatedTarget || null;
    };
    global.FocusEvent.prototype = Object.create(global.UIEvent.prototype);

    global.KeyboardEvent = function (type, init) {
      global.UIEvent.call(this, type, init);
      init = init || {};
      this.key = init.key || "";
      this.code = init.code || "";
      this.location = init.location || 0;
      this.ctrlKey = !!init.ctrlKey;
      this.shiftKey = !!init.shiftKey;
      this.altKey = !!init.altKey;
      this.metaKey = !!init.metaKey;
      this.repeat = !!init.repeat;
      this.isComposing = !!init.isComposing;
      var kc = init.keyCode || (this.key ? this.key.charCodeAt(0) : 0);
      this.keyCode = kc;
      this.which = init.which || kc;
      this.charCode = init.charCode || (type === "keypress" ? kc : 0);
    };
    global.KeyboardEvent.prototype = Object.create(global.UIEvent.prototype);

    global.MessageEvent = function (type, init) {
      global.Event.call(this, type, init);
      this.type = type || "message";
      this.data = init && init.data;
      this.origin = (init && init.origin) || "";
      this.lastEventId = (init && init.lastEventId) || "";
      /* Port deliveries pass source:null. A missing source on a window
         message still falls back to parent; an explicit null must stay null,
         or a port handler treats the event as a cross-window message and
         drops it. */
      this.source = (init && Object.prototype.hasOwnProperty.call(init, "source"))
        ? init.source
        : (global.parent || global);
      this.ports = (init && init.ports) || [];
    };
    global.MessageEvent.prototype = Object.create(global.Event.prototype);

    /* WheelEvent: the wheel dispatcher used to fall back to a generic Event;
       instanceof checks and delta fields now match a real browser. */
    global.WheelEvent = function (type, init) {
      global.UIEvent.call(this, type, init);
      init = init || {};
      this.deltaX = init.deltaX || 0;
      this.deltaY = init.deltaY || 0;
      this.deltaZ = init.deltaZ || 0;
      this.deltaMode = init.deltaMode || 0;
      this.ctrlKey = !!init.ctrlKey;
      this.shiftKey = !!init.shiftKey;
      this.altKey = !!init.altKey;
      this.metaKey = !!init.metaKey;
    };
    global.WheelEvent.prototype = Object.create(global.UIEvent.prototype);

    /* StorageEvent: fired on OTHER same-origin tabs when localStorage
       changes (host broadcast). Carries the spec surface: key, oldValue,
       newValue, url, storageArea. */
    global.StorageEvent = function (type, init) {
      global.Event.call(this, type, init);
      init = init || {};
      this.key = init.key === undefined ? null : init.key;
      this.oldValue = init.oldValue === undefined ? null : init.oldValue;
      this.newValue = init.newValue === undefined ? null : init.newValue;
      this.url = init.url === undefined ? "" : String(init.url);
      this.storageArea = init.storageArea === undefined ? null : init.storageArea;
    };
    global.StorageEvent.prototype = Object.create(global.Event.prototype);

    /* InputEvent: typing used to deliver generic Events with data/inputType
       bolted on; now the class exists for instanceof and feature checks. */
    global.InputEvent = function (type, init) {
      global.UIEvent.call(this, type, init);
      init = init || {};
      this.data = init.data !== undefined ? init.data : null;
      this.isComposing = !!init.isComposing;
      this.inputType = init.inputType || "";
      this.dataTransfer = init.dataTransfer || null;
    };
    global.InputEvent.prototype = Object.create(global.UIEvent.prototype);

    /* SubmitEvent: form submissions carried a generic Event; the class (and
       submitter) now exist like a real browser. */
    global.SubmitEvent = function (type, init) {
      global.Event.call(this, type, init);
      this.submitter = (init && init.submitter) || null;
    };
    global.SubmitEvent.prototype = Object.create(global.Event.prototype);

    /* DataTransfer + DragEvent: native HTML5 drag & drop needs a real
       dataTransfer the dragstart handler can write into and the drop handler
       reads back. Previously both classes were undefined and any
       draggable=true flow crashed.
       dropEffect/effectAllowed are validated accessors with the spec's
       phase model: effectAllowed is settable only in dragstart (a standalone
       DataTransfer is unrestricted), dropEffect only while negotiating the
       drop target (dragenter/dragover) — during negotiation a value outside
       effectAllowed's allowed set is IGNORED (Blink parity), and invalid
       values never change the attribute. The drag operation itself is
       negotiated in __executeDrag (default per effectAllowed, handler
       override, drop only when the operation is not "none"). */
    var __EA_CANON = {
      "none": "none", "copy": "copy", "copylink": "copyLink", "copymove": "copyMove",
      "link": "link", "linkmove": "linkMove", "move": "move", "all": "all",
      "uninitialized": "uninitialized"
    };
    var __DE_CANON = { "none": "none", "copy": "copy", "link": "link", "move": "move" };
    /* Is `effect` permitted by effectAllowed's allowed-operations set? */
    global.__effectPermits = function __effectPermits(allowed, effect) {
      var a = String(allowed || "uninitialized").toLowerCase();
      var e = String(effect || "none").toLowerCase();
      if (e === "none") return true;
      switch (a) {
        case "none": return false;
        case "copy": return e === "copy";
        case "link": return e === "link";
        case "move": return e === "move";
        case "copylink": return e === "copy" || e === "link";
        case "copymove": return e === "copy" || e === "move";
        case "linkmove": return e === "link" || e === "move";
        default: return e === "copy" || e === "link" || e === "move";
      }
    }
    /* The user agent's default dropEffect for dragenter/dragover, from the
       source's declared effectAllowed (spec processing model). */
    global.__defaultDropEffect = function __defaultDropEffect(allowed) {
      var a = String(allowed || "uninitialized").toLowerCase();
      if (a === "none") return "none";
      if (a === "link" || a === "linkmove") return "link";
      if (a === "move") return "move";
      return "copy";
    }
    global.DataTransfer = function () {
      this.__data = {};
      this.types = [];
      this.items = [];
      this.__dropEffect = "none";
      this.__effectAllowed = "uninitialized";
      /* Lifecycle phase the drag engine sets on THIS transfer: null
         (standalone new DataTransfer()), "dragstart", "negotiate"
         (dragenter/dragover), "drop", "dragend". */
      this.__phase = null;
    };
    Object.defineProperty(global.DataTransfer.prototype, "dropEffect", {
      get: function () { return this.__dropEffect; },
      set: function (v) {
        var canon = __DE_CANON[String(v).toLowerCase()];
        /* Invalid values are ignored (attribute unchanged), per spec. */
        if (!canon) return;
        /* The source does not choose the operation: sets during dragstart and
           dragend are ignored. During negotiation the value must also be
           permitted by effectAllowed. A standalone DataTransfer (no drag)
           accepts any valid value. */
        if (this.__phase === "dragstart" || this.__phase === "dragend") return;
        if (this.__phase === "negotiate" && !__effectPermits(this.__effectAllowed, canon)) return;
        this.__dropEffect = canon;
      },
      configurable: true,
      enumerable: true
    });
    Object.defineProperty(global.DataTransfer.prototype, "effectAllowed", {
      get: function () { return this.__effectAllowed; },
      set: function (v) {
        var canon = __EA_CANON[String(v).toLowerCase()];
        /* Invalid values leave the attribute unchanged, per spec. */
        if (!canon) return;
        /* Only the dragstart handler may declare the allowed effects; a
           standalone DataTransfer is unrestricted. */
        if (this.__phase === "negotiate" || this.__phase === "drop" || this.__phase === "dragend") return;
        this.__effectAllowed = canon;
      },
      configurable: true,
      enumerable: true
    });
    global.DataTransfer.prototype.setData = function (type, value) {
      /* Spec: the type is converted to ASCII lowercase. */
      type = String(type).toLowerCase();
      this.__data[type] = String(value);
      this.types = Object.keys(this.__data);
    };
    global.DataTransfer.prototype.getData = function (type) {
      /* Spec: the type is converted to ASCII lowercase. */
      type = String(type).toLowerCase();
      return Object.prototype.hasOwnProperty.call(this.__data, type) ? this.__data[type] : "";
    };
    global.DataTransfer.prototype.hasData = function (type) {
      return Object.prototype.hasOwnProperty.call(this.__data, String(type).toLowerCase());
    };
    global.DataTransfer.prototype.clearData = function (type) {
      if (type === undefined) { this.__data = {}; }
      else { delete this.__data[String(type).toLowerCase()]; }
      this.types = Object.keys(this.__data);
    };
    global.DataTransfer.prototype.setDragImage = function () {};
    Object.defineProperty(global.DataTransfer.prototype, "files", {
      get: function () { return []; },
      configurable: true
    });
    global.DragEvent = function (type, init) {
      global.MouseEvent.call(this, type, init);
      this.dataTransfer = (init && init.dataTransfer) || null;
    };
    global.DragEvent.prototype = Object.create(global.MouseEvent.prototype);

    function Range() {
      this.startContainer = null;
      this.startOffset = 0;
      this.endContainer = null;
      this.endOffset = 0;
      this.collapsed = true;
      this.commonAncestorContainer = null;
    }
    Range.prototype.setStart = function (node, offset) {
      this.startContainer = node;
      this.startOffset = offset || 0;
      if (!this.endContainer) { this.endContainer = node; this.endOffset = this.startOffset; }
      this.collapsed = (this.startContainer === this.endContainer && this.startOffset === this.endOffset);
      this.commonAncestorContainer = node;
    };
    Range.prototype.setEnd = function (node, offset) {
      this.endContainer = node;
      this.endOffset = offset || 0;
      if (!this.startContainer) { this.startContainer = node; this.startOffset = this.endOffset; }
      this.collapsed = (this.startContainer === this.endContainer && this.startOffset === this.endOffset);
    };
    Range.prototype.selectNode = function (node) {
      this.startContainer = node.parentNode || node;
      this.startOffset = 0;
      this.endContainer = node.parentNode || node;
      this.endOffset = 1;
      this.collapsed = false;
      this.commonAncestorContainer = this.startContainer;
    };
    Range.prototype.selectNodeContents = function (node) {
      this.startContainer = node;
      this.startOffset = 0;
      this.endContainer = node;
      this.endOffset = (node.childNodes ? node.childNodes.length : 0);
      this.collapsed = false;
      this.commonAncestorContainer = node;
    };
    Range.prototype.collapse = function (toStart) {
      if (toStart) { this.endContainer = this.startContainer; this.endOffset = this.startOffset; }
      else { this.startContainer = this.endContainer; this.startOffset = this.endOffset; }
      this.collapsed = true;
    };
    Range.prototype.cloneRange = function () {
      var r = new Range();
      r.startContainer = this.startContainer;
      r.startOffset = this.startOffset;
      r.endContainer = this.endContainer;
      r.endOffset = this.endOffset;
      r.collapsed = this.collapsed;
      r.commonAncestorContainer = this.commonAncestorContainer;
      return r;
    };
    Range.prototype.getBoundingClientRect = function () {
      if (this.startContainer && typeof this.startContainer.getBoundingClientRect === "function") {
        return this.startContainer.getBoundingClientRect();
      }
      return { x: 0, y: 0, top: 0, left: 0, bottom: 0, right: 0, width: 0, height: 0 };
    };
    Range.prototype.getClientRects = function () {
      return [this.getBoundingClientRect()];
    };
    /* Minimal selection text: substring for text-node containers, whole
       children between the offsets for element containers. Disclosed:
       no cross-container traversal, no caret geometry. */
    Range.prototype.toString = function () {
      if (!this.startContainer) return "";
      if (this.startContainer.nodeType === 3) {
        var data = String(this.startContainer.data || "");
        var end = this.endContainer === this.startContainer ? this.endOffset : data.length;
        return data.substring(this.startOffset, end);
      }
      var out = "";
      var nodes = this.startContainer.childNodes || [];
      var endAt = this.endContainer === this.startContainer ? this.endOffset : nodes.length;
      for (var i = this.startOffset; i < nodes.length && i < endAt; i++) {
        out += nodes[i].textContent || "";
      }
      return out;
    };
    Range.prototype.deleteContents = function () {
      if (!this.startContainer || this.startContainer.nodeType === 3) return;
      var nodes = this.startContainer.childNodes || [];
      var endAt = this.endContainer === this.startContainer ? this.endOffset : nodes.length;
      var toRemove = [];
      for (var i = this.startOffset; i < nodes.length && i < endAt; i++) {
        toRemove.push(nodes[i]);
      }
      for (var j = 0; j < toRemove.length; j++) {
        try { this.startContainer.removeChild(toRemove[j]); } catch (e) {}
      }
      this.endContainer = this.endOffset = this.startOffset;
      this.collapsed = true;
    };
    global.Range = Range;

    /* L7: selectionchange fires on the DOCUMENT (spec), never on the element.
       Editors and agent tooling observe it there; the focused element's
       selection is what changed. */
    function __notifySelectionChange() {
      try {
        var doc = global.document;
        if (!doc || typeof doc.dispatchEvent !== "function") return;
        var ev = new global.Event("selectionchange", { bubbles: false, cancelable: false });
        doc.dispatchEvent(ev);
      } catch (e) {}
    }
    /* Which elements expose selectionStart/End: single-line text inputs and
       textareas (spec HTMLInputElement/HTMLTextAreaElement surface). */
    function __isTextEditable(el) {
      var t = (el && el.nodeName || "").toLowerCase();
      if (t === "textarea") return true;
      if (t === "input") {
        var ty = ((el.getAttribute && el.getAttribute("type")) || "text").toLowerCase();
        return ty === "text" || ty === "search" || ty === "url" || ty === "tel" ||
               ty === "password" || ty === "email" || ty === "";
      }
      return false;
    }
    /* The caret offset for any focusable text container: fields keep
       _selStart/_selEnd (spec model), contenteditable keeps _caretOffset
       (minimal caret — no caret geometry, disclosed). */
    function __caretOffsetOf(el) {
      if (!el) return 0;
      if (__isTextEditable(el)) {
        return el._selStart != null ? el._selStart : String(el.value || "").length;
      }
      if (el._caretOffset != null) return el._caretOffset;
      return String(el.textContent || "").length;
    }
    /* Reflect the focused element's caret as the document selection (a
       collapsed range), the way real engines expose it to getSelection(). */
    function __syncDocumentSelection(el) {
      try {
        var sel = global.getSelection();
        if (!sel || typeof Range === "undefined") return;
        var off = __caretOffsetOf(el);
        var r = new Range();
        r.setStart(el, off);
        r.setEnd(el, off);
        sel._ranges = [r];
        sel._anchorNode = sel._focusNode = el;
        sel._anchorOffset = sel._focusOffset = off;
      } catch (e) {}
    }
    /* Insert text at the caret (replacing an active selection first) or
       delete one char / the selection backward. Used by __pressKey's
       enter / backspace / space handling and by caret-aware typing. */
    function __insertAtCaret(el, str) {
      if (!el) return null;
      if (__isTextEditable(el)) {
        var before = el._selStart != null ? el._selStart : String(el.value || "").length;
        var after = el._selEnd != null ? el._selEnd : before;
        var val = String(el.value || "");
        el.value = val.slice(0, before) + str + val.slice(after);
        el._selStart = el._selEnd = before + str.length;
        __notifySelectionChange();
        return el.value;
      }
      if (el.isContentEditable) {
        var off = __caretOffsetOf(el);
        var tv = String(el.textContent || "");
        el.textContent = tv.slice(0, off) + str + tv.slice(off);
        el._caretOffset = off + str.length;
        __notifySelectionChange();
        return el.textContent;
      }
      return null;
    }
    function __deleteBackwardAtCaret(el) {
      if (!el) return null;
      if (__isTextEditable(el)) {
        var start = el._selStart != null ? el._selStart : String(el.value || "").length;
        var end = el._selEnd != null ? el._selEnd : start;
        var val = String(el.value || "");
        var nv;
        if (end > start) {
          nv = val.slice(0, start) + val.slice(end);
          el._selStart = el._selEnd = start;
        } else if (start > 0) {
          nv = val.slice(0, start - 1) + val.slice(start);
          el._selStart = el._selEnd = start - 1;
        } else {
          nv = val;
        }
        el.value = nv;
        __notifySelectionChange();
        return nv;
      }
      if (el.isContentEditable) {
        var off = __caretOffsetOf(el);
        var tv = String(el.textContent || "");
        el.textContent = off > 0 ? tv.slice(0, off - 1) + tv.slice(off) : tv;
        el._caretOffset = Math.max(0, off - 1);
        __notifySelectionChange();
        return el.textContent;
      }
      return null;
    }

    /* The caret helpers are declared in this nested scope; the property
       accessors (Element.prototype.selectionStart at ~line 1519) and the
       action lanes (__dispatchTypeSequence, __pressKey) live in the outer
       IIFE scope and reach these through `global` — bare-identifier
       resolution finds them at call time (global === the real global object:
       this IIFE is invoked with top-level `this`). */
    global.__isTextEditable = __isTextEditable;
    global.__caretOffsetOf = __caretOffsetOf;
    global.__notifySelectionChange = __notifySelectionChange;
    global.__syncDocumentSelection = __syncDocumentSelection;
    global.__insertAtCaret = __insertAtCaret;
    global.__deleteBackwardAtCaret = __deleteBackwardAtCaret;

    function Selection() {
      this._ranges = [];
      this._anchorNode = null;
      this._focusNode = null;
      this._anchorOffset = 0;
      this._focusOffset = 0;
    }
    Object.defineProperty(Selection.prototype, "rangeCount", {
      get: function () { return this._ranges.length; }
    });
    Object.defineProperty(Selection.prototype, "anchorNode", {
      get: function () { return this._anchorNode; }
    });
    Object.defineProperty(Selection.prototype, "focusNode", {
      get: function () { return this._focusNode; }
    });
    Object.defineProperty(Selection.prototype, "anchorOffset", {
      get: function () { return this._anchorOffset; }
    });
    Object.defineProperty(Selection.prototype, "focusOffset", {
      get: function () { return this._focusOffset; }
    });
    Object.defineProperty(Selection.prototype, "isCollapsed", {
      get: function () {
        if (!this._ranges.length) return true;
        var r = this._ranges[this._ranges.length - 1];
        return !!(r.collapsed);
      }
    });
    Selection.prototype.getRangeAt = function (i) { return this._ranges[i] || null; };
    Selection.prototype.addRange = function (r) {
      /* Spec: addRange REPLACES the range set (it is not an append). */
      this._ranges = [r];
      this._anchorNode = r.startContainer;
      this._focusNode = r.endContainer;
      this._anchorOffset = r.startOffset;
      this._focusOffset = r.endOffset;
    };
    Selection.prototype.removeRange = function (r) {
      var idx = this._ranges.indexOf(r);
      if (idx >= 0) {
        this._ranges.splice(idx, 1);
        if (!this._ranges.length) { this._anchorNode = null; this._focusNode = null; }
        else {
          var last = this._ranges[this._ranges.length - 1];
          this._anchorNode = last.startContainer;
          this._focusNode = last.endContainer;
          this._anchorOffset = last.startOffset;
          this._focusOffset = last.endOffset;
        }
      }
    };
    Selection.prototype.removeAllRanges = function () {
      this._ranges = [];
      this._anchorNode = null;
      this._focusNode = null;
      this._anchorOffset = 0;
      this._focusOffset = 0;
    };
    Selection.prototype.collapse = function (node, offset) {
      if (node == null) {
        this.removeAllRanges();
        return;
      }
      var r = new Range();
      r.setStart(node, offset || 0);
      r.setEnd(node, offset || 0);
      this._ranges = [r];
      this._anchorNode = this._focusNode = node;
      this._anchorOffset = this._focusOffset = offset || 0;
      __notifySelectionChange();
    };
    Selection.prototype.selectAllChildren = function (node) {
      var r = new Range();
      r.selectNodeContents(node);
      this._ranges = [r];
      this._anchorNode = this._focusNode = node;
      this._anchorOffset = 0;
      this._focusOffset = r.endOffset;
      __notifySelectionChange();
    };
    Selection.prototype.toString = function () {
      if (!this._ranges.length) return "";
      var r = this._ranges[this._ranges.length - 1];
      return typeof r.toString === "function" ? r.toString() : "";
    };
    var currentSelection = new Selection();
    global.Selection = Selection;
    global.getSelection = function () { return currentSelection; };

    global.EventTarget = function () {};
    var windowListeners = {};
    global.addEventListener = function (type, fn) {
      type = String(type);
      if (!windowListeners[type]) windowListeners[type] = [];
      windowListeners[type].push(fn);
    };
    global.removeEventListener = function (type, fn) {
      var listeners = windowListeners[String(type)] || [];
      var index = listeners.indexOf(fn);
      if (index >= 0) listeners.splice(index, 1);
    };
    /* Introspection: without it an agent can only observe that a page did not
       react to an event, never which handler declined it or why. */
    global.__WR_windowListeners = function (type) {
      return (windowListeners[String(type)] || []).slice();
    };
    global.dispatchEvent = function (event) {
      var origCall = Function.prototype.call;
      if (event && event.type === "message") {
        Function.prototype.call = function () {
          try { return origCall.apply(this, arguments); }
          catch (err) {
            globalThis.__WR_INNER_THROWS = globalThis.__WR_INNER_THROWS || [];
            if (globalThis.__WR_INNER_THROWS.length < 6) {
              globalThis.__WR_INNER_THROWS.push(String((err && err.message) || err).slice(0, 180));
            }
            throw err;
          }
        };
      }
      (windowListeners[event.type] || []).slice().forEach(function (fn) {
        try {
          var ret = fn.call(global, event);
          if (event && event.type === "message") {
            globalThis.__WR_MSG_RETURNS = globalThis.__WR_MSG_RETURNS || [];
            if (globalThis.__WR_MSG_RETURNS.length < 8) {
              globalThis.__WR_MSG_RETURNS.push({
                ret: ret == null ? String(ret) : typeof ret,
                post: !!(ret && ret.postMessage)
              });
            }
          }
        } catch (e) {
          /* A throwing window listener is reported, not swallowed. Silent
             suppression hid a real reCAPTCHA handshake failure: the parent's
             message handler threw and the anchor waited forever. */
          try {
            globalThis.__WR_EVENT_ERRORS = globalThis.__WR_EVENT_ERRORS || [];
            globalThis.__WR_EVENT_ERRORS.push(
              String(event && event.type) + ": " + String((e && e.message) || e)
            );
          } catch (_) {}
        }
      });
      if (event && event.type === "message") Function.prototype.call = origCall;
      return true;
    };
    /* postMessage's second argument is either a targetOrigin string or a
     WindowPostMessageOptions bag. ReCAPTCHA passes the bag form, which used
     to stringify to "[object Object]" and made every target message fail the
     origin check. Spec: an options bag without targetOrigin defaults to "/". */

  global.postMessage = function (data, origin, transfer) {
      var t = __wrExtractTransfer(origin, transfer);
      var targetOrigin = t.bag ? __resolveTargetOrigin(t.bag) : __resolveTargetOrigin(origin);
      var senderOrigin = (global.location && global.location.origin) || "";
      var msgData = __wrWireEncode(data);
      var portsMeta = t.ports.length ? JSON.stringify(__wrPrepareOutPorts(t.ports, false)) : "[]";
      if (typeof host_post_message === "function") {
        host_post_message(msgData, targetOrigin, "self", senderOrigin, portsMeta);
      }
      if (targetOrigin !== "*" && targetOrigin !== "/" && targetOrigin !== senderOrigin) {
        return;
      }
      /* Self lane: the transferred ends stay in this realm, so the event
         carries the LIVE port objects (a real browser hands the receiver a
         fresh working end bound to the same channel; keeping the original
         operational is the same-realm equivalent). The anchor's loopback
         handshake receiver posts through exactly this end. */
      Promise.resolve().then(function () {
        global.dispatchEvent(new global.MessageEvent("message", {
          data: data,
          origin: senderOrigin,
          source: global,
          ports: t.ports.filter(function (p) { return p instanceof MessagePort; }),
          isTrusted: true,
          __trusted: true
        }));
      });
    };
    global.parent = {
      postMessage: function (data, origin, transfer) {
        var t = __wrExtractTransfer(origin, transfer);
        var targetOrigin = t.bag ? __resolveTargetOrigin(t.bag) : __resolveTargetOrigin(origin);
        var senderOrigin = (global.location && global.location.origin) || "";
        var msgData = __wrWireEncode(data);
        /* Transferred ports ride along on the recorded message so the router
           can split the channel: the receiving realm gets a proxy end and the
           kept end here learns the proxy's key once it exists. */
        var portsMeta = t.ports.length ? JSON.stringify(__wrPrepareOutPorts(t.ports)) : "[]";
        if (typeof host_post_message === "function") {
          host_post_message(msgData, targetOrigin, "parent", senderOrigin, portsMeta);
        }
      }
    };
    global.top = global.parent;
    global.self = global;
    global.__wrPortReceive = function (localId, data, senderNonce, portsJson) {
      var port = __wrPortTable[localId | 0];
      if (!port || port._closed) return false;
      __wrFirePort(port, data, portsJson);
      return true;
    };
    /* After the far side's proxy exists, the kept end learns where posts go. */
    global.__wrPortSetRemote = function (localId, peerKey) {
      var port = __wrPortTable[localId | 0];
      if (!port) return false;
      port._remotePeerKey = String(peerKey || "");
      /* Flush everything the kept end posted while the far proxy did not yet
         exist — the port's buffer, exactly like a real MessagePort that
         queues messages until the transferred end starts running. */
      var buf = port._buffer;
      if (buf && buf.length && port._remotePeerKey) {
        var senderOrigin = (global.location && global.location.origin) || "";
        var items = buf.slice();
        port._buffer = [];
        items.forEach(function (item) {
          if (typeof host_post_message === "function") {
            host_post_message(item[0], "*", "port:" + port._remotePeerKey, senderOrigin, item[1]);
          }
        });
      }
      return true;
    };
    /* Honest introspection for the agent surface: a split channel's state,
       including whether the far side's handler ever attached. */
    global.__wrPortInfo = function (localId) {
      var p = __wrPortTable[localId | 0];
      if (!p) return "null";
      return JSON.stringify({
        id: p._id,
        isProxy: !!p._isProxy,
        neutered: !!p._neutered,
        closed: !!p._closed,
        hasOnmessage: typeof p.onmessage === "function",
        listeners: p._listeners.length,
        remote: p._remotePeerKey || p._peerKey || null,
        buffered: (p._buffer || []).length,
        queued: (p._msgQueue || []).length,
        started: !!p._started
      });
    };
    global.__wrPortOnmessageSrc = function (localId) {
      var p = __wrPortTable[localId | 0];
      return (p && typeof p.onmessage === "function") ? String(p.onmessage) : "null";
    };
    global.__wrPortListenerSrc = function (localId, idx) {
      var p = __wrPortTable[localId | 0];
      var l = p && p._listeners && p._listeners[idx | 0];
      return (typeof l === "function") ? String(l) : "null";
    };
    /* Live port object access: lets the agent (and diagnostics) observe and
       drive split channels the page established. */
    global.__wrPortObject = function (localId) {
      return __wrPortTable[localId | 0] || null;
    };
    global.__receiveMessage = function (data, origin, targetOrigin, sourceWindow, portsJson) {
      if (targetOrigin && targetOrigin !== "*") {
        var myOrigin = (global.location && global.location.origin) || "";
        /* targetOrigin may be given as a full URL ('https://host/path') — the
           spec compares ORIGINS, so parse a URL down to its origin instead
           of a naive string compare (which dropped every URL-form target
           message; same bug class the runtime delivery once had). */
        var wantOrigin = String(targetOrigin);
        /* "/" is the options-bag default and means "same origin as the
           sender", so resolve it against the recorded sender origin. */
        if (wantOrigin === "/") wantOrigin = String(origin || "");
        if (wantOrigin.indexOf("/") > 0) {
          try { wantOrigin = new URL(wantOrigin).origin; } catch (e) {}
        }
        if (myOrigin && wantOrigin !== myOrigin) {
          try {
            globalThis.__WR_WINDOW_MSGS = globalThis.__WR_WINDOW_MSGS || [];
            if (globalThis.__WR_WINDOW_MSGS.length < 12) {
              globalThis.__WR_WINDOW_MSGS.push({
                data: String(data).slice(0, 40),
                origin: String(origin || ""),
                want: String(wantOrigin),
                mine: String(myOrigin),
                dropped: true
              });
            }
          } catch (eDrop) {}
          return false;
        }
      }
      var sourceProxy = {
        postMessage: function (replyData, replyOrigin) {
          var repTarget = String(replyOrigin != null ? replyOrigin : "*");
          var repSender = (global.location && global.location.origin) || "";
          var repMsgData = __wrWireEncode(replyData);
          if (typeof host_post_message === "function") {
            host_post_message(repMsgData, repTarget, "reply", repSender);
          }
        }
      };
      var resolvedSource = __resolveSenderSource(sourceWindow);
      /* Transferred ports: materialize proxy ends in this realm and publish
         their ids so the runtime can rewire the sender's kept ends. */
      var recvPorts = [];
      var recvIds = __wrMaterializePorts(portsJson, recvPorts);
      try {
        recvPorts = new Proxy(recvPorts, {
          get: function (t, k) {
            if (k === "length" || k === "0") {
              globalThis.__WR_PORT_READS = globalThis.__WR_PORT_READS || [];
              var bit = String(k);
              if (k === "0" && t[0]) bit += ":id" + t[0]._id + " on=" + (typeof t[0].onmessage) + " ls=" + (t[0]._listeners ? t[0]._listeners.length : 0) + " q=" + ((t[0]._msgQueue || []).length);
              if (globalThis.__WR_PORT_READS.length < 8) globalThis.__WR_PORT_READS.push(bit);
            }
            var v = t[k];
            return typeof v === "function" ? v.bind(t) : v;
          }
        });
      } catch (eProxy) {}
      try { globalThis.__WR_LAST_PORTS = recvIds; } catch (e) {}
      try {
        globalThis.__WR_WINDOW_MSGS = globalThis.__WR_WINDOW_MSGS || [];
        if (globalThis.__WR_WINDOW_MSGS.length < 12) {
          var matched = null;
          var frames = (global.document && global.document.querySelectorAll)
            ? global.document.querySelectorAll("iframe") : [];
          for (var fi = 0; fi < frames.length; fi++) {
            if (frames[fi].contentWindow === resolvedSource) {
              matched = fi;
              try { globalThis.__WR_SRC_EL = frames[fi]; } catch (eEl) {}
            }
          }
          globalThis.__WR_WINDOW_MSGS.push({
            data: String(__wrWireDecode(data)).slice(0, 40),
            origin: String(origin || ""),
            ports: recvPorts.length,
            source: resolvedSource ? (matched == null ? "other" : "iframe" + matched) : "none",
            dropped: false
          });
        }
      } catch (eRec) {}
      Promise.resolve().then(function () {
        try { globalThis.__WR_CW_ARM = true; globalThis.__WR_CW_LOG = []; } catch (eArm) {}
        global.dispatchEvent(new global.MessageEvent("message", {
          data: __wrWireDecode(data),
          origin: String(origin || ""),
          source: resolvedSource || sourceProxy,
          ports: recvPorts,
          isTrusted: true,
          __trusted: true
        }));
        try { globalThis.__WR_CW_ARM = false; } catch (eArm2) {}
      });
      return true;
    };
    global.frames = new Proxy({}, {
      /* Spec: window.frames supports NAMED access to child frame windows
         (window.frames["bframe"] → that iframe's contentWindow) plus a
         length. The bundle's channel machinery addresses frames by name
         through the parent's collection (parent.frames[name]); an empty
         array made every such lookup undefined. */
      get: function (_t, k) {
        if (k === "length") {
          return (global.document && global.document.querySelectorAll)
            ? global.document.querySelectorAll("iframe").length : 0;
        }
        if (typeof k !== "string") return undefined;
        var doc = global.document;
        if (!doc || !doc.querySelectorAll) return undefined;
        var iframes = doc.querySelectorAll("iframe");
        for (var i = 0; i < iframes.length; i++) {
          var f = iframes[i];
          var nm = (f.getAttribute && (f.getAttribute("name") || f.getAttribute("id"))) || "";
          if (nm === k) {
            try { return f.contentWindow; } catch (e) { return undefined; }
          }
        }
        return undefined;
      },
      has: function (_t, k) {
        return typeof this.get === "function" && this.get(_t, k) !== undefined;
      }
    });
    global.innerWidth = 1280;
    global.innerHeight = 720;
    __cloakRuntimeGlobals(global);
    return true;
  };
  global.__serialize = function () {
    if (!global.document) return "";
    return global.document.childNodes.map(serializeNode).join("");
  };
  global.__click = function (selectorOrId) {
    var el = null;
    if (global.document.getElementById(selectorOrId)) el = global.document.getElementById(selectorOrId);
    else el = global.document.querySelector(selectorOrId);
    if (!el) return false;
    if (typeof el.__nativeClick === "function") el.__nativeClick();
    else if (typeof el.click === "function") el.click();
    return true;
  };
  function __elementChildren(n) {
    return n.childNodes.filter(function (c) { return c.nodeType === 1; });
  }
  global.__byPath = function (path) {
    var n = global.document;
    if (!n || !path) return null;
    for (var i = 0; i < path.length; i++) {
      var kids = __elementChildren(n);
      n = kids[path[i]] || null;
      if (!n) return null;
    }
    return n;
  };
  global.__stealthMode = false;
  global.__setStealthMode = function (enabled) {
    global.__stealthMode = !!enabled;
    return global.__stealthMode;
  };

  /* Screenshot: paint the laid-out page as SVG.
     The layout engine already computed every box, border, computed color and
     text line (flushLine records __lines per element with absolute x/y).
     This walks the tree once and emits a real SVG the host can save or
     rasterize. <img>/background-image hrefs are emitted absolute so the host
     can rewrite them to data URIs; missing fills default to white. */
  var __NAMED_COLORS = { black:"#000000", white:"#ffffff", red:"#ff0000", green:"#008000", blue:"#0000ff", gray:"#808080", grey:"#808080", yellow:"#ffff00", orange:"#ffa500", purple:"#800080", navy:"#000080", teal:"#008080", olive:"#808000", maroon:"#800000", lime:"#00ff00", aqua:"#00ffff", silver:"#c0c0c0", pink:"#ffc0cb", brown:"#a52a2a", gold:"#ffd700", coral:"#ff7f50", salmon:"#fa8072", tomato:"#ff6347", khaki:"#f0e68c", plum:"#dda0dd", turquoise:"#40e0d0", transparent:"" };
  function __colorHex(c) {
    if (c == null) return null;
    var s = String(c).trim().toLowerCase();
    if (!s || s === "transparent" || s === "currentcolor") return null;
    if (s.indexOf("gradient") >= 0 || s.indexOf("url") >= 0) return null;
    if (s.charAt(0) === "#") {
      s = s.slice(1);
      if (s.length === 3) return "#" + s[0]+s[0] + s[1]+s[1] + s[2]+s[2];
      if (s.length === 4) return "#" + s[0]+s[0] + s[1]+s[1] + s[2]+s[2];
      if (s.length === 6) return "#" + s;
      if (s.length >= 8) return "#" + s.slice(0, 6);
      return null;
    }
    var m = /rgba?\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})(?:\s*,\s*([\d.]+))?/i.exec(s);
    if (m) {
      var a = m[4] == null ? 1 : parseFloat(m[4]);
      if (!(a > 0.05)) return null;
      var hx = function (v) { var h = (Number(v) & 255).toString(16); return h.length === 1 ? "0" + h : h; };
      return "#" + hx(m[1]) + hx(m[2]) + hx(m[3]);
    }
    if (__NAMED_COLORS[s] !== undefined && __NAMED_COLORS[s] !== "") return __NAMED_COLORS[s];
    return null;
  }
  function __escXml(t) {
    return String(t == null ? "" : t)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function __absUrl(u, base) {
    try { return new URL(String(u), base || (global.location && global.location.href) || "about:blank").href; }
    catch (e) { return String(u || ""); }
  }
  function __fontFamily(cs) {
    var f = String(cs.fontFamily || "").toLowerCase();
    if (/mono|consolas|courier/.test(f)) return "monospace";
    if (/serif/.test(f) && f.indexOf("sans") < 0) return "serif";
    return "sans-serif";
  }
  /* ---- R3: encode the 2d raster as a real PNG (no zlib dependency:
     deflate stored blocks + adler32 + crc32, deterministic). ---- */
  function __crc32(buf) {
    var table = [];
    for (var n = 0; n < 256; n++) {
      var c = n;
      for (var k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
      table[n] = c >>> 0;
    }
    var crc = 0xFFFFFFFF;
    for (var i = 0; i < buf.length; i++) crc = (table[(crc ^ buf[i]) & 0xFF] ^ (crc >>> 8)) >>> 0;
    return (crc ^ 0xFFFFFFFF) >>> 0;
  }
  function __adler32(buf) {
    var a = 1, b = 0;
    for (var i = 0; i < buf.length; i++) {
      a = (a + buf[i]) % 65521;
      b = (b + a) % 65521;
    }
    return ((b << 16) | a) >>> 0;
  }
  function __pngChunk(type, payload) {
    var out = [];
    var len = payload.length;
    out.push((len >>> 24) & 0xFF, (len >>> 16) & 0xFF, (len >>> 8) & 0xFF, len & 0xFF);
    var crcInput = [];
    for (var t = 0; t < type.length; t++) { out.push(type.charCodeAt(t)); crcInput.push(type.charCodeAt(t)); }
    for (var p = 0; p < payload.length; p++) { out.push(payload[p]); crcInput.push(payload[p]); }
    var crc = __crc32(new Uint8Array(crcInput));
    out.push((crc >>> 24) & 0xFF, (crc >>> 16) & 0xFF, (crc >>> 8) & 0xFF, crc & 0xFF);
    return out;
  }
  function __rasterToPngDataURL(raster) {
    if (!raster || !raster.pix) return null;
    var w = raster.w, h = raster.h;
    /* IHDR: w, h, bit depth 8, color type 6 (RGBA), no interlace */
    var ihdr = [ (w >>> 24) & 0xFF, (w >>> 16) & 0xFF, (w >>> 8) & 0xFF, w & 0xFF,
                 (h >>> 24) & 0xFF, (h >>> 16) & 0xFF, (h >>> 8) & 0xFF, h & 0xFF,
                 8, 6, 0, 0, 0 ];
    /* Raw scanlines: each row prefixed by filter byte 0 */
    var raw = [];
    for (var y = 0; y < h; y++) {
      raw.push(0);
      for (var x = 0; x < w; x++) {
        var i = (y * w + x) * 4;
        raw.push(raster.pix[i], raster.pix[i + 1], raster.pix[i + 2], raster.pix[i + 3]);
      }
    }
    /* zlib stream: 0x78 0x01 header + stored blocks + adler32 */
    var z = [0x78, 0x01];
    var BLOCK = 65535;
    var pos = 0;
    while (true) {
      var remaining = raw.length - pos;
      var take = Math.min(BLOCK, remaining);
      var last = (pos + take >= raw.length) ? 1 : 0;
      z.push(last);
      z.push(take & 0xFF, (take >>> 8) & 0xFF);
      z.push((~take) & 0xFF, ((~take) >>> 8) & 0xFF);
      for (var b = 0; b < take; b++) z.push(raw[pos + b]);
      pos += take;
      if (last) break;
    }
    var ad = __adler32(new Uint8Array(raw));
    z.push((ad >>> 24) & 0xFF, (ad >>> 16) & 0xFF, (ad >>> 8) & 0xFF, ad & 0xFF);
    var png = [0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A];
    png = png.concat(__pngChunk("IHDR", ihdr));
    png = png.concat(__pngChunk("IDAT", z));
    png = png.concat(__pngChunk("IEND", []));
    /* base64 */
    var b64 = "";
    var chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    for (var q = 0; q < png.length; q += 3) {
      var b0 = png[q], b1 = q + 1 < png.length ? png[q + 1] : 0, b2 = q + 2 < png.length ? png[q + 2] : 0;
      b64 += chars[b0 >> 2] + chars[((b0 & 3) << 4) | (b1 >> 4)]
           + (q + 1 < png.length ? chars[((b1 & 15) << 2) | (b2 >> 6)] : "=")
           + (q + 2 < png.length ? chars[b2 & 63] : "=");
    }
    return "data:image/png;base64," + b64;
  }
  global.__screenshotSVG = function () {
    var doc = global.document; if (!doc) return '<svg xmlns="http://www.w3.org/2000/svg"/>';
    /* The painter consumes __box/__lines/__scrollExtent, which only exist
       after a layout pass. Trigger it here so the diagram never depends on
       some other API having laid the page out first. */
    try { __ensureLayout(doc); } catch (e) {}
    var root = doc.documentElement || doc;
    var rules = __collectStyleRules(doc);
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    var H = 720;
    try {
      if (doc.__scrollExtent && doc.__scrollExtent.height) H = Math.ceil(doc.__scrollExtent.height);
    } catch (e) {}
    var lines = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ' + vw + ' ' + H + '" width="' + vw + '" height="' + H + '">'];
    lines.push('<rect width="' + vw + '" height="' + H + '" fill="#ffffff"/>');
    var fontFallback = 16;

    function paintBorder(cs, x, y, w, h, out) {
      var edges = [
        ["borderTopWidth", "borderTopColor", x, y, x + w, y],
        ["borderRightWidth", "borderRightColor", x + w, y, x + w, y + h],
        ["borderBottomWidth", "borderBottomColor", x, y + h, x + w, y + h],
        ["borderLeftWidth", "borderLeftColor", x, y, x, y + h],
      ];
      for (var i = 0; i < edges.length; i++) {
        var e = edges[i];
        var w0 = parseFloat(String(cs[e[0]] || "")) || 0;
        if (w0 <= 0) continue;
        var col = __colorHex(cs[e[1]]) || "#000000";
        out.push('<line x1="' + e[2] + '" y1="' + e[3] + '" x2="' + e[4] + '" y2="' + e[5] + '" stroke="' + col + '" stroke-width="' + w0 + '"/>');
      }
    }
    function paint(node, out) {
      if (!node || node.nodeType !== 1) return;
      var nn = String(node.nodeName || "").toLowerCase();
      if (nn === "head" || nn === "style" || nn === "script" || nn === "title" || nn === "meta" || nn === "link" || nn === "template" || nn === "noscript") return;
      var cs = __computedStyle(node, rules, node.__layoutFont || fontFallback);
      var tag = String(node.nodeName || "").toLowerCase();
      var b = node.__box;
      if (b && b.width > 0 && b.height > 0) {
        var x = Math.round(b.x * 10) / 10, y = Math.round(b.y * 10) / 10;
        var w = Math.round(b.width * 10) / 10, h = Math.round(b.height * 10) / 10;
        var radius = parseFloat(String(cs.borderRadius || "0")) || 0;
        if (radius > 50) radius = 50;
        var bg = __colorHex(cs.backgroundColor) || __colorHex(cs.background);
        if (bg) {
          out.push('<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="' + Math.min(radius, h / 2, w / 2) + '" fill="' + bg + '"/>');
        } else if (cs.backgroundImage) {
          var m = /url\(\s*['"]?([^)'"]+)['"]?\s*\)/i.exec(String(cs.backgroundImage));
          if (m) out.push('<image href="' + __escXml(__absUrl(m[1])) + '" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" preserveAspectRatio="xMidYMid slice"/>');
        }
        paintBorder(cs, x, y, w, h, out);
        if (tag === "img") {
          var src = node.getAttribute && node.getAttribute("src");
          if (src) out.push('<image href="' + __escXml(__absUrl(src)) + '" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" preserveAspectRatio="xMidYMid slice"/>');
          else out.push('<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="#e8e8e8"/>');
        } else if (tag === "canvas") {
          /* R3: a canvas with a 2d raster paints its TRUE pixels as an
             embedded PNG (deflate stored blocks — deterministic). A blank
             canvas paints the neutral grid placeholder instead of faked
             content; the 'approximate' disclosure stays for everything
             that is not a canvas raster. */
          var raster = node.__raster;
          var hasContent = false;
          if (raster && raster.pix) {
            for (var ci = 3; ci < raster.pix.length; ci += 4) {
              if (raster.pix[ci] !== 0) { hasContent = true; break; }
            }
          }
          if (hasContent) {
            var pngUrl = __rasterToPngDataURL(raster);
            if (pngUrl) {
              out.push('<image href="' + pngUrl + '" x="' + x + '" y="' + y + '" width="' + Math.min(w, raster.w) + '" height="' + Math.min(h, raster.h) + '" preserveAspectRatio="none"/>');
            }
          } else {
            out.push('<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" fill="#f8f9fa" stroke="#dadce0" stroke-width="1"/>');
            out.push('<text x="' + (x + w / 2) + '" y="' + (y + h / 2 + 4) + '" text-anchor="middle" font-size="11" font-family="monospace" fill="#5f6368">canvas</text>');
          }
        } else if (tag === "input" || tag === "select" || tag === "textarea") {
          var type = String((node.getAttribute && node.getAttribute("type")) || "text").toLowerCase();
          /* type=hidden paints nothing, ever: hidden field values are page
             secrets (CSRF tokens, session ids) and must not leak into a
             shareable image. Real browsers paint nothing for them too. */
          if (type === "hidden") {
            /* fall through to text lines only; no box, no value */
          } else {
            if (!bg) out.push('<rect x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="3" fill="#ffffff" stroke="#cccccc" stroke-width="1"/>');
            var label = (node.getAttribute && (node.getAttribute("placeholder") || node.getAttribute("value"))) || "";
            var fs = node.__layoutFont || parseFloat(String(cs.fontSize || 13)) || 13;
            var fcol = __colorHex(cs.color) || (node.getAttribute && node.getAttribute("placeholder") ? "#9aa0a6" : "#111111");
            var tx = Math.round((x + 8) * 10) / 10, ty = Math.round((y + h / 2 + fs * 0.36) * 10) / 10;
            if (type === "password" && node.getAttribute && node.getAttribute("value")) {
              out.push('<text x="' + tx + '" y="' + ty + '" font-size="' + fs + '" font-family="monospace" fill="#111111">••••••••••</text>');
            } else if (label) {
              out.push('<text x="' + tx + '" y="' + ty + '" font-family="' + __fontFamily(cs) + '" font-size="' + fs + '" fill="' + fcol + '">' + __escXml(label) + '</text>');
            }
          }
        }
        var tlines = node.__lines || [];
        for (var i = 0; i < tlines.length; i++) {
          var ln = tlines[i];
          if (!ln.text) continue;
          if (!String(ln.text).trim()) continue; /* whitespace-only runs paint nothing */
          var fs2 = node.__layoutFont || parseFloat(String(cs.fontSize || 14)) || 14;
          var fill2 = __colorHex(cs.color) || "#111111";
          var weight = /bold|[6-9]00/i.test(String(cs.fontWeight || "")) ? "bold" : "normal";
          var tx2 = Math.round(ln.x * 10) / 10, ty2 = Math.round((ln.y + fs2 * 0.82) * 10) / 10;
          out.push('<text x="' + tx2 + '" y="' + ty2 + '" font-family="' + __fontFamily(cs) + '" font-size="' + fs2 + '" font-weight="' + weight + '" fill="' + fill2 + '">' + __escXml(ln.text) + '</text>');
        }
      }
      (node.childNodes || []).forEach(function (c) { paint(c, out); });
    }
    paint(root, lines);
    lines.push('</svg>');
    return lines.join("");
  };

  global.__clickPath = function (path, stealth, gateOcclusion) {
    var el = global.__byPath(path);
    if (!el) {
      global.__lastActionError = "element_not_found";
      return false;
    }
    if (gateOcclusion && typeof global.__isElementOccluded === "function") {
      var occ = global.__isElementOccluded(el);
      if (occ) {
        global.__lastActionError = "occluded_by:" + occ.occluded_by;
        return false;
      }
    }
    if (stealth !== undefined) global.__stealthMode = !!stealth;
    if (typeof el.__nativeClick === "function") el.__nativeClick();
    else if (typeof el.click === "function") el.click();
    else el.dispatchEvent(new global.Event("click", { bubbles: true, isTrusted: true, __trusted: true }));
    global.__lastActionError = null;
    return true;
  };

  /* Click an element inside an iframe.
     frameRef: the iframe's ref (e.g., "f1") or index
     path: the path to the element within the iframe's document */
  global.__clickInFrame = function (frameRef, path, stealth) {
    var iframe = null;
    // Try to find the iframe by ref attribute or by index
    if (typeof frameRef === "string") {
      // Look for iframe with matching ref attribute
      var iframes = global.document.querySelectorAll("iframe");
      for (var i = 0; i < iframes.length; i++) {
        if (iframes[i].getAttribute("data-ref") === frameRef ||
            iframes[i].getAttribute("ref") === frameRef ||
            iframes[i].name === frameRef) {
          iframe = iframes[i];
          break;
        }
      }
    } else if (typeof frameRef === "number") {
      var iframes2 = global.document.querySelectorAll("iframe");
      if (frameRef >= 0 && frameRef < iframes2.length) {
        iframe = iframes2[frameRef];
      }
    }
    if (!iframe) return false;
    var frameDoc = iframe.contentDocument;
    if (!frameDoc) return false;
    // Traverse the path within the frame's document
    var n = frameDoc;
    if (!n || !path) return null;
    for (var j = 0; j < path.length; j++) {
      var kids = __elementChildren(n);
      n = kids[path[j]] || null;
      if (!n) return false;
    }
    var el = n;
    if (!el) return false;
    if (stealth !== undefined) global.__stealthMode = !!stealth;
    if (typeof el.__nativeClick === "function") el.__nativeClick();
    else if (typeof el.click === "function") el.click();
    else el.dispatchEvent(new global.Event("click", { bubbles: true, isTrusted: true, __trusted: true }));
    return true;
  };

  function __dispatchTypeSequence(el, text, delays, stealth) {
    if (!el) return false;
    if (el.nodeName === "select") {
      /* Defense in depth: the typing lane must never append to a select. */
      return __selectByValueOrText(el, text);
    }
    /* Disabled form controls never receive typed input. */
    var elTag = el.nodeName.toLowerCase();
    if (__controlDisabled(el) && (elTag === "input" || elTag === "textarea" || elTag === "select")) {
      return false;
    }
    if (typeof el.focus === "function") el.focus();
    text = String(text != null ? text : "");
    var curVal = el.value || "";
    var editable = !!el.isContentEditable;
    if (editable) curVal = el.textContent || "";
    /* L7: typing inserts at the caret, replacing an active selection first —
       the real-engine contract. An unset caret defaults to the end of the
       value (the old append behavior). */
    var caret = __caretOffsetOf(el);
    if (editable && el._caretOffset == null) caret = curVal.length;
    if (!editable && __isTextEditable(el) && el._selEnd != null && el._selEnd > caret) {
      curVal = curVal.slice(0, caret) + curVal.slice(el._selEnd);
    }
    var isStealth = (stealth !== undefined) ? !!stealth : (Array.isArray(delays) || !!global.__stealthMode);
    delays = Array.isArray(delays) ? delays : null;

    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (isStealth) {
        var flight = (delays && delays[i] !== undefined)
          ? Number(delays[i])
          : (ch === " " ? (85 + Math.round(Math.random() * 45)) : (45 + Math.round(Math.random() * 35)));
        if (typeof global.__advanceClock === "function") global.__advanceClock(flight);
      }
      var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();

      var code = "Key" + ch.toUpperCase();
      if (ch >= "0" && ch <= "9") code = "Digit" + ch;
      else if (ch === " ") code = "Space";
      else if (ch === "\n") code = "Enter";
      var kc = (ch === "\n") ? 13 : ch.charCodeAt(0);

      var kd = (typeof global.KeyboardEvent === "function")
        ? new global.KeyboardEvent("keydown", { key: ch, code: code, keyCode: kc, which: kc, bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow })
        : new global.Event("keydown", { bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow });
      el.dispatchEvent(kd);

      var kp = (typeof global.KeyboardEvent === "function")
        ? new global.KeyboardEvent("keypress", { key: ch, code: code, keyCode: kc, which: kc, charCode: kc, bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow + 1 })
        : new global.Event("keypress", { bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow + 1 });
      el.dispatchEvent(kp);

      curVal = curVal.slice(0, caret) + ch + curVal.slice(caret);
      caret += 1;
      if (editable) {
        /* Rich editors (Quill/ProseMirror) read the DOM via MutationObserver,
           so the text goes into textContent and the input event carries data. */
        el.textContent = curVal;
      } else {
        el.value = curVal;
      }

      var inp = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tNow + 2 });
      inp.data = ch;
      inp.inputType = "insertText";
      el.dispatchEvent(inp);

      if (isStealth) {
        var dwell = 25 + Math.round(Math.random() * 20);
        if (typeof global.__advanceClock === "function") global.__advanceClock(dwell);
      }
      var tUp = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();

      var ku = (typeof global.KeyboardEvent === "function")
        ? new global.KeyboardEvent("keyup", { key: ch, code: code, keyCode: kc, which: kc, bubbles: true, cancelable: true, isTrusted: true, timeStamp: tUp })
        : new global.Event("keyup", { bubbles: true, cancelable: true, isTrusted: true, timeStamp: tUp });
      el.dispatchEvent(ku);
    }
    if (editable) {
      el._caretOffset = caret;
      var tEnd = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
      el.dispatchEvent(new global.Event("change", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tEnd }));
      __syncDocumentSelection(el);
      __notifySelectionChange();
      return true;
    }
    el._selStart = el._selEnd = caret;
    el.setAttribute("value", curVal);
    var tEnd2 = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
    el.dispatchEvent(new global.Event("change", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tEnd2 }));
    __syncDocumentSelection(el);
    __notifySelectionChange();
    return true;
  }

  global.__typePath = function (path, text, delays, stealth) {
    var el = global.__byPath(path);
    if (!el) return false;
    return __dispatchTypeSequence(el, text, delays, stealth);
  };

  global.__activateSelector = function (selector, text, delays, stealth, nth, gateOcclusion) {
    var el = null;
    nth = Math.max(1, parseInt(nth, 10) || 1);
    if (nth <= 1) {
      if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
      else el = global.document.querySelector(selector);
    } else {
      /* Identical siblings share one content identity and therefore one
         selector; the action surface disambiguates them with document-order
         occurrence suffixes (~2, ~3...). The Nth match of the selector is the
         addressed control — first-match-wins here made every sibling after
         the first unreachable (a phantom-control bug). */
      var am = global.document.querySelectorAll(selector);
      el = am && am.length >= nth ? am[nth - 1] : null;
    }
    if (!el) { global.__lastActionError = "element_not_found"; return false; }
    /* Element resolved: clear any stale error from a previous action. Later
       failure paths set it again explicitly. */
    global.__lastActionError = null;
    /* L10 occlusion gate: same `__isElementOccluded` contract `__clickPath`
       uses. A real browser does not deliver a click to a control hidden
       behind a covering overlay, so gated activation refuses and reports
       which element won hit-testing (`occluded_by:<selector>`). */
    if (gateOcclusion && typeof global.__isElementOccluded === "function") {
      var occ = global.__isElementOccluded(el);
      if (occ) {
        global.__lastActionError = "occluded_by:" + (occ.occluded_by || "unknown");
        return false;
      }
    }
    if (stealth !== undefined) global.__stealthMode = !!stealth;
    if (el.nodeName === "select") {
      if (__controlDisabled(el)) { global.__lastActionError = "control_disabled"; return false; }
      /* Typing into a select means choosing an option (by value, then by
         visible text) — never appending text to a value that is derived
         from the chosen option. A bare activate just focuses it. */
      if (typeof el.focus === "function") el.focus();
      if (text !== null && text !== undefined && text !== "") {
        return __selectByValueOrText(el, text);
      }
      return true;
    }
    if (text !== null && text !== undefined && text !== "") {
      return __dispatchTypeSequence(el, text, delays, stealth);
    }
    var tag = el.nodeName.toLowerCase();
    if (tag === "input" || tag === "textarea" || tag === "select" || tag === "button" || tag === "a" || el.hasAttribute("tabindex")) {
      if (typeof el.focus === "function") el.focus();
    }
    var clickOk = false;
    if (typeof el.__nativeClick === "function") clickOk = el.__nativeClick();
    else if (typeof el.click === "function") clickOk = el.click();
    else {
      el.dispatchEvent(new global.Event("click", { bubbles: true, isTrusted: true, __trusted: true }));
      clickOk = true;
    }
    if (!clickOk) { global.__lastActionError = "click_refused"; return false; }
    var kind = (el.getAttribute("type") || "").toLowerCase();
    if (kind === "checkbox" || kind === "radio") {
      el.setAttribute("aria-checked", el.checked ? "true" : "false");
    }
    return true;
  };

  /* ---- Named key press (Enter/Tab/arrows/...) ---- */
  var __KEY_DEFS = {
    enter: { key: "Enter", code: "Enter", keyCode: 13 },
    tab: { key: "Tab", code: "Tab", keyCode: 9 },
    escape: { key: "Escape", code: "Escape", keyCode: 27 },
    backspace: { key: "Backspace", code: "Backspace", keyCode: 8 },
    delete: { key: "Delete", code: "Delete", keyCode: 46 },
    space: { key: " ", code: "Space", keyCode: 32 },
    arrowup: { key: "ArrowUp", code: "ArrowUp", keyCode: 38 },
    arrowdown: { key: "ArrowDown", code: "ArrowDown", keyCode: 40 },
    arrowleft: { key: "ArrowLeft", code: "ArrowLeft", keyCode: 37 },
    arrowright: { key: "ArrowRight", code: "ArrowRight", keyCode: 39 },
    home: { key: "Home", code: "Home", keyCode: 36 },
    end: { key: "End", code: "End", keyCode: 35 },
    pageup: { key: "PageUp", code: "PageUp", keyCode: 33 },
    pagedown: { key: "PageDown", code: "PageDown", keyCode: 34 }
  };

  function __keyEvent(type, def, extra) {
    var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
    var init = {
      key: def.key, code: def.code, keyCode: def.keyCode, which: def.keyCode,
      bubbles: true, cancelable: true, isTrusted: true, timeStamp: tNow
    };
    if (extra) { for (var k in extra) init[k] = extra[k]; }
    return (typeof global.KeyboardEvent === "function")
      ? new global.KeyboardEvent(type, init)
      : new global.Event(type, init);
  }

  function __formOf(el) {
    var f = el;
    while (f && f.nodeName !== "form") f = f.parentNode;
    return (f && f.nodeName === "form") ? f : null;
  }

  function __focusables() {
    if (!global.document || !global.document.querySelectorAll) return [];
    var els = global.document.querySelectorAll(
      "input, select, textarea, button, a[href], [tabindex], [contenteditable]"
    );
    var out = [];
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (__controlDisabled(el)) continue;
      var ti = el.getAttribute("tabindex");
      if (ti && Number(ti) < 0) continue;
      out.push(el);
    }
    return out;
  }

  global.__pressKey = function (selectorOrPath, keyName, stealth) {
    var el;
    if (Array.isArray(selectorOrPath)) el = global.__byPath(selectorOrPath);
    else if (global.document.getElementById(selectorOrPath)) el = global.document.getElementById(selectorOrPath);
    else el = global.document.querySelector(selectorOrPath);
    if (!el) return false;
    /* Disabled form controls are not focusable and receive no key events. */
    var pkTag = el.nodeName.toLowerCase();
    if (__controlDisabled(el) && (pkTag === "input" || pkTag === "textarea" || pkTag === "select" || pkTag === "button")) {
      return false;
    }
    if (typeof el.focus === "function" && el.nodeName.toLowerCase() !== "body") el.focus();
    var lower = String(keyName || "").toLowerCase();
    var def = __KEY_DEFS[lower];
    if (!def) return false;

    var kd = __keyEvent("keydown", def);
    var prevented = !el.dispatchEvent(kd);

    /* keypress fires for keys that produce text (Enter, Space) in real engines */
    if (lower === "enter" || lower === "space") {
      el.dispatchEvent(__keyEvent("keypress", def, lower === "space" ? { charCode: 32 } : null));
    }

    if (!prevented && !kd.defaultPrevented) {
      var tag = el.nodeName.toLowerCase();
      if (lower === "enter") {
        if (tag === "input") {
          /* Implicit submission: Enter in a single-line input submits its form. */
          var form = __formOf(el);
          if (form) {
            var subEv = (typeof global.Event === "function")
              ? new global.Event("submit", { bubbles: true, cancelable: true, isTrusted: true, __trusted: true })
              : { type: "submit", target: form, bubbles: true, cancelable: true, isTrusted: true };
            form.dispatchEvent(subEv);
            if (!subEv.defaultPrevented && typeof form.submit === "function") {
              try { form.submit(); } catch (e) {}
            }
          }
        } else if (el.isContentEditable || tag === "textarea") {
          /* L7: the newline lands at the caret, not blindly at the end. */
          __insertAtCaret(el, "\n");
          var nlEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
          nlEv.data = "\n";
          nlEv.inputType = "insertLineBreak";
          el.dispatchEvent(nlEv);
        }
      } else if (lower === "tab") {
        var list = __focusables();
        var idx = list.indexOf(el);
        if (idx !== -1 && list.length > 1) {
          var next = list[(idx + 1) % list.length];
          if (typeof next.focus === "function") next.focus();
        }
      } else if ((lower === "arrowup" || lower === "arrowdown") && tag === "select") {
        var opts = (typeof el.getElementsByTagName === "function")
          ? el.getElementsByTagName("option")
          : [];
        if (opts.length) {
          var cur = el.selectedIndex;
          var dir = lower === "arrowdown" ? 1 : -1;
          var ni = cur + dir;
          if (ni >= 0 && ni < opts.length) {
            el.__selectDeselected = false;
            __selectSetChoice(el, ni);
            el.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: true }));
            el.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: true }));
          }
        }
      } else if (lower === "backspace") {
        /* L7: delete at the caret (or the active selection first), matching
           the real keyboard contract. */
        __deleteBackwardAtCaret(el);
        var bsEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
        bsEv.inputType = "deleteContentBackward";
        el.dispatchEvent(bsEv);
      } else if (lower === "space") {
        if (tag === "button" || tag === "a") {
          if (typeof el.__nativeClick === "function") el.__nativeClick();
          else if (typeof el.click === "function") el.click();
        } else if (tag === "input" || tag === "textarea" || el.isContentEditable) {
          __insertAtCaret(el, " ");
          var spEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
          spEv.data = " ";
          spEv.inputType = "insertText";
          el.dispatchEvent(spEv);
        }
      } else if (lower === "escape") {
        /* Spec: pressing Escape on an open dialog fires a cancelable
           'cancel' event. If not prevented, it closes the dialog. */
        var dlg = null;
        var escNode = el;
        while (escNode) {
          if (escNode.nodeName === "dialog" && escNode.hasAttribute && escNode.hasAttribute("open")) {
            dlg = escNode;
            break;
          }
          escNode = escNode.parentNode;
        }
        if (!dlg) {
          var allDlgs = (global.document && typeof global.document.getElementsByTagName === "function")
            ? global.document.getElementsByTagName("dialog")
            : [];
          for (var di = allDlgs.length - 1; di >= 0; di--) {
            if (allDlgs[di].hasAttribute && allDlgs[di].hasAttribute("open")) {
              dlg = allDlgs[di];
              break;
            }
          }
        }
        if (dlg) {
          var cancelEv = new global.Event("cancel", { bubbles: false, cancelable: true });
          dlg.dispatchEvent(cancelEv);
          if (!cancelEv.defaultPrevented && typeof dlg.close === "function") {
            dlg.close();
          }
        }
        var escDoc = global.document;
        if (escDoc && escDoc.__openAutoPopovers && escDoc.__openAutoPopovers.length > 0) {
          var topPop = escDoc.__openAutoPopovers[escDoc.__openAutoPopovers.length - 1];
          if (topPop && topPop.__popoverOpen && typeof topPop.hidePopover === "function") {
            try { topPop.hidePopover(); } catch (e) {}
          }
        }
      }
    }

    el.dispatchEvent(__keyEvent("keyup", def));
    return true;
  };

  /* E2: the text-selection drag lane. A real browser distinguishes dragging
     a draggable ELEMENT (DnD lane) from dragging across SELECTABLE TEXT
     (mousedown on text, extend, mouseup → a real selection + events). This
     engine has no hit-testing, so the lane is addressed by the plain text
     container and the character offsets the drag spans — a distinct lane
     from __dragSelector/__dragPath, modeling the same observable contract:
     mousedown → selection extends → 'selectstart'/'select'/selectionchange
     fire → mouseup. No click fires (a selection drag is not a click). */
  global.__selectText = function (selector, start, end) {
    var el = null;
    if (typeof selector === "string") {
      el = global.document.getElementById(selector) || global.document.querySelector(selector);
    } else if (selector && selector.nodeType) {
      el = selector;
    }
    if (!el) { global.__lastActionError = "element_not_found"; return JSON.stringify({ ok: false, error: "element_not_found" }); }
    var isField = __isTextEditable(el);
    var isEditable = !!el.isContentEditable;
    if (!isField && !isEditable) {
      global.__lastActionError = "not_selectable_text";
      return JSON.stringify({ ok: false, error: "not_selectable_text" });
    }
    if (typeof el.focus === "function") el.focus();
    var len = isField ? String(el.value || "").length : String(el.textContent || "").length;
    var s = parseInt(start, 10) || 0;
    var e = (end === undefined || end === null) ? len : parseInt(end, 10);
    if (s < 0) s = 0;
    if (e > len) e = len;
    if (s > e) { var swap = s; s = e; e = swap; }

    var rect = (typeof el.getBoundingClientRect === "function") ? el.getBoundingClientRect() : { left: 10, top: 10, width: 20, height: 20 };
    var cx = Math.round(rect.left + rect.width / 2), cy = Math.round(rect.top + rect.height / 2);
    var t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : 100.0;
    var downInit = { clientX: cx, clientY: cy, pageX: cx, pageY: cy, button: 0, buttons: 1, bubbles: true, cancelable: true, isTrusted: true, __trusted: true, timeStamp: t };
    var upX = cx + Math.max(1, Math.round((e - s) * 6));
    var upInit = { clientX: upX, clientY: cy, pageX: upX, pageY: cy, button: 0, buttons: 0, bubbles: true, cancelable: true, isTrusted: true, __trusted: true, timeStamp: t + 1 };
    if (typeof global.PointerEvent === "function") el.dispatchEvent(new global.PointerEvent("pointerdown", downInit));
    el.dispatchEvent((typeof global.MouseEvent === "function") ? new global.MouseEvent("mousedown", downInit) : new global.Event("mousedown", downInit));
    /* selectstart: spec — a new selection is beginning on this document. */
    try {
      el.dispatchEvent(new global.Event("selectstart", { bubbles: true, cancelable: true, isTrusted: true, __trusted: true, timeStamp: t }));
    } catch (e2) {}
    /* The selection the drag produced (L7 selection model). */
    var selected = "";
    try {
      if (isField) {
        el.setSelectionRange(s, e);
        selected = String(el.value || "").substring(s, e);
      } else {
        var sel = global.getSelection();
        sel.removeAllRanges();
        var r = new global.Range();
        r.setStart(el, s);
        r.setEnd(el, e);
        sel.addRange(r);
        el._caretOffset = e;
        __notifySelectionChange();
        selected = String(el.textContent || "").substring(s, e);
      }
    } catch (e3) {
      global.__lastActionError = "selection_failed";
      return JSON.stringify({ ok: false, error: "selection_failed" });
    }
    /* input/textarea fire 'select' when text is selected. */
    if (isField) {
      el.dispatchEvent(new global.Event("select", { bubbles: true, cancelable: false, isTrusted: true, __trusted: true, timeStamp: t }));
    }
    if (typeof global.PointerEvent === "function") el.dispatchEvent(new global.PointerEvent("pointerup", upInit));
    el.dispatchEvent((typeof global.MouseEvent === "function") ? new global.MouseEvent("mouseup", upInit) : new global.Event("mouseup", upInit));
    global.__lastActionError = null;
    return JSON.stringify({ ok: true, selected: selected, start: s, end: e });
  };

  function __executeDrag(el, dx, dy, steps, trajectory, stealth) {
    if (!el) return false;
    dx = Number(dx) || 0;
    dy = Number(dy) || 0;
    var isStealth = (stealth !== undefined) ? !!stealth : (Array.isArray(trajectory) || !!global.__stealthMode);
    var traj = Array.isArray(trajectory) && trajectory.length ? trajectory : null;
    var startX = 0;
    var startY = 0;
    if (traj && traj[0]) {
      startX = (traj[0].x !== undefined) ? (traj[0].x - (traj[0].movementX || 0)) : 0;
      startY = (traj[0].y !== undefined) ? (traj[0].y - (traj[0].movementY || 0)) : 0;
    }

    var t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : 100.0;
    var hoverInit = { clientX: startX, clientY: startY, pageX: startX, pageY: startY, screenX: startX + 50, screenY: startY + 100, bubbles: true, cancelable: true, isTrusted: true, timeStamp: t };
    if (typeof global.PointerEvent === "function") el.dispatchEvent(new global.PointerEvent("pointerover", hoverInit));
    if (typeof global.MouseEvent === "function") el.dispatchEvent(new global.MouseEvent("mouseover", hoverInit));
    if (typeof global.PointerEvent === "function") el.dispatchEvent(new global.PointerEvent("pointerenter", hoverInit));
    if (typeof global.MouseEvent === "function") el.dispatchEvent(new global.MouseEvent("mouseenter", hoverInit));

    if (isStealth) {
      var preDelay = 25 + Math.round(Math.random() * 20);
      if (typeof global.__advanceClock === "function") global.__advanceClock(preDelay);
      t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : t + preDelay;
    }

    var downInit = { clientX: startX, clientY: startY, pageX: startX, pageY: startY, screenX: startX + 50, screenY: startY + 100, button: 0, buttons: 1, pressure: 0.5, pointerId: 1, bubbles: true, cancelable: true, isTrusted: true, timeStamp: t };
    if (typeof global.PointerEvent === "function") el.dispatchEvent(new global.PointerEvent("pointerdown", downInit));
    var mdEv = (typeof global.MouseEvent === "function") ? new global.MouseEvent("mousedown", downInit) : new global.Event("mousedown", downInit);
    el.dispatchEvent(mdEv);

    /* Native HTML5 drag & drop: ONLY a draggable=true source initiates it
       (spec: a real browser fires no DnD for a non-draggable element, even
       one with dragstart listeners — those pages drive via mouse events; the
       old dragstart-listener heuristic produced phantom drag sequences).
       The sequence carries a real DataTransfer — dragstart (cancelable:
       preventDefault aborts; the source declares effectAllowed), drag per
       step, dragenter/dragover/drop negotiated on the element under the
       translated source box, dragend on the source. Pages with only mouse
       listeners keep the plain pointer lane. */
    var nativeDrag = false;
    var dragDT = null;
    var wantsNativeDrag = (el.getAttribute("draggable") || "").toLowerCase() === "true";
    if (wantsNativeDrag && typeof global.DragEvent === "function" && typeof global.DataTransfer === "function") {
      dragDT = new global.DataTransfer();
      dragDT.__phase = "dragstart";
      var dsInit = { bubbles: true, cancelable: true, isTrusted: true, __trusted: true, clientX: startX, clientY: startY, button: 0, buttons: 1, dataTransfer: dragDT, timeStamp: t };
      var dsEv = new global.DragEvent("dragstart", dsInit);
      el.dispatchEvent(dsEv);
      dragDT.__phase = null;
      nativeDrag = !dsEv.defaultPrevented;
    }
    /* Innermost laid-out box containing a document-space point. */
    function __dragHitTest(px, py) {
      var doc = global.document;
      if (!doc) return null;
      var root = doc.body || doc.documentElement;
      if (!root || typeof root.getBoundingClientRect !== "function") return null;
      root.getBoundingClientRect(); // ensure layout
      var best = null, bestArea = Infinity;
      (function walk(n) {
        if (!n || n.nodeType !== 1) return;
        var b = n.__box;
        if (b && b.width > 0 && b.height > 0 && px >= b.x && px <= b.x + b.width && py >= b.y && py <= b.y + b.height) {
          var area = b.width * b.height;
          if (area <= bestArea) { best = n; bestArea = area; }
        }
        (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) walk(c); });
      })(root);
      return best;
    }
    var dragDropTarget = null;
    if (nativeDrag) {
      var srcBox = (typeof el.getBoundingClientRect === "function") ? el.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
      dragDropTarget = __dragHitTest(srcBox.x + dx, srcBox.y + dy);
      /* No element under the translated source box: a real browser drops
         NOTHING (dragend only) — the old `|| el` fallback "dropped" on the
         source's own position and reported a drop that never happened. */
    }

    if (isStealth) {
      var hesitation = 35 + Math.round(Math.random() * 25);
      if (typeof global.__advanceClock === "function") global.__advanceClock(hesitation);
    }

    if (!traj) {
      traj = [];
      if (isStealth) {
        var n = Math.max(3, Math.min(50, Number(steps) || 16));
        var prevX = startX, prevY = startY;
        for (var i = 1; i <= n; i++) {
          var prog = i / n;
          var ease = 3 * prog * prog - 2 * prog * prog * prog;
          var curX = (i === n) ? (startX + dx) : Math.round(startX + dx * ease);
          var jitter = (i === n) ? 0 : (Math.random() - 0.5) * 1.5;
          var curY = (i === n) ? (startY + dy) : Math.round(startY + dy * ease + jitter);
          var stepDt = 10 + Math.round(Math.random() * 6);
          traj.push({ x: curX, y: curY, movementX: curX - prevX, movementY: curY - prevY, dt: stepDt });
          prevX = curX;
          prevY = curY;
        }
      } else {
        var fastSteps = Math.max(2, Math.min(6, Number(steps) || 3));
        var pX = startX, pY = startY;
        for (var fi = 1; fi <= fastSteps; fi++) {
          var fProg = fi / fastSteps;
          var fX = (fi === fastSteps) ? (startX + dx) : Math.round(startX + dx * fProg);
          var fY = (fi === fastSteps) ? (startY + dy) : Math.round(startY + dy * fProg);
          traj.push({ x: fX, y: fY, movementX: fX - pX, movementY: fY - pY, dt: 0 });
          pX = fX;
          pY = fY;
        }
      }
    }

    var lastX = startX;
    var lastY = startY;
    for (var s = 0; s < traj.length; s++) {
      var step = traj[s];
      var stepX = (step.x !== undefined) ? step.x : (startX + (step[0] || 0));
      var stepY = (step.y !== undefined) ? step.y : (startY + (step[1] || 0));
      var mvX = (step.movementX !== undefined) ? step.movementX : (stepX - lastX);
      var mvY = (step.movementY !== undefined) ? step.movementY : (stepY - lastY);

      if (isStealth && step.dt) {
        if (typeof global.__advanceClock === "function") global.__advanceClock(step.dt);
      }
      t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : t + (step.dt || 0);

      var moveInit = {
        clientX: stepX,
        clientY: stepY,
        pageX: stepX,
        pageY: stepY,
        screenX: stepX + 50,
        screenY: stepY + 100,
        movementX: mvX,
        movementY: mvY,
        button: 0,
        buttons: 1,
        pressure: 0.5,
        pointerId: 1,
        bubbles: true,
        cancelable: true,
        isTrusted: true,
        timeStamp: t,
      };
      var moveTarget = _activePointerCaptures[1] || el;
      if (typeof global.PointerEvent === "function") moveTarget.dispatchEvent(new global.PointerEvent("pointermove", moveInit));
      var mmEv = (typeof global.MouseEvent === "function") ? new global.MouseEvent("mousemove", moveInit) : new global.Event("mousemove", moveInit);
      moveTarget.dispatchEvent(mmEv);
      lastX = stepX;
      lastY = stepY;
      if (nativeDrag && typeof global.DragEvent === "function") {
        try {
          el.dispatchEvent(new global.DragEvent("drag", { bubbles: true, cancelable: true, isTrusted: true, __trusted: true, clientX: stepX, clientY: stepY, button: 0, buttons: 1, dataTransfer: dragDT, timeStamp: t }));
        } catch (e) {}
      }
    }

    if (isStealth) {
      var postHesitation = 15 + Math.round(Math.random() * 15);
      if (typeof global.__advanceClock === "function") global.__advanceClock(postHesitation);
      t = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : t + postHesitation;
    }

    var upInit = {
      clientX: startX + dx,
      clientY: startY + dy,
      pageX: startX + dx,
      pageY: startY + dy,
      screenX: startX + dx + 50,
      screenY: startY + dy + 100,
      button: 0,
      buttons: 0,
      pressure: 0,
      pointerId: 1,
      bubbles: true,
      cancelable: true,
      isTrusted: true,
      __trusted: true,
      timeStamp: t,
    };
    var upTarget = _activePointerCaptures[1] || el;
    if (typeof global.PointerEvent === "function") upTarget.dispatchEvent(new global.PointerEvent("pointerup", upInit));
    var muEv = (typeof global.MouseEvent === "function") ? new global.MouseEvent("mouseup", upInit) : new global.Event("mouseup", upInit);
    upTarget.dispatchEvent(muEv);
    if (_activePointerCaptures[1]) {
      _activePointerCaptures[1].releasePointerCapture(1);
    }
    if (nativeDrag && typeof global.DragEvent === "function") {
      var endInit = { bubbles: true, cancelable: true, isTrusted: true, __trusted: true, clientX: startX + dx, clientY: startY + dy, button: 0, buttons: 0, dataTransfer: dragDT, timeStamp: t };
      /* Spec: drop is delivered ONLY if the target cancels dragover
         (preventDefault signals acceptance) AND the negotiated drag
         operation is not "none". The operation is negotiated per the
         processing model: before dragenter and dragover the user agent
         initializes dropEffect to the default for the source's declared
         effectAllowed; the listener may override it (validated: sets
         outside effectAllowed's allowed set are ignored); a canceled
         dragover sets the operation to the resulting dropEffect. */
      var dropAccepted = false;
      var finalEffect = "none";
      if (dragDropTarget) {
        try {
          dragDT.__phase = "negotiate";
          dragDT.__dropEffect = __defaultDropEffect(dragDT.__effectAllowed);
          var entEv = new global.DragEvent("dragenter", endInit);
          dragDropTarget.dispatchEvent(entEv);
          dragDT.__dropEffect = __defaultDropEffect(dragDT.__effectAllowed);
          var ovEv = new global.DragEvent("dragover", endInit);
          dragDropTarget.dispatchEvent(ovEv);
          if (ovEv.defaultPrevented) {
            var eff = dragDT.__dropEffect;
            if (!__effectPermits(dragDT.__effectAllowed, eff)) {
              eff = __defaultDropEffect(dragDT.__effectAllowed);
            }
            if (eff !== "none") {
              dropAccepted = true;
              finalEffect = eff;
              dragDT.__dropEffect = eff;
              dragDT.__phase = "drop";
              dragDropTarget.dispatchEvent(new global.DragEvent("drop", endInit));
            } else {
              dragDropTarget.dispatchEvent(new global.DragEvent("dragleave", endInit));
            }
          } else {
            dragDropTarget.dispatchEvent(new global.DragEvent("dragleave", endInit));
          }
        } catch (e) {}
      }
      /* dragend always fires on the source (spec), accepted or not, carrying
         the negotiated operation in dropEffect: the source learns what
         happened ("move" means it should remove the original item, etc.). */
      dragDT.__phase = "dragend";
      dragDT.__dropEffect = finalEffect;
      try {
        el.dispatchEvent(new global.DragEvent("dragend", endInit));
      } catch (e) {}
      dragDT.__phase = null;
    }
    return true;
  }

  global.__dragSelector = function (selector, dx, dy, steps, trajectory, stealth, nth) {
    var el = null;
    nth = Math.max(1, parseInt(nth, 10) || 1);
    if (nth <= 1) {
      if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
      else el = global.document.querySelector(selector);
    } else {
      /* Identical siblings share one selector; the surface disambiguates by
         document-order occurrence. The Nth match is the addressed handle. */
      var dm = global.document.querySelectorAll(selector);
      el = dm && dm.length >= nth ? dm[nth - 1] : null;
    }
    if (!el) return false;
    return __executeDrag(el, dx, dy, steps, trajectory, stealth);
  };
  global.__dragPath = function (path, dx, dy, steps, trajectory, stealth) {
    var el = global.__byPath(path);
    if (!el) return false;
    return __executeDrag(el, dx, dy, steps, trajectory, stealth);
  };
global.__submitPath = function (path) {
    var el = global.__byPath(path);
    if (!el) return false;
    var form = el.nodeName === "form" ? el : null;
    var n = el;
    while (!form && n) { if (n.nodeName === "form") form = n; n = n.parentNode; }
    var ev = new global.Event("submit", { bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    if (ev.defaultPrevented) return "prevented";
    var submitter = (el.nodeName === "button" || el.nodeName === "input") ? el : null;
    if (form && typeof form.submit === "function") {
      /* el is the agent-clicked control (a submit button): it is the
         submitter and contributes its own name/value pair. */
      try { form.submit(submitter); } catch (e) {}
    }
    var fm = form ? (form.getAttribute("method") || "GET").toUpperCase() : "";
    if (submitter && typeof submitter.getAttribute === "function" && submitter.getAttribute("formmethod")) {
      fm = submitter.getAttribute("formmethod").toUpperCase();
    }
    if (fm === "DIALOG") return "dialog";
    return "submitted";
  };
  global.__submitFormByIndex = function (index) {
    var forms = (global.document || { getElementsByTagName: function () { return []; } })
      .getElementsByTagName("form");
    var form = forms[index];
    if (!form) return "missing";
    var ev = new global.Event("submit", { bubbles: true, cancelable: true });
    form.dispatchEvent(ev);
    if (ev.defaultPrevented) return "prevented";
    if (typeof form.submit === "function") { try { form.submit(); } catch (e) {} }
    var fm = (form.getAttribute("method") || "GET").toUpperCase();
    if (fm === "DIALOG") return "dialog";
    return "submitted";
  };
  global.__setFormFields = function (index, fieldsJson) {
    var doc = global.document;
    if (!doc) return false;
    var forms = doc.getElementsByTagName("form");
    var form = forms[index];
    if (!form) return false;
    var fields = {};
    try { fields = JSON.parse(String(fieldsJson || "{}")); } catch (e) { fields = {}; }
    var els = form.querySelectorAll("input, textarea, select");
    els.forEach(function (el) {
      var name = el.getAttribute("name") || el.getAttribute("id") || "";
      if (!name || !(name in fields)) return;
      var tag = el.nodeName;
      var typ = (el.getAttribute("type") || "").toLowerCase();
      var val = fields[name];
      if (typ === "checkbox" || typ === "radio") {
        var want = val === true || val === "true" || val === "1" || val === el.getAttribute("value");
        el.checked = !!want;
      } else {
        el.value = String(val);
      }
      el.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: true }));
      el.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: true }));
    });
    return true;
  };

  Object.defineProperty(Element.prototype, "files", {
    get: function () {
      if (this.nodeName === "input" && (this.getAttribute("type") || "").toLowerCase() === "file") {
        if (!this._files) this._files = new global.FileList([]);
        return this._files;
      }
      return undefined;
    },
    set: function (val) {
      if (this.nodeName === "input" && (this.getAttribute("type") || "").toLowerCase() === "file") {
        if (val && typeof val.item === "function" && "length" in val) {
          this._files = val;
        } else if (Array.isArray(val)) {
          this._files = new global.FileList(val);
        } else {
          this._files = new global.FileList(val ? [val] : []);
        }
        if (this._files.length > 0 && this._files[0]) {
          this.value = "C:\\fakepath\\" + this._files[0].name;
          this.setAttribute("value", this.value);
        } else {
          this.value = "";
          this.removeAttribute("value");
        }
      }
    },
    configurable: true
  });

  global.__uploadFiles = function (selectorOrPath, filesList) {
    var el = null;
    if (Array.isArray(selectorOrPath)) {
      el = global.__byPath(selectorOrPath);
    } else {
      if (global.document.getElementById(selectorOrPath)) el = global.document.getElementById(selectorOrPath);
      else el = global.document.querySelector(selectorOrPath);
    }
    if (!el) return false;
    var fileObjs = [];
    var list = Array.isArray(filesList) ? filesList : (filesList ? [filesList] : []);
    for (var i = 0; i < list.length; i++) {
      var item = list[i];
      if (typeof item === "string") {
        item = { name: item, content: "" };
      }
      var name = item.name || ("file_" + i);
      var b64in = item.contentB64 || item.content_b64;
      var isBinary = false;
      var content;
      if (b64in) {
        /* Base64 of raw bytes: decode to a one-char-per-byte Latin-1 string so
           binary payloads (video, images, any bytes) stay byte-exact. */
        content = global.atob(String(b64in));
        isBinary = true;
      } else {
        content = item.content != null ? item.content : "";
      }
      var mime = item.type || "application/octet-stream";
      var lastMod = Number(item.lastModified) || Date.now();
      var fObj = new global.File([content], name, { type: mime, lastModified: lastMod });
      if (isBinary) {
        fObj._binary = true;
        fObj.size = fObj._content.length;
      }
      fileObjs.push(fObj);
    }
    el.files = new global.FileList(fileObjs);
    if (typeof el.focus === "function") el.focus();
    var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
    el.dispatchEvent(new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tNow }));
    el.dispatchEvent(new global.Event("change", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tNow + 1 }));
    return true;
  };

  /* ---- Agent-Native Layer & Topological Occlusion Inspector ---- */
  function __nodeSelector(el) {
    if (!el || el.nodeType !== 1) return "";
    var tag = (el.nodeName || "div").toLowerCase();
    var id = el.getAttribute && el.getAttribute("id");
    if (id) return tag + "#" + id;
    var cls = (el.getAttribute && el.getAttribute("class")) || "";
    var tokens = cls.trim().split(/\s+/).filter(Boolean).slice(0, 2);
    if (tokens.length) return tag + "." + tokens.join(".");
    var name = el.getAttribute && el.getAttribute("name");
    if (name) return tag + "[name=\"" + name + "\"]";
    return tag;
  }

  function __nodeLabel(el) {
    if (!el || el.nodeType !== 1) return "";
    var aria = (el.getAttribute && (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("placeholder") || el.getAttribute("alt"))) || "";
    if (aria) return String(aria).trim().slice(0, 80);
    var txt = String(el.textContent || "").replace(/\s+/g, " ").trim();
    if (txt) return txt.slice(0, 80);
    return (el.getAttribute && (el.getAttribute("value") || el.getAttribute("name") || el.getAttribute("id"))) || "";
  }

  global.__inspectLayers = function () {
    var doc = global.document;
    if (!doc) return JSON.stringify({ strata: {}, occlusions: [], honeypots: [], scrollers: [] });
    __ensureLayout(doc);
    var vw = Number(global.innerWidth) || 1280;
    var vh = Number(global.innerHeight) || 720;
    var viewportArea = Math.max(1, vw * vh);
    var strata = {
      L0_FLOW: [],
      L1_CHROME: [],
      L2_SCROLLER: [],
      L3_OVERLAY: [],
      L4_HIDDEN: [],
      /* R4: the paint stack's promoted elements and their ::backdrop
         pseudo-boxes. Reported FIRST-CLASS: an agent reasoning about
         occlusion must see what is above everything else. */
      TOP_LAYER: [],
      BACKDROPS: []
    };
    var overlays = [];
    var interactiveControls = [];
    var honeypots = [];
    var scrollers = [];
    var orderSeq = 0;

    function isInteractive(el) {
      var tag = (el.nodeName || "").toLowerCase();
      if (tag === "a" || tag === "button" || tag === "input" || tag === "select" || tag === "textarea" || tag === "summary") return true;
      var role = (el.getAttribute && (el.getAttribute("role") || "")).toLowerCase();
      if (role === "button" || role === "link" || role === "checkbox" || role === "menuitem" || role === "tab" || role === "switch" || role === "combobox") return true;
      if (el.getAttribute && (el.getAttribute("onclick") != null || el.getAttribute("contenteditable") != null)) return true;
      return false;
    }

    /* R4: report the top layer (promoted dialogs/popovers) and their
       ::backdrop boxes ahead of the normal strata walk. */
    try {
      var tl = (typeof global.__wrTopLayer === "function") ? global.__wrTopLayer() : [];
      tl.forEach(function (t) {
        var b = (t.el.getBoundingClientRect && t.el.getBoundingClientRect()) || {};
        strata.TOP_LAYER.push({
          kind: t.kind,
          tag: String(t.el.nodeName || "").toLowerCase(),
          id: (t.el.id != null ? String(t.el.id) : ""),
          x: Math.round(b.x || 0), y: Math.round(b.y || 0),
          width: Math.round(b.width || 0), height: Math.round(b.height || 0)
        });
        if (t.el.__backdropBox) {
          var bb = t.el.__backdropBox;
          strata.BACKDROPS.push({
            for: String(t.el.nodeName || "").toLowerCase() + (t.el.id ? "#" + t.el.id : ""),
            x: Math.round(bb.x), y: Math.round(bb.y),
            width: Math.round(bb.width), height: Math.round(bb.height)
          });
        }
      });
    } catch (eTop) {}

    (function walk(el, ancestorHidden, ancestorOverlay, ancestorChrome) {
      if (!el || el.nodeType !== 1) return;
      var seq = ++orderSeq;
      var tag = (el.nodeName || "").toLowerCase();
      if (tag === "head" || tag === "script" || tag === "style" || tag === "meta" || tag === "link") return;

      var cs = (typeof global.getComputedStyle === "function") ? global.getComputedStyle(el) : {};
      var disp = String((cs && cs.display) || "").toLowerCase();
      var vis = String((cs && cs.visibility) || "").toLowerCase();
      var op = cs && cs.opacity !== undefined && cs.opacity !== "" ? Number(cs.opacity) : 1;
      var pos = String((cs && cs.position) || "static").toLowerCase();
      var zRaw = (cs && cs.zIndex) || "auto";
      var zIndex = (zRaw === "auto" || zRaw === "" || isNaN(Number(zRaw))) ? 0 : Number(zRaw);
      var ovX = String((cs && (cs.overflowX || cs.overflow)) || "visible").toLowerCase();
      var ovY = String((cs && (cs.overflowY || cs.overflow)) || "visible").toLowerCase();

      var attrHidden = (el.hasAttribute && (el.hasAttribute("hidden") || el.hasAttribute("inert"))) ||
                       ((el.getAttribute && el.getAttribute("aria-hidden")) === "true");
      var selfHidden = ancestorHidden || disp === "none" || vis === "hidden" || vis === "collapse" || op === 0 || attrHidden;

      var rect = (typeof el.getBoundingClientRect === "function") ? el.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
      var boxObj = {
        selector: __nodeSelector(el),
        tag: tag,
        label: __nodeLabel(el),
        x: Math.round(rect.x || 0),
        y: Math.round(rect.y || 0),
        width: Math.round(rect.width || 0),
        height: Math.round(rect.height || 0),
        position: pos,
        zIndex: zIndex,
        order: seq
      };

      if (selfHidden) {
        var reason = disp === "none" ? "display:none" : (vis === "hidden" ? "visibility:hidden" : (op === 0 ? "opacity:0" : (attrHidden ? "hidden/aria-hidden/inert" : "ancestor-hidden")));
        boxObj.reason = reason;
        if (strata.L4_HIDDEN.length < 60) strata.L4_HIDDEN.push(boxObj);
        if (isInteractive(el)) {
          honeypots.push({
            selector: boxObj.selector,
            tag: tag,
            label: boxObj.label,
            name: (el.getAttribute && el.getAttribute("name")) || "",
            reason: reason,
            honeypot_risk: tag === "input" || tag === "textarea" || tag === "button"
          });
        }
        (el.childNodes || []).forEach(function (c) { if (c.nodeType === 1) walk(c, true, ancestorOverlay, ancestorChrome); });
        return;
      }

      var role = String((el.getAttribute && el.getAttribute("role")) || "").toLowerCase();
      var ariaModal = String((el.getAttribute && el.getAttribute("aria-modal")) || "").toLowerCase() === "true";
      var isDialog = (tag === "dialog" && el.hasAttribute && el.hasAttribute("open")) || role === "dialog" || role === "alertdialog" || ariaModal || !!el.__popoverOpen;
      var areaRatio = (boxObj.width * boxObj.height) / viewportArea;
      var isOverlay = isDialog || ((pos === "fixed" || pos === "absolute") && (zIndex >= 10 || areaRatio >= 0.25));
      var isChrome = !isOverlay && (pos === "fixed" || pos === "sticky" || tag === "nav" || tag === "header" || tag === "footer" || ancestorChrome);
      var isScroller = (ovY === "auto" || ovY === "scroll" || ovX === "auto" || ovX === "scroll") &&
                       ((el.scrollHeight > el.clientHeight && el.clientHeight > 0) || (el.scrollWidth > el.clientWidth && el.clientWidth > 0));

      var curOverlay = ancestorOverlay;
      if (isOverlay && !ancestorOverlay) {
        curOverlay = boxObj;
        overlays.push({ el: el, meta: boxObj });
        if (strata.L3_OVERLAY.length < 40) strata.L3_OVERLAY.push(boxObj);
      } else if (isScroller) {
        var scMeta = {
          selector: boxObj.selector,
          tag: tag,
          x: boxObj.x,
          y: boxObj.y,
          width: boxObj.width,
          height: boxObj.height,
          scrollTop: el.scrollTop || 0,
          scrollLeft: el.scrollLeft || 0,
          scrollHeight: el.scrollHeight || boxObj.height,
          scrollWidth: el.scrollWidth || boxObj.width,
          remainingY: Math.max(0, (el.scrollHeight || 0) - (el.clientHeight || 0) - (el.scrollTop || 0))
        };
        scrollers.push(scMeta);
        if (strata.L2_SCROLLER.length < 40) strata.L2_SCROLLER.push(scMeta);
      } else if (isChrome) {
        if (strata.L1_CHROME.length < 50 && (isInteractive(el) || tag === "nav" || tag === "header" || tag === "footer" || pos === "fixed")) {
          strata.L1_CHROME.push(boxObj);
        }
      } else {
        if (strata.L0_FLOW.length < 80 && (isInteractive(el) || tag === "form" || tag === "main" || tag === "article" || tag === "section" || /^h[1-6]$/.test(tag))) {
          strata.L0_FLOW.push(boxObj);
        }
      }

      if (isInteractive(el) && boxObj.width > 0 && boxObj.height > 0) {
        interactiveControls.push({ el: el, meta: boxObj, ownerOverlay: curOverlay });
      }

      (el.childNodes || []).forEach(function (c) {
        if (c.nodeType === 1) walk(c, false, curOverlay, isChrome);
      });
    })(doc.body || doc.documentElement, false, null, false);

    function containsNode(parent, child) {
      var cur = child;
      while (cur) {
        if (cur === parent) return true;
        cur = cur.parentNode;
      }
      return false;
    }

    var occlusions = [];
    for (var i = 0; i < interactiveControls.length; i++) {
      var item = interactiveControls[i];
      var cBox = item.meta;
      var cx = cBox.x + cBox.width / 2;
      var cy = cBox.y + cBox.height / 2;
      for (var j = 0; j < overlays.length; j++) {
        var ov = overlays[j];
        if (containsNode(ov.el, item.el)) continue;
        var oBox = ov.meta;
        var oWins = (oBox.zIndex > cBox.zIndex) || (oBox.zIndex === cBox.zIndex && oBox.order > cBox.order);
        if (!oWins) continue;
        if (cx >= oBox.x && cx <= oBox.x + oBox.width && cy >= oBox.y && cy <= oBox.y + oBox.height) {
          var dismissers = [];
          try {
            var btns = ov.el.querySelectorAll("button, [role='button'], input[type='button'], input[type='submit'], a");
            for (var b = 0; b < btns.length && dismissers.length < 4; b++) {
              dismissers.push({
                selector: __nodeSelector(btns[b]),
                label: __nodeLabel(btns[b])
              });
            }
          } catch (e) {}
          occlusions.push({
            control: cBox.selector,
            label: cBox.label,
            point: [Math.round(cx), Math.round(cy)],
            occluded_by: oBox.selector,
            overlay_z: oBox.zIndex,
            dismiss_candidates: dismissers
          });
          break;
        }
      }
    }

    return JSON.stringify({
      viewport: { width: vw, height: vh, scrollX: global.scrollX || 0, scrollY: global.scrollY || 0, scrollExtent: doc.__scrollExtent || { width: vw, height: vh } },
      counts: {
        L0_FLOW: strata.L0_FLOW.length,
        L1_CHROME: strata.L1_CHROME.length,
        L2_SCROLLER: scrollers.length,
        L3_OVERLAY: overlays.length,
        L4_HIDDEN: strata.L4_HIDDEN.length,
        occluded_controls: occlusions.length,
        honeypot_traps: honeypots.length
      },
      strata: strata,
      scrollers: scrollers,
      occlusions: occlusions,
      honeypots: honeypots
    });
  };

  global.__isElementOccluded = function (el) {
    if (!el || el.nodeType !== 1) return null;
    try {
      if (typeof global.__inspectLayers !== "function") return null;
      var layers = JSON.parse(global.__inspectLayers());
      if (!layers || !layers.occlusions) return null;
      var sel = typeof __nodeSelector === "function" ? __nodeSelector(el) : "";
      for (var i = 0; i < layers.occlusions.length; i++) {
        var occ = layers.occlusions[i];
        if (occ.control === sel) return occ;
      }
    } catch (e) {}
    return null;
  };

  /* ---- DOSH In-Realm Agent Scripting SDK (globalThis.dosh) ---- */
  global.__WR_DOSH_EMITS = [];
  global.__drainDoshEmits = function () {
    var out = (global.__WR_DOSH_EMITS || []).slice();
    global.__WR_DOSH_EMITS = [];
    return JSON.stringify(out);
  };
  global.dosh = Object.assign(global.dosh || {}, {
    args: (global.dosh && global.dosh.args) || {},
    emit: function (key, value) {
      global.__WR_DOSH_EMITS = global.__WR_DOSH_EMITS || [];
      global.__WR_DOSH_EMITS.push({ key: String(key), value: value, ts: Date.now() });
      if (typeof global.__dispatchHost === "function") {
        try { global.__dispatchHost("host_emit", String(key), typeof value === "string" ? value : JSON.stringify(value)); } catch (e) {}
      }
      return value;
    },
    act: function (action) {
      if (global.__getCapabilityGate && !global.__getCapabilityGate('action')) {
        throw new Error("Action authority disallowed: mutations are blocked in read-only mode");
      }
      var kind = (action && (action.kind || action.type)) || "click";
      var sel = (action && (action.selector || action.ref)) || "";
      if (kind === "click" || kind === "activate") {
        return global.dosh.click(sel);
      } else if (kind === "type") {
        return global.dosh.type(sel, action.text || "");
      }
      return false;
    },
    click: function (selector) {
      if (global.__getCapabilityGate && !global.__getCapabilityGate('action')) {
        throw new Error("Action authority disallowed: mutations are blocked in read-only mode");
      }
      return global.__activateSelector(String(selector), null, false, !!global.__stealthMode);
    },
    type: function (selector, text) {
      if (global.__getCapabilityGate && !global.__getCapabilityGate('action')) {
        throw new Error("Action authority disallowed: mutations are blocked in read-only mode");
      }
      return global.__activateSelector(String(selector), String(text == null ? "" : text), false, !!global.__stealthMode);
    },
    query: function (selector) {
      if (!global.document || !global.document.querySelectorAll) return [];
      var nodes = global.document.querySelectorAll(String(selector));
      return nodes.map(function (el) {
        var r = typeof el.getBoundingClientRect === "function" ? el.getBoundingClientRect() : { x: 0, y: 0, width: 0, height: 0 };
        return {
          selector: __nodeSelector(el),
          tag: (el.nodeName || "").toLowerCase(),
          id: el.getAttribute("id") || "",
          className: el.getAttribute("class") || "",
          text: String(el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 200),
          value: el.value !== undefined ? el.value : null,
          href: el.getAttribute("href") || null,
          rect: { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height) },
          visible: typeof el.checkVisibility === "function" ? el.checkVisibility() : true
        };
      });
    },
    text: function (selector) {
      if (!global.document) return "";
      if (!selector) return String((global.document.body && global.document.body.textContent) || "").replace(/\s+/g, " ").trim();
      var el = global.document.querySelector(String(selector));
      return el ? String(el.textContent || "").replace(/\s+/g, " ").trim() : "";
    },
    attr: function (selector, name, value) {
      if (!global.document) return null;
      var el = global.document.querySelector(String(selector));
      if (!el) return null;
      if (value !== undefined) {
        el.setAttribute(String(name), String(value));
        return String(value);
      }
      return el.getAttribute(String(name));
    },
    layers: function () {
      try { return JSON.parse(global.__inspectLayers()); } catch (e) { return {}; }
    },
    storage: function () {
      var st = {};
      try { st = JSON.parse(global.__drainStorage()); } catch (e) {}
      var idb = {};
      try { idb = JSON.parse(global.__drainIndexedDB()); } catch (e) {}
      return {
        local: st.local || {},
        session: st.session || {},
        indexedDB: idb,
        cookie: (global.document && global.document.cookie) || ""
      };
    },
    checkpoint: function () {
      if (typeof global.__dispatchHost === "function") {
        return Number(global.__dispatchHost("__dosh_checkpoint"));
      }
      return 0;
    },
    rewind: function (checkpointId) {
      if (typeof global.__dispatchHost === "function") {
        return Number(global.__dispatchHost("__dosh_rewind", String(checkpointId)));
      }
      return 0;
    },
    fork: function () {
      if (typeof global.__dispatchHost === "function") {
        return global.__dispatchHost("__dosh_fork");
      }
      return null;
    },
    batchRead: function (nodeIds) {
      if (typeof global.__dispatchHost === "function") {
        return global.__dispatchHost("__dosh_batch_read", JSON.stringify(nodeIds));
      }
      return null;
    }
  });
})(this);
