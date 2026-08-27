import base64
import json

import frappe
import requests
from frappe.utils import add_days, cint, flt, get_datetime, now_datetime, today
from frappe.utils.file_manager import save_file

from ltl_quote.carrier_network.accessorials import (
	build_accessorial_items,
	build_dayton_bol_accessorials_section,
	build_dayton_bol_special_instructions,
	dayton_bol_accessorial_codes,
	dayton_rate_accessorials,
)
from ltl_quote.carrier_network.adapters.base import BaseCarrierAdapter, CarrierRateQuote, ShipmentRequest
from ltl_quote.utils.booking import resolve_shipper_context
from ltl_quote.utils.location import resolve_us_location

DEFAULT_BASE_URL = "https://api.daytonfreight.com"
DEFAULT_ACCOUNT_NUMBER = "0055666"
REQUEST_TIMEOUT = 15
DAYTON_SERVICE_ELIGIBILITY_PATH = "/api/Shipping/ServiceEligibility"

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
DAYTON_MAX_AUTO_RATE_LBS = 12000


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

	def get_auth(self) -> tuple[str, str] | None:
		"""HTTP Basic auth tuple for requests (username truncated to 10 chars per Dayton)."""
		if self.username and self.password:
			return (self.username[:10], self.password)
		return None

	def get_headers(self) -> dict:
		"""Compile a standard Base64 Basic Authentication block using web credentials."""
		headers = {"Content-Type": "application/json"}

		auth = self.get_auth()
		if auth:
			clean_username, password = auth
			auth_string = f"{clean_username}:{password}"
			encoded_auth = base64.b64encode(auth_string.encode("utf-8")).decode("utf-8")
			headers["Authorization"] = f"Basic {encoded_auth}"

		return headers

	def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
		"""Maps LTL Quote Request schema -> Dayton Freight Rates API -> CarrierRateQuote."""
		endpoint = f"{self.base_url}/api/Rates"
		service_option = self._resolve_service_option(request.accessorial_codes)
		dayton_accessorials = dayton_rate_accessorials(request.accessorials, self.carrier_doc)

		clean_weight = self._clean_int(request.total_weight)
		clean_class = self._clean_float(request.freight_class, 70)
		clean_pieces = self._clean_int(request.pieces, 1)
		length, width, height = request.first_handling_dimensions()

		if clean_weight > DAYTON_MAX_AUTO_RATE_LBS:
			return self._rate_error(
				"Dayton does not auto-rate shipments over 12,000 lbs. "
				"Contact pricing@daytonfreight.com for a manual quote."
			)

		item = {
			"weight": clean_weight,
			"class": clean_class,
			"pieces": clean_pieces,
			"description": "LTL Quote Freight Line",
		}
		if length > 0 and width > 0 and height > 0:
			item["length"] = length
			item["width"] = width
			item["height"] = height

		dayton_payload = {
			"accessorials": dayton_accessorials,
			"account": self.account_number,
			"destination": str(request.destination_zip),
			"directOnly": False,
			"handlingUnits": [],
			"items": [item],
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
				error_message = self._format_rate_error(response)
				frappe.log_error(f"Dayton API Error: {response.text}", "LTL Quote - Dayton Rate Failure")
				return self._rate_error(error_message)

			data = response.json()
			parsed = self._parse_rate_response(data)
			eligibility = self.get_service_eligibility(
				request.origin_zip,
				request.destination_zip,
				dayton_payload["shipmentDate"],
			)
			raw_response = dict(parsed.get("raw_response") or data)
			if eligibility:
				raw_response["serviceEligibilityLookup"] = eligibility
				if eligibility.get("service_days"):
					parsed["transit_days"] = int(eligibility["service_days"])

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
				raw_response=raw_response,
			)

		except (ValueError, TypeError, KeyError) as e:
			error_details = f"Dayton response parsing error: {e}"
			frappe.log_error(error_details, "LTL Quote - Dayton Parse Failure")
			return self._rate_error(error_details)

		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Connection Error")
			return self._rate_error(f"Dayton connection error: {e}")

	def _rate_error(self, message: str) -> CarrierRateQuote:
		return CarrierRateQuote(
			carrier_code=self.carrier_code,
			carrier_name=self.carrier.carrier_name,
			total_charge=0,
			transit_days=0,
			error=message,
		)

	@staticmethod
	def _format_rate_error(response) -> str:
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = {}
		errors = data.get("errors") if isinstance(data, dict) else None
		if isinstance(errors, list) and errors:
			first = errors[0] if isinstance(errors[0], dict) else {}
			message = str(first.get("message") or "").strip()
			if "12000" in message or "pricing@daytonfreight.com" in message.lower():
				return (
					"Dayton does not auto-rate shipments over 12,000 lbs. "
					"Contact pricing@daytonfreight.com for a manual quote."
				)
			if message:
				return message
		body = (getattr(response, "text", None) or "").strip()
		if body:
			return f"Dayton could not return a rate (HTTP {response.status_code})."
		return f"Dayton API error: HTTP {getattr(response, 'status_code', '')}"

	def generate_bill_of_lading(self, quote_data: dict) -> dict:
		"""Create a Dayton eBOL from platform quote_data or a pre-built dayton_payload."""
		endpoint = (
			f"{self.base_url}/api/BillOfLading/v2/CreateStandardElectronicBillOfLading"
		)
		dayton_ebol_payload = _sanitize_dayton_ebol_integers(
			self._resolve_dayton_ebol_payload(quote_data)
		)

		frappe.log_error(frappe.as_json(dayton_ebol_payload), "DAYTON REQUEST")
		frappe.logger("dayton").info(f"Dayton eBOL payload: {frappe.as_json(dayton_ebol_payload)}")

		try:
			response = requests.post(
				endpoint,
				headers=self.get_headers(),
				auth=self.get_auth(),
				json=dayton_ebol_payload,
				timeout=60,
			)
			frappe.log_error(response.text, "DAYTON RESPONSE")
			frappe.logger("dayton").info(f"DAYTON RAW RESPONSE: {response.text}")
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton eBOL Connection Error")
			frappe.throw(f"Dayton eBOL request failed: {e}")

		if not response.text:
			frappe.throw(f"Empty response received from Dayton API. HTTP Status: {response.status_code}")

		text_stripped = response.text.lstrip().lower()
		if text_stripped.startswith("<!doctype html") or text_stripped.startswith("<html"):
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
			frappe.throw(_format_dayton_ebol_failure(response.status_code, res_data))

		if response.status_code != 200 or res_data.get("errors"):
			frappe.throw(_format_dayton_ebol_failure(response.status_code, res_data))

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
		payload = _sanitize_dayton_ebol_integers(
			self._build_dayton_update_payload(shipment, quote_request)
		)
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
		sync_dayton_bol_details_to_shipment(
			shipment.name,
			payload,
			bol_result={
				"bol_number": shipment.bol_number,
				"pro_number": shipment.pro_number,
				"dayton_bol_id": shipment.dayton_bol_id,
				"carrier_confirmation": shipment.carrier_confirmation,
			},
			bol_file_url=file_doc.file_url,
		)
		return {"success": True, "file_url": file_doc.file_url}

	def _build_dayton_update_payload(self, shipment, quote_request) -> dict:
		"""
		Builds the v2 Update BOL payload with full accessorial structures
		matching the Digital LTL Council standard.
		"""
		shipper = resolve_shipper_context(quote_request=quote_request)
		platform_settings = frappe.get_single("LTL Platform Settings")
		pickup_date = shipment.pickup_date or today()

		accessorial_rows = list(getattr(quote_request, "accessorials", None) or [])
		accessorial_codes = dayton_bol_accessorial_codes(accessorial_rows)
		special_instructions = build_dayton_bol_special_instructions(accessorial_rows)
		notes = frappe.utils.strip_html(getattr(shipment, "notes", None) or "").strip()
		if notes:
			special_instructions = f"{special_instructions} | {notes}" if special_instructions else notes

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

		handling_unit_count = max(1, cint(flt(quote_request.pieces, 0)))
		weight_lbs = _dayton_int_weight(quote_request.total_weight)
		length = _optional_dayton_dimension(getattr(quote_request, "length", None))
		width = _optional_dayton_dimension(getattr(quote_request, "width", None))
		height = _optional_dayton_dimension(getattr(quote_request, "height", None))
		dimensions_unit = str(getattr(quote_request, "dimension_uom", None) or "IN").upper()
		if dimensions_unit not in ("IN", "CM"):
			dimensions_unit = "IN"

		items = _resolve_dayton_items({}, quote_request)
		first = items[0] if items else {}
		handling_units, weight_lbs, handling_unit_count = _build_dayton_handling_units(
			items=items,
			fallback_weight=weight_lbs,
			fallback_class=str(quote_request.freight_class or "70"),
			fallback_pieces=handling_unit_count,
			fallback_length=length,
			fallback_width=width,
			fallback_height=height,
			fallback_dimension_unit=dimensions_unit,
			fallback_description=str(
				first.get("description") or first.get("item_name") or "General Freight Cargo"
			),
			fallback_nmfc=str(first.get("nmfc") or first.get("nmfc_number") or ""),
			fallback_hazardous=bool(first.get("hazardous") or first.get("hazmat")),
			hu_type="SKID",
			include_hu_id=False,
		)
		# Update payload uses a few extra HU aliases Dayton expects on UPDATE.
		for hu in handling_units:
			count = cint(hu.get("count") or 1)
			hu["handlingUnitQuantity"] = count
			hu["handlingUnitType"] = hu.get("type") or "SKID"
			hu["class"] = str(
				((hu.get("lineItems") or [{}])[0] or {}).get("classification")
				or quote_request.freight_class
				or "70"
			)

		origin_contact_name = str(
			getattr(quote_request, "contact_name", None) or shipper.get("contact_name") or "Shipping Desk"
		)
		origin_contact_phone = self._dayton_contact_phone(
			getattr(quote_request, "contact_phone", None),
			shipper.get("contact_phone"),
		)
		origin_contact_email = str(getattr(quote_request, "origin_contact_email", None) or "").strip()
		destination_contact_name = str(
			getattr(quote_request, "destination_contact_name", None)
			or shipper.get("consignee_name")
			or "Receiving Dock"
		)
		destination_contact_phone = self._dayton_contact_phone(
			getattr(quote_request, "destination_contact_phone", None),
		)
		destination_contact_email = str(
			getattr(quote_request, "destination_contact_email", None) or ""
		).strip()

		return {
			"id": cint(shipment.dayton_bol_id),
			"version": "2.0.0",
			"bol": {
				"requestedPickupDate": get_datetime(pickup_date).strftime("%Y-%m-%dT%H:%M:%SZ"),
				"function": "UPDATE",
				"isTest": bool(platform_settings.use_mock_carriers),
				"requestorRole": "SHIPPER",
				"specialInstructions": special_instructions,
			},
			"images": {
				"includeBol": True,
				"includeShippingLabels": True,
				"shippingLabels": {
					"format": "LETTER",
					"quantity": handling_unit_count,
					"position": 1,
				},
			},
			"referenceNumbers": {
				"pro": str(shipment.pro_number or ""),
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
				"netWeight": weight_lbs,
				"handlingUnits": handling_unit_count,
				"weightUnit": "LBS",
			},
			"accessorials": build_dayton_bol_accessorials_section(accessorial_codes),
			"origin": {
				"account": str(self.account_number),
				"locationId": "",
				"name": shipper["shipper_name"],
				"address1": shipper["shipper_address"],
				"address2": "",
				"city": str(origin_city or quote_request.origin_city or "Dayton"),
				"stateProvince": str(origin_state or quote_request.origin_state or "OH"),
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"phone": origin_contact_phone,
					"phoneExt": "",
					"name": origin_contact_name,
					"email": origin_contact_email,
				},
			},
			"destination": {
				"account": "",
				"locationId": "",
				"name": shipper["consignee_name"],
				"address1": shipper["consignee_address"],
				"address2": "",
				"city": str(destination_city or quote_request.destination_city or "Chicago"),
				"stateProvince": str(destination_state or quote_request.destination_state or "IL"),
				"postalCode": destination_zip,
				"country": "USA",
				"contact": {
					"phone": destination_contact_phone,
					"phoneExt": "",
					"name": destination_contact_name,
					"email": destination_contact_email,
				},
			},
			"billTo": {
				"account": str(self.account_number),
				"locationId": "",
				"name": shipper["shipper_name"],
				"address1": shipper["shipper_address"],
				"address2": "",
				"city": str(origin_city or quote_request.origin_city or "Dayton"),
				"stateProvince": str(origin_state or quote_request.origin_state or "OH"),
				"postalCode": origin_zip,
				"country": "USA",
				"contact": {
					"phone": origin_contact_phone,
					"phoneExt": "",
					"name": origin_contact_name,
					"email": origin_contact_email,
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
		origin_country = str(
			quote_data.get("origin_country")
			or getattr(quote_request, "origin_country", None)
			or "USA"
		).upper()
		if origin_country in ("US", "UNITED STATES"):
			origin_country = "USA"

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
		destination_country = str(
			quote_data.get("destination_country")
			or getattr(quote_request, "destination_country", None)
			or "USA"
		).upper()
		if destination_country in ("US", "UNITED STATES"):
			destination_country = "USA"

		origin_contact_name = str(
			quote_data.get("origin_contact_name")
			or shipper.get("contact_name")
			or "Shipping Desk"
		)
		origin_contact_phone = self._dayton_contact_phone(
			quote_data.get("origin_contact_phone"),
			shipper.get("contact_phone"),
		)
		origin_contact_email = str(
			quote_data.get("origin_contact_email")
			or quote_data.get("contact_email")
			or getattr(quote_request, "origin_contact_email", None)
			or ""
		).strip()
		destination_contact_name = str(
			quote_data.get("destination_contact_name")
			or quote_data.get("consignee_contact_name")
			or getattr(quote_request, "destination_contact_name", None)
			or "Receiving Dock"
		)
		destination_contact_phone = self._dayton_contact_phone(
			quote_data.get("destination_contact_phone"),
			quote_data.get("consignee_contact_phone"),
			getattr(quote_request, "destination_contact_phone", None),
		)
		destination_contact_email = str(
			quote_data.get("destination_contact_email")
			or getattr(quote_request, "destination_contact_email", None)
			or ""
		).strip()

		raw_weight = quote_data.get("total_weight") or quote_request.total_weight
		weight_lbs = _dayton_int_weight(raw_weight)
		handling_unit_count = self._resolve_handling_unit_count(quote_data, quote_request)
		freight_class = str(
			quote_data.get("freight_class") or quote_request.freight_class or "70"
		)

		# Only send HU dimensions when the shipper provided them. Defaulting to
		# 48x40x48 makes Dayton print "HU Dims: ..." under the commodity description.
		length = _optional_dayton_dimension(
			quote_data.get("length") if quote_data.get("length") is not None else getattr(quote_request, "length", None)
		)
		width = _optional_dayton_dimension(
			quote_data.get("width") if quote_data.get("width") is not None else getattr(quote_request, "width", None)
		)
		height = _optional_dayton_dimension(
			quote_data.get("height") if quote_data.get("height") is not None else getattr(quote_request, "height", None)
		)
		dimensions_unit = str(
			quote_data.get("dimension_uom")
			or getattr(quote_request, "dimension_uom", None)
			or "IN"
		).upper()
		if dimensions_unit not in ("IN", "CM"):
			dimensions_unit = "IN"

		items = _resolve_dayton_items(quote_data, quote_request)
		handling_units, weight_lbs, handling_unit_count = _build_dayton_handling_units(
			items=items,
			fallback_weight=weight_lbs,
			fallback_class=freight_class,
			fallback_pieces=handling_unit_count,
			fallback_length=length,
			fallback_width=width,
			fallback_height=height,
			fallback_dimension_unit=dimensions_unit,
			fallback_description=str(
				quote_data.get("commodity_description") or "General Freight Cargo"
			),
			fallback_nmfc=str(quote_data.get("nmfc") or ""),
			fallback_hazardous=bool(quote_data.get("is_hazardous", False)),
			hu_type="PALLET",
			include_hu_id=True,
		)

		pickup_date = quote_data.get("pickup_date") or getattr(quote_request, "pickup_date", None)
		if pickup_date:
			requested_pickup = get_datetime(pickup_date).strftime("%Y-%m-%dT%H:%M:%SZ")
		else:
			requested_pickup = now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ")

		# Prefer accessorials saved on the quote request (with pickup/delivery/load group).
		quote_accessorials = list(getattr(quote_request, "accessorials", None) or [])
		payload_accessorials = quote_data.get("accessorials") or quote_data.get("accessorial_rows") or []
		accessorial_rows = quote_accessorials or payload_accessorials
		if not accessorial_rows and quote_data.get("accessorial_codes"):
			accessorial_rows = [{"accessorial_code": code} for code in quote_data.get("accessorial_codes") or []]
		accessorial_codes = dayton_bol_accessorial_codes(accessorial_rows)
		special_instructions = quote_data.get("special_instructions") or build_dayton_bol_special_instructions(
			accessorial_rows
		)

		return {
			"version": "2.0.0",
			"bol": {
				"requestedPickupDate": requested_pickup,
				"function": "CREATE",
				"isTest": bool(quote_data.get("is_test", False)),
				"requestorRole": "SHIPPER",
				"specialInstructions": special_instructions,
			},
			"images": {
				"includeBol": True,
				"includeShippingLabels": True,
				"shippingLabels": {
					"format": "LETTER",
					"position": 1,
					"quantity": handling_unit_count,
				},
			},
			"payment": {
				"terms": "Prepaid",
			},
			"referenceNumbers": {},
			"accessorials": build_dayton_bol_accessorials_section(accessorial_codes),
			"commodities": {
				"handlingUnits": handling_units,
				"lineItemLayout": "STACKED",
			},
			"shipmentTotals": {
				"cube": 0,
				"cubeDimensionsUnit": "FT",
				"currency": "USD",
				"declaredValue": 0,
				"dimensionsUnit": "IN",
				"grossWeight": weight_lbs,
				"handlingUnits": handling_unit_count,
				"linearLength": 0,
				"netWeight": weight_lbs,
				"weightUnit": "LBS",
			},
			"origin": {
				"account": self.account_number,
				"locationId": "",
				"name": origin_name,
				"address1": origin_address1,
				"address2": "",
				"city": origin_city,
				"stateProvince": origin_state,
				"postalCode": origin_zip,
				"country": origin_country,
				"contact": {
					"phone": origin_contact_phone,
					"phoneExt": "",
					"name": origin_contact_name,
					"email": origin_contact_email,
				},
			},
			"destination": {
				"account": "",
				"locationId": "",
				"name": destination_name,
				"address1": destination_address1,
				"address2": "",
				"city": destination_city,
				"stateProvince": destination_state,
				"postalCode": destination_zip,
				"country": destination_country,
				"contact": {
					"phone": destination_contact_phone,
					"phoneExt": "",
					"name": destination_contact_name,
					"email": destination_contact_email,
				},
			},
			"billTo": {
				"account": self.account_number,
				"locationId": "",
				"name": origin_name,
				"address1": origin_address1,
				"address2": "",
				"city": origin_city,
				"stateProvince": origin_state,
				"postalCode": origin_zip,
				"country": origin_country,
				"contact": {
					"phone": origin_contact_phone,
					"phoneExt": "",
					"name": origin_contact_name,
					"email": origin_contact_email,
				},
			},
		}

	def book_shipment(self, quote_data: dict) -> dict:
		"""Book shipment via Dayton POST /api/BillOfLading/v2/CreateStandardElectronicBillOfLading."""
		return self.generate_bill_of_lading(quote_data)

	def request_pickup(self, quote_data: dict) -> dict:
		"""Backward-compatible alias — prefer ``create_pickup`` on a shipment doc."""
		if isinstance(quote_data, str):
			return self.create_pickup(frappe.get_doc("LTL Shipment", quote_data))
		if quote_data.get("shipment_name"):
			return self.create_pickup(frappe.get_doc("LTL Shipment", quote_data["shipment_name"]))
		shipment_name = quote_data.get("shipment") or quote_data.get("name")
		if shipment_name and frappe.db.exists("LTL Shipment", shipment_name):
			return self.create_pickup(frappe.get_doc("LTL Shipment", shipment_name))
		frappe.throw("Pickup scheduling requires a booked LTL Shipment.")

	def create_pickup(self, shipment) -> dict:
		"""Schedule a Dayton pickup via PUT /api/Pickup after eBOL booking."""
		from ltl_quote.carrier_network.pickup import (
			apply_pickup_response_to_shipment,
			build_pickup_payload_from_shipment,
			normalize_pickup_response,
		)

		if isinstance(shipment, str):
			shipment = frappe.get_doc("LTL Shipment", shipment)

		if shipment.pickup_number:
			frappe.throw(f"Pickup {shipment.pickup_number} is already scheduled for this shipment.")

		payload = build_pickup_payload_from_shipment(shipment, self)
		endpoint = f"{self.base_url}/api/Pickup"

		try:
			response = requests.put(
				endpoint,
				headers=self.get_headers(),
				json=payload,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup Connection Error")
			frappe.throw(f"Dayton pickup request failed: {e}")

		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Pickup Failure")
			frappe.throw(f"Dayton pickup request failed: {response.text}")

		normalized = normalize_pickup_response(response.json() or {})
		normalized["status"] = "acknowledged"
		apply_pickup_response_to_shipment(shipment, normalized, save=True)
		return normalized

	def get_pickup(self, pickup_number: str) -> dict:
		"""Fetch a Dayton pickup via GET /api/Pickup?number=."""
		from ltl_quote.carrier_network.pickup import normalize_pickup_response

		number = str(pickup_number or "").strip()
		if not number:
			frappe.throw("A pickup number is required.")

		endpoint = f"{self.base_url}/api/Pickup"
		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				params={"number": number},
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup GET Error")
			return {"ok": False, "message": str(e), "raw": {}}

		if response.status_code == 404:
			return {"ok": False, "message": "Pickup not found.", "raw": {"code": 404}}
		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Pickup GET Failure")
			return {"ok": False, "message": response.text, "raw": {"code": response.status_code, "text": response.text}}

		data = normalize_pickup_response(response.json() or {})
		data["ok"] = True
		return data

	def update_pickup(self, pickup_number: str, payload: dict) -> dict:
		"""Update a Dayton pickup via POST /api/Pickup."""
		from ltl_quote.carrier_network.pickup import normalize_pickup_response

		number = str(pickup_number or "").strip()
		if not number:
			frappe.throw("A pickup number is required.")

		body = dict(payload or {})
		body["pickupNumber"] = number
		endpoint = f"{self.base_url}/api/Pickup"

		try:
			response = requests.post(
				endpoint,
				headers=self.get_headers(),
				json=body,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup Update Error")
			frappe.throw(f"Dayton pickup update failed: {e}")

		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Pickup Update Failure")
			frappe.throw(f"Dayton pickup update failed: {response.text}")

		data = normalize_pickup_response(response.json() or {})
		data["ok"] = True
		return data

	def update_pickup_by_psid(self, psid: int, payload: dict) -> dict:
		"""Update a pickup line via POST /api/Pickup/ByPSID."""
		from ltl_quote.carrier_network.pickup import normalize_pickup_response

		if not psid:
			frappe.throw("A pickup shipment ID (PSID) is required.")

		body = dict(payload or {})
		body["psid"] = cint(psid)
		endpoint = f"{self.base_url}/api/Pickup/ByPSID"

		try:
			response = requests.post(
				endpoint,
				headers=self.get_headers(),
				json=body,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup ByPSID Error")
			frappe.throw(f"Dayton pickup update failed: {e}")

		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Pickup ByPSID Failure")
			frappe.throw(f"Dayton pickup update failed: {response.text}")

		data = normalize_pickup_response(response.json() or {})
		data["ok"] = True
		return data

	def cancel_pickup(self, number: str) -> dict:
		"""Cancel a Dayton pickup via DELETE /api/Pickup/Cancel?number=."""
		target = str(number or "").strip()
		if not target:
			return {"success": False, "message": "No pickup number or PSID available to cancel."}

		endpoint = f"{self.base_url}/api/Pickup/Cancel?number={target}"
		try:
			response = requests.delete(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Pickup Cancel Error")
			return {"success": False, "message": str(e)}

		if response.status_code == 200:
			return {"success": True, "message": "Pickup cancelled successfully."}

		frappe.log_error(response.text, "LTL Quote - Dayton Pickup Cancel Failure")
		return {"success": False, "message": response.text, "code": response.status_code}

	def dispatch_shipment(self, shipment_data: dict) -> dict:
		"""Schedule or re-sync a Dayton pickup for a booked shipment."""
		from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment

		shipment_name = shipment_data.get("shipment_name")
		if not shipment_name:
			frappe.throw("shipment_name is required to dispatch a Dayton pickup.")

		shipment = frappe.get_doc("LTL Shipment", shipment_name)
		if shipment.pickup_number:
			result = self.get_pickup(shipment.pickup_number)
			if result.get("ok"):
				apply_pickup_response_to_shipment(shipment, result, save=True)
				return {"status": "acknowledged", **result}
			return {"status": "error", "message": result.get("message") or "Could not sync pickup.", **result}

		result = self.create_pickup(shipment)
		return {"status": "acknowledged", **result}

	def get_tracking(self, pro_number: str) -> list[dict]:
		"""Poll Dayton tracking endpoints for live milestone event logs."""
		events = self._fetch_tracking_by_number(pro_number)
		if events:
			return events

		events = self._fetch_tracking_history(pro_number)
		if events:
			return events

		endpoint = f"{self.base_url}/api/Tracking/{pro_number}"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking Connection Error")
			return []

		# Legacy path is a last-resort fallback — never hard-fail the tracking page.
		if response.status_code in (401, 403):
			return []

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

	def _fetch_tracking_history(self, pro_number: str) -> list[dict]:
		"""Query Dayton GET /api/Tracking/History for PRO event history."""
		endpoint = f"{self.base_url}/api/Tracking/History"
		params = {"number": pro_number}

		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				params=params,
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking History Connection Error")
			return []

		# History is optional; empty/unauthorized responses are normal for some accounts.
		if response.status_code in (401, 403):
			return []

		if response.status_code != 200:
			return []

		data = response.json()
		results = data.get("results") if isinstance(data, dict) else data
		if not isinstance(results, list):
			return []

		return [self._parse_dayton_tracking_event(event) for event in results if isinstance(event, dict)]

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

		events: list[dict] = []
		for row in results:
			if not isinstance(row, dict):
				continue
			# Some responses nest a history list under each tracking result.
			nested = row.get("events") or row.get("history") or row.get("statuses")
			if isinstance(nested, list) and nested:
				for item in nested:
					events.append(self._parse_dayton_tracking_event(item if isinstance(item, dict) else row))
				continue
			events.append(self._parse_dayton_tracking_event(row))
		return events

	@staticmethod
	def _parse_dayton_tracking_event(event: dict) -> dict:
		from ltl_quote.carrier_network.tracking import activity_label, is_exception_code, normalize_activity_code

		if not isinstance(event, dict):
			event = {}

		status_block = event.get("status") if isinstance(event.get("status"), dict) else {}
		city = str(event.get("city") or status_block.get("city") or "").strip()
		state = str(event.get("state") or status_block.get("state") or "").strip()
		location = event.get("location") or status_block.get("location")
		if not location and (city or state):
			location = ", ".join(part for part in (city, state) if part)

		raw_code = (
			status_block.get("activityCode")
			or event.get("activityCode")
			or event.get("statusCode")
			or event.get("status_code")
			or ""
		)
		status_code = normalize_activity_code(raw_code) or "IN_TRANSIT"

		description = (
			status_block.get("activity")
			or status_block.get("description")
			or event.get("description")
			or event.get("status_description")
			or event.get("remarks")
			or event.get("comment")
			or (event.get("status") if isinstance(event.get("status"), str) else None)
			or activity_label(status_code)
		)

		event_datetime = (
			status_block.get("time")
			or event.get("eventTime")
			or event.get("dateTime")
			or event.get("date")
			or event.get("pickupDate")
			or event.get("deliveryDate")
			or event.get("event_datetime")
		)

		return {
			"event_datetime": event_datetime,
			"status_code": status_code,
			"status_description": description,
			"location": location or "Terminal Center",
			"is_exception": 1 if event.get("isException") or is_exception_code(status_code) else 0,
		}

	def get_proof_of_delivery(self, pro_number: str) -> dict:
		"""Fetch signed POD document from Dayton GET /api/Documents/{proNumber}?type=POD.

		Verifies the document is indexed via /api/Images/Search before requesting the
		heavy binary, so we don't blindly poll for shipments that aren't scanned yet.
		"""
		gate = self._verify_indexed(pro_number, "PROOF OF DELIVERY")
		if gate.get("blocked"):
			return {"pod_available": False, "message": "Proof of delivery document is not available yet."}

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
		"""Fetch BOL document from Dayton GET /api/Documents/{proNumber}?type=BOL.

		Verifies the BOL is indexed via /api/Images/Search first (fail-open) so we
		skip the heavy binary request when Dayton has not scanned it yet.
		"""
		gate = self._verify_indexed(pro_number, "BILL OF LADING")
		if gate.get("blocked"):
			return {"bol_available": False, "status": "info", "message": DAYTON_BOL_NOT_READY_MESSAGE}

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
					return {
						"bol_available": True,
						"document_base64": document_base64,
						"document_hash": (gate.get("document") or {}).get("hash"),
					}
		except Exception as e:
			frappe.log_error(
				f"Failed to fetch BOL document for PRO {pro_number}: {e}",
				"Dayton BOL Image Error",
			)

		return {"bol_available": False, "status": "info", "message": DAYTON_BOL_NOT_READY_MESSAGE}

	def search_images(self, pro_number: str) -> dict:
		"""Query Dayton GET /api/Images/Search to list the documents (BOL, POD, ...)
		that have been indexed on their servers for a PRO.

		Returns ``{"ok": bool, "documents": [...], "raw": {...}}``. ``ok`` is False
		when the call could not be completed (network error / non-200) so callers can
		fail open instead of assuming "no documents".
		"""
		endpoint = f"{self.base_url}/api/Images/Search"
		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				params={"pro": str(pro_number or "").strip()},
				timeout=REQUEST_TIMEOUT,
			)
			if response.status_code == 200:
				data = response.json() or {}
				return {"ok": True, "documents": data.get("documents") or [], "raw": data}
			return {"ok": False, "documents": [], "raw": {"code": response.status_code, "text": response.text}}
		except Exception:
			frappe.log_error(title="Dayton Images Search Failure", message=frappe.get_traceback())
			return {"ok": False, "documents": [], "raw": {}}

	def _verify_indexed(self, pro_number: str, doc_type: str) -> dict:
		"""Preventative gate consulted before downloading heavy document binaries.

		Fails OPEN: it only reports ``blocked`` when the Images/Search call succeeds
		and explicitly does not list ``doc_type``. Any inconclusive result (network
		error, non-200) leaves ``blocked`` False so existing download behaviour holds.
		"""
		search = self.search_images(pro_number)
		if not search.get("ok"):
			return {"blocked": False, "checked": False}
		document = _find_indexed_dayton_document(search, doc_type)
		return {
			"blocked": document is None,
			"checked": True,
			"document": document,
			"documents": search.get("documents"),
		}

	def cancel_shipment(self, shipment_doc) -> bool:
		"""Cancel a booked pickup via Dayton DELETE /api/Pickup/Cancel."""
		from ltl_quote.carrier_network.pickup import resolve_pickup_cancel_number

		target_number = resolve_pickup_cancel_number(shipment_doc)
		result = self.cancel_pickup(target_number)
		if result.get("success"):
			shipment_doc.pickup_status = "Cancelled"
			shipment_doc.dispatch_status = "Failed"
			shipment_doc.save(ignore_permissions=True)
		return bool(result.get("success"))

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

	def get_service_eligibility(
		self,
		origin_zip: str,
		destination_zip: str,
		shipment_date: str | None = None,
	) -> dict:
		"""GET /api/Shipping/ServiceEligibility for lane transit + service centers.

		Soft-fails (returns {}) on network/HTTP errors so rating is not blocked.
		"""
		origin = str(origin_zip or "").strip()
		destination = str(destination_zip or "").strip()
		if not origin or not destination:
			return {}

		endpoint = f"{self.base_url}{DAYTON_SERVICE_ELIGIBILITY_PATH}"
		params = {
			"origin": origin,
			"destination": destination,
			"date": self._format_eligibility_date(shipment_date),
		}

		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				params=params,
				auth=self.get_auth(),
				timeout=REQUEST_TIMEOUT,
			)
			if response.status_code != 200:
				frappe.log_error(
					f"Status {response.status_code}: {response.text}",
					"Dayton Service Eligibility Failure",
				)
				return {}
			data = response.json() if response.content else {}
			return self._normalize_service_eligibility_response(data)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "Dayton Service Eligibility Connection Error")
			return {}
		except (ValueError, TypeError) as e:
			frappe.log_error(str(e), "Dayton Service Eligibility Parse Error")
			return {}

	def get_service_centers(self) -> list[dict]:
		"""GET /api/ServiceCenters — full terminal catalog with lat/lng.

		Soft-fails (returns []) on network/HTTP/auth errors so sync/UI are not blocked.
		"""
		endpoint = f"{self.base_url}/api/ServiceCenters"
		try:
			response = requests.get(
				endpoint,
				headers=self.get_headers(),
				auth=self.get_auth(),
				timeout=REQUEST_TIMEOUT,
			)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Service Centers Connection Error")
			return []

		if response.status_code in (401, 403):
			frappe.log_error(
				f"Status {response.status_code}: {response.text}",
				"Dayton Service Centers Auth Failure",
			)
			return []

		if response.status_code != 200:
			frappe.log_error(
				f"Status {response.status_code}: {response.text}",
				"Dayton Service Centers Failure",
			)
			return []

		try:
			data = response.json() if response.content else {}
		except (ValueError, TypeError):
			frappe.log_error(response.text, "Dayton Service Centers Parse Error")
			return []

		raw_list = data.get("serviceCenters") if isinstance(data, dict) else data
		if not isinstance(raw_list, list):
			return []

		centers: list[dict] = []
		for row in raw_list:
			normalized = self._normalize_service_center_catalog_row(row)
			if normalized.get("id"):
				centers.append(normalized)
		return centers

	@staticmethod
	def _normalize_service_center_catalog_row(row: dict | None) -> dict:
		"""Normalize a ServiceCenters API row for DocType sync and lookups."""
		if not row or not isinstance(row, dict):
			return {}

		center_id = str(row.get("id") or "").strip().upper()
		lat = row.get("latitude") if row.get("latitude") is not None else row.get("lat")
		lng = row.get("longitude") if row.get("longitude") is not None else row.get("lng")
		try:
			lat_f = float(lat) if lat not in (None, "") else None
		except (TypeError, ValueError):
			lat_f = None
		try:
			lng_f = float(lng) if lng not in (None, "") else None
		except (TypeError, ValueError):
			lng_f = None

		number = row.get("number")
		try:
			number_i = int(number) if number not in (None, "") else None
		except (TypeError, ValueError):
			number_i = None

		out = {
			"id": center_id,
			"number": number_i,
			"name": str(row.get("name") or "").strip(),
			"address1": str(row.get("address1") or "").strip(),
			"address2": str(row.get("address2") or "").strip(),
			"city": str(row.get("city") or "").strip(),
			"state": str(row.get("state") or "").strip().upper(),
			"zip": str(row.get("zip") or "").strip(),
			"phone": str(row.get("phone") or "").strip(),
			"toll_free": str(row.get("tollFree") or row.get("toll_free") or "").strip(),
			"fax": str(row.get("fax") or "").strip(),
		}
		if lat_f is not None:
			out["lat"] = lat_f
		if lng_f is not None:
			out["lng"] = lng_f
		return out

	@staticmethod
	def _format_eligibility_date(shipment_date: str | None = None) -> str:
		"""Format shipment date for ServiceEligibility query (ISO with Z suffix)."""
		if shipment_date:
			dt = get_datetime(shipment_date)
			return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
		return now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ")

	@staticmethod
	def _normalize_service_center(center: dict | None) -> dict:
		if not center or not isinstance(center, dict):
			return {}
		normalized = {
			"id": str(center.get("id") or "").strip(),
			"name": str(center.get("name") or "").strip(),
			"city": str(center.get("city") or "").strip(),
			"state": str(center.get("state") or "").strip(),
			"zip": str(center.get("zip") or "").strip(),
			"phone": str(center.get("phone") or center.get("tollFree") or "").strip(),
			"address1": str(center.get("address1") or "").strip(),
		}
		try:
			from ltl_quote.carrier_network.service_centers import lookup_service_center

			matched = lookup_service_center(
				id=normalized.get("id"),
				city=normalized.get("city"),
				state=normalized.get("state"),
				zip_code=normalized.get("zip"),
			)
			if matched.get("lat") is not None and matched.get("lng") is not None:
				normalized["lat"] = matched["lat"]
				normalized["lng"] = matched["lng"]
			if not normalized.get("name") and matched.get("name"):
				normalized["name"] = matched["name"]
			if not normalized.get("address1") and matched.get("address1"):
				normalized["address1"] = matched["address1"]
			if not normalized.get("phone") and matched.get("phone"):
				normalized["phone"] = matched["phone"]
		except Exception:
			# Catalog may not be migrated/synced yet — eligibility still returns text fields.
			pass
		return normalized

	@staticmethod
	def _normalize_service_eligibility_response(data: dict | None) -> dict:
		"""Normalize Dayton ServiceEligibility JSON for UI and FlowWolf consumers."""
		if not data or not isinstance(data, dict):
			return {}

		nested = data.get("serviceEligibility") or {}
		service_days = nested.get("serviceDays") or data.get("serviceDays")
		try:
			service_days = int(service_days) if service_days is not None else None
		except (TypeError, ValueError):
			service_days = None

		return {
			"service_days": service_days,
			"origin_city": str(data.get("originCity") or data.get("origin_city") or "").strip(),
			"origin_state": str(data.get("originState") or data.get("origin_state") or "").strip(),
			"origin_zip": str(data.get("originZip") or data.get("origin_zip") or "").strip(),
			"destination_city": str(
				data.get("destinationCity") or data.get("destination_city") or ""
			).strip(),
			"destination_state": str(
				data.get("destinationState") or data.get("destination_state") or ""
			).strip(),
			"destination_zip": str(
				data.get("destinationZip") or data.get("destination_zip") or ""
			).strip(),
			"origin_service_center": DaytonCarrierAdapter._normalize_service_center(
				data.get("originServiceCenter")
			),
			"destination_service_center": DaytonCarrierAdapter._normalize_service_center(
				data.get("destinationServiceCenter")
			),
			"raw": data,
		}

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


DAYTON_DEFAULT_BOL_EMAIL = "admin@ltlquote.com"


def _format_dayton_ebol_failure(status_code, res_data: dict) -> str:
	"""Surface Dayton messageStatus.message/resolution instead of dumping the full JSON blob."""
	message_status = (res_data or {}).get("messageStatus") or {}
	message = str(message_status.get("message") or "").strip()
	resolution = str(message_status.get("resolution") or "").strip()
	if message and resolution:
		return f"Dayton eBOL Creation Failed ({status_code}): {message} — {resolution}"
	if message:
		return f"Dayton eBOL Creation Failed ({status_code}): {message}"
	return f"Dayton eBOL Creation Failed ({status_code}): {frappe.as_json(res_data)}"


def _is_valid_dayton_email(value) -> bool:
	email = str(value or "").strip()
	if not email or "@" not in email:
		return False
	local, _, domain = email.partition("@")
	return bool(local and domain and "." in domain)


def _resolve_dayton_bol_email_addresses(quote_data: dict | None = None) -> list[str]:
	"""Return at least one email for Dayton images.email.addresses (required when emailing BOL/labels)."""
	quote_data = quote_data or {}
	candidates: list[str] = []

	for key in (
		"origin_contact_email",
		"contact_email",
		"bol_email",
		"destination_contact_email",
	):
		value = quote_data.get(key)
		if value:
			candidates.append(str(value).strip())

	try:
		settings = frappe.get_single("LTL Platform Settings")
		settings_email = getattr(settings, "default_contact_email", None)
		if settings_email:
			candidates.append(str(settings_email).strip())
	except Exception:
		pass

	session_user = getattr(frappe.session, "user", None)
	if session_user and session_user not in ("Guest", "Administrator"):
		user_email = frappe.db.get_value("User", session_user, "email")
		if user_email:
			candidates.append(str(user_email).strip())
	elif session_user == "Administrator":
		admin_email = frappe.db.get_value("User", "Administrator", "email")
		if admin_email and _is_valid_dayton_email(admin_email):
			candidates.append(str(admin_email).strip())

	addresses: list[str] = []
	seen: set[str] = set()
	for email in candidates:
		normalized = email.lower()
		if not _is_valid_dayton_email(email) or normalized in seen:
			continue
		seen.add(normalized)
		addresses.append(email)

	if not addresses:
		addresses.append(DAYTON_DEFAULT_BOL_EMAIL)

	return addresses


def _dayton_int_weight(value) -> int:
	"""Dayton eBOL expects weight/count fields as integers (rejects values like 1000.0)."""
	return int(float(value or 0))


def _item_as_dict(item) -> dict:
	"""Normalize a booking item dict or quote-request child row into a plain dict."""
	if isinstance(item, dict):
		return item
	return {
		"description": getattr(item, "description", None) or getattr(item, "item_name", None) or "",
		"item_name": getattr(item, "item_name", None) or "",
		"item_number": getattr(item, "item_number", None) or "",
		"freight_class": getattr(item, "freight_class", None) or "",
		"classification": getattr(item, "freight_class", None) or "",
		"nmfc": getattr(item, "nmfc", None) or "",
		"nmfc_number": getattr(item, "nmfc", None) or "",
		"quantity": getattr(item, "quantity", None) or 1,
		"qty": getattr(item, "quantity", None) or 1,
		"weight": getattr(item, "weight", None),
		"weight_unit": getattr(item, "weight_unit", None) or "LBS",
		"length": getattr(item, "length", None),
		"width": getattr(item, "width", None),
		"height": getattr(item, "height", None),
		"dimension_unit": getattr(item, "dimension_unit", None) or "IN",
		"packaging_units": getattr(item, "packaging_units", None) or "",
		"hazmat": getattr(item, "hazmat", None),
		"hazardous": bool(getattr(item, "hazmat", None)),
	}


def _resolve_dayton_items(quote_data: dict | None = None, quote_request=None) -> list[dict]:
	"""Prefer booking payload items; fall back to quote request line_items."""
	quote_data = quote_data or {}
	raw_items = quote_data.get("items")
	if not raw_items and quote_request is not None:
		raw_items = getattr(quote_request, "line_items", None) or []
	return [_item_as_dict(item) for item in (raw_items or []) if item]


def _resolve_dayton_packaging_type(value) -> str:
	"""Resolve a packaging code for Dayton eBOL lineItems.packagingType.

	Prefers synced Dayton Packaging Type ids (e.g. PL, BX). Falls back to the
	legacy word allowlist, then SKID / first synced type.
	"""
	raw = str(value or "").strip()
	legacy = {"SKID", "PALLET", "CARTON", "CRATE", "DRUM", "TOTE", "BUNDLE", "ROLL", "OTHER"}

	if raw:
		# Exact DocType match (Link stores name == id).
		if frappe.db.exists("Dayton Packaging Type", raw):
			return raw
		upper = raw.upper()
		# Match by id case-insensitively.
		matched = frappe.db.get_value("Dayton Packaging Type", {"id": upper}, "name")
		if matched:
			return matched
		# Match description loosely (e.g. free-text "Pallets").
		matched = frappe.db.get_value("Dayton Packaging Type", {"description": raw}, "name")
		if matched:
			return matched
		if upper in legacy:
			return upper

	# Default: first synced packaging type, else SKID.
	first = frappe.db.get_value("Dayton Packaging Type", {}, "name", order_by="id asc")
	return first or "SKID"


def _build_dayton_handling_units(
	*,
	items: list[dict],
	fallback_weight: float | int,
	fallback_class: str,
	fallback_pieces: int,
	fallback_length=None,
	fallback_width=None,
	fallback_height=None,
	fallback_dimension_unit: str = "IN",
	fallback_description: str = "General Freight Cargo",
	fallback_nmfc: str = "",
	fallback_hazardous: bool = False,
	hu_type: str = "PALLET",
	include_hu_id: bool = True,
) -> tuple[list[dict], int, int]:
	"""Build Dayton handlingUnits from line items (one HU per item).

	Returns (handling_units, total_weight_lbs, total_pieces).
	"""
	fallback_pieces = max(1, cint(fallback_pieces or 1))
	fallback_weight_lbs = _dayton_int_weight(fallback_weight)
	fallback_class = str(fallback_class or "70")
	fallback_dimension_unit = str(fallback_dimension_unit or "IN").upper()
	if fallback_dimension_unit not in ("IN", "CM"):
		fallback_dimension_unit = "IN"

	if not items:
		hu_id = "1"
		hu = {
			"count": fallback_pieces,
			"type": hu_type,
			"weight": fallback_weight_lbs,
			"weightUnit": "LBS",
			"tareWeight": 0,
			"stackable": False,
			"lineItems": [
				{
					"handlingUnitId": hu_id,
					"classification": fallback_class,
					"description": str(fallback_description or "General Freight Cargo"),
					"hazardous": bool(fallback_hazardous),
					"nmfc": str(fallback_nmfc or ""),
					"packagingType": _resolve_dayton_packaging_type(None),
					"pieces": fallback_pieces,
					"weight": fallback_weight_lbs,
					"weightUnit": "LBS",
				}
			],
		}
		if include_hu_id:
			hu["id"] = hu_id
		length = _optional_dayton_dimension(fallback_length)
		width = _optional_dayton_dimension(fallback_width)
		height = _optional_dayton_dimension(fallback_height)
		if length and width and height:
			hu["dimensionsUnit"] = fallback_dimension_unit
			hu["length"] = length
			hu["width"] = width
			hu["height"] = height
		return [hu], fallback_weight_lbs, fallback_pieces

	handling_units = []
	total_weight = 0
	total_pieces = 0
	for idx, item in enumerate(items, start=1):
		hu_id = str(idx)
		pieces = max(1, cint(item.get("quantity") if item.get("quantity") not in (None, "") else item.get("qty") or 1))
		item_weight = item.get("weight")
		if item_weight in (None, ""):
			# Spread fallback weight across lines when rows omit weight.
			item_weight = fallback_weight_lbs / max(len(items), 1)
		weight_lbs = _dayton_int_weight(item_weight)
		if weight_lbs <= 0:
			weight_lbs = max(1, fallback_weight_lbs // max(len(items), 1))

		freight_class = str(
			item.get("classification")
			or item.get("freight_class")
			or item.get("nmfc_class")
			or fallback_class
		)
		description = str(
			item.get("description")
			or item.get("commodity_description")
			or item.get("item_name")
			or fallback_description
			or "General Freight Cargo"
		)
		nmfc = str(item.get("nmfc") or item.get("nmfc_number") or fallback_nmfc or "")
		hazardous = bool(
			item.get("hazardous")
			or item.get("hazmat") in (True, 1, "1", "true", "True", "yes", "Y")
			or fallback_hazardous
		)
		packaging = _resolve_dayton_packaging_type(
			item.get("packaging_units") or item.get("packaging_type") or item.get("packagingType")
		)
		dim_unit = str(item.get("dimension_unit") or item.get("dimension_units") or fallback_dimension_unit).upper()
		if dim_unit not in ("IN", "CM"):
			dim_unit = fallback_dimension_unit

		length = _optional_dayton_dimension(item.get("length") if item.get("length") not in (None, "") else fallback_length)
		width = _optional_dayton_dimension(item.get("width") if item.get("width") not in (None, "") else fallback_width)
		height = _optional_dayton_dimension(item.get("height") if item.get("height") not in (None, "") else fallback_height)

		hu = {
			"count": pieces,
			"type": hu_type,
			"weight": weight_lbs,
			"weightUnit": "LBS",
			"tareWeight": 0,
			"stackable": False,
			"lineItems": [
				{
					"handlingUnitId": hu_id,
					"classification": freight_class,
					"description": description,
					"hazardous": hazardous,
					"nmfc": nmfc,
					"packagingType": packaging,
					"pieces": pieces,
					"weight": weight_lbs,
					"weightUnit": "LBS",
				}
			],
		}
		if include_hu_id:
			hu["id"] = hu_id
		if length and width and height:
			hu["dimensionsUnit"] = dim_unit
			hu["length"] = length
			hu["width"] = width
			hu["height"] = height

		handling_units.append(hu)
		total_weight += weight_lbs
		total_pieces += pieces

	return handling_units, max(1, total_weight), max(1, total_pieces)


def _dayton_int_dimension(value, default: int = 48) -> int:
	"""Dayton requires handling-unit length/width/height as integers in 1..999."""
	try:
		dim = int(float(value)) if value not in (None, "") else 0
	except (TypeError, ValueError):
		dim = 0
	if dim <= 0:
		dim = int(default)
	return max(1, min(999, dim))


def _optional_dayton_dimension(value) -> int | None:
	"""Return a clamped dimension only when the shipper provided a positive value."""
	try:
		dim = int(float(value)) if value not in (None, "") else 0
	except (TypeError, ValueError):
		dim = 0
	if dim <= 0:
		return None
	return max(1, min(999, dim))


def _sanitize_dayton_ebol_integers(payload: dict) -> dict:
	"""Force Dayton integer fields before API POST (weights/counts reject floats like 1000.0)."""
	if not isinstance(payload, dict):
		return payload

	payload = dict(payload)
	totals = dict(payload.get("shipmentTotals") or {})
	for key in ("netWeight", "grossWeight", "handlingUnits", "declaredValue", "cube", "linearLength"):
		if key in totals and totals[key] is not None:
			totals[key] = _dayton_int_weight(totals[key])
	if totals:
		payload["shipmentTotals"] = totals

	commodities = dict(payload.get("commodities") or {})
	handling_units = []
	for hu in commodities.get("handlingUnits") or []:
		if not isinstance(hu, dict):
			handling_units.append(hu)
			continue

		row = dict(hu)
		for key in ("weight", "tareWeight", "count", "handlingUnitQuantity"):
			if key in row and row[key] is not None:
				row[key] = _dayton_int_weight(row[key])

		# Only coerce dimensions that were explicitly provided — do not invent 48x40x48
		# defaults (Dayton prints those as "HU Dims" under the commodity description).
		has_explicit_dims = all(
			row.get(key) not in (None, "", 0, "0") for key in ("length", "width", "height")
		)
		if has_explicit_dims:
			row["length"] = _dayton_int_dimension(row.get("length"), 48)
			row["width"] = _dayton_int_dimension(row.get("width"), 40)
			row["height"] = _dayton_int_dimension(row.get("height"), 48)
			row.setdefault("dimensionsUnit", "IN")
		else:
			for key in ("length", "width", "height", "dimensionsUnit"):
				row.pop(key, None)

		line_items = []
		for line in row.get("lineItems") or []:
			if not isinstance(line, dict):
				line_items.append(line)
				continue

			item = dict(line)
			for key in ("weight", "pieces"):
				if key in item and item[key] is not None:
					item[key] = _dayton_int_weight(item[key])

			hazmat = item.get("hazardousDetails")
			if isinstance(hazmat, dict) and hazmat.get("weight") is not None:
				hazmat = dict(hazmat)
				hazmat["weight"] = _dayton_int_weight(hazmat["weight"])
				item["hazardousDetails"] = hazmat

			line_items.append(item)

		if "lineItems" in row:
			row["lineItems"] = line_items
		handling_units.append(row)

	if "handlingUnits" in commodities or handling_units:
		commodities["handlingUnits"] = handling_units
		payload["commodities"] = commodities

	return payload


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

	shipment_name = shipment.name if hasattr(shipment, "name") else str(shipment)
	file_doc = attach_base64_pdf_to_shipment(shipment_name, bol["document_binary"])

	sync_dayton_bol_details_to_shipment(
		shipment_name,
		request_data or {},
		bol_result={
			**(bol_result or {}),
			"bol_number": bol.get("bol_number") or (bol_result or {}).get("bol_number"),
			"pro_number": bol.get("pro_number") or (bol_result or {}).get("pro_number"),
		},
		bol_file_url=file_doc.file_url,
	)

	return {
		"status": "success",
		"bol_number": bol["bol_number"],
		"pro_number": bol.get("pro_number"),
		"document_url": file_doc.file_url,
	}


def sync_dayton_bol_details_to_shipment(
	shipment_name: str,
	request_data: dict,
	bol_result: dict | None = None,
	bol_file_url: str | None = None,
	dayton_payload: dict | None = None,
) -> None:
	"""Map Dayton eBOL payload + response identifiers onto LTL Shipment BOL detail fields."""
	from ltl_quote.utils.bol_mapping import update_ltl_shipment_with_dayton_bol

	payload = dayton_payload
	if payload is None:
		adapter = DaytonCarrierAdapter()
		# Update payloads already look like Dayton schema; create uses platform booking fields.
		if isinstance(request_data, dict) and (
			{"origin", "destination", "commodities"} & set(request_data.keys())
		):
			payload = request_data
		else:
			payload = adapter._resolve_dayton_ebol_payload(request_data or {})

	update_ltl_shipment_with_dayton_bol(
		shipment_name=shipment_name,
		dayton_payload=payload or {},
		bol_result=bol_result,
		bol_file_url=bol_file_url,
	)


def attach_base64_pdf_to_shipment(shipment_id: str, base64_string: str):
	"""Decode Dayton images.bol Base64 and attach a public PDF to the shipment BOL field."""
	filename = f"Dayton_Updated_BOL_{shipment_id}.pdf"
	file_bytes = base64.b64decode(base64_string)

	file_doc = save_file(
		fname=filename,
		content=file_bytes,
		dt="LTL Shipment",
		dn=shipment_id,
		is_private=0,
		decode=False,
		df="bol_document",
	)
	absolute_url = f"{frappe.utils.get_url()}{file_doc.file_url}"
	frappe.db.set_value(
		"LTL Shipment",
		shipment_id,
		{
			"bol_document": file_doc.file_url,
			"bol_document_url": absolute_url,
		},
	)
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
				"message": "Waiting for Dayton to scan this PRO. Events appear after pickup is completed and scanned.",
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


def _resolve_dayton_customer_code(customer_code: str | None = None) -> str:
	"""Prefer explicit customer, else DAYTON LTL Carrier.account_number, else default."""
	code = str(customer_code or "").strip()
	if code:
		return code
	if frappe.db.exists("LTL Carrier", "DAYTON"):
		account = frappe.db.get_value("LTL Carrier", "DAYTON", "account_number")
		if account:
			return str(account).strip()
	return DEFAULT_ACCOUNT_NUMBER


def _normalize_dayton_tracking_range(start_date: str, end_date: str) -> tuple[str, str]:
	"""Accept ISO timestamps or dates; expand date-only values to UTC day bounds."""
	start = str(start_date or "").strip()
	end = str(end_date or "").strip()
	if not start or not end:
		frappe.throw("Both start and end are required for track-by-date.")

	def _expand(value: str, *, end_of_day: bool) -> str:
		if "T" in value or " " in value:
			return value.replace(" ", "T")
		# Date-only: 2026-07-01
		suffix = "T23:59:59Z" if end_of_day else "T00:00:00Z"
		return f"{value}{suffix}"

	return _expand(start, end_of_day=False), _expand(end, end_of_day=True)


@frappe.whitelist()
def fetch_dayton_tracking_by_date(start_date: str, end_date: str, customer_code: str | None = None) -> dict:
	"""Proxies Dayton GET /api/Tracking/ByDate (Postman: start, end, customer)."""
	adapter = DaytonCarrierAdapter()
	start, end = _normalize_dayton_tracking_range(start_date, end_date)
	customer = _resolve_dayton_customer_code(customer_code or adapter.account_number)
	endpoint = f"{adapter.base_url}/api/Tracking/ByDate"
	params = {
		"start": start,
		"end": end,
		"customer": customer,
	}
	try:
		response = requests.get(
			endpoint,
			headers=adapter.get_headers(),
			params=params,
			timeout=REQUEST_TIMEOUT,
		)
		if response.status_code == 200:
			data = response.json()
			if isinstance(data, dict):
				data.setdefault("customer", customer)
				data.setdefault("start", start)
				data.setdefault("end", end)
			return data
		return {"status": "error", "code": response.status_code, "text": response.text}
	except Exception as e:
		frappe.throw(f"Frappe Proxy Error: {str(e)}")


@frappe.whitelist()
def fetch_dayton_pending_shipments(customer_code: str | None = None) -> dict:
	"""Proxies Dayton GET /api/Tracking/Pending?customer=..."""
	adapter = DaytonCarrierAdapter()
	customer = _resolve_dayton_customer_code(customer_code or adapter.account_number)
	endpoint = f"{adapter.base_url}/api/Tracking/Pending"
	params = {"customer": customer}
	try:
		response = requests.get(
			endpoint,
			headers=adapter.get_headers(),
			params=params,
			timeout=REQUEST_TIMEOUT,
		)
		if response.status_code == 200:
			data = response.json()
			if isinstance(data, dict):
				data.setdefault("customer", customer)
			return data
		return {"status": "error", "code": response.status_code, "text": response.text}
	except Exception as e:
		frappe.throw(f"Frappe Proxy Error: {str(e)}")


def _find_indexed_dayton_document(search_result: dict, doc_type: str) -> dict | None:
	"""Return the document dict of a given type from an Images/Search response, else None."""
	target = str(doc_type or "").strip().upper()
	for document in search_result.get("documents") or []:
		if str(document.get("type") or "").strip().upper() == target:
			return document
	return None


def get_dayton_indexed_documents(pro_number: str) -> dict:
	"""Summarize Dayton GET /api/Images/Search for API and UI consumers."""
	pro = str(pro_number or "").strip()
	if not pro:
		return {
			"checked": False,
			"pro": "",
			"documents": [],
			"bol_available": False,
			"pod_available": False,
			"message": "No PRO number assigned yet.",
		}

	adapter = DaytonCarrierAdapter()
	search = adapter.search_images(pro)
	if not search.get("ok"):
		return {
			"checked": False,
			"pro": pro,
			"documents": [],
			"bol_available": False,
			"pod_available": False,
			"message": "Could not verify documents with Dayton right now.",
		}

	documents = search.get("documents") or []
	raw = search.get("raw") or {}
	bol_doc = _find_indexed_dayton_document(search, "BILL OF LADING")
	pod_doc = _find_indexed_dayton_document(search, "PROOF OF DELIVERY")
	return {
		"checked": True,
		"pro": raw.get("pro") or pro,
		"trace_id": raw.get("traceId"),
		"documents": documents,
		"bol_available": bol_doc is not None,
		"bol_hash": (bol_doc or {}).get("hash"),
		"pod_available": pod_doc is not None,
		"pod_hash": (pod_doc or {}).get("hash"),
		"message": (
			None
			if bol_doc
			else "Documents are currently being scanned by Dayton's processing team."
		),
	}


@frappe.whitelist()
def search_dayton_images(pro: str) -> dict:
	"""Query Dayton Freight's Images Search API to verify which documents (BOL, POD,
	etc.) have been indexed on their servers for a PRO.

	Use this as a lightweight "index verification" gate before requesting the heavy
	document binaries via get_bol_document() / get_proof_of_delivery().
	"""
	if not pro:
		frappe.throw("A valid PRO tracking number is required to search images.")

	adapter = DaytonCarrierAdapter()
	result = adapter.search_images(pro)
	if result.get("ok"):
		return {"success": True, "data": result.get("raw") or {"pro": pro, "documents": result.get("documents")}}

	raw = result.get("raw") or {}
	return {"success": False, "code": raw.get("code"), "text": raw.get("text")}


@frappe.whitelist()
def dayton_document_available(pro: str, doc_type: str = "BILL OF LADING") -> dict:
	"""Report whether a specific Dayton document type is indexed and safe to download.

	Powers UI badging (enable/disable "Download" buttons) and cron pre-checks. Also
	returns the content ``hash`` so callers can cache-bust locally: re-download only
	when the remote hash differs from the one stored on the shipment.
	"""
	if not pro:
		return {"available": False, "checked": False, "message": "No PRO number assigned yet."}

	adapter = DaytonCarrierAdapter()
	search = adapter.search_images(pro)
	if not search.get("ok"):
		return {"available": False, "checked": False, "message": "Could not verify documents with Dayton right now."}

	document = _find_indexed_dayton_document(search, doc_type)
	if document:
		return {
			"available": True,
			"checked": True,
			"type": doc_type,
			"hash": document.get("hash"),
			"documents": search.get("documents"),
		}

	return {
		"available": False,
		"checked": True,
		"message": "Documents are currently being scanned by Dayton's processing team.",
		"documents": search.get("documents"),
	}
