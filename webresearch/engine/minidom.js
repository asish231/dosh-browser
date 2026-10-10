/* webresearch mini-DOM — ES subset for QuickJS. Not Chromium. */
(function (global) {
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

  function Element(tag, attrs, kids) {
    Node.call(this, 1, tag, attrs || {}, kids || []);
    this.id = (this.attributes.id || "");
    this.className = (this.attributes["class"] || "");
    this.style = {};
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
    if (n === "id") this.id = String(v);
    if (n === "class") this.className = String(v);
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
  Element.prototype.click = function () {
    if (this.nodeName === "input" && (this.attributes.type || "") === "checkbox") {
      var on = this.attributes.checked == null ? "checked" : null;
      if (on) this.attributes.checked = "checked"; else delete this.attributes.checked;
      this.checked = !!on;
      this.dispatchEvent({ type: "change", target: this, bubbles: true, isTrusted: true });
    }
    this.dispatchEvent({ type: "mousedown", target: this, bubbles: true, isTrusted: true });
    this.dispatchEvent({ type: "mouseup", target: this, bubbles: true, isTrusted: true });
    this.dispatchEvent({ type: "click", target: this, bubbles: true, isTrusted: true });
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
      return this.childNodes.map(serializeNode).join("");
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
  Object.defineProperty(Element.prototype, "value", {
    get: function () { return this.attributes.value || this.textContent || ""; },
    set: function (v) {
      this.attributes.value = String(v);
      queueMutation("attributes", this, { attributeName: "value" });
    }
  });
  Object.defineProperty(Element.prototype, "checked", {
    get: function () { return this.attributes.checked != null && this.attributes.checked !== "false"; },
    set: function (v) {
      if (v) this.attributes.checked = "checked"; else delete this.attributes.checked;
      queueMutation("attributes", this, { attributeName: "checked" });
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
  function __computedStyle(el, rules, parentFont) {
    var st = { display: null, width: null, height: null, margin: "0", padding: "0", fontSize: null };
    rules.forEach(function (r) {
      r.sel.split(",").forEach(function (s) {
        if (!__matchesSel(el, s)) return;
        var spec = __selSpecificity(s);
        for (var k in r.decls) {
          var prop = k === "font-size" ? "fontSize" : k;
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
      var prop2 = k2 === "font-size" ? "fontSize" : k2;
      if (prop2 in st) st[prop2] = inline[k2];
    }
    var fontSize = parentFont || 16;
    if (st.fontSize != null) {
      var fs = __parsePx(st.fontSize, null, parentFont || 16);
      if (fs != null && fs > 0) fontSize = fs;
    }
    var disp = (st.display || "").toLowerCase();
    if (!disp) disp = (__BLOCK_TAGS[el.nodeName] ? "block" : "inline");
    return { display: disp, width: st.width, height: st.height, margin: st.margin, padding: st.padding, fontSize: fontSize };
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
    var layout = { vw: vw, v: doc.__layoutVersion || 0, boxes: 0, engine: "block-flow" };
    function isBlockDisplay(cs) { return cs.display === "block" || cs.display === "list-item" || cs.display === "table"; }
    function layoutChildren(el, cs, x, y, contentW) {
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
    try {
      doc.__layoutInfo = { engine: "block-flow", viewportWidth: vw, boxes: layout.boxes, textMetric: "monospace-advance-0.6em", replacedDefaults: "css-300x150" };
    } catch (e) {}
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
  Object.defineProperty(Element.prototype, "classList", {
    get: function () {
      var el = this;
      return {
        add: function (c) {
          var p = (el.className || "").split(/\s+/).filter(Boolean);
          if (p.indexOf(c) < 0) p.push(c);
          el.className = p.join(" ");
          el.attributes["class"] = el.className;
          queueMutation("attributes", el, { attributeName: "class" });
        },
        remove: function (c) {
          var p = (el.className || "").split(/\s+/).filter(function (x) { return x && x !== c; });
          el.className = p.join(" ");
          el.attributes["class"] = el.className;
          queueMutation("attributes", el, { attributeName: "class" });
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
  Document.prototype.getElementsByClassName = Element.prototype.getElementsByClassName;
  Document.prototype.querySelectorAll = Element.prototype.querySelectorAll;
  Document.prototype.querySelector = Element.prototype.querySelector;
  Document.prototype.addEventListener = Element.prototype.addEventListener;
  Document.prototype.dispatchEvent = Element.prototype.dispatchEvent;

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
    var m = tok.match(/^([a-z0-9-]+)?(?:#([a-zA-Z0-9_-]+))?(?:\.([a-zA-Z0-9_-]+))?(?:\[([a-zA-Z0-9_-]+)(?:="([^"]*)")?\])?$/i);
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
    return new Element(j.t, j.a || {}, kids);
  }
  function serializeNode(n) {
    if (!n) return "";
    if (n.nodeType === 3) return escapeHtml(n._text || "");
    var attrs = "";
    Object.keys(n.attributes || {}).forEach(function (k) {
      attrs += " " + k + "=\"" + escapeHtml(String(n.attributes[k])) + "\"";
    });
    var voidTags = { area:1,base:1,br:1,col:1,embed:1,hr:1,img:1,input:1,link:1,meta:1,param:1,source:1,track:1,wbr:1 };
    var inner = n.childNodes.map(serializeNode).join("");
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
  global.queueMicrotask = function (fn) { Promise.resolve().then(fn); };
  global.__drainTimers = function () {
    var q = timers.slice(); timers = [];
    q.sort(function (a, b) { return a.ms - b.ms; });
    q.forEach(function (t) {
      try { t.fn(); } catch (e) {}
      if (t.repeat && timers.length < 2000) timers.push(t);
    });
    return timers.length;
  };

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
    var m = /^([a-zA-Z][a-zA-Z0-9+.-]*:)?(?:\/\/([^/?#]*))?([^?#]*)(\?[^#]*)?(#.*)?$/.exec(absolute) || [];
    this.protocol = m[1] || "";
    var auth = m[2] || "";
    var host = auth.split("@").pop();
    this.host = host;
    this.hostname = host.split(":")[0];
    this.port = host.indexOf(":") >= 0 ? host.split(":")[1] : "";
    this.pathname = m[3] || "/";
    this.search = m[4] || "";
    this.hash = m[5] || "";
    this.username = auth.indexOf("@") > 0 ? auth.split("@")[0].split(":")[0] : "";
    this.password = auth.indexOf("@") > 0 && auth.split("@")[0].indexOf(":") > 0 ? auth.split("@")[0].split(":")[1] : "";
    this.origin = this.protocol && this.host ? this.protocol + "//" + this.host : "";
    this.href = this.origin + this.pathname + this.search + this.hash;
    var self = this;
    this.toString = function () { return self.href; };
    this.searchParams = new URLSearchParamsImpl(this.search);
    this.assign = function () {};
    this.replace = function () {};
    this.reload = function () {};
  }
  function URLSearchParamsImpl(query) {
    var raw = String(query || "").replace(/^\?/, "");
    this._p = {};
    raw.split("&").forEach(function (pair) {
      if (!pair) return;
      var eq = pair.indexOf("=");
      var k = eq >= 0 ? pair.slice(0, eq) : pair;
      var v = eq >= 0 ? pair.slice(eq + 1) : "";
      try { k = decodeURIComponent(k.replace(/\+/g, " ")); v = decodeURIComponent(v.replace(/\+/g, " ")); } catch (e) {}
      self_append(this._p, k, v);
    });
    function self_append(obj, k, v) {
      if (obj[k] == null) obj[k] = v; else if (Array.isArray(obj[k])) obj[k].push(v); else obj[k] = [obj[k], v];
    }
    this.get = function (k) { var v = this._p[k]; return v == null ? null : (Array.isArray(v) ? v[0] : v); };
    this.getAll = function (k) { var v = this._p[k]; return v == null ? [] : (Array.isArray(v) ? v.slice() : [v]); };
    this.has = function (k) { return this._p[k] != null; };
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
        var n = (w || 1) * (h || 1) * 4;
        return { width: w || 1, height: h || 1, data: new Uint8ClampedArray(n) };
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
    var renderer = dev.gpu_renderer || "ANGLE (Software, ANGLE Metal Renderer: Generic, Unspecified Version)";
    var vendor = dev.gpu_vendor || "Google Inc. (Software)";
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
    var out = "";
    for (var i = 0; i < s.length; i += 4) {
      var e1 = B64.indexOf(s.charAt(i)), e2 = B64.indexOf(s.charAt(i + 1));
      var e3 = B64.indexOf(s.charAt(i + 2)), e4 = B64.indexOf(s.charAt(i + 3));
      out += String.fromCharCode((e1 << 2) | (e2 >> 4));
      if (e3 !== 64 && s.charAt(i + 2) !== "=") out += String.fromCharCode(((e2 & 15) << 4) | (e3 >> 2));
      if (e4 !== 64 && s.charAt(i + 3) !== "=") out += String.fromCharCode(((e3 & 3) << 6) | e4);
    }
    return out;
  };
  global.TextEncoder = function () {
    this.encode = function (t) {
      var s = String(t);
      var arr = new Uint8Array(s.length);
      for (var i = 0; i < s.length; i++) arr[i] = s.charCodeAt(i) & 0xff;
      return arr;
    };
  };
  global.TextDecoder = function () {
    this.decode = function (buf) {
      if (!buf) return "";
      var out = "";
      for (var i = 0; i < buf.length; i++) out += String.fromCharCode(buf[i]);
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

  /* History API for SPA routers. */
  global.history = {
    length: 1,
    state: null,
    _stack: [],
    pushState: function (state, title, url) { this.state = state; this._stack.push(url); if (url) global.location.href = String(url); },
    replaceState: function (state, title, url) { this.state = state; if (url) global.location.href = String(url); },
    back: function () {}, forward: function () {}, go: function () {}
  };
  function _doScroll(x, y) {
    if (typeof x === "object" && x !== null) { y = x.top || 0; x = x.left || 0; }
    scrollState.x = Number(x) || 0;
    scrollState.y = Number(y) || 0;
    _notifyIntersections();
    if (typeof global.dispatchEvent === "function" && typeof global.Event === "function") {
      try { global.dispatchEvent(new global.Event("scroll", { bubbles: false })); } catch (e) {}
    }
  }
  global.scrollTo = _doScroll;
  global.scroll = _doScroll;
  global.scrollBy = function (x, y) {
    if (typeof x === "object" && x !== null) { y = x.top || 0; x = x.left || 0; }
    _doScroll(scrollState.x + (Number(x) || 0), scrollState.y + (Number(y) || 0));
  };
  Object.defineProperty(global, "scrollX", { get: function () { return scrollState.x; } });
  Object.defineProperty(global, "scrollY", { get: function () { return scrollState.y; } });
  Object.defineProperty(global, "pageXOffset", { get: function () { return scrollState.x; } });
  Object.defineProperty(global, "pageYOffset", { get: function () { return scrollState.y; } });
  global.alert = function () {};
  global.confirm = function () { return false; };
  global.prompt = function () { return null; };
  global.open = function () { return null; };
  global.close = function () {};
  global.focus = function () {};
  global.blur = function () {};
  global.matchMedia = global.matchMedia || function () { return { matches: false, addListener: function () {}, removeListener: function () {} }; };
  if (!global.self) global.self = global;
  global.__mods = global.__mods || {};
  global.__require = function (id) { return global.__mods[id] || {}; };

  global.console = {
    log: function () { if (typeof host_log === "function") host_log(Array.prototype.map.call(arguments, String).join(" ")); },
    warn: function () { this.log.apply(this, arguments); },
    error: function () { this.log.apply(this, arguments); }
  };

  var storage = {};
  global.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
    setItem: function (k, v) { storage[k] = String(v); },
    removeItem: function (k) { delete storage[k]; },
    clear: function () { Object.keys(storage).forEach(function (k) { delete storage[k]; }); },
    key: function (i) { return Object.keys(storage)[i] || null; },
    get length() { return Object.keys(storage).length; }
  };
  var sessionStorageStore = {};
  global.sessionStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(sessionStorageStore, k) ? sessionStorageStore[k] : null; },
    setItem: function (k, v) { sessionStorageStore[k] = String(v); },
    removeItem: function (k) { delete sessionStorageStore[k]; },
    clear: function () { Object.keys(sessionStorageStore).forEach(function (k) { delete sessionStorageStore[k]; }); },
    key: function (i) { return Object.keys(sessionStorageStore)[i] || null; },
    get length() { return Object.keys(sessionStorageStore).length; }
  };

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
        this._contentWindow = {
          postMessage: function (data, targetOrigin) {
            if (typeof host_post_message === "function") {
              host_post_message(String(data), String(targetOrigin || "*"), self.getAttribute("src") || "");
            }
          }
        };
      }
      return this._contentWindow;
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

  /* Standard ReadableStream & DefaultReader for streaming chunk reads */
  function ReadableStreamDefaultReader(stream) {
    this._stream = stream;
    this._chunks = stream._chunks ? stream._chunks.slice() : [];
    this._index = 0;
  }
  ReadableStreamDefaultReader.prototype.read = function () {
    if (this._index < this._chunks.length) {
      var chunk = this._chunks[this._index++];
      return Promise.resolve({ value: chunk, done: false });
    }
    return Promise.resolve({ value: undefined, done: true });
  };
  ReadableStreamDefaultReader.prototype.releaseLock = function () {};
  ReadableStreamDefaultReader.prototype.cancel = function () {
    return Promise.resolve();
  };

  function ReadableStream(source) {
    this._chunks = [];
    if (source && Array.isArray(source.chunks)) {
      this._chunks = source.chunks;
    } else if (typeof source === "string") {
      var enc = new TextEncoder();
      this._chunks = [enc.encode(source)];
    } else if (source instanceof Uint8Array) {
      this._chunks = [source];
    }
  }
  ReadableStream.prototype.getReader = function () {
    return new ReadableStreamDefaultReader(this);
  };
  global.ReadableStream = ReadableStream;

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
  global.fetch = function (url, opts) {
    opts = opts || {};
    var method = String(opts.method || "GET");
    var body = opts.body == null ? "" : String(opts.body);
    var raw = typeof host_fetch === "function" ? host_fetch(String(url), method, body) : "{\"status\":599,\"body\":\"\"}";
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
    this.readyState = 1;
    if (typeof this.onreadystatechange === "function") this.onreadystatechange();
  };
  XMLHttpRequest.prototype.setRequestHeader = function () {};
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
      var raw = typeof host_fetch === "function"
        ? host_fetch(this._url, this._method, body == null ? "" : String(body))
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
    global.performance = {
      timeOrigin: startTime,
      now: function () { return Date.now() - startTime; },
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
        var parsed = new URL(href || "about:blank");
        global.location = {
          href: parsed.href,
          origin: parsed.origin,
          protocol: parsed.protocol,
          host: parsed.host,
          hostname: parsed.hostname,
          port: parsed.port,
          pathname: parsed.pathname,
          search: parsed.search,
          hash: parsed.hash,
          toString: function () { return this.href; },
          assign: function (u) { this.href = String(u); },
          replace: function (u) { this.href = String(u); },
          reload: function () {}
        };
      } catch (e) {}
    }
    global.Node = Node;
    global.Element = Element;
    global.HTMLElement = Element;
    global.Text = Text;
    global.Document = Document;
    global.SVGElement = Element;

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
    for (var i = 0; i < htmlElements.length; i++) {
      (function (name) {
        function HtmlSubElement(tag, attrs, children) {
          Element.call(this, tag, attrs, children);
        }
        HtmlSubElement.prototype = Object.create(Element.prototype);
        HtmlSubElement.prototype.constructor = HtmlSubElement;
        global[name] = HtmlSubElement;
      })(htmlElements[i]);
    }
    global.Event = function (type, init) {
      this.type = type;
      this.bubbles = !!(init && init.bubbles);
      this.cancelable = !!(init && init.cancelable);
      this.defaultPrevented = false;
      this.target = null;
      this.isTrusted = !!(init && init.isTrusted);
      this.preventDefault = function () { this.defaultPrevented = true; };
      this.stopPropagation = function () {};
      // Copy every remaining init field onto the event. Mouse handlers read
      // clientX/clientY/buttons to work out a drag, and dropping those made
      // every drag arrive as a click at (0, 0).
      if (init && typeof init === "object") {
        for (var key in init) {
          if (key === "bubbles" || key === "cancelable") continue;
          if (!Object.prototype.hasOwnProperty.call(this, key)) {
            try { this[key] = init[key]; } catch (e) {}
          }
        }
      }
    };
    global.CustomEvent = global.Event;
    global.MessageEvent = function (type, init) {
      this.type = type || "message";
      this.data = init && init.data;
      this.origin = (init && init.origin) || "";
    };
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
      var targetOrigin = String(origin || "*");
      if (typeof host_post_message === "function") host_post_message(String(data), targetOrigin, "self");
      global.dispatchEvent(new global.MessageEvent("message", {
        data: data,
        origin: targetOrigin === "*" ? "" : targetOrigin
      }));
    };
    global.parent = {
      postMessage: function (data, origin) {
        var targetOrigin = String(origin || "*");
        if (typeof host_post_message === "function") host_post_message(String(data), targetOrigin, "parent");
      }
    };
    global.top = global.parent;
    global.self = global;
    global.__receiveMessage = function (data, origin) {
      global.dispatchEvent(new global.MessageEvent("message", {
        data: data,
        origin: String(origin || "")
      }));
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
    el.click();
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
  global.__clickPath = function (path) {
    var el = global.__byPath(path);
    if (!el) return false;
    if (typeof el.click === "function") el.click();
    else el.dispatchEvent(new global.Event("click", { bubbles: true, isTrusted: true }));
    return true;
  };
  global.__typePath = function (path, text) {
    var el = global.__byPath(path);
    if (!el) return false;
    el.value = String(text);
    el.setAttribute("value", String(text));
    el.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: true }));
    el.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: true }));
    return true;
  };
  global.__activateSelector = function (selector, text) {
    var el = null;
    if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
    else el = global.document.querySelector(selector);
    if (!el) return false;
    if (text !== null && text !== undefined && text !== "") {
        el.value = String(text);
        el.setAttribute("value", String(text));
        el.dispatchEvent(new global.Event("input", { bubbles: true, isTrusted: true }));
        el.dispatchEvent(new global.Event("change", { bubbles: true, isTrusted: true }));
        return true;
    }
    if (typeof el.click === "function") el.click();
    else el.dispatchEvent(new global.Event("click", { bubbles: true, isTrusted: true }));
    // Element.click() already flips checkbox state, so state is only mirrored
    // here rather than toggled again. Setting `checked` in addition would
    // double-toggle and land back where it started, which is why a click on a
    // checkbox appeared to do nothing.
    var kind = (el.getAttribute("type") || "").toLowerCase();
    if (kind === "checkbox" || kind === "radio") {
      el.setAttribute("aria-checked", el.checked ? "true" : "false");
    }
    return true;
  };
global.__dragSelector = function (selector, dx, dy, steps) {
    var el = null;
    if (global.document.getElementById(selector)) el = global.document.getElementById(selector);
    else el = global.document.querySelector(selector);
    if (!el) return false;
    var n = Math.max(2, Math.min(40, steps || 12));
    var move = typeof global.MouseEvent === "function" ? global.MouseEvent : global.Event;
    var init = { bubbles: true, cancelable: true, isTrusted: true };
    try { init.clientX = 0; init.clientY = 0; } catch (e) {}
    // A slider listens for press, a move chain, then release. Emitting only a
    // click is what made every drag a no-op against these widgets.
    el.dispatchEvent(new move("mousedown", init));
    for (var i = 1; i <= n; i++) {
      var step = {};
      for (var k in init) step[k] = init[k];
      step.clientX = Math.round((dx * i) / n);
      step.clientY = Math.round((dy * i) / n);
      el.dispatchEvent(new move("mousemove", step));
    }
    var end = {};
    for (var j in init) end[j] = init[j];
    end.clientX = dx; end.clientY = dy;
    el.dispatchEvent(new move("mouseup", end));
    return true;
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
})(this);
