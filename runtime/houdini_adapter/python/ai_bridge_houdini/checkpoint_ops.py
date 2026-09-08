from pathlib import Path


def create(hou, checkpoint_id):
    original = hou.hipFile.path()
    backup = hou.hipFile.saveAsBackup()
    return {
        "checkpoint_id": checkpoint_id,
        "original_hip": original,
        "checkpoint_path": str(Path(backup)),
        "verified": Path(backup).exists(),
    }


def rollback(hou, checkpoint_path, original_hip):
    path = Path(checkpoint_path)
    if not path.exists():
        raise ValueError(f"CHECKPOINT_NOT_FOUND: {checkpoint_path}")
    hou.hipFile.load(str(path), suppress_save_prompt=True, ignore_load_warnings=True)
    hou.hipFile.setName(original_hip)
    return {
        "checkpoint_path": str(path),
        "original_hip": original_hip,
        "current_hip": hou.hipFile.path(),
        "verified": hou.hipFile.path() == original_hip,
    }
