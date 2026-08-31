# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LTLCarrier(Document):
	@frappe.whitelist()
	def sync_accessorials(self):
		"""Populate accessorial mappings from the carrier API / documented codes.

		For Dayton, also refreshes catalog DocTypes (Accessorials + ResponseAccessorials).
		"""
		from ltl_quote.carrier_network.accessorial_sync import sync_carrier_accessorials

		catalog_note = ""
		if (self.connector_type or "").strip() == "Dayton":
			catalog_note = self._sync_dayton_catalogs()

		result = sync_carrier_accessorials(self)
		self.save(ignore_permissions=True)
		if catalog_note:
			result["message"] = f"{result.get('message') or ''} {catalog_note}".strip()
		return result

	@frappe.whitelist()
	def sync_barcode_requirements(self):
		from ltl_quote.api.shipping import sync_smc3_barcode_requirements

		return sync_smc3_barcode_requirements(carrier=self.name)

	@frappe.whitelist()
	def sync_dispatch_response_messages(self):
		from ltl_quote.api.shipping import sync_smc3_dispatch_response_messages

		return sync_smc3_dispatch_response_messages(carrier=self.name)

	def _sync_dayton_catalogs(self) -> str:
		"""Best-effort sync of Dayton Accessorial + Response Accessorial + Service Center DocTypes."""
		from ltl_quote.api.shipping import (
			sync_dayton_accessorials,
			sync_response_accessorials,
			sync_service_centers,
		)

		parts: list[str] = []
		try:
			acc = sync_dayton_accessorials()
			parts.append(f"Synced {acc.get('synced_count', 0)} accessorials")
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Dayton Accessorial catalog sync during carrier sync",
			)
			parts.append("Accessorial catalog sync failed")

		try:
			resp = sync_response_accessorials()
			parts.append(f"{resp.get('synced_count', 0)} response accessorials")
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Dayton Response Accessorial catalog sync during carrier sync",
			)
			parts.append("Response accessorial catalog sync failed")

		try:
			centers = sync_service_centers()
			parts.append(f"{centers.get('synced_count', 0)} service centers")
		except Exception:
			frappe.log_error(
				message=frappe.get_traceback(),
				title="Dayton Service Center catalog sync during carrier sync",
			)
			parts.append("Service center catalog sync failed")

		return "+ ".join(parts) + "." if parts else ""
