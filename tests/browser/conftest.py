"""Two local origins for the tests that need a real page.

Camoufox randomises the canvas per site, so those tests cannot use about:blank —
they need a page served from an origin, and the linkability test needs two
origins the browser counts as *different* sites. That used to mean reaching
example.com and iana.org, which made a full run depend on the internet and on
two third parties not rate-limiting it.

Measured rather than assumed: the browser keys the randomisation on the host, so
`127.0.0.1` and `localhost` are two different sites to it. (The port turned out
not to be part of the key either, which is incidental here — the two names differ
regardless.) `test_the_two_local_origins_are_different_sites` asserts the part
this design rests on instead of trusting this comment.
"""

import asyncio
from collections.abc import Iterator

import pytest
import pytest_asyncio

from tests.browser.support import LocalSites, serve_local_sites
from tests.browser.watchdog import report_stall, watchdog_seconds


@pytest_asyncio.fixture(autouse=True)
async def browser_async_watchdog():
    """CI opts in; always remove the timer before its event loop is torn down."""
    seconds = watchdog_seconds()
    task = asyncio.create_task(report_stall(seconds)) if seconds is not None else None
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.fixture(scope="session")
def local_sites() -> Iterator[LocalSites]:
    with serve_local_sites() as sites:
        yield sites
