# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import now_datetime

from ltl_quote.api.payload import line_item_freight_class
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
		self.connector_type = ""
		self.is_dayton_carrier = False
		self.is_arcbest_carrier = False
		self.is_tforce_carrier = False
		self.is_smc3_carrier = False

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
		contact_email = (
			getattr(self.quote_request, "origin_contact_email", None)
			or getattr(self.quote_request, "contact_email", None)
			or frappe.db.get_value("User", frappe.session.user, "email")
		)

		items = self._serialize_line_items()
		first_item = items[0] if items else {}

		self.booking_payload = {
			"carrier_quote_id": selected.carrier_quote_id,
			"total_charge": selected.total_charge,
			"transit_days": selected.transit_days,
			"origin_zip": self.quote_request.origin_zip,
			"destination_zip": self.quote_request.destination_zip,
			"total_weight": self.quote_request.total_weight,
			"pieces": self.quote_request.pieces or 1,
			"length": getattr(self.quote_request, "length", None),
			"width": getattr(self.quote_request, "width", None),
			"height": getattr(self.quote_request, "height", None),
			"dimension_uom": getattr(self.quote_request, "dimension_uom", None) or "IN",
			"origin_city": origin_city,
			"origin_state": origin_state,
			"destination_city": destination_city,
			"destination_state": destination_state,
			"quote_request": self.quote_request.name,
			"freight_class": line_item_freight_class(
				(getattr(self.quote_request, "line_items", None) or [None])[0],
				self.quote_request.freight_class,
			)
			or self.quote_request.freight_class,
			"shipper_name": shipper["shipper_name"],
			"shipper_address": shipper["shipper_address"],
			"consignee_name": shipper["consignee_name"],
			"consignee_address": shipper["consignee_address"],
			"shipper_company_name": getattr(self.quote_request, "shipper_company_name", None),
			"consignee_company_name": getattr(self.quote_request, "consignee_company_name", None),
			"contact_name": shipper["contact_name"],
			"contact_phone": shipper["contact_phone"],
			"origin_contact_name": getattr(self.quote_request, "contact_name", None) or shipper["contact_name"],
			"origin_contact_phone": getattr(self.quote_request, "contact_phone", None) or shipper["contact_phone"],
			"contact_email": contact_email,
			"origin_contact_email": contact_email,
			"destination_contact_name": getattr(self.quote_request, "destination_contact_name", None),
			"destination_contact_phone": getattr(self.quote_request, "destination_contact_phone", None),
			"destination_contact_email": getattr(
				self.quote_request, "destination_contact_email", None
			),
			"accessorials": [
				{
					"accessorial_code": getattr(row, "accessorial_code", None),
					"service_group": getattr(row, "service_group", None),
					"quantity": getattr(row, "quantity", 1) or 1,
				}
				for row in (self.quote_request.accessorials or [])
			],
			"items": items,
			"commodity_description": first_item.get("description") or first_item.get("item_name") or "",
			"nmfc": first_item.get("nmfc") or "",
			"is_hazardous": bool(first_item.get("hazmat")),
			"payment_terms": getattr(self.quote_request, "payment_terms", None) or "Prepaid",
			"is_test": is_test,
		}

		connector_type = str(getattr(carrier, "connector_type", None) or "").strip()
		self.connector_type = connector_type
		self.is_dayton_carrier = self.carrier_code == "DAYTON" or connector_type == "Dayton"
		self.is_arcbest_carrier = self.carrier_code in ("ARCB", "ARCBEST") or connector_type == "ArcBest API"
		self.is_tforce_carrier = self.carrier_code in ("TFORCE", "TFF") or connector_type == "TForce"
		self.is_smc3_carrier = self.carrier_code == "SMC3" or connector_type == "SMC3"

		self.booking_payload["quoted_scac"] = str(getattr(selected, "quoted_scac", None) or "").strip()
		self.booking_payload["scac"] = self.booking_payload["quoted_scac"]
		self.booking_payload["rate_source"] = str(getattr(selected, "rate_source", None) or "").strip()

		booking_result = self.adapter.book_shipment(self.booking_payload)
		shipment = self._create_shipment(selected, booking_result)
		self._sync_quote_request_bol(shipment, booking_result)

		self.quote_request.status = "Booked"
		self.quote_request.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"shipment": shipment.name,
			"bol_number": shipment.bol_number,
			"pro_number": shipment.pro_number,
			"bol_document_url": shipment.bol_document_url or shipment.bol_document or "",
			"bol_image": getattr(shipment, "bol_image", None) or "",
			"dayton_bol_id": getattr(shipment, "dayton_bol_id", None),
			"tforce_bol_id": getattr(shipment, "tforce_bol_id", None),
		}

	def _serialize_line_items(self) -> list[dict]:
		"""Serialize quote request line items for the carrier booking payload."""
		rows = []
		for row in getattr(self.quote_request, "line_items", None) or []:
			description = getattr(row, "description", None) or getattr(row, "item_name", None) or ""
			freight_class = line_item_freight_class(row, self.quote_request.freight_class)
			nmfc = getattr(row, "nmfc", None) or ""
			qty = getattr(row, "quantity", None) or 1
			rows.append(
				{
					"item_number": getattr(row, "item_number", None) or "",
					"item_name": getattr(row, "item_name", None) or "",
					"item_id": getattr(row, "item_id", None) or "",
					"description": description,
					"commodity_description": description,
					"quantity": qty,
					"qty": qty,
					"units": getattr(row, "units", None) or "",
					"packaging_units": getattr(row, "packaging_units", None) or "",
					"packaging_unit_count": getattr(row, "packaging_unit_count", None),
					"rate": getattr(row, "rate", None),
					"freight_class": freight_class,
					"classification": freight_class,
					"nmfc_class": freight_class,
					"nmfc": nmfc,
					"nmfc_number": nmfc,
					"hazmat": 1 if getattr(row, "hazmat", None) else 0,
					"hazardous": bool(getattr(row, "hazmat", None)),
					"weight": getattr(row, "weight", None),
					"weight_unit": getattr(row, "weight_unit", None) or "LBS",
					"length": getattr(row, "length", None),
					"width": getattr(row, "width", None),
					"height": getattr(row, "height", None),
					"dimension_unit": getattr(row, "dimension_unit", None) or "IN",
					"dimension_units": getattr(row, "dimension_unit", None) or "IN",
					"volume": getattr(row, "volume", None),
					"volume_units": getattr(row, "volume_units", None) or "",
					"area": getattr(row, "area", None),
					"area_units": getattr(row, "area_units", None) or "",
					"linear_feet": getattr(row, "linear_feet", None),
					"hazmat_class_division": getattr(row, "hazmat_class_division", None) or "",
					"hazmat_phone": getattr(row, "hazmat_phone", None) or "",
					"hazmat_contact_company": getattr(row, "hazmat_contact_company", None) or "",
					"hazmat_contact": getattr(row, "hazmat_contact", None) or "",
					"hazmat_number": getattr(row, "hazmat_number", None) or "",
					"hazmat_packaging_group": getattr(row, "hazmat_packaging_group", None) or "",
					"hazmat_number_type": getattr(row, "hazmat_number_type", None) or "",
					"pickup_stop_location": getattr(row, "pickup_stop_location", None) or "",
					"pickup": getattr(row, "pickup", None) or "",
					"drop_stop_location": getattr(row, "drop_stop_location", None) or "",
					"drop": getattr(row, "drop", None) or "",
				}
			)
		return rows

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
				"tforce_bol_id": booking_result.get("tforce_bol_id"),
				"pickup_number": booking_result.get("pickup_number"),
				"dispatch_status": "Pending",
				"current_status": "Booked",
			}
		)

		bol_url = booking_result.get("bol_document_url") or ""
		if bol_url:
			shipment.bol_document = bol_url
			shipment.bol_document_url = bol_url

		shipment.insert(ignore_permissions=True)

		if self.is_dayton_carrier:
			from ltl_quote.carrier_network.adapters.dayton import attach_dayton_bol_to_shipment

			res = attach_dayton_bol_to_shipment(
				shipment,
				self.booking_payload,
				bol_result=booking_result,
			)
			shipment.reload()
			shipment.bol_number = res.get("bol_number") or shipment.bol_number
			shipment.pro_number = res.get("pro_number") or shipment.pro_number
			if res.get("status") == "success" and res.get("document_url"):
				shipment.bol_document = res.get("document_url")
				shipment.bol_document_url = res.get("document_url")
			shipment.status = "Booked"
			shipment.dispatch_status = "Pending"
			shipment.save(ignore_permissions=True)
		elif self.is_tforce_carrier:
			from ltl_quote.carrier_network.adapters.tforce import (
				apply_tforce_bol_details_to_shipment,
				attach_tforce_bol_to_shipment,
			)

			res = attach_tforce_bol_to_shipment(shipment, bol_result=booking_result)
			apply_tforce_bol_details_to_shipment(
				shipment.name,
				quote_data=self.booking_payload,
				bol_result=booking_result,
			)
			shipment.reload()
			shipment.bol_number = res.get("bol_number") or shipment.bol_number
			shipment.pro_number = res.get("pro_number") or shipment.pro_number
			if booking_result.get("tforce_bol_id"):
				shipment.tforce_bol_id = booking_result.get("tforce_bol_id")
			if booking_result.get("pickup_number"):
				shipment.pickup_number = booking_result.get("pickup_number")
			if res.get("status") == "success" and res.get("document_url"):
				file_url = res.get("document_url") or ""
				shipment.bol_document_url = file_url
				if "/files/" in file_url:
					shipment.bol_document = file_url[file_url.find("/files/") :]
				elif "/private/files/" in file_url:
					shipment.bol_document = file_url[file_url.find("/private/files/") :]
				else:
					shipment.bol_document = file_url
			shipment.status = "Booked"
			shipment.dispatch_status = "Pending"
			shipment.save(ignore_permissions=True)
		elif self.is_smc3_carrier:
			from ltl_quote.carrier_network.adapters.smc3 import (
				attach_smc3_bol_images_to_shipment,
				attach_smc3_bol_to_shipment,
			)

			res = attach_smc3_bol_to_shipment(shipment, bol_result=booking_result)
			shipment.reload()
			shipment.bol_number = res.get("bol_number") or shipment.bol_number
			shipment.pro_number = res.get("pro_number") or shipment.pro_number
			shipment.status = "Booked"
			shipment.dispatch_status = "Pending"
			shipment.save(ignore_permissions=True)

			png_result = {}
			try:
				png_result = self.adapter.get_bol_document_image(shipment, raise_on_empty=False)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "LTL Quote - SMC3 BOL PNG Fetch Failure")
				png_result = {}
			if png_result.get("images"):
				img_res = attach_smc3_bol_images_to_shipment(shipment, document_result=png_result)
				if img_res.get("status") == "success":
					shipment.reload()
		elif not shipment.bol_number:
			# Mock / carriers without a real BOL id still need a placeholder reference.
			shipment.bol_number = f"BOL-{shipment.name}"
			shipment.save(ignore_permissions=True)

		return shipment

	def _sync_quote_request_bol(self, shipment, booking_result: dict) -> None:
		"""Mirror BOL fields onto the quote request for UI / accept-path responses."""
		self.quote_request.bol_number = shipment.bol_number or booking_result.get("bol_number")
		self.quote_request.pro_number = shipment.pro_number or booking_result.get("pro_number")
		bol_url = (
			getattr(shipment, "bol_image", None)
			or shipment.bol_document_url
			or shipment.bol_document
			or booking_result.get("bol_document_url")
			or ""
		)
		if bol_url:
			self.quote_request.bol_document_url = bol_url

		carrier_label = self.carrier_code or shipment.carrier
		if self.is_dayton_carrier:
			self.quote_request.add_comment(
				text=(
					f"<b>Dayton Freight eBOL Confirmed Successfully</b><br>"
					f"BOL #: {self.quote_request.bol_number}<br>"
					f"PRO #: {self.quote_request.pro_number}"
					+ (
						f"<br><a href='{bol_url}' target='_blank' "
						f"class='btn btn-xs btn-primary' style='margin-top: 5px; color: #fff;'>"
						f"Download BOL PDF</a>"
						if bol_url
						else ""
					)
				)
			)
		elif self.is_tforce_carrier:
			self.quote_request.add_comment(
				text=(
					f"<b>TForce Freight BOL Confirmed Successfully</b><br>"
					f"BOL #: {self.quote_request.bol_number}<br>"
					f"PRO #: {self.quote_request.pro_number}"
					+ (
						f"<br>Pickup Confirmation #: {booking_result.get('pickup_number')}"
						if booking_result.get("pickup_number")
						else ""
					)
					+ (
						f"<br><a href='{bol_url}' target='_blank' "
						f"class='btn btn-xs btn-primary' style='margin-top: 5px; color: #fff;'>"
						f"Download BOL PDF</a>"
						if bol_url
						else ""
					)
				)
			)
		elif self.is_arcbest_carrier and bol_url:
			self.quote_request.add_comment(
				text=(
					f"<b>ArcBest BOL Generated!</b><br>"
					f"<a href='{bol_url}' target='_blank'>Download PDF</a>"
				)
			)
		elif self.is_smc3_carrier:
			txn = booking_result.get("carrier_confirmation") or ""
			self.quote_request.add_comment(
				text=(
					f"<b>SMC3 BOL Generated</b><br>"
					f"PRO #: {self.quote_request.pro_number}<br>"
					f"BOL #: {self.quote_request.bol_number}"
					+ (f"<br>Transaction ID: {txn}" if txn else "")
					+ (
						f"<br><a href='{bol_url}' target='_blank' "
						f"class='btn btn-xs btn-primary' style='margin-top: 5px; color: #fff;'>"
						f"Download BOL PDF</a>"
						if bol_url
						else ""
					)
				)
			)
		elif carrier_label:
			self.quote_request.add_comment(
				text=f"Shipment booked with {carrier_label}. BOL #: {self.quote_request.bol_number}"
			)

	@staticmethod
	def dispatch_shipment(shipment) -> dict:
		from ltl_quote.carrier_network.pickup import map_pickup_status_to_dispatch_status

		carrier = frappe.get_doc("LTL Carrier", shipment.carrier)
		adapter = get_adapter(carrier)
		result = adapter.dispatch_shipment(
			{
				"shipment_name": shipment.name,
				"pro_number": shipment.pro_number,
				"bol_number": shipment.bol_number,
				"pickup_date": shipment.pickup_date,
			}
		)
		shipment.reload()
		if result.get("status") == "acknowledged":
			shipment.dispatch_status = map_pickup_status_to_dispatch_status(
				result.get("pickup_status"),
				result.get("status"),
			)
			if shipment.status == "Booked":
				shipment.status = "Dispatched"
		elif result.get("ok") is False or result.get("success") is False:
			shipment.dispatch_status = "Failed"
		else:
			shipment.dispatch_status = map_pickup_status_to_dispatch_status(
				result.get("pickup_status"),
				result.get("status"),
			)
		shipment.save(ignore_permissions=True)
		frappe.db.commit()
		return result
