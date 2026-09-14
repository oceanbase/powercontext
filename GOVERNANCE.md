# PowerContext Governance

PowerContext is an open source project in the [OceanBase](https://github.com/oceanbase) organization. It preserves
decisions, constraints, progress, and evidence so that people and agents can continue work after a handoff.

This document describes project roles, how decisions are made, and how contributors can participate. It applies to the
PowerContext repository and project collaboration spaces managed by its maintainers. For development setup, coding
standards, and submission instructions, see the
[contributing guide](https://github.com/oceanbase/powercontext/blob/master/CONTRIBUTING.md).

## Governance principles

- Participation is open to individuals, companies, and research institutions. Contributions include code, documentation,
  tests, design discussions, user support, and tool integrations.
- Roles and permissions are based on sustained contributions, sound judgment, and constructive collaboration.
  Organizational affiliation, commercial partnerships, and sponsorship do not automatically grant authority over project
  decisions.
- Technical proposals and governance decisions are recorded in GitHub issues, pull requests, RFCs, or meeting notes,
  together with their rationale.
- Reviews consider user needs, technical quality, compatibility, ongoing maintenance costs, and the impact on existing
  users.
- The project seeks consensus through discussion.

## Governance structure and roles

The Project Management Committee (PMC) is responsible for project direction and governance. Maintainers handle ongoing
development, reviews, and releases.

| Role        | Responsibilities and permissions                                                                                                          |
| ----------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Contributor | Contributes code, documentation, and tests; reports issues; and participates in discussions, reviews, and community support.              |
| Reviewer    | Regularly reviews changes in their assigned area and may approve them. Maintainers merge approved changes.                                |
| Maintainer  | Maintains the project or assigned modules, manages issues, merges pull requests, coordinates releases, and appoints or removes Reviewers. |
| PMC         | Sets project direction, reviews governance policies, resolves major disputes, and appoints or removes Maintainers.                        |

Anyone may participate in reviews. Approving and merging changes require the appropriate permissions, with their scope
documented in the appointment record.

### PMC responsibilities

The PMC consists of core contributors with a sustained record of involvement in the project. Its responsibilities are to:

- Set development priorities based on user needs and maintenance capacity.
- Review governance policies, define module team responsibilities, and decide on Maintainer appointments and removals.
- Resolve major technical disagreements, community disputes, and appeals, and record decisions and their rationale.
- Support contributor growth and collaboration with other projects and organizations.

The PMC makes decisions collectively through discussion. PMC members may also serve as Maintainers. Permissions to merge
code and publish releases are granted separately according to each Maintainer's scope of responsibility.

### Maintainer responsibilities

- Handle issues, review code and designs, and coordinate development.
- Maintain code, tests, and documentation. Check compatibility, validate and publish releases, and document upgrade
  requirements.
- Help contributors get involved, assess Reviewer nominations, and recommend Maintainer candidates to the PMC.
- Facilitate regular discussions, record important conclusions, and refer unresolved disagreements to the PMC.

Establishing a module maintenance team requires a public proposal identifying its members and responsibilities, subject
to PMC approval. These teams handle routine work within their assigned scope. Changes affecting public interfaces,
behavior across modules, or governance follow the project's standard processes.

## Decision making

### Routine changes

Bug fixes, documentation updates, and improvements with a clear scope use the pull request process. Before merging,
changes must pass the required checks and receive approval from at least one Reviewer or Maintainer responsible for the
relevant area. Authors cannot approve their own changes. A Maintainer with the appropriate permissions merges the change.

Maintainers may require review by contributors with expertise in each affected area for changes that span modules,
affect compatibility, or carry greater risk. They should explain their reasons for declining suggestions or closing
proposals.

### Substantial technical changes

New public APIs, changes to persisted formats or handoff semantics, removal of released features, and changes to core
architecture should follow the [RFC process](https://github.com/oceanbase/powercontext/blob/master/docs/en/rfcs/README.md).

An RFC should describe the problem, objectives, proposed design, alternatives, and effects on existing users and
implementations. Once feedback has been addressed and consensus has been reached, a Maintainer records the decision and
merges the RFC. Matters affecting overall project direction and unresolved disagreements across modules must be referred
to the PMC for a decision before the RFC is merged.

A merged RFC records an accepted design. Implementation plans, priority, and release timing are determined separately.

## Contributor roles and appointments

### Becoming a Reviewer

Contributors may nominate themselves for a Reviewer role if they contribute regularly in an area, understand project
conventions, and provide reliable reviews. A Maintainer may also nominate a contributor. Nominations should describe
relevant contributions and the proposed scope of responsibility and must have the candidate's consent.

Maintainers discuss nominations and decide on Reviewer appointments. They consider the quality of contributions,
knowledge of the relevant area, and collaboration skills. The number of submissions alone does not determine eligibility.

### Becoming a Maintainer

Contributors who consistently carry out review and maintenance work, help resolve design disagreements, and consider the
project's long-term needs may nominate themselves or be nominated by a Maintainer or PMC member. Candidates should
describe the work and scope they are willing to take on.

The PMC discusses nominations and decides on Maintainer appointments. Following approval, the roster is updated,
permissions are configured, and responsibilities are assigned.

### Becoming a PMC member

Core contributors may nominate themselves or be nominated by a Maintainer or PMC member. Candidates should have a
sustained record of contributions and be able to assess technical tradeoffs for the project as a whole. They should also
have earned trust through collaboration across modules, contributor support, or community governance.

A nomination should describe the candidate's relevant contributions, governance experience, and proposed responsibilities
and must have the candidate's consent. The current PMC discusses the nomination and decides on the appointment. The
roster is updated upon approval.

## Community participation and communication

Ways to get involved include reporting bugs, improving documentation and examples, strengthening tests, reviewing
designs, and building integrations with agents and tools.

| Channel                                                                                               | Purpose                                                              |
| ----------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- |
| [GitHub Issues](https://github.com/oceanbase/powercontext/issues)                                     | Bug reports, feature requests, issue tracking, and role nominations. |
| [GitHub Pull Requests](https://github.com/oceanbase/powercontext/pulls)                               | Review of code, documentation, RFCs, and governance changes.         |
| [RFCs](https://github.com/oceanbase/powercontext/tree/master/docs/en/rfcs)                            | Design proposals and decision records.                               |
| [Meeting notes](https://github.com/oceanbase/powercontext/discussions?discussions_q=is%3Aopen+weekly) | Discussions, demos, decisions, and follow-up responsibilities.       |

Decisions reached in informal discussions or meetings should be recorded in the relevant issue, pull request, RFC, or
meeting notes and confirmed through the project's decision-making process.

Participants should respect different backgrounds and levels of experience, focus on the issues being discussed, and
avoid personal attacks, discrimination, and harassment. Anyone who disagrees with the handling of a community matter
may appeal to the PMC. Members not involved in the matter will review the appeal.

Contributors who use AI tools are responsible for understanding and verifying their submissions. They should disclose
their use of AI as required by the
[pull request template](https://github.com/oceanbase/powercontext/blob/master/.github/pull_request_template.md).

## Member roster and document maintenance

### PMC members

| GitHub account                               | Organization                              |
| -------------------------------------------- | ----------------------------------------- |
| [@yihong0618](https://github.com/yihong0618) | [Apache](https://github.com/apache)       |
| [@frostming](https://github.com/frostming)   | [PDM](https://github.com/pdm-project)     |
| [@frf12](https://github.com/frf12)           | [OceanBase](https://github.com/oceanbase) |
| [@AlexStocks](https://github.com/AlexStocks) | [Apache](https://github.com/apache)       |

### Maintainers

| GitHub account                       | Organization                              |
| ------------------------------------ | ----------------------------------------- |
| [@Teingi](https://github.com/Teingi) | [OceanBase](https://github.com/oceanbase) |
| [@PsiACE](https://github.com/PsiACE) | [OceanBase](https://github.com/oceanbase) |

### Maintaining the roster and this document

When a Reviewer appointment is approved, a Maintainer updates the roster to record the member's GitHub account, role,
scope of responsibility, and active status.

Role appointments follow the processes described above. The PMC confirms the scope of authority for each role.
Maintainers keep the roster and access permissions up to date following approved appointments or role changes.

Changes to this document are proposed through pull requests that explain their rationale and impact. The PMC reviews
and approves each change before it is merged. Discussions and decisions are recorded on GitHub.
