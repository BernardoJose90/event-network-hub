from __future__ import annotations

import pytest

from app.naming import parse_upload_name
from app.validation import UploadRejected


@pytest.mark.parametrize(
    ("filename", "event", "version"),
    [
        ("LIS25 _ Event_Network_Requirements_V5.xlsx", "LIS25", 5),
        ("LIS26 | Event_Network_Requirements_V1.xlsx", "LIS26", 1),
        ("LIS26_Event_Network_Requirements_V12.xlsx", "LIS26", 12),
        ("VAN26 _ Event Network Requirements - V1.xlsx", "VAN26", 1),
        ("VAN26 - Event-Network-Requirements-V3.xlsx", "VAN26", 3),
        ("VAN26 Event Network Requirements V2.xlsx", "VAN26", 2),
        ("VAN26 _ Event_Network Requirements_V 4.xlsx", "VAN26", 4),
        ("van26 _ event_network_requirements_v2.xlsx", "VAN26", 2),
    ],
)
def test_accepted_names(filename: str, event: str, version: int) -> None:
    parsed = parse_upload_name(filename)
    assert (parsed.event, parsed.version) == (event, version)
    assert parsed.document_name == f"{event} | Event_Network_Requirements"


@pytest.mark.parametrize(
    ("filename", "message"),
    [
        ("Budget_V1.xlsx", "must contain 'Event Network Requirements'"),
        ("LIS26 _ Network_Requirements_V1.xlsx", "must contain 'Event Network Requirements'"),
        ("LIS26 _ EventNetworkRequirements_V1.xlsx", "must contain 'Event Network Requirements'"),
        ("LIS26Event_Network_Requirements_V1.xlsx", "must contain 'Event Network Requirements'"),
        ("Event_Network_Requirements_V1.xlsx", "event code"),
        ("LIS 26 _ Event_Network_Requirements_V1.xlsx", "event code"),
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


def test_spelling_variants_map_to_the_same_document_and_version() -> None:
    names = [
        "VAN26 _ Event_Network_Requirements_V1.xlsx",
        "VAN26 _ Event Network Requirements - V1.xlsx",
        "VAN26 | Event-Network-Requirements V1.xlsx",
        "van26_event_network_requirements_v1.xlsx",
    ]
    assert {(p.document_name, p.version) for p in map(parse_upload_name, names)} == {
        ("VAN26 | Event_Network_Requirements", 1)
    }
