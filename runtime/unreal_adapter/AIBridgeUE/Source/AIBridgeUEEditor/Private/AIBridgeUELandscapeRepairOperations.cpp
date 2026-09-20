#include "AIBridgeUELandscapeRepair.h"
#include "AIBridgeUELandscapeComparison.h"
#include "Editor.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "FileHelpers.h"
#include "HAL/FileManager.h"
#include "Landscape.h"
#include "LandscapeComponent.h"
#include "LandscapeEditLayer.h"
#include "LandscapeLayerInfoObject.h"
#include "LandscapeStreamingProxy.h"
#include "Misc/App.h"
#include "Misc/DateTime.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "Misc/ScopedSlowTask.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/Package.h"
#include "UObject/UnrealType.h"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeLandscapeRepair, Log, All);

namespace AIBridgeUE::LandscapeRepair
{
namespace
{
using FLayerMap = TMap<FGuid, FLandscapeLayerComponentData>;
struct FContext
{
    UWorld* World = nullptr;
    ALandscape* Parent = nullptr;
    FGuid LayerGuid;
    TArray<ALandscapeProxy*> Proxies;
    TArray<ULandscapeComponent*> Components;
    TArray<UPackage*> Packages;
    FString Map;
    FString LayerPolicyError;
};

FString Arg(const TSharedPtr<FJsonObject>& A, const TCHAR* Key, const TCHAR* Default = TEXT(""))
{
    FString V; return A.IsValid() && A->TryGetStringField(Key, V) ? V : FString(Default);
}
bool BoolArg(const TSharedPtr<FJsonObject>& A, const TCHAR* Key)
{
    bool V = false; if (A.IsValid()) A->TryGetBoolField(Key, V); return V;
}
int32 IntArg(const TSharedPtr<FJsonObject>& A, const TCHAR* Key)
{
    double V = 0; if (A.IsValid()) A->TryGetNumberField(Key, V); return int32(V);
}
FString Digest(FSHA1& H)
{
    H.Final(); uint8 Bytes[FSHA1::DigestSize]; H.GetHash(Bytes);
    return BytesToHex(Bytes, UE_ARRAY_COUNT(Bytes));
}

// These obsolete-layer accessors are not exported by the installed engine.
// Read typed reflected storage, after validating its shape. Never mutate it here.
const FLayerMap* ReadLayerMap(ULandscapeComponent* C, const TCHAR* Name)
{
    const FMapProperty* P = FindFProperty<FMapProperty>(C->GetClass(), Name);
    const FStructProperty* K = P ? CastField<FStructProperty>(P->KeyProp) : nullptr;
    const FStructProperty* V = P ? CastField<FStructProperty>(P->ValueProp) : nullptr;
    if (!K || !V || K->Struct->GetFName() != TEXT("Guid")
        || V->Struct->GetFName() != TEXT("LandscapeLayerComponentData")
        || V->Struct->GetStructureSize() != sizeof(FLandscapeLayerComponentData)) return nullptr;
    return P->ContainerPtrToValuePtr<FLayerMap>(C);
}

UPackage* ActorPackage(AActor* A)
{
    if (UPackage* P = A->GetExternalPackage()) return P;
    return A->GetOutermost();
}

bool Resolve(const TSharedPtr<FJsonObject>& A, FContext& C, FString& Error)
{
    if (!IsInGameThread() || !GEditor || GEditor->PlayWorld) { Error = TEXT("EDITOR_GAME_THREAD_REQUIRED"); return false; }
    C.World = GEditor->GetEditorWorldContext().World();
    if (!C.World || C.World->IsGameWorld()) { Error = TEXT("EDITOR_WORLD_REQUIRED"); return false; }
    C.Map = C.World->GetOutermost()->GetName();
    if (Arg(A, TEXT("map")).IsEmpty() || C.Map != Arg(A, TEXT("map"))) { Error = TEXT("MAP_MISMATCH"); return false; }
    const FString ParentPath = Arg(A, TEXT("landscape_path"));
    int32 ParentCount = 0;
    for (TActorIterator<ALandscape> It(C.World); It; ++It)
    {
        if (!ParentPath.IsEmpty() && It->GetPathName() != ParentPath) continue;
        C.Parent = *It; ++ParentCount;
    }
    if (ParentCount != 1 || !C.Parent->GetLandscapeInfo()) { Error = TEXT("LANDSCAPE_PARENT_NOT_UNIQUE_OR_REGISTERED"); return false; }
    const auto Layers = C.Parent->GetEditLayersConst();
    if (Layers.Num() != 1 || !Layers[0] || !Cast<ULandscapeEditLayerPersistent>(Layers[0]))
        C.LayerPolicyError = TEXT("SINGLE_PERSISTENT_LAYER_REQUIRED");
    else
    {
        const ULandscapeEditLayerBase* L = Layers[0];
        C.LayerGuid = L->GetGuid();
        if (!L->IsVisible() || L->IsLocked() || !FMath::IsNearlyEqual(L->GetAlphaForTargetType(ELandscapeToolTargetType::Heightmap), 1.0f)
            || !FMath::IsNearlyEqual(L->GetAlphaForTargetType(ELandscapeToolTargetType::Weightmap), 1.0f))
            C.LayerPolicyError = TEXT("NON_IDENTITY_LAYER_COMPOSITION");
        if (C.Parent->GetLayersConst()[0].Brushes.Num() != 0) C.LayerPolicyError = TEXT("BRUSH_LAYER_REQUIRES_SEPARATE_STRATEGY");
    }
    C.Packages.AddUnique(ActorPackage(C.Parent));
    for (TActorIterator<ALandscapeProxy> It(C.World); It; ++It)
    {
        ALandscapeProxy* P = *It;
        if (P->GetLandscapeActor() != C.Parent || P->LandscapeComponents.IsEmpty()) continue;
        C.Proxies.Add(P); C.Packages.AddUnique(ActorPackage(P));
        for (ULandscapeComponent* Component : P->LandscapeComponents)
        {
            if (!Component || !Component->IsRegistered()) { Error = TEXT("COMPONENT_NOT_REGISTERED"); return false; }
            C.Components.Add(Component);
        }
    }
    C.Proxies.Sort([](const ALandscapeProxy& X, const ALandscapeProxy& Y) { return X.GetPathName() < Y.GetPathName(); });
    C.Components.Sort([](const ULandscapeComponent& X, const ULandscapeComponent& Y) { return X.GetPathName() < Y.GetPathName(); });
    if (C.Components.IsEmpty()) { Error = TEXT("NO_LOADED_LANDSCAPE_COMPONENTS"); return false; }
    return true;
}

// Semantic mip-zero hashes: height RG only; paint keyed by LayerInfo identity.
// Texture names, packing channels, normals and lower mips are not identities.
bool PixelHash(UTexture2D* T, int32 Channel, int32 X, int32 Y, int32 Side, FString& Hash, FString& Error)
{
    if (!T || !T->Source.IsValid() || T->Source.GetFormat() != TSF_BGRA8) { Error = TEXT("TEXTURE_SOURCE_NOT_BGRA8"); return false; }
    const int32 W = T->Source.GetSizeX(), H = T->Source.GetSizeY();
    if (Side <= 0 || Side > 8192 || X < 0 || Y < 0 || int64(X) + Side > W || int64(Y) + Side > H
        || int64(W) * H > 67108864) { Error = TEXT("TEXTURE_REGION_INVALID"); return false; }
    TArray64<uint8> Bytes;
    if (!T->Source.GetMipData(Bytes, 0) || Bytes.Num() != int64(W) * H * sizeof(FColor)) { Error = TEXT("MIP0_READ_FAILED"); return false; }
    FSHA1 Hasher;
    const FColor* Pixels = reinterpret_cast<const FColor*>(Bytes.GetData());
    TArray<uint8> Row; Row.SetNumUninitialized(Side * (Channel < 0 ? 2 : 1));
    for (int32 RowIndex = 0; RowIndex < Side; ++RowIndex)
    {
        for (int32 Col = 0; Col < Side; ++Col)
        {
            const FColor& P = Pixels[int64(Y + RowIndex) * W + X + Col];
            if (Channel < 0) { Row[Col * 2] = P.R; Row[Col * 2 + 1] = P.G; }
            else Row[Col] = Channel == 0 ? P.R : Channel == 1 ? P.G : Channel == 2 ? P.B : P.A;
        }
        Hasher.Update(Row.GetData(), Row.Num());
    }
    Hash = Digest(Hasher); return true;
}

bool DataHash(ULandscapeComponent* C, const FLandscapeLayerComponentData& D, FString& Out, FString& Error)
{
    UTexture2D* Height = D.HeightmapData.Texture;
    if (!Height) { Error = TEXT("HEIGHTMAP_MISSING"); return false; }
    const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections;
    FString HeightHash;
    if (!PixelHash(Height, -1, FMath::RoundToInt(C->HeightmapScaleBias.Z * Height->Source.GetSizeX()),
        FMath::RoundToInt(C->HeightmapScaleBias.W * Height->Source.GetSizeY()), Side, HeightHash, Error)) return false;
    TArray<FString> Records;
    Records.Add(FString::Printf(TEXT("side=%d|height=%s"), Side, *HeightHash));
    TSet<FString> Seen;
    for (const FWeightmapLayerAllocationInfo& Allocation : D.WeightmapData.LayerAllocations)
    {
        if (!Allocation.LayerInfo || Allocation.WeightmapTextureChannel > 3
            || !D.WeightmapData.Textures.IsValidIndex(Allocation.WeightmapTextureIndex)) { Error = TEXT("PAINT_ALLOCATION_INVALID"); return false; }
        const FString Layer = Allocation.LayerInfo->GetPathName();
        if (Seen.Contains(Layer)) { Error = TEXT("PAINT_LAYER_DUPLICATED"); return false; }
        Seen.Add(Layer);
        UTexture2D* T = D.WeightmapData.Textures[Allocation.WeightmapTextureIndex];
        if (!T) { Error = TEXT("WEIGHTMAP_MISSING"); return false; }
        FString WeightHash;
        if (!PixelHash(T, Allocation.WeightmapTextureChannel, FMath::FloorToInt(C->WeightmapScaleBias.Z * T->Source.GetSizeX()),
            FMath::FloorToInt(C->WeightmapScaleBias.W * T->Source.GetSizeY()), Side, WeightHash, Error)) return false;
        Records.Add(Layer + TEXT("=") + WeightHash);
    }
    Out = BuildPlanFingerprint(Records); return true;
}

bool HeightHash(ULandscapeComponent* C, const FLandscapeLayerComponentData& D, FString& Out, FString& Error)
{
    UTexture2D* Height = D.HeightmapData.Texture;
    if (!Height) { Error = TEXT("HEIGHTMAP_MISSING"); return false; }
    const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections;
    FString Pixels;
    if (!PixelHash(Height, -1,
        FMath::RoundToInt(C->HeightmapScaleBias.Z * Height->Source.GetSizeX()),
        FMath::RoundToInt(C->HeightmapScaleBias.W * Height->Source.GetSizeY()),
        Side, Pixels, Error)) return false;
    Out = BuildPlanFingerprint({FString::Printf(TEXT("side=%d|height=%s"), Side, *Pixels)});
    return true;
}

bool PaintHash(ULandscapeComponent* C, const FLandscapeLayerComponentData& D, FString& Out, FString& Error)
{
    const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections;
    TArray<FString> Records;
    Records.Add(FString::Printf(TEXT("side=%d"), Side));
    TSet<FString> Seen;
    for (const FWeightmapLayerAllocationInfo& Allocation : D.WeightmapData.LayerAllocations)
    {
        if (!Allocation.LayerInfo || Allocation.WeightmapTextureChannel > 3
            || !D.WeightmapData.Textures.IsValidIndex(Allocation.WeightmapTextureIndex))
        { Error = TEXT("PAINT_ALLOCATION_INVALID"); return false; }
        const FString Layer = Allocation.LayerInfo->GetPathName();
        if (Seen.Contains(Layer)) { Error = TEXT("PAINT_LAYER_DUPLICATED"); return false; }
        Seen.Add(Layer);
        UTexture2D* Texture = D.WeightmapData.Textures[Allocation.WeightmapTextureIndex];
        if (!Texture) { Error = TEXT("WEIGHTMAP_MISSING"); return false; }
        FString Pixels;
        if (!PixelHash(Texture, Allocation.WeightmapTextureChannel,
            FMath::FloorToInt(C->WeightmapScaleBias.Z * Texture->Source.GetSizeX()),
            FMath::FloorToInt(C->WeightmapScaleBias.W * Texture->Source.GetSizeY()),
            Side, Pixels, Error)) return false;
        Records.Add(Layer + TEXT("=") + Pixels);
    }
    Out = BuildPlanFingerprint(Records);
    return true;
}

bool IsSafeHeightNormalizationPrecheck(const TSharedPtr<FJsonObject>& Comparison, FString& Error)
{
    Error.Reset();
    if (!Comparison.IsValid()
        || !Comparison->HasTypedField<EJson::Object>(TEXT("summary"))
        || !Comparison->HasTypedField<EJson::Object>(TEXT("boundary_audit")))
    { Error = TEXT("HEIGHT_PRECHECK_INVALID"); return false; }

    const TSharedPtr<FJsonObject> Summary = Comparison->GetObjectField(TEXT("summary"));
    double ExpectedComponents = 0.0, CheckedComponents = 0.0, Errors = 0.0;
    if (!Summary->TryGetNumberField(TEXT("expected_components"), ExpectedComponents)
        || !Summary->TryGetNumberField(TEXT("checked_components"), CheckedComponents)
        || !Summary->TryGetNumberField(TEXT("errors"), Errors)
        || ExpectedComponents <= 0.0 || CheckedComponents != ExpectedComponents || Errors != 0.0)
    { Error = TEXT("HEIGHT_PRECHECK_COVERAGE_INVALID"); return false; }

    const TSharedPtr<FJsonObject> Boundary = Comparison->GetObjectField(TEXT("boundary_audit"));
    double Outside = 0.0, CaptureErrors = 0.0, ChangedGroups = 0.0;
    double Disagreeing = 0.0, Invalid = 0.0, Single = 0.0, ExpectedOwners = 0.0, AuditedOwners = 0.0;
    bool AllAudited = false;
    if (!Boundary->TryGetNumberField(TEXT("changed_samples_outside_component_edges"), Outside)
        || !Boundary->TryGetNumberField(TEXT("capture_errors"), CaptureErrors)
        || !Boundary->TryGetNumberField(TEXT("changed_logical_vertex_groups"), ChangedGroups)
        || !Boundary->TryGetNumberField(TEXT("merged_copies_disagree_groups"), Disagreeing)
        || !Boundary->TryGetNumberField(TEXT("invalid_groups"), Invalid)
        || !Boundary->TryGetNumberField(TEXT("single_component_groups"), Single)
        || !Boundary->TryGetNumberField(TEXT("expected_changed_owner_samples"), ExpectedOwners)
        || !Boundary->TryGetNumberField(TEXT("audited_changed_owner_samples"), AuditedOwners)
        || !Boundary->TryGetBoolField(TEXT("all_changed_samples_audited"), AllAudited)
        || Outside != 0.0 || CaptureErrors != 0.0 || Disagreeing != 0.0 || Invalid != 0.0 || Single != 0.0
        || ExpectedOwners != AuditedOwners || !AllAudited)
    { Error = TEXT("HEIGHT_PRECHECK_BOUNDARY_INVALID"); return false; }

    if (!Boundary->HasTypedField<EJson::Array>(TEXT("groups")))
    { Error = TEXT("HEIGHT_PRECHECK_GROUPS_MISSING"); return false; }
    const TArray<TSharedPtr<FJsonValue>>& Groups = Boundary->GetArrayField(TEXT("groups"));
    if (double(Groups.Num()) != ChangedGroups)
    { Error = TEXT("HEIGHT_PRECHECK_GROUP_COVERAGE_INVALID"); return false; }

    for (const TSharedPtr<FJsonValue>& Value : Groups)
    {
        const TSharedPtr<FJsonObject> Group = Value.IsValid() ? Value->AsObject() : nullptr;
        FString Result, Plane;
        if (!Group.IsValid()
            || !Group->TryGetStringField(TEXT("result"), Result)
            || !Group->TryGetStringField(TEXT("plane"), Plane))
        { Error = TEXT("HEIGHT_PRECHECK_GROUP_INVALID"); return false; }

        if (Result == TEXT("exact") || Result == TEXT("reconciled_from_source")) continue;
        if (Result == TEXT("merged_value_not_in_source") && Plane == TEXT("paint")) continue;
        Error = TEXT("HEIGHT_PRECHECK_UNSAFE_") + Plane + TEXT("_") + Result;
        return false;
    }
    return true;
}

bool CollectAllowedGeneratedPaintLayers(const TSharedPtr<FJsonObject>& Comparison, TSet<FString>& OutLayers, FString& Error)
{
    OutLayers.Reset();
    if (!IsSafeHeightNormalizationPrecheck(Comparison, Error)) return false;
    const TSharedPtr<FJsonObject> Boundary = Comparison->GetObjectField(TEXT("boundary_audit"));
    const TArray<TSharedPtr<FJsonValue>>& Groups = Boundary->GetArrayField(TEXT("groups"));
    for (const TSharedPtr<FJsonValue>& Value : Groups)
    {
        const TSharedPtr<FJsonObject> Group = Value.IsValid() ? Value->AsObject() : nullptr;
        FString Result, Plane, Layer;
        if (!Group.IsValid()
            || !Group->TryGetStringField(TEXT("result"), Result)
            || !Group->TryGetStringField(TEXT("plane"), Plane))
        { Error = TEXT("HEIGHT_ALLOWED_PAINT_GROUP_INVALID"); return false; }
        if (Result != TEXT("merged_value_not_in_source")) continue;
        if (Plane != TEXT("paint") || !Group->TryGetStringField(TEXT("layer"), Layer) || Layer.IsEmpty())
        { Error = TEXT("HEIGHT_ALLOWED_GENERATED_VALUE_NOT_PAINT"); return false; }
        OutLayers.Add(Layer);
    }
    Error.Reset();
    return true;
}

bool IsSafePostMergeHeightNormalization(
    const TSharedPtr<FJsonObject>& Comparison,
    const TSet<FString>& AllowedGeneratedPaintLayers,
    FString& Error)
{
    if (!IsSafeHeightNormalizationPrecheck(Comparison, Error)) return false;
    const TSharedPtr<FJsonObject> Boundary = Comparison->GetObjectField(TEXT("boundary_audit"));
    const TArray<TSharedPtr<FJsonValue>>& Groups = Boundary->GetArrayField(TEXT("groups"));
    for (const TSharedPtr<FJsonValue>& Value : Groups)
    {
        const TSharedPtr<FJsonObject> Group = Value.IsValid() ? Value->AsObject() : nullptr;
        FString Result, Plane, Layer;
        if (!Group.IsValid()
            || !Group->TryGetStringField(TEXT("result"), Result)
            || !Group->TryGetStringField(TEXT("plane"), Plane))
        { Error = TEXT("HEIGHT_POSTMERGE_GROUP_INVALID"); return false; }

        // Height is already required to match the captured Final bit-for-bit.
        // Therefore any remaining height group means the semantic audit and the
        // strict hash check disagree; fail closed.
        if (Plane == TEXT("height"))
        { Error = TEXT("HEIGHT_POSTMERGE_HEIGHT_GROUP_REMAINS"); return false; }
        if (Plane != TEXT("paint"))
        { Error = TEXT("HEIGHT_POSTMERGE_UNKNOWN_PLANE"); return false; }

        if (Result == TEXT("exact") || Result == TEXT("reconciled_from_source")) continue;
        if (Result == TEXT("merged_value_not_in_source"))
        {
            if (!Group->TryGetStringField(TEXT("layer"), Layer) || !AllowedGeneratedPaintLayers.Contains(Layer))
            { Error = TEXT("HEIGHT_POSTMERGE_UNPROVEN_GENERATED_PAINT_LAYER"); return false; }
            continue;
        }
        Error = TEXT("HEIGHT_POSTMERGE_UNSAFE_PAINT_RESULT_") + Result;
        return false;
    }
    Error.Reset();
    return true;
}

bool CopyFinalHeightToEditLayer(const FContext& C, int32& CopiedTextures, FString& Error)
{
    CopiedTextures = 0;
    TSet<UTexture2D*> ProcessedSourceHeightmaps;
    for (ULandscapeComponent* Component : C.Components)
    {
        if (!Component) { Error = TEXT("HEIGHT_COPY_COMPONENT_INVALID"); return false; }
        const FLandscapeLayerComponentData* DestData = Component->GetLayerData(C.LayerGuid);
        UTexture2D* SourceHeightmap = Component->GetHeightmap();
        UTexture2D* DestHeightmap = DestData ? DestData->HeightmapData.Texture.Get() : nullptr;
        if (!DestData || !DestData->IsInitialized() || !SourceHeightmap || !DestHeightmap)
        { Error = TEXT("HEIGHT_COPY_DATA_MISSING: ") + Component->GetPathName(); return false; }

        if (SourceHeightmap->Source.GetSizeX() != DestHeightmap->Source.GetSizeX()
            || SourceHeightmap->Source.GetSizeY() != DestHeightmap->Source.GetSizeY()
            || SourceHeightmap->Source.GetFormat() != DestHeightmap->Source.GetFormat())
        { Error = TEXT("HEIGHT_COPY_SHAPE_MISMATCH: ") + Component->GetPathName(); return false; }

        if (ProcessedSourceHeightmaps.Contains(SourceHeightmap)) continue;

        TArray64<uint8> ExistingMip0Data;
        if (!SourceHeightmap->Source.GetMipData(ExistingMip0Data, 0)
            || ExistingMip0Data.Num() != int64(SourceHeightmap->Source.GetSizeX()) * SourceHeightmap->Source.GetSizeY() * sizeof(FColor))
        { Error = TEXT("HEIGHT_COPY_SOURCE_MIP_FAILED: ") + Component->GetPathName(); return false; }

        Component->Modify();
        DestHeightmap->SetFlags(RF_Transactional);
        DestHeightmap->Modify();
        FColor* Mip0Data = reinterpret_cast<FColor*>(DestHeightmap->Source.LockMip(0));
        if (!Mip0Data)
        { DestHeightmap->ClearFlags(RF_Transactional); Error = TEXT("HEIGHT_COPY_DEST_LOCK_FAILED: ") + Component->GetPathName(); return false; }
        FMemory::Memcpy(Mip0Data, ExistingMip0Data.GetData(), ExistingMip0Data.Num());
        DestHeightmap->Source.UnlockMip(0);
        DestHeightmap->UpdateResource();
        DestHeightmap->ClearFlags(RF_Transactional);
        DestHeightmap->GetOutermost()->MarkPackageDirty();
        Component->GetOutermost()->MarkPackageDirty();
        ProcessedSourceHeightmaps.Add(SourceHeightmap);
        ++CopiedTextures;
    }
    return true;
}

FLandscapeLayerComponentData FinalData(ULandscapeComponent* C);

bool CompareHeightStableExpected(
    const FContext& C,
    const TSharedPtr<FJsonObject>& ExpectedHeight,
    const TSharedPtr<FJsonObject>& ExpectedActivePaint,
    const TSharedPtr<FJsonObject>& ExpectedFinalPaint,
    bool bCompareMerged,
    FString& Error)
{
    // ExpectedFinalPaint is retained in the journal as evidence, but it is not
    // a save gate: UE may legally re-normalize Final paint while Active paint
    // remains byte-identical. Final paint is audited semantically after merge.
    if (!ExpectedHeight.IsValid() || !ExpectedActivePaint.IsValid() || !ExpectedFinalPaint.IsValid()
        || ExpectedHeight->Values.Num() != C.Components.Num()
        || ExpectedActivePaint->Values.Num() != C.Components.Num()
        || ExpectedFinalPaint->Values.Num() != C.Components.Num())
    { Error = TEXT("HEIGHT_STABLE_EXPECTED_COVERAGE_INVALID"); return false; }

    for (ULandscapeComponent* Component : C.Components)
    {
        const FLandscapeLayerComponentData* Active = Component->GetLayerData(C.LayerGuid);
        if (!Active) { Error = TEXT("HEIGHT_STABLE_ACTIVE_MISSING: ") + Component->GetPathName(); return false; }

        FString ExpectedHeightHash, ExpectedActivePaintHash;
        FString ActualHeightHash, ActualPaintHash;
        if (!ExpectedHeight->TryGetStringField(Component->GetPathName(), ExpectedHeightHash)
            || !ExpectedActivePaint->TryGetStringField(Component->GetPathName(), ExpectedActivePaintHash))
        { Error = TEXT("HEIGHT_STABLE_EXPECTED_COMPONENT_MISSING: ") + Component->GetPathName(); return false; }

        if (!HeightHash(Component, *Active, ActualHeightHash, Error) || ActualHeightHash != ExpectedHeightHash)
        { Error = TEXT("HEIGHT_STABLE_ACTIVE_HEIGHT_MISMATCH: ") + Component->GetPathName() + TEXT(" ") + Error; return false; }
        if (!PaintHash(Component, *Active, ActualPaintHash, Error) || ActualPaintHash != ExpectedActivePaintHash)
        { Error = TEXT("HEIGHT_STABLE_ACTIVE_PAINT_CHANGED: ") + Component->GetPathName() + TEXT(" ") + Error; return false; }

        if (bCompareMerged)
        {
            const FLandscapeLayerComponentData Merged = FinalData(Component);
            if (!HeightHash(Component, Merged, ActualHeightHash, Error) || ActualHeightHash != ExpectedHeightHash)
            { Error = TEXT("HEIGHT_STABLE_MERGED_HEIGHT_MISMATCH: ") + Component->GetPathName() + TEXT(" ") + Error; return false; }
        }
    }
    Error.Reset();
    return true;
}

FLandscapeLayerComponentData FinalData(ULandscapeComponent* C)
{
    FLandscapeLayerComponentData D;
    D.HeightmapData.Texture = C->GetHeightmap();
    D.WeightmapData.Textures = C->GetWeightmapTextures();
    D.WeightmapData.LayerAllocations = C->GetWeightmapLayerAllocations();
    return D;
}

bool AtomicJson(const FString& Path, const TSharedPtr<FJsonObject>& J)
{
    if (!J.IsValid()) return false;
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
    FString Text; const auto Writer = TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&Text);
    if (!FJsonSerializer::Serialize(J.ToSharedRef(), Writer)) return false;
    const FString Temp = Path + TEXT(".tmp");
    if (!FFileHelper::SaveStringToFile(Text, *Temp, FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) return false;
    return IFileManager::Get().Move(*Path, *Temp, true, false, false, true);
}

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeDataFingerprintTest, "AIBridgeUE.LandscapeRepair.DataFingerprint", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeDataFingerprintTest::RunTest(const FString& Parameters)
{
    UWorld* TestWorld = UWorld::CreateWorld(EWorldType::EditorPreview, false);
    if (!TestNotNull(TEXT("Isolated test world exists"), TestWorld)) return false;
    ALandscape* Owner = TestWorld->SpawnActor<ALandscape>();
    if (!TestNotNull(TEXT("Component has a legal LandscapeProxy outer"), Owner)) { TestWorld->DestroyWorld(false); return false; }
    auto* C = NewObject<ULandscapeComponent>(Owner);
    C->SubsectionSizeQuads = 3; C->NumSubsections = 1; C->ComponentSizeQuads = 3;
    C->HeightmapScaleBias = FVector4(0.25, 0.25, 0, 0);
    C->WeightmapScaleBias = FVector4(0.25, 0.25, 0.125, 0.125);
    TestNotNull(TEXT("Reflected obsolete storage matches installed engine layout"), ReadLayerMap(C, TEXT("ObsoleteEditLayerData")));
    auto MakeTexture = [](const TArray<FColor>& Colors)
    {
        auto* T = NewObject<UTexture2D>();
        T->Source.Init(4, 4, 1, 1, TSF_BGRA8, reinterpret_cast<const uint8*>(Colors.GetData()));
        return T;
    };
    TArray<FColor> Heights; Heights.Init(FColor(128, 32, 19, 255), 16);
    TArray<FColor> Weights; Weights.Init(FColor(73, 18, 0, 0), 16);
    FLandscapeLayerComponentData A; A.HeightmapData.Texture = MakeTexture(Heights);
    A.WeightmapData.Textures.Add(MakeTexture(Weights));
    FWeightmapLayerAllocationInfo Allocation; Allocation.LayerInfo = NewObject<ULandscapeLayerInfoObject>();
    Allocation.WeightmapTextureIndex = 0; Allocation.WeightmapTextureChannel = 0; A.WeightmapData.LayerAllocations.Add(Allocation);
    FString HashA, HashB, Error;
    TestTrue(TEXT("Valid source data can be hashed"), DataHash(C, A, HashA, Error));
    FLandscapeLayerComponentData B = A;
    for (auto& P : Heights) { P.B = 244; P.A = 5; }
    for (auto& P : Weights) Swap(P.R, P.G);
    B.HeightmapData.Texture = MakeTexture(Heights); B.WeightmapData.Textures[0] = MakeTexture(Weights);
    B.WeightmapData.LayerAllocations[0].WeightmapTextureChannel = 1;
    TestTrue(TEXT("Repacked data can be hashed"), DataHash(C, B, HashB, Error));
    TestEqual(TEXT("Texture renames, channel repacking and normals do not alter terrain-data identity"), HashA, HashB);
    ++Heights[0].G; B.HeightmapData.Texture = MakeTexture(Heights);
    TestTrue(TEXT("Changed height can be hashed"), DataHash(C, B, HashB, Error));
    TestTrue(TEXT("A single height byte change is detected"), HashA != HashB);
    B.WeightmapData.LayerAllocations[0].WeightmapTextureIndex = 9;
    TestFalse(TEXT("Invalid paint allocation is rejected"), DataHash(C, B, HashB, Error));
    TestEqual(TEXT("Allocation diagnostic is specific"), Error, FString(TEXT("PAINT_ALLOCATION_INVALID")));
    TestWorld->DestroyWorld(false);
    return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeJournalTest, "AIBridgeUE.LandscapeRepair.JournalRoundTrip", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeJournalTest::RunTest(const FString& Parameters)
{
    const FString Path = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Tests"), FGuid::NewGuid().ToString() + TEXT(".json"));
    TSharedPtr<FJsonObject> J = MakeShared<FJsonObject>(); J->SetStringField(TEXT("phase"), TEXT("prepared"));
    TestTrue(TEXT("Pointer-backed journal writes atomically"), AtomicJson(Path, J));
    J->SetStringField(TEXT("phase"), TEXT("saved"));
    TestTrue(TEXT("Existing journal is replaced"), AtomicJson(Path, J));
    FString Text; TSharedPtr<FJsonObject> Loaded;
    const bool Read = FFileHelper::LoadFileToString(Text, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Loaded);
    TestTrue(TEXT("Written journal is readable"), Read);
    if (Read) TestEqual(TEXT("Latest phase persisted"), Arg(Loaded, TEXT("phase")), FString(TEXT("saved")));
    TestFalse(TEXT("Null journal is rejected"), AtomicJson(Path, TSharedPtr<FJsonObject>()));
    IFileManager::Get().Delete(*Path);
    return true;
}
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FAIBridgeLandscapeHeightNormalizePolicyTest, "AIBridgeUE.LandscapeRepair.HeightNormalizePolicy", EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)
bool FAIBridgeLandscapeHeightNormalizePolicyTest::RunTest(const FString&)
{
    auto Comparison = MakeShared<FJsonObject>();
    auto Summary = MakeShared<FJsonObject>();
    Summary->SetNumberField(TEXT("expected_components"), 2);
    Summary->SetNumberField(TEXT("checked_components"), 2);
    Summary->SetNumberField(TEXT("errors"), 0);
    Comparison->SetObjectField(TEXT("summary"), Summary);

    auto Boundary = MakeShared<FJsonObject>();
    Boundary->SetNumberField(TEXT("changed_samples_outside_component_edges"), 0);
    Boundary->SetNumberField(TEXT("capture_errors"), 0);
    Boundary->SetNumberField(TEXT("changed_logical_vertex_groups"), 2);
    Boundary->SetNumberField(TEXT("merged_copies_disagree_groups"), 0);
    Boundary->SetNumberField(TEXT("invalid_groups"), 0);
    Boundary->SetNumberField(TEXT("single_component_groups"), 0);
    Boundary->SetNumberField(TEXT("expected_changed_owner_samples"), 4);
    Boundary->SetNumberField(TEXT("audited_changed_owner_samples"), 4);
    Boundary->SetBoolField(TEXT("all_changed_samples_audited"), true);

    auto HeightReconciled = MakeShared<FJsonObject>();
    HeightReconciled->SetStringField(TEXT("result"), TEXT("reconciled_from_source"));
    HeightReconciled->SetStringField(TEXT("plane"), TEXT("height"));
    auto PaintGenerated = MakeShared<FJsonObject>();
    PaintGenerated->SetStringField(TEXT("result"), TEXT("merged_value_not_in_source"));
    PaintGenerated->SetStringField(TEXT("plane"), TEXT("paint"));
    PaintGenerated->SetStringField(TEXT("layer"), TEXT("/Game/Test/GrassLayer.GrassLayer"));
    TArray<TSharedPtr<FJsonValue>> Groups = {
        MakeShared<FJsonValueObject>(HeightReconciled),
        MakeShared<FJsonValueObject>(PaintGenerated)
    };
    Boundary->SetArrayField(TEXT("groups"), Groups);
    Comparison->SetObjectField(TEXT("boundary_audit"), Boundary);

    FString Error;
    TestTrue(TEXT("Generated paint does not block height-only normalization"), IsSafeHeightNormalizationPrecheck(Comparison, Error));
    TestTrue(TEXT("Safe height-only precheck has no error"), Error.IsEmpty());

    TSet<FString> AllowedLayers;
    TestTrue(TEXT("Generated paint layers are captured from proven precheck"), CollectAllowedGeneratedPaintLayers(Comparison, AllowedLayers, Error));
    TestTrue(TEXT("Captured generated paint layer is present"), AllowedLayers.Contains(TEXT("/Game/Test/GrassLayer.GrassLayer")));

    HeightReconciled->SetStringField(TEXT("plane"), TEXT("paint"));
    TestTrue(TEXT("Post merge accepts proven generated paint plus source-selected paint"), IsSafePostMergeHeightNormalization(Comparison, AllowedLayers, Error));

    PaintGenerated->SetStringField(TEXT("layer"), TEXT("/Game/Test/OtherLayer.OtherLayer"));
    TestFalse(TEXT("Post merge rejects generated paint on an unproven layer"), IsSafePostMergeHeightNormalization(Comparison, AllowedLayers, Error));
    TestTrue(TEXT("Unproven paint layer rejection is specific"), Error.Contains(TEXT("UNPROVEN_GENERATED_PAINT_LAYER")));

    PaintGenerated->SetStringField(TEXT("layer"), TEXT("/Game/Test/GrassLayer.GrassLayer"));
    HeightReconciled->SetStringField(TEXT("plane"), TEXT("height"));
    TestFalse(TEXT("Post merge rejects any remaining height difference group"), IsSafePostMergeHeightNormalization(Comparison, AllowedLayers, Error));
    TestTrue(TEXT("Remaining height group rejection is specific"), Error.Contains(TEXT("HEIGHT_GROUP_REMAINS")));

    PaintGenerated->SetStringField(TEXT("plane"), TEXT("height"));
    TestFalse(TEXT("Generated height must block height-only normalization"), IsSafeHeightNormalizationPrecheck(Comparison, Error));
    TestTrue(TEXT("Generated height rejection identifies height"), Error.Contains(TEXT("height")));
    return true;
}
#endif

FString FileHash(const FString& Filename)
{
    TUniquePtr<FArchive> Reader(IFileManager::Get().CreateFileReader(*Filename));
    if (!Reader) return FString();
    FSHA1 Hash; TArray<uint8> Block; Block.SetNumUninitialized(1024 * 1024);
    while (!Reader->AtEnd())
    {
        const int32 Size = int32(FMath::Min<int64>(Block.Num(), Reader->TotalSize() - Reader->Tell()));
        Reader->Serialize(Block.GetData(), Size); if (Reader->IsError()) return FString();
        Hash.Update(Block.GetData(), Size);
    }
    return Digest(Hash);
}

bool BackupPackages(const FContext& C, const FString& Directory, TArray<TSharedPtr<FJsonValue>>& Entries, FString& Error)
{
    TArray<UPackage*> Packages = C.Packages; Packages.AddUnique(C.World->GetOutermost());
    for (UPackage* P : Packages)
    {
        FString Filename;
        if (!P || !FPackageName::DoesPackageExist(P->GetName(), &Filename)) { Error = TEXT("PACKAGE_FILE_MISSING"); return false; }
        Filename = FPaths::ConvertRelativePathToFull(Filename);
        if (IFileManager::Get().IsReadOnly(*Filename)) { Error = TEXT("PACKAGE_READ_ONLY_CHECKOUT_REQUIRED: ") + Filename; return false; }
        const FString Base = FPaths::ChangeExtension(Filename, TEXT(""));
        TArray<FString> Files = {Filename};
        for (const TCHAR* Extension : {TEXT(".uexp"), TEXT(".ubulk"), TEXT(".uptnl")})
            if (IFileManager::Get().FileExists(*(Base + Extension))) Files.Add(Base + Extension);
        for (const FString& Source : Files)
        {
            FString Relative = Source;
            if (!FPaths::MakePathRelativeTo(Relative, *FPaths::ConvertRelativePathToFull(FPaths::ProjectDir())) || Relative.StartsWith(TEXT("..")))
            { Error = TEXT("PACKAGE_OUTSIDE_PROJECT"); return false; }
            const FString Dest = FPaths::Combine(Directory, TEXT("backup"), Relative);
            IFileManager::Get().MakeDirectory(*FPaths::GetPath(Dest), true);
            const FString Before = FileHash(Source);
            if (Before.IsEmpty() || IFileManager::Get().Copy(*Dest, *Source, false, false) != COPY_OK || FileHash(Dest) != Before)
            { Error = TEXT("BACKUP_VERIFICATION_FAILED"); return false; }
            auto Entry = MakeShared<FJsonObject>();
            Entry->SetStringField(TEXT("package"), P->GetName()); Entry->SetStringField(TEXT("source"), Source);
            Entry->SetStringField(TEXT("backup"), Dest); Entry->SetStringField(TEXT("sha1"), Before);
            Entries.Add(MakeShared<FJsonValueObject>(Entry));
        }
    }
    return true;
}

bool CompareExpected(const FContext& C, const TSharedPtr<FJsonObject>& Expected, bool bCompareMerged, FString& Error)
{
    if (!Expected.IsValid() || Expected->Values.Num() != C.Components.Num()) { Error = TEXT("COMPONENT_COVERAGE_CHANGED"); return false; }
    for (ULandscapeComponent* Component : C.Components)
    {
        FString ExpectedHash, Actual;
        const FLandscapeLayerComponentData* Active = Component->GetLayerData(C.LayerGuid);
        if (!Active || !Expected->TryGetStringField(Component->GetPathName(), ExpectedHash)
            || !DataHash(Component, *Active, Actual, Error) || Actual != ExpectedHash)
        { Error = TEXT("ACTIVE_DATA_MISMATCH: ") + Component->GetPathName() + TEXT(" ") + Error; return false; }
        if (bCompareMerged)
        {
            const auto Merged = FinalData(Component);
            if (!DataHash(Component, Merged, Actual, Error) || Actual != ExpectedHash)
            { Error = TEXT("MERGED_DATA_MISMATCH: ") + Component->GetPathName() + TEXT(" ") + Error; return false; }
        }
    }
    return true;
}
}

void AddCapabilities(TArray<TSharedPtr<FJsonValue>>& Capabilities)
{
    for (const bool bWrite : {false, true})
    {
        auto Cap = MakeShared<FJsonObject>();
        Cap->SetStringField(TEXT("name"), bWrite ? TEXT("repair.landscape_edit_layers") : TEXT("inspect.landscape_edit_layers"));
        Cap->SetStringField(TEXT("version"), TEXT("1.0"));
        Cap->SetBoolField(TEXT("write"), bWrite); Cap->SetBoolField(TEXT("host_mutation"), bWrite);
        Cap->SetBoolField(TEXT("manages_checkpoint"), bWrite);
        Cap->SetBoolField(TEXT("rollback"), false); Cap->SetBoolField(TEXT("verification"), true);
        Cap->SetBoolField(TEXT("long_running"), true);
        Cap->SetStringField(TEXT("risk"), bWrite ? TEXT("L2") : TEXT("L1"));
        Capabilities.Add(MakeShared<FJsonValueObject>(Cap));
    }
}

TSharedPtr<FJsonObject> Execute(const TSharedPtr<FJsonObject>& Arguments, bool bDryRun, FString& OutErrorCode, FString& OutErrorMessage)
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("schema_version"), TEXT("aibridge_ue_landscape_repair/1"));
    Report->SetStringField(TEXT("engine"), FEngineVersion::Current().ToString());
    Report->SetBoolField(TEXT("ok"), false); Report->SetBoolField(TEXT("map_saved"), false);
    const auto Fail = [&](const FString& Error) -> TSharedPtr<FJsonObject>
    {
        OutErrorCode = Error.Left(Error.Find(TEXT(":")) >= 0 ? Error.Find(TEXT(":")) : Error.Len());
        OutErrorMessage = Error; Report->SetStringField(TEXT("error"), Error);
        UE_LOG(LogAIBridgeLandscapeRepair, Error, TEXT("%s"), *Error); return Report;
    };
    FString Action = Arg(Arguments, TEXT("action"), TEXT("scan"));
    if (bDryRun) Action = TEXT("scan");
    if (Action != TEXT("scan") && Action != TEXT("apply") && Action != TEXT("normalize") && Action != TEXT("finalize") && Action != TEXT("verify")) return Fail(TEXT("ACTION_UNSUPPORTED"));
    const FString Strategy = Arg(Arguments, TEXT("strategy"), TEXT("final_on_load_v1"));
    const bool bDiskFinal = Strategy == TEXT("disk_final_offline_v1");
    const bool bEditStable = Strategy == TEXT("edit_stable_height_v1");
    if (Strategy != TEXT("final_on_load_v1") && !bDiskFinal && !bEditStable) return Fail(TEXT("STRATEGY_UNSUPPORTED"));
    if ((bDiskFinal || bEditStable) && !IsRunningCommandlet()) return Fail(TEXT("OFFLINE_STRATEGY_COMMANDLET_ONLY"));
    if (bEditStable && Action != TEXT("normalize") && Action != TEXT("finalize") && Action != TEXT("verify")) return Fail(TEXT("EDIT_STABLE_ACTION_UNSUPPORTED"));
    if (!bEditStable && Action == TEXT("normalize")) return Fail(TEXT("NORMALIZE_STRATEGY_REQUIRED"));
    Report->SetStringField(TEXT("strategy"), Strategy);
    FContext C; FString Error;
    if (!Resolve(Arguments, C, Error)) return Fail(Error);
    Report->SetStringField(TEXT("map"), C.Map); Report->SetStringField(TEXT("landscape_path"), C.Parent->GetPathName());
    Report->SetStringField(TEXT("target_layer_guid"), C.LayerGuid.ToString(EGuidFormats::Digits));
    Report->SetNumberField(TEXT("loaded_proxy_count"), C.Proxies.Num()); Report->SetNumberField(TEXT("component_count"), C.Components.Num());
    Report->SetBoolField(TEXT("layers_up_to_date"), C.Parent->IsUpToDate());
    Report->SetStringField(TEXT("coverage_basis"), TEXT("caller_expected_proxy_count_and_loaded_component_arrays"));
    Report->SetStringField(TEXT("layer_policy_error"), C.LayerPolicyError);
    if (Action == TEXT("normalize"))
    {
        if (!bEditStable) return Fail(TEXT("NORMALIZE_STRATEGY_REQUIRED"));
        if (!BoolArg(Arguments, TEXT("allow_overwrite_target"))) return Fail(TEXT("TARGET_OVERWRITE_NOT_AUTHORIZED"));
        if (!FApp::CanEverRender()) return Fail(TEXT("RENDERING_EDITOR_REQUIRED"));
        if (!C.LayerPolicyError.IsEmpty()) return Fail(C.LayerPolicyError);
        if (IntArg(Arguments, TEXT("expected_proxy_count")) != C.Proxies.Num()) return Fail(TEXT("PROXY_COVERAGE_MISMATCH"));

        int32 RemainingOrphans = 0;
        int32 ObsoleteEntries = 0;
        for (ULandscapeComponent* Component : C.Components)
        {
            const FLayerMap* Active = ReadLayerMap(Component, TEXT("LayersData"));
            const FLayerMap* Obsolete = ReadLayerMap(Component, TEXT("ObsoleteEditLayerData"));
            if (!Active || !Obsolete) return Fail(TEXT("ENGINE_LAYER_STORAGE_UNSUPPORTED"));
            for (const auto& Pair : *Active) if (!C.Parent->GetEditLayerConst(Pair.Key)) ++RemainingOrphans;
            ObsoleteEntries += Obsolete->Num();
        }
        Report->SetNumberField(TEXT("orphan_active_layers"), RemainingOrphans);
        Report->SetNumberField(TEXT("obsolete_entries"), ObsoleteEntries);
        if (RemainingOrphans != 0 || ObsoleteEntries != 0) return Fail(TEXT("NORMALIZE_REQUIRES_COMPLETED_MIGRATION"));

        auto Comparison = AnalyzeLandscapeComparison(C.Components, C.LayerGuid);
        Comparison->SetBoolField(TEXT("all_active_hashes_match_captured_source"), true);
        if (!Comparison->HasTypedField<EJson::Object>(TEXT("summary"))) return Fail(TEXT("NORMALIZE_COMPARISON_INVALID"));
        const TSharedPtr<FJsonObject> Summary = Comparison->GetObjectField(TEXT("summary"));
        double Checked = 0.0, CompareErrors = 0.0;
        if (!Summary->TryGetNumberField(TEXT("checked_components"), Checked)
            || !Summary->TryGetNumberField(TEXT("errors"), CompareErrors)
            || Checked != C.Components.Num() || CompareErrors != 0.0)
            return Fail(TEXT("NORMALIZE_COMPARISON_INCOMPLETE"));

        const FString PrecheckPath = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Offline/normalize_precheck.json"));
        if (!AtomicJson(PrecheckPath, Comparison)) return Fail(TEXT("NORMALIZE_PRECHECK_WRITE_FAILED"));
        Report->SetStringField(TEXT("pre_normalize_comparison"), PrecheckPath);
        Report->SetObjectField(TEXT("pre_normalize_summary"), Summary);

        FString PrecheckError;
        if (!IsSafeHeightNormalizationPrecheck(Comparison, PrecheckError))
            return Fail(TEXT("NORMALIZE_HEIGHT_SOURCE_NOT_PROVEN_SAFE: ") + PrecheckError);
        TSet<FString> AllowedGeneratedPaintLayers;
        if (!CollectAllowedGeneratedPaintLayers(Comparison, AllowedGeneratedPaintLayers, PrecheckError))
            return Fail(TEXT("NORMALIZE_ALLOWED_PAINT_CAPTURE_FAILED: ") + PrecheckError);
        TArray<FString> OrderedAllowedPaint = AllowedGeneratedPaintLayers.Array();
        OrderedAllowedPaint.Sort();
        TArray<TSharedPtr<FJsonValue>> AllowedPaintJson;
        for (const FString& Layer : OrderedAllowedPaint) AllowedPaintJson.Add(MakeShared<FJsonValueString>(Layer));
        Report->SetBoolField(TEXT("pre_normalize_height_safe"), true);
        Report->SetArrayField(TEXT("allowed_generated_paint_layers"), AllowedPaintJson);

        auto ExpectedHeight = MakeShared<FJsonObject>();
        auto ExpectedActivePaint = MakeShared<FJsonObject>();
        auto ExpectedFinalPaint = MakeShared<FJsonObject>();
        for (ULandscapeComponent* Component : C.Components)
        {
            const FLandscapeLayerComponentData* Active = Component->GetLayerData(C.LayerGuid);
            if (!Active) return Fail(TEXT("NORMALIZE_ACTIVE_DATA_MISSING: ") + Component->GetPathName());
            const FLandscapeLayerComponentData Final = FinalData(Component);
            FString HeightValue, ActivePaintValue, FinalPaintValue;
            if (!HeightHash(Component, Final, HeightValue, Error))
                return Fail(TEXT("NORMALIZE_FINAL_HEIGHT_HASH_FAILED: ") + Component->GetPathName() + TEXT(" ") + Error);
            if (!PaintHash(Component, *Active, ActivePaintValue, Error))
                return Fail(TEXT("NORMALIZE_ACTIVE_PAINT_HASH_FAILED: ") + Component->GetPathName() + TEXT(" ") + Error);
            if (!PaintHash(Component, Final, FinalPaintValue, Error))
                return Fail(TEXT("NORMALIZE_FINAL_PAINT_HASH_FAILED: ") + Component->GetPathName() + TEXT(" ") + Error);
            ExpectedHeight->SetStringField(Component->GetPathName(), HeightValue);
            ExpectedActivePaint->SetStringField(Component->GetPathName(), ActivePaintValue);
            ExpectedFinalPaint->SetStringField(Component->GetPathName(), FinalPaintValue);
        }

        const FString Run = FGuid::NewGuid().ToString(EGuidFormats::Digits);
        const FString Directory = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Runs"), Run);
        const FString JournalPath = FPaths::Combine(Directory, TEXT("journal.json"));
        auto Journal = MakeShared<FJsonObject>();
        Journal->SetStringField(TEXT("schema_version"), TEXT("aibridge_landscape_run/1"));
        Journal->SetStringField(TEXT("run_id"), Run);
        Journal->SetStringField(TEXT("map"), C.Map);
        Journal->SetStringField(TEXT("parent"), C.Parent->GetPathName());
        Journal->SetStringField(TEXT("guid"), C.LayerGuid.ToString(EGuidFormats::Digits));
        Journal->SetStringField(TEXT("strategy"), Strategy);
        Journal->SetObjectField(TEXT("expected_height"), ExpectedHeight);
        Journal->SetObjectField(TEXT("expected_active_paint"), ExpectedActivePaint);
        Journal->SetObjectField(TEXT("expected_final_paint"), ExpectedFinalPaint);
        Journal->SetArrayField(TEXT("allowed_generated_paint_layers"), AllowedPaintJson);
        Journal->SetNumberField(TEXT("proxy_count"), C.Proxies.Num());
        Journal->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());

        TArray<TSharedPtr<FJsonValue>> Backups;
        if (!BackupPackages(C, Directory, Backups, Error)) return Fail(Error);
        Journal->SetArrayField(TEXT("backups"), Backups);
        Journal->SetStringField(TEXT("phase"), TEXT("backed_up"));
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED"));

        const FString ComparisonPath = FPaths::Combine(Directory, TEXT("pre_normalize_comparison.json"));
        if (!AtomicJson(ComparisonPath, Comparison)) return Fail(TEXT("NORMALIZE_COMPARISON_REPORT_WRITE_FAILED"));
        Journal->SetStringField(TEXT("pre_normalize_comparison"), ComparisonPath);
        Journal->SetBoolField(TEXT("pre_normalize_height_safe"), true);
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED"));

        C.Parent->Modify();
        int32 CopiedHeightTextures = 0;
        if (!CopyFinalHeightToEditLayer(C, CopiedHeightTextures, Error))
        {
            Journal->SetStringField(TEXT("phase"), TEXT("normalize_height_copy_failed"));
            Journal->SetStringField(TEXT("error"), Error);
            AtomicJson(JournalPath, Journal);
            return Fail(Error);
        }

        if (!CompareHeightStableExpected(C, ExpectedHeight, ExpectedActivePaint, ExpectedFinalPaint, false, Error))
        {
            Journal->SetStringField(TEXT("phase"), TEXT("normalize_height_verification_failed"));
            Journal->SetStringField(TEXT("error"), Error);
            AtomicJson(JournalPath, Journal);
            return Fail(Error);
        }

        C.Parent->RequestLayersContentUpdateForceAll();
        Journal->SetNumberField(TEXT("normalized_height_textures"), CopiedHeightTextures);
        Journal->SetStringField(TEXT("phase"), TEXT("normalized_pending_merge"));
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_MUTATION"));
        Report->SetStringField(TEXT("run_id"), Run);
        Report->SetStringField(TEXT("journal"), JournalPath);
        Report->SetStringField(TEXT("comparison_report"), ComparisonPath);
        Report->SetNumberField(TEXT("normalized_height_textures"), CopiedHeightTextures);
        Report->SetBoolField(TEXT("paint_mutated"), false);
        Report->SetBoolField(TEXT("ok"), true);
        Report->SetStringField(TEXT("phase"), TEXT("normalized_pending_merge"));
        return Report;
    }
    if (Action == TEXT("scan") || Action == TEXT("apply"))
    {
        auto Expected = MakeShared<FJsonObject>();
        TArray<FString> Records; Records.Add(C.Map); Records.Add(C.Parent->GetPathName()); Records.Add(C.LayerGuid.ToString());
        TArray<TSharedPtr<FJsonValue>> Details;
        int32 Missing = 0, Recoverable = 0, InvalidActive = 0;
        FString DataError;
        for (ULandscapeComponent* Component : C.Components)
        {
            const FLayerMap* Obsolete = ReadLayerMap(Component, TEXT("ObsoleteEditLayerData"));
            const FLayerMap* Active = ReadLayerMap(Component, TEXT("LayersData"));
            if (!Obsolete || !Active) return Fail(TEXT("ENGINE_LAYER_STORAGE_UNSUPPORTED"));
            int32 ComponentOrphans = 0;
            for (const auto& Pair : *Active)
            {
                if (!C.Parent->GetEditLayerConst(Pair.Key))
                {
                    ++InvalidActive; ++ComponentOrphans;
                    Records.Add(Component->GetPathName() + TEXT("|orphan=") + Pair.Key.ToString());
                }
            }
            FLandscapeLayerComponentData DiskSource;
            const FLandscapeLayerComponentData* Source = Obsolete->Find(FGuid());
            if (bDiskFinal)
            {
                Source = nullptr;
                const bool bBeforeMerge = BoolArg(Arguments, TEXT("disk_final_before_merge")) && !C.Parent->IsUpToDate();
                if (CanUseOfflineDiskFinal(IsRunningCommandlet(), bBeforeMerge, ComponentOrphans) && Obsolete->IsEmpty())
                {
                    DiskSource = FinalData(Component);
                    Source = &DiskSource;
                }
                else DataError = TEXT("OFFLINE_DISK_SOURCE_PRECONDITION_FAILED: ") + Component->GetPathName();
            }
            const FLandscapeLayerComponentData* Destination = Component->GetLayerData(C.LayerGuid);
            auto Detail = MakeShared<FJsonObject>(); Detail->SetStringField(TEXT("component"), Component->GetPathName());
            Detail->SetNumberField(TEXT("obsolete_entries"), Obsolete->Num()); Detail->SetNumberField(TEXT("active_entries"), Active->Num());
            FString SourceHash, DestinationHash;
            if (!Source || !Source->IsInitialized()) ++Missing;
            else if (!DataHash(Component, *Source, SourceHash, Error)) DataError = Error + TEXT(": ") + Component->GetPathName();
            else
            {
                ++Recoverable; Expected->SetStringField(Component->GetPathName(), SourceHash);
                Detail->SetStringField(TEXT("source_hash"), SourceHash);
                Detail->SetNumberField(TEXT("paint_layer_count"), Source->WeightmapData.LayerAllocations.Num());
                UTexture2D* S = Source->HeightmapData.Texture; UTexture2D* D = Destination ? Destination->HeightmapData.Texture.Get() : nullptr;
                // Native copy uses memcpy, so the destination must match before entering it.
                if (!S || !D || S->Source.GetSizeX() != D->Source.GetSizeX() || S->Source.GetSizeY() != D->Source.GetSizeY()
                    || S->Source.GetFormat() != D->Source.GetFormat()) DataError = TEXT("HEIGHTMAP_COPY_SHAPE_MISMATCH");
            }
            if (Destination && !DataHash(Component, *Destination, DestinationHash, Error)) DataError = Error;
            Detail->SetStringField(TEXT("destination_hash"), DestinationHash);
            Records.Add(Component->GetPathName() + TEXT("|source=") + SourceHash + TEXT("|destination=") + DestinationHash
                + TEXT("|transform=") + Component->GetComponentTransform().ToString());
            Details.Add(MakeShared<FJsonValueObject>(Detail));
        }
        const FString Plan = BuildPlanFingerprint(Records);
        Report->SetStringField(TEXT("plan_hash"), Plan); Report->SetNumberField(TEXT("missing_final_snapshots"), Missing);
        Report->SetNumberField(TEXT("recoverable_components"), Recoverable); Report->SetNumberField(TEXT("orphan_active_layers"), InvalidActive);
        Report->SetStringField(TEXT("data_error"), DataError); Report->SetArrayField(TEXT("components"), Details);
        const bool bCoverage = IntArg(Arguments, TEXT("expected_proxy_count")) == C.Proxies.Num();
        const bool bSourceLayout = bDiskFinal ? InvalidActive == C.Components.Num() : InvalidActive == 0;
        const bool bReady = bCoverage && C.LayerPolicyError.IsEmpty() && DataError.IsEmpty() && Missing == 0 && Recoverable > 0 && bSourceLayout;
        Report->SetBoolField(TEXT("ready_to_apply"), bReady);
        if (Action == TEXT("scan")) { Report->SetBoolField(TEXT("ok"), true); Report->SetStringField(TEXT("phase"), TEXT("scanned")); return Report; }
        if (!BoolArg(Arguments, TEXT("allow_overwrite_target"))) return Fail(TEXT("TARGET_OVERWRITE_NOT_AUTHORIZED"));
        FApplyGuardState Guard; Guard.CurrentPlanHash = Plan; Guard.ExpectedPlanHash = Arg(Arguments, TEXT("expected_plan_hash"));
        Guard.bHasPersistentTargetLayer = C.LayerGuid.IsValid() && C.LayerPolicyError.IsEmpty();
        Guard.MissingFinalDataCount = Missing; Guard.RecoverableComponentCount = Recoverable;
        if (!(Error = ValidateApplyGuard(Guard)).IsEmpty()) return Fail(Error);
        if (!bReady) return Fail(TEXT("PREFLIGHT_FAILED: ") + DataError + TEXT(" ") + C.LayerPolicyError);
        if (!FApp::CanEverRender()) return Fail(TEXT("RENDERING_EDITOR_REQUIRED"));
        const FString Run = FGuid::NewGuid().ToString(EGuidFormats::Digits);
        const FString Directory = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Runs"), Run);
        const FString JournalPath = FPaths::Combine(Directory, TEXT("journal.json"));
        auto Journal = MakeShared<FJsonObject>();
        Journal->SetStringField(TEXT("schema_version"), TEXT("aibridge_landscape_run/1")); Journal->SetStringField(TEXT("run_id"), Run);
        Journal->SetStringField(TEXT("map"), C.Map); Journal->SetStringField(TEXT("parent"), C.Parent->GetPathName());
        Journal->SetStringField(TEXT("guid"), C.LayerGuid.ToString(EGuidFormats::Digits)); Journal->SetStringField(TEXT("plan_hash"), Plan);
        Journal->SetObjectField(TEXT("expected_components"), Expected); Journal->SetNumberField(TEXT("proxy_count"), C.Proxies.Num());
        Journal->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
        TArray<TSharedPtr<FJsonValue>> Backups;
        if (!BackupPackages(C, Directory, Backups, Error)) return Fail(Error);
        Journal->SetArrayField(TEXT("backups"), Backups); Journal->SetStringField(TEXT("phase"), TEXT("backed_up"));
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED"));
        Report->SetStringField(TEXT("run_id"), Run); Report->SetStringField(TEXT("journal"), JournalPath);
        FScopedSlowTask Progress(C.Proxies.Num(), FText::FromString(TEXT("AI Bridge: restoring Landscape edit layer data")));
        Progress.MakeDialog(false); C.Parent->Modify(); C.Packages[0]->MarkPackageDirty();
        Journal->SetStringField(TEXT("strategy"), Strategy);
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED"));
        int32 Copied = 0;
        for (ALandscapeProxy* Proxy : C.Proxies)
        {
            if (bDiskFinal)
            {
                const FPlatformMemoryStats Memory = FPlatformMemory::GetStats();
                if (Memory.AvailablePhysical < 8ull * 1024 * 1024 * 1024 || Memory.UsedVirtual >= 32ull * 1024 * 1024 * 1024)
                {
                    Journal->SetStringField(TEXT("phase"), TEXT("memory_guard_stopped_no_save"));
                    AtomicJson(JournalPath, Journal); return Fail(TEXT("MEMORY_GUARD_DURING_COPY_NO_SAVE"));
                }
            }
            Progress.EnterProgressFrame(1);
            C.Parent->CopyDataToEditLayer(Proxy, FGuid(), C.LayerGuid, !bDiskFinal);
            ActorPackage(Proxy)->MarkPackageDirty(); ++Copied;
            Journal->SetNumberField(TEXT("copied_proxies"), Copied); Journal->SetStringField(TEXT("phase"), TEXT("copying"));
            if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_MUTATION"));
            if (Copied % 16 == 0) UE_LOG(LogAIBridgeLandscapeRepair, Display, TEXT("Copied %d/%d proxies"), Copied, C.Proxies.Num());
        }
        if (!CompareExpected(C, Expected, false, Error))
        { Journal->SetStringField(TEXT("phase"), TEXT("copy_verification_failed")); Journal->SetStringField(TEXT("error"), Error); AtomicJson(JournalPath, Journal); return Fail(Error); }
        if (bDiskFinal)
        {
            // Raw disk final is now verified in the persistent destination. Only now
            // remove mismatched layer entries through the native API, before merge.
            int32 Removed = 0;
            for (ULandscapeComponent* Component : C.Components)
            {
                const FLayerMap* Active = ReadLayerMap(Component, TEXT("LayersData"));
                if (!Active) return Fail(TEXT("ENGINE_LAYER_STORAGE_UNSUPPORTED"));
                TArray<FGuid> Orphans;
                for (const auto& Pair : *Active) if (!C.Parent->GetEditLayerConst(Pair.Key)) Orphans.Add(Pair.Key);
                for (const FGuid& Guid : Orphans) { Component->RemoveLayerData(Guid); ++Removed; }
            }
            Journal->SetNumberField(TEXT("orphan_entries_removed_after_verified_copy"), Removed);
            Report->SetNumberField(TEXT("orphan_entries_removed_after_verified_copy"), Removed);
            C.Parent->RequestLayersContentUpdateForceAll();
        }
        Journal->SetStringField(TEXT("phase"), TEXT("applied_pending_merge"));
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_MUTATION"));
        Report->SetBoolField(TEXT("ok"), true); Report->SetStringField(TEXT("phase"), TEXT("applied_pending_merge"));
        Report->SetBoolField(TEXT("source_data_retained"), true); return Report;
    }
    FGuid RunGuid; const FString Run = Arg(Arguments, TEXT("run_id"));
    if (!FGuid::ParseExact(Run, EGuidFormats::Digits, RunGuid)) return Fail(TEXT("RUN_ID_INVALID"));
    const FString JournalPath = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Runs"), Run, TEXT("journal.json"));
    FString Text; TSharedPtr<FJsonObject> Journal;
    if (!FFileHelper::LoadFileToString(Text, *JournalPath) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), Journal) || !Journal.IsValid()) return Fail(TEXT("JOURNAL_UNAVAILABLE"));
    Report->SetStringField(TEXT("run_id"), Run); Report->SetStringField(TEXT("journal"), JournalPath);
    if (Arg(Journal, TEXT("schema_version")) != TEXT("aibridge_landscape_run/1") || Arg(Journal, TEXT("map")) != C.Map
        || Arg(Journal, TEXT("parent")) != C.Parent->GetPathName() || Arg(Journal, TEXT("guid")) != C.LayerGuid.ToString(EGuidFormats::Digits)
        || IntArg(Journal, TEXT("proxy_count")) != C.Proxies.Num() || !C.LayerPolicyError.IsEmpty()) return Fail(TEXT("JOURNAL_TARGET_CHANGED"));
    if (Arg(Journal, TEXT("strategy"), TEXT("final_on_load_v1")) != Strategy) return Fail(TEXT("JOURNAL_STRATEGY_CHANGED"));
    int32 RemainingOrphans = 0;
    for (ULandscapeComponent* Component : C.Components)
    {
        const FLayerMap* Active = ReadLayerMap(Component, TEXT("LayersData"));
        if (!Active) return Fail(TEXT("ENGINE_LAYER_STORAGE_UNSUPPORTED"));
        for (const auto& Pair : *Active) if (!C.Parent->GetEditLayerConst(Pair.Key)) ++RemainingOrphans;
    }
    Report->SetNumberField(TEXT("orphan_active_layers"), RemainingOrphans);
    if (RemainingOrphans != 0) return Fail(TEXT("ORPHAN_LAYERS_REMAIN_NO_SAVE"));
    TSharedPtr<FJsonObject> Expected;
    TSharedPtr<FJsonObject> ExpectedHeight;
    TSharedPtr<FJsonObject> ExpectedActivePaint;
    TSharedPtr<FJsonObject> ExpectedFinalPaint;
    TSet<FString> AllowedGeneratedPaintLayers;
    if (bEditStable)
    {
        if (!Journal->HasTypedField<EJson::Object>(TEXT("expected_height"))
            || !Journal->HasTypedField<EJson::Object>(TEXT("expected_active_paint"))
            || !Journal->HasTypedField<EJson::Object>(TEXT("expected_final_paint"))
            || !Journal->HasTypedField<EJson::Array>(TEXT("allowed_generated_paint_layers")))
            return Fail(TEXT("JOURNAL_HEIGHT_STABLE_EXPECTED_INVALID"));
        ExpectedHeight = Journal->GetObjectField(TEXT("expected_height"));
        ExpectedActivePaint = Journal->GetObjectField(TEXT("expected_active_paint"));
        ExpectedFinalPaint = Journal->GetObjectField(TEXT("expected_final_paint"));
        for (const TSharedPtr<FJsonValue>& Value : Journal->GetArrayField(TEXT("allowed_generated_paint_layers")))
        {
            FString Layer;
            if (!Value.IsValid() || !Value->TryGetString(Layer) || Layer.IsEmpty())
                return Fail(TEXT("JOURNAL_ALLOWED_PAINT_LAYER_INVALID"));
            AllowedGeneratedPaintLayers.Add(Layer);
        }
    }
    else
    {
        if (!Journal->HasTypedField<EJson::Object>(TEXT("expected_components"))) return Fail(TEXT("JOURNAL_INVALID"));
        Expected = Journal->GetObjectField(TEXT("expected_components"));
    }
    if (!FApp::CanEverRender()) return Fail(TEXT("RENDERING_EDITOR_REQUIRED"));
    if (Action == TEXT("finalize"))
    {
        const FString Phase = Arg(Journal, TEXT("phase"));
        const bool bReadyPhase = bEditStable
            ? (Phase == TEXT("normalized_pending_merge") || Phase == TEXT("saving") || Phase == TEXT("save_failed"))
            : (Phase == TEXT("applied_pending_merge") || Phase == TEXT("saving") || Phase == TEXT("save_failed"));
        if (!bReadyPhase) return Fail(TEXT("RUN_NOT_READY_FOR_FINALIZE"));
        if (!BoolArg(Arguments, TEXT("save_packages"))) return Fail(TEXT("SAVE_NOT_AUTHORIZED"));
        if (bEditStable)
        {
            if (!CompareHeightStableExpected(C, ExpectedHeight, ExpectedActivePaint, ExpectedFinalPaint, false, Error))
                return Fail(Error);
        }
        else if (!CompareExpected(C, Expected, false, Error)) return Fail(Error);
        C.Parent->ForceUpdateLayersContent();
    }
    if (!C.Parent->IsUpToDate()) return Fail(TEXT("MERGE_NOT_COMPLETE"));
    if (bEditStable)
    {
        if (!CompareHeightStableExpected(C, ExpectedHeight, ExpectedActivePaint, ExpectedFinalPaint, true, Error))
            return Fail(TEXT("EDIT_STABLE_HEIGHT_STRICT_MISMATCH: ") + Error);

        const auto PostComparison = AnalyzeLandscapeComparison(C.Components, C.LayerGuid);
        PostComparison->SetBoolField(TEXT("all_active_hashes_match_captured_source"), true);
        const FString PostComparisonPath = FPaths::Combine(FPaths::GetPath(JournalPath), TEXT("post_normalize_comparison.json"));
        if (!AtomicJson(PostComparisonPath, PostComparison))
            return Fail(TEXT("POST_NORMALIZE_COMPARISON_WRITE_FAILED_NO_SAVE"));
        FString PaintAuditError;
        if (!IsSafePostMergeHeightNormalization(PostComparison, AllowedGeneratedPaintLayers, PaintAuditError))
        {
            Report->SetStringField(TEXT("post_normalize_comparison"), PostComparisonPath);
            Report->SetStringField(TEXT("paint_semantic_audit_rejection"), PaintAuditError);
            return Fail(TEXT("EDIT_STABLE_PAINT_SEMANTIC_MISMATCH: ") + PaintAuditError);
        }
        Journal->SetStringField(TEXT("post_normalize_comparison"), PostComparisonPath);
        Journal->SetBoolField(TEXT("height_strictly_stable"), true);
        Journal->SetBoolField(TEXT("active_paint_preserved"), true);
        Journal->SetBoolField(TEXT("final_paint_semantically_safe"), true);
        if (!AtomicJson(JournalPath, Journal))
            return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_HEIGHT_PAINT_AUDIT"));
        Report->SetStringField(TEXT("post_normalize_comparison"), PostComparisonPath);
        Report->SetBoolField(TEXT("height_strictly_stable"), true);
        Report->SetBoolField(TEXT("active_paint_preserved"), true);
        Report->SetBoolField(TEXT("final_paint_semantically_safe"), true);
    }
    else if (!CompareExpected(C, Expected, true, Error))
    {
        const FString OriginalError = Error;
        bool bAcceptedBySharedVertexAudit = false;
        FString ActiveError;
        if (CompareExpected(C, Expected, false, ActiveError))
        {
            const auto Comparison = AnalyzeLandscapeComparison(C.Components, C.LayerGuid);
            Comparison->SetBoolField(TEXT("all_active_hashes_match_captured_source"), true);
            const FString ComparisonPath = FPaths::Combine(FPaths::GetPath(JournalPath), TEXT("comparison.json"));
            Report->SetStringField(TEXT("comparison_report"), ComparisonPath);
            Report->SetObjectField(TEXT("comparison_summary"), Comparison->GetObjectField(TEXT("summary")));
            if (!AtomicJson(ComparisonPath, Comparison)) return Fail(TEXT("COMPARISON_REPORT_WRITE_FAILED_NO_SAVE"));
            FString AuditRejection;
            bAcceptedBySharedVertexAudit = IsSafeMergedComparisonForSave(Comparison, AuditRejection);
            Report->SetBoolField(TEXT("merged_data_accepted_by_shared_vertex_audit"), bAcceptedBySharedVertexAudit);
            if (bAcceptedBySharedVertexAudit)
            {
                Report->SetStringField(TEXT("merged_data_acceptance"), TEXT("complete_source_selected_shared_edges"));
                Journal->SetBoolField(TEXT("merged_data_accepted_by_shared_vertex_audit"), true);
                Journal->SetStringField(TEXT("merged_data_acceptance"), TEXT("complete_source_selected_shared_edges"));
                Journal->SetStringField(TEXT("merged_comparison_report"), ComparisonPath);
                if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_BOUNDARY_AUDIT"));
            }
            else Report->SetStringField(TEXT("merged_data_audit_rejection"), AuditRejection);
        }
        else Report->SetStringField(TEXT("active_comparison_error"), ActiveError);
        if (!bAcceptedBySharedVertexAudit) return Fail(OriginalError);
    }
    if (Action == TEXT("verify"))
    {
        int32 ObsoleteCount = 0;
        for (ULandscapeComponent* Component : C.Components)
        {
            const auto* M = ReadLayerMap(Component, TEXT("ObsoleteEditLayerData")); if (!M) return Fail(TEXT("ENGINE_LAYER_STORAGE_UNSUPPORTED"));
            ObsoleteCount += M->Num();
        }
        Report->SetNumberField(TEXT("obsolete_entries"), ObsoleteCount);
        Report->SetBoolField(TEXT("ok"), true); Report->SetStringField(TEXT("phase"), TEXT("data_verified"));
        Report->SetStringField(TEXT("journal_phase"), Arg(Journal, TEXT("phase")));
        Report->SetBoolField(TEXT("reopen_validation_passed"), ObsoleteCount == 0 && Arg(Journal, TEXT("phase")) == TEXT("saved")); return Report;
    }
    TArray<TSharedPtr<FJsonValue>> Outcomes;
    FScopedSlowTask Progress(C.Packages.Num(), FText::FromString(TEXT("AI Bridge: saving verified Landscape packages"))); Progress.MakeDialog(false);
    for (UPackage* Package : C.Packages)
    {
        Progress.EnterProgressFrame(1);
        FString Filename;
        if (!FPackageName::DoesPackageExist(Package->GetName(), &Filename) || IFileManager::Get().IsReadOnly(*Filename)) return Fail(TEXT("PACKAGE_SAVE_PREFLIGHT_FAILED"));
        Journal->SetStringField(TEXT("phase"), TEXT("saving")); Journal->SetStringField(TEXT("saving_package"), Package->GetName());
        if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED"));
        Package->MarkPackageDirty(); TArray<UPackage*> One = {Package};
        const bool bSaved = UEditorLoadingAndSavingUtils::SavePackages(One, false);
        auto Entry = MakeShared<FJsonObject>(); Entry->SetStringField(TEXT("package"), Package->GetName());
        Entry->SetBoolField(TEXT("saved"), bSaved); Entry->SetStringField(TEXT("sha1"), FileHash(Filename));
        Outcomes.Add(MakeShared<FJsonValueObject>(Entry)); Journal->SetArrayField(TEXT("save_results"), Outcomes);
        if (!bSaved) { Journal->SetStringField(TEXT("phase"), TEXT("save_failed")); AtomicJson(JournalPath, Journal); return Fail(TEXT("PACKAGE_SAVE_FAILED: ") + Package->GetName()); }
    }
    Journal->SetStringField(TEXT("phase"), TEXT("saved")); Journal->SetStringField(TEXT("saved_utc"), FDateTime::UtcNow().ToIso8601());
    if (!AtomicJson(JournalPath, Journal)) return Fail(TEXT("JOURNAL_WRITE_FAILED_AFTER_SAVE"));
    Report->SetArrayField(TEXT("save_results"), Outcomes); Report->SetBoolField(TEXT("ok"), true);
    Report->SetBoolField(TEXT("map_saved"), true); Report->SetStringField(TEXT("phase"), TEXT("saved_requires_reopen_verification"));
    return Report;
}
}
