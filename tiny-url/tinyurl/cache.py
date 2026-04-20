from collections import OrderedDict
from threading import RLock


class LRUCache:
    def __init__(self, max_size: int = 1024):
        self.max_size = max_size
        self._items = OrderedDict()
        self._lock = RLock()

    def get(self, key: str):
        with self._lock:
            if key not in self._items:
                return None
            self._items.move_to_end(key)
            return self._items[key]

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            if len(self._items) > self.max_size:
                self._items.popitem(last=False)

    def delete(self, key: str) -> None:
        with self._lock:
            self._items.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
