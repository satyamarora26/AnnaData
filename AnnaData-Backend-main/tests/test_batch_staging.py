from contextlib import contextmanager
from pathlib import Path

import pytest

import knowledge
from source_catalog import load_catalog


@pytest.fixture
def staging(monkeypatch):
    spec = load_catalog(Path(__file__).resolve().parents[1] / 'data/source_manifest.json')['pm_kisan_guidelines']
    class Connection:
        def __init__(self):
            self.calls = []
            self.commits = 0
            self.rollbacks = 0
            self.lease = True
            self.fail_insert = False
        def execute(self, sql, params=()):
            self.calls.append((str(sql), params))
            if 'INSERT INTO documents' in str(sql) and self.fail_insert:
                raise RuntimeError('insert failed')
            return self
        def fetchone(self):
            return (1,) if self.lease else None
        def commit(self):
            self.commits += 1
    conn = Connection()
    @contextmanager
    def connection():
        try:
            yield conn
        except BaseException:
            conn.rollbacks += 1
            raise
    monkeypatch.setattr(knowledge.db, 'connection', connection)
    return spec, conn


def test_batch_uses_one_insert_and_commit(staging):
    spec, conn = staging
    assert knowledge.stage_documents(1, spec, 'hash', ['first', 'second'], 5, [[0.0]*768]*2, deadline_at=10, clock=lambda: 0) == 2
    inserts = [call for call in conn.calls if 'INSERT INTO documents' in call[0]]
    assert len(inserts) == 1
    assert 'FALSE' in inserts[0][0]
    assert 5 in inserts[0][1] and 6 in inserts[0][1]
    assert conn.commits == 1


def test_failed_batch_rolls_back_without_commit(staging):
    spec, conn = staging
    conn.fail_insert = True
    with pytest.raises(RuntimeError, match='insert failed'):
        knowledge.stage_documents(1, spec, 'hash', ['first'], 0, [[0.0]*768], deadline_at=10, clock=lambda: 0)
    assert conn.commits == 0 and conn.rollbacks == 1


def test_lost_lease_does_not_stage(staging):
    spec, conn = staging
    conn.lease = False
    with pytest.raises(knowledge.IngestionLeaseActive):
        knowledge.stage_documents(1, spec, 'hash', ['first'], 0, [[0.0]*768], deadline_at=10, clock=lambda: 0)
    assert not any('INSERT INTO documents' in sql for sql, _ in conn.calls)


def test_expired_deadline_does_not_commit(staging):
    spec, conn = staging
    ticks = iter([0, 0, 0, 10])
    with pytest.raises(TimeoutError):
        knowledge.stage_documents(1, spec, 'hash', ['first'], 0, [[0.0]*768], deadline_at=10, clock=lambda: next(ticks))
    assert conn.commits == 0 and conn.rollbacks == 1


@pytest.mark.parametrize('deadline', [float('inf'), float('nan')])
def test_nonfinite_deadline_rejected(staging, deadline):
    spec, conn = staging
    with pytest.raises(ValueError):
        knowledge.stage_documents(1, spec, 'hash', ['first'], 0, [[0.0]*768], deadline_at=deadline)
    assert conn.calls == []


def test_mismatched_batch_rejected(staging):
    spec, conn = staging
    with pytest.raises(ValueError):
        knowledge.stage_documents(1, spec, 'hash', ['first'], 0, [], deadline_at=10)
    assert conn.calls == []
