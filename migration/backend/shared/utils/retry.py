import time
import functools
from backend.shared.config.logging import logger

def retry(max_attempts: int = 5, base_delay: int = 1):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            attempt = 1
            while attempt <= max_attempts:
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        logger.error(f"Function {func.__name__} failed after {max_attempts} attempts", error=str(e))
                        raise e
                    delay = base_delay * (2 ** (attempt - 1))
                    logger.warning(f"Attempt {attempt} failed, retrying in {delay}s...", error=str(e))
                    time.sleep(delay)
                    attempt += 1
        return wrapper
    return decorator
