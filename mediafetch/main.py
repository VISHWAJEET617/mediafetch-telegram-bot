from __future__ import annotations

import asyncio
import logging

import aiohttp
from aiohttp import web

from .bot import Bot
from .config import Config
from .store import Store
from .telegram import Telegram


async def health(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def run() -> None:
    config = Config.from_env()
    config.data_dir.mkdir(parents=True, exist_ok=True)
    config.jobs_dir.mkdir(parents=True, exist_ok=True)
    store = Store(config.database_path)
    timeout = aiohttp.ClientTimeout(total=None, connect=30, sock_read=config.poll_timeout + 30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        telegram = Telegram(config.bot_token, config.bot_api_base, session)
        bot = Bot(config, telegram, store)
        app = web.Application()
        app.router.add_get("/health", health)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", config.port)
        await site.start()
        try:
            await bot.run()
        finally:
            await runner.cleanup()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run())


if __name__ == "__main__":
    main()

