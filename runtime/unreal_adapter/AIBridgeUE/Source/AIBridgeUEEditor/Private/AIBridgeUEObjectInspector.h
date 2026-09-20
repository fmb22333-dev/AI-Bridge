#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

namespace AIBridgeUE
{
TSharedPtr<FJsonObject> InspectObjectsByPaths(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);

TSharedPtr<FJsonObject> InspectObjectByPath(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);
}
