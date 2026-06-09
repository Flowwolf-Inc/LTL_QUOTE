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
