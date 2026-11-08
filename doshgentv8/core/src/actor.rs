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

use crate::protocol::{DoshButterflyRect, DoshCommand, DoshOwnedResponse, DoshResponse};
use crate::{DoshEngine, HostSeam, JsValue};

/// Full live heap, envelope, pressure, and Parena snapshot from a resident actor.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ActorHeapReport {
    pub used_heap_bytes: usize,
    pub total_heap_bytes: usize,
    pub requested_limit_bytes: usize,
    pub floor_bytes: usize,
    pub ceil_bytes: usize,
    pub granted_bytes: usize,
    pub pressure_epoch: u8,
    pub external_reported_bytes: usize,
    pub shrink_fires: u32,
    pub parena_ram_bytes: usize,
    pub parena_spill_bytes: usize,
    pub parena_text_bytes: usize,
    pub eval_count: usize,
}

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
    EvalBounded {
        code: String,
        control_realm: bool,
        allow_actions: bool,
        args: Vec<String>,
        reply: Sender<Result<String, String>>,
    },
    SetActionAllowed {
        allowed: bool,
        reply: Sender<Result<(), String>>,
    },
    IsActionAllowed {
        reply: Sender<bool>,
    },
    SetControlRealm {
        enabled: bool,
        reply: Sender<Result<(), String>>,
    },
    IsControlRealm {
        reply: Sender<bool>,
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
    DispatchOwned {
        cmd: DoshCommand,
        reply: Sender<DoshOwnedResponse>,
    },
    GetHeapStats {
        reply: Sender<(usize, usize)>, // (used, total)
    },
    GetHeapReport {
        reply: Sender<ActorHeapReport>,
    },
    EvalCount {
        reply: Sender<usize>,
    },
    PollTelemetryRing {
        reply: Sender<Vec<(u32, crate::protocol::DoshEventKind, String, u64)>>,
    },
    ForkParena {
        reply: Sender<Result<crate::parena::Parena, String>>,
    },
    CheckpointParena {
        reply: Sender<Result<u64, String>>,
    },
    RewindParena {
        checkpoint_id: u64,
        reply: Sender<Result<usize, String>>,
    },
    BatchReadParena {
        node_ids: Vec<u32>,
        reply: Sender<Result<Vec<u8>, String>>,
    },
    SetParena {
        parena: crate::parena::Parena,
        reply: Sender<()>,
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

impl std::fmt::Debug for DoshActorHandle {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("DoshActorHandle")
            .field("id", &self.id())
            .finish()
    }
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

    /// Evaluate code in a bounded control realm with explicit action authority.
    pub fn eval_bounded(
        &self,
        code: &str,
        control_realm: bool,
        allow_actions: bool,
        args: &[String],
    ) -> Result<String, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::EvalBounded {
                code: code.to_string(),
                control_realm,
                allow_actions,
                args: args.to_vec(),
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

    /// Set action authority gate (allows or blocks DOM mutations).
    pub fn set_action_allowed(&self, allowed: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetActionAllowed { allowed, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    pub fn is_action_allowed(&self) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsActionAllowed { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Toggle agent control realm.
    pub fn set_control_realm(&self, enabled: bool) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetControlRealm { enabled, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    pub fn is_control_realm(&self) -> Result<bool, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::IsControlRealm { reply: tx })
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

    /// Dispatch a DCP command and return an owned, thread-safe response.
    pub fn dispatch_owned(&self, cmd: DoshCommand) -> Result<DoshOwnedResponse, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::DispatchOwned { cmd, reply: tx })
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

    /// Query full live heap, envelope, pressure, and Parena telemetry from the resident actor.
    pub fn get_heap_report(&self) -> Result<ActorHeapReport, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::GetHeapReport { reply: tx })
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

    /// Drain all pending telemetry events from the actor's ring buffer.
    pub fn poll_telemetry_ring(&self) -> Result<Vec<(u32, crate::protocol::DoshEventKind, String, u64)>, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::PollTelemetryRing { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))
    }

    /// Fork the actor's active page arena.
    pub fn fork_parena(&self) -> Result<crate::parena::Parena, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::ForkParena { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Capture a checkpoint in the actor's active page arena.
    pub fn checkpoint_parena(&self) -> Result<u64, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::CheckpointParena { reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Roll back the actor's active page arena to a checkpoint.
    pub fn rewind_parena(&self, checkpoint_id: u64) -> Result<usize, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::RewindParena { checkpoint_id, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Read multiple nodes from the active page arena in a zero-copy batch.
    pub fn batch_read_parena(&self, node_ids: Vec<u32>) -> Result<Vec<u8>, String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::BatchReadParena { node_ids, reply: tx })
            .map_err(|e| format!("actor send error: {e}"))?;
        rx.recv().map_err(|e| format!("actor recv error: {e}"))?
    }

    /// Install a page arena into the actor.
    pub fn set_parena(&self, parena: crate::parena::Parena) -> Result<(), String> {
        let (tx, rx) = channel();
        self.inner
            .sender
            .send(ActorCommand::SetParena { parena, reply: tx })
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
                ActorCommand::EvalBounded {
                    code,
                    control_realm,
                    allow_actions,
                    args,
                    reply,
                } => {
                    let res = engine.eval_bounded(&code, control_realm, allow_actions, &args);
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
                ActorCommand::SetActionAllowed { allowed, reply } => {
                    let res = engine.set_action_allowed(allowed);
                    let _ = reply.send(res);
                }
                ActorCommand::IsActionAllowed { reply } => {
                    let res = engine.is_action_allowed();
                    let _ = reply.send(res);
                }
                ActorCommand::SetControlRealm { enabled, reply } => {
                    let res = engine.set_control_realm(enabled);
                    let _ = reply.send(res);
                }
                ActorCommand::IsControlRealm { reply } => {
                    let res = engine.is_control_realm();
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
                ActorCommand::DispatchOwned { cmd, reply } => {
                    let resp = engine.dispatch_owned(&cmd);
                    let _ = reply.send(resp);
                }
                ActorCommand::GetHeapStats { reply } => {
                    let used = engine.used_heap_size();
                    let total = engine.total_heap_size();
                    let _ = reply.send((used, total));
                }
                ActorCommand::GetHeapReport { reply } => {
                    let used_heap_bytes = engine.used_heap_size();
                    let total_heap_bytes = engine.total_heap_size();
                    let requested_limit_bytes = engine.requested_limit();
                    let (floor_bytes, ceil_bytes, granted_bytes) = engine.heap_band();
                    let pressure_epoch = engine.pressure_epoch();
                    let external_reported_bytes = engine.external_reported();
                    let shrink_fires = engine.shrink_fires();
                    let (parena_ram_bytes, parena_spill_bytes, parena_text_bytes) = engine.parena_stats();
                    let eval_count = engine.eval_count();
                    let _ = reply.send(ActorHeapReport {
                        used_heap_bytes,
                        total_heap_bytes,
                        requested_limit_bytes,
                        floor_bytes,
                        ceil_bytes,
                        granted_bytes,
                        pressure_epoch,
                        external_reported_bytes,
                        shrink_fires,
                        parena_ram_bytes,
                        parena_spill_bytes,
                        parena_text_bytes,
                        eval_count,
                    });
                }
                ActorCommand::EvalCount { reply } => {
                    let count = engine.eval_count();
                    let _ = reply.send(count);
                }
                ActorCommand::PollTelemetryRing { reply } => {
                    let evs = engine.poll_telemetry_ring();
                    let _ = reply.send(evs);
                }
                ActorCommand::ForkParena { reply } => {
                    let res = engine.fork_parena();
                    let _ = reply.send(res);
                }
                ActorCommand::CheckpointParena { reply } => {
                    let res = engine.checkpoint_parena();
                    let _ = reply.send(res);
                }
                ActorCommand::RewindParena { checkpoint_id, reply } => {
                    let res = engine.rewind_parena(checkpoint_id);
                    let _ = reply.send(res);
                }
                ActorCommand::BatchReadParena { node_ids, reply } => {
                    let res = engine.batch_read_parena(&node_ids);
                    let _ = reply.send(res);
                }
                ActorCommand::SetParena { parena, reply } => {
                    engine.set_parena(parena);
                    let _ = reply.send(());
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

    #[test]
    fn test_actor_bounded_eval_and_action_gate() {
        let actor = DoshActor::spawn(Arc::new(DummySeam), 0).expect("spawn actor");

        // Bounded eval with top-level return in control realm
        let res = actor
            .eval_bounded("return 'result: ' + dosh.args[0];", true, true, &["ok".to_string()])
            .expect("eval_bounded failed");
        assert_eq!(res, "result: ok");

        // Action gate via actor
        actor.set_action_allowed(false).expect("set action allowed");
        assert!(!actor.is_action_allowed().expect("is action allowed"));

        let err = actor.eval("dosh.act({ type: 'click' })");
        assert!(err.is_err());
        assert!(err.unwrap_err().contains("Action authority disallowed"));

        actor.set_action_allowed(true).expect("set action allowed");
        assert!(actor.is_action_allowed().expect("is action allowed"));

        actor.shutdown();
    }

    #[test]
    fn test_actor_parena_fork_checkpoint_rewind() {
        use crate::parena::{Parena, TagId};
        let actor = DoshActor::spawn(Arc::new(DummySeam), 0).expect("spawn actor");

        let mut arena = Parena::new();
        let body = arena.add_node(TagId::Body, 0, &[], "");
        let p = arena.add_node(TagId::P, body, &[], "Initial text");
        actor.set_parena(arena).expect("set parena");

        // Checkpoint
        let cp_id = actor.checkpoint_parena().expect("checkpoint");

        // Fork arena in actor
        let mut forked = actor.fork_parena().expect("fork");
        forked.set_text(p, "Forked text");
        assert_eq!(forked.get_text(p), "Forked text");

        // Batch read
        let bytes = actor.batch_read_parena(vec![p]).expect("batch read");
        assert!(!bytes.is_empty());

        // Rewind
        let restored = actor.rewind_parena(cp_id).expect("rewind");
        assert_eq!(restored, 0); // No mutations were applied to base arena

        actor.shutdown();
    }
}

