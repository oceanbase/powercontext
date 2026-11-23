# Python dependencies in CI

The shared Python and uv setup actions enable the Aliyun PyPI mirror on self-hosted
Linux runners. Hosted runners and local commands retain their existing package
sources. `PC_CI_PYTHON_MIRROR` explicitly opts a nested container into the same
installation policy; Docker build arguments default to empty.

Use `scripts/ci_python_mirror.py` at dependency installation boundaries. It supports
`sync`, `uv-pip`, `pip --python INTERPRETER`, `build`, dependency-resolution
`lock --dry-run`, and `hooks` (only `prek install-hooks`). For commands with temporary
dependencies, use `run UV_OPTIONS -- COMMAND`: installation is prepared separately,
then the actual command executes once. A failing test is never retried as a mirror
fallback.

During a locked installation, only official PyPI artifact download URLs are
temporarily mapped to the mirror. Package versions, hashes, markers, and registry
identities remain unchanged. The exact original lock bytes are restored before
application execution. A failed mirror installation retries the original package
source; hashes and lock validation remain enforced on both attempts. Explicit
index selections and offline installations retain their own source policy.

The existing uv download cache and internal cache backend remain in use. Cache
keys are computed from the original dependency files. Updating a lockfile changes
the cache identity and downloads the newly locked artifacts; nothing requires a
manually maintained package or version list.

Pass `sync-args` to the setup action when a job needs extra dependency groups, for
example `--extra seekdb` or `--extra tracing-otlp --group tracing-tests`. Standalone
projects and nested test environments should call the helper with their own
`--project` path. Public-PyPI release qualification and installer source-selection
tests intentionally keep their explicit sources.
