# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""FLOWWOLF / BlueShip-style request payload normalization."""

from __future__ import annotations

import json
from typing import Any

import frappe

ACCESSORIAL_ALIASES = {
	"lift gate delivery": "LIFTGATE",
	"liftgate": "LIFTGATE",
	"lift gate": "LIFTGATE",
	"residential delivery": "RESIDENTIAL",
	"residential": "RESIDENTIAL",
	"delivery appointment": "APPOINTMENT",
	"appointment": "APPOINTMENT",
	"inside delivery": "INSIDE_DELIVERY",
	"hazmat": "HAZMAT",
	"hazmat handling": "HAZMAT",
	"limited access": "LIMITED_ACCESS",
}

SKIP_KEYS = {
	"cmd",
	"data",
	"payload",
	"save_request",
	"ignore_permissions",
}


def parse_rating_payload(payload: dict | str | None = None, **kwargs) -> dict:
	"""Parse and validate a unified rating request from REST clients or form data."""
	data = _coerce_payload(payload, kwargs)
	_expand_items_payload(data)
	_validate_required(data)
	data = _normalize_fields(data)
	accessorials = data.get("accessorials") or data.get("accessorial_codes") or []
	# Prefer structured accessorials (with pickup/delivery/load group) when both are present.
	structured = data.get("accessorials")
	if isinstance(structured, list) and structured and isinstance(structured[0], dict):
		accessorials = structured
	data["accessorial_rows"] = _resolve_accessorials(accessorials)
	return data


def read_http_request_body() -> dict:
	"""Capture raw JSON or form data from an incoming HTTP request (e.g. Postman)."""
	headers = dict(frappe.request.headers) if getattr(frappe, "request", None) else {}

	if getattr(frappe, "request", None) and frappe.request.data:
		try:
			body = json.loads(frappe.request.data.decode("utf-8"))
		except (ValueError, UnicodeDecodeError):
			body = dict(frappe.local.form_dict)
	else:
		body = dict(frappe.local.form_dict)

	if isinstance(body, dict):
		body.pop("cmd", None)

	return {"headers": headers, "body": body}


def _coerce_payload(payload: dict | str | None, kwargs: dict) -> dict:
	if isinstance(payload, str):
		payload = json.loads(payload)

	data: dict[str, Any] = {}
	if isinstance(payload, dict):
		data.update(payload)

	# Merge form_dict / legacy keyword arguments
	form_dict = dict(getattr(frappe.local, "form_dict", {}) or {})
	for key, value in {**form_dict, **kwargs}.items():
		if key in SKIP_KEYS or value in (None, ""):
			continue
		data.setdefault(key, value)

	# Raw JSON body from Postman / REST clients
	if getattr(frappe, "request", None) and frappe.request.data:
		try:
			raw_body = json.loads(frappe.request.data.decode("utf-8"))
			if isinstance(raw_body, dict):
				for key, value in raw_body.items():
					if key not in SKIP_KEYS and value not in (None, ""):
						data.setdefault(key, value)
		except (ValueError, UnicodeDecodeError):
			pass

	# Nested JSON body under `data` or `payload`
	for nested_key in ("data", "payload"):
		nested = data.get(nested_key)
		if isinstance(nested, str):
			nested = json.loads(nested)
		if isinstance(nested, dict):
			for key, value in nested.items():
				data.setdefault(key, value)

	return data


def _clean_freight_class(value) -> str:
	text = str(value or "").replace(".0", "").strip()
	if not text or text.lower() in {"none", "null"}:
		return ""
	return text


def line_item_freight_class(row, fallback: str = "") -> str:
	"""NMFC class from a UI line item. Prefers `nmfc_class` over header defaults like 70."""
	if isinstance(row, dict):
		data = row
	else:
		data = {
			"nmfc_class": getattr(row, "nmfc_class", None),
			"freight_class": getattr(row, "freight_class", None),
			"classification": getattr(row, "classification", None),
		}
	candidates = (
		data.get("nmfc_class"),
		data.get("freight_class"),
		data.get("classification"),
		data.get("class") if isinstance(row, dict) and not callable(data.get("class")) else None,
		fallback,
	)
	for value in candidates:
		cleaned = _clean_freight_class(value)
		if cleaned:
			return cleaned
	return ""


def _expand_items_payload(data: dict) -> None:
	"""Map Postman-style `items` array into top-level freight fields.

	Keeps the original `items` array on `data` for persistence / BOL booking.
	"""
	items = data.get("items") or []
	if not items:
		return

	# Normalize to a plain list of dicts so downstream code can rely on it.
	normalized = [item for item in items if isinstance(item, dict)]
	data["items"] = normalized
	if not normalized:
		return

	for item in normalized:
		item_class = line_item_freight_class(item)
		if item_class:
			item["nmfc_class"] = item_class
			item["freight_class"] = item_class
			item["classification"] = item_class
			item["class"] = item_class

	first = normalized[0]
	item_class = line_item_freight_class(first)
	if item_class:
		data["freight_class"] = item_class

	frappe.logger().info(f"Payload Items: {[item.get('class') for item in normalized]}")

	if not data.get("commodity_description"):
		data["commodity_description"] = (
			first.get("description") or first.get("commodity_description") or first.get("item_name") or ""
		)

	if not data.get("nmfc"):
		data["nmfc"] = first.get("nmfc") or first.get("nmfc_number") or ""

	if not data.get("weight") and not data.get("total_weight"):
		data["total_weight"] = sum(
			float(item.get("weight") or 0) * max(int(item.get("qty") or item.get("quantity") or 1), 1)
			for item in normalized
		)

	if not data.get("length"):
		for item in normalized:
			if item.get("length") and item.get("width") and item.get("height"):
				data["length"] = item.get("length")
				data["width"] = item.get("width")
				data["height"] = item.get("height")
				if item.get("dimension_unit") or item.get("dimension_units"):
					data["dimension_uom"] = item.get("dimension_unit") or item.get("dimension_units")
				break

	if not data.get("pieces"):
		data["pieces"] = sum(max(int(item.get("qty") or item.get("quantity") or 1), 1) for item in normalized)


def _validate_required(data: dict) -> None:
	required = ("origin_zip", "destination_zip", "freight_class")
	missing = [field for field in required if not data.get(field)]
	weight = data.get("weight") or data.get("total_weight")
	if not weight:
		missing.append("weight")
	if missing:
		frappe.throw(
			f"Missing required parameter(s): {', '.join(missing)}",
			frappe.ValidationError,
		)


def _normalize_fields(data: dict) -> dict:
	data["origin_zip"] = str(data["origin_zip"]).strip()
	data["destination_zip"] = str(data["destination_zip"]).strip()
	data["freight_class"] = str(data["freight_class"])
	data["total_weight"] = float(data.get("weight") or data.get("total_weight"))
	data["length"] = float(data.get("length") or 0)
	data["width"] = float(data.get("width") or 0)
	data["height"] = float(data.get("height") or 0)
	data["pieces"] = int(data.get("pieces") or 1)
	data["timeout"] = int(data.get("timeout") or 0)
	data["save_request"] = _as_bool(data.get("save_request", True))
	if data.get("commodity_description"):
		data["commodity_description"] = str(data["commodity_description"]).strip()
	if data.get("nmfc"):
		data["nmfc"] = str(data["nmfc"]).strip()
	# Keep items as a list of dicts for quote request persistence / BOL.
	items = data.get("items")
	if isinstance(items, list):
		data["items"] = [item for item in items if isinstance(item, dict)]
	for field in ("origin_city", "origin_state", "destination_city", "destination_state"):
		if data.get(field):
			data[field] = str(data[field]).strip()
	return data


def _as_bool(value) -> bool:
	if isinstance(value, bool):
		return value
	if isinstance(value, str):
		return value.lower() not in ("0", "false", "no")
	return bool(value)


def _resolve_accessorials(accessorials: list) -> list[dict]:
	rows = []
	for item in accessorials:
		if isinstance(item, dict):
			raw_code = item.get("code") or item.get("accessorial_code") or item.get("accessorial")
			code = _resolve_accessorial_code(raw_code)
			quantity = max(int(item.get("quantity") or item.get("qty") or 1), 1)
			service_group = str(item.get("service_group") or item.get("group") or "").strip().lower()
			label = str(item.get("label") or item.get("accessorial_name") or "").strip()
		else:
			raw_code = item
			code = _resolve_accessorial_code(item)
			quantity = 1
			service_group = ""
			label = ""
		if not code:
			# Allow synced Dayton catalog codes that are not yet in LTL Accessorial.
			code = _resolve_dayton_catalog_code(raw_code)
		if not code:
			continue
		name = frappe.db.get_value("LTL Accessorial", {"accessorial_code": code})
		if not name:
			name = _ensure_ltl_accessorial(code, label)
		if not name:
			continue
		row = {"accessorial": name, "accessorial_code": code, "quantity": quantity}
		if service_group in {"pickup", "delivery", "load", "origin", "destination"}:
			if service_group == "origin":
				service_group = "pickup"
			elif service_group == "destination":
				service_group = "delivery"
			row["service_group"] = service_group
		if label:
			row["accessorial_name"] = label
		rows.append(row)
	return rows


def _resolve_dayton_catalog_code(value) -> str | None:
	"""Return an uppercase Dayton Accessorial code when present in the synced catalog."""
	if not value:
		return None
	code = str(value).strip().upper()
	if not code:
		return None
	if frappe.db.exists("Dayton Accessorial", {"code": code}):
		return code
	if frappe.db.exists("Dayton Response Accessorial", code):
		return code
	return None


def _ensure_ltl_accessorial(code: str, label: str = "") -> str | None:
	"""Create a lightweight LTL Accessorial master row for a Dayton catalog code."""
	code = str(code or "").strip().upper()
	if not code:
		return None
	existing = frappe.db.get_value("LTL Accessorial", {"accessorial_code": code}, "name")
	if existing:
		return existing
	description = label or code
	if frappe.db.exists("Dayton Accessorial", {"code": code}):
		description = (
			frappe.db.get_value("Dayton Accessorial", {"code": code}, "description") or description
		)
	elif frappe.db.exists("Dayton Response Accessorial", code):
		description = (
			frappe.db.get_value("Dayton Response Accessorial", code, "description") or description
		)
	doc = frappe.get_doc(
		{
			"doctype": "LTL Accessorial",
			"accessorial_code": code,
			"accessorial_name": description,
			"charge_type": "Flat",
			"default_amount": 0,
			"currency": "USD",
			"description": f"Synced from Dayton catalog ({code})",
		}
	)
	doc.insert(ignore_permissions=True)
	return doc.name


def _resolve_accessorial_code(value: str) -> str | None:
	if not value:
		return None
	raw = str(value).strip()
	if frappe.db.exists("LTL Accessorial", raw):
		return frappe.db.get_value("LTL Accessorial", raw, "accessorial_code")
	if frappe.db.exists("LTL Accessorial", {"accessorial_code": raw.upper()}):
		return raw.upper()
	by_name = frappe.db.get_value("LTL Accessorial", {"accessorial_name": raw}, "accessorial_code")
	if by_name:
		return by_name
	return ACCESSORIAL_ALIASES.get(raw.lower())
