# Engine Compatibility

Supported design target:

- UE 5.6 Editor
- UE 5.8 Editor

Avoid direct dependency on unstable editor APIs.

Preferred APIs:

- UEditorSubsystem
- UEditorActorSubsystem
- AssetRegistry

Version-specific code should be isolated with ENGINE_MINOR_VERSION checks.
