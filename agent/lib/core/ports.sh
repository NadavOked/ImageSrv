# ports.sh -- the agent's own ports (#3871, Nadav 16/11 in #3485). POSIX sh (busybox ash).
# The one place the agent names a port; server/core/ports.py is the server's, and
# tests/test_ports_3871.py holds the two equal. The server's address and the stream's
# ports are not here: imagesrv.server and the server's answers name them.

# The RFB monitor listens here, on the distribution address; the server connects to it.
IMAGESRV_MONITOR_PORT=7710
