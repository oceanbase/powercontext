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

//! HTTP/1 connection ownership. Model permits outlive bodies and Hyper's pending writes.

use axum::{body::Body, http::header, response::Response, Router};
use pc_gateway_core::{LifecycleStopReason, RequestControl, RequestLifecycle};
use std::{
    convert::Infallible,
    future::Future,
    pin::Pin,
    sync::{Arc, Mutex},
    task::{Context, Poll},
};
use tokio::{
    io::{AsyncRead, AsyncWrite},
    net::TcpListener,
    sync::{watch, Notify, OwnedSemaphorePermit},
    task::JoinSet,
};
use tower::ServiceExt;

pub(crate) fn is_model_route(path: &str) -> bool {
    matches!(
        path,
        "/gateway/v1/chat/completions" | "/gateway/v1/responses"
    )
}

#[derive(Default)]
struct ModelState {
    control: Option<RequestControl>,
    lifecycle: Option<RequestLifecycle>,
    permit: Option<OwnedSemaphorePermit>,
}

/// Inserted before polling the Router. The connection driver clears resource
/// ownership after dropping Hyper and all inline request/body owners.
#[derive(Clone, Default)]
pub(crate) struct ModelConnection {
    state: Arc<Mutex<ModelState>>,
    registered: Arc<Notify>,
}

impl ModelConnection {
    fn begin(&self, control: RequestControl) {
        let mut state = self.state.lock().unwrap();
        state.lifecycle = Some(control.lifecycle());
        state.control = Some(control);
        self.registered.notify_one();
    }

    pub(crate) fn take_control(&self) -> RequestControl {
        self.state
            .lock()
            .unwrap()
            .control
            .take()
            .expect("one model request per closing connection")
    }

    pub(crate) fn hold(&self, permit: OwnedSemaphorePermit) {
        self.state.lock().unwrap().permit = Some(permit);
    }

    fn lifecycle(&self) -> Option<RequestLifecycle> {
        self.state.lock().unwrap().lifecycle.clone()
    }

    fn interrupt(&self, reason: LifecycleStopReason) {
        if let Some(lifecycle) = self.lifecycle() {
            lifecycle.cancellation().cancel_with(reason);
            let reason = lifecycle.checkpoint().unwrap_err();
            lifecycle.interrupt_local_delivery(reason);
        }
    }

    async fn stopped(&self) -> LifecycleStopReason {
        loop {
            let registered = self.registered.notified();
            if let Some(lifecycle) = self.lifecycle() {
                let cancellation = lifecycle.cancellation();
                tokio::select! {
                    _ = cancellation.cancelled() => {}
                    _ = tokio::time::sleep_until(lifecycle.observation().deadline()) => {}
                }
                return lifecycle.checkpoint().unwrap_err();
            }
            registered.await;
        }
    }
}

/// Hyper can discard a service future on EOF before returning its connection
/// result. Signal the known cause before dropping that future's Executor guard.
struct RequestOwner {
    work: Pin<Box<dyn Future<Output = Result<Response, Infallible>> + Send>>,
    model: Option<ModelConnection>,
    handed_off: bool,
}

impl Future for RequestOwner {
    type Output = Result<Response, Infallible>;

    fn poll(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        match self.work.as_mut().poll(cx) {
            Poll::Ready(Ok(mut response)) => {
                if let Some(model) = &self.model {
                    response.headers_mut().insert(
                        header::CONNECTION,
                        header::HeaderValue::from_static("close"),
                    );
                    if let Some(lifecycle) = model.lifecycle() {
                        lifecycle.begin_local_delivery();
                    }
                }
                self.handed_off = true;
                Poll::Ready(Ok(response))
            }
            other => other,
        }
    }
}

impl Drop for RequestOwner {
    fn drop(&mut self) {
        if !self.handed_off {
            if let Some(model) = &self.model {
                model.interrupt(LifecycleStopReason::RequestCancelled);
            }
        }
    }
}

pub(crate) async fn drive<I>(io: I, app: Router, mut shutdown: watch::Receiver<bool>)
where
    I: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let owner = ModelConnection::default();
    let service_owner = owner.clone();
    let service =
        hyper::service::service_fn(move |request: hyper::Request<hyper::body::Incoming>| {
            let mut request = request.map(Body::new);
            let model = is_model_route(request.uri().path()).then(|| service_owner.clone());
            if let Some(model) = &model {
                let id = crate::middleware::request_id::resolve(request.headers());
                request.extensions_mut().insert(id.clone());
                model.begin(RequestControl::new(
                    tokio::time::Instant::now() + std::time::Duration::from_secs(180),
                    Some(id),
                ));
                request.extensions_mut().insert(model.clone());
            }
            RequestOwner {
                work: Box::pin(app.clone().oneshot(request)),
                model,
                handed_off: false,
            }
        });
    // HTTP/1 only; half-close remains disabled so Hyper can observe EOF while
    // an inline service is waiting. No competing socket reader is installed.
    let mut connection = Box::pin(
        hyper::server::conn::http1::Builder::new()
            .serve_connection(hyper_util::rt::TokioIo::new(io), service),
    );
    let mut draining = false;
    loop {
        tokio::select! {
            biased;
            result = &mut connection => {
                if result.is_err() {
                    owner.interrupt(LifecycleStopReason::RequestCancelled);
                } else if let Some(lifecycle) = owner.lifecycle() {
                    lifecycle.complete_local_delivery();
                }
                break;
            }
            reason = owner.stopped() => {
                owner.interrupt(reason);
                break;
            }
            _ = shutdown.changed(), if !draining => {
                draining = true;
                connection.as_mut().graceful_shutdown();
            }
        }
    }
    // Destruction is synchronous: includes the service future, body and write
    // buffers. Only afterwards may the final owner release the model permit.
    drop(connection);
    *owner.state.lock().unwrap() = ModelState::default();
    drop(owner);
}

pub(crate) async fn serve(
    listener: TcpListener,
    app: Router,
    shutdown: impl Future<Output = ()>,
) -> std::io::Result<()> {
    let (signal, receiver) = watch::channel(false);
    let mut connections = JoinSet::new();
    let mut accept_after = tokio::time::Instant::now();
    tokio::pin!(shutdown);
    loop {
        tokio::select! {
            _ = &mut shutdown => break,
            accepted = async {
                tokio::time::sleep_until(accept_after).await;
                listener.accept().await
            } => {
                match accepted {
                    Ok((stream, _)) => {
                        connections.spawn(drive(stream, app.clone(), receiver.clone()));
                    }
                    Err(error) => {
                        // Preserve Axum's accept recovery: connection errors retry
                        // immediately, resource exhaustion backs off. The outer
                        // select keeps shutdown and owner joins live during backoff.
                        if !matches!(error.kind(), std::io::ErrorKind::ConnectionRefused
                            | std::io::ErrorKind::ConnectionAborted | std::io::ErrorKind::ConnectionReset) {
                            accept_after = tokio::time::Instant::now() + std::time::Duration::from_secs(1);
                        }
                    }
                }
            }
            _ = connections.join_next(), if !connections.is_empty() => {}
        }
    }
    signal.send_replace(true);
    while connections.join_next().await.is_some() {}
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::{extract::Request, middleware::Next};
    use pc_gateway_core::{
        ExecutionObservation, ExecutionState, ExecutionTerminal, LocalDeliveryState, ModelSendState,
    };
    use std::{sync::atomic::Ordering, time::Duration};
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    const REQUEST: &[u8] = b"POST /gateway/v1/chat/completions HTTP/1.1\r\nHost: local\r\nAuthorization: Bearer gateway-key\r\nContent-Length: 63\r\n\r\n{\"model\":\"test\",\"messages\":[{\"role\":\"user\",\"content\":\"hello\"}]}";

    struct WriteProbe<I> {
        io: I,
        pending: Arc<Notify>,
    }
    impl<I: AsyncRead + Unpin> AsyncRead for WriteProbe<I> {
        fn poll_read(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
            buf: &mut tokio::io::ReadBuf<'_>,
        ) -> Poll<std::io::Result<()>> {
            Pin::new(&mut self.io).poll_read(cx, buf)
        }
    }
    impl<I: AsyncWrite + Unpin> AsyncWrite for WriteProbe<I> {
        fn poll_write(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
            bytes: &[u8],
        ) -> Poll<std::io::Result<usize>> {
            let result = Pin::new(&mut self.io).poll_write(cx, bytes);
            if result.is_pending() {
                self.pending.notify_one();
            }
            result
        }
        fn poll_flush(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<std::io::Result<()>> {
            Pin::new(&mut self.io).poll_flush(cx)
        }
        fn poll_shutdown(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
        ) -> Poll<std::io::Result<()>> {
            Pin::new(&mut self.io).poll_shutdown(cx)
        }
    }

    struct BodyDrop {
        body: Body,
        dropped: Arc<Notify>,
    }
    impl Drop for BodyDrop {
        fn drop(&mut self) {
            self.dropped.notify_one();
        }
    }
    impl http_body::Body for BodyDrop {
        type Data = bytes::Bytes;
        type Error = axum::Error;
        fn poll_frame(
            mut self: Pin<&mut Self>,
            cx: &mut Context<'_>,
        ) -> Poll<Option<Result<http_body::Frame<Self::Data>, Self::Error>>> {
            Pin::new(&mut self.body).poll_frame(cx)
        }
        fn is_end_stream(&self) -> bool {
            self.body.is_end_stream()
        }
        fn size_hint(&self) -> http_body::SizeHint {
            self.body.size_hint()
        }
    }

    async fn bounded<F: Future>(work: F) -> F::Output {
        tokio::time::timeout(Duration::from_secs(3), work)
            .await
            .expect("local HTTP/1 operation stalled")
    }

    async fn normal_request(addr: std::net::SocketAddr) -> reqwest::Response {
        reqwest::Client::new()
            .post(format!("http://{addr}/gateway/v1/chat/completions"))
            .bearer_auth("gateway-key")
            .json(
                &serde_json::json!({"model":"test","messages":[{"role":"user","content":"hello"}]}),
            )
            .send()
            .await
            .unwrap()
    }

    #[tokio::test]
    async fn consumed_body_with_pending_writes_holds_capacity_until_connection_ends() {
        for (stop, explicit) in [
            (None, false),
            (Some(LifecycleStopReason::RequestCancelled), false),
            (Some(LifecycleStopReason::DeadlineExceeded), false),
            (Some(LifecycleStopReason::RequestCancelled), true),
        ] {
            let (app, upstream, received) = crate::ingress::tests::lifecycle_router().await;
            let observations = Arc::new(Mutex::new(Vec::<ExecutionObservation>::new()));
            let retained = observations.clone();
            let cancellations = Arc::new(Mutex::new(Vec::new()));
            let retained_cancellations = cancellations.clone();
            let dropped = Arc::new(Notify::new());
            let body_dropped = dropped.clone();
            let app = app.layer(axum::middleware::from_fn(
                move |req: Request, next: Next| {
                    let retained = retained.clone();
                    let retained_cancellations = retained_cancellations.clone();
                    let dropped = body_dropped.clone();
                    async move {
                        let response = next.run(req).await;
                        if let Some(obs) = response.extensions().get::<ExecutionObservation>() {
                            retained.lock().unwrap().push(obs.clone());
                            retained_cancellations.lock().unwrap().push(
                                response
                                    .extensions()
                                    .get::<pc_gateway_core::RequestCancellation>()
                                    .unwrap()
                                    .clone(),
                            );
                        }
                        let (parts, body) = response.into_parts();
                        Response::from_parts(parts, Body::new(BodyDrop { body, dropped }))
                    }
                },
            ));
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let (shutdown, signal) = tokio::sync::oneshot::channel();
            let server_app = app.clone();
            let server = tokio::spawn(serve(listener, server_app, async {
                let _ = signal.await;
            }));
            let (mut client, io) = tokio::io::duplex(64);
            let pending = Arc::new(Notify::new());
            let (_signal, receiver) = watch::channel(false);
            let connection = tokio::spawn(drive(
                WriteProbe {
                    io,
                    pending: pending.clone(),
                },
                app,
                receiver,
            ));
            bounded(client.write_all(REQUEST)).await.unwrap();
            bounded(dropped.notified()).await;
            bounded(pending.notified()).await;
            let observation = observations.lock().unwrap()[0].clone();
            let terminal = observation.snapshot().execution;
            assert_eq!(
                terminal,
                ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
            );
            assert_eq!(
                observation.snapshot().model_send,
                ModelSendState::ResponseReceived
            );
            assert_eq!(
                observation.snapshot().local_delivery,
                LocalDeliveryState::InProgress
            );
            assert!(!connection.is_finished());
            assert_eq!(received.admissions.load(Ordering::SeqCst), 1);
            assert_eq!(received.sends.load(Ordering::SeqCst), 1);
            let second = bounded(normal_request(addr)).await;
            assert_eq!(second.status(), reqwest::StatusCode::SERVICE_UNAVAILABLE);
            assert_eq!(
                second.json::<serde_json::Value>().await.unwrap()["error"],
                "concurrency_limit"
            );
            assert_eq!(received.admissions.load(Ordering::SeqCst), 1);
            assert_eq!(received.sends.load(Ordering::SeqCst), 1);
            match stop {
                None => {
                    let mut response = Vec::new();
                    bounded(client.read_to_end(&mut response)).await.unwrap();
                    assert!(String::from_utf8(response)
                        .unwrap()
                        .contains("connection: close"));
                }
                Some(LifecycleStopReason::RequestCancelled) => {
                    if explicit {
                        cancellations.lock().unwrap()[0].cancel();
                    } else {
                        drop(client);
                    }
                }
                Some(LifecycleStopReason::DeadlineExceeded) => {
                    // The original ingress budget, after response handoff. No new budget.
                    tokio::time::pause();
                    tokio::time::advance(Duration::from_secs(181)).await;
                    tokio::time::resume();
                }
                _ => unreachable!(),
            }
            bounded(connection).await.unwrap();
            assert_eq!(observation.snapshot().execution, terminal);
            assert_eq!(
                observation.snapshot().model_send,
                ModelSendState::ResponseReceived
            );
            assert_eq!(observation.snapshot().stop_reason, stop);
            assert_eq!(
                observation.snapshot().local_delivery,
                stop.map_or(
                    LocalDeliveryState::Completed,
                    LocalDeliveryState::Interrupted
                )
            );
            let recovered = bounded(normal_request(addr)).await;
            assert_eq!(recovered.status(), reqwest::StatusCode::CREATED);
            assert!(recovered.json::<serde_json::Value>().await.unwrap()["choices"].is_array());
            assert_eq!(received.sends.load(Ordering::SeqCst), 2);
            shutdown.send(()).unwrap();
            bounded(server).await.unwrap().unwrap();
            upstream.abort();
            upstream.await.unwrap_err();
        }
    }

    #[tokio::test]
    async fn real_listener_disconnect_stops_execution_without_rewriting_sending_facts() {
        for (phase, model_send) in [
            (1, ModelSendState::NoSend),
            (3, ModelSendState::PossiblySent),
            (2, ModelSendState::ResponseReceived),
        ] {
            let (app, upstream, received) = crate::ingress::tests::lifecycle_router().await;
            received.blocked_phase.store(phase, Ordering::SeqCst);
            let owners = Arc::new(Mutex::new(Vec::<ModelConnection>::new()));
            let retained = owners.clone();
            let app = app.layer(axum::middleware::from_fn(
                move |req: Request, next: Next| {
                    if let Some(owner) = req.extensions().get::<ModelConnection>() {
                        retained.lock().unwrap().push(owner.clone());
                    }
                    async move { next.run(req).await }
                },
            ));
            let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
            let addr = listener.local_addr().unwrap();
            let (shutdown, signal) = tokio::sync::oneshot::channel();
            let server = tokio::spawn(serve(listener, app, async {
                let _ = signal.await;
            }));
            let mut client = tokio::net::TcpStream::connect(addr).await.unwrap();
            client.write_all(REQUEST).await.unwrap();
            bounded(received.entered.notified()).await;
            let owner = owners.lock().unwrap().pop().unwrap();
            let observation = owner.lifecycle().unwrap().observation();
            drop(owner);
            drop(client);
            bounded(async {
                while observation.snapshot().stop_reason.is_none() {
                    tokio::task::yield_now().await;
                }
            })
            .await;
            assert_eq!(
                observation.snapshot().stop_reason,
                Some(LifecycleStopReason::RequestCancelled)
            );
            assert_eq!(observation.snapshot().model_send, model_send);
            assert_eq!(
                observation.snapshot().execution,
                ExecutionState::Finished(ExecutionTerminal::Stopped(
                    LifecycleStopReason::RequestCancelled
                ))
            );
            assert_eq!(
                observation.snapshot().local_delivery,
                LocalDeliveryState::NotStarted
            );
            assert_eq!(
                received.sends.load(Ordering::SeqCst),
                usize::from(phase != 1)
            );
            received.blocked_phase.store(0, Ordering::SeqCst);
            let recovered = bounded(normal_request(addr)).await;
            assert_eq!(recovered.status(), reqwest::StatusCode::CREATED);
            recovered.bytes().await.unwrap();
            owners.lock().unwrap().clear();
            let health = reqwest::get(format!("http://{addr}/healthz"))
                .await
                .unwrap();
            assert_eq!(health.status(), reqwest::StatusCode::OK);
            assert_eq!(
                received.sends.load(Ordering::SeqCst),
                1 + usize::from(phase != 1)
            );
            shutdown.send(()).unwrap();
            bounded(server).await.unwrap().unwrap();
            upstream.abort();
            upstream.await.unwrap_err();
        }
    }

    #[tokio::test]
    async fn real_tcp_backpressure_keeps_capacity_until_original_delivery_deadline() {
        let (app, upstream, received) = crate::ingress::tests::lifecycle_router().await;
        received.response_bytes.store(512 * 1024, Ordering::SeqCst);
        let observations = Arc::new(Mutex::new(Vec::<ExecutionObservation>::new()));
        let retained = observations.clone();
        let app = app.layer(axum::middleware::from_fn(
            move |req: Request, next: Next| {
                let retained = retained.clone();
                async move {
                    let response = next.run(req).await;
                    if let Some(obs) = response.extensions().get::<ExecutionObservation>() {
                        retained.lock().unwrap().push(obs.clone());
                    }
                    response
                }
            },
        ));
        // Small real socket buffers force observable TCP write backpressure.
        let socket = tokio::net::TcpSocket::new_v4().unwrap();
        socket.set_send_buffer_size(4096).unwrap();
        socket.bind("127.0.0.1:0".parse().unwrap()).unwrap();
        let listener = socket.listen(8).unwrap();
        let addr = listener.local_addr().unwrap();
        let socket = tokio::net::TcpSocket::new_v4().unwrap();
        socket.set_recv_buffer_size(4096).unwrap();
        let mut client = socket.connect(addr).await.unwrap();
        let (stream, _) = listener.accept().await.unwrap();
        let pending = Arc::new(Notify::new());
        let (_signal, receiver) = watch::channel(false);
        let connection = tokio::spawn(drive(
            WriteProbe {
                io: stream,
                pending: pending.clone(),
            },
            app.clone(),
            receiver,
        ));
        client.write_all(REQUEST).await.unwrap();
        bounded(pending.notified()).await;
        let observation = observations.lock().unwrap()[0].clone();
        assert_eq!(
            observation.snapshot().local_delivery,
            LocalDeliveryState::InProgress
        );
        assert!(!connection.is_finished());
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown, signal) = tokio::sync::oneshot::channel();
        let server = tokio::spawn(serve(listener, app, async {
            let _ = signal.await;
        }));
        assert_eq!(
            bounded(normal_request(addr)).await.status(),
            reqwest::StatusCode::SERVICE_UNAVAILABLE
        );
        assert_eq!(received.admissions.load(Ordering::SeqCst), 1);
        assert_eq!(received.sends.load(Ordering::SeqCst), 1);
        let deadline = observation.deadline();
        tokio::time::pause();
        tokio::time::advance(deadline - tokio::time::Instant::now() + Duration::from_millis(1))
            .await;
        tokio::time::resume();
        bounded(connection).await.unwrap();
        assert_eq!(
            observation.snapshot().local_delivery,
            LocalDeliveryState::Interrupted(LifecycleStopReason::DeadlineExceeded)
        );
        assert_eq!(
            observation.snapshot().execution,
            ExecutionState::Finished(ExecutionTerminal::ReleaseAuthorized)
        );
        assert_eq!(
            observation.snapshot().model_send,
            ModelSendState::ResponseReceived
        );
        received.response_bytes.store(0, Ordering::SeqCst);
        assert_eq!(
            bounded(normal_request(addr)).await.status(),
            reqwest::StatusCode::CREATED
        );
        shutdown.send(()).unwrap();
        bounded(server).await.unwrap().unwrap();
        upstream.abort();
        upstream.await.unwrap_err();
    }
}
