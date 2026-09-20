from .registry import AdapterDescriptor, AdapterRegistry
from .workspace_project_configure import install_workspace_project_configure

install_workspace_project_configure()

__all__ = ["AdapterDescriptor", "AdapterRegistry"]
