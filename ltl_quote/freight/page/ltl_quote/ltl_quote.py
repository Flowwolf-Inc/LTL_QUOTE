import frappe

# Curated shipper-facing accessorials grouped by stage. Each internal code is
# validated against the LTL Accessorial master before being shown in the UI.
STANDARD_ACCESSORIALS = {
	"pickup": [
		("LIFTGATE", "Liftgate Pickup"),
		("INSIDE_DELIVERY", "Inside Pickup"),
	],
	"delivery": [
		("LIFTGATE", "Liftgate Delivery"),
		("INSIDE_DELIVERY", "Inside Delivery"),
		("RESIDENTIAL", "Residential Delivery"),
		("APPOINTMENT", "Notify Before Delivery"),
	],
	"load": [
		("LIMITED_ACCESS", "Limited Access"),
		("HAZMAT", "Hazmat Handling"),
		("APPOINTMENT", "Delivery Appointment"),
	],
}

# Dayton catalog codes already covered by curated checkboxes (avoid duplicate picks).
CURATED_DAYTON_EXCLUSIONS = {
	"pickup": {"LFTP", "LIFTPU", "IPU", "IPC"},
	"delivery": {"LIFT", "IDC", "RESID", "NOT", "RES"},
}


@frappe.whitelist()
def get_accessorial_options() -> dict:
	"""Return curated accessorials grouped for origin (pickup), destination
	(delivery), and load-based sections, sourced from LTL Accessorial master data."""
	# Map of code -> master name for the codes we care about (single query).
	codes = {code for group in STANDARD_ACCESSORIALS.values() for code, _ in group}
	rows = frappe.get_all(
		"LTL Accessorial",
		filters={"accessorial_code": ["in", list(codes)]},
		fields=["accessorial_code", "accessorial_name"],
	)
	available = {r.accessorial_code: r.accessorial_name for r in rows}

	result: dict[str, list[dict]] = {}
	for group, entries in STANDARD_ACCESSORIALS.items():
		result[group] = [
			{"code": code, "label": label, "master_name": available.get(code) or label}
			for code, label in entries
			if code in available
		]
	return result


@frappe.whitelist()
def get_dayton_accessorial_extras(side: str = "pickup") -> list[dict]:
	"""Unique Dayton catalog options for Origin (pickup) / Destination (delivery) extras.

	Excludes codes already covered by curated checkboxes so the form stays clear.
	"""
	from ltl_quote.api.shipping import get_dayton_accessorial_options

	side_key = str(side or "").strip().lower()
	if side_key in {"origin", "pickup"}:
		group_filter = "Pickup"
		exclude = CURATED_DAYTON_EXCLUSIONS["pickup"]
		side_key = "pickup"
	elif side_key in {"destination", "delivery"}:
		group_filter = "Delivery"
		exclude = CURATED_DAYTON_EXCLUSIONS["delivery"]
		side_key = "delivery"
	else:
		return []

	options = get_dayton_accessorial_options(group=group_filter, unique_codes=1)
	return [
		{
			"code": row["code"],
			"label": row.get("description") or row["code"],
			"description": row.get("description") or row["code"],
			"service_group": side_key,
		}
		for row in options
		if row.get("code") and str(row["code"]).strip().upper() not in exclude
	]


@frappe.whitelist()
def get_recent_quote_requests(limit: int = 10, origin_zip: str = None, destination_zip: str = None) -> list[dict]:
	"""Return the most recent LTL Quote Requests, optionally filtered by the
	origin and/or destination ZIP the user has entered on the dashboard."""
	filters = {}
	if origin_zip and str(origin_zip).strip():
		filters["origin_zip"] = str(origin_zip).strip()
	if destination_zip and str(destination_zip).strip():
		filters["destination_zip"] = str(destination_zip).strip()

	rows = frappe.get_list(
		"LTL Quote Request",
		filters=filters,
		fields=[
			"name",
			"origin_city",
			"origin_state",
			"origin_zip",
			"destination_city",
			"destination_state",
			"destination_zip",
			"total_weight",
			"creation",
			"status",
			"final_carrier",
			"final_charge",
		],
		order_by="creation desc",
		limit_page_length=int(limit or 10),
		ignore_permissions=False,
	)

	carrier_codes = {r.final_carrier for r in rows if r.get("final_carrier")}
	name_by_code = {}
	if carrier_codes:
		for carrier in frappe.get_all(
			"LTL Carrier",
			filters={"name": ("in", list(carrier_codes))},
			fields=["name", "carrier_name"],
		):
			name_by_code[carrier.name] = carrier.carrier_name

	for row in rows:
		code = row.get("final_carrier")
		row["carrier_name"] = name_by_code.get(code) if code else None

	return rows


@frappe.whitelist()
def get_quote_request_detail(name: str) -> dict:
	"""Return a quote request with accessorials and linked shipment for the themed detail view."""
	if not name or not frappe.db.exists("LTL Quote Request", name):
		frappe.throw(f"Quote Request {name} not found.")

	doc = frappe.get_doc("LTL Quote Request", name)
	frappe.has_permission("LTL Quote Request", "read", doc=doc, throw=True)

	accessorials = []
	for row in doc.accessorials or []:
		label = ""
		if row.accessorial:
			label = frappe.db.get_value("LTL Accessorial", row.accessorial, "accessorial_name") or ""
		accessorials.append(
			{
				"name": row.name,
				"accessorial": row.accessorial,
				"accessorial_code": row.accessorial_code,
				"accessorial_name": label or row.accessorial_code,
				"service_group": getattr(row, "service_group", None) or "",
				"quantity": row.quantity or 1,
			}
		)

	shipments = frappe.get_all(
		"LTL Shipment",
		filters={"quote_request": name},
		fields=[
			"name",
			"status",
			"carrier",
			"carrier_name",
			"bol_number",
			"pro_number",
			"bol_document",
			"bol_document_url",
		],
		order_by="creation desc",
	)

	from ltl_quote.utils.booking import resolve_shipment_bol_url

	bol_url = resolve_shipment_bol_url(
		shipment_name=shipments[0].name if shipments else None,
		quote_request=doc,
	)

	return {
		"doc": doc.as_dict(),
		"accessorials": accessorials,
		"shipments": shipments,
		"bol_url": bol_url,
	}


@frappe.whitelist()
def save_quote_request_detail(name: str, data: str | dict | None = None) -> dict:
	"""Persist editable fields from the themed quote-request detail view."""
	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}

	if not name or not frappe.db.exists("LTL Quote Request", name):
		frappe.throw(f"Quote Request {name} not found.")

	doc = frappe.get_doc("LTL Quote Request", name)
	frappe.has_permission("LTL Quote Request", "write", doc=doc, throw=True)

	if doc.status in ("Booked", "Cancelled"):
		frappe.throw(f"Quote Request {name} is {doc.status} and cannot be edited.")

	editable = (
		"origin_zip",
		"origin_city",
		"origin_state",
		"destination_zip",
		"destination_city",
		"destination_state",
		"shipper_company_name",
		"shipper_address",
		"consignee_company_name",
		"consignee_address",
		"contact_name",
		"contact_phone",
		"origin_contact_email",
		"destination_contact_name",
		"destination_contact_phone",
		"destination_contact_email",
		"total_weight",
		"weight_uom",
		"freight_class",
		"length",
		"width",
		"height",
		"dimension_uom",
		"pieces",
		"stackable",
	)
	for field in editable:
		if field in data:
			doc.set(field, data.get(field))

	doc.save()
	frappe.db.commit()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_shipment_detail(name: str) -> dict:
	"""Return an LTL Shipment with BOL lines, tracking, and quote accessorials for the themed view."""
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw(f"Shipment {name} not found.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)

	line_items = []
	for row in doc.bol_line_items or []:
		line_items.append(
			{
				"name": row.name,
				"idx_line_no": row.idx_line_no,
				"handling_unit_qty": row.handling_unit_qty,
				"handling_unit_type": row.handling_unit_type,
				"package_qty": row.package_qty,
				"package_type": row.package_type,
				"freight_class": row.freight_class,
				"nmfc": row.nmfc,
				"hazmat": row.hazmat,
				"commodity_description": row.commodity_description,
				"weight": row.weight,
				"weight_unit": row.weight_unit,
			}
		)

	tracking_events = []
	for row in doc.tracking_events or []:
		tracking_events.append(
			{
				"name": row.name,
				"event_datetime": row.event_datetime,
				"status_code": row.status_code,
				"status_description": row.status_description,
				"location": row.location,
				"is_exception": row.is_exception,
			}
		)

	accessorials = []
	if doc.quote_request and frappe.db.exists("LTL Quote Request", doc.quote_request):
		quote = frappe.get_doc("LTL Quote Request", doc.quote_request)
		for row in quote.accessorials or []:
			label = ""
			if row.accessorial:
				label = frappe.db.get_value("LTL Accessorial", row.accessorial, "accessorial_name") or ""
			accessorials.append(
				{
					"accessorial": row.accessorial,
					"accessorial_code": row.accessorial_code,
					"accessorial_name": label or row.accessorial_code,
					"service_group": getattr(row, "service_group", None) or "",
					"quantity": row.quantity or 1,
				}
			)

	dayton_documents = None
	pickup = None
	from ltl_quote.carrier_network.carrier_identity import (
		CONNECTOR_ARCBEST,
		CONNECTOR_DAYTON,
		CONNECTOR_TFORCE,
		connector_ui_code,
		shipment_connector,
	)
	from ltl_quote.carrier_network.pickup import shipment_pickup_summary

	connector = shipment_connector(doc)
	if connector == CONNECTOR_DAYTON:
		if doc.pro_number:
			from ltl_quote.carrier_network.adapters.dayton import get_dayton_indexed_documents

			dayton_documents = get_dayton_indexed_documents(doc.pro_number)
		from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
		from ltl_quote.carrier_network.pickup import PICKUP_TERMINAL_STATUSES

		live = bool(doc.pickup_number) and str(doc.pickup_status or "") not in PICKUP_TERMINAL_STATUSES
		adapter = DaytonCarrierAdapter() if live else None
		pickup = shipment_pickup_summary(doc, live=live, adapter=adapter)
	elif connector in {CONNECTOR_TFORCE, CONNECTOR_ARCBEST}:
		pickup = shipment_pickup_summary(doc)

	return {
		"doc": doc.as_dict(),
		"line_items": line_items,
		"tracking_events": tracking_events,
		"accessorials": accessorials,
		"dayton_documents": dayton_documents,
		"pickup": pickup,
		"carrier": connector_ui_code(connector),
		"connector": connector,
	}


@frappe.whitelist()
def refresh_shipment_bol(name: str) -> dict:
	"""Fetch a Dayton BOL from carrier APIs when Images/Search reports it indexed."""
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw(f"Shipment {name} not found.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)

	from ltl_quote.carrier_network.adapters.dayton import _fetch_remote_bol_for_shipment, _is_dayton_shipment

	if not _is_dayton_shipment(doc):
		frappe.throw("BOL refresh is only available for Dayton Freight shipments.")

	return _fetch_remote_bol_for_shipment(doc)


@frappe.whitelist()
def schedule_shipment_pickup(name: str) -> dict:
	"""Schedule a carrier pickup for a booked shipment."""
	from ltl_quote.api.shipping import create_arcbest_pickup, create_dayton_pickup, create_tforce_pickup
	from ltl_quote.carrier_network.carrier_identity import (
		CONNECTOR_ARCBEST,
		CONNECTOR_DAYTON,
		CONNECTOR_TFORCE,
		shipment_connector,
	)

	if not name:
		frappe.throw("Shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	connector = shipment_connector(doc)
	if connector == CONNECTOR_TFORCE:
		return create_tforce_pickup(shipment=name)
	if connector == CONNECTOR_DAYTON:
		return create_dayton_pickup(shipment=name)
	if connector == CONNECTOR_ARCBEST:
		return create_arcbest_pickup(shipment=name)
	frappe.throw("Pickup scheduling is only available for Dayton, TForce, and ArcBest shipments.")


@frappe.whitelist()
def get_shipment_pickup(name: str) -> dict:
	"""Fetch stored or live pickup details for a shipment."""
	from ltl_quote.api.shipping import get_arcbest_pickup, get_dayton_pickup, get_tforce_pickup
	from ltl_quote.carrier_network.carrier_identity import (
		CONNECTOR_ARCBEST,
		CONNECTOR_DAYTON,
		CONNECTOR_TFORCE,
		shipment_connector,
	)

	if not name:
		frappe.throw("Shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	connector = shipment_connector(doc)
	if connector == CONNECTOR_TFORCE:
		return get_tforce_pickup(shipment=name)
	if connector == CONNECTOR_DAYTON:
		return get_dayton_pickup(shipment=name)
	if connector == CONNECTOR_ARCBEST:
		return get_arcbest_pickup(shipment=name)
	frappe.throw("Pickup lookup is only available for Dayton, TForce, and ArcBest shipments.")


@frappe.whitelist()
def update_shipment_pickup(name: str, data: str | dict | None = None) -> dict:
	"""Update a scheduled Dayton pickup window or contacts."""
	from ltl_quote.api.shipping import update_dayton_pickup

	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}
	if not name:
		frappe.throw("Shipment ID is required.")
	return update_dayton_pickup(shipment=name, payload=data)


@frappe.whitelist()
def cancel_shipment_pickup(name: str) -> dict:
	"""Cancel a scheduled pickup."""
	from ltl_quote.api.shipping import cancel_arcbest_pickup, cancel_dayton_pickup, cancel_tforce_pickup
	from ltl_quote.carrier_network.carrier_identity import (
		CONNECTOR_ARCBEST,
		CONNECTOR_DAYTON,
		CONNECTOR_TFORCE,
		shipment_connector,
	)

	if not name:
		frappe.throw("Shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	connector = shipment_connector(doc)
	if connector == CONNECTOR_TFORCE:
		return cancel_tforce_pickup(shipment=name)
	if connector == CONNECTOR_DAYTON:
		return cancel_dayton_pickup(shipment=name)
	if connector == CONNECTOR_ARCBEST:
		return cancel_arcbest_pickup(shipment=name)
	frappe.throw("Pickup cancellation is only available for Dayton, TForce, and ArcBest shipments.")


@frappe.whitelist()
def get_pickup_page_data(name: str) -> dict:
	"""Load shipment context and pickup data for the pickup management page."""
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw(f"Shipment {name} not found.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)

	from ltl_quote.carrier_network.adapters.dayton import DaytonCarrierAdapter
	from ltl_quote.carrier_network.carrier_identity import (
		CONNECTOR_DAYTON,
		connector_ui_code,
		shipment_connector,
		supports_pickup,
	)
	from ltl_quote.carrier_network.pickup import apply_pickup_response_to_shipment, shipment_pickup_summary

	connector = shipment_connector(doc)
	if not supports_pickup(doc):
		frappe.throw("Pickup management is only available for Dayton, TForce, and ArcBest shipments.")

	live_result = {}
	raw_pickup = {}
	if connector == CONNECTOR_DAYTON and doc.pickup_number:
		adapter = DaytonCarrierAdapter()
		live_result = adapter.get_pickup(doc.pickup_number)
		if live_result.get("ok"):
			apply_pickup_response_to_shipment(doc, live_result, save=True)
			doc.reload()
			raw_pickup = live_result.get("raw") or {}

	quote_summary = {}
	if doc.quote_request and frappe.db.exists("LTL Quote Request", doc.quote_request):
		quote = frappe.get_doc("LTL Quote Request", doc.quote_request)
		quote_summary = {
			"origin_zip": quote.origin_zip,
			"destination_zip": quote.destination_zip,
			"origin_city": quote.origin_city,
			"origin_state": quote.origin_state,
			"total_weight": quote.total_weight,
			"pieces": quote.pieces,
		}

	pickup = shipment_pickup_summary(doc)
	items = live_result.get("items") or pickup.get("items") or raw_pickup.get("items") or []

	return {
		"doc": doc.as_dict(),
		"pickup": pickup,
		"items": items,
		"raw": raw_pickup,
		"quote": quote_summary,
		"carrier": connector_ui_code(connector),
		"connector": connector,
	}


@frappe.whitelist()
def update_shipment_pickup_by_psid(name: str, data: str | dict | None = None, psid=None) -> dict:
	"""Update a Dayton pickup shipment line by PSID."""
	from ltl_quote.api.shipping import update_dayton_pickup_by_psid

	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}
	if not name:
		frappe.throw("Shipment ID is required.")
	return update_dayton_pickup_by_psid(shipment=name, payload=data, psid=psid)


def _place_label(city=None, state=None, zip_code=None) -> str:
	city = str(city or "").strip()
	state = str(state or "").strip()
	zip_code = str(zip_code or "").strip()
	city_state = ", ".join(part for part in (city, state) if part)
	if city_state and zip_code:
		return f"{city_state} {zip_code}"
	return city_state or zip_code or "—"


def _parse_location_parts(location: str | None) -> dict:
	value = str(location or "").strip()
	if not value:
		return {"label": "—", "city": "", "state": "", "zip": ""}
	# Expect "City, ST" or "City, ST 12345"
	city = ""
	state = ""
	zip_code = ""
	if "," in value:
		left, right = value.split(",", 1)
		city = left.strip()
		tokens = right.strip().split()
		if tokens:
			state = tokens[0]
			if len(tokens) > 1:
				zip_code = tokens[1]
	else:
		city = value
	return {
		"label": value,
		"city": city,
		"state": state,
		"zip": zip_code,
	}


@frappe.whitelist()
def get_tracking_page_data(name: str, refresh: int | str | None = 1) -> dict:
	"""Load shipment context and live carrier tracking for the tracking dashboard."""
	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw(f"Shipment {name} not found.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)

	from ltl_quote.carrier_network.carrier_identity import (
		connector_label,
		connector_ui_code,
		shipment_connector,
		supports_tracking,
	)
	from ltl_quote.carrier_network.service_centers import attach_service_center_coordinates
	from ltl_quote.carrier_network.tracking import (
		activity_label,
		build_seed_tracking_events,
		build_timeline_milestones,
		flatten_tracking_events,
	)
	from ltl_quote.utils.location import attach_zip_coordinates, resolve_us_location

	if not supports_tracking(doc):
		frappe.throw("Tracking dashboard is only available for Dayton, TForce, and ArcBest shipments.")
	if not str(doc.pro_number or "").strip():
		frappe.throw("This shipment does not have a PRO / tracking number yet.")

	connector = shipment_connector(doc)
	carrier_label = connector_label(connector)

	should_refresh = str(refresh if refresh is not None else "1").strip().lower() not in {
		"0",
		"false",
		"no",
	}
	refresh_result = {}
	if should_refresh:
		from ltl_quote.visibility.tracker import ShipmentTracker

		try:
			raw_refresh = ShipmentTracker(doc).refresh()
			doc.reload()
			event_count = int(raw_refresh.get("events") or 0)
			if event_count:
				refresh_result = {
					"status": "success",
					"message": "Tracking details synchronized successfully.",
					"events": event_count,
					"has_exception": raw_refresh.get("has_exception"),
				}
			else:
				refresh_result = {
					"status": "info",
					"message": f"Waiting for {carrier_label} to scan this PRO. Events appear after pickup is completed and scanned.",
					"events": 0,
				}
		except frappe.ValidationError as exc:
			# Keep the tracking page usable (map + seed events) even when carrier auth fails.
			refresh_result = {"status": "error", "message": str(exc) or f"Could not refresh tracking from {carrier_label}."}
		except Exception:
			frappe.log_error(title="Orange Tracking Refresh Failure", message=frappe.get_traceback())
			refresh_result = {"status": "error", "message": f"Could not refresh tracking from {carrier_label}."}

	quote_summary = {}
	quote = None
	if doc.quote_request and frappe.db.exists("LTL Quote Request", doc.quote_request):
		quote = frappe.get_doc("LTL Quote Request", doc.quote_request)
		origin_city, origin_state = resolve_us_location(
			quote.origin_zip, quote.origin_city, quote.origin_state
		)
		destination_city, destination_state = resolve_us_location(
			quote.destination_zip, quote.destination_city, quote.destination_state
		)
		quote_summary = {
			"name": quote.name,
			"origin_zip": quote.origin_zip,
			"origin_city": origin_city,
			"origin_state": origin_state,
			"destination_zip": quote.destination_zip,
			"destination_city": destination_city,
			"destination_state": destination_state,
			"total_weight": quote.total_weight,
			"pieces": quote.pieces,
			"freight_class": quote.freight_class,
		}

	origin = {
		"city": doc.bol_shipper_city or quote_summary.get("origin_city") or "",
		"state": doc.bol_shipper_state or quote_summary.get("origin_state") or "",
		"zip": doc.bol_shipper_postal_code or quote_summary.get("origin_zip") or "",
	}
	origin["label"] = _place_label(origin["city"], origin["state"], origin["zip"])
	origin = attach_zip_coordinates(origin)

	destination = {
		"city": doc.bol_consignee_city or quote_summary.get("destination_city") or "",
		"state": doc.bol_consignee_state or quote_summary.get("destination_state") or "",
		"zip": doc.bol_consignee_postal_code or quote_summary.get("destination_zip") or "",
	}
	destination["label"] = _place_label(destination["city"], destination["state"], destination["zip"])
	destination = attach_zip_coordinates(destination)

	events = flatten_tracking_events(doc.tracking_events)
	awaiting_carrier_scan = not bool(events)
	if awaiting_carrier_scan:
		events = build_seed_tracking_events(doc, quote)

	event_codes = [row.get("status_code") for row in events if not row.get("is_seed")]
	milestone_key, milestones = build_timeline_milestones(
		shipment_status=doc.status,
		has_quote_request=bool(doc.quote_request),
		event_codes=event_codes,
	)

	latest = next((row for row in events if not row.get("is_seed")), None) or (events[0] if events else {})
	current_location = doc.current_location or latest.get("location") or origin["label"]
	current_stop = _parse_location_parts(current_location)
	if not current_stop.get("city") and origin.get("city"):
		current_stop = {**origin, "label": current_location or origin["label"]}
	# Prefer Dayton terminal coords when the event maps to a service center.
	current_stop = attach_service_center_coordinates(current_stop)
	if current_stop.get("lat") is None or current_stop.get("lng") is None:
		if not current_stop.get("zip"):
			current_stop["zip"] = origin.get("zip") or ""
		current_stop = attach_zip_coordinates(current_stop)

	next_stop = destination
	if milestone_key in {"Requested", "Booked", "PickedUp"}:
		next_stop = destination
	elif milestone_key == "Delivered":
		next_stop = destination
	next_stop = attach_zip_coordinates(
		{
			"label": next_stop.get("label") or destination["label"],
			"city": next_stop.get("city") or destination["city"],
			"state": next_stop.get("state") or destination["state"],
			"zip": next_stop.get("zip") or destination["zip"],
		}
	)

	pickup_when = doc.pickup_date or doc.booked_on
	delivery_when = doc.estimated_delivery_date or doc.eta_predicted or doc.actual_delivery_date
	current_status_label = (
		doc.current_status
		or activity_label(latest.get("status_code"))
		or doc.status
		or "—"
	)

	return {
		"doc": doc.as_dict(),
		"carrier": connector_ui_code(connector),
		"connector": connector,
		"quote": quote_summary,
		"tracking": {
			"events": events,
			"milestone": milestone_key,
			"milestones": milestones,
			"current_status": current_status_label,
			"current_location": current_location,
			"last_tracking_update": doc.last_tracking_update,
			"eta": doc.eta_predicted or doc.estimated_delivery_date,
			"has_exception": bool(doc.has_exception),
			"awaiting_carrier_scan": awaiting_carrier_scan,
			"refresh": refresh_result,
		},
		"route": {
			"origin": origin,
			"destination": destination,
			"current_stop": current_stop,
			"next_stop": next_stop,
		},
		"summary": {
			"status": doc.status,
			"pro": doc.pro_number,
			"carrier_name": doc.carrier_name or doc.carrier,
			"pickup_label": origin["label"],
			"pickup_when": pickup_when,
			"delivery_label": destination["label"],
			"delivery_when": delivery_when,
			"quote_request": doc.quote_request,
		},
	}


@frappe.whitelist()
def refresh_shipment_tracking(name: str) -> dict:
	"""Refresh carrier tracking for a shipment and return updated tracking page payload."""
	from ltl_quote.carrier_network.carrier_identity import connector_label, shipment_connector, supports_tracking
	from ltl_quote.visibility.tracker import ShipmentTracker

	if not name:
		frappe.throw("Shipment ID is required.")
	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "read", doc=doc, throw=True)

	if not supports_tracking(doc):
		frappe.throw("Tracking refresh is only available for Dayton, TForce, and ArcBest shipments.")

	carrier_label = connector_label(shipment_connector(doc))
	try:
		raw = ShipmentTracker(doc).refresh()
		result = {
			"status": "success" if raw.get("events") else "info",
			"message": (
				"Tracking details synchronized successfully."
				if raw.get("events")
				else f"Waiting for {carrier_label} to scan this PRO. Events appear after pickup is completed and scanned."
			),
			"events": raw.get("events") or 0,
			"has_exception": raw.get("has_exception"),
		}
	except frappe.ValidationError as exc:
		result = {"status": "error", "message": str(exc)}
	except Exception:
		frappe.log_error(title=f"{carrier_label} Tracking Refresh Failure", message=frappe.get_traceback())
		result = {"status": "error", "message": f"Could not refresh tracking from {carrier_label}."}

	payload = get_tracking_page_data(name, refresh=0)
	payload["refresh_result"] = result
	return payload


@frappe.whitelist()
def save_shipment_detail(name: str, data: str | dict | None = None) -> dict:
	"""Persist editable fields from the themed shipment detail view."""
	if isinstance(data, str):
		data = frappe.parse_json(data)
	data = data or {}

	if not name or not frappe.db.exists("LTL Shipment", name):
		frappe.throw(f"Shipment {name} not found.")

	doc = frappe.get_doc("LTL Shipment", name)
	frappe.has_permission("LTL Shipment", "write", doc=doc, throw=True)

	if doc.status in ("Delivered", "Cancelled"):
		frappe.throw(f"Shipment {name} is {doc.status} and cannot be edited.")

	editable = (
		"pickup_date",
		"pickup_ready",
		"pickup_close",
		"pickup_comments",
		"estimated_delivery_date",
		"actual_delivery_date",
		"bol_number",
		"pro_number",
		"carrier_confirmation",
		"dispatch_status",
		"current_status",
		"has_exception",
		"bol_document_type",
		"bol_scac",
		"bol_date",
		"bol_page_count",
		"bol_payment_terms",
		"bol_special_instructions",
		"bol_total_quantity",
		"bol_grand_total_weight",
		"bol_shipper_name",
		"bol_shipper_address1",
		"bol_shipper_city",
		"bol_shipper_state",
		"bol_shipper_postal_code",
		"bol_shipper_contact_name",
		"bol_shipper_contact_phone",
		"bol_consignee_name",
		"bol_consignee_address1",
		"bol_consignee_city",
		"bol_consignee_state",
		"bol_consignee_postal_code",
		"bol_consignee_contact_name",
		"bol_consignee_contact_phone",
		"bol_bill_to_name",
		"bol_bill_to_address1",
		"bol_bill_to_city",
		"bol_bill_to_state",
		"bol_bill_to_postal_code",
		"bol_bill_to_contact_name",
		"bol_bill_to_contact_phone",
	)
	for field in editable:
		if field in data:
			doc.set(field, data.get(field))

	doc.save()
	frappe.db.commit()
	return {"name": doc.name, "status": doc.status}
