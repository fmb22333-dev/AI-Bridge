#include "Misc/AutomationTest.h"
#include "AIBridgeUELandscapePlaneDiff.h"
#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapePlaneDiffTest, "AIBridgeUE.LandscapeRepair.PlaneDifference", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapePlaneDiffTest::RunTest(const FString&)
{
 using namespace AIBridgeUE::LandscapeRepair;
 TArray<uint16> A = {0, 65535, 32768, 1};
 auto D = ComparePlaneValues(A, A, 4, false);
 TestTrue(TEXT("An exact height plane is valid"), D.bValid);
 TestEqual(TEXT("An exact plane has no changes"), D.Changed, 0);
 auto B = A; B[0] = 2; B[1] = 65534;
 D = ComparePlaneValues(A, B, 4, false);
 TestEqual(TEXT("Exact changed pixel count"), D.Changed, 2);
 TestEqual(TEXT("Maximum raw height delta"), D.MaximumDelta, 2);
 TestEqual(TEXT("Absolute height delta sum"), D.SumAbsoluteDelta, int64(3));
 TestEqual(TEXT("Samples identify changed pixels"), D.Samples.Num(), 2);
 TArray<uint16> Empty; TArray<uint16> Zero = {0,0,0,0};
 D = ComparePlaneValues(Empty, Zero, 4, true);
 TestTrue(TEXT("Missing paint may be diagnosed against explicit zero"), D.bValid);
 TestEqual(TEXT("A newly allocated zero paint plane is pixel-equivalent"), D.Changed, 0);
 TestFalse(TEXT("Missing height is never considered zero"), ComparePlaneValues(Empty, Zero, 4, false).bValid);
 Zero[2] = 1; D = ComparePlaneValues(Empty, Zero, 4, true);
 TestEqual(TEXT("One nonzero added paint pixel is detected"), D.Changed, 1);
 TestFalse(TEXT("Incorrect extent is rejected"), ComparePlaneValues(A, B, 3, false).bValid);
 TestTrue(TEXT("Early equivalent rows retain baseline samples"), ShouldRecordComparisonDetails(31, false));
 TestFalse(TEXT("Late equivalent rows stay compact"), ShouldRecordComparisonDetails(32, false));
 TestTrue(TEXT("First late differing component retains pixel evidence"), ShouldRecordComparisonDetails(32, true));
 TestTrue(TEXT("Last differing component retains pixel evidence"), ShouldRecordComparisonDetails(1023, true));
 TestFalse(TEXT("Invalid diagnostic index is rejected"), ShouldRecordComparisonDetails(-1, true));
 return true;
}
#endif
