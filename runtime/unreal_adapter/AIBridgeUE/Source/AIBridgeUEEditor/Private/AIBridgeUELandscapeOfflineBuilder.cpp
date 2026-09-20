#include "AIBridgeUELandscapeOfflineBuilder.h"
#include "AIBridgeUELandscapeRepair.h"
#include "AssetCompilingManager.h"
#include "IAssetCompilingManager.h"
#include "Async/TaskGraphInterfaces.h"
#include "Containers/Ticker.h"
#include "AIBridgeUELandscapeWaitPolicy.h"
#include "AIBridgeUELandscapeResources.h"
#include "EngineUtils.h"
#include "HAL/FileManager.h"
#include "HAL/PlatformMemory.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "Landscape.h"
#include "LandscapeStreamingProxy.h"
#include "Misc/DateTime.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/WorldPartitionHelpers.h"
#include "WorldPartition/WorldPartitionActorDescInstance.h"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeLandscapeOffline, Log, All);
namespace
{
const FString ProcessInstance = FGuid::NewGuid().ToString(EGuidFormats::Digits);
bool WriteJson(const FString& Path, const TSharedPtr<FJsonObject>& J)
{
    IFileManager::Get().MakeDirectory(*FPaths::GetPath(Path), true);
    FString Text;
    if (!J.IsValid() || !FJsonSerializer::Serialize(J.ToSharedRef(), TJsonWriterFactory<TCHAR, TPrettyJsonPrintPolicy<TCHAR>>::Create(&Text))) return false;
    if (!FFileHelper::SaveStringToFile(Text, *(Path + TEXT(".tmp")), FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)) return false;
    return IFileManager::Get().Move(*Path, *(Path + TEXT(".tmp")), true, false, false, true);
}
bool ReadJson(const FString& Path, TSharedPtr<FJsonObject>& J)
{
    FString Text; return FFileHelper::LoadFileToString(Text, *Path) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text), J) && J.IsValid();
}
bool MemorySafe()
{
    const FPlatformMemoryStats M = FPlatformMemory::GetStats();
    return M.AvailablePhysical >= 8ull * 1024 * 1024 * 1024 && M.UsedVirtual < 32ull * 1024 * 1024 * 1024;
}
void RecordMemory(const TSharedPtr<FJsonObject>& J)
{
    const FPlatformMemoryStats M = FPlatformMemory::GetStats();
    J->SetNumberField(TEXT("private_bytes"), double(M.UsedVirtual));
    J->SetNumberField(TEXT("available_physical_bytes"), double(M.AvailablePhysical));
    J->SetNumberField(TEXT("peak_private_bytes"), double(M.PeakUsedVirtual));
}
}

UAIBridgeUELandscapeOfflineBuilder::UAIBridgeUELandscapeOfflineBuilder(const FObjectInitializer& ObjectInitializer) : Super(ObjectInitializer)
{
    bLoadNonDynamicDataLayers = false;
}

bool UAIBridgeUELandscapeOfflineBuilder::RunInternal(UWorld* World, const FCellInfo&, FPackageSourceControlHelper&)
{
    FString Action = TEXT("scan"), Job, Run;
    int32 ExpectedProxies = 0;
    GetParamValue(TEXT("RepairAction="), Action); GetParamValue(TEXT("RepairJob="), Job);
    GetParamValue(TEXT("RepairRunId="), Run); GetParamValue(TEXT("ExpectedProxies="), ExpectedProxies);
    FGuid JobGuid;
    if (!FGuid::ParseExact(Job, EGuidFormats::Digits, JobGuid) || ExpectedProxies < 0) return false;
    const FString Base = FPaths::Combine(FPaths::ProjectSavedDir(), TEXT("AIBridgeLandscapeRepair/Offline"));
    const FString Output = FPaths::Combine(Base, TEXT("Jobs"), Job, TEXT("report.json"));
    if (IFileManager::Get().FileExists(*Output)) return false; // no mutation replay on job reuse
    auto Report = MakeShared<FJsonObject>();
    Report->SetStringField(TEXT("schema_version"), TEXT("aibridge_landscape_offline/1"));
    Report->SetStringField(TEXT("job_id"), Job); Report->SetStringField(TEXT("action"), Action);
    Report->SetStringField(TEXT("process_instance"), ProcessInstance);
    Report->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
    Report->SetStringField(TEXT("started_utc"), FDateTime::UtcNow().ToIso8601());
    Report->SetBoolField(TEXT("ok"), false); Report->SetBoolField(TEXT("map_saved"), false);
    const auto Fail = [&](const FString& Error)
    {
        Report->SetStringField(TEXT("error"), Error); RecordMemory(Report); WriteJson(Output, Report);
        UE_LOG(LogAIBridgeLandscapeOffline, Error, TEXT("%s; report=%s"), *Error, *Output); return false;
    };
    if (!World || !World->GetWorldPartition()) return Fail(TEXT("WORLD_PARTITION_REQUIRED"));
    if (Action != TEXT("inventory") && Action != TEXT("scan") && Action != TEXT("repair") && Action != TEXT("normalize") && Action != TEXT("verify")) return Fail(TEXT("ACTION_UNSUPPORTED"));
    UWorldPartition* WP = World->GetWorldPartition();
    const FString Map = World->GetOutermost()->GetName(); Report->SetStringField(TEXT("map"), Map);
    TArray<TSharedPtr<FJsonValue>> Packages; TSet<FName> UniquePackages;
    int32 ParentCount = 0, ProxyCount = 0;
    FWorldPartitionHelpers::ForEachActorDescInstance<ALandscapeProxy>(WP, [&](const FWorldPartitionActorDescInstance* D)
    {
        UClass* Native = D->GetActorNativeClass();
        if (Native && Native->IsChildOf(ALandscape::StaticClass())) ++ParentCount;
        if (Native && Native->IsChildOf(ALandscapeStreamingProxy::StaticClass())) ++ProxyCount;
        const FName Package = D->GetActorPackage();
        if (!UniquePackages.Contains(Package)) { UniquePackages.Add(Package); Packages.Add(MakeShared<FJsonValueString>(Package.ToString())); }
        return true;
    });
    Report->SetArrayField(TEXT("landscape_packages"), Packages);
    Report->SetNumberField(TEXT("descriptor_parent_count"), ParentCount);
    Report->SetNumberField(TEXT("descriptor_proxy_count"), ProxyCount);
    if (ExpectedProxies == 0) ExpectedProxies = ProxyCount;
    Report->SetNumberField(TEXT("expected_proxy_count"), ExpectedProxies);
    RecordMemory(Report);
    if (!WriteJson(Output, Report)) return false;
    if (ParentCount != 1 || ProxyCount != ExpectedProxies) return Fail(TEXT("DESCRIPTOR_COVERAGE_MISMATCH"));
    if (Action == TEXT("inventory")) { Report->SetBoolField(TEXT("ok"), true); return WriteJson(Output, Report); }
    if (!MemorySafe()) return Fail(TEXT("MEMORY_HEADROOM_INSUFFICIENT"));
    // Explicit class-filtered loads: never load an entire editor region.
    FWorldPartitionHelpers::FForEachActorWithLoadingResult ParentReferences, ProxyReferences;
    FWorldPartitionHelpers::FForEachActorWithLoadingParams Params;
    Params.bKeepReferences = true; Params.ActorClasses.Add(ALandscape::StaticClass());
    FWorldPartitionHelpers::ForEachActorWithLoading(WP, [](const FWorldPartitionActorDescInstance*) { return true; }, Params, ParentReferences);
    bool bMemoryOk = true; int32 Loaded = 0;
    Params.ActorClasses.Reset(); Params.ActorClasses.Add(ALandscapeStreamingProxy::StaticClass());
    FWorldPartitionHelpers::ForEachActorWithLoading(WP, [&](const FWorldPartitionActorDescInstance*)
    {
        ++Loaded; bMemoryOk = MemorySafe();
        if ((Loaded % 16) == 0) { UE_LOG(LogAIBridgeLandscapeOffline, Display, TEXT("Loaded terrain %d/%d"), Loaded, ExpectedProxies); }
        return bMemoryOk;
    }, Params, ProxyReferences);
    Report->SetNumberField(TEXT("selected_proxies_loaded"), Loaded); RecordMemory(Report);
    if (!bMemoryOk || !MemorySafe()) return Fail(TEXT("MEMORY_GUARD_STOPPED_BEFORE_REPAIR"));
    auto RepairArguments = MakeShared<FJsonObject>(); RepairArguments->SetStringField(TEXT("map"), Map);
    RepairArguments->SetNumberField(TEXT("expected_proxy_count"), ExpectedProxies);
    // No tick or layer merge has been pumped since the class-filtered disk loads.
    const bool bNormalize = Action == TEXT("normalize");
    FString Strategy = bNormalize ? TEXT("edit_stable_height_v1") : TEXT("disk_final_offline_v1");
    RepairArguments->SetStringField(TEXT("strategy"), Strategy);
    RepairArguments->SetBoolField(TEXT("disk_final_before_merge"), !bNormalize);
    Report->SetStringField(TEXT("strategy"), Strategy);
    FString Code, Message; TSharedPtr<FJsonObject> Native;
    const auto Execute = [&](const FString& Step)
    {
        Code.Reset(); Message.Reset(); RepairArguments->SetStringField(TEXT("action"), Step);
        Native = AIBridgeUE::LandscapeRepair::Execute(RepairArguments, false, Code, Message);
        Report->SetStringField(TEXT("last_step"), Step);
        if (Native) Report->SetObjectField(Step, Native);
        RecordMemory(Report); const bool Written = WriteJson(Output, Report);
        bool Ok = false; return Written && Code.IsEmpty() && Native && Native->TryGetBoolField(TEXT("ok"), Ok) && Ok;
    };
    TSharedPtr<FJsonObject> Origin;
    if (Action == TEXT("verify"))
    {
        FGuid RunGuid;
        if (!FGuid::ParseExact(Run, EGuidFormats::Digits, RunGuid) || !ReadJson(FPaths::Combine(Base, TEXT("Runs"), Run + TEXT(".json")), Origin)) return Fail(TEXT("OFFLINE_RUN_ORIGIN_REQUIRED"));
        FString OldInstance, OldMap; Origin->TryGetStringField(TEXT("process_instance"), OldInstance); Origin->TryGetStringField(TEXT("map"), OldMap);
        if (OldInstance.IsEmpty() || OldInstance == ProcessInstance || OldMap != Map) return Fail(TEXT("COLD_PROCESS_REQUIRED"));
        FString OriginStrategy;
        if (Origin->TryGetStringField(TEXT("strategy"), OriginStrategy) && !OriginStrategy.IsEmpty())
        {
            Strategy = OriginStrategy;
            RepairArguments->SetStringField(TEXT("strategy"), Strategy);
            RepairArguments->SetBoolField(TEXT("disk_final_before_merge"), Strategy == TEXT("disk_final_offline_v1"));
            Report->SetStringField(TEXT("strategy"), Strategy);
        }
        RepairArguments->SetStringField(TEXT("run_id"), Run);
    }
    else
    {
        if (Action == TEXT("normalize"))
        {
            if (!HasParam(TEXT("AuthorizeRepair"))) return Fail(TEXT("NORMALIZE_AUTHORIZATION_REQUIRED"));
            RepairArguments->SetBoolField(TEXT("allow_overwrite_target"), true);
            if (!Execute(TEXT("normalize"))) return Fail(Code + TEXT(": ") + Message);
        }
        else
        {
            if (!Execute(TEXT("scan"))) return Fail(Code + TEXT(": ") + Message);
            if (Action == TEXT("scan")) { Report->SetBoolField(TEXT("ok"), true); return WriteJson(Output, Report); }
            bool Ready = false; Native->TryGetBoolField(TEXT("ready_to_apply"), Ready);
            if (!Ready || !HasParam(TEXT("AuthorizeRepair"))) return Fail(TEXT("REPAIR_PREFLIGHT_OR_AUTHORIZATION_FAILED"));
            FString Hash; Native->TryGetStringField(TEXT("plan_hash"), Hash);
            RepairArguments->SetStringField(TEXT("expected_plan_hash"), Hash); RepairArguments->SetBoolField(TEXT("allow_overwrite_target"), true);
            if (!Execute(TEXT("apply"))) return Fail(Code + TEXT(": ") + Message);
        }
        Native->TryGetStringField(TEXT("run_id"), Run); Report->SetStringField(TEXT("run_id"), Run);
        Origin = MakeShared<FJsonObject>(); Origin->SetStringField(TEXT("run_id"), Run); Origin->SetStringField(TEXT("map"), Map);
        Origin->SetStringField(TEXT("process_instance"), ProcessInstance); Origin->SetStringField(TEXT("job_id"), Job);
        Origin->SetStringField(TEXT("strategy"), Strategy);
        Origin->SetNumberField(TEXT("pid"), FPlatformProcess::GetCurrentProcessId());
        if (!WriteJson(FPaths::Combine(Base, TEXT("Runs"), Run + TEXT(".json")), Origin)) return Fail(TEXT("ORIGIN_WRITE_FAILED_AFTER_APPLY"));
        RepairArguments->SetStringField(TEXT("run_id"), Run); RepairArguments->SetBoolField(TEXT("save_packages"), true);
    }
    // Pump completion instead of entering an opaque, unbounded global blocking wait.
    // Keep the same all-manager dependency scope until production evidence permits narrowing it.
    using namespace AIBridgeUE::LandscapeRepair;
    const double CompileStart = FPlatformTime::Seconds();
    double NextProgress = 0.0;
    const FString ProgressPath = FPaths::Combine(Base, TEXT("Jobs"), Job, TEXT("progress.json"));
    const FString CancelPath = FPaths::Combine(Base, TEXT("Jobs"), Job, TEXT("cancel.request"));
    Report->SetStringField(TEXT("phase"), TEXT("compiling_assets"));
    if (!WriteJson(Output, Report)) return false;
    for (;;)
    {
        FAssetCompilingManager& Compiler = FAssetCompilingManager::Get();
        const int32 Remaining = Compiler.GetNumRemainingAssets();
        const double Elapsed = FPlatformTime::Seconds() - CompileStart;
        const EWaitDecision Decision = EvaluateWait(Remaining, Elapsed, MemorySafe(), IFileManager::Get().FileExists(*CancelPath));
        if (Elapsed >= NextProgress || Decision != EWaitDecision::Pending)
        {
            auto Progress = MakeShared<FJsonObject>();
            Progress->SetStringField(TEXT("job_id"), Job); Progress->SetStringField(TEXT("run_id"), Run);
            Progress->SetStringField(TEXT("phase"), TEXT("compiling_assets"));
            Progress->SetStringField(TEXT("utc"), FDateTime::UtcNow().ToIso8601());
            Progress->SetNumberField(TEXT("elapsed_seconds"), Elapsed);
            Progress->SetNumberField(TEXT("deadline_seconds"), CompilationWaitSeconds);
            Progress->SetNumberField(TEXT("remaining_assets"), Remaining);
            auto Managers = MakeShared<FJsonObject>();
            for (IAssetCompilingManager* Manager : Compiler.GetRegisteredManagers())
                if (Manager) Managers->SetNumberField(Manager->GetAssetTypeName().ToString(), Manager->GetNumRemainingAssets());
            Progress->SetObjectField(TEXT("remaining_by_manager"), Managers);
            RecordMemory(Progress); Report->SetObjectField(TEXT("compilation_progress"), Progress);
            if (!WriteJson(ProgressPath, Progress)) return Fail(TEXT("COMPILATION_PROGRESS_WRITE_FAILED_NO_SAVE"));
            UE_LOG(LogAIBridgeLandscapeOffline, Display, TEXT("Compilation wait %.1fs: %d assets remaining"), Elapsed, Remaining);
            NextProgress = Elapsed + 2.0;
        }
        if (Decision == EWaitDecision::Memory) return Fail(TEXT("MEMORY_GUARD_STOPPED_COMPILATION_NO_SAVE"));
        if (Decision == EWaitDecision::Cancelled) return Fail(TEXT("COMPILATION_CANCELLED_NO_SAVE"));
        if (Decision == EWaitDecision::Deadline) return Fail(TEXT("ASSET_COMPILATION_TIMEOUT_NO_SAVE"));
        if (Decision == EWaitDecision::Ready) break;
        // Cooperative boundaries: an individual engine task can still block; progress makes that visible.
        FTaskGraphInterface::Get().ProcessThreadUntilIdle(ENamedThreads::GameThread);
        Compiler.ProcessAsyncTasks(true);
        FTSTicker::GetCoreTicker().Tick(0.05f);
        FPlatformProcess::Sleep(0.05f);
    }
    Report->SetStringField(TEXT("phase"), TEXT("preparing_texture_resources"));
    const FString ResourceReport = FPaths::Combine(Base, TEXT("Jobs"), Job, TEXT("resources.json"));
    Report->SetStringField(TEXT("resource_report"), ResourceReport);
    if (!WriteJson(Output, Report)) return false;
    FString ResourceError;
    if (!PrepareLandscapeResources(World, ResourceReport, CancelPath, ResourceError)) return Fail(ResourceError);
    Report->SetStringField(TEXT("phase"), TEXT("merging_layers"));
    if (!WriteJson(Output, Report)) return false;
    const double Deadline = FPlatformTime::Seconds() + 180.0;
    bool Settled = false;
    while (FPlatformTime::Seconds() < Deadline)
    {
        if (!MemorySafe()) return Fail(TEXT("MEMORY_GUARD_STOPPED_BEFORE_SAVE"));
        Settled = true;
        for (TActorIterator<ALandscape> It(World); It; ++It) { It->ForceUpdateLayersContent(); Settled &= It->IsUpToDate(); }
        if (Settled) break;
        FWorldPartitionHelpers::FakeEngineTick(World); FPlatformProcess::Sleep(0.05f);
    }
    if (!Settled) return Fail(TEXT("MERGE_TIMEOUT_NO_SAVE"));
    if (!Execute(Action == TEXT("verify") ? TEXT("verify") : TEXT("finalize"))) return Fail(Code + TEXT(": ") + Message);
    if (Action == TEXT("verify"))
    {
        bool Passed = false; Native->TryGetBoolField(TEXT("reopen_validation_passed"), Passed);
        if (!Passed) return Fail(TEXT("COLD_REOPEN_DATA_CHECK_FAILED"));
        Report->SetBoolField(TEXT("cold_reopen_verified"), true);
    }
    else
    {
        Report->SetBoolField(TEXT("map_saved"), true);
        if (!WriteJson(FPaths::Combine(Base, TEXT("last_saved_run.json")), Origin)) return Fail(TEXT("RUN_POINTER_WRITE_FAILED_AFTER_SAVE"));
    }
    Report->SetStringField(TEXT("run_id"), Run); Report->SetBoolField(TEXT("ok"), true); RecordMemory(Report);
    UE_LOG(LogAIBridgeLandscapeOffline, Display, TEXT("Offline %s completed; run=%s report=%s"), *Action, *Run, *Output);
    return WriteJson(Output, Report);
}
