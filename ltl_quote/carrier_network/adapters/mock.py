import hashlib
import json
import random
from datetime import timedelta

import frappe
from frappe.utils import add_days, now_datetime

from ltl_quote.api.payload import freight_class_lookup_key
from ltl_quote.carrier_network.adapters.base import BaseCarrierAdapter, CarrierRateQuote, ShipmentRequest

MOCK_CARRIER_PROFILES = {
	"XPO": {"base_multiplier": 1.0, "transit_base": 4, "reliability": 92},
	"ODFL": {"base_multiplier": 0.95, "transit_base": 3, "reliability": 94},
	"SAIA": {"base_multiplier": 1.05, "transit_base": 5, "reliability": 88},
	"ESTES": {"base_multiplier": 0.98, "transit_base": 4, "reliability": 90},
	"FEDEX_FREIGHT": {"base_multiplier": 1.12, "transit_base": 3, "reliability": 91},
	"UPS_FREIGHT": {"base_multiplier": 1.08, "transit_base": 4, "reliability": 93},
}


CLASS_MULTIPLIERS = {
	"50": 0.7,
	"55": 0.75,
	"60": 0.78,
	"65": 0.82,
	"70": 0.85,
	"77.5": 0.92,
	"85": 1.0,
	"92.5": 1.08,
	"100": 1.15,
	"110": 1.22,
	"125": 1.3,
	"150": 1.4,
	"175": 1.5,
	"200": 1.6,
	"250": 1.75,
	"300": 1.9,
	"400": 2.1,
	"500": 2.3,
}


class MockCarrierAdapter(BaseCarrierAdapter):
	"""Simulates carrier API responses for development and demos."""

	def get_rates(self, request: ShipmentRequest) -> CarrierRateQuote:
		code = self.carrier_code.upper().replace(" ", "_")
		profile = MOCK_CARRIER_PROFILES.get(
			code,
			{"base_multiplier": 1.0, "transit_base": 5, "reliability": 85},
		)

		seed = int(
			hashlib.md5(f"{code}:{request.origin_zip}:{request.destination_zip}:{request.total_weight}".encode()).hexdigest(),
			16,
		) % (2**32)
		rng = random.Random(seed)

		base_rate = self._calculate_base_rate(request, rng) * profile["base_multiplier"]
		fuel = round(base_rate * 0.18, 2)
		accessorial = self._calculate_accessorials(request, rng)
		linehaul = round(base_rate - fuel * 0.5, 2)
		total = round(linehaul + fuel + accessorial, 2)
		transit = profile["transit_base"] + rng.randint(0, 2)

		return CarrierRateQuote(
			carrier_code=self.carrier_code,
			carrier_name=self.carrier.carrier_name,
			total_charge=total,
			transit_days=transit,
			linehaul_charge=linehaul,
			fuel_surcharge=fuel,
			accessorial_charge=accessorial,
			currency="USD",
			carrier_quote_id=f"MQ-{code}-{rng.randint(100000, 999999)}",
			service_level="Standard LTL",
			reliability_score=profile.get("reliability", self.carrier.reliability_score or 85),
			accessorial_breakdown=self._accessorial_breakdown(request),
			raw_response={
				"mock": True,
				"carrier": code,
				"lane": f"{request.origin_zip} -> {request.destination_zip}",
			},
		)

	def _calculate_base_rate(self, request: ShipmentRequest, rng: random.Random) -> float:
		weight_factor = request.total_weight * 0.12
		class_key = freight_class_lookup_key(request.freight_class)
		class_multiplier = CLASS_MULTIPLIERS.get(class_key, 1.0)
		distance_factor = 150 + rng.randint(50, 400)
		return round((weight_factor * class_multiplier + distance_factor) * rng.uniform(0.95, 1.08), 2)

	def _calculate_accessorials(self, request: ShipmentRequest, rng: random.Random) -> float:
		charges = {
			"LIFTGATE": 75,
			"RESIDENTIAL": 65,
			"APPOINTMENT": 45,
			"HAZMAT": 120,
			"INSIDE_DELIVERY": 85,
			"LIMITED_ACCESS": 55,
		}
		total = 0.0
		for code in request.expanded_accessorial_codes():
			total += charges.get(code, 50) * rng.uniform(0.9, 1.1)
		return round(total, 2)

	def _accessorial_breakdown(self, request: ShipmentRequest) -> dict[str, float]:
		charges = {
			"LIFTGATE": 75,
			"RESIDENTIAL": 65,
			"APPOINTMENT": 45,
			"INSIDE_DELIVERY": 85,
			"LIMITED_ACCESS": 55,
		}
		breakdown: dict[str, float] = {}
		for code in request.expanded_accessorial_codes():
			breakdown[code] = breakdown.get(code, 0) + charges.get(code, 50)
		return breakdown

	def book_shipment(self, quote_data: dict) -> dict:
		pro = f"PRO{frappe.generate_hash(length=8).upper()}"
		bol = f"BOL-{self.carrier_code}-{frappe.generate_hash(length=6).upper()}"
		return {
			"status": "booked",
			"pro_number": pro,
			"bol_number": bol,
			"carrier_confirmation": f"CNF-{frappe.generate_hash(length=6)}",
			"estimated_delivery": add_days(now_datetime(), quote_data.get("transit_days", 5)),
		}

	def get_tracking(self, pro_number: str) -> list[dict]:
		now = now_datetime()
		return [
			{
				"event_datetime": add_days(now, -2),
				"status_code": "PICKED_UP",
				"status_description": "Picked up from shipper",
				"location": "Origin Terminal",
				"is_exception": 0,
			},
			{
				"event_datetime": add_days(now, -1),
				"status_code": "IN_TRANSIT",
				"status_description": "In transit to destination hub",
				"location": "Regional Hub",
				"is_exception": 0,
			},
			{
				"event_datetime": now,
				"status_code": "OUT_FOR_DELIVERY",
				"status_description": "Out for delivery",
				"location": "Destination Terminal",
				"is_exception": 0,
			},
		]

	def dispatch_shipment(self, shipment_data: dict) -> dict:
		return {"status": "acknowledged", "dispatch_id": f"DSP-{frappe.generate_hash(length=6)}"}
