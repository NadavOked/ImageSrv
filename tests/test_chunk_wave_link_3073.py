"""#3073: the ceiling of a chunk-engine wave comes from the card that wave goes out on.

Since #3075 a chunk wave picks its own card for every wave (`ChunkEngine.select_interface`, as #2668),
but `wave_bitrate` read the link of udp-sender's card (`SenderEngine.link` -> `sender.interface`: the
last udpcast wave's, or the startup card). On a server with two deploy cards of different speeds
(1G and 10G) the chunk wave got 90% of the other card's link: too high (loss on the slow card) or too low.

The fix (server/app.py, server/rounds/sender.py): the chunk engine gets its own `wave_bitrate`, which
reads `sender.link_of(chunk.interface)` -- the same reader (`link_speed`), the chunk wave's card.

#3173: udp-sender is gone, and the reader is the chunk engine's own (`ctx.chunk.link_speed`,
`ctx.chunk.link_of`). What is left of the claim: of the two cards the reader knows, the one read is the
chunk wave's (`ctx.chunk.interface`), and the rate and the ceiling are that card's. The guard that the
udpcast wave read udp-sender's card went with udp-sender.

Negative control: server/app.py and server/rounds/sender.py of lab/vm-1113g (e08f8322) -- the two
chunk tests fail (the 10G card's 6000m on a 1G chunk card, and the other way round).
"""

from __future__ import annotations

import pytest

from server.rounds import drive_rates
from test_rate_by_nic_speed import nic_room  # noqa: F401 -- shared fixture
from test_server_room import room_server  # noqa: F401 -- nic_room's own fixture

pytest.importorskip("fastapi")

SPEEDS = {"eth0": 10000, "eth1": 1000}


def _chunk_card(nic_room, chunk_card):
    """The chunk wave's card, of two the reader knows, each with its own speed; every read is recorded."""
    from test_room_learned_rate import _open_and_join  # noqa: PLC0415
    ctx, asked = nic_room["ctx"], []

    def reader(iface):
        asked.append(iface)
        return {"interface": iface, "speed_mbps": SPEEDS[iface], "why": None}

    ctx.chunk.link_speed = reader
    ctx.chunk.interface = chunk_card
    wave = _open_and_join(nic_room)      # the wave stays open (target 5 of 4 drawers): nothing is sent yet
    asked.clear()
    return ctx, wave, asked


@pytest.mark.parametrize("chunk_card, expected", [
    ("eth1", "600m"),                    # the chunk wave on 1G: 60% of 1G, not of 10G
    ("eth0", "6000m"),                   # and the other way round
])
def test_a_chunk_wave_takes_its_rate_from_its_own_card(nic_room, chunk_card, expected):
    ctx, wave, asked = _chunk_card(nic_room, chunk_card)
    assert ctx.chunk.wave_bitrate({"id": wave}) == expected
    assert asked == [chunk_card]
    stored = drive_rates.decision(ctx.conn, wave)
    assert stored["link"]["interface"] == chunk_card and stored["link"]["speed_mbps"] == SPEEDS[chunk_card]
    assert stored["ceiling"] == ("900m" if chunk_card == "eth1" else "9000m")
