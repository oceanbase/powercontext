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

"""The Server and host processes exchange messages even while the default executor is full."""

import asyncio
import multiprocessing
import pickle
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from powercontext.builtin.plugins.host_protocol import PipeChannel


def test_channel_carries_messages_in_order_while_the_default_executor_is_full():
    async def scenario():
        loop = asyncio.get_running_loop()
        executor = ThreadPoolExecutor(max_workers=1)
        loop.set_default_executor(executor)
        release = threading.Event()
        occupied = loop.run_in_executor(None, release.wait)
        left, right = multiprocessing.Pipe(duplex=True)
        server, host = PipeChannel(left, name="server"), PipeChannel(right, name="host")
        try:
            async with asyncio.timeout(5):
                await asyncio.gather(*(server.send(index) for index in range(20)))
                assert [await host.recv() for _ in range(20)] == list(range(20))
                await host.send("reply")
                assert await server.recv() == "reply"
        finally:
            release.set()
            await occupied
            executor.shutdown()

    asyncio.run(scenario())


def test_channel_reports_the_other_end_going_away_and_refuses_sends_once_closed():
    async def scenario():
        left, right = multiprocessing.Pipe(duplex=True)
        channel = PipeChannel(left, name="server")
        right.close()
        async with asyncio.timeout(5):
            with pytest.raises(EOFError):
                await channel.recv()
            with pytest.raises(EOFError):
                await channel.recv()
            await channel.close()
        with pytest.raises(OSError, match="closed"):
            await channel.send("late")

    asyncio.run(scenario())


def test_channel_raises_a_message_that_cannot_be_received_then_reports_the_end():
    async def scenario():
        left, right = multiprocessing.Pipe(duplex=True)
        channel = PipeChannel(left, name="server")
        right.send_bytes(b"not a pickle")
        async with asyncio.timeout(5):
            with pytest.raises(pickle.UnpicklingError):
                await channel.recv()
            with pytest.raises(EOFError):
                await channel.recv()
        right.close()
        await channel.close()

    asyncio.run(scenario())
