# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe


def refresh_active_shipment_tracking():
	"""Scheduled job: poll carrier APIs for in-transit shipments."""
	from ltl_quote.visibility.tracker import ShipmentTracker

	active = frappe.get_all(
		"LTL Shipment",
		filters={
			"status": ["in", ["Booked", "Dispatched", "In Transit", "Out for Delivery"]],
			"carrier": ["!=", "DAYTON"],
		},
		pluck="name",
	)
	for name in active:
		try:
			shipment = frappe.get_doc("LTL Shipment", name)
			if shipment.pro_number:
				ShipmentTracker(shipment).refresh()
		except Exception:
			frappe.log_error(title=f"LTL Tracking poll failed: {name}")


def sync_all_active_shipments():
	"""Hourly Dayton tracking poll — import dayton only when the job runs."""
	from ltl_quote.carrier_network.adapters.dayton import sync_all_active_shipments as _run

	return _run()
