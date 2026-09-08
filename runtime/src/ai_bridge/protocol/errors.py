class BridgeError(Exception):
    """Base deterministic bridge error."""


class CapabilityNotSupported(BridgeError):
    pass


class SessionNotFound(BridgeError):
    pass
