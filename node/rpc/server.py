"""JSON-RPC server for BerzCoin."""

import json
import asyncio
import base64
import time
import traceback
import os
from typing import TYPE_CHECKING, Any, Dict, Optional, Callable, Awaitable, Union

from aiohttp import web

from shared.utils.logging import get_logger
from .auth import AuthManager

if TYPE_CHECKING:
    from node.app.config import Config

logger = get_logger()

HandlerType = Callable[..., Awaitable[Any]]

# RPC API version exposed on /health only (non-sensitive).
RPC_SERVER_VERSION = "1.0.0"


class RPCServer:
    """JSON-RPC 2.0 server."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8332,
        rpc_dir: str = "~/.berzcoin",
        config: Optional["Config"] = None,
    ):
        """Initialize RPC server.

        Args:
            host: Listen address.
            port: Listen port.
            rpc_dir: Data directory used for RPC cookie and auth files.
            config: Node configuration; when set, ``rpcallowip`` is enforced per request.
        """
        self.host = host
        self.port = port
        self.config: Optional["Config"] = config
        self.auth = AuthManager(os.path.expanduser(rpc_dir))
        self.handlers: Dict[str, HandlerType] = {}
        self.app: Optional[web.Application] = None
        self.runner: Optional[web.AppRunner] = None

    def register_handler(self, method: str, handler: HandlerType) -> None:
        """Register RPC method handler."""
        self.handlers[method] = handler
        logger.debug(f"Registered RPC method: {method}")

    def register_handlers(self, handlers: Dict[str, HandlerType]) -> None:
        """Register multiple handlers."""
        for method, handler in handlers.items():
            self.register_handler(method, handler)

    async def start(self) -> None:
        """Start RPC server."""
        self.app = web.Application()
        self.app.router.add_post('/', self._handle_request)
        self.app.router.add_get('/health', self._handle_health)

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()

        site = web.TCPSite(self.runner, self.host, self.port)
        await site.start()

        logger.info(f"RPC server started on {self.host}:{self.port}")

    async def stop(self) -> None:
        """Stop RPC server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("RPC server stopped")

    def _client_ip(self, request: web.Request) -> str:
        peer = request.remote
        if peer is None:
            return "127.0.0.1"
        if peer.startswith("::ffff:"):
            return peer[7:]
        return peer

    def _rpc_ip_allowed(self, request: web.Request) -> bool:
        if self.config is None:
            return True
        return self.config.is_rpc_allowed(self._client_ip(request))

    async def _handle_request(self, request: web.Request) -> web.Response:
        """Handle JSON-RPC request with IP filtering, then HTTP Basic auth."""
        if self.config is not None and not self._rpc_ip_allowed(request):
            client_ip = self._client_ip(request)
            logger.warning("RPC connection denied from %s", client_ip)
            return web.json_response(
                {
                    'jsonrpc': '2.0',
                    'error': {'code': -32000, 'message': 'Connection refused'},
                    'id': None,
                },
                status=403,
            )
        auth_header = request.headers.get('Authorization')
        if not self._authenticate(auth_header):
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -32000, 'message': 'Unauthorized'}, 'id': None},
                status=401
            )

        try:
            body = await request.json()

            if isinstance(body, list):
                responses = []
                for req in body:
                    response = await self._process_request(req)
                    responses.append(response)
                return web.json_response(responses)

            response = await self._process_request(body)
            return web.json_response(response)

        except json.JSONDecodeError:
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -32700, 'message': 'Parse error'}, 'id': None},
                status=400
            )
        except Exception as e:
            logger.error(f"RPC error: {e}\n{traceback.format_exc()}")
            return web.json_response(
                {'jsonrpc': '2.0', 'error': {'code': -32603, 'message': str(e)}, 'id': None},
                status=500
            )

    async def _process_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Process single JSON-RPC request."""
        if not isinstance(req, dict):
            return {'jsonrpc': '2.0', 'error': {'code': -32600, 'message': 'Invalid Request'}, 'id': None}

        if req.get('jsonrpc') != '2.0':
            return {
                'jsonrpc': '2.0',
                'error': {'code': -32600, 'message': 'Invalid Request'},
                'id': req.get('id')
            }

        method = req.get('method')
        params = req.get('params', [])
        req_id = req.get('id')

        if not method:
            return {
                'jsonrpc': '2.0',
                'error': {'code': -32600, 'message': 'Method not specified'},
                'id': req_id
            }

        handler = self.handlers.get(str(method))
        if not handler:
            return {
                'jsonrpc': '2.0',
                'error': {'code': -32601, 'message': f'Method not found: {method}'},
                'id': req_id
            }

        try:
            start_time = time.time()

            if isinstance(params, dict):
                result = await handler(**params)
            elif isinstance(params, list):
                result = await handler(*params)
            else:
                result = await handler()

            logger.debug(f"RPC {method} completed in {(time.time() - start_time) * 1000:.2f}ms")

            return {'jsonrpc': '2.0', 'result': result, 'id': req_id}

        except Exception as e:
            logger.error(f"RPC method {method} failed: {e}")
            return {
                'jsonrpc': '2.0',
                'error': {'code': -32000, 'message': str(e)},
                'id': req_id
            }

    async def _handle_health(self, request: web.Request) -> web.Response:
        """Liveness probe: status, version, timestamp only (no chain, peers, or wallet info)."""
        if self.config is not None and not self._rpc_ip_allowed(request):
            client_ip = self._client_ip(request)
            logger.warning("RPC health denied from %s", client_ip)
            return web.json_response(
                {'error': {'code': -32000, 'message': 'Connection refused'}},
                status=403,
            )
        return web.json_response(
            {
                "status": "ok",
                "version": RPC_SERVER_VERSION,
                "timestamp": int(time.time()),
            }
        )

    def _authenticate(self, auth_header: Optional[str]) -> bool:
        """Authenticate HTTP Basic against cookie or user db."""
        if not auth_header:
            return False

        if auth_header.startswith('Basic '):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode()
                username, password = decoded.split(':', 1)
                return self.auth.authenticate(username, password)
            except (ValueError, UnicodeDecodeError, IndexError):
                return False

        return False
