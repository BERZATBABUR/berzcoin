"""Stratum mining server."""

import asyncio
import json
import time
import secrets
from typing import Dict, Optional, Any, List
from dataclasses import dataclass, field

from shared.utils.logging import get_logger
from .block_assembler import BlockAssembler

logger = get_logger()


@dataclass
class Miner:
    """Connected miner."""

    id: str
    worker_name: str
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    subscribed: bool = False
    authorized: bool = False
    difficulty: float = 1.0
    shares: int = 0
    accepted_shares: int = 0
    rejected_shares: int = 0
    last_share_time: float = 0
    extra_nonce1: str = field(default_factory=lambda: secrets.token_hex(8))


@dataclass
class Job:
    """Mining job."""

    job_id: str
    block_template: Dict[str, Any]
    created_at: float
    height: int
    difficulty: float


class StratumServer:
    """Stratum mining protocol server (subset for testing)."""

    def __init__(
        self,
        block_assembler: BlockAssembler,
        host: str = "0.0.0.0",
        port: int = 3333,
        share_difficulty: float = 1.0,
    ):
        self.block_assembler = block_assembler
        self.host = host
        self.port = port
        self.share_difficulty = share_difficulty

        self.miners: Dict[str, Miner] = {}
        self.jobs: Dict[str, Job] = {}
        self.current_job: Optional[Job] = None

        self.server: Optional[asyncio.AbstractServer] = None
        self.running = False
        self.job_update_task: Optional[asyncio.Task] = None

        self.job_id_counter = 0
        self.job_update_interval = 30

    async def start(self) -> None:
        self.running = True
        self.server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )

        self.job_update_task = asyncio.create_task(self._update_jobs())

        logger.info(f"Stratum server started on {self.host}:{self.port}")

    async def stop(self) -> None:
        self.running = False

        if self.job_update_task:
            self.job_update_task.cancel()
            try:
                await self.job_update_task
            except asyncio.CancelledError:
                pass

        for miner in self.miners.values():
            try:
                miner.writer.close()
                await miner.writer.wait_closed()
            except OSError:
                pass

        if self.server:
            self.server.close()
            await self.server.wait_closed()

        logger.info("Stratum server stopped")

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info('peername')
        client_ip = peer[0] if peer else "unknown"

        logger.info(f"New miner connection from {client_ip}")

        miner = Miner(
            id=f"{client_ip}:{peer[1]}" if peer and len(peer) > 1 else str(client_ip),
            worker_name="unknown",
            reader=reader,
            writer=writer,
        )
        self.miners[miner.id] = miner

        try:
            while self.running:
                data = await reader.readline()
                if not data:
                    break

                await self._handle_message(miner, data.decode().strip())

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error handling miner {miner.id}: {e}")
        finally:
            self.miners.pop(miner.id, None)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            logger.info(f"Miner {miner.id} disconnected")

    async def _handle_message(self, miner: Miner, message: str) -> None:
        try:
            data = json.loads(message)
            method = data.get('method')
            params = data.get('params', [])
            msg_id = data.get('id')

            if method == 'mining.subscribe':
                await self._handle_subscribe(miner, msg_id)
            elif method == 'mining.authorize':
                await self._handle_authorize(miner, params, msg_id)
            elif method == 'mining.submit':
                await self._handle_submit(miner, params, msg_id)
            elif method == 'mining.configure':
                await self._handle_configure(miner, params, msg_id)
            else:
                await self._send_error(miner, msg_id, f"Unknown method: {method}")

        except json.JSONDecodeError:
            logger.error(f"Invalid JSON from miner {miner.id}: {message[:200]}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")

    async def _handle_subscribe(self, miner: Miner, msg_id: Any) -> None:
        miner.subscribed = True

        response = {
            'id': msg_id,
            'result': [
                [
                    ['mining.notify', 'sub1'],
                    ['mining.set_difficulty', 'sub2'],
                ],
                miner.extra_nonce1,
                8,
            ],
            'error': None,
        }

        await self._send_response(miner, response)
        await self._send_difficulty(miner, self.share_difficulty)

        if self.current_job:
            await self._send_job(miner, self.current_job)

    async def _handle_authorize(self, miner: Miner, params: List[Any], msg_id: Any) -> None:
        if len(params) >= 1:
            miner.worker_name = str(params[0])
            miner.authorized = True

            response = {'id': msg_id, 'result': True, 'error': None}
            await self._send_response(miner, response)

            logger.info(f"Miner {miner.id} authorized as {miner.worker_name}")
        else:
            await self._send_error(miner, msg_id, "Invalid authorization parameters")

    async def _handle_submit(self, miner: Miner, params: List[Any], msg_id: Any) -> None:
        if not miner.authorized:
            await self._send_error(miner, msg_id, "Not authorized")
            return

        try:
            _worker_name = params[0]
            job_id = params[1]
            extra_nonce2 = params[2]
            ntime = params[3]
            nonce = params[4]

            job = self.jobs.get(str(job_id))
            if not job:
                await self._send_error(miner, msg_id, "Job not found")
                return

            valid, is_block = await self._validate_share(
                miner, job, extra_nonce2, ntime, nonce
            )

            if valid:
                miner.accepted_shares += 1
                miner.shares += 1
                miner.last_share_time = time.time()

                response = {'id': msg_id, 'result': True, 'error': None}
                await self._send_response(miner, response)

                if is_block:
                    logger.info(
                        f"Block found by {miner.worker_name} at height {job.height}"
                    )
                    await self._submit_block(job, extra_nonce2, ntime, nonce)
            else:
                miner.rejected_shares += 1
                await self._send_error(miner, msg_id, "Invalid share")

        except (IndexError, TypeError) as e:
            await self._send_error(miner, msg_id, str(e))
        except Exception as e:
            logger.error(f"Error processing submit: {e}")
            await self._send_error(miner, msg_id, str(e))

    async def _handle_configure(self, miner: Miner, params: List[Any], msg_id: Any) -> None:
        _ = params
        response = {
            'id': msg_id,
            'result': {
                'version': '1.0.0',
                'minimum-difficulty': self.share_difficulty,
            },
            'error': None,
        }
        await self._send_response(miner, response)

    async def _validate_share(
        self,
        miner: Miner,
        job: Job,
        extra_nonce2: str,
        ntime: str,
        nonce: str,
    ) -> tuple:
        _ = miner, job, extra_nonce2, ntime, nonce
        # Stub: real implementation would rebuild header and check target / share difficulty
        return True, False

    async def _send_difficulty(self, miner: Miner, difficulty: float) -> None:
        message = {
            'id': None,
            'method': 'mining.set_difficulty',
            'params': [difficulty],
        }
        await self._send_response(miner, message)
        miner.difficulty = difficulty

    async def _send_job(self, miner: Miner, job: Job) -> None:
        block_template = job.block_template

        job_params = [
            job.job_id,
            block_template['previousblockhash'],
            block_template['coinbase_tx'],
            block_template['merkleroot'],
            block_template['bits'],
            hex(block_template['curtime']),
            True,
        ]

        message = {
            'id': None,
            'method': 'mining.notify',
            'params': job_params,
        }

        await self._send_response(miner, message)

    async def _send_response(self, miner: Miner, response: Dict[str, Any]) -> None:
        try:
            data = json.dumps(response) + "\n"
            miner.writer.write(data.encode())
            await miner.writer.drain()
        except OSError as e:
            logger.error(f"Error sending to {miner.id}: {e}")

    async def _send_error(self, miner: Miner, msg_id: Any, message: str) -> None:
        response = {
            'id': msg_id,
            'result': None,
            'error': [20, message, None],
        }
        await self._send_response(miner, response)

    async def _update_jobs(self) -> None:
        while self.running:
            try:
                await self._create_new_job()
                await asyncio.sleep(self.job_update_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error updating jobs: {e}")
                await asyncio.sleep(5)

    async def _create_new_job(self) -> None:
        addr = getattr(self.block_assembler, 'coinbase_address', None)
        template = await self.block_assembler.create_block_template(addr)

        self.job_id_counter += 1
        job = Job(
            job_id=str(self.job_id_counter),
            block_template=template,
            created_at=time.time(),
            height=int(template['height']),
            difficulty=self.share_difficulty,
        )

        self.jobs[job.job_id] = job
        self.current_job = job

        self._clean_old_jobs()

        for miner in self.miners.values():
            if miner.authorized:
                await self._send_job(miner, job)

        logger.debug(f"New job created: {job.job_id} at height {job.height}")

    def _clean_old_jobs(self) -> None:
        now = time.time()
        cur_id = self.current_job.job_id if self.current_job else None
        stale = [
            jid for jid, job in self.jobs.items()
            if now - job.created_at > 3600 and jid != cur_id
        ]
        for jid in stale:
            self.jobs.pop(jid, None)

    async def _submit_block(self, job: Job, extra_nonce2: str, ntime: str, nonce: str) -> None:
        _ = extra_nonce2, ntime, nonce
        logger.info(f"Submitting block at height {job.height} (stub)")

    def get_stats(self) -> Dict[str, Any]:
        total_shares = sum(m.shares for m in self.miners.values())
        total_accepted = sum(m.accepted_shares for m in self.miners.values())
        total_rejected = sum(m.rejected_shares for m in self.miners.values())

        return {
            'running': self.running,
            'connected_miners': len(self.miners),
            'active_jobs': len(self.jobs),
            'current_job_id': self.current_job.job_id if self.current_job else None,
            'total_shares': total_shares,
            'total_accepted': total_accepted,
            'total_rejected': total_rejected,
            'acceptance_rate': total_accepted / total_shares if total_shares > 0 else 0,
            'miners': [
                {
                    'id': m.id,
                    'worker': m.worker_name,
                    'difficulty': m.difficulty,
                    'shares': m.shares,
                    'accepted': m.accepted_shares,
                    'rejected': m.rejected_shares,
                    'last_share': m.last_share_time,
                }
                for m in self.miners.values()
            ],
        }
