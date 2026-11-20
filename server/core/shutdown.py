"""האם התהליך הזה כבר יודע שהוא יורד? (#3094)

`systemctl restart` שולח SIGTERM לכל ה-cgroup, וילד של השרת (`chunkcast send`) מת על האות
הזה בעצמו — לפעמים לפני שהשרת הגיע לסגירה המסודרת (`ChunkEngine.close` קובע `_stop`). מי
שרואה ילד שנפל באמצע גל לא יכול לדעת אם זה השולח או השרת. הראיה החיובית היא **האות שהגיע
לשרת עצמו**: `ports.Listeners._on_signal` — המטפל היחיד ב-SIGTERM/SIGINT של התהליך — קובע
את הדגל לפני כל דבר אחר. לא ניחוש לפי קוד היציאה של הילד.
"""

from __future__ import annotations

import threading

#: נקבע פעם אחת, כשהאות הגיע; אינו מתאפס — התהליך אחריו יורד.
GOING_DOWN = threading.Event()
