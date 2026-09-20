#include "AIBridgeUEFluidFluxExporter.h"

#include "AIBridgeUEObjectInspector.h"
#include "Engine/Texture2D.h"
#include "HAL/FileManager.h"
#include "ImageCore.h"
#include "ImageUtils.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UnrealType.h"

namespace AIBridgeUE
{
namespace
{
static constexpr const TCHAR* ExportSchema = TEXT("aibridge_fluidflux_state_source/1");
static constexpr const TCHAR* StateClassMarker = TEXT("PDA_FluxSimulationState");

struct FMapExportSpec
{
    const TCHAR* PropertyName;
    const TCHAR* FileName;
    const TCHAR* Semantic;
};

static const FMapExportSpec MapSpecs[] = {
    { TEXT("GroundMap"), TEXT("GroundMap.exr"), TEXT("ground_height_source") },
    { TEXT("VelocityDepthFoamMap"), TEXT("VelocityDepthFoamMap.exr"), TEXT("R=velocity_x,G=velocity_y,B=depth,A=foam") },
    { TEXT("HeightWetMap"), TEXT("HeightWetMap.exr"), TEXT("R=surface_height,G=wetness") },
};

static UTexture2D* ReadTextureProperty(UObject* StateObject, const TCHAR* PropertyName)
{
    if (!IsValid(StateObject))
    {
        return nullptr;
    }
    FProperty* Property = StateObject->GetClass()->FindPropertyByName(FName(PropertyName));
    const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property);
    if (ObjectProperty == nullptr)
    {
        return nullptr;
    }
    return Cast<UTexture2D>(ObjectProperty->GetObjectPropertyValue_InContainer(StateObject));
}

static bool ExportTextureSource(
    UTexture2D* Texture,
    const FString& OutputPath,
    const FMapExportSpec& Spec,
    TSharedRef<FJsonObject> OutTextureJson,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    if (!IsValid(Texture))
    {
        OutErrorCode = TEXT("STATE_TEXTURE_MISSING");
        OutErrorMessage = FString::Printf(TEXT("State property %s is not a loaded Texture2D."), Spec.PropertyName);
        return false;
    }

    if (!Texture->Source.IsValid())
    {
        OutErrorCode = TEXT("TEXTURE_SOURCE_UNAVAILABLE");
        OutErrorMessage = FString::Printf(TEXT("Texture Source is unavailable: %s"), *Texture->GetPathName());
        return false;
    }

    if (Texture->Source.GetFormat(0) != TSF_RGBA16F)
    {
        OutErrorCode = TEXT("UNSUPPORTED_SOURCE_FORMAT");
        OutErrorMessage = FString::Printf(
            TEXT("Expected TSF_RGBA16F source for %s, got source format %d."),
            *Texture->GetPathName(),
            static_cast<int32>(Texture->Source.GetFormat(0)));
        return false;
    }

    FImage Image;
    if (!Texture->Source.GetMipImage(Image, 0, 0, 0))
    {
        OutErrorCode = TEXT("SOURCE_MIP_READ_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to read mip0 source image: %s"), *Texture->GetPathName());
        return false;
    }

    if (!FImageUtils::SaveImageByExtension(*OutputPath, Image, 0))
    {
        OutErrorCode = TEXT("SOURCE_IMAGE_WRITE_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to write EXR: %s"), *OutputPath);
        return false;
    }

    OutTextureJson->SetStringField(TEXT("property"), Spec.PropertyName);
    OutTextureJson->SetStringField(TEXT("asset_path"), Texture->GetPathName());
    OutTextureJson->SetStringField(TEXT("file"), FPaths::GetCleanFilename(OutputPath));
    OutTextureJson->SetStringField(TEXT("source_format"), TEXT("TSF_RGBA16F"));
    OutTextureJson->SetStringField(TEXT("semantic"), Spec.Semantic);
    OutTextureJson->SetNumberField(TEXT("size_x"), Image.SizeX);
    OutTextureJson->SetNumberField(TEXT("size_y"), Image.SizeY);
    OutTextureJson->SetBoolField(TEXT("srgb"), Texture->SRGB);
    OutTextureJson->SetStringField(TEXT("gamma"), Texture->SRGB ? TEXT("sRGB") : TEXT("linear"));
    OutTextureJson->SetNumberField(TEXT("mip_index"), 0);
    return true;
}
}

TSharedPtr<FJsonObject> ExportFluidFluxStateSource(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutErrorCode.Reset();
    OutErrorMessage.Reset();

    FString StateObjectPath;
    FString OutputDir;
    if (!Arguments.IsValid()
        || !Arguments->TryGetStringField(TEXT("state_object_path"), StateObjectPath)
        || StateObjectPath.TrimStartAndEnd().IsEmpty())
    {
        OutErrorCode = TEXT("STATE_OBJECT_PATH_REQUIRED");
        OutErrorMessage = TEXT("export.fluidflux_state_source requires state_object_path.");
        return nullptr;
    }
    if (!Arguments->TryGetStringField(TEXT("output_dir"), OutputDir)
        || OutputDir.TrimStartAndEnd().IsEmpty())
    {
        OutErrorCode = TEXT("OUTPUT_DIR_REQUIRED");
        OutErrorMessage = TEXT("export.fluidflux_state_source requires output_dir.");
        return nullptr;
    }

    StateObjectPath = StateObjectPath.TrimStartAndEnd();
    OutputDir = OutputDir.TrimStartAndEnd();
    if (FPaths::IsRelative(OutputDir))
    {
        OutErrorCode = TEXT("OUTPUT_DIR_MUST_BE_ABSOLUTE");
        OutErrorMessage = TEXT("output_dir must be an absolute filesystem path.");
        return nullptr;
    }
    FPaths::NormalizeDirectoryName(OutputDir);
    FPaths::CollapseRelativeDirectories(OutputDir);

    if (IFileManager::Get().DirectoryExists(*OutputDir))
    {
        OutErrorCode = TEXT("OUTPUT_DIR_EXISTS");
        OutErrorMessage = FString::Printf(TEXT("Refusing to overwrite existing export directory: %s"), *OutputDir);
        return nullptr;
    }

    UObject* StateObject = StaticFindObject(UObject::StaticClass(), nullptr, *StateObjectPath, false);
    if (!IsValid(StateObject))
    {
        OutErrorCode = TEXT("STATE_OBJECT_NOT_LOADED");
        OutErrorMessage = FString::Printf(TEXT("State object is not currently loaded: %s"), *StateObjectPath);
        return nullptr;
    }
    if (!StateObject->GetClass()->GetPathName().Contains(StateClassMarker, ESearchCase::IgnoreCase))
    {
        OutErrorCode = TEXT("NOT_FLUIDFLUX_STATE");
        OutErrorMessage = FString::Printf(TEXT("Object is not a PDA_FluxSimulationState: %s"), *StateObjectPath);
        return nullptr;
    }

    if (!IFileManager::Get().MakeDirectory(*OutputDir, true))
    {
        OutErrorCode = TEXT("OUTPUT_DIR_CREATE_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to create export directory: %s"), *OutputDir);
        return nullptr;
    }

    const auto CleanupPartialExport = [&OutputDir]()
    {
        IFileManager::Get().DeleteDirectory(*OutputDir, false, true);
    };

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), ExportSchema);
    Root->SetStringField(TEXT("state_object_path"), StateObjectPath);
    Root->SetStringField(TEXT("output_dir"), OutputDir);
    Root->SetBoolField(TEXT("source_assets_modified"), false);
    Root->SetBoolField(TEXT("source_pixels_exported"), true);
    Root->SetBoolField(TEXT("orientation_verified"), false);
    Root->SetStringField(TEXT("notes"), TEXT("Untouched source-art export for Houdini parity; decode/orientation remain unverified."));

    TArray<TSharedPtr<FJsonValue>> TextureExports;
    for (const FMapExportSpec& Spec : MapSpecs)
    {
        UTexture2D* Texture = ReadTextureProperty(StateObject, Spec.PropertyName);
        TSharedRef<FJsonObject> TextureJson = MakeShared<FJsonObject>();
        const FString OutputPath = FPaths::Combine(OutputDir, Spec.FileName);
        if (!ExportTextureSource(Texture, OutputPath, Spec, TextureJson, OutErrorCode, OutErrorMessage))
        {
            CleanupPartialExport();
            return nullptr;
        }
        TextureExports.Add(MakeShared<FJsonValueObject>(TextureJson));
    }
    Root->SetArrayField(TEXT("textures"), TextureExports);

    TSharedRef<FJsonObject> InspectArgs = MakeShared<FJsonObject>();
    InspectArgs->SetStringField(TEXT("object_path"), StateObjectPath);
    InspectArgs->SetNumberField(TEXT("max_properties"), 512);
    InspectArgs->SetNumberField(TEXT("max_collection_items"), 128);
    FString InspectCode;
    FString InspectMessage;
    if (TSharedPtr<FJsonObject> StateMetadata = InspectObjectByPath(InspectArgs, InspectCode, InspectMessage))
    {
        Root->SetObjectField(TEXT("state_metadata"), StateMetadata.ToSharedRef());
    }

    const FString MetadataPath = FPaths::Combine(OutputDir, TEXT("metadata.json"));
    Root->SetStringField(TEXT("metadata_file"), MetadataPath);
    Root->SetNumberField(TEXT("texture_count"), TextureExports.Num());
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root, Writer) || !FFileHelper::SaveStringToFile(JsonText, *MetadataPath))
    {
        OutErrorCode = TEXT("METADATA_WRITE_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to write metadata.json: %s"), *MetadataPath);
        CleanupPartialExport();
        return nullptr;
    }

    return Root;
}
} // namespace AIBridgeUE
