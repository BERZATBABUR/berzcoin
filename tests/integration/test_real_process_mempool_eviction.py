"""Real-process integration: mined block should evict confirmed tx from mempool across nodes.

This test is opt-in because it requires binding local P2P/RPC sockets.
Enable with: BERZCOIN_RUN_REAL_NET_TESTS=1 pytest -q tests/integration/test_real_process_mempool_eviction.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


def _run_enabled() -> bool:
    return os.environ.get("BERZCOIN_RUN_REAL_NET_TESTS", "0") == "1"


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@unittest.skipUnless(_run_enabled(), "set BERZCOIN_RUN_REAL_NET_TESTS=1 to run real-process network test")
class TestRealProcessMempoolEviction(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="berzcoin-real-proc-"))
        self.node1_dir = self.base / "node1"
        self.node2_dir = self.base / "node2"
        self.node1_dir.mkdir(parents=True, exist_ok=True)
        self.node2_dir.mkdir(parents=True, exist_ok=True)

        self.node1_rpc = _free_port()
        self.node2_rpc = _free_port()
        self.node1_p2p = _free_port()
        self.node2_p2p = _free_port()

        self.node1 = None
        self.node2 = None

        self.py = os.environ.get("PYTHON", "python3")

    def tearDown(self) -> None:
        for proc in (self.node1, self.node2):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        shutil.rmtree(self.base, ignore_errors=True)

    def _write_conf(self, datadir: Path, rpc: int, p2p: int, addnode: int, mining: bool) -> Path:
        conf = datadir / "berzcoin.conf"
        conf.write_text(
            "\n".join(
                [
                    "[main]",
                    "network = regtest",
                    f"datadir = {datadir}",
                    "disablewallet = false",
                    "rpcbind = 127.0.0.1",
                    f"rpcport = {rpc}",
                    "rpcallowip = 127.0.0.1",
                    "bind = 127.0.0.1",
                    f"port = {p2p}",
                    f"addnode = 127.0.0.1:{addnode}",
                    f"mining = {'true' if mining else 'false'}",
                    "autominer = false",
                    "miningaddress =",
                    "debug = true",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return conf

    def _start_node(self, datadir: Path, conf: Path) -> subprocess.Popen:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        return subprocess.Popen(
            [self.py, "-m", "node.app.main", "--regtest", "-datadir", str(datadir), "-conf", str(conf)],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _cli(self, datadir: Path, rpcport: int, *args: str) -> str:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
        proc = subprocess.run(
            [self.py, "-m", "cli.main", "-datadir", str(datadir), "-rpcport", str(rpcport), *args],
            cwd=str(Path(__file__).resolve().parents[2]),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def _wait_rpc(self, datadir: Path, rpcport: int, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                self._cli(datadir, rpcport, "getblockcount")
                return
            except Exception:
                time.sleep(0.25)
        raise TimeoutError(f"RPC not ready on {rpcport}")

    def _wait_tip(self, datadir: Path, rpcport: int, target: int, timeout: float = 30.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                tip = int(self._cli(datadir, rpcport, "getblockcount").splitlines()[-1])
                if tip >= target:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise TimeoutError(f"tip {target} not reached on {rpcport}")

    def test_mined_block_evicts_confirmed_tx_from_mempool(self) -> None:
        conf1 = self._write_conf(self.node1_dir, self.node1_rpc, self.node1_p2p, self.node2_p2p, mining=True)
        conf2 = self._write_conf(self.node2_dir, self.node2_rpc, self.node2_p2p, self.node1_p2p, mining=False)

        self.node1 = self._start_node(self.node1_dir, conf1)
        self.node2 = self._start_node(self.node2_dir, conf2)

        self._wait_rpc(self.node1_dir, self.node1_rpc)
        self._wait_rpc(self.node2_dir, self.node2_rpc)

        wallet1 = json.loads(self._cli(self.node1_dir, self.node1_rpc, "createwallet", "default"))
        self._cli(self.node1_dir, self.node1_rpc, "activatewallet", wallet1["private_key"])

        wallet2 = json.loads(self._cli(self.node2_dir, self.node2_rpc, "createwallet", "default"))
        self._cli(self.node2_dir, self.node2_rpc, "activatewallet", wallet2["private_key"])
        addr2 = self._cli(self.node2_dir, self.node2_rpc, "getnewaddress").splitlines()[-1]

        addr1 = self._cli(self.node1_dir, self.node1_rpc, "getnewaddress").splitlines()[-1]
        self._cli(self.node1_dir, self.node1_rpc, "setminingaddress", addr1)

        self._cli(self.node1_dir, self.node1_rpc, "generate", "101", "--address", addr1)
        self._wait_tip(self.node2_dir, self.node2_rpc, 101)

        sent_line = self._cli(self.node1_dir, self.node1_rpc, "sendtoaddress", addr2, "1.0").splitlines()[-1]
        txid = sent_line.replace("Transaction sent:", "").strip()
        mempool_before = json.loads(self._cli(self.node1_dir, self.node1_rpc, "getrawmempool"))
        self.assertIn(txid, mempool_before)

        self._cli(self.node1_dir, self.node1_rpc, "generate", "1", "--address", addr1)
        self._wait_tip(self.node2_dir, self.node2_rpc, 102)

        mempool_after = json.loads(self._cli(self.node1_dir, self.node1_rpc, "getrawmempool"))
        self.assertNotIn(txid, mempool_after)


if __name__ == "__main__":
    unittest.main()
