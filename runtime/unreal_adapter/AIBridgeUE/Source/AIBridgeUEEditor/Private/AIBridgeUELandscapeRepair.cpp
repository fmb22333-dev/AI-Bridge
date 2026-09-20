#include "AIBridgeUELandscapeRepair.h"
#include "Misc/SecureHash.h"
namespace AIBridgeUE::LandscapeRepair
{
bool CanUseOfflineDiskFinal(bool bCommandlet, bool bBeforeFirstMerge, int32 OrphanLayerCount)
{
    return bCommandlet && bBeforeFirstMerge && OrphanLayerCount == 1;
}
ERecoverySource SelectRecoverySource(const FComponentRecoveryState& State)
{
    return State.bHasObsoleteFinalDataOnLoad ? ERecoverySource::ObsoleteFinalDataOnLoad : ERecoverySource::Unavailable;
}
FString BuildPlanFingerprint(const TArray<FString>& Records)
{
    TArray<FString> Sorted = Records;
    Sorted.Sort();
    FSHA1 Hash;
    for (const FString& Record : Sorted)
    {
        const FString Framed = FString::Printf(TEXT("%d:"), Record.Len()) + Record;
        FTCHARToUTF8 Utf8(*Framed);
        Hash.Update(reinterpret_cast<const uint8*>(Utf8.Get()), Utf8.Length());
    }
    Hash.Final();
    uint8 Digest[FSHA1::DigestSize];
    Hash.GetHash(Digest);
    return BytesToHex(Digest, UE_ARRAY_COUNT(Digest));
}
FString ValidateApplyGuard(const FApplyGuardState& State)
{
    if (!State.bHasPersistentTargetLayer) return TEXT("TARGET_EDIT_LAYER_INVALID");
    if (State.ExpectedPlanHash.IsEmpty()) return TEXT("EXPECTED_PLAN_HASH_REQUIRED");
    if (State.CurrentPlanHash != State.ExpectedPlanHash) return TEXT("PLAN_CHANGED");
    if (State.MissingFinalDataCount > 0) return TEXT("FINAL_SNAPSHOT_MISSING");
    if (State.RecoverableComponentCount <= 0) return TEXT("NO_RECOVERABLE_COMPONENTS");
    return FString();
}
}
