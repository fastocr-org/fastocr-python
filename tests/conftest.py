import inspect

import pytest

from fastocr_sdk import AsyncFastOCR, FastOCR

BASE_URL = "https://api.test"


class Harness:
    """Builds a sync or async client with sleeping disabled, and calls it uniformly."""

    def __init__(self, kind):
        self.kind = kind
        self.sleeps = []
        self.clients = []

    def client(self, **options):
        cls = FastOCR if self.kind == "sync" else AsyncFastOCR
        client = cls(**{"api_key": "fok_test", "base_url": BASE_URL, **options})
        if self.kind == "sync":
            client._sleep = self.sleeps.append
        else:
            async def record(seconds):
                self.sleeps.append(seconds)

            client._sleep = record
        self.clients.append(client)
        return client

    async def close(self):
        for client in self.clients:
            result = client.close()
            if inspect.isawaitable(result):
                await result


async def call(result):
    return await result if inspect.isawaitable(result) else result


@pytest.fixture(params=["sync", "async"])
async def harness(request):
    h = Harness(request.param)
    yield h
    await h.close()


@pytest.fixture
def client(harness):
    return harness.client()
