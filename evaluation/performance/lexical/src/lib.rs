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

//! rust-anything bindings with a PyString adapter for surrogate-containing input.
//! Refresh overwrites this adapter; reapply and revalidate it before using refresh output.

use pyo3::prelude::*;
use pyo3::types::PyString;

pub mod port;

fn error(message: String) -> PyErr {
    use pyo3::exceptions as e;
    let (kind, text) = message.split_once(": ").unwrap_or(("", message.as_str()));
    let text = text.to_string();
    match kind {
        "TypeError" => e::PyTypeError::new_err(text),
        "KeyError" => e::PyKeyError::new_err(text),
        "IndexError" => e::PyIndexError::new_err(text),
        "OverflowError" => e::PyOverflowError::new_err(text),
        "ZeroDivisionError" => e::PyZeroDivisionError::new_err(text),
        "AttributeError" => e::PyAttributeError::new_err(text),
        "RuntimeError" => e::PyRuntimeError::new_err(text),
        "NotImplementedError" => e::PyNotImplementedError::new_err(text),
        "LookupError" => e::PyLookupError::new_err(text),
        "ArithmeticError" => e::PyArithmeticError::new_err(text),
        "UnicodeError" => e::PyUnicodeError::new_err(text),
        "ValueError" => e::PyValueError::new_err(text),
        _ => e::PyValueError::new_err(message),
    }
}

fn analyze_text_inner(value: String) -> Result<String, String> {
    let out = port::analyze_text(value)?;
    Ok(out)
}

/// `analyze_text`.
#[pyfunction]
#[pyo3(name = "analyze_text", signature = (value))]
fn analyze_text(value: &Bound<'_, PyString>) -> PyResult<String> {
    // Surrogates are nonword separators in this str→str contract. Lossy UTF-8
    // conversion preserves their boundary role; it is not suitable for spans.
    let owned = value.to_string_lossy().into_owned();
    value
        .py()
        .detach(move || analyze_text_inner(owned))
        .map_err(error)
}

#[pymodule]
#[pyo3(name = "_native")]
fn native_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(analyze_text, m)?)?;
    Ok(())
}
