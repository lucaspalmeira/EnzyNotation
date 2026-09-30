"""Project-specific exception hierarchy."""


class EnzyNotationError(Exception):
    """Base exception for expected EnzyNotation failures."""


class ConfigurationError(EnzyNotationError):
    """Raised when configuration cannot be loaded or validated."""


class FastaError(EnzyNotationError):
    """Raised when a FASTA file cannot be read or written."""


class ECNumberError(ValueError, EnzyNotationError):
    """Raised when an EC number is malformed."""
