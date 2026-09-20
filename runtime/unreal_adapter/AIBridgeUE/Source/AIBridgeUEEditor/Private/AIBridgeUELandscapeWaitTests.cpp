#include "Misc/AutomationTest.h"
#include "AIBridgeUELandscapeWaitPolicy.h"
#if WITH_DEV_AUTOMATION_TESTS
// Explicit dependency after the initial red test: header creation must invalidate this object.
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeWaitPolicyTest, "AIBridgeUE.LandscapeRepair.WaitPolicy", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeWaitPolicyTest::RunTest(const FString&)
{
    using namespace AIBridgeUE::LandscapeRepair;
    TestEqual(TEXT("Work in flight remains pending"), EvaluateWait(3, 1.0, true, false), EWaitDecision::Pending);
    TestEqual(TEXT("No work is ready"), EvaluateWait(0, 1.0, true, false), EWaitDecision::Ready);
    TestEqual(TEXT("Deadline prevents indefinite waiting"), EvaluateWait(1, 600.0, true, false), EWaitDecision::Deadline);
    TestEqual(TEXT("Low memory blocks even an empty queue"), EvaluateWait(0, 1.0, false, false), EWaitDecision::Memory);
    TestEqual(TEXT("Cancellation wins before readiness"), EvaluateWait(0, 1.0, true, true), EWaitDecision::Cancelled);
    return true;
}
#endif
