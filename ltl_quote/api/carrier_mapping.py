# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Map user-facing carrier_preference strings to LTL Carrier DocType names."""

from __future__ import annotations

import frappe

# User-facing aliases -> LTL Carrier.name (autoname = carrier_code)
CARRIER_DOC_IDS = {
	"DAYTON": "DAYTON",
	"ARCBEST": "ARCB",
	"ARCB": "ARCB",
	"ABF": "ARCB",
	"ABFS": "ARCB",
	"TFORCE": "TFORCE",
	"TFF": "TFORCE",
	"MOCK": "MOCK",
}


def resolve_carrier_id(raw_preference: str | None) -> str | None:
	"""
	Resolve a Postman/API carrier_preference string to an LTL Carrier DocName.

	Returns:
	    - "DAYTON", "ARCB", or "MOCK" when matched
	    - None when no preference (aggregate all enabled carriers)
	"""
	if not raw_preference:
		return None

	raw = str(raw_preference).strip()
	upper = raw.upper()

	if upper in CARRIER_DOC_IDS:
		return CARRIER_DOC_IDS[upper]

	if "DAYTON" in upper:
		return "DAYTON"

	if any(token in upper for token in ("TFORCE", "TFORCE FREIGHT", "TFF")):
		return "TFORCE"

	if any(token in upper for token in ("ARCBEST", "ARCB", "ABF", "ABFS")):
		return "ARCB"

	if "MOCK" in upper:
		return "MOCK"

	if frappe.db.exists("LTL Carrier", raw):
		return raw

	by_code = frappe.db.get_value("LTL Carrier", {"carrier_code": upper}, "name")
	if by_code:
		return by_code

	by_name = frappe.db.get_value("LTL Carrier", {"carrier_name": raw}, "name")
	if by_name:
		return by_name

	# Unrecognized explicit preference — route to mock carriers for safe dev testing
	return "MOCK"


def load_carrier_for_rating(carrier_id: str | None) -> tuple[list, str]:
	"""
	Verify carrier configuration and return documents ready for the rate engine.

	Raises frappe.DoesNotExistError when a specific carrier ID is missing from the DB.
	"""
	if not carrier_id:
		from ltl_quote.carrier_network.registry import get_enabled_carriers

		return get_enabled_carriers(), "Multi-Carrier"

	if carrier_id == "MOCK":
		from ltl_quote.carrier_network.registry import _ensure_mock_carriers

		return _ensure_mock_carriers(), "Mock Carriers"

	if not frappe.db.exists("LTL Carrier", carrier_id):
		raise frappe.DoesNotExistError(f"LTL Carrier '{carrier_id}' not found")

	carrier_doc = frappe.get_doc("LTL Carrier", carrier_id)
	return [carrier_doc], carrier_doc.carrier_name


def log_carrier_label(carrier_id: str | None, carrier_doc_name: str | None = None) -> str:
	"""Map carrier ID to a value accepted by LTL Carrier Transaction Log.carrier_name."""
	if carrier_doc_name:
		name = carrier_doc_name.lower()
		if "dayton" in name:
			return "Dayton Freight"
		if "tforce" in name:
			return "TForce Freight"
		if "arc" in name or "abf" in name:
			return "ArcBest"

	if carrier_id == "DAYTON":
		return "Dayton Freight"
	if carrier_id == "TFORCE":
		return "TForce Freight"
	if carrier_id == "ARCB":
		return "ArcBest"
	if carrier_id == "MOCK":
		return "Mock Carriers"
	return "Multi-Carrier"
