"""Record types of an event's network requirements, and their validation.

Records are stored as JSON in one table (see store.py); these schemas define
and validate each type's fields. List-backed fields (VLAN, location) are
checked against the event's managed lists in service.py.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Text = Annotated[str | None, Field(default=None, max_length=500)]
LongText = Annotated[str | None, Field(default=None, max_length=2000)]
# Whole numbers stay integers (7, not 7.0), as they appear in the sheet.
Count = (Annotated[int, Field(ge=0, le=1_000_000)] | Annotated[float, Field(ge=0, le=1_000_000)] | None)

RecordType = Literal["wifi", "ethernet", "partner", "vlan_extension"]
ListKind = Literal["building", "location", "vlan", "bandwidth"]
GroupKind = Literal["wifi", "ethernet"]

# Which record types live inside a venue and a group (section).
VENUE_TYPES = frozenset({"wifi", "ethernet"})
GROUP_KIND = {"wifi": "wifi", "ethernet": "ethernet"}


# Wi-Fi band dropdown. Older values outside these (e.g. "6 GHz") are kept until changed.
BAND_OPTIONS = ("5 GHz & 6 GHz", "5 GHz")
_BAND_SPELLINGS = {"5ghz": "5 GHz", "6ghz": "6 GHz", "5ghz&6ghz": "5 GHz & 6 GHz", "6ghz&5ghz": "5 GHz & 6 GHz"}


def normalise_band(value: str | None) -> str | None:
    """'5Ghz', '5 ghz' -> '5 GHz'; '5GHz & 6GHz' -> '5 GHz & 6 GHz'; anything else unchanged."""
    if not value:
        return value
    return _BAND_SPELLINGS.get(re.sub(r"\s+", "", value).lower().replace("and", "&"), value)


class _Data(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def _blank_is_none(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            return v or None
        return v


class WifiData(_Data):
    ref_id: Text = None  # the row number, computed for display (service.row_numbers); never stored
    ssid: Text = None
    coverage: Text = None
    password_required: Literal["yes", "no", "open network"] | None = None
    broadcast: Literal["yes", "no"] | None = None
    vlan: Text = None  # from the VLAN list
    band: Text = None  # BAND_OPTIONS for new values; checked in service.py
    password: Text = None  # never returned by list endpoints; see /reveal
    purpose: Text = None

    @field_validator("band")
    @classmethod
    def _band_spelling(cls, v: str | None) -> str | None:
        return normalise_band(v)


class EthernetData(_Data):
    ref_id: Text = None  # the row number, computed for display (service.row_numbers); never stored
    purpose: Text = None
    location: Text = None  # from the building/location lists
    vlan: Text = None  # from the VLAN list
    room: Text = None
    bandwidth_ns: Text = None
    bandwidth_ew: Text = None
    capacity: Text = None
    ports: Count = None
    notes: LongText = None


class PartnerData(_Data):
    ref_id: Text = None  # the row number, computed for display (service.row_numbers); never stored
    partner: Text = None
    pavilion: Text = None
    stand: Text = None
    solution: Text = None
    bandwidth_mbps: Count = None
    default_cables: Count = None
    total_ports: Count = None
    wifi: Count = None
    vlans: Count = None
    notes: LongText = None
    purchased: Literal["yes", "no", "tbc"] | None = None


class VlanExtensionData(_Data):
    vlan: Text = None  # from the VLAN list
    venues: dict[str, bool] = Field(default_factory=dict)  # venue id -> extended there

    @field_validator("venues", mode="before")
    @classmethod
    def _venue_keys(cls, v: Any) -> Any:
        if isinstance(v, dict) and len(v) > 50:
            raise ValueError("too many venues")
        return v


SCHEMAS: dict[str, type[_Data]] = {
    "wifi": WifiData,
    "ethernet": EthernetData,
    "partner": PartnerData,
    "vlan_extension": VlanExtensionData,
}

# Fields checked against the managed lists: field -> list kinds it may come from.
LIST_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "wifi": {"vlan": ("vlan",)},
    "ethernet": {"vlan": ("vlan",), "location": ("building", "location")},
    "partner": {},
    "vlan_extension": {"vlan": ("vlan",)},
}

SECRET_FIELDS = {"wifi": frozenset({"password"})}

# Human labels, used by the change log, the Excel export and the report.
LABELS: dict[str, dict[str, str]] = {
    "wifi": {
        "ref_id": "ID", "ssid": "SSID", "coverage": "Coverage area", "password_required": "Password?",
        "broadcast": "Broadcast?", "vlan": "VLAN", "band": "Band", "password": "Password value",
        "purpose": "Purpose",
    },
    "ethernet": {
        "ref_id": "ID", "purpose": "Purpose", "location": "Location", "vlan": "VLAN", "room": "Area/Room Number",
        "bandwidth_ns": "Bandwidth per device North - South", "bandwidth_ew": "Bandwidth per device East - West",
        "capacity": "Capacity", "ports": "Ethernet Points", "notes": "Notes",
    },
    "partner": {
        "ref_id": "ID", "partner": "Partner & Exhibitor", "pavilion": "Pav", "stand": "Stand Number",
        "solution": "Network Solution", "bandwidth_mbps": "Bandwidth Amount", "default_cables": "Default Cable",
        "total_ports": "Total ethernet ports", "wifi": "WiFi", "vlans": "Vlan", "notes": "Note(s)",
        "purchased": "Purchased",
    },
    "vlan_extension": {"vlan": "Vlan name", "venues": "Venues"},
}

TYPE_LABELS = {"wifi": "Wi-Fi network", "ethernet": "Ethernet requirement", "partner": "Partner order",
               "vlan_extension": "VLAN extension"}


def validate_data(record_type: str, data: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalise a record's data; returns it without empty fields."""
    model = SCHEMAS[record_type].model_validate(data)
    return model.model_dump(exclude_none=True)


class NewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue_id: int | None = None
    group_id: int | None = None
    before_id: int | None = None  # insert before this record (same venue/group); None = at the end
    data: dict[str, Any] = Field(default_factory=dict)


class RecordPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    data: dict[str, Any] = Field(default_factory=dict)  # only the fields being changed; null clears


class RecordMove(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    before_id: int | None = None
    group_id: int | None = None


class ListValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(min_length=1, max_length=200)


class NamedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
