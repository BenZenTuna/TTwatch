import logging

import httpx
import tenacity

logger = logging.getLogger(__name__)


def _is_retryable(exception: BaseException) -> bool:
    """Only retry server errors, rate limits, and connection failures.
    Never retry 4xx client errors.
    """
    if isinstance(exception, httpx.HTTPStatusError):
        return (
            exception.response.status_code >= 500
            or exception.response.status_code == 429
        )
    return isinstance(
        exception, (httpx.TimeoutException, httpx.ConnectError, ConnectionError)
    )


retry_config = dict(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, min=2, max=30),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=lambda rs: logger.warning(
        f"Retry {rs.attempt_number} for {rs.fn.__name__}"
    ),
)

# Extended retry for LAN service startup — used by workers during boot
lan_startup_retry = dict(
    stop=tenacity.stop_after_attempt(30),
    wait=tenacity.wait_exponential(multiplier=2, min=5, max=60),
    retry=tenacity.retry_if_exception(_is_retryable),
    before_sleep=lambda rs: logger.info(
        f"Waiting for remote service ({rs.attempt_number}/30)..."
    ),
)
