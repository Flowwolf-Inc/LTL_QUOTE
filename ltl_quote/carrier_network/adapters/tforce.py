# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""TForce Freight rating + BOL + pickup adapter (OAuth2 Bearer)."""

from __future__ import annotations

import base64
import re
import time
from typing import Any

import frappe
import requests
from frappe.utils import cint, flt, getdate, now_datetime, nowdate
from frappe.utils.file_manager import save_file

from ltl_quote.carrier_network.accessorials import (
	build_accessorial_items,
	build_accessorial_items_from_payload,
	tforce_rate_service_options,
)
from ltl_quote.utils.location import resolve_us_location
from ltl_quote.carrier_network.adapters.base import (
	AccessorialItem,
	BaseCarrierAdapter,
	CarrierRateQuote,
	ShipmentRequest,
)
from ltl_quote.utils.booking import resolve_shipper_context

DEFAULT_BASE_URL = "https://api.tforcefreight.com"
DEFAULT_API_VERSION = "cie-v1"
DEFAULT_SERVICE_CODE = "308"
DEFAULT_BILLING_CODE = "30"
DEFAULT_TOKEN_URL = (
	"https://login.microsoftonline.com/ca4f5969-c10f-40d4-8127-e74b691f95de/oauth2/v2.0/token"
)
DEFAULT_SCOPE = (
	"https://tffproduction.onmicrosoft.com/f06cb173-a8e6-44ad-89a1-06c1070a1f62/.default"
)
REQUEST_TIMEOUT = 30

LINEHAUL_CODES = {"LND_GROSS", "AFTR_DSCNT"}
FUEL_CODES = {"FUEL_SUR", "FUS_FEE"}
SKIP_ACCESSORIAL_CODES = LINEHAUL_CODES | FUEL_CODES | {"DSCNT", "DSCNT_RATE"}

_TOKEN_CACHE: dict[str, tuple[str, float]] = {}


class TForceCarrierAdapter(BaseCarrierAdapter):
	"""TForce Freight Rating + BOL API connector."""

	def __init__(self, carrier_doc=None):
		super().__init__(carrier_doc)

		self.carrier_doc = carrier_doc or self.carrier
		if not self.carrier_doc and getattr(self.carrier, "name", None):
			self.carrier_doc = self.carrier
		elif not self.carrier_doc:
			if frappe.db.exists("LTL Carrier", "TFORCE"):
				self.carrier_doc = frappe.get_doc("LTL Carrier", "TFORCE")

		if not self.carrier_doc:
			frappe.throw("TForce carrier record (TFORCE) not found in LTL Carrier.")

		self.carrier = self.carrier_doc
		self.base_url = (self.carrier_doc.get("api_base_url") or DEFAULT_BASE_URL).rstrip("/")
		self.api_version = (self.carrier_doc.get("api_version") or DEFAULT_API_VERSION).strip() or DEFAULT_API_VERSION
		self.account_number = self.carrier_doc.get("account_number") or ""
		self.client_id = self._password_or_plain("api_key")
		self.client_secret = self._password_or_plain("api_secret")
		self._oauth_config = self._parse_oauth_notes()

	def _password_or_plain(self, field: str) -> str:
		value = ""
		if hasattr(self.carrier_doc, "get_password"):
			value = self.carrier_doc.get_password(field, raise_exception=False) or ""
		if value:
			return value
		plain = self.carrier_doc.get(field) or ""
		if plain and hasattr(self.carrier_doc, "is_dummy_password") and self.carrier_doc.is_dummy_password(plain):
			return ""
		return str(plain or "")

	def _parse_oauth_notes(self) -> dict:
		"""Optional JSON in LTL Carrier.notes for token_url / scope / serviceCode / billingCode."""
		raw = (self.carrier_doc.get("notes") or "").strip()
		if not raw.startswith("{"):
			return {}
		try:
			parsed = frappe.parse_json(raw)
			return parsed if isinstance(parsed, dict) else {}
		except Exception:
			return {}

	def get_bearer_token(self) -> str:
		"""Return a Bearer access token (cached OAuth client_credentials, or static api_key)."""
		if not self.client_id:
			frappe.throw(
				"TForce credentials missing. Set API Key (client_id) and API Secret (client_secret), "
				"or paste a Bearer access token into API Key and leave API Secret blank."
			)

		# Static bearer token mode: only API Key is filled.
		if self.client_id and not self.client_secret:
			return self.client_id

		cache_key = getattr(self.carrier_doc, "name", None) or "TFORCE"
		cached = _TOKEN_CACHE.get(cache_key)
		now = time.time()
		if cached and cached[1] > now + 60:
			return cached[0]

		token_url = self._oauth_config.get("token_url") or DEFAULT_TOKEN_URL
		scope = self._oauth_config.get("scope") or DEFAULT_SCOPE
		response = requests.post(
			token_url,
			data={
				"client_id": self.client_id,
				"client_secret": self.client_secret,
				"grant_type": "client_credentials",
				"scope": scope,
			},
			headers={"Content-Type": "application/x-www-form-urlencoded"},
			timeout=REQUEST_TIMEOUT,
		)
		if response.status_code != 200:
			frappe.throw(
				f"TForce OAuth token request failed: HTTP {response.status_code} | {response.text}"
			)

		payload = response.json()
		access_token = payload.get("access_token")
		if not access_token:
			frappe.throw(f"TForce OAuth response missing access_token: {payload}")

		expires_in = max(cint(payload.get("expires_in") or 3600), 60)
		_TOKEN_CACHE[cache_key] = (access_token, now + expires_in)
		return access_token

	def get_headers(self) -> dict:
		return {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"Authorization": f"Bearer {self.get_bearer_token()}",
			"Cache-Control": "no-cache",
		}

	def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
		"""Map ShipmentRequest → TForce getRate → CarrierRateQuote."""
		endpoint = f"{self.base_url}/rating/getRate"
		params = {"api-version": self.api_version}
		payload = self._build_rate_payload(request)
		timeout = self._request_timeout()

		try:
			response = requests.post(
				endpoint,
				params=params,
				headers=self.get_headers(),
				json=payload,
				timeout=timeout,
			)
			if response.status_code != 200 and self._is_invalid_nmfc_response(response):
				for commodity in payload.get("commodities") or []:
					if isinstance(commodity, dict):
						commodity.pop("nmfc", None)
				response = requests.post(
					endpoint,
					params=params,
					headers=self.get_headers(),
					json=payload,
					timeout=timeout,
				)
			if response.status_code != 200:
				error = self._format_http_error(response)
				self._log("LTL Quote - TForce Rate Failure", error)
				return self._error_quote(error)

			data = response.json()
			parsed = self._parse_rate_response(data)
			if parsed.get("error"):
				return self._error_quote(parsed["error"], raw_response=data)

			return CarrierRateQuote(
				carrier_code=self.carrier_code,
				carrier_name=self.carrier.carrier_name or "TForce Freight",
				total_charge=parsed["total_charge"],
				transit_days=parsed["transit_days"],
				linehaul_charge=parsed["linehaul_charge"],
				fuel_surcharge=parsed["fuel_surcharge"],
				accessorial_charge=parsed["accessorial_charge"],
				currency=parsed.get("currency") or "USD",
				carrier_quote_id=parsed.get("carrier_quote_id") or "",
				service_level=parsed.get("service_level") or "TForce Freight LTL",
				reliability_score=float(getattr(self.carrier, "reliability_score", None) or 90),
				accessorial_breakdown=parsed.get("accessorial_breakdown") or {},
				raw_response=data,
			)
		except requests.exceptions.RequestException as e:
			self._log("LTL Quote - TForce Connection Error", str(e))
			return self._error_quote(f"TForce connection error: {e}")
		except (ValueError, TypeError, KeyError) as e:
			self._log("LTL Quote - TForce Parse Failure", f"TForce parse error: {e}")
			return self._error_quote(f"TForce response parsing error: {e}")

	def book_shipment(self, quote_data: dict) -> dict:
		"""Create TForce BOL via POST /shipping/bol/create and return normalized booking result."""
		return self.generate_bill_of_lading(quote_data)

	def generate_bill_of_lading(self, quote_data: dict) -> dict:
		"""Map platform booking payload → TForce shipping/bol/create → booking result."""
		endpoint = f"{self.base_url}/shipping/bol/create"
		params = {"api-version": self.api_version}
		payload = self._build_bol_payload(quote_data or {})
		timeout = self._request_timeout()

		try:
			response = requests.post(
				endpoint,
				params=params,
				headers=self.get_headers(),
				json=payload,
				timeout=timeout,
			)
		except requests.exceptions.RequestException as e:
			self._log("LTL Quote - TForce BOL Connection Error", str(e))
			frappe.throw(f"TForce BOL connection error: {e}")

		if response.status_code != 200:
			error = self._format_http_error(response)
			self._log("LTL Quote - TForce BOL Failure", error)
			frappe.throw(error)

		try:
			data = response.json()
		except ValueError:
			frappe.throw(f"TForce BOL returned non-JSON response: {response.text[:250]}")

		return self._parse_bol_response(data)

	def get_tracking(self, pro_number: str) -> list[dict]:
		"""Poll TForce GET /track/pro/{pro} and return normalized tracking events."""
		pro = str(pro_number or "").strip()
		if not pro:
			return []

		endpoint = f"{self.base_url}/track/pro/{pro}"
		params = {"api-version": self.api_version}
		try:
			response = requests.get(
				endpoint,
				params=params,
				headers=self.get_headers(),
				timeout=self._request_timeout(),
			)
		except requests.exceptions.RequestException as e:
			self._log("LTL Quote - TForce Tracking Connection Error", str(e))
			return []

		if response.status_code in (401, 403):
			self._log(
				"LTL Quote - TForce Tracking Auth Failure",
				f"HTTP {response.status_code} for PRO {pro}: {response.text[:500]}",
			)
			return []

		if response.status_code != 200:
			self._log(
				"LTL Quote - TForce Tracking Failure",
				self._format_http_error(response),
			)
			return []

		try:
			data = response.json() if response.content else {}
		except ValueError:
			self._log("LTL Quote - TForce Tracking Parse Failure", response.text[:500])
			return []

		return self._parse_tracking_response(data)

	def _parse_tracking_response(self, data: dict) -> list[dict]:
		if not isinstance(data, dict):
			return []

		summary = data.get("summary") or {}
		status = summary.get("responseStatus") or {}
		code = str(status.get("code") or "").upper()
		if code and code not in {"OK", "1", "SUCCESS"}:
			return []

		details = data.get("detail") or []
		if isinstance(details, dict):
			details = [details]
		detail = next((row for row in details if isinstance(row, dict)), None)
		if not detail:
			return []

		detail_status = detail.get("detailStatus") or {}
		detail_code = str(detail_status.get("code") or "").upper()
		if detail_code and detail_code not in {"1", "OK", "SUCCESS"}:
			return []

		current = detail.get("currentStatus") if isinstance(detail.get("currentStatus"), dict) else {}
		pickup = detail.get("pickup") if isinstance(detail.get("pickup"), dict) else {}
		delivery = detail.get("delivery") if isinstance(detail.get("delivery"), dict) else {}
		estimated = delivery.get("estimated") if isinstance(delivery.get("estimated"), dict) else {}
		actual = delivery.get("actual") if isinstance(delivery.get("actual"), dict) else {}

		estimated_delivery = estimated.get("date") or ""
		actual_delivery = actual.get("date") or ""
		pickup_date = pickup.get("date") or ""
		signed_by = str(delivery.get("signedBy") or "").strip()

		events: list[dict] = []
		raw_events = detail.get("events") or []
		if not isinstance(raw_events, list):
			raw_events = []
		for row in raw_events:
			if not isinstance(row, dict):
				continue
			parsed = self._parse_tracking_event(row)
			parsed["estimated_delivery"] = estimated_delivery
			parsed["actual_delivery"] = actual_delivery
			parsed["pickup_date"] = pickup_date
			parsed["signed_by"] = signed_by
			events.append(parsed)

		if not events and (current or pickup_date or actual_delivery):
			status_code = self._tforce_status_code(
				current.get("code"),
				current.get("details"),
				current.get("description"),
			)
			location = (
				(actual.get("serviceCenter") if actual_delivery else "")
				or (estimated.get("serviceCenter") if estimated_delivery else "")
				or pickup.get("serviceCenter")
				or ""
			)
			events.append(
				{
					"event_datetime": actual_delivery or pickup_date or estimated_delivery,
					"status_code": status_code,
					"status_description": str(
						current.get("description") or self._tforce_status_label(status_code)
					),
					"location": location,
					"is_exception": 1 if status_code in {"EXCEPTION", "EX"} else 0,
					"estimated_delivery": estimated_delivery,
					"actual_delivery": actual_delivery,
					"pickup_date": pickup_date,
					"signed_by": signed_by,
				}
			)
		return events

	@staticmethod
	def _parse_tracking_event(event: dict) -> dict:
		from ltl_quote.carrier_network.tracking import is_exception_code

		description = str(
			event.get("displayDescription") or event.get("description") or ""
		).strip()
		status_code = TForceCarrierAdapter._tforce_status_code(
			event.get("code"),
			event.get("details"),
			description,
		)
		return {
			"event_datetime": event.get("date") or event.get("event_datetime"),
			"status_code": status_code,
			"status_description": description or TForceCarrierAdapter._tforce_status_label(status_code),
			"location": str(event.get("serviceCenter") or event.get("location") or "").strip(),
			"is_exception": 1 if is_exception_code(status_code) else 0,
		}

	@staticmethod
	def _tforce_status_code(code=None, details=None, description=None) -> str:
		from ltl_quote.carrier_network.tracking import normalize_activity_code

		detail = str(details or "").strip()
		detail_map = {
			"004": "VOIDED",
			"005": "IN_TRANSIT",
			"006": "OUT_FOR_DELIVERY",
			"011": "DELIVERED",
			"013": "EXCEPTION",
		}
		if detail in detail_map:
			return detail_map[detail]

		value = normalize_activity_code(code)
		code_map = {
			"DL": "DELIVERED",
			"D1": "DELIVERED",
			"OF": "OUT_FOR_DELIVERY",
			"OFD": "OUT_FOR_DELIVERY",
			"PU": "PICKED_UP",
			"PK": "PICKED_UP",
			"P1": "PICKED_UP",
			"AR": "IN_TRANSIT",
			"DP": "IN_TRANSIT",
			"IT": "IN_TRANSIT",
			"EX": "EXCEPTION",
			"XC": "EXCEPTION",
			"VD": "VOIDED",
		}
		if value in code_map:
			return code_map[value]
		if value in {"DELIVERED", "OUT_FOR_DELIVERY", "PICKED_UP", "IN_TRANSIT", "EXCEPTION", "VOIDED"}:
			return value

		text = str(description or "").strip().lower()
		if "deliver" in text and "attempt" not in text and "out for" not in text:
			return "DELIVERED"
		if "out for delivery" in text:
			return "OUT_FOR_DELIVERY"
		if "picked" in text or "pick-up" in text or "pickup" in text:
			return "PICKED_UP"
		if "exception" in text or "delay" in text or "weather" in text:
			return "EXCEPTION"
		if "void" in text or "cancel" in text:
			return "VOIDED"
		if text:
			return "IN_TRANSIT"
		return "IN_TRANSIT"

	@staticmethod
	def _tforce_status_label(code: str) -> str:
		from ltl_quote.carrier_network.tracking import activity_label

		return activity_label(code)

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
		"""Schedule a TForce pickup via POST /pickup/request after BOL booking."""
		from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment

		if isinstance(shipment, str):
			shipment = frappe.get_doc("LTL Shipment", shipment)

		if shipment.pickup_number:
			frappe.throw(f"Pickup {shipment.pickup_number} is already scheduled for this shipment.")

		payload = self._build_pickup_payload_from_shipment(shipment)
		endpoint = f"{self.base_url}/pickup/request"
		params = {"api-version": self.api_version}
		timeout = self._request_timeout()

		try:
			response = requests.post(
				endpoint,
				params=params,
				headers=self.get_headers(),
				json=payload,
				timeout=timeout,
			)
		except requests.exceptions.RequestException as e:
			self._log("LTL Quote - TForce Pickup Connection Error", str(e))
			frappe.throw(f"TForce pickup request failed: {e}")

		if response.status_code != 200:
			error = self._format_pickup_http_error(response)
			self._log("LTL Quote - TForce Pickup Failure", error)
			frappe.throw(error)

		try:
			data = response.json() if response.content else {}
		except ValueError:
			frappe.throw(f"TForce pickup returned non-JSON response: {response.text[:250]}")

		normalized = self._parse_pickup_response(data)
		pickup_block = payload.get("pickup") or {}
		if pickup_block.get("date") and pickup_block.get("time"):
			normalized["ready"] = f"{pickup_block['date']} {pickup_block['time']}"
		if pickup_block.get("date") and pickup_block.get("closeTime"):
			normalized["close"] = f"{pickup_block['date']} {pickup_block['closeTime']}"
		normalized["status"] = "acknowledged"
		apply_pickup_response_to_shipment(shipment, normalized, save=True)
		return normalized

	def get_pickup(self, pickup_number: str) -> dict:
		"""TForce has no pickup GET — return the stored confirmation shape."""
		number = str(pickup_number or "").strip()
		if not number:
			return {"ok": False, "message": "A pickup confirmation number is required.", "raw": {}}
		return {
			"ok": True,
			"pickup_number": number,
			"pickup_status": "Scheduled",
			"raw": {"confirmationNumber": number},
		}

	def cancel_pickup(self, number: str) -> dict:
		"""Cancel a TForce pickup via DELETE /pickup/request/{confirmationNumber}."""
		target = str(number or "").strip()
		if not target:
			return {"success": False, "message": "No pickup confirmation number available to cancel."}

		endpoint = f"{self.base_url}/pickup/request/{target}"
		params = {"api-version": self.api_version}
		try:
			response = requests.delete(
				endpoint,
				params=params,
				headers=self.get_headers(),
				timeout=self._request_timeout(),
			)
		except requests.exceptions.RequestException as e:
			self._log("LTL Quote - TForce Pickup Cancel Error", str(e))
			return {"success": False, "message": str(e)}

		if response.status_code == 200:
			return {"success": True, "message": "Pickup cancelled successfully."}

		error = self._format_pickup_http_error(response)
		self._log("LTL Quote - TForce Pickup Cancel Failure", error)
		return {"success": False, "message": error, "code": response.status_code}

	def dispatch_shipment(self, shipment_data: dict) -> dict:
		"""Schedule a TForce pickup for a booked shipment."""
		shipment_name = shipment_data.get("shipment_name")
		if not shipment_name:
			frappe.throw("shipment_name is required to dispatch a TForce pickup.")

		shipment = frappe.get_doc("LTL Shipment", shipment_name)
		if shipment.pickup_number:
			return {
				"status": "acknowledged",
				"ok": True,
				"pickup_number": shipment.pickup_number,
				"pickup_status": shipment.pickup_status or "Scheduled",
				"message": f"Pickup {shipment.pickup_number} is already scheduled.",
			}

		result = self.create_pickup(shipment)
		return {"status": "acknowledged", **result}

	def _build_pickup_payload_from_shipment(self, shipment) -> dict:
		"""Map an LTL Shipment + quote request to TForce POST /pickup/request."""
		from ltl_quote.carrier_network.pickup import (
			_load_quote_request,
			_pickup_comments,
			default_pickup_window,
			resolve_pickup_window,
		)

		quote_request = _load_quote_request(shipment)
		shipper = resolve_shipper_context({}, quote_request)

		origin_zip = str(
			getattr(shipment, "bol_shipper_postal_code", None)
			or (getattr(quote_request, "origin_zip", None) if quote_request else "")
			or ""
		).strip()
		origin_city = str(
			getattr(shipment, "bol_shipper_city", None)
			or (getattr(quote_request, "origin_city", None) if quote_request else "")
			or ""
		).strip()
		origin_state = str(
			getattr(shipment, "bol_shipper_state", None)
			or (getattr(quote_request, "origin_state", None) if quote_request else "")
			or ""
		).strip()
		destination_zip = str(
			getattr(shipment, "bol_consignee_postal_code", None)
			or (getattr(quote_request, "destination_zip", None) if quote_request else "")
			or ""
		).strip()

		origin_city, origin_state = resolve_us_location(origin_zip, origin_city, origin_state)
		if not origin_zip:
			frappe.throw("Origin ZIP is required for TForce pickup scheduling.")
		if not origin_state:
			frappe.throw(
				"Origin state is required for TForce pickup scheduling. Provide origin state or a valid US origin ZIP."
			)
		if not destination_zip:
			frappe.throw("Destination ZIP is required for TForce pickup scheduling.")

		ready_dt, close_dt = resolve_pickup_window(shipment)
		if ready_dt <= now_datetime():
			ready_dt, close_dt = default_pickup_window()

		pickup_date = ready_dt.strftime("%Y-%m-%d")
		origin_phone = self._phone_object(
			getattr(shipment, "bol_shipper_contact_phone", None) or shipper.get("contact_phone"),
			dashed=True,
		)
		origin_email = self._pickup_email(
			getattr(quote_request, "origin_contact_email", None) if quote_request else None,
			getattr(quote_request, "contact_email", None) if quote_request else None,
		)
		company_name = str(
			getattr(shipment, "bol_shipper_name", None) or shipper.get("shipper_name") or "Shipper"
		)
		contact_name = str(
			getattr(shipment, "bol_shipper_contact_name", None)
			or shipper.get("contact_name")
			or "Shipper"
		)

		origin = {
			"companyName": company_name,
			"email": origin_email,
			"contactName": contact_name,
			"phone": origin_phone,
			"address": self._pickup_address(
				getattr(shipment, "bol_shipper_address1", None) or shipper.get("shipper_address"),
				origin_city,
				origin_state,
				origin_zip,
			),
		}

		payload: dict[str, Any] = {
			"pickup": {
				"date": pickup_date,
				"time": ready_dt.strftime("%H:%M:%S"),
				"openTime": "08:00:00",
				"closeTime": close_dt.strftime("%H:%M:%S"),
			},
			"requester": {
				"companyName": company_name,
				"contactName": contact_name,
				"email": origin_email,
				"phone": origin_phone,
				"thirdParty": False,
			},
			"origin": origin,
			"destination": {
				"postalCode": destination_zip,
				"country": "US",
			},
			"lineItems": self._pickup_line_items(shipment, quote_request),
			"instructions": {
				"pickup": str(_pickup_comments(shipment, quote_request) or "Pickup as scheduled"),
				"handling": "Handle with care",
				"delivery": "Deliver as scheduled",
			},
			"pomIndicator": False,
		}

		pro = str(getattr(shipment, "pro_number", None) or "").strip()
		if pro:
			payload["pickup"]["existingShipment"] = {"pro": pro}

		accessorials = build_accessorial_items(
			getattr(quote_request, "accessorials", None) if quote_request else None
		)
		services = list((tforce_rate_service_options(accessorials, self.carrier_doc) or {}).get("pickup") or [])
		if services:
			payload["services"] = services

		return payload

	def _pickup_line_items(self, shipment, quote_request) -> list[dict]:
		rows: list[dict] = []
		quote_items = getattr(quote_request, "line_items", None) if quote_request else None
		if quote_items:
			for row in quote_items:
				rows.append(
					self._pickup_line_item(
						{
							"description": getattr(row, "description", None) or getattr(row, "item_name", None),
							"weight": getattr(row, "weight", None),
							"weight_unit": getattr(row, "weight_unit", None),
							"pieces": getattr(row, "quantity", None) or getattr(row, "qty", None),
							"packaging_type": getattr(row, "units", None) or getattr(row, "packaging_units", None),
							"hazmat": getattr(row, "hazmat", None),
						}
					)
				)
		if not rows:
			for row in getattr(shipment, "bol_line_items", None) or []:
				rows.append(
					self._pickup_line_item(
						{
							"description": getattr(row, "commodity_description", None),
							"weight": getattr(row, "weight", None),
							"weight_unit": getattr(row, "weight_unit", None),
							"pieces": getattr(row, "package_qty", None) or getattr(row, "handling_unit_qty", None),
							"packaging_type": getattr(row, "package_type", None) or getattr(row, "handling_unit_type", None),
							"hazmat": getattr(row, "hazmat", None),
						}
					)
				)
		if not rows:
			rows.append(
				self._pickup_line_item(
					{
						"description": "General Freight",
						"weight": getattr(quote_request, "total_weight", None) if quote_request else 1,
						"weight_unit": "LBS",
						"pieces": getattr(quote_request, "pieces", None) if quote_request else 1,
						"packaging_type": "BOX",
						"hazmat": False,
					}
				)
			)
		return rows

	@staticmethod
	def _pickup_line_item(item: dict) -> dict:
		packaging = str(
			item.get("packaging_type") or item.get("packagingType") or item.get("units") or "BOX"
		).strip().upper() or "BOX"
		aliases = {
			"PALLET": "PLT",
			"SKID": "SKD",
			"CRATE": "CRT",
			"CARTON": "CTN",
			"DRUM": "DRM",
			"BAG": "BAG",
			"BOX": "BOX",
		}
		packaging = aliases.get(packaging, packaging if len(packaging) <= 4 else "BOX")
		return {
			"description": str(item.get("description") or "General Freight")[:50],
			"weight": max(flt(item.get("weight") or 1), 1),
			"weightUnit": str(item.get("weight_unit") or item.get("weightUnit") or "LBS").upper() or "LBS",
			"pieces": max(cint(item.get("pieces") or item.get("qty") or item.get("quantity") or 1), 1),
			"packagingType": packaging,
			"hazardous": bool(item.get("hazmat") or item.get("hazardous")),
		}

	@staticmethod
	def _pickup_address(address1, city, state, postal, country: str = "US") -> dict:
		return {
			"address1": str(address1 or "Shipper Address"),
			"city": str(city or "Unknown"),
			"stateProvinceCode": str(state or "XX"),
			"postalCode": str(postal or ""),
			"country": country or "US",
		}

	@staticmethod
	def _pickup_email(*values) -> str:
		for value in values:
			email = str(value or "").strip()
			if email and "@" in email:
				return email
		if frappe.session.user:
			session_email = frappe.db.get_value("User", frappe.session.user, "email")
			if session_email and "@" in str(session_email):
				return str(session_email).strip()
		return "shipping@example.com"

	def _parse_pickup_response(self, data: dict) -> dict:
		status = data.get("responseStatus") or {}
		code = str(status.get("code") or "").upper()
		description = str(status.get("description") or status.get("message") or "Pickup failed")
		if code and code not in {"1", "OK", "SUCCESS"}:
			frappe.throw(f"TForce pickup rejected: {description}")

		txn = data.get("transactionReference") or {}
		confirmation = str(txn.get("confirmationNumber") or "").strip()
		if not confirmation:
			frappe.throw("TForce pickup succeeded but returned no confirmationNumber.")

		alerts = status.get("alerts") or []
		alert_messages = []
		for alert in alerts:
			if isinstance(alert, dict):
				msg = str(alert.get("message") or "").strip()
				if msg:
					alert_messages.append(msg)
			elif alert:
				alert_messages.append(str(alert))

		return {
			"ok": True,
			"pickup_number": confirmation,
			"pickup_status": "Scheduled",
			"transaction_id": str(txn.get("transactionId") or ""),
			"email_sent": txn.get("emailSent"),
			"origin_is_rural": txn.get("originIsRural"),
			"destination_is_rural": txn.get("destinationIsRural"),
			"alerts": alerts,
			"alert_messages": alert_messages,
			"raw": data,
		}

	@staticmethod
	def _format_pickup_http_error(response) -> str:
		code = ""
		message = ""
		try:
			data = response.json()
			status = (data.get("responseStatus") if isinstance(data, dict) else None) or {}
			code = str(status.get("code") or "")
			message = str(status.get("description") or status.get("message") or "")
			alerts = status.get("alerts") or []
			if not message and alerts:
				first = alerts[0] if isinstance(alerts, list) else {}
				if isinstance(first, dict):
					message = str(first.get("message") or "")
		except Exception:
			message = ""
		if not message:
			message = (getattr(response, "text", None) or "TForce pickup request failed")[:240]
		prefix = f"TForce pickup HTTP {getattr(response, 'status_code', '')}"
		if code:
			return f"{prefix} ({code}): {message}"
		return f"{prefix}: {message}"

	def _build_bol_payload(self, quote_data: dict) -> dict:
		quote_ref = quote_data.get("quote_request")
		quote_doc = None
		if quote_ref and frappe.db.exists("LTL Quote Request", quote_ref):
			quote_doc = frappe.get_doc("LTL Quote Request", quote_ref)
		shipper = resolve_shipper_context(quote_data=quote_data, quote_request=quote_doc)
		pickup_date = self._resolve_bol_pickup_date(quote_data)
		service_code = str(
			self._oauth_config.get("serviceCode") or DEFAULT_SERVICE_CODE
		)
		billing_code = str(
			self._oauth_config.get("billingCode") or DEFAULT_BILLING_CODE
		)

		accessorials = self._accessorials_from_quote_data(quote_data)
		service_options = tforce_rate_service_options(accessorials, self.carrier_doc)
		commodities = self._build_bol_commodities(quote_data)
		handling = self._build_handling_units(quote_data, commodities)

		origin_phone = self._phone_object(
			quote_data.get("origin_contact_phone") or quote_data.get("contact_phone") or shipper.get("contact_phone")
		)
		dest_phone = self._phone_object(
			quote_data.get("destination_contact_phone") or shipper.get("contact_phone")
		)

		ship_from = {
			"name": str(quote_data.get("shipper_name") or shipper.get("shipper_name") or "Shipper"),
			"email": str(
				quote_data.get("origin_contact_email")
				or quote_data.get("contact_email")
				or "shipping@example.com"
			),
			"phone": origin_phone,
			"contact": str(
				quote_data.get("origin_contact_name")
				or quote_data.get("contact_name")
				or shipper.get("contact_name")
				or "Shipper"
			),
			"address": {
				"addressLine": str(
					quote_data.get("shipper_address") or shipper.get("shipper_address") or "Shipper Address"
				),
				"city": str(quote_data.get("origin_city") or "Unknown"),
				"stateProvinceCode": str(quote_data.get("origin_state") or "XX"),
				"postalCode": str(quote_data.get("origin_zip") or ""),
				"country": "US",
			},
		}
		ship_to = {
			"name": str(quote_data.get("consignee_name") or shipper.get("consignee_name") or "Consignee"),
			"email": str(quote_data.get("destination_contact_email") or "receiver@example.com"),
			"phone": dest_phone,
			"contact": str(
				quote_data.get("destination_contact_name")
				or shipper.get("consignee_name")
				or "Consignee"
			),
			"address": {
				"addressLine": str(
					quote_data.get("consignee_address")
					or shipper.get("consignee_address")
					or "Consignee Address"
				),
				"city": str(quote_data.get("destination_city") or "Unknown"),
				"stateProvinceCode": str(quote_data.get("destination_state") or "XX"),
				"postalCode": str(quote_data.get("destination_zip") or ""),
				"country": "US",
			},
		}

		pickup_codes = list(service_options.get("pickup") or [])
		delivery_codes = list(service_options.get("delivery") or [])
		if "RESP" in pickup_codes:
			ship_from["isResidential"] = True
		if "RESD" in delivery_codes:
			ship_to["isResidential"] = True

		payload: dict[str, Any] = {
			"requestOptions": {
				"serviceCode": service_code,
				"pickupDate": pickup_date,
				"previewRate": True,
				"timeInTransit": True,
				"bolPrintFormat": "TFF",
			},
			"shipFrom": ship_from,
			"shipTo": ship_to,
			"payment": {
				"payer": dict(ship_from),
				"billingCode": billing_code,
			},
			"handlingUnitOne": handling["handlingUnitOne"],
			"commodities": commodities,
			"references": [
				{
					"number": str(quote_data.get("quote_request") or f"LTL-{nowdate()}"),
					"type": "BL",
					"quantity": max(cint(quote_data.get("pieces") or 1), 1),
					"weight": flt(quote_data.get("total_weight") or 0),
				}
			],
			"documents": {
				"image": [
					{"type": "20", "format": "01"},
				]
			},
		}

		if handling.get("handlingUnitTwo"):
			payload["handlingUnitTwo"] = handling["handlingUnitTwo"]

		if pickup_codes or delivery_codes:
			payload["serviceOptions"] = {}
			if pickup_codes:
				payload["serviceOptions"]["pickup"] = pickup_codes
			if delivery_codes:
				payload["serviceOptions"]["delivery"] = delivery_codes

		instructions_pickup = (
			quote_data.get("pickup_comments")
			or quote_data.get("pickup_instructions")
			or "Pickup as scheduled"
		)
		instructions_delivery = quote_data.get("delivery_instructions") or "Deliver as scheduled"
		payload["instructions"] = {
			"pickup": str(instructions_pickup),
			"delivery": str(instructions_delivery),
		}

		return payload

	@staticmethod
	def _accessorials_from_quote_data(quote_data: dict) -> list[AccessorialItem]:
		rows = quote_data.get("accessorials") or quote_data.get("accessorial_rows") or []
		if rows:
			return build_accessorial_items_from_payload(rows)
		codes = quote_data.get("accessorial_codes") or []
		return build_accessorial_items_from_payload(
			[{"code": c} for c in codes] if codes else []
		)

	def _build_bol_commodities(self, quote_data: dict) -> list[dict]:
		items = quote_data.get("items") or []
		hazmat_default = bool(quote_data.get("is_hazardous"))
		if any(code == "HAZMAT" for code in [
			str(a.get("accessorial_code") or a.get("code") or "").upper()
			for a in (quote_data.get("accessorials") or [])
			if isinstance(a, dict)
		]):
			hazmat_default = True

		commodities: list[dict] = []
		if items:
			for item in items:
				if isinstance(item, dict):
					commodities.append(self._commodity_from_item(item, hazmat_default))
		if not commodities:
			commodities.append(
				self._commodity_from_item(
					{
						"description": quote_data.get("commodity_description") or "General Freight",
						"freight_class": quote_data.get("freight_class") or "70",
						"qty": quote_data.get("pieces") or 1,
						"weight": quote_data.get("total_weight") or 0,
						"hazmat": hazmat_default,
						"length": quote_data.get("length"),
						"width": quote_data.get("width"),
						"height": quote_data.get("height"),
						"nmfc": quote_data.get("nmfc"),
					},
					hazmat_default,
				)
			)
		return commodities

	@staticmethod
	def _build_handling_units(quote_data: dict, commodities: list[dict]) -> dict:
		"""Build TForce BOL handlingUnitOne.

		handlingUnitOne.typeCode enum ONLY allows: CBY, CRT, PLT, SKD, TOT.
		(OTH/LOO are only valid on handlingUnitTwo — never send them on unit one.)
		"""
		pieces = max(cint(quote_data.get("pieces") or 0), 1)
		if not pieces:
			pieces = max(sum(cint(c.get("pieces") or 0) for c in commodities), 1)

		unit_one_codes = {"CBY", "CRT", "PLT", "SKD", "TOT"}
		pkg_aliases = {
			"PALLET": "PLT",
			"PLT": "PLT",
			"SKID": "SKD",
			"SKD": "SKD",
			"CRATE": "CRT",
			"CRT": "CRT",
			"TOTE": "TOT",
			"TOTES": "TOT",
			"TOT": "TOT",
			"CARBOY": "CBY",
			"CBY": "CBY",
		}

		unit_one = "SKD"
		for c in commodities:
			pt = str(c.get("packagingType") or quote_data.get("packaging_type") or "").strip().upper()
			mapped = pkg_aliases.get(pt)
			if mapped and mapped in unit_one_codes:
				unit_one = mapped
				break

		return {
			"handlingUnitOne": {
				"quantity": pieces,
				"typeCode": unit_one,
			}
		}

	@staticmethod
	def _phone_object(raw, *, dashed: bool = False) -> dict:
		digits = re.sub(r"\D", "", str(raw or ""))
		if len(digits) < 10:
			digits = "0000000000"
		number = digits[:10]
		if dashed:
			number = f"{number[:3]}-{number[3:6]}-{number[6:]}"
		return {"number": number, "extension": digits[10:14] if len(digits) > 10 else ""}

	def _resolve_bol_pickup_date(self, quote_data: dict) -> str:
		raw = quote_data.get("pickup_date")
		try:
			return getdate(raw or nowdate()).strftime("%Y-%m-%d")
		except Exception:
			return str(nowdate())

	def _parse_bol_response(self, data: dict) -> dict:
		summary = data.get("summary") or {}
		code = str(summary.get("code") or (summary.get("responseStatus") or {}).get("code") or "").upper()
		message = str(
			summary.get("message")
			or (summary.get("responseStatus") or {}).get("message")
			or "BOL failed"
		)
		if code and code not in {"OK", "1", "SUCCESS"}:
			frappe.throw(f"TForce BOL rejected: {message}")

		detail = data.get("detail") or {}
		if isinstance(detail, list):
			detail = detail[0] if detail else {}

		bol_id = detail.get("bolId") or detail.get("bolID")
		pro_number = str(detail.get("pro") or detail.get("proNumber") or "").strip()
		if not bol_id and not pro_number:
			frappe.throw("TForce BOL succeeded but returned no bolId/PRO.")

		pickup = detail.get("pickup") or {}
		pickup_txn = pickup.get("transactionReference") or {}
		confirmation = str(pickup_txn.get("confirmationNumber") or "").strip()

		document_binary = _extract_bol_pdf_base64(detail)

		bol_number = str(bol_id or confirmation or pro_number)
		transit_days = 0
		rate_detail = detail.get("rateDetail") or []
		if isinstance(rate_detail, list) and rate_detail:
			tit = (rate_detail[0].get("timeInTransit") or {})
			transit_days = cint(tit.get("timeInTransit") or 0)

		return {
			"status": "booked",
			"bol_number": bol_number,
			"pro_number": pro_number,
			"carrier_confirmation": confirmation or bol_number,
			"pickup_number": confirmation,
			"tforce_bol_id": str(bol_id or ""),
			"document_binary": document_binary,
			"transit_days": transit_days,
			"raw_response": data,
		}

	def _request_timeout(self) -> int:
		try:
			settings = frappe.get_single("LTL Platform Settings")
			return cint(settings.rate_request_timeout_seconds) or REQUEST_TIMEOUT
		except Exception:
			return REQUEST_TIMEOUT

	def _build_rate_payload(self, request: ShipmentRequest) -> dict:
		pickup_date = self._resolve_pickup_date(request)
		service_code = str(self._oauth_config.get("serviceCode") or DEFAULT_SERVICE_CODE)
		billing_code = str(self._oauth_config.get("billingCode") or DEFAULT_BILLING_CODE)
		customer_context = str(getattr(request, "customer_context", None) or f"LTL-{nowdate()}").replace(" ", "-")

		ship_from = self._address_block(request.origin_city, request.origin_state, request.origin_zip)
		ship_to = self._address_block(
			request.destination_city, request.destination_state, request.destination_zip
		)
		service_options = tforce_rate_service_options(request.accessorials, self.carrier_doc)
		commodities = self._build_commodities(request)
		for commodity in commodities:
			if not isinstance(commodity, dict):
				continue
			nmfc = commodity.get("nmfc")
			if nmfc and not TForceCarrierAdapter._valid_nmfc_prime(
				(nmfc or {}).get("prime") if isinstance(nmfc, dict) else nmfc
			):
				commodity.pop("nmfc", None)

		payload = {
			"requestOptions": {
				"serviceCode": service_code,
				"pickupDate": pickup_date,
				"type": "L",
				"densityEligible": False,
				"timeInTransit": True,
				"quoteNumber": True,
				"customerContext": customer_context,
			},
			"shipFrom": {"address": ship_from},
			"shipTo": {"address": ship_to},
			"payment": {
				"payer": {"address": ship_from},
				"billingCode": billing_code,
			},
			"commodities": commodities,
		}
		if service_options.get("pickup") or service_options.get("delivery"):
			payload["serviceOptions"] = {
				k: v for k, v in service_options.items() if v
			}
		return payload

	@staticmethod
	def _address_block(city, state, postal) -> dict:
		return {
			"city": str(city or "Unknown"),
			"stateProvinceCode": str(state or "XX"),
			"postalCode": str(postal or ""),
			"country": "US",
		}

	def _resolve_pickup_date(self, request: ShipmentRequest) -> str:
		raw = getattr(request, "pickup_date", None)
		try:
			return getdate(raw or nowdate()).strftime("%Y-%m-%d")
		except Exception:
			return str(nowdate())

	def _build_commodities(self, request: ShipmentRequest) -> list[dict]:
		items = getattr(request, "items", None) or []
		hazmat_default = "HAZMAT" in (request.accessorial_codes or [])
		commodities: list[dict] = []
		if isinstance(items, list) and items:
			for item in items:
				if isinstance(item, dict):
					commodities.append(self._commodity_from_item(item, hazmat_default))
		if commodities:
			return commodities

		commodity = self._commodity_from_item(
			{
				"classification": request.freight_class,
				"qty": request.pieces,
				"weight": request.total_weight,
				"length": request.length,
				"width": request.width,
				"height": request.height,
				"hazmat": hazmat_default,
			},
			hazmat_default,
		)
		return [commodity]

	@staticmethod
	def _commodity_from_item(item: dict, hazmat_default: bool = False) -> dict:
		packaging = str(
			item.get("packagingType")
			or item.get("packaging_type")
			or item.get("package_type")
			or item.get("units")
			or "BOX"
		).strip().upper() or "BOX"
		aliases = {
			"PALLET": "PLT",
			"SKID": "SKD",
			"CRATE": "CRT",
			"CARTON": "CTN",
			"DRUM": "DRM",
		}
		packaging = aliases.get(packaging, packaging)

		weight_val = flt(item.get("weight") or 0)
		pieces = max(cint(item.get("pieces") or item.get("qty") or item.get("quantity") or 1), 1)
		freight_class = str(
			item.get("class")
			or item.get("classification")
			or item.get("freight_class")
			or item.get("nmfc_class")
			or "70"
		)
		hazmat = bool(
			item.get("dangerousGoods")
			if item.get("dangerousGoods") is not None
			else (item.get("hazmat") or item.get("hazardous") or hazmat_default)
		)

		commodity: dict[str, Any] = {
			"description": str(
				item.get("description")
				or item.get("commodity_description")
				or item.get("item_name")
				or "General Freight"
			)[:50],
			"class": freight_class,
			"pieces": pieces,
			"weight": {
				"weight": weight_val,
				"weightUnit": str(item.get("weight_unit") or item.get("weightUnit") or "LBS"),
			},
			"packagingType": packaging,
			"dangerousGoods": hazmat,
		}

		nmfc_block = TForceCarrierAdapter._nmfc_block(item)
		if nmfc_block:
			commodity["nmfc"] = nmfc_block

		length = flt(item.get("length") or 0)
		width = flt(item.get("width") or 0)
		height = flt(item.get("height") or 0)
		if length and width and height:
			commodity["dimensions"] = {
				"length": length,
				"width": width,
				"height": height,
				"unit": str(item.get("dimension_unit") or item.get("unit") or "IN"),
			}
		return commodity

	@staticmethod
	def _nmfc_block(item: dict) -> dict | None:
		"""Return TForce nmfc {prime, sub} only when the prime is numeric.

		TForce getRate rejects the whole quote with code 1023 when NMFC prime is
		not a catalog number. Class-only rating is valid without NMFC.
		"""
		if not isinstance(item, dict):
			return None
		raw = item.get("nmfc") if item.get("nmfc") not in (None, "") else item.get("nmfc_number")
		sub_raw = str(item.get("nmfc_sub") or item.get("nmfcSub") or "").strip()
		if isinstance(raw, dict):
			prime = str(raw.get("prime") or raw.get("number") or "").strip()
			sub_raw = str(raw.get("sub") or sub_raw or "").strip()
		else:
			prime = str(raw or "").strip()
		if not prime:
			return None
		match = re.match(r"^(\d{4,8})\s*[-./]\s*(\d{1,4})$", prime)
		if match:
			prime, sub_raw = match.group(1), match.group(2)
		if not TForceCarrierAdapter._valid_nmfc_prime(prime):
			return None
		sub_digits = re.sub(r"\D", "", sub_raw) or "00"
		return {"prime": prime, "sub": sub_digits.zfill(2)[:2]}

	@staticmethod
	def _valid_nmfc_prime(prime) -> bool:
		value = str(prime or "").strip()
		return bool(re.fullmatch(r"\d{4,8}", value))

	@staticmethod
	def _format_http_error(response) -> str:
		code = ""
		message = ""
		try:
			data = response.json()
			summary = data.get("summary") if isinstance(data, dict) else {}
			status = (summary or {}).get("responseStatus") or {}
			code = str(status.get("code") or (summary or {}).get("code") or "")
			message = str(status.get("message") or (summary or {}).get("message") or "")
		except Exception:
			message = ""
		if not message:
			message = (getattr(response, "text", None) or "TForce request failed")[:240]
		prefix = f"TForce HTTP {getattr(response, 'status_code', '')}"
		if code:
			return f"{prefix} ({code}): {message}"
		return f"{prefix}: {message}"

	@staticmethod
	def _is_invalid_nmfc_response(response) -> bool:
		if getattr(response, "status_code", None) != 400:
			return False
		try:
			data = response.json()
			status = ((data.get("summary") or {}).get("responseStatus") or {})
			code = str(status.get("code") or "")
			message = str(status.get("message") or "").lower()
			return code == "1023" or "invalid nmfc" in message
		except Exception:
			return "invalid nmfc" in (getattr(response, "text", "") or "").lower()

	@staticmethod
	def _log(title: str, message: str) -> None:
		try:
			frappe.log_error(title=str(title or "TForce")[:140], message=str(message or "")[:8000])
		except Exception:
			pass

	def _parse_rate_response(self, data: dict) -> dict:
		summary = data.get("summary") or {}
		status = summary.get("responseStatus") or {}
		code = str(status.get("code") or summary.get("code") or "").upper()
		message = str(status.get("message") or summary.get("message") or "TForce rating failed")
		if code and code not in {"OK", "1", "SUCCESS"}:
			return {"error": f"TForce rating rejected: {message}"}

		details = data.get("detail") or []
		if not details:
			return {"error": "TForce returned no rate detail"}
		detail = details[0] if isinstance(details, list) else details

		detail_status = detail.get("detailStatus") or {}
		detail_code = str(detail_status.get("code") or "").upper()
		if detail_code and detail_code not in {"1", "OK", "SUCCESS"}:
			return {
				"error": f"TForce detail status: {detail_status.get('message') or 'detail failed'}"
			}

		rates = detail.get("rate") or []
		linehaul = 0.0
		fuel = 0.0
		accessorial = 0.0
		breakdown: dict[str, float] = {}
		for row in rates:
			if not isinstance(row, dict):
				continue
			code = str(row.get("code") or "").upper()
			value = flt(row.get("value") or 0)
			if code in LINEHAUL_CODES:
				# Prefer AFTR_DSCNT when present (net linehaul after discount).
				if code == "AFTR_DSCNT" or not linehaul:
					linehaul = value
				continue
			if code in FUEL_CODES:
				fuel += value
				continue
			if code in SKIP_ACCESSORIAL_CODES:
				continue
			accessorial += value
			breakdown[code] = breakdown.get(code, 0) + value

		shipment_charges = detail.get("shipmentCharges") or {}
		total_obj = shipment_charges.get("total") or {}
		total = flt(total_obj.get("value") or 0)
		currency = str(total_obj.get("currency") or "USD")
		if not total:
			total = linehaul + fuel + accessorial

		transit = detail.get("timeInTransit") or {}
		transit_days = cint(transit.get("timeInTransit") or 0)
		quote_number = str(summary.get("quoteNumber") or "")
		service = detail.get("service") or {}
		service_level = str(service.get("description") or "TForce Freight LTL")

		return {
			"total_charge": total,
			"transit_days": transit_days,
			"linehaul_charge": linehaul,
			"fuel_surcharge": fuel,
			"accessorial_charge": accessorial,
			"currency": currency,
			"carrier_quote_id": f"TFF-{quote_number}" if quote_number else "",
			"service_level": service_level,
			"accessorial_breakdown": breakdown,
		}

	def _error_quote(self, message: str, raw_response: dict | None = None) -> CarrierRateQuote:
		return CarrierRateQuote(
			carrier_code=self.carrier_code,
			carrier_name=getattr(self.carrier, "carrier_name", None) or "TForce Freight",
			total_charge=0,
			transit_days=0,
			error=message,
			raw_response=raw_response or {},
		)


def _is_tforce_shipment(shipment) -> bool:
	carrier = str(getattr(shipment, "carrier", None) or "").upper()
	if carrier in {"TFORCE", "TFF"}:
		return True
	if carrier and frappe.db.exists("LTL Carrier", carrier):
		connector = str(frappe.db.get_value("LTL Carrier", carrier, "connector_type") or "")
		if connector == "TForce":
			return True
	carrier_name = str(getattr(shipment, "carrier_name", None) or "").lower()
	return "tforce" in carrier_name


def _extract_bol_pdf_base64(detail: dict) -> str:
	"""Prefer TForce document type 20 (BOL PDF) from bol/create documents.image."""
	docs = detail.get("documents") or {}
	images = docs.get("image") if isinstance(docs, dict) else []
	if not isinstance(images, list):
		return ""

	preferred = ""
	for image in images:
		if not isinstance(image, dict):
			continue
		data_b64 = str(image.get("data") or "").strip()
		if not data_b64:
			continue
		img_type = str(image.get("type") or "").strip()
		img_format = str(image.get("format") or "").strip().upper()
		status = str(image.get("status") or "").strip().upper()
		if status and status not in {"OK", "1", "SUCCESS"}:
			continue
		if img_type in {"20", "BOL"} or img_format in {"PDF", "01"}:
			preferred = data_b64
			if img_type in {"20", "BOL"}:
				return preferred
	return preferred


def _decode_pdf_bytes(document_binary: str) -> bytes | None:
	raw = str(document_binary or "").strip()
	if not raw:
		return None
	if "," in raw and raw.lower().startswith("data:"):
		raw = raw.split(",", 1)[1]
	raw = "".join(raw.split())
	try:
		file_bytes = base64.b64decode(raw)
	except Exception:
		return None
	marker = file_bytes.find(b"%PDF")
	if marker < 0:
		return None
	file_bytes = file_bytes[marker:]
	if len(file_bytes) < 100:
		return None
	return file_bytes


def apply_tforce_bol_details_to_shipment(
	shipment_name: str,
	quote_data: dict | None = None,
	bol_result: dict | None = None,
) -> None:
	"""Persist TForce party / commodity / identifier fields onto the shipment."""
	if not shipment_name or not frappe.db.exists("LTL Shipment", shipment_name):
		return

	quote_data = quote_data or {}
	bol_result = bol_result or {}
	shipment = frappe.get_doc("LTL Shipment", shipment_name)

	shipment.bol_document_type = shipment.bol_document_type or "Bill of Lading"
	shipment.bol_scac = shipment.bol_scac or "TFFA"
	shipment.bol_date = shipment.bol_date or nowdate()
	shipment.bol_quote_id = shipment.bol_quote_id or str(quote_data.get("quote_request") or "")
	shipment.bol_payment_terms = shipment.bol_payment_terms or "Prepaid"

	shipment.bol_shipper_name = quote_data.get("shipper_name") or shipment.bol_shipper_name
	shipment.bol_shipper_address1 = quote_data.get("shipper_address") or shipment.bol_shipper_address1
	shipment.bol_shipper_city = quote_data.get("origin_city") or shipment.bol_shipper_city
	shipment.bol_shipper_state = quote_data.get("origin_state") or shipment.bol_shipper_state
	shipment.bol_shipper_postal_code = quote_data.get("origin_zip") or shipment.bol_shipper_postal_code
	shipment.bol_shipper_contact_name = (
		quote_data.get("origin_contact_name") or quote_data.get("contact_name") or shipment.bol_shipper_contact_name
	)
	shipment.bol_shipper_contact_phone = (
		quote_data.get("origin_contact_phone") or quote_data.get("contact_phone") or shipment.bol_shipper_contact_phone
	)

	shipment.bol_consignee_name = quote_data.get("consignee_name") or shipment.bol_consignee_name
	shipment.bol_consignee_address1 = quote_data.get("consignee_address") or shipment.bol_consignee_address1
	shipment.bol_consignee_city = quote_data.get("destination_city") or shipment.bol_consignee_city
	shipment.bol_consignee_state = quote_data.get("destination_state") or shipment.bol_consignee_state
	shipment.bol_consignee_postal_code = quote_data.get("destination_zip") or shipment.bol_consignee_postal_code
	shipment.bol_consignee_contact_name = (
		quote_data.get("destination_contact_name") or shipment.bol_consignee_contact_name
	)
	shipment.bol_consignee_contact_phone = (
		quote_data.get("destination_contact_phone") or shipment.bol_consignee_contact_phone
	)

	shipment.bol_bill_to_name = shipment.bol_shipper_name
	shipment.bol_bill_to_address1 = shipment.bol_shipper_address1
	shipment.bol_bill_to_city = shipment.bol_shipper_city
	shipment.bol_bill_to_state = shipment.bol_shipper_state
	shipment.bol_bill_to_postal_code = shipment.bol_shipper_postal_code

	if bol_result.get("tforce_bol_id"):
		shipment.tforce_bol_id = bol_result.get("tforce_bol_id")
	if bol_result.get("bol_number"):
		shipment.bol_number = bol_result.get("bol_number")
	if bol_result.get("pro_number"):
		shipment.pro_number = bol_result.get("pro_number")
	if bol_result.get("pickup_number"):
		shipment.pickup_number = bol_result.get("pickup_number")

	items = quote_data.get("items") or []
	if items and not shipment.bol_line_items:
		total_qty = 0
		total_weight = 0.0
		for idx, item in enumerate(items, start=1):
			if not isinstance(item, dict):
				continue
			qty = max(cint(item.get("qty") or item.get("quantity") or item.get("pieces") or 1), 1)
			weight = flt(item.get("weight") or 0)
			total_qty += qty
			total_weight += weight
			shipment.append(
				"bol_line_items",
				{
					"idx_line_no": idx,
					"handling_unit_qty": qty,
					"handling_unit_type": str(item.get("units") or item.get("packaging_units") or "SKID"),
					"package_qty": qty,
					"package_type": str(item.get("packaging_type") or item.get("units") or "BOX"),
					"freight_class": str(item.get("freight_class") or item.get("classification") or ""),
					"nmfc": str(item.get("nmfc") or item.get("nmfc_number") or ""),
					"hazmat": 1 if item.get("hazmat") or item.get("hazardous") else 0,
					"commodity_description": str(
						item.get("description") or item.get("item_name") or "General Freight"
					),
					"weight": weight,
					"weight_unit": str(item.get("weight_unit") or "LBS"),
					"length": cint(item.get("length") or 0) or None,
					"width": cint(item.get("width") or 0) or None,
					"height": cint(item.get("height") or 0) or None,
					"dimension_unit": str(item.get("dimension_unit") or "IN"),
					"quote_reference": str(quote_data.get("quote_request") or ""),
				},
			)
		if total_qty:
			shipment.bol_total_quantity = total_qty
		if total_weight:
			shipment.bol_grand_total_weight = total_weight

	shipment.save(ignore_permissions=True)


def attach_tforce_bol_to_shipment(shipment, bol_result: dict | None = None) -> dict:
	"""Attach TForce BOL PDF (base64) to LTL Shipment and the linked quote request."""
	bol_result = bol_result or {}
	shipment_name = shipment.name if hasattr(shipment, "name") else str(shipment)
	document_binary = bol_result.get("document_binary") or ""
	if not document_binary:
		raw = bol_result.get("raw_response") or {}
		if isinstance(raw, dict):
			detail = raw.get("detail") or {}
			if isinstance(detail, list):
				detail = detail[0] if detail else {}
			document_binary = _extract_bol_pdf_base64(detail)
	bol_number = bol_result.get("bol_number") or ""
	pro_number = bol_result.get("pro_number") or ""

	result = {
		"status": "pending",
		"bol_number": bol_number,
		"pro_number": pro_number,
		"message": "TForce BOL created but PDF binary was not returned.",
	}

	updates = {
		"bol_number": bol_number or None,
		"pro_number": pro_number or None,
		"tforce_bol_id": bol_result.get("tforce_bol_id") or None,
		"carrier_confirmation": bol_result.get("carrier_confirmation") or bol_number or None,
		"pickup_number": bol_result.get("pickup_number") or None,
	}
	updates = {k: v for k, v in updates.items() if v}
	if updates:
		frappe.db.set_value("LTL Shipment", shipment_name, updates, update_modified=False)

	if not document_binary:
		frappe.db.commit()
		return result

	try:
		file_bytes = _decode_pdf_bytes(document_binary)
		if not file_bytes:
			result["message"] = "TForce document binary was not a usable PDF."
			frappe.db.commit()
			return result

		filename = f"TForce_BOL_{bol_number or shipment_name}.pdf"
		file_doc = save_file(
			fname=filename,
			content=file_bytes,
			dt="LTL Shipment",
			dn=shipment_name,
			is_private=0,
			decode=False,
			df="bol_document",
		)
		file_url = file_doc.file_url
		absolute_url = f"{frappe.utils.get_url()}{file_url}"
		frappe.db.set_value(
			"LTL Shipment",
			shipment_name,
			{
				"bol_document": file_url,
				"bol_document_url": absolute_url,
				"bol_document_type": "Bill of Lading",
			},
		)

		quote_name = frappe.db.get_value("LTL Shipment", shipment_name, "quote_request")
		if quote_name:
			existing = frappe.db.exists(
				"File",
				{
					"attached_to_doctype": "LTL Quote Request",
					"attached_to_name": quote_name,
					"file_url": file_url,
				},
			)
			if not existing:
				try:
					frappe.get_doc(
						{
							"doctype": "File",
							"file_name": filename,
							"file_url": file_url,
							"attached_to_doctype": "LTL Quote Request",
							"attached_to_name": quote_name,
							"is_private": 0,
						}
					).insert(ignore_permissions=True)
				except Exception:
					frappe.log_error(frappe.get_traceback(), "LTL Quote - TForce Quote BOL File Link")
			frappe.db.set_value(
				"LTL Quote Request",
				quote_name,
				{
					"bol_number": bol_number or None,
					"pro_number": pro_number or None,
					"bol_document_url": absolute_url,
				},
				update_modified=False,
			)

		frappe.db.commit()
		return {
			"status": "success",
			"bol_number": bol_number,
			"pro_number": pro_number,
			"document_url": absolute_url,
		}
	except Exception:
		frappe.log_error(frappe.get_traceback(), "LTL Quote - TForce BOL Attach Failure")
		frappe.db.commit()
		return {
			"status": "error",
			"bol_number": bol_number,
			"pro_number": pro_number,
			"message": "Failed to attach TForce BOL PDF.",
		}
