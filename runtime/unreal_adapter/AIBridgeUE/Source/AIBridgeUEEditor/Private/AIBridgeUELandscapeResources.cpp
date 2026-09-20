#include "AIBridgeUELandscapeResources.h"
#include "AIBridgeUELandscapeResourcePolicy.h"
#include "Engine/Texture2D.h"
#include "EngineUtils.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMemory.h"
#include "HAL/PlatformTime.h"
#include "LandscapeComponent.h"
#include "LandscapeProxy.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "RenderingThread.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "TextureResource.h"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeLandscapeResources, Log, All);
namespace AIBridgeUE::LandscapeRepair
{
namespace
{
bool SaveResourceReport(const FString& Path, const TSharedPtr<FJsonObject>& Json)
{
    FString Text;
    if (!FJsonSerializer::Serialize(Json.ToSharedRef(), TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&Text))) return false;
    if (!FFileHelper::SaveStringToFile(Text, *(Path + TEXT(".tmp")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) return false;
    return IFileManager::Get().Move(*Path, *(Path + TEXT(".tmp")), true, false, false, true);
}
bool SourceHash(UTexture2D* Texture, FString& Hash)
{
    if (!Texture || !Texture->Source.IsValid() || Texture->Source.GetNumMips() <= 0) return false;
    TArray64<uint8> Bytes;
    if (!Texture->Source.GetMipData(Bytes, 0) || Bytes.IsEmpty()) return false;
    FSHAHash Digest; FSHA1::HashBuffer(Bytes.GetData(), Bytes.Num(), Digest.Hash);
    Hash = Digest.ToString(); return true;
}
TSharedPtr<FJsonObject> Describe(UTexture2D* Texture)
{
    auto J = MakeShared<FJsonObject>();
    FTexturePlatformData* Data = Texture->GetPlatformData();
    FTextureResource* Resource = Texture->GetResource();
    J->SetStringField(TEXT("texture"), Texture->GetPathName());
    J->SetNumberField(TEXT("source_mips"), Texture->Source.GetNumMips());
    J->SetNumberField(TEXT("platform_mips"), Data ? Data->Mips.Num() : 0);
    J->SetNumberField(TEXT("platform_format"), Data ? int32(Data->PixelFormat) : int32(PF_Unknown));
    J->SetBoolField(TEXT("resource_valid"), Resource != nullptr);
    J->SetBoolField(TEXT("rhi_valid"), Resource && Resource->TextureRHI.IsValid());
    J->SetBoolField(TEXT("default_resource"), Texture->IsDefaultTexture());
    return J;
}
ETextureReadiness Readiness(UTexture2D* Texture, bool SourceValid)
{
    FTexturePlatformData* Data = Texture->GetPlatformData();
    FTextureResource* Resource = Texture->GetResource();
    return EvaluateTextureReadiness(SourceValid, Texture->IsDefaultTexture(), Data ? Data->Mips.Num() : 0,
        Data && Data->PixelFormat != PF_Unknown, Resource != nullptr, Resource && Resource->TextureRHI.IsValid());
}
}
bool PrepareLandscapeResources(UWorld* World, const FString& ReportPath, const FString& CancelPath, FString& Error)
{
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("schema"), TEXT("landscape_resource_readiness/1"));
    Report->SetBoolField(TEXT("ok"), false); Report->SetBoolField(TEXT("map_saved"), false);
    TArray<TSharedPtr<FJsonValue>> Entries;
    int32 Checked = 0, Rebuilt = 0;
    const auto Persist = [&]() { Report->SetNumberField(TEXT("checked"), Checked); Report->SetNumberField(TEXT("rebuilt"), Rebuilt); Report->SetArrayField(TEXT("textures"), Entries); return SaveResourceReport(ReportPath, Report); };
    const auto Fail = [&](const FString& Message) { Error = Message; Report->SetStringField(TEXT("error"), Message); Persist(); UE_LOG(LogAIBridgeLandscapeResources, Error, TEXT("%s"), *Message); return false; };
    if (!World) return Fail(TEXT("RESOURCE_WORLD_REQUIRED"));
    TSet<UTexture2D*> Unique;
    TSet<UTexture2D*> Pool;
    for (TActorIterator<ALandscapeProxy> It(World); It; ++It)
    {
        for (ULandscapeComponent* C : It->LandscapeComponents) if (C)
        {
            if (UTexture2D* H = C->GetHeightmap(false)) Unique.Add(H);
            for (UTexture2D* W : C->GetWeightmapTextures(false)) if (W) Unique.Add(W);
            C->ForEachLayer([&](const FGuid&, FLandscapeLayerComponentData& D)
            {
                if (D.HeightmapData.Texture) Unique.Add(D.HeightmapData.Texture);
                for (UTexture2D* W : D.WeightmapData.Textures) if (W) Unique.Add(W);
            });
        }
        // The engine can reuse an unreferenced pool texture during weightmap reallocation.
        for (const auto& Pair : It->WeightmapUsageMap) if (Pair.Key) { Unique.Add(Pair.Key); Pool.Add(Pair.Key); }
    }
    TArray<UTexture2D*> Textures = Unique.Array();
    Textures.Sort([](const UTexture2D& A, const UTexture2D& B) { return A.GetPathName() < B.GetPathName(); });
    Report->SetNumberField(TEXT("texture_count"), Textures.Num());
    Report->SetNumberField(TEXT("allocation_pool_textures"), Pool.Num());
    if (!Persist()) return Fail(TEXT("RESOURCE_REPORT_WRITE_FAILED"));
    const double Start = FPlatformTime::Seconds();
    FlushRenderingCommands();
    for (UTexture2D* T : Textures)
    {
        const FPlatformMemoryStats M = FPlatformMemory::GetStats();
        if (M.AvailablePhysical < 8ull * 1024 * 1024 * 1024 || M.UsedVirtual >= 32ull * 1024 * 1024 * 1024) return Fail(TEXT("RESOURCE_MEMORY_GUARD_NO_SAVE"));
        if (FPlatformTime::Seconds() - Start > 300.0) return Fail(TEXT("RESOURCE_PREPARATION_TIMEOUT_NO_SAVE"));
        if (IFileManager::Get().FileExists(*CancelPath)) return Fail(TEXT("RESOURCE_PREPARATION_CANCELLED_NO_SAVE"));
        FString BeforeHash;
        const bool SourceValid = SourceHash(T, BeforeHash);
        auto Entry = Describe(T); Entry->SetBoolField(TEXT("allocation_pool"), Pool.Contains(T)); Entry->SetStringField(TEXT("source_hash"), BeforeHash);
        Entries.Add(MakeShared<FJsonValueObject>(Entry));
        if (!SourceValid) return Fail(TEXT("RESOURCE_SOURCE_DATA_MISSING: ") + T->GetPathName());
        T->BlockOnAnyAsyncBuild();
        ETextureReadiness State = Readiness(T, true);
        if (State != ETextureReadiness::Ready)
        {
            if (Rebuilt >= 128) return Fail(TEXT("RESOURCE_REBUILD_LIMIT_NO_SAVE"));
            Entry->SetBoolField(TEXT("rebuild_started"), true); if (!Persist()) return Fail(TEXT("RESOURCE_REPORT_WRITE_FAILED"));
            UE_LOG(LogAIBridgeLandscapeResources, Display, TEXT("Rebuilding derived texture resource: %s"), *T->GetPathName());
            T->UpdateResourceWithParams(static_cast<UTexture::EUpdateResourceFlags>(static_cast<uint32>(UTexture::EUpdateResourceFlags::ForceRebuild) | static_cast<uint32>(UTexture::EUpdateResourceFlags::Synchronous)));
            T->BlockOnAnyAsyncBuild();
            FlushRenderingCommands();
            ++Rebuilt; Entry->SetObjectField(TEXT("after"), Describe(T));
            FString AfterHash;
            if (!SourceHash(T, AfterHash) || AfterHash != BeforeHash) return Fail(TEXT("RESOURCE_REBUILD_CHANGED_SOURCE_NO_SAVE: ") + T->GetPathName());
            if (Readiness(T, true) != ETextureReadiness::Ready) return Fail(TEXT("RESOURCE_NOT_READY_AFTER_REBUILD_NO_SAVE: ") + T->GetPathName());
        }
        ++Checked;
        if (Checked % 64 == 0) { if (!Persist()) return Fail(TEXT("RESOURCE_REPORT_WRITE_FAILED")); UE_LOG(LogAIBridgeLandscapeResources, Display, TEXT("Texture readiness %d/%d; rebuilt %d"), Checked, Textures.Num(), Rebuilt); }
    }
    Report->SetBoolField(TEXT("ok"), true); return Persist();
}
}
