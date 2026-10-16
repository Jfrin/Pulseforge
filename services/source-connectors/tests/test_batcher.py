import asyncio

from app.batcher import Batcher
from app.results import Batch


class FakeBatchStore:
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    async def flush_batch(self, source, *, max_size, max_age_s):
        self.calls.append((source, max_size, max_age_s))
        return self.script.pop(0) if self.script else None


async def test_flushes_each_source_until_nothing_is_due():
    store = FakeBatchStore([Batch("b1", "a", "size", 5), Batch("b2", "a", "time", 2), None, None])
    out = await Batcher(store, ["a", "b"], max_size=5, max_age_s=60).flush_all_due()
    assert [b.batch_id for b in out] == ["b1", "b2"]
    assert [c[0] for c in store.calls] == ["a", "a", "a", "b"]
    assert store.calls[0][1:] == (5, 60)


async def test_run_forever_ticks_and_stops_on_event():
    store = FakeBatchStore([])
    batcher = Batcher(store, ["a"], tick_s=0.01)
    stop = asyncio.Event()
    task = asyncio.create_task(batcher.run_forever(stop))
    await asyncio.sleep(0.05)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(store.calls) >= 2


async def test_a_failing_tick_does_not_kill_the_loop():
    class Flaky(FakeBatchStore):
        async def flush_batch(self, source, *, max_size, max_age_s):
            self.calls.append(source)
            if len(self.calls) == 1:
                raise ConnectionError("db down")
            return None

    store = Flaky([])
    stop = asyncio.Event()
    task = asyncio.create_task(Batcher(store, ["a"], tick_s=0.01).run_forever(stop))
    await asyncio.sleep(0.06)
    stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert len(store.calls) >= 2
