"""Один выделенный поток для всех COM-вызовов (pycaw, ярлыки .lnk).

COM требует, чтобы объект использовался в том же потоке, где создан,
поэтому всё, что трогает comtypes, выполняется только здесь.
"""
import asyncio
import concurrent.futures
import gc
import logging
import sys

log = logging.getLogger("com")


def _init():
    if sys.platform != "win32":
        return                    # на Linux это просто отдельный поток для звука (pulsectl)
    try:
        import comtypes  # noqa: F401  (импорт сам инициализирует COM в этом потоке)
        comtypes.CoInitialize()
    except Exception:
        log.exception("COM не инициализирован")


EXECUTOR = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="com", initializer=_init)


async def run(fn, *args):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(EXECUTOR, fn, *args)


async def gc_loop():
    """Автоматический сборщик мусора выключен (gc.disable в deck.py): иначе он может
    освободить COM-объект в чужом потоке, и Windows убьёт процесс (access violation).
    Поэтому мусор собираем сами и только в COM-потоке."""
    while True:
        await asyncio.sleep(5)
        try:
            await run(gc.collect)
        except Exception:
            log.exception("gc")
