//! `parena.rs` — Page Arena (Parena) Struct-of-Arrays (SoA) Host Memory.
//!
//! Implements Layer 1 of the Dosh State Weave:
//! Page state lives in host-owned contiguous linear memory outside the V8 cage.
//! The JS engine holds borrowed index handles only.
//!
//! Subsystems:
//! - Struct-of-Arrays (SoA): tags, parents, attribute blob, contiguous UTF-8 text arena.
//! - Checkpoints & Dirty-Diff Rewind: Tracks mutation bytes, rollbacks in microseconds.
//! - Fast Forking: Deep copy of contiguous memory slices without isolate cloning.
//! - Batch Zero-Copy Reads: Opcode 0x0C binary DOM inspection.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};

static EPOCH_GEN: AtomicU64 = AtomicU64::new(1);
static CHECKPOINT_GEN: AtomicU64 = AtomicU64::new(1);

/// Mapped Tag IDs for common HTML elements.
#[repr(u16)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum TagId {
    Unknown = 0,
    Html = 1,
    Head = 2,
    Body = 3,
    Div = 4,
    Span = 5,
    P = 6,
    A = 7,
    Button = 8,
    Input = 9,
    Form = 10,
    Select = 11,
    Option = 12,
    Textarea = 13,
    Ul = 14,
    Ol = 15,
    Li = 16,
    H1 = 17,
    H2 = 18,
    H3 = 19,
    H4 = 20,
    H5 = 21,
    H6 = 22,
    Table = 23,
    Thead = 24,
    Tbody = 25,
    Tr = 26,
    Th = 27,
    Td = 28,
    Img = 29,
    Iframe = 30,
    Script = 31,
    Style = 32,
    Link = 33,
    Meta = 34,
    Header = 35,
    Footer = 36,
    Nav = 37,
    Main = 38,
    Section = 39,
    Article = 40,
    Aside = 41,
    Label = 42,
    TextNode = 100,
    Document = 101,
}

impl TagId {
    pub fn from_name(name: &str) -> Self {
        match name.to_ascii_lowercase().as_str() {
            "html" => Self::Html,
            "head" => Self::Head,
            "body" => Self::Body,
            "div" => Self::Div,
            "span" => Self::Span,
            "p" => Self::P,
            "a" => Self::A,
            "button" => Self::Button,
            "input" => Self::Input,
            "form" => Self::Form,
            "select" => Self::Select,
            "option" => Self::Option,
            "textarea" => Self::Textarea,
            "ul" => Self::Ul,
            "ol" => Self::Ol,
            "li" => Self::Li,
            "h1" => Self::H1,
            "h2" => Self::H2,
            "h3" => Self::H3,
            "h4" => Self::H4,
            "h5" => Self::H5,
            "h6" => Self::H6,
            "table" => Self::Table,
            "thead" => Self::Thead,
            "tbody" => Self::Tbody,
            "tr" => Self::Tr,
            "th" => Self::Th,
            "td" => Self::Td,
            "img" => Self::Img,
            "iframe" => Self::Iframe,
            "script" => Self::Script,
            "style" => Self::Style,
            "link" => Self::Link,
            "meta" => Self::Meta,
            "header" => Self::Header,
            "footer" => Self::Footer,
            "nav" => Self::Nav,
            "main" => Self::Main,
            "section" => Self::Section,
            "article" => Self::Article,
            "aside" => Self::Aside,
            "label" => Self::Label,
            "#text" => Self::TextNode,
            "#document" | "document" => Self::Document,
            _ => Self::Unknown,
        }
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            Self::Html => "html",
            Self::Head => "head",
            Self::Body => "body",
            Self::Div => "div",
            Self::Span => "span",
            Self::P => "p",
            Self::A => "a",
            Self::Button => "button",
            Self::Input => "input",
            Self::Form => "form",
            Self::Select => "select",
            Self::Option => "option",
            Self::Textarea => "textarea",
            Self::Ul => "ul",
            Self::Ol => "ol",
            Self::Li => "li",
            Self::H1 => "h1",
            Self::H2 => "h2",
            Self::H3 => "h3",
            Self::H4 => "h4",
            Self::H5 => "h5",
            Self::H6 => "h6",
            Self::Table => "table",
            Self::Thead => "thead",
            Self::Tbody => "tbody",
            Self::Tr => "tr",
            Self::Th => "th",
            Self::Td => "td",
            Self::Img => "img",
            Self::Iframe => "iframe",
            Self::Script => "script",
            Self::Style => "style",
            Self::Link => "link",
            Self::Meta => "meta",
            Self::Header => "header",
            Self::Footer => "footer",
            Self::Nav => "nav",
            Self::Main => "main",
            Self::Section => "section",
            Self::Article => "article",
            Self::Aside => "aside",
            Self::Label => "label",
            Self::TextNode => "#text",
            Self::Document => "document",
            Self::Unknown => "unknown",
        }
    }
}

/// Packed action descriptor for operable controls.
#[repr(C)]
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct PackedAction {
    pub node_id: u32,
    pub kind: u8,   // 1: navigate, 2: submit, 3: activate, 4: drag, 5: dropzone, 6: embed
    pub flags: u8,  // bit 0: disabled, bit 1: visible, bit 2: stealth
    pub reserved: u16,
    pub target_offset: u32,
    pub target_len: u32,
}

/// Dirty byte range captured during mutations for backward delta rewinds.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct DirtyRange {
    pub frame: u8, // 0: text_arena, 1: attr_data, 2: tags, 3: parents, 4: actions
    pub offset: usize,
    pub old_bytes: Vec<u8>,
}

/// Checkpoint marker for fast rollback.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Checkpoint {
    pub id: u64,
    pub epoch_id: u64,
    pub dirty_cursor: usize,
    pub node_count: usize,
    pub text_len: usize,
    pub attr_len: usize,
    pub actions_len: usize,
    pub text_offsets: Vec<u32>,
    pub text_lens: Vec<u32>,
    pub attr_offsets: Vec<u32>,
    pub attr_lens: Vec<u32>,
}

/// Host-owned Struct-of-Arrays Page Arena.
#[derive(Clone, Debug)]
pub struct Parena {
    pub epoch_id: u64,
    pub tags: Vec<u16>,
    pub parents: Vec<u32>,
    pub attr_data: Vec<u8>,
    pub attr_offsets: Vec<u32>,
    pub attr_lens: Vec<u32>,
    pub text_arena: Vec<u8>,
    pub text_offsets: Vec<u32>,
    pub text_lens: Vec<u32>,
    pub actions: Vec<PackedAction>,
    pub dirty_log: Vec<DirtyRange>,
}

impl Default for Parena {
    fn default() -> Self {
        Self::new()
    }
}

impl Parena {
    /// Create a new empty page arena with a fresh epoch.
    pub fn new() -> Self {
        let epoch_id = EPOCH_GEN.fetch_add(1, Ordering::Relaxed);
        Self {
            epoch_id,
            tags: Vec::with_capacity(512),
            parents: Vec::with_capacity(512),
            attr_data: Vec::with_capacity(2048),
            attr_offsets: Vec::with_capacity(512),
            attr_lens: Vec::with_capacity(512),
            text_arena: Vec::with_capacity(4096),
            text_offsets: Vec::with_capacity(512),
            text_lens: Vec::with_capacity(512),
            actions: Vec::with_capacity(64),
            dirty_log: Vec::new(),
        }
    }

    /// Number of nodes in the arena.
    #[inline]
    pub fn len(&self) -> usize {
        self.tags.len()
    }

    #[inline]
    pub fn is_empty(&self) -> bool {
        self.tags.is_empty()
    }

    /// Host-resident bytes this arena currently holds OUTSIDE the V8 cage.
    /// Reported to V8's external-memory pacer so its GC reacts to DOM/host
    /// growth it otherwise cannot see (doshgentv8/core/src/lib.rs Stage 0).
    /// Capacity-based: an arena that grew and shrank keeps its high-water
    /// reservation, and that reservation is the real RAM cost.
    pub fn external_bytes(&self) -> usize {
        self.text_arena.capacity()
            + self.attr_data.capacity()
            + self.tags.capacity() * std::mem::size_of::<u16>()
            + self.parents.capacity() * std::mem::size_of::<u32>()
            + self.attr_offsets.capacity() * std::mem::size_of::<u32>()
            + self.attr_lens.capacity() * std::mem::size_of::<u32>()
            + self.text_offsets.capacity() * std::mem::size_of::<u32>()
            + self.text_lens.capacity() * std::mem::size_of::<u32>()
            + self.actions.capacity() * std::mem::size_of::<PackedAction>()
    }

    /// Add a node to the arena, returning its node ID (0-indexed).
    pub fn add_node(
        &mut self,
        tag: TagId,
        parent_id: u32,
        attrs: &[(&str, &str)],
        text: &str,
    ) -> u32 {
        let node_id = self.tags.len() as u32;

        self.tags.push(tag as u16);
        self.parents.push(parent_id);

        // Pack attributes: [key_len: u16, val_len: u16, key_bytes, val_bytes]*
        let attr_offset = self.attr_data.len() as u32;
        let mut attr_len = 0u32;
        for (k, v) in attrs {
            let k_bytes = k.as_bytes();
            let v_bytes = v.as_bytes();
            self.attr_data.extend_from_slice(&(k_bytes.len() as u16).to_le_bytes());
            self.attr_data.extend_from_slice(&(v_bytes.len() as u16).to_le_bytes());
            self.attr_data.extend_from_slice(k_bytes);
            self.attr_data.extend_from_slice(v_bytes);
            attr_len += 4 + k_bytes.len() as u32 + v_bytes.len() as u32;
        }
        self.attr_offsets.push(attr_offset);
        self.attr_lens.push(attr_len);

        // Pack contiguous UTF-8 text
        let text_bytes = text.as_bytes();
        let text_offset = self.text_arena.len() as u32;
        let text_len = text_bytes.len() as u32;
        if text_len > 0 {
            self.text_arena.extend_from_slice(text_bytes);
        }
        self.text_offsets.push(text_offset);
        self.text_lens.push(text_len);

        node_id
    }

    /// Read the text of a node.
    pub fn get_text(&self, node_id: u32) -> &str {
        let idx = node_id as usize;
        if idx >= self.text_offsets.len() {
            return "";
        }
        let off = self.text_offsets[idx] as usize;
        let len = self.text_lens[idx] as usize;
        if off + len > self.text_arena.len() {
            return "";
        }
        std::str::from_utf8(&self.text_arena[off..off + len]).unwrap_or("")
    }

    /// Read the tag ID of a node.
    pub fn get_tag(&self, node_id: u32) -> TagId {
        let idx = node_id as usize;
        if idx >= self.tags.len() {
            return TagId::Unknown;
        }
        match self.tags[idx] {
            1 => TagId::Html,
            2 => TagId::Head,
            3 => TagId::Body,
            4 => TagId::Div,
            5 => TagId::Span,
            6 => TagId::P,
            7 => TagId::A,
            8 => TagId::Button,
            9 => TagId::Input,
            10 => TagId::Form,
            11 => TagId::Select,
            12 => TagId::Option,
            13 => TagId::Textarea,
            14 => TagId::Ul,
            15 => TagId::Ol,
            16 => TagId::Li,
            17 => TagId::H1,
            18 => TagId::H2,
            19 => TagId::H3,
            20 => TagId::H4,
            21 => TagId::H5,
            22 => TagId::H6,
            23 => TagId::Table,
            24 => TagId::Thead,
            25 => TagId::Tbody,
            26 => TagId::Tr,
            27 => TagId::Th,
            28 => TagId::Td,
            29 => TagId::Img,
            30 => TagId::Iframe,
            31 => TagId::Script,
            32 => TagId::Style,
            33 => TagId::Link,
            34 => TagId::Meta,
            35 => TagId::Header,
            36 => TagId::Footer,
            37 => TagId::Nav,
            38 => TagId::Main,
            39 => TagId::Section,
            40 => TagId::Article,
            41 => TagId::Aside,
            42 => TagId::Label,
            100 => TagId::TextNode,
            101 => TagId::Document,
            _ => TagId::Unknown,
        }
    }

    /// Read the parent node ID.
    pub fn get_parent(&self, node_id: u32) -> Option<u32> {
        let idx = node_id as usize;
        if idx >= self.parents.len() {
            None
        } else {
            Some(self.parents[idx])
        }
    }

    /// Read the attributes of a node into a key-value map.
    pub fn get_attrs(&self, node_id: u32) -> HashMap<String, String> {
        let mut map = HashMap::new();
        let idx = node_id as usize;
        if idx >= self.attr_offsets.len() {
            return map;
        }
        let off = self.attr_offsets[idx] as usize;
        let total_len = self.attr_lens[idx] as usize;
        if off + total_len > self.attr_data.len() {
            return map;
        }

        let slice = &self.attr_data[off..off + total_len];
        let mut cursor = 0;
        while cursor + 4 <= slice.len() {
            let k_len = u16::from_le_bytes([slice[cursor], slice[cursor + 1]]) as usize;
            let v_len = u16::from_le_bytes([slice[cursor + 2], slice[cursor + 3]]) as usize;
            cursor += 4;
            if cursor + k_len + v_len > slice.len() {
                break;
            }
            let k = String::from_utf8_lossy(&slice[cursor..cursor + k_len]).to_string();
            cursor += k_len;
            let v = String::from_utf8_lossy(&slice[cursor..cursor + v_len]).to_string();
            cursor += v_len;
            map.insert(k, v);
        }
        map
    }

    /// Update node text in-place, logging the dirty diff for rollback.
    pub fn set_text(&mut self, node_id: u32, new_text: &str) -> bool {
        let idx = node_id as usize;
        if idx >= self.text_offsets.len() {
            return false;
        }

        let old_off = self.text_offsets[idx] as usize;
        let old_len = self.text_lens[idx] as usize;
        let new_bytes = new_text.as_bytes();

        // If the new text fits in the same span, update in place
        if new_bytes.len() <= old_len {
            let old_slice = self.text_arena[old_off..old_off + old_len].to_vec();
            self.dirty_log.push(DirtyRange {
                frame: 0, // text_arena
                offset: old_off,
                old_bytes: old_slice,
            });
            self.text_arena[old_off..old_off + new_bytes.len()].copy_from_slice(new_bytes);
            self.text_lens[idx] = new_bytes.len() as u32;
        } else {
            // Append new span to the end of text_arena
            let new_off = self.text_arena.len() as u32;
            self.dirty_log.push(DirtyRange {
                frame: 0,
                offset: old_off,
                old_bytes: self.text_arena[old_off..old_off + old_len].to_vec(),
            });
            self.text_arena.extend_from_slice(new_bytes);
            self.text_offsets[idx] = new_off;
            self.text_lens[idx] = new_bytes.len() as u32;
        }
        true
    }

    /// Register an actionable target in the arena.
    pub fn add_action(
        &mut self,
        node_id: u32,
        kind: u8,
        flags: u8,
        target_str: &str,
    ) {
        let target_bytes = target_str.as_bytes();
        let target_off = self.text_arena.len() as u32;
        let target_len = target_bytes.len() as u32;
        if target_len > 0 {
            self.text_arena.extend_from_slice(target_bytes);
        }
        self.actions.push(PackedAction {
            node_id,
            kind,
            flags,
            reserved: 0,
            target_offset: target_off,
            target_len,
        });
    }

    /// Capture a checkpoint of current arena state for dirty-diff rollbacks.
    pub fn checkpoint(&self) -> Checkpoint {
        let id = CHECKPOINT_GEN.fetch_add(1, Ordering::Relaxed);
        Checkpoint {
            id,
            epoch_id: self.epoch_id,
            dirty_cursor: self.dirty_log.len(),
            node_count: self.tags.len(),
            text_len: self.text_arena.len(),
            attr_len: self.attr_data.len(),
            actions_len: self.actions.len(),
            text_offsets: self.text_offsets.clone(),
            text_lens: self.text_lens.clone(),
            attr_offsets: self.attr_offsets.clone(),
            attr_lens: self.attr_lens.clone(),
        }
    }

    /// Roll back the arena state to a previously captured checkpoint.
    /// Walks the dirty diff log in reverse, restoring modified bytes, and
    /// truncating newly added nodes and text back to checkpoint dimensions.
    pub fn rewind(&mut self, cp: &Checkpoint) -> Result<usize, String> {
        if cp.epoch_id != self.epoch_id {
            return Err(format!(
                "checkpoint epoch {} mismatch with arena epoch {}",
                cp.epoch_id, self.epoch_id
            ));
        }

        let mut restored_bytes = 0;

        // 1. Walk dirty ranges in reverse from current cursor back to checkpoint cursor
        while self.dirty_log.len() > cp.dirty_cursor {
            if let Some(range) = self.dirty_log.pop() {
                if range.frame == 0 {
                    // text_arena
                    let end = (range.offset + range.old_bytes.len()).min(self.text_arena.len());
                    let copy_len = end.saturating_sub(range.offset);
                    self.text_arena[range.offset..range.offset + copy_len]
                        .copy_from_slice(&range.old_bytes[..copy_len]);
                    restored_bytes += copy_len;
                }
            }
        }

        // 2. Truncate / restore node offsets and lengths
        self.tags.truncate(cp.node_count);
        self.parents.truncate(cp.node_count);
        self.attr_offsets = cp.attr_offsets.clone();
        self.attr_lens = cp.attr_lens.clone();
        self.text_offsets = cp.text_offsets.clone();
        self.text_lens = cp.text_lens.clone();

        // 3. Truncate newly appended buffers
        self.text_arena.truncate(cp.text_len);
        self.attr_data.truncate(cp.attr_len);
        self.actions.truncate(cp.actions_len);

        Ok(restored_bytes)
    }

    /// Fork the page arena in microseconds.
    /// Produces an independent arena via fast memcpy of contiguous frames,
    /// assigning a new epoch and fresh dirty log.
    pub fn fork(&self) -> Self {
        let new_epoch = EPOCH_GEN.fetch_add(1, Ordering::Relaxed);
        Self {
            epoch_id: new_epoch,
            tags: self.tags.clone(),
            parents: self.parents.clone(),
            attr_data: self.attr_data.clone(),
            attr_offsets: self.attr_offsets.clone(),
            attr_lens: self.attr_lens.clone(),
            text_arena: self.text_arena.clone(),
            text_offsets: self.text_offsets.clone(),
            text_lens: self.text_lens.clone(),
            actions: self.actions.clone(),
            dirty_log: Vec::new(),
        }
    }

    /// Zero-copy binary batch read of specified node IDs (Opcode 0x0C).
    /// Serializes requested nodes into a compact binary slice without JSON roundtrips.
    pub fn batch_read(&self, node_ids: &[u32]) -> Vec<u8> {
        let mut buf = Vec::with_capacity(node_ids.len() * 32);
        // Header: [count: u32]
        buf.extend_from_slice(&(node_ids.len() as u32).to_le_bytes());

        for &nid in node_ids {
            let tag = self.get_tag(nid);
            let parent = self.get_parent(nid).unwrap_or(u32::MAX);
            let text = self.get_text(nid);
            let text_bytes = text.as_bytes();

            // Record: [node_id: u32, tag: u16, parent: u32, text_len: u32, text_bytes...]
            buf.extend_from_slice(&nid.to_le_bytes());
            buf.extend_from_slice(&(tag as u16).to_le_bytes());
            buf.extend_from_slice(&parent.to_le_bytes());
            buf.extend_from_slice(&(text_bytes.len() as u32).to_le_bytes());
            buf.extend_from_slice(text_bytes);
        }

        buf
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parena_basic_operations() {
        let mut arena = Parena::new();
        let doc_id = arena.add_node(TagId::Document, u32::MAX, &[], "");
        let html_id = arena.add_node(TagId::Html, doc_id, &[("lang", "en")], "");
        let body_id = arena.add_node(TagId::Body, html_id, &[], "");
        let btn_id = arena.add_node(TagId::Button, body_id, &[("id", "submit-btn"), ("class", "primary")], "Submit Form");

        assert_eq!(arena.len(), 4);
        assert_eq!(arena.get_tag(btn_id), TagId::Button);
        assert_eq!(arena.get_parent(btn_id), Some(body_id));
        assert_eq!(arena.get_text(btn_id), "Submit Form");

        let attrs = arena.get_attrs(btn_id);
        assert_eq!(attrs.get("id"), Some(&"submit-btn".to_string()));
        assert_eq!(attrs.get("class"), Some(&"primary".to_string()));
    }

    #[test]
    fn test_parena_checkpoint_and_rewind() {
        let mut arena = Parena::new();
        let body = arena.add_node(TagId::Body, 0, &[], "");
        let input = arena.add_node(TagId::Input, body, &[("type", "text")], "Initial Text");

        let cp = arena.checkpoint();

        // Mutate text
        arena.set_text(input, "Modified User Input");
        assert_eq!(arena.get_text(input), "Modified User Input");

        // Add extra node
        let extra = arena.add_node(TagId::Div, body, &[], "Dynamically Injected");
        assert_eq!(arena.len(), 3);
        assert_eq!(arena.get_text(extra), "Dynamically Injected");

        // Rewind to checkpoint
        let restored = arena.rewind(&cp).expect("rewind failed");
        assert!(restored > 0);

        assert_eq!(arena.len(), 2);
        assert_eq!(arena.get_text(input), "Initial Text");
    }

    #[test]
    fn test_parena_fast_fork() {
        let mut arena = Parena::new();
        let body = arena.add_node(TagId::Body, 0, &[], "");
        let p = arena.add_node(TagId::P, body, &[], "Branch Main");

        // Fast fork
        let mut branch_a = arena.fork();
        let mut branch_b = arena.fork();

        assert_ne!(arena.epoch_id, branch_a.epoch_id);
        assert_ne!(branch_a.epoch_id, branch_b.epoch_id);

        // Branch A modifies text
        branch_a.set_text(p, "Branch Alpha Verdict");
        // Branch B adds a node
        branch_b.add_node(TagId::Span, p, &[], "Branch Beta Child");

        assert_eq!(arena.get_text(p), "Branch Main");
        assert_eq!(branch_a.get_text(p), "Branch Alpha Verdict");
        assert_eq!(branch_b.len(), 3);
        assert_eq!(arena.len(), 2);
    }

    #[test]
    fn test_parena_batch_read() {
        let mut arena = Parena::new();
        let n0 = arena.add_node(TagId::H1, 0, &[], "Title Heading");
        let n1 = arena.add_node(TagId::P, 0, &[], "Paragraph content");

        let bytes = arena.batch_read(&[n0, n1]);
        assert!(!bytes.is_empty());
        let count = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
        assert_eq!(count, 2);
    }
}
