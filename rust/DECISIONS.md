# Lexical pilot decisions

## 2026-11-20: Independent native extension trial

Keep the root Python application and default behavior unchanged. A separate experimental PyO3 wheel tests the narrow analyze_text boundary before selecting production packaging. Gateway has a different owner and is not used as an extension workspace.

Unicode and Jieba dictionary behavior are compatibility inputs. Use explicit source assets and versions; a Rust dependency with a similar name is not evidence of equivalence. Runtime input conversion must handle Python surrogate strings or report a verified incompatibility.

## Fixed dictionary and scalar-only adaptation

The effective Jieba dictionaries match; their raw bytes differ because Python repeats B超. Rust adds frequency 3 to NUL, which cannot match a Han-only run, preserving the reference total 60101967. Unicode16 casefold and word classification are exported as data, not Python runtime callbacks. The adapted PyString entry replaces surrogate codepoints with nonword separators and detaches Rust computation from the GIL; this is limited to analyze_text without spans.
