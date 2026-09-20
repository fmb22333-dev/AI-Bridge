#pragma once
#include "CoreMinimal.h"
class UWorld;
namespace AIBridgeUE::LandscapeRepair
{
// Checks active/final textures and allocation-pool candidates before GPU composition.
// Rebuilds only derived resources from verified source pixels; never synthesizes missing source.
bool PrepareLandscapeResources(UWorld* World, const FString& ReportPath, const FString& CancelPath, FString& Error);
}
