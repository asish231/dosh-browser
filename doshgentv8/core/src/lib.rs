//! doshgentv8-core — pure-Rust V8 embedding for the dosh-browser engine.
//!
//! No Python in this crate: the host seam is a trait (`HostSeam`), so the
//! future pure-Rust product implements it natively and this crate is
//! consumed unchanged (docs/DOSHGENTV8-MASTER-PLAN.md, Phase 5).
//!
//! The contract mirrors the QuickJS seam in `webresearch/engine/runtime.py`:
//! host callables are synchronous, string-in/string-out; the host drives the
//! microtask pump; limits are host-set. Page JS runs on a real V8.

use std::cell::Cell;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Mutex;
use std::time::Duration;

pub mod actor;
pub mod parena;
pub mod protocol;
pub mod session;
pub mod watchdog;

pub use actor::{DoshActor, DoshActorHandle};
pub use parena::{Checkpoint, PackedAction, Parena, TagId};
pub use session::SessionPool;
pub use watchdog::{arm as arm_watchdog, WatchdogSupervisor, WatchdogToken};

/// Synchronous host seam. `args` are the JS arguments stringified (the same
/// marshalling the QuickJS `add_callable` lane uses). `Ok(Some(s))` becomes a
/// JS string, `Ok(None)` becomes `undefined`, and `Err(msg)` becomes a
/// catchable JS exception — unlike QuickJS's deferred SystemError, which no
/// JS try/catch could intercept.
pub trait HostSeam: Send + Sync {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String>;
}

#[derive(Debug, Clone, PartialEq)]
pub enum JsValue {
    Null,
    Undefined,
    Bool(bool),
    Int(i64),
    Float(f64),
    String(String),
}

impl std::fmt::Display for JsValue {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            JsValue::Null => write!(f, "null"),
            JsValue::Undefined => write!(f, "undefined"),
            JsValue::Bool(b) => write!(f, "{b}"),
            JsValue::Int(i) => write!(f, "{i}"),
            JsValue::Float(n) => write!(f, "{n}"),
            JsValue::String(s) => write!(f, "{s}"),
        }
    }
}

/// Contiguous bidirectional Butterfly storage inspired by JavaScriptCore.
/// Named properties are indexed at negative offsets (-1, -2, ...),
/// while array elements are indexed at non-negative offsets (0, 1, 2, ...).
#[derive(Debug, Clone)]
pub struct ButterflyStorage<T> {
    data: Vec<Option<T>>,
    property_capacity: usize,
    element_count: usize,
}

impl<T: Clone> ButterflyStorage<T> {
    pub fn new(property_capacity: usize, element_capacity: usize) -> Self {
        let total = property_capacity + element_capacity;
        let mut data = Vec::with_capacity(total);
        data.resize(total, None);
        Self {
            data,
            property_capacity,
            element_count: 0,
        }
    }

    /// Access a named property by 1-based negative offset (-1, -2, ...)
    pub fn get_property(&self, offset: usize) -> Option<&T> {
        if offset == 0 || offset > self.property_capacity {
            return None;
        }
        let index = self.property_capacity - offset;
        self.data.get(index).and_then(|x| x.as_ref())
    }

    /// Store a named property by 1-based negative offset (-1, -2, ...)
    pub fn set_property(&mut self, offset: usize, val: T) -> bool {
        if offset == 0 || offset > self.property_capacity {
            return false;
        }
        let index = self.property_capacity - offset;
        self.data[index] = Some(val);
        true
    }

    /// Access an element by non-negative index (0, 1, 2, ...)
    pub fn get_element(&self, index: usize) -> Option<&T> {
        let abs_index = self.property_capacity + index;
        self.data.get(abs_index).and_then(|x| x.as_ref())
    }

    /// Store an element by non-negative index (0, 1, 2, ...)
    pub fn set_element(&mut self, index: usize, val: T) {
        let abs_index = self.property_capacity + index;
        if abs_index >= self.data.len() {
            self.data.resize(abs_index + 1, None);
        }
        self.data[abs_index] = Some(val);
        if index >= self.element_count {
            self.element_count = index + 1;
        }
    }

    pub fn element_count(&self) -> usize {
        self.element_count
    }

    pub fn property_capacity(&self) -> usize {
        self.property_capacity
    }
}

#[allow(dead_code)]
pub(crate) struct HostBridge {
    pub seam: Arc<dyn HostSeam>,
    pub oom: Arc<AtomicBool>,
    pub interrupt_handle: v8::IsolateHandle,
    pub fuel: Arc<AtomicU64>,
    pub fuel_enabled: Arc<AtomicBool>,
    pub eval_allowed: Arc<AtomicBool>,
    pub wasm_allowed: Arc<AtomicBool>,
    pub network_allowed: Arc<AtomicBool>,
}

unsafe extern "C" fn near_heap_limit_callback(
    data: *mut std::ffi::c_void,
    current_heap_limit: usize,
    _initial_heap_limit: usize,
) -> usize {
    if !data.is_null() {
        let bridge = &*(data as *mut HostBridge);
        bridge.oom.store(true, Ordering::SeqCst);
        bridge.interrupt_handle.terminate_execution();
    }
    current_heap_limit + 16 * 1024 * 1024
}

// The bridge pointer is installed for the duration of each eval/pump on the
// calling thread. V8 callbacks only fire during those calls, on the same
// thread, so a thread-local keeps host state out of the isolate (leaving
// V8's embedder data slots free for later phases).
thread_local! {
    static HOST_BRIDGE: Cell<*mut HostBridge> = Cell::new(std::ptr::null_mut());
}

/// The one native function installed in the realm: a dispatcher. Every host
/// callable is a plain JS wrapper over it, so a single FunctionTemplate
/// serves all host names and no template-data API is needed to recover the
/// callee's name:
///   globalThis.host_x = function () {
///     return __dispatchHost.apply(null, ["host_x"].concat(Array.prototype.slice.call(arguments)));
///   };
fn host_dispatch(
    scope: &mut v8::PinScope,
    args: v8::FunctionCallbackArguments,
    mut rv: v8::ReturnValue<v8::Value>,
) {
    let bridge_ptr = HOST_BRIDGE.with(|b| b.get());
    if bridge_ptr.is_null() {
        return; // no eval in flight on this thread; do not guess
    }
    let bridge = unsafe { &*bridge_ptr };
    let argc = args.length();
    if argc < 1 {
        return; // the dispatcher must carry the host name as argument 0
    }
    let name = match args.get(0).to_string(scope) {
        Some(s) => s.to_rust_string_lossy(scope),
        None => return,
    };
    let mut host_args: Vec<String> = Vec::with_capacity((argc as usize).saturating_sub(1));
    for i in 1..argc {
        let s = args
            .get(i)
            .to_string(scope)
            .map(|x| x.to_rust_string_lossy(scope))
            .unwrap_or_default();
        host_args.push(s);
    }

    // Agentic Inventions / WebKit Features handled directly at host seam:
    if name == "__dosh_step" {
        let units: u64 = host_args.get(0).and_then(|s| s.parse().ok()).unwrap_or(1);
        if bridge.fuel_enabled.load(Ordering::SeqCst) {
            let current = bridge.fuel.load(Ordering::SeqCst);
            if current <= units {
                bridge.fuel.store(0, Ordering::SeqCst);
                let m = v8::String::new(scope, "fuel exhausted")
                    .unwrap_or_else(|| v8::String::empty(scope));
                scope.throw_exception(m.into());
                return;
            } else {
                bridge.fuel.store(current - units, Ordering::SeqCst);
            }
        }
        rv.set_undefined();
        return;
    }

    if name == "__dosh_fuel_remaining" {
        let rem = bridge.fuel.load(Ordering::SeqCst);
        let s = v8::String::new(scope, &rem.to_string())
            .unwrap_or_else(|| v8::String::empty(scope));
        rv.set(s.into());
        return;
    }

    // Capability Gate: enforce network restriction on host_fetch / host_ws_*
    if !bridge.network_allowed.load(Ordering::SeqCst)
        && (name.starts_with("host_fetch") || name.starts_with("host_ws_"))
    {
        let m = v8::String::new(scope, "network capability disallowed for this context")
            .unwrap_or_else(|| v8::String::empty(scope));
        scope.throw_exception(m.into());
        return;
    }

    match bridge.seam.call(&name, &host_args) {
        Ok(Some(ret)) => {
            if let Some(s) = v8::String::new(scope, &ret) {
                rv.set(s.into());
            }
        }
        Ok(None) => rv.set_undefined(),
        Err(msg) => {
            let m = v8::String::new(scope, &msg)
                .unwrap_or_else(|| v8::String::empty(scope));
            scope.throw_exception(m.into());
        }
    }
}

const EXTENSIONS_SCRIPT: &str = r#"
(function () {
    // 1. WebKit/JSC Butterfly contiguous memory layout
    globalThis.Butterfly = class Butterfly {
        constructor(keys = [], elements = []) {
            this._keys = Array.from(keys);
            this._props = new Array(this._keys.length);
            this._elements = Array.from(elements);
            this._keyMap = Object.create(null);
            for (let i = 0; i < this._keys.length; i++) {
                this._keyMap[this._keys[i]] = i;
            }
        }
        getProperty(key) {
            const idx = this._keyMap[key];
            return idx !== undefined ? this._props[idx] : undefined;
        }
        setProperty(key, val) {
            let idx = this._keyMap[key];
            if (idx === undefined) {
                idx = this._keys.length;
                this._keys.push(key);
                this._keyMap[key] = idx;
            }
            this._props[idx] = val;
        }
        getElement(index) {
            return this._elements[index];
        }
        setElement(index, val) {
            this._elements[index] = val;
        }
        get length() {
            return this._elements.length;
        }
        push(val) {
            this._elements.push(val);
            return this._elements.length;
        }
        inspect() {
            const props = {};
            for (let i = 0; i < this._keys.length; i++) {
                props[this._keys[i]] = this._props[i];
            }
            return { properties: props, elements: this._elements };
        }
    };
    globalThis.__createButterfly = function(keys, elements) {
        return new globalThis.Butterfly(keys, elements);
    };

    // 2. Fuel metering step helper
    globalThis.__dosh_step = function(n = 1) {
        return globalThis.__dispatchHost("__dosh_step", String(n));
    };

    // 3. Host-Clock Injection (Date.now / performance.now)
    let _virtualTime = null;
    let _timeOrigin = 0;
    const _origDateNow = Date.now;
    const _origPerfNow = typeof performance !== 'undefined' && performance.now ? performance.now.bind(performance) : () => Date.now();

    globalThis.__setVirtualTime = function(t) {
        _virtualTime = Number(t);
        _timeOrigin = _virtualTime;
    };
    globalThis.__advanceVirtualTime = function(dt) {
        if (_virtualTime !== null) {
            _virtualTime += Number(dt);
        }
    };
    globalThis.__getVirtualTime = function() {
        return _virtualTime;
    };
    Date.now = function() {
        return _virtualTime !== null ? _virtualTime : _origDateNow();
    };
    if (typeof performance !== 'undefined') {
        performance.now = function() {
            return _virtualTime !== null ? (_virtualTime - _timeOrigin) : _origPerfNow();
        };
    }

    // 4. Seeded PRNG (XorShift128+)
    let _seeded = false;
    let _s0 = 0n, _s1 = 0n;
    const _origRandom = Math.random;

    globalThis.__setRandomSeed = function(seed) {
        _seeded = true;
        let s = BigInt(seed);
        if (s === 0n) s = 1n;
        function splitmix64() {
            s = (s + 0x9e3779b97f4a7c15n) & 0xffffffffffffffffn;
            let z = s;
            z = ((z ^ (z >> 30n)) * 0xbf58476d1ce4e5b9n) & 0xffffffffffffffffn;
            z = ((z ^ (z >> 27n)) * 0x94d049bb133111ebn) & 0xffffffffffffffffn;
            return (z ^ (z >> 31n)) & 0xffffffffffffffffn;
        }
        _s0 = splitmix64();
        _s1 = splitmix64();
    };
    globalThis.__resetRandom = function() {
        _seeded = false;
    };
    Math.random = function() {
        if (!_seeded) return _origRandom();
        let s1 = _s0;
        const s0 = _s1;
        _s0 = s0;
        s1 = (s1 ^ (s1 << 23n)) & 0xffffffffffffffffn;
        _s1 = (s1 ^ s0 ^ (s1 >> 17n) ^ (s0 >> 26n)) & 0xffffffffffffffffn;
        const sum = (_s1 + s0) & 0xffffffffffffffffn;
        return Number(sum >> 11n) / 9007199254740992;
    };

    // 5. Capability Gates: eval & wasm & network & waasm
    let _evalAllowed = true;
    let _wasmAllowed = true;
    let _waasmCaps = {
        read_dom: true,
        act: true,
        emit: true,
        net_query: true,
        log: true,
        snapshot: true,
    };
    let _origWebAssembly = typeof WebAssembly !== 'undefined' ? WebAssembly : undefined;

    globalThis.__setCapabilityGate = function(cap, allowed) {
        if (cap === 'eval') _evalAllowed = !!allowed;
        if (cap === 'wasm') {
            _wasmAllowed = !!allowed;
            if (!_wasmAllowed) {
                try { delete globalThis.WebAssembly; } catch (e) {}
            } else if (_origWebAssembly) {
                globalThis.WebAssembly = _origWebAssembly;
            }
        }
        if (cap === 'action') _actionAllowed = !!allowed;
        if (typeof cap === 'string' && cap.indexOf('waasm.') === 0) {
            const sub = cap.slice(6);
            if (sub in _waasmCaps) _waasmCaps[sub] = !!allowed;
        }
    };
    globalThis.__getCapabilityGate = function(cap) {
        if (cap === 'eval') return _evalAllowed;
        if (cap === 'wasm') return _wasmAllowed;
        if (cap === 'action') return _actionAllowed;
        if (typeof cap === 'string' && cap.indexOf('waasm.') === 0) {
            const sub = cap.slice(6);
            return sub in _waasmCaps ? _waasmCaps[sub] : false;
        }
        return true;
    };

    // WAASM Host Namespace for Agent-Native WebAssembly
    globalThis.waasm = {
        _lastMemory: null,
        setMemory: function(mem) { this._lastMemory = mem; },
        getBufferView: function(ptr, len) {
            if (!this._lastMemory || !this._lastMemory.buffer) return null;
            return new Uint8Array(this._lastMemory.buffer, ptr, len);
        },
        read_dom: function(spec_ptr, spec_len) {
            if (!_waasmCaps.read_dom) throw new WebAssembly.LinkError("WAASM capability disallowed: read_dom");
            if (typeof globalThis.host_waasm_read_dom === 'function') {
                return globalThis.host_waasm_read_dom(spec_ptr, spec_len);
            }
            return 0;
        },
        act: function(actions_ptr, actions_len) {
            if (!_waasmCaps.act) throw new WebAssembly.LinkError("WAASM capability disallowed: act");
            if (typeof globalThis.host_waasm_act === 'function') {
                return globalThis.host_waasm_act(actions_ptr, actions_len);
            }
            return 0;
        },
        emit: function(k_ptr, k_len, v_ptr, v_len) {
            if (!_waasmCaps.emit) throw new WebAssembly.LinkError("WAASM capability disallowed: emit");
            if (typeof globalThis.host_waasm_emit === 'function') {
                return globalThis.host_waasm_emit(k_ptr, k_len, v_ptr, v_len);
            }
            return 0;
        },
        net_query: function(query_ptr, query_len) {
            if (!_waasmCaps.net_query) throw new WebAssembly.LinkError("WAASM capability disallowed: net_query");
            if (typeof globalThis.host_waasm_net_query === 'function') {
                return globalThis.host_waasm_net_query(query_ptr, query_len);
            }
            return 0;
        },
        log: function(level, msg_ptr, msg_len) {
            if (!_waasmCaps.log) throw new WebAssembly.LinkError("WAASM capability disallowed: log");
            if (typeof globalThis.host_waasm_log === 'function') {
                return globalThis.host_waasm_log(level, msg_ptr, msg_len);
            }
            return 0;
        },
        snapshot: function(kind) {
            if (!_waasmCaps.snapshot) throw new WebAssembly.LinkError("WAASM capability disallowed: snapshot");
            if (typeof globalThis.host_waasm_snapshot === 'function') {
                return globalThis.host_waasm_snapshot(kind);
            }
            return 0;
        }
    };

    if (_origWebAssembly && _origWebAssembly.instantiate) {
        const _v8Instantiate = _origWebAssembly.instantiate;
        _origWebAssembly.instantiate = function(source, imports) {
            imports = imports || {};
            if (source instanceof _origWebAssembly.Module) {
                const capSections = _origWebAssembly.Module.customSections(source, "waasm.capabilities");
                if (capSections && capSections.length > 0) {
                    try {
                        const dec = new TextDecoder();
                        const capJson = JSON.parse(dec.decode(capSections[0]));
                        if (Array.isArray(capJson)) {
                            for (const c of capJson) {
                                if (!globalThis.__getCapabilityGate('waasm.' + c)) {
                                    return Promise.reject(new WebAssembly.LinkError("WAASM capability disallowed: " + c));
                                }
                            }
                        }
                    } catch (e) {
                        if (e instanceof WebAssembly.LinkError) return Promise.reject(e);
                    }
                }
            }
            if (!imports.waasm) {
                imports.waasm = globalThis.waasm;
            } else {
                for (const k in imports.waasm) {
                    if (!globalThis.__getCapabilityGate('waasm.' + k)) {
                        return Promise.reject(new WebAssembly.LinkError("WAASM capability disallowed: " + k));
                    }
                }
            }
            return _v8Instantiate.call(this, source, imports).then(function(res) {
                const inst = (res && res.instance) ? res.instance : res;
                if (inst && inst.exports && inst.exports.memory) {
                    globalThis.waasm.setMemory(inst.exports.memory);
                }
                return res;
            });
        };
    }

    if (_origWebAssembly && _origWebAssembly.Instance) {
        const _v8Instance = _origWebAssembly.Instance;
        _origWebAssembly.Instance = function(module, imports) {
            imports = imports || {};
            const capSections = _origWebAssembly.Module.customSections(module, "waasm.capabilities");
            if (capSections && capSections.length > 0) {
                try {
                    const dec = new TextDecoder();
                    const capJson = JSON.parse(dec.decode(capSections[0]));
                    if (Array.isArray(capJson)) {
                        for (const c of capJson) {
                            if (!globalThis.__getCapabilityGate('waasm.' + c)) {
                                throw new WebAssembly.LinkError("WAASM capability disallowed: " + c);
                            }
                        }
                    }
                } catch (e) {
                    if (e instanceof WebAssembly.LinkError) throw e;
                }
            }
            if (!imports.waasm) {
                imports.waasm = globalThis.waasm;
            } else {
                for (const k in imports.waasm) {
                    if (!globalThis.__getCapabilityGate('waasm.' + k)) {
                        throw new WebAssembly.LinkError("WAASM capability disallowed: " + k);
                    }
                }
            }
            const inst = new _v8Instance(module, imports);
            if (inst && inst.exports && inst.exports.memory) {
                globalThis.waasm.setMemory(inst.exports.memory);
            }
            return inst;
        };
        _origWebAssembly.Instance.prototype = _v8Instance.prototype;
    }

    const _origEval = globalThis.eval;
    globalThis.eval = function(code) {
        if (!_evalAllowed) {
            throw new EvalError("Code generation from strings disallowed for this context");
        }
        return _origEval.call(this, code);
    };

    const _origFunction = globalThis.Function;
    globalThis.Function = function(...args) {
        if (!_evalAllowed) {
            throw new EvalError("Code generation from strings disallowed for this context");
        }
        return _origFunction.apply(this, args);
    };
    globalThis.Function.prototype = _origFunction.prototype;

    // 6. Proper Tail Calls (PTC / TCO Trampoline)
    globalThis.__tco = function(fn) {
        return function(...args) {
            let result = fn(...args);
            while (typeof result === 'function' && result.__tco_thunk) {
                result = result();
            }
            return result;
        };
    };
    globalThis.__thunk = function(fn, ...args) {
        const thunk = () => fn(...args);
        thunk.__tco_thunk = true;
        return thunk;
    };

    // 7. dosh.* Agent Bridge & Control Realm
    let _controlRealm = false;
    let _actionAllowed = true;

    globalThis.__setControlRealm = function(enabled) {
        _controlRealm = Boolean(enabled);
    };
    globalThis.__isControlRealm = function() {
        return _controlRealm;
    };

    globalThis.dosh = {
        args: [],
        call: function(name, ...args) {
            return globalThis.__dispatchHost.apply(null, [name].concat(args));
        },
        act: function(action) {
            if (!_actionAllowed) {
                throw new Error("Action authority disallowed: mutations are blocked in read-only mode");
            }
            if (typeof globalThis.__applyAction === 'function') {
                return globalThis.__applyAction(action);
            }
            return globalThis.__dispatchHost("host_action", JSON.stringify(action));
        },
        emit: function(event, data) {
            return globalThis.__dispatchHost("host_emit", event, typeof data === 'string' ? data : JSON.stringify(data));
        },
        text: function() {
            if (typeof document !== 'undefined' && document.body) {
                return document.body.innerText || document.body.textContent || "";
            }
            return "";
        },
        actions: function() {
            if (typeof globalThis.__extractActions === 'function') {
                return globalThis.__extractActions();
            }
            return [];
        }
    };
})();
"#;

struct ReentrancyGuard {
    flag: Arc<AtomicBool>,
}

impl ReentrancyGuard {
    fn enter(flag: &Arc<AtomicBool>) -> Result<Self, String> {
        if flag.swap(true, Ordering::SeqCst) {
            return Err("re-entrancy error: nested eval while isolate is already entered".to_string());
        }
        Ok(Self {
            flag: flag.clone(),
        })
    }
}

impl Drop for ReentrancyGuard {
    fn drop(&mut self) {
        self.flag.store(false, Ordering::SeqCst);
    }
}

/// A V8 isolate + context pair, host-driven: microtasks are explicit and the
/// pump is `pump_jobs()`. One per browser context, like QuickJS `Context`.
pub struct DoshEngine {
    // Declared first: Global<Context> must drop while the isolate exists
    // (rusty_v8's Global Drop is also isolate-liveness aware, verified).
    context: v8::Global<v8::Context>,
    isolate: v8::OwnedIsolate,
    bridge: *mut HostBridge,
    interrupt_handle: v8::IsolateHandle,
    is_evaluating: Arc<AtomicBool>,
    wall_limit: f64,
    memory_limit: usize,
    oom: Arc<AtomicBool>,
    eval_count: usize,
    fuel: Arc<AtomicU64>,
    fuel_enabled: Arc<AtomicBool>,
    virtual_time: Arc<Mutex<Option<f64>>>,
    eval_allowed: Arc<AtomicBool>,
    wasm_allowed: Arc<AtomicBool>,
    network_allowed: Arc<AtomicBool>,
    action_allowed: Arc<AtomicBool>,
    control_realm: Arc<AtomicBool>,
    layout_buffer: Vec<protocol::DoshButterflyRect>,
    pub telemetry_ring: protocol::DoshTelemetryRing,
    pub parena: Option<parena::Parena>,
    pub checkpoints: HashMap<u64, parena::Checkpoint>,
}

impl DoshEngine {
    pub fn new(seam: Arc<dyn HostSeam>) -> Result<Self, String> {
        Self::new_with_limit(seam, 0)
    }

    pub fn new_with_limit(seam: Arc<dyn HostSeam>, memory_limit: usize) -> Result<Self, String> {
        static V8_INIT: std::sync::Once = std::sync::Once::new();
        V8_INIT.call_once(|| {
            // Prune unneeded V8 subsystems / lean JIT tiering for agent execution:
            // --no-opt: Prune heavy TurboFan optimizing compiler and SSA graphs
            // --no-maglev: Prune mid-tier Maglev compiler
            // --max-semi-space-size=1: Shrink young generation semi-space to 1MB (cuts memory bloat)
            // --stack-trace-limit=10: Limit stack trace overhead
            let flags = std::env::var("WR_V8_FLAGS").unwrap_or_else(|_| {
                "--no-opt --no-maglev --max-semi-space-size=1 --stack-trace-limit=10".to_string()
            });
            v8::V8::set_flags_from_string(&flags);
            let platform = v8::new_default_platform(0, false).make_shared();
            v8::V8::initialize_platform(platform);
            v8::V8::initialize();
        });

        let mut params = v8::CreateParams::default();
        if memory_limit > 0 {
            params = params.heap_limits(0, memory_limit);
        }
        let mut isolate = v8::Isolate::new(params);
        let interrupt_handle = isolate.thread_safe_handle();
        let oom = Arc::new(AtomicBool::new(false));

        let fuel = Arc::new(AtomicU64::new(0));
        let fuel_enabled = Arc::new(AtomicBool::new(false));
        let eval_allowed = Arc::new(AtomicBool::new(true));
        let wasm_allowed = Arc::new(AtomicBool::new(true));
        let network_allowed = Arc::new(AtomicBool::new(true));
        let action_allowed = Arc::new(AtomicBool::new(true));
        let control_realm = Arc::new(AtomicBool::new(false));
        let virtual_time = Arc::new(Mutex::new(None));

        let bridge = Box::into_raw(Box::new(HostBridge {
            seam,
            oom: oom.clone(),
            interrupt_handle: interrupt_handle.clone(),
            fuel: fuel.clone(),
            fuel_enabled: fuel_enabled.clone(),
            eval_allowed: eval_allowed.clone(),
            wasm_allowed: wasm_allowed.clone(),
            network_allowed: network_allowed.clone(),
        }));

        isolate.add_near_heap_limit_callback(near_heap_limit_callback, bridge as *mut std::ffi::c_void);
        // The host drives the job pump, matching the QuickJS contract: an
        // eval runs to completion, then the host calls pump_jobs.
        isolate.set_microtasks_policy(v8::MicrotasksPolicy::Explicit);
        isolate.set_capture_stack_trace_for_uncaught_exceptions(true, 10);

        let context_global;
        {
            v8::scope!(scope, &mut isolate);
            let context = v8::Context::new(scope, Default::default());
            let scope = &mut v8::ContextScope::new(scope, context);
            let global = context.global(scope);
            let tmpl = v8::FunctionTemplate::new(scope, host_dispatch);
            let func = tmpl
                .get_function(scope)
                .ok_or("FunctionTemplate::get_function failed")?;
            let key = v8::String::new(scope, "__dispatchHost").ok_or("v8 string alloc")?;
            if global.set(scope, key.into(), func.into()) != Some(true) {
                return Err("could not install __dispatchHost".into());
            }

            // Run context extensions (Butterfly, Clock, Seeded PRNG, Capability gates, PTC)
            let ext_src = v8::String::new(scope, EXTENSIONS_SCRIPT).ok_or("extensions alloc failed")?;
            if let Some(script) = v8::Script::compile(scope, ext_src, None) {
                script.run(scope);
            }

            context_global = v8::Global::new(scope, context);
        }

        unsafe {
            isolate.exit();
        }

        Ok(Self {
            context: context_global,
            isolate,
            bridge,
            interrupt_handle,
            is_evaluating: Arc::new(AtomicBool::new(false)),
            wall_limit: 0.0,
            memory_limit,
            oom,
            eval_count: 0,
            fuel,
            fuel_enabled,
            virtual_time,
            eval_allowed,
            wasm_allowed,
            network_allowed,
            action_allowed,
            control_realm,
            layout_buffer: Vec::new(),
            telemetry_ring: protocol::DoshTelemetryRing::new(),
            parena: None,
            checkpoints: HashMap::new(),
        })
    }

    /// Register a host callable in the realm as a JS wrapper over the
    /// dispatcher. Names are plain `A-Za-z0-9_` (the engine's are `host_*`).
    pub fn add_host(&mut self, name: &str) -> Result<(), String> {
        if name.is_empty() || !name.chars().all(|c| c.is_ascii_alphanumeric() || c == '_') {
            return Err(format!("invalid host callable name: {name:?}"));
        }
        // Rust's Debug formatting of a str yields a JSON-safe quoted literal
        // for these ASCII names; used as a JS string literal below.
        let quoted = format!("{name:?}");
        let code = format!(
            "globalThis[{q}] = function () {{ return globalThis.__dispatchHost.apply(null, [{q}].concat(Array.prototype.slice.call(arguments))); }}; true",
            q = quoted
        );
        self.eval(&code).map(|_| ())
    }

    /// Evaluate JS and return the result stringified (the `quickjs.Context`
    /// eval contract). Microtasks stay pending until `pump_jobs`.
    pub fn eval(&mut self, code: &str) -> Result<String, String> {
        self.eval_raw(code).map(|v| match v {
            JsValue::Undefined => String::new(),
            _ => v.to_string(),
        })
    }

    /// Evaluate JS and return the raw typed `JsValue`.
    pub fn eval_raw(&mut self, code: &str) -> Result<JsValue, String> {
        let _guard = ReentrancyGuard::enter(&self.is_evaluating)?;
        self.isolate.cancel_terminate_execution();

        // Process-wide watchdog deadline, only armed when a limit is set.
        let _watchdog = if self.wall_limit > 0.0 {
            Some(watchdog::arm(
                self.interrupt_handle.clone(),
                Duration::from_secs_f64(self.wall_limit),
            ))
        } else {
            None
        };

        HOST_BRIDGE.with(|b| b.set(self.bridge));
        unsafe {
            self.isolate.enter();
        }
        let out = self.eval_inner_raw(code);
        unsafe {
            self.isolate.exit();
        }
        HOST_BRIDGE.with(|b| b.set(std::ptr::null_mut()));

        self.eval_count += 1;
        out
    }

    fn eval_inner_raw(&mut self, code: &str) -> Result<JsValue, String> {
        v8::scope_with_context!(scope, &mut self.isolate, &self.context);
        let source = v8::String::new(scope, code).ok_or("source alloc failed")?;
        v8::tc_scope!(scope, scope);
        let script = match v8::Script::compile(scope, source, None) {
            Some(s) => s,
            None => {
                let msg = scope
                    .exception()
                    .and_then(|e| e.to_string(scope))
                    .map(|s| s.to_rust_string_lossy(scope))
                    .unwrap_or_else(|| "SyntaxError".into());
                return Err(msg);
            }
        };
        match script.run(scope) {
            Some(v) => {
                if v.is_null() {
                    Ok(JsValue::Null)
                } else if v.is_undefined() {
                    Ok(JsValue::Undefined)
                } else if v.is_boolean() {
                    Ok(JsValue::Bool(v.is_true()))
                } else if v.is_int32() {
                    Ok(JsValue::Int(v.int32_value(scope).unwrap_or(0) as i64))
                } else if v.is_number() {
                    let n = v.number_value(scope).unwrap_or(0.0);
                    if n.fract() == 0.0 && n >= (i64::MIN as f64) && n <= (i64::MAX as f64) {
                        Ok(JsValue::Int(n as i64))
                    } else {
                        Ok(JsValue::Float(n))
                    }
                } else {
                    Ok(JsValue::String(
                        v.to_string(scope)
                            .map(|s| s.to_rust_string_lossy(scope))
                            .unwrap_or_default(),
                    ))
                }
            }
            None => {
                if self.oom.load(Ordering::SeqCst) {
                    self.oom.store(false, Ordering::SeqCst);
                    return Err("out of memory".into());
                }
                if scope.has_terminated() {
                    return Err("interrupted".into());
                }
                let msg = scope
                    .exception()
                    .and_then(|e| e.to_string(scope))
                    .map(|s| s.to_rust_string_lossy(scope))
                    .unwrap_or_else(|| "script error".into());
                if msg == "null" {
                    return Err("interrupted".into());
                }
                Err(msg)
            }
        }
    }

    pub fn eval_count(&self) -> usize {
        self.eval_count
    }

    pub fn used_heap_size(&mut self) -> usize {
        let stats = self.isolate.get_heap_statistics();
        stats.used_heap_size() + stats.external_memory()
    }

    pub fn total_heap_size(&mut self) -> usize {
        let stats = self.isolate.get_heap_statistics();
        stats.total_heap_size()
    }

    /// Drain pending microtasks (one checkpoint drains the whole queue).
    /// Returns false — after a checkpoint the queue is empty, so the host's
    /// `while ctx.execute_pending_job():` loop runs one drain and stops.
    pub fn pump_jobs(&mut self) -> bool {
        let _guard = match ReentrancyGuard::enter(&self.is_evaluating) {
            Ok(g) => g,
            Err(_) => return false,
        };
        self.isolate.cancel_terminate_execution();
        HOST_BRIDGE.with(|b| b.set(self.bridge));
        unsafe {
            self.isolate.enter();
        }
        self.isolate.perform_microtask_checkpoint();
        unsafe {
            self.isolate.exit();
        }
        HOST_BRIDGE.with(|b| b.set(std::ptr::null_mut()));
        false
    }

    /// Wall-clock budget per subsequent eval, in seconds. 0 disables.
    pub fn set_time_limit(&mut self, seconds: f64) {
        self.wall_limit = seconds.max(0.0);
    }

    /// Heap budget in bytes. Honest Phase-1 note: V8 heap limits are fixed at
    /// isolate creation (`CreateParams::heap_limits`); this stores the budget
    /// for the next construction and is reported by `memory_limit()`. The
    /// live safety bound today is `set_time_limit`.
    pub fn set_memory_limit(&mut self, bytes: usize) {
        self.memory_limit = bytes;
    }

    pub fn memory_limit(&self) -> usize {
        self.memory_limit
    }

    /// Set deterministic instruction/step fuel limit.
    pub fn set_fuel(&mut self, fuel: u64) {
        self.fuel.store(fuel, Ordering::SeqCst);
        self.fuel_enabled.store(true, Ordering::SeqCst);
    }

    pub fn fuel_remaining(&self) -> u64 {
        self.fuel.load(Ordering::SeqCst)
    }

    pub fn disable_fuel(&mut self) {
        self.fuel_enabled.store(false, Ordering::SeqCst);
    }

    /// Set deterministic virtual host clock (Date.now / performance.now).
    pub fn set_virtual_time(&mut self, millis: f64) -> Result<(), String> {
        if let Ok(mut vt) = self.virtual_time.lock() {
            *vt = Some(millis);
        }
        self.eval(&format!("globalThis.__setVirtualTime({millis});"))?;
        Ok(())
    }

    pub fn advance_virtual_time(&mut self, delta_millis: f64) -> Result<(), String> {
        if let Ok(mut vt) = self.virtual_time.lock() {
            if let Some(ref mut t) = *vt {
                *t += delta_millis;
            }
        }
        self.eval(&format!("globalThis.__advanceVirtualTime({delta_millis});"))?;
        Ok(())
    }

    pub fn virtual_time(&self) -> Option<f64> {
        self.virtual_time.lock().ok().and_then(|g| *g)
    }

    /// Set deterministic PRNG seed for Math.random (XorShift128+).
    pub fn set_random_seed(&mut self, seed: u64) -> Result<(), String> {
        self.eval(&format!("globalThis.__setRandomSeed({seed}n);"))?;
        Ok(())
    }

    pub fn reset_random(&mut self) -> Result<(), String> {
        self.eval("globalThis.__resetRandom();")?;
        Ok(())
    }

    /// Capability Gate: allow or disallow eval() and Function constructor.
    pub fn set_eval_allowed(&mut self, allowed: bool) -> Result<(), String> {
        self.eval_allowed.store(allowed, Ordering::SeqCst);
        self.eval(&format!("globalThis.__setCapabilityGate('eval', {allowed});"))?;
        Ok(())
    }

    pub fn is_eval_allowed(&self) -> bool {
        self.eval_allowed.load(Ordering::SeqCst)
    }

    /// Capability Gate: allow or disallow WebAssembly.
    pub fn set_wasm_allowed(&mut self, allowed: bool) -> Result<(), String> {
        self.wasm_allowed.store(allowed, Ordering::SeqCst);
        self.eval(&format!("globalThis.__setCapabilityGate('wasm', {allowed});"))?;
        Ok(())
    }

    pub fn is_wasm_allowed(&self) -> bool {
        self.wasm_allowed.load(Ordering::SeqCst)
    }

    /// Capability Gate: allow or disallow host network calls (host_fetch, host_ws_*).
    pub fn set_network_allowed(&mut self, allowed: bool) {
        self.network_allowed.store(allowed, Ordering::SeqCst);
    }

    pub fn is_network_allowed(&self) -> bool {
        self.network_allowed.load(Ordering::SeqCst)
    }

    /// Capability Gate: allow or disallow a specific WAASM capability (e.g. "read_dom", "act", "emit", "net_query", "log", "snapshot").
    pub fn set_waasm_capability(&mut self, cap: &str, allowed: bool) -> Result<(), String> {
        self.eval(&format!("globalThis.__setCapabilityGate('waasm.{cap}', {allowed});"))?;
        Ok(())
    }

    pub fn is_waasm_capability_allowed(&mut self, cap: &str) -> bool {
        self.eval(&format!("globalThis.__getCapabilityGate('waasm.{cap}');"))
            .map(|v| v == "true")
            .unwrap_or(false)
    }

    /// Set action authority gate (allows or blocks DOM mutating actions like click/type/form submit).
    pub fn set_action_allowed(&mut self, allowed: bool) -> Result<(), String> {
        self.action_allowed.store(allowed, Ordering::SeqCst);
        self.eval(&format!("globalThis.__setCapabilityGate('action', {allowed});"))?;
        Ok(())
    }

    pub fn is_action_allowed(&self) -> bool {
        self.action_allowed.load(Ordering::SeqCst)
    }

    /// Toggle agent control realm (enables agent verbs and top-level return).
    pub fn set_control_realm(&mut self, enabled: bool) -> Result<(), String> {
        self.control_realm.store(enabled, Ordering::SeqCst);
        self.eval(&format!("globalThis.__setControlRealm({enabled});"))?;
        Ok(())
    }

    pub fn is_control_realm(&self) -> bool {
        self.control_realm.load(Ordering::SeqCst)
    }

    /// Evaluate code in a bounded control realm with explicit action authority and argument injection.
    pub fn eval_bounded(
        &mut self,
        code: &str,
        control_realm: bool,
        allow_actions: bool,
        args: &[String],
    ) -> Result<String, String> {
        let prev_realm = self.is_control_realm();
        let prev_actions = self.is_action_allowed();

        let _ = self.set_control_realm(control_realm);
        let _ = self.set_action_allowed(allow_actions);

        let args_json = format!(
            "[{}]",
            args.iter()
                .map(|s| format!("{s:?}"))
                .collect::<Vec<_>>()
                .join(",")
        );
        let _ = self.eval(&format!("if (globalThis.dosh) {{ globalThis.dosh.args = {args_json}; }}"));

        let wrapped = if control_realm {
            format!("(function() {{ {code} }})()")
        } else {
            code.to_string()
        };

        let result = self.eval(&wrapped);

        let _ = self.set_control_realm(prev_realm);
        let _ = self.set_action_allowed(prev_actions);

        result
    }

    /// Update or query the contiguous Butterfly layout rectangle cache.
    pub fn set_layout_rects(&mut self, rects: Vec<protocol::DoshButterflyRect>) {
        self.layout_buffer = rects;
    }

    pub fn get_layout_rects(&self) -> &[protocol::DoshButterflyRect] {
        &self.layout_buffer
    }

    /// Poll and drain the lock-free telemetry ring buffer.
    pub fn poll_telemetry_ring(&mut self) -> Vec<(u32, protocol::DoshEventKind, String, u64)> {
        self.telemetry_ring.drain()
    }

    /// Access the active Parena page arena.
    pub fn parena(&self) -> Option<&parena::Parena> {
        self.parena.as_ref()
    }

    /// Mutably access the active Parena page arena.
    pub fn parena_mut(&mut self) -> Option<&mut parena::Parena> {
        self.parena.as_mut()
    }

    /// Set or replace the active Parena page arena.
    pub fn set_parena(&mut self, arena: parena::Parena) {
        self.parena = Some(arena);
    }

    /// Fork the current page arena in microseconds.
    pub fn fork_parena(&self) -> Result<parena::Parena, String> {
        self.parena
            .as_ref()
            .map(|p| p.fork())
            .ok_or_else(|| "no active parena in engine".to_string())
    }

    /// Capture a dirty-diff checkpoint of current arena state.
    pub fn checkpoint_parena(&mut self) -> Result<u64, String> {
        if let Some(parena) = &self.parena {
            let cp = parena.checkpoint();
            let id = cp.id;
            self.checkpoints.insert(id, cp);
            Ok(id)
        } else {
            Err("no active parena in engine".to_string())
        }
    }

    /// Roll back the arena state to a previously captured checkpoint ID.
    pub fn rewind_parena(&mut self, checkpoint_id: u64) -> Result<usize, String> {
        let cp = self
            .checkpoints
            .get(&checkpoint_id)
            .cloned()
            .ok_or_else(|| format!("checkpoint id {checkpoint_id} not found"))?;
        if let Some(parena) = &mut self.parena {
            parena.rewind(&cp)
        } else {
            Err("no active parena in engine".to_string())
        }
    }

    /// Zero-copy binary batch read of specified node IDs.
    pub fn batch_read_parena(&self, node_ids: &[u32]) -> Result<Vec<u8>, String> {
        if let Some(parena) = &self.parena {
            Ok(parena.batch_read(node_ids))
        } else {
            Err("no active parena in engine".to_string())
        }
    }

    /// Dispatch an in-memory DCP command directly to the V8 C++ engine.
    pub fn dispatch_command(&mut self, cmd: &protocol::DoshCommand) -> protocol::DoshResponse {
        match cmd.opcode {
            0x00 => protocol::DoshResponse::ok(),
            0x01 => {
                if let Some(code) = cmd.as_payload_str() {
                    match self.eval(code) {
                        Ok(_) => protocol::DoshResponse::ok(),
                        Err(e) => protocol::DoshResponse::error(&e),
                    }
                } else {
                    protocol::DoshResponse::error("missing payload for EVAL")
                }
            }
            0x02 => {
                self.set_fuel(cmd.param);
                protocol::DoshResponse::ok()
            }
            0x03 => {
                let rem = self.fuel_remaining();
                protocol::DoshResponse::with_numeric(rem)
            }
            0x04 => {
                let _ = self.set_virtual_time(cmd.param as f64);
                protocol::DoshResponse::ok()
            }
            0x05 => {
                let _ = self.set_random_seed(cmd.param);
                protocol::DoshResponse::ok()
            }
            0x06 => {
                let _ = self.set_eval_allowed((cmd.param & 1) != 0);
                let _ = self.set_wasm_allowed((cmd.param & 2) != 0);
                self.set_network_allowed((cmd.param & 4) != 0);
                protocol::DoshResponse::ok()
            }
            0x07 => {
                let drained = self.pump_jobs();
                protocol::DoshResponse::with_numeric(if drained { 1 } else { 0 })
            }
            0x08 => {
                let len = self.layout_buffer.len();
                let bytes = len * std::mem::size_of::<protocol::DoshButterflyRect>();
                protocol::DoshResponse {
                    status: protocol::DoshStatusCode::Ok as u8,
                    data_type: 4, // Buffer
                    reserved: 0,
                    result_len: bytes as u32,
                    numeric_val: len as u64,
                    result_data: if self.layout_buffer.is_empty() {
                        std::ptr::null()
                    } else {
                        self.layout_buffer.as_ptr() as *const u8
                    },
                }
            }
            0x09 => {
                let used = self.used_heap_size() as u64;
                protocol::DoshResponse::with_numeric(used)
            }
            0x0A => {
                let allowed = cmd.param != 0;
                let _ = self.set_action_allowed(allowed);
                protocol::DoshResponse::ok()
            }
            0x0B => {
                let enabled = cmd.param != 0;
                let _ = self.set_control_realm(enabled);
                protocol::DoshResponse::ok()
            }
            0x0C => {
                if let Some(parena) = &self.parena {
                    let mut node_ids = Vec::new();
                    if cmd.payload_len >= 4 && !cmd.payload.is_null() {
                        let count = (cmd.payload_len / 4) as usize;
                        unsafe {
                            let slice = std::slice::from_raw_parts(cmd.payload as *const u32, count);
                            node_ids.extend_from_slice(slice);
                        }
                    } else if cmd.param > 0 {
                        node_ids.push(cmd.param as u32);
                    }
                    let bytes = parena.batch_read(&node_ids);
                    protocol::DoshResponse {
                        status: protocol::DoshStatusCode::Ok as u8,
                        data_type: 4, // Buffer
                        reserved: 0,
                        result_len: bytes.len() as u32,
                        numeric_val: node_ids.len() as u64,
                        result_data: bytes.as_ptr(),
                    }
                } else {
                    protocol::DoshResponse::error("no active parena in engine")
                }
            }
            0x0D => {
                let drained = self.telemetry_ring.drain();
                protocol::DoshResponse::with_numeric(drained.len() as u64)
            }
            _ => protocol::DoshResponse::error("unknown opcode"),
        }
    }
}

impl Drop for DoshEngine {
    fn drop(&mut self) {
        let bridge = self.bridge;
        self.bridge = std::ptr::null_mut();
        unsafe {
            drop(Box::from_raw(bridge));
            self.isolate.enter();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct DummySeam;
    impl HostSeam for DummySeam {
        fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
            if name == "echo" {
                Ok(Some(args.join(",")))
            } else if name == "host_fetch" {
                Ok(Some("fetched".into()))
            } else {
                Ok(None)
            }
        }
    }

    #[test]
    fn test_butterfly_storage_bidirectional() {
        let mut storage: ButterflyStorage<String> = ButterflyStorage::new(4, 4);
        storage.set_property(1, "prop1".into());
        storage.set_property(2, "prop2".into());
        storage.set_element(0, "elem0".into());
        storage.set_element(1, "elem1".into());

        assert_eq!(storage.get_property(1).map(|s| s.as_str()), Some("prop1"));
        assert_eq!(storage.get_property(2).map(|s| s.as_str()), Some("prop2"));
        assert_eq!(storage.get_element(0).map(|s| s.as_str()), Some("elem0"));
        assert_eq!(storage.get_element(1).map(|s| s.as_str()), Some("elem1"));
        assert_eq!(storage.element_count(), 2);
    }

    #[test]
    fn test_js_butterfly_layout() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let res = engine.eval(r#"
            const b = __createButterfly(["name", "tag"], ["child1", "child2"]);
            b.setProperty("name", "button");
            b.setProperty("tag", "BUTTON");
            b.push("child3");
            JSON.stringify({
                name: b.getProperty("name"),
                tag: b.getProperty("tag"),
                first: b.getElement(0),
                third: b.getElement(2),
                len: b.length
            });
        "#).unwrap();
        assert!(res.contains("\"name\":\"button\""));
        assert!(res.contains("\"first\":\"child1\""));
        assert!(res.contains("\"third\":\"child3\""));
        assert!(res.contains("\"len\":3"));
    }

    #[test]
    fn test_virtual_host_clock_injection() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        engine.set_virtual_time(1700000000000.0).unwrap();
        assert_eq!(engine.eval("Date.now()").unwrap(), "1700000000000");

        engine.advance_virtual_time(500.0).unwrap();
        assert_eq!(engine.eval("Date.now()").unwrap(), "1700000000500");
    }

    #[test]
    fn test_deterministic_seeded_prng() {
        let mut engine1 = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let mut engine2 = DoshEngine::new(Arc::new(DummySeam)).unwrap();

        engine1.set_random_seed(424242).unwrap();
        engine2.set_random_seed(424242).unwrap();

        let r1_a = engine1.eval("Math.random()").unwrap();
        let r1_b = engine1.eval("Math.random()").unwrap();

        let r2_a = engine2.eval("Math.random()").unwrap();
        let r2_b = engine2.eval("Math.random()").unwrap();

        assert_eq!(r1_a, r2_a);
        assert_eq!(r1_b, r2_b);
        assert_ne!(r1_a, r1_b);
    }

    #[test]
    fn test_capability_gate_eval() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        assert_eq!(engine.eval("eval('2 + 3')").unwrap(), "5");

        engine.set_eval_allowed(false).unwrap();
        let err = engine.eval("eval('2 + 3')");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("disallowed"));
    }

    #[test]
    fn test_capability_gate_wasm() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        assert_eq!(engine.eval("typeof WebAssembly").unwrap(), "object");

        engine.set_wasm_allowed(false).unwrap();
        assert_eq!(engine.eval("typeof WebAssembly").unwrap(), "undefined");

        engine.set_wasm_allowed(true).unwrap();
        assert_eq!(engine.eval("typeof WebAssembly").unwrap(), "object");
    }

    #[test]
    fn test_capability_gate_network() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        engine.add_host("host_fetch").unwrap();
        assert_eq!(engine.eval("globalThis.host_fetch('https://example.com')").unwrap(), "fetched");

        engine.set_network_allowed(false);
        let err = engine.eval("globalThis.host_fetch('https://example.com')");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("network capability disallowed"));
    }

    #[test]
    fn test_fuel_metering_trap() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        engine.set_fuel(5);
        assert_eq!(engine.fuel_remaining(), 5);

        // Consume 3
        engine.eval("__dosh_step(3)").unwrap();
        assert_eq!(engine.fuel_remaining(), 2);

        // Attempting to consume 3 more when only 2 left should trap
        let err = engine.eval("__dosh_step(3)");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("fuel exhausted"));
        assert_eq!(engine.fuel_remaining(), 0);
    }

    #[test]
    fn test_proper_tail_call_trampoline() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let res = engine.eval(r#"
            const sum = __tco(function f(n, acc = 0) {
                if (n <= 0) return acc;
                return __thunk(f, n - 1, acc + n);
            });
            sum(100000);
        "#).unwrap();
        assert_eq!(res, "5000050000");
    }

    #[test]
    fn test_waasm_agentic_wasm_capabilities() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        assert_eq!(engine.eval("typeof globalThis.waasm").unwrap(), "object");
        assert_eq!(engine.eval("typeof globalThis.waasm.read_dom").unwrap(), "function");
        assert_eq!(engine.eval("typeof globalThis.waasm.act").unwrap(), "function");

        // Allowed by default
        assert!(engine.is_waasm_capability_allowed("read_dom"));
        assert!(engine.is_waasm_capability_allowed("act"));
        assert_eq!(engine.eval("globalThis.waasm.read_dom(0, 0)").unwrap(), "0");

        // Disallow act
        engine.set_waasm_capability("act", false).unwrap();
        assert!(!engine.is_waasm_capability_allowed("act"));
        let err = engine.eval("globalThis.waasm.act(0, 0)");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("capability disallowed"));

        // Re-allow act
        engine.set_waasm_capability("act", true).unwrap();
        assert!(engine.is_waasm_capability_allowed("act"));
        assert_eq!(engine.eval("globalThis.waasm.act(0, 0)").unwrap(), "0");
    }

    #[test]
    fn test_dcp_protocol_dispatch() {
        use protocol::{DoshCommand, DoshOpCode, DoshStatusCode};
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();

        // 1. Eval via DCP
        let eval_cmd = DoshCommand::new(DoshOpCode::Eval).with_payload("globalThis.__dcpTest = 42; true");
        let resp = engine.dispatch_command(&eval_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);

        // 2. Set Fuel via DCP
        let fuel_cmd = DoshCommand::new(DoshOpCode::SetFuel).with_param(5000);
        let resp = engine.dispatch_command(&fuel_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert_eq!(engine.fuel_remaining(), 5000);

        // 3. Get Fuel via DCP
        let get_fuel_cmd = DoshCommand::new(DoshOpCode::GetFuel);
        let resp = engine.dispatch_command(&get_fuel_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert_eq!(resp.numeric_val, 5000);

        // 4. Set Clock via DCP
        let clock_cmd = DoshCommand::new(DoshOpCode::SetClock).with_param(1700000000000);
        let resp = engine.dispatch_command(&clock_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert_eq!(engine.virtual_time(), Some(1700000000000.0));

        // 5. Heap Stats via DCP
        let heap_cmd = DoshCommand::new(DoshOpCode::GetHeapStats);
        let resp = engine.dispatch_command(&heap_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert!(resp.numeric_val > 0);

        // 6. Butterfly Layout Rects via DCP
        let rect = protocol::DoshButterflyRect {
            element_id: 1,
            x: 10.0,
            y: 20.0,
            width: 100.0,
            height: 50.0,
            z_index: 0,
            occluded_stratum: 0,
            flags: 0,
        };
        engine.set_layout_rects(vec![rect]);
        let layout_cmd = DoshCommand::new(DoshOpCode::GetLayout);
        let resp = engine.dispatch_command(&layout_cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert_eq!(resp.data_type, 4);
        assert_eq!(resp.numeric_val, 1);
        assert_eq!(resp.result_len as usize, std::mem::size_of::<protocol::DoshButterflyRect>());
        assert!(!resp.result_data.is_null());
    }

    #[test]
    fn test_reentrancy_guard_catches_nested_eval() {
        struct ReentrantSeam {
            engine_ptr: std::sync::atomic::AtomicPtr<DoshEngine>,
        }
        impl HostSeam for ReentrantSeam {
            fn call(&self, name: &str, _args: &[String]) -> Result<Option<String>, String> {
                if name == "call_reentrant" {
                    let ptr = self.engine_ptr.load(Ordering::SeqCst);
                    if !ptr.is_null() {
                        let engine = unsafe { &mut *ptr };
                        // Attempt nested eval while isolate is already entered
                        match engine.eval("1 + 1") {
                            Ok(_) => Ok(Some("unexpected_success".into())),
                            Err(e) => Err(e),
                        }
                    } else {
                        Ok(None)
                    }
                } else {
                    Ok(None)
                }
            }
        }

        let seam = Arc::new(ReentrantSeam {
            engine_ptr: std::sync::atomic::AtomicPtr::new(std::ptr::null_mut()),
        });
        let mut engine = DoshEngine::new(seam.clone()).unwrap();
        seam.engine_ptr.store(&mut engine as *mut DoshEngine, Ordering::SeqCst);

        engine.add_host("call_reentrant").unwrap();

        // When JS calls call_reentrant, it hits the ReentrancyGuard and catches the error in JS
        let res = engine.eval(r#"
            let caught = "";
            try {
                call_reentrant();
            } catch (e) {
                caught = e.toString();
            }
            caught;
        "#).unwrap();

        assert!(res.contains("re-entrancy error"), "expected re-entrancy error, got: {}", res);

        // Disarm pointer before engine drops
        seam.engine_ptr.store(std::ptr::null_mut(), Ordering::SeqCst);
    }

    #[test]
    fn test_watchdog_execution_timeout() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        // Set a 50ms time limit
        engine.set_time_limit(0.05);

        // Infinite loop must be terminated by the process-wide watchdog supervisor
        let res = engine.eval("while(true) {}");
        assert!(res.is_err());
        let err = res.unwrap_err();
        assert_eq!(err, "interrupted");

        // The isolate must recover for subsequent commands
        engine.set_time_limit(0.0);
        let recovery = engine.eval("10 + 20").unwrap();
        assert_eq!(recovery, "30");
    }

    #[test]
    fn test_control_realm_top_level_return_and_args() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();

        // Control realm allows top-level return and injects dosh.args
        let res = engine
            .eval_bounded(
                "return dosh.args[0] + ' ' + dosh.args[1];",
                true,
                true,
                &["Agent".to_string(), "Native".to_string()],
            )
            .expect("eval_bounded failed");

        assert_eq!(res, "Agent Native");
    }

    #[test]
    fn test_action_authority_gate_blocks_mutations() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();

        // When action is allowed
        assert!(engine.is_action_allowed());
        let allowed = engine.eval("globalThis.__getCapabilityGate('action')").unwrap();
        assert_eq!(allowed, "true");

        // Revoke action authority
        engine.set_action_allowed(false).unwrap();
        assert!(!engine.is_action_allowed());
        let disallowed = engine.eval("globalThis.__getCapabilityGate('action')").unwrap();
        assert_eq!(disallowed, "false");

        // dosh.act must throw an explicit authority error
        let err = engine.eval("dosh.act({ type: 'click', ref: 'btn_1' });");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("Action authority disallowed"));

        // Re-enable action authority
        engine.set_action_allowed(true).unwrap();
        assert!(engine.is_action_allowed());
    }

    #[test]
    fn test_dcp_protocol_action_and_realm_opcodes() {
        use protocol::{DoshCommand, DoshOpCode, DoshStatusCode};
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();

        // Set Action Gate to false via DCP
        let cmd = DoshCommand::new(DoshOpCode::SetAllowActions).with_param(0);
        let resp = engine.dispatch_command(&cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert!(!engine.is_action_allowed());

        // Set Action Gate to true via DCP
        let cmd = DoshCommand::new(DoshOpCode::SetAllowActions).with_param(1);
        let resp = engine.dispatch_command(&cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert!(engine.is_action_allowed());

        // Set Control Realm via DCP
        let cmd = DoshCommand::new(DoshOpCode::SetControlRealm).with_param(1);
        let resp = engine.dispatch_command(&cmd);
        assert_eq!(resp.status, DoshStatusCode::Ok as u8);
        assert!(engine.is_control_realm());
    }
}
