// Copyright (c) 2026 OceanBase.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

//! Native lexical analysis for the default PowerContext runtime.

use pyo3::prelude::*;
use pyo3::types::PyString;
use std::sync::OnceLock;

pub mod port;
static UNICODE: OnceLock<String> = OnceLock::new();

#[pyfunction]
fn analyze_text(value: &Bound<'_, PyString>) -> PyResult<String> {
    // Surrogates are nonword separators in this projection-only contract.
    // Lossy conversion preserves that boundary but must not be used for offsets.
    let owned = value.to_string_lossy().into_owned();
    let unicode = UNICODE
        .get()
        .expect("module initialization selected Unicode tables");
    value
        .py()
        .detach(move || port::analyze_text(owned, unicode))
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pymodule]
fn _lexical(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let unicode: String = m
        .py()
        .import("unicodedata")?
        .getattr("unidata_version")?
        .extract()?;
    port::check_unicode(&unicode).map_err(pyo3::exceptions::PyImportError::new_err)?;
    let _ = UNICODE.set(unicode);
    m.add_function(wrap_pyfunction!(analyze_text, m)?)?;
    Ok(())
}
