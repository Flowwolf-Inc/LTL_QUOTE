# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""Shared booking context helpers for carrier dispatch."""

from __future__ import annotations

import frappe


def get_default_shipper_context() -> dict[str, str]:
	"""Return organization-wide shipper defaults from LTL Platform Settings."""
	settings = frappe.get_single("LTL Platform Settings")
	return {
		"shipper_name": (settings.get("default_shipper_name") or "Main Warehouse Dispatch").strip(),
		"shipper_address": (settings.get("default_shipper_address") or "123 Logistics Way").strip(),
		"contact_name": (settings.get("default_contact_name") or "Shipping Desk").strip(),
		"contact_phone": (settings.get("default_contact_phone") or "0000000000").strip(),
		"consignee_name": (settings.get("default_consignee_name") or "Destination Receiver").strip(),
		"consignee_address": (settings.get("default_consignee_address") or "456 Customer Ave").strip(),
	}


def resolve_shipper_context(quote_data: dict | None = None, quote_request=None) -> dict[str, str]:
	"""Merge shipper/contact fields from booking payload, quote request, and platform defaults."""
	quote_data = quote_data or {}
	defaults = get_default_shipper_context()

	def _pick(*values: str | None) -> str:
		for value in values:
			clean = str(value or "").strip()
			if clean:
				return clean
		return ""

	request = quote_request or frappe._dict()
	return {
		"shipper_name": _pick(
			quote_data.get("shipper_name"),
			quote_data.get("shipper_company_name"),
			getattr(request, "shipper_company_name", None),
			getattr(request, "shipper_name", None),
			defaults["shipper_name"],
		),
		"shipper_address": _pick(
			quote_data.get("shipper_address"),
			getattr(request, "shipper_address", None),
			defaults["shipper_address"],
		),
		"consignee_name": _pick(
			quote_data.get("consignee_name"),
			quote_data.get("consignee_company_name"),
			getattr(request, "consignee_company_name", None),
			getattr(request, "consignee_name", None),
			defaults["consignee_name"],
		),
		"consignee_address": _pick(
			quote_data.get("consignee_address"),
			getattr(request, "consignee_address", None),
			defaults["consignee_address"],
		),
		"contact_name": _pick(
			quote_data.get("contact_name"),
			getattr(request, "contact_name", None),
			defaults["contact_name"],
		),
		"contact_phone": _pick(
			quote_data.get("contact_phone"),
			getattr(request, "contact_phone", None),
			defaults["contact_phone"],
		),
	}


def _absolute_site_url(url: str) -> str:
	"""Return an absolute site URL for relative file paths."""
	url = str(url or "").strip()
	if not url:
		return ""
	if url.startswith("http://") or url.startswith("https://"):
		return url
	if url.startswith("/"):
		return f"{frappe.utils.get_url()}{url}"
	return f"{frappe.utils.get_url()}/{url.lstrip('/')}"


def resolve_shipment_bol_url(shipment_name: str | None = None, quote_request=None) -> str:
	"""Resolve a browser-openable absolute BOL URL, preferring the Document PNG."""
	if shipment_name:
		row = frappe.db.get_value(
			"LTL Shipment",
			shipment_name,
			["bol_image", "bol_document_url", "bol_document"],
			as_dict=True,
		)
		if row:
			for key in ("bol_image", "bol_document_url", "bol_document"):
				url = str(row.get(key) or "").strip()
				if url:
					return _absolute_site_url(url)

	if quote_request:
		if isinstance(quote_request, str):
			doc = frappe.db.get_value(
				"LTL Quote Request",
				quote_request,
				["bol_document_url"],
				as_dict=True,
			)
		else:
			doc = quote_request
		if doc:
			url = str(
				doc.get("bol_document_url") if isinstance(doc, dict) else getattr(doc, "bol_document_url", None) or ""
			).strip()
			if url:
				return _absolute_site_url(url)

	return ""


def resolve_shipment_bol_image_url(shipment_name: str | None = None) -> str:
	"""Resolve the attached SMC3 BOL PNG preview URL."""
	if not shipment_name:
		return ""
	attach = str(frappe.db.get_value("LTL Shipment", shipment_name, "bol_image") or "").strip()
	if not attach:
		return ""
	return _absolute_site_url(attach)
