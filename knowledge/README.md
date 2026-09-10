# AI Bridge Knowledge Pack

The Knowledge Pack is intentionally separate from the stable Adapter Kernel.

## Public distribution scope

This repository's distributable knowledge may contain only **generic validated/promoted** knowledge:

- recipes / syntax sugar
- diagnostic/error rules
- aliases
- construction templates
- host heuristics
- validation rules
- capability guidance

Project-specific evidence is not public execution authority.

## Lifecycle

Recommended lifecycle:

`observed -> candidate -> validated -> promoted -> deprecated`

Only validated/promoted entries whose executable scope is generic may be included in a public release.

## Runtime behavior

Normal knowledge changes should be hot-loadable where supported. Malformed knowledge must fail closed and preserve the previous validated pack.

## Source versus distribution

During migration, the old development repository remains the historical evidence source. This public repository will receive only sanitized/generic content produced by the clean Knowledge Pack filter; ProjectFamilyA/ProjectFamilyB-specific evidence, local paths and project-family recipes must not be copied here as generic knowledge.

## Planned layout

- `houdini/recipes/`
- `houdini/diagnostics/`
- `houdini/aliases/`
- `houdini/templates/`
- future `unreal/` and `blender/` packs

The authoritative filtering/build implementation is being migrated with Runtime deployment code.
