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

//! Native-only secrets. No secret-bearing type implements Serialize or Debug.
use crate::error::SafeError;
use serde::{Deserialize, Deserializer, Serialize};
use ts_rs::TS;
use zeroize::Zeroizing;

const SERVICE: &str = "com.powercontext.desktop.preview";
pub struct Secret(Zeroizing<String>);
impl Secret {
    pub fn new(value: String) -> Result<Self, SafeError> {
        let value = Zeroizing::new(value);
        if value.is_empty() || value.len() > 2048 || !value.bytes().all(|c| c.is_ascii_graphic()) {
            return Err(SafeError::InvalidCredential);
        }
        Ok(Self(value))
    }
    pub(crate) fn expose(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for Secret {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(|_| serde::de::Error::custom("invalid credential"))
    }
}

/// IDs must be native-generated opaque identifiers, never endpoints or account names.
pub struct CredentialId(String);
impl CredentialId {
    pub fn as_str(&self) -> &str {
        &self.0
    }
    pub fn new(id: String) -> Result<Self, SafeError> {
        if id.is_empty()
            || id.len() > 80
            || !id.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-')
        {
            return Err(SafeError::InvalidCredential);
        }
        Ok(Self(id))
    }
}

pub trait Vault: Send + Sync {
    fn put(&self, id: &CredentialId, secret: &Secret) -> Result<(), SafeError>;
    fn read(&self, id: &CredentialId) -> Result<Secret, SafeError>;
    fn delete(&self, id: &CredentialId) -> Result<(), SafeError>;
}

pub struct WindowsVault;
#[cfg(windows)]
impl WindowsVault {
    fn entry(id: &CredentialId) -> Result<keyring::Entry, SafeError> {
        keyring::Entry::new(SERVICE, &id.0).map_err(|_| SafeError::CredentialUnavailable)
    }
}
#[cfg(windows)]
impl Vault for WindowsVault {
    fn put(&self, id: &CredentialId, secret: &Secret) -> Result<(), SafeError> {
        Self::entry(id)?
            .set_password(secret.expose())
            .map_err(|_| SafeError::CredentialUnavailable)
    }
    fn read(&self, id: &CredentialId) -> Result<Secret, SafeError> {
        let value = Self::entry(id)?.get_password().map_err(|e| match e {
            keyring::Error::NoEntry => SafeError::CredentialMissing,
            _ => SafeError::CredentialUnavailable,
        })?;
        Secret::new(value)
    }
    fn delete(&self, id: &CredentialId) -> Result<(), SafeError> {
        match Self::entry(id)?.delete_credential() {
            Ok(()) | Err(keyring::Error::NoEntry) => Ok(()),
            Err(_) => Err(SafeError::CredentialUnavailable),
        }
    }
}
#[cfg(not(windows))]
impl Vault for WindowsVault {
    fn put(&self, _: &CredentialId, _: &Secret) -> Result<(), SafeError> {
        Err(SafeError::CredentialUnavailable)
    }
    fn read(&self, _: &CredentialId) -> Result<Secret, SafeError> {
        Err(SafeError::CredentialUnavailable)
    }
    fn delete(&self, _: &CredentialId) -> Result<(), SafeError> {
        Err(SafeError::CredentialUnavailable)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize, TS)]
#[serde(rename_all = "snake_case")]
pub enum StorageChoice {
    Persistent,
    SessionOnly,
}
/// Renderer-to-native input only. There is deliberately no Serialize or Debug implementation.
/// S2 must resolve the native-owned connection ID before consuming this request.
#[derive(Deserialize, TS)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct CredentialWriteRequest {
    #[ts(type = "string")]
    secret: Secret,
    storage: StorageChoice,
}

/// The entire successful write response; neither a secret nor its vault identifier is exposed.
#[derive(Serialize, TS)]
pub struct CredentialWriteReceipt {
    storage: StorageChoice,
}

impl CredentialWriteRequest {
    pub fn storage_choice(&self) -> StorageChoice {
        self.storage
    }
    pub fn store(
        self,
        vault: &(impl Vault + ?Sized),
        native_id: CredentialId,
    ) -> Result<(Credential, CredentialWriteReceipt), SafeError> {
        let credential = Credential::store(vault, native_id, self.secret, self.storage)?;
        Ok((
            credential,
            CredentialWriteReceipt {
                storage: self.storage,
            },
        ))
    }
}

pub enum Credential {
    Persistent(CredentialId),
    SessionOnly(Secret),
}
impl Credential {
    /// Failure is returned to the caller; session storage requires a separate explicit choice.
    pub fn store(
        vault: &(impl Vault + ?Sized),
        id: CredentialId,
        secret: Secret,
        choice: StorageChoice,
    ) -> Result<Self, SafeError> {
        match choice {
            StorageChoice::Persistent => {
                vault.put(&id, &secret)?;
                Ok(Self::Persistent(id))
            }
            StorageChoice::SessionOnly => Ok(Self::SessionOnly(secret)),
        }
    }
    pub fn load(&self, vault: &(impl Vault + ?Sized)) -> Result<Secret, SafeError> {
        match self {
            Self::Persistent(id) => vault.read(id),
            Self::SessionOnly(secret) => Secret::new(secret.expose().to_owned()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Unavailable;
    impl Vault for Unavailable {
        fn put(&self, _: &CredentialId, _: &Secret) -> Result<(), SafeError> {
            Err(SafeError::CredentialUnavailable)
        }
        fn read(&self, _: &CredentialId) -> Result<Secret, SafeError> {
            Err(SafeError::CredentialUnavailable)
        }
        fn delete(&self, _: &CredentialId) -> Result<(), SafeError> {
            Err(SafeError::CredentialUnavailable)
        }
    }
    #[test]
    fn write_protocol_requires_explicit_storage_and_returns_only_metadata() {
        for input in [
            r#"{"secret":"synthetic"}"#,
            r#"{"secret":"synthetic","storage":"automatic"}"#,
            r#"{"secret":"synthetic","storage":"session_only","nativeId":"chosen"}"#,
        ] {
            assert!(serde_json::from_str::<CredentialWriteRequest>(input).is_err());
        }
        let invalid = serde_json::from_str::<CredentialWriteRequest>(
            r#"{"secret":"private value","storage":"persistent"}"#,
        );
        let error = invalid.err().unwrap().to_string();
        assert!(!error.contains("private value"));
        let persistent: CredentialWriteRequest =
            serde_json::from_str(r#"{"secret":"synthetic","storage":"persistent"}"#).unwrap();
        assert!(matches!(
            persistent.store(&Unavailable, CredentialId::new("native".into()).unwrap()),
            Err(SafeError::CredentialUnavailable)
        ));
        let session: CredentialWriteRequest =
            serde_json::from_str(r#"{"secret":"synthetic","storage":"session_only"}"#).unwrap();
        let (credential, receipt) = session
            .store(&Unavailable, CredentialId::new("native".into()).unwrap())
            .unwrap();
        assert_eq!(credential.load(&Unavailable).unwrap().expose(), "synthetic");
        assert_eq!(
            serde_json::to_value(receipt).unwrap(),
            serde_json::json!({"storage":"session_only"})
        );
    }

    #[test]
    fn unavailable_vault_never_silently_downgrades() {
        let result = Credential::store(
            &Unavailable,
            CredentialId::new("test".into()).unwrap(),
            Secret::new("synthetic".into()).unwrap(),
            StorageChoice::Persistent,
        );
        assert!(matches!(result, Err(SafeError::CredentialUnavailable)));
        let session = Credential::store(
            &Unavailable,
            CredentialId::new("test".into()).unwrap(),
            Secret::new("synthetic".into()).unwrap(),
            StorageChoice::SessionOnly,
        )
        .unwrap();
        assert_eq!(session.load(&Unavailable).unwrap().expose(), "synthetic");
    }
}
