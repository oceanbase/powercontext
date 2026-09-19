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

//! Atomic, bounded profile storage. Only references to our own vault entries are persisted.
use crate::{
    credentials::{Credential, CredentialId, CredentialWriteRequest, Vault},
    error::SafeError,
    transport::Endpoint,
};
use serde::{Deserialize, Serialize};
use std::{collections::BTreeMap, io::Write, path::PathBuf, sync::Arc};
use ts_rs::TS;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum Authentication {
    UnauthenticatedLoopback,
    Bearer,
}

#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct StoredProfile {
    id: String,
    revision: u32,
    name: String,
    endpoint: String,
    authentication: Authentication,
    ca_pem: Option<String>,
    compatibility: Option<String>,
    credential_ref: Option<String>,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
struct Document {
    schema: u32,
    profiles: Vec<StoredProfile>,
    pending_deletions: Vec<String>,
}
impl Default for Document {
    fn default() -> Self {
        Self {
            schema: 1,
            profiles: vec![],
            pending_deletions: vec![],
        }
    }
}

#[derive(Clone, Serialize, TS)]
#[serde(rename_all = "camelCase")]
pub struct ProfileView {
    pub id: String,
    pub revision: u32,
    pub name: String,
    pub endpoint: String,
    pub authentication: Authentication,
    pub ca_pem: Option<String>,
    pub compatibility: Option<String>,
    pub credential_state: CredentialState,
}
#[derive(Clone, Copy, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum CredentialState {
    NotRequired,
    Stored,
    SessionOnly,
    Missing,
}

// A request never implements Serialize/Debug because it can temporarily carry a secret.
#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct ProfileInput {
    pub id: Option<String>,
    pub revision: Option<u32>,
    pub name: String,
    pub endpoint: String,
    pub authentication: Authentication,
    pub ca_pem: Option<String>,
    pub compatibility: Option<String>,
    pub keep_credential: bool,
    pub credential: Option<CredentialWriteRequest>,
}

pub struct ProfileRepository {
    path: PathBuf,
    document: Document,
    session: BTreeMap<String, Arc<Credential>>,
    vault: Arc<dyn Vault>,
}
impl ProfileRepository {
    pub fn open(path: PathBuf, vault: Arc<dyn Vault>) -> Result<Self, SafeError> {
        let document = if path.exists() {
            let metadata = path.metadata().map_err(|_| SafeError::Storage)?;
            if metadata.len() > 1024 * 1024 {
                return Err(SafeError::ProfileCorrupt);
            }
            let bytes = std::fs::read(&path).map_err(|_| SafeError::Storage)?;
            serde_json::from_slice::<Document>(&bytes).map_err(|_| SafeError::ProfileCorrupt)?
        } else {
            Document::default()
        };
        if document.schema != 1
            || document.profiles.len() > 20
            || document.pending_deletions.len() > 100
        {
            return Err(SafeError::ProfileCorrupt);
        }
        let mut ids = std::collections::BTreeSet::new();
        let mut names = std::collections::BTreeSet::new();
        let mut refs = std::collections::BTreeSet::new();
        for profile in &document.profiles {
            if !valid_id(&profile.id)
                || profile.revision == 0
                || !ids.insert(&profile.id)
                || !names.insert(profile.name.to_lowercase())
                || validate_fields(
                    &profile.name,
                    &profile.endpoint,
                    profile.authentication,
                    profile.ca_pem.as_deref(),
                )
                .is_err()
                || profile
                    .credential_ref
                    .as_ref()
                    .is_some_and(|id| !valid_id(id) || !refs.insert(id))
            {
                return Err(SafeError::ProfileCorrupt);
            }
        }
        if document
            .pending_deletions
            .iter()
            .any(|id| !valid_id(id) || refs.contains(id))
        {
            return Err(SafeError::ProfileCorrupt);
        }
        let mut result = Self {
            path,
            document,
            session: BTreeMap::new(),
            vault,
        };
        result.cleanup();
        Ok(result)
    }
    pub fn views(&self) -> Vec<ProfileView> {
        self.document
            .profiles
            .iter()
            .map(|p| ProfileView {
                id: p.id.clone(),
                revision: p.revision,
                name: p.name.clone(),
                endpoint: p.endpoint.clone(),
                authentication: p.authentication,
                ca_pem: p.ca_pem.clone(),
                compatibility: p.compatibility.clone(),
                credential_state: if p.authentication == Authentication::UnauthenticatedLoopback {
                    CredentialState::NotRequired
                } else if p.credential_ref.is_some() {
                    CredentialState::Stored
                } else if self.session.contains_key(&p.id) {
                    CredentialState::SessionOnly
                } else {
                    CredentialState::Missing
                },
            })
            .collect()
    }
    pub fn pending_cleanup(&self) -> usize {
        self.document.pending_deletions.len()
    }
    pub fn api(&self, id: &str) -> Result<crate::transport::ServerApi, SafeError> {
        let p = self
            .document
            .profiles
            .iter()
            .find(|p| p.id == id)
            .ok_or(SafeError::NotFound)?;
        let secret = if p.authentication == Authentication::Bearer {
            Some(if let Some(reference) = &p.credential_ref {
                self.vault.read(&CredentialId::new(reference.clone())?)?
            } else {
                self.session
                    .get(id)
                    .ok_or(SafeError::CredentialMissing)?
                    .load(self.vault.as_ref())?
            })
        } else {
            None
        };
        crate::transport::ServerApi::new(
            Endpoint::parse(&p.endpoint)?,
            p.ca_pem.as_deref().map(str::as_bytes),
            secret,
        )
    }
    pub fn save(&mut self, input: ProfileInput) -> Result<ProfileView, SafeError> {
        let endpoint = validate_fields(
            &input.name,
            &input.endpoint,
            input.authentication,
            input.ca_pem.as_deref(),
        )?;
        if input.compatibility.as_ref().is_some_and(|v| v.len() > 128) {
            return Err(SafeError::InvalidInput);
        }
        let old = input
            .id
            .as_ref()
            .map(|id| {
                self.document
                    .profiles
                    .iter()
                    .find(|p| &p.id == id)
                    .cloned()
                    .ok_or(SafeError::NotFound)
            })
            .transpose()?;
        if old.as_ref().map(|p| p.revision) != input.revision {
            return Err(SafeError::Conflict);
        }
        if old.is_none() && self.document.profiles.len() >= 20 {
            return Err(SafeError::InvalidInput);
        }
        if self.document.profiles.iter().any(|p| {
            Some(&p.id) != input.id.as_ref()
                && p.name.to_lowercase() == input.name.trim().to_lowercase()
        }) {
            return Err(SafeError::DuplicateName);
        }
        let same_target = old.as_ref().is_some_and(|p| {
            p.endpoint == endpoint
                && p.ca_pem == input.ca_pem
                && p.authentication == input.authentication
        });
        if input.keep_credential && (!same_target || input.credential.is_some()) {
            return Err(SafeError::InvalidCredential);
        }
        if input.authentication == Authentication::UnauthenticatedLoopback
            && input.credential.is_some()
        {
            return Err(SafeError::InvalidCredential);
        }
        let id = input.id.unwrap_or_else(new_id);
        let revision = old
            .as_ref()
            .map_or(Some(1), |p| p.revision.checked_add(1))
            .ok_or(SafeError::Storage)?;
        let mut reference = if input.keep_credential {
            old.as_ref().and_then(|p| p.credential_ref.clone())
        } else {
            None
        };
        let mut new_credential = None;
        if let Some(request) = input.credential {
            let credential_id = new_id();
            // Persist cleanup intent before touching the OS vault, including crash/failure paths.
            if request.storage_choice() == crate::credentials::StorageChoice::Persistent {
                let mut journal = self.document.clone();
                journal.pending_deletions.push(credential_id.clone());
                self.persist(&journal)?;
                self.document = journal;
            }
            let stored = request.store(
                self.vault.as_ref(),
                CredentialId::new(credential_id.clone())?,
            );
            match stored {
                Ok((credential, _)) => {
                    if matches!(credential, Credential::Persistent(_)) {
                        reference = Some(credential_id);
                    }
                    new_credential = Some(Arc::new(credential));
                }
                Err(error) => {
                    self.cleanup();
                    return Err(error);
                }
            }
        }
        let profile = StoredProfile {
            id: id.clone(),
            revision,
            name: input.name.trim().into(),
            endpoint,
            authentication: input.authentication,
            ca_pem: input.ca_pem,
            compatibility: input.compatibility,
            credential_ref: reference,
        };
        let mut next = self.document.clone();
        next.profiles.retain(|p| p.id != id);
        if let Some(old_ref) = old.and_then(|p| p.credential_ref)
            && Some(&old_ref) != profile.credential_ref.as_ref()
        {
            next.pending_deletions.push(old_ref);
        }
        next.pending_deletions
            .retain(|r| Some(r) != profile.credential_ref.as_ref());
        next.profiles.push(profile);
        if let Err(error) = self.persist(&next) {
            self.cleanup();
            return Err(error);
        }
        self.document = next;
        if !input.keep_credential {
            self.session.remove(&id);
        }
        if let Some(credential) = new_credential
            && matches!(*credential, Credential::SessionOnly(_))
        {
            self.session.insert(id.clone(), credential);
        }
        self.cleanup();
        self.views()
            .into_iter()
            .find(|p| p.id == id)
            .ok_or(SafeError::Storage)
    }
    pub fn remove(&mut self, id: &str, revision: u32) -> Result<(), SafeError> {
        let old = self
            .document
            .profiles
            .iter()
            .find(|p| p.id == id)
            .ok_or(SafeError::NotFound)?;
        if old.revision != revision {
            return Err(SafeError::Conflict);
        }
        let mut next = self.document.clone();
        if let Some(reference) = &old.credential_ref {
            next.pending_deletions.push(reference.clone());
        }
        next.profiles.retain(|p| p.id != id);
        self.persist(&next)?;
        self.document = next;
        self.session.remove(id);
        self.cleanup();
        Ok(())
    }
    fn persist(&self, document: &Document) -> Result<(), SafeError> {
        if document.pending_deletions.len() > 100 {
            return Err(SafeError::CredentialUnavailable);
        }
        let parent = self.path.parent().ok_or(SafeError::Storage)?;
        std::fs::create_dir_all(parent).map_err(|_| SafeError::Storage)?;
        let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| SafeError::Storage)?;
        let bytes = serde_json::to_vec_pretty(document).map_err(|_| SafeError::Storage)?;
        if bytes.len() > 1024 * 1024 {
            return Err(SafeError::InvalidInput);
        }
        file.write_all(&bytes)
            .and_then(|_| file.as_file().sync_all())
            .map_err(|_| SafeError::Storage)?;
        file.persist(&self.path).map_err(|_| SafeError::Storage)?;
        Ok(())
    }
    fn cleanup(&mut self) {
        let mut next = self.document.clone();
        next.pending_deletions.retain(|id| {
            CredentialId::new(id.clone())
                .and_then(|id| self.vault.delete(&id))
                .is_err()
        });
        if next.pending_deletions != self.document.pending_deletions && self.persist(&next).is_ok()
        {
            self.document = next;
        }
    }
}
fn new_id() -> String {
    format!("desktop-{}", uuid::Uuid::new_v4())
}
fn valid_id(id: &str) -> bool {
    id.strip_prefix("desktop-")
        .is_some_and(|id| uuid::Uuid::parse_str(id).is_ok())
}
fn validate_fields(
    name: &str,
    endpoint: &str,
    authentication: Authentication,
    ca: Option<&str>,
) -> Result<String, SafeError> {
    if name.trim().is_empty() || name.chars().count() > 128 || name.chars().any(char::is_control) {
        return Err(SafeError::InvalidInput);
    }
    let endpoint = Endpoint::parse(endpoint)?;
    if authentication == Authentication::UnauthenticatedLoopback && !endpoint.is_loopback() {
        return Err(SafeError::InvalidCredential);
    }
    crate::transport::Transport::new(ca.map(str::as_bytes))?;
    Ok(endpoint.as_str().into())
}
