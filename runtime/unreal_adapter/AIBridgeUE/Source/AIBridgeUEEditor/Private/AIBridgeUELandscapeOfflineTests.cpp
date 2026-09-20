#include "CoreMinimal.h"
#include "Misc/AutomationTest.h"
#include "WorldPartition/WorldPartitionBuilder.h"
#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeOfflinePolicyTest, "AIBridgeUE.LandscapeRepair.OfflinePolicy", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeOfflinePolicyTest::RunTest(const FString& Parameters)
{
    UClass* Class = FindObject<UClass>(nullptr, TEXT("/Script/AIBridgeUEEditor.AIBridgeUELandscapeOfflineBuilder"));
    if (!TestNotNull(TEXT("Dedicated offline builder is registered"), Class)) return false;
    if (!TestTrue(TEXT("Builder uses the native World Partition lifecycle"), Class->IsChildOf(UWorldPartitionBuilder::StaticClass()))) return false;
    const UWorldPartitionBuilder* Builder = CastChecked<UWorldPartitionBuilder>(Class->GetDefaultObject());
    TestEqual(TEXT("Never request entire-world loading"), Builder->GetLoadingMode(), UWorldPartitionBuilder::ELoadingMode::Custom);
    TestTrue(TEXT("Terrain composition retains rendering in the commandlet"), Builder->RequiresCommandletRendering());
    return true;
}
#endif
