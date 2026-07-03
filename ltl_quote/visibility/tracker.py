# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

from ltl_quote.carrier_network.registry import get_adapter


class ShipmentTracker:
	"""Real-time tracking updates, ETA prediction, and exception detection."""

	def __init__(self, shipment):
		self.shipment = shipment
		self.settings = frappe.get_single("LTL Platform Settings")

	def refresh(self) -> dict:
		if not self.shipment.pro_number:
			frappe.throw("PRO / tracking number required for visibility updates.")

		carrier = frappe.get_doc("LTL Carrier", self.shipment.carrier)
		adapter = get_adapter(carrier)
		events = adapter.get_tracking(self.shipment.pro_number)

		self.shipment.tracking_events = []
		has_exception = False
		latest = None

		for ev in sorted(events, key=lambda e: e.get("event_datetime") or ""):
			self.shipment.append(
				"tracking_events",
				{
					"event_datetime": ev.get("event_datetime"),
					"status_code": ev.get("status_code"),
					"status_description": ev.get("status_description"),
					"location": ev.get("location"),
					"is_exception": ev.get("is_exception", 0),
					"exception_type": ev.get("exception_type"),
					"source": carrier.carrier_name,
				},
			)
			if ev.get("is_exception"):
				has_exception = True
			latest = ev

		if latest:
			self.shipment.current_status = latest.get("status_description")
			self.shipment.current_location = latest.get("location")
			self.shipment.last_tracking_update = now_datetime()
			self._update_shipment_status(latest.get("status_code"))

		self.shipment.has_exception = has_exception
		self.shipment.eta_predicted = self._predict_eta()
		self.shipment.save(ignore_permissions=True)
		frappe.db.commit()

		if has_exception and self.settings.enable_exception_alerts:
			self._send_exception_alert()

		return {"events": len(events), "has_exception": has_exception}

	def _update_shipment_status(self, status_code: str | None):
		mapping = {
			"PICKED_UP": "In Transit",
			"IN_TRANSIT": "In Transit",
			"OUT_FOR_DELIVERY": "Out for Delivery",
			"DELIVERED": "Delivered",
		}
		if status_code and status_code in mapping:
			self.shipment.status = mapping[status_code]

	def _predict_eta(self):
		if self.shipment.estimated_delivery_date:
			from frappe.utils import get_datetime

			return get_datetime(self.shipment.estimated_delivery_date)
		return None

	def _send_exception_alert(self):
		frappe.publish_realtime(
			"ltl_shipment_exception",
			{"shipment": self.shipment.name, "carrier": self.shipment.carrier_name},
			user=frappe.session.user,
		)
