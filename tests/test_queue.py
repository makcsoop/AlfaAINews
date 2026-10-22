import logging

from backend.agents.queue import (
    JOBS_KEY,
    ZSET_KEY,
    AnalysisJob,
    InMemoryQueue,
    RedisQueue,
    make_queue,
)


def job(story="st_1", sector="46", version=1) -> AnalysisJob:
    return AnalysisJob(story, version, sector)


class FakeRedis:
    """Минимальная заглушка клиента: только вызовы, которые использует RedisQueue."""

    def __init__(self):
        self.z: dict[str, float] = {}
        self.h: dict[str, str] = {}

    def zadd(self, key, mapping):
        assert key == ZSET_KEY and key.startswith("newsai:queue:")
        self.z.update(mapping)

    def zscore(self, key, member):
        return self.z.get(member)

    def zpopmax(self, key, count=1):
        if not self.z:
            return []
        m = max(self.z, key=lambda k: (self.z[k], k))
        return [(m.encode(), self.z.pop(m))]  # настоящий клиент отдаёт bytes

    def zcard(self, key):
        return len(self.z)

    def hset(self, key, field, value):
        assert key == JOBS_KEY
        self.h[field] = value

    def hget(self, key, field):
        v = self.h.get(field)
        return v.encode() if v is not None else None

    def hdel(self, key, field):
        self.h.pop(field, None)


def both():
    return [InMemoryQueue(), RedisQueue(FakeRedis())]


def test_priority_order():
    for q in both():
        q.push(job("a"), 0.2)
        q.push(job("b"), 0.9)
        q.push(job("c"), 0.5)
        assert len(q) == 3
        assert [q.pop().story_id for _ in range(3)] == ["b", "c", "a"]
        assert q.pop() is None and len(q) == 0


def test_dedup_and_priority_raise():
    for q in both():
        assert q.push(job("a"), 0.2) is True
        assert q.push(job("b"), 0.5) is True
        assert q.push(job("a"), 0.9) is False  # дубль: приоритет вырос
        assert len(q) == 2 and q.contains(job("a").key)
        assert q.push(job("b"), 0.1) is False  # понижение игнорируется
        assert [q.pop().story_id for _ in range(2)] == ["a", "b"]
        assert not q.contains(job("a").key)


def test_new_version_or_sector_is_separate_job():
    for q in both():
        q.push(job("a", version=1), 0.5)
        q.push(job("a", version=2), 0.5)
        q.push(job("a", sector="62"), 0.5)
        assert len(q) == 3


def test_inmemory_fifo_on_ties():
    q = InMemoryQueue()
    for s in "abc":
        q.push(job(s), 0.5)
    assert [q.pop().story_id for _ in range(3)] == ["a", "b", "c"]


def test_repush_after_pop():
    for q in both():
        q.push(job("a"), 0.5)
        q.pop()
        assert q.push(job("a"), 0.1) is True
        assert q.pop() == job("a")


def test_redis_stores_body_and_cleans_up():
    fake = FakeRedis()
    q = RedisQueue(fake)
    q.push(job("a"), 1)
    assert set(fake.h) == {job("a").key}
    assert q.pop() == job("a")
    assert fake.h == {} and fake.z == {}


def test_make_queue_without_redis_warns(caplog):
    with caplog.at_level(logging.WARNING):
        q = make_queue(None)
    assert isinstance(q, InMemoryQueue) and "в памяти" in caplog.text


def test_make_queue_unreachable_redis_falls_back(caplog):
    with caplog.at_level(logging.WARNING):
        q = make_queue("redis://127.0.0.1:1/0")
    assert isinstance(q, InMemoryQueue) and "Redis недоступен" in caplog.text
