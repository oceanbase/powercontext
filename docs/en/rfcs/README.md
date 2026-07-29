# PowerContext RFCs

RFCs record substantial design proposals and the decisions made about them.

An RFC describes design intent, not released behavior or implementation progress. Use the current source and
[API reference](../modules.md) for implemented public contracts and availability.

The "RFC" (request for comments) process provides a consistent path for substantial changes so maintainers and contributors can build consensus before implementation work starts.

Many changes, including bug fixes, documentation improvements, and small internal refactors, can be implemented and reviewed through the normal GitHub pull request workflow.

Some changes are substantial enough that they should go through a design review first. The RFC process is meant to make those decisions explicit, durable, and easier to revisit.

## Which changes require an RFC?

Any substantial change or addition that would require significant design or implementation work should generally be an RFC.

Examples include:

- A new public API, integration boundary, or extension mechanism.
- A change to persisted formats, handoff semantics, or compatibility guarantees.
- The removal of a feature that has already shipped.
- A large refactor or reorganization that changes core architecture.

The final judgment of whether a change needs an RFC is left to project maintainers.

If a pull request implements a substantial feature without an RFC, maintainers may ask for an RFC before continuing review.

## Before creating an RFC

Before opening an RFC, try to validate the problem and design direction with maintainers and other contributors.

Useful preparatory steps include:

- Open a GitHub issue to describe the problem and collect early feedback.
- Share alternatives and tradeoffs before committing to one implementation path.
- Keep the initial scope narrow enough to review and implement.

## The RFC process

- Fork the [PowerContext repo](https://github.com/oceanbase/powercontext) and create a branch from `main`.
- Copy [`0000_example.md`](0000_example.md) to `0000-my-feature.md`, where `my-feature` is descriptive.
- Do not assign an RFC number before opening the pull request. The RFC number should match the pull request number.
- Submit a pull request containing the RFC document under `docs/en/rfcs/` and keep its Chinese translation in sync.
- After the pull request is open, rename the `0000-` prefix to the pull request number.
- Build consensus and integrate feedback through normal pull request review.
- Make revisions as additional commits so reviewers can follow the design history.
- After merge, keep the RFC as a durable design record.
- If a dedicated implementation issue already exists, it may be linked from the RFC as an optional `Tracking Issue`.

## Implementing an RFC

A merged RFC does not imply implementation priority, assignment, or completion.

The RFC author is encouraged, but not required, to implement the design.

The RFC process does not require a tracking issue. Do not create one solely to advance an RFC or record its progress.

If the design needs material changes, submit a follow-up pull request or a new RFC rather than silently changing the
implementation contract.

## Tips

- Write enough detail that someone other than the RFC author can implement the design.
- Be explicit about drawbacks, alternatives, and compatibility risks.
- Keep implementation details concrete where they affect public behavior.
- Treat unresolved questions as part of the design, not as review leftovers.
