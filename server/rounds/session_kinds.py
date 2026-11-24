"""The kinds of a session (#2531): a leaf module, so `sessions` and `session_view` need not import each other."""
from __future__ import annotations

#: שידור מולטיקאסט — udp-sender אחד, ולכן חריץ אחד בכל המערכת.
MULTICAST = "multicast"
#: משיכת HTTP של תחנה בודדת — בלי חריץ, כמה במקביל.
UNICAST = "unicast"
KINDS = (MULTICAST, UNICAST)
