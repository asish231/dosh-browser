//! WebSocket transport for the dosh program (plan item N2).
//!
//! A thin wrapper over `tokio-tungstenite` (RFC 6455, ws + wss). What this
//! module adds is *evidence*: every session open/close and every frame — text
//! or binary, inbound or outbound — is recorded into the session's ledger as
//! `WsSessionRecord`/`WsFrameRecord`, so `net.ws` can show what the page
//! actually exchanged. Inbound frames are also queued for the page runtime,
//! which pumps them into script as `globalThis.__wsDeliver(id, data)`.
//!
//! Frame payload capture is capped at 4096 bytes; the frame record's real
//! length is kept either way so an oversized capture is disclosed, not lost.
//!
//! The ledger is the engine's `Arc<tokio::sync::Mutex<NetworkLedger>>` — pump
//! tasks run on the async runtime, lock it with `.await`; synchronous host-seam
//! senders block briefly on it (frame bookkeeping only, no awaits inside).

use std::collections::HashMap;
use std::sync::Arc;

use futures_util::{SinkExt, StreamExt};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, Mutex as AsyncMutex};
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::http::HeaderValue;
use tokio_tungstenite::tungstenite::protocol::CloseFrame as TtCloseFrame;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::{MaybeTlsStream, WebSocketStream};

use crate::telemetry::NetworkLedger;

/// Cap on a single recorded frame payload. True length is always recorded.
pub const MAX_RECORDED_FRAME_BYTES: usize = 4096;

pub type SharedLedger = Arc<AsyncMutex<NetworkLedger>>;

/// Inbound frame queued for the page runtime.
#[derive(Debug, Clone)]
pub struct WsInbound {
    pub session: u64,
    pub data: Vec<u8>,
    pub binary: bool,
}

struct SessionCtl {
    send_tx: mpsc::UnboundedSender<Message>,
    close_tx: mpsc::UnboundedSender<u16>,
}

pub struct WsRegistry {
    ledger: SharedLedger,
    sessions: HashMap<u64, SessionCtl>,
    inbound_tx: mpsc::UnboundedSender<WsInbound>,
    inbound_rx: mpsc::UnboundedReceiver<WsInbound>,
}

impl WsRegistry {
    pub fn new(ledger: SharedLedger) -> Self {
        let (inbound_tx, inbound_rx) = mpsc::unbounded_channel();
        Self {
            ledger,
            sessions: HashMap::new(),
            inbound_tx,
            inbound_rx,
        }
    }

    /// Perform the RFC 6455 handshake and start the pump task for the socket.
    /// The ledger records the session and every subsequent frame.
    pub async fn connect(
        &mut self,
        url: &str,
        extra_headers: &HashMap<String, String>,
    ) -> Result<u64, String> {
        let (session, stream, _protocol) = handshake_open(url, &self.ledger, extra_headers).await?;
        self.admit(session, stream);
        Ok(session)
    }

    /// Attach an already-handshaken socket to this registry and start its
    /// pump task. The synchronous half of `connect` for host-seam callers.
    pub fn admit(&mut self, session: u64, stream: WebSocketStream<MaybeTlsStream<TcpStream>>) {
        let (send_tx, send_rx) = mpsc::unbounded_channel::<Message>();
        let (close_tx, close_rx) = mpsc::unbounded_channel::<u16>();
        let ledger = self.ledger.clone();
        let inbound = self.inbound_tx.clone();
        tokio::spawn(pump_session(session, stream, ledger, inbound, close_rx, send_rx));
        self.sessions.insert(session, SessionCtl { send_tx, close_tx });
    }

    pub async fn send_text(&mut self, session: u64, text: &str) -> Result<(), String> {
        self.send(session, Message::text(text)).await
    }

    pub async fn send_binary(&mut self, session: u64, payload: &[u8]) -> Result<(), String> {
        self.send(session, Message::binary(payload.to_vec())).await
    }

    async fn send(&mut self, session: u64, msg: Message) -> Result<(), String> {
        let (direction_opcode, payload): (u8, &[u8]) = match &msg {
            Message::Text(t) => (1, t.as_bytes()),
            Message::Binary(b) => (2, b),
            _ => (1, &[]),
        };
        self.ledger
            .lock()
            .await
            .ws_frame(session, "out", direction_opcode, payload, payload.len());
        self.sessions
            .get(&session)
            .ok_or_else(|| format!("no websocket session {session}"))?
            .send_tx
            .send(msg)
            .map_err(|_| "websocket session already closed".to_string())
    }

    /// Ask the pump to send a protocol close and finish the session.
    pub fn close(&mut self, session: u64, code: u16) -> Result<(), String> {
        if let Some(ctl) = self.sessions.remove(&session) {
            ctl.close_tx
                .send(code)
                .map_err(|_| "websocket session already closed".to_string())?;
        }
        Ok(())
    }

    /// Inbound frames the page runtime has not yet been delivered. Consuming
    /// these marks them visited; `net.ws` history comes from the ledger,
    /// which is never drained by this.
    pub fn take_inbound(&mut self) -> Vec<WsInbound> {
        let mut out = Vec::new();
        while let Ok(f) = self.inbound_rx.try_recv() {
            out.push(f);
        }
        out
    }

    pub fn is_live(&self, session: u64) -> bool {
        self.sessions.contains_key(&session)
    }
}

/// Async half of the open path: RFC 6455 handshake + ledger session open.
/// Returns the session id and the raw socket for a subsequent `admit`.
pub async fn handshake_open(
    url: &str,
    ledger: &SharedLedger,
    extra_headers: &HashMap<String, String>,
) -> Result<(u64, WebSocketStream<MaybeTlsStream<TcpStream>>, String), String> {
    let mut request = url
        .into_client_request()
        .map_err(|e| format!("bad websocket url: {e}"))?;
    for (k, v) in extra_headers {
        let name = tokio_tungstenite::tungstenite::http::HeaderName::from_lowercase(
            k.to_lowercase().as_bytes(),
        )
        .map_err(|e| format!("bad header name {k}: {e}"))?;
        request
            .headers_mut()
            .insert(name, HeaderValue::from_str(v).map_err(|e| format!("bad header value: {e}"))?);
    }

    let (stream, resp) = tokio_tungstenite::connect_async(request)
        .await
        .map_err(|e| format!("ws connect failed: {e}"))?;
    // Evidence trail: the negotiated subprotocol (if any), returned with the
    // session so the page sees its real negotiated value.
    let _protocol = resp
        .headers()
        .get("sec-websocket-protocol")
        .and_then(|v| v.to_str().ok())
        .map(str::to_string)
        .unwrap_or_default();

    let session = ledger.lock().await.ws_open(url);
    Ok((session, stream, _protocol))
}

/// Serve one socket until close: forwards outbound frames the shell gives us,
/// and pumps inbound frames into the ledger + the page inbox.
async fn pump_session(
    session: u64,
    mut stream: WebSocketStream<MaybeTlsStream<TcpStream>>,
    ledger: SharedLedger,
    inbound: mpsc::UnboundedSender<WsInbound>,
    mut close_rx: mpsc::UnboundedReceiver<u16>,
    mut send_rx: mpsc::UnboundedReceiver<Message>,
) {
    loop {
        tokio::select! {
            maybe_close = close_rx.recv() => {
                let code = maybe_close.unwrap_or(1000);
                let _ = stream
                    .send(Message::Close(Some(TtCloseFrame {
                        code: code.into(),
                        reason: "".into(),
                    })))
                    .await;
                ledger.lock().await.ws_close(session, code);
                break;
            }
            maybe_out = send_rx.recv() => {
                match maybe_out {
                    Some(m) => {
                        if stream.send(m).await.is_err() {
                            ledger.lock().await.ws_close(session, 1006);
                            break;
                        }
                    }
                    None => {
                        // Registry dropped the sender: graceful protocol close.
                        let _ = stream.send(Message::Close(None)).await;
                        ledger.lock().await.ws_close(session, 1000);
                        break;
                    }
                }
            }
            maybe_in = stream.next() => {
                match maybe_in {
                    Some(Ok(Message::Text(t))) => {
                        let bytes = t.as_bytes();
                        record_frame(&ledger, session, "in", 1, bytes).await;

                        let _ = inbound.send(WsInbound {
                            session,
                            data: bytes.to_vec(),
                            binary: false,
                        });
                    }
                    Some(Ok(Message::Binary(b))) => {
                        record_frame(&ledger, session, "in", 2, &b).await;
                        let _ = inbound.send(WsInbound {
                            session,
                            data: b.to_vec(),
                            binary: true,
                        });
                    }
                    Some(Ok(Message::Ping(p))) => {
                        // tungstenite auto-answers with Pong; the ping is
                        // recorded as an inbound control frame (opcode 9).
                        record_frame(&ledger, session, "in", 9, p.as_ref()).await;
                    }
                    Some(Ok(Message::Close(cf))) => {
                        ledger.lock().await.ws_close(session, cf.map(|c| u16::from(c.code)).unwrap_or(1006));
                        break;
                    }
                    Some(Ok(_)) => {
                        // Pong / other control frames: transport-managed.
                    }
                    Some(Err(_)) | None => {
                        ledger.lock().await.ws_close(session, 1006);
                        break;
                    }
                }
            }
        }
    }
}

async fn record_frame(ledger: &SharedLedger, session: u64, direction: &str, opcode: u8, payload: &[u8]) {
    let capped = if payload.len() > MAX_RECORDED_FRAME_BYTES {
        &payload[..MAX_RECORDED_FRAME_BYTES]
    } else {
        payload
    };
    ledger
        .lock()
        .await
        .ws_frame(session, direction, opcode, capped, payload.len());
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A real in-process RFC 6455 echo server (serves as the peer side).
    async fn spawn_echo_server() -> String {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        tokio::spawn(async move {
            while let Ok((sock, _)) = listener.accept().await {
                tokio::spawn(async move {
                    let ws = tokio_tungstenite::accept_async(sock).await.unwrap();
                    let (mut tx, mut rx) = ws.split();
                    while let Some(Ok(m)) = rx.next().await {
                        match m {
                            Message::Text(t) => {
                                if tx.send(Message::text(format!("echo: {t}"))).await.is_err() {
                                    break;
                                }
                            }
                            Message::Binary(b) => {
                                if tx.send(Message::binary(b)).await.is_err() {
                                    break;
                                }
                            }
                            Message::Close(_) => break,
                            _ => {}
                        }
                    }
                });
            }
        });
        format!("ws://{addr}/echo")
    }

    #[tokio::test]
    async fn test_ws_roundtrip_text_binary_and_ledger() {
        let addr = spawn_echo_server().await;
        let ledger: SharedLedger =
            Arc::new(AsyncMutex::new(NetworkLedger::new(50, false)));
        let mut reg = WsRegistry::new(ledger.clone());

        let session = reg.connect(&addr, &HashMap::new()).await.expect("connect");

        // Outbound text -> server echoes it back with a prefix.
        reg.send_text(session, "hello ws").await.unwrap();
        // Binary frame round-trip.
        reg.send_binary(session, &[0x01, 0x02, 0x03]).await.unwrap();

        // Pump the inbound frames into the fictional "page".
        let mut got = Vec::new();
        for _ in 0..50 {
            for frame in reg.take_inbound() {
                got.push((frame.binary, String::from_utf8_lossy(&frame.data).to_string()));
            }
            if got.len() >= 2 {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        assert!(got.contains(&(
            false,
            "echo: hello ws".to_string(),
        )));
        assert!(got.iter().any(|(bin, _)| *bin));

        // Ledger evidence: 2 inbound + 2 outbound.
        let view = reg_ledger_view(&ledger, session).await;
        assert_eq!(view["total"], 4);
        let frames = view["frames"].as_array().unwrap();
        let in_text = frames
            .iter()
            .find(|f| f["direction"] == "in" && f["opcode"] == 1)
            .unwrap();
        assert_eq!(in_text["text"], "echo: hello ws");
        let out_text = frames
            .iter()
            .find(|f| f["direction"] == "out" && f["opcode"] == 1)
            .unwrap();
        assert_eq!(out_text["text"], "hello ws");

        // Protocol close is recorded.
        reg.close(session, 1000).unwrap();
        for _ in 0..25 {
            let view = reg_ledger_view(&ledger, session).await;
            if view["closed"] == true {
                break;
            }
            tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        }
        let view = reg_ledger_view(&ledger, session).await;
        assert_eq!(view["closed"], true);
    }

    async fn reg_ledger_view(ledger: &SharedLedger, session: u64) -> serde_json::Value {
        ledger.lock().await.ws_view(session, 0)
    }
}
