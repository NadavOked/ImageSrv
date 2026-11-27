"""#4026: while no deploy network exists the "רשת הפצה" tab is hidden (#3029), so no server text may send the operator to it.

The text names the button the console shows instead. The label is pinned to console.js, so a rename there fails here.
"""

from console_src import console_js
from server.net.deploy_net import DeployState

SETUP_BUTTON = "הגדרת רשת הפצה…"      # written out here on purpose: a rename must fail a test, not follow the code silently


def test_the_unconfigured_hint_names_the_setup_button_and_not_the_hidden_tab():
    hint = DeployState("none", None, None).public()["hint"]
    assert f'"{SETUP_BUTTON}"' in hint, hint
    assert "בלשונית רשת הפצה" not in hint, hint
    assert "בחר כרטיס" in hint, hint


def test_the_button_label_is_the_one_the_console_shows():
    assert f'"{SETUP_BUTTON}"' in console_js()
