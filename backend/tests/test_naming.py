from __future__ import annotations

import pytest

from app.naming import parse_upload_name
from app.validation import UploadRejected


@pytest.mark.parametrize(
    ("filename", "event", "version"),
    [
        ("LIS26 | Event_Network_Requirements_V1.xlsx", "LIS26", 1),
        ("LIS25 _ Event_Network_Requirements_V5.xlsx", "LIS25", 5),
        ("LIS26_Event_Network_Requirements_V12.xlsx", "LIS26", 12),
        ("LIS26 - Event_Network_Requirements_V3.xlsx", "LIS26", 3),
        ("lis26 event_network_requirements_v2.xlsx", "LIS26", 2),
        ("WS27 _ Event_Network_Requirements V4.xlsx", "WS27", 4),
    ],
)
def test_accepted_names(filename: str, event: str, version: int) -> None:
    parsed = parse_upload_name(filename)
    assert (parsed.event, parsed.version) == (event, version)
    assert parsed.document_name == f"{event} | Event_Network_Requirements"


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("Budget_V1.xlsx", "must contain 'Event_Network_Requirements'"),
        ("LIS26 _ Network_Requirements_V1.xlsx", "must contain 'Event_Network_Requirements'"),
        ("Event_Network_Requirements_V1.xlsx", "must start with an event code"),
        ("LIS 26 _ Event_Network_Requirements_V1.xlsx", "must start with an event code"),
        ("LIS26 _ Event_Network_Requirements.xlsx", "must end with a version number"),
        ("LIS26 _ Event_Network_Requirements_V1 (1).xlsx", "must end with a version number"),
        ("LIS26 _ Event_Network_Requirements_final.xlsx", "must end with a version number"),
        ("LIS26 _ Event_Network_Requirements_V0.xlsx", "1 or higher"),
        (None, "must contain"),
    ],
)
def test_rejected_names(filename: str | None, message: str) -> None:
    with pytest.raises(UploadRejected, match=message):
        parse_upload_name(filename)
