#include "AIBridgeUEAnimCurveFbxExporter.h"

#include "Animation/AnimCurveTypes.h"
#include "Animation/AnimData/IAnimationDataModel.h"
#include "Animation/AnimSequence.h"
#include "Curves/RichCurve.h"
#include "Exporters/Exporter.h"
#include "HAL/FileManager.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "UObject/UObjectGlobals.h"

THIRD_PARTY_INCLUDES_START
#include <fbxsdk.h>
THIRD_PARTY_INCLUDES_END

namespace AIBridgeUE
{
namespace
{
static constexpr const TCHAR* ExportSchema = TEXT("aibridge_animsequence_fbx_curves/1");
static constexpr double TimeToleranceSeconds = 1.0e-6;
static constexpr float ValueTolerance = 1.0e-5f;
static constexpr float TangentTolerance = 1.0e-4f;

static FbxNode* FindSkeletonRoot(FbxNode* Node)
{
    if (Node == nullptr)
    {
        return nullptr;
    }

    if (Node->GetSkeleton() != nullptr)
    {
        FbxNode* Root = Node;
        while (Root->GetParent() != nullptr && Root->GetParent()->GetSkeleton() != nullptr)
        {
            Root = Root->GetParent();
        }
        return Root;
    }

    for (int32 ChildIndex = 0; ChildIndex < Node->GetChildCount(); ++ChildIndex)
    {
        if (FbxNode* Found = FindSkeletonRoot(Node->GetChild(ChildIndex)))
        {
            return Found;
        }
    }
    return nullptr;
}

static void CleanupTempFile(const FString& Path)
{
    if (!Path.IsEmpty())
    {
        IFileManager::Get().Delete(*Path, false, true, true);
    }
}

static bool ExportBaseAnimSequence(
    UAnimSequence* AnimSequence,
    const FString& TempPath,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    UExporter* Exporter = UExporter::FindExporter(AnimSequence, TEXT("fbx"));
    if (Exporter == nullptr)
    {
        OutErrorCode = TEXT("FBX_EXPORTER_NOT_FOUND");
        OutErrorMessage = TEXT("Unreal could not resolve an FBX exporter for the AnimSequence.");
        return false;
    }

    Exporter->SetBatchMode(true);
    Exporter->SetShowExportOption(false);

    const int32 ExportResult = UExporter::ExportToFile(
        AnimSequence,
        Exporter,
        *TempPath,
        false, // InSelectedOnly
        false, // NoReplaceIdentical: always overwrite the temporary FBX
        false  // Prompt
    );
    if (ExportResult <= 0 || !IFileManager::Get().FileExists(*TempPath))
    {
        OutErrorCode = TEXT("BASE_FBX_EXPORT_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Unreal base AnimSequence FBX export failed: %s"), *TempPath);
        return false;
    }
    return true;
}

static bool ValidateCurveTransport(
    const TArray<FFloatCurve>& FloatCurves,
    int32& OutSourceCurveKeyCount,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutSourceCurveKeyCount = 0;
    for (const FFloatCurve& Curve : FloatCurves)
    {
        const TArray<FRichCurveKey>& Keys = Curve.FloatCurve.GetConstRefOfKeys();
        OutSourceCurveKeyCount += Keys.Num();
        for (const FRichCurveKey& Key : Keys)
        {
            if (Key.TangentWeightMode != RCTWM_WeightedNone)
            {
                OutErrorCode = TEXT("WEIGHTED_CURVE_TANGENTS_UNSUPPORTED");
                OutErrorMessage = FString::Printf(
                    TEXT("Curve %s contains weighted tangents; refusing to silently change authoritative curve shape."),
                    *Curve.GetName().ToString());
                return false;
            }
        }
    }
    return true;
}

static void BuildTransportKeys(const FFloatCurve& Curve, TArray<FRichCurveKey>& OutKeys)
{
    const TArray<FRichCurveKey>& SourceKeys = Curve.FloatCurve.GetConstRefOfKeys();
    if (!SourceKeys.IsEmpty())
    {
        OutKeys = SourceKeys;
        return;
    }

    OutKeys.Reset();
    FRichCurveKey SyntheticKey(0.0f, Curve.Evaluate(0.0f));
    SyntheticKey.InterpMode = RCIM_Constant;
    SyntheticKey.TangentWeightMode = RCTWM_WeightedNone;
    OutKeys.Add(SyntheticKey);
}

static ERichCurveInterpMode EffectiveInterpModeForKey(
    const TArray<FRichCurveKey>& Keys,
    int32 KeyIndex)
{
    if (KeyIndex == Keys.Num() - 1 && KeyIndex > 0)
    {
        return Keys[KeyIndex - 1].InterpMode;
    }
    return Keys[KeyIndex].InterpMode;
}

static bool ExpectedFbxInterpolation(
    ERichCurveInterpMode InterpMode,
    FbxAnimCurveDef::EInterpolationType& OutInterpolation,
    FString& OutErrorCode,
    FString& OutErrorMessage,
    const FString& CurveName)
{
    switch (InterpMode)
    {
    case RCIM_Constant:
        OutInterpolation = FbxAnimCurveDef::eInterpolationConstant;
        return true;
    case RCIM_Linear:
        OutInterpolation = FbxAnimCurveDef::eInterpolationLinear;
        return true;
    case RCIM_Cubic:
        OutInterpolation = FbxAnimCurveDef::eInterpolationCubic;
        return true;
    default:
        OutErrorCode = TEXT("UNSUPPORTED_CURVE_INTERPOLATION");
        OutErrorMessage = FString::Printf(
            TEXT("Curve %s uses unsupported interpolation mode %d."),
            *CurveName,
            static_cast<int32>(InterpMode));
        return false;
    }
}

static bool ConfigureFbxKey(
    FbxAnimCurve* FbxCurve,
    int FbxKeyIndex,
    const FRichCurveKey& Key,
    ERichCurveInterpMode EffectiveInterpMode,
    FString& OutErrorCode,
    FString& OutErrorMessage,
    const FString& CurveName)
{
    FbxAnimCurveDef::EInterpolationType FbxInterpolation = FbxAnimCurveDef::eInterpolationLinear;
    if (!ExpectedFbxInterpolation(
        EffectiveInterpMode,
        FbxInterpolation,
        OutErrorCode,
        OutErrorMessage,
        CurveName))
    {
        return false;
    }

    FbxCurve->KeySetInterpolation(FbxKeyIndex, FbxInterpolation);
    if (EffectiveInterpMode == RCIM_Constant)
    {
        FbxCurve->KeySetConstantMode(FbxKeyIndex, FbxAnimCurveDef::eConstantStandard);
    }
    else if (EffectiveInterpMode == RCIM_Cubic)
    {
        FbxCurve->KeySetTangentMode(FbxKeyIndex, FbxAnimCurveDef::eTangentBreak);
        FbxCurve->KeySetLeftDerivative(FbxKeyIndex, Key.ArriveTangent);
        FbxCurve->KeySetRightDerivative(FbxKeyIndex, Key.LeaveTangent);
    }
    return true;
}

static bool EmbedFloatCurves(
    UAnimSequence* AnimSequence,
    const FString& BaseFbxPath,
    const FString& CurvedFbxPath,
    int32& OutCurveCount,
    int32& OutSourceCurveKeyCount,
    int32& OutCurveKeyCount,
    TArray<FString>& OutCurveNames,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    const IAnimationDataModel* DataModel = AnimSequence->GetDataModel();
    if (DataModel == nullptr)
    {
        OutErrorCode = TEXT("ANIM_DATA_MODEL_MISSING");
        OutErrorMessage = FString::Printf(TEXT("AnimSequence has no animation data model: %s"), *AnimSequence->GetPathName());
        return false;
    }

    const TArray<FFloatCurve>& FloatCurves = DataModel->GetFloatCurves();
    if (!ValidateCurveTransport(FloatCurves, OutSourceCurveKeyCount, OutErrorCode, OutErrorMessage))
    {
        return false;
    }

    FbxManager* Manager = FbxManager::Create();
    if (Manager == nullptr)
    {
        OutErrorCode = TEXT("FBX_MANAGER_CREATE_FAILED");
        OutErrorMessage = TEXT("Failed to create Autodesk FBX manager.");
        return false;
    }

    FbxIOSettings* IOSettings = FbxIOSettings::Create(Manager, IOSROOT);
    Manager->SetIOSettings(IOSettings);

    FbxImporter* Importer = FbxImporter::Create(Manager, "AIBridgeCurveImporter");
    FbxScene* Scene = FbxScene::Create(Manager, "AIBridgeCurveScene");
    if (Importer == nullptr || Scene == nullptr
        || !Importer->Initialize(TCHAR_TO_UTF8(*BaseFbxPath), -1, Manager->GetIOSettings())
        || !Importer->Import(Scene))
    {
        if (Importer != nullptr)
        {
            Importer->Destroy();
        }
        Manager->Destroy();
        OutErrorCode = TEXT("FBX_IMPORT_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to reopen Unreal-exported FBX for curve injection: %s"), *BaseFbxPath);
        return false;
    }
    Importer->Destroy();

    FbxNode* SkeletonRoot = FindSkeletonRoot(Scene->GetRootNode());
    if (SkeletonRoot == nullptr)
    {
        Manager->Destroy();
        OutErrorCode = TEXT("FBX_SKELETON_ROOT_NOT_FOUND");
        OutErrorMessage = TEXT("The exported FBX contains no skeleton root node for curve properties.");
        return false;
    }

    FbxAnimStack* AnimStack = Scene->GetSrcObject<FbxAnimStack>(0);
    if (AnimStack == nullptr)
    {
        AnimStack = FbxAnimStack::Create(Scene, "AIBridgeAnimStack");
    }
    FbxAnimLayer* AnimLayer = AnimStack->GetMember<FbxAnimLayer>(0);
    if (AnimLayer == nullptr)
    {
        AnimLayer = FbxAnimLayer::Create(Scene, "BaseLayer");
        AnimStack->AddMember(AnimLayer);
    }
    Scene->SetCurrentAnimationStack(AnimStack);

    OutCurveCount = 0;
    OutCurveKeyCount = 0;
    OutCurveNames.Reset();

    for (const FFloatCurve& Curve : FloatCurves)
    {
        const FString CurveName = Curve.GetName().ToString();
        if (CurveName.IsEmpty())
        {
            continue;
        }

        TArray<FRichCurveKey> TransportKeys;
        BuildTransportKeys(Curve, TransportKeys);

        FTCHARToUTF8 CurveNameUtf8(*CurveName);
        FbxProperty Property = SkeletonRoot->FindProperty(CurveNameUtf8.Get());
        if (!Property.IsValid())
        {
            Property = FbxProperty::Create(SkeletonRoot, FbxDoubleDT, CurveNameUtf8.Get());
        }
        if (!Property.IsValid())
        {
            Manager->Destroy();
            OutErrorCode = TEXT("FBX_PROPERTY_CREATE_FAILED");
            OutErrorMessage = FString::Printf(TEXT("Failed to create FBX user property for curve: %s"), *CurveName);
            return false;
        }

        Property.ModifyFlag(FbxPropertyFlags::eUserDefined, true);
        Property.ModifyFlag(FbxPropertyFlags::eAnimatable, true);
        Property.Set(static_cast<double>(TransportKeys[0].Value));

        FbxAnimCurve* FbxCurve = Property.GetCurve(AnimLayer, true);
        if (FbxCurve == nullptr)
        {
            Manager->Destroy();
            OutErrorCode = TEXT("FBX_ANIM_CURVE_CREATE_FAILED");
            OutErrorMessage = FString::Printf(TEXT("Failed to create FBX animation curve for property: %s"), *CurveName);
            return false;
        }

        FbxCurve->KeyModifyBegin();
        for (int32 KeyIndex = 0; KeyIndex < TransportKeys.Num(); ++KeyIndex)
        {
            const FRichCurveKey& Key = TransportKeys[KeyIndex];
            FbxTime KeyTime;
            KeyTime.SetSecondDouble(static_cast<double>(Key.Time));
            const int FbxKeyIndex = FbxCurve->KeyAdd(KeyTime);
            FbxCurve->KeySetValue(FbxKeyIndex, Key.Value);

            if (!ConfigureFbxKey(
                FbxCurve,
                FbxKeyIndex,
                Key,
                EffectiveInterpModeForKey(TransportKeys, KeyIndex),
                OutErrorCode,
                OutErrorMessage,
                CurveName))
            {
                FbxCurve->KeyModifyEnd();
                Manager->Destroy();
                return false;
            }
        }
        FbxCurve->KeyModifyEnd();

        ++OutCurveCount;
        OutCurveKeyCount += TransportKeys.Num();
        OutCurveNames.Add(CurveName);
    }

    FbxExporter* SceneExporter = FbxExporter::Create(Manager, "AIBridgeCurveExporter");
    const bool bExported = SceneExporter != nullptr
        && SceneExporter->Initialize(TCHAR_TO_UTF8(*CurvedFbxPath), -1, Manager->GetIOSettings())
        && SceneExporter->Export(Scene);
    if (SceneExporter != nullptr)
    {
        SceneExporter->Destroy();
    }
    Manager->Destroy();

    if (!bExported || !IFileManager::Get().FileExists(*CurvedFbxPath))
    {
        OutErrorCode = TEXT("FBX_EXPORT_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to write curve-preserving FBX: %s"), *CurvedFbxPath);
        return false;
    }
    return true;
}

static bool VerifyEmbeddedCurves(
    UAnimSequence* AnimSequence,
    const FString& CurvedFbxPath,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    const IAnimationDataModel* DataModel = AnimSequence->GetDataModel();
    if (DataModel == nullptr)
    {
        OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
        OutErrorMessage = TEXT("AnimSequence data model disappeared before FBX curve verification.");
        return false;
    }

    FbxManager* Manager = FbxManager::Create();
    if (Manager == nullptr)
    {
        OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
        OutErrorMessage = TEXT("Failed to create FBX manager for curve verification.");
        return false;
    }
    FbxIOSettings* IOSettings = FbxIOSettings::Create(Manager, IOSROOT);
    Manager->SetIOSettings(IOSettings);
    FbxImporter* Importer = FbxImporter::Create(Manager, "AIBridgeCurveVerifier");
    FbxScene* Scene = FbxScene::Create(Manager, "AIBridgeCurveVerifyScene");
    if (Importer == nullptr || Scene == nullptr
        || !Importer->Initialize(TCHAR_TO_UTF8(*CurvedFbxPath), -1, Manager->GetIOSettings())
        || !Importer->Import(Scene))
    {
        if (Importer != nullptr)
        {
            Importer->Destroy();
        }
        Manager->Destroy();
        OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Could not reopen exported FBX for verification: %s"), *CurvedFbxPath);
        return false;
    }
    Importer->Destroy();

    FbxNode* SkeletonRoot = FindSkeletonRoot(Scene->GetRootNode());
    FbxAnimStack* AnimStack = Scene->GetSrcObject<FbxAnimStack>(0);
    FbxAnimLayer* AnimLayer = AnimStack != nullptr ? AnimStack->GetMember<FbxAnimLayer>(0) : nullptr;
    if (SkeletonRoot == nullptr || AnimLayer == nullptr)
    {
        Manager->Destroy();
        OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
        OutErrorMessage = TEXT("Exported FBX is missing the skeleton root or animation layer during curve verification.");
        return false;
    }

    for (const FFloatCurve& Curve : DataModel->GetFloatCurves())
    {
        const FString CurveName = Curve.GetName().ToString();
        if (CurveName.IsEmpty())
        {
            continue;
        }

        TArray<FRichCurveKey> ExpectedKeys;
        BuildTransportKeys(Curve, ExpectedKeys);
        FTCHARToUTF8 CurveNameUtf8(*CurveName);
        FbxProperty Property = SkeletonRoot->FindProperty(CurveNameUtf8.Get());
        FbxAnimCurve* ReadbackCurve = Property.IsValid() ? Property.GetCurve(AnimLayer, false) : nullptr;
        if (!Property.IsValid() || ReadbackCurve == nullptr || ReadbackCurve->KeyGetCount() != ExpectedKeys.Num())
        {
            Manager->Destroy();
            OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
            OutErrorMessage = FString::Printf(TEXT("Curve property/key count mismatch after FBX write: %s"), *CurveName);
            return false;
        }

        for (int32 KeyIndex = 0; KeyIndex < ExpectedKeys.Num(); ++KeyIndex)
        {
            const FRichCurveKey& ExpectedKey = ExpectedKeys[KeyIndex];
            const double ReadbackTime = ReadbackCurve->KeyGetTime(KeyIndex).GetSecondDouble();
            const float ReadbackValue = ReadbackCurve->KeyGetValue(KeyIndex);
            if (FMath::Abs(ReadbackTime - static_cast<double>(ExpectedKey.Time)) > TimeToleranceSeconds
                || FMath::Abs(ReadbackValue - ExpectedKey.Value) > ValueTolerance)
            {
                Manager->Destroy();
                OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
                OutErrorMessage = FString::Printf(TEXT("Curve key time/value mismatch after FBX write: %s key %d"), *CurveName, KeyIndex);
                return false;
            }

            const ERichCurveInterpMode ExpectedInterpMode = EffectiveInterpModeForKey(ExpectedKeys, KeyIndex);
            FbxAnimCurveDef::EInterpolationType ExpectedFbxInterp = FbxAnimCurveDef::eInterpolationLinear;
            if (!ExpectedFbxInterpolation(
                ExpectedInterpMode,
                ExpectedFbxInterp,
                OutErrorCode,
                OutErrorMessage,
                CurveName))
            {
                Manager->Destroy();
                return false;
            }
            if (ReadbackCurve->KeyGetInterpolation(KeyIndex) != ExpectedFbxInterp)
            {
                Manager->Destroy();
                OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
                OutErrorMessage = FString::Printf(TEXT("Curve interpolation mismatch after FBX write: %s key %d"), *CurveName, KeyIndex);
                return false;
            }

            if (ExpectedInterpMode == RCIM_Constant
                && ReadbackCurve->KeyGetConstantMode(KeyIndex) != FbxAnimCurveDef::eConstantStandard)
            {
                Manager->Destroy();
                OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
                OutErrorMessage = FString::Printf(TEXT("Curve constant mode mismatch after FBX write: %s key %d"), *CurveName, KeyIndex);
                return false;
            }
            if (ExpectedInterpMode == RCIM_Cubic)
            {
                const float LeftDerivative = ReadbackCurve->KeyGetLeftDerivative(KeyIndex);
                const float RightDerivative = ReadbackCurve->KeyGetRightDerivative(KeyIndex);
                if (FMath::Abs(LeftDerivative - ExpectedKey.ArriveTangent) > TangentTolerance
                    || FMath::Abs(RightDerivative - ExpectedKey.LeaveTangent) > TangentTolerance)
                {
                    Manager->Destroy();
                    OutErrorCode = TEXT("FBX_CURVE_VERIFY_FAILED");
                    OutErrorMessage = FString::Printf(TEXT("Curve tangent mismatch after FBX write: %s key %d"), *CurveName, KeyIndex);
                    return false;
                }
            }
        }
    }

    Manager->Destroy();
    return true;
}
}

TSharedPtr<FJsonObject> ExportAnimSequenceFbxWithCurves(
    const TSharedPtr<FJsonObject>& Arguments,
    FString& OutErrorCode,
    FString& OutErrorMessage)
{
    OutErrorCode.Reset();
    OutErrorMessage.Reset();

    FString AssetPath;
    FString OutputPath;
    if (!Arguments.IsValid()
        || !Arguments->TryGetStringField(TEXT("asset_path"), AssetPath)
        || AssetPath.TrimStartAndEnd().IsEmpty())
    {
        OutErrorCode = TEXT("ANIM_SEQUENCE_PATH_REQUIRED");
        OutErrorMessage = TEXT("export.animsequence_fbx_with_curves requires asset_path.");
        return nullptr;
    }
    if (!Arguments->TryGetStringField(TEXT("output_path"), OutputPath)
        || OutputPath.TrimStartAndEnd().IsEmpty())
    {
        OutErrorCode = TEXT("OUTPUT_PATH_REQUIRED");
        OutErrorMessage = TEXT("export.animsequence_fbx_with_curves requires output_path.");
        return nullptr;
    }

    AssetPath = AssetPath.TrimStartAndEnd();
    OutputPath = OutputPath.TrimStartAndEnd();
    if (FPaths::IsRelative(OutputPath))
    {
        OutErrorCode = TEXT("OUTPUT_PATH_MUST_BE_ABSOLUTE");
        OutErrorMessage = TEXT("output_path must be an absolute filesystem path.");
        return nullptr;
    }
    FPaths::NormalizeFilename(OutputPath);
    FPaths::CollapseRelativeDirectories(OutputPath);
    if (!FPaths::GetExtension(OutputPath).Equals(TEXT("fbx"), ESearchCase::IgnoreCase))
    {
        OutErrorCode = TEXT("OUTPUT_PATH_MUST_BE_FBX");
        OutErrorMessage = TEXT("output_path must end in .fbx.");
        return nullptr;
    }

    const FString OutputDir = FPaths::GetPath(OutputPath);
    if (!IFileManager::Get().DirectoryExists(*OutputDir))
    {
        OutErrorCode = TEXT("OUTPUT_DIR_MISSING");
        OutErrorMessage = FString::Printf(TEXT("Output directory does not exist: %s"), *OutputDir);
        return nullptr;
    }

    UAnimSequence* AnimSequence = LoadObject<UAnimSequence>(nullptr, *AssetPath);
    if (!IsValid(AnimSequence))
    {
        OutErrorCode = TEXT("ANIM_SEQUENCE_NOT_FOUND");
        OutErrorMessage = FString::Printf(TEXT("AnimSequence could not be loaded: %s"), *AssetPath);
        return nullptr;
    }

    const FString Token = FGuid::NewGuid().ToString(EGuidFormats::Digits);
    const FString BaseTempPath = FPaths::Combine(OutputDir, FString::Printf(TEXT(".aibridge_%s_base.fbx"), *Token));
    const FString CurvedTempPath = FPaths::Combine(OutputDir, FString::Printf(TEXT(".aibridge_%s_curves.fbx"), *Token));
    const auto CleanupTemps = [&]()
    {
        CleanupTempFile(BaseTempPath);
        CleanupTempFile(CurvedTempPath);
    };

    if (!ExportBaseAnimSequence(AnimSequence, BaseTempPath, OutErrorCode, OutErrorMessage))
    {
        CleanupTemps();
        return nullptr;
    }

    int32 CurveCount = 0;
    int32 SourceCurveKeyCount = 0;
    int32 CurveKeyCount = 0;
    TArray<FString> CurveNames;
    if (!EmbedFloatCurves(
        AnimSequence,
        BaseTempPath,
        CurvedTempPath,
        CurveCount,
        SourceCurveKeyCount,
        CurveKeyCount,
        CurveNames,
        OutErrorCode,
        OutErrorMessage))
    {
        CleanupTemps();
        return nullptr;
    }

    if (!VerifyEmbeddedCurves(AnimSequence, CurvedTempPath, OutErrorCode, OutErrorMessage))
    {
        CleanupTemps();
        return nullptr;
    }

    if (!IFileManager::Get().Move(*OutputPath, *CurvedTempPath, true, true, false, true))
    {
        OutErrorCode = TEXT("OUTPUT_COMMIT_FAILED");
        OutErrorMessage = FString::Printf(TEXT("Failed to atomically commit exported FBX: %s"), *OutputPath);
        CleanupTemps();
        return nullptr;
    }
    CleanupTempFile(BaseTempPath);

    TSharedRef<FJsonObject> Result = MakeShared<FJsonObject>();
    Result->SetStringField(TEXT("schema_version"), ExportSchema);
    Result->SetStringField(TEXT("asset_path"), AnimSequence->GetPathName());
    Result->SetStringField(TEXT("output_path"), OutputPath);
    Result->SetNumberField(TEXT("curve_count"), CurveCount);
    Result->SetNumberField(TEXT("source_curve_key_count"), SourceCurveKeyCount);
    Result->SetNumberField(TEXT("curve_key_count"), CurveKeyCount);
    Result->SetBoolField(TEXT("curve_transport_verified"), true);
    Result->SetBoolField(TEXT("source_assets_modified"), false);
    Result->SetStringField(TEXT("curve_transport"), TEXT("FBX_ANIMATED_USER_PROPERTIES_ON_SKELETON_ROOT"));
    Result->SetStringField(TEXT("curve_interpolation"), TEXT("UE_RICH_CURVE_CONSTANT_LINEAR_CUBIC_UNWEIGHTED"));

    TArray<TSharedPtr<FJsonValue>> CurveNameValues;
    CurveNameValues.Reserve(CurveNames.Num());
    for (const FString& CurveName : CurveNames)
    {
        CurveNameValues.Add(MakeShared<FJsonValueString>(CurveName));
    }
    Result->SetArrayField(TEXT("curve_names"), CurveNameValues);
    return Result;
}
} // namespace AIBridgeUE
