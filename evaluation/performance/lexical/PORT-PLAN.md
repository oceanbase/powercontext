# Lexical migration contract

The migration unit is `powercontext.builtin.artifacts.search.analyze_text(str) -> str`. The normal runtime uses a Rust extension distributed inside the root package. The public span/query paths remain Python because their offset contracts are separate migration units.

Preserve NFC-before-casefold, explicit Han ranges, Jieba 0.42.1 segmentation with HMM disabled, word classification, apostrophe handling, token order and frequency, reserved-prefix escaping, UTF-8 byte thresholds and SHA256 terms. Preserve `TEXT_ANALYZER_VERSION`, storage projections, schemas and query admission.

The implementation belongs beside its owning Artifact search module. Hatchling retains ownership of the root package; maturin builds the internal PyO3 extension. The crate is independent of gateway and desktop and uses their Rust 1.97.1 toolchain. Release delivery must build actual platform wheels and install them before publication, preserving the single-candidate upgrade acceptance gate.

Acceptance requires default native identity without a plugin or runtime switch, exact output comparisons on CPython 3.11–3.14, meaningful native and Python behavior tests, installed-wheel SQLite write/reopen/search, root-wheel/source-distribution content equivalence, and measured throughput including the Python/native boundary. Local Windows evidence and hosted platform CI results must be reported separately. This migration does not authorize publication, rollout or another migration unit.
