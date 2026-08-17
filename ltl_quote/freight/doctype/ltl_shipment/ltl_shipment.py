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

	@frappe.whitelist()
	def cancel_pickup(self):
		from ltl_quote.api.shipping import cancel_arcbest_pickup, cancel_dayton_pickup, cancel_tforce_pickup
		from ltl_quote.carrier_network.carrier_identity import (
			CONNECTOR_ARCBEST,
			CONNECTOR_DAYTON,
			CONNECTOR_TFORCE,
			shipment_connector,
		)

		connector = shipment_connector(self)
		if connector == CONNECTOR_TFORCE:
			return cancel_tforce_pickup(shipment=self.name)
		if connector == CONNECTOR_DAYTON:
			return cancel_dayton_pickup(shipment=self.name)
		if connector == CONNECTOR_ARCBEST:
			return cancel_arcbest_pickup(shipment=self.name)
		frappe.throw("Pickup cancellation is only supported for Dayton, TForce, and ArcBest shipments.")

	@frappe.whitelist()
	def update_electronic_bol(self):
		from ltl_quote.carrier_network.adapters.dayton import update_electronic_bol

		if str(self.carrier or "").upper() != "DAYTON":
			frappe.throw("Electronic BOL updates are only supported for Dayton shipments.")

		return update_electronic_bol(self.name)

	@frappe.whitelist()
	def fetch_dayton_tracking_updates(self):
		"""Document controller method triggered via doc: frm.doc from the desk UI."""
		from ltl_quote.carrier_network.adapters.dayton import (
			fetch_dayton_tracking_updates as run_sync,
		)

		return run_sync(self.name)
