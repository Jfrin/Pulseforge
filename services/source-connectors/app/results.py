from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommitResult:
    """Outcome of committing one page: records that were new vs already seen."""

    new: int
    duplicates: int


@dataclass(frozen=True, slots=True)
class PassResult:
    """Outcome of one full polling pass."""

    new: int
    duplicates: int
