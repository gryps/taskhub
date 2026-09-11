import asyncio
from collections import defaultdict, deque
from contextlib import asynccontextmanager


class FairProjectBudget:
    """Global/project budget with round-robin admission between waiting projects."""

    def __init__(self, global_limit: int = 4, default_project_limit: int = 2):
        self.global_limit = global_limit
        self.default_project_limit = default_project_limit
        self.active = 0
        self.project_active: dict[str, int] = defaultdict(int)
        self.project_grants: dict[str, int] = defaultdict(int)
        self.waiters: dict[str, deque[tuple[asyncio.Future, int, int]]] = defaultdict(deque)
        self.order: deque[str] = deque()
        self.last_project = ""
        self.lock = asyncio.Lock()

    @asynccontextmanager
    async def slot(
        self, project_id: str, project_limit: int | None = None, priority_weight: int = 1
    ):
        limit = project_limit or self.default_project_limit
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        async with self.lock:
            if not self.waiters[project_id]:
                self.order.append(project_id)
            self.waiters[project_id].append((future, limit, max(1, priority_weight)))
            self._grant()
        await future
        try:
            yield
        finally:
            async with self.lock:
                self.active -= 1
                self.project_active[project_id] -= 1
                self._grant()

    def _grant(self) -> None:
        stalled = 0
        while self.active < self.global_limit and self.order and stalled < len(self.order):
            if len(self.order) > 1 and self.order[0] == self.last_project:
                self.order.rotate(-1)
            eligible = [
                (self.project_grants[item] / self.waiters[item][0][2], index, item)
                for index, item in enumerate(self.order)
                if self.project_active[item] < self.waiters[item][0][1]
            ]
            if not eligible:
                break
            _score, index, project_id = min(eligible)
            self.order.rotate(-index)
            self.order.popleft()
            self.order.rotate(index)
            queue = self.waiters[project_id]
            if not queue:
                self.waiters.pop(project_id, None)
                continue
            future, project_limit, _weight = queue[0]
            if self.project_active[project_id] >= project_limit:
                self.order.append(project_id)
                stalled += 1
                continue
            queue.popleft()
            self.active += 1
            self.project_active[project_id] += 1
            self.project_grants[project_id] += 1
            self.last_project = project_id
            if queue:
                self.order.append(project_id)
            else:
                self.waiters.pop(project_id, None)
            if not future.done():
                future.set_result(None)
            stalled = 0
