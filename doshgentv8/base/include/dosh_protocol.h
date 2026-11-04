/*
 * dosh_protocol.h — DOSH Control Protocol (DCP) C-ABI Definition.
 *
 * Zero-copy in-memory control and telemetry contract between
 * doshgentv8 (V8 C++ engine) and dosh-browser-core (Rust browser shell).
 *
 * Copyright (c) 2026 DOSH Browser Authors. Apache-2.0 License.
 */

#ifndef DOSH_PROTOCOL_H_
#define DOSH_PROTOCOL_H_

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Command OpCodes (Host -> V8) */
typedef enum {
    DOSH_OP_NOOP           = 0x00,
    DOSH_OP_EVAL           = 0x01,  /* Run JS script in realm */
    DOSH_OP_SET_FUEL       = 0x02,  /* Set Liftoff WASM execution fuel */
    DOSH_OP_GET_FUEL       = 0x03,  /* Query remaining WASM fuel */
    DOSH_OP_SET_CLOCK      = 0x04,  /* Injected virtual timestamp (ms) */
    DOSH_OP_SET_PRNG_SEED  = 0x05,  /* Deterministic PRNG seed */
    DOSH_OP_SET_GATE       = 0x06,  /* Configure security gates (eval/net/wasm) */
    DOSH_OP_DRAIN_TASKS    = 0x07,  /* Synchronously drain microtask queue */
    DOSH_OP_GET_LAYOUT     = 0x08,  /* Retrieve Butterfly layout memory view */
    DOSH_OP_GET_HEAP_STATS = 0x09   /* Read 128MB pruned heap metrics */
} DoshOpCode;

/* Response Status (V8 -> Host) */
typedef enum {
    DOSH_STATUS_OK             = 0x00,
    DOSH_STATUS_ERROR          = 0x01,
    DOSH_STATUS_FUEL_EXHAUSTED = 0x02,
    DOSH_STATUS_GATE_BLOCKED   = 0x03
} DoshStatusCode;

/* Event Kinds for Lock-Free Ring Buffer */
typedef enum {
    DOSH_EVT_CONSOLE_LOG    = 0x10,
    DOSH_EVT_CONSOLE_WARN   = 0x11,
    DOSH_EVT_CONSOLE_ERROR  = 0x12,
    DOSH_EVT_MICROTASK_END  = 0x13,
    DOSH_EVT_WASM_FUEL_TRAP = 0x14,
    DOSH_EVT_WASM_EXPORT    = 0x15,
    DOSH_EVT_GATE_BLOCKED   = 0x16
} DoshEventKind;

/* Packed 32-Byte Command Packet */
#pragma pack(push, 1)
typedef struct {
    uint8_t     opcode;         /* DoshOpCode */
    uint8_t     realm_id;       /* 0=Control Realm, 1=Page Realm, 2+=Frames */
    uint16_t    flags;          /* Strict, trusted event, etc. */
    uint32_t    payload_len;    /* Payload length in bytes */
    uint64_t    param;          /* Fuel limit, epoch timestamp, or gate bits */
    const char* payload;        /* Raw string or binary pointer (zero-copy) */
} DoshCommand;

/* Packed Response Packet */
typedef struct {
    uint8_t     status;         /* DoshStatusCode */
    uint8_t     data_type;      /* 0=None, 1=String, 2=Int, 3=Float, 4=Buffer */
    uint16_t    reserved;
    uint32_t    result_len;     /* Result length in bytes */
    uint64_t    numeric_val;    /* Numeric return value */
    const char* result_data;    /* Pointer to result string/buffer */
} DoshResponse;

/* DOM Butterfly Layout Geometry (Zero-Copy) */
typedef struct {
    uint32_t element_id;
    float    x;
    float    y;
    float    width;
    float    height;
    uint16_t z_index;
    uint8_t  occluded_stratum;  /* 0=Visible, 1=Occluded/Covered, 2=Offscreen */
    uint8_t  flags;             /* bit 0: is_clickable, bit 1: is_scroll_container */
} DoshButterflyRect;

/* WASM Liftoff Statistics */
typedef struct {
    uint64_t fuel_remaining;
    uint64_t fuel_consumed;
    uint32_t memory_pages;
    uint32_t active_instances;
} DoshWasmStats;

/* V8 Heap Statistics */
typedef struct {
    size_t   total_heap_size;
    size_t   used_heap_size;
    size_t   heap_size_limit;
    size_t   malloced_memory;
    uint32_t gc_count;
} DoshHeapStats;

/* Single Ring Event */
typedef struct {
    uint32_t seq;
    uint8_t  kind;              /* DoshEventKind */
    uint8_t  realm_id;
    uint16_t data_len;
    uint64_t timestamp_ms;
    char     data[256];
} DoshRingEvent;

#define DOSH_RING_CAPACITY 1024

/* Lock-Free Shared Ring Buffer */
typedef struct {
    volatile uint32_t write_head;
    volatile uint32_t read_tail;
    DoshRingEvent     events[DOSH_RING_CAPACITY];
} DoshTelemetryRing;
#pragma pack(pop)

#ifdef __cplusplus
}
#endif

#endif /* DOSH_PROTOCOL_H_ */

