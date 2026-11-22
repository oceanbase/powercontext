# Upload CI diagnostics

Wrap the pinned upstream upload action with at most three attempts. A failed request may have finalized its archive before its response was lost. In that case, preserve the completed archive and return its original ID, URL and SHA-256 digest instead of deleting it or reporting an upload failure.

Each caller must own a unique artifact name within the workflow run, including its matrix dimensions. The recovery lookup is run-scoped; it cannot distinguish two concurrent upload owners that use the same name. This helper is for diagnostic archives, not build or release handoffs with overwrite requirements.

The workflow needs `actions: read` for recovery. The initial lookup records existing same-name IDs. If it fails, normal uploads can still succeed, but metadata recovery is disabled. After an upload failure, recovery accepts only one new same-name archive with a SHA-256 digest, positive size and an unexpired state. An archive recorded in the initial snapshot cannot turn a failed upload into success. No artifact is overwritten or deleted.

The original upload error remains in the job log. Failed attempts wait eight and twelve seconds before retrying. If no upstream attempt succeeds and no completed archive can be recovered, the composite action fails. Successful empty uploads keep the upstream `warn` / `ignore` behavior and empty outputs.

Inputs and outputs retain the corresponding `actions/upload-artifact` meanings. Normal uploads return the original action outputs. The `artifact-digest` recovery output is the hexadecimal digest, without the REST API's `sha256:` prefix.
