
- **Interpreter set is what the host actually has.** 3.11.14 / 3.12.16 / 3.13.3 executed; 3.14 has
  no interpreter and is not downloaded, so every 3.14 cell stays `BLOCKED`. The presence of
  `unicode16.json` in #2735 is not evidence that 3.14 executes, and no table is treated as a
  substitute for an interpreter.
- **The single cross-version divergence is a UCD assignment gap, not a probe artifact.**
  `ucd_1e4ec_combining` differs only on 3.11 because U+1E4EC is `Cn` (`combining=0`) under UCD
  14.0.0 and `Mn` (`combining=232`) from UCD 15.0.0, which changes NFC composition and therefore
  term boundaries and spans. Reported as a result, not normalized away.
- **Fixture recording stays lossless under surrogates.** Error `args` are encoded with the same
  typed codec as results; converting them with `str()` wrote a raw lone surrogate into the JSON and
  made the whole recording unreadable. Dropping that column would have silently lost the A-04
  error cases.
- **Negative controls must fail on the target assertion, not on a path error.** A wrong probe path
  produced exit 2 for both controls, which the status logic would have mislabeled as red. Fixed the
  invocation and required `target_assertion_failed`, so a PASS now means the assertion fired.
