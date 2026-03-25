"""Component lifecycle management."""

from typing import Dict, List, Optional

from shared.utils.logging import get_logger

logger = get_logger()


class Component:
    """Base component class."""

    def __init__(self, name: str):
        self.name = name
        self.is_initialized = False
        self.is_running = False

    async def initialize(self) -> bool:
        self.is_initialized = True
        return True

    async def start(self) -> bool:
        self.is_running = True
        return True

    async def stop(self) -> bool:
        self.is_running = False
        return True

    async def health_check(self) -> bool:
        return self.is_running

    def get_stats(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "initialized": self.is_initialized,
            "running": self.is_running,
        }


class ComponentManager:
    """Manage component lifecycle."""

    def __init__(self) -> None:
        self.components: Dict[str, Component] = {}
        self.dependencies: Dict[str, List[str]] = {}
        self._start_order: List[str] = []

    def register(self, component: Component, dependencies: Optional[List[str]] = None) -> None:
        self.components[component.name] = component
        self.dependencies[component.name] = dependencies or []
        logger.info("Registered component: %s", component.name)

    async def initialize_all(self) -> bool:
        order = self._resolve_dependencies()
        for name in order:
            comp = self.components.get(name)
            if comp:
                logger.info("Initializing component: %s", name)
                if not await comp.initialize():
                    logger.error("Failed to initialize %s", name)
                    return False
        return True

    async def start_all(self) -> bool:
        if not self._start_order:
            self._resolve_dependencies()
        for name in self._start_order:
            comp = self.components.get(name)
            if comp:
                logger.info("Starting component: %s", name)
                if not await comp.start():
                    logger.error("Failed to start %s", name)
                    return False
        return True

    async def stop_all(self) -> None:
        if not self._start_order:
            self._resolve_dependencies()
        for name in reversed(self._start_order):
            comp = self.components.get(name)
            if comp and comp.is_running:
                logger.info("Stopping component: %s", name)
                await comp.stop()

    async def health_check(self) -> Dict[str, bool]:
        return {name: await c.health_check() for name, c in self.components.items()}

    def _resolve_dependencies(self) -> List[str]:
        visited: set = set()
        order: List[str] = []

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            for dep in self.dependencies.get(name, []):
                if dep in self.components:
                    visit(dep)
            order.append(name)

        for name in self.components:
            visit(name)

        self._start_order = order
        return order

    def get_component(self, name: str) -> Optional[Component]:
        return self.components.get(name)

    def get_all_components(self) -> List[str]:
        return list(self.components.keys())

    def get_stats(self) -> Dict[str, object]:
        return {
            "total_components": len(self.components),
            "running_components": sum(
                1 for c in self.components.values() if c.is_running
            ),
            "components": {n: c.get_stats() for n, c in self.components.items()},
        }
