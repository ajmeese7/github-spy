"""HTTP client for the GitHub REST API with caching, rate limiting, and pagination."""

from __future__ import annotations

import contextlib
import logging
import time
from datetime import UTC
from typing import TYPE_CHECKING, Any

import httpx
from rich.progress import Progress, SpinnerColumn, TextColumn

from github_spy.models import CacheEntry, RateLimitInfo

if TYPE_CHECKING:
    from collections.abc import Generator

log = logging.getLogger(__name__)

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
DEFAULT_USER_AGENT = "github-spy/0.1.0"

# Warn when remaining requests drop below this threshold
RATE_LIMIT_WARN_THRESHOLD = 50
# Max retries on transient failures
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 2.0


def _parse_rate_limit(headers: httpx.Headers) -> RateLimitInfo:
    """Extract rate limit info from GitHub response headers."""
    reset_str = headers.get("x-ratelimit-reset")
    from datetime import datetime

    reset_at = None
    if reset_str:
        with contextlib.suppress(ValueError, OSError):
            reset_at = datetime.fromtimestamp(int(reset_str), tz=UTC)

    return RateLimitInfo(
        limit=int(headers.get("x-ratelimit-limit", "0")),
        remaining=int(headers.get("x-ratelimit-remaining", "0")),
        reset_at=reset_at,
        used=int(headers.get("x-ratelimit-used", "0")),
    )


class GitHubClient:
    """Stateless HTTP client for the GitHub API.

    Handles authentication, conditional requests, rate limiting,
    pagination, and retry with backoff.
    """

    def __init__(
        self,
        token: str | None = None,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: int = 30,
    ) -> None:
        self.token = token
        self.user_agent = user_agent
        headers: dict[str, str] = {
            "User-Agent": user_agent,
            "X-GitHub-Api-Version": API_VERSION,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(
            base_url=API_BASE,
            headers=headers,
            timeout=timeout,
            follow_redirects=True,
        )
        self.last_rate_limit: RateLimitInfo | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def get_json(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        cache_entry: CacheEntry | None = None,
    ) -> tuple[int, Any | None, httpx.Headers]:
        """Make a GET request, returning (status, parsed_json, headers).

        Returns status 304 with None data when the server confirms nothing changed.
        """
        headers: dict[str, str] = {"Accept": accept}
        if cache_entry:
            if cache_entry.etag:
                headers["If-None-Match"] = cache_entry.etag
            if cache_entry.last_modified:
                headers["If-Modified-Since"] = cache_entry.last_modified

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._client.get(path, params=params, headers=headers)
                self.last_rate_limit = _parse_rate_limit(resp.headers)
                self._check_rate_limit()

                if resp.status_code == 304:
                    return 304, None, resp.headers

                resp.raise_for_status()
                data = resp.json() if resp.content else None
                return resp.status_code, data, resp.headers

            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                # Rate limited: sleep until reset
                if status == 403 and "rate limit" in exc.response.text.lower():
                    self._wait_for_rate_limit_reset(exc.response.headers)
                    continue
                # Server errors: retry with backoff
                if status >= 500:
                    last_exc = exc
                    if attempt < MAX_RETRIES - 1:
                        sleep_time = RETRY_BACKOFF_BASE**attempt
                        log.warning(
                            "GitHub returned %d, retrying in %.1fs (attempt %d/%d)",
                            status,
                            sleep_time,
                            attempt + 1,
                            MAX_RETRIES,
                        )
                        time.sleep(sleep_time)
                        continue
                raise RuntimeError(
                    f"GitHub API error {status} for {path}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < MAX_RETRIES - 1:
                    sleep_time = RETRY_BACKOFF_BASE**attempt
                    log.warning(
                        "Network error for %s, retrying in %.1fs (attempt %d/%d)",
                        path,
                        sleep_time,
                        attempt + 1,
                        MAX_RETRIES,
                    )
                    time.sleep(sleep_time)
                    continue
                raise RuntimeError(f"Network error for {path}: {exc}") from exc

        raise RuntimeError(f"Exhausted retries for {path}") from last_exc

    def paginate(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
        max_pages: int = 10,
        per_page: int = 100,
        cache_getter: Any | None = None,
        cache_setter: Any | None = None,
    ) -> Generator[tuple[int, list[dict[str, Any]], int], None, None]:
        """Yield (status, items, page_number) for each page of a paginated endpoint.

        cache_getter: callable(cache_key) -> CacheEntry
        cache_setter: callable(cache_key, headers) -> None
        """
        base_params = dict(params or {})
        base_params["per_page"] = per_page

        for page in range(1, max_pages + 1):
            page_params = {**base_params, "page": page}
            cache_key = f"{path}:page:{page}:per:{per_page}"

            cache_entry = None
            if cache_getter:
                cache_entry = cache_getter(cache_key)

            status, data, resp_headers = self.get_json(
                path, params=page_params, accept=accept, cache_entry=cache_entry
            )

            if cache_setter and status != 304:
                cache_setter(cache_key, resp_headers)

            if status == 304:
                yield 304, [], page
                continue

            items = data if isinstance(data, list) else []
            yield status, items, page

            if len(items) < per_page:
                break

    def _check_rate_limit(self) -> None:
        """Log a warning if rate limit is getting low."""
        rl = self.last_rate_limit
        if rl and rl.remaining > 0 and rl.remaining < RATE_LIMIT_WARN_THRESHOLD:
            log.warning(
                "GitHub API rate limit running low: %d/%d remaining (resets at %s)",
                rl.remaining,
                rl.limit,
                rl.reset_at.isoformat() if rl.reset_at else "unknown",
            )

    def _wait_for_rate_limit_reset(self, headers: httpx.Headers) -> None:
        """Sleep until the rate limit resets, with a progress indicator."""
        rl = _parse_rate_limit(headers)
        if not rl.reset_at:
            # Fallback: sleep 60s
            log.warning("Rate limited but no reset time found, sleeping 60s")
            time.sleep(60)
            return

        from datetime import datetime

        now = datetime.now(UTC)
        wait_seconds = max(1, int((rl.reset_at - now).total_seconds()) + 1)

        with Progress(
            SpinnerColumn(),
            TextColumn("[yellow]Rate limited. Waiting {task.fields[remaining]}s for reset..."),
        ) as progress:
            task = progress.add_task("waiting", remaining=wait_seconds)
            for remaining in range(wait_seconds, 0, -1):
                progress.update(task, remaining=remaining)
                time.sleep(1)
