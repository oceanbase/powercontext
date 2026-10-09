# Privacy

The plugin sends explicitly supplied tool parameters and the configured Scope to the administrator-selected PowerContext Server over HTTP/HTTPS. Parameters can include queries, Memory text, explicit Source content/metadata, evidence references, Handoff objects and candidate identifiers. It also sends the configured bearer token in the Authorization header for authentication.

The plugin has no independent data store or telemetry endpoint. It does not automatically capture conversations, files, environment variables, credentials or tool traces. Dify controls credential storage and application/run logs; PowerContext controls retained Sources, Memory, Artifacts, access auditing and deletion. Generated content may use the model providers configured on the PowerContext Server.

One provider credential represents a shared fixed Scope. All users authorized to use that credential can operate within that Scope subject to Server policy. No per-user or per-conversation isolation is inferred from Dify runtime fields.

Only capture content you intend to retain. Remove credentials and private information before invoking tools. Capture rejects some known secret patterns and secret-bearing JSON keys, including nested metadata, but that check is best effort and is not comprehensive redaction. Complete Server responses become Dify tool outputs and may be visible to the application's model and run logs.

HTTPS certificate verification is enabled. Non-loopback plain HTTP requires an explicit administrator opt-in. Token values, raw error bodies and transport diagnostics are excluded from plugin error outputs. Contact the deployment owner for retention or access concerns; plugin source and issue reporting are available at [oceanbase/powercontext](https://github.com/oceanbase/powercontext).
