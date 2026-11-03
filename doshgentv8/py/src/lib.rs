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

use doshgentv8_core::{DoshEngine, HostSeam};
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
}

#[pymethods]
impl DoshContext {
    #[new]
    fn new() -> PyResult<Self> {
        let host = Arc::new(SharedPyHost(Mutex::new(PyHost {
            callables: HashMap::new(),
        })));
        let engine = DoshEngine::new(host.clone()).map_err(PyRuntimeError::new_err)?;
        Ok(Self { engine, host })
    }

    /// Evaluate JS; returns the result as a string (quickjs parity).
    fn eval(&mut self, code: &str) -> PyResult<String> {
        self.engine.eval(code).map_err(PyRuntimeError::new_err)
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
        self.engine.set_memory_limit(bytes as usize);
    }

    /// Wall-clock budget per eval, in seconds. 0 disables.
    fn set_time_limit(&mut self, seconds: f64) {
        self.engine.set_time_limit(seconds);
    }

    /// Accepted for quickjs.Context parity. V8 manages its own stack guards,
    /// so there is no separate host-set stack limit to configure.
    fn set_max_stack_size(&mut self, _bytes: u64) {}
}

#[pymodule]
fn doshgentv8_rust(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<DoshContext>()?;
    Ok(())
}
