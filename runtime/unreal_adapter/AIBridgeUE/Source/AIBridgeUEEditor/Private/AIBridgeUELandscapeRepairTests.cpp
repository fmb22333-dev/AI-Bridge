#include "AIBridgeUELandscapeRepair.h"

#include "Misc/AutomationTest.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAIBridgeUELandscapeRepairSourcePolicyTest,
    "AIBridgeUE.LandscapeRepair.SourcePolicy",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAIBridgeUELandscapeRepairSourcePolicyTest::RunTest(const FString& Parameters)
{
    using namespace AIBridgeUE::LandscapeRepair;

    FComponentRecoveryState Recoverable;
    Recoverable.bHasObsoleteFinalDataOnLoad = true;
    Recoverable.bHasNamedObsoleteLayerData = true;
    TestEqual(
        TEXT("Final OnLoad snapshot is authoritative for visual recovery"),
        SelectRecoverySource(Recoverable),
        ERecoverySource::ObsoleteFinalDataOnLoad);

    FComponentRecoveryState MissingFinal;
    MissingFinal.bHasObsoleteFinalDataOnLoad = false;
    MissingFinal.bHasNamedObsoleteLayerData = true;
    TestEqual(
        TEXT("Missing final snapshot does not silently fall back"),
        SelectRecoverySource(MissingFinal),
        ERecoverySource::Unavailable);

    return true;
}

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAIBridgeUELandscapeRepairPlanGuardTest,
    "AIBridgeUE.LandscapeRepair.PlanGuard",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAIBridgeUELandscapeRepairPlanGuardTest::RunTest(const FString& Parameters)
{
    using namespace AIBridgeUE::LandscapeRepair;

    const TArray<FString> RecordsA = { TEXT("component=B|final=1"), TEXT("component=A|final=1") };
    const TArray<FString> RecordsB = { TEXT("component=A|final=1"), TEXT("component=B|final=1") };
    const TArray<FString> RecordsChanged = { TEXT("component=A|final=1"), TEXT("component=B|final=0") };

    const FString HashA = BuildPlanFingerprint(RecordsA);
    const FString HashB = BuildPlanFingerprint(RecordsB);
    const FString HashChanged = BuildPlanFingerprint(RecordsChanged);
    TestFalse(TEXT("Plan fingerprint is non-empty"), HashA.IsEmpty());
    TestEqual(TEXT("Plan fingerprint is order-independent"), HashA, HashB);
    TestTrue(TEXT("Plan fingerprint changes with recovery state"), HashA != HashChanged);

    FApplyGuardState Ready;
    Ready.CurrentPlanHash = HashA;
    Ready.ExpectedPlanHash = HashA;
    Ready.bHasPersistentTargetLayer = true;
    Ready.MissingFinalDataCount = 0;
    Ready.RecoverableComponentCount = 2;
    TestTrue(TEXT("Ready plan is accepted"), ValidateApplyGuard(Ready).IsEmpty());

    FApplyGuardState Drifted = Ready;
    Drifted.ExpectedPlanHash = HashChanged;
    TestEqual(TEXT("Plan drift is rejected"), ValidateApplyGuard(Drifted), FString(TEXT("PLAN_CHANGED")));

    FApplyGuardState MissingFinal = Ready;
    MissingFinal.MissingFinalDataCount = 1;
    TestEqual(TEXT("Missing Final OnLoad is rejected"), ValidateApplyGuard(MissingFinal), FString(TEXT("FINAL_SNAPSHOT_MISSING")));

    FApplyGuardState MissingTarget = Ready;
    MissingTarget.bHasPersistentTargetLayer = false;
    TestEqual(TEXT("Non-persistent destination is rejected"), ValidateApplyGuard(MissingTarget), FString(TEXT("TARGET_EDIT_LAYER_INVALID")));

    return true;
}
