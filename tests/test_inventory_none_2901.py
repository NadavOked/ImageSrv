"""#2901: המחרוזת "None" ש-VMware מדווח בשדה DMI ריק אינה ערך — היא לא נכנסת לשם הדגם ("VMware, Inc. None")."""
from __future__ import annotations

from server.agent import inventory


def test_a_literal_none_in_a_dmi_field_is_null():
    got = inventory.well_formed({"dmi": {"sys_vendor": "VMware, Inc.", "product_name": "VMware Virtual Platform",
                                         "product_version": "None", "board_name": " none "}, "pci": [], "tpm": None})
    assert got is not None
    assert got["dmi"]["product_version"] is None and got["dmi"]["board_name"] is None
    assert got["dmi"]["sys_vendor"] == "VMware, Inc."


def test_a_real_value_that_merely_contains_none_stays():
    got = inventory.well_formed({"dmi": {"sys_vendor": "None of Us Ltd", "product_name": "Nonesuch 5"}, "pci": [], "tpm": None})
    assert got["dmi"]["sys_vendor"] == "None of Us Ltd" and got["dmi"]["product_name"] == "Nonesuch 5"
