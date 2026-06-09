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
	_validate_required(data)
	data = _normalize_fields(data)
	data["accessorial_rows"] = _resolve_accessorials(data.get("accessorials") or [])
	return data


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

	# Nested JSON body under `data` or `payload`
	for nested_key in ("data", "payload"):
		nested = data.get(nested_key)
		if isinstance(nested, str):
			nested = json.loads(nested)
		if isinstance(nested, dict):
			for key, value in nested.items():
				data.setdefault(key, value)

	return data


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
		code = _resolve_accessorial_code(item)
		if not code:
			continue
		name = frappe.db.get_value("LTL Accessorial", {"accessorial_code": code})
		if name:
			rows.append({"accessorial": name, "accessorial_code": code, "quantity": 1})
	return rows


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
