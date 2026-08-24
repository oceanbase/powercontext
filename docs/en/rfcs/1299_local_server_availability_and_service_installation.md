- Proposal Name: local_server_availability_and_service_installation
- Start Date: 2026-08-21
- RFC PR: [oceanbase/powercontext#1299](https://github.com/oceanbase/powercontext/pull/1299)
- Tracking Issue: [oceanbase/powercontext#1298](https://github.com/oceanbase/powercontext/issues/1298)

# Summary

PowerContext will separate local Server execution from installation and deployment lifecycle. The Server CLI will
continue to provide the explicit foreground command `powercontext server run`. Agent integrations will fail open while
making Server unavailability visible, and `powercontext doctor` will explain whether the installed service registration,
native service manager, Server liveness, and Server readiness agree.

For a personal installation, an opt-in distribution-owned service-install layer will register the existing foreground
Server command with the operating system's native per-user service manager. It will not live under the Server CLI,
enable itself during setup, require administrator privileges, or create a second PowerContext supervisor. Managed
deployments will continue to use containers or administrator-managed system services. The RFC defines one contract for
Linux, macOS, and Windows while allowing the accepted implementation to be split into independently reviewed changes.

# Motivation

Agent integrations depend on a reachable PowerContext Server but do not own its process lifecycle. The normal local
entry point is intentionally foreground:

```text
powercontext server run
```

That default is inspectable and reversible, but it disappears when the terminal closes or the machine restarts. A
later agent session can continue without recall or capture because integrations fail open. Existing structured
diagnostics may be written to logs or stderr, but the user can still experience the failure as silent loss of
PowerContext behavior.

Putting login autostart directly under `powercontext server` would mix two responsibilities. The Server package owns how
to run one configured process. Installation and distribution own whether an operating system should persistently
launch that process. The distinction also separates two deployment profiles:

- A personal installation can use a native service owned by the current user.
- A managed deployment should use a container or an administrator-managed system service with deployment-specific
  configuration, credentials, health checks, and restart policy.

PowerContext needs a durable design for that boundary before implementation. It also needs diagnostics that help a
user distinguish an uninstalled service, an inactive native service, an unreachable Server, and a live but unready
Runtime.

# Guide-level explanation

## Deployment profiles

PowerContext documents three ways to run the Server.

### Interactive personal use

The existing command remains the default and keeps its current behavior:

```text
powercontext server run
```

It runs in the foreground, prints logs to the terminal, and stops on `Ctrl-C`. Installing PowerContext or an agent
integration does not create persistent operating-system state.

### Persistent personal use

A user who installed both the CLI and the ready-to-run Server role can explicitly install a per-user service:

```text
powercontext service install
powercontext service status
```

The public lifecycle command group is `powercontext service`. It is not part of `powercontext server` and manages
exactly one PowerContext-owned personal Server registration for the current operating-system user. The initial
contract has no named service profiles. Installation is off by default and does not require `root`, `SYSTEM`, or an
administrator account.

The personal service accepts only a loopback Server bind. Its endpoint comes from the local Server settings used by the
registration, not from a Client `server_url` or a remote `powercontext doctor --server-url` target. Additional local
instances remain foreground processes; shared, remotely addressed, or publicly bound Servers are managed deployments.

The native registration invokes a distribution-owned internal launcher. After a duplicate-prevention preflight, that
launcher transfers control in the same manager-owned process to the same Server runner used by the foreground entry
point; it does not remain as a second daemon or supervisor:

```text
powercontext server run
```

To remove the registration:

```text
powercontext service uninstall
```

Uninstalling the registration stops a Server instance owned by that registration and removes only artifacts created
by PowerContext. It does not terminate an unrelated foreground Server.

### Managed deployment

The personal service installer is not a production deployment manager. A managed installation uses the project's
container image or an administrator-managed `systemd` system unit, launch daemon, Windows service, or equivalent
orchestrator. PowerContext does not install those privileged resources through `powercontext service`.

Client and integration settings may continue to target such a remote Server. The personal-service commands neither
register nor mutate remote endpoints, and diagnostics must not recommend local service installation for them.

## What users see when the Server is unavailable

Agent integrations remain fail-open: failure to recall or capture does not block the host task. They must nevertheless
surface a content-free `server_unavailable` diagnostic through the host's warning or diagnostic channel. An integration
must not attempt to install, start, or restart the Server from a prompt hook.

The warning directs the user to:

```text
powercontext doctor
```

Doctor reports separate facts rather than collapsing them into one connection error:

- whether a personal service registration exists;
- whether the native service manager reports it active;
- whether the configured Server endpoint is live;
- whether the live Server is ready;
- a recovery action appropriate to the observed state.

Examples include:

```text
service_registration  ok             not_installed (optional)
server_liveness       failed         run powercontext server run, or install the personal service
server_readiness      skipped        not checked because Server liveness failed
```

and:

```text
service_registration  installed
service_manager       inactive       inspect the native user-service logs
server_liveness       failed         registered service did not become reachable
server_readiness      skipped        not checked because Server liveness failed
```

`powercontext service status` is narrower than `powercontext doctor`. It reports the one local registration, native
manager state, definition drift, local Server liveness, and log location, but it does not replace Server readiness
diagnostics. A remote diagnostic target receives liveness and readiness checks without local personal-service
correlation.

# Reference-level explanation

## Responsibility boundaries

The accepted design assigns one owner to each concern:

| Concern | Owner | Required behavior |
| --- | --- | --- |
| Construct and run the ASGI process | Server role | Keep `powercontext server run` foreground and independently usable |
| Recall and capture during an agent task | Integration | Fail open and surface Server unavailability without owning lifecycle |
| Diagnose an installed environment | CLI diagnostics | Correlate registration, manager state, liveness, and readiness |
| Register a personal background process | Distribution/service-install layer | Manage one native per-user artifact and invoke the internal launcher |
| Run a managed deployment | Operator or orchestrator | Use containers or administrator-managed system services |

No integration imports a platform service adapter. No platform service adapter belongs to
`src/powercontext/server` or changes Server application startup. The distribution layer exposes its user contract
through the top-level `powercontext service` command group, but command placement does not transfer ownership to the
Server role.

The command provider is available only when the CLI and ready-to-run Server role are installed together, matching the
documented `powercontext[cli,server]` personal installation. A client-only installation does not expose local service
lifecycle commands.

## CLI contract

The initial distribution contract is:

```text
powercontext service install
powercontext service uninstall
powercontext service status
```

The commands apply only to the current user's single personal service. There are no named profiles and no `--system`,
`--machine`, `--root`, or administrator installation modes. They derive their endpoint from local `ServerSettings`;
the root Client `--server-url` option and `ClientSettings.server_url` never select the service target.

### Install

Install performs these steps:

1. Confirm that the ready-to-run Server role and a qualified native user-service adapter are available.
2. Load local Server settings, derive the intended endpoint, and reject a non-loopback bind.
3. Resolve an absolute, non-shell command for the distribution-owned internal launcher.
4. Probe the intended endpoint. A valid PowerContext liveness response suppresses immediate startup; an occupied
   endpoint with an invalid response is a conflict and fails before native state changes.
5. Render and validate an artifact containing the fixed ownership marker, package version, definition version, intended
   endpoint, and launcher command.
6. Create or update only PowerContext's personal Server registration and enable it for future user logins.
7. Start it immediately by default unless step 4 found an already-live PowerContext Server.
8. Report registration, definition, native manager, liveness, and log-location facts after the operation.

Repeated installation with the same desired definition is successful and makes no semantic change. Installation with
a stale PowerContext-owned definition reconciles it atomically where the native manager permits. If the manager-owned
service is active and the executable or definition changed, reconciliation performs a controlled restart. It never
terminates a live foreground Server merely because it occupies the intended endpoint.

Preflight, artifact replacement, and enablement form the registration transaction. Failure before that transaction
commits rolls back a newly written PowerContext artifact. Immediate start occurs after commit: if startup fails, the
valid enabled registration remains installed, the command exits nonzero, and its output reports the manager failure,
unreachable endpoint, and log location. Re-running `powercontext service install` reconciles and retries that state.
The command never overwrites a foreign unit, task, or launch agent with a similar display name.

The internal launcher repeats the endpoint preflight on every native start, including after login. It exits successfully
without spawning when the intended endpoint already serves the PowerContext liveness contract, reports a conflict when
the address is occupied by something else, and otherwise transfers control to the runner used by
`powercontext server run`. This launcher is a one-shot guard, not a long-running supervisor.

### Uninstall

Uninstall first asks the native manager to stop a manager-owned process. If that stop fails, it retains the
registration and exits nonzero rather than removing ownership metadata while the process may still run. After a
successful stop, it disables and removes the verified PowerContext registration. It does not kill a process solely
because it listens on the intended port. Repeated uninstall when no registration exists succeeds.

If removal is incomplete, the command reports the remaining artifact and a native recovery command. It must never
broaden cleanup to a directory, an arbitrary task name, or an unverified process identifier.

### Status

Status is read-only and has human-readable and JSON output. Its stable state model contains:

```text
support: supported | unsupported
registration: installed | not_installed | invalid | unknown
definition: current | stale | missing_executable | unknown
manager: active | inactive | failed | unknown
server_liveness: live | unreachable | unknown
log_location: <native journal selector or per-user path> | unavailable
```

Registration is the state of the exact PowerContext-owned native artifact. Definition compares its recorded executable,
package version, and definition version with the current distribution. Manager state comes from the native manager.
Liveness probes the loopback endpoint recorded for that registration. These values remain independent: a foreground
Server can be live while registration is absent, and a registration can exist while the Server is unreachable.

Human and JSON output carry the same facts and recovery action. Exit status is zero only when support is available, the
registration is installed, its definition is current, the manager is active, and the Server is live; every other
combination exits nonzero without hiding the individual facts. Output must not include credentials, complete process
environments, or unrelated native-service metadata.

## Native personal-service adapters

The following mappings are normative adapter designs, not a claim that every release supports every platform. An
adapter reports `supported` only after it ships and passes base CLI/Server smoke tests plus native lifecycle tests on a
matching operating-system runner. Until then it reports `unsupported`.

Every qualified adapter registers the current user, invokes the same absolute internal launcher without a shell, uses
one fixed project-owned native identifier, and exposes a concrete log location through `powercontext service status`
and `powercontext doctor`.

### Linux

The Linux adapter is `systemd --user`. It owns a unit under the user's systemd configuration directory and uses the
user service manager for enable, start, stop, status, and logs. Logs use the user journal, and status returns the exact
journal selector. The adapter never writes under `/etc/systemd/system` and never enables linger. A Linux environment
without an available user `systemd` manager reports `unsupported`; it does not silently fall back to a shell startup
file or desktop-specific autostart entry.

### macOS

The macOS adapter is a per-user `LaunchAgent` under the user's `Library/LaunchAgents` directory. It uses `launchd`'s
current-user domain, configures explicit PowerContext-owned per-user stdout and stderr paths, and never creates a
`LaunchDaemon` or privileged helper.

### Windows

The Windows adapter is a `Task Scheduler` task triggered when the current user logs on. It runs as that user and never
as `SYSTEM`. A hidden process window is acceptable. The launcher redirects Server output to explicit
PowerContext-owned per-user log files because Task Scheduler history is not Server stdout or stderr. The adapter does
not install a Windows Service.

Native identifiers and paths are fixed project constants for the one-per-user service. Each artifact contains a stable
ownership marker and definition version so status and uninstall can distinguish a PowerContext-owned definition from
a foreign resource across compatible package renames. Exact platform strings are adapter constants covered by
rendering and ownership tests.

## Configuration and credentials

The service installer records the executable, required arguments, and non-secret service metadata. It does not copy
the caller's complete environment, shell profile, API keys, bearer tokens, or provider credentials into a native
registration artifact.

The initial personal-service profile therefore relies on configuration available to the native user-service
environment. `powercontext service status` and `powercontext doctor` may report observable configuration divergence,
but they do not read or echo secrets. Deployments requiring credential injection or environment management beyond the
native user-service contract remain operator-managed.

A portable credential store, environment snapshot, or cross-platform secret-file format is not introduced by this
RFC. File-backed settings and secrets remain a separate concern handled through the project's existing
`pydantic-settings` direction; they are not a general-availability condition for this service lifecycle proposal.

## Integration availability signal

Each integration already has a host-specific execution model, so the display mechanism is adapter-specific. The
common semantic contract is:

- transport failure, timeout, or HTTP 503 maps to `server_unavailable`;
- recall and capture remain independently fail-open;
- the diagnostic contains no prompt, recalled content, token, or credential;
- the host task proceeds without injected PowerContext content;
- the integration provides a discoverable path to `powercontext doctor`;
- repeated unavailability uses a host-appropriate bounded or deduplicated presentation instead of warning on every
  prompt forever;
- the hook never starts or installs a Server.

Authentication failure, version mismatch, invalid response, an empty successful result, and Server unavailability
remain distinct outcomes. Each supported integration must demonstrate in an acceptance test or recorded host fixture
that its selected channel is visible to the user. Writing structured JSON to stderr satisfies this requirement only
when that host actually exposes stderr. Implementations retain content-free structured diagnostics for troubleshooting;
the exact visible channel and deduplication mechanism remain host-adapter choices.

## Doctor diagnostics

`powercontext doctor` remains the authoritative installed-environment diagnostic. It keeps the existing
`ok | degraded | failed | skipped` vocabulary and adds the following local service checks for a loopback target when
no registration exists, or when the target matches the endpoint recorded by an existing registration:

| Check | Meaning |
| --- | --- |
| `service_support` | A qualified native personal-service adapter is available |
| `service_registration` | The exact PowerContext-owned registration state |
| `service_definition` | The executable, package version, and definition version match the current distribution |
| `service_manager` | The native manager's state for that registration |
| `server_liveness` | The selected endpoint answers the existing liveness contract |
| `server_readiness` | The live Server reports ready, degraded, or not ready |

Doctor does not infer registration from an open port and does not infer liveness from a manager process identifier.
When facts disagree, its detail names the disagreement and gives the next safe action. JSON diagnostics preserve the
same check names and status vocabulary used by human output.

Personal service installation is optional, so `unsupported` and `not_installed` are advisory when no registration
exists and do not make the overall doctor result nonzero. The human and JSON detail still expose those facts; for
example, `service_registration` may be `ok` with detail `not_installed (optional)`. Definition and manager checks are
omitted until a registration exists. Once a PowerContext registration exists, an invalid or stale definition, native
manager failure, or unreachable intended Server contributes `degraded` or `failed` according to the existing
diagnostic rules.

For a remote target, or a loopback target different from the registered endpoint, doctor performs endpoint liveness
and readiness diagnostics without local registration or manager correlation. It must not recommend
`powercontext service install` for a remote or operator-managed Server.

## Upgrade and executable drift

The registration points to the resolved absolute internal launcher rather than a shell alias and records the package
and definition versions. Status validates that the executable still exists and reports `stale` or
`missing_executable` when the installed distribution no longer matches.

Updating the Python distribution does not silently rewrite operating-system state. Running
`powercontext service install` again reconciles the registration with the current distribution and performs a
controlled restart only when an active manager-owned process must adopt the new definition. Documentation includes
that reconciliation step until the distribution has a transactional upgrade hook.

## Failure handling and observability

Native start failures remain visible in the user journal on Linux and in explicit per-user log files on macOS and
Windows. `powercontext service status` and `powercontext doctor` return the relevant selector or path. Platform
adapters return structured failures without including secret environment values.

Native managers restart only after unexpected failure, never after the launcher's clean already-live exit. Restart
attempts use a finite manager-specific policy with delay or backoff so a persistent configuration or address conflict
cannot become an unbounded rapid loop. Exact rendered restart conditions and limits are tested per adapter.

## Security and compatibility

- Personal service installation is opt-in and per-user.
- Setup commands never enable it implicitly.
- No operation requests privilege elevation.
- Exactly one personal registration exists per operating-system user; named profiles are out of initial scope.
- The personal-service installer accepts only a loopback bind and rejects public or remote service targets.
- Client and integration remote endpoint settings never select a local service registration.
- The feature introduces no new HTTP, MCP, persistence, or authentication contract.
- `powercontext server run` retains its current foreground behavior.
- Uninstall removes only verified PowerContext-owned artifacts.
- Diagnostics and logs never expose credentials or captured content.

## Delivery and testing

Acceptance of this RFC does not require one implementation pull request. The tracking issue may split work into:

1. host-visible integration diagnostics;
2. correlated doctor diagnostics and the shared service state model;
3. the distribution-owned CLI and adapter protocol;
4. Linux, macOS, and Windows adapters;
5. documentation and platform integration tests.

The shared state model and security rules apply to every platform adapter. Documentation and
`powercontext service status` state the actual support in a release rather than implying that an adapter exists before
it ships. Adding Windows personal-service adapter support also requires the project's base install and Server smoke
tests to pass on Windows; the RFC alone does not expand the README's current macOS/Linux support claim.

Pure tests cover definition rendering, ownership verification, loopback validation, idempotent reconciliation,
transaction rollback, post-commit start failure, stop-before-remove behavior, executable drift, status exit semantics,
redaction, log-location output, and every internal-launcher preflight outcome. Platform tests run only on a matching
operating system and verify native register, query, start, stop, and remove behavior. CI need not simulate an
interactive login, but it asserts the native login trigger, exact launcher command, and bounded restart policy.

Integration tests verify that unavailable, authentication failure, version mismatch, invalid response, and empty
success remain distinct, content-free, fail-open outcomes. Per-host acceptance evidence verifies actual warning
visibility and bounded repetition. Doctor tests cover optional uninstalled state, local registration correlation,
remote endpoint behavior, and every fact combination that produces a different recovery action. A platform is
advertised as supported only while these base and native tests run on a matching maintained runner.

# Drawbacks

- A distribution layer with three native adapters is more code and operational surface than a foreground command.
- Native user-service environments differ, especially in how they receive configuration and credentials.
- Host-visible warnings can become noisy if a Server stays unavailable; integrations need host-appropriate
  presentation without hiding the condition.
- A split implementation can temporarily produce different platform support across releases.
- A top-level service command adds CLI vocabulary for a lifecycle that some users will never need.
- Personal service installation does not solve managed deployment, remote access, multi-user isolation, or production
  secret management.

# Rationale and alternatives

## Put autostart under the Server CLI

Commands such as `powercontext server autostart enable` are discoverable beside `powercontext server run`, but they
make the Server role own installation and operating-system persistence. This RFC keeps process construction and
service installation separate.

## Start the Server from an integration hook

This removes a setup step but makes short-lived, latency-sensitive hooks own process lifecycle. Concurrent hosts can
race, cold startup can delay a prompt, and credentials or configuration may not match. Hooks remain consumers that
surface unavailability.

## Enable a service during setup

Implicit installation surprises users with persistent processes and operating-system state. Setup continues to install
integrations and recommend explicit next steps; service installation remains opt-in.

## Publish manual recipes only

Manual `systemd`, `launchd`, and `Task Scheduler` instructions avoid adapter code but drift across releases and
provide no shared status, ownership, uninstall, or doctor contract. Native managers remain the mechanism, but
PowerContext owns the reversible personal registration.

## Install a privileged system service everywhere

Machine-wide services start outside the user's normal trust and configuration boundary and require elevation.
Personal installation does not need that authority. Managed operators can still define privileged services
explicitly.

## Build a PowerContext supervisor

A cross-platform supervisor would duplicate native restart, logging, and lifecycle facilities. The service-install
layer registers the one-shot preflight launcher and does not supervise the Server itself.

## Require one pull request for all platforms

One change avoids temporary support differences but couples independent native integrations and makes review and
rollback harder. The RFC fixes the shared contract; the tracking issue can split implementation while documentation
reports actual availability.

## Make no change

Users can keep a terminal open or create their own service definitions, but unavailable integrations can remain
confusing and personal registrations will continue to lack a supported diagnostic and uninstall path.

# Prior art

`systemd` user services, macOS `LaunchAgent`s, and per-user `Task Scheduler` tasks provide native login-session
lifecycle, logging, and status without a project-specific supervisor. Developer tools commonly keep an interactive
foreground command while offering a separate install or service command for persistent personal use.

Containers and administrator-managed services are established deployment boundaries for managed workloads because
they make identity, configuration, credentials, restart policy, and observability explicit. This RFC applies that
separation to PowerContext instead of treating every local or managed Server as the same autostart problem.

# Unresolved questions

None at the normative contract level. Exact native identifier strings and host-visible warning mechanisms remain
adapter implementation details, but each must be fixed in code and proven by the acceptance evidence required above
before that adapter is advertised as supported. Managed deployment automation, public binding, remote multi-user
service profiles, privileged installation, and a portable secret store remain out of scope.

# Future possibilities

- Add distribution-specific container manifests or administrator templates without changing the personal service
  contract.
- Add named personal service profiles if one-per-user operation proves insufficient.
- Add a stable non-secret Server configuration file and platform credential integrations in separate proposals.
- Reconcile a personal service registration during a transactional package upgrade.
- Add native desktop status surfaces after the CLI and diagnostic contracts are stable.
- Extend doctor with bounded log excerpts when they can be collected without exposing secrets.
