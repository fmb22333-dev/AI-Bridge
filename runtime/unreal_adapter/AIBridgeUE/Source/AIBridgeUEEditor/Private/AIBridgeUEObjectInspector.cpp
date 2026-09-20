#include "AIBridgeUEObjectInspector.h"

#include "Engine/Texture2D.h"
#include "UObject/UObjectGlobals.h"
#include "UObject/UnrealType.h"

namespace AIBridgeUE
{
namespace
{
static constexpr int32 DefaultMaxProperties = 256;
static constexpr int32 MaxPropertiesHardLimit = 1024;
static constexpr int32 DefaultMaxCollectionItems = 64;
static constexpr int32 MaxCollectionItemsHardLimit = 256;
static constexpr int32 MaxValueChars = 4096;

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

static TSharedRef<FJsonObject> MakeObjectIdentity(const UObject* Object)
{
    TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
    Json->SetStringField(TEXT("name"), Object ? Object->GetName() : TEXT(""));
    Json->SetStringField(TEXT("path"), Object ? Object->GetPathName() : TEXT(""));
    Json->SetStringField(TEXT("class"), Object ? Object->GetClass()->GetName() : TEXT(""));
    Json->SetStringField(TEXT("class_path"), Object ? Object->GetClass()->GetPathName() : TEXT(""));
    Json->SetBoolField(TEXT("is_asset"), Object && Object->IsAsset());
    return Json;
}

static void AddTextureMetadata(UObject* Object, TSharedRef<FJsonObject> Json)
{
    if (const UTexture2D* Texture = Cast<UTexture2D>(Object))
    {
        TSharedRef<FJsonObject> TextureJson = MakeShared<FJsonObject>();
        TextureJson->SetNumberField(TEXT("size_x"), Texture->GetSizeX());
        TextureJson->SetNumberField(TEXT("size_y"), Texture->GetSizeY());
        Json->SetObjectField(TEXT("texture2d"), TextureJson);
    }
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

    TSharedRef<FJsonObject> Json = MakeObjectIdentity(ReferencedObject);
    Json->SetStringField(TEXT("property"), PropertyName);
    Json->SetBoolField(TEXT("soft"), false);
    AddTextureMetadata(ReferencedObject, Json);
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
    Json->SetBoolField(TEXT("soft"), true);
    OutReferences.Add(MakeShared<FJsonValueObject>(Json));
}

static bool IsValueProperty(const FProperty* Property)
{
    return CastField<FBoolProperty>(Property) != nullptr
        || CastField<FNumericProperty>(Property) != nullptr
        || CastField<FStrProperty>(Property) != nullptr
        || CastField<FNameProperty>(Property) != nullptr
        || CastField<FEnumProperty>(Property) != nullptr
        || CastField<FByteProperty>(Property) != nullptr
        || CastField<FStructProperty>(Property) != nullptr;
}

static void CollectObjectMetadata(
    UObject* Object,
    int32 MaxProperties,
    int32 MaxCollectionItems,
    TArray<TSharedPtr<FJsonValue>>& OutProperties,
    TArray<TSharedPtr<FJsonValue>>& OutReferences,
    bool& bPropertiesTruncated)
{
    TSet<FString> SeenReferences;
    int32 Emitted = 0;

    for (TFieldIterator<FProperty> It(Object->GetClass(), EFieldIteratorFlags::IncludeSuper); It; ++It)
    {
        const FProperty* Property = *It;
        if (Property == nullptr)
        {
            continue;
        }
        if (Emitted >= MaxProperties)
        {
            bPropertiesTruncated = true;
            break;
        }

        const FString PropertyName = Property->GetName();
        const void* ValuePtr = Property->ContainerPtrToValuePtr<void>(Object);

        if (CastField<FSoftObjectProperty>(Property) != nullptr)
        {
            const FString Value = ExportPropertyText(Property, ValuePtr, Object);
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), Property->GetClass()->GetName());
            Json->SetStringField(TEXT("value"), Value);
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
            AddSoftReference(PropertyName, Value, OutReferences, SeenReferences);
            continue;
        }

        if (const FObjectPropertyBase* ObjectProperty = CastField<FObjectPropertyBase>(Property))
        {
            UObject* ReferencedObject = ObjectProperty->GetObjectPropertyValue_InContainer(Object);
            AddLoadedReference(PropertyName, ReferencedObject, OutReferences, SeenReferences);
            continue;
        }

        if (const FArrayProperty* ArrayProperty = CastField<FArrayProperty>(Property))
        {
            FScriptArrayHelper Helper(ArrayProperty, ValuePtr);
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), TEXT("Array"));
            Json->SetNumberField(TEXT("count"), Helper.Num());

            TArray<TSharedPtr<FJsonValue>> Values;
            const int32 Count = FMath::Min(Helper.Num(), MaxCollectionItems);
            for (int32 Index = 0; Index < Count; ++Index)
            {
                const void* ItemPtr = Helper.GetRawPtr(Index);
                const FString IndexedName = FString::Printf(TEXT("%s[%d]"), *PropertyName, Index);
                if (CastField<FSoftObjectProperty>(ArrayProperty->Inner) != nullptr)
                {
                    AddSoftReference(
                        IndexedName,
                        ExportPropertyText(ArrayProperty->Inner, ItemPtr, Object),
                        OutReferences,
                        SeenReferences);
                }
                else if (const FObjectPropertyBase* InnerObject = CastField<FObjectPropertyBase>(ArrayProperty->Inner))
                {
                    UObject* ReferencedObject = InnerObject->GetObjectPropertyValue(ItemPtr);
                    AddLoadedReference(IndexedName, ReferencedObject, OutReferences, SeenReferences);
                }
                else if (IsValueProperty(ArrayProperty->Inner))
                {
                    Values.Add(MakeShared<FJsonValueString>(ExportPropertyText(ArrayProperty->Inner, ItemPtr, Object)));
                }
            }
            if (!Values.IsEmpty())
            {
                Json->SetArrayField(TEXT("values"), Values);
            }
            Json->SetBoolField(TEXT("truncated"), Helper.Num() > MaxCollectionItems);
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
            continue;
        }

        if (CastField<FMapProperty>(Property) != nullptr || CastField<FSetProperty>(Property) != nullptr)
        {
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), Property->GetClass()->GetName());
            Json->SetStringField(TEXT("value"), ExportPropertyText(Property, ValuePtr, Object));
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
            continue;
        }

        if (IsValueProperty(Property))
        {
            TSharedRef<FJsonObject> Json = MakeShared<FJsonObject>();
            Json->SetStringField(TEXT("name"), PropertyName);
            Json->SetStringField(TEXT("type"), Property->GetClass()->GetName());
            Json->SetStringField(TEXT("value"), ExportPropertyText(Property, ValuePtr, Object));
            OutProperties.Add(MakeShared<FJsonValueObject>(Json));
            ++Emitted;
        }
    }
}
} // namespace

TSharedPtr<FJsonObject> InspectObjectByPath(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutErrorCode.Reset();
    OutErrorMessage.Reset();

    FString ObjectPath;
    if (!Arguments.IsValid()
        || !Arguments->TryGetStringField(TEXT("object_path"), ObjectPath)
        || ObjectPath.TrimStartAndEnd().IsEmpty())
    {
        OutErrorCode = TEXT("OBJECT_PATH_REQUIRED");
        OutErrorMessage = TEXT("inspect.object requires a non-empty object_path.");
        return nullptr;
    }
    ObjectPath = ObjectPath.TrimStartAndEnd();

    UObject* Object = StaticFindObject(UObject::StaticClass(), nullptr, *ObjectPath, false);
    if (!IsValid(Object))
    {
        OutErrorCode = TEXT("OBJECT_NOT_LOADED");
        OutErrorMessage = FString::Printf(TEXT("Object is not currently loaded: %s"), *ObjectPath);
        return nullptr;
    }

    const int32 MaxProperties = ReadBoundedInt(
        Arguments,
        TEXT("max_properties"),
        DefaultMaxProperties,
        MaxPropertiesHardLimit);
    const int32 MaxCollectionItems = ReadBoundedInt(
        Arguments,
        TEXT("max_collection_items"),
        DefaultMaxCollectionItems,
        MaxCollectionItemsHardLimit);

    TSharedRef<FJsonObject> Root = MakeObjectIdentity(Object);
    Root->SetStringField(TEXT("schema_version"), TEXT("aibridge_ue_object/1"));
    Root->SetBoolField(TEXT("read_only"), true);
    Root->SetBoolField(TEXT("loaded_only"), true);
    AddTextureMetadata(Object, Root);

    TArray<TSharedPtr<FJsonValue>> Properties;
    TArray<TSharedPtr<FJsonValue>> References;
    bool bPropertiesTruncated = false;
    CollectObjectMetadata(
        Object,
        MaxProperties,
        MaxCollectionItems,
        Properties,
        References,
        bPropertiesTruncated);
    Root->SetArrayField(TEXT("properties"), Properties);
    Root->SetArrayField(TEXT("asset_references"), References);
    Root->SetNumberField(TEXT("property_count"), Properties.Num());
    Root->SetNumberField(TEXT("reference_count"), References.Num());
    Root->SetBoolField(TEXT("properties_truncated"), bPropertiesTruncated);
    return Root;
}

TSharedPtr<FJsonObject> InspectObjectsByPaths(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutErrorCode.Reset();
    OutErrorMessage.Reset();
    if (!Arguments.IsValid())
    {
        OutErrorCode = TEXT("OBJECT_PATHS_REQUIRED");
        OutErrorMessage = TEXT("inspect.objects requires object_paths.");
        return nullptr;
    }

    const TArray<TSharedPtr<FJsonValue>>* PathValues = nullptr;
    if (!Arguments->TryGetArrayField(TEXT("object_paths"), PathValues) || PathValues == nullptr || PathValues->IsEmpty())
    {
        OutErrorCode = TEXT("OBJECT_PATHS_REQUIRED");
        OutErrorMessage = TEXT("inspect.objects requires a non-empty object_paths array.");
        return nullptr;
    }

    const int32 MaxObjects = FMath::Min(PathValues->Num(), 128);
    TArray<TSharedPtr<FJsonValue>> Objects;
    Objects.Reserve(MaxObjects);
    TArray<TSharedPtr<FJsonValue>> Missing;

    for (int32 Index = 0; Index < MaxObjects; ++Index)
    {
        FString Path;
        if (!(*PathValues)[Index].IsValid() || !(*PathValues)[Index]->TryGetString(Path) || Path.TrimStartAndEnd().IsEmpty())
        {
            continue;
        }
        TSharedRef<FJsonObject> SingleArgs = MakeShared<FJsonObject>();
        SingleArgs->SetStringField(TEXT("object_path"), Path.TrimStartAndEnd());
        double Number = 0.0;
        if (Arguments->TryGetNumberField(TEXT("max_properties"), Number))
        {
            SingleArgs->SetNumberField(TEXT("max_properties"), Number);
        }
        if (Arguments->TryGetNumberField(TEXT("max_collection_items"), Number))
        {
            SingleArgs->SetNumberField(TEXT("max_collection_items"), Number);
        }

        FString ItemCode;
        FString ItemMessage;
        TSharedPtr<FJsonObject> Item = InspectObjectByPath(SingleArgs, ItemCode, ItemMessage);
        if (Item.IsValid())
        {
            Objects.Add(MakeShared<FJsonValueObject>(Item.ToSharedRef()));
        }
        else
        {
            TSharedRef<FJsonObject> MissingItem = MakeShared<FJsonObject>();
            MissingItem->SetStringField(TEXT("object_path"), Path);
            MissingItem->SetStringField(TEXT("code"), ItemCode);
            MissingItem->SetStringField(TEXT("message"), ItemMessage);
            Missing.Add(MakeShared<FJsonValueObject>(MissingItem));
        }
    }

    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("schema_version"), TEXT("aibridge_ue_objects/1"));
    Root->SetBoolField(TEXT("read_only"), true);
    Root->SetBoolField(TEXT("loaded_only"), true);
    Root->SetArrayField(TEXT("objects"), Objects);
    Root->SetArrayField(TEXT("missing"), Missing);
    Root->SetNumberField(TEXT("requested_count"), PathValues->Num());
    Root->SetNumberField(TEXT("returned_count"), Objects.Num());
    Root->SetNumberField(TEXT("missing_count"), Missing.Num());
    Root->SetBoolField(TEXT("truncated"), PathValues->Num() > MaxObjects);
    return Root;
}

} // namespace AIBridgeUE
