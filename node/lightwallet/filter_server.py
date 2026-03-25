"""Compact filter server for light clients."""

import asyncio
import struct
from typing import Any, Dict, List, Optional

from shared.core.block import Block
from shared.core.hashes import hash256
from shared.utils.logging import get_logger
from node.chain.chainstate import ChainState
from node.storage.blocks_store import BlocksStore
from .cfilters import CompactFilter

logger = get_logger()


class FilterServer:
    """Serve compact filters to light clients."""

    def __init__(
        self,
        chainstate: ChainState,
        blocks_store: BlocksStore,
        host: str = "0.0.0.0",
        port: int = 8334,
    ):
        self.chainstate = chainstate
        self.blocks_store = blocks_store
        self.host = host
        self.port = port

        self.filters: Dict[int, CompactFilter] = {}
        self.filter_headers: Dict[int, bytes] = {}

        self.server: Optional[Any] = None
        self.running = False

    async def start(self) -> None:
        self.running = True
        await self._precompute_filters()
        self.server = await asyncio.start_server(self._handle_client, self.host, self.port)
        logger.info("Filter server started on %s:%s", self.host, self.port)

    async def stop(self) -> None:
        self.running = False
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        logger.info("Filter server stopped")

    async def _precompute_filters(self) -> None:
        best_height = self.chainstate.get_best_height()
        for height in range(0, best_height + 1):
            if height in self.filters:
                continue
            block = self.blocks_store.read_block(height)
            if block:
                await self._compute_filter(block, height)
        logger.info("Precomputed %s filters", len(self.filters))

    async def _compute_filter(self, block: Block, height: int) -> None:
        cf = CompactFilter(block.header.hash())
        payload = cf.build_from_block(block)
        self.filters[height] = cf
        self.filter_headers[height] = hash256(payload)
        logger.debug("Computed filter for block %s", height)

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = writer.get_extra_info("peername")
        client_ip = peer[0] if peer else "?"
        logger.info("Filter client connected from %s", client_ip)
        try:
            while self.running:
                command = await reader.read(1)
                if not command:
                    break
                if command == b"g":
                    await self._handle_get_filter(reader, writer)
                elif command == b"h":
                    await self._handle_get_header(reader, writer)
                elif command == b"m":
                    await self._handle_get_headers(reader, writer)
                else:
                    writer.write(b"e")
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Error handling client: %s", e)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _handle_get_filter(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        height_data = await reader.read(4)
        if len(height_data) < 4:
            return
        height = struct.unpack("<I", height_data)[0]
        cf = self.filters.get(height)
        if not cf:
            writer.write(b"e")
            await writer.drain()
            return
        filter_data = cf.serialize()
        writer.write(struct.pack("<I", len(filter_data)))
        writer.write(filter_data)
        await writer.drain()

    async def _handle_get_header(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        height_data = await reader.read(4)
        if len(height_data) < 4:
            return
        height = struct.unpack("<I", height_data)[0]
        header = self.filter_headers.get(height)
        if not header:
            writer.write(b"e")
            await writer.drain()
            return
        writer.write(header)
        await writer.drain()

    async def _handle_get_headers(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        data = await reader.read(8)
        if len(data) < 8:
            return
        start_height = struct.unpack("<I", data[:4])[0]
        count = struct.unpack("<I", data[4:8])[0]
        for i in range(count):
            height = start_height + i
            hdr = self.filter_headers.get(height) or (b"\x00" * 32)
            writer.write(hdr)
        await writer.drain()

    def get_filter(self, height: int) -> Optional[CompactFilter]:
        return self.filters.get(height)

    def get_filter_header(self, height: int) -> Optional[bytes]:
        return self.filter_headers.get(height)

    def get_filter_headers_range(self, start_height: int, count: int) -> List[bytes]:
        headers: List[bytes] = []
        for i in range(count):
            height = start_height + i
            headers.append(self.filter_headers.get(height) or (b"\x00" * 32))
        return headers

    async def add_block(self, block: Block, height: int) -> None:
        await self._compute_filter(block, height)
        logger.debug("Added filter for new block %s", height)

    def get_stats(self) -> Dict[str, int]:
        return {
            "total_filters": len(self.filters),
            "total_headers": len(self.filter_headers),
            "filter_count": len(self.filters),
        }
