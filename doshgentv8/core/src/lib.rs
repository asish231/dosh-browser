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
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::thread;
use std::time::Duration;

/// Synchronous host seam. `args` are the JS arguments stringified (the same
/// marshalling the QuickJS `add_callable` lane uses). `Ok(Some(s))` becomes a
/// JS string, `Ok(None)` becomes `undefined`, and `Err(msg)` becomes a
/// catchable JS exception — unlike QuickJS's deferred SystemError, which no
/// JS try/catch could intercept.
pub trait HostSeam {
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

pub(crate) struct HostBridge {
    pub seam: Arc<dyn HostSeam>,
    pub oom: Arc<AtomicBool>,
    pub interrupt_handle: v8::IsolateHandle,
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

/// A V8 isolate + context pair, host-driven: microtasks are explicit and the
/// pump is `pump_jobs()`. One per browser context, like QuickJS `Context`.
pub struct DoshEngine {
    // Declared first: Global<Context> must drop while the isolate exists
    // (rusty_v8's Global Drop is also isolate-liveness aware, verified).
    context: v8::Global<v8::Context>,
    isolate: v8::OwnedIsolate,
    bridge: *mut HostBridge,
    interrupt_handle: v8::IsolateHandle,
    dropped: Arc<AtomicBool>,
    wall_gen: Arc<AtomicU64>,
    wall_limit: f64,
    memory_limit: usize,
    oom: Arc<AtomicBool>,
    eval_count: usize,
}

impl DoshEngine {
    pub fn new(seam: Arc<dyn HostSeam>) -> Result<Self, String> {
        Self::new_with_limit(seam, 0)
    }

    pub fn new_with_limit(seam: Arc<dyn HostSeam>, memory_limit: usize) -> Result<Self, String> {
        static V8_INIT: std::sync::Once = std::sync::Once::new();
        V8_INIT.call_once(|| {
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

        let bridge = Box::into_raw(Box::new(HostBridge {
            seam,
            oom: oom.clone(),
            interrupt_handle: interrupt_handle.clone(),
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
            dropped: Arc::new(AtomicBool::new(false)),
            wall_gen: Arc::new(AtomicU64::new(0)),
            wall_limit: 0.0,
            memory_limit,
            oom,
            eval_count: 0,
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
        self.isolate.cancel_terminate_execution();

        // Per-eval wall-clock watchdog, only armed when a limit is set.
        let watchdog = if self.wall_limit > 0.0 {
            let gen = self.wall_gen.fetch_add(1, Ordering::SeqCst) + 1;
            let handle = self.interrupt_handle.clone();
            let wall_gen = self.wall_gen.clone();
            let dropped = self.dropped.clone();
            let limit = self.wall_limit;
            thread::spawn(move || {
                thread::sleep(Duration::from_secs_f64(limit));
                if !dropped.load(Ordering::SeqCst) && wall_gen.load(Ordering::SeqCst) == gen {
                    handle.terminate_execution();
                }
            });
            true
        } else {
            false
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
        if watchdog {
            // Disarm: the sleeping thread sees a stale token and no-ops.
            self.wall_gen.fetch_add(1, Ordering::SeqCst);
        }
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
}

impl Drop for DoshEngine {
    fn drop(&mut self) {
        self.dropped.store(true, Ordering::SeqCst);
        let bridge = self.bridge;
        self.bridge = std::ptr::null_mut();
        unsafe {
            drop(Box::from_raw(bridge));
            self.isolate.enter();
        }
    }
}
