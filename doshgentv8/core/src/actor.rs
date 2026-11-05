//! `actor.rs` — Resident V8 Actor Model (`DoshActor`).
//!
//! Upstream V8 `Isolate` is strictly `!Send` and `!Sync`. In a multi-threaded
//! or asynchronous agent browser runtime, pinning each V8 Isolate to a dedicated
//! resident actor thread allows:
//!
//! 1. Thread-safe execution: `DoshActorHandle` is `Send + Sync + Clone`.
//! 2. Zero-teardown state persistence: The DOM, JS closures, prototype chains,
//!    event listeners, and global variables remain warm and alive across requests.
//! 3. Bounded communication: Commands are delivered over bounded MPSC channels
//!    and results returned synchronously or asynchronously via oneshot reply channels.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{channel, Receiver, Sender};
use std::sync::Arc;

use crate::protocol::{DoshButterflyRect, DoshCommand, DoshResponse};
use crate::{DoshEngine, HostSeam, JsValue};

static ACTOR_ID_GEN: AtomicU64 = AtomicU64::new(1);

/// Commands dispatched to the resident V8 actor thread.
pub enum ActorCommand {
    Eval {
        code: String,
        reply: Sender<Result<String, String>>,
    },
    EvalRaw {
        code: String,
        reply: Sender<Result<JsValue, String>>,
    },
    PumpJobs {
        reply: Sender<bool>,
    },
    AddHost {
        name: String,
        reply: Sender<Result<(), String>>,
    },
    SetTimeLimit {
        seconds: f64,
        reply: Sender<()>,
    },
    SetFuel {
        fuel: u64,
        reply: Sender<()>,
    },
    FuelRemaining {
        reply: Sender<u64>,
    },
    SetVirtualTime {
        millis: f64,
        reply: Sender<Result<(), String>>,
    },
    AdvanceVirtualTime {
        delta_millis: f64,
        reply: Sender<Result<(), String>>,
    },
    VirtualTime {
        reply: Sender<Option<f64>>,
    },
    SetRandomSeed {
        seed: u64,
        reply: Sender<Result<(), String>>,
    },
    ResetRandom {
        reply: Sender<Result<(), String>>,
    },
    SetEvalAllowed {
        allowed: bool,
        reply: Sender<Result<(), String>>,
    },
    IsEvalAllowed {
        reply: Sender<bool>,
    },
    SetWasmAllowed {
        allowed: bool,
        reply: Sender<Result<(), String>>,
    },
    IsWasmAllowed {
        reply: Sender<bool>,
    },
    SetNetworkAllowed {
        allowed: bool,
        reply: Sender<()>,
    },
    IsNetworkAllowed {
        reply: Sender<bool>,
    },
    SetWaasmCapability {
        cap: String,
        allowed: bool,
        reply: Sender<Result<(), String>>,
    },
    IsWaasmCapabilityAllowed {
        cap: String,
        reply: Sender<bool>,
    },
    SetLayoutRects {
        rects: Vec<DoshButterflyRect>,
        reply: Sender<()>,
    },
    GetLayoutRects {
        reply: Sender<Vec<DoshButterflyRect>>,
    },
    DispatchCommand {
        cmd: DoshCommand,
        reply: Sender<DoshResponse>,
    },
    GetHeapStats {
        reply: Sender<(usize, usize)>, // (used, total)
    },
    EvalCount {
        reply: Sender<usize>,
    },
    Shutdown,
}

struct ActorInner {
    id: u64,
    sender: Sender<ActorCommand>,
}

impl Drop for ActorInner {
    fn drop(&mut self) {
        let _ = self.sender.send(ActorCommand::Shutdown);
    }
}

/// A cloneable, thread-safe (`Send + Sync`) handle to a resident V8 isolate actor.
#[derive(Clone)]
pub struct DoshActorHandle {
    inner: Arc<ActorInner>,
}

impl DoshActorHandle {
    #[inline]
    pub fn id(&self) -> u64 {
        self.inner.id
    }

    /// Evaluate JavaScript and return the stringified result.
    pub fn eval(&self, code: &str) -> Result<String, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::Eval {
                code: code.to_string(),
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Evaluate JavaScript and return the raw `JsValue`.
    pub fn eval_raw(&self, code: &str) -> Result<JsValue, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::EvalRaw {
                code: code.to_string(),
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Run microtasks checkpoint.
    pub fn pump_jobs(&self) -> bool {
        let (tx, rx) = channel();
        if self.inner.sender.send(ActorCommand::PumpJobs { reply: tx }).is_err() {
            return false;
        }
        rx.recv().unwrap_or(false)
    }

    /// Register a host callable name in globalThis.
    pub fn add_host(&self, name: &str) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::AddHost {
                name: name.to_string(),
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Set wall-clock execution limit in seconds.
    pub fn set_time_limit(&self, seconds: f64) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetTimeLimit { seconds, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Set fuel budget for cooperative bytecode metering.
    pub fn set_fuel(&self, fuel: u64) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetFuel { fuel, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Query remaining fuel.
    pub fn fuel_remaining(&self) -> Result<u64, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::FuelRemaining { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Inject virtual timestamp.
    pub fn set_virtual_time(&self, millis: f64) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetVirtualTime { millis, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Advance virtual timestamp.
    pub fn advance_virtual_time(&self, delta_millis: f64) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::AdvanceVirtualTime {
                delta_millis,
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Query current virtual time.
    pub fn virtual_time(&self) -> Result<Option<f64>, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::VirtualTime { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Set PRNG seed.
    pub fn set_random_seed(&self, seed: u64) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetRandomSeed { seed, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Reset PRNG seed.
    pub fn reset_random(&self) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::ResetRandom { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Allow or disallow dynamic code generation.
    pub fn set_eval_allowed(&self, allowed: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetEvalAllowed { allowed, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    pub fn is_eval_allowed(&self) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsEvalAllowed { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Allow or disallow WebAssembly.
    pub fn set_wasm_allowed(&self, allowed: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetWasmAllowed { allowed, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    pub fn is_wasm_allowed(&self) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsWasmAllowed { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Allow or disallow host network calls.
    pub fn set_network_allowed(&self, allowed: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetNetworkAllowed { allowed, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    pub fn is_network_allowed(&self) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsNetworkAllowed { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Allow or disallow specific WAASM capability.
    pub fn set_waasm_capability(&self, cap: &str, allowed: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetWaasmCapability {
                cap: cap.to_string(),
                allowed,
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    pub fn is_waasm_capability_allowed(&self, cap: &str) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsWaasmCapabilityAllowed {
                cap: cap.to_string(),
                reply: tx,
            })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Update layout rectangle cache.
    pub fn set_layout_rects(&self, rects: Vec<DoshButterflyRect>) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetLayoutRects { rects, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Retrieve layout rectangle cache.
    pub fn get_layout_rects(&self) -> Result<Vec<DoshButterflyRect>, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::GetLayoutRects { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Dispatch in-memory DCP command directly.
    pub fn dispatch_command(&self, cmd: DoshCommand) -> Result<DoshResponse, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::DispatchCommand { cmd, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Query heap statistics: `(used_heap_bytes, total_heap_bytes)`.
    pub fn get_heap_stats(&self) -> Result<(usize, usize), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::GetHeapStats { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Query number of completed script evaluations.
    pub fn eval_count(&self) -> Result<usize, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::EvalCount { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Explicitly request the actor to shut down.
    pub fn shutdown(&self) {
        let _ = self.inner.sender.send(ActorCommand::Shutdown);
    }
}

/// The resident V8 actor spawner.
pub struct DoshActor;

impl DoshActor {
    /// Spawn a new resident V8 actor on a dedicated OS thread.
    pub fn spawn(seam: Arc<dyn HostSeam>, memory_limit: usize) -> Result<DoshActorHandle, String> {
        let id = ACTOR_ID_GEN.fetch_add(1, Ordering::Relaxed);
        let (cmd_tx, cmd_rx) = channel::<ActorCommand>();
        let (init_tx, init_rx) = channel::<Result<(), String>>();

        let thread_name = format!("dosh-actor-{}", id);
        std::thread::Builder::new()
            .name(thread_name)
            .spawn(move || {
                let mut engine = match DoshEngine::new_with_limit(seam, memory_limit) {
                    Ok(e) => {
                        let _ = init_tx.send(Ok(()));
                        e
                    }
                    Err(err) => {
                        let _ = init_tx.send(Err(err));
                        return;
                    }
                };

                Self::actor_loop(&mut engine, cmd_rx);
            })
            .map_err(|e| format!("failed to spawn actor thread: {e}"))?;

        // Synchronously wait for isolate initialization
        init_rx
            .recv()
            .map_err(|e| format!("actor init channel recv error: {e}"))??;

        Ok(DoshActorHandle {
            inner: Arc::new(ActorInner { id, sender: cmd_tx }),
        })
    }

    fn actor_loop(engine: &mut DoshEngine, receiver: Receiver<ActorCommand>) {
        while let Ok(cmd) = receiver.recv() {
            match cmd {
                ActorCommand::Eval { code, reply } => {
                    let res = engine.eval(&code);
                    let _ = reply.send(res);
                }
                ActorCommand::EvalRaw { code, reply } => {
                    let res = engine.eval_raw(&code);
                    let _ = reply.send(res);
                }
                ActorCommand::PumpJobs { reply } => {
                    let drained = engine.pump_jobs();
                    let _ = reply.send(drained);
                }
                ActorCommand::AddHost { name, reply } => {
                    let res = engine.add_host(&name);
                    let _ = reply.send(res);
                }
                ActorCommand::SetTimeLimit { seconds, reply } => {
                    engine.set_time_limit(seconds);
                    let _ = reply.send(());
                }
                ActorCommand::SetFuel { fuel, reply } => {
                    engine.set_fuel(fuel);
                    let _ = reply.send(());
                }
                ActorCommand::FuelRemaining { reply } => {
                    let rem = engine.fuel_remaining();
                    let _ = reply.send(rem);
                }
                ActorCommand::SetVirtualTime { millis, reply } => {
                    let res = engine.set_virtual_time(millis);
                    let _ = reply.send(res);
                }
                ActorCommand::AdvanceVirtualTime {
                    delta_millis,
                    reply,
                } => {
                    let res = engine.advance_virtual_time(delta_millis);
                    let _ = reply.send(res);
                }
                ActorCommand::VirtualTime { reply } => {
                    let vt = engine.virtual_time();
                    let _ = reply.send(vt);
                }
                ActorCommand::SetRandomSeed { seed, reply } => {
                    let res = engine.set_random_seed(seed);
                    let _ = reply.send(res);
                }
                ActorCommand::ResetRandom { reply } => {
                    let res = engine.reset_random();
                    let _ = reply.send(res);
                }
                ActorCommand::SetEvalAllowed { allowed, reply } => {
                    let res = engine.set_eval_allowed(allowed);
                    let _ = reply.send(res);
                }
                ActorCommand::IsEvalAllowed { reply } => {
                    let res = engine.is_eval_allowed();
                    let _ = reply.send(res);
                }
                ActorCommand::SetWasmAllowed { allowed, reply } => {
                    let res = engine.set_wasm_allowed(allowed);
                    let _ = reply.send(res);
                }
                ActorCommand::IsWasmAllowed { reply } => {
                    let res = engine.is_wasm_allowed();
                    let _ = reply.send(res);
                }
                ActorCommand::SetNetworkAllowed { allowed, reply } => {
                    engine.set_network_allowed(allowed);
                    let _ = reply.send(());
                }
                ActorCommand::IsNetworkAllowed { reply } => {
                    let res = engine.is_network_allowed();
                    let _ = reply.send(res);
                }
                ActorCommand::SetWaasmCapability {
                    cap,
                    allowed,
                    reply,
                } => {
                    let res = engine.set_waasm_capability(&cap, allowed);
                    let _ = reply.send(res);
                }
                ActorCommand::IsWaasmCapabilityAllowed { cap, reply } => {
                    let res = engine.is_waasm_capability_allowed(&cap);
                    let _ = reply.send(res);
                }
                ActorCommand::SetLayoutRects { rects, reply } => {
                    engine.set_layout_rects(rects);
                    let _ = reply.send(());
                }
                ActorCommand::GetLayoutRects { reply } => {
                    let rects = engine.get_layout_rects().to_vec();
                    let _ = reply.send(rects);
                }
                ActorCommand::DispatchCommand { cmd, reply } => {
                    let resp = engine.dispatch_command(&cmd);
                    let _ = reply.send(resp);
                }
                ActorCommand::GetHeapStats { reply } => {
                    let used = engine.used_heap_size();
                    let total = engine.total_heap_size();
                    let _ = reply.send((used, total));
                }
                ActorCommand::EvalCount { reply } => {
                    let count = engine.eval_count();
                    let _ = reply.send(count);
                }
                ActorCommand::Shutdown => {
                    break;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    struct DummySeam;
    impl HostSeam for DummySeam {
        fn call(&self, name: &str, args: &[String]) -> Result<Option<String>, String> {
            if name == "greet" {
                Ok(Some(format!("Hello, {}", args.join(" "))))
            } else {
                Ok(None)
            }
        }
    }

    #[test]
    fn test_actor_lifecycle_and_eval() {
        let actor = DoshActor::spawn(Arc::new(DummySeam), 0).expect("failed to spawn actor");

        // Resident evaluation 1
        let res1 = actor.eval("1 + 2").expect("eval failed");
        assert_eq!(res1, "3");

        // Resident state persistence: define variable
        actor.eval("var myState = { counter: 42 };").expect("eval failed");

        // Resident evaluation 2: verify variable persists in same isolate
        let res2 = actor.eval("myState.counter += 1; myState.counter").expect("eval failed");
        assert_eq!(res2, "43");

        // Thread safety: clone actor and run from another OS thread
        let actor_clone = actor.clone();
        let handle = std::thread::spawn(move || {
            actor_clone.eval("myState.counter * 2").expect("eval in thread failed")
        });
        let thread_res = handle.join().expect("thread join failed");
        assert_eq!(thread_res, "86");

        // Host callable in actor
        actor.add_host("greet").expect("add host failed");
        let greeted = actor.eval("greet('Agentic', 'Actor')").expect("greet failed");
        assert_eq!(greeted, "Hello, Agentic Actor");

        // Check heap stats
        let (used, total) = actor.get_heap_stats().expect("heap stats failed");
        assert!(used > 0);
        assert!(total >= used);

        actor.shutdown();
    }
}
