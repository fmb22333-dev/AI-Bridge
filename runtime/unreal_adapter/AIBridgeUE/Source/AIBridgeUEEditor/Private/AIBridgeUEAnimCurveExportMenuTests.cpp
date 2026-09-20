#if WITH_DEV_AUTOMATION_TESTS

#include "AIBridgeUEAnimCurveExportMenu.h"

#include "Animation/AnimMontage.h"
#include "Animation/AnimSequence.h"
#include "Misc/AutomationTest.h"

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAIBridgeUEAnimCurveExportSelectionTest,
    "AIBridgeUE.AnimCurveExport.SelectionFilter",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAIBridgeUEAnimCurveExportSelectionTest::RunTest(const FString& Parameters)
{
    UAnimSequence* AnimA = NewObject<UAnimSequence>(GetTransientPackage());
    UAnimSequence* AnimB = NewObject<UAnimSequence>(GetTransientPackage());
    UAnimMontage* Montage = NewObject<UAnimMontage>(GetTransientPackage());

    const TArray<FAssetData> EmptySelection;
    const TArray<FAssetData> SingleSelection{FAssetData(AnimA, true)};
    const TArray<FAssetData> MultiSelection{FAssetData(AnimA, true), FAssetData(AnimB, true)};
    const TArray<FAssetData> MixedSelection{FAssetData(AnimA, true), FAssetData(Montage, true)};

    TestFalse(TEXT("Empty selection is rejected"), AIBridgeUE::IsAnimSequenceOnlySelection(EmptySelection));
    TestTrue(TEXT("Single AnimSequence is accepted"), AIBridgeUE::IsAnimSequenceOnlySelection(SingleSelection));
    TestTrue(TEXT("Multiple AnimSequences are accepted"), AIBridgeUE::IsAnimSequenceOnlySelection(MultiSelection));
    TestFalse(
        TEXT("Mixed AnimSequence and non-AnimSequence selection is rejected"),
        AIBridgeUE::IsAnimSequenceOnlySelection(MixedSelection));

    return true;
}

#endif
