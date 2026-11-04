"""Test micro-neural DOM interactivity extraction in the action surface."""

from webresearch.actions import collect


def test_react_synthetic_button_extraction():
    html = """
    <html>
      <body>
        <div class="header">
          <h1>My Store</h1>
        </div>
        <div class="content">
          <div data-dosh-interact="64" class="px-4 py-2 bg-blue-600 rounded text-white font-medium">
            Proceed to Checkout
          </div>
        </div>
      </body>
    </html>
    """
    surface = collect(html, "https://store.example.com")
    assert len(surface.actions) == 1
    action = surface.actions[0]
    assert action.role == "button"
    assert action.kind == "activate"
    assert "Proceed to Checkout" in action.label
    assert "neural button" in action.notes


def test_vue_vei_dominated_hierarchy():
    html = """
    <html>
      <body>
        <div data-dosh-interact="128" class="btn btn-primary">
          <span class="btn-text">Save Changes</span>
          <svg class="icon" viewBox="0 0 24 24"></svg>
        </div>
      </body>
    </html>
    """
    surface = collect(html, "https://app.example.com")
    # Tree domination: only the outer div should be extracted, not the inner span or svg
    assert len(surface.actions) == 1
    action = surface.actions[0]
    assert action.role == "button"
    assert action.kind == "activate"
    assert "Save Changes" in action.label


def test_unsemantic_link_with_data_href():
    html = """
    <html>
      <body>
        <div data-dosh-interact="1" data-href="/dashboard" class="nav-item cursor-pointer">
          User Dashboard
        </div>
      </body>
    </html>
    """
    surface = collect(html, "https://app.example.com")
    assert len(surface.actions) == 1
    action = surface.actions[0]
    assert action.role == "link"
    assert action.kind == "navigate"
    assert action.target == "https://app.example.com/dashboard"
    assert "User Dashboard" in action.label


def test_non_interactive_layout_container_ignored():
    html = """
    <html>
      <body>
        <div class="container mx-auto p-4">
          <p>Just a plain article text that should never become an action.</p>
        </div>
      </body>
    </html>
    """
    surface = collect(html, "https://example.com")
    assert len(surface.actions) == 0

