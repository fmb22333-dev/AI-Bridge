#include "Misc/AutomationTest.h"
#include "AIBridgeUELandscapeComparison.h"
#include "AIBridgeUELandscapePlaneDiff.h"

#if WITH_DEV_AUTOMATION_TESTS

IMPLEMENT_SIMPLE_AUTOMATION_TEST(
    FAIBridgeLandscapeBoundaryTopologyTest,
    "AIBridgeUE.LandscapeRepair.BoundaryTopology",
    EAutomationTestFlags::EditorContext | EAutomationTestFlags::EngineFilter)

bool FAIBridgeLandscapeBoundaryTopologyTest::RunTest(const FString&)
{
    using namespace AIBridgeUE::LandscapeRepair;

    const FLandscapeLogicalVertex FirstSubsectionEdge =
        PackedTexelToLandscapeVertex(127, 42, 127, 2, 0, 0);
    const FLandscapeLogicalVertex SecondSubsectionEdge =
        PackedTexelToLandscapeVertex(128, 42, 127, 2, 0, 0);
    TestTrue(TEXT("The last texel of subsection zero is valid"), FirstSubsectionEdge.bValid);
    TestTrue(TEXT("The first texel of subsection one is valid"), SecondSubsectionEdge.bValid);
    TestEqual(TEXT("Duplicated subsection X texels map to the same logical vertex"), FirstSubsectionEdge.X, 127);
    TestEqual(TEXT("The duplicate subsection texel keeps the same X coordinate"), SecondSubsectionEdge.X, 127);
    TestEqual(TEXT("The Y coordinate remains unchanged"), SecondSubsectionEdge.Y, 42);

    const FLandscapeLogicalVertex ComponentMaximum =
        PackedTexelToLandscapeVertex(255, 255, 127, 2, 0, 0);
    const FLandscapeLogicalVertex NeighborMinimum =
        PackedTexelToLandscapeVertex(0, 0, 127, 2, 254, 254);
    TestEqual(TEXT("A 256 texel packed component contains 254 logical quads in X"), ComponentMaximum.X, 254);
    TestEqual(TEXT("A 256 texel packed component contains 254 logical quads in Y"), ComponentMaximum.Y, 254);
    TestEqual(TEXT("The neighboring component shares the same landscape X vertex"), NeighborMinimum.X, ComponentMaximum.X);
    TestEqual(TEXT("The neighboring component shares the same landscape Y vertex"), NeighborMinimum.Y, ComponentMaximum.Y);
    TestFalse(
        TEXT("A packed coordinate outside the component plane is rejected"),
        PackedTexelToLandscapeVertex(256, 0, 127, 2, 0, 0).bValid);
    TestTrue(TEXT("The minimum X texel belongs to the component edge"), IsPackedComponentEdge(0, 42, 256));
    TestTrue(TEXT("The maximum Y texel belongs to the component edge"), IsPackedComponentEdge(42, 255, 256));
    TestFalse(TEXT("An interior texel is not a component edge"), IsPackedComponentEdge(127, 128, 256));
    TestFalse(TEXT("An invalid packed extent has no component edge"), IsPackedComponentEdge(0, 0, 0));

    const TArray<uint16> ConflictingSource = {34249, 32768};
    const TArray<uint16> ReconciledMerged = {34249, 34249};
    const FSharedVertexAudit Reconciled = ClassifySharedVertex(ConflictingSource, ReconciledMerged);
    TestEqual(
        TEXT("A consistent merged value selected from conflicting source copies is reconciliation"),
        Reconciled.Result,
        ESharedVertexAuditResult::ReconciledFromSource);
    TestTrue(TEXT("Authoritative source reconciliation is semantically safe"), Reconciled.bEquivalent);
    TestEqual(TEXT("The selected source value is retained"), Reconciled.MergedValue, uint16(34249));

    const TArray<uint16> GeneratedMerged = {33000, 33000};
    const FSharedVertexAudit Generated = ClassifySharedVertex(ConflictingSource, GeneratedMerged);
    TestEqual(
        TEXT("A merged value absent from every source owner is rejected"),
        Generated.Result,
        ESharedVertexAuditResult::MergedValueNotInSource);
    TestFalse(TEXT("A generated merged value is not equivalent"), Generated.bEquivalent);

    const TArray<uint16> DisagreeingMerged = {34249, 32768};
    const FSharedVertexAudit Disagreeing = ClassifySharedVertex(ConflictingSource, DisagreeingMerged);
    TestEqual(
        TEXT("Merged duplicate owners must agree"),
        Disagreeing.Result,
        ESharedVertexAuditResult::MergedCopiesDisagree);
    TestFalse(TEXT("Unresolved merged copies are not equivalent"), Disagreeing.bEquivalent);

    const TArray<FSharedVertexOwnerSample> Owners = {
        {0, 32768, 34249},
        {1, 34249, 34249},
        {1, 34249, 34249}
    };
    const FSharedVertexOwnerAudit OwnerAudit = AuditSharedVertexOwners(Owners);
    TestEqual(TEXT("One physical owner changed during reconciliation"), OwnerAudit.ChangedOwners, 1);
    TestEqual(TEXT("Subsection duplicates do not increase the component owner count"), OwnerAudit.UniqueComponents, 2);
    TestEqual(
        TEXT("Owner aggregation retains the authoritative source classification"),
        OwnerAudit.ValueAudit.Result,
        ESharedVertexAuditResult::ReconciledFromSource);
    TestTrue(TEXT("The aggregated owner reconciliation is safe"), OwnerAudit.ValueAudit.bEquivalent);

    auto SafeComparison = MakeShared<FJsonObject>();
    SafeComparison->SetStringField(TEXT("schema"), TEXT("landscape_pixel_comparison/2"));
    SafeComparison->SetBoolField(TEXT("diagnostic_only"), true);
    SafeComparison->SetBoolField(TEXT("all_active_hashes_match_captured_source"), true);
    auto SafeSummary = MakeShared<FJsonObject>();
    SafeSummary->SetNumberField(TEXT("expected_components"), 4);
    SafeSummary->SetNumberField(TEXT("checked_components"), 4);
    SafeSummary->SetNumberField(TEXT("errors"), 0);
    SafeSummary->SetNumberField(TEXT("height_changed_pixels"), 2);
    SafeSummary->SetNumberField(TEXT("paint_changed_pixels"), 1);
    SafeComparison->SetObjectField(TEXT("summary"), SafeSummary);
    TArray<TSharedPtr<FJsonValue>> SafeComponents;
    for (int32 Index = 0; Index < 4; ++Index) SafeComponents.Add(MakeShared<FJsonValueNull>());
    SafeComparison->SetArrayField(TEXT("components"), SafeComponents);
    auto SafeBoundary = MakeShared<FJsonObject>();
    SafeBoundary->SetStringField(TEXT("schema"), TEXT("landscape_shared_vertex_audit/1"));
    SafeBoundary->SetBoolField(TEXT("diagnostic_only"), true);
    SafeBoundary->SetNumberField(TEXT("tracked_changed_logical_vertices"), 2);
    SafeBoundary->SetNumberField(TEXT("changed_samples_outside_component_edges"), 0);
    SafeBoundary->SetNumberField(TEXT("capture_errors"), 0);
    SafeBoundary->SetNumberField(TEXT("changed_logical_vertex_groups"), 2);
    SafeBoundary->SetNumberField(TEXT("reconciled_from_source_groups"), 2);
    SafeBoundary->SetNumberField(TEXT("exact_groups"), 0);
    SafeBoundary->SetNumberField(TEXT("merged_value_not_in_source_groups"), 0);
    SafeBoundary->SetNumberField(TEXT("merged_copies_disagree_groups"), 0);
    SafeBoundary->SetNumberField(TEXT("invalid_groups"), 0);
    SafeBoundary->SetNumberField(TEXT("single_component_groups"), 0);
    SafeBoundary->SetNumberField(TEXT("expected_changed_owner_samples"), 3);
    SafeBoundary->SetNumberField(TEXT("audited_changed_owner_samples"), 3);
    SafeBoundary->SetBoolField(TEXT("all_changed_samples_audited"), true);
    SafeBoundary->SetBoolField(TEXT("all_changes_shared_vertex_equivalent"), true);
    TArray<TSharedPtr<FJsonValue>> SafeGroups;
    SafeGroups.Add(MakeShared<FJsonValueNull>());
    SafeGroups.Add(MakeShared<FJsonValueNull>());
    SafeBoundary->SetArrayField(TEXT("groups"), SafeGroups);
    SafeComparison->SetObjectField(TEXT("boundary_audit"), SafeBoundary);

    FString AcceptanceError;
    TestTrue(
        TEXT("A complete source-selected shared-edge reconciliation may pass the save gate"),
        IsSafeMergedComparisonForSave(SafeComparison, AcceptanceError));
    TestTrue(TEXT("A safe comparison has no rejection reason"), AcceptanceError.IsEmpty());

    SafeBoundary->SetNumberField(TEXT("merged_value_not_in_source_groups"), 1);
    TestFalse(
        TEXT("A merged value that does not exist in any source owner must keep the save gate closed"),
        IsSafeMergedComparisonForSave(SafeComparison, AcceptanceError));
    TestEqual(TEXT("The unsafe value rejection is explicit"), AcceptanceError, FString(TEXT("MERGED_VALUE_NOT_IN_SOURCE")));
    SafeBoundary->SetNumberField(TEXT("merged_value_not_in_source_groups"), 0);

    SafeComparison->SetBoolField(TEXT("all_active_hashes_match_captured_source"), false);
    TestFalse(
        TEXT("The audit cannot override an active edit-layer mismatch"),
        IsSafeMergedComparisonForSave(SafeComparison, AcceptanceError));
    TestEqual(TEXT("The active-data rejection is explicit"), AcceptanceError, FString(TEXT("ACTIVE_SOURCE_MISMATCH")));

    return true;
}

#endif
