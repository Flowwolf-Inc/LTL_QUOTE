# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, now_datetime

from ltl_quote.carrier_network.registry import get_adapter
from ltl_quote.utils.currency import get_quote_currency


class ShipmentExecutor:
	"""Booking, BOL generation, and carrier dispatch."""

	def __init__(self, quote_request):
		self.quote_request = quote_request

	def book(self) -> dict:
		idx = int(self.quote_request.selected_carrier_quote or 0)
		quotes = self.quote_request.carrier_quotes or []
		if idx < 0 or idx >= len(quotes):
			frappe.throw("Select a valid carrier quote before booking.")

		selected = quotes[idx]
		carrier = frappe.get_doc("LTL Carrier", selected.carrier)
		adapter = get_adapter(carrier)

		quote_data = {
			"carrier_quote_id": selected.carrier_quote_id,
			"total_charge": selected.total_charge,
			"transit_days": selected.transit_days,
			"origin_zip": self.quote_request.origin_zip,
			"destination_zip": self.quote_request.destination_zip,
			"total_weight": self.quote_request.total_weight,
			"pieces": self.quote_request.pieces or 1,
			"origin_city": self.quote_request.origin_city,
			"origin_state": self.quote_request.origin_state,
			"destination_city": self.quote_request.destination_city,
			"destination_state": self.quote_request.destination_state,
			"quote_request": self.quote_request.name,
		}

		booking_result = adapter.book_shipment(quote_data)
		shipment = self._create_shipment(selected, booking_result)
		self._generate_bol(shipment)

		self.quote_request.status = "Booked"
		self.quote_request.save(ignore_permissions=True)
		frappe.db.commit()

		return {"shipment": shipment.name, "bol_number": shipment.bol_number, "pro_number": shipment.pro_number}

	def _create_shipment(self, selected, booking_result: dict):
		shipment = frappe.get_doc(
			{
				"doctype": "LTL Shipment",
				"quote_request": self.quote_request.name,
				"carrier": selected.carrier,
				"status": "Booked",
				"booked_on": now_datetime(),
				"total_charge": selected.total_charge,
				"currency": selected.currency or get_quote_currency(),
				"transit_days": selected.transit_days,
				"estimated_delivery_date": booking_result.get("estimated_delivery")
				or selected.estimated_delivery_date,
				"bol_number": booking_result.get("bol_number"),
				"pro_number": booking_result.get("pro_number"),
				"carrier_confirmation": booking_result.get("carrier_confirmation"),
				"dispatch_status": "Pending",
				"current_status": "Booked",
			}
		)
		shipment.insert(ignore_permissions=True)
		return shipment

	def _generate_bol(self, shipment):
		"""Generate BOL metadata; PDF generation can be extended via Print Format."""
		if not shipment.bol_number:
			shipment.bol_number = f"BOL-{shipment.name}"
			shipment.save(ignore_permissions=True)

	@staticmethod
	def dispatch_shipment(shipment) -> dict:
		carrier = frappe.get_doc("LTL Carrier", shipment.carrier)
		adapter = get_adapter(carrier)
		result = adapter.dispatch_shipment(
			{
				"pro_number": shipment.pro_number,
				"bol_number": shipment.bol_number,
				"pickup_date": shipment.pickup_date,
			}
		)
		shipment.dispatch_status = "Acknowledged" if result.get("status") == "acknowledged" else "Sent to Carrier"
		shipment.status = "Dispatched"
		shipment.save(ignore_permissions=True)
		frappe.db.commit()
		return result
