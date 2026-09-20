#pragma once
namespace AIBridgeUE::LandscapeRepair
{
// Pure policy shared by the native pump and automation tests. Deadlines are cooperative.
enum class EWaitDecision { Pending, Ready, Deadline, Memory, Cancelled };
constexpr double CompilationWaitSeconds = 600.0;
constexpr EWaitDecision EvaluateWait(int Remaining, double Elapsed, bool bMemorySafe, bool bCancelled)
{
    if (bCancelled) return EWaitDecision::Cancelled;
    if (!bMemorySafe) return EWaitDecision::Memory;
    if (Elapsed >= CompilationWaitSeconds) return EWaitDecision::Deadline;
    return Remaining == 0 ? EWaitDecision::Ready : EWaitDecision::Pending;
}
}
