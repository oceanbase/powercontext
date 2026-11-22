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

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::sync::atomic::{AtomicU64, Ordering};

mod port;
mod unicode_tables;

static COMPLETED: AtomicU64 = AtomicU64::new(0);

#[pyfunction]
fn chunk_ranges(payload: &[u8]) -> PyResult<Vec<(usize, usize)>> {
    let text = port::decode(payload).map_err(PyValueError::new_err)?;
    let ranges = port::chunk_ranges(&text);
    COMPLETED.fetch_add(1, Ordering::Relaxed);
    Ok(ranges)
}

#[pyfunction]
fn execution_stats() -> u64 {
    COMPLETED.load(Ordering::Relaxed)
}

#[pyfunction]
fn build_identity() -> &'static str {
    "PC-RUST-2740:markdown-v1:utf32le"
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chunk_ranges, m)?)?;
    m.add_function(wrap_pyfunction!(execution_stats, m)?)?;
    m.add_function(wrap_pyfunction!(build_identity, m)?)?;
    Ok(())
}
