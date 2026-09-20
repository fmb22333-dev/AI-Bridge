# AI Bridge P0 Safety Kernel

Release target: Runtime 0.2.0.0
Houdini Adapter: 0.5.0
Validation: compile PASS, 44/44 tests PASS after migration-script cleanup
Date: 2026-09-06

P0 establishes the safety baseline required before automatic Knowledge/Recipe growth.

Mailbox V4: COMMAND_V4 carries generation; Bridge must ACK the same generation and command_id before execution; RESULT_V4 preserves generation. V3/V2/Contents remain fallback paths.

Maintenance: bridge.update.begin owns an exclusive transaction_id; write/delete/validate/publish require the same transaction after P0 activation; foreign transactions are rejected; publish releases the lock.

Recipe Guard: max 64 steps, max 30 seconds, recipe-to-recipe invocation disabled in P0, unbounded loop/retry fields rejected, primitive whitelist, zero implicit retry, structured RECIPE_STEP_FAILED, RECIPE_VALIDATION_FAILED, RECIPE_BUDGET_EXCEEDED, RECIPE_CYCLE_DETECTED and RECIPE_CAPABILITY_VIOLATION responses, bounded execution trace.

RepairLoopGuard: duplicate (rule_id,state_fingerprint) returns REPAIR_LOOP_DETECTED; changed state can retry only within finite total cap; cap returns RECIPE_RETRY_LIMIT.

Knowledge activation: malformed knowledge fails closed, previous valid data remains active, knowledge.status reports knowledge_degraded and validation_errors, activation mode last_known_good.

P0 changes stable Houdini Adapter kernel, so 0.5.0 requires one Houdini restart after running-safe staging. Ordinary Knowledge Pack data changes remain restart-free afterward.

Final clean pre-release result: 44/44 PASS.
