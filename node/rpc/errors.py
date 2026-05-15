"""JSON-RPC error helpers."""

from dataclasses import dataclass


@dataclass
class RPCError(Exception):
    """Typed JSON-RPC failure with explicit code/message."""

    code: int
    message: str

    def __str__(self) -> str:
        return self.message


def invalid_params(message: str) -> RPCError:
    return RPCError(-32602, message)

