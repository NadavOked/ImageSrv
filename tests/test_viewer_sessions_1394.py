import asyncio

from server.console import viewer_sessions


def test_revoking_a_console_cookie_cancels_its_live_viewer():
    async def scenario():
        started = asyncio.Event()

        async def viewer():
            viewer_sessions.register("session-a", "admin", asyncio.current_task())
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                viewer_sessions.forget("session-a", asyncio.current_task())

        task = asyncio.create_task(viewer())
        await started.wait()
        assert viewer_sessions.revoke("session-a") is True
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert task.cancelled()
        assert viewer_sessions.revoke("session-a") is False

    asyncio.run(scenario())


def test_revoking_a_user_cancels_every_live_viewer_for_that_user():
    async def scenario():
        tasks = []
        for token in ("session-a", "session-b"):
            task = asyncio.create_task(asyncio.Event().wait())
            viewer_sessions.register(token, "Admin", task)
            tasks.append(task)
        assert viewer_sessions.revoke_user("admin") == 2
        await asyncio.gather(*tasks, return_exceptions=True)
        assert all(task.cancelled() for task in tasks)

    asyncio.run(scenario())
