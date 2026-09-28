# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""List, enable, or disable LTL Carriers used as quote sources."""

from __future__ import annotations

import frappe
from frappe.utils import cint

from ltl_quote.api.carrier_mapping import CARRIER_DOC_IDS

ENABLE_WORDS = {"enable", "enabled", "on", "1", "true", "yes"}
DISABLE_WORDS = {"disable", "disabled", "off", "0", "false", "no"}
STATUS_FILTERS = {"all", "enabled", "disabled"}


@frappe.whitelist(methods=["GET", "POST"])
def list_quote_sources(status=None):
	"""Return every LTL Carrier, grouped by the Enabled flag.

	GET and POST both use the same URL:

	    /api/method/ltl_quote.api.carrier.list_quote_sources

	All carriers (default):

	    GET /api/method/ltl_quote.api.carrier.list_quote_sources
	    {"status": "all"}

	Enabled only:

	    ?status=enabled

	Disabled only:

	    ?status=disabled

	Any logged-in user may call this. The payload is carrier identity and the
	Enabled flag only; API keys and secrets are not read.
	"""
	if frappe.session.user == "Guest":
		frappe.throw("Login to list quote sources.", frappe.PermissionError)

	scope = _parse_status(status)
	rows = frappe.get_all(
		"LTL Carrier",
		fields=["name", "carrier_name", "connector_type", "scac", "enabled"],
		order_by="carrier_name asc",
	)
	enabled = []
	disabled = []
	for row in rows:
		item = _list_item(row)
		if item["enabled"]:
			enabled.append(item)
		else:
			disabled.append(item)

	payload = {
		"status": "success",
		"count": len(enabled) + len(disabled),
		"enabled_count": len(enabled),
		"disabled_count": len(disabled),
	}
	if scope in ("all", "enabled"):
		payload["enabled"] = enabled
	if scope in ("all", "disabled"):
		payload["disabled"] = disabled
	if scope != "all":
		payload["count"] = len(payload.get("enabled") or payload.get("disabled") or [])
	return payload


@frappe.whitelist(methods=["GET", "POST"])
def set_carrier_enabled(carrier=None, enabled=None, action=None):
	"""Turn an LTL Carrier on or off.

	GET and POST both use the same URL:

	    /api/method/ltl_quote.api.carrier.set_carrier_enabled

	Enable Dayton:

	    ?carrier=DAYTON&enabled=1
	    ?carrier=DAYTON&action=enable

	Disable Dayton:

	    {"carrier": "DAYTON", "enabled": 0}
	    {"carrier": "DAYTON", "action": "disable"}

	Omit enabled and action to read the current flag without changing it.
	"""
	name = _resolve_carrier(carrier)
	if not frappe.has_permission("LTL Carrier", "read", name):
		frappe.throw("Not permitted to read this carrier.", frappe.PermissionError)

	flag = _parse_enabled(enabled, action)
	if flag is None:
		current = cint(frappe.db.get_value("LTL Carrier", name, "enabled"))
		return _payload(name, current)

	if not frappe.has_permission("LTL Carrier", "write", name):
		frappe.throw("Not permitted to enable or disable this carrier.", frappe.PermissionError)

	frappe.db.set_value("LTL Carrier", name, "enabled", flag, update_modified=True)
	frappe.db.commit()
	return _payload(name, flag)


def _list_item(row) -> dict:
	flag = cint(row.get("enabled") if isinstance(row, dict) else getattr(row, "enabled", 0))
	name = row.get("name") if isinstance(row, dict) else getattr(row, "name", "")
	carrier_name = row.get("carrier_name") if isinstance(row, dict) else getattr(row, "carrier_name", None)
	connector = row.get("connector_type") if isinstance(row, dict) else getattr(row, "connector_type", None)
	scac = row.get("scac") if isinstance(row, dict) else getattr(row, "scac", None)
	return {
		"carrier": name,
		"carrier_name": carrier_name or name,
		"connector_type": connector or "",
		"scac": scac or "",
		"enabled": int(flag),
	}


def _parse_status(status) -> str:
	if status in (None, ""):
		return "all"
	word = str(status).strip().lower()
	if word in STATUS_FILTERS:
		return word
	frappe.throw("status must be enabled, disabled, or all.")


def _payload(name: str, flag: int) -> dict:
	carrier_name = frappe.db.get_value("LTL Carrier", name, "carrier_name")
	return {
		"status": "success",
		"carrier": name,
		"carrier_name": carrier_name,
		"enabled": int(flag),
		"action": "enable" if flag else "disable",
	}


def _resolve_carrier(carrier) -> str:
	raw = str(carrier or "").strip()
	if not raw:
		frappe.throw("Carrier is required.")

	found = frappe.db.get_value("LTL Carrier", raw, "name")
	if found:
		return found

	mapped = CARRIER_DOC_IDS.get(raw.upper())
	if mapped:
		found = frappe.db.get_value("LTL Carrier", mapped, "name")
		if found:
			return found

	by_code = frappe.db.get_value("LTL Carrier", {"carrier_code": raw.upper()}, "name")
	if by_code:
		return by_code

	by_name = frappe.db.get_value("LTL Carrier", {"carrier_name": raw}, "name")
	if by_name:
		return by_name

	frappe.throw(f"Carrier {raw} was not found.")


def _parse_enabled(enabled, action) -> int | None:
	if action not in (None, ""):
		word = str(action).strip().lower()
		if word in ENABLE_WORDS:
			return 1
		if word in DISABLE_WORDS:
			return 0
		frappe.throw("action must be enable or disable.")

	if enabled in (None, ""):
		return None

	word = str(enabled).strip().lower()
	if word in ENABLE_WORDS:
		return 1
	if word in DISABLE_WORDS:
		return 0
	return 1 if cint(enabled) else 0
