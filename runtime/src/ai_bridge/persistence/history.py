from .db import BridgeDB


class CommandHistory:
    def __init__(self, db: BridgeDB) -> None:
        self.db = db

    def get(self, command_id: str):
        return self.db.get_command(command_id)
