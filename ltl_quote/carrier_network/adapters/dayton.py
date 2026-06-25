import base64
import json

import frappe
import requests
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime, today
from frappe.utils.file_manager import save_file

from ltl_quote.carrier_network.accessorials import (
	build_accessorial_items,
	dayton_rate_accessorials,
)
from ltl_quote.carrier_network.adapters.base import BaseCarrierAdapter, CarrierRateQuote, ShipmentRequest
from ltl_quote.utils.booking import resolve_shipper_context
from ltl_quote.utils.location import resolve_us_location

DEFAULT_BASE_URL = "https://api.daytonfreight.com"
DEFAULT_ACCOUNT_NUMBER = "0055666"
REQUEST_TIMEOUT = 15

TEST_BOL_PDF_BASE64 = (
	"JVBERi0xLjEKMSAwIG9iajw8IC9UeXBlIC9DYXRhbG9nIC9QYWdlcyAyIDAgUiA+PmVuZG9iagoyIDAgb2Jq"
	"PDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj5lbmRvYmoKMyAwIG9iajw8IC9UeXBl"
	"IC9QYWdlIC9NZWRpYUJveCBbMCAwIDYxMiA3OTJdIC9QYXJlbnQgMiAwIFIgL1Jlc291cmNlcyA8PCA+PiA+"
	"PmVuZG9iagp4cmVmCjAgNAowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMDkgMDAwMDAgbiAKMDAwMDAw"
	"MDA1MiAwMDAwMCBuIAowMDAwMDAwMTAxIDAwMDAwIG4gCnRyYWlsZXI8PCAvU2l6ZSA0IC9Sb290IDEgMCBS"
	"ID4+CnN0YXJ0eHJlZgoxNzgKJSVFT0Y="
)

DAYTON_BOL_NOT_READY_MESSAGE = (
	"Dayton Freight has registered the booking, but the official BOL document binary "
	"is not yet finalized on their server. Please try again in a few minutes."
)

MIN_DAYTON_DOCUMENT_BYTES = 100


class DaytonCarrierAdapter(BaseCarrierAdapter):
	"""Dayton Freight Lines production API connector."""

	def __init__(self, carrier_doc=None):
		super().__init__(carrier_doc)

		self.carrier_doc = carrier_doc or self.carrier
		if not self.carrier_doc and getattr(self.carrier, "name", None):
			self.carrier_doc = self.carrier
		elif not self.carrier_doc:
			if frappe.db.exists("LTL Carrier", "DAYTON"):
				self.carrier_doc = frappe.get_doc("LTL Carrier", "DAYTON")

		if not self.carrier_doc:
			frappe.throw("Dayton carrier record (DAYTON) not found in LTL Carrier.")

		self.carrier = self.carrier_doc

		self.base_url = (self.carrier_doc.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")
		self.account_number = self.carrier_doc.get("account_number") or DEFAULT_ACCOUNT_NUMBER

		self.username = self.carrier_doc.get_password("api_key", raise_exception=False) or ""
		self.password = self.carrier_doc.get_password("api_secret", raise_exception=False) or ""

	def get_headers(self) -> dict:
		"""Compile a standard Base64 Basic Authentication block using web credentials."""
		headers = {"Content-Type": "application/json"}

		if self.username and self.password:
			clean_username = self.username[:10]
			auth_string = f"{clean_username}:{self.password}"
			encoded_auth = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
			headers["Authorization"] = f"Basic {encoded_auth}"

		return headers

	def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
		"""Maps LTL Quote Request schema -> Dayton Freight Rates API -> CarrierRateQuote."""
		endpoint = f"{self.base_url}/api/Rates"
		service_option = self._resolve_service_option(request.accessorial_codes)
		dayton_accessorials = dayton_rate_accessorials(request.accessorials)

		clean_weight = self._clean_int(request.total_weight)
		clean_class = self._clean_float(request.freight_class, 70)
		clean_pieces = self._clean_int(request.pieces, 1)

		dayton_payload = {
			"accessorials": dayton_accessorials,
			"account": self.account_number,
			"destination": str(request.destination_zip),
			"directOnly": False,
			"handlingUnits": [],
			"items": [
				{
					"weight": clean_weight,
					"class": clean_class,
					"pieces": clean_pieces,
					"description": "LTL Quote Freight Line",
				}
			],
			"origin": str(request.origin_zip),
			"serviceOptions": service_option,
			"shipmentDate": now_datetime().strftime("%Y-%m-%dT%H:%M:%S"),
			"skids": None,
			"terms": "Prepaid",
		}

		headers = self.get_headers()

		try:
			response = requests.post(
				endpoint,
				headers=headers,
				json=dayton_payload,
				timeout=REQUEST_TIMEOUT,
			)

			if response.status_code != 200:
				error_details = f"Status Code: {response.status_code} | Response: {response.text}"
				frappe.msgprint(msg=error_details, title="Dayton API Raw Rejection", indicator="red")
				frappe.log_error(f"Dayton API Error: {response.text}", "LTL Quote - Dayton Rate Failure")
				return CarrierRateQuote(
					carrier_code=self.carrier_code,
					carrier_name=self.carrier.carrier_name,
					total_charge=0,
					transit_days=0,
					error=f"Dayton API error: {error_details}",
				)

			data = response.json()
			parsed = self._parse_rate_response(data)

			return CarrierRateQuote(
				carrier_code=self.carrier_code,
				carrier_name=self.carrier.carrier_name or parsed["carrier_name"],
				total_charge=parsed["total_charge"],
				transit_days=parsed["transit_days"],
				linehaul_charge=parsed["base_charge"],
				fuel_surcharge=parsed["fuel_surcharge"],
				accessorial_charge=parsed["accessorial_charge"],
				currency="USD",
				carrier_quote_id=f"DAY-{parsed['quote_id']}" if parsed["quote_id"] else "",
				service_level=parsed["movement_type"],
				reliability_score=float(getattr(self.carrier, "reliability_score", None) or 90),
				raw_response=parsed["raw_response"],
			)

		except (ValueError, TypeError, KeyError) as e:
			error_details = f"Dayton response parsing error: {e}"
			frappe.msgprint(msg=error_details, title="Dayton API Parse Failure", indicator="red")
			frappe.log_error(error_details, "LTL Quote - Dayton Parse Failure")
			return CarrierRateQuote(
				carrier_code=self.carrier_code,
				carrier_name=self.carrier.carrier_name,
				total_charge=0,
				transit_days=0,
				error=error_details,
			)

		except requests.exceptions.RequestException as e:
			error_details = f"Connection Error: {e}"
			frappe.msgprint(msg=error_details, title="Dayton API Connection Failure", indicator="red")
			frappe.log_error(str(e), "LTL Quote - Dayton Connection Error")
			return CarrierRateQuote(
				carrier_code=self.carrier_code,
				carrier_name=self.carrier.carrier_name,
				total_charge=0,
				transit_days=0,
				error=f"Dayton connection error: {e}",
			)

	def generate_bill_of_lading(self, quote_data: dict) -> dict:
		"""Create a Dayton eBOL from platform quote_data or a pre-built dayton_payload."""
		endpoint = (
			f"{self.base_url}/api/BillOfLading/v2/CreateStandardElectronicBillOfLading"
		)
		dayton_ebol_payload = self._resolve_dayton_ebol_payload(quote_data)

		frappe.log_error(frappe.as_json(dayton_ebol_payload), "DAYTON REQUEST")
		frappe.logger("dayton").info(f"Dayton eBOL payload: {frappe.as_json(dayton_ebol_payload)}")

		try:
			response = requests.post(
				endpoint,
				headers=self.get_headers(),
				json=dayton_ebol_payload,
				timeout=20,
			)
			frappe.log_error(response.text, "DAYTON RESPONSE")
			frappe.logger("dayton").info(f"DAYTON RAW RESPONSE: {response.text}")
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton eBOL Connection Error")
			frappe.throw(f"Dayton eBOL request failed: {e}")

		if not response.text:
			frappe.throw(f"Empty response received from Dayton API. HTTP Status: {response.status_code}")

		if "html" in response.text.lower() or "<!doctype html>" in response.text.lower():
			frappe.log_error(response.text, "Dayton Returned HTML Error Webpage")
			frappe.throw(
				(
					f"Dayton returned an HTML web page instead of JSON data (HTTP Status {response.status_code}). "
					"Please check your 'Error Log List' in Frappe for full response markup."
				),
				title="Dayton Connection Error",
			)

		try:
			res_data = response.json()
		except Exception:
			frappe.log_error(
				f"Failed to decode JSON. Raw response content: {response.text}",
				"Dayton Text Payload",
			)
			frappe.throw(
				f"Dayton returned a non-JSON string response (HTTP {response.status_code}): {response.text[:250]}"
			)

		message_status = res_data.get("messageStatus") or {}
		status_text = str(message_status.get("status") or "").lower()
		acceptable_statuses = {"success", "pass"}
		if status_text and status_text not in acceptable_statuses:
			frappe.throw(
				f"Dayton eBOL Creation Failed ({response.status_code}): {frappe.as_json(res_data)}"
			)

		if response.status_code != 200 or res_data.get("errors"):
			frappe.throw(
				f"Dayton eBOL Creation Failed ({response.status_code}): {frappe.as_json(res_data)}"
			)

		ref_nums = res_data.get("referenceNumbers") or {}
		bol_number = str(ref_nums.get("shipmentConfirmationNumber") or "")
		pro_number = str(ref_nums.get("pro") or "")
		dayton_bol_id = str(res_data.get("id") or bol_number or "")

		images_dict = res_data.get("images") or {}
		base64_pdf = images_dict.get("bol") or images_dict.get("bolDocument") or ""
		base64_labels = images_dict.get("shippingLabels") or ""

		if quote_data.get("return_raw_file"):
			if not base64_pdf:
				frappe.throw("No document binary returned from Dayton Freight API.")

			pdf_bytes = base64.b64decode(base64_pdf)

			frappe.response["filename"] = f"BOL_{bol_number or 'draft'}.pdf"
			frappe.response["filecontent"] = pdf_bytes
			frappe.response["type"] = "pdf"
			return

		return {
			"status": "booked",
			"bol_number": bol_number,
			"pro_number": pro_number,
			"carrier_confirmation": bol_number,
			"dayton_bol_id": dayton_bol_id,
			"document_binary": base64_pdf,
			"shipping_labels_binary": base64_labels,
		}

	def update_electronic_bol(self, shipment_name: str) -> dict:
		"""Map Frappe shipment data to Dayton UPDATE eBOL schema and persist returned PDF."""
		shipment = frappe.get_doc("LTL Shipment", shipment_name)
		if not shipment.dayton_bol_id:
			frappe.throw("Dayton BOL ID is required before updating an electronic BOL.")

		quote_request = frappe.get_doc("LTL Quote Request", shipment.quote_request)
		payload = self._build_dayton_update_payload(shipment, quote_request)
		endpoint = (
			f"{self.base_url}/api/BillOfLading/v2/UpdateStandardElectronicBillOfLading"
		)

		try:
			response = requests.post(
				endpoint,
				data=json.dumps(payload),
				headers=self.get_headers(),
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as exc:
			frappe.log_error(str(exc), "LTL Quote - Dayton eBOL Update Connection Error")
			return {"success": False, "error": str(exc)}

		if response.status_code != 200:
			error_body = response.text or f"HTTP {response.status_code} with empty body"
			frappe.log_error(
				f"Dayton Update BOL HTTP {response.status_code}: {error_body}",
				"LTL Carrier API Sync Fail",
			)
			return {"success": False, "error": error_body}

		try:
			response_data = response.json()
		except ValueError:
			frappe.log_error(response.text, "LTL Quote - Dayton eBOL Update Parse Failure")
			return {"success": False, "error": response.text}

		message_status = response_data.get("messageStatus") or {}
		status_text = str(message_status.get("status") or "").lower()
		if status_text and status_text not in {"success", "pass"}:
			return {"success": False, "error": frappe.as_json(response_data)}

		if response_data.get("errors"):
			return {"success": False, "error": frappe.as_json(response_data)}

		images_dict = response_data.get("images") or {}
		base64_pdf_string = images_dict.get("bol") or images_dict.get("bolDocument") or ""
		if not _is_usable_dayton_document_binary(base64_pdf_string):
			return {
				"success": False,
				"status": "info",
				"message": DAYTON_BOL_NOT_READY_MESSAGE,
			}

		file_doc = attach_base64_pdf_to_shipment(shipment.name, base64_pdf_string)
		return {"success": True, "file_url": file_doc.file_url}

	def _build_dayton_update_payload(self, shipment, quote_request) -> dict:
		"""Build Dayton UPDATE eBOL payload from LTL Shipment and linked quote request."""
		shipper = resolve_shipper_context(quote_request=quote_request)
		platform_settings = frappe.get_single("LTL Platform Settings")

		origin_zip = str(quote_request.origin_zip)
		destination_zip = str(quote_request.destination_zip)
		origin_city, origin_state = resolve_us_location(
			origin_zip,
			quote_request.origin_city,
			quote_request.origin_state,
		)
		destination_city, destination_state = resolve_us_location(
			destination_zip,
			quote_request.destination_city,
			quote_request.destination_state,
		)

		carrier_quote_id = self._shipment_carrier_quote_id(shipment, quote_request)
		handling_unit_count = max(1, cint(flt(quote_request.pieces, 0)))
		weight_lbs = cint(flt(quote_request.total_weight, 0))
		pickup_date = shipment.pickup_date or today()
		special_instructions = frappe.utils.strip_html(shipment.notes or "").strip()
		if not special_instructions:
			special_instructions = "Updated via Flowwolf API Portal."

		handling_units = [
			{
				"count": handling_unit_count,
				"handlingUnitQuantity": handling_unit_count,
				"handlingUnitType": "SKID",
				"type": "SKID",
				"weight": weight_lbs,
				"class": str(quote_request.freight_class or "70"),
			}
		]

		return {
			"id": cint(shipment.dayton_bol_id),
			"bol": {
				"requestedPickupDate": get_datetime(pickup_date).strftime("%Y-%m-%dT%H:%M:%SZ"),
				"function": "UPDATE",
				"isTest": bool(platform_settings.use_mock_carriers),
				"requestorRole": "Shipper",
				"specialInstructions": special_instructions,
			},
			"version": "2.0.0",
			"images": {
				"includeBol": True,
				"includeShippingLabels": True,
				"shippingLabels": {
					"format": "LETTER",
					"quantity": handling_unit_count,
				},
			},
			"referenceNumbers": {
				"quoteId": self._dayton_rate_quote_id({"carrier_quote_id": carrier_quote_id}),
			},
			"payment": {
				"terms": "Prepaid",
			},
			"commodities": {
				"lineItemLayout": "STACKED",
				"handlingUnits": handling_units,
			},
			"shipmentTotals": {
				"grossWeight": weight_lbs,
				"handlingUnits": handling_unit_count,
				"weightUnit": "LBS",
			},
			"origin": {
				"account": self.account_number,
				"name": shipper["shipper_name"],
				"address1": shipper["shipper_address"],
				"city": str(origin_city or quote_request.origin_city or "Dayton"),
				"stateProvince": str(origin_state or quote_request.origin_state or "OH"),
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"name": shipper["contact_name"],
					"phone": self._dayton_contact_phone(shipper.get("contact_phone")),
				},
			},
			"destination": {
				"name": shipper["consignee_name"],
				"address1": shipper["consignee_address"],
				"city": str(destination_city or quote_request.destination_city or "Chicago"),
				"stateProvince": str(destination_state or quote_request.destination_state or "IL"),
				"postalCode": destination_zip,
				"country": "USA",
				"contact": {
					"name": shipper["consignee_name"],
					"phone": self._dayton_contact_phone(),
				},
			},
			"billTo": {
				"account": self.account_number,
				"name": shipper["shipper_name"],
				"address1": shipper["shipper_address"],
				"city": str(origin_city or quote_request.origin_city or "Dayton"),
				"stateProvince": str(origin_state or quote_request.origin_state or "OH"),
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"name": shipper["contact_name"],
					"phone": self._dayton_contact_phone(shipper.get("contact_phone")),
				},
			},
		}

	@staticmethod
	def _shipment_carrier_quote_id(shipment, quote_request) -> str:
		idx = cint(quote_request.selected_carrier_quote)
		quotes = quote_request.carrier_quotes or []
		if 0 <= idx < len(quotes):
			return str(quotes[idx].carrier_quote_id or "")
		return str(shipment.carrier_confirmation or "")

	def _resolve_dayton_ebol_payload(self, quote_data: dict) -> dict:
		"""Use explicit dayton_payload, a root-level Dayton schema, or build from platform fields."""
		explicit = quote_data.get("dayton_payload")
		if explicit:
			return explicit

		dayton_keys = {
			"version",
			"bol",
			"images",
			"referenceNumbers",
			"payment",
			"commodities",
			"shipmentTotals",
			"origin",
			"destination",
			"billTo",
		}
		if dayton_keys & quote_data.keys():
			return {key: quote_data[key] for key in dayton_keys if key in quote_data}

		return self._build_dayton_ebol_payload(quote_data)

	def _build_dayton_ebol_payload(self, quote_data: dict) -> dict:
		"""Maps platform inputs into Dayton's NMFTA / LTL Digital Council v2 eBOL API layout."""
		quote_request = self._load_quote_request(quote_data)
		shipper = resolve_shipper_context(quote_data, quote_request)

		origin_zip = str(quote_data.get("origin_zip") or quote_request.origin_zip)
		destination_zip = str(quote_data.get("destination_zip") or quote_request.destination_zip)
		origin_city, origin_state = resolve_us_location(
			origin_zip,
			quote_data.get("origin_city") or quote_request.origin_city,
			quote_data.get("origin_state") or quote_request.origin_state,
		)
		destination_city, destination_state = resolve_us_location(
			destination_zip,
			quote_data.get("destination_city") or quote_request.destination_city,
			quote_data.get("destination_state") or quote_request.destination_state,
		)

		origin_name = str(shipper.get("shipper_name") or "Main Warehouse Dispatch")
		origin_address1 = str(shipper.get("shipper_address") or "123 Logistics Way")
		origin_city = str(origin_city or quote_data.get("origin_city") or quote_request.origin_city or "Dayton")
		origin_state = str(origin_state or quote_data.get("origin_state") or quote_request.origin_state or "OH")

		destination_name = str(
			shipper.get("consignee_name")
			or quote_data.get("consignee_name")
			or quote_data.get("consignee_company_name")
			or "Destination Receiver"
		)
		destination_address1 = str(
			shipper.get("consignee_address")
			or quote_data.get("consignee_address")
			or "456 Customer Ave"
		)
		destination_city = str(
			destination_city
			or quote_data.get("destination_city")
			or quote_request.destination_city
			or "Chicago"
		)
		destination_state = str(
			destination_state
			or quote_data.get("destination_state")
			or quote_request.destination_state
			or "IL"
		)

		origin_contact_name = str(
			quote_data.get("origin_contact_name")
			or shipper.get("contact_name")
			or "Shipping Desk"
		)
		origin_contact_phone = self._dayton_contact_phone(
			quote_data.get("origin_contact_phone"),
			shipper.get("contact_phone"),
		)
		destination_contact_name = str(
			quote_data.get("destination_contact_name")
			or quote_data.get("consignee_contact_name")
			or "Receiving Dock"
		)
		destination_contact_phone = self._dayton_contact_phone(
			quote_data.get("destination_contact_phone"),
			quote_data.get("consignee_contact_phone"),
		)

		raw_weight = quote_data.get("total_weight") or quote_request.total_weight
		weight_lbs = cint(flt(raw_weight, 0))
		handling_unit_count = self._resolve_handling_unit_count(quote_data, quote_request)

		return {
			"version": "2.0.0",
			"bol": {
				"requestedPickupDate": now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
				"function": "CREATE",
				"isTest": bool(quote_data.get("is_test", False)),
				"requestorRole": "Shipper",
				"specialInstructions": quote_data.get("special_instructions", "LTL Freight Shipment"),
			},
			"payment": {
				"terms": "Prepaid",
			},
			"referenceNumbers": {
				"quoteId": self._dayton_rate_quote_id(quote_data),
			},
			"commodities": {
				"handlingUnits": [
					{
						"count": handling_unit_count,
						"type": "PT",
						"weight": weight_lbs,
						"weightUnit": "LB",
						"freightClass": str(
							quote_data.get("freight_class") or quote_request.freight_class or "70"
						),
						"description": "Palletized Freight",
					}
				],
				"lineItemLayout": "STACKED",
			},
			"origin": {
				"account": self.account_number,
				"name": origin_name,
				"address1": origin_address1,
				"city": origin_city,
				"stateProvince": origin_state,
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"name": origin_contact_name,
					"phone": origin_contact_phone,
				},
			},
			"destination": {
				"name": destination_name,
				"address1": destination_address1,
				"city": destination_city,
				"stateProvince": destination_state,
				"postalCode": destination_zip,
				"country": "USA",
				"contact": {
					"name": destination_contact_name,
					"phone": destination_contact_phone,
				},
			},
			"billTo": {
				"account": self.account_number,
				"name": origin_name,
				"address1": origin_address1,
				"city": origin_city,
				"stateProvince": origin_state,
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"name": origin_contact_name,
					"phone": origin_contact_phone,
				},
			},
		}

	def book_shipment(self, quote_data: dict) -> dict:
		"""Book shipment via Dayton POST /api/BillOfLading/v2/CreateStandardElectronicBillOfLading."""
		return self.generate_bill_of_lading(quote_data)

	def request_pickup(self, quote_data: dict) -> dict:
		"""Maps quote booking context -> Dayton PUT /api/Pickup (optional dispatch step)."""
		endpoint = f"{self.base_url}/api/Pickup"
		quote_request = self._load_quote_request(quote_data)
		pickup_date = quote_data.get("pickup_date") or today()
		pickup_accessorials = dayton_rate_accessorials(build_accessorial_items(getattr(quote_request, "accessorials", None)))

		shipper = resolve_shipper_context(quote_data, quote_request)

		origin_zip = str(quote_data.get("origin_zip") or quote_request.origin_zip)
		origin_city, origin_state = resolve_us_location(
			origin_zip,
			quote_data.get("origin_city") or quote_request.origin_city,
			quote_data.get("origin_state") or quote_request.origin_state,
		)
		if not origin_state:
			frappe.throw(
				"Origin state is required for Dayton pickup booking. Provide origin state or a valid US origin ZIP."
			)

		dayton_pickup_payload = {
			"customerReferenceNumber": str(quote_data.get("quote_request") or quote_data.get("carrier_quote_id")),
			"sendConfirmationTo": [frappe.session.user],
			"sendReceiptTo": [],
			"details": [
				{
					"destinationZip": str(quote_data.get("destination_zip") or quote_request.destination_zip),
					"handlingUnits": self._clean_int(quote_data.get("pieces") or quote_request.pieces, 1),
					"weight": self._clean_int(quote_data.get("total_weight") or quote_request.total_weight),
					"isHazardous": bool(quote_data.get("is_hazardous", False)),
				}
			],
			"shipper": {
				"name": str(
					shipper.get("shipper_name")
					or quote_data.get("shipper_company_name")
					or "Main Warehouse Dispatch"
				),
				"address": {
					"address1": str(
						shipper.get("shipper_address")
						or quote_data.get("shipper_address")
						or "123 Logistics Way"
					),
					"city": str(origin_city or quote_data.get("origin_city") or quote_request.origin_city or "Dayton"),
					"state": str(origin_state or quote_data.get("origin_state") or quote_request.origin_state or "OH"),
					"zip": origin_zip,
				},
			},
			"ready": f"{pickup_date}T09:00:00",
			"close": f"{pickup_date}T17:00:00",
			"contact": {
				"name": str(
					quote_data.get("contact_name") or shipper.get("contact_name") or "Shipping Desk"
				),
				"phone": str(
					quote_data.get("contact_phone") or shipper.get("contact_phone") or "0000000000"
				),
				"extension": None,
				"fax": None,
				"email": None,
			},
			"requester": {
				"name": str(frappe.session.user),
				"phone": "0000000000",
				"extension": None,
				"fax": None,
				"email": None,
			},
			"accessorials": pickup_accessorials,
			"comments": "Booked via LTL Quote platform",
			"pickupInstructions": None,
			"isTest": False,
		}

		try:
			response = requests.put(
				endpoint,
				headers=self.get_headers(),
				json=dayton_pickup_payload,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup Connection Error")
			frappe.throw(f"Dayton pickup request failed: {e}")

		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Pickup Failure")
			frappe.throw(f"Dayton pickup request failed: {response.text}")

		res_data = response.json()
		shipments = res_data.get("shipments") or [{}]
		transit_days = int(quote_data.get("transit_days") or 2)

		return {
			"status": "booked",
			"pro_number": shipments[0].get("pro"),
			"bol_number": res_data.get("pickupNumber"),
			"carrier_confirmation": res_data.get("pickupNumber"),
			"estimated_delivery": add_days(now_datetime(), transit_days),
		}

	def get_tracking(self, pro_number: str) -> list[dict]:
		"""Poll Dayton tracking endpoints for live milestone event logs."""
		events = self._fetch_tracking_by_number(pro_number)
		if events:
			return events

		endpoint = f"{self.base_url}/api/Tracking/{pro_number}"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking Connection Error")
			return []

		if response.status_code in (401, 403):
			frappe.throw("Authentication failed with Dayton Lines API.")

		if response.status_code != 200:
			frappe.log_error(
				f"Tracking query failed for PRO {pro_number}: {response.text}",
				"Dayton Tracking Error",
			)
			return []

		data = response.json()
		events = []
		raw_events = data if isinstance(data, list) else data.get("events") or data.get("results") or []
		for event in raw_events:
			events.append(self._parse_dayton_tracking_event(event))
		return events

	def _fetch_tracking_by_number(self, pro_number: str) -> list[dict]:
		"""Query Dayton GET /api/Tracking/ByNumber for PRO-based milestone history."""
		endpoint = f"{self.base_url}/api/Tracking/ByNumber"
		params = {"type": "pro", "number": pro_number}

		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				params=params,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking ByNumber Connection Error")
			return []

		if response.status_code in (401, 403):
			frappe.throw("Authentication failed with Dayton Lines API.")

		if response.status_code != 200:
			return []

		data = response.json()
		results = data.get("results") if isinstance(data, dict) else data
		if not isinstance(results, list):
			return []

		return [self._parse_dayton_tracking_event(event) for event in results]

	@staticmethod
	def _parse_dayton_tracking_event(event: dict) -> dict:
		city = str(event.get("city") or "").strip()
		state = str(event.get("state") or "").strip()
		location = event.get("location")
		if not location and (city or state):
			location = ", ".join(part for part in (city, state) if part)

		return {
			"event_datetime": (
				event.get("eventTime")
				or event.get("dateTime")
				or event.get("date")
				or event.get("event_datetime")
			),
			"status_code": event.get("statusCode") or event.get("status_code") or "IN_TRANSIT",
			"status_description": (
				event.get("status")
				or event.get("description")
				or event.get("status_description")
				or event.get("remarks")
				or event.get("comment")
				or "Cargo Movement Updated"
			),
			"location": location or "Terminal Center",
			"is_exception": 1 if event.get("isException") else 0,
		}

	def get_proof_of_delivery(self, pro_number: str) -> dict:
		"""Fetch signed POD document from Dayton GET /api/Documents/{proNumber}?type=POD."""
		endpoint = f"{self.base_url}/api/Documents/{pro_number}?type=POD"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
			if response.status_code == 200:
				res_data = response.json()
				return {
					"pod_available": True,
					"signed_by": res_data.get("signedBy") or "Consignee Signed",
					"delivery_date": res_data.get("deliveryDate"),
					"document_base64": res_data.get("imageDocument"),
				}
		except Exception as e:
			frappe.log_error(
				f"Failed to fetch document image for PRO {pro_number}: {e}",
				"Dayton Image Error",
			)

		return {"pod_available": False, "message": "Proof of delivery document is not available yet."}

	def get_bol_document(self, pro_number: str) -> dict:
		"""Fetch BOL document from Dayton GET /api/Documents/{proNumber}?type=BOL."""
		endpoint = f"{self.base_url}/api/Documents/{pro_number}?type=BOL"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
			if response.status_code == 200:
				res_data = response.json()
				document_base64 = (
					res_data.get("imageDocument")
					or res_data.get("bol")
					or res_data.get("bolDocument")
					or ""
				)
				if _is_usable_dayton_document_binary(document_base64):
					return {"bol_available": True, "document_base64": document_base64}
		except Exception as e:
			frappe.log_error(
				f"Failed to fetch BOL document for PRO {pro_number}: {e}",
				"Dayton BOL Image Error",
			)

		return {"bol_available": False, "status": "info", "message": DAYTON_BOL_NOT_READY_MESSAGE}

	def cancel_shipment(self, shipment_doc) -> bool:
		"""Cancel a booked pickup via Dayton DELETE /api/Pickup/Cancel."""
		target_number = shipment_doc.bol_number or shipment_doc.carrier_confirmation
		if not target_number:
			return False

		endpoint = f"{self.base_url}/api/Pickup/Cancel?number={target_number}"

		try:
			response = requests.delete(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Cancellation Connection Error")
			return False

		if response.status_code == 200:
			return True

		frappe.log_error(response.text, "LTL Quote - Dayton Cancellation Failure")
		return False

	@staticmethod
	def _dayton_contact_phone(*values: str | None, default: str = "8005551212") -> str:
		"""Return a 10-digit phone string acceptable to Dayton's eBOL API."""
		for value in values:
			digits = "".join(char for char in str(value or "") if char.isdigit())
			if len(digits) >= 10:
				phone = digits[-10:]
				if phone != "0" * 10:
					return phone
		return default

	@staticmethod
	def _resolve_handling_unit_count(quote_data: dict, quote_request) -> int:
		"""Resolve handling unit count from booking payload or loaded quote request (always >= 1)."""
		raw_count = quote_data.get("pieces")
		if raw_count is None:
			raw_count = getattr(quote_request, "pieces", None)
		return max(1, cint(flt(raw_count, 0)))

	@staticmethod
	def _dayton_rate_quote_id(quote_data: dict) -> str:
		"""Return the raw Dayton Rate API id (strip internal DAY- prefix if present)."""
		raw_id = str(quote_data.get("carrier_quote_id") or quote_data.get("quote_id") or "")
		if raw_id.upper().startswith("DAY-"):
			raw_id = raw_id[4:]
		return raw_id

	@staticmethod
	def _parse_rate_response(data: dict) -> dict:
		"""Map Dayton /api/Rates JSON to internal quote fields (exact API key casing)."""
		service_eligibility = data.get("serviceEligibility") or {}
		service_days = service_eligibility.get("serviceDays", 1) or 1

		return {
			"carrier": "DAYTON",
			"carrier_name": "Dayton Freight Lines",
			"quote_id": str(data.get("id") or ""),
			"base_charge": flt(data.get("gross")),
			"fuel_surcharge": flt(data.get("fuelSurchargeAmount")),
			"accessorial_charge": flt(data.get("additionalCharges", 0.0)),
			"discount_amount": flt(data.get("discount")),
			"total_charge": flt(data.get("total")),
			"transit_days": int(service_days),
			"movement_type": data.get("movementType", "Direct"),
			"raw_response": data,
		}

	@staticmethod
	def _clean_numeric_string(value, default=0) -> str:
		return str(value if value is not None else default).replace(",", "")

	@staticmethod
	def _clean_int(value, default=0) -> int:
		return int(float(DaytonCarrierAdapter._clean_numeric_string(value, default)))

	@staticmethod
	def _clean_float(value, default=0) -> float:
		return float(DaytonCarrierAdapter._clean_numeric_string(value, default))

	@staticmethod
	def _resolve_service_option(accessorial_codes: list[str]) -> str:
		codes = {code.upper() for code in (accessorial_codes or [])}
		if "AM" in codes:
			return "AM"
		if "PM" in codes:
			return "PM"
		return "None"

	@staticmethod
	def _load_quote_request(quote_data: dict):
		quote_request_name = quote_data.get("quote_request") or quote_data.get("quote_request_id")
		if quote_request_name and frappe.db.exists("LTL Quote Request", quote_request_name):
			return frappe.get_doc("LTL Quote Request", quote_request_name)
		return frappe._dict(
			{
				"origin_zip": quote_data.get("origin_zip"),
				"destination_zip": quote_data.get("destination_zip"),
				"total_weight": quote_data.get("total_weight"),
				"pieces": max(1, cint(flt(quote_data.get("pieces"), 0))),
				"origin_city": quote_data.get("origin_city"),
				"origin_state": quote_data.get("origin_state"),
				"destination_city": quote_data.get("destination_city"),
				"destination_state": quote_data.get("destination_state"),
				"freight_class": quote_data.get("freight_class"),
				"shipper_company_name": quote_data.get("shipper_company_name"),
				"shipper_address": quote_data.get("shipper_address"),
				"consignee_company_name": quote_data.get("consignee_company_name"),
				"consignee_address": quote_data.get("consignee_address"),
			}
		)


def _save_bol_pdf_file(base64_pdf: str, bol_number: str) -> str:
	"""Decode Dayton images.bol Base64 and persist as a public Frappe File; return full URL."""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"BOL_{bol_number}.pdf",
			"is_private": 0,
			"content": base64_pdf,
			"decode": True,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return f"{frappe.utils.get_url()}{file_doc.file_url}"


def _is_dayton_test_request(request_data: dict, bol_result: dict | None = None) -> bool:
	if request_data.get("is_test"):
		return True

	dayton_payload = request_data.get("dayton_payload") or request_data
	bol_section = dayton_payload.get("bol") or {}
	if bol_section.get("isTest"):
		return True

	if bol_result and str(bol_result.get("bol_number") or "").startswith("Test_"):
		return True

	return False


def _is_usable_dayton_document_binary(document_binary: str | bytes | None) -> bool:
	"""Return True when Dayton returned a non-trivial PDF/image payload."""
	if not document_binary:
		return False

	try:
		if isinstance(document_binary, bytes):
			raw_bytes = document_binary
		else:
			raw_bytes = base64.b64decode(str(document_binary))
	except Exception:
		return False

	return len(raw_bytes) >= MIN_DAYTON_DOCUMENT_BYTES


def _dayton_bol_pending_response(bol_result: dict | None = None) -> dict:
	response = {
		"status": "info",
		"message": DAYTON_BOL_NOT_READY_MESSAGE,
	}
	if bol_result:
		response["bol_number"] = bol_result.get("bol_number")
		response["pro_number"] = bol_result.get("pro_number")
	return response


def _resolve_dayton_bol_binary(request_data: dict, bol_result: dict | None = None) -> dict:
	"""Resolve Dayton BOL PDF bytes and reference numbers from booking payload or API."""
	request_data = dict(request_data or {})
	request_data["return_raw_file"] = False

	if bol_result is None:
		adapter = DaytonCarrierAdapter()
		bol_result = adapter.generate_bill_of_lading(request_data)

	base64_pdf = bol_result.get("document_binary")
	bol_number = bol_result.get("bol_number") or "draft"

	if not _is_usable_dayton_document_binary(base64_pdf):
		if _is_dayton_test_request(request_data, bol_result):
			base64_pdf = TEST_BOL_PDF_BASE64
		else:
			return _dayton_bol_pending_response(bol_result)

	return {
		"status": "success",
		"bol_number": bol_number,
		"pro_number": bol_result.get("pro_number"),
		"document_binary": base64_pdf,
	}


def resolve_dayton_bol_download(request_data: dict, bol_result: dict | None = None) -> dict:
	"""Shared download_dayton_bol logic usable from HTTP API and booking executor."""
	bol = _resolve_dayton_bol_binary(request_data, bol_result=bol_result)
	if bol.get("status") == "info":
		return bol

	full_document_url = _save_bol_pdf_file(bol["document_binary"], bol["bol_number"])

	return {
		"status": "success",
		"bol_number": bol["bol_number"],
		"pro_number": bol.get("pro_number"),
		"document_url": full_document_url,
	}


def attach_dayton_bol_to_shipment(shipment, request_data: dict, bol_result: dict | None = None) -> dict:
	"""Persist Dayton BOL PDF on an LTL Shipment using download_dayton_bol resolution logic."""
	bol = _resolve_dayton_bol_binary(request_data, bol_result=bol_result)
	if bol.get("status") == "info":
		return bol

	file_doc = attach_base64_pdf_to_shipment(shipment.name, bol["document_binary"])

	return {
		"status": "success",
		"bol_number": bol["bol_number"],
		"pro_number": bol.get("pro_number"),
		"document_url": file_doc.file_url,
	}


def attach_base64_pdf_to_shipment(shipment_id: str, base64_string: str):
	"""Decode Dayton images.bol Base64 and attach a private PDF to the shipment BOL field."""
	filename = f"Dayton_Updated_BOL_{shipment_id}.pdf"
	file_bytes = base64.b64decode(base64_string)

	file_doc = save_file(
		fname=filename,
		content=file_bytes,
		dt="LTL Shipment",
		dn=shipment_id,
		is_private=1,
		decode=False,
		df="bol_document",
	)
	frappe.db.set_value("LTL Shipment", shipment_id, "bol_document", file_doc.file_url)
	frappe.db.commit()
	return file_doc


DAYTON_CARRIER_CODE = "DAYTON"
SHIPMENT_TRACKING_ID_FIELDS = (
	"name",
	"dayton_bol_id",
	"pro_number",
	"carrier_confirmation",
	"bol_number",
)


def _normalize_postal_code(postal_code) -> str:
	return str(postal_code or "").strip()[:10]


def _find_booked_dayton_shipments(origin_postal_code, destination_postal_code) -> list[dict]:
	origin = _normalize_postal_code(origin_postal_code)
	destination = _normalize_postal_code(destination_postal_code)
	if not origin or not destination:
		return []

	return frappe.db.sql(
		"""
		SELECT
			s.name,
			s.bol_document,
			s.dayton_bol_id,
			s.pro_number,
			s.carrier_confirmation,
			s.bol_number,
			s.status,
			s.carrier
		FROM `tabLTL Shipment` s
		INNER JOIN `tabLTL Quote Request` q ON q.name = s.quote_request
		WHERE s.status = 'Booked'
			AND s.carrier = %(carrier)s
			AND q.origin_zip = %(origin)s
			AND q.destination_zip = %(destination)s
		ORDER BY s.booked_on DESC, s.modified DESC
		""",
		{"carrier": DAYTON_CARRIER_CODE, "origin": origin, "destination": destination},
		as_dict=True,
	)


def _filter_shipments_by_tracking_id(shipments: list[dict], tracking_or_booking_id) -> list[dict]:
	needle = str(tracking_or_booking_id or "").strip()
	if not needle:
		return shipments

	return [
		shipment
		for shipment in shipments
		if any(str(shipment.get(field) or "").strip() == needle for field in SHIPMENT_TRACKING_ID_FIELDS)
	]


def _resolve_booked_dayton_shipment(shipments: list[dict], tracking_or_booking_id=None) -> tuple[dict | None, dict | None]:
	if not shipments:
		return None, {
			"success": False,
			"error": "No matching booked Dayton shipment found for the provided routing parameters.",
		}

	if tracking_or_booking_id:
		matched = _filter_shipments_by_tracking_id(shipments, tracking_or_booking_id)
		if not matched:
			return None, {
				"success": False,
				"error": "No shipment matched the provided tracking_or_booking_id.",
			}
		if len(matched) > 1:
			return None, {
				"success": False,
				"error": "Multiple booked Dayton shipments match the provided tracking_or_booking_id.",
				"matching_shipments": [row.name for row in matched],
			}
		return matched[0], None

	if len(shipments) == 1:
		return shipments[0], None

	return None, {
		"success": False,
		"error": "Multiple booked Dayton shipments match the provided zip codes. Supply tracking_or_booking_id to disambiguate.",
		"matching_shipments": [row.name for row in shipments],
	}


def _fetch_remote_bol_for_shipment(shipment: dict, adapter: DaytonCarrierAdapter | None = None) -> dict:
	"""Retrieve a missing BOL attachment from Dayton and persist it on the shipment."""
	adapter = adapter or DaytonCarrierAdapter()
	shipment_name = shipment.name

	if shipment.get("dayton_bol_id"):
		result = adapter.update_electronic_bol(shipment_name)
		if result.get("success"):
			return {
				"success": True,
				"file_url": result.get("file_url"),
				"shipment": shipment_name,
				"source": "dayton_update_ebol",
			}
		if result.get("status") == "info":
			return {
				"success": False,
				"status": "info",
				"message": result.get("message") or DAYTON_BOL_NOT_READY_MESSAGE,
				"shipment": shipment_name,
			}
		return {
			"success": False,
			"error": result.get("error") or result.get("message") or "Failed to retrieve BOL from Dayton update eBOL API.",
			"shipment": shipment_name,
		}

	pro_number = str(shipment.get("pro_number") or "").strip()
	if pro_number:
		doc_result = adapter.get_bol_document(pro_number)
		if doc_result.get("bol_available") and doc_result.get("document_base64"):
			file_doc = attach_base64_pdf_to_shipment(shipment_name, doc_result["document_base64"])
			return {
				"success": True,
				"file_url": file_doc.file_url,
				"shipment": shipment_name,
				"source": "dayton_documents_api",
			}

		return {
			"success": False,
			"status": doc_result.get("status") or "info",
			"message": doc_result.get("message") or DAYTON_BOL_NOT_READY_MESSAGE,
			"shipment": shipment_name,
		}

	return {
		"success": False,
		"error": "Matching shipment found but no Dayton BOL ID or PRO number is available to retrieve the document.",
		"shipment": shipment_name,
	}


@frappe.whitelist()
def get_booked_bol_url(origin_postal_code, destination_postal_code, tracking_or_booking_id=None):
	"""
	Looks up an existing, booked shipment matching the routing parameters
	and returns its associated BOL document attachment URL.
	"""
	shipments = _find_booked_dayton_shipments(origin_postal_code, destination_postal_code)
	shipment, error = _resolve_booked_dayton_shipment(shipments, tracking_or_booking_id)
	if error:
		return error

	if shipment.get("bol_document"):
		return {
			"success": True,
			"file_url": shipment.bol_document,
			"shipment": shipment.name,
			"source": "local",
		}

	return _fetch_remote_bol_for_shipment(shipment)


@frappe.whitelist()
def update_electronic_bol(shipment_name: str) -> dict:
	"""Frappe API route: update an existing Dayton eBOL and store the returned PDF."""
	shipment = frappe.get_doc("LTL Shipment", shipment_name)
	carrier = frappe.get_doc("LTL Carrier", shipment.carrier)
	adapter = DaytonCarrierAdapter(carrier)
	return adapter.update_electronic_bol(shipment_name)


@frappe.whitelist(allow_guest=True)
def download_dayton_bol():
	request_data = frappe.request.get_json() or {}
	return resolve_dayton_bol_download(request_data)


@frappe.whitelist()
def fetch_dayton_tracking_updates(shipment_name):
	"""Query Dayton Freight Tracking API and update the LTL Shipment tracking_events table."""
	shipment = frappe.get_doc("LTL Shipment", shipment_name)

	if not _is_dayton_shipment(shipment):
		return {
			"status": "error",
			"message": "Tracking refresh is only supported for Dayton Freight shipments.",
		}

	tracking_number = shipment.pro_number
	if not tracking_number:
		return {
			"status": "error",
			"message": "No PRO or Tracking Number assigned to this shipment yet.",
		}

	try:
		from ltl_quote.visibility.tracker import ShipmentTracker

		result = ShipmentTracker(shipment).refresh()
		if not result.get("events"):
			return {
				"status": "info",
				"message": "Shipment registered, but transit tracking events are not populated yet.",
			}

		return {
			"status": "success",
			"message": "Tracking details synchronized successfully.",
			"events": result.get("events"),
			"has_exception": result.get("has_exception"),
		}
	except frappe.ValidationError as exc:
		return {"status": "error", "message": str(exc)}
	except Exception:
		frappe.log_error(title="Dayton Tracking Pull Failure", message=frappe.get_traceback())
		return {"status": "error", "message": "Connection tracking error while polling Dayton Freight."}


def _is_dayton_shipment(shipment) -> bool:
	carrier = str(shipment.carrier or "").upper()
	if carrier == DAYTON_CARRIER_CODE:
		return True

	carrier_name = str(shipment.carrier_name or "").lower()
	return "dayton" in carrier_name


def sync_all_active_shipments():
	"""Automated cron runner pulling milestones for active Dayton shipments."""
	active_shipments = frappe.get_all(
		"LTL Shipment",
		filters={
			"carrier": "DAYTON",
			"status": ["in", ["Booked", "Dispatched", "In Transit", "Out for Delivery"]],
			"current_status": ["not in", ["Delivered", "Cancelled"]],
		},
		pluck="name",
	)

	for name in active_shipments:
		try:
			fetch_dayton_tracking_updates(name)
		except Exception:
			frappe.log_error(
				title=f"Dayton tracking sync failed: {name}",
				message=frappe.get_traceback(),
			)


@frappe.whitelist()
def fetch_dayton_tracking_by_date(start_date: str, end_date: str, customer_code: str) -> dict:
	"""Proxies Dayton's ByDate endpoint through Frappe Localhost."""
	adapter = DaytonCarrierAdapter()
	endpoint = f"{adapter.base_url}/api/Tracking/ByDate"
	params = {
		"startstring": start_date,
		"endstring": end_date,
		"customerstring": customer_code
	}
	try:
		response = requests.get(endpoint, headers=adapter.get_headers(), params=params, timeout=10)
		return response.json() if response.status_code == 200 else {"status": "error", "code": response.status_code, "text": response.text}
	except Exception as e:
		frappe.throw(f"Frappe Proxy Error: {str(e)}")


@frappe.whitelist()
def fetch_dayton_pending_shipments(customer_code: str) -> dict:
	"""Proxies Dayton's Pending shipments endpoint through Frappe Localhost."""
	adapter = DaytonCarrierAdapter()
	endpoint = f"{adapter.base_url}/api/Tracking/Pending"
	params = {
		"customer": customer_code
	}
	try:
		response = requests.get(endpoint, headers=adapter.get_headers(), params=params, timeout=10)
		return response.json() if response.status_code == 200 else {"status": "error", "code": response.status_code, "text": response.text}
	except Exception as e:
		frappe.throw(f"Frappe Proxy Error: {str(e)}")
