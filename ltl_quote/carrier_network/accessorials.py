# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Shared accessorial code normalization and carrier-specific mappings."""

from __future__ import annotations

from ltl_quote.carrier_network.adapters.base import AccessorialItem

# ArcBest XML rate quote flags (aquotexml.asp)
ARCBEST_ACCESSORIAL_PARAMS: dict[str, str] = {
	"LIFTGATE": "Acc_GRD_DEL",
	"RESIDENTIAL": "Acc_RDEL",
	"HAZMAT": "Acc_HAZ",
	"APPOINTMENT": "Acc_APPT",
	"INSIDE_DELIVERY": "Acc_IDEL",
	"LIMITED_ACCESS": "Acc_LAD",
}

# Dayton Freight delivery special service codes (api.daytonfreight.com/documentation/details)
DAYTON_DELIVERY_ACCESSORIAL_CODES: dict[str, int] = {
	"LIFTGATE": 29,
	"RESIDENTIAL": 23,
	"APPOINTMENT": 84,
	"INSIDE_DELIVERY": 27,
	"LIMITED_ACCESS": 132,
}


def normalize_accessorial_code(code: str | None) -> str | None:
	if not code:
		return None
	return str(code).strip().upper()


def build_accessorial_items(rows) -> list[AccessorialItem]:
	"""Build AccessorialItem list from LTL Quote Request accessorial child rows."""
	items: list[AccessorialItem] = []
	for row in rows or []:
		code = normalize_accessorial_code(getattr(row, "accessorial_code", None))
		if not code and getattr(row, "accessorial", None):
			import frappe

			code = normalize_accessorial_code(
				frappe.db.get_value("LTL Accessorial", row.accessorial, "accessorial_code")
			)
		if not code:
			continue
		quantity = max(int(getattr(row, "quantity", None) or 1), 1)
		items.append(AccessorialItem(code=code, quantity=quantity))
	return items


def build_accessorial_items_from_payload(rows: list[dict]) -> list[AccessorialItem]:
	items: list[AccessorialItem] = []
	for row in rows or []:
		if isinstance(row, dict):
			code = normalize_accessorial_code(row.get("accessorial_code") or row.get("code"))
			quantity = max(int(row.get("quantity") or row.get("qty") or 1), 1)
		else:
			code = normalize_accessorial_code(str(row))
			quantity = 1
		if code:
			items.append(AccessorialItem(code=code, quantity=quantity))
	return items


def unique_accessorial_codes(accessorials: list[AccessorialItem]) -> list[str]:
	"""Unique codes for flag-based carrier APIs (quantity enables the flag once)."""
	seen: set[str] = set()
	ordered: list[str] = []
	for item in accessorials:
		if item.code and item.code not in seen:
			seen.add(item.code)
			ordered.append(item.code)
	return ordered


def expanded_accessorial_codes(accessorials: list[AccessorialItem]) -> list[str]:
	"""Codes repeated by quantity for per-unit pricing (mock adapter)."""
	expanded: list[str] = []
	for item in accessorials:
		if not item.code:
			continue
		expanded.extend([item.code] * max(int(item.quantity or 1), 1))
	return expanded


def arcbest_accessorial_params(accessorials: list[AccessorialItem]) -> dict[str, str]:
	params: dict[str, str] = {}
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		param_name = ARCBEST_ACCESSORIAL_PARAMS.get(item.code)
		if param_name:
			params[param_name] = "Y"
	return params


def dayton_rate_accessorials(accessorials: list[AccessorialItem]) -> list[int]:
	"""Map seeded accessorial codes to Dayton delivery service codes."""
	codes: list[int] = []
	seen: set[int] = set()
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		dayton_code = DAYTON_DELIVERY_ACCESSORIAL_CODES.get(item.code)
		if dayton_code is not None and dayton_code not in seen:
			codes.append(dayton_code)
			seen.add(dayton_code)
	return codes
