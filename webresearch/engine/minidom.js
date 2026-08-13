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

  Node.prototype.appendChild = function (child) {
    if (!child) return child;
    if (child.parentNode) child.parentNode.removeChild(child);
    child.parentNode = this;
    this.childNodes.push(child);
    return child;
  };
  Node.prototype.removeChild = function (child) {
    var i = this.childNodes.indexOf(child);
    if (i >= 0) {
      this.childNodes.splice(i, 1);
      child.parentNode = null;
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
      if (this.nodeType === 3) { this._text = String(v); return; }
      this.childNodes = [];
      if (v) this.appendChild(new Text(String(v)));
    }
  });
  Object.defineProperty(Node.prototype, "innerText", {
    get: function () { return this.textContent; },
    set: function (v) { this.textContent = v; }
  });
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
  }
  Element.prototype = Object.create(Node.prototype);
  Element.prototype.constructor = Element;
  Element.prototype.getAttribute = function (n) { return this.attributes[n] == null ? null : String(this.attributes[n]); };
  Element.prototype.setAttribute = function (n, v) {
    this.attributes[n] = String(v);
    if (n === "id") this.id = String(v);
    if (n === "class") this.className = String(v);
  };
  Element.prototype.removeAttribute = function (n) { delete this.attributes[n]; };
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
    var list = (this._listeners[type] || []).slice();
    for (var i = 0; i < list.length; i++) {
      try { list[i].call(this, ev); } catch (e) {}
    }
    if (typeof this["on" + type] === "function") {
      try { this["on" + type](ev); } catch (e) {}
    }
    return true;
  };
  Element.prototype.click = function () {
    if (this.nodeName === "input" && (this.attributes.type || "") === "checkbox") {
      var on = this.attributes.checked == null ? "checked" : null;
      if (on) this.attributes.checked = "checked"; else delete this.attributes.checked;
      this.checked = !!on;
      this.dispatchEvent({ type: "change", target: this });
    }
    this.dispatchEvent({ type: "mousedown", target: this, bubbles: true });
    this.dispatchEvent({ type: "mouseup", target: this, bubbles: true });
    this.dispatchEvent({ type: "click", target: this, bubbles: true });
  };
  Object.defineProperty(Element.prototype, "innerHTML", {
    get: function () {
      return this.childNodes.map(serializeNode).join("");
    },
    set: function (html) {
      var parsed = hydrate(parseHtmlFragment(String(html || "")));
      this.childNodes = [];
      var self = this;
      parsed.forEach(function (c) { self.appendChild(c); });
    }
  });
  Object.defineProperty(Element.prototype, "value", {
    get: function () { return this.attributes.value || this.textContent || ""; },
    set: function (v) { this.attributes.value = String(v); }
  });
  Object.defineProperty(Element.prototype, "checked", {
    get: function () { return this.attributes.checked != null && this.attributes.checked !== "false"; },
    set: function (v) {
      if (v) this.attributes.checked = "checked"; else delete this.attributes.checked;
    }
  });
  Element.prototype.getBoundingClientRect = function () {
    return { x: 0, y: 0, top: 0, left: 0, bottom: 78, right: 304, width: 304, height: 78 };
  };
  Object.defineProperty(Element.prototype, "offsetWidth", { get: function () { return 304; } });
  Object.defineProperty(Element.prototype, "offsetHeight", { get: function () { return 78; } });
  Object.defineProperty(Element.prototype, "clientWidth", { get: function () { return 304; } });
  Object.defineProperty(Element.prototype, "clientHeight", { get: function () { return 78; } });
  Object.defineProperty(Element.prototype, "classList", {
    get: function () {
      var el = this;
      return {
        add: function (c) {
          var p = (el.className || "").split(/\s+/).filter(Boolean);
          if (p.indexOf(c) < 0) p.push(c);
          el.className = p.join(" ");
          el.attributes["class"] = el.className;
        },
        remove: function (c) {
          var p = (el.className || "").split(/\s+/).filter(function (x) { return x && x !== c; });
          el.className = p.join(" ");
          el.attributes["class"] = el.className;
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
  Document.prototype.createElement = function (tag) { return new Element(String(tag).toLowerCase()); };
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
  global.setInterval = global.setTimeout;
  global.clearInterval = function () {};
  global.requestAnimationFrame = function (fn) { return global.setTimeout(fn, 16); };
  global.__drainTimers = function () {
    var q = timers.slice(); timers = [];
    q.sort(function (a, b) { return a.ms - b.ms; });
    q.forEach(function (t) { try { t.fn(); } catch (e) {} });
    return timers.length;
  };

  global.console = {
    log: function () { if (typeof host_log === "function") host_log(Array.prototype.map.call(arguments, String).join(" ")); },
    warn: function () { this.log.apply(this, arguments); },
    error: function () { this.log.apply(this, arguments); }
  };

  var storage = {};
  global.localStorage = {
    getItem: function (k) { return Object.prototype.hasOwnProperty.call(storage, k) ? storage[k] : null; },
    setItem: function (k, v) { storage[k] = String(v); },
    removeItem: function (k) { delete storage[k]; }
  };

  global.fetch = function (url, opts) {
    opts = opts || {};
    var method = String(opts.method || "GET");
    var body = opts.body == null ? "" : String(opts.body);
    var raw = typeof host_fetch === "function" ? host_fetch(String(url), method, body) : "{\"status\":599,\"body\":\"\"}";
    var res = typeof raw === "string" ? JSON.parse(raw) : raw;
    var text = res.body || "";
    return Promise.resolve({
      ok: res.status >= 200 && res.status < 300,
      status: res.status || 0,
      url: res.url || String(url),
      type: "basic",
      text: function () { return Promise.resolve(text); },
      json: function () { return Promise.resolve(JSON.parse(text || "null")); }
    });
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
    doc.defaultView = global;
    global.window = global;
    global.navigator = {
      userAgent: "webresearch/0.2 (QuickJS; mini-DOM)",
      language: "en-US",
      languages: ["en-US", "en"],
      webdriver: false,
      platform: "MacIntel"
    };
    global.location = {
      href: href || "about:blank",
      toString: function () { return this.href; }
    };
    global.Node = Node;
    global.Element = Element;
    global.HTMLElement = Element;
    global.Text = Text;
    global.Document = Document;
    global.Event = function (type, init) {
      this.type = type;
      this.bubbles = !!(init && init.bubbles);
      this.target = null;
    };
    global.MessageEvent = function (type, init) {
      this.type = type || "message";
      this.data = init && init.data;
      this.origin = (init && init.origin) || "";
    };
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
      if (typeof host_post_message === "function") host_post_message(String(data), targetOrigin);
      global.dispatchEvent(new global.MessageEvent("message", {
        data: data,
        origin: targetOrigin === "*" ? "" : targetOrigin
      }));
    };
    global.parent = global;
    global.top = global;
    global.self = global;
    global.frames = [];
    global.innerWidth = 1280;
    global.innerHeight = 720;
    global.atob = function (s) { return s; };
    global.btoa = function (s) { return s; };
    global.TextEncoder = function () { this.encode = function (t) { return t; }; };
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
})(this);
