#pragma once
namespace AIBridgeUE::LandscapeRepair
{
enum class ETextureReadiness { Ready, PendingCompilation, InvalidSource, RebuildResource };
constexpr ETextureReadiness EvaluateTextureReadiness(bool SourceValid, bool Pending, int Mips, bool FormatValid, bool ResourceValid, bool RHIValid)
{
    if (!SourceValid) return ETextureReadiness::InvalidSource;
    if (Pending) return ETextureReadiness::PendingCompilation;
    if (Mips <= 0 || !FormatValid || !ResourceValid || !RHIValid) return ETextureReadiness::RebuildResource;
    return ETextureReadiness::Ready;
}
}
