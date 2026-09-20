#include "AIBridgeUELandscapeRepair.h"
#include "Misc/AutomationTest.h"
#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeDiskFinalPolicyTest, "AIBridgeUE.LandscapeRepair.DiskFinalPolicy", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeDiskFinalPolicyTest::RunTest(const FString& Parameters)
{
    using namespace AIBridgeUE::LandscapeRepair;
    TestTrue(TEXT("Pristine commandlet disk final with exactly one orphan can be captured"), CanUseOfflineDiskFinal(true, true, 1));
    TestFalse(TEXT("Interactive editor cannot substitute disk final for obsolete snapshot"), CanUseOfflineDiskFinal(false, true, 1));
    TestFalse(TEXT("Already composed data is not a load snapshot"), CanUseOfflineDiskFinal(true, false, 1));
    TestFalse(TEXT("Healthy components must not be overwritten"), CanUseOfflineDiskFinal(true, true, 0));
    TestFalse(TEXT("Multiple orphan layers require explicit separate handling"), CanUseOfflineDiskFinal(true, true, 2));
    return true;
}
#endif
