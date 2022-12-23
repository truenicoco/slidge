from asyncio import iscoroutine, run_coroutine_threadsafe
from functools import wraps


def make_sync(func, loop):
    """
    Wrap async function in synchronous operation, running against the given loop in thread-safe mode.
    """

    @wraps(func)
    def wrapper(*args, **kwargs):
        result = func(*args, **kwargs)
        if iscoroutine(result):
            future = run_coroutine_threadsafe(result, loop)
            return future.result()
        return result

    return wrapper
