# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Per-login Quote Source and Accessorial preferences."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import frappe
from frappe.utils import cint


@contextmanager
def _ignore_permissions():
	previous = frappe.flags.ignore_permissions
	frappe.flags.ignore_permissions = True
	try:
		yield
	finally:
		frappe.flags.ignore_permissions = previous


STANDARD_ACCESSORIALS = {
	"pickup": [
		("LIFTGATE", "Liftgate Pickup"),
		("INSIDE_DELIVERY", "Inside Pickup"),
	],
	"delivery": [
		("LIFTGATE", "Liftgate Delivery"),
		("INSIDE_DELIVERY", "Inside Delivery"),
		("RESIDENTIAL", "Residential Delivery"),
		("APPOINTMENT", "Notify Before Delivery"),
	],
	"load": [
		("LIMITED_ACCESS", "Limited Access"),
		("HAZMAT", "Hazmat Handling"),
		("APPOINTMENT", "Delivery Appointment"),
	],
}

SERVICE_GROUPS = ("pickup", "delivery", "load")


def session_user() -> str | None:
	try:
		user = getattr(getattr(frappe, "session", None), "user", None)
	except Exception:
		return None
	user = str(user or "").strip()
	if not user or user == "Guest":
		return None
	return user


def get_user_settings_name(user: str | None = None) -> str | None:
	user = user or session_user()
	if not user:
		return None
	try:
		return frappe.db.get_value("LTL User Settings", {"user": user}, "name")
	except Exception:
		return None


def get_user_enabled_carrier_ids(user: str | None = None, create: bool = False) -> set[str] | None:
	"""Return enabled carrier names for the user, or None when no settings exist.

	None means callers should fall back to every globally enabled LTL Carrier.
	An empty set means the user turned every source off.
	"""
	user = user or session_user()
	if not user:
		return None
	name = get_user_settings_name(user)
	if not name and create:
		doc = ensure_user_settings(user)
		name = getattr(doc, "name", None)
	if not name:
		return None
	try:
		rows = frappe.get_all(
			"LTL User Quote Source",
			filters={"parent": name, "enabled": 1},
			pluck="carrier",
		)
	except Exception:
		return None
	return {str(row).strip().upper() for row in rows if row}


def ensure_user_settings(user: str | None = None):
	"""Create or refresh the session user's settings with missing defaults."""
	user = user or session_user()
	if not user:
		frappe.throw(frappe._("You must be logged in to save quote preferences."))

	with _ignore_permissions():
		name = get_user_settings_name(user)
		if name:
			doc = frappe.get_doc("LTL User Settings", name)
		else:
			doc = frappe.get_doc({"doctype": "LTL User Settings", "user": user})

		changed = _seed_quote_sources(doc)
		changed = _seed_accessorials(doc) or changed
		if not getattr(doc, "name", None):
			doc.insert(ignore_permissions=True)
		elif changed:
			doc.save(ignore_permissions=True)
		return doc


def _globally_enabled_carriers() -> list:
	from ltl_quote.carrier_network.registry import get_enabled_carriers

	try:
		with _ignore_permissions():
			return list(get_enabled_carriers() or [])
	except Exception:
		return []



def _seed_quote_sources(doc) -> bool:
	existing = {
		str(getattr(row, "carrier", None) or "").strip().upper()
		for row in (doc.quote_sources or [])
		if getattr(row, "carrier", None)
	}
	changed = False
	for carrier in _globally_enabled_carriers():
		name = str(getattr(carrier, "name", None) or "").strip()
		if not name or name.upper() in existing:
			continue
		doc.append(
			"quote_sources",
			{
				"carrier": name,
				"carrier_name": getattr(carrier, "carrier_name", None) or name,
				"enabled": 1,
			},
		)
		existing.add(name.upper())
		changed = True
	return changed


def _master_accessorial_map() -> dict[str, dict[str, str]]:
	codes = {code for group in STANDARD_ACCESSORIALS.values() for code, _ in group}
	if not codes:
		return {}
	try:
		rows = frappe.get_all(
			"LTL Accessorial",
			filters={"accessorial_code": ["in", list(codes)]},
			fields=["name", "accessorial_code", "accessorial_name"],
		)
	except Exception:
		return {}
	return {str(row.accessorial_code): row for row in rows}


def _seed_accessorials(doc) -> bool:
	existing = {
		(
			str(getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None) or "").strip().upper(),
			str(getattr(row, "service_group", None) or "").strip().lower(),
		)
		for row in (doc.accessorials or [])
	}
	master = _master_accessorial_map()
	changed = False
	for group, entries in STANDARD_ACCESSORIALS.items():
		for code, label in entries:
			key = (code.upper(), group)
			if key in existing:
				continue
			master_row = master.get(code)
			if not master_row:
				continue
			doc.append(
				"accessorials",
				{
					"accessorial": master_row.name,
					"accessorial_code": code,
					"accessorial_name": master_row.accessorial_name or label,
					"service_group": group,
					"show_on_form": 1,
					"default_selected": 0,
				},
			)
			existing.add(key)
			changed = True
	return changed


def _serialize_quote_source(row, carrier_doc=None) -> dict[str, Any]:
	carrier = str(getattr(row, "carrier", None) or "")
	return {
		"name": carrier,
		"carrier_code": getattr(carrier_doc, "carrier_code", None) or carrier,
		"carrier_name": getattr(row, "carrier_name", None)
		or getattr(carrier_doc, "carrier_name", None)
		or carrier,
		"scac": getattr(carrier_doc, "scac", None) or "",
		"connector_type": getattr(carrier_doc, "connector_type", None) or "",
		"reliability_score": getattr(carrier_doc, "reliability_score", None),
		"enabled": cint(getattr(row, "enabled", 0)),
	}


def quote_source_list(user: str | None = None) -> list[dict]:
	"""Globally enabled carriers with the user's Enabled-for-quotes flag."""
	doc = ensure_user_settings(user)
	by_id = {
		str(getattr(row, "carrier", None) or "").strip().upper(): row
		for row in (doc.quote_sources or [])
		if getattr(row, "carrier", None)
	}
	rows = []
	for carrier in _globally_enabled_carriers():
		name = str(getattr(carrier, "name", None) or "")
		pref = by_id.get(name.upper())
		enabled = cint(getattr(pref, "enabled", 1) if pref else 1)
		if not pref:
			enabled = 1
		row = pref or type("Row", (), {"carrier": name, "carrier_name": carrier.carrier_name, "enabled": enabled})()
		payload = _serialize_quote_source(row, carrier)
		payload["enabled"] = enabled
		rows.append(payload)
	rows.sort(key=lambda item: str(item.get("carrier_name") or item.get("name") or "").lower())
	return rows


def accessorial_preference_list(user: str | None = None) -> dict[str, list[dict]]:
	"""All seeded accessorial prefs grouped for the Settings page."""
	doc = ensure_user_settings(user)
	grouped: dict[str, list[dict]] = {group: [] for group in SERVICE_GROUPS}
	seen: set[tuple[str, str]] = set()
	for row in doc.accessorials or []:
		group = str(getattr(row, "service_group", None) or "").strip().lower()
		if group not in grouped:
			continue
		code = str(getattr(row, "accessorial_code", None) or getattr(row, "accessorial", None) or "").strip()
		if not code:
			continue
		label = _accessorial_label(code, group, getattr(row, "accessorial_name", None))
		grouped[group].append(
			{
				"accessorial": getattr(row, "accessorial", None) or code,
				"code": code,
				"label": label,
				"master_name": getattr(row, "accessorial_name", None) or label,
				"service_group": group,
				"show_on_form": cint(getattr(row, "show_on_form", 0)),
				"default_selected": cint(getattr(row, "default_selected", 0)),
			}
		)
		seen.add((code.upper(), group))

	master = _master_accessorial_map()
	for group, entries in STANDARD_ACCESSORIALS.items():
		for code, label in entries:
			if (code.upper(), group) in seen:
				continue
			master_row = master.get(code)
			if not master_row:
				continue
			grouped[group].append(
				{
					"accessorial": master_row.name,
					"code": code,
					"label": label,
					"master_name": master_row.accessorial_name or label,
					"service_group": group,
					"show_on_form": 1,
					"default_selected": 0,
				}
			)
	return grouped


def accessorial_options_for_form(user: str | None = None) -> dict[str, list[dict]]:
	"""Shown accessorials for New Carrier Quote, including default_selected."""
	prefs = accessorial_preference_list(user)
	result: dict[str, list[dict]] = {}
	for group, rows in prefs.items():
		result[group] = [
			{
				"code": row["code"],
				"label": row["label"],
				"master_name": row["master_name"],
				"default_selected": cint(row.get("default_selected")),
			}
			for row in rows
			if cint(row.get("show_on_form"))
		]
	return result


def _accessorial_label(code: str, group: str, fallback: str | None = None) -> str:
	for item_code, label in STANDARD_ACCESSORIALS.get(group, []):
		if item_code == code:
			return label
	return str(fallback or code)


def _require_session_user(user: str | None = None) -> str:
	current = session_user()
	if not current:
		frappe.throw(frappe._("You must be logged in to save quote preferences."))
	if user and str(user) != current and "System Manager" not in frappe.get_roles(current):
		frappe.throw(frappe._("You can only change your own quote preferences."))
	return user or current


@frappe.whitelist()
def get_quote_source_list() -> list[dict]:
	return quote_source_list()


@frappe.whitelist()
def get_accessorial_preferences() -> dict[str, list[dict]]:
	return accessorial_preference_list()


@frappe.whitelist()
def get_quote_source_detail(carrier: str) -> dict:
	carrier = str(carrier or "").strip()
	if not carrier:
		frappe.throw(frappe._("Carrier is required."))
	if not frappe.db.exists("LTL Carrier", carrier):
		frappe.throw(frappe._("Carrier not found."))
	with _ignore_permissions():
		doc = frappe.get_doc("LTL Carrier", carrier)
	prefs = quote_source_list()

	enabled = 0
	for row in prefs:
		if str(row.get("name") or "").upper() == carrier.upper():
			enabled = cint(row.get("enabled"))
			break
	payload = doc.as_dict()
	payload["enabled"] = enabled
	if _is_shipper():
		for field in ("api_key", "api_secret", "notes"):
			payload[field] = ""
	return payload


@frappe.whitelist()
def set_quote_source_enabled(carrier: str, enabled: int = 0) -> dict:
	user = _require_session_user()
	carrier = str(carrier or "").strip()
	if not carrier or not frappe.db.exists("LTL Carrier", carrier):
		frappe.throw(frappe._("Carrier not found."))

	global_ids = {
		str(getattr(item, "name", None) or "").upper() for item in _globally_enabled_carriers()
	}
	if carrier.upper() not in global_ids:
		frappe.throw(frappe._("This carrier is not available on the platform."))

	doc = ensure_user_settings(user)
	flag = 1 if cint(enabled) else 0
	row = next(
		(
			item
			for item in (doc.quote_sources or [])
			if str(getattr(item, "carrier", None) or "").upper() == carrier.upper()
		),
		None,
	)
	if row:
		row.enabled = flag
	else:
		with _ignore_permissions():
			carrier_doc = frappe.get_doc("LTL Carrier", carrier)
		doc.append(
			"quote_sources",
			{
				"carrier": carrier_doc.name,
				"carrier_name": carrier_doc.carrier_name,
				"enabled": flag,
			},
		)
	doc.save(ignore_permissions=True)
	return {"carrier": carrier, "enabled": flag}


@frappe.whitelist()
def set_accessorial_preference(
	accessorial: str | None = None,
	accessorial_code: str | None = None,
	service_group: str = "",
	show_on_form: int = 1,
	default_selected: int = 0,
) -> dict:
	user = _require_session_user()
	group = str(service_group or "").strip().lower()
	if group not in SERVICE_GROUPS:
		frappe.throw(frappe._("Invalid service group."))

	code = str(accessorial_code or "").strip().upper()
	link = str(accessorial or "").strip()
	if not code and link:
		code = str(frappe.db.get_value("LTL Accessorial", link, "accessorial_code") or link).upper()
	if not link and code:
		link = frappe.db.get_value("LTL Accessorial", {"accessorial_code": code}, "name") or code
	if not code:
		frappe.throw(frappe._("Accessorial is required."))

	show = 1 if cint(show_on_form) else 0
	selected = 1 if cint(default_selected) else 0
	if selected:
		show = 1
	if not show:
		selected = 0

	doc = ensure_user_settings(user)
	row = next(
		(
			item
			for item in (doc.accessorials or [])
			if str(getattr(item, "accessorial_code", None) or getattr(item, "accessorial", None) or "").upper()
			== code
			and str(getattr(item, "service_group", None) or "").lower() == group
		),
		None,
	)
	if row:
		row.show_on_form = show
		row.default_selected = selected
		if not getattr(row, "accessorial", None):
			row.accessorial = link
		if not getattr(row, "accessorial_code", None):
			row.accessorial_code = code
	else:
		master_name = frappe.db.get_value("LTL Accessorial", link, "accessorial_name") if link else code
		doc.append(
			"accessorials",
			{
				"accessorial": link or code,
				"accessorial_code": code,
				"accessorial_name": master_name,
				"service_group": group,
				"show_on_form": show,
				"default_selected": selected,
			},
		)
	doc.save(ignore_permissions=True)
	return {
		"code": code,
		"service_group": group,
		"show_on_form": show,
		"default_selected": selected,
	}


def _is_shipper() -> bool:
	user = session_user()
	if not user:
		return False
	roles = frappe.get_roles(user)
	if "Administrator" in roles or "System Manager" in roles:
		return False
	return "Shipper" in roles
