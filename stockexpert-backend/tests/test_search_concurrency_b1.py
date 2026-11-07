"""Shared Qdrant collection creation must tolerate compatible first-writer races."""
from types import SimpleNamespace

import httpx
import pytest
from qdrant_client import models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.services.search import ensure_collection


class RacingClient:
    def __init__(self, status=409, size=3):
        self.status, self.size = status, size
        self.reads = 0

    def create_collection(self, *args, **kwargs):
        raise UnexpectedResponse(self.status, 'test conflict', b'{}', httpx.Headers())

    def get_collection(self, *args):
        self.reads += 1
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(
            vectors=models.VectorParams(size=self.size, distance=models.Distance.COSINE))))


def test_parallel_first_writer_reuses_only_compatible_collection():
    client = RacingClient()
    ensure_collection(client, 3)
    assert client.reads == 1


def test_parallel_first_writer_rejects_incompatible_dimension():
    with pytest.raises(ValueError, match='incompatible'):
        ensure_collection(RacingClient(size=5), 3)


@pytest.mark.parametrize('status', [401, 400, 500])
def test_collection_creation_does_not_hide_other_failures(status):
    client = RacingClient(status=status)
    with pytest.raises(UnexpectedResponse):
        ensure_collection(client, 3)
    assert client.reads == 0
