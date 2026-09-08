from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field


class Workspace(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")
    workspace_id: str = Field(min_length=1)
    root: Path

    @classmethod
    def create(cls, workspace_id: str, root: Path) -> "Workspace":
        resolved = Path(root).expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"workspace root does not exist: {resolved}")
        if not resolved.is_dir():
            raise NotADirectoryError(f"workspace root is not a directory: {resolved}")
        return cls(workspace_id=workspace_id, root=resolved)

    def contains(self, path: Path) -> bool:
        candidate = Path(path).expanduser().resolve(strict=False)
        try:
            candidate.relative_to(self.root)
            return True
        except ValueError:
            return False


class WorkspaceRegistry:
    def __init__(self) -> None:
        self._items: dict[str, Workspace] = {}

    def register(self, workspace_id: str, root: Path) -> Workspace:
        if workspace_id in self._items:
            raise ValueError(f"workspace already registered: {workspace_id}")
        ws = Workspace.create(workspace_id, root)
        self._items[workspace_id] = ws
        return ws

    def get(self, workspace_id: str) -> Workspace:
        return self._items[workspace_id]

    def list(self) -> list[Workspace]:
        return list(self._items.values())

    def remove(self, workspace_id: str) -> Workspace:
        try:
            return self._items.pop(workspace_id)
        except KeyError as exc:
            raise KeyError(f"workspace not registered: {workspace_id}") from exc
