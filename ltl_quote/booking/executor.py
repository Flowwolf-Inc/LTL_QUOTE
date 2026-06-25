# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

from ltl_quote.carrier_network.registry import get_adapter
from ltl_quote.utils.currency import get_quote_currency
from ltl_quote.utils.booking import resolve_shipper_context
from ltl_quote.utils.location import resolve_us_location


class ShipmentExecutor:
	"""Booking, BOL generation, and carrier dispatch."""

	def __init__(self, quote_request):
		self.quote_request = quote_request
		self.adapter = None
		self.carrier_code = ""
		self.booking_payload: dict = {}

	def book(self, is_test: bool = False) -> dict:
		"""Orchestrates the platform booking execution path."""
		idx = int(self.quote_request.selected_carrier_quote or 0)
		quotes = self.quote_request.carrier_quotes or []
		if idx < 0 or idx >= len(quotes):
			frappe.throw("Select a valid carrier quote before booking.")

		selected = quotes[idx]
		carrier = frappe.get_doc("LTL Carrier", selected.carrier)
		self.adapter = get_adapter(carrier)
		self.carrier_code = str(getattr(carrier, "carrier_code", None) or carrier.name or "").upper()

		origin_city, origin_state = resolve_us_location(
			self.quote_request.origin_zip,
			self.quote_request.origin_city,
			self.quote_request.origin_state,
		)
		destination_city, destination_state = resolve_us_location(
			self.quote_request.destination_zip,
			self.quote_request.destination_city,
			self.quote_request.destination_state,
		)

		if not origin_state:
			frappe.throw(
				"Origin state is required to book a shipment. Enter origin state or use a valid US origin ZIP."
			)

		shipper = resolve_shipper_context(quote_request=self.quote_request)

		self.booking_payload = {
			"carrier_quote_id": selected.carrier_quote_id,
			"total_charge": selected.total_charge,
			"transit_days": selected.transit_days,
			"origin_zip": self.quote_request.origin_zip,
			"destination_zip": self.quote_request.destination_zip,
			"total_weight": self.quote_request.total_weight,
			"pieces": self.quote_request.pieces or 1,
			"origin_city": origin_city,
			"origin_state": origin_state,
			"destination_city": destination_city,
			"destination_state": destination_state,
			"quote_request": self.quote_request.name,
			"freight_class": self.quote_request.freight_class,
			"shipper_name": shipper["shipper_name"],
			"shipper_address": shipper["shipper_address"],
			"consignee_name": shipper["consignee_name"],
			"consignee_address": shipper["consignee_address"],
			"contact_name": shipper["contact_name"],
			"contact_phone": shipper["contact_phone"],
			"is_test": is_test,
		}

		connector_type = str(getattr(carrier, "connector_type", None) or "").strip()
		self.connector_type = connector_type
		self.is_dayton_carrier = self.carrier_code == "DAYTON" or connector_type == "Dayton"

		if self.is_dayton_carrier:
			booking_result = self.adapter.generate_bill_of_lading(self.booking_payload)
		else:
			booking_result = self.adapter.book_shipment(self.booking_payload)

		shipment = self._create_shipment(selected, booking_result)
		if not self.is_dayton_carrier:
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
				"dayton_bol_id": booking_result.get("dayton_bol_id"),
				"dispatch_status": "Pending",
				"current_status": "Booked",
			}
		)
		shipment.insert(ignore_permissions=True)

		if self.is_dayton_carrier:
			from ltl_quote.carrier_network.adapters.dayton import attach_dayton_bol_to_shipment

			res = attach_dayton_bol_to_shipment(
				shipment,
				self.booking_payload,
				bol_result=booking_result,
			)
			shipment.bol_number = res.get("bol_number") or shipment.bol_number
			shipment.pro_number = res.get("pro_number") or shipment.pro_number
			if res.get("status") == "success" and res.get("document_url"):
				shipment.bol_document = res.get("document_url")
			shipment.status = "Booked"
			shipment.dispatch_status = "Pending"
			shipment.save(ignore_permissions=True)

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
