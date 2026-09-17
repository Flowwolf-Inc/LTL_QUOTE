from frappe.utils import fmt_money

from ltl_quote.carrier_network.adapters.base import CarrierRateQuote
from ltl_quote.utils.currency import get_quote_currency


class DecisionEngine:
	"""
	Evaluates carrier quotes on cost, transit time, and reliability.
	Outputs: Cheapest, Fastest, Best Value (balanced).
	"""

	def __init__(self, quotes: list[CarrierRateQuote], settings=None):
		self.quotes = [q for q in quotes if not q.error and q.total_charge]
		self.settings = settings

	def compute(self) -> dict:
		if not self.quotes:
			return {}

		cheapest = min(self.quotes, key=lambda q: q.total_charge)
		fastest = min(self.quotes, key=lambda q: q.transit_days or 999)
		best_value = self._best_value()

		return {
			"cheapest": cheapest,
			"fastest": fastest,
			"best_value": best_value,
			"cheapest_label": self._format_label(cheapest, "Cheapest"),
			"fastest_label": self._format_label(fastest, "Fastest"),
			"best_value_label": self._format_label(best_value, "Best Value"),
		}

	def _best_value(self) -> CarrierRateQuote:
		cost_w = float(getattr(self.settings, "cost_weight", None) or 0.5)
		transit_w = float(getattr(self.settings, "transit_weight", None) or 0.3)
		rel_w = float(getattr(self.settings, "reliability_weight", None) or 0.2)

		costs = [q.total_charge for q in self.quotes]
		transits = [q.transit_days or 1 for q in self.quotes]
		reliabilities = [q.reliability_score or 80 for q in self.quotes]

		min_cost, max_cost = min(costs), max(costs) or 1
		min_transit, max_transit = min(transits), max(transits) or 1

		def score(q: CarrierRateQuote) -> float:
			cost_norm = (q.total_charge - min_cost) / (max_cost - min_cost + 0.01)
			transit_norm = ((q.transit_days or 1) - min_transit) / (max_transit - min_transit + 0.01)
			rel_norm = 1 - ((q.reliability_score or 80) - min(reliabilities)) / 100
			return cost_w * cost_norm + transit_w * transit_norm + rel_w * rel_norm

		return min(self.quotes, key=score)

	@staticmethod
	def _format_label(quote: CarrierRateQuote, tag: str) -> str:
		currency = quote.currency or get_quote_currency()
		amount = fmt_money(quote.total_charge, currency=currency)
		source = f" · {quote.rate_source}" if getattr(quote, "rate_source", None) else ""
		return f"[{tag}] {quote.carrier_name} — {amount} | {quote.transit_days} days{source}"


def rank_quotes(quotes: list, settings=None) -> list[dict]:
	"""
	Normalize carrier quotes into FLOWWOLF response schema with recommendation tags.
	Accepts CarrierRateQuote objects or dicts from carrier adapters.
	"""
	normalized = [_normalize_quote(quote) for quote in quotes if quote]
	normalized = [quote for quote in normalized if quote.get("total_cost") is not None and not quote.get("error")]

	if not normalized:
		return []

	carrier_quotes = [_to_carrier_rate_quote(quote) for quote in normalized]
	recommendations = DecisionEngine(carrier_quotes, settings).compute()

	tag_map: dict[str, list[str]] = {}
	for tag_key, label in (("cheapest", "Cheapest"), ("fastest", "Fastest"), ("best_value", "Best Value")):
		selected = recommendations.get(tag_key)
		if not selected:
			continue
		code = _quote_tag_key(selected)
		tag_map.setdefault(code, []).append(label)

	ranked = sorted(normalized, key=lambda quote: quote["total_cost"])
	for quote in ranked:
		tags = tag_map.get(_quote_tag_key(quote), [])
		quote["tags"] = tags
		quote["tag"] = tags[0] if tags else None
		if not quote.get("error"):
			quote.pop("error", None)

	return ranked


def _normalize_quote(quote) -> dict:
	if isinstance(quote, CarrierRateQuote):
		service_eligibility = (quote.raw_response or {}).get("serviceEligibilityLookup")
		return {
			"carrier": quote.carrier_name,
			"carrier_name": quote.carrier_name,
			"carrier_code": quote.carrier_code,
			"total_cost": float(quote.total_charge),
			"total_charge": float(quote.total_charge),
			"transit_days": quote.transit_days,
			"currency": quote.currency or get_quote_currency(),
			"linehaul_charge": quote.linehaul_charge,
			"fuel_surcharge": quote.fuel_surcharge,
			"accessorial_charge": quote.accessorial_charge,
			"reliability_score": quote.reliability_score,
			"service_level": quote.service_level,
			"carrier_quote_id": quote.carrier_quote_id,
			"estimated_delivery_date": quote.estimated_delivery_date,
			"service_eligibility": service_eligibility,
			"source": quote.rate_source or None,
			"scac": quote.quoted_scac or None,
			"error": quote.error,
		}

	carrier_name = quote.get("carrier_name") or quote.get("carrier")
	total_charge = float(quote.get("total_charge") or quote.get("total_cost") or 0)
	return {
		"carrier": carrier_name,
		"carrier_name": carrier_name,
		"carrier_code": quote.get("carrier_code") or quote.get("carrier") or quote.get("scac"),
		"total_cost": total_charge,
		"total_charge": total_charge,
		"transit_days": quote.get("transit_days"),
		"currency": quote.get("currency") or get_quote_currency(),
		"linehaul_charge": quote.get("linehaul_charge"),
		"fuel_surcharge": quote.get("fuel_surcharge"),
		"accessorial_charge": quote.get("accessorial_charge"),
		"reliability_score": quote.get("reliability_score"),
		"service_level": quote.get("service_level"),
		"carrier_quote_id": quote.get("carrier_quote_id"),
		"estimated_delivery_date": quote.get("estimated_delivery_date"),
		"service_eligibility": quote.get("service_eligibility"),
		"source": quote.get("source") or quote.get("rate_source"),
		"scac": quote.get("scac") or quote.get("quoted_scac"),
		"error": quote.get("error"),
	}


def _to_carrier_rate_quote(quote: dict) -> CarrierRateQuote:
	return CarrierRateQuote(
		carrier_code=quote["carrier_code"],
		carrier_name=quote["carrier"],
		total_charge=quote["total_cost"],
		transit_days=quote.get("transit_days") or 0,
		linehaul_charge=quote.get("linehaul_charge") or 0,
		fuel_surcharge=quote.get("fuel_surcharge") or 0,
		accessorial_charge=quote.get("accessorial_charge") or 0,
		currency=quote.get("currency") or get_quote_currency(),
		carrier_quote_id=quote.get("carrier_quote_id") or "",
		service_level=quote.get("service_level") or "",
		reliability_score=quote.get("reliability_score") or 0,
		rate_source=quote.get("source") or quote.get("rate_source") or "",
		quoted_scac=quote.get("scac") or quote.get("quoted_scac") or "",
		estimated_delivery_date=quote.get("estimated_delivery_date"),
	)


def _quote_tag_key(quote) -> str:
	if isinstance(quote, CarrierRateQuote):
		return quote.carrier_quote_id or quote.carrier_code
	return quote.get("carrier_quote_id") or quote.get("carrier_code") or ""
