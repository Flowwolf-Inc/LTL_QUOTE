# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.utils import cint, flt, getdate


def update_ltl_shipment_with_dayton_bol(
	shipment_name: str,
	dayton_payload: dict,
	booking_response: dict | None = None,
	bol_result: dict | None = None,
	bol_file_url: str | None = None,
) -> None:
	"""Persist Dayton eBOL party/commodity details onto an LTL Shipment."""
	if not shipment_name or not frappe.db.exists("LTL Shipment", shipment_name):
		return

	dayton_payload = dayton_payload or {}
	bol_result = bol_result or {}
	booking_response = booking_response or {}

	shipment = frappe.get_doc("LTL Shipment", shipment_name)
	_apply_party_fields(shipment, dayton_payload)
	_apply_service_fields(shipment, dayton_payload)
	_apply_identifiers(shipment, bol_result, booking_response)
	_apply_scac_and_document_meta(shipment, dayton_payload)
	_apply_line_items(shipment, dayton_payload)
	_apply_totals(shipment, dayton_payload)

	if bol_file_url:
		shipment.bol_document = bol_file_url
		shipment.bol_document_url = bol_file_url
	elif shipment.bol_document and not shipment.bol_document_url:
		shipment.bol_document_url = shipment.bol_document

	shipment.save(ignore_permissions=True)
	frappe.db.commit()


def _party_contact(party: dict) -> dict:
	contact = party.get("contact") or {}
	if not isinstance(contact, dict):
		contact = {}
	return contact


def _apply_party_fields(shipment, dayton_payload: dict) -> None:
	origin = dayton_payload.get("origin") or {}
	destination = dayton_payload.get("destination") or {}
	bill_to = dayton_payload.get("billTo") or {}

	origin_contact = _party_contact(origin)
	destination_contact = _party_contact(destination)
	bill_to_contact = _party_contact(bill_to)

	shipment.bol_shipper_name = origin.get("name") or shipment.bol_shipper_name
	shipment.bol_shipper_address1 = origin.get("address1") or shipment.bol_shipper_address1
	shipment.bol_shipper_city = origin.get("city") or shipment.bol_shipper_city
	shipment.bol_shipper_state = origin.get("stateProvince") or shipment.bol_shipper_state
	shipment.bol_shipper_postal_code = origin.get("postalCode") or shipment.bol_shipper_postal_code
	shipment.bol_shipper_contact_name = origin_contact.get("name") or shipment.bol_shipper_contact_name
	shipment.bol_shipper_contact_phone = origin_contact.get("phone") or shipment.bol_shipper_contact_phone

	shipment.bol_consignee_name = destination.get("name") or shipment.bol_consignee_name
	shipment.bol_consignee_address1 = destination.get("address1") or shipment.bol_consignee_address1
	shipment.bol_consignee_city = destination.get("city") or shipment.bol_consignee_city
	shipment.bol_consignee_state = destination.get("stateProvince") or shipment.bol_consignee_state
	shipment.bol_consignee_postal_code = (
		destination.get("postalCode") or shipment.bol_consignee_postal_code
	)
	shipment.bol_consignee_contact_name = (
		destination_contact.get("name") or shipment.bol_consignee_contact_name
	)
	shipment.bol_consignee_contact_phone = (
		destination_contact.get("phone") or shipment.bol_consignee_contact_phone
	)

	shipment.bol_bill_to_name = bill_to.get("name") or shipment.bol_bill_to_name
	shipment.bol_bill_to_address1 = bill_to.get("address1") or shipment.bol_bill_to_address1
	shipment.bol_bill_to_city = bill_to.get("city") or shipment.bol_bill_to_city
	shipment.bol_bill_to_state = bill_to.get("stateProvince") or shipment.bol_bill_to_state
	shipment.bol_bill_to_postal_code = bill_to.get("postalCode") or shipment.bol_bill_to_postal_code
	shipment.bol_bill_to_contact_name = bill_to_contact.get("name") or shipment.bol_bill_to_contact_name
	shipment.bol_bill_to_contact_phone = (
		bill_to_contact.get("phone") or shipment.bol_bill_to_contact_phone
	)


def _apply_service_fields(shipment, dayton_payload: dict) -> None:
	bol = dayton_payload.get("bol") or {}
	payment = dayton_payload.get("payment") or {}
	refs = dayton_payload.get("referenceNumbers") or {}

	shipment.bol_special_instructions = (
		bol.get("specialInstructions") or shipment.bol_special_instructions
	)
	shipment.bol_payment_terms = payment.get("terms") or shipment.bol_payment_terms
	shipment.bol_quote_id = str(refs.get("quoteId") or shipment.bol_quote_id or "")

	pickup = bol.get("requestedPickupDate")
	if pickup:
		try:
			shipment.bol_date = getdate(pickup)
		except Exception:
			pass
	elif shipment.booked_on and not shipment.bol_date:
		shipment.bol_date = getdate(shipment.booked_on)

	shipment.bol_document_type = shipment.bol_document_type or "Bill of Lading"


def _apply_identifiers(shipment, bol_result: dict, booking_response: dict) -> None:
	merged = {**booking_response, **bol_result}
	if merged.get("bol_number"):
		shipment.bol_number = merged["bol_number"]
	if merged.get("pro_number"):
		shipment.pro_number = merged["pro_number"]
	if merged.get("dayton_bol_id"):
		shipment.dayton_bol_id = merged["dayton_bol_id"]
	if merged.get("carrier_confirmation"):
		shipment.carrier_confirmation = merged["carrier_confirmation"]
	elif merged.get("bol_number") and not shipment.carrier_confirmation:
		shipment.carrier_confirmation = merged["bol_number"]


def _apply_scac_and_document_meta(shipment, dayton_payload: dict) -> None:
	if shipment.carrier and frappe.db.exists("LTL Carrier", shipment.carrier):
		scac = frappe.db.get_value("LTL Carrier", shipment.carrier, "scac")
		if scac:
			shipment.bol_scac = scac

	images = dayton_payload.get("images") or {}
	labels = images.get("shippingLabels") or {}
	page_count = labels.get("quantity")
	if page_count is not None:
		shipment.bol_page_count = cint(page_count)


def _apply_line_items(shipment, dayton_payload: dict) -> None:
	commodities = dayton_payload.get("commodities") or {}
	handling_units = commodities.get("handlingUnits") or []
	quote_id = str((dayton_payload.get("referenceNumbers") or {}).get("quoteId") or "")

	shipment.set("bol_line_items", [])
	line_no = 0

	for hu in handling_units:
		if not isinstance(hu, dict):
			continue

		line_items = hu.get("lineItems") or []
		if line_items:
			for line in line_items:
				if not isinstance(line, dict):
					continue
				line_no += 1
				shipment.append(
					"bol_line_items",
					_line_item_from_hu_and_line(line_no, hu, line, quote_id),
				)
		else:
			line_no += 1
			shipment.append(
				"bol_line_items",
				_line_item_from_hu_only(line_no, hu, quote_id),
			)


def _line_item_from_hu_and_line(line_no: int, hu: dict, line: dict, quote_id: str) -> dict:
	description = str(line.get("description") or hu.get("description") or "General Freight Cargo")
	if quote_id and quote_id not in description:
		description = f"{description} (Quote {quote_id})"

	return {
		"idx_line_no": line_no,
		"handling_unit_qty": cint(hu.get("count") or hu.get("handlingUnitQuantity") or 1),
		"handling_unit_type": str(hu.get("type") or hu.get("handlingUnitType") or "PALLET"),
		"package_qty": cint(line.get("pieces") or hu.get("count") or 1),
		"package_type": str(line.get("packagingType") or "SKID"),
		"freight_class": str(
			line.get("classification") or line.get("freightClass") or hu.get("class") or hu.get("freightClass") or ""
		),
		"nmfc": str(line.get("nmfc") or ""),
		"hazmat": 1 if line.get("hazardous") or hu.get("hazardous") else 0,
		"commodity_description": description,
		"weight": flt(line.get("weight") if line.get("weight") is not None else hu.get("weight")),
		"weight_unit": str(line.get("weightUnit") or hu.get("weightUnit") or "LBS"),
		"length": cint(hu.get("length") or 0) or None,
		"width": cint(hu.get("width") or 0) or None,
		"height": cint(hu.get("height") or 0) or None,
		"dimension_unit": str(hu.get("dimensionsUnit") or "IN"),
		"quote_reference": quote_id,
	}


def _line_item_from_hu_only(line_no: int, hu: dict, quote_id: str) -> dict:
	description = str(hu.get("description") or "General Freight Cargo")
	if quote_id and quote_id not in description:
		description = f"{description} (Quote {quote_id})"

	return {
		"idx_line_no": line_no,
		"handling_unit_qty": cint(hu.get("count") or hu.get("handlingUnitQuantity") or 1),
		"handling_unit_type": str(hu.get("type") or hu.get("handlingUnitType") or "PALLET"),
		"package_qty": cint(hu.get("count") or 1),
		"package_type": str(hu.get("packagingType") or "SKID"),
		"freight_class": str(hu.get("class") or hu.get("freightClass") or ""),
		"nmfc": str(hu.get("nmfc") or ""),
		"hazmat": 1 if hu.get("hazardous") else 0,
		"commodity_description": description,
		"weight": flt(hu.get("weight")),
		"weight_unit": str(hu.get("weightUnit") or "LBS"),
		"length": cint(hu.get("length") or 0) or None,
		"width": cint(hu.get("width") or 0) or None,
		"height": cint(hu.get("height") or 0) or None,
		"dimension_unit": str(hu.get("dimensionsUnit") or "IN"),
		"quote_reference": quote_id,
	}


def _apply_totals(shipment, dayton_payload: dict) -> None:
	totals = dayton_payload.get("shipmentTotals") or {}
	if totals.get("handlingUnits") is not None:
		shipment.bol_total_quantity = cint(totals.get("handlingUnits"))
	elif shipment.bol_line_items:
		shipment.bol_total_quantity = sum(
			cint(row.handling_unit_qty or 0) for row in shipment.bol_line_items
		)

	if totals.get("grossWeight") is not None or totals.get("netWeight") is not None:
		shipment.bol_grand_total_weight = flt(
			totals.get("grossWeight") if totals.get("grossWeight") is not None else totals.get("netWeight")
		)
	elif shipment.bol_line_items:
		shipment.bol_grand_total_weight = sum(flt(row.weight or 0) for row in shipment.bol_line_items)
