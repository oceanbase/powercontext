# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Observable client ownership and cancellation behavior without network access."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine

import httpx
import pytest

from benchmark.locomo_plus.jev_transport import JevClientPool


class TrackedTransport(httpx.MockTransport):
    def __init__(self, handler: Callable[[httpx.Request], Coroutine[None, None, httpx.Response]]) -> None:
        super().__init__(handler)
        self.closed = False
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()

    async def aclose(self) -> None:
        self.close_started.set()
        await self.close_release.wait()
        self.closed = True
        await super().aclose()


async def request(pool: JevClientPool) -> httpx.Response:
    async with pool.lease() as client:
        response = await client.get("https://synthetic.invalid/")
        response.raise_for_status()
        return response


def test_successful_lease_reuses_healthy_transport_until_pool_shutdown() -> None:
    async def run() -> None:
        transports = []

        def factory() -> httpx.AsyncClient:
            identifier = len(transports)

            async def respond(_: httpx.Request) -> httpx.Response:
                return httpx.Response(200, json={"backend": identifier})

            transport = TrackedTransport(respond)
            transports.append(transport)
            return httpx.AsyncClient(transport=transport)

        async with JevClientPool(1, client_factory=factory) as pool:
            responses = [(await request(pool)).json() for _ in range(3)]
            assert responses == [{"backend": 0}] * 3
            assert all(not transport.closed for transport in transports)
        assert all(transport.closed for transport in transports)

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["connect", "http-status", "caller", "deadline"])
def test_failed_lease_closes_only_its_transport_and_keeps_healthy_peer_running(failure: str) -> None:
    async def run() -> None:
        transports = []
        entered = [asyncio.Event(), asyncio.Event()]
        release = [asyncio.Event(), asyncio.Event()]

        def factory() -> httpx.AsyncClient:
            identifier = len(transports)
            assert sum(not transport.closed for transport in transports) < 2

            async def respond(_: httpx.Request) -> httpx.Response:
                if identifier < 2:
                    entered[identifier].set()
                    await release[identifier].wait()
                if identifier == 0 and failure == "connect":
                    raise httpx.ConnectError("synthetic")
                return httpx.Response(503 if identifier == 0 and failure == "http-status" else 200)

            transport = TrackedTransport(respond)
            transports.append(transport)
            return httpx.AsyncClient(transport=transport)

        async with JevClientPool(2, client_factory=factory) as pool:

            async def bad() -> httpx.Response:
                async with asyncio.timeout(0.05 if failure == "deadline" else None):
                    return await request(pool)

            failing = asyncio.create_task(bad())
            await entered[0].wait()
            healthy = asyncio.create_task(request(pool))
            await entered[1].wait()
            if failure == "caller":
                failing.cancel()
            elif failure != "deadline":
                release[0].set()
            error_type = {
                "connect": httpx.ConnectError,
                "http-status": httpx.HTTPStatusError,
                "caller": asyncio.CancelledError,
                "deadline": TimeoutError,
            }[failure]
            with pytest.raises(error_type):
                await failing
            assert transports[0].closed and not transports[1].closed
            assert not healthy.done()
            assert (await request(pool)).status_code == 200
            release[1].set()
            assert (await healthy).status_code == 200
        assert all(transport.closed for transport in transports)

    asyncio.run(run())


def test_ten_client_limit_and_cancelled_waiter_preserve_inflight_requests() -> None:
    async def run() -> None:
        transports = []
        entered = asyncio.Queue()
        release = asyncio.Event()

        def factory() -> httpx.AsyncClient:
            assert sum(not transport.closed for transport in transports) < 10

            async def respond(_: httpx.Request) -> httpx.Response:
                entered.put_nowait(None)
                await release.wait()
                return httpx.Response(200)

            transport = TrackedTransport(respond)
            transports.append(transport)
            return httpx.AsyncClient(transport=transport)

        async with JevClientPool(10, client_factory=factory) as pool:
            jobs = [asyncio.create_task(request(pool)) for _ in range(10)]
            for _ in range(10):
                await entered.get()
            waiter = asyncio.create_task(request(pool))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            assert not any(job.done() for job in jobs)
            assert all(not transport.closed for transport in transports)
            release.set()
            assert all(response.status_code == 200 for response in await asyncio.gather(*jobs))
            assert (await request(pool)).status_code == 200
        assert all(transport.closed for transport in transports)

    asyncio.run(run())


def test_repeated_cancellation_keeps_capacity_retired_until_transport_really_closes() -> None:
    async def run() -> None:
        started = asyncio.Event()
        transports = []

        def factory() -> httpx.AsyncClient:
            first = not transports
            assert all(transport.closed for transport in transports)

            async def respond(_: httpx.Request) -> httpx.Response:
                if first:
                    started.set()
                    await asyncio.Event().wait()
                return httpx.Response(200)

            transport = TrackedTransport(respond)
            if first:
                transport.close_release.clear()
            transports.append(transport)
            return httpx.AsyncClient(transport=transport)

        async with JevClientPool(1, client_factory=factory) as pool:
            job = asyncio.create_task(request(pool))
            await started.wait()
            job.cancel()
            await transports[0].close_started.wait()
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
            following = asyncio.create_task(request(pool))
            await asyncio.sleep(0)
            assert not following.done() and not transports[0].closed
            transports[0].close_release.set()
            assert (await following).status_code == 200
        assert all(transport.closed for transport in transports)

    asyncio.run(run())


def test_repeated_shutdown_cancellation_drains_transport_before_propagating() -> None:
    async def run() -> None:
        async def respond(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        transport = TrackedTransport(respond)
        transport.close_release.clear()
        pool = JevClientPool(1, client_factory=lambda: httpx.AsyncClient(transport=transport))
        await pool.__aenter__()
        assert (await request(pool)).status_code == 200
        shutdown = asyncio.create_task(pool.aclose())
        await transport.close_started.wait()
        shutdown.cancel()
        await asyncio.sleep(0)
        shutdown.cancel()
        await asyncio.sleep(0)
        assert not shutdown.done()
        transport.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert transport.closed
        await pool.aclose()

    asyncio.run(run())


def test_shutdown_waits_for_borrower_and_rejects_new_leases() -> None:
    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def respond(_: httpx.Request) -> httpx.Response:
            started.set()
            await release.wait()
            return httpx.Response(200)

        transport = TrackedTransport(respond)
        pool = JevClientPool(1, client_factory=lambda: httpx.AsyncClient(transport=transport))
        await pool.__aenter__()
        job = asyncio.create_task(request(pool))
        await started.wait()
        shutdown = asyncio.create_task(pool.aclose())
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="not accepting"):
            await request(pool)
        assert not transport.closed and not job.done()
        release.set()
        assert (await job).status_code == 200
        await shutdown
        assert transport.closed

    asyncio.run(run())


def test_factory_failure_is_not_retried_and_leaves_capacity_for_next_call() -> None:
    async def run() -> None:
        fail = True

        async def respond(_: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        transport = TrackedTransport(respond)

        def factory() -> httpx.AsyncClient:
            nonlocal fail
            if fail:
                fail = False
                raise ValueError
            return httpx.AsyncClient(transport=transport)

        async with JevClientPool(1, client_factory=factory) as pool:
            with pytest.raises(ValueError):
                await request(pool)
            assert (await request(pool)).status_code == 200
        assert transport.closed

    asyncio.run(run())


def test_cleanup_failure_preserves_request_error_and_prevents_further_leases() -> None:
    class FailingCloseTransport(TrackedTransport):
        async def aclose(self) -> None:
            raise OSError

    async def run() -> None:
        async def respond(_: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        transport = FailingCloseTransport(respond)
        pool = JevClientPool(1, client_factory=lambda: httpx.AsyncClient(transport=transport))
        await pool.__aenter__()
        with pytest.raises(httpx.HTTPStatusError):
            await request(pool)
        with pytest.raises(RuntimeError, match="not accepting"):
            await request(pool)
        with pytest.raises(RuntimeError, match="cleanup"):
            await pool.aclose()

    asyncio.run(run())
