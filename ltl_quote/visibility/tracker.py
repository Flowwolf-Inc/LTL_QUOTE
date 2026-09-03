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

		for ev in sorted(events, key=lambda e: str(e.get("event_datetime") or "")):
			self.shipment.append(
				"tracking_events",
				{
					"event_datetime": ev.get("event_datetime") or now_datetime(),
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

		previous_status = str(self.shipment.status or "")
		if latest:
			self.shipment.current_status = latest.get("status_description")
			self.shipment.current_location = latest.get("location")
			self.shipment.last_tracking_update = now_datetime()
			self._update_shipment_status(
				latest.get("status_code"),
				latest.get("status_description"),
				events=events,
			)
			self._apply_tracking_dates(events)

		self.shipment.has_exception = has_exception
		self.shipment.eta_predicted = self._predict_eta()
		self.shipment.save(ignore_permissions=True)
		self._log_delivery_transition(previous_status, latest, events)
		frappe.db.commit()

		if has_exception and self.settings.enable_exception_alerts:
			self._send_exception_alert()

		return {"events": len(events), "has_exception": has_exception}

	def _update_shipment_status(self, status_code: str | None, description: str | None = None, events=None):
		from ltl_quote.carrier_network.tracking import highest_shipment_status, shipment_status_from_activity

		mapped = highest_shipment_status(events) if events else None
		if not mapped:
			mapped = shipment_status_from_activity(status_code, description)
		if mapped:
			self.shipment.status = mapped
			return

		# Legacy normalized codes from older parsers / mock adapters.
		mapping = {
			"PICKED_UP": "In Transit",
			"IN_TRANSIT": "In Transit",
			"OUT_FOR_DELIVERY": "Out for Delivery",
			"DELIVERED": "Delivered",
			"D1": "Delivered",
			"VOIDED": "Cancelled",
		}
		if status_code and status_code in mapping:
			self.shipment.status = mapping[status_code]

	def _apply_tracking_dates(self, events: list[dict]) -> None:
		from frappe.utils import getdate

		from ltl_quote.carrier_network.tracking import delivery_details_from_events

		pickup_date = None
		estimated = None
		for ev in events or []:
			pickup_date = ev.get("pickup_date") or pickup_date
			estimated = ev.get("estimated_delivery") or estimated
		if pickup_date and not self.shipment.pickup_date:
			try:
				self.shipment.pickup_date = getdate(pickup_date)
			except Exception:
				pass
		if estimated:
			try:
				self.shipment.estimated_delivery_date = getdate(estimated)
			except Exception:
				pass

		details = delivery_details_from_events(events)
		actual = details.get("actual_delivery_date")
		if actual:
			try:
				self.shipment.actual_delivery_date = getdate(actual)
			except Exception:
				pass
			if details.get("actual_delivery_time") and self.shipment.meta.has_field("actual_delivery_time"):
				self.shipment.actual_delivery_time = details["actual_delivery_time"]
			if details.get("delivery_signature") and self.shipment.meta.has_field("delivery_signature"):
				self.shipment.delivery_signature = details["delivery_signature"]
			self.shipment.status = "Delivered"

	def _log_delivery_transition(self, previous_status: str, latest: dict | None, events: list[dict] | None):
		if str(self.shipment.status or "") != "Delivered" or previous_status == "Delivered":
			return
		from ltl_quote.carrier_network.tracking import delivery_details_from_events

		details = delivery_details_from_events(events)
		when = details.get("actual_delivery_date") or (latest or {}).get("event_datetime") or ""
		time = details.get("actual_delivery_time") or ""
		signature = details.get("delivery_signature") or ""
		parts = ["SMC3 status update: Delivered"]
		if when:
			parts.append(f"Date: {when}")
		if time:
			parts.append(f"Time: {time}")
		if signature:
			parts.append(f"Signature: {frappe.utils.escape_html(signature)}")
		comment = " — ".join(parts)
		try:
			self.shipment.add_comment("Comment", comment)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "LTL Shipment - Delivery Comment")
		quote_name = str(self.shipment.quote_request or "").strip()
		if quote_name and frappe.db.exists("LTL Quote Request", quote_name):
			try:
				frappe.get_doc("LTL Quote Request", quote_name).add_comment("Comment", comment)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "LTL Quote Request - Delivery Comment")

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
