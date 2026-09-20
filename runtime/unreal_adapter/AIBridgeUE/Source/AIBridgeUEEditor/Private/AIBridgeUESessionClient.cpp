#include "AIBridgeUESessionClient.h"

#include "AIBridgeUEInspector.h"
#include "AIBridgeUEFluidFluxExporter.h"
#include "AIBridgeUEAnimCurveFbxExporter.h"
#include "AIBridgeUEObjectInspector.h"
#include "AIBridgeUELandscapeRepair.h"
#include "Async/Async.h"
#include "Dom/JsonObject.h"
#include "HAL/PlatformMisc.h"
#include "HAL/PlatformProcess.h"
#include "HAL/PlatformTime.h"
#include "HttpModule.h"
#include "Misc/EngineVersion.h"
#include "Misc/FileHelper.h"
#include "Misc/Guid.h"
#include "Misc/Paths.h"
#include "Modules/ModuleManager.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeUESession, Log, All);

namespace
{
static constexpr const TCHAR* AdapterVersion = TEXT("0.5.3");
static constexpr float HeartbeatIntervalSeconds = 3.0f;
static constexpr float CommandPollIntervalSeconds = 0.25f;
static constexpr float CommandPollTimeoutSeconds = 2.0f;
}

void FAIBridgeUESessionClient::Start()
{
    bShuttingDown = false;
    if (FPlatformMisc::GetEnvironmentVariable(TEXT("AIBRIDGE_UE_DISABLE_SESSION")) == TEXT("1")) return;
    const FString GuidDigits = FGuid::NewGuid().ToString(EGuidFormats::Digits).Left(12).ToUpper();
    SessionId = FString(TEXT("UE-")) + GuidDigits;
    TickerHandle = FTSTicker::GetCoreTicker().AddTicker(
        FTickerDelegate::CreateRaw(this, &FAIBridgeUESessionClient::Tick),
        CommandPollIntervalSeconds);
    TryRegister();
}

void FAIBridgeUESessionClient::Stop()
{
    bShuttingDown = true;
    if (TickerHandle.IsValid())
    {
        FTSTicker::GetCoreTicker().RemoveTicker(TickerHandle);
        TickerHandle.Reset();
    }
    for (FHttpRequestPtr& Request : ActiveRequests)
    {
        if (Request.IsValid())
        {
            Request->OnProcessRequestComplete().Unbind();
            Request->CancelRequest();
        }
    }
    ActiveRequests.Reset();
}

FString FAIBridgeUESessionClient::GetConnectionFilePath() const
{
    FString UserHome = FPlatformMisc::GetEnvironmentVariable(TEXT("USERPROFILE"));
    if (UserHome.IsEmpty())
    {
        UserHome = FPlatformProcess::UserDir();
    }
    return FPaths::Combine(UserHome, TEXT(".ai_bridge"), TEXT("connection.json"));
}

bool FAIBridgeUESessionClient::LoadConnectionConfig()
{
    FString JsonText;
    if (!FFileHelper::LoadFileToString(JsonText, *GetConnectionFilePath()))
    {
        return false;
    }
    TSharedPtr<FJsonObject> Root;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(JsonText);
    if (!FJsonSerializer::Deserialize(Reader, Root) || !Root.IsValid())
    {
        return false;
    }
    FString NewUrl;
    FString NewToken;
    if (!Root->TryGetStringField(TEXT("url"), NewUrl)
        || !Root->TryGetStringField(TEXT("token"), NewToken)
        || NewUrl.IsEmpty()
        || NewToken.IsEmpty())
    {
        return false;
    }
    while (NewUrl.EndsWith(TEXT("/")))
    {
        NewUrl.LeftChopInline(1, EAllowShrinking::No);
    }
    if (NewUrl.IsEmpty())
    {
        return false;
    }
    ConnectionUrl = MoveTemp(NewUrl);
    AuthToken = MoveTemp(NewToken);
    bReportedConfigProblem = false;
    return true;
}

FString FAIBridgeUESessionClient::CurrentProjectFile() const
{
    FString ProjectFile = FPaths::GetProjectFilePath();
    if (!ProjectFile.IsEmpty())
    {
        ProjectFile = FPaths::ConvertRelativePathToFull(ProjectFile);
        FPaths::NormalizeFilename(ProjectFile);
    }
    return ProjectFile;
}

FHttpRequestPtr FAIBridgeUESessionClient::CreateRequest(const FString& Verb, const FString& Route)
{
    if (ConnectionUrl.IsEmpty() || AuthToken.IsEmpty())
    {
        return nullptr;
    }
    FHttpModule& HttpModule = FModuleManager::LoadModuleChecked<FHttpModule>(TEXT("HTTP"));
    FHttpRequestPtr Request = HttpModule.CreateRequest();
    Request->SetURL(ConnectionUrl + Route);
    Request->SetVerb(Verb);
    Request->SetHeader(TEXT("Content-Type"), TEXT("application/json"));
    Request->SetHeader(TEXT("Authorization"), FString(TEXT("Bearer ")) + AuthToken);
    return Request;
}

void FAIBridgeUESessionClient::TrackRequest(const FHttpRequestPtr& Request)
{
    if (Request.IsValid())
    {
        ActiveRequests.Add(Request);
    }
}

void FAIBridgeUESessionClient::ForgetRequest(const FHttpRequestPtr& Request)
{
    ActiveRequests.RemoveSingleSwap(Request);
}

FString FAIBridgeUESessionClient::Serialize(const TSharedRef<FJsonObject>& Root) const
{
    FString JsonText;
    const TSharedRef<TJsonWriter<>> Writer = TJsonWriterFactory<>::Create(&JsonText);
    return FJsonSerializer::Serialize(Root, Writer) ? JsonText : FString();
}

FString FAIBridgeUESessionClient::BuildRegistrationJson() const
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("session_id"), SessionId);
    Root->SetStringField(TEXT("adapter"), TEXT("unreal"));
    Root->SetStringField(TEXT("adapter_version"), AdapterVersion);
    Root->SetStringField(TEXT("host_version"), FEngineVersion::Current().ToString());
    Root->SetNumberField(TEXT("pid"), static_cast<double>(FPlatformProcess::GetCurrentProcessId()));
    Root->SetStringField(TEXT("project_file"), CurrentProjectFile());

    TSharedRef<FJsonObject> Capability = MakeShared<FJsonObject>();
    Capability->SetStringField(TEXT("name"), TEXT("inspect.current_level"));
    Capability->SetStringField(TEXT("version"), TEXT("1.0"));
    Capability->SetBoolField(TEXT("write"), false);
    Capability->SetStringField(TEXT("risk"), TEXT("L1"));
    Capability->SetBoolField(TEXT("rollback"), false);
    Capability->SetBoolField(TEXT("verification"), true);
    TArray<TSharedPtr<FJsonValue>> TestedVersions;
    TestedVersions.Add(MakeShared<FJsonValueString>(TEXT("5.6")));
    TestedVersions.Add(MakeShared<FJsonValueString>(TEXT("5.8")));
    Capability->SetArrayField(TEXT("tested_host_versions"), TestedVersions);
    TArray<TSharedPtr<FJsonValue>> Capabilities;
    Capabilities.Add(MakeShared<FJsonValueObject>(Capability));

    TSharedRef<FJsonObject> ObjectCapability = MakeShared<FJsonObject>();
    ObjectCapability->SetStringField(TEXT("name"), TEXT("inspect.object"));
    ObjectCapability->SetStringField(TEXT("version"), TEXT("1.0"));
    ObjectCapability->SetBoolField(TEXT("write"), false);
    ObjectCapability->SetStringField(TEXT("risk"), TEXT("L1"));
    ObjectCapability->SetBoolField(TEXT("rollback"), false);
    ObjectCapability->SetBoolField(TEXT("verification"), true);
    ObjectCapability->SetArrayField(TEXT("tested_host_versions"), TestedVersions);
    Capabilities.Add(MakeShared<FJsonValueObject>(ObjectCapability));

    TSharedRef<FJsonObject> ObjectsCapability = MakeShared<FJsonObject>();
    ObjectsCapability->SetStringField(TEXT("name"), TEXT("inspect.objects"));
    ObjectsCapability->SetStringField(TEXT("version"), TEXT("1.0"));
    ObjectsCapability->SetBoolField(TEXT("write"), false);
    ObjectsCapability->SetStringField(TEXT("risk"), TEXT("L1"));
    ObjectsCapability->SetBoolField(TEXT("rollback"), false);
    ObjectsCapability->SetBoolField(TEXT("verification"), true);
    ObjectsCapability->SetArrayField(TEXT("tested_host_versions"), TestedVersions);
    Capabilities.Add(MakeShared<FJsonValueObject>(ObjectsCapability));

    TSharedRef<FJsonObject> ExportCapability = MakeShared<FJsonObject>();
    ExportCapability->SetStringField(TEXT("name"), TEXT("export.fluidflux_state_source"));
    ExportCapability->SetStringField(TEXT("version"), TEXT("1.0"));
    ExportCapability->SetBoolField(TEXT("write"), true);
    ExportCapability->SetStringField(TEXT("risk"), TEXT("L2"));
    ExportCapability->SetBoolField(TEXT("host_mutation"), false);
    ExportCapability->SetBoolField(TEXT("rollback"), false);
    ExportCapability->SetBoolField(TEXT("verification"), true);
    ExportCapability->SetArrayField(TEXT("tested_host_versions"), TestedVersions);
    Capabilities.Add(MakeShared<FJsonValueObject>(ExportCapability));

    TSharedRef<FJsonObject> AnimCurveExportCapability = MakeShared<FJsonObject>();
    AnimCurveExportCapability->SetStringField(TEXT("name"), TEXT("export.animsequence_fbx_with_curves"));
    AnimCurveExportCapability->SetStringField(TEXT("version"), TEXT("1.0"));
    AnimCurveExportCapability->SetBoolField(TEXT("write"), true);
    AnimCurveExportCapability->SetStringField(TEXT("risk"), TEXT("L2"));
    AnimCurveExportCapability->SetBoolField(TEXT("host_mutation"), false);
    AnimCurveExportCapability->SetBoolField(TEXT("rollback"), false);
    AnimCurveExportCapability->SetBoolField(TEXT("verification"), true);
    AnimCurveExportCapability->SetArrayField(TEXT("tested_host_versions"), TestedVersions);
    Capabilities.Add(MakeShared<FJsonValueObject>(AnimCurveExportCapability));

    AIBridgeUE::LandscapeRepair::AddCapabilities(Capabilities);
    Root->SetStringField(TEXT("adapter_version"), TEXT("0.5.3"));
    Root->SetArrayField(TEXT("capabilities"), Capabilities);
    return Serialize(Root);
}

FString FAIBridgeUESessionClient::BuildHeartbeatJson() const
{
    TSharedRef<FJsonObject> Root = MakeShared<FJsonObject>();
    Root->SetStringField(TEXT("project_file"), CurrentProjectFile());
    return Serialize(Root);
}

void FAIBridgeUESessionClient::TryRegister()
{
    if (bShuttingDown || bRegisterRequestInFlight)
    {
        return;
    }
    if (!LoadConnectionConfig())
    {
        if (!bReportedConfigProblem)
        {
            UE_LOG(LogAIBridgeUESession, Warning, TEXT("AI Bridge connection config unavailable or invalid: %s"), *GetConnectionFilePath());
            bReportedConfigProblem = true;
        }
        bRegistered = false;
        return;
    }
    FHttpRequestPtr Request = CreateRequest(TEXT("POST"), TEXT("/adapter/register"));
    const FString Payload = BuildRegistrationJson();
    if (!Request.IsValid() || Payload.IsEmpty())
    {
        return;
    }
    bRegisterRequestInFlight = true;
    Request->SetContentAsString(Payload);
    Request->OnProcessRequestComplete().BindRaw(this, &FAIBridgeUESessionClient::HandleRegistrationResponse);
    TrackRequest(Request);
    if (!Request->ProcessRequest())
    {
        Request->OnProcessRequestComplete().Unbind();
        ForgetRequest(Request);
        bRegisterRequestInFlight = false;
    }
}

void FAIBridgeUESessionClient::HandleRegistrationResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded)
{
    ForgetRequest(Request);
    bRegisterRequestInFlight = false;
    if (bShuttingDown)
    {
        return;
    }
    if (bSucceeded && Response.IsValid() && Response->GetResponseCode() >= 200 && Response->GetResponseCode() < 300)
    {
        const bool bWasRegistered = bRegistered;
        bRegistered = true;
        bReportedRegistrationProblem = false;
        LastHeartbeatSentAt = 0.0;
        if (!bWasRegistered)
        {
            UE_LOG(LogAIBridgeUESession, Display, TEXT("AI Bridge Unreal session registered: %s"), *SessionId);
        }
        return;
    }
    bRegistered = false;
    if (!bReportedRegistrationProblem)
    {
        const int32 Code = Response.IsValid() ? Response->GetResponseCode() : 0;
        UE_LOG(LogAIBridgeUESession, Warning, TEXT("AI Bridge Unreal session registration failed (HTTP %d)."), Code);
        bReportedRegistrationProblem = true;
    }
}

void FAIBridgeUESessionClient::SendHeartbeat()
{
    if (bShuttingDown || !bRegistered || bHeartbeatRequestInFlight)
    {
        return;
    }
    FHttpRequestPtr Request = CreateRequest(TEXT("POST"), FString(TEXT("/adapter/heartbeat/")) + SessionId);
    const FString Payload = BuildHeartbeatJson();
    if (!Request.IsValid() || Payload.IsEmpty())
    {
        return;
    }
    bHeartbeatRequestInFlight = true;
    LastHeartbeatSentAt = FPlatformTime::Seconds();
    Request->SetContentAsString(Payload);
    Request->OnProcessRequestComplete().BindRaw(this, &FAIBridgeUESessionClient::HandleHeartbeatResponse);
    TrackRequest(Request);
    if (!Request->ProcessRequest())
    {
        Request->OnProcessRequestComplete().Unbind();
        ForgetRequest(Request);
        bHeartbeatRequestInFlight = false;
        LastHeartbeatSentAt = 0.0;
    }
}

void FAIBridgeUESessionClient::HandleHeartbeatResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded)
{
    ForgetRequest(Request);
    bHeartbeatRequestInFlight = false;
    if (bShuttingDown)
    {
        return;
    }
    if (bSucceeded && Response.IsValid() && Response->GetResponseCode() >= 200 && Response->GetResponseCode() < 300)
    {
        bReportedHeartbeatProblem = false;
        return;
    }
    const int32 Code = Response.IsValid() ? Response->GetResponseCode() : 0;
    if (Code == 404)
    {
        bRegistered = false;
    }
    if (!bReportedHeartbeatProblem)
    {
        UE_LOG(LogAIBridgeUESession, Warning, TEXT("AI Bridge Unreal heartbeat failed (HTTP %d)."), Code);
        bReportedHeartbeatProblem = true;
    }
}

void FAIBridgeUESessionClient::TryPoll()
{
    if (bShuttingDown || !bRegistered || bPollRequestInFlight)
    {
        return;
    }
    const FString Route = FString::Printf(TEXT("/adapter/poll/%s?timeout=%.1f"), *SessionId, CommandPollTimeoutSeconds);
    FHttpRequestPtr Request = CreateRequest(TEXT("GET"), Route);
    if (!Request.IsValid())
    {
        return;
    }
    bPollRequestInFlight = true;
    Request->OnProcessRequestComplete().BindRaw(this, &FAIBridgeUESessionClient::HandlePollResponse);
    TrackRequest(Request);
    if (!Request->ProcessRequest())
    {
        Request->OnProcessRequestComplete().Unbind();
        ForgetRequest(Request);
        bPollRequestInFlight = false;
    }
}

void FAIBridgeUESessionClient::HandlePollResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded)
{
    ForgetRequest(Request);
    bPollRequestInFlight = false;
    if (bShuttingDown || !bSucceeded || !Response.IsValid())
    {
        return;
    }
    const int32 Code = Response->GetResponseCode();
    if (Code == 404)
    {
        bRegistered = false;
        return;
    }
    if (Code < 200 || Code >= 300)
    {
        return;
    }
    const FString Body = Response->GetContentAsString().TrimStartAndEnd();
    if (Body.IsEmpty() || Body.Equals(TEXT("null"), ESearchCase::IgnoreCase))
    {
        return;
    }
    TSharedPtr<FJsonObject> Command;
    const TSharedRef<TJsonReader<>> Reader = TJsonReaderFactory<>::Create(Body);
    if (!FJsonSerializer::Deserialize(Reader, Command) || !Command.IsValid())
    {
        UE_LOG(LogAIBridgeUESession, Warning, TEXT("AI Bridge Unreal received invalid command JSON."));
        return;
    }
    if (IsInGameThread())
    {
        ExecuteCommand(Command);
    }
    else
    {
        AsyncTask(ENamedThreads::GameThread, [this, Command]()
        {
            if (!bShuttingDown)
            {
                ExecuteCommand(Command);
            }
        });
    }
}

TSharedRef<FJsonObject> FAIBridgeUESessionClient::MakeBaseEnvelope(const FString& CommandId, const FString& Status) const
{
    TSharedRef<FJsonObject> Envelope = MakeShared<FJsonObject>();
    Envelope->SetStringField(TEXT("command_id"), CommandId);
    Envelope->SetStringField(TEXT("status"), Status);
    Envelope->SetBoolField(TEXT("rollback_available"), false);
    TSharedRef<FJsonObject> LastKnownState = MakeShared<FJsonObject>();
    LastKnownState->SetStringField(TEXT("host"), TEXT("alive"));
    LastKnownState->SetStringField(TEXT("session"), SessionId);
    Envelope->SetObjectField(TEXT("last_known_state"), LastKnownState);
    Envelope->SetField(TEXT("evidence_id"), MakeShared<FJsonValueNull>());
    return Envelope;
}

void FAIBridgeUESessionClient::ExecuteCommand(const TSharedPtr<FJsonObject>& Command)
{
    FString CommandId = TEXT("unknown");
    FString Operation;
    Command->TryGetStringField(TEXT("command_id"), CommandId);
    Command->TryGetStringField(TEXT("operation"), Operation);
    TSharedPtr<FJsonObject> Arguments;
    if (Command->HasTypedField<EJson::Object>(TEXT("arguments")))
    {
        Arguments = Command->GetObjectField(TEXT("arguments"));
    }

    FString ErrorCode;
    FString ErrorMessage;
    TSharedPtr<FJsonObject> Readback;
    if (Operation == TEXT("repair.landscape_edit_layers") || Operation == TEXT("inspect.landscape_edit_layers"))
    {
        bool bDryRun = true;
        if (Command->HasTypedField<EJson::Object>(TEXT("execution")))
            Command->GetObjectField(TEXT("execution"))->TryGetBoolField(TEXT("dry_run"), bDryRun);
        if (Operation == TEXT("inspect.landscape_edit_layers")) bDryRun = true;
        Readback = AIBridgeUE::LandscapeRepair::Execute(Arguments, bDryRun, ErrorCode, ErrorMessage);
        bool bOk = false;
        if (Readback.IsValid()) Readback->TryGetBoolField(TEXT("ok"), bOk);
        auto Envelope = MakeBaseEnvelope(CommandId, bOk ? TEXT("success") : TEXT("failed"));
        Envelope->SetObjectField(TEXT("result"), Readback.IsValid() ? Readback.ToSharedRef() : MakeShared<FJsonObject>());
        auto Stages = MakeShared<FJsonObject>();
        FString Phase; if (Readback.IsValid()) Readback->TryGetStringField(TEXT("phase"), Phase);
        Stages->SetStringField(TEXT("LANDSCAPE_REPAIR"), bOk ? Phase : TEXT("FAILED"));
        Envelope->SetObjectField(TEXT("stages"), Stages);
        if (bOk) Envelope->SetField(TEXT("failure"), MakeShared<FJsonValueNull>());
        else
        {
            auto Failure = MakeShared<FJsonObject>();
            Failure->SetStringField(TEXT("origin"), TEXT("adapter"));
            Failure->SetStringField(TEXT("stage"), TEXT("landscape_repair"));
            Failure->SetStringField(TEXT("code"), ErrorCode.IsEmpty() ? TEXT("LANDSCAPE_REPAIR_FAILED") : ErrorCode);
            Failure->SetStringField(TEXT("message"), ErrorMessage);
            Failure->SetBoolField(TEXT("retryable"), false);
            Envelope->SetObjectField(TEXT("failure"), Failure);
        }
        SendCommandResult(Envelope);
        return;
    }
    if (Operation.Equals(TEXT("inspect.current_level"), ESearchCase::CaseSensitive))
    {
        Readback = AIBridgeUE::InspectCurrentEditorLevel(Arguments, ErrorCode, ErrorMessage);
    }
    else if (Operation.Equals(TEXT("inspect.object"), ESearchCase::CaseSensitive))
    {
        Readback = AIBridgeUE::InspectObjectByPath(Arguments, ErrorCode, ErrorMessage);
    }
    else if (Operation.Equals(TEXT("inspect.objects"), ESearchCase::CaseSensitive))
    {
        Readback = AIBridgeUE::InspectObjectsByPaths(Arguments, ErrorCode, ErrorMessage);
    }
    else if (Operation.Equals(TEXT("export.fluidflux_state_source"), ESearchCase::CaseSensitive))
    {
        Readback = AIBridgeUE::ExportFluidFluxStateSource(Arguments, ErrorCode, ErrorMessage);
    }
    else if (Operation.Equals(TEXT("export.animsequence_fbx_with_curves"), ESearchCase::CaseSensitive))
    {
        Readback = AIBridgeUE::ExportAnimSequenceFbxWithCurves(Arguments, ErrorCode, ErrorMessage);
    }
    else
    {
        SendFailure(CommandId, TEXT("UNSUPPORTED_OPERATION"), FString::Printf(TEXT("Unsupported Unreal operation: %s"), *Operation));
        return;
    }

    if (!Readback.IsValid())
    {
        SendFailure(CommandId, ErrorCode, ErrorMessage);
        return;
    }

    TSharedRef<FJsonObject> Envelope = MakeBaseEnvelope(CommandId, TEXT("success"));
    TSharedRef<FJsonObject> Stages = MakeShared<FJsonObject>();
    const bool bExportOperation =
        Operation.Equals(TEXT("export.fluidflux_state_source"), ESearchCase::CaseSensitive)
        || Operation.Equals(TEXT("export.animsequence_fbx_with_curves"), ESearchCase::CaseSensitive);
    Stages->SetStringField(bExportOperation ? TEXT("EXPORT") : TEXT("READBACK"), TEXT("VERIFIED"));
    Envelope->SetObjectField(TEXT("stages"), Stages);
    Envelope->SetObjectField(TEXT("result"), Readback.ToSharedRef());
    Envelope->SetField(TEXT("failure"), MakeShared<FJsonValueNull>());
    SendCommandResult(Envelope);
}

void FAIBridgeUESessionClient::SendFailure(const FString& CommandId, const FString& Code, const FString& Message)
{
    TSharedRef<FJsonObject> Envelope = MakeBaseEnvelope(CommandId, TEXT("failed"));
    TSharedRef<FJsonObject> Stages = MakeShared<FJsonObject>();
    Stages->SetStringField(TEXT("EXECUTE"), TEXT("FAILED"));
    Envelope->SetObjectField(TEXT("stages"), Stages);
    Envelope->SetObjectField(TEXT("result"), MakeShared<FJsonObject>());
    TSharedRef<FJsonObject> Failure = MakeShared<FJsonObject>();
    Failure->SetStringField(TEXT("origin"), TEXT("adapter"));
    Failure->SetStringField(TEXT("stage"), TEXT("dispatch"));
    Failure->SetStringField(TEXT("code"), Code);
    Failure->SetStringField(TEXT("message"), Message);
    Failure->SetBoolField(TEXT("retryable"), false);
    Envelope->SetObjectField(TEXT("failure"), Failure);
    SendCommandResult(Envelope);
}

void FAIBridgeUESessionClient::SendCommandResult(const TSharedRef<FJsonObject>& Envelope)
{
    if (bShuttingDown || !bRegistered)
    {
        return;
    }
    FHttpRequestPtr Request = CreateRequest(TEXT("POST"), FString(TEXT("/adapter/result/")) + SessionId);
    const FString Payload = Serialize(Envelope);
    if (!Request.IsValid() || Payload.IsEmpty())
    {
        return;
    }
    Request->SetContentAsString(Payload);
    Request->OnProcessRequestComplete().BindRaw(this, &FAIBridgeUESessionClient::HandleResultResponse);
    TrackRequest(Request);
    if (!Request->ProcessRequest())
    {
        Request->OnProcessRequestComplete().Unbind();
        ForgetRequest(Request);
    }
}

void FAIBridgeUESessionClient::HandleResultResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded)
{
    ForgetRequest(Request);
    if (!bShuttingDown && bSucceeded && Response.IsValid() && Response->GetResponseCode() == 404)
    {
        bRegistered = false;
    }
}

bool FAIBridgeUESessionClient::Tick(float DeltaTime)
{
    (void)DeltaTime;
    if (bShuttingDown)
    {
        return false;
    }
    if (!bRegistered)
    {
        TryRegister();
        return true;
    }
    const double Now = FPlatformTime::Seconds();
    if (!bHeartbeatRequestInFlight && (LastHeartbeatSentAt <= 0.0 || Now - LastHeartbeatSentAt >= HeartbeatIntervalSeconds))
    {
        SendHeartbeat();
    }
    TryPoll();
    return true;
}
