// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use crate::AuthError;
use fs2::FileExt;
use serde_json::Value;
use std::{
    fs::{File, OpenOptions},
    io::{Read, Write},
    path::Path,
    time::Duration,
};

pub(super) const STORE_LIMIT: u64 = 1024 * 1024;
pub(super) const OWNED_SCHEMA: &str = "powercontext.gateway.oauth.v1";

pub(super) fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, AuthError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or(AuthError::Credentials)
}

pub(super) fn expiry(token: &Value) -> Result<i64, AuthError> {
    let value = &token["expires_at"];
    if let Some(seconds) = value.as_i64() {
        return Ok(seconds);
    }
    chrono::DateTime::parse_from_rfc3339(value.as_str().ok_or(AuthError::Credentials)?)
        .map(|v| v.timestamp())
        .map_err(|_| AuthError::Credentials)
}

pub(super) fn read_store(path: &Path) -> Result<Value, AuthError> {
    let file = File::open(path).map_err(|_| AuthError::Credentials)?;
    let mut bytes = Vec::new();
    file.take(STORE_LIMIT + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| AuthError::Credentials)?;
    if bytes.len() as u64 > STORE_LIMIT {
        return Err(AuthError::Credentials);
    }
    serde_json::from_slice(&bytes).map_err(|_| AuthError::Credentials)
}

pub(super) async fn lock_file(lock_path: &Path) -> Result<File, AuthError> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create(true).truncate(false);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let file = options.open(lock_path).map_err(|_| AuthError::Store)?;
    loop {
        match file.try_lock_exclusive() {
            Ok(()) => return Ok(file),
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                tokio::time::sleep(Duration::from_millis(10)).await;
            }
            Err(_) => return Err(AuthError::Store),
        }
    }
}

pub(super) fn write_store(path: &Path, data: &Value) -> Result<(), AuthError> {
    persist_store(path, data, true)
}

pub(super) fn persist_store(path: &Path, data: &Value, replace: bool) -> Result<(), AuthError> {
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let mut file = tempfile::NamedTempFile::new_in(parent).map_err(|_| AuthError::Store)?;
    serde_json::to_writer(file.as_file_mut(), data).map_err(|_| AuthError::Store)?;
    file.write_all(b"\n").map_err(|_| AuthError::Store)?;
    file.as_file().sync_all().map_err(|_| AuthError::Store)?;
    if replace {
        file.persist(path).map_err(|_| AuthError::Store)?;
    } else {
        file.persist_noclobber(path).map_err(|_| AuthError::Store)?;
    }
    #[cfg(unix)]
    File::open(parent)
        .and_then(|f| f.sync_all())
        .map_err(|_| AuthError::Store)?;
    Ok(())
}

pub(super) async fn lock_owned(path: &Path) -> Result<File, AuthError> {
    let mut lock_path = path.as_os_str().to_os_string();
    lock_path.push(".lock");
    tokio::time::timeout(Duration::from_secs(30), lock_file(Path::new(&lock_path)))
        .await
        .map_err(|_| AuthError::Store)?
}

pub(super) async fn lock_new(path: &Path) -> Result<File, AuthError> {
    if path.exists() {
        return Err(AuthError::Store);
    }
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    if !parent.is_dir() {
        return Err(AuthError::Store);
    }
    let lock = lock_owned(path).await?;
    if path.exists() {
        return Err(AuthError::Store);
    }
    Ok(lock)
}

pub(super) fn new_owned_record(subscription: &str) -> Value {
    serde_json::json!({"schema":OWNED_SCHEMA,"subscription":subscription,
        "device_id":uuid::Uuid::new_v4().to_string()})
}
