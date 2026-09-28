# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Envoy and Amerilux organization names, quote sources, and accessorials."""

from __future__ import annotations

from typing import Any

import frappe
from frappe.utils import cint

from ltl_quote.api.user_settings import (
	SERVICE_GROUPS,
	STANDARD_ACCESSORIALS,
	_accessorial_label,
	_globally_enabled_carriers,
	_ignore_permissions,
	_master_accessorial_map,
	_seed_accessorials,
	_seed_quote_sources,
	_serialize_quote_source,
)

ORG_NAMES = {
	"ENVOY": "Envoy",
	"AMERILUX": "Amerilux",
}


def org_display_name(code: str | None) -> str:
	"""Return Envoy or Amerilux. Unknown codes stay blank."""
	key = str(code or "").strip().upper()
	if not key:
		return ""
	return ORG_NAMES.get(key, "")


def read_request_org_code(*sources) -> str:
	"""Read ENVOY or AMERILUX from the tenant header, then the request body."""
	candidates: list[Any] = []
	header = _header_tenant()
	if header:
		candidates.append(header)
	for source in sources:
		candidates.extend(_tenant_values(source))
	try:
		from ltl_quote.api.carrier_mapping import read_request_json

		candidates.extend(_tenant_values(read_request_json()))
	except Exception:
		pass
	for raw in candidates:
		key = str(raw or "").strip().upper()
		if key in ORG_NAMES:
			return key
	return ""


def user_org_code(user: str | None = None) -> str:
	"""Return ENVOY or AMERILUX when the login belongs to that organization."""
	try:
		current = user or str(getattr(getattr(frappe, "session", None), "user", None) or "")
	except Exception:
		current = ""
	text = str(current or "").strip().lower()
	if "amerilux" in text:
		return "AMERILUX"
	if "envoy" in text:
		return "ENVOY"
	return ""


def require_org_access(org_code: str) -> str:
	"""Administrator may open either org. An org login may open only its own."""
	key = str(org_code or "").strip().upper()
	if key not in ORG_NAMES:
		frappe.throw("Unknown organization.")
	try:
		current = str(getattr(getattr(frappe, "session", None), "user", None) or "").strip()
	except Exception:
		current = ""
	if current == "Administrator":
		return key
	if user_org_code(current) != key:
		frappe.throw("Not permitted to view this organization.", frappe.PermissionError)
	return key


def ensure_org_settings(org_code: str):
	"""Create or refresh one org's quote sources and accessorials."""
	key = str(org_code or "").strip().upper()
	if key not in ORG_NAMES:
		frappe.throw("Unknown organization.")
	with _ignore_permissions():
		if frappe.db.exists("LTL Org Settings", key):
			doc = frappe.get_doc("LTL Org Settings", key)
		else:
			doc = frappe.get_doc(
				{
					"doctype": "LTL Org Settings",
					"org_code": key,
					"org_name": ORG_NAMES[key],
				}
			)
		renamed = (doc.org_name or "") != ORG_NAMES[key]
		if renamed:
			doc.org_name = ORG_NAMES[key]
		changed = _seed_quote_sources(doc)
		changed = _seed_accessorials(doc) or changed or renamed
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		elif changed:
			doc.save(ignore_permissions=True)
		return doc


def carriers_for_org(org_code: str) -> list:
	"""Globally enabled carriers the org left on for quotes."""
	doc = ensure_org_settings(org_code)
	enabled = {
		str(getattr(row, "carrier", None) or "").strip().upper()
		for row in (doc.quote_sources or [])
		if getattr(row, "carrier", None) and cint(getattr(row, "enabled", 0))
	}
	return [
		carrier
		for carrier in _globally_enabled_carriers()
		if str(getattr(carrier, "name", None) or "").strip().upper() in enabled
	]


def org_quote_source_rows(doc) -> list[dict]:
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
		row = pref or type("Row", (), {"carrier": name, "carrier_name": carrier.carrier_name, "enabled": enabled})()
		payload = _serialize_quote_source(row, carrier)
		payload["enabled"] = enabled
		rows.append(payload)
	rows.sort(key=lambda item: str(item.get("carrier_name") or item.get("name") or "").lower())
	return rows


def org_accessorial_groups(doc) -> dict[str, list[dict]]:
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


@frappe.whitelist(methods=["GET", "POST"])
def get_org_settings(org_code: str | None = None) -> dict:
	key = require_org_access(org_code or "")
	doc = ensure_org_settings(key)
	key = str(doc.org_code or doc.name).upper()
	return {
		"org_code": key,
		"org_name": ORG_NAMES.get(key) or doc.org_name,
		"quote_sources": org_quote_source_rows(doc),
		"accessorials": org_accessorial_groups(doc),
	}


@frappe.whitelist(methods=["POST"])
def set_org_quote_source_enabled(org_code: str | None = None, carrier: str | None = None, enabled: int = 0) -> dict:
	key = require_org_access(org_code or "")
	doc = ensure_org_settings(key)
	carrier_name = str(carrier or "").strip()
	if not carrier_name or not frappe.db.exists("LTL Carrier", carrier_name):
		frappe.throw("Carrier not found.")
	global_ids = {str(getattr(item, "name", None) or "").upper() for item in _globally_enabled_carriers()}
	if carrier_name.upper() not in global_ids:
		frappe.throw("This carrier is not available on the platform.")
	flag = 1 if cint(enabled) else 0
	row = next(
		(
			item
			for item in (doc.quote_sources or [])
			if str(getattr(item, "carrier", None) or "").upper() == carrier_name.upper()
		),
		None,
	)
	if row:
		row.enabled = flag
	else:
		with _ignore_permissions():
			carrier_doc = frappe.get_doc("LTL Carrier", carrier_name)
		doc.append(
			"quote_sources",
			{
				"carrier": carrier_doc.name,
				"carrier_name": carrier_doc.carrier_name,
				"enabled": flag,
			},
		)
	doc.save(ignore_permissions=True)
	return {"org_code": doc.org_code, "carrier": carrier_name, "enabled": flag}


@frappe.whitelist(methods=["POST"])
def set_org_accessorial_preference(
	org_code: str | None = None,
	accessorial: str | None = None,
	accessorial_code: str | None = None,
	service_group: str = "",
	show_on_form: int = 1,
	default_selected: int = 0,
) -> dict:
	key = require_org_access(org_code or "")
	doc = ensure_org_settings(key)
	group = str(service_group or "").strip().lower()
	if group not in SERVICE_GROUPS:
		frappe.throw("Invalid service group.")
	code = str(accessorial_code or "").strip().upper()
	link = str(accessorial or "").strip()
	if not code and link:
		code = str(frappe.db.get_value("LTL Accessorial", link, "accessorial_code") or link).upper()
	if not link and code:
		link = frappe.db.get_value("LTL Accessorial", {"accessorial_code": code}, "name") or code
	if not code:
		frappe.throw("Accessorial is required.")
	show = 1 if cint(show_on_form) else 0
	selected = 1 if cint(default_selected) else 0
	if selected:
		show = 1
	if not show:
		selected = 0
	row = next(
		(
			item
			for item in (doc.accessorials or [])
			if str(getattr(item, "accessorial_code", None) or getattr(item, "accessorial", None) or "").upper() == code
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
		"org_code": doc.org_code,
		"code": code,
		"service_group": group,
		"show_on_form": show,
		"default_selected": selected,
	}


def _header_tenant() -> str:
	req = getattr(getattr(frappe, "local", None), "request", None)
	if req is None:
		return ""
	headers = getattr(req, "headers", None)
	if not headers:
		return ""
	try:
		return str(headers.get("X-LTL-Tenant") or "").strip()
	except Exception:
		return ""


def _tenant_values(source) -> list:
	if source is None:
		return []
	if isinstance(source, str):
		text = source.strip()
		if text.startswith("{") and text.endswith("}"):
			try:
				parsed = frappe.parse_json(text)
			except Exception:
				return [text]
			return _tenant_values(parsed)
		return [text]
	if not isinstance(source, dict):
		return []
	values = []
	for key in ("tenant_code", "org"):
		if source.get(key) not in (None, ""):
			values.append(source.get(key))
	for nested_key in ("payload", "data"):
		nested = source.get(nested_key)
		values.extend(_tenant_values(nested))
	return values
