"""Differential checks against Bitcoin Core behavior (optional)."""

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest

from node.wallet.core.tx_builder import TransactionBuilder
from shared.core.transaction import Transaction, TxIn, TxOut
from shared.crypto.address import public_key_to_address
from shared.crypto.keys import PrivateKey


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _BitcoinCoreHarness:
    def __init__(self):
        self.bitcoind = shutil.which("bitcoind")
        self.bitcoin_cli = shutil.which("bitcoin-cli")
        self.proc = None
        self.tmpdir = None
        self.rpc_port = None
        self.rpc_user = "berz"
        self.rpc_password = "berz-test"

    def available(self) -> bool:
        return bool(self.bitcoind and self.bitcoin_cli)

    def start(self) -> None:
        if not self.available():
            raise RuntimeError("bitcoind/bitcoin-cli not available")

        self.tmpdir = tempfile.TemporaryDirectory(prefix="berzcore_diff_")
        self.rpc_port = _free_port()
        cmd = [
            self.bitcoind,
            "-regtest",
            "-server",
            "-txindex=1",
            "-fallbackfee=0.0001",
            f"-rpcuser={self.rpc_user}",
            f"-rpcpassword={self.rpc_password}",
            f"-rpcport={self.rpc_port}",
            f"-datadir={self.tmpdir.name}",
            "-listen=0",
            "-dnsseed=0",
            "-discover=0",
            "-noprinttoconsole",
        ]
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        deadline = time.time() + 25
        last_err = ""
        while time.time() < deadline:
            try:
                self.rpc(["getblockchaininfo"])
                return
            except Exception as e:
                last_err = str(e)
                time.sleep(0.25)

        raise RuntimeError(f"bitcoind did not become ready: {last_err}")

    def stop(self) -> None:
        if self.proc is not None:
            try:
                self.rpc(["stop"])
            except Exception:
                pass
            try:
                self.proc.wait(timeout=8)
            except Exception:
                self.proc.kill()
        if self.tmpdir is not None:
            self.tmpdir.cleanup()

    def rpc(self, args):
        if self.rpc_port is None:
            raise RuntimeError("rpc_port is not initialized")
        cmd = [
            self.bitcoin_cli,
            "-regtest",
            f"-rpcuser={self.rpc_user}",
            f"-rpcpassword={self.rpc_password}",
            f"-rpcport={self.rpc_port}",
            *args,
        ]
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT, text=True)
        out = out.strip()
        if out.startswith("{") or out.startswith("["):
            return json.loads(out)
        return out


class TestBitcoinCoreDifferential(unittest.TestCase):
    @staticmethod
    def _preflight_hint() -> str:
        return (
            "Run scripts/core_diff_preflight.sh, then run: "
            "BERZ_ENABLE_CORE_DIFF=1 pytest -q tests/integration/test_bitcoin_core_differential.py -rs"
        )

    @classmethod
    def setUpClass(cls):
        enabled = os.getenv("BERZ_ENABLE_CORE_DIFF", "0") == "1"
        strict = os.getenv("BERZ_REQUIRE_CORE_DIFF", "0") == "1"
        if not enabled:
            msg = (
                "set BERZ_ENABLE_CORE_DIFF=1 to run Bitcoin Core differential tests. "
                f"{cls._preflight_hint()}"
            )
            if strict:
                raise RuntimeError(msg)
            raise unittest.SkipTest(msg)
        cls.h = _BitcoinCoreHarness()
        if not cls.h.available():
            missing = []
            if not cls.h.bitcoind:
                missing.append("bitcoind")
            if not cls.h.bitcoin_cli:
                missing.append("bitcoin-cli")
            msg = (
                f"missing external dependencies: {', '.join(missing)}. "
                f"{cls._preflight_hint()}"
            )
            if strict:
                raise RuntimeError(msg)
            raise unittest.SkipTest(msg)
        cls.h.start()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "h"):
            cls.h.stop()

    def test_script_type_decoding_parity(self) -> None:
        priv = PrivateKey(123456789)
        pub = priv.public_key()

        vectors = [
            ("p2pkh", public_key_to_address(pub, network="regtest", segwit=False), "pubkeyhash"),
            ("p2wpkh", public_key_to_address(pub, network="regtest", segwit=True), "witness_v0_keyhash"),
        ]

        tb = TransactionBuilder("regtest")
        for _name, addr, expected_core_type in vectors:
            script_hex = tb._create_script_pubkey(addr).hex()
            decoded = self.h.rpc(["decodescript", script_hex])
            self.assertEqual(decoded.get("type"), expected_core_type)

    def test_txid_wtxid_parity(self) -> None:
        tx = Transaction(version=2)
        tx.vin = [
            TxIn(prev_tx_hash=b"\x01" * 32, prev_tx_index=1, script_sig=b"\x51", sequence=0xFFFFFFFE)
        ]
        tx.vout = [TxOut(value=12345, script_pubkey=b"\x51")]
        tx.locktime = 12

        raw = tx.serialize(include_witness=True).hex()
        decoded = self.h.rpc(["decoderawtransaction", raw])

        self.assertEqual(decoded.get("txid"), tx.txid()[::-1].hex())
        self.assertEqual(decoded.get("hash"), tx.wtxid()[::-1].hex())


if __name__ == "__main__":
    unittest.main()
