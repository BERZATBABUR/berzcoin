"""Script verification flags for BerzCoin."""

class ScriptFlags:
    """Script verification flags."""

    VERIFY_NONE = 0
    VERIFY_P2SH = 1 << 0
    VERIFY_STRICTENC = 1 << 1
    VERIFY_DERSIG = 1 << 2
    VERIFY_LOW_S = 1 << 3
    VERIFY_NULLDUMMY = 1 << 4
    VERIFY_SIGPUSHONLY = 1 << 5
    VERIFY_MINIMALDATA = 1 << 6
    VERIFY_DISCOURAGE_UPGRADABLE_NOPS = 1 << 7
    VERIFY_CLEANSTACK = 1 << 8
    VERIFY_CHECKLOCKTIMEVERIFY = 1 << 9
    VERIFY_CHECKSEQUENCEVERIFY = 1 << 10
    VERIFY_WITNESS = 1 << 11
    VERIFY_DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM = 1 << 12
    VERIFY_MINIMALIF = 1 << 13
    VERIFY_NULLFAIL = 1 << 14
    VERIFY_WITNESS_PUBKEYTYPE = 1 << 15
    VERIFY_DISCOURAGE_UPGRADABLE_TAPROOT_VERSION = 1 << 16

    STANDARD_VERIFY_FLAGS = (
        VERIFY_P2SH |
        VERIFY_STRICTENC |
        VERIFY_DERSIG |
        VERIFY_LOW_S |
        VERIFY_NULLDUMMY |
        VERIFY_SIGPUSHONLY |
        VERIFY_MINIMALDATA |
        VERIFY_DISCOURAGE_UPGRADABLE_NOPS |
        VERIFY_CLEANSTACK |
        VERIFY_CHECKLOCKTIMEVERIFY |
        VERIFY_CHECKSEQUENCEVERIFY |
        VERIFY_WITNESS |
        VERIFY_DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM |
        VERIFY_MINIMALIF |
        VERIFY_NULLFAIL |
        VERIFY_WITNESS_PUBKEYTYPE
    )

    MANDATORY_VERIFY_FLAGS = (
        VERIFY_P2SH |
        VERIFY_STRICTENC |
        VERIFY_DERSIG |
        VERIFY_LOW_S |
        VERIFY_NULLDUMMY |
        VERIFY_SIGPUSHONLY |
        VERIFY_MINIMALDATA |
        VERIFY_DISCOURAGE_UPGRADABLE_NOPS |
        VERIFY_CLEANSTACK |
        VERIFY_CHECKLOCKTIMEVERIFY |
        VERIFY_CHECKSEQUENCEVERIFY |
        VERIFY_WITNESS |
        VERIFY_DISCOURAGE_UPGRADABLE_WITNESS_PROGRAM
    )

    def __init__(self, flags: int = STANDARD_VERIFY_FLAGS):
        self.flags = flags

    def is_enabled(self, flag: int) -> bool:
        return (self.flags & flag) != 0

    def enable(self, flag: int) -> None:
        self.flags |= flag

    def disable(self, flag: int) -> None:
        self.flags &= ~flag

    def __repr__(self) -> str:
        flags = []
        if self.is_enabled(self.VERIFY_P2SH):
            flags.append("P2SH")
        if self.is_enabled(self.VERIFY_WITNESS):
            flags.append("WITNESS")
        if self.is_enabled(self.VERIFY_CHECKLOCKTIMEVERIFY):
            flags.append("CLTV")
        if self.is_enabled(self.VERIFY_CHECKSEQUENCEVERIFY):
            flags.append("CSV")
        return f"ScriptFlags({', '.join(flags) if flags else 'NONE'})"
