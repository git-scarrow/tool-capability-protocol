"""TCP error types and exceptions."""


class TCPError(Exception):
    """Base exception for all TCP errors."""
    pass


class E_MAGIC(TCPError):
    """Invalid magic bytes in header."""
    pass


class E_VERSION_UNSUPPORTED(TCPError):
    """Unsupported protocol version."""
    pass


class E_ENDIAN_UNSUPPORTED(TCPError):
    """Unsupported endianness."""
    pass


class E_RESERVED_BIT_SET(TCPError):
    """Reserved bits are non-zero."""
    pass


class E_CRC(TCPError):
    """CRC mismatch in header."""
    pass


class E_LENGTH_MISMATCH(TCPError):
    """Data length doesn't match declared total_length."""
    pass


class E_TLV_TRUNCATED(TCPError):
    """TLV header or payload truncated."""
    pass


class E_TLV_OVERFLOW(TCPError):
    """TLV extends beyond descriptor boundary."""
    pass


class E_REQ_UNKNOWN_TLV(TCPError):
    """Unknown TLV with required flag set."""
    pass


class E_SCHEMA(TCPError):
    """Schema validation failed."""
    pass


class E_SIG_BAD(TCPError):
    """Signature verification failed."""
    pass