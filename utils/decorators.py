import functools
import logging
import time
import random
from typing import Any, Callable, Optional, Union
import requests

# Try to import DBClient for type checking, but avoid circular imports at runtime if possible
# We really just need to know it has a 'log_step' method.

logger = logging.getLogger(__name__)

# HTTP status codes that should trigger retry
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential_base: float = 2.0,
    jitter: bool = True
):
    """
    Decorator for retrying functions with exponential backoff.
    Handles transient failures in external API calls.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Initial delay between retries in seconds
        max_delay: Maximum delay between retries
        exponential_base: Base for exponential backoff calculation
        jitter: Whether to add random jitter to prevent thundering herd
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    # Only retry on specific status codes
                    if hasattr(e, 'response') and e.response is not None:
                        if e.response.status_code not in RETRYABLE_STATUS_CODES:
                            raise  # Don't retry client errors like 400, 401, 404
                    last_exception = e
                except (requests.exceptions.ConnectionError, 
                        requests.exceptions.Timeout,
                        requests.exceptions.ChunkedEncodingError,
                        ValueError) as e:  # ValueError covers json.JSONDecodeError
                    last_exception = e
                except Exception as e:
                    # Don't retry unknown exceptions
                    raise
                
                if attempt < max_retries:
                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (exponential_base ** attempt), max_delay)
                    
                    # Add jitter to prevent thundering herd
                    if jitter:
                        delay = delay * (0.5 + random.random())
                    
                    logger.warning(
                        f"Retry {attempt + 1}/{max_retries} for {func.__name__} "
                        f"after {delay:.2f}s. Error: {last_exception}"
                    )
                    time.sleep(delay)
            
            # All retries exhausted
            logger.error(f"All {max_retries} retries exhausted for {func.__name__}")
            raise last_exception
        return wrapper
    return decorator

def monitor_and_log(
    log_message: str = "Error executing function",
    default_return: Any = None,
    reraise: bool = False
):
    """
    Decorator to wrap functions with try/except, logging errors to both 
    standard logger and the database (if a 'db' argument is present).

    Args:
        log_message (str): Prefix for the error message.
        default_return (Any): Value to return on exception if reraise is False.
        reraise (bool): Whether to raise the exception after logging.
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                # 1. Log to standard logger
                error_msg = f"{log_message}: {e}"
                logger.error(error_msg)

                # 2. Try to find 'db' in args or kwargs to log to DB
                db_client = None
                
                # Check kwargs
                if 'db' in kwargs:
                    db_client = kwargs['db']
                else:
                    # Check args - inspect function signature to find position?
                    # Or just iterate and check type/attributes?
                    # Simple heuristic: check for object with 'log_step' method
                    for arg in args:
                        if hasattr(arg, 'log_step'):
                            db_client = arg
                            break
                            
                if db_client and hasattr(db_client, 'log_step'):
                    try:
                        db_client.log_step("ERROR", error_msg)
                    except Exception as db_e:
                        logger.error(f"Failed to log to DB within decorator: {db_e}")

                if reraise:
                    raise e
                return default_return
        return wrapper
    return decorator
