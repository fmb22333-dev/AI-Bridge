#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "Interfaces/IHttpRequest.h"
#include "Interfaces/IHttpResponse.h"

class FAIBridgeUESessionClient final
{
public:
    void Start();
    void Stop();

private:
    FString GetConnectionFilePath() const;
    bool LoadConnectionConfig();
    FString CurrentProjectFile() const;
    FHttpRequestPtr CreateRequest(const FString& Verb, const FString& Route);
    void TrackRequest(const FHttpRequestPtr& Request);
    void ForgetRequest(const FHttpRequestPtr& Request);
    FString Serialize(const TSharedRef<FJsonObject>& Root) const;
    FString BuildRegistrationJson() const;
    FString BuildHeartbeatJson() const;

    void TryRegister();
    void HandleRegistrationResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded);
    void SendHeartbeat();
    void HandleHeartbeatResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded);
    void TryPoll();
    void HandlePollResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded);
    void ExecuteCommand(const TSharedPtr<FJsonObject>& Command);
    void SendCommandResult(const TSharedRef<FJsonObject>& Envelope);
    void HandleResultResponse(FHttpRequestPtr Request, FHttpResponsePtr Response, bool bSucceeded);
    void SendFailure(const FString& CommandId, const FString& Code, const FString& Message);
    TSharedRef<FJsonObject> MakeBaseEnvelope(const FString& CommandId, const FString& Status) const;
    bool Tick(float DeltaTime);

    FString ConnectionUrl;
    FString AuthToken;
    FString SessionId;
    FTSTicker::FDelegateHandle TickerHandle;
    TArray<FHttpRequestPtr> ActiveRequests;
    double LastHeartbeatSentAt = 0.0;
    bool bRegistered = false;
    bool bRegisterRequestInFlight = false;
    bool bHeartbeatRequestInFlight = false;
    bool bPollRequestInFlight = false;
    bool bReportedConfigProblem = false;
    bool bReportedRegistrationProblem = false;
    bool bReportedHeartbeatProblem = false;
    bool bShuttingDown = false;
};
