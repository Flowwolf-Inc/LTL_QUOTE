# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

"""SMC3 Pricing Aggregate adapter — one connector, many network-carrier quotes."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from types import SimpleNamespace

import frappe
import requests
from frappe.utils import cint, flt, getdate
from frappe.utils.file_manager import save_file

from ltl_quote.carrier_network.adapters.base import (
	BaseCarrierAdapter,
	CarrierRateQuote,
	ShipmentRequest,
)
from ltl_quote.carrier_network.smc3_onboarded import (
	SANDBOX_BILL_ACCOUNT,
	SANDBOX_EVA_ACCESS_ID,
	carrier_display_name,
	is_demo_display_name,
	is_sandbox_scac,
	supports_contract_dynamic,
)
from ltl_quote.carrier_network.smc3_token import (
	AUTH_USER_MESSAGE,
	SMC3AuthError,
	SMC3TokenService,
	is_invalid_access_token,
)
from ltl_quote.carrier_network.smc3_bol import (
	DEFAULT_BOL_BASE,
	DEFAULT_DOCUMENT_DEMO_PRO,
	DEFAULT_SANDBOX_ACCOUNT,
	build_bol_payload,
	extract_bol_pdf,
	extract_bol_png_images,
	extract_reference_numbers,
	quote_data_from_shipment,
	sanitize_bol_log,
)
from ltl_quote.carrier_network.smc3_quote_mapper import (
	RATE_SOURCE,
	build_aggregate_payload,
	build_carrier_entry,
	transform_carrier_results,
)
from ltl_quote.utils.booking import resolve_shipper_context
from ltl_quote.utils.location import resolve_us_location
from ltl_quote.utils.transaction_log import log_carrier_transaction

DEFAULT_ENDPOINT = "https://pricing.smc3.com/pricing/v3/app/aggregate"
LEGACY_V1_ENDPOINT = "https://pricing.smc3.com/pricing/aggregate/v1/app/"
DEFAULT_APA_BASE = "https://apa.smc3.com/apa/assignment/v2/app/carriers"
DEFAULT_DOCUMENT_BASE = "https://document.smc3.com/document/v1/app"
DEFAULT_MINOR_VERSION = "1.2"
DEFAULT_WAIT_SECONDS = 30
MAX_CARRIERS_PER_REQUEST = 35

BOOKING_NOT_SUPPORTED = (
	"SMC3 tracking and dispatch are not available for this quote yet."
)


class SMC3CarrierAdapter(BaseCarrierAdapter):
	"""SMC3 Aggregate Pricing v3 connector (POST /pricing/v3/app/aggregate)."""

	def __init__(self, carrier_doc=None):
		super().__init__(carrier_doc)
		self.carrier_doc = carrier_doc or self.carrier
		if not self.carrier_doc and getattr(self.carrier, "name", None):
			self.carrier_doc = self.carrier
		elif not self.carrier_doc and frappe.db.exists("LTL Carrier", "SMC3"):
			self.carrier_doc = frappe.get_doc("LTL Carrier", "SMC3")

		if not self.carrier_doc:
			frappe.throw("SMC3 carrier record (SMC3) not found in LTL Carrier.")

		self.carrier = self.carrier_doc
		self._config = self._parse_notes()
		self.endpoint = self._resolve_endpoint()
		self.token_service = SMC3TokenService(self.carrier_doc, self._config)

	def _parse_notes(self) -> dict:
		raw = (self.carrier_doc.get("notes") or "").strip()
		if not raw.startswith("{"):
			return {}
		try:
			parsed = frappe.parse_json(raw)
			return parsed if isinstance(parsed, dict) else {}
		except Exception:
			return {}

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

	def _resolve_endpoint(self) -> str:
		base = (self.carrier_doc.get("api_base_url") or DEFAULT_ENDPOINT).strip()
		if not base or base.rstrip("/") == "https://pricing.smc3.com":
			return DEFAULT_ENDPOINT
		normalized = base.rstrip("/")
		if normalized.endswith("/pricing/v3/app/aggregate"):
			return normalized
		if "pricing/aggregate" in normalized:
			return normalized + "/"
		if normalized.endswith("/pricing"):
			return f"{normalized}/v3/app/aggregate"
		return DEFAULT_ENDPOINT if "smc3.com" in normalized else normalized

	def _uses_iso_pickup_date(self) -> bool:
		return "/pricing/v3/" in self.endpoint

	def _v1_payload(self, payload: dict) -> dict:
		"""v1 aggregate expects pickupDate as YYYYMMDD, not ISO."""
		body = dict(payload or {})
		transit = dict(body.get("transit") or {})
		pickup = str(transit.get("pickupDate") or "").replace("-", "")
		if pickup:
			transit["pickupDate"] = pickup
			body["transit"] = transit
		return body

	def _wait_seconds(self) -> int:
		# SMC3 documents X-Willing-To-Wait-Seconds as 1–30 (default 10).
		return max(1, min(cint(self._config.get("willing_to_wait_seconds") or DEFAULT_WAIT_SECONDS), 30))

	def get_headers(self) -> dict:
		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"Authorization": f"Bearer {self.token_service.get_token()}",
			"X-Minor-Version": str(self._config.get("minor_version") or DEFAULT_MINOR_VERSION),
			"X-Willing-To-Wait-Seconds": str(self._wait_seconds()),
		}
		demo = self._config.get("demo_instructions")
		if demo is None:
			demo = "PASS"
		if str(demo).strip():
			headers["X-Demo-Instructions"] = str(demo).strip()
		return headers

	def get_rates(self, request: ShipmentRequest) -> list[CarrierRateQuote] | None:
		try:
			self.token_service.get_token()
		except SMC3AuthError as exc:
			self._log("LTL Quote - SMC3 Auth Failure", str(exc))
			return None

		display_carriers = self._display_network_carriers()
		if not display_carriers:
			return [
				self._error_quote(
					"No SMC3 network carriers with Contract/Dynamic pricing are enabled. "
					"Add SCACs on LTL Carrier SMC3."
				)
			]

		harvest = self._sandbox_harvest_row()
		chunk_size = MAX_CARRIERS_PER_REQUEST - (1 if harvest else 0)
		jobs = []
		for i in range(0, len(display_carriers), chunk_size):
			display_batch = display_carriers[i : i + chunk_size]
			request_batch = ([harvest] + display_batch) if harvest else display_batch
			jobs.append((display_batch, self._build_payload(request, request_batch)))
		quotes: list[CarrierRateQuote] = []
		last_error = None
		last_data: dict | None = None

		if len(jobs) == 1:
			batch_quotes, last_error, last_data = self._post_and_parse(*jobs[0])
			quotes.extend(batch_quotes)
		else:
			workers = min(3, len(jobs))
			with ThreadPoolExecutor(max_workers=workers) as executor:
				futures = [
					executor.submit(self._post_and_parse, batch, payload) for batch, payload in jobs
				]
				for future in as_completed(futures):
					batch_quotes, error, data = future.result()
					quotes.extend(batch_quotes)
					if error:
						last_error = error
					if data:
						last_data = data

		if last_error:
			self._log("LTL Quote - SMC3 Rate Failure", last_error)

		if quotes:
			return quotes
		if last_error is None and not last_data:
			return None

		message = last_error or "SMC3 returned no usable carrier quotes."
		if isinstance(last_data, dict):
			top_status = last_data.get("messageStatus") or {}
			message = top_status.get("message") or message
		return [self._error_quote(message, raw_response=last_data or {})]

	def _post_and_parse(
		self, network_carriers: list[dict], payload: dict
	) -> tuple[list[CarrierRateQuote], str | None, dict | None]:
		timeout = self._wait_seconds() + 10
		try:
			response = self._post_aggregate(payload, timeout)
		except SMC3AuthError as exc:
			self._log("LTL Quote - SMC3 Auth Failure", str(exc))
			return [], None, None
		except requests.exceptions.RequestException as e:
			return [], f"SMC3 connection error: {e}", None

		if is_invalid_access_token(response):
			self._log("LTL Quote - SMC3 Auth Failure", self._format_http_error(response))
			return [], None, None

		if response.status_code not in (200, 207):
			return [], self._format_http_error(response), None

		try:
			data = response.json() if response.content else {}
		except ValueError:
			return [], f"SMC3 returned non-JSON response: {response.text[:250]}", None

		return self._parse_response(data, network_carriers), None, data if isinstance(data, dict) else {}

	def _post_aggregate(self, payload: dict, timeout: int, retry_auth: bool = True):
		headers = self.get_headers()
		response = self.token_service.request(
			"POST",
			self.endpoint,
			headers=headers,
			json=payload,
			timeout=timeout,
			retry_auth=retry_auth,
		)
		# v3 is not enabled for every SMC3 token; fall back to the v1 aggregate path.
		if response.status_code == 403 and "/pricing/v3/" in self.endpoint:
			response = self.token_service.request(
				"POST",
				LEGACY_V1_ENDPOINT,
				headers=headers,
				json=self._v1_payload(payload),
				timeout=timeout,
				retry_auth=retry_auth,
			)
		return response

	def book_shipment(self, quote_data: dict) -> dict:
		origin_city, origin_state = resolve_us_location(
			quote_data.get("origin_zip"),
			quote_data.get("origin_city"),
			quote_data.get("origin_state"),
		)
		origin = {
			"city": str(origin_city or quote_data.get("origin_city") or "").strip(),
			"stateProvince": str(origin_state or quote_data.get("origin_state") or "").strip(),
			"postalCode": str(quote_data.get("origin_zip") or "").strip(),
			"country": self._apa_country(quote_data),
		}
		if not origin["postalCode"]:
			frappe.throw("Origin ZIP is required to assign an SMC3 PRO number.")
		scac = self._apa_scac(quote_data)
		is_test = self._apa_is_test(quote_data)
		data = self._assign_pro_number(scac, origin, is_test, dest_zip=quote_data.get("destination_zip"))
		status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
		if str(status.get("status") or "").upper() != "PASS":
			frappe.throw(status.get("message") or "SMC3 PRO assignment failed.")
		pro = str(data.get("proNumber") or data.get("pro_number") or "").strip()
		if not pro:
			frappe.throw("SMC3 APA did not return a PRO number.")
		transaction_id = str(data.get("transactionId") or "").strip()
		dest_city, dest_state = resolve_us_location(
			quote_data.get("destination_zip"),
			quote_data.get("destination_city"),
			quote_data.get("destination_state"),
		)
		shipper = resolve_shipper_context(quote_data=quote_data)
		bol_payload = dict(quote_data or {})
		bol_payload["pro_number"] = pro
		bol_payload["origin_city"] = origin["city"]
		bol_payload["origin_state"] = origin["stateProvince"]
		bol_payload["destination_city"] = dest_city or quote_data.get("destination_city")
		bol_payload["destination_state"] = dest_state or quote_data.get("destination_state")
		bol_payload.setdefault("shipper_name", shipper.get("shipper_name"))
		bol_payload.setdefault("shipper_address", shipper.get("shipper_address"))
		bol_payload.setdefault("consignee_name", shipper.get("consignee_name"))
		bol_payload.setdefault("consignee_address", shipper.get("consignee_address"))
		bol_payload.setdefault("contact_name", shipper.get("contact_name"))
		bol_payload.setdefault("contact_phone", shipper.get("contact_phone"))
		bol_data = self._create_bill_of_lading(scac, bol_payload, is_test)
		bol_status = bol_data.get("messageStatus") if isinstance(bol_data.get("messageStatus"), dict) else {}
		if str(bol_status.get("status") or "").upper() != "PASS":
			frappe.throw(bol_status.get("message") or "SMC3 bill of lading create failed.")
		refs = extract_reference_numbers(bol_data)
		path_pro = self._bol_path_pro(pro, refs)
		put_data = self._update_bill_of_lading(scac, bol_payload, is_test, path_pro)
		put_status = put_data.get("messageStatus") if isinstance(put_data.get("messageStatus"), dict) else {}
		if str(put_status.get("status") or "").upper() != "PASS":
			frappe.throw(put_status.get("message") or "SMC3 bill of lading update failed.")
		put_refs = extract_reference_numbers(put_data)
		scn = put_refs.get("shipment_confirmation") or refs.get("shipment_confirmation") or ""
		document_binary = extract_bol_pdf(put_data) or extract_bol_pdf(bol_data)
		if not document_binary:
			frappe.throw("SMC3 BOL did not return a PDF image.")
		bol_txn = str(put_data.get("transactionId") or bol_data.get("transactionId") or "").strip()
		quoted_scac = str(put_data.get("scac") or bol_data.get("scac") or data.get("scac") or scac).upper()
		png_result = self.get_bol_document_image(
			SimpleNamespace(
				pro_number=pro,
				bol_number=scn,
				bol_scac=quoted_scac,
				bol_shipper_postal_code=bol_payload.get("origin_zip"),
				bol_consignee_postal_code=bol_payload.get("destination_zip"),
				quote_request=bol_payload.get("quote_request"),
				smc3_bol_pro=path_pro,
			),
			quote_data=bol_payload,
			raise_on_empty=False,
		)
		return {
			"status": "booked",
			"pro_number": pro,
			"bol_number": self._unique_bol_number(scn or pro, quote_data),
			"pickup_number": scn or None,
			"carrier_confirmation": bol_txn or transaction_id,
			"quoted_scac": quoted_scac,
			"smc3_bol_pro": path_pro,
			"document_binary": document_binary,
			"document_images": png_result.get("images") or [],
			"document_image": png_result,
			"raw_response": {
				"apa": data,
				"bol": sanitize_bol_log(bol_data),
				"bol_update": sanitize_bol_log(put_data),
				"document": png_result.get("raw_response") or {},
			},
		}

	def update_bill_of_lading(self, shipment, quote_data: dict | None = None) -> dict:
		"""PUT an existing SMC3 BOL by PRO and return a normalized booking-style result."""
		quote_data = quote_data or quote_data_from_shipment(shipment)
		scac = self._apa_scac(quote_data)
		is_test = self._apa_is_test(quote_data)
		pro = self._bol_path_pro(
			quote_data.get("pro_number") or getattr(shipment, "pro_number", None),
			{"pro": quote_data.get("smc3_bol_pro") or getattr(shipment, "smc3_bol_pro", None)},
		)
		if not pro:
			frappe.throw("A PRO number is required to update an SMC3 bill of lading.")
		data = self._update_bill_of_lading(scac, quote_data, is_test, pro)
		status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
		if str(status.get("status") or "").upper() != "PASS":
			frappe.throw(status.get("message") or "SMC3 bill of lading update failed.")
		refs = extract_reference_numbers(data)
		document_binary = extract_bol_pdf(data)
		if not document_binary:
			frappe.throw("SMC3 BOL update did not return a PDF image.")
		return {
			"status": "updated",
			"pro_number": str(getattr(shipment, "pro_number", None) or refs.get("pro") or pro).strip(),
			"bol_number": str(getattr(shipment, "bol_number", None) or refs.get("shipment_confirmation") or "").strip(),
			"pickup_number": refs.get("shipment_confirmation") or getattr(shipment, "pickup_number", None),
			"carrier_confirmation": str(data.get("transactionId") or getattr(shipment, "carrier_confirmation", None) or "").strip(),
			"smc3_bol_pro": pro,
			"document_binary": document_binary,
			"raw_response": sanitize_bol_log(data),
		}

	def delete_bill_of_lading(self, shipment, quote_data: dict | None = None) -> dict:
		"""DELETE an existing SMC3 BOL by PRO. No request body."""
		quote_data = quote_data or quote_data_from_shipment(shipment)
		scac = self._apa_scac(quote_data)
		pro = self._bol_path_pro(
			quote_data.get("pro_number") or getattr(shipment, "pro_number", None),
			{"pro": quote_data.get("smc3_bol_pro") or getattr(shipment, "smc3_bol_pro", None)},
		)
		if not pro:
			frappe.throw("A PRO number is required to cancel an SMC3 bill of lading.")
		data = self._delete_bill_of_lading(scac, quote_data, pro)
		status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
		if str(status.get("status") or "").upper() != "PASS":
			frappe.throw(status.get("message") or "SMC3 bill of lading cancel failed.")
		return {
			"status": "cancelled",
			"transaction_id": str(data.get("transactionId") or "").strip(),
			"scac": str(data.get("scac") or scac).upper(),
			"pro_number": pro,
			"raw_response": data,
		}

	def get_bol_document_image(self, shipment, quote_data: dict | None = None, raise_on_empty: bool = True) -> dict:
		"""GET the SMC3 Document API BOL as PNG pages."""
		quote_data = quote_data or quote_data_from_shipment(shipment)
		scac = self._apa_scac(quote_data)
		assigned_pro = self._document_pro(shipment, quote_data)
		origin_zip, dest_zip = self._document_postal_codes(shipment, quote_data)
		if not assigned_pro:
			if raise_on_empty:
				frappe.throw("A PRO number is required to fetch the SMC3 BOL image.")
			return {"status": "error", "images": [], "message": "A PRO number is required to fetch the SMC3 BOL image."}
		if not origin_zip or not dest_zip:
			if raise_on_empty:
				frappe.throw("Origin and destination postal codes are required to fetch the SMC3 BOL image.")
			return {
				"status": "error",
				"images": [],
				"message": "Origin and destination postal codes are required to fetch the SMC3 BOL image.",
			}

		result = self._fetch_bol_document(scac, assigned_pro, origin_zip, dest_zip, raise_on_error=False)
		images = result.get("images") or []
		used_pro = assigned_pro
		if not images and self._is_sandbox_mode():
			demo_pro = self._sandbox_document_demo_pro()
			if demo_pro and demo_pro != assigned_pro:
				result = self._fetch_bol_document(scac, demo_pro, origin_zip, dest_zip, raise_on_error=False)
				images = result.get("images") or []
				if images:
					used_pro = demo_pro

		if not images:
			if raise_on_empty:
				frappe.throw(result.get("message") or "SMC3 document API did not return a BOL PNG image.")
			return {
				"status": "error",
				"pro_number": assigned_pro,
				"scac": scac,
				"images": [],
				"message": result.get("message") or "SMC3 document API did not return a BOL PNG image.",
				"raw_response": result.get("raw_response") or {},
			}

		return {
			"status": "success",
			"pro_number": str(getattr(shipment, "pro_number", None) or assigned_pro or used_pro).strip(),
			"scac": str(result.get("scac") or scac).upper(),
			"transaction_id": str(result.get("transaction_id") or "").strip(),
			"images": images,
			"raw_response": result.get("raw_response") or {},
		}

	def _fetch_bol_document(
		self,
		scac: str,
		pro: str,
		origin_zip: str,
		dest_zip: str,
		raise_on_error: bool = True,
	) -> dict:
		params = {
			"documentType": "BL",
			"fileType": "PNG",
			"proNumber": pro,
			"originPostalCode": origin_zip,
			"destinationPostalCode": dest_zip,
		}
		url = self._document_url(scac)
		headers = self._apa_headers()
		empty = {
			"status": "error",
			"pro_number": pro,
			"scac": scac,
			"images": [],
			"raw_response": {},
		}
		try:
			response = self.token_service.request(
				"GET", url, headers=headers, params=params, timeout=60
			)
		except SMC3AuthError:
			if raise_on_error:
				frappe.throw(AUTH_USER_MESSAGE)
			empty["message"] = AUTH_USER_MESSAGE
			return empty
		except requests.exceptions.RequestException as exc:
			self._log_apa(
				url, headers, params, str(exc), "Connection Failed", {"postalCode": origin_zip}, dest_zip, method="GET"
			)
			if raise_on_error:
				frappe.throw(f"SMC3 document connection error: {exc}")
			empty["message"] = f"SMC3 document connection error: {exc}"
			return empty

		data = None
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = None

		log_status = "Booked"
		if is_invalid_access_token(response) or response.status_code not in (200, 201, 207):
			log_status = "API Error"
		elif isinstance(data, dict):
			status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
			if str(status.get("status") or "").upper() != "PASS":
				log_status = "API Error"
		self._log_apa(
			url,
			headers,
			params,
			sanitize_bol_log(data) if isinstance(data, dict) else (response.text or "")[:500],
			log_status,
			{"postalCode": origin_zip},
			dest_zip,
			method="GET",
		)

		if is_invalid_access_token(response):
			if raise_on_error:
				frappe.throw(AUTH_USER_MESSAGE)
			empty["message"] = AUTH_USER_MESSAGE
			return empty
		if response.status_code not in (200, 201, 207):
			message = ""
			if isinstance(data, dict):
				status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
				message = str(status.get("message") or "")
			if raise_on_error:
				if message:
					frappe.throw(message)
				frappe.throw(self._format_http_error(response))
			empty["message"] = message or self._format_http_error(response)
			empty["raw_response"] = sanitize_bol_log(data) if isinstance(data, dict) else {}
			return empty
		if data is None or not isinstance(data, dict):
			message = f"SMC3 document returned non-JSON response: {(response.text or '')[:250]}"
			if raise_on_error:
				frappe.throw(message)
			empty["message"] = message
			return empty
		status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
		if str(status.get("status") or "").upper() != "PASS":
			message = status.get("message") or "SMC3 document request failed."
			if raise_on_error:
				frappe.throw(message)
			empty["message"] = message
			empty["raw_response"] = sanitize_bol_log(data)
			return empty

		images = extract_bol_png_images(data)
		return {
			"status": "success" if images else "error",
			"pro_number": str((data.get("referenceNumbers") or {}).get("proNumber") or pro).strip(),
			"scac": str(data.get("scac") or scac).upper(),
			"transaction_id": str(data.get("transactionId") or "").strip(),
			"images": images,
			"message": "" if images else "SMC3 document API did not return a BOL PNG image.",
			"raw_response": sanitize_bol_log(data),
		}

	def _document_pro(self, shipment, quote_data: dict) -> str:
		return str(
			getattr(shipment, "pro_number", None)
			or quote_data.get("pro_number")
			or getattr(shipment, "smc3_bol_pro", None)
			or ""
		).strip()

	def _sandbox_document_demo_pro(self) -> str:
		return str(self._config.get("document_demo_pro") or DEFAULT_DOCUMENT_DEMO_PRO).strip()

	def _document_postal_codes(self, shipment, quote_data: dict) -> tuple[str, str]:
		origin = str(
			getattr(shipment, "bol_shipper_postal_code", None) or quote_data.get("origin_zip") or ""
		).strip()
		dest = str(
			getattr(shipment, "bol_consignee_postal_code", None) or quote_data.get("destination_zip") or ""
		).strip()
		return origin, dest

	def _unique_bol_number(self, candidate: str, quote_data: dict) -> str:
		value = str(candidate or "").strip()
		if not value:
			return value
		if not frappe.db.exists("LTL Shipment", {"bol_number": value}):
			return value
		suffix = str(quote_data.get("quote_request") or "").strip()
		if suffix:
			unique = f"{value}-{suffix}"
			if not frappe.db.exists("LTL Shipment", {"bol_number": unique}):
				return unique
		return f"{value}-{frappe.generate_hash(length=6)}"

	def _apa_scac(self, quote_data: dict) -> str:
		if self._is_sandbox_mode():
			return "SMCA"
		scac = str(quote_data.get("quoted_scac") or quote_data.get("scac") or "").strip().upper()
		if scac and scac not in {"SMC3", "SMC"}:
			return scac
		frappe.throw("This SMC3 quote is missing a network SCAC for PRO assignment.")

	def _apa_is_test(self, quote_data: dict) -> bool:
		if self._is_sandbox_mode():
			return True
		raw = quote_data.get("is_test")
		if isinstance(raw, str):
			return raw.strip().lower() in {"1", "true", "yes", "y"}
		return bool(raw)

	def _apa_country(self, quote_data: dict) -> str:
		country = str(quote_data.get("origin_country") or "USA").strip().upper()
		if country in {"US", "UNITED STATES", "UNITED STATES OF AMERICA"}:
			return "USA"
		return country or "USA"

	def _apa_url(self, scac: str) -> str:
		base = str(self._config.get("apa_base_url") or DEFAULT_APA_BASE).rstrip("/")
		return f"{base}/{str(scac or '').strip().upper()}"

	def _bol_url(self, scac: str, pro: str | None = None) -> str:
		base = str(self._config.get("bol_base_url") or DEFAULT_BOL_BASE).rstrip("/")
		url = f"{base}/{str(scac or '').strip().upper()}"
		pro = str(pro or "").strip()
		if pro:
			return f"{url}/{pro}"
		return url

	def _document_url(self, scac: str) -> str:
		base = str(self._config.get("document_base_url") or DEFAULT_DOCUMENT_BASE).rstrip("/")
		return f"{base}/{str(scac or '').strip().upper()}"

	def _bol_path_pro(self, assigned_pro, refs: dict | None = None) -> str:
		refs = refs or {}
		bol_pro = str(refs.get("pro") or "").strip()
		assigned = str(assigned_pro or "").strip()
		if bol_pro and bol_pro.upper() not in {"SMC3", "SMC", "SMCA"}:
			return bol_pro
		if assigned:
			return assigned
		if self._is_sandbox_mode():
			return "PRO1234"
		return ""

	def _bol_account(self) -> str:
		bill_to = self._config.get("bill_to") if isinstance(self._config.get("bill_to"), dict) else {}
		account = str(
			self._config.get("bol_account")
			or bill_to.get("account")
			or self.carrier_doc.get("account_number")
			or ""
		).strip()
		if account:
			return account
		if self._is_sandbox_mode():
			return DEFAULT_SANDBOX_ACCOUNT
		return self._default_bill_account() or DEFAULT_SANDBOX_ACCOUNT

	def _create_bill_of_lading(self, scac: str, quote_data: dict, is_test: bool, retry_auth: bool = True) -> dict:
		payload = build_bol_payload(quote_data, is_test=is_test, account=self._bol_account(), function="Create")
		return self._send_bill_of_lading("POST", scac, payload, quote_data, retry_auth=retry_auth)

	def _update_bill_of_lading(self, scac: str, quote_data: dict, is_test: bool, pro: str, retry_auth: bool = True) -> dict:
		payload = build_bol_payload(quote_data, is_test=is_test, account=self._bol_account(), function="Create")
		try:
			return self._send_bill_of_lading(
				"PUT", scac, payload, quote_data, pro=pro, retry_auth=retry_auth
			)
		except frappe.ValidationError:
			if self._is_sandbox_mode() and str(pro or "").strip().upper() != "PRO1234":
				frappe.clear_messages()
				return self._send_bill_of_lading(
					"PUT", scac, payload, quote_data, pro="PRO1234", retry_auth=False
				)
			raise

	def _delete_bill_of_lading(self, scac: str, quote_data: dict, pro: str, retry_auth: bool = True) -> dict:
		try:
			return self._send_bol_delete(scac, quote_data, pro, retry_auth=retry_auth)
		except frappe.ValidationError:
			if self._is_sandbox_mode() and str(pro or "").strip().upper() != "PRO1234":
				frappe.clear_messages()
				return self._send_bol_delete(scac, quote_data, "PRO1234", retry_auth=False)
			raise

	def _send_bol_delete(self, scac: str, quote_data: dict, pro: str, retry_auth: bool = True) -> dict:
		url = self._bol_url(scac, pro)
		headers = self._apa_headers()
		origin_zip = str((quote_data or {}).get("origin_zip") or "")
		dest_zip = str((quote_data or {}).get("destination_zip") or "")
		try:
			response = self.token_service.request(
				"DELETE", url, headers=headers, timeout=60, retry_auth=retry_auth
			)
		except SMC3AuthError:
			frappe.throw(AUTH_USER_MESSAGE)
		except requests.exceptions.RequestException as exc:
			self._log_apa(
				url, headers, None, str(exc), "Connection Failed", {"postalCode": origin_zip}, dest_zip, method="DELETE"
			)
			frappe.throw(f"SMC3 bill of lading connection error: {exc}")

		data = None
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = None

		log_status = "Cancelled"
		if is_invalid_access_token(response) or response.status_code not in (200, 201, 204, 207):
			log_status = "API Error"
		elif isinstance(data, dict):
			status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
			if str(status.get("status") or "").upper() != "PASS":
				log_status = "API Error"
		self._log_apa(
			url,
			headers,
			None,
			data if isinstance(data, dict) else (response.text or "")[:500],
			log_status,
			{"postalCode": origin_zip},
			dest_zip,
			method="DELETE",
		)

		if is_invalid_access_token(response):
			frappe.throw(AUTH_USER_MESSAGE)
		if response.status_code not in (200, 201, 204, 207):
			if isinstance(data, dict):
				status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
				if status.get("message"):
					frappe.throw(status.get("message"))
			frappe.throw(self._format_http_error(response))
		if response.status_code == 204:
			return {"messageStatus": {"status": "PASS", "message": "Transaction was successful."}, "scac": scac}
		if data is None:
			frappe.throw(f"SMC3 BOL returned non-JSON response: {response.text[:250]}")
		if not isinstance(data, dict):
			frappe.throw("SMC3 BOL returned an unexpected payload.")
		return data

	def _send_bill_of_lading(
		self,
		method: str,
		scac: str,
		payload: dict,
		quote_data: dict,
		pro: str | None = None,
		retry_auth: bool = True,
		retried_without_pro: bool = False,
	) -> dict:
		url = self._bol_url(scac, pro)
		headers = self._apa_headers()
		origin_zip = str(quote_data.get("origin_zip") or "")
		dest_zip = str(quote_data.get("destination_zip") or "")
		verb = str(method or "POST").upper()
		try:
			response = self.token_service.request(
				verb, url, headers=headers, json=payload, timeout=60, retry_auth=retry_auth
			)
		except SMC3AuthError:
			frappe.throw(AUTH_USER_MESSAGE)
		except requests.exceptions.RequestException as exc:
			self._log_apa(
				url, headers, payload, str(exc), "Connection Failed", {"postalCode": origin_zip}, dest_zip, method=verb
			)
			frappe.throw(f"SMC3 bill of lading connection error: {exc}")

		data = None
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = None

		log_status = "Booked"
		if is_invalid_access_token(response) or response.status_code not in (200, 201, 207):
			log_status = "API Error"
		elif isinstance(data, dict):
			status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
			if str(status.get("status") or "").upper() != "PASS":
				log_status = "API Error"
		self._log_apa(
			url,
			headers,
			payload,
			sanitize_bol_log(data) if isinstance(data, dict) else (response.text or "")[:500],
			log_status,
			{"postalCode": origin_zip},
			dest_zip,
			method=verb,
		)

		if is_invalid_access_token(response):
			frappe.throw(AUTH_USER_MESSAGE)
		if (
			verb == "POST"
			and response.status_code in (400, 422)
			and not retried_without_pro
			and isinstance(payload.get("referenceNumbers"), dict)
		):
			payload = dict(payload)
			payload.pop("referenceNumbers", None)
			return self._send_bill_of_lading(
				verb, scac, payload, quote_data, pro=pro, retry_auth=False, retried_without_pro=True
			)
		if response.status_code not in (200, 201, 207):
			if isinstance(data, dict):
				status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
				if status.get("message"):
					frappe.throw(status.get("message"))
			frappe.throw(self._format_http_error(response))
		if data is None:
			frappe.throw(f"SMC3 BOL returned non-JSON response: {response.text[:250]}")
		if not isinstance(data, dict):
			frappe.throw("SMC3 BOL returned an unexpected payload.")
		return data

	def _assign_pro_number(
		self,
		scac: str,
		origin: dict,
		is_test: bool,
		retry_auth: bool = True,
		dest_zip=None,
	) -> dict:
		payload = {
			"isTest": "true" if is_test else "false",
			"origin": origin,
		}
		url = self._apa_url(scac)
		headers = self._apa_headers()
		try:
			response = self.token_service.request(
				"POST",
				url,
				headers=headers,
				json=payload,
				timeout=self._wait_seconds() + 10,
				retry_auth=retry_auth,
			)
		except SMC3AuthError:
			frappe.throw(AUTH_USER_MESSAGE)
		except requests.exceptions.RequestException as exc:
			self._log_apa(url, headers, payload, str(exc), "Connection Failed", origin, dest_zip)
			frappe.throw(f"SMC3 PRO assignment connection error: {exc}")

		data = None
		try:
			data = response.json() if response.content else {}
		except ValueError:
			data = None

		log_status = "Booked"
		if is_invalid_access_token(response) or response.status_code not in (200, 201, 207):
			log_status = "API Error"
		elif isinstance(data, dict):
			status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
			if str(status.get("status") or "").upper() != "PASS":
				log_status = "API Error"
		self._log_apa(url, headers, payload, data if data is not None else (response.text or ""), log_status, origin, dest_zip)

		if is_invalid_access_token(response):
			frappe.throw(AUTH_USER_MESSAGE)
		if response.status_code not in (200, 201, 207):
			if isinstance(data, dict):
				status = data.get("messageStatus") if isinstance(data.get("messageStatus"), dict) else {}
				if status.get("message"):
					frappe.throw(status.get("message"))
			frappe.throw(self._format_http_error(response))
		if data is None:
			frappe.throw(f"SMC3 APA returned non-JSON response: {response.text[:250]}")
		if not isinstance(data, dict):
			frappe.throw("SMC3 APA returned an unexpected payload.")
		return data

	def _log_apa(self, url, headers, payload, response_payload, status, origin, dest_zip, method: str = "POST") -> None:
		log_carrier_transaction(
			carrier="SMC3",
			method=str(method or "POST").upper(),
			url=url,
			origin=str((origin or {}).get("postalCode") or ""),
			dest=str(dest_zip or ""),
			headers=headers,
			request_body=payload,
			response_text=response_payload,
			status=status,
		)

	def _apa_headers(self) -> dict:
		headers = {
			"Content-Type": "application/json",
			"Accept": "application/json",
			"Authorization": f"Bearer {self.token_service.get_token()}",
		}
		minor = str(self._config.get("minor_version") or DEFAULT_MINOR_VERSION).strip()
		if minor:
			headers["X-Minor-Version"] = minor
		demo = self._config.get("apa_demo_instructions")
		if demo is None and self._is_sandbox_mode():
			demo = self._config.get("demo_instructions") or "PASS"
		if demo is not None and str(demo).strip():
			headers["X-Demo-Instructions"] = str(demo).strip()
		return headers

	def get_tracking(self, pro_number: str) -> list[dict]:
		return []

	def dispatch_shipment(self, shipment_data: dict) -> dict:
		return {"status": "unsupported", "message": BOOKING_NOT_SUPPORTED}

	def _network_carriers(self) -> list[dict]:
		rows = []
		for row in self.carrier_doc.get("smc3_network_carriers") or []:
			if not cint(getattr(row, "enabled", 1)):
				continue
			scac = str(getattr(row, "scac", "") or "").strip().upper()
			if not scac:
				continue
			if not self._row_supports_contract_dynamic(row, scac):
				continue
			rows.append(
				{
					"scac": scac,
					"carrier_label": str(getattr(row, "carrier_label", "") or "").strip(),
					"eva_access_id": str(getattr(row, "eva_access_id", "") or "").strip(),
					"account": str(getattr(row, "account", "") or "").strip(),
				}
			)
		return rows

	def _display_network_carriers(self) -> list[dict]:
		rows = self._network_carriers()
		real = [row for row in rows if not is_sandbox_scac(row["scac"])]
		if real:
			return real
		return [row for row in rows if is_sandbox_scac(row["scac"])]

	def _sandbox_harvest_row(self) -> dict | None:
		"""Include SMCA in sandbox requests so demo PASS rates can be remapped onto real SCACs."""
		display = self._display_network_carriers()
		if not display or is_sandbox_scac(display[0]["scac"]):
			return None
		if not self._is_sandbox_mode():
			return None
		for row in self._network_carriers():
			if is_sandbox_scac(row["scac"]):
				return row
		return {
			"scac": "SMCA",
			"carrier_label": "",
			"eva_access_id": SANDBOX_EVA_ACCESS_ID,
			"account": SANDBOX_BILL_ACCOUNT,
		}

	def _is_sandbox_mode(self) -> bool:
		eva = str(self._config.get("eva_access_id") or "").strip()
		return eva == SANDBOX_EVA_ACCESS_ID or eva.startswith("SANDBOX")

	def _default_bill_account(self) -> str:
		bill_to_defaults = self._config.get("bill_to") if isinstance(self._config.get("bill_to"), dict) else {}
		account = str(bill_to_defaults.get("account") or self.carrier_doc.get("account_number") or "").strip()
		if account:
			return account
		if self._is_sandbox_mode():
			for row in self._network_carriers():
				if is_sandbox_scac(row["scac"]) and row.get("account"):
					return row["account"]
			return SANDBOX_BILL_ACCOUNT
		return ""

	def _pricing_types(self) -> list[str]:
		from ltl_quote.carrier_network.smc3_quote_mapper import _normalize_pricing_types

		return _normalize_pricing_types(self._config.get("pricing_types"))

	def _service_levels(self) -> list[str]:
		from ltl_quote.carrier_network.smc3_quote_mapper import _normalize_service_levels

		return _normalize_service_levels(self._config.get("service_levels"))

	def _row_supports_contract_dynamic(self, row, scac: str) -> bool:
		has_contract = getattr(row, "contract_pricing", None)
		has_dynamic = getattr(row, "dynamic_pricing", None)
		if has_contract is None and has_dynamic is None:
			return supports_contract_dynamic(scac)
		return bool(cint(has_contract) or cint(has_dynamic))

	def _build_payload(self, request: ShipmentRequest, network_carriers: list[dict]) -> dict:
		origin_city, origin_state = resolve_us_location(
			request.origin_zip, request.origin_city, request.origin_state
		)
		dest_city, dest_state = resolve_us_location(
			request.destination_zip, request.destination_city, request.destination_state
		)
		shipper = resolve_shipper_context()
		default_eva = str(self._config.get("eva_access_id") or SANDBOX_EVA_ACCESS_ID)
		bill_to_defaults = self._config.get("bill_to") if isinstance(self._config.get("bill_to"), dict) else {}
		payment_cfg = self._config.get("payment") if isinstance(self._config.get("payment"), dict) else {}
		account_number = self._default_bill_account()
		payment = {
			"terms": (
				payment_cfg.get("terms")
				or getattr(request, "payment_terms", None)
				or "Prepaid"
			),
			"payer": (
				payment_cfg.get("payer")
				or getattr(request, "payment_payer", None)
				or "Shipper"
			),
		}

		carriers = []
		for row in network_carriers:
			bill_account = row["account"] or account_number
			bill_to = {
				"account": bill_account,
				"name": bill_to_defaults.get("name") or shipper["shipper_name"],
				"address": bill_to_defaults.get("address") or shipper["shipper_address"],
				"city": bill_to_defaults.get("city") or origin_city or "",
				"stateProvince": bill_to_defaults.get("stateProvince") or origin_state or "",
				"postalCode": bill_to_defaults.get("postalCode") or request.origin_zip or "",
				"country": bill_to_defaults.get("country") or "USA",
			}
			carriers.append(
				build_carrier_entry(
					row["scac"],
					row["eva_access_id"] or default_eva,
					bill_to,
					payment,
				)
			)

		iso = self._uses_iso_pickup_date()
		pickup = getdate()
		if pickup:
			pickup_date = pickup.strftime("%Y-%m-%d") if iso else pickup.strftime("%Y%m%d")
		else:
			now = datetime.utcnow()
			pickup_date = now.strftime("%Y-%m-%d") if iso else now.strftime("%Y%m%d")

		accessorial_codes = [
			code
			for code in (getattr(request, "accessorial_codes", None) or [])
			if code
		]

		return build_aggregate_payload(
			origin={
				"account": str(self._config.get("origin_account") or ""),
				"name": shipper["shipper_name"],
				"address": shipper["shipper_address"],
				"city": origin_city or "",
				"stateProvince": origin_state or "",
				"postalCode": str(request.origin_zip or ""),
				"country": "USA",
			},
			destination={
				"account": str(self._config.get("destination_account") or ""),
				"name": shipper["consignee_name"],
				"address": shipper["consignee_address"],
				"city": dest_city or "",
				"stateProvince": dest_state or "",
				"postalCode": str(request.destination_zip or ""),
				"country": "USA",
			},
			payment_terms=payment["terms"],
			commodities=self._build_commodities(request),
			carriers=carriers,
			pricing_types=self._pricing_types(),
			service_levels=self._service_levels(),
			pickup_date=pickup_date,
			accessorial_codes=accessorial_codes or None,
			iso_pickup_date=iso,
		)

	def _build_commodities(self, request: ShipmentRequest) -> list[dict]:
		commodities = []
		for item in request.items or []:
			weight = flt(item.get("weight") or 0)
			if weight <= 0:
				continue
			commodities.append(
				{
					"classification": str(
						item.get("classification") or item.get("freight_class") or request.freight_class or ""
					),
					"weight": _as_smc3_number(weight),
					"description": str(item.get("description") or item.get("item_name") or "Freight"),
					"length": _as_smc3_number(item.get("length") or request.length or 0),
					"width": _as_smc3_number(item.get("width") or request.width or 0),
					"height": _as_smc3_number(item.get("height") or request.height or 0),
					"pieces": _as_smc3_number(item.get("qty") or request.pieces or 1),
					"packagingType": _packaging_type(item.get("packaging_type")),
				}
			)
		if commodities:
			return commodities
		return [
			{
				"classification": str(request.freight_class or ""),
				"weight": _as_smc3_number(request.total_weight or 0),
				"description": "Freight",
				"length": _as_smc3_number(request.length or 0),
				"width": _as_smc3_number(request.width or 0),
				"height": _as_smc3_number(request.height or 0),
				"pieces": _as_smc3_number(request.pieces or 1),
				"packagingType": "PAT",
			}
		]

	def _parse_response(self, data: dict, network_carriers: list[dict]) -> list[CarrierRateQuote]:
		labels = {row["scac"]: row["carrier_label"] for row in network_carriers if row.get("carrier_label")}
		requested = [row["scac"] for row in network_carriers if not is_sandbox_scac(row["scac"])]
		mapped = transform_carrier_results(
			data,
			labels,
			requested_scacs=requested,
			is_sandbox=self._is_sandbox_mode(),
		)
		reliability = float(getattr(self.carrier, "reliability_score", None) or 80)
		connector = str(getattr(self.carrier, "name", None) or "SMC3")
		quotes: list[CarrierRateQuote] = []
		for item in mapped:
			name = str(item.get("carrier_name") or "").strip()
			scac = str(item.get("scac") or "").strip().upper()
			if is_sandbox_scac(scac) or is_demo_display_name(name):
				name = carrier_display_name(scac, labels.get(scac))
			if name.upper() in {"SMC3", "SMC", ""} or is_demo_display_name(name) or is_sandbox_scac(scac):
				continue
			quotes.append(
				CarrierRateQuote(
					carrier_code=f"SMC3-{scac or 'NET'}-{item.get('service_level') or 'STND'}-{item.get('pricing_type') or 'RATE'}",
					carrier_name=name,
					total_charge=item["total_charge"],
					transit_days=item["transit_days"],
					linehaul_charge=item.get("linehaul_charge") or 0,
					fuel_surcharge=item.get("fuel_surcharge") or 0,
					accessorial_charge=item.get("accessorial_charge") or 0,
					currency=item.get("currency") or "USD",
					carrier_quote_id=item.get("carrier_quote_id") or "",
					service_level=item["service_level"],
					reliability_score=reliability,
					accessorial_breakdown=item.get("accessorial_breakdown") or {},
					raw_response=item.get("raw_response") or {},
					rate_source=RATE_SOURCE,
					quoted_scac=scac,
					connector_carrier=connector,
					estimated_delivery_date=item.get("estimated_delivery_date"),
				)
			)
		return quotes

	def _error_quote(self, error: str, raw_response: dict | None = None) -> CarrierRateQuote:
		return CarrierRateQuote(
			carrier_code=str(getattr(self.carrier, "carrier_code", None) or "SMC3"),
			carrier_name=str(getattr(self.carrier, "carrier_name", None) or "SMC3"),
			total_charge=0,
			transit_days=0,
			error=error,
			raw_response=raw_response or {},
			rate_source=RATE_SOURCE,
			connector_carrier=str(getattr(self.carrier, "name", None) or "SMC3"),
		)

	def _format_http_error(self, response) -> str:
		if is_invalid_access_token(response):
			return AUTH_USER_MESSAGE
		body = (response.text or "")[:500]
		return f"SMC3 HTTP {response.status_code}: {body or response.reason}"

	def _log(self, title: str, message: str) -> None:
		frappe.log_error(message=message, title=title)


def attach_smc3_bol_to_shipment(shipment, bol_result: dict | None = None) -> dict:
	"""Attach SMC3 BOL PDF (base64) to LTL Shipment and the linked quote request."""
	import base64

	bol_result = bol_result or {}
	shipment_name = shipment.name if hasattr(shipment, "name") else str(shipment)
	document_binary = str(bol_result.get("document_binary") or "").strip()
	bol_number = bol_result.get("bol_number") or ""
	pro_number = bol_result.get("pro_number") or ""
	result = {
		"status": "pending",
		"bol_number": bol_number,
		"pro_number": pro_number,
		"message": "SMC3 BOL created but PDF binary was not returned.",
	}
	updates = {
		"bol_number": bol_number or None,
		"pro_number": pro_number or None,
		"carrier_confirmation": bol_result.get("carrier_confirmation") or None,
		"pickup_number": bol_result.get("pickup_number") or None,
	}
	updates = {k: v for k, v in updates.items() if v}
	if updates:
		frappe.db.set_value("LTL Shipment", shipment_name, updates, update_modified=False)
	if not document_binary:
		frappe.db.commit()
		return result

	raw = document_binary
	if "," in raw and raw.lower().startswith("data:"):
		raw = raw.split(",", 1)[1]
	raw = "".join(raw.split())
	try:
		file_bytes = base64.b64decode(raw)
	except Exception:
		frappe.db.commit()
		result["message"] = "SMC3 document binary was not valid Base64."
		return result
	marker = file_bytes.find(b"%PDF")
	if marker < 0 or len(file_bytes[marker:]) < 100:
		frappe.db.commit()
		result["message"] = "SMC3 document binary was not a usable PDF."
		return result
	file_bytes = file_bytes[marker:]

	try:
		filename = f"SMC3_BOL_{bol_number or pro_number or shipment_name}.pdf"
		file_doc = save_file(
			fname=filename,
			content=file_bytes,
			dt="LTL Shipment",
			dn=shipment_name,
			is_private=0,
			decode=False,
		)
		file_url = file_doc.file_url
		absolute_url = f"{frappe.utils.get_url()}{file_url}"
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
					frappe.log_error(frappe.get_traceback(), "LTL Quote - SMC3 Quote BOL File Link")
			frappe.db.set_value(
				"LTL Quote Request",
				quote_name,
				{
					"bol_number": bol_number or None,
					"pro_number": pro_number or None,
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
		frappe.log_error(frappe.get_traceback(), "LTL Quote - SMC3 BOL Attach Failure")
		frappe.db.commit()
		return {
			"status": "error",
			"bol_number": bol_number,
			"pro_number": pro_number,
			"message": "Failed to attach SMC3 BOL PDF.",
		}


def _decode_png_bytes(raw: str) -> bytes | None:
	import base64

	text = str(raw or "").strip()
	if "," in text and text.lower().startswith("data:"):
		text = text.split(",", 1)[1]
	text = "".join(text.split())
	if not text:
		return None
	try:
		file_bytes = base64.b64decode(text)
	except Exception:
		return None
	if not file_bytes.startswith(b"\x89PNG"):
		return None
	return file_bytes


def attach_smc3_bol_images_to_shipment(shipment, document_result: dict | None = None) -> dict:
	"""Attach SMC3 Document API PNG pages to bol_image and the BOL preview fields."""
	document_result = document_result or {}
	shipment_name = shipment.name if hasattr(shipment, "name") else str(shipment)
	pro_number = str(document_result.get("pro_number") or getattr(shipment, "pro_number", None) or "").strip()
	images = document_result.get("images") or []
	if not isinstance(images, list):
		images = [images] if images else []

	page_urls = []
	first_file_url = ""
	try:
		page_no = 0
		for raw in images:
			file_bytes = _decode_png_bytes(raw)
			if not file_bytes:
				continue
			page_no += 1
			if page_no == 1:
				filename = f"SMC3_BOL_{pro_number or shipment_name}.png"
			else:
				filename = f"SMC3_BOL_{pro_number or shipment_name}_p{page_no}.png"
			file_doc = save_file(
				fname=filename,
				content=file_bytes,
				dt="LTL Shipment",
				dn=shipment_name,
				is_private=0,
				decode=False,
				df="bol_image" if page_no == 1 else None,
			)
			file_url = file_doc.file_url
			page_urls.append(f"{frappe.utils.get_url()}{file_url}")
			if page_no == 1:
				first_file_url = file_url
	except Exception:
		frappe.log_error(frappe.get_traceback(), "LTL Quote - SMC3 BOL PNG Attach Failure")
		return {
			"status": "error",
			"pro_number": pro_number,
			"message": "Failed to attach SMC3 BOL PNG.",
		}

	if not first_file_url:
		return {
			"status": "error",
			"pro_number": pro_number,
			"message": "SMC3 document binary was not a usable PNG.",
		}

	absolute_url = f"{frappe.utils.get_url()}{first_file_url}"
	frappe.db.set_value(
		"LTL Shipment",
		shipment_name,
		{
			"bol_image": first_file_url,
			"bol_document": first_file_url,
			"bol_document_url": absolute_url,
			"bol_document_type": "Bill of Lading",
		},
		update_modified=False,
	)
	quote_name = frappe.db.get_value("LTL Shipment", shipment_name, "quote_request")
	if quote_name:
		existing = frappe.db.exists(
			"File",
			{
				"attached_to_doctype": "LTL Quote Request",
				"attached_to_name": quote_name,
				"file_url": first_file_url,
			},
		)
		if not existing:
			try:
				frappe.get_doc(
					{
						"doctype": "File",
						"file_name": f"SMC3_BOL_{pro_number or shipment_name}.png",
						"file_url": first_file_url,
						"attached_to_doctype": "LTL Quote Request",
						"attached_to_name": quote_name,
						"is_private": 0,
					}
				).insert(ignore_permissions=True)
			except Exception:
				frappe.log_error(frappe.get_traceback(), "LTL Quote - SMC3 Quote BOL PNG Link")
		frappe.db.set_value(
			"LTL Quote Request",
			quote_name,
			{"bol_document_url": absolute_url},
			update_modified=False,
		)
	frappe.db.commit()
	return {
		"status": "success",
		"pro_number": pro_number,
		"image_url": absolute_url,
		"image_urls": page_urls,
		"page_count": len(page_urls),
		"document_url": absolute_url,
	}


def fetch_smc3_bol_image(shipment_name: str) -> dict:
	"""GET the SMC3 BOL PNG and attach it to the shipment."""
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_SMC3, shipment_connector
	from ltl_quote.carrier_network.registry import get_adapter

	name = str(shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_SMC3:
		frappe.throw("BOL image fetch is only available for SMC3 shipments.")
	if not str(doc.pro_number or "").strip() and not str(doc.bol_number or "").strip():
		frappe.throw("A PRO or BOL number is required to fetch this SMC3 bill of lading image.")

	carrier = frappe.get_doc("LTL Carrier", doc.carrier)
	adapter = get_adapter(carrier)
	result = adapter.get_bol_document_image(doc)
	attached = attach_smc3_bol_images_to_shipment(doc, document_result=result)
	if attached.get("status") != "success":
		frappe.throw(attached.get("message") or "Failed to attach SMC3 BOL PNG.")
	return {
		"status": "success",
		"shipment": doc.name,
		"pro_number": attached.get("pro_number") or result.get("pro_number") or doc.pro_number or "",
		"scac": result.get("scac") or "",
		"transaction_id": result.get("transaction_id") or "",
		"image_url": attached.get("image_url") or "",
		"image_urls": attached.get("image_urls") or [],
		"page_count": attached.get("page_count") or 0,
	}


def cancel_smc3_shipment_bol(shipment_name: str) -> dict:
	"""DELETE the SMC3 BOL for a booked shipment and mark it Cancelled."""
	from ltl_quote.carrier_network.carrier_identity import CONNECTOR_SMC3, shipment_connector
	from ltl_quote.carrier_network.registry import get_adapter

	name = str(shipment_name or "").strip()
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw("A valid shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)
	if shipment_connector(doc) != CONNECTOR_SMC3:
		frappe.throw("BOL cancellation is only available for SMC3 shipments.")
	if str(doc.status or "") == "Cancelled":
		frappe.throw("This shipment is already cancelled.")
	if str(doc.status or "") not in {"Booked", "Dispatched", "In Transit", "Out for Delivery", "Exception"}:
		frappe.throw("Only a booked SMC3 shipment can cancel its bill of lading.")
	if not str(doc.pro_number or "").strip() and not str(doc.bol_number or "").strip():
		frappe.throw("A PRO or BOL number is required to cancel this SMC3 bill of lading.")

	carrier = frappe.get_doc("LTL Carrier", doc.carrier)
	adapter = get_adapter(carrier)
	result = adapter.delete_bill_of_lading(doc)

	doc.status = "Cancelled"
	if hasattr(doc, "current_status"):
		doc.current_status = "Cancelled"
	doc.save(ignore_permissions=True)

	quote_name = str(doc.quote_request or "").strip()
	if quote_name and frappe.db.exists("LTL Quote Request", quote_name):
		frappe.db.set_value("LTL Quote Request", quote_name, "status", "Cancelled")

	frappe.db.commit()
	return {
		"status": "success",
		"shipment": doc.name,
		"transaction_id": result.get("transaction_id") or "",
		"scac": result.get("scac") or "",
		"pro_number": result.get("pro_number") or doc.pro_number or "",
	}


def _as_smc3_number(value) -> str:
	number = flt(value or 0)
	if number == int(number):
		return str(int(number))
	return f"{number:.2f}".rstrip("0").rstrip(".")


def _packaging_type(value) -> str:
	raw = str(value or "").strip().upper()
	if raw in {"PAT", "PLT", "PALLET", "PALLETS"}:
		return "PAT"
	if raw in {"CTN", "CARTON", "BOX", "BOXES"}:
		return "CTN"
	if raw in {"DRM", "DRUM", "DRUMS"}:
		return "DRM"
	if raw in {"PCS", "PIECE", "PIECES"}:
		return "PCS"
	return "PAT"
