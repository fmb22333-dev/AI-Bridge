# AI Bridge — Public Learning Policy

**Status:** Normative public product rule

## Product principle

AI Bridge is expected to become more capable through validated use. Repeated successful workflows, recurring failure signatures and stable host-specific rules should be promoted into reusable knowledge so future AI agents need fewer commands, less bespoke host code and less rediscovery.

Typical progression:

`raw primitive -> repeated pattern -> candidate recipe/rule -> validation -> promoted generic knowledge`

## Knowledge targets

Reusable knowledge may become:

- typed primitive guidance;
- bounded recipe/syntax sugar;
- diagnostic/error rule;
- alias or construction template;
- host/validation heuristic.

## Lifecycle

Use explicit lifecycle metadata where practical:

`observed -> candidate -> validated -> promoted -> deprecated`

A one-off guess must not become permanent execution authority.

## Kernel versus Knowledge Pack

Stable Adapter Kernel:

- transport/session
- host-safe thread handoff
- dispatch
- checkpoints/rollback
- typed primitive operations
- dynamic knowledge loader

Hot-loadable Knowledge Pack:

- recipes
- diagnostics
- aliases
- templates
- host heuristics
- validation rules
- capability guidance

Normal knowledge updates should not require a host restart where hot-loading is supported.

## Public distribution filter

The public Knowledge Pack may contain only entries whose executable scope is generic and whose state is validated/promoted for distribution.

Exclude:

- project-family recipes/templates that have not generalized;
- candidate-only execution authority;
- private project names/node names/paths used only as evidence;
- user project authority documents;
- command/recovery history;
- credentials or machine state.

A generalized rule may survive after project-specific provenance/evidence is stripped from the distributable form.

## Failure-path learning

Failures are learning evidence. Repeated malformed arguments, host errors, unsafe retry behavior, transport races or recovery failures should harden the generic execution boundary rather than only patch a single project.

## Promotion safety

Promotion requires evidence such as regression tests, live host validation, repeated independent success, or another explicit validation contract.

Syntax sugar must remain bounded, fail closed, preserve risk and expose enough execution trace for later promotion/demotion decisions.

## Maintenance rule

Reusable discoveries must not remain only in chat history. Evaluate them for machine-readable promotion so a new AI can recover them without reproducing the original investigation.
