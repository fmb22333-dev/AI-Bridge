# AI Bridge Execution Specification & Handoff

**Status:** Authoritative handoff / execution contract  
**Last updated:** 2026-09-06  
**Current validated Runtime:** 0.1.9.2  
**Current Supervisor:** 0.1.2  
**Current Houdini Adapter:** 0.4.0  
**Primary host validated:** Houdini 21.0.440 on Windows  
**Bus repository:** `fmb22333-dev/ai-bridge-bus`  
**Runtime update branch:** `bridge-runtime`

> Future AI agents must read this document before modifying AI Bridge. Do not rediscover or replace validated behavior unless a test proves the documented behavior is wrong.

---

## 1. Project goal

AI Bridge is a local execution bridge controlled remotely through GitHub. It is designed for environments where there is no usable public inbound endpoint and network tunneling has unacceptable latency or complexity.

Target hosts:
- Houdini
- Blender / Blender Python
- Unreal Engine
- Unreal Engine C++
- future DCC/tool adapters

Current priority is Houdini compatibility and host-operation coverage.

Primary design goals:
1. AI-directed execution with minimal manual user steps.
2. Local host execution remains fast; remote transport may be slower.
3. Runtime can update itself safely.
4. Supervisor remains small, stable, and rollback-capable.
5. Explicit capabilities are preferred over unrestricted arbitrary code execution.
6. All destructive/write operations should support verification and, where practical, rollback/checkpoints.
7. User-facing startup remains simple even if internals become complex.

---

## 2. Human-facing filesystem contract

The root directory must stay visually simple.

Expected prominent entries:
- `AI_Bridge.bat` — normal daily start
- `START_AI_BRIDGE.bat`
- `OPEN_AI_BRIDGE.bat`
- `STOP_AI_BRIDGE.bat`
- `README_FIRST.txt`

Internal structure:
- `_System/` — Supervisor and stable infrastructure
- `Runtime/Current` — legacy/bootstrap path only
- `Runtime/Versions/<version>` — versioned Runtime installs
- `Runtime/Staging` — self-update work area
- `Docs/` or Runtime-bundled docs — persistent architecture/handoff information

Do not make normal users manually edit JSON or search for hidden scripts.

---

## 3. Current architecture

```text
ChatGPT
  |
  | GitHub API
  v
GitHub Issue #1
  |
  | Mailbox V3 (fixed Issue Comment)
  v
AI Bridge Runtime
  |
  | local Core API
  v
Host Adapter Session
  |
  +--> Houdini UI thread execution
  |
  +--> result sender thread
  |
  v
Runtime
  |
  | PATCH same mailbox comment
  v
GitHub Result
  |
  v
ChatGPT
```

Update path:

```text
ChatGPT
  -> bridge_admin
  -> Runtime/Staging
  -> compile + pytest
  -> publish runtime_bundle.zip + runtime-release.json
  -> bridge-runtime branch
  -> Supervisor
  -> Runtime/Versions/<version>
  -> health check
  -> activate
  -> rollback on failure
```

The running Runtime must not overwrite or rename itself directly.

---

## 4. GitHub transport: validated evolution

### 4.1 Removed paths

The following are deprecated and must not be reintroduced without explicit evidence:
- GitHub Actions command gateway
- GitHub Actions result relay
- Cloudflare relay / endpoint
- public tunnel requirement

Old workflow files and Cloudflare endpoint were physically removed from `main`.

Reason:
- Actions Runner startup added several seconds.
- Cloudflare/public-relay path added deployment complexity without sufficient benefit for this user's no-public-network environment.

### 4.2 Contents Bus

Old form:
```text
command JSON commit
-> local Bridge poll
-> host execute
-> result JSON commit
```

Important optimization already implemented:
- Do **not** re-read every historical command whenever the directory changes.
- Maintain known SHA/index and read only new/changed commands.
- Result upload should avoid unnecessary pre-read GET when creating a new result.
- Conditional GET/ETag is required for polling.

Measured optimized baseline:
- end-to-end approximately **6.4 s**
- local Bridge/host execution approximately **tens of milliseconds**

### 4.3 Issue Comment V2

Form:
```text
new Issue Comment = command
-> Bridge polls comments
-> execute
-> PATCH same comment = result
```

Measured:
- approximately **4.06 s** end-to-end
- about 36% faster than the optimized Contents baseline

Required fine-grained PAT permissions:
- Contents: Read and write
- Issues: Read and write
- Metadata: read
- repository access should be restricted to the dedicated bridge repository

### 4.4 Mailbox V3 — current primary transport

Form:
```text
one fixed Issue Comment
AI_BRIDGE_MAILBOX_V3
       |
       | UPDATE
       v
AI_BRIDGE_COMMAND_V3 + JSON
       |
       | Bridge conditional GET
       v
execute
       |
       | PATCH same comment
       v
AI_BRIDGE_RESULT_V3 + JSON
```

Current mode name:
`issue_mailbox_v3`

Fallback order:
1. Mailbox V3
2. Issue Comment V2
3. Contents Bus

Never remove fallbacks without replacement validation.

Measured 4-run sample after V3:
- 3.369 s
- 3.464 s
- 4.476 s
- 3.420 s
- median: **~3.44 s**

Median split:
- ChatGPT -> GitHub mailbox UPDATE: **~1.91 s**
- mailbox UPDATE -> result visible: **~1.53 s**
- Bridge local execution: **~0.02 s**

Conclusion:
Bridge/host execution is not the dominant latency source. GitHub/API/tool latency dominates. Do not add complexity to save tens of milliseconds unless there is a measurable end-to-end benefit.

---

## 5. GitHub rate-limit policy

Do not use blind high-frequency polling.

Current strategy:
- Mailbox V3 healthy-quota floor: **0.25 s**
- V2/Contents default floor: **0.5 s**
- ETag / `If-None-Match` conditional GET
- use 304 responses when unchanged
- track `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`
- automatic backoff when quota gets low
- if conditional caching appears broken (poor 304 ratio), increase poll interval

Historical concern:
0.5 s unconditional polling would theoretically generate 7200 requests/hour and is not acceptable against ordinary authenticated primary-rate-limit budgets.

Dashboard/state should surface:
- remaining quota
- reset
- current poll interval
- 304 ratio / conditional-cache behavior
- active transport mode

---

## 6. Supervisor contract

Current Supervisor: **0.1.2**

Responsibilities:
- start Runtime
- stop Runtime
- monitor Runtime
- pull published Runtime update
- validate activation
- retain previous Runtime
- rollback on failed health check
- avoid spawning repeated browser tabs on automatic Runtime restarts

### Windows-specific rule

Do **not** rename a directory containing the running Runtime.

Validated failure:
`WinError 32` occurred when Supervisor tried:
```text
Runtime/Current -> Runtime/Previous
```
while Windows still had the directory in use.

Correct model:
```text
Runtime/Versions/0.1.7.0
Runtime/Versions/0.1.6.0
...
active runtime pointer/state
```

Switch by activating a versioned directory, not by renaming the currently running directory.

### Browser behavior

Automatic updates/restarts must **not** open new browser tabs.

Browser rules:
- manual normal start: open Dashboard once
- automatic Runtime restart/update: do not open page
- `OPEN_AI_BRIDGE.bat`: explicit manual open

---

## 7. Runtime self-update contract

Runtime has a restricted `bridge_admin` adapter.

Validated operations:
- `bridge.update.status`
- `bridge.update.begin`
- `bridge.update.write_file`
- `bridge.update.delete_file`
- `bridge.update.validate`
- `bridge.update.publish`

Normal AI-driven update flow:

```text
bridge.update.begin
-> modify only Staging
-> bridge.update.validate
   -> Python compile
   -> pytest
-> bridge.update.publish(version)
-> Supervisor sees manifest
-> install Runtime/Versions/<version>
-> activate
-> health check
-> keep previous version for rollback
```

Do not publish if validation fails.

Current runtime publication channel:
- repository: `fmb22333-dev/ai-bridge-bus`
- branch: `bridge-runtime`
- manifest: `runtime-release.json`
- bundle: `runtime_bundle.zip`

This branch is a bootstrap source/update channel. It can later move to a dedicated `ai-bridge` source repository without changing the Supervisor model.

---

## 8. Houdini Adapter architecture

Validated host:
- Houdini **21.0.440**
- Windows
- current Adapter: **0.2.0**

Source location inside Runtime:
```text
houdini_adapter/python/ai_bridge_houdini/
```

Important modules:
- `client.py` — session registration, background polling, UI handoff, result sender
- `dispatcher.py` — operation routing
- `inspect_ops.py`
- `parm_ops.py`
- `code_ops.py`
- `node_ops.py`
- `cook_ops.py`
- `checkpoint_ops.py`
- `compat_ops.py` — compatibility pack additions

### Threading rule

Houdini API execution stays on the Houdini UI thread.

Validated structure:
- background worker long-polls local Core for commands
- command is queued into UI thread
- dispatcher executes `hou` operations on UI thread
- independent result sender thread posts completed result back to Core

Do not execute normal `hou` graph mutation from arbitrary background threads.

### Historical latency bug already fixed

Old design used one worker for command poll + result upload. A completed result could wait behind the next long poll, adding roughly **~2.3 s** in observed testing.

Current design uses a dedicated Result Sender thread. Do not merge these paths back together.

---

## 9. Houdini session semantics

Houdini sessions are ephemeral.

Example:
- before restart: `HOU-1F600CB578DA`
- after restart: `HOU-194963782FDD`

Never hardcode an old session ID.

Before host operations:
1. read current Bridge status
2. resolve currently connected Houdini session
3. bind command to that session
4. include expected `project_file` where appropriate

Many Houdini operations require an explicit session. A command without session may return:
`SESSION_REQUIRED`

Project file is also a safety/targeting guard.

---

## 10. Houdini capabilities — Adapter 0.2.0

### Previously validated

- `session.status`
- `inspect.context`
- `inspect.node`
- `inspect.network`
- `inspect.find`
- `parm.read`
- `parm.write`
- `code.read`
- `code.write`
- `code.patch`
- `node.create`
- `node.delete`
- `node.connect`
- `node.disconnect`
- `frame.set`
- `cook.execute`
- `host.errors`
- `checkpoint.create`
- `rollback.execute`

### Houdini Compatibility Pack 1 — Adapter 0.2.0

Added:
- `adapter.capabilities`
- `hip.status`
- `hip.save`
- `selection.get`
- `selection.set`
- `node.state`
- `node.set_state`
- `parm.batch_read`
- `parm.batch_write`

Validation:
- Runtime tests: **17/17 PASS**
- live `adapter.capabilities`: PASS
- live `hip.status`: PASS
- live `node.state`: PASS
- live `selection.get`: PASS
- live `selection.set`: PASS
- live `parm.batch_read`: PASS
- live `parm.batch_write`: PASS
- `hip.save`: unit-tested only; intentionally not live-tested because it persists the user's HIP

Current live project during validation:
`E:/AA/SpeedUP.hip`

---

## 11. Houdini write-safety rules

### Optimistic concurrency

Code/parameter writes may require `expected_hash`.

Validated behavior:
`code.write` without an expected hash returned:
`EXPECTED_HASH_REQUIRED`

Correct pattern:
```text
code.read / parm.read
-> receive current value + hash
-> write/patch with expected_hash
-> verify readback
```

Do not bypass this protection simply to make a command shorter.

### Checkpoints

Riskier graph mutation should request:
`execution.checkpoint = auto`

Observed mutation operations can return:
- checkpoint ID
- rollback availability
- readback verification

### Risk levels

Keep operation metadata explicit:
- L1: low-risk/read/local UI state
- L2: graph mutation / reversible structural write
- L3: persistent project/file write (e.g. `hip.save`)

Do not silently downgrade risk.

---

## 12. Arbitrary Python execution policy

Do **not** make unrestricted `python.exec` the default compatibility strategy.

Preferred hierarchy:
1. explicit typed operation
2. batch typed operation
3. code read/write for known node code parameters
4. narrowly scoped high-risk host script capability only when explicit typed operations are insufficient

Reason:
- capability discovery is easier
- verification can be operation-specific
- rollback semantics are clearer
- future Blender/UE adapters can share conceptual operation families
- arbitrary execution makes auditing and risk boundaries weak

A generic host-script capability may be added later, but it must be explicitly high risk and sandboxed/scoped where possible.

---

## 13. Failed/unsafe experiment: Houdini Adapter hot reload

Attempted goal:
update Adapter 0.1.x -> 0.2.0 without restarting Houdini.

Experiment:
- created temporary Geometry + Python SOP
- copied updated Adapter modules
- used `importlib.reload` on Adapter modules
- attempted to update active Runtime registration

Result:
- current Houdini Adapter session dropped from Core
- session did not automatically recover
- user restart was required

Conclusion:
**Do not hot-reload the live Adapter thread/client modules with `importlib.reload`.**

Adapter updates currently require Houdini restart unless a future controlled reconnect/reloader is specifically designed and validated.

The temporary test node was later deleted successfully.

---

## 14. Host compatibility design direction

Do not build future adapters as unrelated command sets.

Preferred common conceptual families:

```text
adapter.*
session.*
project/scene/hip.*
selection.*
inspect.*
node/object/actor.*
parm/property.*
code.*
cook/build/compile.*
host.errors
checkpoint.*
rollback.*
```

Host-specific terminology may differ, but common intent should remain recognizable.

Planned hosts:
- Houdini
- Blender
- Blender Python
- Unreal Engine
- Unreal Engine C++

First priority remains broad, safe Houdini control.

---

## 15. Operational checklist for future AI

Before modifying Bridge:
1. Read this document.
2. Read current `.ai-bridge/status/<bridge_id>.json`.
3. Confirm Runtime version, Supervisor version, transport mode, host sessions.
4. If editing Runtime, use `bridge_admin` Staging workflow.
5. Validate before publish.
6. Never assume Houdini session IDs survive restart.
7. Prefer Mailbox V3; preserve V2/Contents fallback.
8. Preserve ETag/rate-limit protections.
9. Do not reintroduce Actions/Cloudflare by default.
10. Do not rename a running Runtime directory on Windows.
11. Do not hot-reload active Houdini Adapter client/thread modules.
12. Record meaningful architecture changes and experiment results back into this document.

---

## 16. Version landmarks

- pre-GitHub-only: Cloudflare/Actions experiments; rejected for current use case
- GitHub-only Contents path: functional, initially ~15 s observed on first full live loop
- optimized Contents path: ~6.4 s
- Comment V2: ~4.06 s
- Mailbox V3 / Runtime 0.1.6.0: median ~3.44 s
- Runtime 0.1.7.0:
  - Houdini Compatibility Pack 1
  - Adapter 0.2.0
  - 17 tests PASS
  - live compatibility validation PASS after Houdini restart

---

## 17. Documentation maintenance rule

This file is part of the product, not optional notes.

Any change that affects:
- architecture
- update mechanism
- protocol
- permissions
- performance assumptions
- host compatibility
- rollback
- risk model
- known failed approaches

must update this document in the same development cycle.

Future AI handoff should cite this file as the authoritative starting point.


---

## 18. Houdini Compatibility Pack 2 — Adapter 0.3.0 / Runtime 0.1.8.0

Purpose: reduce command count, reduce ad-hoc host code generation, preflight common mistakes before mutation, and make failures machine-actionable.

### New typed graph operations

- `node.create_configured`
  - creates one node
  - sets parameters in the same operation
  - sets common node state/position/comment/color flags
  - optionally connects inputs
  - removes the newly created node if configuration fails
  - intended to replace repeated `node.create + parm.write + node.set_state + node.connect` sequences

- `network.validate`
  - preflights a declarative graph specification without mutation
  - validates parent, node types, duplicate names, known parameter names, connection endpoints, and connection indices
  - use this before large graph construction when node/type uncertainty exists

- `network.apply`
  - creates/configures/connects multiple nodes from one declarative payload
  - supports logical node ids so connections do not need repeated absolute paths
  - optional `layout=true`
  - default `allow_update_existing=false`; existing nodes can be referenced but are not mutated unless explicitly opted in
  - newly created nodes are internally cleaned up if the operation fails
  - when existing nodes may be modified, keep `execution.checkpoint=auto` so normal Bridge rollback remains authoritative

Preferred graph-building order:

```text
network.validate
-> network.apply (checkpoint=auto)
-> inspect.network / cook.execute / host.errors as required
```

Do not generate temporary Python/VEX merely to create, configure, position, or connect ordinary Houdini nodes when these typed operations can express the change.

### Structured failure schema

Houdini dispatcher failures now aim to return stable machine-readable fields:

```text
origin
stage
code
category
exception_type
message
retryable
suggestion
context
```

Cook failures may additionally include:
- `host_errors`
- `host_warnings`

Important normalized error classes include:
- `EXPECTED_HASH_REQUIRED`
- `CONFLICT`
- `NODE_TYPE_NOT_FOUND`
- `NODE_NOT_FOUND`
- `PARM_NOT_FOUND`
- `NODE_ALREADY_EXISTS`
- `NODE_TYPE_MISMATCH`
- `CONNECTION_ENDPOINT_NOT_FOUND`
- `INVALID_CONNECTION_INDEX`
- `PERMISSION_DENIED`
- `OBJECT_WAS_DELETED`
- `HOUDINI_OPERATION_FAILED`
- `HOST_COOK_ERROR`
- `CAPABILITY_NOT_SUPPORTED`
- `PROJECT_FILE_MISMATCH`
- `READBACK_MISMATCH`

Future AI should branch on `code/category/retryable` rather than parsing arbitrary English exception text whenever possible.

### Runtime adapter scaffolding

New top-level adapter surfaces are reserved and included in release compile validation:

```text
blender_adapter/
  python/ai_bridge_blender/

unreal_adapter/
  python/ai_bridge_unreal/
  cpp/
```

These are scaffold-only. Houdini remains the active implementation priority.

`bridge_admin` allowed Runtime surfaces are explicitly extended only for these named adapter directories; the updater remains deny-by-default for arbitrary top-level paths.

### Validation status

Pre-publish Runtime validation for Pack 2:
- compile: PASS
- tests: **23/23 PASS**

Houdini Adapter updates still require a Houdini restart. Do not revive the failed live `importlib.reload` approach documented above.


### Safe Adapter update staging result (validated 2026-09-06)

A second Adapter update experiment was performed after Runtime 0.1.8.0 publication:

- copied the new `ai_bridge_houdini/*.py` files from the active Runtime version to the currently installed Houdini adapter package directory
- did **not** call `importlib.reload`
- did **not** mutate the active Adapter thread/runtime object
- current Houdini Adapter session stayed connected
- temporary staging nodes were removed after the copy

Conclusion:

```text
copy updated Adapter files on disk
-> keep current in-memory Adapter untouched
-> restart Houdini later
-> new Adapter version loads on next process start
```

This is currently the preferred upgrade preparation path. It is materially safer than live module reload. Future work may formalize this as a typed Adapter staging/update operation so temporary Python SOPs are no longer needed.


---

## 19. Knowledge Accumulation Contract — "越用越聪明"

AI Bridge is not intended to remain a fixed collection of low-level commands. It must accumulate validated host knowledge through use.

The Bridge knowledge model has four promotion targets:

1. **Primitive typed operation** — a host primitive that is common, stable, and benefits from strict validation/readback. Example: `network.apply`.
2. **Compound recipe / syntax sugar** — a repeated multi-step construction pattern expressible on top of stable primitives. Recipes should be preferred over adding new kernel code when possible.
3. **Error/diagnostic rule** — a recurring failure signature with a stable root cause, repair strategy, or retry policy.
4. **Host/validation rule** — process knowledge such as thread restrictions, checkpoint requirements, Build/Test separation, or required readback/cook behavior.

### Promotion pipeline

```text
observe repeated pattern
-> record candidate
-> classify target layer
-> validate against historical samples and/or live host
-> promote into versioned Knowledge Pack
-> future AI prefers promoted rule/recipe
-> update this execution specification when the rule changes architecture or safety behavior
```

Do not auto-promote a rule from one accidental success. Promotion requires evidence. A rule may be demoted or revised when a counterexample is demonstrated.

### Kernel vs Knowledge Pack

The Houdini Adapter should evolve toward two layers:

```text
Stable Adapter Kernel
  - transport/session/threading
  - capability dispatch
  - checkpoints/readback
  - primitive typed operations
  - dynamic knowledge loader

Hot-loadable Knowledge Pack
  - recipes
  - error catalog
  - diagnostic rules
  - construction templates
  - host heuristics
```

The goal is that adding or editing normal syntax sugar, recipes, or error mappings **does not require restarting Houdini**. Only changes to the stable Adapter Kernel should require a process restart.

### Rule for future AI

When a task is solved, future AI should ask internally:
- Did this require a repeated multi-command sequence that can become a recipe?
- Did a new stable error signature/root cause appear?
- Did a host-specific constraint or validation rule become clear?
- Would promotion reduce future code payload, command count, or failure probability?

If yes, add a candidate or promote the validated rule during the same development cycle instead of leaving the knowledge only in chat history.


---

## 20. Hot-load Knowledge Pack — Adapter 0.4.0 / Runtime 0.1.9.0

Goal: make normal syntax-sugar growth and error-rule accumulation restart-free.

### Stable kernel entry points

Added stable capabilities:
- `knowledge.status`
- `knowledge.search`
- `recipe.list`
- `recipe.get`
- `recipe.validate`
- `recipe.apply`

These operation names are part of the stable Adapter Kernel. New recipes do not require new capability names.

### Hot-loadable files

The Adapter reads these files at call time:

```text
houdini_adapter/python/ai_bridge_houdini/knowledge/
  recipes/*.json
  error_catalog.json
  host_rules.json
```

No Python module reload is needed when these JSON files change.

Validated behavior:
- adding a recipe JSON after the module is already imported is visible on the next `recipe.list` / `knowledge.status` call;
- changing `error_catalog.json` is visible to the next error-classification call;
- content digest changes when Knowledge Pack content changes;
- `restart_required_for_knowledge_changes = false`;
- Runtime validation after migration cleanup: **29/29 tests PASS**.

### Seed recipes

Initial promoted recipes:
- `network.build`
- `network.build_and_cook`
- `code.safe_patch_and_cook`

Recipe execution uses a whitelist of existing typed primitives rather than arbitrary Python execution.

The recipe engine supports:
- exact argument injection via `{"$arg": "name"}`;
- previous-step output references via `{"$result": "step.key"}`;
- deferred validation of runtime step outputs;
- conditional steps such as collecting `host.errors` only when a Cook fails;
- trace/result output for auditability.

### Historical Houdini seed knowledge

The first Knowledge Pack is seeded from prior Houdini project conversations and handoff material, including:
- first-real-error diagnostic rule and cascade-error caution;
- Build/Test separation;
- explicit VEX typed temporaries for ambiguous generic-return overloads;
- vector position vs `matrix3` separation;
- VEX-vs-C++ language mismatch patterns;
- attribute read-after-write caution;
- cross-input semantic/name matching rather than index assumptions;
- descendant-aware hierarchy mutation;
- Cook after structural edits;
- checkpoint before risky structure mutation;
- Houdini UI-thread mutation authority;
- separate Result Sender performance rule;
- optimistic concurrency with `expected_hash`.

The external curated seed is also stored on the `bridge-runtime` branch as `HOUDINI_KNOWLEDGE_SEED.md`.

### Restart boundary after 0.4.0

**No Houdini restart expected for:**
- adding/editing recipe JSON;
- adding/editing error signatures and repair suggestions;
- adding/editing host/validation rules;
- promoting repeated multi-step patterns when existing recipe primitives can express them.

**Houdini restart still required for:**
- changes to `client.py` transport/session/threading;
- changes to the dispatcher kernel itself;
- adding a new primitive operation that requires new Python implementation/capability wiring;
- changing host-thread execution semantics;
- changes to bootstrap/adapter process lifecycle.

Preferred future strategy: keep the stable kernel small and move repeated behavior upward into Knowledge Pack recipes whenever practical.


---

## 21. Controlled Houdini Adapter Staging — Runtime 0.1.9.1

A new BridgeAdmin capability is available:

- `bridge.adapter.stage_houdini` (L1 controlled file-staging operation; it does not require a Houdini checkpoint)

Purpose: stage the Adapter files used by the **next** Houdini process without touching the currently running Adapter modules.

Implementation contract:
- reuses the existing `install_houdini_adapter.py` installer logic;
- uses `running_safe=True` while Houdini may be open;
- does not call `importlib.reload`;
- does not re-register or mutate the active Houdini Adapter thread/runtime object;
- returns `restart_required` and `hot_reload_performed=false`;
- uses the currently running Runtime version as the Adapter source.

Installer modes:

```text
running_safe=True
  -> overlay/copy new files into the installed Adapter tree
  -> preserve existing files that may still back the running process
  -> stage package metadata/source hash
  -> restart Houdini later to activate new Kernel

running_safe=False
  -> cold-install behavior
  -> clean replacement of python/scripts trees
```

This replaces the temporary Python SOP staging experiments for normal future Adapter Kernel upgrades.

Validation before release:
- compile: PASS
- tests: **33/33 PASS**
- running-safe staging preserves existing files in regression tests;
- cold install still removes stale files;
- the staging path contains no `importlib.reload`.


### Risk classification correction

Initial live invocation exposed a generic Core interaction: declaring this operation as L2 caused `SESSION_REQUIRED_FOR_CHECKPOINT` because BridgeAdmin has no Houdini Session. The operation itself does not mutate HIP/host state; it only overlays validated Adapter files into the configured Houdini user directory. Therefore its capability risk is **L1**. This avoids meaningless host checkpoint creation while preserving the deny-by-default BridgeAdmin surface.


---

## 22. Long-running host operation session liveness — Runtime 0.2.6.2

Observed during AutoUV V0.13.1 live validation on Houdini 21.0.440:

- `PART_PLAN_EXECUTOR` required about 27.9 seconds for a forced Cook.
- Houdini's UI event-loop tick was blocked by the synchronous Cook.
- Adapter heartbeat intentionally paused while the UI tick was stale.
- Core session staleness was 12 seconds, so the still-running Adapter was temporarily classified as `SESSION_DISCONNECTED`.
- The same PID and Session recovered after the Cook, proving this was a liveness-classification error rather than a host crash.

Corrected contract:

- explicit heartbeat remains liveness evidence and may update `project_file`;
- authenticated `/adapter/poll/<session>` traffic is also transport-liveness evidence and refreshes `last_seen_at`;
- authenticated `/adapter/result/<session>` traffic also refreshes `last_seen_at`;
- poll/result activity must not change session identity, PID, project file, capabilities, or registration timestamp;
- the 12-second stale timeout remains in force when the Adapter worker actually stops communicating;
- Houdini UI-pump health remains a separate execution-readiness signal. Do not use session-disconnect semantics as a proxy for a blocked UI tick.

This separation is intentional: a long synchronous Houdini Cook can block the UI thread while the Adapter transport worker remains healthy. Session connectivity describes Adapter transport liveness; UI-pump diagnostics describe host execution readiness.

Validation:
- targeted liveness regression: 11/11 PASS before release;
- live acceptance criterion: a long AutoUV Cook followed immediately by another Houdini command must not return `SESSION_DISCONNECTED`;
- no Houdini Adapter kernel change is required for this fix.


---

## 23. Post-long-Cook UI command handoff — Adapter 0.5.10 / Runtime 0.2.6.3

Follow-up live validation after Runtime 0.2.6.2 proved the transport-liveness fix worked: the next command was no longer rejected with `SESSION_DISCONNECTED`. It exposed a second, independent false-positive path in the Houdini Adapter.

Observed behavior:
- after a ~24.9 second AutoUV forced Cook, the next command reached the Adapter successfully;
- the Adapter rejected it before queueing because `last_ui_tick_monotonic` was older than the 4-second UI-tick threshold;
- failure was `ADAPTER_UI_PUMP_STALLED`, even though the historical tick age alone did not prove that the newly arrived command could not be consumed.

Corrected contract:
- a stale historical UI-tick timestamp is diagnostic context, not sufficient authority to reject a newly polled command;
- newly polled commands are queued to the Houdini UI handoff;
- the existing bounded pending-UI timeout remains authoritative: if the UI thread does not actually claim the queued command within `UI_COMMAND_STALL_SECONDS` (currently 3 seconds), the Adapter returns `ADAPTER_UI_PUMP_STALLED`;
- abandoned commands remain protected from later execution by the existing `abandoned_ui` guard;
- true UI-pump failure therefore remains fail-closed, but long synchronous host work no longer causes an immediate false rejection solely from stale tick history.

Validation before release:
- targeted liveness regression: 12/12 PASS;
- Adapter version: 0.5.10;
- live acceptance criterion after cold restart: long AutoUV Cook -> immediate follow-up Houdini command succeeds without `SESSION_DISCONNECTED` or stale-history pre-rejection.


---

## 24. Explicit deferred UI wake after long synchronous host work — Adapter 0.5.11 / Runtime 0.2.6.4

Final live validation of Adapter 0.5.10 showed that transport liveness and stale-history pre-rejection were fixed, but exposed a real scheduling gap:

- AutoUV `PART_PLAN_EXECUTOR` forced Cook completed successfully after ~15.3 seconds;
- the immediate follow-up command was accepted and queued;
- Houdini's existing `hou.ui.addEventLoopCallback` did not run again within the bounded 3-second handoff window;
- the command therefore correctly failed with `ADAPTER_UI_PUMP_STALLED`.

This is not a session-disconnect problem and not a stale-timestamp false positive. It is a missing main-thread wake after a long synchronous host operation.

Corrected handoff:
- keep `hou.ui.addEventLoopCallback(runtime.tick)` as the normal continuous UI pump;
- after the Adapter worker receives and queues a new command, also request one explicit main-thread drain using `hdefereval.executeDeferred(runtime.tick)`;
- `hdefereval` is imported lazily in UI Houdini only;
- if deferred scheduling itself fails, record the failure but preserve the normal event-loop callback as fallback;
- the existing pending-UI timeout remains authoritative: a command still fails closed if it is not actually claimed by the UI thread within `UI_COMMAND_STALL_SECONDS`.

Rationale:
SideFX documents `hdefereval.executeDeferred` as the supported mechanism for deferring work from background/event contexts onto Houdini's main thread. The Bridge uses it only to wake the existing typed dispatcher; no HOM graph mutation is moved to a background thread.

Validation before release:
- targeted liveness regression: 14/14 PASS;
- Adapter version: 0.5.11;
- final live acceptance after cold restart:
  `long AutoUV Cook -> immediate follow-up Houdini command`
  must succeed without `SESSION_DISCONNECTED` or `ADAPTER_UI_PUMP_STALLED`.


---

## 25. Post-long-Cook bounded UI recovery grace — Adapter 0.5.12 / Runtime 0.2.6.5

Adapter 0.5.11 live validation disproved the explicit `hdefereval.executeDeferred(runtime.tick)` wake hypothesis.

Observed:
- AutoUV `PART_PLAN_EXECUTOR` forced Cook completed successfully in ~16.0 seconds;
- the next command arrived about 10 seconds later;
- despite the deferred wake request, Houdini did not consume that command inside the normal 3-second UI handoff window;
- `ADAPTER_UI_PUMP_STALLED` was returned at command age ~4.0 seconds with UI-tick age ~14.0 seconds;
- a later retry on the same PID/Session succeeded, proving the UI callback recovered naturally rather than remaining permanently dead.

SideFX semantics explain this result: `hou.ui.addEventLoopCallback` runs when the UI event loop is idle, and `hdefereval.executeDeferred` also defers work until the main event loop can process it. It is therefore not a force-wake authority for a still-busy post-Cook UI.

Corrected contract:
- remove the ineffective `executeDeferred` wake path;
- keep the ordinary UI handoff timeout at 3 seconds for normal operations;
- when a host dispatch itself lasts at least 5 seconds, arm a bounded post-operation recovery grace lasting at most 60 seconds from dispatch completion;
- commands queued while that recovery grace is active are allowed to wait for the normal Houdini event loop to resume instead of being failed at 3 seconds;
- if the grace expires and the command still has not been claimed by the UI thread, the existing `ADAPTER_UI_PUMP_STALLED` fail-closed behavior applies;
- the grace is contextual and bounded; it does not globally weaken normal stall detection.

Validation before release:
- targeted liveness regression: 15/15 PASS;
- includes short-operation no-grace behavior, long-operation grace arming, and eventual fail-closed expiration after grace;
- Adapter version: 0.5.12.

Final live acceptance after cold restart:
`long AutoUV Cook -> follow-up command during recovery window`
must complete without `SESSION_DISCONNECTED` and without premature `ADAPTER_UI_PUMP_STALLED`.

## Transport Isolation P0 (Runtime 0.2.6.67+)

The V5 transport execution contract is lane-isolated: same-Session Host commands are FIFO/serialized; different Sessions and Runtime adapter lanes may execute concurrently. Runtime 0.2.6.79+ additionally partitions no-session `workspace` adapter execution by `command.workspace`, so a long command in one Workspace cannot head-of-line block an unrelated Workspace, while commands targeting the same Workspace remain FIFO. `bridge_transport / command.status` and `bridge_transport / transport.ping` are control-plane operations and must not be queued behind Host work. Result publication remains serialized at the transport boundary.

GitHub authentication failures are transport degradation, not Host/Core failure. 401/Bad credentials and permission-style 403 set `auth_degraded` with bounded exponential retry; rate limits use `rate_limited`. A successful poll restores `connected`.

Presence/status JSON is a durable last-published snapshot, not proof of live reachability. Use `transport.ping` for live reachability.

Execution-budget timeout with `host_operation_may_still_be_running=true` marks the Session `busy_unknown`; subsequent Host work for that Session fails closed with `SESSION_BUSY_UNKNOWN` until the exact late Adapter result clears the guard. Heartbeat alone never clears it.

See `AI_BRIDGE_TRANSPORT_ISOLATION_P0.md` for the compact operational contract.

## Transport Ergonomics P1 (Runtime 0.2.6.68+)

Large owned-V5 results use recursive structured summaries rather than whole-key omission. Ordinary large delivery has zero extra GitHub Contents writes; the canonical full terminal result remains in the local command database and is recoverable with `bridge_transport / command.status` + `include_result=true`. Full recovery up to the bounded Comment limit is returned directly; larger explicit recovery creates an on-demand `full_reference`. Stale-generation and Comment-PATCH failure still persist the original full result as durable Contents fallback.

V5 wrappers may add optional transport metadata `predecessor={command_id, require}` where `require` is `terminal` or `success`. The predecessor is validated before durable accept/ACK. Invalid, missing, nonterminal, or non-success dependencies fail closed with `INVALID_PREDECESSOR`, `PREDECESSOR_NOT_FOUND`, `PREDECESSOR_NOT_TERMINAL`, or `PREDECESSOR_NOT_SUCCESS`. Rejected successors are not queued; submit a fresh generation after the dependency condition is satisfied.

Predecessor metadata does not enter `CommandEnvelope` or Host Adapter schemas. Existing V5 senders remain compatible, and the 0.2.6.67 Transport Isolation contract remains unchanged. See `AI_BRIDGE_TRANSPORT_ERGONOMICS_P1.md`.


## Presence runtime-version authority (Runtime 0.2.6.80+)

Durable Presence `bridge_version` is derived from the active Runtime `pyproject.toml` through the shared runtime-version helper. Transport extensions, including Supabase primary integration, must not replace that value with a literal. The Runtime publisher changes `pyproject.toml` and Dashboard display version only; it must not patch transport source files to carry release identity. This prevents an extension layer from pinning Presence to an older Runtime version after Supervisor activation.
