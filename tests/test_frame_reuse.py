"""Frame lifecycle regressions: frames stay alive across action passes.

Before this fix, every action pass (click/type/scroll/press) re-fetched every
iframe document and re-ran its scripts from zero: init messages repeated on
every pass, frame state (counters, loaded data, timers) was wiped, and each
pass re-downloaded the frame + its subresources. The session now passes the
loaded FrameDocuments back into load_frames: unchanged frames are reused with
their live QuickJS contexts, new parent->frame messages are pumped into the
live context, and child->parent messages are delivered exactly once (per-frame
cursor).
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://parent.test"

PARENT = """<html><body>
<iframe id="f" src="https://child.test/child"></iframe>
<button id="poke" onclick="document.getElementById('f').contentWindow.postMessage('ping','*');">Poke</button>
<button id="noop" onclick="window.__noop=1;">Noop</button>
<button id="swap" onclick="document.getElementById('f').setAttribute('src','https://child.test/other');">Swap</button>
<button id="kill" onclick="document.body.removeChild(document.getElementById('f'));">Kill</button>
<script>
window.__log = [];
window.addEventListener('message', function (e) { window.__log.push(String(e.data)); });
</script>
</body></html>"""

CHILD = """<html><body>
<div id="got">nothing</div>
<script>
window.__seen = [];
window.addEventListener('message', function (e) {
  window.__seen.push(e.data);
  document.getElementById('got').textContent = 'GOT:' + window.__seen.length;
  e.source.postMessage('seen:' + window.__seen.length, '*');
});
window.parent.postMessage('init', '*');
</script>
</body></html>"""

OTHER_CHILD = """<html><body><div id="who">other frame</div></body></html>"""


class _Counting:
    def __init__(self) -> None:
        self.child_fetches = 0
        self.other_fetches = 0

    def client(self) -> httpx.AsyncClient:
        log = self

        def handler(req: httpx.Request):
            if req.url.host == "child.test":
                if req.url.path == "/child":
                    log.child_fetches += 1
                    return httpx.Response(200, text=CHILD, headers={"content-type": "text/html"})
                if req.url.path == "/other":
                    log.other_fetches += 1
                    return httpx.Response(200, text=OTHER_CHILD, headers={"content-type": "text/html"})
            return httpx.Response(200, text=PARENT, headers={"content-type": "text/html"})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def _click(session: Session, label: str, max_chars: int = 30_000) -> None:
    ref = next(a.ref for a in session.action_surface(max_actions=100_000).actions if a.label == label)
    await session.act(ref, max_chars=max_chars)


@pytest.mark.asyncio
async def test_frames_not_refetched_on_action_pass():
    log = _Counting()
    session = Session(log.client(), "s_reuse_fetch")
    await session.goto(f"{BASE}/", max_chars=30_000)
    assert log.child_fetches == 1, "child must be fetched at load"
    await _click(session, "Noop")
    await _click(session, "Noop")
    assert log.child_fetches == 1, (
        f"action passes must reuse the loaded frame, but it was fetched {log.child_fetches}x"
    )


@pytest.mark.asyncio
async def test_frame_init_message_delivered_exactly_once():
    log = _Counting()
    session = Session(log.client(), "s_reuse_init")
    await session.goto(f"{BASE}/", max_chars=30_000)
    await _click(session, "Noop")
    await _click(session, "Noop")
    entries = session.current_tab.ctx.eval("(window.__log||[]).join(',')")
    assert entries.count("init") == 1, (
        f"frame init message must reach the parent exactly once, got: {entries!r}"
    )


@pytest.mark.asyncio
async def test_frame_state_persists_across_passes():
    """The child's window.__seen must survive action passes: the second poke
    must reply 'seen:2', not 'seen:1' again (a re-run frame forgets)."""
    log = _Counting()
    session = Session(log.client(), "s_reuse_state")
    await session.goto(f"{BASE}/", max_chars=30_000)
    await _click(session, "Poke")
    await _click(session, "Poke")
    entries = session.current_tab.ctx.eval("(window.__log||[]).join(',')")
    assert "seen:1" in entries and "seen:2" in entries, (
        f"frame state must persist across passes, got: {entries!r}"
    )
    child = next(f for f in session.frame_documents if "child.test" in f.url)
    line = [l for l in child.html.splitlines() if 'id="got"' in l]
    assert line and "GOT:2" in line[0], (
        f"the child's DOM must show 2 delivered messages, got: {line}"
    )


@pytest.mark.asyncio
async def test_parent_message_reaches_live_frame_and_reply_returns():
    log = _Counting()
    session = Session(log.client(), "s_reuse_reply")
    await session.goto(f"{BASE}/", max_chars=30_000)
    await _click(session, "Poke")
    entries = session.current_tab.ctx.eval("(window.__log||[]).join(',')")
    assert "seen:1" in entries, f"pump must deliver to the live frame ctx: {entries!r}"
    child = next(f for f in session.frame_documents if "child.test" in f.url)
    line = [l for l in child.html.splitlines() if 'id="got"' in l]
    assert line and "GOT:1" in line[0], (
        f"pumped frame must serialize its mutated DOM: {line}"
    )


@pytest.mark.asyncio
async def test_changed_src_refetches_new_frame_only():
    log = _Counting()
    session = Session(log.client(), "s_reuse_swap")
    await session.goto(f"{BASE}/", max_chars=30_000)
    assert log.child_fetches == 1
    await _click(session, "Swap")
    assert log.other_fetches == 1, "a changed src must be fetched fresh"
    assert log.child_fetches == 1, "the old frame must not be re-fetched after the swap"
    urls = [f.url for f in session.frame_documents]
    assert any("other" in u for u in urls), f"new frame must be in the surface: {urls}"
    assert not any(u.endswith("/child") for u in urls), f"old frame must drop out: {urls}"


@pytest.mark.asyncio
async def test_removed_frame_dropped_from_surface():
    log = _Counting()
    session = Session(log.client(), "s_reuse_kill")
    await session.goto(f"{BASE}/", max_chars=30_000)
    assert session.frame_documents, "frame must be present at load"
    await _click(session, "Kill")
    assert session.frame_documents == [], (
        f"removed iframe must drop from the frame list: {[f.url for f in session.frame_documents]}"
    )


@pytest.mark.asyncio
async def test_frame_document_identity_is_reused():
    """The reused frame is the SAME FrameDocument object with its live ctx —
    not a rebuilt copy."""
    log = _Counting()
    session = Session(log.client(), "s_reuse_identity")
    await session.goto(f"{BASE}/", max_chars=30_000)
    before = next(f for f in session.frame_documents if "child.test" in f.url)
    ctx_before = before.js_info.get("ctx")
    await _click(session, "Noop")
    after = next(f for f in session.frame_documents if "child.test" in f.url)
    assert after is before, "unchanged frame must be reused, not rebuilt"
    assert after.js_info.get("ctx") is ctx_before, "live frame ctx must survive the pass"


@pytest.mark.asyncio
async def test_history_back_then_action_pass_with_frames():
    """back() restores the entry's frame list; the next action pass must not
    crash or re-fetch them from the network."""
    log = _Counting()
    session = Session(log.client(), "s_reuse_back")
    await session.goto(f"{BASE}/one", max_chars=30_000)   # entry 0
    assert log.child_fetches == 1
    await _click(session, "Noop")
    await session.goto(f"{BASE}/two", max_chars=30_000)   # entry 1
    session.back()
    page = session.page
    assert page is not None
    log.child_fetches = 0
    await _click(session, "Noop")                          # rebuild + reuse
    assert log.child_fetches == 0, (
        "restored entry's frames must be reused, not re-fetched on back()"
    )
    assert any("child.test" in f.url for f in session.frame_documents)
