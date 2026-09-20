#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
class ULandscapeComponent;
namespace AIBridgeUE::LandscapeRepair
{
// Read-only diagnostics. This function never permits or performs a package save.
TSharedPtr<FJsonObject> AnalyzeLandscapeComparison(const TArray<ULandscapeComponent*>& Components, const FGuid& LayerGuid);
// Conservative save-gate policy for merge differences that are proven to be
// complete shared-edge reconciliations selected from captured source owners.
bool IsSafeMergedComparisonForSave(const TSharedPtr<FJsonObject>& Comparison, FString& OutRejectionReason);
}
