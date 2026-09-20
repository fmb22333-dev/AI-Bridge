#include "AIBridgeUEInspector.h"

#include "Components/ActorComponent.h"
#include "Editor.h"
#include "Engine/World.h"
#include "EngineUtils.h"
#include "GameFramework/Actor.h"
#include "HAL/FileManager.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/App.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "UObject/UnrealType.h"

namespace AIBridgeUE
{
static constexpr const TCHAR* SceneSchema = TEXT("aibridge_ue_scene/1");
static constexpr const TCHAR* StateClassMarker = TEXT("PDA_FluxSimulationState");
static constexpr int32 MaxActorsHardLimit = 2000;
static constexpr int32 MaxPropertiesHardLimit = 512;
static constexpr int32 MaxCollectionItemsHardLimit = 128;
static constexpr int32 MaxValueChars = 1024;

static int32 ReadBoundedInt(
    const TSharedPtr<FJsonObject>& Arguments,
    const TCHAR* Field,
    int32 DefaultValue,
    int32 HardMax)
{
    if (!Arguments.IsValid())
    {
        return DefaultValue;
    }
    double Number = 0.0;
    if (!Arguments->TryGetNumberField(Field, Number))
    {
        return DefaultValue;
    }
    return FMath::Clamp(static_cast<int32>(Number), 1, HardMax);
}

FInspectOptions ReadInspectOptions(const TSharedPtr<FJsonObject>& Arguments)
{
    FInspectOptions Options;
    if (Arguments.IsValid())
    {
        Arguments->TryGetBoolField(TEXT("fluidflux_only"), Options.bFluidFluxOnly);
    }
    Options.MaxActors = ReadBoundedInt(Arguments, TEXT("max_actors"), Options.MaxActors, MaxActorsHardLimit);
    Options.MaxProperties = ReadBoundedInt(
        Arguments,
        TEXT("max_properties"),
        Options.MaxProperties,
        MaxPropertiesHardLimit);
    Options.MaxCollectionItems = ReadBoundedInt(
        Arguments,
        TEXT("max_collection_items"),
        Options.MaxCollectionItems,
        MaxCollectionItemsHardLimit);
    return Options;
}

static bool ContainsFluxMarker(const FString& Value)
{
    return Value.Contains(TEXT("FluidFlux"), ESearchCase::IgnoreCase)
        || Value.Contains(TEXT("FluxSimulation"), ESearchCase::IgnoreCase)
        || Value.Contains(TEXT("FluxSurface"), ESearchCase::IgnoreCase)
        || Value.Contains(TEXT("FluxDomain"), ESearchCase::IgnoreCase)
        || Value.Contains(StateClassMarker, ESearchCase::IgnoreCase);
}

static bool IsFluidFluxActor(const AActor* Actor)
{
    if (!IsValid(Actor))
    {
        return false;
    }
#if WITH_EDITOR
    const FString Label = Actor->GetActorLabel();
#else
    const FString Label;
#endif
    return ContainsFluxMarker(Actor->GetName())
        || ContainsFluxMarker(Actor->GetClass()->GetName())
        || ContainsFluxMarker(Actor->GetClass()->GetPathName())
        || ContainsFluxMarker(Label);
}

static TSharedRef<FJsonObject> MakeObjectIdentity(const UObject* Object)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    if (!IsValid(Object))
    {
        Json->SetStringField(TEXT("name"), TEXT(""));
        Json->SetStringField(TEXT("path"), TEXT(""));
        Json->SetStringField(TEXT("class"), TEXT(""));
        Json->SetStringField(TEXT("class_path"), TEXT(""));
        return Json;
    }
    Json->SetStringField(TEXT("name"), Object->GetName());
    Json->SetStringField(TEXT("path"), Object->GetPathName());
    Json->SetStringField(TEXT("class"), Object->GetClass()->GetName());
    Json->SetStringField(TEXT("class_path"), Object->GetClass()->GetPathName());
    return Json;
}

static TSharedRef<FJsonObject> MakeVector(const FVector& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("x"), Value.X);
    Json->SetNumberField(TEXT("y"), Value.Y);
    Json->SetNumberField(TEXT("z"), Value.Z);
    return Json;
}

static TSharedRef<FJsonObject> MakeRotator(const FRotator& Value)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetNumberField(TEXT("pitch"), Value.Pitch);
    Json->SetNumberField(TEXT("yaw"), Value.Yaw);
    Json->SetNumberField(TEXT("roll"), Value.Roll);
    return Json;
}

static FString ExportPropertyText(const FProperty* Property, const void* ValuePtr, UObject* Owner)
{
    FString Value;
    if (Property != nullptr && ValuePtr != nullptr)
    {
        Property->ExportTextItem_Direct(Value, ValuePtr, nullptr, Owner, PPF_None, nullptr);
    }
    if (Value.Len() > MaxValueChars)
    {
        Value.LeftInline(MaxValueChars, EAllowShrinking::No);
        Value += TEXT("...[truncated]");
    }
    return Value;
}

static void AddLoadedReference(
    const FString& PropertyName,
    UObject* ReferencedObject,
    TArray<TSharedPtr<FJsonValue>>& OutReferences,
    TSet<FString>& Seen)
{
    if (!IsValid(ReferencedObject))
    {
        return;
    }
    const FString ObjectPath = ReferencedObject->GetPathName();
    const FString Key = PropertyName + TEXT("|") + ObjectPath;
    if (Seen.Contains(Key))
    {
        return;
    }
    Seen.Add(Key);
    const FString ClassPath = ReferencedObject->GetClass()->GetPathName();
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("property"), PropertyName);
    Json->SetStringField(TEXT("object_path"), ObjectPath);
    Json->SetStringField(TEXT("class"), ReferencedObject->GetClass()->GetName());
    Json->SetStringField(TEXT("class_path"), ClassPath);
    Json->SetBoolField(TEXT("is_asset"), ReferencedObject->IsAsset());
    Json->SetBoolField(TEXT("fluidflux_related"), ContainsFluxMarker(ObjectPath) || ContainsFluxMarker(ClassPath));
    Json->SetBoolField(TEXT("soft"), false);
    OutReferences.Add(MakeShared<FJsonValueObject>(Json));
}

static void AddSoftReference(
    const FString& PropertyName,
    FString ObjectPath,
    TArray<TSharedPtr<FJsonValue>>& OutReferences,
    TSet<FString>& Seen)
{
    ObjectPath = ObjectPath.TrimStartAndEnd();
    if (ObjectPath.IsEmpty() || ObjectPath.Equals(TEXT("None"), ESearchCase::IgnoreCase))
    {
        return;
    }
    const FString Key = PropertyName + TEXT("|") + ObjectPath;
    if (Seen.Contains(Key))
    {
        return;
    }
    Seen.Add(Key);
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("property"), PropertyName);
    Json->SetStringField(TEXT("object_path"), ObjectPath);
    Json->SetStringField(TEXT("class"), TEXT(""));
    Json->SetStringField(TEXT("class_path"), TEXT(""));
    Json->SetBoolField(TEXT("is_asset"), true);
    Json->SetBoolField(TEXT("fluidflux_related"), ContainsFluxMarker(ObjectPath));
    Json->SetBoolField(TEXT("soft"), true);
    OutReferences.Add(MakeShared<FJsonValueObject>(Json));
}

static bool IsSimpleScalar(const FProperty* Property)
{
    return CastField<FBoolProperty>(Property) != nullptr
        || CastField<FNumericProperty>(Property) != nullptr
        || CastField<FStrProperty>(Property) != nullptr
        || CastField<FNameProperty>(Property) != nullptr
        || CastField<FEnumProperty>(Property) != nullptr
        || CastField<FByteProperty>(Property) != nullptr;
}

static void CollectShallowMetadata(
    UObject* Owner,
    const FInspectOptions& Options,
    TArray<TSharedPtr<FJsonValue>>& OutProperties,
    TArray<TSharedPtr<FJsonValue>>& OutReferences)
{
    if (!IsValid(Owner))
    {
        return;
    }

    TSet<FString> Seen;
    int32 Emitted = 0;
    for (TFieldIterator<FProperty> It(Owner->GetClass(), EFieldIteratorFlags::IncludeSuper); It; ++It)
    {
        if (Emitted >= Options.MaxProperties)
        {
            break;
        }
        const FProperty* Property = *It;
        if (Property == nullptr)
        {
            continue;
        }
        const FString PropertyName = Property->GetName();
        const void* ValuePtr = Property->ContainerPtrToValuePtr<void>(Owner);

        if (CastField<FSoftObjectProperty>(Property) != nullptr)
        {
            const FString Value = ExportPropertyText(Property, ValuePtr, Owner);
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), Property->GetClass()->GetName());
            Json->SetStringField(TEXT("value"), Value);
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
            AddSoftReference(PropertyName, Value, OutReferences, Seen);
            continue;
        }

        if (const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
        {
            UObject* ReferencedObject = ObjectProperty->GetObjectPropertyValue_InContainer(Owner);
            if (IsValid(ReferencedObject) && ReferencedObject != Owner)
            {
                AddLoadedReference(PropertyName, ReferencedObject, OutReferences, Seen);
            }
            continue;
        }

        if (const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property))
        {
            FScriptArrayHelper Helper(ArrayProperty, ValuePtr);
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), TEXT("Array"));
            Json->SetNumberField(TEXT("count"), Helper.Num());
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;

            const int32 Count = FMath::Min(Helper.Num(), Options.MaxCollectionItems);
            for (int32 Index = 0; Index < Count; ++Index)
            {
                const void* ItemPtr = Helper.GetRawPtr(Index);
                const FString IndexedName = FString::Printf(TEXT("%s[%d]"), *PropertyName, Index);
                if (CastField<FSoftObjectProperty>(ArrayProperty->Inner) != nullptr)
                {
                    AddSoftReference(
                        IndexedName,
                        ExportPropertyText(ArrayProperty->Inner, ItemPtr, Owner),
                        OutReferences,
                        Seen);
                }
                else if (const FObjectPropertyBase* InnerObject = CastField<FObjectPropertyBase>(ArrayProperty->Inner))
                {
                    UObject* ReferencedObject = InnerObject->GetObjectPropertyValue(ItemPtr);
                    if (IsValid(ReferencedObject) && ReferencedObject != Owner)
                    {
                        AddLoadedReference(IndexedName, ReferencedObject, OutReferences, Seen);
                    }
                }
            }
            continue;
        }

        if (IsSimpleScalar(Property))
        {
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), Property->GetClass()->GetName());
            Json->SetStringField(TEXT("value"), ExportPropertyText(Property, ValuePtr, Owner));
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
        }
    }
}

static TSharedRef<FJsonObject> InspectActor(AActor* Actor, const FInspectOptions& Options)
{
    TSharedRef<FJsonObject> Json = MakeObjectIdentity(Actor);
    Json->SetBoolField(TEXT("fluidflux_related"), IsFluidFluxActor(Actor));
#if WITH_EDITOR
    Json->SetStringField(TEXT("label"), Actor->GetActorLabel());
#endif
    const FTransform Transform = Actor->GetActorTransform();
    Json->SetObjectField(TEXT("location"), MakeVector(Transform.GetLocation()));
    Json->SetObjectField(TEXT("rotation"), MakeRotator(Transform.Rotator()));
    Json->SetObjectField(TEXT("scale"), MakeVector(Transform.GetScale3D()));

    FVector BoundsOrigin = FVector::ZeroVector;
    FVector BoundsExtent = FVector::ZeroVector;
    Actor->GetActorBounds(false, BoundsOrigin, BoundsExtent);
    Json->SetObjectField(TEXT("bounds_origin"), MakeVector(BoundsOrigin));
    Json->SetObjectField(TEXT("bounds_extent"), MakeVector(BoundsExtent));

    if (AActor* ParentActor = Actor->GetAttachParentActor())
    {
        Json->SetObjectField(TEXT("parent_actor"), MakeObjectIdentity(ParentActor));
    }
    else
    {
        Json->SetField(TEXT("parent_actor"), MakeShared<FJsonValueNull>());
    }

    TArray<AActor*> AttachedActors;
    Actor->GetAttachedActors(AttachedActors, true, false);
    TArray<TSharedPtr<FJsonValue>> AttachedJson;
    const int32 AttachedCount = FMath::Min(AttachedActors.Num(), Options.MaxCollectionItems);
    for (int32 Index = 0; Index < AttachedCount; ++Index)
    {
        if (IsValid(AttachedActors[Index]))
        {
            AttachedJson.Add(MakeShared<FJsonValueObject>(MakeObjectIdentity(AttachedActors[Index])));
        }
    }
    Json->SetArrayField(TEXT("attached_actors"), AttachedJson);
    Json->SetNumberField(TEXT("attached_actor_count"), AttachedActors.Num());
    Json->SetBoolField(TEXT("attached_actors_truncated"), AttachedActors.Num() > Options.MaxCollectionItems);

    TArray<TSharedPtr<FJsonValue>> Properties;
    TArray<TSharedPtr<FJsonValue>> References;
    CollectShallowMetadata(Actor, Options, Properties, References);
    Json->SetArrayField(TEXT("properties"), Properties);
    Json->SetArrayField(TEXT("asset_references"), References);

    TArray<UActorComponent*> Components;
    Actor->GetComponents(Components);
    TArray<TSharedPtr<FJsonValue>> ComponentsJson;
    const int32 ComponentCount = FMath::Min(Components.Num(), Options.MaxCollectionItems);
    for (int32 Index = 0; Index < ComponentCount; ++Index)
    {
        UActorComponent* Component = Components[Index];
        if (!IsValid(Component))
        {
            continue;
        }
        TSharedRef<FJsonObject> ComponentJson = MakeObjectIdentity(Component);
        ComponentJson->SetBoolField(
            TEXT("fluidflux_related"),
            ContainsFluxMarker(Component->GetName())
                || ContainsFluxMarker(Component->GetClass()->GetName())
                || ContainsFluxMarker(Component->GetClass()->GetPathName()));

        TArray<TSharedPtr<FJsonValue>> ComponentProperties;
        TArray<TSharedPtr<FJsonValue>> ComponentReferences;
        CollectShallowMetadata(Component, Options, ComponentProperties, ComponentReferences);
        ComponentJson->SetArrayField(TEXT("properties"), ComponentProperties);
        ComponentJson->SetArrayField(TEXT("asset_references"), ComponentReferences);
        ComponentsJson.Add(MakeShared<FJsonValueObject>(ComponentJson));
    }
    Json->SetArrayField(TEXT("components"), ComponentsJson);
    Json->SetNumberField(TEXT("component_count"), Components.Num());
    Json->SetBoolField(TEXT("components_truncated"), Components.Num() > Options.MaxCollectionItems);
    return Json;
}

static TSharedRef<FJsonObject> InspectWorld(UWorld* World, const FInspectOptions& Options)
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), SceneSchema);
    Root->SetBoolField(TEXT("read_only"), true);
    Root->SetStringField(TEXT("engine_version"), FEngineVersion::Current().ToString());
    Root->SetStringField(TEXT("project_name"), FApp::GetProjectName());
    Root->SetStringField(TEXT("generated_utc"), FDateTime::UtcNow().ToIso8601());
    Root->SetStringField(TEXT("world_name"), World->GetName());
    Root->SetStringField(TEXT("world_path"), World->GetPathName());
    Root->SetStringField(TEXT("world_package"), World->GetOutermost()->GetName());
    Root->SetBoolField(TEXT("fluidflux_only"), Options.bFluidFluxOnly);
    Root->SetNumberField(TEXT("max_actors"), Options.MaxActors);
    Root->SetNumberField(TEXT("max_properties"), Options.MaxProperties);
    Root->SetNumberField(TEXT("max_collection_items"), Options.MaxCollectionItems);

    if (const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIBridgeUE")))
    {
        Root->SetStringField(TEXT("plugin_version"), Plugin->GetDescriptor().VersionName);
    }

    TArray<TSharedPtr<FJsonValue>> Actors;
    int32 LoadedActorCount = 0;
    int32 FluidFluxActorCount = 0;
    int32 MatchingActorCount = 0;
    for (TActorIterator<AActor> It(World); It; ++It)
    {
        AActor* Actor = *It;
        if (!IsValid(Actor))
        {
            continue;
        }
        ++LoadedActorCount;
        const bool bFluidFlux = IsFluidFluxActor(Actor);
        if (bFluidFlux)
        {
            ++FluidFluxActorCount;
        }
        if (Options.bFluidFluxOnly && !bFluidFlux)
        {
            continue;
        }
        ++MatchingActorCount;
        if (Actors.Num() < Options.MaxActors)
        {
            Actors.Add(MakeShared<FJsonValueObject>(InspectActor(Actor, Options)));
        }
    }
    Root->SetNumberField(TEXT("loaded_actor_count"), LoadedActorCount);
    Root->SetNumberField(TEXT("fluidflux_actor_count"), FluidFluxActorCount);
    Root->SetNumberField(TEXT("matching_actor_count"), MatchingActorCount);
    Root->SetNumberField(TEXT("returned_actor_count"), Actors.Num());
    Root->SetBoolField(TEXT("actors_truncated"), MatchingActorCount > Actors.Num());
    Root->SetArrayField(TEXT("actors"), Actors);
    return Root;
}

TSharedPtr<FJsonObject> InspectCurrentEditorLevel(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutErrorCode.Reset();
    OutErrorMessage.Reset();
    if (GEditor == nullptr)
    {
        OutErrorCode = TEXT("EDITOR_UNAVAILABLE");
        OutErrorMessage = TEXT("GEditor is unavailable.");
        return nullptr;
    }
    UWorld* World = GEditor->GetEditorWorldContext().World();
    if (!IsValid(World))
    {
        OutErrorCode = TEXT("WORLD_UNAVAILABLE");
        OutErrorMessage = TEXT("Editor world is unavailable.");
        return nullptr;
    }
    return InspectWorld(World, ReadInspectOptions(Arguments));
}

bool WriteCurrentLevelManifest(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutPath,
    FString& OutError)
{
    FString ErrorCode;
    FString ErrorMessage;
    const TSharedPtr<FJsonObject> Root = InspectCurrentEditorLevel(Arguments, ErrorCode, ErrorMessage);
    if (!Root.IsValid())
    {
        OutError = ErrorCode + TEXT(": ") + ErrorMessage;
        return false;
    }

    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    if (!FJsonSerializer::Serialize(Root.ToSharedRef(), Writer))
    {
        OutError = TEXT("Failed to serialize current-level manifest.");
        return false;
    }

    const FString OutputDirectory = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AI_Bridge"));
    IFileManager::Get().MakeDirectory(*OutputDirectory, true);
    OutPath = FPaths::Combine(OutputDirectory, TEXT("fluidflux_manifest.json"));
    if (!FFileHelper::SaveStringToFile(JsonText, *OutPath))
    {
        OutError = FString::Printf(TEXT("Failed to write manifest: %s"), *OutPath);
        return false;
    }
    return true;
}
} // namespace AIBridgeUE
