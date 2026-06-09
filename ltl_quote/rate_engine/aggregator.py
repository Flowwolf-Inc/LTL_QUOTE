# Copyright (c) 2026, LTL Quote and contributors
# For license information, please see license.txt

import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import frappe
from frappe.utils import add_days, getdate, now_datetime

from ltl_quote.carrier_network.adapters.base import ShipmentRequest, CarrierRateQuote
from ltl_quote.carrier_network.registry import get_adapter, get_enabled_carriers
from ltl_quote.decision_engine.recommender import DecisionEngine
from ltl_quote.utils.currency import get_quote_currency


class RateAggregator:
	"""Core rate aggregation — fetch LTL rates from multiple carriers in parallel."""

	def __init__(self, quote_request):
		self.doc = quote_request
		self.settings = frappe.get_single("LTL Platform Settings")

	def aggregate(self, timeout: int | None = None) -> dict:
		self.doc.status = "Aggregating"
		self.doc.carrier_quotes = []
		self.doc.save(ignore_permissions=True)
		frappe.db.commit()

		request = self._build_shipment_request()
		carriers = get_enabled_carriers()
		quotes = []
		errors = []

		site = frappe.local.site
		user = frappe.session.user

		max_workers = min(
			self.settings.parallel_carrier_requests or 10,
			len(carriers) or 1,
		)
		deadline = timeout or self.settings.rate_request_timeout_seconds or 30

		with ThreadPoolExecutor(max_workers=max_workers) as executor:
			futures = {
				executor.submit(
					self._fetch_carrier_rate,
					carrier.name,
					request,
					site,
					user,
				): carrier
				for carrier in carriers
			}
			try:
				completed = as_completed(futures, timeout=deadline)
				for future in completed:
					carrier = futures[future]
					try:
						quote = future.result()
						if not quote or not hasattr(quote, "total_charge"):
							errors.append(
								{
									"carrier": carrier.carrier_name,
									"error": "Carrier returned no quote object",
								}
							)
						elif quote.error:
							errors.append({"carrier": carrier.carrier_name, "error": quote.error})
						elif quote.total_charge is None:
							errors.append(
								{
									"carrier": carrier.carrier_name,
									"error": "Carrier quote missing total_charge",
								}
							)
						else:
							quotes.append(quote)
					except Exception as e:
						frappe.log_error(title=f"LTL Rate Error: {carrier.carrier_code}")
						errors.append({"carrier": carrier.carrier_name, "error": str(e)})
			except TimeoutError:
				for future, carrier in futures.items():
					if not future.done():
						errors.append({"carrier": carrier.carrier_name, "error": "API response timeout"})

		self._apply_quotes_to_doc(quotes)
		recommendations = DecisionEngine(quotes, self.settings).compute()
		self._apply_recommendations(recommendations)

		self.doc.status = "Quoted" if quotes else "Error"
		self.doc.aggregated_on = now_datetime()
		if errors:
			if isinstance(errors[0], dict):
				self.doc.error_log = "\n".join(f"{item['carrier']}: {item['error']}" for item in errors)
			else:
				self.doc.error_log = "\n".join(errors)
		self.doc.save(ignore_permissions=True)
		frappe.db.commit()

		return {
			"quotes_received": len(quotes),
			"errors": errors,
			"carriers_pinged": len(carriers),
			"raw_quotes": quotes,
			"recommendations": {
				"cheapest": recommendations.get("cheapest_label"),
				"fastest": recommendations.get("fastest_label"),
				"best_value": recommendations.get("best_value_label"),
			},
		}

	def _build_shipment_request(self) -> ShipmentRequest:
		accessorial_codes = [
			row.accessorial_code or frappe.db.get_value("LTL Accessorial", row.accessorial, "accessorial_code")
			for row in (self.doc.accessorials or [])
		]
		return ShipmentRequest(
			origin_zip=self.doc.origin_zip,
			destination_zip=self.doc.destination_zip,
			total_weight=float(str(self.doc.total_weight or 0).replace(",", "")),
			freight_class=str(self.doc.freight_class or "").replace(",", ""),
			length=float(self.doc.length or 0),
			width=float(self.doc.width or 0),
			height=float(self.doc.height or 0),
			pieces=int(float(str(self.doc.pieces or 1).replace(",", ""))),
			accessorial_codes=[c for c in accessorial_codes if c],
			origin_city=self.doc.origin_city or "",
			origin_state=self.doc.origin_state or "",
			destination_city=self.doc.destination_city or "",
			destination_state=self.doc.destination_state or "",
		)

	def _fetch_carrier_rate(self, carrier_name: str, request: ShipmentRequest, site: str, user: str):
		"""Fetch a single carrier rate in a worker thread (requires its own Frappe context)."""
		try:
			frappe.init(site=site, force=True)
			frappe.connect(set_admin_as_user=False)
			if user:
				frappe.set_user(user)

			carrier = frappe.get_doc("LTL Carrier", carrier_name)
			adapter = get_adapter(carrier)
			quote = adapter.get_rates(request)
			if not quote:
				return CarrierRateQuote(
					carrier_code=carrier.carrier_code,
					carrier_name=carrier.carrier_name,
					total_charge=0,
					transit_days=0,
					error="Carrier returned no quote object",
				)
			return quote
		except Exception as e:
			frappe.log_error(message=str(e), title=f"LTL Rate Error: {carrier_name}")

			carrier_name_label = carrier_name
			carrier_code = carrier_name
			if frappe.db.exists("LTL Carrier", carrier_name):
				carrier_name_label = frappe.db.get_value("LTL Carrier", carrier_name, "carrier_name")
				carrier_code = frappe.db.get_value("LTL Carrier", carrier_name, "carrier_code")

			return CarrierRateQuote(
				carrier_code=carrier_code or carrier_name,
				carrier_name=carrier_name_label or carrier_name,
				total_charge=0,
				transit_days=0,
				error=str(e),
			)
		finally:
			frappe.destroy()

	def _apply_quotes_to_doc(self, quotes):
		quote_currency = get_quote_currency()
		self.doc.carrier_quotes = []
		for q in sorted(quotes, key=lambda x: x.total_charge):
			est_delivery = add_days(getdate(), q.transit_days) if q.transit_days else None
			self.doc.append(
				"carrier_quotes",
				{
					"carrier": q.carrier_code,
					"carrier_name": q.carrier_name,
					"carrier_quote_id": q.carrier_quote_id,
					"status": "Received",
					"total_charge": q.total_charge,
					"currency": q.currency or quote_currency,
					"transit_days": q.transit_days,
					"estimated_delivery_date": est_delivery,
					"linehaul_charge": q.linehaul_charge,
					"fuel_surcharge": q.fuel_surcharge,
					"accessorial_charge": q.accessorial_charge,
					"reliability_score": q.reliability_score,
					"service_level": q.service_level,
					"accessorial_breakdown": json.dumps(q.accessorial_breakdown) if q.accessorial_breakdown else None,
					"raw_response": json.dumps(q.raw_response, indent=2) if q.raw_response else None,
				},
			)

	def _apply_recommendations(self, recommendations: dict):
		self.doc.recommended_cheapest = recommendations.get("cheapest_label", "")
		self.doc.recommended_fastest = recommendations.get("fastest_label", "")
		self.doc.recommended_best_value = recommendations.get("best_value_label", "")
