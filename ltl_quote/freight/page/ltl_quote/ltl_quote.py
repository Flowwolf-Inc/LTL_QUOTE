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
def get_recent_quote_requests(limit: int = 10, origin_zip: str = None, destination_zip: str = None) -> list[dict]:
	"""Return the most recent LTL Quote Requests, optionally filtered by the
	origin and/or destination ZIP the user has entered on the dashboard."""
	filters = {}
	if origin_zip and str(origin_zip).strip():
		filters["origin_zip"] = str(origin_zip).strip()
	if destination_zip and str(destination_zip).strip():
		filters["destination_zip"] = str(destination_zip).strip()

	return frappe.get_list(
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
		],
		order_by="creation desc",
		limit_page_length=int(limit or 10),
		ignore_permissions=False,
	)


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
		fields=["name", "status", "bol_number", "pro_number"],
		order_by="creation desc",
	)

	return {
		"doc": doc.as_dict(),
		"accessorials": accessorials,
		"shipments": shipments,
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
					"quantity": row.quantity or 1,
				}
			)

	return {
		"doc": doc.as_dict(),
		"line_items": line_items,
		"tracking_events": tracking_events,
		"accessorials": accessorials,
	}


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
