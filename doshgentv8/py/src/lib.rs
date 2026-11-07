//! doshgentv8-py — thin PyO3 facade over doshgentv8-core.
//!
//! This crate is deliberately the ONLY throwaway piece: the engine logic all
//! lives in doshgentv8-core behind the `HostSeam` trait. When the product
//! rewrites to pure Rust, core is consumed directly and this file disappears.
//!
//! Surface mirrors `quickjs.Context` (the seam `webresearch/engine/runtime.py`
//! and `webresearch/shell.py` already speak): `eval`, `add_callable`,
//! `execute_pending_job`, `set_memory_limit`, `set_time_limit`,
//! `set_max_stack_size`.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use doshgentv8_core::{DoshEngine, HostSeam, JsValue};
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::PyTuple;

struct PyHost {
    callables: HashMap<String, Py<PyAny>>,
}

impl PyHost {
    fn call_inner(
        &self,
        py: Python<'_>,
        name: &str,
        args: &[String],
    ) -> Result<Option<String>, String> {
        let cb = self
            .callables
            .get(name)
            .ok_or_else(|| format!("no host callable registered: {name}"))?;
        let tuple = PyTuple::new(py, args.to_vec()).map_err(|e| format!("arg marshal: {e}"))?;
        let res = cb
            .call(py, tuple, None)
            .map_err(|e| format!("host {name} raised: {e}"))?;
        if res.is_none(py) {
            return Ok(None);
        }
        match res.extract::<String>(py) {
            Ok(s) => Ok(Some(s)),
            Err(e) => Err(format!("host {name} returned a non-string: {e}")),
        }
    }
}

// Local newtype so the orphan rule is satisfied (HostSeam is doshgentv8-core's
// trait; Mutex is std's type).
struct SharedPyHost(Mutex<PyHost>);

impl HostSeam for SharedPyHost {
    fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
        Python::attach(|py| {
            let host = self.0.lock().map_err(|_| "host lock poisoned")?;
            host.call_inner(py, name, args)
        })
    }
}

/// A V8 execution context. Drop-in for `quickjs.Context` in the product seam.
#[pyclass(unsendable)]
struct DoshContext {
    engine: DoshEngine,
    host: Arc<SharedPyHost>,
    /// Wall-clock budget last set via set_time_limit. Tracked here because
    /// set_memory_limit may swap in a fresh engine (heap limits are fixed at
    /// isolate creation); without re-applying, the swap silently disarmed the
    /// watchdog. 0.0 = disabled.
    wall_limit: f64,
}

#[pymethods]
impl DoshContext {
    #[new]
    #[pyo3(signature = (memory_limit = None))]
    fn new(memory_limit: Option<usize>) -> PyResult<Self> {
        let host = Arc::new(SharedPyHost(Mutex::new(PyHost {
            callables: HashMap::new(),
        })));
        let engine = DoshEngine::new_with_limit(host.clone(), memory_limit.unwrap_or(0))
            .map_err(PyRuntimeError::new_err)?;
        Ok(Self { engine, host, wall_limit: 0.0 })
    }

    /// Evaluate JS; returns typed Python value (quickjs parity).
    fn eval(&mut self, py: Python<'_>, code: &str) -> PyResult<Py<PyAny>> {
        let val = self.engine.eval_raw(code).map_err(PyRuntimeError::new_err)?;
        match val {
            JsValue::Null | JsValue::Undefined => Ok(py.None()),
            JsValue::Bool(b) => Ok(b.into_pyobject(py)?.to_owned().into_any().unbind()),
            JsValue::Int(i) => Ok(i.into_pyobject(py)?.to_owned().into_any().unbind()),
            JsValue::Float(f) => Ok(f.into_pyobject(py)?.to_owned().into_any().unbind()),
            JsValue::String(s) => Ok(s.into_pyobject(py)?.to_owned().into_any().unbind()),
        }
    }

    /// Register a synchronous Python callable under `name` in the JS realm.
    /// String in, string out (or None -> undefined) — the seam contract.
    fn add_callable(&mut self, name: &str, callback: Py<PyAny>) -> PyResult<()> {
        {
            let mut host =
                self.host.0.lock().map_err(|_| PyRuntimeError::new_err("host lock poisoned"))?;
            host.callables.insert(name.to_string(), callback);
        }
        self.engine.add_host(name).map_err(PyRuntimeError::new_err)
    }

    /// Drain pending microtasks. One V8 checkpoint drains the whole queue, so
    /// this returns false (the host loop `while ctx.execute_pending_job()`
    /// runs one drain and stops).
    fn execute_pending_job(&mut self) -> bool {
        self.engine.pump_jobs()
    }

    /// Heap budget in bytes (applied at isolate construction; see core docs).
    fn set_memory_limit(&mut self, bytes: u64) {
        if self.engine.eval_count() == 0 {
            if let Ok(mut new_engine) = DoshEngine::new_with_limit(self.host.clone(), bytes as usize) {
                if let Ok(guard) = self.host.0.lock() {
                    for name in guard.callables.keys() {
                        let _ = new_engine.add_host(name);
                    }
                }
                // Preserve the armed wall-clock budget across the engine
                // swap: the fresh engine defaults to wall_limit=0 (watchdog
                // disarmed), which previously silently cancelled a
                // set_time_limit issued before this call.
                new_engine.set_time_limit(self.wall_limit);
                self.engine = new_engine;
                return;
            }
        }
        self.engine.set_memory_limit(bytes as usize);
    }

    /// Wall-clock budget per eval, in seconds. 0 disables.
    fn set_time_limit(&mut self, seconds: f64) {
        self.wall_limit = seconds;
        self.engine.set_time_limit(seconds);
    }

    /// Accepted for quickjs.Context parity. V8 manages its own stack guards,
    /// so there is no separate host-set stack limit to configure.
    fn set_max_stack_size(&mut self, _bytes: u64) {}

    /// Heap-pressure epoch of the current eval (0 = calm, >=2 = exhaustion hit).
    /// Lets the Python lane observe GC pressure for eviction decisions.
    fn pressure_epoch(&mut self) -> u8 {
        self.engine.pressure_epoch()
    }

    /// Stage-5: measured heap envelope — (floor_bytes, ceil_bytes,
    /// granted_bytes). Lets hosts size sessions and budget pressure.
    fn heap_band(&mut self) -> (u64, u64, u64) {
        let (f, c, g) = self.engine.heap_band();
        (f as u64, c as u64, g as u64)
    }

    /// Stage-5: times the shrink pass asked V8 for a low-memory GC.
    fn shrink_fires(&mut self) -> u32 {
        self.engine.shrink_fires()
    }

    /// Stage-5: Parena residency — (ram_bytes, spill_bytes, text_bytes).
    /// The active/passive split of the page arena, observable per context.
    fn parena_stats(&mut self) -> (u64, u64, u64) {
        let (r, s, t) = self.engine.parena_stats();
        (r as u64, s as u64, t as u64)
    }

    /// Host bytes currently reported to V8's external-memory pacer.
    fn external_reported(&mut self) -> usize {
        self.engine.external_reported()
    }

    /// Explicit close / dispose hook for parity.
    fn close(&mut self) {}

    fn heap_used(&mut self) -> usize {
        self.engine.used_heap_size()
    }

    fn heap_total(&mut self) -> usize {
        self.engine.total_heap_size()
    }

    /// Set deterministic instruction/step fuel limit.
    fn set_fuel(&mut self, fuel: u64) {
        self.engine.set_fuel(fuel);
    }

    fn fuel_remaining(&self) -> u64 {
        self.engine.fuel_remaining()
    }

    fn disable_fuel(&mut self) {
        self.engine.disable_fuel();
    }

    /// Set deterministic virtual host clock in milliseconds.
    fn set_virtual_time(&mut self, millis: f64) -> PyResult<()> {
        self.engine.set_virtual_time(millis).map_err(PyRuntimeError::new_err)
    }

    fn advance_virtual_time(&mut self, delta_millis: f64) -> PyResult<()> {
        self.engine.advance_virtual_time(delta_millis).map_err(PyRuntimeError::new_err)
    }

    fn virtual_time(&self) -> Option<f64> {
        self.engine.virtual_time()
    }

    /// Set deterministic PRNG seed for Math.random.
    fn set_random_seed(&mut self, seed: u64) -> PyResult<()> {
        self.engine.set_random_seed(seed).map_err(PyRuntimeError::new_err)
    }

    fn reset_random(&mut self) -> PyResult<()> {
        self.engine.reset_random().map_err(PyRuntimeError::new_err)
    }

    /// Capability Gate: allow or disallow eval() and Function constructor.
    fn set_eval_allowed(&mut self, allowed: bool) -> PyResult<()> {
        self.engine.set_eval_allowed(allowed).map_err(PyRuntimeError::new_err)
    }

    fn is_eval_allowed(&self) -> bool {
        self.engine.is_eval_allowed()
    }

    /// Capability Gate: allow or disallow WebAssembly.
    fn set_wasm_allowed(&mut self, allowed: bool) -> PyResult<()> {
        self.engine.set_wasm_allowed(allowed).map_err(PyRuntimeError::new_err)
    }

    fn is_wasm_allowed(&self) -> bool {
        self.engine.is_wasm_allowed()
    }

    /// Capability Gate: allow or disallow host network calls.
    fn set_network_allowed(&mut self, allowed: bool) {
        self.engine.set_network_allowed(allowed);
    }

    fn is_network_allowed(&self) -> bool {
        self.engine.is_network_allowed()
    }
}

/// Stage-5: process-global pooled ArrayBuffer allocator stats —
/// (live_bytes, peak_bytes, allocs, budget_bytes). Requires WR_AB_POOL=1
/// (or the Rust-side switch) at isolate creation; stats exist regardless.
#[pyfunction]
fn ab_pool_stats_py() -> (u64, u64, u64, u64) {
    let s = doshgentv8_core::ab_pool::ab_pool_stats();
    (s.live as u64, s.peak as u64, s.allocs, s.budget as u64)
}

#[pymodule]
fn doshgentv8_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DoshContext>()?;
    m.add_function(wrap_pyfunction!(ab_pool_stats_py, m)?)?;
    Ok(())
}
