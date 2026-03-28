"""Stack operations for Bitcoin script."""

from typing import List, Optional

class Stack:
    """Stack for script execution."""

    def __init__(self):
        self._items: List[bytes] = []
        self._altstack: List[bytes] = []

    def push(self, item: bytes) -> None:
        self._items.append(item)

    def pop(self) -> Optional[bytes]:
        if not self._items:
            return None
        return self._items.pop()

    def peek(self, index: int = -1) -> Optional[bytes]:
        try:
            return self._items[index]
        except IndexError:
            return None

    def top(self) -> Optional[bytes]:
        return self.peek(-1)

    def size(self) -> int:
        return len(self._items)

    def is_empty(self) -> bool:
        return len(self._items) == 0

    def clear(self) -> None:
        self._items.clear()

    def clear_altstack(self) -> None:
        self._altstack.clear()

    def to_altstack(self) -> bool:
        item = self.pop()
        if item is None:
            return False
        self._altstack.append(item)
        return True

    def from_altstack(self) -> bool:
        if not self._altstack:
            return False
        item = self._altstack.pop()
        self.push(item)
        return True

    def dup(self) -> bool:
        item = self.top()
        if item is None:
            return False
        self.push(item)
        return True

    def dup2(self) -> bool:
        if self.size() < 2:
            return False
        top = self.peek(-1)
        second = self.peek(-2)
        if top is None or second is None:
            return False
        self.push(second)
        self.push(top)
        return True

    def drop(self) -> bool:
        return self.pop() is not None

    def drop2(self) -> bool:
        if self.size() < 2:
            return False
        self.pop()
        self.pop()
        return True

    def over(self) -> bool:
        if self.size() < 2:
            return False
        item = self.peek(-2)
        if item is None:
            return False
        self.push(item)
        return True

    def pick(self, n: int) -> bool:
        if n < 0 or n >= self.size():
            return False
        item = self.peek(-(n + 1))
        if item is None:
            return False
        self.push(item)
        return True

    def roll(self, n: int) -> bool:
        if n < 0 or n >= self.size():
            return False
        index = -(n + 1)
        item = self._items.pop(index)
        self.push(item)
        return True

    def swap(self) -> bool:
        if self.size() < 2:
            return False
        top = self.pop()
        second = self.pop()
        if top is None or second is None:
            return False
        self.push(top)
        self.push(second)
        return True

    def tuck(self) -> bool:
        if self.size() < 2:
            return False
        top = self.pop()
        second = self.pop()
        if top is None or second is None:
            return False
        self.push(top)
        self.push(second)
        self.push(top)
        return True

    def nip(self) -> bool:
        if self.size() < 2:
            return False
        top = self.pop()
        if top is None:
            return False
        self.pop()
        self.push(top)
        return True

    def rot(self) -> bool:
        if self.size() < 3:
            return False
        a = self.pop()
        b = self.pop()
        c = self.pop()
        if a is None or b is None or c is None:
            return False
        self.push(b)
        self.push(a)
        self.push(c)
        return True

    def depth(self) -> int:
        return len(self._items)

    def altstack_size(self) -> int:
        return len(self._altstack)

    def get_items(self) -> List[bytes]:
        return self._items.copy()

    def __repr__(self) -> str:
        items = [item.hex()[:16] for item in reversed(self._items)]
        return f"Stack({items})"
