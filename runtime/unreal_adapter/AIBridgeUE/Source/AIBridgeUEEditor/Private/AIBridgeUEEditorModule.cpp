#include "Modules/ModuleManager.h"

#include "AIBridgeUEAnimCurveExportMenu.h"
#include "AIBridgeUEAnimCurveFbxExporter.h"
#include "AIBridgeUEInspector.h"
#include "AIBridgeUESessionClient.h"
#include "Animation/AnimSequence.h"
#include "ContentBrowserMenuContexts.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "Framework/Docking/TabManager.h"
#include "HAL/PlatformProcess.h"
#include "Landscape.h"
#include "LandscapeStreamingProxy.h"
#include "DesktopPlatformModule.h"
#include "Framework/Application/SlateApplication.h"
#include "Framework/Commands/UIAction.h"
#include "IDesktopPlatform.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/MessageDialog.h"
#include "Misc/Paths.h"
#include "ToolMenu.h"
#include "ToolMenuSection.h"
#include "ToolMenus.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/Layout/SScrollBox.h"
#include "Widgets/SBoxPanel.h"
#include "Widgets/Text/STextBlock.h"

#define LOCTEXT_NAMESPACE "FAIBridgeUEEditorModule"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeUE, Log, All);

namespace AIBridgeUE
{
const FName LandscapeRepairTabName(TEXT("AIBridgeUE.LandscapeRepair"));

bool IsAnimSequenceOnlySelection(const TArray<FAssetData>& SelectedAssets)
{
    if (SelectedAssets.IsEmpty())
    {
        return false;
    }

    const FTopLevelAssetPath AnimSequenceClassPath = UAnimSequence::StaticClass()->GetClassPathName();
    for (const FAssetData& AssetData : SelectedAssets)
    {
        if (AssetData.AssetClassPath != AnimSequenceClassPath)
        {
            return false;
        }
    }
    return true;
}
}

class FAIBridgeUEEditorModule final : public IModuleInterface
{
public:
    virtual void StartupModule() override
    {
        MenuStartupHandle = UToolMenus::RegisterStartupCallback(
            FSimpleMulticastDelegate::FDelegate::CreateRaw(this, &FAIBridgeUEEditorModule::RegisterMenus));

        FGlobalTabmanager::Get()->RegisterNomadTabSpawner(
            AIBridgeUE::LandscapeRepairTabName,
            FOnSpawnTab::CreateRaw(this, &FAIBridgeUEEditorModule::SpawnLandscapeRepairTab))
            .SetDisplayName(LOCTEXT("LandscapeRepairTabTitle", "Landscape Repair"))
            .SetTooltipText(LOCTEXT("LandscapeRepairTabTooltip", "Inspect and queue guarded offline Landscape Edit Layer repair jobs."))
            .SetMenuType(ETabSpawnerMenuType::Hidden);

        SessionClient = MakeUnique<FAIBridgeUESessionClient>();
        SessionClient->Start();

        UE_LOG(
            LogAIBridgeUE,
            Display,
            TEXT("AI Bridge UE Editor V0.5.2 loaded; curve-preserving AnimSequence FBX export enabled."));
    }

    virtual void ShutdownModule() override
    {
        if (SessionClient)
        {
            SessionClient->Stop();
            SessionClient.Reset();
        }

        FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(AIBridgeUE::LandscapeRepairTabName);

        if (UToolMenus::TryGet() != nullptr)
        {
            UToolMenus::UnRegisterStartupCallback(MenuStartupHandle);
            UToolMenus::UnregisterOwner(this);
        }
    }

private:
    void RegisterMenus()
    {
        FToolMenuOwnerScoped OwnerScoped(this);

        if (UToolMenu* WindowMenu = UToolMenus::Get()->ExtendMenu(TEXT("LevelEditor.MainMenu.Window")))
        {
            FToolMenuSection& Section = WindowMenu->FindOrAddSection(TEXT("AIBridgeUE"));
            Section.AddMenuEntry(
                TEXT("AIBridgeUE_InspectCurrentLevel"),
                LOCTEXT("InspectCurrentLevelLabel", "Inspect Current Level"),
                LOCTEXT(
                    "InspectCurrentLevelTooltip",
                    "Read the current level and write a shallow read-only scene manifest."),
                FSlateIcon(),
                FUIAction(FExecuteAction::CreateRaw(this, &FAIBridgeUEEditorModule::InspectCurrentLevel)),
                EUserInterfaceActionType::Button,
                NAME_None);

            Section.AddMenuEntry(
                TEXT("AIBridgeUE_LandscapeRepair"),
                LOCTEXT("LandscapeRepairLabel", "Landscape Repair"),
                LOCTEXT(
                    "LandscapeRepairTooltip",
                    "Open the guarded offline Landscape Edit Layer repair panel. Bridge is not required."),
                FSlateIcon(),
                FUIAction(FExecuteAction::CreateRaw(this, &FAIBridgeUEEditorModule::OpenLandscapeRepairPanel)),
                EUserInterfaceActionType::Button,
                NAME_None);
        }

        if (UToolMenu* AssetContextMenu = UToolMenus::Get()->ExtendMenu(TEXT("ContentBrowser.AssetContextMenu")))
        {
            FToolMenuSection& Section = AssetContextMenu->FindOrAddSection(TEXT("AIBridgeUE"));
            Section.AddDynamicEntry(
                TEXT("AIBridgeUE_AnimCurveExportDynamic"),
                FNewToolMenuSectionDelegate::CreateRaw(
                    this,
                    &FAIBridgeUEEditorModule::PopulateAnimSequenceCurveExportMenu));
        }
    }

    void OpenLandscapeRepairPanel()
    {
        FGlobalTabmanager::Get()->TryInvokeTab(AIBridgeUE::LandscapeRepairTabName);
    }

    bool ResolveLandscapeRepairTarget(
        FString& OutMap,
        FString& OutLandscapePath,
        int32& OutProxyCount,
        int32& OutComponentCount,
        FString& OutError) const
    {
        OutMap.Reset();
        OutLandscapePath.Reset();
        OutProxyCount = 0;
        OutComponentCount = 0;
        OutError.Reset();

        if (GEditor == nullptr || GEditor->PlayWorld != nullptr)
        {
            OutError = TEXT("Editor world is not available.");
            return false;
        }

        UWorld* World = GEditor->GetEditorWorldContext().World();
        if (World == nullptr || World->IsGameWorld())
        {
            OutError = TEXT("Open an editor world before using Landscape Repair.");
            return false;
        }

        TArray<ALandscape*> Parents;
        for (TActorIterator<ALandscape> It(World); It; ++It)
        {
            if (It->GetLandscapeInfo() != nullptr)
            {
                Parents.Add(*It);
            }
        }

        if (Parents.Num() != 1)
        {
            OutError = FString::Printf(
                TEXT("This release intentionally supports exactly one registered Landscape parent per map; found %d."),
                Parents.Num());
            return false;
        }

        ALandscape* Parent = Parents[0];
        for (TActorIterator<ALandscapeStreamingProxy> It(World); It; ++It)
        {
            ALandscapeStreamingProxy* Proxy = *It;
            if (Proxy != nullptr && Proxy->GetLandscapeActor() == Parent && !Proxy->LandscapeComponents.IsEmpty())
            {
                ++OutProxyCount;
                OutComponentCount += Proxy->LandscapeComponents.Num();
            }
        }

        OutMap = World->GetOutermost()->GetName();
        OutLandscapePath = Parent->GetPathName();
        return true;
    }

    FText GetLandscapeRepairStatusText() const
    {
        FString Map;
        FString LandscapePath;
        FString Error;
        int32 ProxyCount = 0;
        int32 ComponentCount = 0;
        if (!ResolveLandscapeRepairTarget(Map, LandscapePath, ProxyCount, ComponentCount, Error))
        {
            return FText::FromString(TEXT("Target unavailable: ") + Error);
        }

        return FText::FromString(FString::Printf(
            TEXT("Map: %s\nLandscape: %s\nLoaded streaming proxies: %d\nLoaded components: %d\n\n")
            TEXT("Repair jobs are executed in an independent UnrealEditor-Cmd process after this editor exits."),
            *Map,
            *LandscapePath,
            ProxyCount,
            ComponentCount));
    }

    FReply QueueLandscapeRepairAction(const FString Action)
    {
        FString Map;
        FString LandscapePath;
        FString Error;
        int32 ProxyCount = 0;
        int32 ComponentCount = 0;
        if (!ResolveLandscapeRepairTarget(Map, LandscapePath, ProxyCount, ComponentCount, Error))
        {
            FMessageDialog::Open(EAppMsgType::Ok, FText::FromString(Error));
            return FReply::Handled();
        }

#if !PLATFORM_WINDOWS
        FMessageDialog::Open(
            EAppMsgType::Ok,
            LOCTEXT("LandscapeRepairWindowsOnly", "The first public/manual scheduler currently supports Windows Editor only."));
        return FReply::Handled();
#else
        FString ProjectFile = FPaths::ConvertRelativePathToFull(FPaths::GetProjectFilePath());
        FPaths::NormalizeFilename(ProjectFile);
        const TSharedPtr<IPlugin> Plugin = IPluginManager::Get().FindPlugin(TEXT("AIBridgeUE"));
        if (!Plugin.IsValid())
        {
            FMessageDialog::Open(EAppMsgType::Ok, LOCTEXT("LandscapeRepairPluginMissing", "AIBridgeUE plugin directory could not be resolved."));
            return FReply::Handled();
        }
        FString ScriptPath = FPaths::ConvertRelativePathToFull(
            FPaths::Combine(Plugin->GetBaseDir(), TEXT("Resources/LandscapeRepair/LandscapeRepair_ScheduledJob.ps1")));
        FPaths::NormalizeFilename(ScriptPath);
        FString EditorExecutable = FString(FPlatformProcess::ExecutablePath());
        FPaths::NormalizeFilename(EditorExecutable);

        if (ProjectFile.IsEmpty() || !FPaths::FileExists(ProjectFile))
        {
            FMessageDialog::Open(EAppMsgType::Ok, LOCTEXT("LandscapeRepairProjectMissing", "Project file could not be resolved."));
            return FReply::Handled();
        }
        if (!FPaths::FileExists(ScriptPath))
        {
            FMessageDialog::Open(
                EAppMsgType::Ok,
                FText::FromString(TEXT("Landscape repair scheduler is missing:\n") + ScriptPath));
            return FReply::Handled();
        }

        const FString Arguments = FString::Printf(
            TEXT("-NoProfile -NonInteractive -ExecutionPolicy Bypass -File \"%s\" -Mode launch -Action %s ")
            TEXT("-RequesterPid %u -WaitForRequesterExit -ProjectFile \"%s\" -Map \"%s\" ")
            TEXT("-ExpectedProxies %d -EditorExecutable \"%s\" -Reopen"),
            *ScriptPath,
            *Action,
            FPlatformProcess::GetCurrentProcessId(),
            *ProjectFile,
            *Map,
            0,
            *EditorExecutable);

        FProcHandle Handle = FPlatformProcess::CreateProc(
            TEXT("powershell.exe"),
            *Arguments,
            true,
            true,
            false,
            nullptr,
            0,
            *FPaths::ProjectDir(),
            nullptr);

        if (!Handle.IsValid())
        {
            FMessageDialog::Open(
                EAppMsgType::Ok,
                LOCTEXT("LandscapeRepairQueueFailed", "Failed to start the Landscape repair scheduler."));
            return FReply::Handled();
        }

        FPlatformProcess::CloseProc(Handle);
        FMessageDialog::Open(
            EAppMsgType::Ok,
            FText::FromString(
                FString::Printf(
                    TEXT("Queued offline Landscape action: %s\n\n")
                    TEXT("Close this Unreal Editor normally. The job waits for this process to exit, ")
                    TEXT("runs in UnrealEditor-Cmd, then reopens the project.\n\n")
                    TEXT("Unsaved unrelated work should be saved or discarded normally before closing."),
                    *Action)));
        return FReply::Handled();
#endif
    }

    TSharedRef<SDockTab> SpawnLandscapeRepairTab(const FSpawnTabArgs&)
    {
        return SNew(SDockTab)
            .TabRole(ETabRole::NomadTab)
            [
                SNew(SScrollBox)
                + SScrollBox::Slot()
                [
                    SNew(SVerticalBox)
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f)
                    [
                        SNew(STextBlock)
                        .Text(LOCTEXT(
                            "LandscapeRepairIntro",
                            "Guarded Landscape Edit Layer maintenance. Bridge is optional; all write operations run offline in a separate commandlet process."))
                        .AutoWrapText(true)
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f, 0.0f, 12.0f, 12.0f)
                    [
                        SNew(STextBlock)
                        .Text_Lambda([this]() { return GetLandscapeRepairStatusText(); })
                        .AutoWrapText(true)
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f, 4.0f)
                    [
                        SNew(SButton)
                        .Text(LOCTEXT("LandscapeRepairScan", "Queue Offline Scan"))
                        .ToolTipText(LOCTEXT("LandscapeRepairScanTip", "Read-only scan. Close the editor after queuing; the project reopens when complete."))
                        .OnClicked_Lambda([this]() { return QueueLandscapeRepairAction(TEXT("scan")); })
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f, 4.0f)
                    [
                        SNew(SButton)
                        .Text(LOCTEXT("LandscapeRepairMigrate", "Repair Orphan Edit Layer Data"))
                        .ToolTipText(LOCTEXT("LandscapeRepairMigrateTip", "Run the guarded disk-final migration path with backup, verification and save gates."))
                        .OnClicked_Lambda([this]() { return QueueLandscapeRepairAction(TEXT("repair")); })
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f, 4.0f)
                    [
                        SNew(SButton)
                        .Text(LOCTEXT("LandscapeRepairNormalize", "Stabilize Height Shared Edges"))
                        .ToolTipText(LOCTEXT("LandscapeRepairNormalizeTip", "Run the Height-only stabilization path after migration is clean."))
                        .OnClicked_Lambda([this]() { return QueueLandscapeRepairAction(TEXT("normalize")); })
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f, 4.0f, 12.0f, 12.0f)
                    [
                        SNew(SButton)
                        .Text(LOCTEXT("LandscapeRepairVerify", "Cold Verify Last Saved Repair"))
                        .ToolTipText(LOCTEXT("LandscapeRepairVerifyTip", "Cold-reopen verification of the last saved Landscape repair run."))
                        .OnClicked_Lambda([this]() { return QueueLandscapeRepairAction(TEXT("verify")); })
                    ]
                    + SVerticalBox::Slot()
                    .AutoHeight()
                    .Padding(12.0f)
                    [
                        SNew(STextBlock)
                        .Text(LOCTEXT(
                            "LandscapeRepairLimitations",
                            "Current safety envelope: UE 5.8 Editor, World Partition, exactly one registered Landscape parent, one identity persistent Edit Layer, no Landscape Blueprint Brush layer. Unsupported configurations fail closed instead of being guessed."))
                        .AutoWrapText(true)
                    ]
                ]
            ];
    }

    void PopulateAnimSequenceCurveExportMenu(FToolMenuSection& Section)
    {
        const UContentBrowserAssetContextMenuContext* Context =
            Section.FindContext<UContentBrowserAssetContextMenuContext>();
        if (Context == nullptr || !AIBridgeUE::IsAnimSequenceOnlySelection(Context->SelectedAssets))
        {
            return;
        }

        const TArray<FAssetData> SelectedAssets = Context->SelectedAssets;
        Section.AddSubMenu(
            TEXT("AIBridgeUE_AnimSequenceSubMenu"),
            LOCTEXT("AIBridgeAssetSubMenuLabel", "AI Bridge"),
            LOCTEXT("AIBridgeAssetSubMenuTooltip", "AI Bridge asset actions."),
            FNewToolMenuDelegate::CreateLambda(
                [this, SelectedAssets](UToolMenu* SubMenu)
                {
                    FToolMenuSection& ExportSection =
                        SubMenu->FindOrAddSection(TEXT("AIBridgeUE_AnimationExport"));
                    ExportSection.AddMenuEntry(
                        TEXT("AIBridgeUE_ExportAnimSequenceFbxWithCurves"),
                        LOCTEXT("ExportAnimSequenceFbxWithCurvesLabel", "Export FBX With Curves"),
                        LOCTEXT(
                            "ExportAnimSequenceFbxWithCurvesTooltip",
                            "Export the selected AnimSequence assets to FBX while preserving Unreal Float Curves as animated FBX custom properties."),
                        FSlateIcon(),
                        FUIAction(FExecuteAction::CreateLambda(
                            [this, SelectedAssets]()
                            {
                                ExportAnimSequencesWithCurves(SelectedAssets);
                            })),
                        EUserInterfaceActionType::Button,
                        NAME_None);
                }));
    }

    void ExportAnimSequencesWithCurves(const TArray<FAssetData>& SelectedAssets)
    {
        if (!AIBridgeUE::IsAnimSequenceOnlySelection(SelectedAssets))
        {
            return;
        }

        IDesktopPlatform* DesktopPlatform = FDesktopPlatformModule::Get();
        if (DesktopPlatform == nullptr)
        {
            UE_LOG(LogAIBridgeUE, Error, TEXT("DesktopPlatform is unavailable; cannot choose an FBX export directory."));
            FMessageDialog::Open(
                EAppMsgType::Ok,
                LOCTEXT("DesktopPlatformUnavailable", "Unable to open the export-folder picker."));
            return;
        }

        FString InitialDirectory = LastAnimCurveExportDirectory;
        if (InitialDirectory.IsEmpty())
        {
            InitialDirectory = FPaths::ProjectSavedDir();
        }

        const void* ParentWindowHandle = nullptr;
        if (FSlateApplication::IsInitialized())
        {
            ParentWindowHandle = FSlateApplication::Get().FindBestParentWindowHandleForDialogs(
                nullptr,
                ESlateParentWindowSearchMethod::ActiveWindow);
        }

        FString OutputDirectory;
        if (!DesktopPlatform->OpenDirectoryDialog(
                ParentWindowHandle,
                TEXT("Export AnimSequence FBX With Curves"),
                InitialDirectory,
                OutputDirectory))
        {
            return;
        }

        FPaths::NormalizeDirectoryName(OutputDirectory);
        LastAnimCurveExportDirectory = OutputDirectory;

        int32 SuccessCount = 0;
        int32 FailureCount = 0;
        int32 TotalCurveCount = 0;

        for (const FAssetData& AssetData : SelectedAssets)
        {
            const FString AssetPath = AssetData.GetSoftObjectPath().ToString();
            FString OutputPath = FPaths::ConvertRelativePathToFull(
                FPaths::Combine(OutputDirectory, AssetData.AssetName.ToString() + TEXT(".fbx")));
            FPaths::NormalizeFilename(OutputPath);

            TSharedRef<FJsonObject> Arguments = MakeShared<FJsonObject>();
            Arguments->SetStringField(TEXT("asset_path"), AssetPath);
            Arguments->SetStringField(TEXT("output_path"), OutputPath);

            FString ErrorCode;
            FString ErrorMessage;
            const TSharedPtr<FJsonObject> Result =
                AIBridgeUE::ExportAnimSequenceFbxWithCurves(Arguments, ErrorCode, ErrorMessage);
            if (!Result.IsValid())
            {
                ++FailureCount;
                UE_LOG(
                    LogAIBridgeUE,
                    Error,
                    TEXT("AnimSequence curve FBX export failed [%s] %s -> %s: %s"),
                    *ErrorCode,
                    *AssetPath,
                    *OutputPath,
                    *ErrorMessage);
                continue;
            }

            ++SuccessCount;
            double CurveCount = 0.0;
            if (Result->TryGetNumberField(TEXT("curve_count"), CurveCount))
            {
                TotalCurveCount += FMath::RoundToInt(CurveCount);
            }

            UE_LOG(
                LogAIBridgeUE,
                Display,
                TEXT("AnimSequence curve FBX export verified: %s -> %s"),
                *AssetPath,
                *OutputPath);
        }

        FString Summary = FString::Printf(
            TEXT("Exported: %d / Failed: %d / Curves: %d\n\nOutput: %s"),
            SuccessCount,
            FailureCount,
            TotalCurveCount,
            *OutputDirectory);
        if (FailureCount > 0)
        {
            Summary += TEXT("\n\nSee Output Log for failure details.");
        }

        FMessageDialog::Open(EAppMsgType::Ok, FText::FromString(Summary));
    }

    void InspectCurrentLevel()
    {
        FString OutputPath;
        FString Error;
        if (!AIBridgeUE::WriteCurrentLevelManifest(nullptr, OutputPath, Error))
        {
            UE_LOG(LogAIBridgeUE, Error, TEXT("Current-level inspection failed: %s"), *Error);
            return;
        }

        UE_LOG(LogAIBridgeUE, Display, TEXT("Read-only scene manifest written: %s"), *OutputPath);
    }

    FDelegateHandle MenuStartupHandle;
    TUniquePtr<FAIBridgeUESessionClient> SessionClient;
    FString LastAnimCurveExportDirectory;
};

IMPLEMENT_MODULE(FAIBridgeUEEditorModule, AIBridgeUEEditor)

#undef LOCTEXT_NAMESPACE
