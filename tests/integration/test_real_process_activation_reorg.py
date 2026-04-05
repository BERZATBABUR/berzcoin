"""Real-process integration: nodes cross activation height and reorg to heavier branch.

This test is opt-in because it binds local P2P/RPC sockets and launches multiple
node processes.
Enable with:
BERZCOIN_RUN_REAL_NET_TESTS=1 pytest -q BerzCoin/tests/integration/test_real_process_activation_reorg.py
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from shared.consensus.buried_deployments import HARDFORK_TX_V2


def _run_enabled() -> bool:
    return os.environ.get("BERZCOIN_RUN_REAL_NET_TESTS", "0") == "1"


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


@unittest.skipUnless(_run_enabled(), "set BERZCOIN_RUN_REAL_NET_TESTS=1 to run real-process activation/reorg test")
class TestRealProcessActivationReorg(unittest.TestCase):
    def setUp(self) -> None:
        self.base = Path(tempfile.mkdtemp(prefix="berzcoin-real-activation-reorg-"))
        self.node1_dir = self.base / "node1"
        self.node2_dir = self.base / "node2"
        self.node3_dir = self.base / "node3"
        self.node1_dir.mkdir(parents=True, exist_ok=True)
        self.node2_dir.mkdir(parents=True, exist_ok=True)
        self.node3_dir.mkdir(parents=True, exist_ok=True)

        self.node1_rpc = _free_port()
        self.node2_rpc = _free_port()
        self.node3_rpc = _free_port()
        self.node1_p2p = _free_port()
        self.node2_p2p = _free_port()
        self.node3_p2p = _free_port()

        self.node1 = None
        self.node2 = None
        self.node3 = None

        self.py = os.environ.get("PYTHON", "python3")
        self.repo_root = Path(__file__).resolve().parents[2]
        self.activation_height = 103

    def tearDown(self) -> None:
        for proc in (self.node1, self.node2, self.node3):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        shutil.rmtree(self.base, ignore_errors=True)

    def _write_conf(self, datadir: Path, rpc: int, p2p: int, addnode_port: int | None, mining: bool) -> Path:
        lines = [
            "[main]",
            "network = regtest",
            f"datadir = {datadir}",
            "disablewallet = false",
            "rpcbind = 127.0.0.1",
            f"rpcport = {rpc}",
            "rpcallowip = 127.0.0.1",
            "bind = 127.0.0.1",
            f"port = {p2p}",
            f"mining = {'true' if mining else 'false'}",
            "autominer = false",
            "miningaddress =",
            "debug = true",
            f"activation_height_{HARDFORK_TX_V2} = {self.activation_height}",
            "node_consensus_version = 2",
            "enforce_hardfork_guardrails = true",
        ]
        if addnode_port is not None:
            lines.append(f"addnode = 127.0.0.1:{addnode_port}")
        lines.append("")

        conf = datadir / "berzcoin.conf"
        conf.write_text("\n".join(lines), encoding="utf-8")
        return conf

    def _start_node(self, datadir: Path, conf: Path) -> subprocess.Popen:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        return subprocess.Popen(
            [self.py, "-m", "node.app.main", "--regtest", "-datadir", str(datadir), "-conf", str(conf)],
            cwd=str(self.repo_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _stop_proc(self, proc: subprocess.Popen | None) -> None:
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()

    def _cli(self, datadir: Path, rpcport: int, *args: str) -> str:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        proc = subprocess.run(
            [self.py, "-m", "cli.main", "-datadir", str(datadir), "-rpcport", str(rpcport), *args],
            cwd=str(self.repo_root),
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        return proc.stdout.strip()

    def _wait_rpc(self, datadir: Path, rpcport: int, timeout: float = 45.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            try:
                self._cli(datadir, rpcport, "getblockcount")
                return
            except Exception:
                time.sleep(0.25)
        raise TimeoutError(f"RPC not ready on {rpcport}")

    def _wait_tip_at_least(self, datadir: Path, rpcport: int, target: int, timeout: float = 90.0) -> None:
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

    def test_two_node_reorg_across_hardfork_activation(self) -> None:
        # 1) Start two connected nodes and sync common history to height 101.
        conf1_connected = self._write_conf(
            self.node1_dir, self.node1_rpc, self.node1_p2p, self.node2_p2p, mining=True
        )
        conf2_connected = self._write_conf(
            self.node2_dir, self.node2_rpc, self.node2_p2p, self.node1_p2p, mining=True
        )

        self.node1 = self._start_node(self.node1_dir, conf1_connected)
        self.node2 = self._start_node(self.node2_dir, conf2_connected)
        self._wait_rpc(self.node1_dir, self.node1_rpc)
        self._wait_rpc(self.node2_dir, self.node2_rpc)

        addr1 = self._cli(self.node1_dir, self.node1_rpc, "getnewaddress").splitlines()[-1]
        addr2 = self._cli(self.node2_dir, self.node2_rpc, "getnewaddress").splitlines()[-1]
        self._cli(self.node1_dir, self.node1_rpc, "setminingaddress", addr1)
        self._cli(self.node2_dir, self.node2_rpc, "setminingaddress", addr2)

        self._cli(self.node1_dir, self.node1_rpc, "generate", "101", "--address", addr1)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 101)

        # 2) Isolate node2 by stopping it; node1 mines shorter branch to height 104 (cross activation at 103).
        self._stop_proc(self.node2)
        self.node2 = None

        self._cli(self.node1_dir, self.node1_rpc, "generate", "3", "--address", addr1)
        self._wait_tip_at_least(self.node1_dir, self.node1_rpc, 104)
        old_node1_tip = self._cli(self.node1_dir, self.node1_rpc, "getbestblockhash").splitlines()[-1]

        # 3) Start node2 isolated (no addnode) from shared ancestor and mine heavier branch to 106.
        conf2_isolated = self._write_conf(
            self.node2_dir, self.node2_rpc, self.node2_p2p, None, mining=True
        )
        self.node2 = self._start_node(self.node2_dir, conf2_isolated)
        self._wait_rpc(self.node2_dir, self.node2_rpc)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 101)
        self._cli(self.node2_dir, self.node2_rpc, "generate", "5", "--address", addr2)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 106)
        node2_tip = self._cli(self.node2_dir, self.node2_rpc, "getbestblockhash").splitlines()[-1]

        # 4) Reconnect node2 to node1 and verify node1 reorgs to node2's heavier chain across activation boundary.
        self._stop_proc(self.node2)
        self.node2 = self._start_node(self.node2_dir, conf2_connected)
        self._wait_rpc(self.node2_dir, self.node2_rpc)

        self._wait_tip_at_least(self.node1_dir, self.node1_rpc, 106)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 106)

        final1 = self._cli(self.node1_dir, self.node1_rpc, "getbestblockhash").splitlines()[-1]
        final2 = self._cli(self.node2_dir, self.node2_rpc, "getbestblockhash").splitlines()[-1]
        h1 = int(self._cli(self.node1_dir, self.node1_rpc, "getblockcount").splitlines()[-1])
        h2 = int(self._cli(self.node2_dir, self.node2_rpc, "getblockcount").splitlines()[-1])

        self.assertEqual(h1, 106)
        self.assertEqual(h2, 106)
        self.assertEqual(final1, final2)
        self.assertEqual(final1, node2_tip)
        self.assertNotEqual(final1, old_node1_tip)

    def test_three_node_rejoin_after_activation_reorg(self) -> None:
        # 1) Start three connected nodes and sync common history.
        conf1_connected = self._write_conf(
            self.node1_dir, self.node1_rpc, self.node1_p2p, self.node2_p2p, mining=True
        )
        conf2_connected = self._write_conf(
            self.node2_dir, self.node2_rpc, self.node2_p2p, self.node1_p2p, mining=True
        )
        conf3_connected = self._write_conf(
            self.node3_dir, self.node3_rpc, self.node3_p2p, self.node1_p2p, mining=True
        )

        self.node1 = self._start_node(self.node1_dir, conf1_connected)
        self.node2 = self._start_node(self.node2_dir, conf2_connected)
        self.node3 = self._start_node(self.node3_dir, conf3_connected)
        self._wait_rpc(self.node1_dir, self.node1_rpc)
        self._wait_rpc(self.node2_dir, self.node2_rpc)
        self._wait_rpc(self.node3_dir, self.node3_rpc)

        addr1 = self._cli(self.node1_dir, self.node1_rpc, "getnewaddress").splitlines()[-1]
        addr2 = self._cli(self.node2_dir, self.node2_rpc, "getnewaddress").splitlines()[-1]
        addr3 = self._cli(self.node3_dir, self.node3_rpc, "getnewaddress").splitlines()[-1]
        self._cli(self.node1_dir, self.node1_rpc, "setminingaddress", addr1)
        self._cli(self.node2_dir, self.node2_rpc, "setminingaddress", addr2)
        self._cli(self.node3_dir, self.node3_rpc, "setminingaddress", addr3)

        self._cli(self.node1_dir, self.node1_rpc, "generate", "101", "--address", addr1)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 101)
        self._wait_tip_at_least(self.node3_dir, self.node3_rpc, 101)

        # 2) Isolate node2 and node3; node1 mines short branch to 104.
        self._stop_proc(self.node2)
        self._stop_proc(self.node3)
        self.node2 = None
        self.node3 = None

        self._cli(self.node1_dir, self.node1_rpc, "generate", "3", "--address", addr1)
        self._wait_tip_at_least(self.node1_dir, self.node1_rpc, 104)

        # 3) Node2 mines heavier competing branch in isolation to 106.
        conf2_isolated = self._write_conf(
            self.node2_dir, self.node2_rpc, self.node2_p2p, None, mining=True
        )
        self.node2 = self._start_node(self.node2_dir, conf2_isolated)
        self._wait_rpc(self.node2_dir, self.node2_rpc)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 101)
        self._cli(self.node2_dir, self.node2_rpc, "generate", "5", "--address", addr2)
        self._wait_tip_at_least(self.node2_dir, self.node2_rpc, 106)
        node2_tip = self._cli(self.node2_dir, self.node2_rpc, "getbestblockhash").splitlines()[-1]

        # 4) Reconnect node2 and verify node1 reorgs to the heavier branch.
        self._stop_proc(self.node2)
        self.node2 = self._start_node(self.node2_dir, conf2_connected)
        self._wait_rpc(self.node2_dir, self.node2_rpc)
        self._wait_tip_at_least(self.node1_dir, self.node1_rpc, 106)

        # 5) Bring node3 back and confirm it converges to the post-reorg tip.
        self.node3 = self._start_node(self.node3_dir, conf3_connected)
        self._wait_rpc(self.node3_dir, self.node3_rpc)
        self._wait_tip_at_least(self.node3_dir, self.node3_rpc, 106)

        final1 = self._cli(self.node1_dir, self.node1_rpc, "getbestblockhash").splitlines()[-1]
        final2 = self._cli(self.node2_dir, self.node2_rpc, "getbestblockhash").splitlines()[-1]
        final3 = self._cli(self.node3_dir, self.node3_rpc, "getbestblockhash").splitlines()[-1]
        h1 = int(self._cli(self.node1_dir, self.node1_rpc, "getblockcount").splitlines()[-1])
        h2 = int(self._cli(self.node2_dir, self.node2_rpc, "getblockcount").splitlines()[-1])
        h3 = int(self._cli(self.node3_dir, self.node3_rpc, "getblockcount").splitlines()[-1])

        self.assertEqual(h1, 106)
        self.assertEqual(h2, 106)
        self.assertEqual(h3, 106)
        self.assertEqual(final1, final2)
        self.assertEqual(final2, final3)
        self.assertEqual(final1, node2_tip)


if __name__ == "__main__":
    unittest.main()
