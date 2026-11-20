# Migration status

| Unit | Technical evidence | Independent final verification | Reference | Default integration |
| --- | --- | --- | --- | --- |
| analyze_text | PASS for bounded Windows CPython3.14/Unicode16 pilot | Pending; final independent review has not completed | 5b557788c33b57b8ef55a3f3446ee72d693270a4 | Not enabled |

Tool diff: 2033/2033. Strict unique inputs: N=2645, R=2645, F=0, E=0; 2648 executions include three repeated independent expectations. Existing tests in native process: 13 passed, 20 native calls. Tool gate passed; independent wheel installed and strictly verified in a clean environment.

Warm offline single-thread medians including bridge: Chinese documents 8.85x, English documents 3.88x (final rerun; raw initial and final samples retained); no full request-chain speedup claim. Other Python/Unicode versions, platforms and production packaging are unverified.

Published measurement samples, source identities, limits and reproduction instructions: [results](lexical-rs/results/README.md). Additional task-local logs are generated in `output/lexical-pilot/` and are not required to read the published report. This is a technically validated local trial; independent acceptance, default integration, CI, merge and release approval remain separate.
