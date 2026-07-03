# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LTLCarrier(Document):
	@frappe.whitelist()
	def sync_accessorials(self):
		"""Populate accessorial mappings from the carrier API / documented codes."""
		from ltl_quote.carrier_network.accessorial_sync import sync_carrier_accessorials

		return sync_carrier_accessorials(self)
