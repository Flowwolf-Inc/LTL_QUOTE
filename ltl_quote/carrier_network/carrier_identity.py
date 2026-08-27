# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Resolve LTL Shipment / LTL Carrier records to a stable connector key."""

from __future__ import annotations

import frappe

CONNECTOR_DAYTON = "dayton"
CONNECTOR_TFORCE = "tforce"
CONNECTOR_ARCBEST = "arcbest"
CONNECTOR_MOCK = "mock"
CONNECTOR_SMC3 = "smc3"
CONNECTOR_OTHER = "other"

PICKUP_CONNECTORS = {CONNECTOR_DAYTON, CONNECTOR_TFORCE, CONNECTOR_ARCBEST}
TRACKING_CONNECTORS = {CONNECTOR_DAYTON, CONNECTOR_TFORCE, CONNECTOR_ARCBEST}

_CONNECTOR_TYPE_MAP = {
	"DAYTON": CONNECTOR_DAYTON,
	"TFORCE": CONNECTOR_TFORCE,
	"ARCBEST API": CONNECTOR_ARCBEST,
	"ARCBEST": CONNECTOR_ARCBEST,
	"SMC3": CONNECTOR_SMC3,
	"MOCK": CONNECTOR_MOCK,
}

_CODE_MAP = {
	"DAYTON": CONNECTOR_DAYTON,
	"TFORCE": CONNECTOR_TFORCE,
	"TFF": CONNECTOR_TFORCE,
	"ARCB": CONNECTOR_ARCBEST,
	"ARCBEST": CONNECTOR_ARCBEST,
	"ABF": CONNECTOR_ARCBEST,
	"ABFS": CONNECTOR_ARCBEST,
	"SMC3": CONNECTOR_SMC3,
	"MOCK": CONNECTOR_MOCK,
}

_LABELS = {
	CONNECTOR_DAYTON: "Dayton",
	CONNECTOR_TFORCE: "TForce",
	CONNECTOR_ARCBEST: "ArcBest",
	CONNECTOR_MOCK: "Mock",
	CONNECTOR_SMC3: "SMC3",
	CONNECTOR_OTHER: "Carrier",
}

_UI_CODES = {
	CONNECTOR_DAYTON: "DAYTON",
	CONNECTOR_TFORCE: "TFORCE",
	CONNECTOR_ARCBEST: "ARCB",
	CONNECTOR_MOCK: "MOCK",
	CONNECTOR_SMC3: "SMC3",
	CONNECTOR_OTHER: "OTHER",
}


def shipment_connector(shipment) -> str:
	"""Return dayton | tforce | arcbest | mock | other for a shipment or carrier-like object."""
	carrier_name = str(getattr(shipment, "carrier", None) or "").strip()
	carrier_code = str(getattr(shipment, "carrier_code", None) or "").strip().upper()
	carrier_label = str(getattr(shipment, "carrier_name", None) or "").strip()
	connector_type = str(getattr(shipment, "connector_type", None) or "").strip()

	if carrier_name and frappe.db.exists("LTL Carrier", carrier_name):
		row = frappe.db.get_value(
			"LTL Carrier",
			carrier_name,
			["connector_type", "carrier_code", "carrier_name"],
			as_dict=True,
		)
		if row:
			connector_type = connector_type or str(row.get("connector_type") or "")
			carrier_code = carrier_code or str(row.get("carrier_code") or "").upper()
			carrier_label = carrier_label or str(row.get("carrier_name") or "")

	mapped = _CONNECTOR_TYPE_MAP.get(connector_type.upper().strip())
	if mapped:
		return mapped

	mapped = _CODE_MAP.get(carrier_code) or _CODE_MAP.get(carrier_name.upper())
	if mapped:
		return mapped

	blob = f"{carrier_name} {carrier_code} {carrier_label} {connector_type}".lower()
	if "dayton" in blob:
		return CONNECTOR_DAYTON
	if "tforce" in blob or blob.strip() == "tff":
		return CONNECTOR_TFORCE
	if "arcbest" in blob or "abf" in blob:
		return CONNECTOR_ARCBEST
	if "mock" in blob:
		return CONNECTOR_MOCK
	if "smc3" in blob:
		return CONNECTOR_SMC3
	return CONNECTOR_OTHER


def connector_label(key: str) -> str:
	return _LABELS.get(key) or _LABELS[CONNECTOR_OTHER]


def connector_ui_code(key: str) -> str:
	return _UI_CODES.get(key) or _UI_CODES[CONNECTOR_OTHER]


def supports_pickup(shipment) -> bool:
	return shipment_connector(shipment) in PICKUP_CONNECTORS


def supports_tracking(shipment) -> bool:
	return shipment_connector(shipment) in TRACKING_CONNECTORS
