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

//! 端口接管代理：非 `/gateway/*` 的流量原样转发到 Python 的 UDS。
//! 原 Core/MCP 路由由此透传；流式（SSE/分块）由 hyper 客户端透传保证。

use crate::AppState;
use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use hyper::Uri as HyperUri;
use hyper_util::client::legacy::Client;
use hyper_util::rt::{TokioExecutor, TokioIo};
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use std::sync::Arc;
use tokio::net::UnixStream;

pub type SharedProxy = (Arc<AppState>, ProxyState);

#[derive(Clone)]
pub struct ProxyState {
    inner: Option<Arc<ProxyInner>>,
}

struct ProxyInner {
    socket: PathBuf,
    client: Client<UnixConnector, Body>,
}

impl ProxyState {
    pub fn from_env() -> Self {
        match std::env::var("PC_GATEWAY_CORE_UDS") {
            Ok(sock) => {
                let client = Client::builder(TokioExecutor::new()).build(UnixConnector {
                    socket: PathBuf::from(&sock),
                });
                tracing::info!(%sock, "proxy: forwarding non-gateway routes to Python UDS");
                Self {
                    inner: Some(Arc::new(ProxyInner {
                        socket: PathBuf::from(sock),
                        client,
                    })),
                }
            }
            Err(_) => {
                tracing::info!("proxy: no UDS configured; standalone mode");
                Self { inner: None }
            }
        }
    }
}

/// UDS 连接器：忽略 URI，始终连配置的 socket 路径。
/// 实现 tower Service（hyper-util legacy Client 的 Connect 约束）。
#[derive(Clone)]
struct UnixConnector {
    socket: PathBuf,
}

impl tower::Service<HyperUri> for UnixConnector {
    type Response = TokioIo<UnixStream>;
    type Error = std::io::Error;
    type Future = Pin<Box<dyn Future<Output = Result<Self::Response, Self::Error>> + Send>>;

    fn poll_ready(
        &mut self,
        _cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Result<(), Self::Error>> {
        std::task::Poll::Ready(Ok(()))
    }

    fn call(&mut self, _uri: HyperUri) -> Self::Future {
        let path = self.socket.clone();
        Box::pin(async move { UnixStream::connect(path).await.map(TokioIo::new) })
    }
}

pub async fn fallback(State((_, proxy)): State<SharedProxy>, req: Request) -> Response {
    let Some(inner) = proxy.inner else {
        return (
            StatusCode::NOT_FOUND,
            axum::Json(serde_json::json!({
                "error": "not_found",
                "hint": "standalone mode: only /gateway/v1 and /healthz are served",
            })),
        )
            .into_response();
    };

    let (parts, body) = req.into_parts();
    // hyper legacy client 要求绝对 URI；UDS 连接器忽略 host，用占位 authority。
    let path_q = parts
        .uri
        .path_and_query()
        .cloned()
        .unwrap_or_else(|| "/".parse().expect("root uri"));
    let uri = HyperUri::builder()
        .scheme("http")
        .authority("powercontext.internal")
        .path_and_query(path_q)
        .build()
        .unwrap_or_else(|_| HyperUri::default());

    let mut fwd = hyper::Request::builder()
        .method(parts.method)
        .uri(uri)
        .header("host", HeaderValue::from_static("powercontext"));
    if let Some(headers) = fwd.headers_mut() {
        *headers = parts.headers;
        if !headers.contains_key("host") {
            headers.insert("host", HeaderValue::from_static("powercontext"));
        }
    }
    let hyper_req = match fwd.body(body) {
        Ok(r) => r,
        Err(e) => {
            return (
                StatusCode::BAD_REQUEST,
                axum::Json(serde_json::json!({ "error": "bad_request", "detail": e.to_string() })),
            )
                .into_response()
        }
    };

    let _ = inner.socket; // socket 路径已注入连接器
    match inner.client.request(hyper_req).await {
        Ok(resp) => {
            let (rparts, rbody) = resp.into_parts();
            let mut response = Response::builder().status(rparts.status);
            if let Some(h) = response.headers_mut() {
                *h = rparts.headers;
            }
            response.body(Body::new(rbody)).unwrap_or_else(|e| {
                (StatusCode::BAD_GATEWAY, format!("proxy build: {e}")).into_response()
            })
        }
        Err(e) => (
            StatusCode::BAD_GATEWAY,
            axum::Json(serde_json::json!({
                "error": "core_unreachable",
                "detail": e.to_string(),
            })),
        )
            .into_response(),
    }
}
