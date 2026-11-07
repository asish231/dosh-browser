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
use std::sync::atomic::{AtomicBool, AtomicU64, AtomicU8, AtomicUsize, Ordering};
use std::sync::Mutex;
use std::time::Duration;

pub mod actor;
pub mod parena;
pub mod protocol;
pub mod session;
pub mod ab_pool;
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
    pub parena: Arc<Mutex<Option<parena::Parena>>>,
    pub checkpoints: Arc<Mutex<HashMap<u64, parena::Checkpoint>>>,
    /// Heap-pressure escalation epoch for the CURRENT eval (Stage-1 ladder).
    /// 0 = calm; 1 = near-limit callback fired once (GC was granted headroom);
    /// >=2 = genuine exhaustion — the next callback terminates execution.
    pub pressure_epoch: Arc<AtomicU8>,
    /// Hard ceiling the ladder will never grant beyond. Computed at isolate
    /// construction from the configured memory_limit (0 = uncapped policy:
    /// per-epoch bump only, max PRESSURE_MAX_EPOCHS).
    pub hard_limit: usize,
    /// Last limit granted by the ladder callback (Stage-2 envelope tracking).
    /// Initialized to the configured memory_limit; the callback stores every
    /// raise so the shrink pass can compare live heap vs granted budget.
    pub current_limit: Arc<AtomicUsize>,
    /// Host bytes last reported to V8's external-memory pacer (Stage 0).
    pub last_external: AtomicUsize,
}

/// Max escalation epochs under the uncapped policy (memory_limit == 0).
const PRESSURE_MAX_EPOCHS: u8 = 3;
/// Headroom granted per epoch: +25% of the current limit.
const PRESSURE_BUMP_FRAC: usize = 4;
/// Shrink pass (Stage 2): when the live heap stays under this fraction of the
/// granted limit for SHRINK_STREAK_N consecutive job-pump drains, ask V8 for a
/// low-memory GC so it releases pages back (Go-pacer inspired: measure the
/// live set, size the budget from it).
const SHRINK_LIVE_FRAC: usize = 5; // under 1/5 (20%) of granted budget
const SHRINK_STREAK_N: u8 = 2;

/// Stage-2 env knob: envelope floor in MiB (below this granted budget we never
/// ask V8 to release pages). WR_JS_HEAP_FLOOR_MB, default 32; 0 = no floor.
fn env_heap_floor_mb() -> usize {
    std::env::var("WR_JS_HEAP_FLOOR_MB")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(32)
}

/// Stage-2 env knob: envelope ceiling in MiB — clamps the ladder's bumps.
/// WR_JS_HEAP_CEIL_MB, default 512.
///
/// Stage-3 env knob lives with it: resident text-arena watermark in MiB
/// before the cold tail spills to disk. WR_PARENA_SPILL_MB, default 64;
/// 0 disables the auto-spill (explicit spill_parena_tail still works).
fn env_parena_spill_mb() -> usize {
    std::env::var("WR_PARENA_SPILL_MB")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(64)
}
fn env_heap_ceil_mb() -> usize {
    std::env::var("WR_JS_HEAP_CEIL_MB")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .unwrap_or(512)
}
/// When a memory_limit IS configured, the hard ceiling is
/// limit + max(64 MiB, limit/2) — bounded, generous enough for one full GC.
fn pressure_hard_limit(memory_limit: usize) -> usize {
    if memory_limit == 0 {
        0
    } else {
        memory_limit + (64 * 1024 * 1024).max(memory_limit / 2)
    }
}

/// Stage-2 measured heap envelope for one isolate.
/// Returns (floor_bytes, ceil_bytes, hard_limit, initial_granted).
/// - With a configured memory_limit: hard = max(limit, min(pressure_hard_limit(limit), ceil))
///   (the ladder may grow the heap past the configured limit, but never past the band).
/// - Uncapped (limit 0): hard = ceil — the envelope IS the policy; the
///   epoch backstop remains as a safety net if the ceiling is 0.
fn heap_band(memory_limit: usize) -> (usize, usize, usize, usize) {
    let floor = env_heap_floor_mb().saturating_mul(1024 * 1024);
    let ceil = env_heap_ceil_mb().max(1).saturating_mul(1024 * 1024);
    if memory_limit > 0 {
        let base = pressure_hard_limit(memory_limit);
        let hard = memory_limit.max(base.min(ceil));
        (floor, ceil, hard, memory_limit)
    } else {
        (floor, ceil, ceil, 0)
    }
}

/// Stage-1 pressure-graded near-heap-limit response.
///
/// V8's contract for this callback: it fires when the heap approaches the
/// limit, and the RETURN VALUE is the new limit — returning a higher limit
/// lets the GC that triggered the callback complete and reclaim, instead of
/// crashing. The old implementation unconditionally terminated execution on
/// the FIRST callback (a hard kill with +16 MiB of irrelevant headroom):
/// collectable garbage was indistinguishable from genuine exhaustion.
///
/// The ladder:
///   epoch 0 → 1 : grant +25% headroom (bounded by hard_limit) so GC finishes.
///                Collectable loads survive; the flag stays false.
///   epoch ≥ 1  : genuine growth against the ceiling — set the oom flag and
///                terminate (the raised limit is only the unwinding lifeboat).
///
/// Epochs are reset per eval (see eval_raw), so one page's pressure never
/// poisons the next.
/// V8 contract for this callback: the RETURN VALUE is the new limit, and
/// "if the callback returns a value that is not higher than the current
/// limit, the OOM handler is called" — a process-fatal abort (SIGTRAP), NOT
/// our honest error. So EVERY branch here must return a raised limit; the
/// oom flag + terminate_execution are what actually stop the runaway, while
/// the raised limit is the lifeboat that lets V8 unwind out of the GC.
unsafe extern "C" fn near_heap_limit_callback(
    data: *mut std::ffi::c_void,
    current_heap_limit: usize,
    _initial_heap_limit: usize,
) -> usize {
    if data.is_null() {
        return current_heap_limit;
    }
    let bridge = &*(data as *mut HostBridge);
    let bump = (current_heap_limit / PRESSURE_BUMP_FRAC).max(16 * 1024 * 1024);
    let epoch = bridge.pressure_epoch.fetch_add(1, Ordering::SeqCst);
    if epoch >= 1 {
        // Second (or later) callback in the same eval: the headroom we
        // granted was consumed without reclaim — genuine exhaustion. Flag
        // the OOM and terminate the JS loop, but STILL raise the limit so
        // V8 can finish this GC and unwind (returning an unchanged limit is
        // a process-fatal abort). Execution is already being terminated, so
        // this bump is an unwinding lifeboat, not a growth permit.
        bridge.oom.store(true, Ordering::SeqCst);
        bridge.interrupt_handle.terminate_execution();
        let new_limit = current_heap_limit + bump;
        bridge.current_limit.store(new_limit, Ordering::SeqCst);
        return new_limit;
    }
    // First callback: bounded headroom so the GC that fired completes.
    let ceiling = bridge.hard_limit;
    let new_limit = current_heap_limit + bump;
    if ceiling > 0 {
        if new_limit >= ceiling {
            // Even one more full GC pass cannot fit under the ceiling.
            bridge.oom.store(true, Ordering::SeqCst);
            bridge.interrupt_handle.terminate_execution();
            bridge.current_limit.store(new_limit, Ordering::SeqCst);
            return new_limit;
        }
    } else if epoch + 1 >= PRESSURE_MAX_EPOCHS {
        // Uncapped policy: refuse to grow forever.
        bridge.oom.store(true, Ordering::SeqCst);
        bridge.interrupt_handle.terminate_execution();
        bridge.current_limit.store(new_limit, Ordering::SeqCst);
        return new_limit;
    }
    bridge.current_limit.store(new_limit, Ordering::SeqCst);
    new_limit
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

    // Direct Parena verbs from JS runtime (synchronous, avoids actor channel deadlock)
    if name == "__dosh_checkpoint" || name == "host_checkpoint" {
        if let Ok(mut c_map) = bridge.checkpoints.lock() {
            if let Ok(p_guard) = bridge.parena.lock() {
                if let Some(p) = p_guard.as_ref() {
                    let cp = p.checkpoint();
                    let id = cp.id;
                    c_map.insert(id, cp);
                    let s = v8::String::new(scope, &id.to_string())
                        .unwrap_or_else(|| v8::String::empty(scope));
                    rv.set(s.into());
                    return;
                }
            }
        }
        let m = v8::String::new(scope, "no active parena in engine")
            .unwrap_or_else(|| v8::String::empty(scope));
        scope.throw_exception(m.into());
        return;
    }

    if name == "__dosh_rewind" || name == "host_rewind" {
        let cp_id_str = host_args.first().cloned().unwrap_or_default();
        let cp_id = cp_id_str.parse::<u64>().unwrap_or(0);
        let cp = bridge.checkpoints.lock().ok().and_then(|m| m.get(&cp_id).cloned());
        if let Some(checkpoint) = cp {
            if let Ok(mut p_guard) = bridge.parena.lock() {
                if let Some(p) = p_guard.as_mut() {
                    match p.rewind(&checkpoint) {
                        Ok(reverted_bytes) => {
                            let s = v8::String::new(scope, &reverted_bytes.to_string())
                                .unwrap_or_else(|| v8::String::empty(scope));
                            rv.set(s.into());
                            return;
                        }
                        Err(e) => {
                            let m = v8::String::new(scope, &e)
                                .unwrap_or_else(|| v8::String::empty(scope));
                            scope.throw_exception(m.into());
                            return;
                        }
                    }
                }
            }
        }
        let m = v8::String::new(scope, &format!("checkpoint id {cp_id} not found"))
            .unwrap_or_else(|| v8::String::empty(scope));
        scope.throw_exception(m.into());
        return;
    }

    if name == "__dosh_batch_read" || name == "host_batch_read" {
        let ids_json = host_args.first().cloned().unwrap_or_default();
        let ids: Vec<u32> = serde_json::from_str(&ids_json).unwrap_or_default();
        if let Ok(p_guard) = bridge.parena.lock() {
            if let Some(p) = p_guard.as_ref() {
                let bytes = p.batch_read(&ids);
                let hex_str = hex::encode(&bytes);
                let s = v8::String::new(scope, &hex_str)
                    .unwrap_or_else(|| v8::String::empty(scope));
                rv.set(s.into());
                return;
            }
        }
        let m = v8::String::new(scope, "no active parena in engine")
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
        },
        checkpoint: function() {
            return Number(globalThis.__dispatchHost("__dosh_checkpoint"));
        },
        rewind: function(cpId) {
            return Number(globalThis.__dispatchHost("__dosh_rewind", String(cpId)));
        },
        fork: function() {
            return globalThis.__dispatchHost("__dosh_fork");
        },
        batchRead: function(nodeIds) {
            return globalThis.__dispatchHost("__dosh_batch_read", JSON.stringify(nodeIds));
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
    pressure_epoch: Arc<AtomicU8>,
    last_external: Arc<AtomicUsize>,
    /// Stage-2 measured heap envelope. floor: below this granted budget we
    /// never ask V8 to release pages. ceil: mirrored into the bridge's
    /// hard_limit (single source of truth for the ladder). shrink_streak:
    /// consecutive job-pump drains that saw the live heap under 40% of the
    /// granted limit; two in a row trigger a low-memory GC.
    heap_floor: usize,
    heap_ceil: usize,
    shrink_streak: u8,
    /// Times the shrink pass asked V8 for a low-memory GC (Stage 2).
    /// Observable so the pass is testable without depending on V8's
    /// asynchronous page-release behavior.
    shrink_fires: u32,
    current_limit: Arc<AtomicUsize>,
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
    pub parena: Arc<Mutex<Option<parena::Parena>>>,
    pub checkpoints: Arc<Mutex<HashMap<u64, parena::Checkpoint>>>,
}

static V8_INIT: std::sync::Once = std::sync::Once::new();

pub(crate) fn init_v8() {
    V8_INIT.call_once(|| {
        let flags = std::env::var("WR_V8_FLAGS").unwrap_or_else(|_| {
            "--no-opt --no-maglev --max-semi-space-size=1 --stack-trace-limit=10".to_string()
        });
        v8::V8::set_flags_from_string(&flags);
        let platform = v8::new_default_platform(0, false).make_shared();
        v8::V8::initialize_platform(platform);
        v8::V8::initialize();
    });
}

impl DoshEngine {
    pub fn new(seam: Arc<dyn HostSeam>) -> Result<Self, String> {
        Self::new_with_limit(seam, 0)
    }

    pub fn new_with_limit(seam: Arc<dyn HostSeam>, memory_limit: usize) -> Result<Self, String> {
        init_v8();

        let mut params = v8::CreateParams::default();
        if memory_limit > 0 {
            params = params.heap_limits(0, memory_limit);
        }
        // Stage 4: opt-in pooled ArrayBuffer allocator (host accounting).
        if ab_pool::ab_pool_enabled() {
            params = params.array_buffer_allocator(ab_pool::make_pooled_allocator());
        }
        let mut isolate = v8::Isolate::new(params);
        let interrupt_handle = isolate.thread_safe_handle();
        let oom = Arc::new(AtomicBool::new(false));
        let pressure_epoch = Arc::new(AtomicU8::new(0));
        let last_external = Arc::new(AtomicUsize::new(0));
        let (heap_floor, _heap_ceil, heap_hard, _initial) = heap_band(memory_limit);
        let current_limit = Arc::new(AtomicUsize::new(memory_limit));

        let fuel = Arc::new(AtomicU64::new(0));
        let fuel_enabled = Arc::new(AtomicBool::new(false));
        let eval_allowed = Arc::new(AtomicBool::new(true));
        let wasm_allowed = Arc::new(AtomicBool::new(true));
        let network_allowed = Arc::new(AtomicBool::new(true));
        let action_allowed = Arc::new(AtomicBool::new(true));
        let control_realm = Arc::new(AtomicBool::new(false));
        let virtual_time = Arc::new(Mutex::new(None));
        let parena = Arc::new(Mutex::new(None));
        let checkpoints = Arc::new(Mutex::new(HashMap::new()));

        let bridge = Box::into_raw(Box::new(HostBridge {
            seam,
            oom: oom.clone(),
            interrupt_handle: interrupt_handle.clone(),
            fuel: fuel.clone(),
            fuel_enabled: fuel_enabled.clone(),
            eval_allowed: eval_allowed.clone(),
            wasm_allowed: wasm_allowed.clone(),
            network_allowed: network_allowed.clone(),
            parena: parena.clone(),
            checkpoints: checkpoints.clone(),
            pressure_epoch: pressure_epoch.clone(),
            hard_limit: heap_hard,
            current_limit: current_limit.clone(),
            last_external: AtomicUsize::new(0),
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
            pressure_epoch,
            last_external,
            heap_floor,
            heap_ceil: _heap_ceil,
            shrink_streak: 0,
            shrink_fires: 0,
            current_limit,
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
            parena,
            checkpoints,
        })
    }

    /// Create an engine booted warm from a pre-baked binary snapshot blob.
    pub fn new_with_snapshot(seam: Arc<dyn HostSeam>, snapshot_bytes: &[u8]) -> Result<Self, String> {
        Self::new_with_snapshot_and_limit(seam, snapshot_bytes, 0)
    }

    /// Create an engine booted warm from snapshot with heap limit.
    pub fn new_with_snapshot_and_limit(
        seam: Arc<dyn HostSeam>,
        snapshot_bytes: &[u8],
        memory_limit: usize,
    ) -> Result<Self, String> {
        init_v8();

        let mut params = v8::CreateParams::default().snapshot_blob(v8::StartupData::from(snapshot_bytes.to_vec()));
        if memory_limit > 0 {
            params = params.heap_limits(0, memory_limit);
        }
        // Stage 4: opt-in pooled ArrayBuffer allocator (host accounting).
        if ab_pool::ab_pool_enabled() {
            params = params.array_buffer_allocator(ab_pool::make_pooled_allocator());
        }
        let mut isolate = v8::Isolate::new(params);
        let interrupt_handle = isolate.thread_safe_handle();
        let oom = Arc::new(AtomicBool::new(false));
        let pressure_epoch = Arc::new(AtomicU8::new(0));
        let last_external = Arc::new(AtomicUsize::new(0));
        let (heap_floor, _heap_ceil, heap_hard, _initial) = heap_band(memory_limit);
        let current_limit = Arc::new(AtomicUsize::new(memory_limit));

        let fuel = Arc::new(AtomicU64::new(0));
        let fuel_enabled = Arc::new(AtomicBool::new(false));
        let eval_allowed = Arc::new(AtomicBool::new(true));
        let wasm_allowed = Arc::new(AtomicBool::new(true));
        let network_allowed = Arc::new(AtomicBool::new(true));
        let action_allowed = Arc::new(AtomicBool::new(true));
        let control_realm = Arc::new(AtomicBool::new(false));
        let virtual_time = Arc::new(Mutex::new(None));
        let parena = Arc::new(Mutex::new(None));
        let checkpoints = Arc::new(Mutex::new(HashMap::new()));

        let bridge = Box::into_raw(Box::new(HostBridge {
            seam,
            oom: oom.clone(),
            interrupt_handle: interrupt_handle.clone(),
            fuel: fuel.clone(),
            fuel_enabled: fuel_enabled.clone(),
            eval_allowed: eval_allowed.clone(),
            wasm_allowed: wasm_allowed.clone(),
            network_allowed: network_allowed.clone(),
            parena: parena.clone(),
            checkpoints: checkpoints.clone(),
            pressure_epoch: pressure_epoch.clone(),
            hard_limit: heap_hard,
            current_limit: current_limit.clone(),
            last_external: AtomicUsize::new(0),
        }));

        isolate.add_near_heap_limit_callback(near_heap_limit_callback, bridge as *mut std::ffi::c_void);
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
            pressure_epoch,
            last_external,
            heap_floor,
            heap_ceil: _heap_ceil,
            shrink_streak: 0,
            shrink_fires: 0,
            current_limit,
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
            parena,
            checkpoints,
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
        // Stage 1: each eval starts with a calm pressure ladder. One page's
        // near-limit event must not poison the next eval on this isolate.
        self.pressure_epoch.store(0, Ordering::SeqCst);

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
        // Stage 0: after the eval, report host-side page-arena bytes to V8's
        // external-memory pacer (host_parse_html and friends grew the Parena
        // during this eval). V8's GC then reacts to DOM growth it previously
        // could not see. Negative deltas (arena dropped/reset) are fine — the
        // API is explicitly delta-based.
        self.report_external_parena();
        // Stage 3: past the watermark, spill the arena's cold text tail to
        // disk (RAM release), then report the drop to the pacer. The
        // isolate is entered here, matching report_external_parena.
        let spill_wm = env_parena_spill_mb().saturating_mul(1024 * 1024);
        if spill_wm > 0 && self.parena_bytes() > spill_wm {
            let spilled = match self.parena.lock() {
                Ok(mut guard) => match guard.as_mut() {
                    Some(arena) => arena.spill_tail(spill_wm),
                    None => false,
                },
                Err(_) => false,
            };
            if spilled {
                self.report_external_parena();
            }
        }
        unsafe {
            self.isolate.exit();
        }
        HOST_BRIDGE.with(|b| b.set(std::ptr::null_mut()));

        self.eval_count += 1;
        out
    }

    /// Bytes the Parena currently holds outside the V8 cage.
    fn parena_bytes(&self) -> usize {
        self.parena
            .lock()
            .map(|guard| {
                guard
                    .as_ref()
                    .map(|a| a.external_bytes())
                    .unwrap_or(0)
            })
            .unwrap_or(0)
    }

    /// Stage-5 observability: (ram_bytes, spill_bytes, logical_text_bytes)
    /// for the attached Parena. RAM is the resident cost (post-spill);
    /// spill is the offloaded cold tail; text_len is the logical total.
    pub fn parena_stats(&self) -> (usize, usize, usize) {
        self.parena
            .lock()
            .map(|guard| {
                match guard.as_ref() {
                    Some(a) => (a.external_bytes(), a.spill_bytes(), a.text_len()),
                    None => (0, 0, 0),
                }
            })
            .unwrap_or((0, 0, 0))
    }

    /// Stage 0: report the Parena's external-bytes delta to V8. The isolate
    /// must be entered (called from inside eval_raw only).
    fn report_external_parena(&mut self) {
        let now = self.parena_bytes();
        let last = self.last_external.load(Ordering::SeqCst);
        if now == last {
            return;
        }
        self.isolate
            .adjust_amount_of_external_allocated_memory(now as i64 - last as i64);
        self.last_external.store(now, Ordering::SeqCst);
    }

    /// Current pressure-epoch of the ladder (0 = calm, >=2 = exhaustion hit).
    pub fn pressure_epoch(&self) -> u8 {
        self.pressure_epoch.load(Ordering::SeqCst)
    }

    /// Host bytes currently reported to V8's external-memory pacer.
    pub fn external_reported(&self) -> usize {
        self.last_external.load(Ordering::SeqCst)
    }

    /// Stage-3: spill the arena's cold text tail to disk at an explicit
    /// watermark (bytes). Returns true when a spill happened. The RAM drop
    /// is reported to V8's external-memory pacer so its GC sees the release.
    pub fn spill_parena_tail(&mut self, watermark_bytes: usize) -> bool {
        let spilled = {
            let mut guard = match self.parena.lock() {
                Ok(g) => g,
                Err(_) => return false,
            };
            match guard.as_mut() {
                Some(arena) => arena.spill_tail(watermark_bytes),
                None => false,
            }
        };
        if spilled {
            unsafe {
                self.isolate.enter();
            }
            self.report_external_parena();
            unsafe {
                self.isolate.exit();
            }
        }
        spilled
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
        // Stage-2 shrink pass: measure the live heap against the granted
        // budget (Go-pacer style). If the live set stays under 20% of the
        // granted limit for SHRINK_STREAK_N consecutive drains — and the
        // granted budget is above the floor — ask V8 for a low-memory GC so
        // it releases pages back to the OS instead of squatting on the
        // high-water mark (Goose's reserve-big/commit-lazy, via V8's own
        // machinery). V8 lowers its limit itself after such a GC.
        let granted = self.current_limit.load(Ordering::SeqCst);
        if granted > self.heap_floor {
            let used = self.isolate.get_heap_statistics().used_heap_size();
            if used.saturating_mul(SHRINK_LIVE_FRAC) < granted {
                self.shrink_streak = self.shrink_streak.saturating_add(1);
                if self.shrink_streak >= SHRINK_STREAK_N {
                    self.isolate.low_memory_notification();
                    self.shrink_fires += 1;
                    self.shrink_streak = 0;
                }
            } else {
                self.shrink_streak = 0;
            }
        }
        unsafe {
            self.isolate.exit();
        }
        HOST_BRIDGE.with(|b| b.set(std::ptr::null_mut()));
        false
    }

    /// Stage-2: times the shrink pass asked V8 for a low-memory GC.
    pub fn shrink_fires(&self) -> u32 {
        self.shrink_fires
    }

    /// Stage-2: (floor_bytes, ceil_bytes, granted_limit) for this engine.
    pub fn heap_band(&self) -> (usize, usize, usize) {
        (
            self.heap_floor,
            self.heap_ceil,
            self.current_limit.load(Ordering::SeqCst),
        )
    }

    /// Stage-2: override the envelope at runtime (MiB). The ceiling clamps
    /// future ladder bumps; the floor gates the shrink pass.
    pub fn set_heap_band(&mut self, floor_mb: usize, ceil_mb: usize) {
        self.heap_floor = floor_mb.saturating_mul(1024 * 1024);
        self.heap_ceil = ceil_mb.max(1).saturating_mul(1024 * 1024);
        // Keep the bridge's hard limit consistent with the new ceiling.
        let granted = self.current_limit.load(Ordering::SeqCst);
        let hard = if granted > 0 {
            granted.max(pressure_hard_limit(granted).min(self.heap_ceil))
        } else {
            self.heap_ceil
        };
        if let Some(bridge) = unsafe { self.bridge.as_mut() } {
            bridge.hard_limit = hard;
        }
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

    /// Set or replace the active Parena page arena.
    pub fn set_parena(&self, arena: parena::Parena) {
        if let Ok(mut p) = self.parena.lock() {
            *p = Some(arena);
        }
    }

    /// Fork the current page arena in microseconds.
    pub fn fork_parena(&self) -> Result<parena::Parena, String> {
        self.parena
            .lock()
            .map_err(|e| e.to_string())?
            .as_ref()
            .map(|p| p.fork())
            .ok_or_else(|| "no active parena in engine".to_string())
    }

    /// Capture a dirty-diff checkpoint of current arena state.
    pub fn checkpoint_parena(&self) -> Result<u64, String> {
        let mut checkpoints = self.checkpoints.lock().map_err(|e| e.to_string())?;
        let parena = self.parena.lock().map_err(|e| e.to_string())?;
        if let Some(p) = parena.as_ref() {
            let cp = p.checkpoint();
            let id = cp.id;
            checkpoints.insert(id, cp);
            Ok(id)
        } else {
            Err("no active parena in engine".to_string())
        }
    }

    /// Roll back the arena state to a previously captured checkpoint ID.
    pub fn rewind_parena(&self, checkpoint_id: u64) -> Result<usize, String> {
        let checkpoints = self.checkpoints.lock().map_err(|e| e.to_string())?;
        let cp = checkpoints
            .get(&checkpoint_id)
            .cloned()
            .ok_or_else(|| format!("checkpoint id {checkpoint_id} not found"))?;
        let mut parena = self.parena.lock().map_err(|e| e.to_string())?;
        if let Some(p) = parena.as_mut() {
            p.rewind(&cp)
        } else {
            Err("no active parena in engine".to_string())
        }
    }

    /// Zero-copy binary batch read of specified node IDs.
    pub fn batch_read_parena(&self, node_ids: &[u32]) -> Result<Vec<u8>, String> {
        let parena = self.parena.lock().map_err(|e| e.to_string())?;
        if let Some(p) = parena.as_ref() {
            Ok(p.batch_read(node_ids))
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
                match self.batch_read_parena(&node_ids) {
                    Ok(bytes) => protocol::DoshResponse {
                        status: protocol::DoshStatusCode::Ok as u8,
                        data_type: 4, // Buffer
                        reserved: 0,
                        result_len: bytes.len() as u32,
                        numeric_val: node_ids.len() as u64,
                        result_data: bytes.as_ptr(),
                    },
                    Err(e) => protocol::DoshResponse::error(&e),
                }
            }
            0x0D => {
                let drained = self.telemetry_ring.drain();
                protocol::DoshResponse::with_numeric(drained.len() as u64)
            }
            0x0E => {
                // ForkParena
                match self.fork_parena() {
                    Ok(_) => protocol::DoshResponse::ok(),
                    Err(e) => protocol::DoshResponse::error(&e),
                }
            }
            0x0F => {
                // CheckpointParena
                match self.checkpoint_parena() {
                    Ok(id) => protocol::DoshResponse::with_numeric(id),
                    Err(e) => protocol::DoshResponse::error(&e),
                }
            }
            0x10 => {
                // RewindParena
                match self.rewind_parena(cmd.param) {
                    Ok(bytes) => protocol::DoshResponse::with_numeric(bytes as u64),
                    Err(e) => protocol::DoshResponse::error(&e),
                }
            }
            _ => protocol::DoshResponse::error("unknown opcode"),
        }
    }
}

/// Create a binary snapshot blob pre-baking EXTENSIONS_SCRIPT and optional custom script.
pub fn create_dosh_snapshot(custom_script: Option<&str>) -> Result<Vec<u8>, String> {
    init_v8();

    let mut isolate = v8::Isolate::snapshot_creator(None, None);
    {
        v8::scope!(scope, &mut isolate);
        let context = v8::Context::new(scope, Default::default());
        let scope = &mut v8::ContextScope::new(scope, context);

        let ext_src = v8::String::new(scope, EXTENSIONS_SCRIPT).ok_or("extensions alloc failed")?;
        if let Some(script) = v8::Script::compile(scope, ext_src, None) {
            script.run(scope);
        }

        if let Some(extra) = custom_script {
            let extra_src = v8::String::new(scope, extra).ok_or("custom script alloc failed")?;
            if let Some(script) = v8::Script::compile(scope, extra_src, None) {
                script.run(scope);
            }
        }

        scope.set_default_context(context);
    }

    let blob = isolate
        .create_blob(v8::FunctionCodeHandling::Clear)
        .ok_or_else(|| "Failed to create V8 snapshot blob".to_string())?;

    Ok(blob.to_vec())
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

    // ------------------------------------------------------------------
    // Stage 1: pressure-graded near-heap-limit ladder
    // ------------------------------------------------------------------

    #[test]
    fn test_collectable_waves_survive_pressure() {
        // 24 MiB heap, then ~64 MiB of SHORT-LIVED garbage: the live set stays
        // tiny, so this load is collectable. The old callback terminated on
        // the FIRST near-limit hit, so garbage-heavy hydration died with
        // "out of memory" (the build.nvidia.com failure class). The ladder
        // must let V8's GC complete instead: this eval succeeds.
        // Timing-sensitive by nature (GC pacing), so a wall budget bounds it.
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 24 * 1024 * 1024).unwrap();
        engine.set_time_limit(20.0);
        let res = engine.eval(
            "var t = 0;\n\
             for (var i = 0; i < 64000; i++) { var s = 'x'.repeat(1024); t += s.length; }\n\
             t",
        );
        assert!(
            res.is_ok(),
            "collectable load must survive the pressure ladder, got: {:?}",
            res.err()
        );
        assert_eq!(res.unwrap(), "65536000"); // 64,000 x 1024 chars of collectable text
        // Ladder never reached genuine exhaustion.
        assert!(engine.pressure_epoch() < 2, "epoch: {}", engine.pressure_epoch());
    }

    #[test]
    fn test_genuine_growth_terminates_with_oom() {
        // 16 MiB heap, retained growth: every allocation stays live, so the
        // ladder's headroom is consumed without reclaim. The eval must die
        // with an honest "out of memory" (epoch >= 2), bounded by the hard
        // ceiling (limit + max(64 MiB, limit/2)) — not hang, not crash.
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 16 * 1024 * 1024).unwrap();
        engine.set_time_limit(30.0);
        let res = engine.eval(
            "var a = [];\n\
             for (;;) a.push('x'.repeat(65536));\n\
             a.length",
        );
        assert!(res.is_err());
        assert_eq!(res.unwrap_err(), "out of memory");
        assert!(engine.pressure_epoch() >= 2, "epoch: {}", engine.pressure_epoch());
    }

    #[test]
    fn test_pressure_epoch_resets_per_eval() {
        // One eval genuinely exhausts the heap. The NEXT eval on the same
        // isolate must start from a calm ladder — a poisoned epoch from page
        // A must not kill page B (the same regression class the facade's
        // engine-swap disarming had).
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 16 * 1024 * 1024).unwrap();
        engine.set_time_limit(30.0);
        let res = engine.eval("var a = []; for (;;) a.push('y'.repeat(65536)); a.length");
        assert_eq!(res.unwrap_err(), "out of memory");
        assert!(engine.pressure_epoch() >= 2);

        // Recovery: fresh eval, calm ladder, works.
        engine.set_time_limit(10.0);
        let ok = engine.eval("7 * 6");
        assert_eq!(ok.unwrap(), "42");
        assert_eq!(engine.pressure_epoch(), 0, "epoch must reset per eval");
    }

    // ------------------------------------------------------------------
    // Stage 0: external-memory reporting (Parena -> V8 pacer)
    // ------------------------------------------------------------------

    #[test]
    fn test_external_reporting_after_parena() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        assert_eq!(engine.external_reported(), 0, "nothing reported before a Parena exists");

        let mut arena = parena::Parena::default();
        let big_text = "x".repeat(200_000);
        arena.add_node(parena::TagId::Div, 0, &[("id", "root")], &big_text);
        let expected = arena.external_bytes();
        assert!(expected > 0, "arena must hold the big text");
        engine.set_parena(arena);

        // Any eval reports the host-side arena delta to V8's pacer.
        let ok = engine.eval("1 + 1");
        assert_eq!(ok.unwrap(), "2");
        assert_eq!(
            engine.external_reported(),
            expected,
            "arena bytes must be reported after the eval"
        );
    }

    // ------------------------------------------------------------------
    // Stage 2: measured heap envelope (band, shrink pass, ceiling clamp)
    // ------------------------------------------------------------------

    #[test]
    fn test_heap_band_surface_and_setter() {
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 64 * 1024 * 1024).unwrap();
        let (floor, _ceil, granted) = engine.heap_band();
        assert_eq!(granted, 64 * 1024 * 1024, "granted = configured limit");
        assert!(floor > 0, "default floor must exist");

        // Runtime override: floor/ceil move together; granted stays.
        engine.set_heap_band(8, 256);
        let (floor2, ceil2, granted2) = engine.heap_band();
        assert_eq!(floor2, 8 * 1024 * 1024);
        assert_eq!(ceil2, 256 * 1024 * 1024);
        assert_eq!(granted2, 64 * 1024 * 1024);
    }

    #[test]
    fn test_shrink_pass_fires_on_low_live_set() {
        // Waves of garbage, then everything dropped: the live set ends far
        // under 20% of the granted budget, so consecutive job-pump drains
        // must trip the shrink pass (low-memory GC request to V8).
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 64 * 1024 * 1024).unwrap();
        engine.set_time_limit(20.0);
        engine
            .eval("var keep = []; for (var i = 0; i < 2000; i++) keep.push('x'.repeat(1024)); keep.length")
            .unwrap();
        // Drop the live set and pump past the streak threshold.
        engine.eval("keep = null; 1").unwrap();
        let before = engine.shrink_fires();
        engine.pump_jobs();
        engine.pump_jobs();
        engine.pump_jobs();
        assert!(
            engine.shrink_fires() > before,
            "shrink pass must fire when the live set is far below the granted budget (fires: {} -> {})",
            before,
            engine.shrink_fires()
        );
    }

    #[test]
    fn test_shrink_pass_gated_by_floor() {
        // Floor ABOVE the granted budget: the shrink pass must never fire —
        // small heaps are allowed to stay small without churning GCs.
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 64 * 1024 * 1024).unwrap();
        engine.set_time_limit(20.0);
        engine.set_heap_band(256, 512); // floor 256 MiB > granted 64 MiB
        engine.eval("var k = []; for (var i = 0; i < 1000; i++) k.push('x'.repeat(1024)); k = null; 1").unwrap();
        for _ in 0..6 {
            engine.pump_jobs();
        }
        assert_eq!(engine.shrink_fires(), 0, "floor must gate the shrink pass");
    }

    #[test]
    fn test_ceiling_clamps_ladder() {
        // A tight ceiling must clamp the ladder's growth: genuine retained
        // growth dies at the ceiling with an honest "out of memory" rather
        // than growing past the envelope.
        let mut engine = DoshEngine::new_with_limit(Arc::new(DummySeam), 16 * 1024 * 1024).unwrap();
        engine.set_time_limit(30.0);
        engine.set_heap_band(8, 32); // ceiling 32 MiB (vs 80 MiB unclamped)
        let res = engine.eval("var a = []; for (;;) a.push('z'.repeat(65536)); a.length");
        assert_eq!(res.unwrap_err(), "out of memory");
        assert!(engine.pressure_epoch() >= 1);
    }

    // ------------------------------------------------------------------
    // Stage 3: Parena spill tier (cold tail to disk, hot prefix in RAM)
    // ------------------------------------------------------------------

    #[test]
    fn test_spill_moves_cold_tail_and_reads_stay_correct() {
        let mut arena = parena::Parena::default();
        let texts = ["a".repeat(50_000), "b".repeat(50_000), "c".repeat(50_000)];
        for t in &texts {
            arena.add_node(parena::TagId::Div, 0, &[], t);
        }
        let total = arena.text_len();
        assert_eq!(total, 150_000);

        // Spill with a 64 KiB watermark: the cold tail goes to disk.
        assert!(arena.spill_tail(64 * 1024), "spill must happen past the watermark");
        assert!(arena.spill_bytes() > 0, "cold tail must live on disk");
        assert_eq!(arena.text_len(), total, "logical text length is unchanged");
        assert!(
            arena.text_arena.len() <= 64 * 1024,
            "hot prefix must be at most the watermark, got {}",
            arena.text_arena.len()
        );

        // Every node's text must read back IDENTICAL through the tier —
        // hot reads, spilled reads and boundary-crossing reads included.
        for (i, t) in texts.iter().enumerate() {
            assert_eq!(&arena.get_text(i as u32), t, "node {} text corrupted by spill", i);
        }
    }

    #[test]
    fn test_spill_append_and_write_semantics() {
        let mut arena = parena::Parena::default();
        arena.add_node(parena::TagId::Div, 0, &[], &"x".repeat(100_000));
        assert!(arena.spill_tail(16 * 1024));

        // Append after spill: goes to the spill file; reads stay correct.
        let appended = "y".repeat(20_000);
        let n1 = arena.add_node(parena::TagId::P, 0, &[], &appended);
        assert_eq!(&arena.get_text(n1), &appended);
        assert!(arena.spill_bytes() > 20_000, "append must land in the spill");
        assert_eq!(arena.text_len(), 120_000);

        // Write to a spilled node: un-spills (re-resident), stays correct.
        let mutated = "z".repeat(30_000);
        assert!(arena.set_text(0, &mutated), "set_text must succeed");
        assert!(arena.spill.is_none(), "a text write must un-spill the arena");
        assert_eq!(&arena.get_text(0), &mutated);
        assert_eq!(arena.text_arena.len(), arena.text_len(), "fully resident after un-spill");
    }

    #[test]
    fn test_fork_of_spilled_arena_is_complete() {
        let mut arena = parena::Parena::default();
        let texts = ["h".repeat(40_000), "i".repeat(40_000), "j".repeat(40_000)];
        for t in &texts {
            arena.add_node(parena::TagId::Span, 0, &[], t);
        }
        assert!(arena.spill_tail(32 * 1024));

        // Fork must materialize the FULL logical text — a partial clone of
        // the hot prefix would silently lose every spilled node.
        let forked = arena.fork();
        assert!(forked.spill.is_none(), "fork is re-resident by construction");
        assert_eq!(forked.text_len(), 120_000);
        for (i, t) in texts.iter().enumerate() {
            assert_eq!(&forked.get_text(i as u32), t, "forked node {} corrupted", i);
        }
        // The source arena is untouched.
        for (i, t) in texts.iter().enumerate() {
            assert_eq!(&arena.get_text(i as u32), t);
        }
    }

    #[test]
    fn test_checkpoint_restore_across_spill() {
        let mut arena = parena::Parena::default();
        arena.add_node(parena::TagId::Div, 0, &[], "original text one");
        let cp = arena.checkpoint();
        assert!(arena.spill_tail(8)); // tiny watermark: everything spills
        assert!(arena.spill.is_some());

        // Mutate after spilling (this un-spills), then restore.
        assert!(arena.set_text(0, "CHANGED"));
        assert_ne!(&arena.get_text(0), "original text one");
        arena.rewind(&cp).unwrap();
        assert_eq!(&arena.get_text(0), "original text one", "restore must rewind the text");
    }

    #[test]
    fn test_engine_spill_reports_external_drop() {
        // Engine-level: spilling releases RAM and the pacer sees the drop.
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let mut arena = parena::Parena::default();
        arena.add_node(parena::TagId::Div, 0, &[], &"k".repeat(200_000));
        engine.set_parena(arena);

        // First eval reports the arena's resident bytes.
        engine.eval("1 + 1").unwrap();
        let before = engine.external_reported();
        assert!(before >= 200_000, "arena RAM must be reported, got {}", before);

        // Explicit spill at a 64 KiB watermark.
        assert!(engine.spill_parena_tail(64 * 1024));
        let after = engine.external_reported();
        assert!(
            after < before,
            "spill must release RAM and report the drop: {} -> {}",
            before,
            after
        );
    }

    // ------------------------------------------------------------------
    // Stage 4: pooled ArrayBuffer allocator (opt-in host accounting)
    // ------------------------------------------------------------------

    #[test]
    fn test_pooled_allocator_accounts_array_buffers() {
        // Shared global pool: serialize with the ab_pool unit tests that
        // toggle the switch (parallel #[test] runs race otherwise).
        let _g = ab_pool::TEST_LOCK.lock().unwrap_or_else(|e| e.into_inner());
        // Opt-in pool, then a big ArrayBuffer allocation: the pool must see
        // the backing store (live grows during the eval, peak tracks it).
        ab_pool::set_ab_pool_enabled(true);
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let before = ab_pool::ab_pool_stats();
        engine
            .eval("var ab = new ArrayBuffer(50 * 1024 * 1024); ab.byteLength")
            .unwrap();
        let after = ab_pool::ab_pool_stats();
        assert!(
            after.allocs > before.allocs,
            "the allocation must be counted (allocs {} -> {})",
            before.allocs,
            after.allocs
        );
        // Peak is a MAX, not a sum: the 50 MiB backing store must APPEAR in
        // it, and it must never decrease (a parallel test may have already
        // pushed peak above zero through the shared pool).
        assert!(
            after.peak >= 50 * 1024 * 1024,
            "peak must track the 50 MiB backing store (peak {} -> {})",
            before.peak,
            after.peak
        );
        assert!(after.peak >= before.peak, "peak must never decrease");
        // Leave the switch in its default-off state for other tests.
        ab_pool::set_ab_pool_enabled(false);
    }

    #[test]
    fn test_snapshot_creator_api() {
        init_v8();

        let mut isolate = v8::Isolate::snapshot_creator(None, None);
        {
            v8::scope!(scope, &mut isolate);
            let context = v8::Context::new(scope, Default::default());
            let scope = &mut v8::ContextScope::new(scope, context);

            // Compile EXTENSIONS_SCRIPT into snapshot
            let ext_src = v8::String::new(scope, EXTENSIONS_SCRIPT).unwrap();
            let script = v8::Script::compile(scope, ext_src, None).unwrap();
            script.run(scope);

            let minidom_src_str = include_str!("../../../webresearch/engine/minidom.js");
            let minidom_src = v8::String::new(scope, minidom_src_str).unwrap();
            let minidom_script = v8::Script::compile(scope, minidom_src, None).unwrap();
            minidom_script.run(scope);

            scope.set_default_context(context);
        }

        let blob = isolate.create_blob(v8::FunctionCodeHandling::Clear).expect("blob");
        assert!(!blob.is_empty());

        // Now create a normal isolate from this snapshot blob!
        let params = v8::CreateParams::default().snapshot_blob(blob);
        let mut iso2 = v8::Isolate::new(params);
        {
            v8::scope!(scope, &mut iso2);
            let ctx2 = v8::Context::new(scope, Default::default());
            let scope = &mut v8::ContextScope::new(scope, ctx2);

            let code = v8::String::new(scope, "typeof globalThis.__load + ' ' + typeof globalThis.dosh.text").unwrap();
            let script = v8::Script::compile(scope, code, None).unwrap();
            let res = script.run(scope).unwrap();
            let s = res.to_rust_string_lossy(scope);
            assert_eq!(s, "function function");
        }
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

    #[test]
    fn test_in_realm_dosh_checkpoint_and_rewind() {
        let mut engine = DoshEngine::new(Arc::new(DummySeam)).unwrap();
        let mut parena = parena::Parena::new();
        let n1 = parena.add_node(parena::TagId::Div, 0, &[("id", "root")], "Initial Text");
        engine.set_parena(parena);

        // Capture checkpoint via JS dosh.checkpoint()
        let cp_res = engine.eval("dosh.checkpoint()").expect("checkpoint from JS");
        let cp_id: u64 = cp_res.parse().expect("parsed checkpoint id");
        assert!(cp_id > 0);

        // Mutate node in parena
        {
            let mut guard = engine.parena.lock().unwrap();
            let p = guard.as_mut().unwrap();
            p.set_text(n1, "Mutated Text in Action");
            assert_eq!(p.get_text(n1), "Mutated Text in Action");
        }

        // Rewind via JS dosh.rewind(cp_id)
        let rew_res = engine.eval(&format!("dosh.rewind({cp_id})")).expect("rewind from JS");
        let reverted_bytes: usize = rew_res.parse().expect("parsed reverted bytes");
        assert!(reverted_bytes > 0);

        // Verify node text reverted to initial
        {
            let guard = engine.parena.lock().unwrap();
            let p = guard.as_ref().unwrap();
            assert_eq!(p.get_text(n1), "Initial Text");
        }
    }

    #[test]
    fn test_engine_boot_from_snapshot() {
        let snapshot_blob = create_dosh_snapshot(None).expect("snapshot creation");
        assert!(!snapshot_blob.is_empty());

        let mut engine = DoshEngine::new_with_snapshot(Arc::new(DummySeam), &snapshot_blob)
            .expect("engine booted from snapshot");

        // Evaluates immediately without needing to re-compile extensions
        let res = engine.eval("typeof globalThis.Butterfly + ' ' + typeof globalThis.dosh.text").unwrap();
        assert_eq!(res, "function function");
    }
}
