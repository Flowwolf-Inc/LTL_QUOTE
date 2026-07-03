# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Shared accessorial code normalization and carrier-specific mappings."""

from __future__ import annotations

from ltl_quote.carrier_network.adapters.base import AccessorialItem


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


def carrier_accessorial_map(carrier_doc) -> dict[str, str]:
	"""Read enabled accessorial mappings from a carrier into {internal_code: carrier_code}."""
	mapping: dict[str, str] = {}
	if not carrier_doc:
		return mapping
	for row in carrier_doc.get("accessorial_mappings") or []:
		if not getattr(row, "enabled", 1):
			continue
		internal_code = normalize_accessorial_code(
			getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None)
		)
		carrier_code = (getattr(row, "carrier_accessorial_code", None) or "").strip()
		if internal_code and carrier_code:
			mapping[internal_code] = carrier_code
	return mapping


def arcbest_accessorial_params(accessorials: list[AccessorialItem], carrier_doc) -> dict[str, str]:
	"""Build ArcBest XML flag params from the carrier's accessorial mapping table."""
	code_map = carrier_accessorial_map(carrier_doc)
	params: dict[str, str] = {}
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		param_name = code_map.get(normalize_accessorial_code(item.code))
		if param_name:
			params[param_name] = "Y"
	return params


def dayton_rate_accessorials(accessorials: list[AccessorialItem], carrier_doc) -> list:
	"""Map accessorial codes to Dayton service codes via the carrier mapping table.

	Dayton's REST rate API accepts the string service codes returned by
	GET /api/Shipping/Accessorials (e.g. ``LIFT``, ``RESID``). Numeric-only codes are
	coerced to int for backward compatibility with any legacy integer mappings.
	"""
	code_map = carrier_accessorial_map(carrier_doc)
	codes: list = []
	seen: set = set()
	for item in accessorials:
		if not item.code or item.quantity < 1:
			continue
		mapped = code_map.get(normalize_accessorial_code(item.code))
		if not mapped:
			continue
		value = str(mapped).strip()
		if not value:
			continue
		code = int(value) if value.isdigit() else value
		if code not in seen:
			codes.append(code)
			seen.add(code)
	return codes
