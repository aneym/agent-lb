from __future__ import annotations


class FederationNotFoundError(Exception):
    def __init__(self, identifier: str) -> None:
        self.identifier = identifier
        super().__init__(f"Federation resource not found: {identifier}")


class FederationConflictError(Exception):
    def __init__(self, account_id: str, *, current_owner: str | None) -> None:
        self.account_id = account_id
        self.current_owner = current_owner
        super().__init__(f"Account {account_id!r} is not available for checkout (current owner={current_owner!r})")


class FederationNotConfiguredError(Exception):
    def __init__(self, message: str = "Federation peer is not configured on this instance") -> None:
        super().__init__(message)


class FederationPeerRequestError(Exception):
    """Raised by a FederationPeerClient implementation when a peer HTTP call fails."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        super().__init__(message)
