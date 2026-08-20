"""Two local origins for the tests that need a real page.

Camoufox randomises the canvas per site, so those tests cannot use about:blank —
they need a page served from an origin, and the linkability test needs two
origins the browser counts as *different* sites. That used to mean reaching
example.com and iana.org, which made a full run depend on the internet and on
two third parties not rate-limiting it.

Measured rather than assumed: the browser keys the randomisation on the host, so
`127.0.0.1` and `localhost` are two different sites to it, while the port is not
part of the key. `test_the_two_local_origins_are_different_sites` asserts that
keying instead of trusting this comment.
"""

from collections.abc import Iterator

import pytest

from tests.browser.support import LocalSites, serve_local_sites


@pytest.fixture(scope="session")
def local_sites() -> Iterator[LocalSites]:
    with serve_local_sites() as sites:
        yield sites
