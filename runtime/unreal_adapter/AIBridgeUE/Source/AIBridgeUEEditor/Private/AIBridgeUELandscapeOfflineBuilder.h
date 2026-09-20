#pragma once
#include "CoreMinimal.h"
#include "WorldPartition/WorldPartitionBuilder.h"
#include "AIBridgeUELandscapeOfflineBuilder.generated.h"

// Offline orchestration only. Recovery policy remains in LandscapeRepair::Execute.
UCLASS()
class UAIBridgeUELandscapeOfflineBuilder : public UWorldPartitionBuilder
{
    GENERATED_BODY()
public:
    UAIBridgeUELandscapeOfflineBuilder(const FObjectInitializer& ObjectInitializer);
    virtual bool RequiresCommandletRendering() const override { return true; }
    virtual ELoadingMode GetLoadingMode() const override { return ELoadingMode::Custom; }
protected:
    virtual bool RunInternal(UWorld* World, const FCellInfo& CellInfo, FPackageSourceControlHelper& PackageHelper) override;
};
