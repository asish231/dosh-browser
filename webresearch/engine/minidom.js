/* webresearch mini-DOM — ES subset for QuickJS. Not Chromium. */
(function (global) {
  var _NativePromise = global.Promise;
  var _nativePromiseResolve = _NativePromise ? _NativePromise.resolve.bind(_NativePromise) : null;
  /* ECMAScript Integer-Indexed Exotic Objects [[Set]]: out-of-bounds writes are no-ops returning true */
  (function () {
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

  Node.prototype.appendChild = function (child) {
    if (!child) return child;
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    queueMutation("childList", this, { addedNodes: [child] });
    _notifyConnected(child);
    /* Runtime-injected scripts (document.createElement('script')) can't be seen
       by the static extractor. Push them onto a queue that the host drains after
       each pump pass so LinkedIn-style dynamically-loaded ESM bundles execute. */
    if (child && (child.nodeName === "SCRIPT" || (child.nodeName === "#document-fragment" && child.querySelectorAll))) {
      var scripts = [];
      if (child.nodeName === "SCRIPT") scripts = [child];
      else scripts = child.querySelectorAll("script");
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
    if (!ref) return this.appendChild(child);
    if (child.parentNode) child.parentNode.removeChild(child);
    var i = this.childNodes.indexOf(ref);
    if (i < 0) return this.appendChild(child);
    child.parentNode = this;
    this.childNodes.splice(i, 0, child);
    queueMutation("childList", this, { addedNodes: [child] });
    _notifyConnected(child);
    return child;
  };
  Node.prototype.replaceChild = function (neu, old) {
    this.insertBefore(neu, old);
    this.removeChild(old);
    return old;
  };
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

  function CSSStyleDeclaration(el) {
    this._el = el;
  }
  CSSStyleDeclaration.prototype.setProperty = function (prop, val, priority) {
    var camel = String(prop).replace(/-([a-z])/g, function (_, c) { return c.toUpperCase(); });
    this[camel] = String(val);
    this[prop] = String(val);
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
    return old;
  };
  global.CSSStyleDeclaration = CSSStyleDeclaration;

  function Element(tag, attrs, kids) {
    Node.call(this, 1, tag, attrs || {}, kids || []);
    this.style = new CSSStyleDeclaration(this);
    if (this.attributes.style) {
      var sParts = String(this.attributes.style).split(";");
      for (var sIdx = 0; sIdx < sParts.length; sIdx++) {
        var sP = sParts[sIdx].trim();
        if (!sP) continue;
        var sCol = sP.indexOf(":");
        if (sCol > 0) this.style.setProperty(sP.slice(0, sCol).trim(), sP.slice(sCol + 1).trim());
      }
    }
    this.dataset = {};
    if (this.nodeName === "canvas") _attachCanvas(this);
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
    if (typeof this.attributeChangedCallback === "function") {
      var obs = (this.constructor && this.constructor.observedAttributes) || this.observedAttributes;
      if (Array.isArray(obs) && obs.indexOf(n) >= 0) {
        try { this.attributeChangedCallback(n, oldVal, String(v)); } catch (e) {}
      }
    }
  };
  Element.prototype.removeAttribute = function (n) {
    delete this.attributes[n];
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
        try { list[i].call(node, ev); } catch (e) {}
      }
      if (typeof node["on" + type] === "function") {
        try { node["on" + type](ev); } catch (e) {}
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
    if (this.disabled && (tag === "input" || tag === "button" || tag === "select"
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
            try { form.submit(); } catch (e) {}
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
  Element.prototype.submit = function () {
    if (this.nodeName !== "form") return;
    var action = this.getAttribute("action") || (global.location ? global.location.href : "");
    var method = (this.getAttribute("method") || "GET").toUpperCase();
    var enctype = (this.getAttribute("enctype") || "application/x-www-form-urlencoded").toLowerCase();
    var fields = {};
    var formFiles = {};
    var inputs = this.querySelectorAll("input, textarea, select");
    for (var i = 0; i < inputs.length; i++) {
      var inp = inputs[i];
      var name = inp.getAttribute("name");
      if (!name) continue;
      var type = (inp.getAttribute("type") || "").toLowerCase();
      if ((type === "checkbox" || type === "radio") && !inp.checked) continue;
      if (type === "checkbox" || type === "radio") {
        /* HTML spec: the default value of checkbox/radio is "on". */
        fields[name] = inp.getAttribute("value") != null ? inp.getAttribute("value") : "on";
        continue;
      }
      if (inp.nodeName === "select" && inp.getAttribute("multiple") != null) {
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
      fields[name] = inp.value != null ? inp.value : (inp.getAttribute("value") || "");
    }
    if (method === "GET") {
      try {
        var base = (global.location && global.location.href) ? global.location.href : "about:blank";
        var u = new URL(action, base);
        for (var k in fields) {
          u.searchParams.set(k, fields[k]);
        }
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
    var ev = (typeof global.Event === "function")
      ? new global.Event("submit", { bubbles: true, cancelable: true, isTrusted: true })
      : { type: "submit", target: this, bubbles: true, cancelable: true, isTrusted: true };
    if (submitter) ev.submitter = submitter;
    this.dispatchEvent(ev);
    if (!ev.defaultPrevented) {
      this.submit();
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
        else if (!this.__selectDeselected && opts.length) picked = opts[0];
        return picked ? __optionValue(picked) : "";
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
      return opts.length ? 0 : -1;
    },
    set: function (i) {
      if (this.nodeName !== "select") return;
      var n = Math.floor(Number(i));
      if (isNaN(n) || n < 0) {
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
           explicitly chosen (HTML spec); a chosen sibling wins. */
        if (__selectAttrIndex(sel) >= 0) return false;
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
  Object.defineProperty(Element.prototype, "checked", {
    get: function () { return this.attributes.checked != null && this.attributes.checked !== "false"; },
    set: function (v) {
      if (v) this.attributes.checked = "checked"; else delete this.attributes.checked;
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
   * vertically with margin collapsing, inline text wraps by measured advance.
   * Text uses a monospace advance metric (0.6em/char) since no font engine
   * ships here; replaced elements default to the CSS-spec 300x150 box unless
   * sized. Viewport width comes from window.innerWidth. This is a simplified
   * but real engine: every number derives from markup + styles, never hashed.
   * Known simplifications: no floats/positioning/flex/grid, px + % + em only,
   * single font metric, no bidi/shaping. Reported via __layoutInfo. */
  var __BLOCK_TAGS = { address:1, article:1, aside:1, blockquote:1, body:1, br:0, canvas:0, dd:1, div:1, dl:1, dt:1, fieldset:1, figcaption:1, figure:1, footer:1, form:1, h1:1, h2:1, h3:1, h4:1, h5:1, h6:1, header:1, hgroup:1, hr:1, html:1, iframe:0, img:0, input:0, legend:1, li:1, main:1, nav:1, noscript:1, ol:1, option:0, output:0, p:1, pre:1, section:1, select:0, table:1, tbody:1, td:1, textarea:0, tfoot:1, th:1, thead:1, tr:1, ul:1, video:0, button:0 };
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
  function __matchesSel(el, sel) {
    sel = sel.trim().toLowerCase();
    if (!sel || sel === "*") return true;
    var tag = null, id = null, cls = null;
    var m = sel.match(/^([a-z][a-z0-9]*)?(#[a-z0-9\-_]+)?((?:\.[a-z0-9\-_]+)*)$/);
    if (!m) return false;
    tag = m[1] || null; id = m[2] ? m[2].slice(1) : null;
    var classes = m[3] ? m[3].split(".").filter(Boolean) : [];
    if (tag && el.nodeName !== tag) return false;
    if (id && el.id !== id) return false;
    var have = (" " + (el.className || "") + " ").toLowerCase();
    for (var i = 0; i < classes.length; i++) {
      if (have.indexOf(" " + classes[i] + " ") < 0) return false;
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
    if (k === "font-size") return "fontSize";
    if (k === "flex-direction") return "flexDirection";
    if (k === "justify-content") return "justifyContent";
    if (k === "align-items") return "alignItems";
    return k;
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
      gap: null,
      width: null,
      height: null,
      margin: "0",
      padding: "0",
      fontSize: null,
      /* visibility participates in the cascade: checkVisibility() must see
         stylesheet visibility:hidden, which the old template dropped. */
      visibility: null,
      opacity: null
    };
    rules.forEach(function (r) {
      r.sel.split(",").forEach(function (s) {
        if (!__matchesSel(el, s)) return;
        var spec = __selSpecificity(s);
        for (var k in r.decls) {
          var prop = __normalizeProp(k);
          if (!(prop in st)) return;
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
    var fontSize = parentFont || 16;
    if (st.fontSize != null) {
      var fs = __parsePx(st.fontSize, null, parentFont || 16);
      if (fs != null && fs > 0) fontSize = fs;
    }
    var disp = (st.display || "").toLowerCase();
    if (!disp) disp = (__BLOCK_TAGS[el.nodeName] ? "block" : "inline");
    var pos = (st.position || "").toLowerCase();
    var flexDir = (st.flexDirection || "row").toLowerCase();
    return {
      display: disp,
      position: pos,
      top: st.top,
      left: st.left,
      bottom: st.bottom,
      right: st.right,
      flexDirection: flexDir,
      gap: st.gap,
      width: st.width,
      height: st.height,
      margin: st.margin,
      padding: st.padding,
      fontSize: fontSize,
      visibility: st.visibility,
      opacity: st.opacity
    };
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

  function __advance(ch, fontSize) {
    if (Object.prototype.hasOwnProperty.call(__CHAR_ADVANCES, ch)) {
      return __CHAR_ADVANCES[ch] * fontSize;
    }
    var code = ch.charCodeAt(0);
    if (code >= 0x1100 && (code <= 0x115F || (code >= 0x2E80 && code <= 0x9FFF) || (code >= 0xAC00 && code <= 0xD7A3) || (code >= 0xF900 && code <= 0xFAFF))) return fontSize;
    return 0.55 * fontSize;
  }
  function __textWidth(s, fontSize) {
    var w = 0;
    for (var i = 0; i < s.length; i++) w += __advance(s[i], fontSize);
    return w;
  }
  var __NON_RENDERED = { head:1, style:1, script:1, title:1, meta:1, link:1, template:1, noscript:0 };
  function __ensureLayout(doc) {
    var vw = (typeof global.innerWidth === "number" && global.innerWidth > 0) ? global.innerWidth : 1280;
    if (doc.__layoutCache && doc.__layoutCache.vw === vw && doc.__layoutCache.v === doc.__layoutVersion) return doc.__layoutCache;
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
          return c.nodeType === 1 && !__NON_RENDERED[c.nodeName];
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
            c.__box = { x: 0, y: 0, width: 0, height: 0 };
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
        var curX = x;
        var curY = y;
        var maxRowH = 0;
        var totalColW = 0;
        var isCol = (cs.flexDirection === "column");
        var children = (el.childNodes || []).filter(function (c) {
          return c.nodeType === 1 && !__NON_RENDERED[c.nodeName];
        });
        children.forEach(function (c, idx) {
          var ccs = __computedStyle(c, rules, cs.fontSize);
          if (ccs.display === "none") {
            c.__box = { x: 0, y: 0, width: 0, height: 0 };
            layout.boxes++;
            return;
          }
          if (ccs.position === "absolute" || ccs.position === "fixed") {
            layoutAbsolute(c, ccs, x, y, contentW, 0);
            return;
          }
          var margin = __parseSides(ccs.margin, contentW, ccs.fontSize);
          var padding = __parseSides(ccs.padding, contentW, ccs.fontSize);
          var w = __parsePx(ccs.width, contentW, ccs.fontSize);
          var h = __parsePx(ccs.height, null, ccs.fontSize);
          if (isCol) {
            if (w == null) w = Math.max(0, contentW - margin[1] - margin[3]);
            var itemContentW = Math.max(0, w - padding[1] - padding[3]);
            var r = layoutChildren(c, ccs, x + margin[3] + padding[3], curY + margin[0] + padding[0], itemContentW);
            var itemH = (h == null) ? Math.max(0, r.bottom - (curY + margin[0] + padding[0])) : h;
            var boxH = padding[0] + itemH + padding[2];
            c.__box = { x: x + margin[3], y: curY + margin[0], width: w, height: boxH };
            layout.boxes++;
            curY += margin[0] + boxH + margin[2] + (idx < children.length - 1 ? gap : 0);
            if (w + margin[1] + margin[3] > totalColW) totalColW = w + margin[1] + margin[3];
          } else {
            if (w == null) {
              if (__REPLACED_TAGS[c.nodeName]) {
                w = 100;
              } else {
                var tw = __textWidth(c.textContent || "", ccs.fontSize);
                w = Math.max(30, Math.min(contentW, tw + padding[1] + padding[3] + 20));
              }
            }
            var itemContentW = Math.max(0, w - padding[1] - padding[3]);
            var r = layoutChildren(c, ccs, curX + margin[3] + padding[3], y + margin[0] + padding[0], itemContentW);
            var itemH = (h == null) ? Math.max(0, r.bottom - (y + margin[0] + padding[0])) : h;
            var boxH = padding[0] + itemH + padding[2];
            c.__box = { x: curX + margin[3], y: y + margin[0], width: w, height: boxH };
            layout.boxes++;
            var fullItemW = margin[3] + w + margin[1];
            curX += fullItemW + (idx < children.length - 1 ? gap : 0);
            if (boxH + margin[0] + margin[2] > maxRowH) maxRowH = boxH + margin[0] + margin[2];
          }
        });
        return { bottom: isCol ? curY : (y + maxRowH), maxW: isCol ? totalColW : (curX - x) };
      }
      var cursor = y;
      var line = null;
      var maxW = 0;
      function flushLine() {
        if (!line) return;
        var h = 0;
        line.runs.forEach(function (r) { if (r.h > h) h = r.h; });
        if (h <= 0) h = cs.fontSize * 1.2;
        line.runs.forEach(function (r) {
          r.el.__box = { x: r.x, y: cursor, width: r.w, height: h };
          layout.boxes++;
        });
        cursor += h;
        if (line.w > maxW) maxW = line.w;
        line = null;
      }
      function pushRun(targetEl, text, fontSize) {
        var words = String(text).split(/(\s+)/);
        words.forEach(function (word) {
          if (word === "") return;
          if (/^\s+$/.test(word)) {
            var sp = __textWidth(" ", fontSize);
            if (!line) line = { x: x, w: 0, runs: [] };
            if (line.w + sp > contentW && line.w > 0) flushLine();
            if (!line) line = { x: x, w: 0, runs: [] };
            line.runs.push({ el: targetEl, x: x + line.w, w: sp, h: fontSize * 1.2 });
            line.w += sp;
            return;
          }
          var ww = __textWidth(word, fontSize);
          if (!line) line = { x: x, w: 0, runs: [] };
          if (line.w + ww > contentW && line.w > 0) flushLine();
          if (!line) line = { x: x, w: 0, runs: [] };
          line.runs.push({ el: targetEl, x: x + line.w, w: ww, h: fontSize * 1.2 });
          line.w += ww;
        });
      }
      function flowInline(targetEl, node, fontSize) {
        (node.childNodes || []).forEach(function (c) {
          if (c.nodeType === 3) { pushRun(targetEl, __nodeText(c), fontSize); return; }
          if (c.nodeType !== 1) return;
          var ccs = __computedStyle(c, rules, fontSize);
          if (ccs.display === "none") { c.__box = { x: 0, y: 0, width: 0, height: 0 }; return; }
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
          flowInline(c, c, ccs.fontSize);
        });
      }
      (el.childNodes || []).forEach(function (c) {
        if (c.nodeType === 3) { pushRun(el, __nodeText(c), cs.fontSize); return; }
        if (c.nodeType !== 1) return;
        var ccs = __computedStyle(c, rules, cs.fontSize);
        if (ccs.display === "none") { c.__box = { x: 0, y: 0, width: 0, height: 0 }; layout.boxes++; return; }
        if (ccs.position === "absolute" || ccs.position === "fixed") {
          layoutAbsolute(c, ccs, x, cursor, contentW, 0);
          return;
        }
        if (isBlockDisplay(ccs)) {
          flushLine();
          var cb = layoutBlock(c, x, cursor, contentW, cs.fontSize);
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
        flowInline(c, c, ccs.fontSize);
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
    function layoutBlock(el, x, y, availW, parentFont) {
      if (__NON_RENDERED[el.nodeName]) { el.__box = { x: 0, y: 0, width: 0, height: 0 }; return { bottom: y, w: 0 }; }
      var cs = __computedStyle(el, rules, parentFont);
      if (cs.display === "none") { el.__box = { x: 0, y: 0, width: 0, height: 0 }; return { bottom: y, w: 0 }; }
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
      }
      layout.boxes++;
      return { bottom: by + totalH + margin[2], w: margin[3] + w + margin[1] };
    }
    var root = doc.documentElement || doc;
    var bodyMargin = 8;
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
      (function __walkExtent(n) {
        if (!n) return;
        if (n.__box) {
          var r = n.__box.x + n.__box.width;
          var b = n.__box.y + n.__box.height;
          if (r > __maxR) __maxR = r;
          if (b > __maxB) __maxB = b;
        }
        (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) __walkExtent(c); });
      })(root);
      doc.__scrollExtent = { width: __maxR, height: __maxB };
    } catch (e) {}
    try {
      doc.__layoutInfo = { engine: "block-flow+flex+grid+positioned", viewportWidth: vw, boxes: layout.boxes, textMetric: "monospace-advance-0.6em", replacedDefaults: "css-300x150" };
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
  /* scrollWidth/scrollHeight: for the documentElement these are the full
     document scroll extents (laid-out boxes may overflow the viewport);
     for any other element the box dims (per-element overflow is not
     tracked — disclosed approximation). */
  Object.defineProperty(Element.prototype, "scrollWidth", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) { __ensureLayout(doc); if (doc.documentElement === this && doc.__scrollExtent) return doc.__scrollExtent.width; }
      var b = this.__box || { width: 0 };
      return b.width;
    },
    configurable: true
  });
  Object.defineProperty(Element.prototype, "scrollHeight", {
    get: function () {
      var doc = this.ownerDocument || global.document;
      if (doc) { __ensureLayout(doc); if (doc.documentElement === this && doc.__scrollExtent) return doc.__scrollExtent.height; }
      var b = this.__box || { height: 0 };
      return b.height;
    },
    configurable: true
  });
  /* ---- scrollIntoView family ----
     Real scroll-to-element against the block-flow geometry: computes the
     target scroll position from the element's laid-out box, clamps to the
     document extent, and drives the same _doScroll lane as wheel scrolling
     (scroll + IntersectionObserver side effects included). */
  Element.prototype.scrollIntoView = function (arg) {
    var opts = (arg && typeof arg === "object") ? arg : {};
    var block = opts.block || (arg === false ? "end" : "start");
    var doc = this.ownerDocument || global.document;
    if (doc) __ensureLayout(doc);
    var b = this.__box || { x: 0, y: 0, width: 0, height: 0 };
    var vh = (typeof global.innerHeight === "number" && global.innerHeight > 0) ? global.innerHeight : 720;
    var cur = scrollState ? scrollState.y : 0;
    var target = cur;
    if (block === "start") target = b.y;
    else if (block === "center") target = b.y - (vh - b.height) / 2;
    else if (block === "end") target = b.y + b.height - vh;
    else if (block === "nearest") {
      var top = b.y, bottom = b.y + b.height;
      if (top >= cur && bottom <= cur + vh) return; // already fully visible
      target = (top < cur) ? top : Math.max(bottom - vh, top - (vh - b.height));
    }
    var clamped = _clampScroll(scrollState ? scrollState.x : 0, target);
    _doScroll(clamped[0], clamped[1]);
    return undefined;
  };
  Element.prototype.scrollIntoViewIfNeeded = function (arg) {
    var doc = this.ownerDocument || global.document;
    if (doc) __ensureLayout(doc);
    var b = this.__box || { x: 0, y: 0, width: 0, height: 0 };
    var vh = (typeof global.innerHeight === "number" && global.innerHeight > 0) ? global.innerHeight : 720;
    var cur = scrollState ? scrollState.y : 0;
    if (b.y >= cur && b.y + b.height <= cur + vh) return; // visible: no-op
    this.scrollIntoView(arg && typeof arg === "object" ? arg : { block: "nearest" });
  };
  /* ---- checkVisibility ----
     Consults the real cascade (__computedStyle over collected rules), not
     just inline styles — display:none by stylesheet rule counts, and
     visibility:hidden anywhere on the ancestor chain counts. */
  Element.prototype.checkVisibility = function (opts) {
    opts = opts || {};
    var doc = this.ownerDocument || global.document;
    var rules = doc ? __collectStyleRules(doc) : [];
    var n = this;
    while (n && n.nodeType === 1) {
      var cs = __computedStyle(n, rules, 16);
      if ((cs.display || "").toLowerCase() === "none") return false;
      var vis = (cs.visibility || "").toLowerCase();
      if (vis === "hidden" || vis === "collapse") return false;
      if (opts.checkOpacity && Number(cs.opacity) === 0) return false;
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
        if (el && typeof el.checkVisibility === "function" && !el.checkVisibility()) hidden = true;
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
     attribute (there is no top layer). */
  Element.prototype.showPopover = function () {
    if (this.__popoverOpen) return;
    var ev = new global.Event("beforetoggle", { bubbles: true, cancelable: true });
    ev.oldState = "closed";
    ev.newState = "open";
    this.dispatchEvent(ev);
    if (ev.defaultPrevented) return;
    this.__popoverOpen = true;
    this.setAttribute("open", "");
    var t = new global.Event("toggle", { bubbles: true });
    t.oldState = "closed";
    t.newState = "open";
    this.dispatchEvent(t);
  };
  Element.prototype.hidePopover = function () {
    if (!this.__popoverOpen) return;
    this.__popoverOpen = false;
    this.removeAttribute("open");
    var t = new global.Event("toggle", { bubbles: true });
    t.oldState = "open";
    t.newState = "closed";
    this.dispatchEvent(t);
  };
  Element.prototype.togglePopover = function (prefer) {
    if (prefer === undefined) {
      if (this.__popoverOpen) this.hidePopover();
      else this.showPopover();
    } else if (prefer) this.showPopover();
    else this.hidePopover();
  };
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
      this.setAttribute("open", "");
    };
    __DialogCtor.prototype.showModal = function () {
      if (this.open) return;
      this.setAttribute("open", "");
      this.dispatchEvent(new global.Event("open", { bubbles: false }));
    };
    __DialogCtor.prototype.close = function (returnValue) {
      if (!this.open) return;
      this.removeAttribute("open");
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
    var tokens = sel.split(/\s+/);
    var sets = [collectElements(root)];
    tokens.forEach(function (tok) {
      var next = [];
      sets[sets.length - 1].forEach(function (n) {
        collectElements(n).forEach(function (c) {
          if (matchSimple(c, tok) && next.indexOf(c) < 0) next.push(c);
        });
        if (n !== root && matchSimple(n, tok) && next.indexOf(n) < 0 && n.nodeType === 1) next.push(n);
      });
      sets.push(next);
    });
    return sets[sets.length - 1];
  }
  function matchSimple(el, tok) {
    if (el.nodeType !== 1) return false;
    if (tok === "*") return true;
    if (tok.charAt(0) === "#") return el.id === tok.slice(1) || el.attributes.id === tok.slice(1);
    if (tok.charAt(0) === ".") return (el.className || "").split(/\s+/).indexOf(tok.slice(1)) >= 0;
    var m = tok.match(/^([a-z0-9-]+)?(?:#([a-zA-Z0-9_-]+))?(?:\.([a-zA-Z0-9_-]+))?(?:\[([a-zA-Z0-9_-]+)(?:=["']?([^"'\]]*)["']?)?\])?$/i);
    if (!m) return el.nodeName === tok.toLowerCase();
    if (m[1] && el.nodeName !== m[1].toLowerCase()) return false;
    if (m[2] && el.id !== m[2] && el.attributes.id !== m[2]) return false;
    if (m[3] && (el.className || "").split(/\s+/).indexOf(m[3]) < 0) return false;
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
  global.setTimeout = function (fn, ms) {
    timers.push({ fn: fn, ms: ms || 0 });
    return timers.length;
  };
  global.clearTimeout = function () {};
  global.setInterval = function (fn, ms) {
    timers.push({ fn: fn, ms: ms || 0, repeat: true });
    return timers.length;
  };
  global.clearInterval = function () {};
  global.requestAnimationFrame = function (fn) { return global.setTimeout(fn, 16); };
  global.cancelAnimationFrame = function () {};
  global.queueMicrotask = function (fn) {
    if (_nativePromiseResolve) {
      _nativePromiseResolve().then(fn);
    } else {
      global.setTimeout(fn, 0);
    }
  };
  global.__drainTimers = function () {
    var q = timers.slice(); timers = [];
    q.sort(function (a, b) { return a.ms - b.ms; });
    q.forEach(function (t) {
      try { t.fn(); } catch (e) {}
      if (t.repeat && timers.length < 2000) timers.push(t);
    });
    return timers.length;
  };

  /* Parse an absolute URL string into plain fields on `u`; returns the raw
     query (with its leading "?") for the caller to feed into searchParams. */
  function __urlParseInto(u, absolute) {
    var m = /^([a-zA-Z][a-zA-Z0-9+.-]*:)?(?:\/\/([^/?#]*))?([^?#]*)(\?[^#]*)?(#.*)?$/.exec(absolute) || [];
    u.protocol = m[1] || "";
    var auth = m[2] || "";
    var host = auth.split("@").pop();
    u.host = host;
    u.hostname = host.split(":")[0];
    u.port = host.indexOf(":") >= 0 ? host.split(":")[1] : "";
    u.pathname = m[3] || "/";
    u.hash = m[5] || "";
    u.username = auth.indexOf("@") > 0 ? auth.split("@")[0].split(":")[0] : "";
    u.password = auth.indexOf("@") > 0 && auth.split("@")[0].indexOf(":") > 0 ? auth.split("@")[0].split(":")[1] : "";
    return m[4] || "";
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
    /* href/search/origin are live accessors over the parsed fields and the
       searchParams object, per the spec's URLUtils contract: every
       searchParams mutation (set/append/delete/sort) is reflected in .href,
       .search and .toString(), and assigning .search/.href re-parses. The
       old plain-data href froze at construction, so searchParams writes
       were silently dropped and every engine-side GET form submit navigated
       without its query string (a verified P0: session.submit on a GET form
       fetched the action URL with no query at all). */
    this.searchParams = new URLSearchParamsImpl(initialSearch);
    Object.defineProperty(this, "search", {
      get: function () {
        var qs = self.searchParams.toString();
        return qs ? "?" + qs : "";
      },
      set: function (v) { self.searchParams._replace(String(v == null ? "" : v)); },
      configurable: true,
      enumerable: true
    });
    Object.defineProperty(this, "origin", {
      get: function () { return (self.protocol && self.host) ? self.protocol + "//" + self.host : ""; },
      configurable: true,
      enumerable: true
    });
    Object.defineProperty(this, "href", {
      get: function () { return self.origin + self.pathname + self.search + (self.hash || ""); },
      set: function (v) {
        var s = __urlParseInto(self, String(v == null ? "" : v));
        self.searchParams._replace(s);
      },
      configurable: true,
      enumerable: true
    });
    this.toString = function () { return self.href; };
    this.assign = function (u) { try { global.__WR_NAVIGATED = String(u || ""); } catch (e) {} };
    this.replace = function (u) { try { global.__WR_NAVIGATED = String(u || ""); } catch (e) {} };
    this.reload = function () {};
  }
  /* Shared parser for URLSearchParams sources: "?a=1&b=2" -> {a:"1",b:"2"},
     with duplicate keys collapsing to arrays. */
  function __spParse(query) {
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
    var self = this;
    this.get = function (k) { var v = this._p[k]; return v == null ? null : (Array.isArray(v) ? v[0] : v); };
    this.getAll = function (k) { var v = this._p[k]; return v == null ? [] : (Array.isArray(v) ? v.slice() : [v]); };
    this.has = function (k) { return this._p[k] != null; };
    /* append/set/delete/sort: the standard mutation surface. A real miss:
       LinkedIn's interop iframe URL builder called params.append("_l", v)
       and crashed "not a function" — killing the whole login render. */
    this.append = function (k, v) {
      var cur = this._p[k];
      if (cur == null) this._p[k] = String(v);
      else if (Array.isArray(cur)) cur.push(String(v));
      else this._p[k] = [cur, String(v)];
    };
    this.set = function (k, v) { this._p[k] = String(v); };
    this.delete = function (k) { delete this._p[k]; };
    this.sort = function () {
      var keys = Object.keys(this._p).sort();
      var sorted = {};
      for (var i = 0; i < keys.length; i++) sorted[keys[i]] = this._p[keys[i]];
      this._p = sorted;
    };
    /* entries/keys/values + iterator: real production code calls
       Object.fromEntries(params.entries()) (LinkedIn's interop tracking,
       which crashed "not a function" without them). */
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
      // Object.fromEntries / Array.from consume Symbol.iterator; a bare
      // next() method is not enough (verified: "value is not iterable").
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
    /* Reset the contents in place, keeping object identity: code holding a
       reference to `url.searchParams` must observe `url.search = "..."` and
       `url.href = "..."` re-assignments. */
    this._replace = function (query) { this._p = __spParse(query); };
    this.toString = function () {
      var out = [];
      for (var k in this._p) {
        var v = this._p[k];
        if (Array.isArray(v)) v.forEach(function (x) { out.push(encodeURIComponent(k) + "=" + encodeURIComponent(x)); });
        else out.push(encodeURIComponent(k) + "=" + encodeURIComponent(v));
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
  global.MediaStream = global.MediaStream || function () {
    this.id = "";
    this.active = false;
    this.getTracks = function () { return []; };
    this.getAudioTracks = function () { return []; };
  };
  global.AudioNode = global.AudioNode || function () {};
  global.WorkletNode = global.WorkletNode || function () {};

  /* IntersectionObserver / ResizeObserver (scroll-driven). MutationObserver is defined earlier. */
  var scrollState = { x: 0, y: 0 };
  var intersectionWatchers = [];

  function _elementIntersects(el) {
    if (!el || typeof el.getBoundingClientRect !== "function") return true;
    var r = el.getBoundingClientRect();
    var vh = global.innerHeight || 720;
    var vw = global.innerWidth || 1280;
    return (r.bottom - scrollState.y) > 0 && (r.top - scrollState.y) < vh &&
           (r.right - scrollState.x) > 0 && (r.left - scrollState.x) < vw;
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

  global.getComputedStyle = function (el) {
    return (el && el.style) ? el.style : {
      getPropertyValue: function () { return ""; },
      length: 0
    };
  };
  global.matchMedia = function (q) {
    return { matches: false, media: String(q || ""), addListener: function () {}, removeListener: function () {}, addEventListener: function () {}, removeEventListener: function () {} };
  };
  global.requestIdleCallback = function (fn) { return global.setTimeout(fn, 1); };
  global.cancelIdleCallback = function () {};

  /* Canvas / WebGL / Audio / Worker / WASM stubs: plausible, non-crashing. */
  function makeContext2D(canvas) {
    var noop = function () {};
    var ctx = {
      canvas: canvas || null, fillStyle: "#000", strokeStyle: "#000", font: "10px sans-serif",
      globalAlpha: 1, lineWidth: 1, textBaseline: "alphabetic", textAlign: "start",
      fillRect: noop, strokeRect: noop, clearRect: noop, fillText: noop, strokeText: noop,
      beginPath: noop, closePath: noop, moveTo: noop, lineTo: noop, arc: noop, rect: noop,
      ellipse: noop, quadraticCurveTo: noop, bezierCurveTo: noop, arcTo: noop,
      fill: noop, stroke: noop, clip: noop, save: noop, restore: noop, translate: noop,
      scale: noop, rotate: noop, setTransform: noop, transform: noop, resetTransform: noop,
      drawImage: noop, setLineDash: noop, getLineDash: function () { return []; },
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
        var cw = w || 1, ch = h || 1;
        var n = cw * ch * 4;
        var buf = new Uint8ClampedArray(n);
        // Fill with deterministic non-zero data based on canvas operations
        // Real browsers have sub-pixel rendering artifacts; all-zero is a dead giveaway
        var ops = (this._canvas && this._canvas.__ops) ? this._canvas.__ops : 0;
        var seed = (ops * 31) + ((x || 0) * 17) + ((y || 0) * 7);
        for (var i = 0; i < n; i += 4) {
          var px = i / 4;
          var py = Math.floor(px / cw);
          var pxx = px % cw;
          var h2 = (((seed ^ (pxx * 374761393) ^ (py * 668265263)) >>> 0) % 256);
          if (ops > 0) {
            // When drawing has occurred, produce plausible pixel data
            buf[i]     = (h2 + pxx * 2 + py) % 256;
            buf[i + 1] = (h2 + pxx + py * 3) % 256;
            buf[i + 2] = (h2 + py * 2 + pxx * 3) % 256;
            buf[i + 3] = 255;
          } else {
            // "Blank" canvas still has sub-pixel noise in real browsers
            buf[i]     = 255;
            buf[i + 1] = 255;
            buf[i + 2] = 255;
            buf[i + 3] = 0;
          }
        }
        return { width: cw, height: ch, data: buf };
      },
      putImageData: noop, createImageData: function (w, h) {
        return { width: w || 1, height: h || 1, data: new Uint8ClampedArray((w || 1) * (h || 1) * 4) };
      },
      createLinearGradient: function () { return { addColorStop: noop }; },
      createRadialGradient: function () { return { addColorStop: noop }; },
      createPattern: function () { return {}; }
    };
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
    return {
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
      createBuffer: function () { return {}; }, bindBuffer: function () {},
      createShader: function () { return {}; }, shaderSource: function () {}, compileShader: function () {},
      createProgram: function () { return {}; }, attachShader: function () {}, linkProgram: function () {},
      getProgramParameter: function () { return true; }, getShaderParameter: function () { return true; },
      useProgram: function () {}, drawArrays: function () {}, drawElements: function () {},
      clearColor: function () {}, clear: function () {}, viewport: function () {},
      enable: function () {}, disable: function () {}, getError: function () { return 0; },
      isContextLost: function () { return false; }
    };
  }

  function _attachCanvas(el) {
    el.width = parseInt((el.attributes && el.attributes.width) || "300", 10);
    el.height = parseInt((el.attributes && el.attributes.height) || "150", 10);
    el.__ops = 0;
    el.getContext = function (type) {
      type = String(type || "").toLowerCase();
      if (type === "2d") { var c = makeContext2D(el); return c; }
      if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") {
        return makeWebGLContext(el);
      }
      return null;
    };
    el.toDataURL = function (mime) {
      el.__ops++;
      return _canvasDataURL(el);
    };
    el.toBlob = function (cb) { if (typeof cb === "function") cb(null); };
    el.captureStream = function () { return { getTracks: function () { return []; } }; };
  }

  global.CanvasRenderingContext2D = makeContext2D;
  global.WebGLRenderingContext = function (canvas) { return makeWebGLContext(canvas); };
  global.WebGL2RenderingContext = function (canvas) { return makeWebGLContext(canvas); };
  global.OffscreenCanvas = function (w, h) {
    this.width = w || 300; this.height = h || 150;
    this.__ops = 0;
    this.getContext = function (type) {
      type = String(type || "").toLowerCase();
      if (type === "2d") return makeContext2D(this);
      if (type === "webgl" || type === "experimental-webgl" || type === "webgl2") return makeWebGLContext(this);
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
    if (weekday === "long") parts.push(DAYS_EN[date.getDay()]);
    else if (weekday === "short") parts.push(DAYS_EN[date.getDay()].slice(0, 3));
    else if (weekday === "narrow") parts.push(DAYS_EN[date.getDay()].charAt(0));
    if (year) parts.push(pad4(date.getFullYear()));
    if (month === "long") parts.push(MONTHS_EN[date.getMonth()]);
    else if (month === "short") parts.push(MONTHS_EN[date.getMonth()].slice(0, 3));
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
    return { locale: this._locale === "en" ? "en-US" : this._locale, calendar: "gregory", numberingSystem: "latn", timeZone: "UTC" };
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
    fixed = _group(fixed);
    if (this._style === "currency") return fixed + " " + this._currency;
    if (this._style === "percent") return fixed + "%";
    return fixed;
  };
  NumberFormat.prototype.resolvedOptions = function () {
    return { locale: this._locale === "en" ? "en-US" : this._locale, style: this._style, currency: this._currency, minimumFractionDigits: this._min, maximumFractionDigits: this._max };
  };
  IntlShim.NumberFormat = NumberFormat;

  function PluralRules(locale, options) {
    this._locale = _normLocale(locale);
    this._type = (options && options.type) || "cardinal";
  }
  PluralRules.prototype.select = function (n) {
    n = Number(n);
    if (this._type === "ordinal") {
      if (n % 100 >= 11 && n % 100 <= 13) return "other";
      if (n % 10 === 1) return "one";
      if (n % 10 === 2) return "two";
      if (n % 10 === 3) return "few";
      return "other";
    }
    if (n === 1) return "one";
    return "other";
  };
  PluralRules.prototype.resolvedOptions = function () { return { locale: this._locale, type: this._type }; };
  IntlShim.PluralRules = PluralRules;

  function ListFormat(locale, options) {
    this._locale = _normLocale(locale);
    this._type = (options && options.type) || "conjunction";
  }
  ListFormat.prototype.format = function (items) {
    items = Array.isArray(items) ? items.map(String) : [String(items)];
    if (items.length === 0) return "";
    if (items.length === 1) return items[0];
    if (items.length === 2) return items[0] + (this._type === "disjunction" ? " or " : " and ") + items[1];
    var sep = this._type === "disjunction" ? ", or " : ", and ";
    return items.slice(0, -1).join(", ") + sep + items[items.length - 1];
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

  /* ---- structuredClone (JSON-deep clone; disclosed: no functions/DOM nodes) ---- */
  global.structuredClone = function (value) {
    if (value === undefined) return undefined;
    return JSON.parse(JSON.stringify(value));
  };

  /* ---- WeakRef / FinalizationRegistry (safe shims: strong retention,
         no callbacks — semantically a conservative over-approximation) ---- */
  global.WeakRef = function (target) { this._target = target; };
  global.WeakRef.prototype.deref = function () { return this._target; };
  global.FinalizationRegistry = function () {
    this.register = function () {};
    this.unregister = function () { return false; };
    this.cleanupSome = function () {};
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

  /* ---- performance.getEntriesByName/getEntriesByType (empty timeline) ---- */
  if (global.performance && !global.performance.getEntriesByName) {
    global.performance.getEntriesByName = function () { return []; };
    global.performance.getEntriesByType = function () { return []; };
    global.performance.getEntries = function () { return []; };
    global.performance.mark = function () { return { name: "", entryType: "mark", startTime: 0, duration: 0 }; };
    global.performance.measure = function () { return { name: "", entryType: "measure", startTime: 0, duration: 0 }; };
    global.performance.now = global.performance.now || function () { return Date.now(); };
  }

  function AudioContextStub() {
    this.sampleRate = 44100;
    this.destination = {};
    this.createOscillator = function () {
      return { connect: function () {}, start: function () {}, stop: function () {}, frequency: { value: 0 }, type: "sine" };
    };
    this.createAnalyser = function () {
      return { connect: function () {}, getFloatFrequencyData: function (a) { for (var i = 0; i < a.length; i++) a[i] = -100; }, fftSize: 2048, frequencyBinCount: 1024 };
    };
    this.createGain = function () { return { connect: function () {}, gain: { value: 1 } }; };
    this.createDynamicsCompressor = function () {
      return { connect: function () {}, threshold: { value: -24 }, knee: { value: 30 }, ratio: { value: 12 }, attack: { value: 0.003 }, release: { value: 0.25 } };
    };
    this.createBuffer = function (ch, len, rate) { return { getChannelData: function () { return new Float32Array(len || 1); }, length: len || 1 }; };
    this.createBufferSource = function () { return { connect: function () {}, start: function () {}, buffer: null }; };
  }
  global.AudioContext = AudioContextStub;
  global.webkitAudioContext = AudioContextStub;

  global.Worker = function (url) {
    this.url = String(url || "");
    this.onmessage = null;
    this.postMessage = function () {};
    this.terminate = function () {};
    this.addEventListener = function () {};
    this.removeEventListener = function () {};
  };
  global.SharedWorker = global.Worker;
  global.WebAssembly = global.WebAssembly || {
    instantiate: function () { return Promise.resolve({ module: {}, instance: { exports: {} } }); },
    compile: function () { return Promise.resolve({}); },
    validate: function () { return false; }
  };

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

  /* MessageChannel & MessagePort for macro-task scheduling (React Scheduler) */
  function MessagePort() {
    this._other = null;
    this.onmessage = null;
    this._listeners = [];
  }
  MessagePort.prototype.postMessage = function (data) {
    var other = this._other;
    if (!other) return;
    setTimeout(function () {
      var evt = new MessageEvent("message", { data: data });
      if (typeof other.onmessage === "function") other.onmessage(evt);
      other._listeners.forEach(function (l) {
        try { l(evt); } catch (e) {}
      });
    }, 0);
  };
  MessagePort.prototype.addEventListener = function (type, fn) {
    if (type === "message") this._listeners.push(fn);
  };
  MessagePort.prototype.removeEventListener = function (type, fn) {
    this._listeners = this._listeners.filter(function (l) { return l !== fn; });
  };
  MessagePort.prototype.start = function () {};
  MessagePort.prototype.close = function () {};

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
     pushState/replaceState record the request on __WR_PUSHED/__WR_REPLACED
     for the host session (which owns the real history entries); back/forward/
     go record a move on __WR_HISTORY_MOVE. The host performs the actual entry
     moves and dispatches popstate into the rebuilt document — the engine
     context cannot outlive a navigation, so it cannot own history alone. */
  global.history = {
    length: 1,
    state: null,
    _stack: [],
    pushState: function (state, title, url) {
      this.state = state;
      this._stack.push(url);
      if (url) {
        if (typeof global._updateLocationQuietly === "function") global._updateLocationQuietly(url);
        else if (global.location) global.location.href = String(url);
      }
      if (url) {
        var abs = String(url);
        try { abs = new URL(String(url), (global.location && global.location.href) || "about:blank").href; } catch (e) {}
        var ser = null;
        try { ser = (state === undefined) ? null : JSON.parse(JSON.stringify(state == null ? null : state)); } catch (e) { ser = null; }
        global.__WR_PUSHED = { url: abs, state: ser };
      }
    },
    replaceState: function (state, title, url) {
      this.state = state;
      if (url) {
        if (typeof global._updateLocationQuietly === "function") global._updateLocationQuietly(url);
        else if (global.location) global.location.href = String(url);
        var abs2 = String(url);
        try { abs2 = new URL(String(url), (global.location && global.location.href) || "about:blank").href; } catch (e) {}
        global.__WR_REPLACED = { url: abs2, state: state === undefined ? null : state };
      }
    },
    back: function () { global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) - 1; },
    forward: function () { global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) + 1; },
    go: function (delta) {
      var d = Math.floor(Number(delta) || 0);
      global.__WR_HISTORY_MOVE = (global.__WR_HISTORY_MOVE || 0) + d;
    }
  };
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
    _notifyIntersections();
    if (typeof global.dispatchEvent === "function" && typeof global.Event === "function") {
      try {
        var tNow = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
        global.dispatchEvent(new global.Event("scroll", { bubbles: false, isTrusted: true, timeStamp: tNow }));
      } catch (e) {}
    }
  }
  global.scrollTo = _doScroll;
  global.scroll = _doScroll;
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
      var ease = progress < 0.5 ? 2 * progress * progress : -1 + (4 - 2 * progress) * progress;
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
    if (typeof x === "object" && x !== null) { y = x.top || 0; x = x.left || 0; }
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
    log: function () {
      if (typeof host_log === "function") {
        host_log(Array.prototype.map.call(arguments, this._fmt).join(" "));
      }
    },
    warn: function () { this.log.apply(this, arguments); },
    error: function () { this.log.apply(this, arguments); },
    info: function () { this.log.apply(this, arguments); },
    debug: function () { this.log.apply(this, arguments); },
    trace: function () { this.log.apply(this, arguments); }
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
  var storage = {};
  var __seedL = __storageSeed.local;
  if (__seedL) {
    for (var __slk in __seedL) {
      if (Object.prototype.hasOwnProperty.call(__seedL, __slk)) storage[__slk] = String(__seedL[__slk]);
    }
  }
  var sessionStorageStore = {};
  var __seedS = __storageSeed.session;
  if (__seedS) {
    for (var __ssk in __seedS) {
      if (Object.prototype.hasOwnProperty.call(__seedS, __ssk)) sessionStorageStore[__ssk] = String(__seedS[__ssk]);
    }
  }
  global.__drainStorage = function () {
    try { return JSON.stringify({ local: storage, session: sessionStorageStore }); }
    catch (e) { return "{}"; }
  };
  global.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
    setItem: function (k, v) { storage[k] = String(v); },
    removeItem: function (k) { delete storage[k]; },
    clear: function () { Object.keys(storage).forEach(function (k) { delete storage[k]; }); },
    key: function (i) { return Object.keys(storage)[i] || null; },
    get length() { return Object.keys(storage).length; }
  };
  global.sessionStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(sessionStorageStore, k) ? sessionStorageStore[k] : null; },
    setItem: function (k, v) { sessionStorageStore[k] = String(v); },
    removeItem: function (k) { delete sessionStorageStore[k]; },
    clear: function () { Object.keys(sessionStorageStore).forEach(function (k) { delete sessionStorageStore[k]; }); },
    key: function (i) { return Object.keys(sessionStorageStore)[i] || null; },
    get length() { return Object.keys(sessionStorageStore).length; }
  };

  /* In-memory IndexedDB subsystem (zero external dependencies, zero RAM bloat). */
  (function () {
    var idbDatabases = {};

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
      return new IDBObjectStore(name, dbData.stores[name], this);
    };
    IDBTransaction.prototype.abort = function () {};
    IDBTransaction.prototype.commit = function () {};

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
      this._data.indices[name] = { name: name, keyPath: keyPath, options: options || {} };
      return this.index(name);
    };
    IDBObjectStore.prototype.index = function (name) {
      var self = this;
      return {
        name: name,
        get: function (k) { return self.get(k); },
        getAll: function () { return self.getAll(); },
        count: function () { return self.count(); }
      };
    };

    var IDBFactory = {
      open: function (name, version) {
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
    get: function () {
      if (this.nodeName.toLowerCase() !== "iframe") return null;
      if (!this._contentWindow) {
        var self = this;
        var cw = {
          postMessage: function (data, targetOrigin) {
            if (typeof host_post_message === "function") {
              var msgData = (typeof data === "string") ? data : JSON.stringify(data);
              host_post_message(msgData, String(targetOrigin || "*"), self.getAttribute("src") || "");
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
     defined"). Our fetch is synchronous so signals never actually abort a
     request in flight; the controller faithfully reports abort state and
     fires its events for listeners — the honest shape of this engine. */
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
      signal.reason = reason !== undefined ? reason : new Error("This operation was aborted");
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
      return Promise.reject(opts.signal.reason || new Error("This operation was aborted"));
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
    var raw = typeof host_fetch === "function" ? host_fetch(String(url), method, body, JSON.stringify(customHeaders), bodyB64) : "{\"status\":599,\"body\":\"\"}";
    var res = typeof raw === "string" ? JSON.parse(raw) : raw;
    var respHeaders = new Headers();
    if (res.content_type) respHeaders.set("content-type", res.content_type);
    return Promise.resolve(new Response(res.body || "", {
      status: res.status || 0,
      url: res.url || String(url),
      headers: respHeaders
    }));
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
          var raw = host_fetch(self.url, "GET", "");
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
        ? host_fetch(this._url, this._method, reqBody, JSON.stringify(this._headers), reqBodyB64)
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
        enumerateDevices: function () { return Promise.resolve([]); },
        getUserMedia: function () { return Promise.reject(new Error("NotSupportedError")); }
      },
      /* sendBeacon: fire-and-forget POST through the owned host_fetch lane
         (CORS-checked there like every in-page request). Returns true when the
         request was handed to the host, per spec semantics. */
      sendBeacon: function (url, data) {
        try {
          var body = "";
          if (data == null) body = "";
          else if (typeof data === "string") body = data;
          else if (data instanceof global.FormData || data._data) {
            var sfd = (typeof __serializeFormData === "function") ? __serializeFormData(data) : { body: "", contentType: "" };
            body = sfd.body;
          } else if (typeof data === "object") body = JSON.stringify(data);
          else body = String(data);
          var abs = String(url || "");
          try { abs = new URL(String(url || ""), (global.location && global.location.href) || "about:blank").href; } catch (e) {}
          if (typeof host_fetch === "function") {
            host_fetch(abs, "POST", body, JSON.stringify({ "content-type": "text/plain;charset=UTF-8" }), "");
            return true;
          }
          return false;
        } catch (e) { return false; }
      },
      /* Geolocation: an agent browser has no location hardware. The honest
         answer is PERMISSION_DENIED — same as a real browser with location
         blocked — never invented coordinates. */
      geolocation: {
        getCurrentPosition: function (success, error) {
          if (typeof error === "function") {
            error({ code: 1, message: "User has denied permission to use positioning" });
          }
        },
        watchPosition: function (success, error) {
          if (typeof error === "function") {
            error({ code: 1, message: "User has denied permission to use positioning" });
          }
          return 0;
        },
        clearWatch: function () {}
      },
      /* Service worker container: register() resolves with a registration so
         feature-detecting pages proceed; actual SW analysis/installation is
         host-side (sw.py regex + strategy simulation) and navigator.serviceWorker
         does not imply SW JS execution. controller is null: no page is
         controlled by a SW in this engine. */
      serviceWorker: {
        controller: null,
        ready: Promise.resolve({
          active: { scope: "/", state: "activated", unregister: function () { return Promise.resolve(true); } }
        }),
        register: function (scriptUrl, options) {
          var scope = (options && options.scope) || "/";
          return Promise.resolve({
            scope: scope,
            installing: null,
            waiting: null,
            active: { scope: scope, state: "activated", scriptURL: String(scriptUrl || ""), unregister: function () { return Promise.resolve(true); } },
            unregister: function () { return Promise.resolve(true); }
          });
        },
        getRegistration: function () { return Promise.resolve(undefined); },
        getRegistrations: function () { return Promise.resolve([]); },
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
        (__bcRegistry[this.name] = __bcRegistry[this.name] || []).push(this);
        this.postMessage = function (data) {
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
          var list = __bcRegistry[this.name] || [];
          __bcRegistry[this.name] = list.filter(function (ch) { return ch !== this; });
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
       collapsed at the innermost containing element. Disclosed approximation:
       no caret geometry, so the Range carries the element, not an offset. */
    Document.prototype.caretRangeFromPoint = function (x, y) {
      __ensureLayout(this);
      var px = Number(x) || 0, py = Number(y) || 0;
      var best = null;
      (function walk(n) {
        if (!n || n.nodeType !== 1) return;
        var b = n.__box;
        if (b && px >= b.x && px <= b.x + b.width && py >= b.y && py <= b.y + b.height) {
          if (!best) best = n;
        }
        (n.childNodes || []).forEach(function (c) { if (c.nodeType === 1) walk(c); });
      })(this.body || this.documentElement);
      var r = new global.Range();
      if (best) {
        r.startContainer = best;
        r.endContainer = best;
        r.collapsed = true;
      }
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
      return _clockOffset;
    };
    global.__getClockOffset = function () { return _clockOffset; };
    global.performance = {
      timeOrigin: startTime,
      now: function () { return (Date.now() - startTime) + _clockOffset; },
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
      getEntries: function () { return []; },
      getEntriesByType: function () { return []; },
      mark: function () {},
      measure: function () {}
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
      this.source = (init && init.source) || global.parent || global;
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
       draggable=true flow crashed. */
    global.DataTransfer = function () {
      this.dropEffect = "none";
      this.effectAllowed = "uninitialized";
      this.__data = {};
      this.types = [];
      this.items = [];
    };
    global.DataTransfer.prototype.setData = function (type, value) {
      type = String(type);
      this.__data[type] = String(value);
      this.types = Object.keys(this.__data);
    };
    global.DataTransfer.prototype.getData = function (type) {
      type = String(type);
      return Object.prototype.hasOwnProperty.call(this.__data, type) ? this.__data[type] : "";
    };
    global.DataTransfer.prototype.hasData = function (type) {
      return Object.prototype.hasOwnProperty.call(this.__data, String(type));
    };
    global.DataTransfer.prototype.clearData = function (type) {
      if (type === undefined) { this.__data = {}; }
      else { delete this.__data[String(type)]; }
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
    global.Range = Range;

    function Selection() {
      this._ranges = [];
    }
    Object.defineProperty(Selection.prototype, "rangeCount", {
      get: function () { return this._ranges.length; }
    });
    Selection.prototype.getRangeAt = function (i) { return this._ranges[i] || null; };
    Selection.prototype.addRange = function (r) { if (this._ranges.indexOf(r) < 0) this._ranges.push(r); };
    Selection.prototype.removeRange = function (r) {
      var idx = this._ranges.indexOf(r);
      if (idx >= 0) this._ranges.splice(idx, 1);
    };
    Selection.prototype.removeAllRanges = function () { this._ranges = []; };
    Selection.prototype.collapse = function () { this._ranges = []; };
    Selection.prototype.selectAllChildren = function (node) {
      this._ranges = [];
      var r = new Range();
      r.selectNodeContents(node);
      this._ranges.push(r);
    };
    Selection.prototype.toString = function () { return ""; };
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
    global.dispatchEvent = function (event) {
      (windowListeners[event.type] || []).slice().forEach(function (fn) {
        try { fn.call(global, event); } catch (e) {}
      });
      return true;
    };
    global.postMessage = function (data, origin) {
      var targetOrigin = String(origin != null ? origin : "*");
      var senderOrigin = (global.location && global.location.origin) || "";
      var msgData = (typeof data === "string") ? data : JSON.stringify(data);
      if (typeof host_post_message === "function") {
        host_post_message(msgData, targetOrigin, "self", senderOrigin);
      }
      if (targetOrigin !== "*" && targetOrigin !== "/" && targetOrigin !== senderOrigin) {
        return;
      }
      Promise.resolve().then(function () {
        global.dispatchEvent(new global.MessageEvent("message", {
          data: data,
          origin: senderOrigin,
          source: global
        }));
      });
    };
    global.parent = {
      postMessage: function (data, origin) {
        var targetOrigin = String(origin != null ? origin : "*");
        var senderOrigin = (global.location && global.location.origin) || "";
        var msgData = (typeof data === "string") ? data : JSON.stringify(data);
        if (typeof host_post_message === "function") {
          host_post_message(msgData, targetOrigin, "parent", senderOrigin);
        }
      }
    };
    global.top = global.parent;
    global.self = global;
    global.__receiveMessage = function (data, origin, targetOrigin) {
      if (targetOrigin && targetOrigin !== "*") {
        var myOrigin = (global.location && global.location.origin) || "";
        if (myOrigin && targetOrigin !== myOrigin) {
          return false;
        }
      }
      var sourceProxy = {
        postMessage: function (replyData, replyOrigin) {
          var repTarget = String(replyOrigin != null ? replyOrigin : "*");
          var repSender = (global.location && global.location.origin) || "";
          var repMsgData = (typeof replyData === "string") ? replyData : JSON.stringify(replyData);
          if (typeof host_post_message === "function") {
            host_post_message(repMsgData, repTarget, "reply", repSender);
          }
        }
      };
      Promise.resolve().then(function () {
        global.dispatchEvent(new global.MessageEvent("message", {
          data: data,
          origin: String(origin || ""),
          source: sourceProxy
        }));
      });
      return true;
    };
    global.frames = [];
    global.innerWidth = 1280;
    global.innerHeight = 720;
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

  global.__clickPath = function (path, stealth) {
    var el = global.__byPath(path);
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
    if (el.disabled && (elTag === "input" || elTag === "textarea" || elTag === "select")) {
      return false;
    }
    if (typeof el.focus === "function") el.focus();
    text = String(text != null ? text : "");
    var curVal = el.value || "";
    var editable = !!el.isContentEditable;
    if (editable) curVal = el.textContent || "";
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

      curVal += ch;
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
      var tEnd = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
      el.dispatchEvent(new global.Event("change", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tEnd }));
      return true;
    }
    el.setAttribute("value", curVal);
    var tEnd2 = (global.performance && typeof global.performance.now === "function") ? global.performance.now() : Date.now();
    el.dispatchEvent(new global.Event("change", { bubbles: true, cancelable: false, isTrusted: true, timeStamp: tEnd2 }));
    return true;
  }

  global.__typePath = function (path, text, delays, stealth) {
    var el = global.__byPath(path);
    if (!el) return false;
    return __dispatchTypeSequence(el, text, delays, stealth);
  };

  global.__activateSelector = function (selector, text, delays, stealth) {
    var el = null;
    if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
    else el = global.document.querySelector(selector);
    if (!el) return false;
    if (stealth !== undefined) global.__stealthMode = !!stealth;
    if (el.nodeName === "select") {
      if (el.disabled) return false;
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
    if (!clickOk) return false;
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
      if (el.disabled) continue;
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
    if (el.disabled && (pkTag === "input" || pkTag === "textarea" || pkTag === "select" || pkTag === "button")) {
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
        } else if (el.isContentEditable) {
          el.textContent = (el.textContent || "") + "\n";
          var nlEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
          nlEv.data = "\n";
          nlEv.inputType = "insertLineBreak";
          el.dispatchEvent(nlEv);
        } else if (tag === "textarea") {
          el.value = (el.value || "") + "\n";
          var taEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
          taEv.data = "\n";
          taEv.inputType = "insertLineBreak";
          el.dispatchEvent(taEv);
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
        if (tag === "input" || tag === "textarea") {
          el.value = String(el.value || "").slice(0, -1);
        } else if (el.isContentEditable) {
          el.textContent = String(el.textContent || "").slice(0, -1);
        }
        var bsEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
        bsEv.inputType = "deleteContentBackward";
        el.dispatchEvent(bsEv);
      } else if (lower === "space") {
        if (tag === "button" || tag === "a") {
          if (typeof el.__nativeClick === "function") el.__nativeClick();
          else if (typeof el.click === "function") el.click();
        } else if (tag === "input" || tag === "textarea" || el.isContentEditable) {
          if (tag === "input" || tag === "textarea") el.value = (el.value || "") + " ";
          else el.textContent = (el.textContent || "") + " ";
          var spEv = new global.Event("input", { bubbles: true, cancelable: false, isTrusted: true });
          spEv.data = " ";
          spEv.inputType = "insertText";
          el.dispatchEvent(spEv);
        }
      }
    }

    el.dispatchEvent(__keyEvent("keyup", def));
    return true;
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

    /* Native HTML5 drag & drop: when the source is draggable (or listens for
       dragstart), the drag sequence carries a real DataTransfer — dragstart
       (cancelable: preventDefault aborts), drag per step, dragover/drop on the
       element under the translated source box, dragend on the source. Pages
       with only mouse listeners keep the plain pointer lane. */
    var nativeDrag = false;
    var dragDT = null;
    var wantsNativeDrag = (el.getAttribute("draggable") || "").toLowerCase() === "true"
      || (el._listeners && el._listeners["dragstart"] && el._listeners["dragstart"].length > 0);
    if (wantsNativeDrag && typeof global.DragEvent === "function" && typeof global.DataTransfer === "function") {
      dragDT = new global.DataTransfer();
      var dsInit = { bubbles: true, cancelable: true, isTrusted: true, __trusted: true, clientX: startX, clientY: startY, button: 0, buttons: 1, dataTransfer: dragDT, timeStamp: t };
      var dsEv = new global.DragEvent("dragstart", dsInit);
      el.dispatchEvent(dsEv);
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
      dragDropTarget = __dragHitTest(srcBox.x + dx, srcBox.y + dy) || el;
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
      try {
        dragDropTarget.dispatchEvent(new global.DragEvent("dragover", endInit));
        dragDropTarget.dispatchEvent(new global.DragEvent("drop", endInit));
      } catch (e) {}
      try {
        el.dispatchEvent(new global.DragEvent("dragend", endInit));
      } catch (e) {}
    }
    return true;
  }

  global.__dragSelector = function (selector, dx, dy, steps, trajectory, stealth) {
    var el = null;
    if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
    else el = global.document.querySelector(selector);
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
    if (form && typeof form.submit === "function") { try { form.submit(); } catch (e) {} }
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
})(this);
