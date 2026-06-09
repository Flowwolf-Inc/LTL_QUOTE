# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe

from ltl_quote.visibility.tracker import ShipmentTracker


def refresh_active_shipment_tracking():
	"""Scheduled job: poll carrier APIs for in-transit shipments."""
	active = frappe.get_all(
		"LTL Shipment",
		filters={"status": ["in", ["Booked", "Dispatched", "In Transit", "Out for Delivery"]]},
		pluck="name",
	)
	for name in active:
		try:
			shipment = frappe.get_doc("LTL Shipment", name)
			if shipment.pro_number:
				ShipmentTracker(shipment).refresh()
		except Exception:
			frappe.log_error(title=f"LTL Tracking poll failed: {name}")
