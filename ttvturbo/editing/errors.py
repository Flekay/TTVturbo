"""Errors raised by the non-destructive edit-project backend."""

class EditError(Exception):
    """Base edit-project error."""


class EditNotFoundError(EditError):
    """Requested project, branch, commit, sequence, or merge was not found."""


class EditValidationError(EditError):
    """Input or an edit operation is invalid."""


class EditConflictError(EditError):
    """Optimistic-concurrency, source-integrity, or merge conflict."""


class EditStorageError(EditError):
    """SQLite or persistence failure."""
