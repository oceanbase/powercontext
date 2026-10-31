/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

use super::*;
use std::{
    fs,
    path::{Path, PathBuf},
    sync::mpsc,
};

fn fixture_dir() -> tempfile::TempDir {
    tempfile::Builder::new()
        .prefix("ops-bridge-")
        .tempdir_in("/private/tmp")
        .unwrap()
}
#[cfg(unix)]
fn executable(path: &Path, bytes: &[u8]) {
    use std::os::unix::fs::PermissionsExt;
    fs::write(path, bytes).unwrap();
    fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
}
fn private(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        fs::set_permissions(
            path,
            fs::Permissions::from_mode(if path.is_dir() { 0o700 } else { 0o600 }),
        )
        .unwrap();
    }
}
struct Fixture {
    _dir: tempfile::TempDir,
    resources: PathBuf,
    local: PathBuf,
    home: PathBuf,
    support: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let dir = fixture_dir();
        let root = dir.path();
        let resources = root.join("resources");
        let local = root.join("local");
        let home = root.join("home");
        let support = local.join("PowerContext/Ops");
        for d in [&resources, &home, &support.join("bundle/runtime")] {
            fs::create_dir_all(d).unwrap();
        }
        private(&support);
        executable(&support.join("bundle/runtime/helper"), HELPER.as_bytes());
        fs::write(support.join("bundle/entry.py"), b"# synthetic test entry\n").unwrap();
        let files = ["runtime/helper", "entry.py"]
            .map(|p| {
                (
                    p.to_owned(),
                    trust::sha256(&fs::read(support.join("bundle").join(p)).unwrap()),
                )
            })
            .into_iter()
            .collect::<std::collections::BTreeMap<_, _>>();
        let size = files
            .keys()
            .map(|p| fs::metadata(support.join("bundle").join(p)).unwrap().len())
            .sum::<u64>();
        let manifest = json!({"format_version":1,"python_version":"3.12.15","platform":if cfg!(windows){"win32"}else{"darwin"},"architecture":std::env::consts::ARCH,"python_relative":"runtime/helper","arguments":["-I","-S","-B","entry.py"],"requirements_sha256":"a".repeat(64),"installed_bytes":size,"files_sha256":files});
        let bytes = serde_json::to_vec(&manifest).unwrap();
        fs::write(support.join("bundle/bundle.json"), &bytes).unwrap();
        fs::write(resources.join("ops-provision.json"),serde_json::to_vec(&json!({"format_version":1,"bundle_manifest_sha256":trust::sha256(&bytes),"binding_mode":"target_file"})).unwrap()).unwrap();
        fs::write(support.join("target.json"),serde_json::to_vec(&json!({"format_version":1,"target_id":"test-server","python_executable":home.join("missing-server-python"),"package_version":"1.0.0","env_file":home.join("missing.env"),"configuration_owned":true})).unwrap()).unwrap();
        private(&support.join("target.json"));
        Self {
            _dir: dir,
            resources,
            local,
            home,
            support,
        }
    }
    fn host(&self) -> OpsHost {
        OpsHost::new(
            Some(self.resources.clone()),
            Some(self.local.clone()),
            Some(self.home.clone()),
        )
    }
    fn approve(&self, host: &OpsHost) -> OpsApprovalInput {
        let reply = host
            .preflight(OpsPreflightInput {
                operation: OpsOperation::RemoveRegistration,
                options: OpsOptions::default(),
                resume: None,
            })
            .unwrap();
        let a = reply.approval.expect("validated preflight");
        OpsApprovalInput {
            approval_id: a.approval_id,
            plan_id: a.plan_id,
        }
    }
    fn behavior(&self, value: &str) {
        fs::write(self.support.join("behavior"), value).unwrap();
    }
}
const HELPER: &str = r#"#!/usr/bin/python3
import sys,json,hashlib,time,pathlib,os
root=pathlib.Path(__file__).resolve().parents[2]
request=json.loads(sys.stdin.readline())
def emit(**fields):
 print(json.dumps(dict(protocol_version=1,request_id=request['request_id'],type='result',**fields),sort_keys=True,separators=(',',':')),flush=True)
def digest(v): return hashlib.sha256(json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
components=dict(support='supported',registration='not_installed',definition='unknown',manager_ownership='not_loaded',manager='inactive',liveness='unreachable',readiness='not_probed',endpoint=None,maintenance=None,resources='missing_executable',configuration='missing_or_invalid')
action=request['action']
behavior=(root/'behavior').read_text() if (root/'behavior').exists() else ''
if action=='inspect':
 emit(outcome='succeeded',components=components)
elif action=='config_validate':
 emit(outcome='rejected',error=dict(code='configuration_invalid'));sys.exit(1)
elif action=='preflight':
 target=json.loads(pathlib.Path(sys.argv[sys.argv.index('--target-file')+1]).read_text());target.pop('format_version')
 p=dict(protocol_version=1,target_id=target['target_id'],binding_id=digest(target),operation=request['operation'],options=request['options'],before='b'*64,effects=dict(may_start=False,preserves_data=True),target_summary={k:target[k] for k in ('python_executable','package_version','env_file')})
 p['target_summary'].update(endpoint=None,data_dir=None,start_on_login=True);p['plan_id']=digest(p);emit(outcome='succeeded',plan=p)
else:
 op=request['operation_id'];file=root/(op+'.test-result.json');cancel=root/(op+'.test-cancel')
 def save(value):
  temporary=file.with_suffix('.tmp');temporary.write_text(json.dumps(value));os.replace(temporary,file)
 def status(result=None,attempt=1):
  return dict(operation_id=op,attempt=attempt,phase='finished' if result else 'executing',executor=dict(pid=os.getpid(),observation='not_confirmed_exited'),execution_state='finished' if result else 'unknown',outcome=result['outcome'] if result else 'pending',cancel_requested=cancel.exists(),result=result,observation=components,history=[dict(attempt=n,outcome=None if n<attempt else (result['outcome'] if result else None)) for n in range(1,attempt+1)],recovery_actions=[] if result else ['inspect','preflight'])
 if action in ('apply','resume'):
  n=request.get('expected_attempt',0)+1;save(status(attempt=n))
  if behavior=='broken_progress':
   reader,writer=os.pipe();os.close(reader);os.dup2(writer,1);os.close(writer)
  try:
   print(json.dumps(dict(protocol_version=1,request_id=request['request_id'],type='accepted',operation_id=op,attempt=n)),flush=True)
   print(json.dumps(dict(protocol_version=1,request_id=request['request_id'],type='progress',operation_id=op,attempt=n,sequence=1,phase='service')),flush=True)
  except BrokenPipeError:
   result=dict(outcome='failed',error=dict(code='progress_unavailable'))
  else:
   if behavior in ('await_cancel','await_release'):
    signal=cancel if behavior=='await_cancel' else root/(op+'.test-release')
    deadline=time.monotonic()+10
    while not signal.exists() and time.monotonic()<deadline:time.sleep(0.01)
    assert signal.exists(), 'test synchronization timed out'
   else:time.sleep(0.4 if behavior!='slow' else 0.9)
   result=dict(outcome='cancelled' if cancel.exists() else 'succeeded',components=components)
  final=status(result,n);save(final);emit(**final)
 elif not file.exists():
  emit(outcome='rejected',error=dict(code='operation_missing'));sys.exit(1)
 else:
  if action=='cancel':cancel.write_text('requested')
  emit(**json.loads(file.read_text()))
"#;

#[test]
#[cfg(target_os = "macos")]
fn independent_missing_target_inspection_and_unhealthy_result_are_preserved() {
    let f = Fixture::new();
    let host = f.host();
    let reply = host.inspect();
    assert_eq!(reply.outcome, OpsOutcome::Succeeded);
    assert_eq!(
        reply.components.unwrap().resources,
        Some(OpsRuntimeResources::MissingExecutable)
    );
    let reply = host.config_validate();
    assert_eq!(reply.outcome, OpsOutcome::Rejected);
    assert_eq!(reply.error, Some(OpsErrorCode::ConfigurationInvalid));
    assert_eq!(reply.exit_code, Some(1));
}

#[test]
fn absent_target_provisioning_is_unavailable_and_invalid_storage_stays_distinct() {
    let f = Fixture::new();
    fs::remove_file(f.support.join("target.json")).unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::Unavailable)
    );
    fs::remove_dir_all(&f.support).unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::Unavailable)
    );
    assert!(f.host().references().unwrap().operations.is_empty());
    fs::write(&f.support, b"not a directory").unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::Storage)
    );
}

#[test]
fn non_utf8_native_path_returns_unavailable_without_panicking() {
    use std::{ffi::OsString, os::unix::ffi::OsStringExt};
    let mut f = Fixture::new();
    f.home.push(OsString::from_vec(vec![0xff]));
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::Unavailable)
    );
}
#[test]
#[cfg(target_os = "macos")]
fn approvals_are_one_shot_expiring_and_bound_to_exact_plan_and_target() {
    let f = Fixture::new();
    let host = f.host();
    let mut a = f.approve(&host);
    let original = a.clone();
    a.plan_id = "c".repeat(64);
    assert_eq!(
        host.apply(a, |_| {}).unwrap_err(),
        OpsBridgeError::StaleApproval
    );
    assert_eq!(
        host.apply(original, |_| {}).unwrap_err(),
        OpsBridgeError::StaleApproval
    );
    let a = f.approve(&host);
    host.0
        .approvals
        .lock()
        .unwrap()
        .get_mut(&a.approval_id)
        .unwrap()
        .created = Instant::now() - APPROVAL_TTL;
    assert_eq!(
        host.apply(a, |_| {}).unwrap_err(),
        OpsBridgeError::StaleApproval
    );
    let a = f.approve(&host);
    let mut target: Value =
        serde_json::from_slice(&fs::read(f.support.join("target.json")).unwrap()).unwrap();
    target["package_version"] = "2.0.0".into();
    fs::write(
        f.support.join("target.json"),
        serde_json::to_vec(&target).unwrap(),
    )
    .unwrap();
    assert_eq!(
        host.apply(a, |_| {}).unwrap_err(),
        OpsBridgeError::StaleApproval
    );
    assert!(host.references().unwrap().operations.is_empty());
}
#[test]
#[cfg(target_os = "macos")]
fn progress_arrives_during_apply_and_status_cancel_use_independent_children() {
    let f = Fixture::new();
    f.behavior("await_cancel");
    let host = f.host();
    let approval = f.approve(&host);
    let (send, recv) = mpsc::channel();
    let worker = host.clone();
    let thread = std::thread::spawn(move || {
        worker
            .apply(approval, move |v| {
                send.send(v).unwrap();
            })
            .unwrap()
    });
    let event = recv.recv_timeout(Duration::from_secs(3)).unwrap();
    assert!(event.accepted);
    assert!(!thread.is_finished());
    let status = host
        .operation_status(OpsStatusInput {
            operation_id: event.operation_id.clone(),
        })
        .unwrap();
    assert_eq!(status.outcome, OpsOutcome::Pending);
    host.cancel(OpsCancelInput {
        operation_id: event.operation_id.clone(),
        attempt: event.attempt,
    })
    .unwrap();
    assert_eq!(thread.join().unwrap().outcome, OpsOutcome::Cancelled);
    let reopened = f.host();
    assert_eq!(
        reopened.references().unwrap().operations[0].operation_id,
        event.operation_id
    );
    assert_eq!(
        reopened
            .operation_status(OpsStatusInput {
                operation_id: event.operation_id
            })
            .unwrap()
            .outcome,
        OpsOutcome::Cancelled
    );
}
#[test]
#[cfg(target_os = "macos")]
fn lost_response_keeps_reference_and_helper_finishes_without_replay() {
    let f = Fixture::new();
    let host = f.host();
    let approval = f.approve(&host);
    f.behavior("slow");
    let mut short = host;
    Arc::get_mut(&mut short.0).unwrap().deadline = Some(Duration::from_millis(200));
    let reply = short.apply(approval, |_| {}).unwrap();
    assert_eq!(reply.outcome, OpsOutcome::Unknown);
    assert_eq!(reply.bridge_error, Some(OpsBridgeError::Timeout));
    let reopened = f.host();
    assert_eq!(
        reopened.references().unwrap().operations[0].operation_id,
        reply.operation_id
    );
    let status = await_terminal_status(&reopened, &reply.operation_id);
    // A slow interpreter can lose the pipe before progress is delivered. Like
    // real Ops, that is a durable pre-mutation failure, not perpetual pending.
    match status.outcome {
        OpsOutcome::Succeeded => {}
        OpsOutcome::Failed => assert_eq!(
            status.status.unwrap().result.unwrap().error,
            Some(OpsErrorCode::ProgressUnavailable)
        ),
        other => panic!("unexpected terminal outcome: {other:?}"),
    }
    assert_eq!(status.attempt, Some(1));
}

#[test]
fn broken_progress_pipe_records_failure_and_can_be_queried_without_replay() {
    let f = Fixture::new();
    f.behavior("broken_progress");
    let host = f.host();
    let reply = host.apply(f.approve(&host), |_| {}).unwrap();
    assert_eq!(reply.outcome, OpsOutcome::Unknown);
    let reopened = f.host();
    let references = reopened.references().unwrap();
    assert_eq!(references.operations.len(), 1);
    assert_eq!(references.operations[0].operation_id, reply.operation_id);
    let status = await_terminal_status(&reopened, &reply.operation_id);
    assert_eq!(status.outcome, OpsOutcome::Failed);
    assert_eq!(status.attempt, Some(1));
    assert_eq!(
        status.status.unwrap().result.unwrap().error,
        Some(OpsErrorCode::ProgressUnavailable)
    );
}

fn await_terminal_status(host: &OpsHost, operation_id: &str) -> OpsOperationReply {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let status = host
            .operation_status(OpsStatusInput {
                operation_id: operation_id.into(),
            })
            .unwrap();
        if status.outcome != OpsOutcome::Pending
            && status.error != Some(OpsErrorCode::OperationMissing)
        {
            return status;
        }
        assert!(
            Instant::now() < deadline,
            "helper did not settle: {status:?}"
        );
        std::thread::sleep(Duration::from_millis(25));
    }
}
#[test]
#[cfg(target_os = "macos")]
fn malformed_reference_store_fails_before_dispatch_and_foreign_binding_cannot_resume() {
    let f = Fixture::new();
    let host = f.host();
    let a = f.approve(&host);
    let path = f.support.join("bridge-operations.json");
    fs::write(&path, b"broken").unwrap();
    private(&path);
    assert!(host.apply(a, |_| {}).is_err());
    fs::remove_file(path).unwrap();
    let a = f.approve(&host);
    let reply = host.apply(a, |_| {}).unwrap();
    let mut target: Value =
        serde_json::from_slice(&fs::read(f.support.join("target.json")).unwrap()).unwrap();
    target["target_id"] = "other".into();
    fs::write(
        f.support.join("target.json"),
        serde_json::to_vec(&target).unwrap(),
    )
    .unwrap();
    assert_eq!(
        host.references().unwrap().operations[0].binding_status,
        OpsReferenceBindingStatus::Different
    );
    assert_eq!(
        host.operation_status(OpsStatusInput {
            operation_id: reply.operation_id
        })
        .unwrap_err(),
        OpsBridgeError::ForeignBinding
    );
}
#[test]
#[cfg(target_os = "macos")]
fn bundle_and_packaged_pins_reject_tampering_and_extra_files() {
    let f = Fixture::new();
    fs::write(f.support.join("bundle/entry.py"), b"tampered").unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::IntegrityFailure)
    );
    let f = Fixture::new();
    fs::write(f.support.join("bundle/packages-extra.py"), b"extra").unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::IntegrityFailure)
    );
    let f = Fixture::new();
    fs::write(f.resources.join("ops-provision.json"), b"{}").unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::IntegrityFailure)
    );
}
fn child(
    script: &str,
    deadline: Duration,
    request: Value,
    emit: impl Fn(OpsProgress) + Send + 'static,
) -> Result<(Value, i32), OpsBridgeError> {
    let dir = fixture_dir();
    let file = dir.path().join("child.py");
    fs::write(&file, script).unwrap();
    let host = OpsHost::new(None, None, None);
    let action = if request["action"] == "apply" {
        OpsAction::Apply
    } else {
        OpsAction::Inspect
    };
    let guard = host.enter(action).unwrap();
    process::run(
        trust::Launch {
            executable: PathBuf::from("/usr/bin/python3"),
            args: vec![
                "-I".into(),
                "-S".into(),
                "-B".into(),
                file.to_str().unwrap().into(),
            ],
            env: Default::default(),
            cwd: dir.path().into(),
        },
        request,
        deadline,
        guard,
        emit,
    )
}
#[test]
#[cfg(target_os = "macos")]
fn real_children_bound_streams_deadlines_and_inherited_pipe_descendants() {
    for stream in ["stdout", "stderr"] {
        let script = format!(
            "import sys,time\nsys.{stream}.write('x'*1100000)\nsys.{stream}.flush()\ntime.sleep(1)\n"
        );
        assert_eq!(
            child(
                &script,
                Duration::from_secs(2),
                request(OpsAction::Inspect, json!({})),
                |_| {}
            )
            .unwrap_err(),
            OpsBridgeError::OutputTooLarge
        );
    }
    let started = Instant::now();
    assert_eq!(
        child(
            "import time\ntime.sleep(1)",
            Duration::from_millis(50),
            request(OpsAction::Inspect, json!({})),
            |_| {}
        )
        .unwrap_err(),
        OpsBridgeError::Timeout
    );
    assert!(started.elapsed() < Duration::from_secs(1));
    let started = Instant::now();
    let result=child("import os,time,json,sys\nr=json.loads(sys.stdin.readline())\nif os.fork()==0:\n time.sleep(2);os._exit(0)\nprint(json.dumps(dict(protocol_version=1,request_id=r['request_id'],type='result',outcome='rejected',error=dict(code='configuration_invalid'))),flush=True)\nsys.exit(1)\n",Duration::from_secs(4),request(OpsAction::Inspect,json!({})),|_|{}).unwrap();
    assert_eq!(result.1, 1);
    assert!(started.elapsed() < Duration::from_secs(1));
}
#[test]
fn stream_rejects_wrong_identity_duplicate_keys_unknown_enums_and_sequence() {
    let request = request(OpsAction::Apply, json!({"operation_id":"op"}));
    let id = request["request_id"].as_str().unwrap();
    let accepted = json!({"protocol_version":1,"request_id":id,"type":"accepted","operation_id":"op","attempt":1});
    let progress = json!({"protocol_version":1,"request_id":id,"type":"progress","operation_id":"op","attempt":1,"sequence":1,"phase":"service"});
    let mut s = wire::Stream::new(&request).unwrap();
    assert!(s.line(&serde_json::to_vec(&progress).unwrap()).is_err());
    let mut s = wire::Stream::new(&request).unwrap();
    assert!(
        s.line(&serde_json::to_vec(&accepted).unwrap())
            .unwrap()
            .is_some()
    );
    assert!(
        s.line(&serde_json::to_vec(&progress).unwrap())
            .unwrap()
            .is_some()
    );
    assert!(s.line(&serde_json::to_vec(&progress).unwrap()).is_err());
    for changed in [
        json!({"request_id":"foreign"}),
        json!({"protocol_version":2}),
        json!({"operation_id":"foreign"}),
        json!({"attempt":2}),
    ] {
        let mut v = accepted.clone();
        for (k, x) in changed.as_object().unwrap() {
            v[k] = x.clone();
        }
        assert!(
            wire::Stream::new(&request)
                .unwrap()
                .line(&serde_json::to_vec(&v).unwrap())
                .is_err()
        );
    }
    let duplicate = format!(
        "{{\"protocol_version\":1,\"protocol_version\":1,\"request_id\":\"{id}\",\"type\":\"accepted\",\"operation_id\":\"op\",\"attempt\":1}}"
    );
    assert!(
        wire::Stream::new(&request)
            .unwrap()
            .line(duplicate.as_bytes())
            .is_err()
    );
    let r = request_from_inspect();
    let invalid = json!({"protocol_version":1,"request_id":r["request_id"],"type":"result","outcome":"rejected","error":{"code":"secret_provider_exception"}});
    assert!(
        wire::Stream::new(&r)
            .unwrap()
            .line(&serde_json::to_vec(&invalid).unwrap())
            .is_err()
    );
}
fn request_from_inspect() -> Value {
    request(OpsAction::Inspect, json!({}))
}
#[test]
fn renderer_inputs_cannot_supply_paths_commands_or_plan_bodies() {
    for v in [
        json!({"operation":"stop","options":{},"executable":"/bin/sh"}),
        json!({"operation":"shell","options":{}}),
        json!({"operation":"stop","options":{"url":"https://example.invalid"}}),
    ] {
        assert!(serde_json::from_value::<OpsPreflightInput>(v).is_err());
    }
    assert!(
        serde_json::from_value::<OpsApprovalInput>(
            json!({"approvalId":"abc","planId":"a".repeat(64),"plan":{}})
        )
        .is_err()
    );
    assert!(
        OpsOptions {
            http_port: Some(1234),
            ..Default::default()
        }
        .validate(OpsOperation::Register)
        .is_err()
    );
}
#[test]
fn control_admission_remains_available_when_ordinary_helpers_are_reserved() {
    let h = OpsHost::new(None, None, None);
    let guards = (0..4)
        .map(|_| h.enter(OpsAction::Apply).unwrap())
        .collect::<Vec<_>>();
    assert!(matches!(
        h.enter(OpsAction::Inspect),
        Err(OpsBridgeError::Busy)
    ));
    let status = h.enter(OpsAction::OperationStatus).unwrap();
    let cancel = h.enter(OpsAction::Cancel).unwrap();
    drop((guards, status, cancel));
    h.shutdown();
    assert!(matches!(
        h.enter(OpsAction::Inspect),
        Err(OpsBridgeError::ShuttingDown)
    ));
}

#[test]
#[ignore = "requires a prepared local Python environment with Ops dependencies; read-only real subprocess"]
#[cfg(target_os = "macos")]
fn real_current_ops_inspect_and_preflight_with_missing_server_runtime() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(2)
        .unwrap();
    let python = PathBuf::from(
        std::env::var_os("POWERCONTEXT_OPS_TEST_PYTHON").expect("set explicit prepared Python"),
    );
    let probe = std::process::Command::new(&python)
        .args([
            "-c",
            "import sysconfig;print(sysconfig.get_path('purelib'))",
        ])
        .output()
        .unwrap();
    assert!(probe.status.success());
    let site = String::from_utf8(probe.stdout).unwrap().trim().to_owned();
    let dir = fixture_dir();
    let home = dir.path().join("home");
    fs::create_dir(&home).unwrap();
    private(&home);
    let target = dir.path().join("target.json");
    fs::write(&target,serde_json::to_vec(&json!({"format_version":1,"target_id":"disposable-native-bridge-test","python_executable":home.join("missing-python"),"package_version":"1.0.0","env_file":home.join("missing.env"),"configuration_owned":true})).unwrap()).unwrap();
    private(&target);
    let entry = dir.path().join("entry.py");
    fs::write(&entry,format!("import sys,runpy\nsys.path[:0]=[{},{}]\nrunpy.run_module('powercontext.ops',run_name='__main__')\n",serde_json::to_string(&root.join("src").to_str().unwrap()).unwrap(),serde_json::to_string(&site).unwrap())).unwrap();
    for action in [OpsAction::Inspect, OpsAction::Preflight] {
        let h = OpsHost::new(None, None, None);
        let guard = h.enter(action).unwrap();
        let fields = if action == OpsAction::Preflight {
            json!({"operation":"remove_registration","options":{}})
        } else {
            json!({})
        };
        let (v, code) = process::run(
            trust::Launch {
                executable: python.clone(),
                args: vec![
                    "-I".into(),
                    "-S".into(),
                    "-B".into(),
                    entry.to_str().unwrap().into(),
                    "--target-file".into(),
                    target.to_str().unwrap().into(),
                    "--state-dir".into(),
                    dir.path().join("state").to_str().unwrap().into(),
                ],
                env: std::collections::BTreeMap::from([
                    ("HOME".into(), home.to_str().unwrap().into()),
                    ("PATH".into(), "/usr/bin:/bin:/usr/sbin:/sbin".into()),
                ]),
                cwd: dir.path().into(),
            },
            request(action, fields),
            Duration::from_secs(30),
            guard,
            |_| {},
        )
        .unwrap();
        if action == OpsAction::Preflight && v["outcome"] == "rejected" {
            // A headless host can observe launchd ownership as unknown. The real
            // runtime must retain its refusal rather than claim a supported removal.
            assert_eq!(code, 1);
            assert_eq!(v["error"]["code"], "ownership_conflict");
            continue;
        }
        assert_eq!(code, 0, "{v}");
        assert_eq!(v["outcome"], "succeeded");
        if action == OpsAction::Inspect {
            assert_eq!(v["components"]["resources"], "missing_executable");
        } else {
            assert_eq!(v["plan"]["operation"], "remove_registration");
            assert_eq!(v["plan"]["effects"]["may_start"], false);
        }
    }
    assert!(!dir.path().join("state").exists());
    assert!(!home.join("missing-python").exists());
}

#[test]
fn fresh_resume_approval_retains_operation_identity_and_advances_attempt() {
    let f = Fixture::new();
    f.behavior("await_cancel");
    let h = f.host();
    let a = f.approve(&h);
    let (send, recv) = mpsc::channel();
    let worker = h.clone();
    let thread = std::thread::spawn(move || {
        worker
            .apply(a, move |e| {
                let _ = send.send(e);
            })
            .unwrap()
    });
    let e = recv.recv_timeout(Duration::from_secs(3)).unwrap();
    h.cancel(OpsCancelInput {
        operation_id: e.operation_id.clone(),
        attempt: 1,
    })
    .unwrap();
    assert_eq!(thread.join().unwrap().outcome, OpsOutcome::Cancelled);
    fs::remove_file(f.support.join(format!("{}.test-cancel", e.operation_id))).unwrap();
    f.behavior("");
    let approval = h
        .preflight(OpsPreflightInput {
            operation: OpsOperation::RemoveRegistration,
            options: OpsOptions::default(),
            resume: Some(OpsResumeReference {
                operation_id: e.operation_id.clone(),
                expected_attempt: 1,
            }),
        })
        .unwrap()
        .approval
        .unwrap();
    let result = h
        .resume(
            OpsApprovalInput {
                approval_id: approval.approval_id,
                plan_id: approval.plan_id,
            },
            |_| {},
        )
        .unwrap();
    assert_eq!(result.operation_id, e.operation_id);
    assert_eq!(result.attempt, Some(2));
    assert_eq!(result.outcome, OpsOutcome::Succeeded);
    assert_eq!(h.references().unwrap().operations.len(), 1);
}

#[test]
fn quit_closes_admission_and_preserves_accepted_helper_outcome() {
    let f = Fixture::new();
    f.behavior("await_release");
    let h = f.host();
    let a = f.approve(&h);
    let (send, recv) = mpsc::channel();
    let worker = h.clone();
    let thread = std::thread::spawn(move || {
        worker
            .apply(a, move |e| {
                let _ = send.send(e);
            })
            .unwrap()
    });
    let event = recv.recv_timeout(Duration::from_secs(3)).unwrap();
    h.shutdown();
    let result = thread.join().unwrap();
    assert_eq!(result.outcome, OpsOutcome::Unknown);
    assert_eq!(result.bridge_error, Some(OpsBridgeError::ShuttingDown));
    assert_eq!(h.inspect().bridge_error, Some(OpsBridgeError::ShuttingDown));
    fs::write(
        f.support
            .join(format!("{}.test-release", event.operation_id)),
        b"continue",
    )
    .unwrap();
    let status = await_terminal_status(&f.host(), &event.operation_id);
    assert_eq!(status.outcome, OpsOutcome::Succeeded);
}

#[test]
fn original_builder_runtime_file_aliases_are_supported_but_external_links_are_rejected() {
    use std::os::unix::fs::symlink;
    let f = Fixture::new();
    let root = f.support.join("bundle");
    symlink("helper", root.join("runtime/alias")).unwrap();
    let mut manifest: Value =
        serde_json::from_slice(&fs::read(root.join("bundle.json")).unwrap()).unwrap();
    manifest["python_relative"] = "runtime/alias".into();
    manifest["files_sha256"]["runtime/alias"] = manifest["files_sha256"]["runtime/helper"].clone();
    manifest["installed_bytes"] = (manifest["installed_bytes"].as_u64().unwrap()
        + fs::metadata(root.join("runtime/helper")).unwrap().len())
    .into();
    let bytes = serde_json::to_vec(&manifest).unwrap();
    fs::write(root.join("bundle.json"), &bytes).unwrap();
    fs::write(f.resources.join("ops-provision.json"),serde_json::to_vec(&json!({"format_version":1,"binding_mode":"target_file","bundle_manifest_sha256":trust::sha256(&bytes)})).unwrap()).unwrap();
    assert_eq!(f.host().inspect().outcome, OpsOutcome::Succeeded);
    fs::copy(root.join("runtime/helper"), f.support.join("outside")).unwrap();
    fs::remove_file(root.join("runtime/alias")).unwrap();
    symlink("../../outside", root.join("runtime/alias")).unwrap();
    assert_eq!(
        f.host().inspect().bridge_error,
        Some(OpsBridgeError::IntegrityFailure)
    );
}

#[test]
fn managed_catalog_copy_is_private_immutable_and_pinned_across_launches() {
    let f = Fixture::new();
    fs::create_dir(f.resources.join("server")).unwrap();
    let bytes = b"{\"synthetic_catalog\":1}";
    let digest = trust::sha256(bytes);
    fs::write(f.resources.join("server/catalog.json"), bytes).unwrap();
    let mut provision: Value =
        serde_json::from_slice(&fs::read(f.resources.join("ops-provision.json")).unwrap()).unwrap();
    provision["binding_mode"] = "managed_catalog".into();
    provision["catalog_sha256"] = digest.clone().into();
    fs::write(
        f.resources.join("ops-provision.json"),
        serde_json::to_vec(&provision).unwrap(),
    )
    .unwrap();
    let h = f.host();
    let binding = h.0.trust.binding().unwrap();
    let launch = h.0.trust.launch(&binding).unwrap();
    let copy = PathBuf::from(&launch.args[5]);
    assert_eq!(copy, f.support.join(format!("catalog-{digest}.json")));
    assert_eq!(fs::read(&copy).unwrap(), bytes);
    fs::write(&copy, b"changed").unwrap();
    assert!(matches!(
        h.0.trust.launch(&binding),
        Err(OpsBridgeError::IntegrityFailure)
    ));
    fs::write(f.resources.join("server/catalog.json"), b"changed").unwrap();
    assert!(matches!(
        h.0.trust.binding(),
        Err(OpsBridgeError::IntegrityFailure)
    ));
}

#[test]
fn database_admission_v1_extensions_remain_bounded_and_actionable() {
    let r = request(OpsAction::Inspect, json!({}));
    let v = json!({"protocol_version":1,"request_id":r["request_id"],"type":"result","outcome":"succeeded","components":{"support":"supported","registration":"not_installed","definition":"unknown","manager_ownership":"not_loaded","manager":"inactive","liveness":"unknown","readiness":"not_probed","endpoint":null,"maintenance":null,"resources":"missing_executable","configuration":"missing_or_invalid","database":{"classification":"existing_ready","allowed_to_start":true,"fingerprint":"a".repeat(64),"reason":"server_ready"}}});
    let mut stream = wire::Stream::new(&r).unwrap();
    stream.line(&serde_json::to_vec(&v).unwrap()).unwrap();
    assert_eq!(
        wire::inspection(&stream.finish().unwrap(), 0)
            .unwrap()
            .components
            .unwrap()
            .database,
        Some(OpsDatabaseClassification::ExistingReady)
    );
    for code in [
        "database_recovery_required",
        "database_migration_required",
        "database_configuration_mismatch",
        "database_snapshot_too_large",
        "database_snapshot_space_insufficient",
        "database_admission_timeout",
        "database_changed_during_admission",
    ] {
        let r = request(
            OpsAction::Preflight,
            json!({"operation":"start","options":{}}),
        );
        let v = json!({"protocol_version":1,"request_id":r["request_id"],"type":"result","outcome":"rejected","error":{"code":code}});
        let mut stream = wire::Stream::new(&r).unwrap();
        stream.line(&serde_json::to_vec(&v).unwrap()).unwrap();
        assert_eq!(
            serde_json::to_value(wire::error(&stream.finish().unwrap()["error"]).unwrap()).unwrap(),
            code
        );
    }
}

#[test]
#[ignore = "requires an independently produced native Ops bundle and externally supplied manifest pin"]
fn actual_independent_bundle_runs_through_native_trust_and_read_only_bridge() {
    fn copy(source: &Path, destination: &Path) {
        fs::create_dir_all(destination).unwrap();
        for item in fs::read_dir(source).unwrap() {
            let item = item.unwrap();
            let path = item.path();
            let to = destination.join(item.file_name());
            let metadata = fs::symlink_metadata(&path).unwrap();
            if metadata.file_type().is_symlink() {
                std::os::unix::fs::symlink(fs::read_link(path).unwrap(), to).unwrap();
            } else if metadata.is_dir() {
                copy(&path, &to);
            } else {
                assert!(metadata.is_file());
                fs::copy(path, to).unwrap();
            }
        }
    }
    let source = PathBuf::from(
        std::env::var_os("POWERCONTEXT_OPS_TEST_BUNDLE").expect("set produced bundle directory"),
    );
    let pin = std::env::var("POWERCONTEXT_OPS_TEST_BUNDLE_SHA256")
        .expect("set externally approved manifest pin");
    assert!(trust::is_digest(&pin));
    let f = Fixture::new();
    let bundle = f.support.join("bundle");
    fs::remove_dir_all(&bundle).unwrap();
    copy(&source, &bundle);
    fs::write(
        f.resources.join("ops-provision.json"),
        serde_json::to_vec(
            &json!({"format_version":1,"binding_mode":"target_file","bundle_manifest_sha256":pin}),
        )
        .unwrap(),
    )
    .unwrap();
    let host = f.host();
    let inspection = host.inspect();
    assert_eq!(inspection.outcome, OpsOutcome::Succeeded, "{inspection:?}");
    assert_eq!(
        inspection.components.unwrap().resources,
        Some(OpsRuntimeResources::MissingExecutable)
    );
    let preflight = host
        .preflight(OpsPreflightInput {
            operation: OpsOperation::RemoveRegistration,
            options: OpsOptions::default(),
            resume: None,
        })
        .unwrap();
    assert!(preflight.bridge_error.is_none(), "{preflight:?}");
    if preflight.outcome == OpsOutcome::Succeeded {
        assert!(!preflight.approval.unwrap().effects.may_start);
    } else {
        assert_eq!(preflight.outcome, OpsOutcome::Rejected);
        assert_eq!(preflight.error, Some(OpsErrorCode::OwnershipConflict));
    }
    assert!(host.references().unwrap().operations.is_empty());
    assert!(!f.support.join("state").exists());
    let dependency = bundle.join("packages/powercontext/ops/__init__.py");
    let mut bytes = fs::read(&dependency).unwrap();
    bytes.extend_from_slice(b"\n# changed disposable dependency\n");
    fs::write(dependency, bytes).unwrap();
    assert_eq!(
        host.inspect().bridge_error,
        Some(OpsBridgeError::IntegrityFailure)
    );
}
