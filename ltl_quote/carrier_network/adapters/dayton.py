import base64

import frappe
import requests
from frappe.utils import add_days, flt, now_datetime, today

from ltl_quote.carrier_network.accessorials import (
	build_accessorial_items,
	dayton_rate_accessorials,
)
from ltl_quote.carrier_network.adapters.base import BaseCarrierAdapter, CarrierRateQuote, ShipmentRequest

DEFAULT_BASE_URL = "https://api.daytonfreight.com"
DEFAULT_ACCOUNT_NUMBER = "0055666"
REQUEST_TIMEOUT = 15


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

	def book_shipment(self, quote_data: dict) -> dict:
		"""Maps quote booking context -> Dayton Create Pickup API."""
		endpoint = f"{self.base_url}/api/Pickup"
		quote_request = self._load_quote_request(quote_data)
		pickup_date = quote_data.get("pickup_date") or today()
		pickup_accessorials = dayton_rate_accessorials(build_accessorial_items(getattr(quote_request, "accessorials", None)))

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
				"name": str(quote_data.get("shipper_name") or quote_request.origin_city or "Origin Warehouse"),
				"address": {
					"address1": str(quote_data.get("shipper_address") or ""),
					"city": str(quote_data.get("origin_city") or quote_request.origin_city or ""),
					"state": str(quote_data.get("origin_state") or quote_request.origin_state or ""),
					"zip": str(quote_data.get("origin_zip") or quote_request.origin_zip),
				},
			},
			"ready": f"{pickup_date}T09:00:00",
			"close": f"{pickup_date}T17:00:00",
			"contact": {
				"name": str(quote_data.get("contact_name") or "Shipping Desk"),
				"phone": str(quote_data.get("contact_phone") or "0000000000"),
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
		"""Fetch tracking events from Dayton Freight tracking API."""
		endpoint = f"{self.base_url}/api/Tracking/{pro_number}"

		try:
			response = requests.get(endpoint, headers=self.get_headers(), timeout=REQUEST_TIMEOUT)
		except requests.exceptions.RequestException as e:
			frappe.log_error(str(e), "LTL Quote - Dayton Tracking Connection Error")
			return []

		if response.status_code != 200:
			frappe.log_error(response.text, "LTL Quote - Dayton Tracking Failure")
			return []

		data = response.json()
		events = []
		for event in data if isinstance(data, list) else data.get("events") or []:
			events.append(
				{
					"event_datetime": event.get("dateTime") or event.get("event_datetime"),
					"status_code": event.get("statusCode") or event.get("status_code") or "IN_TRANSIT",
					"status_description": event.get("description") or event.get("status_description") or "",
					"location": event.get("location") or "",
					"is_exception": 1 if event.get("isException") else 0,
				}
			)
		return events

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
		quote_request_name = quote_data.get("quote_request")
		if quote_request_name and frappe.db.exists("LTL Quote Request", quote_request_name):
			return frappe.get_doc("LTL Quote Request", quote_request_name)
		return frappe._dict(
			{
				"origin_zip": quote_data.get("origin_zip"),
				"destination_zip": quote_data.get("destination_zip"),
				"total_weight": quote_data.get("total_weight"),
				"pieces": quote_data.get("pieces") or 1,
				"origin_city": quote_data.get("origin_city"),
				"origin_state": quote_data.get("origin_state"),
			}
		)
