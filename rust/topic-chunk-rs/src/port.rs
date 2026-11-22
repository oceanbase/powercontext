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

//! Final ranges in Python codepoints; all classification comes from the target interpreter.
use crate::unicode_tables::{DIGIT, SPACE};

const MAX: usize = 1800;
const TARGET: usize = 1200;
const TAIL: usize = 300;
const OVERLAP: usize = 160;

#[derive(Clone, Copy, Debug)]
struct Span {
    start: usize,
    end: usize,
    forced: bool,
}

fn classified(cp: u32, table: &[(u32, u32)]) -> bool {
    let index = table.partition_point(|&(start, _)| start <= cp);
    index > 0 && cp <= table[index - 1].1
}

fn horizontal(cp: u32) -> bool {
    cp == 32 || cp == 9
}

fn trim(text: &[u32], mut start: usize, mut end: usize) -> Option<Span> {
    while start < end && classified(text[start], SPACE) {
        start += 1;
    }
    while end > start && classified(text[end - 1], SPACE) {
        end -= 1;
    }
    (start < end).then_some(Span {
        start,
        end,
        forced: false,
    })
}

fn marker(text: &[u32], start: usize) -> bool {
    let mut position = start;
    while position < text.len() && text[position] == 35 && position - start < 6 {
        position += 1;
    }
    if position > start && position < text.len() && horizontal(text[position]) {
        return true;
    }
    position = start;
    while position < text.len() && horizontal(text[position]) {
        position += 1;
    }
    if position + 1 < text.len()
        && matches!(text[position], 45 | 42 | 43)
        && text[position + 1] == 32
    {
        return true;
    }
    let digits_start = position;
    while position < text.len() && classified(text[position], DIGIT) {
        position += 1;
    }
    position > digits_start
        && position + 1 < text.len()
        && matches!(text[position], 46 | 41)
        && text[position + 1] == 32
}

fn blocks(text: &[u32]) -> Vec<usize> {
    let mut points = vec![0];
    let mut position = 0;
    while position < text.len() {
        if text[position] == 10 {
            let mut end = position + 1;
            while end < text.len() && horizontal(text[end]) {
                end += 1;
            }
            if end < text.len() && text[end] == 10 {
                while end < text.len() && text[end] == 10 {
                    end += 1;
                }
                points.push(position);
                position = end;
                continue;
            }
        }
        if (position == 0 || text[position - 1] == 10) && marker(text, position) {
            points.push(position);
        }
        position += 1;
    }
    points.push(text.len());
    points.dedup();
    points
}

fn windows(text: &[u32], start: usize, end: usize, trimmed: bool) -> Vec<Span> {
    let mut spans = Vec::new();
    let mut position = start;
    while position < end {
        let window_end = position.saturating_add(MAX).min(end);
        let span = if trimmed {
            trim(text, position, window_end)
        } else {
            Some(Span {
                start: position,
                end: window_end,
                forced: true,
            })
        };
        if let Some(mut span) = span {
            span.forced = true;
            spans.push(span);
        }
        if window_end == end {
            break;
        }
        position = window_end - OVERLAP;
    }
    spans
}

fn sentences(text: &[u32], block: Span) -> Vec<Span> {
    let mut points = vec![block.start];
    let mut position = block.start;
    while position < block.end {
        if position > block.start
            && matches!(text[position - 1], 46 | 33 | 63 | 0x3002 | 0xFF01 | 0xFF1F)
        {
            if horizontal(text[position]) {
                while position < block.end && horizontal(text[position]) {
                    position += 1;
                }
                points.push(position);
                continue;
            }
            if text[position] == 10 {
                points.push(position);
            }
        }
        position += 1;
    }
    points.push(block.end);
    points.dedup();
    let mut result = Vec::new();
    for pair in points.windows(2) {
        if let Some(span) = trim(text, pair[0], pair[1]) {
            if span.end - span.start <= MAX {
                result.push(span);
            } else {
                result.extend(windows(text, span.start, span.end, false));
            }
        }
    }
    result
}

pub fn chunk_ranges(text: &[u32]) -> Vec<(usize, usize)> {
    let mut semantic = Vec::new();
    for pair in blocks(text).windows(2) {
        if let Some(block) = trim(text, pair[0], pair[1]) {
            if block.end - block.start <= MAX {
                semantic.push(block);
            } else {
                semantic.extend(sentences(text, block));
            }
        }
    }
    let mut assembled: Vec<Span> = Vec::new();
    let mut current: Option<Span> = None;
    for span in semantic {
        if span.forced {
            if let Some(previous) = current.take() {
                assembled.push(previous);
            }
            assembled.push(span);
        } else if let Some(previous) = current {
            if span.end - previous.start <= MAX {
                let combined = Span {
                    start: previous.start,
                    end: span.end,
                    forced: false,
                };
                if combined.end - combined.start >= TARGET {
                    assembled.push(combined);
                    current = None;
                } else {
                    current = Some(combined);
                }
            } else {
                assembled.push(previous);
                current = Some(span);
            }
        } else {
            current = Some(span);
        }
    }
    if let Some(previous) = current {
        assembled.push(previous);
    }
    if assembled.len() >= 2 {
        let tail = assembled[assembled.len() - 1];
        let previous = assembled[assembled.len() - 2];
        if !tail.forced && tail.end - tail.start < TAIL && tail.end - previous.start <= MAX {
            assembled.truncate(assembled.len() - 2);
            assembled.push(Span {
                start: previous.start,
                end: tail.end,
                forced: false,
            });
        }
    }
    if assembled.len() > 200 {
        assembled = windows(text, 0, text.len(), true);
    }
    assembled
        .into_iter()
        .map(|span| (span.start, span.end))
        .collect()
}

pub fn decode(payload: &[u8]) -> Result<Vec<u32>, &'static str> {
    if !payload.len().is_multiple_of(4) {
        return Err("UTF-32LE payload length must be divisible by four");
    }
    payload
        .chunks_exact(4)
        .map(|bytes| {
            let cp = u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]);
            if cp > 0x10FFFF {
                Err("UTF-32LE codepoint exceeds 0x10FFFF")
            } else {
                Ok(cp)
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn forced_tail_and_surrogates() {
        assert_eq!(
            chunk_ranges(&vec![120; 1801]),
            vec![(0, 1800), (1640, 1801)]
        );
        assert_eq!(
            chunk_ranges(&vec![0x1F642; 3600]),
            vec![(0, 1800), (1640, 3440), (3280, 3600)]
        );
        assert_eq!(decode(&[0, 0xD8, 0, 0]), Ok(vec![0xD800]));
        assert!(decode(&[0]).is_err());
        assert!(decode(&[0, 0, 0x11, 0]).is_err());
    }

    #[test]
    fn empty_and_unicode_classification() {
        assert!(chunk_ranges(&[]).is_empty());
        assert!(chunk_ranges(&[32, 9, 10, 0x3000]).is_empty());
        assert!(classified(0x1E5F1, DIGIT));
        assert!(!classified(0xB2, DIGIT));
        assert!(!classified(0x200B, SPACE));
    }
}
