//! `protocol.rs` — DOSH Control Protocol (DCP) Rust FFI & C-ABI Bindings.
//!
//! Direct in-memory zero-copy interface mirroring `dosh_protocol.h`.

use std::sync::atomic::{AtomicU32, Ordering};

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoshOpCode {
    Noop = 0x00,
    Eval = 0x01,
    SetFuel = 0x02,
    GetFuel = 0x03,
    SetClock = 0x04,
    SetPrngSeed = 0x05,
    SetGate = 0x06,
    DrainTasks = 0x07,
    GetLayout = 0x08,
    GetHeapStats = 0x09,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoshStatusCode {
    Ok = 0x00,
    Error = 0x01,
    FuelExhausted = 0x02,
    GateBlocked = 0x03,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DoshEventKind {
    ConsoleLog = 0x10,
    ConsoleWarn = 0x11,
    ConsoleError = 0x12,
    MicrotaskEnd = 0x13,
    WasmFuelTrap = 0x14,
    WasmExport = 0x15,
    GateBlocked = 0x16,
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct DoshCommand {
    pub opcode: u8,
    pub realm_id: u8,
    pub flags: u16,
    pub payload_len: u32,
    pub param: u64,
    pub payload: *const u8,
}

impl DoshCommand {
    pub fn new(opcode: DoshOpCode) -> Self {
        Self {
            opcode: opcode as u8,
            realm_id: 0,
            flags: 0,
            payload_len: 0,
            param: 0,
            payload: std::ptr::null(),
        }
    }

    pub fn with_realm(mut self, realm_id: u8) -> Self {
        self.realm_id = realm_id;
        self
    }

    pub fn with_param(mut self, param: u64) -> Self {
        self.param = param;
        self
    }

    pub fn with_payload(mut self, s: &str) -> Self {
        self.payload = s.as_ptr();
        self.payload_len = s.len() as u32;
        self
    }

    pub fn as_payload_str(&self) -> Option<&str> {
        if self.payload.is_null() || self.payload_len == 0 {
            return None;
        }
        unsafe {
            let slice = std::slice::from_raw_parts(self.payload, self.payload_len as usize);
            std::str::from_utf8(slice).ok()
        }
    }
}

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct DoshResponse {
    pub status: u8,
    pub data_type: u8,
    pub reserved: u16,
    pub result_len: u32,
    pub numeric_val: u64,
    pub result_data: *const u8,
}

impl DoshResponse {
    pub fn ok() -> Self {
        Self {
            status: DoshStatusCode::Ok as u8,
            data_type: 0,
            reserved: 0,
            result_len: 0,
            numeric_val: 0,
            result_data: std::ptr::null(),
        }
    }

    pub fn with_numeric(val: u64) -> Self {
        Self {
            status: DoshStatusCode::Ok as u8,
            data_type: 2,
            reserved: 0,
            result_len: 0,
            numeric_val: val,
            result_data: std::ptr::null(),
        }
    }

    pub fn error(msg: &str) -> Self {
        Self {
            status: DoshStatusCode::Error as u8,
            data_type: 1,
            reserved: 0,
            result_len: msg.len() as u32,
            numeric_val: 0,
            result_data: msg.as_ptr(),
        }
    }

    pub fn as_str(&self) -> Option<&str> {
        if self.result_data.is_null() || self.result_len == 0 {
            return None;
        }
        unsafe {
            let slice = std::slice::from_raw_parts(self.result_data, self.result_len as usize);
            std::str::from_utf8(slice).ok()
        }
    }
}

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct DoshButterflyRect {
    pub element_id: u32,
    pub x: f32,
    pub y: f32,
    pub width: f32,
    pub height: f32,
    pub z_index: u16,
    pub occluded_stratum: u8,
    pub flags: u8,
}

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct DoshWasmStats {
    pub fuel_remaining: u64,
    pub fuel_consumed: u64,
    pub memory_pages: u32,
    pub active_instances: u32,
}

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct DoshHeapStats {
    pub total_heap_size: usize,
    pub used_heap_size: usize,
    pub heap_size_limit: usize,
    pub malloced_memory: usize,
    pub gc_count: u32,
}

pub const DOSH_RING_CAPACITY: usize = 1024;

#[repr(C)]
#[derive(Clone, Copy)]
pub struct DoshRingEvent {
    pub seq: u32,
    pub kind: u8,
    pub realm_id: u8,
    pub data_len: u16,
    pub timestamp_ms: u64,
    pub data: [u8; 256],
}

impl Default for DoshRingEvent {
    fn default() -> Self {
        Self {
            seq: 0,
            kind: 0,
            realm_id: 0,
            data_len: 0,
            timestamp_ms: 0,
            data: [0u8; 256],
        }
    }
}

#[repr(C)]
pub struct DoshTelemetryRing {
    pub write_head: AtomicU32,
    pub read_tail: AtomicU32,
    pub events: [DoshRingEvent; DOSH_RING_CAPACITY],
}

impl DoshTelemetryRing {
    pub fn new() -> Self {
        Self {
            write_head: AtomicU32::new(0),
            read_tail: AtomicU32::new(0),
            events: [DoshRingEvent::default(); DOSH_RING_CAPACITY],
        }
    }

    pub fn push(&mut self, kind: DoshEventKind, realm_id: u8, msg: &str, timestamp_ms: u64) {
        let head = self.write_head.load(Ordering::Relaxed);
        let idx = (head as usize) % DOSH_RING_CAPACITY;
        let mut event = DoshRingEvent {
            seq: head,
            kind: kind as u8,
            realm_id,
            data_len: 0,
            timestamp_ms,
            data: [0u8; 256],
        };
        let bytes = msg.as_bytes();
        let copy_len = bytes.len().min(256);
        event.data[..copy_len].copy_from_slice(&bytes[..copy_len]);
        event.data_len = copy_len as u16;
        self.events[idx] = event;
        self.write_head.store(head + 1, Ordering::Release);
    }

    pub fn drain(&self) -> Vec<(u32, DoshEventKind, String, u64)> {
        let mut out = Vec::new();
        let head = self.write_head.load(Ordering::Acquire);
        let mut tail = self.read_tail.load(Ordering::Relaxed);
        while tail < head {
            let idx = (tail as usize) % DOSH_RING_CAPACITY;
            let ev = &self.events[idx];
            let msg = String::from_utf8_lossy(&ev.data[..(ev.data_len as usize)]).to_string();
            let kind = match ev.kind {
                0x10 => DoshEventKind::ConsoleLog,
                0x11 => DoshEventKind::ConsoleWarn,
                0x12 => DoshEventKind::ConsoleError,
                0x13 => DoshEventKind::MicrotaskEnd,
                0x14 => DoshEventKind::WasmFuelTrap,
                0x15 => DoshEventKind::WasmExport,
                0x16 => DoshEventKind::GateBlocked,
                _ => DoshEventKind::ConsoleLog,
            };
            out.push((ev.seq, kind, msg, ev.timestamp_ms));
            tail += 1;
        }
        self.read_tail.store(tail, Ordering::Release);
        out
    }
}
