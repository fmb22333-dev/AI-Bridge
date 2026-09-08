# AI Bridge — AI Agent Protocol

**Status:** Public integration contract

## 1. Purpose

This protocol lets an authorized AI agent enter an AI Bridge installation without relying on chat memory or manual explanation from the user.

## 2. Two repository roles

AI Bridge intentionally separates:

1. **Shared product repository** — `fmb22333-dev/AI-Bridge`
   - Runtime
   - Supervisor
   - host adapters
   - installer
   - generic distributable Knowledge Pack
   - public specifications

2. **Per-install Bus repository** — created for each user during first-run setup
   - `PROJECT_STATE_INDEX.json`
   - `AI_BRIDGE_READ_FIRST.md`
   - `.ai-bridge/status/<bridge_id>.json`
   - GitHub transport resources
   - that user's project authority documents

The product repository is not authority for a user's live project/runtime state.

## 3. Mandatory AI entry sequence

When an AI is given access to a user's Bus repository:

1. Fetch `main:PROJECT_STATE_INDEX.json`.
2. Treat it as the single machine entrypoint.
3. Resolve the live runtime status pointer declared by the index.
4. Resolve the current project authority document only when a project is registered/selected.
5. Resolve normative Bridge specifications from the `runtime_source` declared by the index.
6. Use repository-wide search only if an indexed pointer is missing or invalid.

## 4. Authority order

Unless the generated index declares a newer compatible policy, use:

1. live runtime status
2. current project authority
3. normative Bridge specifications / promoted Knowledge Pack
4. historical handoff or chat memory

Static documentation must not override volatile live fields such as Runtime version, session IDs, workspaces, transport state, or write-block state.

## 5. GitHub authorization model

The AI must operate through an authorized GitHub identity, application, connector, or token. GitHub repository permissions remain the security boundary.

Never ask users to share a personal GitHub token in chat. Credentials are stored locally by Bridge setup and are not committed to either repository.

## 6. Transport discovery

Before sending a remote Bridge command, read live runtime status. Use the transport mode advertised there. Do not assume a fixed mailbox/channel protocol from static documentation.

When multi-channel transport is advertised, independent AI conversations must use independent channels and must not overwrite one another's command slot.

## 7. Command recovery

A missing chat-visible response is not permission to replay a mutation.

Before repeating a command:

- recover its durable transport state/result when possible;
- if the prior command is known accepted but non-terminal, do not duplicate it;
- if project state is stale, recover project state before mutation.

## 8. Project targeting

Never guess a project, session, workspace, host, repository, or project file when more than one plausible target exists.

Project-specific paths and state belong only in the user's Bus/project authority documents, never in the public generic Knowledge Pack.

## 9. Knowledge model

The Adapter Kernel and Knowledge Pack are separate concepts.

- Kernel: transport, session, dispatch, checkpoint/rollback, typed primitives.
- Knowledge Pack: recipes, diagnostics, aliases, templates, host heuristics, validation rules.

Reusable knowledge follows a lifecycle such as:

`observed -> candidate -> validated -> promoted -> deprecated`

Only generic validated/promoted knowledge may enter the public distributable Knowledge Pack.

## 10. Safety rules

- Fail closed on malformed knowledge or invalid command arguments.
- Syntax sugar must not weaken child-operation risk/checkpoint/verification requirements.
- Do not silently bypass a promoted capability; record a fallback reason when fallback is required.
- Do not infer success from a missing error.
- Do not expose or commit secrets, local Bridge IDs from another installation, current sessions, command history, or project-specific evidence.

## 11. First-run result

A correctly initialized user installation must leave enough GitHub state that a new authorized AI can recover solely by being told the Bus repository location and the rule: **read `PROJECT_STATE_INDEX.json` first**.
