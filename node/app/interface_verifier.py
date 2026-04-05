"""Run end-to-end two-node verification flow for dashboard interface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class _NodeProc:
    proc: subprocess.Popen
    datadir: Path
    rpcport: int


class TwoNodeFlowVerifier:
    """Spawns two local nodes and verifies propagation + mempool confirmation flow."""

    def __init__(self) -> None:
        self.repo_root = Path(__file__).resolve().parents[2]
        self.py = os.environ.get("PYTHON", "python3")
        self.wallet_passphrase = "ui-test-passphrase"

    def run(self, timeout_secs: int = 180) -> Dict[str, object]:
        started_at = time.time()
        steps: List[Dict[str, object]] = []
        base = Path(tempfile.mkdtemp(prefix="berzcoin-ui-verify-"))
        node1: Optional[_NodeProc] = None
        node2: Optional[_NodeProc] = None
        try:
            node1_dir = base / "node1"
            node2_dir = base / "node2"
            node1_dir.mkdir(parents=True, exist_ok=True)
            node2_dir.mkdir(parents=True, exist_ok=True)

            node1_rpc, node2_rpc = 19643, 19644
            node1_p2p, node2_p2p = 19645, 19646

            conf1 = self._write_conf(node1_dir, node1_rpc, node1_p2p, node2_p2p, mining=True)
            conf2 = self._write_conf(node2_dir, node2_rpc, node2_p2p, node1_p2p, mining=False)

            node1 = self._start_node(node1_dir, conf1, node1_rpc)
            node2 = self._start_node(node2_dir, conf2, node2_rpc)
            self._wait_rpc(node1, timeout_secs=30)
            self._wait_rpc(node2, timeout_secs=30)
            steps.append({"name": "starts 2 real node processes", "ok": True})

            wallet1 = json.loads(self._cli(node1, "createwallet", "default"))
            self._cli(node1, "activatewallet", wallet1["private_key"])
            wallet2 = json.loads(self._cli(node2, "createwallet", "default"))
            self._cli(node2, "activatewallet", wallet2["private_key"])
            info1 = json.loads(self._cli(node1, "getwalletinfo"))
            if "private_key" in info1 or "seed_phrase" in info1:
                raise RuntimeError("wallet info unexpectedly exposed secrets")
            addr2 = self._last_line(self._cli(node2, "getnewaddress"))
            steps.append({"name": "activates wallets on both (secret-safe wallet info)", "ok": True})

            addr1 = self._last_line(self._cli(node1, "getnewaddress"))
            self._cli(node1, "setminingaddress", addr1)
            self._cli(node1, "generate", "101", "--address", addr1)
            self._wait_tip(node2, target=101, timeout_secs=60)
            steps.append({"name": "mines/funds on node1", "ok": True, "detail": f"node1 address {addr1}"})

            self._cli(node1, "walletlock")
            locked_send = self._cli_try(node1, "sendtoaddress", addr2, "0.01")
            if locked_send.returncode == 0:
                raise RuntimeError("walletlock check failed: sendtoaddress succeeded while locked")
            if "Wallet is locked" not in locked_send.stderr:
                raise RuntimeError(
                    "walletlock check failed: expected 'Wallet is locked' rejection reason"
                )
            unlock = self._wallet_unlock(node1, timeout_secs=120)
            if not unlock:
                raise RuntimeError("walletpassphrase did not unlock wallet")
            steps.append({"name": "wallet lock/unlock flow enforced for signing", "ok": True})

            sent = self._last_line(self._cli(node1, "sendtoaddress", addr2, "1.0"))
            txid = sent.replace("Transaction sent:", "").strip()
            if not txid:
                raise RuntimeError("sendtoaddress did not return txid")
            steps.append({"name": "sends tx from node1 to node2 address", "ok": True, "detail": txid})

            mempool_before = json.loads(self._cli(node1, "getrawmempool"))
            if txid not in mempool_before:
                raise RuntimeError("transaction not found in node1 mempool before confirmation")
            steps.append({"name": "confirms tx appears in node1 mempool", "ok": True})

            self._cli(node1, "generate", "1", "--address", addr1)
            steps.append({"name": "mines a confirming block", "ok": True})

            self._wait_tip(node2, target=102, timeout_secs=60)
            mempool_after = json.loads(self._cli(node1, "getrawmempool"))
            if txid in mempool_after:
                raise RuntimeError("transaction still present in mempool after confirmation")
            steps.append(
                {
                    "name": "confirms tx is evicted from node1 mempool after confirmation (with node2 synced)",
                    "ok": True,
                }
            )

            self._cli(node1, "generate", "1", "--address", addr1)
            self._wait_tip(node2, target=103, timeout_secs=60)
            steps.append(
                {
                    "name": "crosses configured soft/hard activation heights with both nodes synced",
                    "ok": True,
                }
            )

            return {
                "ok": True,
                "steps": steps,
                "duration_secs": round(time.time() - started_at, 2),
                "base_dir": str(base),
            }
        except Exception as e:
            steps.append({"name": "flow failed", "ok": False, "detail": str(e)})
            return {
                "ok": False,
                "steps": steps,
                "duration_secs": round(time.time() - started_at, 2),
                "base_dir": str(base),
                "error": str(e),
            }
        finally:
            self._stop_node(node1)
            self._stop_node(node2)
            shutil.rmtree(base, ignore_errors=True)

    def _write_conf(
        self,
        datadir: Path,
        rpcport: int,
        p2p_port: int,
        addnode_port: int,
        mining: bool,
    ) -> Path:
        conf = datadir / "berzcoin.conf"
        conf.write_text(
            "\n".join(
                [
                    "[main]",
                    "network = regtest",
                    f"datadir = {datadir}",
                    "disablewallet = false",
                    "rpcbind = 127.0.0.1",
                    f"rpcport = {rpcport}",
                    "rpcallowip = 127.0.0.1",
                    "bind = 127.0.0.1",
                    f"port = {p2p_port}",
                    f"addnode = 127.0.0.1:{addnode_port}",
                    f"mining = {'true' if mining else 'false'}",
                    "autominer = false",
                    "miningaddress =",
                    "debug = true",
                    f"wallet_encryption_passphrase = {self.wallet_passphrase}",
                    "wallet_debug_secrets = false",
                    "activation_height_berz_softfork_bip34_strict = 102",
                    "activation_height_berz_hardfork_tx_v2 = 103",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        return conf

    def _start_node(self, datadir: Path, conf: Path, rpcport: int) -> _NodeProc:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        proc = subprocess.Popen(
            [self.py, "-m", "node.app.main", "--regtest", "-datadir", str(datadir), "-conf", str(conf)],
            cwd=str(self.repo_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return _NodeProc(proc=proc, datadir=datadir, rpcport=rpcport)

    def _stop_node(self, node: Optional[_NodeProc]) -> None:
        if node is None:
            return
        if node.proc.poll() is not None:
            return
        node.proc.terminate()
        try:
            node.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            node.proc.kill()

    def _cli(self, node: _NodeProc, *args: str) -> str:
        proc = self._cli_try(node, *args, check=True)
        return proc.stdout.strip()

    def _cli_try(self, node: _NodeProc, *args: str, check: bool = False) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.repo_root)
        proc = subprocess.run(
            [self.py, "-m", "cli.main", "-datadir", str(node.datadir), "-rpcport", str(node.rpcport), *args],
            cwd=str(self.repo_root),
            env=env,
            check=check,
            capture_output=True,
            text=True,
        )
        return proc

    def _wallet_unlock(self, node: _NodeProc, timeout_secs: int) -> bool:
        passphrases = [self.wallet_passphrase, "berzcoin-dev-insecure-passphrase"]
        for phrase in passphrases:
            result = self._cli_try(
                node,
                "walletpassphrase",
                phrase,
                str(int(timeout_secs)),
                check=False,
            )
            if result.returncode != 0:
                continue
            try:
                payload = json.loads(result.stdout.strip() or "{}")
            except Exception:
                continue
            if str(payload.get("status", "")) == "unlocked":
                return True
        return False

    def _wait_rpc(self, node: _NodeProc, timeout_secs: int) -> None:
        end = time.time() + timeout_secs
        while time.time() < end:
            if node.proc.poll() is not None:
                raise RuntimeError(f"node process exited early (rpcport={node.rpcport})")
            try:
                self._cli(node, "getblockcount")
                return
            except Exception:
                time.sleep(0.25)
        raise RuntimeError(f"rpc not ready on port {node.rpcport}")

    def _wait_tip(self, node: _NodeProc, target: int, timeout_secs: int) -> None:
        end = time.time() + timeout_secs
        while time.time() < end:
            if node.proc.poll() is not None:
                raise RuntimeError(f"node process exited early while waiting tip (rpcport={node.rpcport})")
            try:
                tip = int(self._last_line(self._cli(node, "getblockcount")))
                if tip >= target:
                    return
            except Exception:
                pass
            time.sleep(0.5)
        raise RuntimeError(f"node at rpcport {node.rpcport} did not reach tip {target}")

    @staticmethod
    def _last_line(text: str) -> str:
        lines = [ln for ln in text.splitlines() if ln.strip()]
        return lines[-1] if lines else ""
