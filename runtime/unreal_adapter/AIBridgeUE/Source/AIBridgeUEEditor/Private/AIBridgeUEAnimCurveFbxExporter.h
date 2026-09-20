#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

namespace AIBridgeUE
{
TSharedPtr<FJsonObject> ExportAnimSequenceFbxWithCurves(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);
}
