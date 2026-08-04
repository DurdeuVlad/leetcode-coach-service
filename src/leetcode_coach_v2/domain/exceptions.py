class DomainError(ValueError):
    """A rejected domain request; callers may safely show its message."""


class NotFound(DomainError):
    pass


class Conflict(DomainError):
    pass


class ApprovalExpired(DomainError):
    pass
