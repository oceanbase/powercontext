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

use powercontext_desktop::{
    connections::profiles::{CredentialState, ProfileInput, ProfileRepository},
    credentials::{CredentialId, Secret, Vault},
    error::SafeError,
};
use std::{
    collections::BTreeSet,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
};

#[derive(Default)]
struct TestVault {
    ids: Mutex<BTreeSet<String>>,
    unavailable: AtomicBool,
}
impl Vault for TestVault {
    fn put(&self, id: &CredentialId, _: &Secret) -> Result<(), SafeError> {
        if self.unavailable.load(Ordering::Relaxed) {
            return Err(SafeError::CredentialUnavailable);
        }
        self.ids.lock().unwrap().insert(id.as_str().into());
        Ok(())
    }
    fn read(&self, id: &CredentialId) -> Result<Secret, SafeError> {
        if self.ids.lock().unwrap().contains(id.as_str()) {
            Secret::new("synthetic-private-marker".into())
        } else {
            Err(SafeError::CredentialMissing)
        }
    }
    fn delete(&self, id: &CredentialId) -> Result<(), SafeError> {
        if self.unavailable.load(Ordering::Relaxed) {
            return Err(SafeError::CredentialUnavailable);
        }
        self.ids.lock().unwrap().remove(id.as_str());
        Ok(())
    }
}
fn input(name: &str, storage: &str) -> ProfileInput {
    serde_json::from_value(serde_json::json!({
        "id":null,"revision":null,"name":name,"endpoint":"http://localhost:8000/prefix",
        "authentication":"bearer","caPem":null,"compatibility":null,"keepCredential":false,
        "credential":{"secret":"synthetic-private-marker","storage":storage}
    }))
    .unwrap()
}
#[test]
fn persistence_restart_and_remove_never_store_or_return_secrets() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles.json");
    let vault = Arc::new(TestVault::default());
    let mut repo = ProfileRepository::open(path.clone(), vault.clone()).unwrap();
    let profile = repo.save(input("中文配置", "persistent")).unwrap();
    assert!(matches!(profile.credential_state, CredentialState::Stored));
    let view = serde_json::to_string(&profile).unwrap();
    assert!(!view.contains("synthetic-private-marker"));
    assert!(!view.contains("credentialRef"));
    assert!(
        !std::fs::read_to_string(&path)
            .unwrap()
            .contains("synthetic-private-marker")
    );
    drop(repo);
    let mut repo = ProfileRepository::open(path, vault.clone()).unwrap();
    assert!(repo.api(&profile.id).is_ok());
    repo.remove(&profile.id, profile.revision).unwrap();
    assert!(repo.views().is_empty());
    assert!(vault.ids.lock().unwrap().is_empty());
}
#[test]
fn explicit_session_works_without_vault_and_expires_on_restart() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles.json");
    let vault = Arc::new(TestVault::default());
    vault.unavailable.store(true, Ordering::Relaxed);
    let mut repo = ProfileRepository::open(path.clone(), vault.clone()).unwrap();
    assert!(matches!(
        repo.save(input("Persistent", "persistent")),
        Err(SafeError::CredentialUnavailable)
    ));
    assert!(repo.views().is_empty());
    let session = repo.save(input("Session", "session_only")).unwrap();
    assert!(matches!(
        session.credential_state,
        CredentialState::SessionOnly
    ));
    assert!(repo.api(&session.id).is_ok());
    drop(repo);
    let repo = ProfileRepository::open(path, vault).unwrap();
    assert!(matches!(
        repo.views()[0].credential_state,
        CredentialState::Missing
    ));
    assert!(matches!(
        repo.api(&session.id),
        Err(SafeError::CredentialMissing)
    ));
}
#[test]
fn retargeting_cannot_keep_a_credential_and_edits_require_current_revision() {
    let dir = tempfile::tempdir().unwrap();
    let vault = Arc::new(TestVault::default());
    let mut repo =
        ProfileRepository::open(dir.path().join("profiles.json"), vault.clone()).unwrap();
    let original = repo.save(input("One", "persistent")).unwrap();
    let mut changed = input("One", "persistent");
    changed.id = Some(original.id.clone());
    changed.revision = Some(original.revision);
    changed.endpoint = "https://another.example".into();
    changed.keep_credential = true;
    changed.credential = None;
    assert!(matches!(
        repo.save(changed),
        Err(SafeError::InvalidCredential)
    ));
    assert_eq!(repo.views()[0].endpoint, original.endpoint);
    let mut changed = input("One", "persistent");
    changed.id = Some(original.id.clone());
    changed.revision = Some(original.revision);
    changed.endpoint = "https://another.example".into();
    changed.credential = None;
    let updated = repo.save(changed).unwrap();
    assert!(matches!(updated.credential_state, CredentialState::Missing));
    assert!(vault.ids.lock().unwrap().is_empty());
    assert!(matches!(
        repo.remove(&original.id, original.revision),
        Err(SafeError::Conflict)
    ));
    assert!(matches!(
        repo.save(input("one", "session_only")),
        Err(SafeError::DuplicateName)
    ));
}
#[test]
fn failed_cleanup_is_retained_and_completed_on_reopen() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles.json");
    let vault = Arc::new(TestVault::default());
    let mut repo = ProfileRepository::open(path.clone(), vault.clone()).unwrap();
    let profile = repo.save(input("One", "persistent")).unwrap();
    vault.unavailable.store(true, Ordering::Relaxed);
    repo.remove(&profile.id, profile.revision).unwrap();
    assert_eq!(repo.pending_cleanup(), 1);
    drop(repo);
    vault.unavailable.store(false, Ordering::Relaxed);
    let repo = ProfileRepository::open(path, vault.clone()).unwrap();
    assert_eq!(repo.pending_cleanup(), 0);
    assert!(vault.ids.lock().unwrap().is_empty());
}
#[test]
fn corrupt_configuration_is_reported_without_overwriting_it() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("profiles.json");
    std::fs::write(&path, "broken configuration").unwrap();
    assert!(matches!(
        ProfileRepository::open(path.clone(), Arc::new(TestVault::default())),
        Err(SafeError::ProfileCorrupt)
    ));
    assert_eq!(
        std::fs::read_to_string(path).unwrap(),
        "broken configuration"
    );
}
