#include "AIBridgeUELandscapeComparison.h"
#include "AIBridgeUELandscapePlaneDiff.h"
#include "Engine/Texture2D.h"
#include "LandscapeComponent.h"
#include "LandscapeLayerInfoObject.h"
#include "HAL/PlatformTime.h"

DEFINE_LOG_CATEGORY_STATIC(LogAIBridgeLandscapeComparison, Log, All);
namespace AIBridgeUE::LandscapeRepair
{
namespace
{
struct FComponentSamples
{
 TArray<uint16> Height;
 TMap<FString, TArray<uint16>> Paint;
};
struct FBoundaryKey
{
 int32 X = 0;
 int32 Y = 0;
 int32 Plane = INDEX_NONE;
 bool operator==(const FBoundaryKey& Other) const { return X == Other.X && Y == Other.Y && Plane == Other.Plane; }
 friend uint32 GetTypeHash(const FBoundaryKey& Key)
 {
  return HashCombineFast(HashCombineFast(::GetTypeHash(Key.X), ::GetTypeHash(Key.Y)), ::GetTypeHash(Key.Plane));
 }
};
struct FBoundaryOwnerRecord
{
 FSharedVertexOwnerSample Value;
 int32 PackedX = 0;
 int32 PackedY = 0;
};
struct FBoundaryGroup
{
 TArray<FBoundaryOwnerRecord> Owners;
};
bool ReadPlane(UTexture2D* T, int32 Channel, int32 X, int32 Y, int32 Side, TArray<uint16>& Values, FString& Error)
{
 if (!T || !T->Source.IsValid() || T->Source.GetFormat() != TSF_BGRA8) { Error = TEXT("PLANE_SOURCE_INVALID"); return false; }
 const int32 W = T->Source.GetSizeX(), H = T->Source.GetSizeY();
 if (Side <= 0 || Side > 8192 || X < 0 || Y < 0 || int64(X) + Side > W || int64(Y) + Side > H || int64(W) * H > 67108864) { Error = TEXT("PLANE_EXTENT_INVALID"); return false; }
 TArray64<uint8> Bytes;
 if (!T->Source.GetMipData(Bytes, 0) || Bytes.Num() != int64(W) * H * sizeof(FColor)) { Error = TEXT("PLANE_MIP_READ_FAILED"); return false; }
 const FColor* Pixels = reinterpret_cast<const FColor*>(Bytes.GetData());
 Values.SetNumUninitialized(Side * Side);
 for (int32 Row = 0; Row < Side; ++Row) for (int32 Col = 0; Col < Side; ++Col)
 {
  const FColor& P = Pixels[int64(Y + Row) * W + X + Col];
  Values[Row * Side + Col] = Channel < 0 ? uint16((uint16(P.R) << 8) | P.G) : Channel == 0 ? P.R : Channel == 1 ? P.G : Channel == 2 ? P.B : P.A;
 }
 return true;
}
bool Capture(ULandscapeComponent* C, const FLandscapeLayerComponentData& D, FComponentSamples& Samples, FString& Error)
{
 const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections;
 UTexture2D* H = D.HeightmapData.Texture;
 if (!H || !ReadPlane(H, -1, FMath::RoundToInt(C->HeightmapScaleBias.Z * H->Source.GetSizeX()), FMath::RoundToInt(C->HeightmapScaleBias.W * H->Source.GetSizeY()), Side, Samples.Height, Error)) return false;
 for (const FWeightmapLayerAllocationInfo& A : D.WeightmapData.LayerAllocations)
 {
  if (!A.LayerInfo || A.WeightmapTextureChannel > 3 || !D.WeightmapData.Textures.IsValidIndex(A.WeightmapTextureIndex)) { Error = TEXT("DIAGNOSTIC_PAINT_ALLOCATION_INVALID"); return false; }
  const FString Key = A.LayerInfo->GetPathName();
  if (Samples.Paint.Contains(Key)) { Error = TEXT("DIAGNOSTIC_DUPLICATE_PAINT"); return false; }
  UTexture2D* T = D.WeightmapData.Textures[A.WeightmapTextureIndex];
  if (!T || !ReadPlane(T, A.WeightmapTextureChannel, FMath::FloorToInt(C->WeightmapScaleBias.Z * T->Source.GetSizeX()), FMath::FloorToInt(C->WeightmapScaleBias.W * T->Source.GetSizeY()), Side, Samples.Paint.Add(Key), Error)) return false;
 }
 return true;
}
TSharedPtr<FJsonObject> PlaneJson(const FPlaneDifference& D, const TArray<uint16>& A, const TArray<uint16>& B, int32 Side)
{
 auto J = MakeShared<FJsonObject>();
 J->SetBoolField(TEXT("valid"), D.bValid); J->SetNumberField(TEXT("changed_pixels"), D.Changed);
 J->SetNumberField(TEXT("maximum_delta"), D.MaximumDelta); J->SetNumberField(TEXT("sum_absolute_delta"), double(D.SumAbsoluteDelta));
 J->SetBoolField(TEXT("before_present"), !A.IsEmpty()); J->SetBoolField(TEXT("after_present"), !B.IsEmpty());
 int32 NonzeroA = 0, NonzeroB = 0, Interior = 0; int64 SumA = 0, SumB = 0;
 for (uint16 V : A) { NonzeroA += V != 0; SumA += V; }
 for (uint16 V : B) { NonzeroB += V != 0; SumB += V; }
 if (D.bValid) for (int32 I = 0; I < Side * Side; ++I)
 {
  const int32 X = I % Side, Y = I / Side;
  if (X > 0 && X < Side - 1 && Y > 0 && Y < Side - 1 && (A.IsEmpty() ? 0 : A[I]) != (B.IsEmpty() ? 0 : B[I])) ++Interior;
 }
 J->SetNumberField(TEXT("nonzero_before"), NonzeroA); J->SetNumberField(TEXT("nonzero_after"), NonzeroB);
 J->SetNumberField(TEXT("sum_before"), double(SumA)); J->SetNumberField(TEXT("sum_after"), double(SumB)); J->SetNumberField(TEXT("interior_changed_pixels"), Interior);
 TArray<TSharedPtr<FJsonValue>> Examples;
 for (int32 I : D.Samples)
 {
  auto E = MakeShared<FJsonObject>(); E->SetNumberField(TEXT("x"), I % Side); E->SetNumberField(TEXT("y"), I / Side);
  E->SetNumberField(TEXT("before"), A.IsEmpty() ? 0 : A[I]); E->SetNumberField(TEXT("after"), B.IsEmpty() ? 0 : B[I]);
  Examples.Add(MakeShared<FJsonValueObject>(E));
 }
 J->SetArrayField(TEXT("samples"), Examples); return J;
}
}

bool IsSafeMergedComparisonForSave(const TSharedPtr<FJsonObject>& Comparison, FString& OutRejectionReason)
{
 OutRejectionReason.Reset();
 const auto Reject = [&OutRejectionReason](const TCHAR* Reason)
 {
  OutRejectionReason = Reason;
  return false;
 };
 const auto HasString = [](const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, const TCHAR* Expected)
 {
  return Object.IsValid() && Object->HasTypedField<EJson::String>(Name) && Object->GetStringField(Name) == Expected;
 };
 const auto HasTrue = [](const TSharedPtr<FJsonObject>& Object, const TCHAR* Name)
 {
  return Object.IsValid() && Object->HasTypedField<EJson::Boolean>(Name) && Object->GetBoolField(Name);
 };
 const auto ReadCount = [](const TSharedPtr<FJsonObject>& Object, const TCHAR* Name, double& Value)
 {
  return Object.IsValid() && Object->TryGetNumberField(Name, Value) && FMath::IsFinite(Value)
   && Value >= 0.0 && Value == double(int64(Value));
 };

 if (!Comparison.IsValid()) return Reject(TEXT("COMPARISON_INVALID"));
 if (!HasString(Comparison, TEXT("schema"), TEXT("landscape_pixel_comparison/2"))) return Reject(TEXT("COMPARISON_SCHEMA_INVALID"));
 if (!HasTrue(Comparison, TEXT("diagnostic_only"))) return Reject(TEXT("COMPARISON_NOT_DIAGNOSTIC"));
 if (!HasTrue(Comparison, TEXT("all_active_hashes_match_captured_source"))) return Reject(TEXT("ACTIVE_SOURCE_MISMATCH"));
 if (!Comparison->HasTypedField<EJson::Object>(TEXT("summary"))) return Reject(TEXT("SUMMARY_INVALID"));
 const TSharedPtr<FJsonObject> Summary = Comparison->GetObjectField(TEXT("summary"));
 double ExpectedComponents = 0, CheckedComponents = 0, Errors = 0, HeightChangedPixels = 0, PaintChangedPixels = 0;
 if (!ReadCount(Summary, TEXT("expected_components"), ExpectedComponents)
  || !ReadCount(Summary, TEXT("checked_components"), CheckedComponents)
  || !ReadCount(Summary, TEXT("errors"), Errors)
  || !ReadCount(Summary, TEXT("height_changed_pixels"), HeightChangedPixels)
  || !ReadCount(Summary, TEXT("paint_changed_pixels"), PaintChangedPixels)) return Reject(TEXT("SUMMARY_INVALID"));
 if (ExpectedComponents <= 0 || CheckedComponents != ExpectedComponents || Errors != 0
  || !Comparison->HasTypedField<EJson::Array>(TEXT("components"))
  || double(Comparison->GetArrayField(TEXT("components")).Num()) != ExpectedComponents) return Reject(TEXT("COMPONENT_COVERAGE_MISMATCH"));
 const double ChangedOwnerSamples = HeightChangedPixels + PaintChangedPixels;
 if (ChangedOwnerSamples <= 0) return Reject(TEXT("NO_MERGED_PIXEL_DIFFERENCE"));

 if (!Comparison->HasTypedField<EJson::Object>(TEXT("boundary_audit"))) return Reject(TEXT("BOUNDARY_AUDIT_INVALID"));
 const TSharedPtr<FJsonObject> Boundary = Comparison->GetObjectField(TEXT("boundary_audit"));
 if (!HasString(Boundary, TEXT("schema"), TEXT("landscape_shared_vertex_audit/1"))) return Reject(TEXT("BOUNDARY_AUDIT_SCHEMA_INVALID"));
 if (!HasTrue(Boundary, TEXT("diagnostic_only"))) return Reject(TEXT("BOUNDARY_AUDIT_NOT_DIAGNOSTIC"));
 double TrackedVertices = 0, OutsideEdges = 0, CaptureErrors = 0, ChangedGroups = 0, ReconciledGroups = 0, ExactGroups = 0;
 double GeneratedGroups = 0, DisagreeingGroups = 0, InvalidGroups = 0, SingleComponentGroups = 0, ExpectedOwners = 0, AuditedOwners = 0;
 if (!ReadCount(Boundary, TEXT("tracked_changed_logical_vertices"), TrackedVertices)
  || !ReadCount(Boundary, TEXT("changed_samples_outside_component_edges"), OutsideEdges)
  || !ReadCount(Boundary, TEXT("capture_errors"), CaptureErrors)
  || !ReadCount(Boundary, TEXT("changed_logical_vertex_groups"), ChangedGroups)
  || !ReadCount(Boundary, TEXT("reconciled_from_source_groups"), ReconciledGroups)
  || !ReadCount(Boundary, TEXT("exact_groups"), ExactGroups)
  || !ReadCount(Boundary, TEXT("merged_value_not_in_source_groups"), GeneratedGroups)
  || !ReadCount(Boundary, TEXT("merged_copies_disagree_groups"), DisagreeingGroups)
  || !ReadCount(Boundary, TEXT("invalid_groups"), InvalidGroups)
  || !ReadCount(Boundary, TEXT("single_component_groups"), SingleComponentGroups)
  || !ReadCount(Boundary, TEXT("expected_changed_owner_samples"), ExpectedOwners)
  || !ReadCount(Boundary, TEXT("audited_changed_owner_samples"), AuditedOwners)) return Reject(TEXT("BOUNDARY_AUDIT_INVALID"));
 if (OutsideEdges != 0) return Reject(TEXT("CHANGED_SAMPLE_OUTSIDE_COMPONENT_EDGE"));
 if (CaptureErrors != 0) return Reject(TEXT("BOUNDARY_CAPTURE_ERROR"));
 if (GeneratedGroups != 0) return Reject(TEXT("MERGED_VALUE_NOT_IN_SOURCE"));
 if (DisagreeingGroups != 0) return Reject(TEXT("MERGED_COPIES_DISAGREE"));
 if (InvalidGroups != 0) return Reject(TEXT("INVALID_BOUNDARY_GROUP"));
 if (SingleComponentGroups != 0) return Reject(TEXT("SINGLE_COMPONENT_CHANGE"));
 if (TrackedVertices <= 0 || TrackedVertices != ChangedGroups) return Reject(TEXT("LOGICAL_VERTEX_COVERAGE_MISMATCH"));
 if (!Boundary->HasTypedField<EJson::Array>(TEXT("groups"))
  || double(Boundary->GetArrayField(TEXT("groups")).Num()) != ChangedGroups) return Reject(TEXT("GROUP_LIST_COVERAGE_MISMATCH"));
 if (ExactGroups != 0 || ReconciledGroups != ChangedGroups) return Reject(TEXT("GROUP_CLASSIFICATION_COVERAGE_MISMATCH"));
 if (ExpectedOwners != ChangedOwnerSamples || AuditedOwners != ExpectedOwners) return Reject(TEXT("OWNER_SAMPLE_COVERAGE_MISMATCH"));
 if (!HasTrue(Boundary, TEXT("all_changed_samples_audited"))) return Reject(TEXT("AUDIT_COMPLETENESS_FLAG_FALSE"));
 if (!HasTrue(Boundary, TEXT("all_changes_shared_vertex_equivalent"))) return Reject(TEXT("SHARED_VERTEX_EQUIVALENCE_FALSE"));
 return true;
}

TSharedPtr<FJsonObject> AnalyzeLandscapeComparison(const TArray<ULandscapeComponent*>& Components, const FGuid& LayerGuid)
{
 auto Report = MakeShared<FJsonObject>();
 Report->SetStringField(TEXT("schema"), TEXT("landscape_pixel_comparison/2")); Report->SetBoolField(TEXT("diagnostic_only"), true);
 auto Summary = MakeShared<FJsonObject>(); TArray<TSharedPtr<FJsonValue>> Rows;
 int32 Checked = 0, Errors = 0, HeightChanged = 0, PaintChanged = 0, Equivalent = 0, AddedZero = 0, RemovedZero = 0, MaxHeight = 0, MaxPaint = 0;
 int64 HeightPixels = 0, PaintPixels = 0;
 const double Start = FPlatformTime::Seconds();
 const TArray<uint16> Empty;
 TSet<FString> GlobalPaintLayerSet;
 for (ULandscapeComponent* C : Components)
 {
  if (!C) continue;
  if (const FLandscapeLayerComponentData* Active = C->GetLayerData(LayerGuid))
   for (const FWeightmapLayerAllocationInfo& Allocation : Active->WeightmapData.LayerAllocations)
    if (Allocation.LayerInfo) GlobalPaintLayerSet.Add(Allocation.LayerInfo->GetPathName());
  for (const FWeightmapLayerAllocationInfo& Allocation : C->GetWeightmapLayerAllocations())
   if (Allocation.LayerInfo) GlobalPaintLayerSet.Add(Allocation.LayerInfo->GetPathName());
 }
 TArray<FString> GlobalPaintLayers = GlobalPaintLayerSet.Array(); GlobalPaintLayers.Sort();
 TMap<FString, int32> PaintLayerIndices;
 for (int32 LayerIndex = 0; LayerIndex < GlobalPaintLayers.Num(); ++LayerIndex) PaintLayerIndices.Add(GlobalPaintLayers[LayerIndex], LayerIndex);
 TSet<FBoundaryKey> ChangedBoundaryKeys;
 int64 ChangedSamplesOutsideComponentEdges = 0;
 for (ULandscapeComponent* C : Components)
 {
  if (FPlatformTime::Seconds() - Start > 600.0) { ++Errors; Report->SetStringField(TEXT("error"), TEXT("DIAGNOSTIC_DEADLINE")); break; }
  auto Row = MakeShared<FJsonObject>(); Row->SetStringField(TEXT("component"), C->GetPathName());
  FComponentSamples A, B; FString Error;
  const FLandscapeLayerComponentData* Active = C->GetLayerData(LayerGuid);
  FLandscapeLayerComponentData Final; Final.HeightmapData.Texture = C->GetHeightmap(); Final.WeightmapData.Textures = C->GetWeightmapTextures(); Final.WeightmapData.LayerAllocations = C->GetWeightmapLayerAllocations();
  if (!Active || !Capture(C, *Active, A, Error) || !Capture(C, Final, B, Error))
  {
   ++Errors; Row->SetStringField(TEXT("error"), Error.IsEmpty() ? TEXT("ACTIVE_DATA_MISSING") : Error); Rows.Add(MakeShared<FJsonValueObject>(Row)); continue;
  }
  const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections, Count = Side * Side;
  const FPlaneDifference H = ComparePlaneValues(A.Height, B.Height, Count, false);
  Row->SetNumberField(TEXT("height_changed_pixels"), H.Changed); Row->SetNumberField(TEXT("height_maximum_delta"), H.MaximumDelta);
  bool PixelsEqual = H.bValid && H.Changed == 0;
  HeightChanged += H.Changed > 0; HeightPixels += H.Changed; MaxHeight = FMath::Max(MaxHeight, H.MaximumDelta);
  if (H.bValid && H.Changed > 0)
  {
   const FIntPoint SectionBase = C->GetSectionBase();
   for (int32 I = 0; I < Count; ++I) if (A.Height[I] != B.Height[I])
   {
    const int32 X = I % Side, Y = I / Side;
    if (!IsPackedComponentEdge(X, Y, Side)) { ++ChangedSamplesOutsideComponentEdges; continue; }
    const FLandscapeLogicalVertex Vertex = PackedTexelToLandscapeVertex(X, Y, C->SubsectionSizeQuads, C->NumSubsections, SectionBase.X, SectionBase.Y);
    if (Vertex.bValid) ChangedBoundaryKeys.Add({Vertex.X, Vertex.Y, INDEX_NONE}); else ++ChangedSamplesOutsideComponentEdges;
   }
  }
  TSet<FString> Keys; for (const auto& P : A.Paint) Keys.Add(P.Key); for (const auto& P : B.Paint) Keys.Add(P.Key);
  TArray<FString> OrderedKeys = Keys.Array(); OrderedKeys.Sort();
  TArray<TSharedPtr<FJsonValue>> PaintDetails; int32 ChangedPlanes = 0, Added = 0, Removed = 0;
  for (const FString& Key : OrderedKeys)
  {
   const auto* PA = A.Paint.Find(Key); const auto* PB = B.Paint.Find(Key);
   const auto& VA = PA ? *PA : Empty; const auto& VB = PB ? *PB : Empty;
   const FPlaneDifference D = ComparePlaneValues(VA, VB, Count, true);
   PixelsEqual &= D.bValid && D.Changed == 0; ChangedPlanes += D.Changed > 0; PaintPixels += D.Changed; MaxPaint = FMath::Max(MaxPaint, D.MaximumDelta);
   if (!PA && D.Changed == 0) { ++AddedZero; ++Added; }
   if (!PB && D.Changed == 0) { ++RemovedZero; ++Removed; }
   if (D.bValid && D.Changed > 0)
   {
    const FIntPoint SectionBase = C->GetSectionBase();
    const int32 Plane = PaintLayerIndices.FindChecked(Key);
    for (int32 I = 0; I < Count; ++I)
    {
     const uint16 SourceValue = VA.IsEmpty() ? 0 : VA[I];
     const uint16 MergedValue = VB.IsEmpty() ? 0 : VB[I];
     if (SourceValue == MergedValue) continue;
     const int32 X = I % Side, Y = I / Side;
     if (!IsPackedComponentEdge(X, Y, Side)) { ++ChangedSamplesOutsideComponentEdges; continue; }
     const FLandscapeLogicalVertex Vertex = PackedTexelToLandscapeVertex(X, Y, C->SubsectionSizeQuads, C->NumSubsections, SectionBase.X, SectionBase.Y);
     if (Vertex.bValid) ChangedBoundaryKeys.Add({Vertex.X, Vertex.Y, Plane}); else ++ChangedSamplesOutsideComponentEdges;
    }
   }
   if (ShouldRecordComparisonDetails(Checked, D.Changed > 0) && (D.Changed || !PA || !PB)) { auto P = PlaneJson(D, VA, VB, Side); P->SetStringField(TEXT("layer"), Key); PaintDetails.Add(MakeShared<FJsonValueObject>(P)); }
  }
  PaintChanged += ChangedPlanes > 0; Equivalent += PixelsEqual; ++Checked;
  Row->SetNumberField(TEXT("paint_changed_planes"), ChangedPlanes); Row->SetNumberField(TEXT("added_zero_planes"), Added); Row->SetNumberField(TEXT("removed_zero_planes"), Removed);
  Row->SetBoolField(TEXT("pixel_equivalent"), PixelsEqual);
  if (ShouldRecordComparisonDetails(Checked - 1, !PixelsEqual)) { Row->SetObjectField(TEXT("height"), PlaneJson(H, A.Height, B.Height, Side)); Row->SetArrayField(TEXT("paint"), PaintDetails); }
  Rows.Add(MakeShared<FJsonValueObject>(Row));
  if (Checked % 64 == 0) UE_LOG(LogAIBridgeLandscapeComparison, Display, TEXT("Pixel comparison %d/%d, differing height components %d, paint components %d"), Checked, Components.Num(), HeightChanged, PaintChanged);
 }
 auto BoundaryAudit = MakeShared<FJsonObject>();
 BoundaryAudit->SetStringField(TEXT("schema"), TEXT("landscape_shared_vertex_audit/1"));
 BoundaryAudit->SetBoolField(TEXT("diagnostic_only"), true);
 BoundaryAudit->SetNumberField(TEXT("tracked_changed_logical_vertices"), ChangedBoundaryKeys.Num());
 BoundaryAudit->SetNumberField(TEXT("changed_samples_outside_component_edges"), double(ChangedSamplesOutsideComponentEdges));
 TMap<FBoundaryKey, FBoundaryGroup> BoundaryGroups;
 BoundaryGroups.Reserve(ChangedBoundaryKeys.Num());
 int32 BoundaryCaptureErrors = 0;
 if (Errors == 0 && ChangedSamplesOutsideComponentEdges == 0)
 {
  for (int32 ComponentIndex = 0; ComponentIndex < Components.Num(); ++ComponentIndex)
  {
   if (FPlatformTime::Seconds() - Start > 600.0) { ++BoundaryCaptureErrors; BoundaryAudit->SetStringField(TEXT("error"), TEXT("BOUNDARY_AUDIT_DEADLINE")); break; }
   ULandscapeComponent* C = Components[ComponentIndex];
   const FLandscapeLayerComponentData* Active = C ? C->GetLayerData(LayerGuid) : nullptr;
   FLandscapeLayerComponentData Final;
   if (C) { Final.HeightmapData.Texture = C->GetHeightmap(); Final.WeightmapData.Textures = C->GetWeightmapTextures(); Final.WeightmapData.LayerAllocations = C->GetWeightmapLayerAllocations(); }
   FComponentSamples A, B; FString BoundaryError;
   if (!C || !Active || !Capture(C, *Active, A, BoundaryError) || !Capture(C, Final, B, BoundaryError))
   {
    ++BoundaryCaptureErrors; continue;
   }
   const int32 Side = (C->SubsectionSizeQuads + 1) * C->NumSubsections;
   const FIntPoint SectionBase = C->GetSectionBase();
   const auto AddOwner = [&](int32 X, int32 Y)
   {
    const FLandscapeLogicalVertex Vertex = PackedTexelToLandscapeVertex(X, Y, C->SubsectionSizeQuads, C->NumSubsections, SectionBase.X, SectionBase.Y);
    if (!Vertex.bValid) return;
    const int32 I = Y * Side + X;
    const FBoundaryKey HeightKey{Vertex.X, Vertex.Y, INDEX_NONE};
    if (ChangedBoundaryKeys.Contains(HeightKey))
    {
     FBoundaryOwnerRecord Owner; Owner.Value = {ComponentIndex, A.Height[I], B.Height[I]}; Owner.PackedX = X; Owner.PackedY = Y;
     BoundaryGroups.FindOrAdd(HeightKey).Owners.Add(Owner);
    }
    for (int32 Plane = 0; Plane < GlobalPaintLayers.Num(); ++Plane)
    {
     const FBoundaryKey PaintKey{Vertex.X, Vertex.Y, Plane};
     if (!ChangedBoundaryKeys.Contains(PaintKey)) continue;
     const TArray<uint16>* SourcePlane = A.Paint.Find(GlobalPaintLayers[Plane]);
     const TArray<uint16>* MergedPlane = B.Paint.Find(GlobalPaintLayers[Plane]);
     FBoundaryOwnerRecord Owner;
     Owner.Value = {ComponentIndex, SourcePlane ? (*SourcePlane)[I] : uint16(0), MergedPlane ? (*MergedPlane)[I] : uint16(0)};
     Owner.PackedX = X; Owner.PackedY = Y; BoundaryGroups.FindOrAdd(PaintKey).Owners.Add(Owner);
    }
   };
   for (int32 X = 0; X < Side; ++X) { AddOwner(X, 0); if (Side > 1) AddOwner(X, Side - 1); }
   for (int32 Y = 1; Y + 1 < Side; ++Y) { AddOwner(0, Y); if (Side > 1) AddOwner(Side - 1, Y); }
   if ((ComponentIndex + 1) % 64 == 0) UE_LOG(LogAIBridgeLandscapeComparison, Display, TEXT("Shared-vertex audit %d/%d"), ComponentIndex + 1, Components.Num());
  }
 }
 TArray<FBoundaryKey> OrderedBoundaryKeys = ChangedBoundaryKeys.Array();
 OrderedBoundaryKeys.Sort([](const FBoundaryKey& L, const FBoundaryKey& R)
 {
  if (L.Plane != R.Plane) return L.Plane < R.Plane;
  if (L.Y != R.Y) return L.Y < R.Y;
  return L.X < R.X;
 });
 TArray<TSharedPtr<FJsonValue>> BoundaryRows;
 int32 ReconciledGroups = 0, ExactGroups = 0, GeneratedGroups = 0, DisagreeingGroups = 0, InvalidGroups = 0, SingleComponentGroups = 0;
 int64 AuditedChangedOwners = 0;
 for (const FBoundaryKey& Key : OrderedBoundaryKeys)
 {
  const FBoundaryGroup* Group = BoundaryGroups.Find(Key);
  TArray<FSharedVertexOwnerSample> OwnerValues;
  if (Group) { OwnerValues.Reserve(Group->Owners.Num()); for (const FBoundaryOwnerRecord& Owner : Group->Owners) OwnerValues.Add(Owner.Value); }
  const FSharedVertexOwnerAudit Audit = AuditSharedVertexOwners(OwnerValues);
  AuditedChangedOwners += Audit.ChangedOwners;
  SingleComponentGroups += Audit.UniqueComponents == 1;
  FString ResultName;
  switch (Audit.ValueAudit.Result)
  {
   case ESharedVertexAuditResult::Exact: ResultName = TEXT("exact"); ++ExactGroups; break;
   case ESharedVertexAuditResult::ReconciledFromSource: ResultName = TEXT("reconciled_from_source"); ++ReconciledGroups; break;
   case ESharedVertexAuditResult::MergedValueNotInSource: ResultName = TEXT("merged_value_not_in_source"); ++GeneratedGroups; break;
   case ESharedVertexAuditResult::MergedCopiesDisagree: ResultName = TEXT("merged_copies_disagree"); ++DisagreeingGroups; break;
   default: ResultName = TEXT("invalid"); ++InvalidGroups; break;
  }
  auto GroupJson = MakeShared<FJsonObject>();
  GroupJson->SetNumberField(TEXT("landscape_x"), Key.X); GroupJson->SetNumberField(TEXT("landscape_y"), Key.Y);
  GroupJson->SetStringField(TEXT("plane"), Key.Plane == INDEX_NONE ? TEXT("height") : TEXT("paint"));
  if (Key.Plane != INDEX_NONE && GlobalPaintLayers.IsValidIndex(Key.Plane)) GroupJson->SetStringField(TEXT("layer"), GlobalPaintLayers[Key.Plane]);
  GroupJson->SetStringField(TEXT("result"), ResultName); GroupJson->SetBoolField(TEXT("equivalent"), Audit.ValueAudit.bEquivalent);
  GroupJson->SetNumberField(TEXT("merged_value"), Audit.ValueAudit.MergedValue); GroupJson->SetNumberField(TEXT("source_unique_values"), Audit.ValueAudit.SourceUniqueValues);
  GroupJson->SetNumberField(TEXT("physical_owner_samples"), OwnerValues.Num()); GroupJson->SetNumberField(TEXT("component_owners"), Audit.UniqueComponents);
  GroupJson->SetNumberField(TEXT("changed_owner_samples"), Audit.ChangedOwners);
  TArray<TSharedPtr<FJsonValue>> OwnerRows;
  if (Group) for (const FBoundaryOwnerRecord& Owner : Group->Owners)
  {
   auto OwnerJson = MakeShared<FJsonObject>();
   OwnerJson->SetNumberField(TEXT("component_index"), Owner.Value.ComponentIndex);
   if (Components.IsValidIndex(Owner.Value.ComponentIndex) && Components[Owner.Value.ComponentIndex]) OwnerJson->SetStringField(TEXT("component"), Components[Owner.Value.ComponentIndex]->GetPathName());
   OwnerJson->SetNumberField(TEXT("packed_x"), Owner.PackedX); OwnerJson->SetNumberField(TEXT("packed_y"), Owner.PackedY);
   OwnerJson->SetNumberField(TEXT("source"), Owner.Value.SourceValue); OwnerJson->SetNumberField(TEXT("merged"), Owner.Value.MergedValue);
   OwnerJson->SetBoolField(TEXT("changed"), Owner.Value.SourceValue != Owner.Value.MergedValue);
   OwnerJson->SetBoolField(TEXT("source_matches_selected_value"), Owner.Value.SourceValue == Audit.ValueAudit.MergedValue);
   OwnerRows.Add(MakeShared<FJsonValueObject>(OwnerJson));
  }
  GroupJson->SetArrayField(TEXT("owners"), OwnerRows); BoundaryRows.Add(MakeShared<FJsonValueObject>(GroupJson));
 }
 const int64 TotalChangedOwners = HeightPixels + PaintPixels;
 const int32 UnsafeGroups = GeneratedGroups + DisagreeingGroups + InvalidGroups;
 BoundaryAudit->SetNumberField(TEXT("capture_errors"), BoundaryCaptureErrors);
 BoundaryAudit->SetNumberField(TEXT("changed_logical_vertex_groups"), OrderedBoundaryKeys.Num());
 BoundaryAudit->SetNumberField(TEXT("reconciled_from_source_groups"), ReconciledGroups);
 BoundaryAudit->SetNumberField(TEXT("exact_groups"), ExactGroups);
 BoundaryAudit->SetNumberField(TEXT("merged_value_not_in_source_groups"), GeneratedGroups);
 BoundaryAudit->SetNumberField(TEXT("merged_copies_disagree_groups"), DisagreeingGroups);
 BoundaryAudit->SetNumberField(TEXT("invalid_groups"), InvalidGroups);
 BoundaryAudit->SetNumberField(TEXT("single_component_groups"), SingleComponentGroups);
 BoundaryAudit->SetNumberField(TEXT("expected_changed_owner_samples"), double(TotalChangedOwners));
 BoundaryAudit->SetNumberField(TEXT("audited_changed_owner_samples"), double(AuditedChangedOwners));
 BoundaryAudit->SetBoolField(TEXT("all_changed_samples_audited"), BoundaryCaptureErrors == 0 && ChangedSamplesOutsideComponentEdges == 0 && AuditedChangedOwners == TotalChangedOwners);
 BoundaryAudit->SetBoolField(TEXT("all_changes_shared_vertex_equivalent"), BoundaryCaptureErrors == 0 && ChangedSamplesOutsideComponentEdges == 0 && AuditedChangedOwners == TotalChangedOwners && UnsafeGroups == 0);
 BoundaryAudit->SetArrayField(TEXT("groups"), BoundaryRows);
 Summary->SetNumberField(TEXT("expected_components"), Components.Num()); Summary->SetNumberField(TEXT("checked_components"), Checked); Summary->SetNumberField(TEXT("errors"), Errors);
 Summary->SetNumberField(TEXT("pixel_equivalent_components"), Equivalent); Summary->SetNumberField(TEXT("height_changed_components"), HeightChanged); Summary->SetNumberField(TEXT("paint_changed_components"), PaintChanged);
 Summary->SetNumberField(TEXT("height_changed_pixels"), double(HeightPixels)); Summary->SetNumberField(TEXT("paint_changed_pixels"), double(PaintPixels));
 Summary->SetNumberField(TEXT("maximum_height_delta"), MaxHeight); Summary->SetNumberField(TEXT("maximum_paint_delta"), MaxPaint);
 Summary->SetNumberField(TEXT("added_zero_paint_planes"), AddedZero); Summary->SetNumberField(TEXT("removed_zero_paint_planes"), RemovedZero);
 Summary->SetBoolField(TEXT("all_pixels_equivalent"), Errors == 0 && Checked == Components.Num() && Equivalent == Checked);
 Report->SetObjectField(TEXT("summary"), Summary); Report->SetObjectField(TEXT("boundary_audit"), BoundaryAudit); Report->SetArrayField(TEXT("components"), Rows); return Report;
}
}
