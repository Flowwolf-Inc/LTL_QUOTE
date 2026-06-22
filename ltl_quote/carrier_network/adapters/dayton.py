import base64

import frappe
import requests
from frappe.utils import add_days, cint, flt, now_datetime, today

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
			"document_binary": base64_pdf,
			"shipping_labels_binary": base64_labels,
		}

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
		"""Poll Dayton tracking endpoint for live milestone event logs."""
		endpoint = f"{self.base_url}/api/Tracking/{pro_number}"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking Connection Error")
			return []

		if response.status_code != 200:
			frappe.log_error(
				f"Tracking query failed for PRO {pro_number}: {response.text}",
				"Dayton Tracking Error",
			)
			return []

		data = response.json()
		events = []
		raw_events = data if isinstance(data, list) else data.get("events") or []
		for event in raw_events:
			events.append(
				{
					"event_datetime": event.get("dateTime") or event.get("event_datetime"),
					"status_code": event.get("statusCode") or event.get("status_code") or "IN_TRANSIT",
					"status_description": event.get("description") or event.get("status_description") or "Cargo Movement Updated",
					"location": event.get("location") or "Terminal Center",
					"is_exception": 1 if event.get("isException") else 0,
				}
			)
		return events

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


@frappe.whitelist(allow_guest=True)
def download_dayton_bol():
	request_data = frappe.request.get_json() or {}
	adapter = DaytonCarrierAdapter()
	request_data["return_raw_file"] = False

	try:
		bol_result = adapter.generate_bill_of_lading(request_data)
	except frappe.ValidationError:
		raise

	base64_pdf = bol_result.get("document_binary")
	bol_number = bol_result.get("bol_number") or "draft"

	if not base64_pdf:
		if _is_dayton_test_request(request_data, bol_result):
			base64_pdf = TEST_BOL_PDF_BASE64
		else:
			frappe.throw("No document binary returned from Dayton Freight production API.")

	full_document_url = _save_bol_pdf_file(base64_pdf, bol_number)

	return {
		"status": "success",
		"bol_number": bol_number,
		"pro_number": bol_result.get("pro_number"),
		"document_url": full_document_url,
	}
