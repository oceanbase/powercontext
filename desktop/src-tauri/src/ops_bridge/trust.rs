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

//! Distribution pins, fixed launch paths, and private reference storage.
use super::{OpsBridgeError as Error, wire};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    collections::{BTreeMap, BTreeSet},
    fs,
    io::{Read, Write},
    path::{Component, Path, PathBuf},
};
mod security;

const MAX_MANIFEST: usize = 16 * 1024 * 1024;
const MAX_FILES: usize = 100_000;
const MAX_BUNDLE_BYTES: u64 = 8 * 1024 * 1024 * 1024;

pub(super) struct Trust {
    resources: Option<PathBuf>,
    pub support: Option<PathBuf>,
    local_data: Option<PathBuf>,
    home: Option<PathBuf>,
    // Only packaged configuration is an authority. Bundle metadata cannot set this pin.
    provision: Result<Provision, Error>,
}
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct Provision {
    format_version: u8,
    bundle_manifest_sha256: String,
    binding_mode: Mode,
    #[serde(default)]
    catalog_sha256: Option<String>,
}
#[derive(Clone, Copy, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum Mode {
    TargetFile,
    ManagedCatalog,
}
#[derive(Clone)]
pub(super) struct Binding {
    pub fingerprint: String,
    pub target_id: Option<String>,
    pub catalog_digest: Option<String>,
    pub target_digest: Option<String>,
    bytes: Vec<u8>,
    digest: String,
}
pub(super) struct Launch {
    pub executable: PathBuf,
    pub args: Vec<String>,
    pub env: BTreeMap<String, String>,
    pub cwd: PathBuf,
}
impl Trust {
    pub fn new(
        resources: Option<PathBuf>,
        local_data: Option<PathBuf>,
        home: Option<PathBuf>,
    ) -> Self {
        let provision = resources
            .as_ref()
            .ok_or(Error::Unavailable)
            .and_then(|r| read(&r.join("ops-provision.json"), 16 * 1024))
            .and_then(|bytes| {
                let p: Provision =
                    serde_json::from_slice(&bytes).map_err(|_| Error::IntegrityFailure)?;
                if p.format_version != 1
                    || !is_digest(&p.bundle_manifest_sha256)
                    || match p.binding_mode {
                        Mode::TargetFile => p.catalog_sha256.is_some(),
                        Mode::ManagedCatalog => !p.catalog_sha256.as_deref().is_some_and(is_digest),
                    }
                {
                    return Err(Error::IntegrityFailure);
                }
                Ok(p)
            });
        Self {
            support: local_data.as_ref().map(|d| d.join("PowerContext/Ops")),
            resources,
            local_data,
            home,
            provision,
        }
    }
    pub fn binding(&self) -> Result<Binding, Error> {
        if !cfg!(any(windows, target_os = "macos")) {
            return Err(Error::UnsupportedPlatform);
        }
        let p = self.provision.as_ref().map_err(|e| *e)?;
        let support = self.support.as_ref().ok_or(Error::Unavailable)?;
        let bytes = match p.binding_mode {
            Mode::TargetFile => {
                private_directory(support, false)?;
                read_private(&support.join("target.json"), 16 * 1024)?
            }
            Mode::ManagedCatalog => read(
                &self
                    .resources
                    .as_ref()
                    .ok_or(Error::Unavailable)?
                    .join("server/catalog.json"),
                32 * 1024 * 1024,
            )?,
        };
        let digest = sha256(&bytes);
        let mut target_digest = None;
        let target_id = if p.binding_mode == Mode::TargetFile {
            let target: Target =
                serde_json::from_slice(&bytes).map_err(|_| Error::IntegrityFailure)?;
            target.validate()?;
            let mut value: serde_json::Value =
                serde_json::from_slice(&bytes).map_err(|_| Error::IntegrityFailure)?;
            let fields = value.as_object_mut().ok_or(Error::IntegrityFailure)?;
            fields.remove("format_version");
            if fields.get("isolated_runtime").is_some_and(|v| v == false) {
                fields.remove("isolated_runtime");
            }
            value.sort_all_objects();
            target_digest = Some(sha256(
                &serde_json::to_vec(&value).map_err(|_| Error::IntegrityFailure)?,
            ));
            Some(target.target_id)
        } else {
            if p.catalog_sha256.as_ref() != Some(&digest) {
                return Err(Error::IntegrityFailure);
            }
            None
        };
        let fingerprint = sha256(&serde_json::to_vec(&serde_json::json!({
            "bundle":p.bundle_manifest_sha256,"mode":if p.binding_mode == Mode::TargetFile {"target"} else {"catalog"},
            "binding":digest,"support":path_string(support)?,
            "home":self.home.as_deref().map(path_string).transpose()?,
            "local_data":self.local_data.as_deref().map(path_string).transpose()?,
        })).map_err(|_| Error::Storage)?);
        Ok(Binding {
            fingerprint,
            target_id,
            target_digest,
            catalog_digest: p.catalog_sha256.clone(),
            bytes,
            digest,
        })
    }
    pub fn launch(&self, binding: &Binding) -> Result<Launch, Error> {
        if self.binding()?.fingerprint != binding.fingerprint {
            return Err(Error::StaleApproval);
        }
        let p = self.provision.as_ref().map_err(|e| *e)?;
        let support = self.support.as_ref().ok_or(Error::Unavailable)?;
        private_directory(support, true)?;
        let root = support.join("bundle");
        let executable = verify_bundle(&root, &p.bundle_manifest_sha256)?;
        // Digest-named copies remain immutable across concurrent operations and catalog updates.
        let mode = if p.binding_mode == Mode::TargetFile {
            "target"
        } else {
            "catalog"
        };
        let file = support.join(format!("{mode}-{}.json", binding.digest));
        if file.try_exists().map_err(|_| Error::Storage)? {
            if read_private(&file, binding.bytes.len())? != binding.bytes {
                return Err(Error::IntegrityFailure);
            }
        } else {
            atomic(support, &file, &binding.bytes)?;
        }
        let mut args = vec![
            "-I".into(),
            "-S".into(),
            "-B".into(),
            path_string(&root.join("entry.py"))?,
        ];
        match p.binding_mode {
            Mode::TargetFile => args.extend([
                "--target-file".into(),
                path_string(&file)?,
                "--state-dir".into(),
                path_string(&support.join("state"))?,
            ]),
            Mode::ManagedCatalog => args.extend([
                "--catalog-file".into(),
                path_string(&file)?,
                "--catalog-sha256".into(),
                binding.digest.clone(),
            ]),
        }
        let home = self
            .home
            .as_ref()
            .filter(|p| p.is_absolute())
            .ok_or(Error::Unavailable)?;
        let mut env = BTreeMap::from([("HOME".into(), path_string(home)?)]);
        #[cfg(windows)]
        {
            let system = PathBuf::from(std::env::var_os("SystemRoot").ok_or(Error::Unavailable)?);
            if !system.is_absolute() {
                return Err(Error::Unavailable);
            }
            env.insert("SystemRoot".into(), path_string(&system)?);
            env.insert("PATH".into(), path_string(&system.join("System32"))?);
            env.insert("USERPROFILE".into(), path_string(home)?);
            env.insert(
                "LOCALAPPDATA".into(),
                path_string(self.local_data.as_ref().ok_or(Error::Unavailable)?)?,
            );
            env.insert(
                "APPDATA".into(),
                path_string(&home.join("AppData/Roaming"))?,
            );
            env.insert("TEMP".into(), path_string(&std::env::temp_dir())?);
            env.insert("TMP".into(), path_string(&std::env::temp_dir())?);
        }
        #[cfg(not(windows))]
        {
            env.insert("PATH".into(), "/usr/bin:/bin:/usr/sbin:/sbin".into());
            env.insert("TMPDIR".into(), "/tmp".into());
        }
        Ok(Launch {
            executable,
            args,
            env,
            cwd: support.clone(),
        })
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Target {
    format_version: u8,
    target_id: String,
    python_executable: String,
    package_version: String,
    env_file: String,
    configuration_owned: bool,
    #[serde(default)]
    isolated_runtime: bool,
}
impl Target {
    fn validate(&self) -> Result<(), Error> {
        wire::identifier(&self.target_id).map_err(|_| Error::IntegrityFailure)?;
        if self.format_version != 1
            || self.package_version.is_empty()
            || self.package_version.len() > 128
            || [&self.python_executable, &self.env_file]
                .iter()
                .any(|s| s.len() > 4096 || !Path::new(s).is_absolute() || s.contains('\0'))
        {
            return Err(Error::IntegrityFailure);
        }
        let _ = (self.configuration_owned, self.isolated_runtime);
        Ok(())
    }
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format_version: u8,
    python_version: String,
    platform: String,
    architecture: String,
    python_relative: String,
    arguments: Vec<String>,
    requirements_sha256: String,
    installed_bytes: u64,
    files_sha256: BTreeMap<String, String>,
}
fn verify_bundle(root: &Path, pin: &str) -> Result<PathBuf, Error> {
    no_links(root)?;
    let manifest_bytes = read(&root.join("bundle.json"), MAX_MANIFEST)?;
    if sha256(&manifest_bytes) != pin {
        return Err(Error::IntegrityFailure);
    }
    let m: Manifest =
        serde_json::from_slice(&manifest_bytes).map_err(|_| Error::IntegrityFailure)?;
    if m.format_version != 1
        || m.python_version.is_empty()
        || m.python_version.len() > 128
        || m.arguments != ["-I", "-S", "-B", "entry.py"]
        || !is_digest(&m.requirements_sha256)
        || m.files_sha256.is_empty()
        || m.files_sha256.len() > MAX_FILES
        || m.installed_bytes > MAX_BUNDLE_BYTES
    {
        return Err(Error::IntegrityFailure);
    }
    let platform = if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "unsupported"
    };
    let arch = m.architecture.to_ascii_lowercase();
    if m.platform != platform
        || !match std::env::consts::ARCH {
            "aarch64" => matches!(arch.as_str(), "arm64" | "aarch64"),
            "x86_64" => matches!(arch.as_str(), "x86_64" | "amd64"),
            _ => false,
        }
    {
        return Err(Error::UnsupportedPlatform);
    }
    if !m.python_relative.starts_with("runtime/")
        || !m.files_sha256.contains_key(&m.python_relative)
        || !m.files_sha256.contains_key("entry.py")
    {
        return Err(Error::IntegrityFailure);
    }
    let mut actual = BTreeSet::new();
    inventory(root, root, &mut actual)?;
    let expected = m
        .files_sha256
        .keys()
        .cloned()
        .chain(["bundle.json".into()])
        .collect::<BTreeSet<_>>();
    if actual != expected {
        return Err(Error::IntegrityFailure);
    }
    let mut bytes = 0u64;
    for (name, digest) in &m.files_sha256 {
        relative(name)?;
        if !is_digest(digest) {
            return Err(Error::IntegrityFailure);
        }
        let path = root.join(name);
        let meta = fs::symlink_metadata(&path).map_err(|_| Error::Unavailable)?;
        let resolved = if meta.file_type().is_symlink() {
            // The original builder inventories file aliases by their dereferenced content.
            // Allow only runtime-local relative aliases to a separately inventoried regular file.
            if !name.starts_with("runtime/")
                || fs::read_link(&path)
                    .map_err(|_| Error::IntegrityFailure)?
                    .is_absolute()
            {
                return Err(Error::IntegrityFailure);
            }
            let resolved = path.canonicalize().map_err(|_| Error::IntegrityFailure)?;
            let runtime = root.join("runtime");
            if !resolved.starts_with(&runtime) || !resolved.is_file() {
                return Err(Error::IntegrityFailure);
            }
            let dest = resolved
                .strip_prefix(root)
                .map_err(|_| Error::IntegrityFailure)?
                .to_str()
                .ok_or(Error::IntegrityFailure)?
                .replace('\\', "/");
            if m.files_sha256.get(&dest) != Some(digest) {
                return Err(Error::IntegrityFailure);
            }
            no_links(&resolved)?;
            resolved
        } else {
            no_links(&path)?;
            path
        };
        let mut file = fs::File::open(&resolved).map_err(|_| Error::Unavailable)?;
        let meta = file.metadata().map_err(|_| Error::Unavailable)?;
        if !meta.is_file() || meta.len() > MAX_BUNDLE_BYTES {
            return Err(Error::IntegrityFailure);
        }
        bytes = bytes
            .checked_add(meta.len())
            .filter(|b| *b <= MAX_BUNDLE_BYTES)
            .ok_or(Error::IntegrityFailure)?;
        let mut hasher = Sha256::new();
        let mut buffer = [0u8; 64 * 1024];
        let mut size = 0u64;
        loop {
            let n = file
                .read(&mut buffer)
                .map_err(|_| Error::IntegrityFailure)?;
            if n == 0 {
                break;
            }
            size += n as u64;
            if size > meta.len() {
                return Err(Error::IntegrityFailure);
            }
            hasher.update(&buffer[..n]);
        }
        if size != meta.len() || format!("{:x}", hasher.finalize()) != *digest {
            return Err(Error::IntegrityFailure);
        }
    }
    if bytes != m.installed_bytes {
        return Err(Error::IntegrityFailure);
    }
    let executable = root.join(relative(&m.python_relative)?);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if fs::metadata(&executable)
            .map_err(|_| Error::Unavailable)?
            .permissions()
            .mode()
            & 0o111
            == 0
        {
            return Err(Error::IntegrityFailure);
        }
    }
    Ok(executable)
}
fn inventory(root: &Path, path: &Path, files: &mut BTreeSet<String>) -> Result<(), Error> {
    if path.components().count() - root.components().count() > 32 {
        return Err(Error::IntegrityFailure);
    }
    for entry in fs::read_dir(path).map_err(|_| Error::Unavailable)? {
        let entry = entry.map_err(|_| Error::Unavailable)?;
        let p = entry.path();
        let m = fs::symlink_metadata(&p).map_err(|_| Error::Unavailable)?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            if m.file_attributes() & 0x400 != 0 {
                return Err(Error::IntegrityFailure);
            }
        }
        if m.is_dir() {
            inventory(root, &p, files)?;
        } else if m.is_file() || m.file_type().is_symlink() {
            if m.file_type().is_symlink() && !p.is_file() {
                return Err(Error::IntegrityFailure);
            }
            let n = p
                .strip_prefix(root)
                .map_err(|_| Error::IntegrityFailure)?
                .to_str()
                .ok_or(Error::IntegrityFailure)?
                .replace('\\', "/");
            files.insert(n);
            if files.len() > MAX_FILES + 1 {
                return Err(Error::IntegrityFailure);
            }
        } else {
            return Err(Error::IntegrityFailure);
        }
    }
    Ok(())
}
fn relative(name: &str) -> Result<PathBuf, Error> {
    if name.is_empty()
        || name.len() > 1024
        || name.contains(['\\', ':', '\0'])
        || name
            .split('/')
            .any(|p| p.is_empty() || p == "." || p == "..")
    {
        return Err(Error::IntegrityFailure);
    }
    let p = Path::new(name);
    if !p.components().all(|p| matches!(p, Component::Normal(_))) {
        return Err(Error::IntegrityFailure);
    }
    Ok(p.into())
}
fn path_string(path: &Path) -> Result<String, Error> {
    path.to_str()
        .filter(|s| !s.contains('\0'))
        .map(str::to_owned)
        .ok_or(Error::Unavailable)
}
pub(super) fn sha256(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub(super) fn is_digest(s: &str) -> bool {
    s.len() == 64
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn no_links(path: &Path) -> Result<(), Error> {
    if !path.is_absolute() || path.components().any(|p| matches!(p, Component::ParentDir)) {
        return Err(Error::IntegrityFailure);
    }
    for part in path.ancestors() {
        match fs::symlink_metadata(part) {
            Ok(m) => {
                if m.file_type().is_symlink() {
                    return Err(Error::IntegrityFailure);
                }
                #[cfg(windows)]
                {
                    use std::os::windows::fs::MetadataExt;
                    if m.file_attributes() & 0x400 != 0 {
                        return Err(Error::IntegrityFailure);
                    }
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err(Error::Unavailable),
        }
    }
    Ok(())
}
fn read(path: &Path, max: usize) -> Result<Vec<u8>, Error> {
    no_links(path)?;
    let mut f = fs::File::open(path).map_err(|_| Error::Unavailable)?;
    let m = f.metadata().map_err(|_| Error::Unavailable)?;
    if !m.is_file() || m.len() > max as u64 {
        return Err(Error::IntegrityFailure);
    }
    let mut bytes = Vec::new();
    Read::by_ref(&mut f)
        .take(max as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| Error::Unavailable)?;
    if bytes.len() > max {
        return Err(Error::IntegrityFailure);
    }
    Ok(bytes)
}
fn read_private(path: &Path, max: usize) -> Result<Vec<u8>, Error> {
    no_links(path)?;
    if !path.try_exists().map_err(|_| Error::Storage)? {
        return Err(Error::Unavailable);
    }
    security::validate(path)?;
    read(path, max)
}
fn private_directory(path: &Path, create: bool) -> Result<(), Error> {
    no_links(path)?;
    if !path.try_exists().map_err(|_| Error::Storage)? {
        if !create {
            return Err(Error::Unavailable);
        }
        if let Some(parent) = path.parent()
            && !parent.exists()
        {
            private_directory(parent, true)?;
        }
        match fs::create_dir(path) {
            Ok(()) => security::restrict(path, true)?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(_) => return Err(Error::Storage),
        }
    }
    if !path.is_dir() {
        return Err(Error::Storage);
    }
    security::validate(path)
}
fn atomic(dir: &Path, path: &Path, bytes: &[u8]) -> Result<(), Error> {
    private_directory(dir, true)?;
    no_links(path)?;
    if path.exists() {
        security::validate(path)?;
    }
    let mut temp = tempfile::NamedTempFile::new_in(dir).map_err(|_| Error::Storage)?;
    security::restrict(temp.path(), false)?;
    temp.write_all(bytes)
        .and_then(|_| temp.as_file().sync_all())
        .map_err(|_| Error::Storage)?;
    temp.persist(path).map_err(|_| Error::Storage)?;
    #[cfg(unix)]
    fs::File::open(dir)
        .and_then(|f| f.sync_all())
        .map_err(|_| Error::Storage)?;
    Ok(())
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Document {
    format_version: u8,
    operations: Vec<Reference>,
}
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Reference {
    pub operation_id: String,
    pub binding_id: String,
}
pub(super) struct References {
    support: Option<PathBuf>,
}
impl References {
    pub fn new(support: Option<PathBuf>) -> Self {
        Self { support }
    }
    pub fn read(&self) -> Result<Vec<Reference>, Error> {
        let Some(dir) = &self.support else {
            return Ok(vec![]);
        };
        let path = dir.join("bridge-operations.json");
        no_links(&path).map_err(|_| Error::Storage)?;
        if !path.try_exists().map_err(|_| Error::Storage)? {
            return Ok(vec![]);
        }
        private_directory(dir, false)?;
        let d: Document = serde_json::from_slice(&read_private(&path, 1024 * 1024)?)
            .map_err(|_| Error::Storage)?;
        if d.format_version != 1 || d.operations.len() > 4096 {
            return Err(Error::Storage);
        }
        let mut ids = BTreeSet::new();
        for r in &d.operations {
            if wire::identifier(&r.operation_id).is_err()
                || !is_digest(&r.binding_id)
                || !ids.insert(&r.operation_id)
            {
                return Err(Error::Storage);
            }
        }
        Ok(d.operations)
    }
    pub fn require(&self, id: &str, binding: &str) -> Result<(), Error> {
        match self.read()?.iter().find(|r| r.operation_id == id) {
            Some(r) if r.binding_id == binding => Ok(()),
            Some(_) => Err(Error::ForeignBinding),
            None => Err(Error::UnknownOperation),
        }
    }
    pub fn add(&self, id: &str, binding: &str) -> Result<(), Error> {
        let dir = self.support.as_ref().ok_or(Error::Storage)?;
        let mut operations = self.read()?;
        if let Some(r) = operations.iter().find(|r| r.operation_id == id) {
            return if r.binding_id == binding {
                Ok(())
            } else {
                Err(Error::ForeignBinding)
            };
        }
        if operations.len() >= 4096 {
            return Err(Error::ReferenceLimit);
        }
        operations.push(Reference {
            operation_id: id.into(),
            binding_id: binding.into(),
        });
        atomic(
            dir,
            &dir.join("bridge-operations.json"),
            &serde_json::to_vec(&Document {
                format_version: 1,
                operations,
            })
            .map_err(|_| Error::Storage)?,
        )
    }
}
