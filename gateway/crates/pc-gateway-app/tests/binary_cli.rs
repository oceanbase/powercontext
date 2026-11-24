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

//! Informational arguments must work without Core or configuration.

use std::process::Command;

#[test]
fn version_and_help_do_not_start_the_gateway() {
    for argument in ["--version", "-V", "--help", "-h"] {
        let output = Command::new(env!("CARGO_BIN_EXE_pc-gateway"))
            .arg(argument)
            .env("PC_GATEWAY_LISTEN", "invalid-address")
            .env("PC_GATEWAY_CORE_BASE", "http://127.0.0.1:1")
            .output()
            .unwrap();
        assert!(output.status.success(), "{argument}");
        let text = String::from_utf8(output.stdout).unwrap();
        if argument == "--version" || argument == "-V" {
            assert!(text.starts_with("pc-gateway "));
            assert!(text.contains(
                option_env!("PC_GATEWAY_BUILD_VERSION").unwrap_or(env!("CARGO_PKG_VERSION"))
            ));
            assert!(text.contains(option_env!("PC_GATEWAY_BUILD_COMMIT").unwrap_or("unknown")));
        } else {
            assert!(text.contains("Usage: pc-gateway"));
            assert!(text.contains("PC_GATEWAY_*"));
        }
    }
}
