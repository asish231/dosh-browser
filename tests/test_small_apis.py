"""Web Platform Small APIs hardening regressions:
1. navigator.geolocation.watchPosition returns unique monotonic IDs & clearWatch works.
2. navigator.serviceWorker.register resolves scriptURL to absolute URL.
3. Popover API throws InvalidStateError and NotSupportedError, auto-closes other auto popovers,
   hides closed popovers from checkVisibility and action surface, and dismisses on Escape.
4. history.scrollRestoration exists with auto default and manual toggle.
5. checkVisibility only checks visibility when checkVisibilityCSS is true, and opacity when checkOpacity is true.
"""

import httpx
import pytest

from webresearch.session import Session

BASE = "https://test.local"


def _client(pages: dict[str, str]) -> httpx.AsyncClient:
    def handler(req: httpx.Request):
        body = pages.get(req.url.path, "<html><body>404</body></html>")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------------------
# 1. Geolocation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_geolocation_watch_position_unique_ids_and_clear():
    page = """<html><body><script>
    var id1 = navigator.geolocation.watchPosition(function(){}, function(){});
    var id2 = navigator.geolocation.watchPosition(function(){}, function(){});
    var id3 = navigator.geolocation.watchPosition(function(){}, function(){});
    navigator.geolocation.clearWatch(id2);
    window.__res = [id1, id2, id3].join(',');
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_geo")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__res") == "1,2,3"


# ---------------------------------------------------------------------------
# 2. Service Worker scriptURL resolution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_worker_script_url_resolved_absolute():
    page = """<html><body><script>
    window.__sw_res = {};
    navigator.serviceWorker.register('sw.js').then(function(r) {
        window.__sw_res.rel = r.active.scriptURL;
    });
    navigator.serviceWorker.register('/root-sw.js').then(function(r) {
        window.__sw_res.root = r.active.scriptURL;
    });
    </script></body></html>"""
    session = Session(_client({"/app/index.html": page}), "s_sw")
    await session.goto(f"{BASE}/app/index.html")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__sw_res.rel") == "https://test.local/app/sw.js"
    assert ctx.eval("window.__sw_res.root") == "https://test.local/root-sw.js"


# ---------------------------------------------------------------------------
# 3. Popover API
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_popover_error_handling_and_auto_close():
    page = """<html><body>
    <div id="no-popover">Not a popover</div>
    <div id="p1" popover="auto"><button id="btn1">P1 Button</button></div>
    <div id="p2" popover="auto"><button id="btn2">P2 Button</button></div>
    <div id="manual" popover="manual"><button id="btn-man">Manual Button</button></div>
    <script>window.__ready = true;</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_popover")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx

    # 1. NotSupportedError when lacking popover attribute
    assert ctx.eval("""
    (function() {
        try {
            document.getElementById('no-popover').showPopover();
            return 'no-throw';
        } catch (e) {
            return e.name;
        }
    })()
    """) == "NotSupportedError"

    assert ctx.eval("""
    (function() {
        try {
            document.getElementById('no-popover').hidePopover();
            return 'no-throw';
        } catch (e) {
            return e.name;
        }
    })()
    """) == "NotSupportedError"

    # 2. InvalidStateError when calling hidePopover on a closed popover
    assert ctx.eval("""
    (function() {
        try {
            document.getElementById('p1').hidePopover();
            return 'no-throw';
        } catch (e) {
            return e.name;
        }
    })()
    """) == "InvalidStateError"

    # 3. showPopover works, sets open, and throws InvalidStateError when already showing
    assert ctx.eval("""
    (function() {
        var p1 = document.getElementById('p1');
        p1.showPopover();
        try {
            p1.showPopover();
            return 'no-throw';
        } catch (e) {
            return e.name;
        }
    })()
    """) == "InvalidStateError"

    # 4. InvalidStateError when showing disconnected element
    assert ctx.eval("""
    (function() {
        var disc = document.createElement('div');
        disc.setAttribute('popover', 'auto');
        try {
            disc.showPopover();
            return 'no-throw';
        } catch (e) {
            return e.name;
        }
    })()
    """) == "InvalidStateError"

    # 5. Showing p2 auto-closes p1 (mutual exclusivity / light dismiss)
    assert ctx.eval("document.getElementById('p1').matches ? document.getElementById('p1').hasAttribute('open') : document.getElementById('p1').popoverOpen") is True
    ctx.eval("document.getElementById('p2').showPopover();")
    assert ctx.eval("document.getElementById('p2').popoverOpen") is True
    assert ctx.eval("document.getElementById('p1').popoverOpen") is False

    # 6. Manual popovers do NOT close auto popovers and vice versa
    ctx.eval("document.getElementById('manual').showPopover();")
    assert ctx.eval("document.getElementById('p2').popoverOpen") is True
    assert ctx.eval("document.getElementById('manual').popoverOpen") is True

    # 7. togglePopover handles boolean force parameter
    ctx.eval("document.getElementById('p2').togglePopover(true);")  # already open: no-op, no throw
    assert ctx.eval("document.getElementById('p2').popoverOpen") is True
    ctx.eval("document.getElementById('p2').togglePopover(false);") # closes
    assert ctx.eval("document.getElementById('p2').popoverOpen") is False
    ctx.eval("document.getElementById('p2').togglePopover(false);") # already closed: no-op, no throw
    assert ctx.eval("document.getElementById('p2').popoverOpen") is False


@pytest.mark.asyncio
async def test_popover_visibility_and_action_surface():
    page = """<html><body>
    <button id="open-btn">Open</button>
    <div id="pop" popover="auto">
        <button id="inside-btn">Inside Popover</button>
    </div>
    <script>window.__ready = true;</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_pop_vis")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx

    # Closed popover: hidden from checkVisibility and excluded from action surface
    assert ctx.eval("document.getElementById('pop').checkVisibility()") is False
    assert ctx.eval("document.getElementById('inside-btn').checkVisibility()") is False
    surf1 = session.action_surface()
    labels1 = [a.label for a in surf1.actions]
    assert "Inside Popover" not in labels1
    assert "Open" in labels1

    # Open popover: visible and included in action surface
    ctx.eval("document.getElementById('pop').showPopover();")
    assert ctx.eval("document.getElementById('pop').checkVisibility()") is True
    assert ctx.eval("document.getElementById('inside-btn').checkVisibility()") is True
    surf2 = session.action_surface()
    labels2 = [a.label for a in surf2.actions]
    assert "Inside Popover" in labels2

    # Escape key dismisses open auto popover
    inside_act = [a for a in surf2.actions if a.label == "Inside Popover"][0]
    await session.press_key(inside_act.ref, "escape")
    assert ctx.eval("document.getElementById('pop').popoverOpen") is False
    assert ctx.eval("document.getElementById('inside-btn').checkVisibility()") is False


# ---------------------------------------------------------------------------
# 4. History scrollRestoration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_history_scroll_restoration():
    page = """<html><body><script>
    window.__has_sr = 'scrollRestoration' in history;
    window.__default_sr = history.scrollRestoration;
    history.scrollRestoration = 'manual';
    window.__manual_sr = history.scrollRestoration;
    history.scrollRestoration = 'invalid_ignored';
    window.__after_invalid_sr = history.scrollRestoration;
    history.scrollRestoration = 'auto';
    window.__back_auto_sr = history.scrollRestoration;
    </script></body></html>"""
    session = Session(_client({"/": page}), "s_hist")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx
    assert ctx.eval("window.__has_sr") is True
    assert ctx.eval("window.__default_sr") == "auto"
    assert ctx.eval("window.__manual_sr") == "manual"
    assert ctx.eval("window.__after_invalid_sr") == "manual"
    assert ctx.eval("window.__back_auto_sr") == "auto"


# ---------------------------------------------------------------------------
# 5. checkVisibility options
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_visibility_options():
    page = """<html><head><style>
    #hidden-vis { visibility: hidden; }
    #opacity-0 { opacity: 0; }
    #disp-none { display: none; }
    </style></head><body>
    <div id="hidden-vis">Hidden Vis</div>
    <div id="opacity-0">Opacity Zero</div>
    <div id="disp-none">Display None</div>
    <script>window.__ready = true;</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_vis_opts")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx

    # display: none is always false
    assert ctx.eval("document.getElementById('disp-none').checkVisibility()") is False
    assert ctx.eval("document.getElementById('disp-none').checkVisibility({ checkVisibilityCSS: true })") is False

    # visibility: hidden is true by default, false with checkVisibilityCSS: true
    assert ctx.eval("document.getElementById('hidden-vis').checkVisibility()") is True
    assert ctx.eval("document.getElementById('hidden-vis').checkVisibility({ checkVisibilityCSS: true })") is False

    # opacity: 0 is true by default, false with checkOpacity: true
    assert ctx.eval("document.getElementById('opacity-0').checkVisibility()") is True
    assert ctx.eval("document.getElementById('opacity-0').checkVisibility({ checkOpacity: true })") is False

    # Disconnected element is false
    assert ctx.eval("document.createElement('div').checkVisibility()") is False


# ---------------------------------------------------------------------------
# E4: full Popover semantics — declarative target + light dismiss (Phase 3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_popover_declarative_target_and_light_dismiss():
    """E4: declarative popovertarget/popovertargetaction toggles the popover;
    click-outside light-dismiss closes auto popovers (manual popovers stay)."""
    page = """<html><body>
    <button id="trigger" popovertarget="pop">Toggle</button>
    <button id="elsewhere">Elsewhere</button>
    <div id="pop" popover="auto"><button id="inside">Inside</button></div>
    <div id="manual" popover="manual">Manual</div>
    <script>window.__ready = true;</script>
    </body></html>"""
    session = Session(_client({"/": page}), "s_popover_e4")
    await session.goto(f"{BASE}/")
    ctx = session.current_tab.ctx

    # Popover starts closed
    assert ctx.eval("document.getElementById('pop').popoverOpen") is False

    # Click the trigger button: popover opens (declarative popovertarget)
    ctx.eval("document.getElementById('trigger').click();")
    assert ctx.eval("document.getElementById('pop').popoverOpen") is True

    # Click elsewhere: light dismiss closes the auto popover
    ctx.eval("document.getElementById('elsewhere').click();")
    assert ctx.eval("document.getElementById('pop').popoverOpen") is False

    # Click trigger again: popover opens
    ctx.eval("document.getElementById('trigger').click();")
    assert ctx.eval("document.getElementById('pop').popoverOpen") is True

    # Open the manual popover
    ctx.eval("document.getElementById('manual').showPopover();")
    assert ctx.eval("document.getElementById('manual').popoverOpen") is True

    # Click elsewhere: auto popover closes, manual popover stays
    ctx.eval("document.getElementById('elsewhere').click();")
    assert ctx.eval("document.getElementById('pop').popoverOpen") is False
    assert ctx.eval("document.getElementById('manual').popoverOpen") is True

    # Click inside the manual popover: it stays open (light dismiss only
    # fires on clicks outside the popover)
    ctx.eval("document.getElementById('inside').click();")
    assert ctx.eval("document.getElementById('manual').popoverOpen") is True


