# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class LTLShipment(Document):
	@frappe.whitelist()
	def refresh_tracking(self):
		from ltl_quote.visibility.tracker import ShipmentTracker

		tracker = ShipmentTracker(self)
		return tracker.refresh()

	@frappe.whitelist()
	def dispatch_to_carrier(self):
		from ltl_quote.booking.executor import ShipmentExecutor

		return ShipmentExecutor.dispatch_shipment(self)

	@frappe.whitelist()
	def fetch_proof_of_delivery(self):
		from ltl_quote.carrier_network.registry import get_adapter

		if not self.pro_number:
			frappe.throw("PRO / tracking number required to fetch proof of delivery.")

		carrier = frappe.get_doc("LTL Carrier", self.carrier)
		adapter = get_adapter(carrier)
		return adapter.get_proof_of_delivery(self.pro_number)
