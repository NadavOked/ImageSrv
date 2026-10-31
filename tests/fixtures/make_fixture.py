r"""יוצר את tests/fixtures/system-mini.hiv — ‏hive רישום מינימלי לטסטים.

רץ על Windows בלבד, פעם אחת (התוצר נכנס ל-git). ‏RegLoadAppKey יוצר
קובץ hive חדש בלי הרשאות מנהל — בניגוד ל-reg save שדורש SeBackupPrivilege.
המבנה מחקה את הפינה של SYSTEM שהסוכן נוגע בה (agent/lib/postdeploy/hostname.sh):
‏Select, ‏ComputerName, ו-Tcpip\Parameters עם ערכים שכנים מגוונים —
שהטסטים יוכיחו ש-hivewrite משאיר אותם במקום (#33). ‏#1331: גם שני
שירותי האחסון ה-in-box (agent/lib/postdeploy/bootstorage.sh) — ‏storahci עם
‏StartOverride כמו במכונה שהותקנה במצב RAID, ו-stornvme בלעדיו.
"""

import ctypes
import winreg
from ctypes import wintypes
from pathlib import Path

OUT = Path(__file__).with_name("system-mini.hiv")
OUT.unlink(missing_ok=True)   # נבנה מאפס בכל הרצה — לא נטען ומתווסף

advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
KEY_ALL_ACCESS = 0xF003F

hkey = wintypes.HKEY()
rc = advapi32.RegLoadAppKeyW(
    str(OUT), ctypes.byref(hkey), KEY_ALL_ACCESS, 0, 0
)
if rc != 0:
    raise OSError(rc, "RegLoadAppKeyW failed")
# winreg מקבל גם handle גולמי (int) בכל מקום שמצופה מפתח פתוח.
root = hkey.value

def set_values(path: str, values: dict) -> None:
    key = winreg.CreateKeyEx(root, path, 0, winreg.KEY_ALL_ACCESS)
    with key:
        for name, (kind, data) in values.items():
            winreg.SetValueEx(key, name, 0, kind, data)

set_values("Select", {
    "Current": (winreg.REG_DWORD, 1),
    "Default": (winreg.REG_DWORD, 1),
})
set_values(r"ControlSet001\Control\ComputerName\ComputerName", {
    "ComputerName": (winreg.REG_SZ, "TEST-WIN11"),
})
set_values(r"ControlSet001\Services\Tcpip\Parameters", {
    "Hostname": (winreg.REG_SZ, "TEST-WIN11"),
    "NV Hostname": (winreg.REG_SZ, "TEST-WIN11"),
    "Domain": (winreg.REG_SZ, ""),
    "NameServer": (winreg.REG_SZ, "10.0.0.1"),
    "DataBasePath": (
        winreg.REG_EXPAND_SZ, r"%SystemRoot%\System32\drivers\etc"
    ),
    "EnableICMPRedirect": (winreg.REG_DWORD, 1),
})

# ‏#1331: הערכים כמו בשירות in-box שאינו boot-start. ‏Type/ErrorControl/
# ‏ImagePath/Group הם השכנים ש-`hivewrite -d` חייב להשאיר במקום.
for svc in ("storahci", "stornvme"):
    set_values(rf"ControlSet001\Services\{svc}", {
        "Start": (winreg.REG_DWORD, 3),
        "Type": (winreg.REG_DWORD, 1),
        "ErrorControl": (winreg.REG_DWORD, 3),
        "Group": (winreg.REG_SZ, "SCSI Miniport"),
        "ImagePath": (winreg.REG_EXPAND_SZ, rf"System32\drivers\{svc}.sys"),
    })
set_values(r"ControlSet001\Services\storahci\StartOverride", {
    "0": (winreg.REG_DWORD, 3),
})

# הסגירה מנתקת את ה-hive מהתהליך ומרוקנת אותו לדיסק.
advapi32.RegCloseKey(hkey)

# Windows משאיר קובצי יומן-טרנזקציות ריקים ליד ה-hive — לא חלק מה-fixture.
for log in OUT.parent.glob(OUT.name + ".LOG*"):
    log.unlink()
print(f"fixture written: {OUT} ({OUT.stat().st_size} bytes)")
