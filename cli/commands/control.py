"""Control CLI commands."""

import argparse
from typing import Any, Optional


class ControlCommands:
    """Control CLI commands."""

    def __init__(self, handler: Any):
        self.handler = handler

    @staticmethod
    def add_parser(subparsers: argparse._SubParsersAction) -> None:
        p = subparsers.add_parser('getinfo', help='Get node info')
        p.set_defaults(command='getinfo')

        p = subparsers.add_parser('stop', help='Stop node')
        p.set_defaults(command='stop')

        p = subparsers.add_parser('nodehelp', help='RPC help (named nodehelp to avoid argparse help)')
        p.add_argument('rpccommand', nargs='?', help='Command name')
        p.set_defaults(command='nodehelp')

        p = subparsers.add_parser('ping', help='Ping node')
        p.set_defaults(command='ping')

        p = subparsers.add_parser('uptime', help='Get node uptime')
        p.set_defaults(command='uptime')

        p = subparsers.add_parser('getnetworkinfo', help='Get network info')
        p.set_defaults(command='getnetworkinfo')

    async def get_info(self):
        return await self.handler.call('get_info')

    async def stop(self):
        return await self.handler.call('stop')

    async def help(self, command: Optional[str] = None):
        return await self.handler.call('help', command)

    async def ping(self):
        return await self.handler.call('ping')

    async def uptime(self):
        return await self.handler.call('uptime')

    async def get_network_info(self):
        return await self.handler.call('get_network_info')
