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

use super::Error;
use std::path::Path;

#[cfg(unix)]
pub(super) fn validate(path: &Path) -> Result<(), Error> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::symlink_metadata(path).map_err(|_| Error::Storage)?;
    if m.file_type().is_symlink() || m.uid() != unsafe { libc::getuid() } || m.mode() & 0o077 != 0 {
        return Err(Error::Storage);
    }
    Ok(())
}
#[cfg(unix)]
pub(super) fn restrict(path: &Path, directory: bool) -> Result<(), Error> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(
        path,
        std::fs::Permissions::from_mode(if directory { 0o700 } else { 0o600 }),
    )
    .map_err(|_| Error::Storage)?;
    validate(path)
}
#[cfg(windows)]
mod windows {
    use super::*;
    use std::{os::windows::ffi::OsStrExt, ptr};
    use windows_sys::Win32::{
        Foundation::{CloseHandle, LocalFree},
        Security::{Authorization::*, *},
        System::Threading::{GetCurrentProcess, OpenProcessToken},
    };
    struct Local(*mut std::ffi::c_void);
    impl Drop for Local {
        fn drop(&mut self) {
            unsafe {
                LocalFree(self.0);
            }
        }
    }
    fn token() -> Result<Vec<u64>, Error> {
        unsafe {
            let mut handle = ptr::null_mut();
            if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut handle) == 0 {
                return Err(Error::Storage);
            }
            let mut size = 0;
            GetTokenInformation(handle, TokenUser, ptr::null_mut(), 0, &mut size);
            let mut data = vec![0u64; (size as usize).div_ceil(8)];
            let ok =
                GetTokenInformation(handle, TokenUser, data.as_mut_ptr().cast(), size, &mut size);
            CloseHandle(handle);
            if ok == 0 {
                return Err(Error::Storage);
            }
            Ok(data)
        }
    }
    fn wide(path: &Path) -> Vec<u16> {
        path.as_os_str().encode_wide().chain([0]).collect()
    }
    pub(crate) fn validate(path: &Path) -> Result<(), Error> {
        let data = token()?;
        unsafe {
            let sid = (*(data.as_ptr().cast::<TOKEN_USER>())).User.Sid;
            let mut owner = ptr::null_mut();
            let mut acl = ptr::null_mut();
            let mut descriptor = ptr::null_mut();
            if GetNamedSecurityInfoW(
                wide(path).as_ptr(),
                SE_FILE_OBJECT,
                OWNER_SECURITY_INFORMATION | DACL_SECURITY_INFORMATION,
                &mut owner,
                ptr::null_mut(),
                &mut acl,
                ptr::null_mut(),
                &mut descriptor,
            ) != 0
            {
                return Err(Error::Storage);
            }
            let _owned = Local(descriptor);
            if owner.is_null() || acl.is_null() || EqualSid(owner, sid) == 0 || IsValidAcl(acl) == 0
            {
                return Err(Error::Storage);
            }
            for i in 0..(*acl).AceCount {
                let mut ace = ptr::null_mut();
                if GetAce(acl, u32::from(i), &mut ace) == 0
                    || (*(ace.cast::<ACE_HEADER>())).AceType != 0
                // ACCESS_ALLOWED_ACE_TYPE
                {
                    return Err(Error::Storage);
                }
                let allowed = ace.cast::<ACCESS_ALLOWED_ACE>();
                let who = ptr::addr_of!((*allowed).SidStart).cast_mut().cast();
                if IsValidSid(who) == 0
                    || (EqualSid(who, sid) == 0
                        && IsWellKnownSid(who, WinLocalSystemSid) == 0
                        && IsWellKnownSid(who, WinBuiltinAdministratorsSid) == 0)
                {
                    return Err(Error::Storage);
                }
            }
        }
        Ok(())
    }
    pub(crate) fn restrict(path: &Path, directory: bool) -> Result<(), Error> {
        let data = token()?;
        unsafe {
            let sid = (*(data.as_ptr().cast::<TOKEN_USER>())).User.Sid;
            let mut sid_text = ptr::null_mut();
            if ConvertSidToStringSidW(sid, &mut sid_text) == 0 {
                return Err(Error::Storage);
            }
            let _sid = Local(sid_text.cast());
            let mut len = 0;
            while *sid_text.add(len) != 0 {
                len += 1;
            }
            let owner = String::from_utf16(std::slice::from_raw_parts(sid_text, len))
                .map_err(|_| Error::Storage)?;
            let flags = if directory { "OICI" } else { "" };
            let sddl = format!("O:{owner}D:P(A;{flags};FA;;;{owner})")
                .encode_utf16()
                .chain([0])
                .collect::<Vec<_>>();
            let mut descriptor = ptr::null_mut();
            if ConvertStringSecurityDescriptorToSecurityDescriptorW(
                sddl.as_ptr(),
                1,
                &mut descriptor,
                ptr::null_mut(),
            ) == 0
            {
                return Err(Error::Storage);
            }
            let _descriptor = Local(descriptor);
            if SetFileSecurityW(
                wide(path).as_ptr(),
                OWNER_SECURITY_INFORMATION
                    | DACL_SECURITY_INFORMATION
                    | PROTECTED_DACL_SECURITY_INFORMATION,
                descriptor,
            ) == 0
            {
                return Err(Error::Storage);
            }
        }
        validate(path)
    }
}
#[cfg(windows)]
pub(super) use windows::{restrict, validate};
#[cfg(not(any(unix, windows)))]
pub(super) fn validate(_: &Path) -> Result<(), Error> {
    Err(Error::UnsupportedPlatform)
}
#[cfg(not(any(unix, windows)))]
pub(super) fn restrict(_: &Path, _: bool) -> Result<(), Error> {
    Err(Error::UnsupportedPlatform)
}
