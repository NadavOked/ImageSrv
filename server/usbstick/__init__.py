"""#1920 -- the clone stick (IMGSRV_DATA) plugged into the server: its learning log
comes in, what the server knows goes out. Admin only, on a button, never automatic.

* ``device``    -- which block device is the stick (positive evidence only) and how
                   it is mounted, read-only until the operator confirms a sync.
* ``records``   -- the stick's ``reports/clone-*.json`` under a hard schema.
* ``knowledge`` -- what the server writes back, and nothing else.
* ``sync``      -- the plan the operator sees, and the one approved run of it.
"""
