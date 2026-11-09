// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use super::*;
use axum::{
    body::Bytes,
    extract::State,
    http::HeaderMap,
    response::IntoResponse,
    routing::{get, post},
    Router,
};
use serde_json::json;
use std::{
    collections::{BTreeMap, VecDeque},
    future::Future,
    sync::{Arc, Mutex},
};

#[derive(Default)]
struct Peer {
    replies: Mutex<VecDeque<Value>>,
    forms: Mutex<Vec<Value>>,
    tokens: Mutex<Vec<String>>,
    accounts: Mutex<Vec<Option<String>>>,
    token_started: tokio::sync::Notify,
    release_token: tokio::sync::Notify,
    resource_response: Value,
    device_reply: Mutex<Value>,
    device_forms: Mutex<Vec<Value>>,
    github_tokens: Mutex<Vec<String>>,
}

async fn token(
    State(peer): State<Arc<Peer>>,
    headers: HeaderMap,
    body: Bytes,
) -> axum::response::Response {
    assert!(!headers.contains_key("authorization"));
    assert_eq!(headers["content-type"], "application/x-www-form-urlencoded");
    let form: BTreeMap<String, String> = oauth2::url::form_urlencoded::parse(&body)
        .into_owned()
        .collect();
    peer.forms.lock().unwrap().push(json!(form));
    let reply = peer
        .replies
        .lock()
        .unwrap()
        .pop_front()
        .expect("unexpected token exchange");
    peer.token_started.notify_one();
    if reply["wait_for_cancel"] == true {
        peer.release_token.notified().await;
        return axum::Json(json!({"access_token":"late-write-forbidden", "expires_in":3600}))
            .into_response();
    }
    scripted_response(&reply)
}

async fn device(
    State(peer): State<Arc<Peer>>,
    headers: HeaderMap,
    body: Bytes,
) -> axum::response::Response {
    assert!(!headers.contains_key("authorization"));
    assert!(headers["user-agent"]
        .to_str()
        .unwrap()
        .starts_with("PowerContextGateway/"));
    let form: BTreeMap<String, String> = oauth2::url::form_urlencoded::parse(&body)
        .into_owned()
        .collect();
    peer.device_forms.lock().unwrap().push(json!(form));
    axum::Json(peer.device_reply.lock().unwrap().clone()).into_response()
}

async fn copilot_token(
    State(peer): State<Arc<Peer>>,
    headers: HeaderMap,
) -> axum::response::Response {
    assert_eq!(headers["editor-version"], "vscode/1.140.0");
    assert!(headers["user-agent"]
        .to_str()
        .unwrap()
        .starts_with("PowerContextGateway/"));
    peer.github_tokens
        .lock()
        .unwrap()
        .push(headers["authorization"].to_str().unwrap().into());
    let reply = peer
        .replies
        .lock()
        .unwrap()
        .pop_front()
        .expect("unexpected Copilot session exchange");
    scripted_response(&reply)
}

async fn discovery(
    State(peer): State<Arc<Peer>>,
    headers: HeaderMap,
    axum::Json(body): axum::Json<Value>,
) -> axum::response::Response {
    assert_eq!(headers["authorization"], "Bearer google-access");
    assert!(!headers.contains_key("x-goog-api-key"));
    peer.device_forms.lock().unwrap().push(body);
    axum::Json(peer.device_reply.lock().unwrap().clone()).into_response()
}

fn scripted_response(reply: &Value) -> axum::response::Response {
    let body = reply
        .get("text")
        .and_then(Value::as_str)
        .map(String::from)
        .unwrap_or_else(|| serde_json::to_string(&reply["json"]).unwrap());
    let mut response = (
        axum::http::StatusCode::from_u16(reply["status"].as_u64().unwrap() as u16).unwrap(),
        [("content-type", "application/json")],
        body,
    )
        .into_response();
    if let Some(headers) = reply["headers"].as_object() {
        for (name, value) in headers {
            response.headers_mut().insert(
                axum::http::HeaderName::from_bytes(name.as_bytes()).unwrap(),
                value.as_str().unwrap().parse().unwrap(),
            );
        }
    }
    response
}

async fn resource(State(peer): State<Arc<Peer>>, headers: HeaderMap) -> axum::response::Response {
    peer.tokens.lock().unwrap().push(
        headers["authorization"]
            .to_str()
            .unwrap()
            .strip_prefix("Bearer ")
            .unwrap()
            .into(),
    );
    peer.accounts.lock().unwrap().push(
        headers
            .get("chatgpt-account-id")
            .map(|v| v.to_str().unwrap().into()),
    );
    scripted_response(&peer.resource_response)
}

struct Server {
    task: tokio::task::JoinHandle<()>,
    base: String,
    peer: Arc<Peer>,
}

impl Drop for Server {
    fn drop(&mut self) {
        self.task.abort();
    }
}

impl Server {
    async fn new(http: &Value) -> Self {
        let peer = Arc::new(Peer {
            replies: Mutex::new(http["token_responses"].as_array().unwrap().clone().into()),
            resource_response: http["resource_response"].clone(),
            ..Default::default()
        });
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let base = format!("http://{}", listener.local_addr().unwrap());
        let router = Router::new()
            .route("/token", post(token))
            .route("/device", post(device))
            .route("/copilot-token", get(copilot_token))
            .route("/load", post(discovery))
            .route("/resource", post(resource))
            .with_state(peer.clone());
        let task = tokio::spawn(async move { axum::serve(listener, router).await.unwrap() });
        Self { task, base, peer }
    }

    fn auth(&self, kind: Subscription, path: &Path) -> SubscriptionAuth {
        let mut auth = SubscriptionAuth::new(kind, path.into()).unwrap();
        // Tests replace only the fixed token peer. Original resource URLs still pass the
        // production destination validator before any file or HTTP operation.
        auth.token_url = format!("{}/token", self.base);
        auth.client = reqwest::Client::builder()
            .no_proxy()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        auth
    }
}

async fn request(auth: &SubscriptionAuth, server: &Server, url: &str, now: i64, action: &Value) {
    match auth.headers(url, now).await {
        Err(error) => {
            assert_eq!(
                action["outcome"], "auth_error",
                "unexpected auth failure: {error}"
            );
            assert!(std::error::Error::source(&error).is_none());
            let error = format!("{error:?}: {error}");
            assert!(!error.contains("synthetic-secret-error"));
            assert!(!error.contains(auth.path.to_str().unwrap()));
        }
        Ok(headers) => {
            assert_ne!(action["outcome"], "auth_error");
            let mut request = auth.client.post(format!("{}/resource", server.base));
            for (name, value) in headers {
                request = request.header(name, value);
            }
            let response = request.send().await.unwrap();
            if action["outcome"] == "http_error" {
                assert_eq!(
                    response.status().as_u16(),
                    action["status"].as_u64().unwrap() as u16
                );
            } else {
                assert_eq!(action["outcome"], "success");
                assert!(response.status().is_success());
            }
        }
    }
}

fn replace(path: &Path, value: &Value) -> Vec<u8> {
    let bytes = if value.is_null() {
        b"not json".to_vec()
    } else {
        serde_json::to_vec(value).unwrap()
    };
    let mut file = tempfile::NamedTempFile::new_in(path.parent().unwrap()).unwrap();
    file.write_all(&bytes).unwrap();
    file.persist(path).unwrap();
    bytes
}

fn contract() -> Value {
    read_contract("contract.json")
}

fn read_contract(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/oauth")
        .join(name);
    let contract: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    assert_eq!(
        contract["schema_version"], 1,
        "unsupported OAuth contract version"
    );
    contract
}

#[tokio::test]
async fn shared_oauth_contract() {
    for name in ["contract.json", "upstream/republic-grok.json"] {
        let contract = read_contract(name);
        for scenario in contract["scenarios"].as_array().unwrap() {
            tokio::time::timeout(Duration::from_secs(15), scenario_case(&contract, scenario))
                .await
                .unwrap_or_else(|_| panic!("OAuth case timed out: {}", scenario["id"]));
        }
    }
}

async fn scenario_case(contract: &Value, scenario: &Value) {
    let profile = &contract["profiles"][scenario["profile"].as_str().unwrap()];
    let kind = match scenario["profile"].as_str().unwrap() {
        "grok-cli" => Subscription::Grok,
        "codex-cli" => Subscription::Codex,
        _ => panic!("unsupported OAuth profile"),
    };
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("auth.json");
    let mut unchanged = replace(&path, &scenario["credentials"]);
    let mut original = scenario["credentials"].clone();
    let server = Server::new(&scenario["http"]).await;
    let auth = Arc::new(server.auth(kind, &path));
    let now = scenario["now"].as_i64().unwrap();
    let mut expected_accounts = Vec::new();
    for action in scenario["actions"].as_array().unwrap() {
        let url = action["url"]
            .as_str()
            .unwrap_or(profile["resource_url"].as_str().unwrap());
        match action["type"].as_str().unwrap() {
            "replace_credentials" => {
                unchanged = replace(&path, &action["credentials"]);
                original = action["credentials"].clone();
            }
            "request" => {
                let before = server.peer.tokens.lock().unwrap().len();
                request(&auth, &server, url, now, action).await;
                if server.peer.tokens.lock().unwrap().len() > before {
                    expected_accounts.push(
                        original
                            .pointer("/tokens/account_id")
                            .and_then(Value::as_str)
                            .map(String::from),
                    );
                }
            }
            "concurrent_requests" => {
                let count = action["count"].as_u64().unwrap();
                let auths: Vec<_> = (0..count).map(|_| server.auth(kind, &path)).collect();
                let futures: Vec<_> = auths
                    .iter()
                    .map(|auth| Box::pin(request(auth, &server, url, now, action)))
                    .collect();
                let mut futures = futures;
                std::future::poll_fn(|cx| {
                    futures.retain_mut(|future| future.as_mut().poll(cx).is_pending());
                    if futures.is_empty() {
                        std::task::Poll::Ready(())
                    } else {
                        std::task::Poll::Pending
                    }
                })
                .await;
                expected_accounts.extend((0..count).map(|_| None));
            }
            "cancel_during_refresh" => {
                assert_eq!(action["outcome"], "cancelled");
                let auth = auth.clone();
                let url = url.to_owned();
                let task = tokio::spawn(async move { auth.headers(&url, now).await });
                server.peer.token_started.notified().await;
                task.abort();
                assert!(task.await.unwrap_err().is_cancelled());
                server.peer.release_token.notify_one();
                let lock = tokio::time::timeout(Duration::from_secs(1), lock_store(&path))
                    .await
                    .unwrap()
                    .unwrap();
                drop(lock);
                // A late token response cannot resume a cancelled future and overwrite the file.
                tokio::time::sleep(Duration::from_millis(30)).await;
                assert_eq!(std::fs::read(&path).unwrap(), unchanged);
            }
            _ => panic!("unsupported OAuth action"),
        }
    }
    assert_eq!(
        *server.peer.accounts.lock().unwrap(),
        expected_accounts,
        "{}",
        scenario["id"]
    );
    let mut actual = server.peer.tokens.lock().unwrap().clone();
    let mut expected: Vec<String> =
        serde_json::from_value(scenario["expect"]["resource_tokens"].clone()).unwrap();
    if scenario["actions"]
        .as_array()
        .unwrap()
        .iter()
        .any(|a| a["type"] == "concurrent_requests")
    {
        actual.sort();
        expected.sort();
    }
    assert_eq!(actual, expected, "{}", scenario["id"]);
    assert_eq!(
        *server.peer.forms.lock().unwrap(),
        *scenario["expect"]["refresh_forms"].as_array().unwrap(),
        "{}",
        scenario["id"]
    );
    assert!(
        server.peer.replies.lock().unwrap().is_empty(),
        "{}",
        scenario["id"]
    );
    let store = &scenario["expect"]["store"];
    match store["mode"].as_str().unwrap() {
        "unchanged" => assert_eq!(
            std::fs::read(&path).unwrap(),
            unchanged,
            "{}",
            scenario["id"]
        ),
        "updated" => {
            let mut stored = read_store(&path).unwrap();
            let current = &stored[GROK_SCOPE];
            assert_eq!(current["key"], store["access_token"]);
            assert_eq!(current["refresh_token"], store["refresh_token"]);
            assert_eq!(
                expiry(current).unwrap(),
                store["expires_at"].as_i64().unwrap()
            );
            for key in ["key", "refresh_token", "expires_at"] {
                stored[GROK_SCOPE].as_object_mut().unwrap().remove(key);
                original[GROK_SCOPE].as_object_mut().unwrap().remove(key);
            }
            assert_eq!(stored, original);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                assert_eq!(
                    std::fs::metadata(&path).unwrap().permissions().mode() & 0o777,
                    0o600
                );
            }
        }
        _ => panic!("unsupported store expectation"),
    }
}

#[test]
fn oauth_process_worker() {
    let Some(path) = std::env::var_os("PC_TEST_OAUTH_STORE") else {
        return;
    };
    let endpoint = std::env::var("PC_TEST_OAUTH_ENDPOINT").unwrap();
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async {
        let mut auth = SubscriptionAuth::new(Subscription::Grok, path.into()).unwrap();
        auth.token_url = endpoint;
        auth.client = reqwest::Client::builder().no_proxy().build().unwrap();
        let headers = auth
            .headers("https://api.x.ai/v1/responses", 2_000_000_000)
            .await
            .unwrap();
        assert_eq!(headers[0].1, "Bearer synthetic-renewed");
    });
}

#[tokio::test]
async fn rotating_token_is_committed_once_across_processes() {
    let contract = contract();
    let scenario = contract["scenarios"]
        .as_array()
        .unwrap()
        .iter()
        .find(|s| s["id"] == "grok-cli-expired-rotate")
        .unwrap();
    let server = Server::new(&scenario["http"]).await;
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("auth.json");
    replace(&path, &scenario["credentials"]);
    let mut workers = Vec::new();
    for _ in 0..4 {
        workers.push(
            tokio::process::Command::new(std::env::current_exe().unwrap())
                .args(["--exact", "tests::oauth_process_worker", "--nocapture"])
                .env("PC_TEST_OAUTH_STORE", &path)
                .env("PC_TEST_OAUTH_ENDPOINT", format!("{}/token", server.base))
                .kill_on_drop(true)
                .spawn()
                .unwrap(),
        );
    }
    for worker in &mut workers {
        assert!(tokio::time::timeout(Duration::from_secs(10), worker.wait())
            .await
            .unwrap()
            .unwrap()
            .success());
    }
    assert_eq!(server.peer.forms.lock().unwrap().len(), 1);
    assert_eq!(
        read_store(&path).unwrap()[GROK_SCOPE]["refresh_token"],
        "synthetic-rotated"
    );
}

#[tokio::test]
async fn managed_device_login_then_refresh_and_readback() {
    for (kind, target, verification, client_id) in [
        (
            Subscription::Qwen,
            "https://api.qwen.ai/compatible-mode/v1/chat/completions",
            "https://chat.qwen.ai/authorize",
            "qwen-code-cli",
        ),
        (
            Subscription::Kimi,
            "https://api.kimi.com/coding/v1/chat/completions",
            "https://auth.kimi.com/device",
            "17e5f671-d194-4dfb-9706-5516cb48c098",
        ),
    ] {
        let server = Server::new(&json!({"token_responses":[
            {"status":200,"json":{"access_token":"login-access","refresh_token":"login-refresh","token_type":"Bearer","expires_in":3600}},
            {"status":200,"json":{"access_token":"renewed-access","refresh_token":"rotated-refresh","token_type":"Bearer","expires_in":3600}},
        ],"resource_response":{"status":200,"json":{"choices":[{"message":{"role":"assistant","content":"accepted"}}]}}})).await;
        *server.peer.device_reply.lock().unwrap() = json!({
            "device_code":"test-device", "user_code":"ABCD", "verification_uri":verification,
            "verification_uri_complete":format!("{verification}?user_code=ABCD"), "expires_in":600, "interval":1,
        });
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("gateway.json");
        let mut auth = server.auth(kind, &path);
        auth.device_url = format!("{}/device", server.base);
        tokio::time::timeout(
            Duration::from_secs(5),
            auth.login_device(|uri, code| {
                assert!(uri.starts_with(verification));
                assert_eq!(code, "ABCD");
            }),
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!(
            server.peer.device_forms.lock().unwrap()[0]["client_id"],
            client_id
        );
        if kind == Subscription::Qwen {
            assert_eq!(
                server.peer.device_forms.lock().unwrap()[0]["scope"],
                "model:invoke profile:read"
            );
        }
        let mut stored = read_store(&path).unwrap();
        assert_eq!(stored["access_token"], "login-access");
        let device_id = stored["device_id"].clone();
        assert!(uuid::Uuid::parse_str(device_id.as_str().unwrap()).is_ok());
        assert!(auth
            .login_device(|_, _| panic!("existing login must be preserved"))
            .await
            .is_err());
        request(&auth, &server, target, 0, &json!({"outcome":"success"})).await;
        stored["expires_at"] = json!(0);
        write_store(&path, &stored).unwrap();
        let other = server.auth(kind, &path);
        let (first, second) = tokio::join!(auth.headers(target, 100), other.headers(target, 100));
        for result in [first, second] {
            let headers = result.unwrap();
            assert!(headers
                .iter()
                .any(|(k, v)| k == "authorization" && v == "Bearer renewed-access"));
            if kind == Subscription::Kimi {
                assert!(headers
                    .iter()
                    .any(|(k, v)| k == "x-msh-platform" && v == "kimi_cli"));
                assert!(headers
                    .iter()
                    .any(|(k, v)| k == "x-msh-device-id" && v == device_id.as_str().unwrap()));
            }
        }
        let reopened = read_store(&path).unwrap();
        assert_eq!(reopened["refresh_token"], "rotated-refresh");
        assert_eq!(reopened["expires_at"], 3700);
        assert_eq!(reopened["device_id"], device_id);
        assert_eq!(
            server.peer.forms.lock().unwrap().as_slice(),
            &[
                json!({"grant_type":"urn:ietf:params:oauth:grant-type:device_code", "device_code":"test-device", "client_id":client_id}),
                json!({"grant_type":"refresh_token", "refresh_token":"login-refresh", "client_id":client_id}),
            ]
        );
        assert!(server.peer.replies.lock().unwrap().is_empty());
    }
}

#[tokio::test]
async fn copilot_session_exchange_cache_and_credential_reload() {
    let response = |token: &str| {
        json!({"status":200,"json":{"token":token,"expires_at":3600,
        "endpoints":{"api":"https://api.githubcopilot.com"}}})
    };
    let server = Server::new(
        &json!({"token_responses":[response("session-one"),response("session-two")],
        "resource_response":{"status":200,"json":{"choices":[]}}}),
    )
    .await;
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("github.json");
    let initial = replace(&path, &json!({"oauth_token":"github-one"}));
    let mut auth = server.auth(Subscription::Copilot, &path);
    auth.token_url = format!("{}/copilot-token", server.base);
    let url = "https://api.githubcopilot.com/chat/completions";
    let (first, second) = tokio::join!(auth.headers(url, 0), auth.headers(url, 0));
    for result in [first, second] {
        let headers = result.unwrap();
        assert!(headers
            .iter()
            .any(|(k, v)| k == "authorization" && v == "Bearer session-one"));
        assert!(headers
            .iter()
            .any(|(k, v)| k == "editor-version" && v == "vscode/1.140.0"));
    }
    assert_eq!(std::fs::read(&path).unwrap(), initial);
    replace(&path, &json!({"oauth_token":"github-two"}));
    let headers = auth.headers(url, 0).await.unwrap();
    assert!(headers
        .iter()
        .any(|(k, v)| k == "authorization" && v == "Bearer session-two"));
    assert_eq!(
        *server.peer.github_tokens.lock().unwrap(),
        ["token github-one", "token github-two"]
    );
}

#[tokio::test]
async fn copilot_refuses_untrusted_session_endpoint_before_inference() {
    let server = Server::new(&json!({"token_responses":[{"status":200,"json":{
        "token":"secret", "expires_at":3600,"endpoints":{"api":"https://api.githubcopilot.com.attacker.test"}
    }}],"resource_response":{"status":200,"json":{}}})).await;
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("github.json");
    replace(&path, &json!({"oauth_token":"github-one"}));
    let mut auth = server.auth(Subscription::Copilot, &path);
    auth.token_url = format!("{}/copilot-token", server.base);
    assert!(matches!(
        auth.headers("https://api.githubcopilot.com/chat/completions", 0)
            .await,
        Err(AuthError::Target)
    ));
    assert!(server.peer.tokens.lock().unwrap().is_empty());
}

#[tokio::test]
async fn gemini_cli_uses_one_read_only_login_for_project_discovery_and_inference() {
    let server =
        Server::new(&json!({"token_responses":[],"resource_response":{"status":200,"json":{}}}))
            .await;
    *server.peer.device_reply.lock().unwrap() =
        json!({"currentTier":{"id":"standard-tier"},"cloudaicompanionProject":"project-123"});
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("oauth_creds.json");
    let mut credentials = json!({"access_token":"google-access","refresh_token":"never-exchange-cli-refresh", "expiry_date":2_000_003_600_000i64});
    let original = replace(&path, &credentials);
    let mut auth = server.auth(Subscription::Gemini, &path);
    auth.codeassist_url = format!("{}/load", server.base);
    auth.codeassist_project = None;
    let target = "https://cloudcode-pa.googleapis.com/v1internal:generateContent";
    let (headers, project) = auth.gemini_context(target, 2_000_000_000).await.unwrap();
    assert_eq!(project, "project-123");
    assert_eq!(headers[0].1, "Bearer google-access");
    assert_eq!(std::fs::read(&path).unwrap(), original);
    assert_eq!(server.peer.device_forms.lock().unwrap().len(), 1);
    credentials["expiry_date"] = json!(0);
    let expired = replace(&path, &credentials);
    assert!(auth.gemini_context(target, 2_000_000_000).await.is_err());
    assert_eq!(server.peer.device_forms.lock().unwrap().len(), 1);
    assert_eq!(std::fs::read(&path).unwrap(), expired);
    assert!(server.peer.forms.lock().unwrap().is_empty());
}

#[tokio::test]
async fn shared_upstream_qwen_and_kimi_rotation_vectors() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../../tests/fixtures/oauth/upstream/provider-vectors.json");
    let vectors: Value = serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
    for (key, kind, target) in [
        (
            "qwen",
            Subscription::Qwen,
            "https://api.qwen.ai/compatible-mode/v1/chat/completions",
        ),
        (
            "kimi",
            Subscription::Kimi,
            "https://api.kimi.com/coding/v1/chat/completions",
        ),
    ] {
        let vector = &vectors[key];
        let mut replies: Vec<Value> = vector["responses"]
            .as_array()
            .unwrap()
            .iter()
            .map(|r| json!({"status":200,"json":r}))
            .collect();
        replies.push(json!({"status":200,"json":vector["malformed_token"]}));
        let server = Server::new(
            &json!({"token_responses":replies,"resource_response":{"status":200,"json":{}}}),
        )
        .await;
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("owned.json");
        replace(
            &path,
            &json!({"schema":"powercontext.gateway.oauth.v1","subscription":kind,"access_token":"expired",
            "refresh_token":vector["initial_refresh_token"],"token_type":"Bearer","expires_at":0,"device_id":"own-device"}),
        );
        let auth = server.auth(kind, &path);
        for response in vector["responses"].as_array().unwrap() {
            let mut stored = read_store(&path).unwrap();
            stored["expires_at"] = json!(0);
            write_store(&path, &stored).unwrap();
            let headers = auth.headers(target, 100).await.unwrap();
            assert!(headers.iter().any(|(name, value)| name == "authorization"
                && value == &format!("Bearer {}", response["access_token"].as_str().unwrap())));
        }
        let mut stored = read_store(&path).unwrap();
        assert_eq!(stored["refresh_token"], vector["expected_refresh_token"]);
        assert_eq!(
            *server.peer.forms.lock().unwrap(),
            *vector["expected_forms"].as_array().unwrap()
        );
        stored["expires_at"] = json!(0);
        write_store(&path, &stored).unwrap();
        let previous = std::fs::read(&path).unwrap();
        assert!(auth.headers(target, 100).await.is_err());
        assert_eq!(std::fs::read(&path).unwrap(), previous);
        if key == "qwen" {
            *server.peer.device_reply.lock().unwrap() = vector["malformed_device"].clone();
            let mut auth = server.auth(kind, &directory.path().join("new-login.json"));
            auth.device_url = format!("{}/device", server.base);
            assert!(auth
                .login_device(|_, _| panic!("malformed device response was presented"))
                .await
                .is_err());
            assert_eq!(
                server.peer.forms.lock().unwrap().len(),
                vector["expected_forms"].as_array().unwrap().len() + 1
            );
        }
    }
}
