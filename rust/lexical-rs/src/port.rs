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

//! Unicode16 / Jieba0.42.1-compatible terms; computation has no Python fallback.

#![forbid(unsafe_code)]

use std::collections::HashMap;
use std::fmt::Write;
use std::sync::OnceLock;

use jieba_rs::Jieba;
use serde::Deserialize;
use sha2::{Digest, Sha256};
use unicode_normalization::UnicodeNormalization;

#[derive(Deserialize)]
struct Compatibility {
    unicode: String,
    casefold: HashMap<u32, String>,
    word_ranges: Vec<(u32, u32)>,
    frequency_correction: usize,
}

struct Analyzer {
    compatibility: Compatibility,
    tokenizer: OnceLock<Jieba>,
}

static ANALYZER: OnceLock<Result<Analyzer, String>> = OnceLock::new();

fn analyzer() -> Result<&'static Analyzer, String> {
    ANALYZER
        .get_or_init(|| {
            let compatibility: Compatibility =
                serde_json::from_str(include_str!("../data/unicode16.json"))
                    .map_err(|_| "RuntimeError: invalid analyzer data".to_owned())?;
            if compatibility.unicode != "16.0.0"
                || unicode_normalization::UNICODE_VERSION != (16, 0, 0)
            {
                return Err("RuntimeError: incompatible Unicode tables".into());
            }
            Ok(Analyzer {
                compatibility,
                tokenizer: OnceLock::new(),
            })
        })
        .as_ref()
        .map_err(Clone::clone)
}

fn is_han(c: char) -> bool {
    matches!(c as u32, 0x3400..=0x4dbf | 0x4e00..=0x9fff | 0xf900..=0xfaff | 0x20000..=0x2fa1f)
}

impl Analyzer {
    fn word(&self, c: char) -> bool {
        if c.is_ascii() {
            return c.is_ascii_alphanumeric() || c == '_';
        }
        let number = c as u32;
        let ranges = &self.compatibility.word_ranges;
        let index = ranges.partition_point(|range| range.1 < number);
        ranges.get(index).is_some_and(|range| range.0 <= number)
    }

    fn normalize(&self, value: &str) -> String {
        if value.is_ascii() {
            return value.to_ascii_lowercase();
        }
        let mut normalized = String::with_capacity(value.len());
        for c in value.nfc() {
            if let Some(folded) = self.compatibility.casefold.get(&(c as u32)) {
                normalized.push_str(folded);
            } else {
                normalized.push(c);
            }
        }
        normalized
    }

    fn terms(&self, run: &str, han: bool, out: &mut Vec<String>) {
        if han {
            let tokenizer = self.tokenizer.get_or_init(|| {
                let mut tokenizer = Jieba::new();
                // Python counts a duplicate B超 row in its total. NUL cannot be in
                // these Han-only runs, so this restores weight without a new match.
                tokenizer.add_word("\0", Some(self.compatibility.frequency_correction), None);
                tokenizer
            });
            out.extend(
                tokenizer
                    .cut(run, false)
                    .into_iter()
                    .map(|token| encode(token.word)),
            );
            return;
        }
        let chars: Vec<char> = run.chars().collect();
        let mut index = 0;
        while index < chars.len() {
            if !self.word(chars[index]) {
                index += 1;
                continue;
            }
            let start = index;
            index += 1;
            while index < chars.len() {
                if self.word(chars[index]) {
                    index += 1;
                } else if matches!(chars[index], '\'' | '’')
                    && chars.get(index + 1).is_some_and(|c| self.word(*c))
                {
                    index += 2;
                } else {
                    break;
                }
            }
            let term: String = chars[start..index]
                .iter()
                .map(|&c| if c == '’' { '\'' } else { c })
                .collect();
            out.push(encode(&term));
        }
    }
}

fn hex(bytes: &[u8]) -> String {
    let mut result = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        // Writing into String cannot fail; keep a fallible formatting API out of callers.
        let _ = write!(result, "{byte:02x}");
    }
    result
}

fn encode(term: &str) -> String {
    if (3..=80).contains(&term.len())
        && term.bytes().all(|b| b.is_ascii_alphanumeric())
        && !term.starts_with("pcu")
        && !term.starts_with("pch")
    {
        return term.to_owned();
    }
    if term.len() <= 40 {
        format!("pcu{}", hex(term.as_bytes()))
    } else {
        format!("pch{}", hex(&Sha256::digest(term.as_bytes())))
    }
}

/// Preserve Python document word order, frequency and backend-safe term encoding.
pub fn analyze_text(value: String) -> Result<String, String> {
    let analyzer = analyzer()?;
    let normalized = analyzer.normalize(&value);
    let mut terms = Vec::new();
    let mut start = 0;
    let mut han = normalized.chars().next().is_some_and(is_han);
    for (index, c) in normalized.char_indices() {
        if is_han(c) != han {
            analyzer.terms(&normalized[start..index], han, &mut terms);
            start = index;
            han = !han;
        }
    }
    analyzer.terms(&normalized[start..], han, &mut terms);
    Ok(terms.join(" "))
}

#[cfg(test)]
mod tests {
    use super::analyze_text;

    #[test]
    fn preserves_word_encoding_and_frequency() {
        assert_eq!(analyze_text(String::new()), Ok(String::new()));
        assert_eq!(
            analyze_text("ABC ab ABC".into()),
            Ok("abc pcu6162 abc".into())
        );
        assert_eq!(
            analyze_text("pcu pch _".into()),
            Ok("pcu706375 pcu706368 pcu5f".into())
        );
    }

    #[test]
    fn applies_nfc_before_casefold_and_python_word_boundaries() {
        assert_eq!(
            analyze_text("ǰ a\u{200d}b".into()),
            Ok("pcu6a pcu61 pcu62".into())
        );
        assert_eq!(
            analyze_text("don't can’t".into()),
            Ok("pcu646f6e2774 pcu63616e2774".into())
        );
    }
}
