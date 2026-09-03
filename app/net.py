import logging
import time

logger = logging.getLogger(__name__)

# Transient failures worth retrying: socket timeouts and connection resets
# (Windows raises these as TimeoutError/ConnectionError with WinError 10060 /
# 10054), plus anything the Google API / gspread / httplib2 / requests stack
# ultimately raises for a network problem -- requests.exceptions.RequestException
# derives from OSError, and socket.timeout is TimeoutError, so a single OSError
# catch covers them all. Auth errors, bad requests and ValueErrors are not
# OSError subclasses, so they still propagate immediately.
RETRYABLE_EXCEPTIONS = (OSError,)

# gspread and googleapiclient raise their OWN exception types (NOT OSError) for
# an HTTP-level failure -- including the transient 429 / 5xx that a retry almost
# always clears (the "[503] The service is currently unavailable" that used to
# crash a submission mid-write). Retry those too, matched on status code so a
# real 400 / 403 / 404 still fails fast.
_TRANSIENT_HTTP_STATUS = {408, 429, 500, 502, 503, 504}


def _http_status(error: BaseException) -> int | None:
    """Best-effort HTTP status from a Google API client exception, without
    importing gspread / googleapiclient here. gspread.APIError exposes ``.code``;
    googleapiclient.errors.HttpError has ``.resp.status``; anything wrapping a
    requests ``Response`` has ``.response.status_code``.
    """
    for value in (
        getattr(error, "code", None),
        getattr(getattr(error, "resp", None), "status", None),
        getattr(getattr(error, "response", None), "status_code", None),
    ):
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _is_retryable(error: BaseException) -> bool:
    return isinstance(error, RETRYABLE_EXCEPTIONS) or _http_status(error) in _TRANSIENT_HTTP_STATUS


def retry_network(
    func, *args,
    attempts: int = 3, delay: float = 2.0, backoff: float = 2.0,
    description: str = "network call", already_done=None, **kwargs,
):
    """Call ``func(*args, **kwargs)``, retrying transient network errors with
    exponential backoff. Re-raises the last error if every attempt fails.

    ``already_done`` is an optional predicate checked *before each retry* (never
    before the first attempt). If it returns True the loop stops and returns
    None -- use it when ``func`` is a non-idempotent write (e.g. appending a
    Sheet row) whose previous attempt may actually have landed on the server
    even though the response never came back.
    """
    last_error: BaseException | None = None
    wait = delay
    for attempt in range(1, attempts + 1):
        if attempt > 1 and already_done is not None:
            try:
                if already_done():
                    logger.warning("%s: a previous attempt landed after all; not retrying", description)
                    return None
            except Exception:
                pass  # can't confirm -- fall through and just retry the call
        try:
            return func(*args, **kwargs)
        except Exception as error:
            if not _is_retryable(error):
                raise
            last_error = error
            if attempt < attempts:
                logger.warning(
                    "%s failed (attempt %d/%d): %r -- retrying in %.1fs",
                    description, attempt, attempts, error, wait,
                )
                time.sleep(wait)
                wait *= backoff
    # Retries exhausted. Normalise a transient HTTP failure (gspread/googleapiclient
    # 5xx) to OSError so callers' existing "network problem" handling catches it
    # the same as a socket error.
    if not isinstance(last_error, OSError):
        raise OSError(f"{description} failed after {attempts} attempts: {last_error}") from last_error
    raise last_error
