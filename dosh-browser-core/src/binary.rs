//! Agent-Native Binary Format (ANBF v1.0 / .anb) Compiler.
//! Direct port of `webresearch/engine/binary.py`.
//!
//! Implements:
//! - 32-byte fixed header (Magic 'ANB1', version, flags, status, epoch ms, table offsets, CRC32).
//! - Semantic Block Table (dense row-delimited tuples for tables, headings, code, text).
//! - Action Graph Table (content-addressed action refs, kinds, and execution routings).
//! - State Delta Table (micro-patch streaming for interactive multi-step tasks).

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::time::{SystemTime, UNIX_EPOCH};

pub const MAGIC: &[u8; 4] = b"ANB1";
pub const HEADER_SIZE: usize = 32;
pub const CURRENT_VERSION: u16 = 1;

pub const BLOCK_TYPE_HEADING: u8 = 1;
pub const BLOCK_TYPE_TEXT: u8 = 2;
pub const BLOCK_TYPE_TABLE: u8 = 3;
pub const BLOCK_TYPE_CODE: u8 = 4;
pub const BLOCK_TYPE_LIST: u8 = 5;
pub const BLOCK_TYPE_QUOTE: u8 = 6;
pub const BLOCK_TYPE_SCHEMA: u8 = 7;
pub const BLOCK_TYPE_OTHER: u8 = 8;

pub const ACTION_KIND_NAVIGATE: u8 = 1;
pub const ACTION_KIND_SUBMIT: u8 = 2;
pub const ACTION_KIND_ACTIVATE: u8 = 3;
pub const ACTION_KIND_DRAG: u8 = 4;
pub const ACTION_KIND_DROPZONE: u8 = 5;
pub const ACTION_KIND_EMBED: u8 = 6;
pub const ACTION_KIND_OTHER: u8 = 7;

pub const DELTA_KIND_MUTATE: u8 = 1;
pub const DELTA_KIND_INSERT: u8 = 2;
pub const DELTA_KIND_REMOVE: u8 = 3;
pub const DELTA_KIND_STATE: u8 = 4;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnbHeader {
    pub magic: [u8; 4],
    pub version: u16,
    pub flags: u16,
    pub status: u16,
    pub timestamp_ms: u64,
    pub block_offset: u32,
    pub action_offset: u32,
    pub delta_offset: u32,
    pub crc32: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnbDelta {
    pub kind: String,
    pub target_id: String,
    pub content: String,
    pub timestamp: f64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnbDocument {
    pub header: AnbHeader,
    pub url: String,
    pub title: String,
    pub blocks: Vec<crate::types::AcdBlock>,
    pub actions: Vec<crate::types::AcdAction>,
    pub deltas: Vec<AnbDelta>,
    pub metadata: HashMap<String, serde_json::Value>,
}

fn crc32(data: &[u8]) -> u32 {
    // CRC-32 (IEEE 802.3) polynomial 0xEDB88320
    let mut crc = 0xFFFFFFFFu32;
    for &byte in data {
        crc ^= byte as u32;
        for _ in 0..8 {
            let mask = -( (crc & 1) as i32 ) as u32;
            crc = (crc >> 1) ^ (0xEDB88320 & mask);
        }
    }
    !crc
}

fn pack_str(s: &str) -> Vec<u8> {
    let bytes = s.as_bytes();
    let mut out = Vec::new();
    if bytes.len() >= 0xFFFF {
        out.extend_from_slice(&0xFFFFu16.to_be_bytes());
        out.extend_from_slice(&(bytes.len() as u32).to_be_bytes());
    } else {
        out.extend_from_slice(&(bytes.len() as u16).to_be_bytes());
    }
    out.extend_from_slice(bytes);
    out
}

fn unpack_str(buf: &[u8], offset: &mut usize) -> Result<String, String> {
    if *offset + 2 > buf.len() {
        return Err("Unexpected EOF reading string length".to_string());
    }
    let mut len = u16::from_be_bytes([buf[*offset], buf[*offset + 1]]) as usize;
    *offset += 2;
    if len == 0xFFFF {
        if *offset + 4 > buf.len() {
            return Err("Unexpected EOF reading 32-bit string length".to_string());
        }
        len = u32::from_be_bytes([buf[*offset], buf[*offset + 1], buf[*offset + 2], buf[*offset + 3]]) as usize;
        *offset += 4;
    }
    if *offset + len > buf.len() {
        return Err("Unexpected EOF reading string content".to_string());
    }
    let s = String::from_utf8_lossy(&buf[*offset..*offset + len]).to_string();
    *offset += len;
    Ok(s)
}

fn block_type_to_byte(t: &str) -> u8 {
    match t.to_lowercase().as_str() {
        "heading" => BLOCK_TYPE_HEADING,
        "text" => BLOCK_TYPE_TEXT,
        "table" => BLOCK_TYPE_TABLE,
        "code" => BLOCK_TYPE_CODE,
        "list" => BLOCK_TYPE_LIST,
        "quote" => BLOCK_TYPE_QUOTE,
        "schema" => BLOCK_TYPE_SCHEMA,
        _ => BLOCK_TYPE_OTHER,
    }
}

fn byte_to_block_type(b: u8) -> &'static str {
    match b {
        BLOCK_TYPE_HEADING => "heading",
        BLOCK_TYPE_TEXT => "text",
        BLOCK_TYPE_TABLE => "table",
        BLOCK_TYPE_CODE => "code",
        BLOCK_TYPE_LIST => "list",
        BLOCK_TYPE_QUOTE => "quote",
        BLOCK_TYPE_SCHEMA => "schema",
        _ => "text",
    }
}

fn action_kind_to_byte(k: &str) -> u8 {
    match k.to_lowercase().as_str() {
        "navigate" => ACTION_KIND_NAVIGATE,
        "submit" => ACTION_KIND_SUBMIT,
        "activate" => ACTION_KIND_ACTIVATE,
        "drag" => ACTION_KIND_DRAG,
        "dropzone" => ACTION_KIND_DROPZONE,
        "embed" => ACTION_KIND_EMBED,
        _ => ACTION_KIND_OTHER,
    }
}

fn byte_to_action_kind(b: u8) -> &'static str {
    match b {
        ACTION_KIND_NAVIGATE => "navigate",
        ACTION_KIND_SUBMIT => "submit",
        ACTION_KIND_ACTIVATE => "activate",
        ACTION_KIND_DRAG => "drag",
        ACTION_KIND_DROPZONE => "dropzone",
        ACTION_KIND_EMBED => "embed",
        _ => "activate",
    }
}

pub fn pack_anbf(
    acd: &crate::types::Acd,
    status: u16,
    flags: u16,
    deltas: Option<&[AnbDelta]>,
) -> Vec<u8> {
    let now_ms = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);

    let mut body = Vec::new();

    // 1. Doc string table (URL, Title, unavailable list, framework keys)
    body.extend_from_slice(&pack_str(&acd.url));
    body.extend_from_slice(&pack_str(&acd.title));
    body.extend_from_slice(&(acd.unavailable.len() as u16).to_be_bytes());
    for u in &acd.unavailable {
        body.extend_from_slice(&pack_str(u));
    }
    body.extend_from_slice(&(acd.framework.len() as u16).to_be_bytes());
    for (k, _) in &acd.framework {
        body.extend_from_slice(&pack_str(k));
    }

    let block_offset = (HEADER_SIZE + body.len()) as u32;

    // 2. Blocks
    body.extend_from_slice(&(acd.blocks.len() as u32).to_be_bytes());
    for b in &acd.blocks {
        let btype = block_type_to_byte(&b.block_type);
        body.push(btype);
        body.extend_from_slice(&pack_str(&b.id));
        body.extend_from_slice(&pack_str(&b.text));
        body.extend_from_slice(&(b.level.unwrap_or(0) as u8).to_be_bytes());
        body.extend_from_slice(&(b.rows.len() as u16).to_be_bytes());
        for row in &b.rows {
            body.extend_from_slice(&(row.len() as u16).to_be_bytes());
            for cell in row {
                body.extend_from_slice(&pack_str(cell));
            }
        }
    }

    let action_offset = (HEADER_SIZE + body.len()) as u32;

    // 3. Actions
    body.extend_from_slice(&(acd.actions.len() as u32).to_be_bytes());
    for act in &acd.actions {
        let akind = action_kind_to_byte(&act.kind);
        body.push(akind);
        body.extend_from_slice(&pack_str(&act.ref_id));
        body.extend_from_slice(&pack_str(&act.label));
        body.extend_from_slice(&pack_str(&act.role));
        body.extend_from_slice(&pack_str(&act.run));
        body.extend_from_slice(&pack_str(&act.name));
        body.extend_from_slice(&pack_str(&act.href));
        body.push(0u8);
    }

    let delta_offset = (HEADER_SIZE + body.len()) as u32;

    // 4. Deltas
    let deltas_slice = deltas.unwrap_or(&[]);
    body.extend_from_slice(&(deltas_slice.len() as u32).to_be_bytes());
    for d in deltas_slice {
        let dkind = match d.kind.as_str() {
            "mutate" => DELTA_KIND_MUTATE,
            "insert" => DELTA_KIND_INSERT,
            "remove" => DELTA_KIND_REMOVE,
            "state" => DELTA_KIND_STATE,
            _ => DELTA_KIND_MUTATE,
        };
        body.push(dkind);
        body.extend_from_slice(&pack_str(&d.target_id));
        body.extend_from_slice(&pack_str(&d.content));
        let d_ms = (d.timestamp * 1000.0) as u64;
        body.extend_from_slice(&d_ms.to_be_bytes());
    }

    // Header assembly
    let body_crc = crc32(&body);
    let mut full = Vec::with_capacity(HEADER_SIZE + body.len());
    full.extend_from_slice(MAGIC);
    full.extend_from_slice(&CURRENT_VERSION.to_be_bytes());
    full.extend_from_slice(&flags.to_be_bytes());
    full.extend_from_slice(&status.to_be_bytes());
    // 6-byte epoch timestamp (as 2 bytes high, 4 bytes low)
    let time_hi = ((now_ms >> 32) & 0xFFFF) as u16;
    let time_lo = (now_ms & 0xFFFFFFFF) as u32;
    full.extend_from_slice(&time_hi.to_be_bytes());
    full.extend_from_slice(&time_lo.to_be_bytes());
    full.extend_from_slice(&block_offset.to_be_bytes());
    full.extend_from_slice(&action_offset.to_be_bytes());
    full.extend_from_slice(&delta_offset.to_be_bytes());
    full.extend_from_slice(&body_crc.to_be_bytes());

    full.extend_from_slice(&body);
    full
}

pub fn unpack_anbf(data: &[u8]) -> Result<AnbDocument, String> {
    if data.len() < HEADER_SIZE {
        return Err("Data too short for ANBF header".to_string());
    }
    if &data[0..4] != MAGIC {
        return Err("Invalid ANBF magic bytes".to_string());
    }
    let version = u16::from_be_bytes([data[4], data[5]]);
    let flags = u16::from_be_bytes([data[6], data[7]]);
    let status = u16::from_be_bytes([data[8], data[9]]);
    let time_hi = u16::from_be_bytes([data[10], data[11]]) as u64;
    let time_lo = u32::from_be_bytes([data[12], data[13], data[14], data[15]]) as u64;
    let timestamp_ms = (time_hi << 32) | time_lo;
    let block_offset = u32::from_be_bytes([data[16], data[17], data[18], data[19]]);
    let action_offset = u32::from_be_bytes([data[20], data[21], data[22], data[23]]);
    let delta_offset = u32::from_be_bytes([data[24], data[25], data[26], data[27]]);
    let expected_crc = u32::from_be_bytes([data[28], data[29], data[30], data[31]]);

    let body = &data[HEADER_SIZE..];
    let actual_crc = crc32(body);
    if actual_crc != expected_crc {
        return Err(format!("CRC32 mismatch: expected {:08x}, got {:08x}", expected_crc, actual_crc));
    }

    let header = AnbHeader {
        magic: *MAGIC,
        version,
        flags,
        status,
        timestamp_ms,
        block_offset,
        action_offset,
        delta_offset,
        crc32: expected_crc,
    };

    let mut offset = HEADER_SIZE;
    let url = unpack_str(data, &mut offset)?;
    let title = unpack_str(data, &mut offset)?;

    let num_unavailable = u16::from_be_bytes([data[offset], data[offset + 1]]) as usize;
    offset += 2;
    let mut unavailable = Vec::new();
    for _ in 0..num_unavailable {
        unavailable.push(unpack_str(data, &mut offset)?);
    }

    let num_framework = u16::from_be_bytes([data[offset], data[offset + 1]]) as usize;
    offset += 2;
    let mut framework_keys = Vec::new();
    for _ in 0..num_framework {
        framework_keys.push(unpack_str(data, &mut offset)?);
    }

    // Blocks
    offset = block_offset as usize;
    let num_blocks = u32::from_be_bytes([data[offset], data[offset + 1], data[offset + 2], data[offset + 3]]) as usize;
    offset += 4;
    let mut blocks = Vec::with_capacity(num_blocks);
    for _ in 0..num_blocks {
        let btype = byte_to_block_type(data[offset]).to_string();
        offset += 1;
        let id = unpack_str(data, &mut offset)?;
        let text = unpack_str(data, &mut offset)?;
        let level = data[offset] as i64;
        offset += 1;
        let num_rows = u16::from_be_bytes([data[offset], data[offset + 1]]) as usize;
        offset += 2;
        let mut rows = Vec::with_capacity(num_rows);
        for _ in 0..num_rows {
            let row_len = u16::from_be_bytes([data[offset], data[offset + 1]]) as usize;
            offset += 2;
            let mut row = Vec::with_capacity(row_len);
            for _ in 0..row_len {
                row.push(unpack_str(data, &mut offset)?);
            }
            rows.push(row);
        }
        blocks.push(crate::types::AcdBlock {
            id,
            block_type: btype,
            text,
            level: if level > 0 { Some(level) } else { None },
            ref_id: "".to_string(),
            role: "".to_string(),
            href: "".to_string(),
            rows,
            locator: "".to_string(),
            ..Default::default()
        });
    }

    // Actions
    offset = action_offset as usize;
    let num_actions = u32::from_be_bytes([data[offset], data[offset + 1], data[offset + 2], data[offset + 3]]) as usize;
    offset += 4;
    let mut actions = Vec::with_capacity(num_actions);
    for _ in 0..num_actions {
        let kind = byte_to_action_kind(data[offset]).to_string();
        offset += 1;
        let ref_id = unpack_str(data, &mut offset)?;
        let label = unpack_str(data, &mut offset)?;
        let role = unpack_str(data, &mut offset)?;
        let run = unpack_str(data, &mut offset)?;
        let name = unpack_str(data, &mut offset)?;
        let href = unpack_str(data, &mut offset)?;
        let _flag = data[offset];
        offset += 1;
        actions.push(crate::types::AcdAction {
            ref_id,
            kind,
            label,
            role,
            run,
            name,
            href,
        });
    }

    // Deltas
    offset = delta_offset as usize;
    let mut deltas = Vec::new();
    if offset + 4 <= data.len() {
        let num_deltas = u32::from_be_bytes([data[offset], data[offset + 1], data[offset + 2], data[offset + 3]]) as usize;
        offset += 4;
        for _ in 0..num_deltas {
            let dkind_byte = data[offset];
            offset += 1;
            let target_id = unpack_str(data, &mut offset)?;
            let content = unpack_str(data, &mut offset)?;
            let d_ms = u64::from_be_bytes([
                data[offset], data[offset + 1], data[offset + 2], data[offset + 3],
                data[offset + 4], data[offset + 5], data[offset + 6], data[offset + 7],
            ]);
            offset += 8;
            let kind = match dkind_byte {
                DELTA_KIND_MUTATE => "mutate",
                DELTA_KIND_INSERT => "insert",
                DELTA_KIND_REMOVE => "remove",
                DELTA_KIND_STATE => "state",
                _ => "mutate",
            }.to_string();
            deltas.push(AnbDelta {
                kind,
                target_id,
                content,
                timestamp: d_ms as f64 / 1000.0,
            });
        }
    }

    let mut metadata = HashMap::new();
    metadata.insert("unavailable".to_string(), serde_json::to_value(unavailable).unwrap());
    metadata.insert("framework_keys".to_string(), serde_json::to_value(framework_keys).unwrap());

    Ok(AnbDocument {
        header,
        url,
        title,
        blocks,
        actions,
        deltas,
        metadata,
    })
}

pub fn anbf_to_agent_view(doc: &AnbDocument, max_blocks: usize) -> String {
    let mut lines = Vec::new();
    lines.push(format!("ANBF v{} [{}] {} ({})", doc.header.version, doc.header.status, doc.title, doc.url));
    lines.push(format!("STATS: {} blocks, {} actions, {} deltas", doc.blocks.len(), doc.actions.len(), doc.deltas.len()));

    for block in doc.blocks.iter().take(max_blocks) {
        if block.block_type == "table" {
            lines.push(format!("[{} TABLE {} rows]", block.id, block.rows.len()));
            for row in block.rows.iter().take(8) {
                lines.push(format!("  | {} |", row.join(" | ")));
            }
        } else if block.block_type == "heading" {
            lines.push(format!("[{} H{}] {}", block.id, block.level.unwrap_or(1), block.text));
        } else if block.block_type == "code" {
            lines.push(format!("[{} CODE] {}", block.id, block.text));
        } else {
            lines.push(format!("[{}] {}", block.id, block.text));
        }
    }

    if doc.blocks.len() > max_blocks {
        lines.push(format!("... ({} more blocks omitted)", doc.blocks.len() - max_blocks));
    }

    lines.push("--- ACTIONS ---".to_string());
    for act in doc.actions.iter().take(100) {
        let tgt = if !act.run.is_empty() && act.run != act.ref_id {
            format!(" -> {}", act.run)
        } else {
            String::new()
        };
        lines.push(format!("[{} {}] {}{}", act.ref_id, act.kind, act.label, tgt));
    }

    lines.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_pack_and_unpack_anbf_roundtrip() {
        let acd = crate::types::Acd {
            version: "1".to_string(),
            url: "https://example.com/test".to_string(),
            title: "Test Page".to_string(),
            blocks: vec![
                crate::types::AcdBlock {
                    id: "b1".to_string(),
                    block_type: "heading".to_string(),
                    text: "Welcome".to_string(),
                    level: Some(1),
                    ref_id: "".to_string(),
                    role: "".to_string(),
                    href: "".to_string(),
                    rows: vec![],
                    locator: "".to_string(),
                    ..Default::default()
                },
                crate::types::AcdBlock {
                    id: "b2".to_string(),
                    block_type: "table".to_string(),
                    text: "".to_string(),
                    level: None,
                    ref_id: "".to_string(),
                    role: "".to_string(),
                    href: "".to_string(),
                    rows: vec![vec!["Col1".to_string(), "Col2".to_string()], vec!["A".to_string(), "B".to_string()]],
                    locator: "".to_string(),
                    ..Default::default()
                },
            ],
            actions: vec![
                crate::types::AcdAction {
                    ref_id: "a1".to_string(),
                    kind: "navigate".to_string(),
                    label: "Go somewhere".to_string(),
                    role: "link".to_string(),
                    run: "https://example.com/dest".to_string(),
                    name: "".to_string(),
                    href: "".to_string(),
                }
            ],
            collections: vec![],
            links: vec![],
            api: vec![],
            frames: vec![],
            framework: HashMap::new(),
            errors: vec![],
            counts: HashMap::new(),
            unavailable: vec!["no-auth".to_string()],
        };

        let packed = pack_anbf(&acd, 200, 0, None);
        assert!(packed.starts_with(MAGIC));
        assert!(packed.len() > HEADER_SIZE);

        let doc = unpack_anbf(&packed).expect("unpack should succeed");
        assert_eq!(doc.url, "https://example.com/test");
        assert_eq!(doc.title, "Test Page");
        assert_eq!(doc.blocks.len(), 2);
        assert_eq!(doc.blocks[0].text, "Welcome");
        assert_eq!(doc.blocks[1].rows.len(), 2);
        assert_eq!(doc.actions.len(), 1);
        assert_eq!(doc.actions[0].label, "Go somewhere");

        let agent_view = anbf_to_agent_view(&doc, 10);
        assert!(agent_view.contains("ANBF v1 [200]"));
        assert!(agent_view.contains("[b1 H1] Welcome"));
    }
}
