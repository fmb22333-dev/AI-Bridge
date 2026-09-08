from pydantic import BaseModel, ConfigDict, Field
from ai_bridge.protocol.capability import CapabilityDescriptor


class AdapterDescriptor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: str = Field(min_length=1)
    adapter_version: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)
    capabilities: list[CapabilityDescriptor] = Field(default_factory=list)


class AdapterRegistry:
    def __init__(self) -> None:
        self._items: dict[str, AdapterDescriptor] = {}

    def register(self, descriptor: AdapterDescriptor, *, replace: bool = False) -> None:
        if descriptor.adapter in self._items and not replace:
            raise ValueError(f"adapter already registered: {descriptor.adapter}")
        self._items[descriptor.adapter] = descriptor

    def get(self, adapter: str) -> AdapterDescriptor:
        return self._items[adapter]

    def list(self) -> list[AdapterDescriptor]:
        return list(self._items.values())
