"""#2097: only the installer's destructive apply button uses the danger style."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"


def _c(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_the_summary_apply_button_is_danger():
    assert 'install_footer_style(a,cr,l,"החל התקנה",HIT_INSTALL_NEXT,1,1,BTN_DANGER);' in _c("screens_install_final.c")


def test_next_and_reboot_keep_the_primary_footer():
    for name, count in (("screens_install_setup.c", 3), ("screens_install_forms.c", 4)):
        source = _c(name)
        assert source.count('install_footer(a,cr,l,"הבא",HIT_INSTALL_NEXT,') == count
        assert not re.search(r'install_footer_style\([^;]*"הבא"', source)
    assert 'install_footer(a,cr,l,"הפעל מחדש",HIT_INSTALL_NEXT,0,1);' in _c("screens_install_final.c")


def test_the_footer_wrapper_defaults_to_primary_and_forwards_the_style():
    source = _c("screens_install.c")
    wrapper = re.search(r'void install_footer\([^{}]+\)\{([^{}]*)\}', source)
    assert wrapper is not None
    assert wrapper.group(1) == "install_footer_style(a,cr,l,primary,primary_id,allow_back,enabled,BTN_PRIMARY);"
    assert "if(primary_enabled)draw_btn(a,cr,pr,&p,primary_style,primary_id);" in source
    assert re.search(r'void install_footer_style\([^;]+int primary_enabled,\s*int primary_style\);', _c("screens_install.h"))


def test_no_other_install_screen_uses_a_danger_footer():
    danger_calls = []
    for path in sorted(SRC.glob("screens_install*.c")):
        calls = re.findall(r'install_footer_style\([^;]*\bBTN_DANGER\s*\);', path.read_text(encoding="utf-8"))
        danger_calls.extend((path.name, call) for call in calls)
    assert danger_calls == [("screens_install_final.c", 'install_footer_style(a,cr,l,"החל התקנה",HIT_INSTALL_NEXT,1,1,BTN_DANGER);')]
