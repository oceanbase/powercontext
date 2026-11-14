# Claude Code CLI Handoff adapter

The default macOS Host offers preflight for Claude Code CLI `2.1.323`. It checks the registered handler and
the recorded Terminal.app or Ghostty preference, uses the default local Claude profile, and limits package reads
to the current Host's delivery directory. The deferred factory discovers the existing PowerContext CLI as needed.
Without that executable, an already configured receiver can pass preflight, but a missing or disabled integration
requires manual setup.

The standalone `createClaudeCodeCliAdapter()` and `createDefaultClaudeCodeCliAdapter()` still supply an
unqualified entry. Use the awaitable `createClaudeCodeCliTarget` for explicit configuration or pass a
constructed shared integration service to `createClaudeCodeCliAdapter`.

For native launch, supply the existing receiver prompt builder, the shared
Claude integration service, the receiver from `createClaudeCodeCliReceiver`, and
real qualification evidence for the exact CLI version. An evidence string is
not itself an acceptance test; supply it only after the corresponding version
and workflow have been verified.

The shared integration service's configuration key identifies the actual Claude
configuration directory and installer write domain, including `CLAUDE_CONFIG_DIR`
when applicable. When the default profile has no `CLAUDE_CONFIG_DIR`, the
service preserves its absence: setting it changes Claude's global configuration
file. The receiver rejects a profile that differs from the observed terminal.
Its check uses the existing PowerContext Claude doctor, whose `claude plugin
list` can initialize or update Claude configuration. It is not a guaranteed
read-only operation. The installer uses the existing PowerContext Claude setup after user consent. Its
installer must coordinate both Claude targets and not settle while a setup child
can still write. The adapter does not install Claude Code itself.

The receiver uses a Host-provided persistent `generationKey`. The built-in route uses a local environment
with the normal user home and refuses unsupported terminal preferences or custom Claude permissions. For other
configurations, `launchEnvironment`, `environmentEvidence`, and `qualifiedPackageRoots` must come from the actual
handler-launched terminal and verified receiver access. The receiver checks that macOS routes `claude-cli://` to the
intended CLI runtime. It must observe the new terminal's effective working
directory, Server identity, Scope, configuration generation, new-session and
unsent-prefill behavior, and its ability to read the delivery package. The Host's
own filesystem access, a manifest entry, and the existence of a URL handler app
are insufficient evidence for these claims. The CLI runtime checks the selected
Launch Services handler and executable, the installed plugin, MCP authentication
and required tools, and resolves Scope with the installed plugin. This also
invokes `claude plugin list` and may write Claude configuration. It does not
independently prove the terminal environment or receiver sandbox. An unavailable
or uncertain observation must fail closed. The preflight service compares the observed Server,
Scope and directory against the source; the delivery service owns its existing
package and receipt identities.

The official [Claude Code deep-link documentation](https://code.claude.com/docs/en/deep-links)
describes `claude-cli://open`, `cwd`, and `q`. It states that the prompt remains
unsent until the user presses Enter, and that handler registration occurs after
an interactive session sends something or starts agent view in a trusted folder.
A noninteractive `claude -p` invocation is not a prefilled session.

For qualification on a prepared machine, record the macOS, Claude CLI, DSH,
plugin and Server versions and package identity. Confirm the actual protocol
handler and target runtime without sending a work prompt. With the user directing
native application operations, run preflight and the existing installation or
repair flow as needed; open one handoff in a distinct session and verify the
exact directory, Server and Scope, readable package and visible unsent draft.
The user may then send the draft and verify the receiver's real Continue and
exact ACK. Repeat after changing the CLI or handler version. Record failures and
unknowns rather than treating an OS open request as delivery or acceptance.
