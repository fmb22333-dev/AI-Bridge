#pragma once
#include "CoreMinimal.h"
#include "HAL/PlatformMemory.h"
#include "Dom/JsonObject.h"
#if WITH_DEV_AUTOMATION_TESTS
#include "Misc/AutomationTest.h"
#endif
namespace AIBridgeUE::LandscapeRepair
{
// Extend by explicit strategy versions; never silently substitute recovery sources.
enum class ERecoverySource : uint8 { Unavailable = 0, ObsoleteFinalDataOnLoad = 1 };
struct FComponentRecoveryState
{
    bool bHasObsoleteFinalDataOnLoad = false;
    bool bHasNamedObsoleteLayerData = false;
};
struct FApplyGuardState
{
    FString CurrentPlanHash;
    FString ExpectedPlanHash;
    bool bHasPersistentTargetLayer = false;
    int32 MissingFinalDataCount = 0;
    int32 RecoverableComponentCount = 0;
};
ERecoverySource SelectRecoverySource(const FComponentRecoveryState& State);
FString BuildPlanFingerprint(const TArray<FString>& Records);
FString ValidateApplyGuard(const FApplyGuardState& State);
bool CanUseOfflineDiskFinal(bool bCommandlet, bool bBeforeFirstMerge, int32 OrphanLayerCount);
// scan -> apply (no save) -> finalize (explicit verified package save) -> verify.
TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Arguments, bool bDryRun, FString& OutErrorCode, FString& OutErrorMessage);
void AddCapabilities(TArray<TSharedPtr<FJsonValue>>& Capabilities);
}
