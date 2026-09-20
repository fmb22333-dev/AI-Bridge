#include "Misc/AutomationTest.h"
#include "AIBridgeUELandscapeResourcePolicy.h"
#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeResourcePolicyTest, "AIBridgeUE.LandscapeRepair.ResourcePolicy", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeResourcePolicyTest::RunTest(const FString&)
{
    using namespace AIBridgeUE::LandscapeRepair;
    TestEqual(TEXT("Complete source and GPU state is ready"), EvaluateTextureReadiness(true, false, 1, true, true, true), ETextureReadiness::Ready);
    TestEqual(TEXT("Source fingerprint alone cannot permit zero platform mips"), EvaluateTextureReadiness(true, false, 0, false, false, false), ETextureReadiness::RebuildResource);
    TestEqual(TEXT("Unknown platform format cannot render"), EvaluateTextureReadiness(true, false, 1, false, true, true), ETextureReadiness::RebuildResource);
    TestEqual(TEXT("Null texture resource cannot render"), EvaluateTextureReadiness(true, false, 1, true, false, false), ETextureReadiness::RebuildResource);
    TestEqual(TEXT("Uninitialized RHI cannot render"), EvaluateTextureReadiness(true, false, 1, true, true, false), ETextureReadiness::RebuildResource);
    TestEqual(TEXT("Pending compilation must finish first"), EvaluateTextureReadiness(true, true, 0, false, false, false), ETextureReadiness::PendingCompilation);
    TestEqual(TEXT("Absent source data must never be invented"), EvaluateTextureReadiness(false, false, 0, false, false, false), ETextureReadiness::InvalidSource);
    return true;
}
#endif
