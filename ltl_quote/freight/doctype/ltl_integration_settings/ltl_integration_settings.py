# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document
from frappe.utils import cint


class LTLIntegrationSettings(Document):
	def validate(self):
		seen: set[str] = set()
		for row in self.customer_mappings or []:
			customer = str(row.customer or "").strip()
			if not customer:
				raise frappe.ValidationError("Customer is required on every mapping row.")
			row.customer = customer
			key = customer.upper()
			if key in seen:
				raise frappe.ValidationError(f"Customer {customer} is mapped more than once.")
			seen.add(key)
			_validate_target_doctype(str(row.target_doctype or "").strip())


def _validate_target_doctype(target_doctype: str) -> None:
	if not target_doctype:
		raise frappe.ValidationError("Target DocType is required on every mapping row.")
	if not frappe.db.exists("DocType", target_doctype):
		raise frappe.ValidationError(f"DocType {target_doctype} does not exist.")
	meta = frappe.get_meta(target_doctype)
	if meta.istable:
		raise frappe.ValidationError(f"{target_doctype} is a child table and cannot be an inbound target.")
	if meta.issingle:
		raise frappe.ValidationError(f"{target_doctype} is a Single DocType and cannot be an inbound target.")


def get_customer_mapping(customer_id: str) -> dict | None:
	"""Return the mapping row for a customer code, or None when unset."""
	needle = str(customer_id or "").strip().upper()
	if not needle:
		return None
	try:
		settings = frappe.get_single("LTL Integration Settings")
	except Exception:
		return None
	for row in settings.customer_mappings or []:
		if str(row.customer or "").strip().upper() == needle:
			return {
				"customer": str(row.customer or "").strip(),
				"target_doctype": str(row.target_doctype or "").strip(),
				"disabled": cint(row.disabled),
			}
	return None


def inbound_routing_enabled() -> bool:
	try:
		settings = frappe.get_single("LTL Integration Settings")
	except Exception:
		return False
	return bool(cint(settings.enabled))
