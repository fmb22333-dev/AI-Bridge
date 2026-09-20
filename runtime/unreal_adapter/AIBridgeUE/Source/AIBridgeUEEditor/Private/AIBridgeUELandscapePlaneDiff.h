#pragma once
#include "CoreMinimal.h"
namespace AIBridgeUE::LandscapeRepair
{
struct FLandscapeLogicalVertex
{
 bool bValid = false;
 int32 X = 0;
 int32 Y = 0;
};

// Landscape stores each subsection as a power-of-two vertex plane. The last
// vertex of one subsection and the first vertex of the next subsection are two
// packed texels representing the same logical Landscape vertex.
inline FLandscapeLogicalVertex PackedTexelToLandscapeVertex(
 int32 PackedX,
 int32 PackedY,
 int32 SubsectionSizeQuads,
 int32 NumSubsections,
 int32 SectionBaseX,
 int32 SectionBaseY)
{
 FLandscapeLogicalVertex Result;
 if (SubsectionSizeQuads <= 0 || NumSubsections <= 0) return Result;
 const int64 SubsectionVertices = int64(SubsectionSizeQuads) + 1;
 const int64 PackedSide = SubsectionVertices * NumSubsections;
 if (PackedSide > MAX_int32 || PackedX < 0 || PackedY < 0 || PackedX >= PackedSide || PackedY >= PackedSide) return Result;
 Result.bValid = true;
 Result.X = SectionBaseX + (PackedX / int32(SubsectionVertices)) * SubsectionSizeQuads + (PackedX % int32(SubsectionVertices));
 Result.Y = SectionBaseY + (PackedY / int32(SubsectionVertices)) * SubsectionSizeQuads + (PackedY % int32(SubsectionVertices));
 return Result;
}

inline bool IsPackedComponentEdge(int32 PackedX, int32 PackedY, int32 PackedSide)
{
 return PackedSide > 0 && PackedX >= 0 && PackedY >= 0 && PackedX < PackedSide && PackedY < PackedSide
  && (PackedX == 0 || PackedY == 0 || PackedX == PackedSide - 1 || PackedY == PackedSide - 1);
}

enum class ESharedVertexAuditResult : uint8
{
 Invalid,
 Exact,
 ReconciledFromSource,
 MergedValueNotInSource,
 MergedCopiesDisagree
};

struct FSharedVertexAudit
{
 ESharedVertexAuditResult Result = ESharedVertexAuditResult::Invalid;
 bool bEquivalent = false;
 uint16 MergedValue = 0;
 int32 SourceOwnerCount = 0;
 int32 MergedOwnerCount = 0;
 int32 SourceUniqueValues = 0;
};

// A merge is semantically safe at a shared vertex only when every merged copy
// agrees and that value existed in at least one of the source owners.
inline FSharedVertexAudit ClassifySharedVertex(const TArray<uint16>& SourceValues, const TArray<uint16>& MergedValues)
{
 FSharedVertexAudit Audit;
 Audit.SourceOwnerCount = SourceValues.Num();
 Audit.MergedOwnerCount = MergedValues.Num();
 if (SourceValues.IsEmpty() || MergedValues.IsEmpty()) return Audit;

 TSet<uint16> UniqueSourceValues;
 for (uint16 Value : SourceValues) UniqueSourceValues.Add(Value);
 Audit.SourceUniqueValues = UniqueSourceValues.Num();
 Audit.MergedValue = MergedValues[0];
 for (uint16 Value : MergedValues)
 {
  if (Value != Audit.MergedValue)
  {
   Audit.Result = ESharedVertexAuditResult::MergedCopiesDisagree;
   return Audit;
  }
 }
 if (!UniqueSourceValues.Contains(Audit.MergedValue))
 {
  Audit.Result = ESharedVertexAuditResult::MergedValueNotInSource;
  return Audit;
 }
 Audit.Result = UniqueSourceValues.Num() == 1
  ? ESharedVertexAuditResult::Exact
  : ESharedVertexAuditResult::ReconciledFromSource;
 Audit.bEquivalent = true;
 return Audit;
}

struct FSharedVertexOwnerSample
{
 int32 ComponentIndex = INDEX_NONE;
 uint16 SourceValue = 0;
 uint16 MergedValue = 0;
};

struct FSharedVertexOwnerAudit
{
 FSharedVertexAudit ValueAudit;
 int32 ChangedOwners = 0;
 int32 UniqueComponents = 0;
};

inline FSharedVertexOwnerAudit AuditSharedVertexOwners(const TArray<FSharedVertexOwnerSample>& Owners)
{
 FSharedVertexOwnerAudit Result;
 TArray<uint16> SourceValues;
 TArray<uint16> MergedValues;
 TSet<int32> Components;
 SourceValues.Reserve(Owners.Num());
 MergedValues.Reserve(Owners.Num());
 for (const FSharedVertexOwnerSample& Owner : Owners)
 {
  SourceValues.Add(Owner.SourceValue);
  MergedValues.Add(Owner.MergedValue);
  if (Owner.SourceValue != Owner.MergedValue) ++Result.ChangedOwners;
  if (Owner.ComponentIndex != INDEX_NONE) Components.Add(Owner.ComponentIndex);
 }
 Result.UniqueComponents = Components.Num();
 Result.ValueAudit = ClassifySharedVertex(SourceValues, MergedValues);
 return Result;
}

// Zero-based component index; extracted existing sampling policy for regression coverage.
inline bool ShouldRecordComparisonDetails(int32 ComponentIndex, bool bDifferent)
{
 return ComponentIndex >= 0 && (ComponentIndex < 32 || bDifferent);
}
struct FPlaneDifference
{
 bool bValid = false;
 int32 Changed = 0;
 int32 MaximumDelta = 0;
 int64 SumAbsoluteDelta = 0;
 TArray<int32> Samples;
};
// Exact diagnostic comparison. Missing-as-zero is explicit and only for paint.
inline FPlaneDifference ComparePlaneValues(const TArray<uint16>& Before, const TArray<uint16>& After, int32 Count, bool bMissingIsZero)
{
 FPlaneDifference D;
 if (Count <= 0 || (Before.Num() != Count && !(bMissingIsZero && Before.IsEmpty())) || (After.Num() != Count && !(bMissingIsZero && After.IsEmpty()))) return D;
 D.bValid = true;
 for (int32 I = 0; I < Count; ++I)
 {
  const int32 A = Before.IsEmpty() ? 0 : Before[I];
  const int32 B = After.IsEmpty() ? 0 : After[I];
  const int32 Delta = FMath::Abs(A - B);
  if (Delta) { ++D.Changed; D.MaximumDelta = FMath::Max(D.MaximumDelta, Delta); D.SumAbsoluteDelta += Delta; if (D.Samples.Num() < 8) D.Samples.Add(I); }
 }
 return D;
}
}
