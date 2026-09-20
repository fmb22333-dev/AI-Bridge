using UnrealBuildTool;

public class AIBridgeUEEditor : ModuleRules
{
    public AIBridgeUEEditor(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

        PublicDependencyModuleNames.AddRange(new string[]
        {
            "Core"
        });

        PrivateDependencyModuleNames.AddRange(new string[]
        {
            "CoreUObject",
            "Engine",
            "ImageCore",
            "RenderCore",
            "RHI",
            "Landscape",
            "UnrealEd",
            "LevelEditor",
            "AssetRegistry",
            "ContentBrowser",
            "DesktopPlatform",
            "ToolMenus",
            "Slate",
            "SlateCore",
            "HTTP",
            "Json",
            "JsonUtilities",
            "Projects"
        });

        AddEngineThirdPartyPrivateStaticDependencies(Target, "FBX");
    }
}
