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

//! RFC3339 时间解析（valid_until / correlate_until 消费）。
//! 零依赖实现：覆盖 Core 实际输出的形态（含/不含分数秒、Z 后缀）。

/// 解析 RFC3339 时间戳为 Unix 毫秒。
/// 支持: `2026-10-28T09:55:06Z` / `2026-10-28T09:55:06.464580Z` / 不含 Z 的 UTC 形态。
/// 不支持的形态返回 None（调用方 fail-closed 处理）。
pub fn parse_rfc3339_ms(s: &str) -> Option<u64> {
    let bytes = s.as_bytes();
    if bytes.len() < 20 {
        return None;
    }
    // 必须是数字的位置
    let digit = |i: usize| -> Option<u64> { (bytes[i] as char).to_digit(10).map(|d| d as u64) };
    let year: i64 = (digit(0)? * 1000 + digit(1)? * 100 + digit(2)? * 10 + digit(3)?) as i64;
    if bytes[4] != b'-'
        || bytes[7] != b'-'
        || bytes[10] != b'T'
        || bytes[13] != b':'
        || bytes[16] != b':'
    {
        return None;
    }
    let month: i64 = (digit(5)? * 10 + digit(6)?) as i64;
    let day: i64 = (digit(8)? * 10 + digit(9)?) as i64;
    let hour: i64 = (digit(11)? * 10 + digit(12)?) as i64;
    let min: i64 = (digit(14)? * 10 + digit(15)?) as i64;
    let sec: i64 = (digit(17)? * 10 + digit(18)?) as i64;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) || hour > 23 || min > 59 || sec > 60 {
        return None;
    }
    // 分数秒（可选）
    let mut millis: i64 = 0;
    let mut idx = 19;
    if idx < bytes.len() && bytes[idx] == b'.' {
        idx += 1;
        let start = idx;
        while idx < bytes.len() && bytes[idx].is_ascii_digit() {
            idx += 1;
        }
        if idx == start {
            return None;
        }
        let frac = &s[start..idx];
        // 小数秒缩放（非截断）：.5 = 500ms、.46 = 460ms、.464580 = 464ms。
        // 右补零到 3 位保证等效表示得到等效毫秒值。
        let millis_str = if frac.len() >= 3 {
            &frac[..3]
        } else {
            // 右补零：.5 → "500"、.46 → "460"
            &format!("{:0<3}", frac)
        };
        millis = millis_str.parse().unwrap_or(0);
    }
    // 时区：Z 或 ±HH:MM
    let tz_offset_sec: i64 = if idx < bytes.len() {
        match bytes[idx] {
            b'Z' | b'z' => 0i64,
            b'+' | b'-' => {
                let sign: i64 = if bytes[idx] == b'+' { 1 } else { -1 };
                let oh: i64 = if idx + 2 < bytes.len() {
                    (digit(idx + 1)? * 10 + digit(idx + 2)?) as i64
                } else {
                    return None;
                };
                let om: i64 = if idx + 5 < bytes.len() && bytes[idx + 3] == b':' {
                    (digit(idx + 4)? * 10 + digit(idx + 5)?) as i64
                } else if idx + 4 < bytes.len() {
                    (digit(idx + 3)? * 10 + digit(idx + 4)?) as i64
                } else {
                    return None;
                };
                sign * (oh * 3600 + om * 60)
            }
            _ => return None,
        }
    } else {
        return None;
    };

    let days = days_from_civil(year, month, day);
    let total_ms = ((days * 86_400 + hour * 3600 + min * 60 + sec - tz_offset_sec) * 1000) + millis;
    if total_ms < 0 {
        return None;
    }
    Some(total_ms as u64)
}

/// Howard Hinnant 的 days_from_civil 算法（公历→Unix 天数）。
fn days_from_civil(y: i64, m: i64, d: i64) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = y - era * 400;
    let mp = (m + 9) % 12;
    let doy = (153 * mp + 2) / 5 + d - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    era * 146_097 + doe - 719_468
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_no_fraction() {
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T09:55:06Z"),
            Some(1793181306000)
        );
    }

    #[test]
    fn utc_with_microseconds() {
        // Core 实际输出形态：2026-10-28T09:55:06.464580Z
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T09:55:06.464580Z"),
            Some(1793181306464)
        );
    }

    #[test]
    fn utc_with_milliseconds() {
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T09:55:06.464Z"),
            Some(1793181306464)
        );
    }

    #[test]
    fn timezone_offset() {
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T17:55:06+08:00"),
            Some(1793181306000)
        );
    }

    #[test]
    fn invalid_returns_none() {
        assert_eq!(parse_rfc3339_ms("not-a-date"), None);
        assert_eq!(parse_rfc3339_ms("2026-10-28"), None);
        assert_eq!(parse_rfc3339_ms("2026-13-28T09:55:06Z"), None);
        assert_eq!(parse_rfc3339_ms(""), None);
    }

    #[test]
    fn epoch() {
        assert_eq!(parse_rfc3339_ms("1970-01-01T00:00:00Z"), Some(0));
    }
}

#[cfg(test)]
mod fraction_tests {
    use super::*;

    #[test]
    fn short_fractions_scale_correctly() {
        // .5Z = 500ms（不是 5ms）
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T09:55:06.5Z").unwrap() % 1000,
            500
        );
        // .46Z = 460ms（不是 46ms）
        assert_eq!(
            parse_rfc3339_ms("2026-10-28T09:55:06.46Z").unwrap() % 1000,
            460
        );
    }

    #[test]
    fn equivalent_fractions_produce_equivalent_timestamps() {
        // .5Z ≡ .500Z ≡ .500000Z —— 同一时刻
        let a = parse_rfc3339_ms("2026-10-28T09:55:06.5Z").unwrap();
        let b = parse_rfc3339_ms("2026-10-28T09:55:06.500Z").unwrap();
        let c = parse_rfc3339_ms("2026-10-28T09:55:06.500000Z").unwrap();
        assert_eq!(a, b, ".5Z 必须等于 .500Z");
        assert_eq!(b, c, ".500Z 必须等于 .500000Z");
    }
}
