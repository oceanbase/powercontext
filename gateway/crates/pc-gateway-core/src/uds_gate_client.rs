// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! 同机组合部署形态：HTTP over Unix Domain Socket。
//!
//! PR #2199：启用后 Rust 独占公网 TCP 端口，Python 只监听本机 UDS；
//! 网关的 check/capabilities 都走这个通道。
//!
//! 连接按对端（唯一的 socket 路径）池化：keep-alive 复用空闲连接，
//! 池有上限；connect/read 均有超时，防止对端假死导致请求悬挂。
//! 认证与 TCP 形态一致（UDS 不替代 bearer 认证）。

use crate::{GateClient, GateError};
use pc_gateway_types::{
    CaptureContentSourceRequest, ContextCheckRequest, ContextCheckResult, PrepareContextRequest,
    PrepareContextResult,
};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt, ReadHalf, WriteHalf};
use tokio::net::UnixStream;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
const READ_TIMEOUT: Duration = Duration::from_secs(30);
const MAX_IDLE_CONNECTIONS: usize = 8;
const MAX_HEADER_BYTES: usize = 64 * 1024;
const MAX_BODY_BYTES: usize = 8 * 1024 * 1024;

#[derive(Clone)]
pub struct UdsGateClient {
    socket: PathBuf,
    token: Option<String>,
    pool: Arc<Mutex<Vec<UnixStream>>>,
}

impl std::fmt::Debug for UdsGateClient {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("UdsGateClient")
            .field("socket", &self.socket)
            .field("token", &self.token.as_ref().map(|_| ".."))
            .field("idle", &self.pool.lock().map(|p| p.len()).unwrap_or(0))
            .finish()
    }
}

impl UdsGateClient {
    pub fn new(socket: impl Into<PathBuf>) -> Self {
        Self::with_token(socket, std::env::var("PC_GATEWAY_CORE_TOKEN").ok())
    }

    pub fn with_token(socket: impl Into<PathBuf>, token: Option<String>) -> Self {
        Self {
            socket: socket.into(),
            token: token.filter(|t| !t.is_empty()),
            pool: Arc::new(Mutex::new(Vec::new())),
        }
    }

    fn take_idle(&self) -> Option<UnixStream> {
        self.pool.lock().ok()?.pop()
    }

    fn return_idle(&self, stream: UnixStream) {
        if let Ok(mut pool) = self.pool.lock() {
            if pool.len() < MAX_IDLE_CONNECTIONS {
                pool.push(stream);
            }
        }
    }

    /// One operation budget covers pool acquisition, both attempts and complete framing.
    /// Only read-only operations and exact capture bytes may replay after handoff.
    async fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
        replay_safe: bool,
    ) -> Result<(u16, Vec<u8>), GateError> {
        tokio::time::timeout(READ_TIMEOUT, async {
            for attempt in 0..2 {
                if attempt != 0 {
                    // Return to the enclosing lifecycle before starting recovery, even
                    // when the failed socket completed synchronously in this poll.
                    tokio::task::yield_now().await;
                }
                match self.request_once(method, path, body, attempt == 0).await {
                    Ok(response) => return Ok(response),
                    Err(failure) => {
                        if attempt == 0
                            && matches!(failure.error, GateError::GateUnavailable(_))
                            && (replay_safe || !failure.handed_off)
                        {
                            continue;
                        }
                        return Err(failure.error);
                    }
                }
            }
            unreachable!("the last attempt always returns")
        })
        .await
        .map_err(|_| GateError::GateUnavailable("uds operation timeout".into()))?
    }

    async fn request_once(
        &self,
        method: &str,
        path: &str,
        body: Option<&[u8]>,
        prefer_pool: bool,
    ) -> Result<(u16, Vec<u8>), RpcFailure> {
        let mut leased = LeasedConnection::new(self.clone());
        let stream = leased.acquire(prefer_pool).await.map_err(|e| RpcFailure {
            error: GateError::GateUnavailable(format!("uds connect: {}", e.kind())),
            handed_off: false,
        })?;
        let (mut rd, mut wr) = tokio::io::split(stream);

        let body = body.unwrap_or(&[]);
        let mut head = format!(
            "{method} {path} HTTP/1.1\r\nHost: powercontext\r\nContent-Type: application/json\r\n\
             x-powercontext-scope-mode: current\r\nContent-Length: {}\r\n",
            body.len()
        );
        if let Some(t) = &self.token {
            head.push_str(&format!("Authorization: Bearer {t}\r\n"));
        }
        head.push_str("Connection: keep-alive\r\n\r\n");

        // Track successful writes, not response arrival or error text. A partial header
        // is already handoff; an EOF afterwards says nothing about Core execution.
        let mut request = head.into_bytes();
        request.extend_from_slice(body);
        let mut written = 0;
        while written < request.len() {
            let result = wr.write(&request[written..]).await;
            match result {
                Ok(n) if n > 0 => written += n,
                _ => {
                    return Err(RpcFailure {
                        error: GateError::GateUnavailable("uds write failed".into()),
                        handed_off: written != 0,
                    })
                }
            }
        }
        let (status, body, reusable) =
            read_response(&mut rd).await.map_err(|error| RpcFailure {
                error,
                handed_off: true,
            })?;
        if reusable {
            leased.release(rd, wr);
        }
        Ok((status, body))
    }
}

// Private transport evidence; never infer handoff from a GateError message.
struct RpcFailure {
    error: GateError,
    handed_off: bool,
}

/// 从池中租借/归还连接。出错时不归还（连接状态不可信）。
struct LeasedConnection {
    client: UdsGateClient,
    returned: bool,
}

impl LeasedConnection {
    fn new(client: UdsGateClient) -> Self {
        Self {
            client,
            returned: false,
        }
    }

    async fn acquire(&mut self, prefer_pool: bool) -> std::io::Result<UnixStream> {
        // 先试空闲连接；真正的失效（对端已关闭）会在读写时报错，
        // 由上层的单次重试兜底。
        if prefer_pool {
            if let Some(idle) = self.client.take_idle() {
                if idle.writable().await.is_ok() {
                    return Ok(idle);
                }
            }
        }
        tokio::time::timeout(CONNECT_TIMEOUT, UnixStream::connect(&self.client.socket))
            .await
            .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "uds connect timeout"))?
    }

    /// 仅在读到完整响应且连接可复用时调用。
    fn release(&mut self, rd: ReadHalf<UnixStream>, wr: WriteHalf<UnixStream>) {
        if self.returned {
            return;
        }
        let stream = rd.unsplit(wr);
        self.client.return_idle(stream);
        self.returned = true;
    }
}

/// 读取完整 HTTP/1.1 响应。返回 (status, body, 连接是否可复用)。
async fn read_response(rd: &mut ReadHalf<UnixStream>) -> Result<(u16, Vec<u8>, bool), GateError> {
    let mut buf: Vec<u8> = Vec::with_capacity(8 * 1024);
    let header_end = loop {
        if let Some(pos) = find_terminator(&buf) {
            break pos;
        }
        if buf.len() > MAX_HEADER_BYTES {
            return Err(GateError::Malformed("uds header too large".into()));
        }
        let n = read_with_timeout(rd, &mut buf, 4096).await?;
        if n == 0 {
            return Err(GateError::GateUnavailable(
                "uds closed before headers".into(),
            ));
        }
    };

    let head = String::from_utf8_lossy(&buf[..header_end]).into_owned();
    let mut lines = head.lines();
    let status_line = lines.next().unwrap_or_default();
    let status: u16 = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| GateError::Malformed(format!("bad status line: {status_line}")))?;

    if !(200..300).contains(&status) {
        return Err(GateError::GateRejected(status));
    }
    let mut content_length: Option<usize> = None;
    let mut chunked = false;
    let mut keep_alive = true; // HTTP/1.1 默认 keep-alive
    for line in lines {
        let Some((k, v)) = line.split_once(':') else {
            continue;
        };
        let k = k.trim().to_ascii_lowercase();
        let v = v.trim();
        match k.as_str() {
            "content-length" => content_length = v.parse().ok(),
            "transfer-encoding" if v.to_ascii_lowercase().contains("chunked") => chunked = true,
            "connection" if v.to_ascii_lowercase().contains("close") => keep_alive = false,
            _ => {}
        }
    }

    let mut rest = buf.split_off(header_end + 4);
    let body = if chunked {
        // chunked：读完 0-chunk 后必须消费 trailer（至少终止空行），
        // 否则残留字节会污染复用连接上的下一个响应。
        let (decoded, trailer_clean) = read_chunked(rd, &mut rest).await?;
        if !trailer_clean {
            keep_alive = false; // trailer 未完整消费，连接不可复用
        }
        decoded
    } else {
        let len = content_length.unwrap_or(rest.len());
        // P2 review：Content-Length 声明值超预算时立即拒绝，不读完再拒。
        if len > MAX_BODY_BYTES {
            return Err(GateError::Malformed("uds body too large".into()));
        }
        if rest.len() < len {
            read_more(rd, &mut rest, len).await?;
            rest
        } else if rest.len() > len {
            // 超出声明长度：缓冲里有不属于本响应的字节（串包/残留），
            // 截断正文，且连接不可复用。
            rest.truncate(len);
            keep_alive = false;
            rest
        } else {
            rest
        }
    };
    if body.len() > MAX_BODY_BYTES {
        return Err(GateError::Malformed("uds body too large".into()));
    }
    Ok((status, body, keep_alive))
}

async fn read_more(
    rd: &mut ReadHalf<UnixStream>,
    buf: &mut Vec<u8>,
    target: usize,
) -> Result<(), GateError> {
    while buf.len() < target {
        let n = read_with_timeout(rd, buf, 8192).await?;
        if n == 0 {
            return Err(GateError::GateUnavailable("uds closed mid-body".into()));
        }
    }
    Ok(())
}

/// 解码 chunked 编码。返回 (body, trailer 是否完整消费)。
async fn read_chunked(
    rd: &mut ReadHalf<UnixStream>,
    buf: &mut Vec<u8>,
) -> Result<(Vec<u8>, bool), GateError> {
    let mut out = Vec::new();
    loop {
        // 找一行作为 chunk size。
        let line_end = loop {
            if let Some(pos) = buf.windows(2).position(|w| w == b"\r\n") {
                break pos;
            }
            let n = read_with_timeout(rd, buf, 4096).await?;
            if n == 0 {
                return Err(GateError::GateUnavailable("uds closed mid-chunk".into()));
            }
        };
        let size_str = String::from_utf8_lossy(&buf[..line_end]).into_owned();
        buf.drain(..line_end + 2);
        let size = usize::from_str_radix(size_str.trim().split(';').next().unwrap_or("0"), 16)
            .map_err(|_| GateError::Malformed(format!("bad chunk size: {size_str}")))?;
        // P2 review：对端可控的 size 必须在算术前验预算（防溢出 panic）。
        if size > MAX_BODY_BYTES {
            return Err(GateError::Malformed(format!(
                "chunk size {size:#x} exceeds body budget {MAX_BODY_BYTES}"
            )));
        }
        if size == 0 {
            // 消费 trailer：连续读到空行（或读尽缓冲且对端无更多数据）。
            loop {
                if let Some(pos) = buf.windows(2).position(|w| w == b"\r\n") {
                    let line = String::from_utf8_lossy(&buf[..pos]).into_owned();
                    buf.drain(..pos + 2);
                    if line.is_empty() {
                        return Ok((out, true)); // trailer 结束空行
                    }
                    continue; // trailer 头部行
                }
                if read_with_timeout(rd, buf, 1024).await? == 0 {
                    return Err(GateError::GateUnavailable("uds closed mid-trailer".into()));
                }
            }
        }
        // 累计预算：在读取 chunk 数据之前检查（不在读完 64 MiB 后才拒绝）。
        if out.len() + size > MAX_BODY_BYTES {
            return Err(GateError::Malformed("uds body too large".into()));
        }
        let needed = size + 2; // 数据 + CRLF
        while buf.len() < needed {
            let n = read_with_timeout(rd, buf, 8192).await?;
            if n == 0 {
                return Err(GateError::GateUnavailable(
                    "uds closed mid-chunk-data".into(),
                ));
            }
        }
        if &buf[size..needed] != b"\r\n" {
            return Err(GateError::Malformed("invalid chunk data terminator".into()));
        }
        out.extend_from_slice(&buf[..size]);
        buf.drain(..needed);
    }
}

/// 超时读取一块数据追加到 buf，返回读取字节数（0 = 对端关闭）。
async fn read_with_timeout(
    rd: &mut ReadHalf<UnixStream>,
    buf: &mut Vec<u8>,
    chunk_size: usize,
) -> Result<usize, GateError> {
    let mut chunk = vec![0u8; chunk_size];
    let n = tokio::time::timeout(READ_TIMEOUT, rd.read(&mut chunk))
        .await
        .map_err(|_| GateError::GateUnavailable("uds read timeout".into()))?
        .map_err(|e| GateError::GateUnavailable(format!("uds read: {e}")))?;
    buf.extend_from_slice(&chunk[..n]);
    Ok(n)
}

fn find_terminator(buf: &[u8]) -> Option<usize> {
    buf.windows(4).position(|w| w == b"\r\n\r\n")
}

impl GateClient for UdsGateClient {
    async fn check(&self, request: ContextCheckRequest) -> Result<ContextCheckResult, GateError> {
        let body = serde_json::to_vec(&request).expect("serialize gate request");
        let this = self.clone();
        {
            let (status, body) = this
                .request("POST", "/v1/context/check", Some(&body), true)
                .await?;
            if !(200..300).contains(&status) {
                return Err(GateError::GateRejected(status));
            }
            serde_json::from_slice(&body)
                .map_err(|e| GateError::Malformed(format!("decode check: {e}")))
        }
    }

    async fn capabilities(&self) -> Result<serde_json::Value, GateError> {
        let this = self.clone();
        {
            let (status, body) = this.request("GET", "/v1/capabilities", None, true).await?;
            if !(200..300).contains(&status) {
                return Err(GateError::GateRejected(status));
            }
            serde_json::from_slice(&body)
                .map_err(|e| GateError::Malformed(format!("decode capabilities: {e}")))
        }
    }

    async fn prepare(
        &self,
        request: PrepareContextRequest,
    ) -> Result<PrepareContextResult, GateError> {
        let body = serde_json::to_vec(&request).expect("serialize gate request");
        let this = self.clone();
        {
            let (status, body) = this
                .request("POST", "/v1/context/prepare", Some(&body), false)
                .await?;
            if !(200..300).contains(&status) {
                return Err(GateError::GateRejected(status));
            }
            serde_json::from_slice(&body)
                .map_err(|e| GateError::Malformed(format!("decode prepare: {e}")))
        }
    }

    async fn capture(
        &self,
        request: CaptureContentSourceRequest,
    ) -> Result<serde_json::Value, GateError> {
        let body = serde_json::to_vec(&request).expect("serialize gate request");
        let this = self.clone();
        {
            let (status, body) = this
                .request("POST", "/v1/sources/content", Some(&body), true)
                .await?;
            if !(200..300).contains(&status) {
                return Err(GateError::GateRejected(status));
            }
            serde_json::from_slice(&body)
                .map_err(|e| GateError::Malformed(format!("decode capture: {e}")))
        }
    }
}
