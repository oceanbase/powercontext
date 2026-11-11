// Copyright (c) 2026 OceanBase.
// SPDX-License-Identifier: Apache-2.0

use serde::de::{Deserialize, Deserializer, Error, MapAccess, SeqAccess, Visitor};
use std::{collections::BTreeSet, fmt};

/// Reject ambiguous objects before a Value decoder can discard their evidence.
pub(crate) fn unambiguous_value(bytes: &[u8]) -> Option<serde_json::Value> {
    let mut decoder = serde_json::Deserializer::from_slice(bytes);
    UniqueKeys::deserialize(&mut decoder).ok()?;
    decoder.end().ok()?;
    serde_json::from_slice(bytes).ok()
}

struct UniqueKeys;

impl<'de> Deserialize<'de> for UniqueKeys {
    fn deserialize<D: Deserializer<'de>>(decoder: D) -> Result<Self, D::Error> {
        decoder.deserialize_any(UniqueKeys).map(|()| UniqueKeys)
    }
}

impl<'de> Visitor<'de> for UniqueKeys {
    type Value = ();

    fn expecting(&self, formatter: &mut fmt::Formatter) -> fmt::Result {
        formatter.write_str("JSON without duplicate object keys")
    }

    fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<(), M::Error> {
        let mut keys = BTreeSet::new();
        while let Some(key) = map.next_key::<String>()? {
            if !keys.insert(key) {
                return Err(M::Error::custom("duplicate JSON key"));
            }
            map.next_value::<UniqueKeys>()?;
        }
        Ok(())
    }

    fn visit_seq<S: SeqAccess<'de>>(self, mut sequence: S) -> Result<(), S::Error> {
        while sequence.next_element::<UniqueKeys>()?.is_some() {}
        Ok(())
    }

    fn visit_bool<E: Error>(self, _: bool) -> Result<(), E> {
        Ok(())
    }
    fn visit_i64<E: Error>(self, _: i64) -> Result<(), E> {
        Ok(())
    }
    fn visit_u64<E: Error>(self, _: u64) -> Result<(), E> {
        Ok(())
    }
    fn visit_f64<E: Error>(self, _: f64) -> Result<(), E> {
        Ok(())
    }
    fn visit_str<E: Error>(self, _: &str) -> Result<(), E> {
        Ok(())
    }
    fn visit_unit<E: Error>(self) -> Result<(), E> {
        Ok(())
    }
}
