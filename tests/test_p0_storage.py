"""P0-5 regressions: web storage survives navigation, history restore, and
session restarts.

Before this fix, localStorage/sessionStorage were closures inside each engine
context, and every navigation built a fresh context: a token stored on page 1
read null on page 2 of the same origin. Now the host seeds both stores into
each fresh context before page scripts run and drains them back after every
JS pass: localStorage per origin on the Session (optionally persisted to the
profile's sqlite storage table — previously dead code), sessionStorage per
tab. All probes run the real session/engine paths; only the HTTP transport is
a mock.
"""

import httpx
import pytest

from webresearch.profile import Profile
from webresearch.session import Session

BASE = "https://test.local"

PAGES = {
    "/a": """<html><body><h1>A</h1><script>
      localStorage.setItem('token', 'tok-123');
      sessionStorage.setItem('sess', 'sess-a');
    </script></body></html>""",
    "/b": """<html><body><h1>B</h1><div id="v">unset</div><script>
      document.getElementById('v').textContent =
        'ls=' + localStorage.getItem('token') + '|ss=' + sessionStorage.getItem('sess');
    </script></body></html>""",
    "/remove": """<html><body><h1>R</h1><script>
      localStorage.removeItem('token');
      sessionStorage.clear();
    </script></body></html>""",
}


def _client() -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        body = PAGES.get(req.url.path, "<html><body>404</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _read_probe(session: Session) -> str:
    line = [l for l in session.html.splitlines() if 'id="v"' in l]
    assert line, "probe div missing from serialized page"
    return line[0]


@pytest.mark.asyncio
async def test_local_storage_survives_navigation():
    session = Session(_client(), "s_ls1")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.goto(f"{BASE}/b", max_chars=20_000)
    probe = _read_probe(session)
    assert "ls=tok-123" in probe, probe
    assert "ss=sess-a" in probe, probe


@pytest.mark.asyncio
async def test_session_storage_survives_history_restore():
    """back() drops the ctx; the next action rebuilds it from the stores."""
    session = Session(_client(), "s_ls2")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.goto(f"{BASE}/b", max_chars=20_000)
    await session.goto(f"{BASE}/a", max_chars=20_000)  # history: [a, b, a]
    session.back()  # -> /b
    await session.scroll(y=100, max_chars=20_000)  # forces a fresh ctx build
    probe = _read_probe(session)
    assert "ls=tok-123" in probe, probe
    assert "ss=sess-a" in probe, probe


@pytest.mark.asyncio
async def test_removal_propagates_not_just_additions():
    session = Session(_client(), "s_ls3")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.goto(f"{BASE}/remove", max_chars=20_000)
    await session.goto(f"{BASE}/b", max_chars=20_000)
    probe = _read_probe(session)
    assert "ls=null" in probe, probe
    assert "ss=null" in probe, probe


@pytest.mark.asyncio
async def test_session_storage_is_per_tab():
    session = Session(_client(), "s_ls4")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    session.new_tab("tab_2")
    session.switch_tab("tab_2")
    await session.goto(f"{BASE}/b", max_chars=20_000)
    probe = _read_probe(session)
    # localStorage is origin-scoped and shared; sessionStorage must not leak
    # from tab_1 into tab_2.
    assert "ls=tok-123" in probe, probe
    assert "ss=null" in probe, probe


@pytest.mark.asyncio
async def test_local_storage_persists_to_profile(tmp_path):
    profile_name = str(tmp_path / "prof")
    session = Session(_client(), "s_ls5", profile=profile_name)
    await session.goto(f"{BASE}/a", max_chars=20_000)

    # A completely separate session with the same profile reads the value.
    session2 = Session(_client(), "s_ls6", profile=profile_name)
    await session2.goto(f"{BASE}/b", max_chars=20_000)
    probe = _read_probe(session2)
    assert "ls=tok-123" in probe, probe
    # And the sqlite table holds it (the previously dead storage lane).
    profile = Profile(profile_name)
    assert profile.load_storage(BASE) == {"token": "tok-123"}


@pytest.mark.asyncio
async def test_no_profile_still_works_in_memory():
    session = Session(_client(), "s_ls7")  # no profile attached
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.goto(f"{BASE}/b", max_chars=20_000)
    probe = _read_probe(session)
    assert "ls=tok-123" in probe, probe


@pytest.mark.asyncio
async def test_storage_survives_engine_actions_on_same_page():
    """An action pass on the same document must not lose stored values."""
    session = Session(_client(), "s_ls8")
    await session.goto(f"{BASE}/a", max_chars=20_000)
    await session.scroll(y=200, max_chars=20_000)
    ctx = session.current_tab.ctx
    assert ctx is not None
    assert ctx.eval("localStorage.getItem('token')") == "tok-123"
    assert ctx.eval("sessionStorage.getItem('sess')") == "sess-a"
