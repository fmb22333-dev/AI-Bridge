class EmergencyStop:
    def __init__(self) -> None:
        self._write_blocked = False

    @property
    def write_blocked(self) -> bool:
        return self._write_blocked

    def stop_writes(self) -> None:
        self._write_blocked = True

    def resume_writes(self) -> None:
        self._write_blocked = False
