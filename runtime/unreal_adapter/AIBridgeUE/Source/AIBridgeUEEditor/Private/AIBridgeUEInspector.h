#pragma once

#include "CoreMinimal.h"
#include "Dom/JsonObject.h"

namespace AIBridgeUE
{
struct FInspectOptions
{
    bool bFluidFluxOnly = true;
    int32 MaxActors = 512;
    int32 MaxProperties = 192;
    int32 MaxCollectionItems = 64;
};

FInspectOptions ReadInspectOptions(const TSharedPtr<FJsonObject>& Arguments);

TSharedPtr<FJsonObject> InspectCurrentEditorLevel(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);

TSharedPtr<FJsonObject> InspectObjectByPath(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);

TSharedPtr<FJsonObject> InspectObjectsByPaths(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage);

bool WriteCurrentLevelManifest(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutPath,
    FString& OutError);
}
