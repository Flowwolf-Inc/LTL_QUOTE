window.ltl_quote = window.ltl_quote || {};

frappe.pages["ltl-quote"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("LTL Quote"),
		single_column: true,
	});

	// The dashboard renders its own chrome, so hide the default desk page header.
	page.wrapper.find(".page-head").hide();

	wrapper.ltl_dashboard = new ltl_quote.Dashboard(wrapper, page);
};

frappe.pages["ltl-quote"].on_page_show = function (wrapper) {
	document.body.classList.add("ltl-fullscreen");
	const dash = wrapper.ltl_dashboard;
	if (!dash) return;

	const opts = frappe.route_options || {};
	const view = opts.ltl_view;
	const name = opts.ltl_name;
	if (view || name) {
		frappe.route_options = {};
		if (name && (view === "shipment" || view === "shipments")) {
			dash.open_shipment_detail(name);
			return;
		}
		if (name && (view === "quote" || view === "quotes")) {
			dash.open_quote_detail(name);
			return;
		}
		if (view === "quote" || view === "quotes" || view === "shipments" || view === "carriers" || view === "accessorials") {
			dash.body.find(".ltl-nav-item").removeClass("active");
			dash.body.find(`.ltl-nav-item[data-view="${view}"]`).addClass("active");
			dash.show_view(view);
			return;
		}
	}

	dash.load_recent_requests();
};

/** Navigate to the themed LTL Quote page (never the legacy Desk Form/List). */
ltl_quote.open_dashboard = function (opts = {}) {
	frappe.route_options = Object.assign({}, frappe.route_options || {}, {
		ltl_view: opts.view || null,
		ltl_name: opts.name || null,
	});
	frappe.set_route("ltl-quote");
};

frappe.pages["ltl-quote"].on_page_hide = function () {
	document.body.classList.remove("ltl-fullscreen");
};

const FREIGHT_CLASSES = [
	"50", "55", "60", "65", "70", "77.5", "85", "92.5", "100", "110",
	"125", "150", "175", "200", "250", "300", "400", "500",
];

// Mockup checkbox label -> internal LTL Accessorial code sent to the rating API.
const PICKUP_ACCESSORIALS = [
	{ label: "Liftgate Pickup", code: "LIFTGATE" },
	{ label: "Inside Pickup", code: "INSIDE_DELIVERY" },
];
const DELIVERY_ACCESSORIALS = [
	{ label: "Liftgate Delivery", code: "LIFTGATE" },
	{ label: "Inside Delivery", code: "INSIDE_DELIVERY" },
	{ label: "Residential Delivery", code: "RESIDENTIAL" },
	{ label: "Notify Before Delivery", code: "APPOINTMENT" },
];
const LOAD_ACCESSORIALS = [
	{ label: "Limited Access", code: "LIMITED_ACCESS" },
	{ label: "Delivery Appointment", code: "APPOINTMENT" },
];

function new_line_item(overrides = {}) {
	return Object.assign(
		{
			id: `li_${Date.now()}_${Math.floor(Math.random() * 100000)}`,
			item_number: "",
			item_name: "",
			item_id: "",
			rate: "",
			description: "",
			units: "",
			quantity: "",
			packaging_units: "",
			packaging_unit_count: "",
			dimension_units: "",
			length: "",
			width: "",
			height: "",
			volume_units: "",
			volume: "",
			area_units: "",
			area: "",
			weight_units: "",
			weight: "",
			hazmat_class_division: "",
			hazmat_phone: "",
			hazmat_contact_company: "",
			hazmat_contact: "",
			hazmat_number: "",
			hazmat_packaging_group: "",
			hazmat: "",
			hazmat_number_type: "",
			linear_feet: "",
			nmfc_class: "",
			nmfc_number: "",
			pickup_stop_location: "",
			pickup: "",
			drop_stop_location: "",
			drop: "",
		},
		overrides
	);
}

const NAV_SECTIONS = [
	{
		title: "RATE & BOOKING",
		items: [
			{ label: "New Carrier Quote", icon: "fa fa-file-text-o", view: "quote", badge: "+", active: true },
			{ label: "LTL Quote List", icon: "fa fa-list-alt", view: "quotes" },
			{ label: "LTL Shipment", icon: "fa fa-truck", view: "shipments" },
			{ label: "LTL Carrier", icon: "fa fa-users", view: "carriers" },
		],
	},
	{
		title: "SETTINGS",
		items: [
			{ label: "Carrier", icon: "fa fa-building-o", view: "carriers" },
			{ label: "Accessorial", icon: "fa fa-tags", view: "accessorials" },
		],
	},
];

// Themed in-page list views. Each renders inside the dashboard shell.
const LIST_VIEWS = {
	quotes: {
		doctype: "LTL Quote Request",
		title: "LTL Quote List",
		sub: "All rate requests across carriers.",
		icon: "fa fa-list-alt",
		fields: ["name", "origin_city", "origin_state", "origin_zip", "destination_city", "destination_state", "destination_zip", "total_weight", "freight_class", "status", "creation"],
		order_by: "creation desc",
		search: ["name", "origin_zip", "destination_zip", "origin_city", "destination_city"],
		columns: [
			{ label: "Request ID", type: "mono", key: "name" },
			{ label: "Origin", type: "origin" },
			{ label: "Destination", type: "destination" },
			{ label: "Weight (lbs)", type: "num", key: "total_weight" },
			{ label: "Class", key: "freight_class" },
			{ label: "Status", type: "status", key: "status" },
			{ label: "Created On", type: "datetime", key: "creation" },
		],
	},
	shipments: {
		doctype: "LTL Shipment",
		title: "LTL Shipment",
		sub: "Booked shipments and their tracking status.",
		icon: "fa fa-truck",
		fields: ["name", "carrier_name", "carrier", "status", "bol_number", "pro_number", "total_charge", "currency", "transit_days", "booked_on", "creation", "bol_document", "bol_document_url"],
		order_by: "creation desc",
		search: ["name", "carrier_name", "bol_number", "pro_number", "status"],
		columns: [
			{ label: "Shipment ID", type: "mono", key: "name" },
			{ label: "Carrier", type: "text", key: "carrier_name", fallback: "carrier" },
			{ label: "BOL #", key: "bol_number" },
			{ label: "PRO #", key: "pro_number" },
			{ label: "Total Charge", type: "money", key: "total_charge" },
			{ label: "Status", type: "status", key: "status" },
			{ label: "Booked On", type: "datetime", key: "booked_on" },
		],
	},
	carriers: {
		doctype: "LTL Carrier",
		title: "LTL Carrier",
		sub: "Configured carriers and their integrations.",
		icon: "fa fa-users",
		fields: ["name", "carrier_code", "carrier_name", "scac", "connector_type", "reliability_score", "enabled"],
		order_by: "carrier_name asc",
		search: ["carrier_code", "carrier_name", "scac", "connector_type"],
		columns: [
			{ label: "Code", type: "mono", key: "carrier_code" },
			{ label: "Carrier Name", type: "text", key: "carrier_name" },
			{ label: "SCAC", key: "scac" },
			{ label: "Connector", key: "connector_type" },
			{ label: "Reliability", type: "num", key: "reliability_score" },
			{ label: "Enabled", type: "bool", key: "enabled" },
		],
	},
	accessorials: {
		doctype: "LTL Accessorial",
		title: "Accessorial",
		sub: "Accessorial service catalog.",
		icon: "fa fa-tags",
		fields: ["name", "accessorial_code", "accessorial_name", "charge_type", "default_amount", "currency"],
		order_by: "accessorial_name asc",
		search: ["accessorial_code", "accessorial_name", "charge_type"],
		columns: [
			{ label: "Code", type: "mono", key: "accessorial_code" },
			{ label: "Name", type: "text", key: "accessorial_name" },
			{ label: "Charge Type", key: "charge_type" },
			{ label: "Default Amount", type: "money", key: "default_amount" },
		],
	},
};

function resolve_bol_url(row) {
	if (!row) return "";
	const url = String(row.bol_document_url || "").trim();
	if (url) return url;
	const attach = String(row.bol_document || "").trim();
	if (!attach) return "";
	if (attach.startsWith("http")) return attach;
	return window.location.origin + attach;
}

ltl_quote.Dashboard = class Dashboard {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.body = $(page.main).addClass("ltl-dashboard-root");
		this.quote_request_id = null;
		this.quotes = [];
		this.booking_context = null;
		this.quote_request_status = null;
		this.expanded = false;
		this.load_acc_expanded = false;
		this.line_items_expanded = false;
		this.line_items = [];
		this.editing_line_item = null;
		this.editing_line_item_is_new = false;
		this.acc_options = {
			pickup: PICKUP_ACCESSORIALS,
			delivery: DELIVERY_ACCESSORIALS,
			load: LOAD_ACCESSORIALS,
		};
		this.init();
	}

	init() {
		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.get_accessorial_options",
			callback: (r) => {
				if (r.message && (r.message.delivery || []).length) {
					this.acc_options = r.message;
				}
				this.build();
			},
			error: () => this.build(),
		});
	}

	build() {
		this.render();
		this.bind_events();
		this.refresh_line_items_table();
		this.load_recent_requests();
	}

	render() {
		this.body.html(`
			<div class="ltl-dashboard">
				${this.render_sidebar()}
				<div class="ltl-main">
					<div class="ltl-topbar">
						<div class="ltl-breadcrumb">
							<span>LTL Quote</span>
							<span class="sep">&rsaquo;</span>
							<span class="current">New Carrier Quote</span>
						</div>
						<div class="ltl-topbar-right">
							<span class="ltl-bell"><i class="fa fa-bell-o"></i></span>
							<span class="ltl-user">
								<span class="ltl-avatar">${(frappe.session.user_fullname || "U").slice(0, 2).toUpperCase()}</span>
								<span class="ltl-username">${frappe.utils.escape_html(frappe.session.user_fullname || frappe.session.user)}</span>
							</span>
						</div>
					</div>
					<div class="ltl-scroll">
						<div class="ltl-view ltl-view-quote">
							${this.render_form_header()}
							${this.render_shipment_form()}
							<div class="ltl-card ltl-rates-card" style="display:none;">
								<div class="ltl-card-head"><i class="fa fa-refresh"></i> Available Carrier Rates</div>
								<div class="ltl-rates-body"></div>
							</div>
							<div class="ltl-card">
								<div class="ltl-card-head"><i class="fa fa-history"></i> Recent Quote Requests</div>
								<div class="ltl-recent-body"><div class="ltl-empty">Loading…</div></div>
							</div>
						</div>
						<div class="ltl-view ltl-view-list" style="display:none;">
							<div class="ltl-page-head">
								<div class="ltl-page-head-left">
									<span class="ltl-page-icon"><i class="fa fa-list-alt ltl-list-icon"></i></span>
									<div>
										<div class="ltl-page-title ltl-list-title">List</div>
										<div class="ltl-page-sub ltl-list-sub"></div>
									</div>
								</div>
								<div class="ltl-page-head-actions">
									<input type="text" class="ltl-input ltl-list-search" placeholder="Search…" />
									<button class="ltl-btn ltl-btn-primary ltl-list-new"><i class="fa fa-plus"></i> New</button>
								</div>
							</div>
							<div class="ltl-card">
								<div class="ltl-list-body"><div class="ltl-empty">Loading…</div></div>
							</div>
						</div>
						<div class="ltl-view ltl-view-detail" style="display:none;">
							<div class="ltl-detail-body"><div class="ltl-empty">Loading…</div></div>
						</div>
						<div class="ltl-view ltl-view-line-item" style="display:none;">
							<div class="ltl-line-item-edit-body"></div>
						</div>
					</div>
				</div>
			</div>
		`);
	}

	render_sidebar() {
		const sections = NAV_SECTIONS.map((section) => {
			const title = section.title ? `<div class="ltl-nav-title">${section.title}</div>` : "";
			const items = section.items
				.map((item) => {
					const badge = item.badge ? `<span class="ltl-nav-badge">${item.badge}</span>` : "";
					const active = item.active ? "active" : "";
					return `
						<a class="ltl-nav-item ${active}" data-view="${item.view}">
							<span class="ltl-nav-ico"><i class="${item.icon}"></i></span>
							<span class="ltl-nav-label">${item.label}</span>
							${badge}
						</a>`;
				})
				.join("");
			return `<div class="ltl-nav-section">${title}${items}</div>`;
		}).join("");

		return `
			<aside class="ltl-sidebar">
				<div class="ltl-brand">
					<span class="ltl-brand-logo"><i class="fa fa-cube"></i></span>
					<span class="ltl-brand-text"><b>LTL</b><small>Logistics</small></span>
				</div>
				<nav class="ltl-nav">${sections}</nav>
				<div class="ltl-help">
					<div class="ltl-help-title">Need Help?</div>
					<div class="ltl-help-text">Contact our support team for assistance.</div>
					<button class="ltl-help-btn">Contact Support</button>
				</div>
			</aside>`;
	}

	render_form_header() {
		return `
			<div class="ltl-page-head">
				<div class="ltl-page-head-left">
					<span class="ltl-page-icon"><i class="fa fa-file-text-o"></i></span>
					<div>
						<div class="ltl-page-title">New Carrier Quote Request</div>
						<div class="ltl-page-sub">Enter shipment details to get the best LTL rates from our carriers.</div>
					</div>
				</div>
				<div class="ltl-page-head-actions">
					<button class="ltl-btn ltl-btn-light" data-action="clear">Clear All</button>
					<button class="ltl-btn ltl-btn-primary" data-action="fetch"><i class="fa fa-bolt"></i> Fetch Rates</button>
				</div>
			</div>`;
	}

	freight_options() {
		const options = FREIGHT_CLASSES.map((c) => `<option value="${c}">${c}</option>`).join("");
		return `<option value="" selected>Select class</option>${options}`;
	}

	accessorial_boxes(list, group) {
		return list
			.map(
				(a, i) => `
			<label class="ltl-check">
				<input type="checkbox" data-acc="${a.code}" data-group="${group}" data-idx="${i}" />
				<span>${a.label}</span>
			</label>`
			)
			.join("");
	}

	// Compact 4-field view shown while Shipment Details is collapsed.
	render_collapsed_fields() {
		return `
			<div class="ltl-ship-collapsed">
				<div class="ltl-grid ltl-grid-4">
					<div class="ltl-field">
						<label>Origin ZIP</label>
						<input type="text" class="ltl-input" data-field="origin_zip" placeholder="e.g. 60601" />
					</div>
					<div class="ltl-field">
						<label>Destination ZIP</label>
						<input type="text" class="ltl-input" data-field="destination_zip" placeholder="e.g. 75201" />
					</div>
					<div class="ltl-field">
						<label>Total Weight (lbs)</label>
						<input type="number" class="ltl-input" data-field="weight" placeholder="2500" />
					</div>
					<div class="ltl-field">
						<label>Freight Class</label>
						<select class="ltl-input" data-field="freight_class">${this.freight_options()}</select>
					</div>
				</div>
			</div>`;
	}

	// Detailed Origin / Destination card with Details + Accessorials tabs.
	render_od_card(side) {
		const is_origin = side === "origin";
		const title = is_origin ? "Origin" : "Destination";
		const loc_label = is_origin ? "Pickup Location" : "Delivery Location";
		const city_label = is_origin ? "Pickup City" : "Delivery City";
		const date_label = is_origin ? "Pickup Date" : "Delivery Date";
		const hours_label = is_origin ? "Pickup Hours" : "Delivery Hours";
		const pfx = `exp_${side}_`;
		const acc = is_origin ? this.acc_options.pickup : this.acc_options.delivery;
		const star = ' <span class="req">*</span>';
		const date_req = is_origin ? star : "";
		const hours_req = is_origin ? star : "";
		return `
			<div class="ltl-od-card">
				<div class="ltl-od-title">${title}</div>
				<div class="ltl-tabs">
					<span class="ltl-tab active" data-tab-target="${side}-details">Details</span>
					<span class="ltl-tab" data-tab-target="${side}-acc">Accessorials</span>
				</div>
				<div class="ltl-tab-pane" data-tab-pane="${side}-details">
					<div class="ltl-grid ltl-grid-2">
						<div class="ltl-field"><label>Zip</label>
							<input type="text" class="ltl-input" data-field="exp_${side}_zip" placeholder="e.g. ${is_origin ? "60601" : "75201"}" /></div>
						<div class="ltl-field"><label>${loc_label}${star}</label>
							<input type="text" class="ltl-input" data-field="${pfx}location" /></div>
						<div class="ltl-field"><label>Street Address</label>
							<input type="text" class="ltl-input" data-field="${pfx}address" /></div>
						<div class="ltl-field"><label>${city_label}${star}</label>
							<input type="text" class="ltl-input" data-field="${pfx}city" /></div>
						<div class="ltl-field"><label>State${star}</label>
							<input type="text" class="ltl-input" data-field="${pfx}state" /></div>
						<div class="ltl-field"><label>Country${star}</label>
							<input type="text" class="ltl-input" data-field="${pfx}country" placeholder="USA" /></div>
						<div class="ltl-field"><label>${date_label}${date_req}</label>
							<input type="date" class="ltl-input" data-field="${pfx}date" /></div>
						<div class="ltl-field"><label>${hours_label}${hours_req}</label>
							<input type="text" class="ltl-input" data-field="${pfx}hours" placeholder="0800-1700" /></div>
					</div>
					<div class="ltl-grid ltl-grid-2">
						<div class="ltl-field"><label>Contact${star}</label>
							<input type="text" class="ltl-input" data-field="${pfx}contact" /></div>
						<div class="ltl-field"><label>Email</label>
							<input type="email" class="ltl-input" data-field="${pfx}email" placeholder="name@example.com" /></div>
					</div>
				</div>
				<div class="ltl-tab-pane ltl-od-acc-pane" data-tab-pane="${side}-acc" style="display:none;">
					<div class="ltl-od-acc-panel">
						<div class="ltl-acc-grid ltl-acc-grid-od">${this.accessorial_boxes(acc, side)}</div>
					</div>
				</div>
			</div>`;
	}

	// Full detailed view shown while Shipment Details is expanded.
	render_expanded_fields() {
		return `
			<div class="ltl-ship-expanded" style="display:none;">
				<div class="ltl-od-grid">
					${this.render_od_card("origin")}
					${this.render_od_card("destination")}
				</div>
				<div class="ltl-grid ltl-grid-2" style="margin-top:18px;">
					<div class="ltl-field">
						<label>Total Weight (lbs) <span class="req">*</span></label>
						<input type="number" class="ltl-input" data-field="exp_weight" placeholder="2500" />
					</div>
					<div class="ltl-field">
						<label>Freight Class <span class="req">*</span></label>
						<select class="ltl-input" data-field="exp_freight_class">${this.freight_options()}</select>
					</div>
				</div>
				<div class="ltl-collapse-card ltl-load-acc-card" style="margin-top:18px;">
					<div class="ltl-collapse-head" data-action="toggle-load-acc">
						<span><i class="fa fa-chevron-down ltl-chevron"></i> Load Based Accessorials</span>
					</div>
					<div class="ltl-collapse-body" style="display:none;">
						<div class="ltl-acc-grid">${this.accessorial_boxes(this.acc_options.load, "load")}</div>
					</div>
				</div>
				${this.render_line_items_bar()}
			</div>`;
	}

	render_line_items_bar() {
		return `
			<div class="ltl-collapse-card ltl-line-items">
				<div class="ltl-collapse-head ltl-line-items-head" data-action="toggle-line-items">
					<div class="ltl-line-items-head-left">
						<i class="fa fa-chevron-down ltl-chevron"></i>
						<span class="ltl-line-items-icon"><i class="fa fa-list-ul"></i></span>
						<div>
							<div class="ltl-line-items-title">Line Items</div>
							<div class="ltl-line-items-sub">Manage line items for this shipment</div>
						</div>
					</div>
					<button type="button" class="ltl-btn ltl-btn-line-add" data-action="add-line-item">
						<i class="fa fa-plus"></i> Add Line Item
					</button>
				</div>
				<div class="ltl-collapse-body" style="display:none;">
					<div class="ltl-line-items-card">
						<div class="ltl-line-items-card-head">
							<span class="ltl-line-items-card-icon"><i class="fa fa-file-text-o"></i></span>
							<div>
								<div class="ltl-line-items-card-title">Line Items</div>
								<div class="ltl-line-items-count">0 items</div>
							</div>
						</div>
						<div class="ltl-line-items-table-wrap">
							<table class="ltl-table ltl-line-items-table">
								<thead>
									<tr>
										<th class="ltl-li-check"><input type="checkbox" class="ltl-li-select-all" /></th>
										<th class="ltl-li-no">No.</th>
										<th>Item Description <span class="req">*</span></th>
										<th>Item Number</th>
										<th>NMFC Class <span class="req">*</span></th>
										<th>NMFC Number</th>
										<th class="ltl-li-actions"></th>
									</tr>
								</thead>
								<tbody class="ltl-line-items-body"></tbody>
							</table>
						</div>
						<div class="ltl-line-items-footer">
							<span class="ltl-line-items-showing">Showing 0 of 0 items</span>
						</div>
					</div>
				</div>
			</div>`;
	}

	refresh_line_items_table() {
		const body = this.body.find(".ltl-line-items-body");
		if (!body.length) return;
		const items = this.line_items || [];
		if (!items.length) {
			body.html(`<tr><td colspan="7" class="ltl-empty-cell">No line items yet. Click + Add Line Item to begin.</td></tr>`);
		} else {
			body.html(items.map((item, idx) => this.render_line_item_row(idx + 1, item)).join(""));
		}
		const count = items.length;
		const label = count === 1 ? "1 item" : `${count} items`;
		this.body.find(".ltl-line-items-count").text(label);
		this.body.find(".ltl-line-items-showing").text(`Showing ${count} of ${count} item${count === 1 ? "" : "s"}`);
		this.body.find(".ltl-li-select-all").prop("checked", false);
	}

	render_line_item_row(no, data = {}) {
		const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
		const trunc = (v, n = 42) => {
			const s = String(v || "");
			return s.length > n ? `${s.slice(0, n)}…` : s;
		};
		const desc = data.description || data.item_name || "—";
		return `
			<tr class="ltl-line-item-row" data-line-id="${esc(data.id || "")}">
				<td class="ltl-li-check"><input type="checkbox" class="ltl-li-row-check" /></td>
				<td class="ltl-li-no">${no}</td>
				<td title="${esc(desc)}">${esc(trunc(desc))}</td>
				<td>${esc(data.item_number || "—")}</td>
				<td>${esc(data.nmfc_class || "—")}</td>
				<td>${esc(data.nmfc_number || "—")}</td>
				<td class="ltl-li-actions">
					<button type="button" class="ltl-li-icon-btn" data-action="edit-line-item" title="${__("Edit")}">
						<i class="fa fa-pencil"></i>
					</button>
					<button type="button" class="ltl-li-icon-btn ltl-li-remove" data-action="remove-line-item" title="${__("Remove")}">
						<i class="fa fa-trash-o"></i>
					</button>
				</td>
			</tr>`;
	}

	li_input(label, key, opts = {}) {
		const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
		const val = esc((this.editing_line_item && this.editing_line_item[key]) || "");
		const ph = opts.placeholder ? ` placeholder="${esc(opts.placeholder)}"` : "";
		const req = opts.required ? ' <span class="ltl-required" aria-hidden="true">*</span>' : "";
		const req_attr = opts.required ? ' required aria-required="true"' : "";
		if (opts.type === "textarea") {
			return `
				<div class="ltl-field ${opts.className || ""}">
					<label>${label}${req}</label>
					<textarea class="ltl-input" data-li-edit="${key}" rows="${opts.rows || 4}"${ph}${req_attr}>${val}</textarea>
				</div>`;
		}
		if (opts.type === "select") {
			const options = (opts.options || [])
				.map((c) => `<option value="${esc(c)}" ${val === String(c) ? "selected" : ""}>${esc(c)}</option>`)
				.join("");
			return `
				<div class="ltl-field ${opts.className || ""}">
					<label>${label}${req}</label>
					<select class="ltl-input" data-li-edit="${key}"${req_attr}>
						<option value="">Select</option>${options}
					</select>
				</div>`;
		}
		return `
			<div class="ltl-field ${opts.className || ""}">
				<label>${label}${req}</label>
				<input type="${opts.type || "text"}" class="ltl-input" data-li-edit="${key}" value="${val}"${ph}${req_attr} />
			</div>`;
	}

	render_line_item_editor(item) {
		this.editing_line_item = item;
		const nmfc_opts = { type: "select", options: FREIGHT_CLASSES };
		return `
			<div class="ltl-line-item-edit">
				<div class="ltl-line-item-edit-hero">
					<div class="ltl-line-item-edit-hero-left">
						<span class="ltl-line-item-edit-hero-icon"><i class="fa fa-cube"></i></span>
						<div>
							<div class="ltl-line-item-edit-hero-title">Line Item Details</div>
							<div class="ltl-line-item-edit-hero-sub">Edit commodity, dimensions, classification, and locations</div>
						</div>
					</div>
				</div>

				<section class="ltl-li-edit-section">
					<div class="ltl-li-edit-section-head"><i class="fa fa-cube"></i> Item Details</div>
					<div class="ltl-li-edit-grid ltl-li-edit-grid-3">
						<div class="ltl-li-edit-col">
							${this.li_input("Item Number", "item_number")}
							${this.li_input("Item Name", "item_name")}
							${this.li_input("Item ID", "item_id")}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("Rate", "rate", { type: "number", placeholder: "0.00" })}
							${this.li_input("Description", "description", { type: "textarea", rows: 5, required: true })}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("Units", "units")}
							${this.li_input("Quantity", "quantity", { type: "number", required: true })}
							${this.li_input("Packaging Units", "packaging_units")}
							${this.li_input("Packaging Unit Count", "packaging_unit_count", { type: "number" })}
						</div>
					</div>
				</section>

				<section class="ltl-li-edit-section">
					<div class="ltl-li-edit-section-head"><i class="fa fa-arrows-h"></i> Dimensions &amp; Weight</div>
					<div class="ltl-li-edit-grid ltl-li-edit-grid-3">
						<div class="ltl-li-edit-col">
							${this.li_input("Dimension Units", "dimension_units", { placeholder: "IN" })}
							${this.li_input("Length", "length", { type: "number", required: true })}
							${this.li_input("Width", "width", { type: "number", required: true })}
							${this.li_input("Height", "height", { type: "number", required: true })}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("Volume Units", "volume_units")}
							${this.li_input("Volume", "volume", { type: "number" })}
							${this.li_input("Area Units", "area_units")}
							${this.li_input("Area", "area", { type: "number" })}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("Weight Units", "weight_units", { placeholder: "LBS" })}
							${this.li_input("Weight", "weight", { type: "number", required: true })}
						</div>
					</div>
				</section>

				<section class="ltl-li-edit-section">
					<div class="ltl-li-edit-section-head"><i class="fa fa-tag"></i> Commodity &amp; Classification</div>
					<div class="ltl-li-edit-grid ltl-li-edit-grid-3">
						<div class="ltl-li-edit-col">
							${this.li_input("HazMat Class/Division", "hazmat_class_division")}
							${this.li_input("HAZ Mat Phone Number", "hazmat_phone")}
							${this.li_input("HAZ Mat Contact Company", "hazmat_contact_company")}
							${this.li_input("HazMat Contact", "hazmat_contact")}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("HazMat Number", "hazmat_number")}
							${this.li_input("HazMat Packaging Group", "hazmat_packaging_group")}
							${this.li_input("HazMat", "hazmat")}
							${this.li_input("HazMat Number Type", "hazmat_number_type")}
						</div>
						<div class="ltl-li-edit-col">
							${this.li_input("Linear Feet", "linear_feet", { type: "number" })}
							${this.li_input("NMFC Class", "nmfc_class", { ...nmfc_opts, required: true })}
							${this.li_input("NMFC Number", "nmfc_number", { required: true })}
						</div>
					</div>
				</section>

				<section class="ltl-li-edit-section">
					<div class="ltl-li-edit-section-head"><i class="fa fa-map-marker"></i> Locations</div>
					<div class="ltl-li-edit-locations">
						<div class="ltl-li-edit-loc-block">
							<div class="ltl-li-edit-loc-title">Pickup</div>
							<div class="ltl-li-edit-grid ltl-li-edit-grid-2">
								${this.li_input("Pickup Stop Location", "pickup_stop_location")}
								${this.li_input("Pickup", "pickup")}
							</div>
						</div>
						<div class="ltl-li-edit-loc-block">
							<div class="ltl-li-edit-loc-title">Drop</div>
							<div class="ltl-li-edit-grid ltl-li-edit-grid-2">
								${this.li_input("Drop Stop Location", "drop_stop_location")}
								${this.li_input("Drop", "drop")}
							</div>
						</div>
					</div>
				</section>

				<div class="ltl-li-edit-footer">
					<button type="button" class="ltl-btn ltl-btn-line-cancel" data-action="cancel-line-item">Cancel</button>
					<button type="button" class="ltl-btn ltl-btn-primary" data-action="save-line-item">
						Save Item Details
					</button>
				</div>
			</div>`;
	}

	open_line_item_editor(item, is_new) {
		this.editing_line_item = Object.assign({}, item);
		this.editing_line_item_is_new = !!is_new;
		this.body.find(".ltl-line-item-edit-body").html(this.render_line_item_editor(this.editing_line_item));
		this.show_view("line-item");
	}

	close_line_item_editor() {
		this.editing_line_item = null;
		this.editing_line_item_is_new = false;
		this.body.find(".ltl-nav-item").removeClass("active");
		this.body.find('.ltl-nav-item[data-view="quote"]').addClass("active");
		this.show_view("quote");
		if (!this.expanded) this.toggle_shipment();
		else {
			this.body.find(".ltl-ship-card").addClass("expanded");
			this.body.find(".ltl-ship-collapsed").hide();
			this.body.find(".ltl-ship-expanded").show();
		}
		this.refresh_line_items_table();
		this.ensure_line_items_expanded();
		const el = this.body.find(".ltl-line-items")[0];
		if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
	}

	collect_line_item_form() {
		const data = Object.assign({}, this.editing_line_item || new_line_item());
		this.body.find("[data-li-edit]").each(function () {
			const key = $(this).attr("data-li-edit");
			data[key] = ($(this).val() || "").toString().trim();
		});
		return data;
	}

	save_line_item_editor() {
		const data = this.collect_line_item_form();
		const required = [
			{ key: "description", label: __("Description") },
			{ key: "quantity", label: __("Quantity") },
			{ key: "weight", label: __("Weight") },
			{ key: "nmfc_class", label: __("NMFC Class") },
			{ key: "nmfc_number", label: __("NMFC Number") },
			{ key: "length", label: __("Length") },
			{ key: "width", label: __("Width") },
			{ key: "height", label: __("Height") },
		];
		const missing = required.filter((f) => !String(data[f.key] || "").trim());
		if (missing.length) {
			frappe.show_alert(
				{
					message: __("Please fill required fields: {0}", [missing.map((f) => f.label).join(", ")]),
					indicator: "orange",
				},
				6
			);
			const first = missing[0].key;
			const el = this.body.find(`[data-li-edit="${first}"]`);
			if (el.length) {
				el.addClass("ltl-input-invalid").focus();
				setTimeout(() => el.removeClass("ltl-input-invalid"), 2500);
			}
			return;
		}

		if (this.editing_line_item_is_new) {
			this.line_items.push(data);
		} else {
			const idx = this.line_items.findIndex((r) => r.id === data.id);
			if (idx >= 0) this.line_items[idx] = data;
			else this.line_items.push(data);
		}
		frappe.show_alert({ message: __("Item details saved"), indicator: "green" }, 3);
		this.close_line_item_editor();
	}

	collect_line_items() {
		return (this.line_items || [])
			.filter((row) => row.description || row.item_name || row.item_number || row.nmfc_class)
			.map((row) => ({
				...row,
				classification: row.nmfc_class,
				freight_class: row.nmfc_class,
				nmfc: row.nmfc_number,
				qty: parseInt(row.quantity || 1, 10) || 1,
				quantity: parseInt(row.quantity || 1, 10) || 1,
				weight: row.weight || "",
			}));
	}

	require_line_items() {
		const items = this.collect_line_items();
		const valid = items.filter(
			(row) =>
				(row.description || row.item_name) &&
				(row.nmfc_class || row.freight_class) &&
				row.nmfc_number &&
				row.quantity &&
				row.weight &&
				row.length &&
				row.width &&
				row.height
		);
		if (!valid.length) {
			frappe.show_alert(
				{
					message: __(
						"Add at least one complete Line Item (Description, Quantity, Weight, NMFC Class, NMFC Number, Length, Width, Height), then click Save Item Details."
					),
					indicator: "orange",
				},
				8
			);
			if (!this.expanded) this.toggle_shipment();
			this.ensure_line_items_expanded();
			const el = this.body.find(".ltl-line-items")[0];
			if (el) el.scrollIntoView({ behavior: "smooth", block: "start" });
			return null;
		}
		return valid;
	}

	render_shipment_form() {
		return `
			<div class="ltl-card ltl-ship-card">
				<div class="ltl-card-head ltl-ship-head" data-action="toggle-ship">
					<span><i class="fa fa-chevron-down ltl-chevron"></i> Shipment Details</span>
				</div>
				${this.render_collapsed_fields()}
				${this.render_expanded_fields()}
			</div>`;
	}

	bind_events() {
		this.body.on("click", "[data-action='fetch']", () => this.fetch_rates());
		this.body.on("click", "[data-action='clear']", () => this.clear_form());
		this.body.on("click", "[data-action='new_quote']", () => this.clear_form());
		this.body.on("click", "[data-action='dashboard']", () => this.body[0].querySelector(".ltl-scroll").scrollTo(0, 0));
		this.body.on("click", ".ltl-help-btn", () => frappe.msgprint(__("Please reach out to your platform administrator.")));

		this.body.on("click", ".ltl-nav-item[data-view]", (e) => {
			this.body.find(".ltl-nav-item").removeClass("active");
			$(e.currentTarget).addClass("active");
			this.show_view($(e.currentTarget).attr("data-view"));
		});

		this.body.on("click", ".ltl-list-new", () => {
			if (this.current_list) frappe.new_doc(this.current_list.doctype);
		});

		this.body.on("click", ".ltl-list-row", (e) => {
			if ($(e.target).closest(".ltl-list-actions, .ltl-bol-attach, .ltl-recent-view").length) return;
			const name = $(e.currentTarget).attr("data-name");
			if (!this.current_list || !name) return;
			if (this.current_list.doctype === "LTL Quote Request") {
				this.open_quote_detail(name);
				return;
			}
			if (this.current_list.doctype === "LTL Shipment") {
				this.open_shipment_detail(name);
				return;
			}
			frappe.set_route("Form", this.current_list.doctype, name);
		});

		this.body.on("click", ".ltl-bol-attach:not(.ltl-bol-attach-muted), .ltl-detail-view-bol:not(:disabled)", (e) => {
			e.stopPropagation();
			const url = $(e.currentTarget).attr("data-bol-url");
			if (url) window.open(url, "_blank");
		});

		this.body.on(
			"input",
			".ltl-list-search",
			frappe.utils.debounce((e) => this.filter_list($(e.currentTarget).val()), 250)
		);

		this.body.on("click", ".ltl-book-btn", (e) => {
			const idx = parseInt($(e.currentTarget).attr("data-idx"), 10);
			this.book_shipment(idx);
		});

		this.body.on("click", ".ltl-recent-view", (e) => {
			e.stopPropagation();
			const name =
				$(e.currentTarget).attr("data-name") ||
				$(e.currentTarget).closest("tr").attr("data-name");
			if (!name) return;
			if (this.current_list && this.current_list.doctype === "LTL Shipment") {
				this.open_shipment_detail(name);
				return;
			}
			if (this.current_list && this.current_list.doctype !== "LTL Quote Request") {
				frappe.set_route("Form", this.current_list.doctype, name);
				return;
			}
			this.open_quote_detail(name);
		});

		this.body.on("click", "[data-action='detail-cancel']", () => this.close_detail_view());
		this.body.on("click", "[data-action='detail-save']", () => {
			if (this.detail_type === "shipment") this.save_shipment_detail();
			else this.save_quote_detail();
		});
		this.body.on("click", "[data-action='open-desk-form']", (e) => {
			const name = $(e.currentTarget).attr("data-name");
			const doctype = $(e.currentTarget).attr("data-doctype") || "LTL Quote Request";
			if (name) frappe.set_route("Form", doctype, name);
		});
		this.body.on("click", ".ltl-detail-shipment-link", (e) => {
			e.preventDefault();
			const shipment = $(e.currentTarget).attr("data-shipment");
			if (shipment) this.open_shipment_detail(shipment);
		});
		this.body.on("click", ".ltl-detail-quote-link", (e) => {
			e.preventDefault();
			const quote =
				$(e.currentTarget).attr("data-quote") || $(e.currentTarget).val();
			if (quote) this.open_quote_detail(quote);
		});

		const zip_selector =
			"[data-field='origin_zip'],[data-field='destination_zip']," +
			"[data-field='exp_origin_zip'],[data-field='exp_destination_zip']";
		const debounced_recent = frappe.utils.debounce(() => this.load_recent_requests(), 400);
		this.body.on("input", zip_selector, debounced_recent);

		this.body.on("click", "[data-action='toggle-ship']", () => this.toggle_shipment());
		this.body.on("click", "[data-action='toggle-load-acc']", () => this.toggle_load_accessorials());
		this.body.on("click", "[data-action='toggle-line-items']", (e) => {
			if ($(e.target).closest("[data-action='add-line-item']").length) return;
			this.toggle_line_items_section();
		});

		this.body.on("click", ".ltl-tab", (e) => {
			const target = $(e.currentTarget).attr("data-tab-target");
			const card = $(e.currentTarget).closest(".ltl-od-card");
			card.find(".ltl-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			card.find(".ltl-tab-pane").hide();
			card.find(`[data-tab-pane='${target}']`).show();
		});

		this.body.on("click", "[data-action='add-line-item']", (e) => {
			e.preventDefault();
			e.stopPropagation();
			this.ensure_line_items_expanded();
			this.open_line_item_editor(new_line_item(), true);
		});

		this.body.on("click", "[data-action='edit-line-item']", (e) => {
			e.preventDefault();
			e.stopPropagation();
			const id = $(e.currentTarget).closest(".ltl-line-item-row").attr("data-line-id");
			const item = (this.line_items || []).find((r) => r.id === id);
			if (!item) return;
			this.open_line_item_editor(item, false);
		});

		this.body.on("click", "[data-action='remove-line-item']", (e) => {
			e.preventDefault();
			e.stopPropagation();
			const id = $(e.currentTarget).closest(".ltl-line-item-row").attr("data-line-id");
			this.line_items = (this.line_items || []).filter((r) => r.id !== id);
			this.refresh_line_items_table();
		});

		this.body.on("click", "[data-action='cancel-line-item']", () => this.close_line_item_editor());
		this.body.on("click", "[data-action='save-line-item']", () => this.save_line_item_editor());

		this.body.on("change", ".ltl-li-select-all", (e) => {
			const checked = $(e.currentTarget).is(":checked");
			this.body.find(".ltl-li-row-check").prop("checked", checked);
		});
	}

	toggle_shipment() {
		this.expanded = !this.expanded;
		this.sync_core_fields(this.expanded);
		const card = this.body.find(".ltl-ship-card");
		card.toggleClass("expanded", this.expanded);
		card.find(".ltl-ship-collapsed").toggle(!this.expanded);
		card.find(".ltl-ship-expanded").toggle(this.expanded);
		this.load_recent_requests();
	}

	toggle_load_accessorials() {
		this.load_acc_expanded = !this.load_acc_expanded;
		const card = this.body.find(".ltl-load-acc-card");
		card.toggleClass("expanded", this.load_acc_expanded);
		card.children(".ltl-collapse-body").toggle(this.load_acc_expanded);
	}

	toggle_line_items_section(force_open) {
		if (force_open === true) this.line_items_expanded = true;
		else if (force_open === false) this.line_items_expanded = false;
		else this.line_items_expanded = !this.line_items_expanded;
		const card = this.body.find(".ltl-line-items");
		card.toggleClass("expanded", this.line_items_expanded);
		card.children(".ltl-collapse-body").toggle(this.line_items_expanded);
	}

	ensure_line_items_expanded() {
		if (!this.line_items_expanded) this.toggle_line_items_section(true);
	}

	// Keep the shared core fields (zip / weight / class) in sync across the two views.
	sync_core_fields(to_expanded) {
		const set = (field, value) => this.body.find(`[data-field='${field}']`).val(value);
		const get = (field) => this.body.find(`[data-field='${field}']`).val();
		const pairs = [
			["origin_zip", "exp_origin_zip"],
			["destination_zip", "exp_destination_zip"],
			["weight", "exp_weight"],
			["freight_class", "exp_freight_class"],
		];
		pairs.forEach(([collapsed, expanded]) => {
			if (to_expanded) {
				set(expanded, get(collapsed));
			} else {
				set(collapsed, get(expanded));
			}
		});
	}

	collect_payload() {
		const val = (field) => (this.body.find(`[data-field='${field}']`).val() || "").trim();
		const accessorials = [];
		const codes = [];
		this.body.find("input[data-acc]:checked").each(function () {
			const code = $(this).attr("data-acc");
			const group = $(this).attr("data-group") || "";
			const label = ($(this).closest("label").find("span").first().text() || "").trim();
			if (!code) return;
			accessorials.push({ code, group, service_group: group, label });
			if (!codes.includes(code)) codes.push(code);
		});

		const pfx = this.expanded ? "exp_" : "";
		const payload = {
			origin_zip: val(`${pfx}origin_zip`),
			destination_zip: val(`${pfx}destination_zip`),
			weight: val(`${pfx}weight`),
			freight_class: val(`${pfx}freight_class`),
			pieces: 1,
			accessorial_codes: codes,
			accessorials,
		};

		if (this.expanded) {
			Object.assign(payload, {
				origin_city: val("exp_origin_city"),
				origin_state: val("exp_origin_state"),
				destination_city: val("exp_destination_city"),
				destination_state: val("exp_destination_state"),
				shipper_company_name: val("exp_origin_location"),
				shipper_address: val("exp_origin_address"),
				consignee_company_name: val("exp_destination_location"),
				consignee_address: val("exp_destination_address"),
				contact_name: val("exp_origin_contact"),
				origin_contact_name: val("exp_origin_contact"),
				origin_contact_email: val("exp_origin_email"),
				destination_contact_name: val("exp_destination_contact"),
				destination_contact_email: val("exp_destination_email"),
				origin_country: val("exp_origin_country") || "USA",
				destination_country: val("exp_destination_country") || "USA",
				pickup_date: val("exp_origin_date"),
				pickup_hours: val("exp_origin_hours"),
				delivery_date: val("exp_destination_date"),
				delivery_hours: val("exp_destination_hours"),
			});
		}

		const line_items = this.collect_line_items();
		if (line_items.length) {
			payload.items = line_items;
			payload.commodity_description = line_items[0].description || line_items[0].item_name || "";
			payload.nmfc = line_items[0].nmfc || line_items[0].nmfc_number || "";
			if (!payload.freight_class && line_items[0].freight_class) {
				payload.freight_class = line_items[0].freight_class;
			}

			const rolled_weight = line_items.reduce((sum, row) => {
				const w = parseFloat(row.weight);
				const qty = Math.max(parseInt(row.quantity || row.qty || 1, 10) || 1, 1);
				return sum + (Number.isFinite(w) ? w * qty : 0);
			}, 0);
			if (rolled_weight > 0) {
				payload.weight = String(rolled_weight);
			}

			const rolled_pieces = line_items.reduce((sum, row) => {
				return sum + Math.max(parseInt(row.quantity || row.qty || 1, 10) || 1, 1);
			}, 0);
			if (rolled_pieces > 0) {
				payload.pieces = rolled_pieces;
			}

			const first_with_dims = line_items.find((row) => row.length && row.width && row.height);
			if (first_with_dims) {
				payload.length = first_with_dims.length;
				payload.width = first_with_dims.width;
				payload.height = first_with_dims.height;
				payload.dimension_uom = first_with_dims.dimension_unit || first_with_dims.dimension_units || "IN";
			}
		}

		return payload;
	}

	fetch_rates() {
		if (!this.require_line_items()) return;

		const payload = this.collect_payload();
		const val = (field) => (this.body.find(`[data-field='${field}']`).val() || "").trim();
		const missing = [];
		if (!payload.origin_zip) missing.push("Origin ZIP");
		if (!payload.destination_zip) missing.push("Destination ZIP");
		if (!payload.weight) missing.push("Total Weight");
		if (!payload.freight_class) missing.push("Freight Class");

		if (this.expanded) {
			if (!val("exp_origin_location")) missing.push("Pickup Location");
			if (!val("exp_origin_city")) missing.push("Pickup City");
			if (!val("exp_origin_state")) missing.push("Origin State");
			if (!val("exp_origin_country")) missing.push("Origin Country");
			if (!val("exp_origin_date")) missing.push("Pickup Date");
			if (!val("exp_origin_hours")) missing.push("Pickup Hours");
			if (!val("exp_origin_contact")) missing.push("Origin Contact");
			if (!val("exp_destination_location")) missing.push("Delivery Location");
			if (!val("exp_destination_city")) missing.push("Delivery City");
			if (!val("exp_destination_state")) missing.push("Destination State");
			if (!val("exp_destination_country")) missing.push("Destination Country");
			if (!val("exp_destination_contact")) missing.push("Destination Contact");
		}

		if (missing.length) {
			frappe.show_alert({ message: __("Please fill: {0}", [missing.join(", ")]), indicator: "orange" }, 6);
			return;
		}

		const $btn = this.body.find("[data-action='fetch']");
		$btn.prop("disabled", true).html('<i class="fa fa-spinner fa-spin"></i> Fetching…');

		frappe.call({
			method: "ltl_quote.api.quote.get_ltl_rates",
			args: { payload: JSON.stringify(payload) },
			callback: (r) => {
				$btn.prop("disabled", false).html('<i class="fa fa-bolt"></i> Fetch Rates');
				const res = r.message || {};
				if (res.status !== "success" || !res.data || !(res.data.quotes || []).length) {
					const err = (res.errors && res.errors.length && (res.errors[0].error || res.errors[0])) || res.error;
					this.quotes = [];
					this.quote_request_id = res.quote_request_id || null;
					this.render_rates();
					frappe.show_alert(
						{ message: err ? __("No quotes: {0}", [err]) : __("No carrier quotes were returned"), indicator: "orange" },
						7
					);
					return;
				}
				this.quote_request_id = res.quote_request_id;
				this.quotes = res.data.quotes;
				this.booking_context = null;
				this.load_booking_context(this.quote_request_id, () => {
					this.render_rates();
					frappe.show_alert(
						{ message: __("Quotes received — {0} carrier rates", [this.quotes.length]), indicator: "green" },
						7
					);
				});
			},
			error: () => {
				$btn.prop("disabled", false).html('<i class="fa fa-bolt"></i> Fetch Rates');
			},
		});
	}

	render_rates() {
		const card = this.body.find(".ltl-rates-card");
		const container = this.body.find(".ltl-rates-body");
		if (!this.quotes.length) {
			card.hide();
			container.empty();
			return;
		}
		const tag_badge = (t) =>
			`<span class="ltl-tag ltl-tag-${t.toLowerCase().replace(/\s+/g, "-")}">${this.tag_icon(t)} ${t}</span>`;

		const rows = this.quotes
			.map((q, idx) => {
				const currency = q.currency || "USD";
				const total = format_currency(q.total_cost, currency);
				const base = format_currency(q.linehaul_charge || 0, currency);
				const acc = format_currency(q.accessorial_charge || 0, currency);
				const transit = q.transit_days ? `${q.transit_days} Business Days` : "—";
				const service = frappe.utils.escape_html(q.service_level || "Standard LTL");
				const rating = q.reliability_score ? (q.reliability_score / 20).toFixed(1) : "—";
				const tags = q.tags || (q.tag ? [q.tag] : []);
				const tag_cells = tags.length
					? tags.map(tag_badge).join(" ")
					: '<span class="ltl-tag-none">—</span>';
				const recommended = tags.length ? "ltl-row-recommended" : "";
				return `
					<tr class="${recommended}">
						<td><div class="ltl-carrier-cell"><span class="ltl-carrier-badge">${frappe.utils.escape_html((q.carrier || "?").slice(0, 3).toUpperCase())}</span>
							<span>${frappe.utils.escape_html(q.carrier || q.carrier_code)}</span></div></td>
						<td><div class="ltl-tag-wrap">${tag_cells}</div></td>
						<td>${service}</td>
						<td>${transit}</td>
						<td class="ltl-total">${total}</td>
						<td>${base}</td>
						<td>${acc}</td>
						<td><span class="ltl-rating">${rating} <i class="fa fa-star"></i></span></td>
						<td>${this.render_rate_action(q, idx)}</td>
					</tr>`;
			})
			.join("");

		container.html(`
			<div class="ltl-rec-legend">
				<span class="ltl-tag ltl-tag-cheapest">${this.tag_icon("Cheapest")} Cheapest</span>
				<span class="ltl-tag ltl-tag-fastest">${this.tag_icon("Fastest")} Fastest</span>
				<span class="ltl-tag ltl-tag-best-value">${this.tag_icon("Best Value")} Best Value</span>
				<span class="ltl-rec-hint">Recommendation engine ranks carriers by cost, transit time &amp; reliability.</span>
			</div>
			<table class="ltl-table">
				<thead>
					<tr>
						<th>Carrier</th><th>Recommendation</th><th>Service Type</th><th>Transit Time</th>
						<th>Total Rate</th><th>Base Rate</th><th>Accessorials</th>
						<th>Rating</th><th>Action</th>
					</tr>
				</thead>
				<tbody>${rows}</tbody>
			</table>`);
		card.show();
	}

	tag_icon(tag) {
		const t = (tag || "").toLowerCase();
		if (t.includes("cheap")) return '<i class="fa fa-tag"></i>';
		if (t.includes("fast")) return '<i class="fa fa-bolt"></i>';
		if (t.includes("best")) return '<i class="fa fa-star"></i>';
		return "";
	}

	normalize_carrier_code(code) {
		return String(code || "")
			.toUpperCase()
			.replace(/[^A-Z0-9]/g, "");
	}

	render_rate_action(q, idx) {
		const ctx = this.booking_context;
		const code = this.normalize_carrier_code(q.carrier_code || q.carrier);
		const booked_norm = ctx?.booked_carrier_code
			? this.normalize_carrier_code(ctx.booked_carrier_code)
			: "";

		if (ctx?.shipment && booked_norm && code === booked_norm) {
			return `<button class="ltl-btn ltl-btn-light ltl-book-btn" data-idx="${idx}">${__("View Shipment")}</button>`;
		}
		if (ctx?.shipment) {
			return `<span class="ltl-status ltl-status-grey">${__("Booked")}</span>`;
		}
		return `<button class="ltl-btn ltl-btn-primary ltl-book-btn" data-idx="${idx}">${__("Book Shipment")}</button>`;
	}

	load_booking_context(quote_request_id, callback) {
		if (!quote_request_id) {
			this.booking_context = null;
			this.quote_request_status = null;
			if (callback) callback();
			return;
		}
		frappe.call({
			method: "ltl_quote.api.quote.get_quote_booking_context",
			args: { quote_request_id },
			callback: (r) => {
				const ctx = r.message || {};
				if (ctx.is_booked && ctx.shipment) {
					this.apply_booking_context({
						shipment: ctx.shipment,
						booked_carrier: ctx.booked_carrier,
						bol_document_url: ctx.bol_url,
						bol_number: ctx.bol_number,
					});
					this.quote_request_status = ctx.quote_status;
				} else {
					this.booking_context = null;
					this.quote_request_status = ctx.quote_status || null;
				}
				if (callback) callback();
			},
			error: () => {
				if (callback) callback();
			},
		});
	}

	apply_booking_context(res) {
		const shipment = res.shipment || (res.data && res.data.shipment);
		if (!shipment) return false;
		this.booking_context = {
			shipment,
			booked_carrier_code: res.booked_carrier || res.booked_carrier_code || "",
			bol_url: res.bol_document_url || res.bol_url || "",
			bol_number: res.bol_number || "",
		};
		this.quote_request_status = "Booked";
		return true;
	}

	open_shipment_view(shipmentName) {
		if (!shipmentName) return;
		this.body.find(".ltl-nav-item").removeClass("active");
		this.body.find('.ltl-nav-item[data-view="shipments"]').addClass("active");
		this.show_view("shipments");
		const cfg = LIST_VIEWS.shipments;
		this.current_list = cfg;
		frappe.db
			.get_list(cfg.doctype, {
				fields: cfg.fields,
				order_by: cfg.order_by,
				limit: 100,
			})
			.then((rows) => {
				this.list_rows = rows || [];
				this.render_list_table(this.list_rows);
				frappe.set_route("Form", "LTL Shipment", shipmentName);
			});
	}

	book_shipment(idx) {
		const quote = this.quotes[idx];
		if (!quote || !this.quote_request_id) return;

		if (!this.require_line_items()) return;

		const code = this.normalize_carrier_code(quote.carrier_code || quote.carrier);
		const booked = this.booking_context?.booked_carrier_code;
		if (this.booking_context?.shipment) {
			if (booked && code === this.normalize_carrier_code(booked)) {
				this.open_shipment_view(this.booking_context.shipment);
			}
			return;
		}
		this.show_booking_modal(idx);
	}

	show_booking_modal(idx) {
		const quote = this.quotes[idx];
		if (!quote) return;

		const shipment = this.collect_payload();
		const dialog = new frappe.ui.Dialog({
			title: __("Confirm Booking"),
			size: "large",
			fields: [{ fieldtype: "HTML", fieldname: "summary" }],
			primary_action_label: __("Confirm Booking"),
			primary_action: () => {
				if (!this.require_line_items()) return;
				dialog.hide();
				this.execute_booking(quote);
			},
		});

		dialog.$wrapper.addClass("ltl-book-dialog");
		dialog.fields_dict.summary.$wrapper.html(this.render_booking_modal_html(quote, shipment));
		dialog.show();
	}

	render_booking_modal_html(quote, shipment) {
		const esc = frappe.utils.escape_html;
		const currency = quote.currency || "USD";
		const carrier_name = quote.carrier || quote.carrier_code || "—";
		const carrier_code = (quote.carrier_code || carrier_name).slice(0, 3).toUpperCase();
		const service = esc(quote.service_level || "Standard LTL");
		const rating = quote.reliability_score ? (quote.reliability_score / 20).toFixed(1) : null;
		const tags = quote.tags || (quote.tag ? [quote.tag] : []);
		const tag_badge = (t) =>
			`<span class="ltl-tag ltl-tag-${t.toLowerCase().replace(/\s+/g, "-")}">${this.tag_icon(t)} ${esc(t)}</span>`;
		const tag_html = tags.length
			? tags.map(tag_badge).join(" ")
			: '<span class="ltl-tag-none">—</span>';

		const transit_days = quote.transit_days || 0;
		const transit_label =
			transit_days === 1
				? __("1 Business Day")
				: transit_days
					? __("{0} Business Days", [transit_days])
					: "—";

		const origin_zip = esc(shipment.origin_zip || "—");
		const destination_zip = esc(shipment.destination_zip || "—");
		const weight = shipment.weight ? `${Number(shipment.weight).toLocaleString()} lbs` : "—";
		const freight_class = shipment.freight_class ? __("Class {0}", [esc(shipment.freight_class)]) : "—";

		const detail_row = (label, value) =>
			`<div class="ltl-book-row"><span class="ltl-book-label">${label}</span><span class="ltl-book-value">${value}</span></div>`;

		const cost_row = (label, amount, extra_class = "") =>
			`<div class="ltl-book-row ${extra_class}"><span class="ltl-book-label">${label}</span><span class="ltl-book-value">${amount}</span></div>`;

		const fuel_surcharge = Number(quote.fuel_surcharge) || 0;
		const fuel_row = fuel_surcharge > 0
			? cost_row(__("Fuel Surcharge"), format_currency(fuel_surcharge, currency))
			: "";

		const accessorial = Number(quote.accessorial_charge) || 0;
		const accessorial_display =
			accessorial > 0
				? `+ ${format_currency(accessorial, currency)}`
				: format_currency(accessorial, currency);

		return `
			<div class="ltl-book-modal">
				<div class="ltl-book-header">
					<div class="ltl-book-carrier">
						<span class="ltl-carrier-badge">${esc(carrier_code)}</span>
						<div>
							<div class="ltl-book-carrier-name">${esc(carrier_name)}</div>
							<div class="ltl-book-meta">
								<span>${service}</span>
								${rating ? `<span class="ltl-rating">${rating} <i class="fa fa-star"></i></span>` : ""}
							</div>
						</div>
					</div>
					<div class="ltl-tag-wrap ltl-book-tags">${tag_html}</div>
				</div>
				<div class="ltl-book-grid">
					<div class="ltl-book-panel">
						<div class="ltl-book-panel-head">${__("Operational Details")}</div>
						<div class="ltl-book-panel-body">
							${detail_row(__("Transit Window"), esc(transit_label))}
							${detail_row(__("Route Lane"), `${origin_zip} <i class="fa fa-long-arrow-right ltl-book-arrow"></i> ${destination_zip}`)}
							${detail_row(__("Shipment Mass"), esc(weight))}
							${detail_row(__("Freight Class"), freight_class)}
						</div>
					</div>
					<div class="ltl-book-panel">
						<div class="ltl-book-panel-head">${__("Cost Summary")}</div>
						<div class="ltl-book-panel-body">
							${cost_row(__("Base Linehaul"), format_currency(quote.linehaul_charge || 0, currency))}
							${fuel_row}
							${cost_row(__("Accessorials"), accessorial_display)}
							${cost_row(__("Total Rate"), format_currency(quote.total_cost, currency), "ltl-book-total")}
						</div>
					</div>
				</div>
				<p class="ltl-book-footer">${__(
					"Review details before confirming booking with the carrier."
				)}</p>
			</div>`;
	}

	execute_booking(quote) {
		const line_items = this.require_line_items();
		if (!line_items) return;

		const first = line_items[0] || {};
		frappe.call({
			method: "ltl_quote.api.quote.accept_carrier_quote",
			args: {
				quote_request_id: this.quote_request_id,
				carrier_code: quote.carrier_code,
				total_charge: quote.total_cost,
				carrier_quote_id: quote.carrier_quote_id || "",
				items: JSON.stringify(line_items),
				commodity_description: first.description || first.item_name || "",
				nmfc: first.nmfc || first.nmfc_number || "",
			},
			freeze: true,
			freeze_message: __("Booking shipment…"),
			callback: (r) => {
				const res = r.message || {};
				if (res.status === "success" || res.status === "already_booked") {
					const booked = this.apply_booking_context(res);
					if (booked) {
						this.render_rates();
						const msg =
							res.status === "already_booked"
								? __("Shipment already booked — opening details")
								: __("Shipment booked — BOL {0}", [res.bol_number || "Pending"]);
						frappe.show_alert({ message: msg, indicator: "green" }, 8);
						this.load_recent_requests();
						this.open_shipment_view(this.booking_context.shipment);
						return;
					}
				}
				if (res.status === "success") {
					frappe.show_alert(
						{ message: __("Shipment booked — BOL {0}", [res.bol_number || "Pending"]), indicator: "green" },
						8
					);
					this.load_recent_requests();
				} else {
					frappe.msgprint({
						title: __("Booking Failed"),
						message: res.message || res.error || __("Unknown error"),
						indicator: "red",
					});
				}
			},
		});
	}

	current_zips() {
		const pfx = this.expanded ? "exp_" : "";
		const val = (f) => (this.body.find(`[data-field='${f}']`).val() || "").trim();
		return { origin_zip: val(`${pfx}origin_zip`), destination_zip: val(`${pfx}destination_zip`) };
	}

	load_recent_requests() {
		const { origin_zip, destination_zip } = this.current_zips();
		const filtered = !!(origin_zip || destination_zip);
		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.get_recent_quote_requests",
			args: { limit: 8, origin_zip: origin_zip, destination_zip: destination_zip },
			callback: (r) => this.render_recent(r.message || [], filtered),
		});
	}

	render_recent(rows, filtered) {
		const container = this.body.find(".ltl-recent-body");
		if (!rows.length) {
			container.html(
				`<div class="ltl-empty">${
					filtered
						? "No quote requests match the entered origin / destination ZIP."
						: "No quote requests yet."
				}</div>`
			);
			return;
		}
		const loc = (city, state, zip) =>
			frappe.utils.escape_html([city, state].filter(Boolean).join(", ") || zip || "—");
		const status_class = (s) =>
			({
				"Quotes Received": "green",
				Booked: "blue",
				Accepted: "blue",
				Draft: "grey",
				Pending: "orange",
				"API Error": "red",
			}[s] || "grey");

		const body = rows
			.map((row) => {
				const carrier =
					row.carrier_name || row.final_carrier
						? frappe.utils.escape_html(row.carrier_name || row.final_carrier)
						: "—";
				const rate =
					row.final_charge != null && row.final_charge !== ""
						? format_currency(row.final_charge, "USD")
						: "—";
				return `
			<tr>
				<td class="ltl-mono">${frappe.utils.escape_html(row.name)}</td>
				<td>${loc(row.origin_city, row.origin_state, row.origin_zip)}</td>
				<td>${loc(row.destination_city, row.destination_state, row.destination_zip)}</td>
				<td>${row.total_weight ? Number(row.total_weight).toLocaleString() : "—"}</td>
				<td>${carrier}</td>
				<td>${rate}</td>
				<td>${frappe.datetime.str_to_user(row.creation)}</td>
				<td><span class="ltl-status ltl-status-${status_class(row.status)}">${frappe.utils.escape_html(row.status || "—")}</span></td>
				<td><span class="ltl-recent-view" data-name="${frappe.utils.escape_html(row.name)}" title="View"><i class="fa fa-eye"></i></span></td>
			</tr>`;
			})
			.join("");

		container.html(`
			<table class="ltl-table">
				<thead>
					<tr><th>Request ID</th><th>Origin</th><th>Destination</th><th>Weight (lbs)</th><th>Carrier</th><th>Rate</th><th>Created On</th><th>Status</th><th>Action</th></tr>
				</thead>
				<tbody>${body}</tbody>
			</table>`);
	}

	show_view(key) {
		const is_quote = key === "quote";
		const is_detail = key === "detail";
		const is_line_item = key === "line-item";
		this.body.find(".ltl-view-quote").toggle(is_quote);
		this.body.find(".ltl-view-list").toggle(!is_quote && !is_detail && !is_line_item);
		this.body.find(".ltl-view-detail").toggle(is_detail);
		this.body.find(".ltl-view-line-item").toggle(is_line_item);
		this.body.find(".ltl-scroll")[0].scrollTo(0, 0);

		if (is_quote) {
			this.current_list = null;
			this.detail_doc = null;
			this.detail_type = null;
			this.body.find(".ltl-breadcrumb .current").text("New Carrier Quote");
			return;
		}

		if (is_line_item) {
			this.body.find(".ltl-breadcrumb .current").text("Line Item Details");
			return;
		}

		if (is_detail) {
			const label = this.detail_type === "shipment" ? "Shipment" : "Quote Request";
			this.body.find(".ltl-breadcrumb .current").text(label);
			return;
		}

		const cfg = LIST_VIEWS[key];
		if (!cfg) return;
		this.current_list = cfg;
		this.detail_doc = null;
		this.detail_type = null;
		this.body.find(".ltl-breadcrumb .current").text(cfg.title);
		this.body.find(".ltl-list-title").text(cfg.title);
		this.body.find(".ltl-list-sub").text(cfg.sub || "");
		this.body.find(".ltl-list-icon").attr("class", cfg.icon);
		this.body.find(".ltl-list-new").html(`<i class="fa fa-plus"></i> New ${frappe.utils.escape_html(cfg.title)}`);
		this.body.find(".ltl-list-search").val("");
		this.load_list(cfg);
	}

	open_quote_detail(name) {
		if (!name) return;
		this.detail_return_view = this.current_list
			? this.current_list.doctype === "LTL Shipment"
				? "shipments"
				: "quotes"
			: this.detail_type === "shipment"
				? "shipments"
				: "quote";
		this.detail_type = "quote";
		this.body.find(".ltl-nav-item").removeClass("active");
		this.body.find('.ltl-nav-item[data-view="quotes"]').addClass("active");
		this.show_view("detail");
		const container = this.body.find(".ltl-detail-body");
		container.html('<div class="ltl-empty">Loading…</div>');

		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.get_quote_request_detail",
			args: { name },
			callback: (r) => {
				if (!r.message || !r.message.doc) {
					container.html('<div class="ltl-empty">Unable to load quote request.</div>');
					return;
				}
				this.detail_doc = r.message;
				container.html(this.render_quote_detail(r.message));
			},
			error: () => {
				container.html('<div class="ltl-empty">Unable to load quote request.</div>');
			},
		});
	}

	open_shipment_detail(name) {
		if (!name) return;
		this.detail_return_view = "shipments";
		this.detail_type = "shipment";
		this.body.find(".ltl-nav-item").removeClass("active");
		this.body.find('.ltl-nav-item[data-view="shipments"]').addClass("active");
		this.show_view("detail");
		const container = this.body.find(".ltl-detail-body");
		container.html('<div class="ltl-empty">Loading…</div>');

		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.get_shipment_detail",
			args: { name },
			callback: (r) => {
				if (!r.message || !r.message.doc) {
					container.html('<div class="ltl-empty">Unable to load shipment.</div>');
					return;
				}
				this.detail_doc = r.message;
				container.html(this.render_shipment_detail(r.message));
			},
			error: () => {
				container.html('<div class="ltl-empty">Unable to load shipment.</div>');
			},
		});
	}

	close_detail_view() {
		const back = this.detail_return_view || (this.detail_type === "shipment" ? "shipments" : "quotes");
		this.detail_doc = null;
		this.detail_type = null;
		this.body.find(".ltl-nav-item").removeClass("active");
		const nav = back === "quote" ? "quote" : back;
		this.body.find(`.ltl-nav-item[data-view="${nav}"]`).addClass("active");
		this.show_view(back === "quote" ? "quote" : back);
	}

	close_quote_detail() {
		this.close_detail_view();
	}

	render_quote_detail(payload) {
		const doc = payload.doc || {};
		const accessorials = payload.accessorials || [];
		const shipments = payload.shipments || [];
		const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
		const val = (v) => esc(v);
		const readonly = ["Booked", "Cancelled"].includes(doc.status);
		const ro = readonly ? "readonly" : "";
		const dis = readonly ? "disabled" : "";

		const freight_opts = FREIGHT_CLASSES.map(
			(c) => `<option value="${c}" ${String(doc.freight_class) === c ? "selected" : ""}>${c}</option>`
		).join("");

		const shipment_tabs = shipments.length
			? shipments
					.map(
						(s, i) => `
				<button type="button" class="ltl-detail-conn-tab ${i === 0 ? "active" : ""} ltl-detail-shipment-link"
					data-shipment="${esc(s.name)}">${i + 1} LTL Shipment</button>`
					)
					.join("")
			: `<span class="ltl-detail-conn-empty">No linked shipments</span>`;

		const acc_rows = accessorials.length
			? accessorials
					.map(
						(row, idx) => `
				<tr>
					<td>${idx + 1}</td>
					<td>${esc(row.accessorial_name || row.accessorial || "—")}</td>
					<td class="ltl-mono">${esc(row.accessorial_code || "—")}</td>
					<td>${esc(row.quantity || 1)}</td>
				</tr>`
					)
					.join("")
			: `<tr><td colspan="4" class="ltl-empty-cell">No accessorials selected</td></tr>`;

		const fmt_dt = (v) => (v ? frappe.datetime.str_to_user(v) : "");

		return `
			<div class="ltl-detail">
				<div class="ltl-detail-hero">
					<div class="ltl-detail-hero-left">
						<span class="ltl-detail-hero-icon"><i class="fa fa-truck"></i></span>
						<div>
							<div class="ltl-detail-hero-title">LTL Quote Request</div>
							<div class="ltl-detail-hero-sub">Create and manage LTL shipment quote requests</div>
						</div>
					</div>
					<div class="ltl-detail-hero-badge">Quote ID: ${esc(doc.name)}</div>
				</div>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-link"></i> 1. CONNECTIONS</div>
					<div class="ltl-detail-conn-row">
						${shipment_tabs}
						<button type="button" class="ltl-detail-conn-add" data-action="open-desk-form"
							data-name="${esc(doc.name)}" title="Open in Desk">+</button>
					</div>
					<div class="ltl-detail-grid ltl-detail-grid-3">
						<div class="ltl-field">
							<label>Status</label>
							<input class="ltl-input ltl-detail-status" value="${val(doc.status)}" readonly />
						</div>
						<div class="ltl-field">
							<label>Requested On</label>
							<input class="ltl-input" value="${val(fmt_dt(doc.requested_on))}" readonly />
							<small class="ltl-tz">${frappe.boot.time_zone?.user || frappe.boot.time_zone || ""}</small>
						</div>
						<div class="ltl-field">
							<label>Rates Aggregated On</label>
							<input class="ltl-input" value="${val(fmt_dt(doc.aggregated_on))}" readonly />
							<small class="ltl-tz">${frappe.boot.time_zone?.user || frappe.boot.time_zone || ""}</small>
						</div>
					</div>
				</section>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-map-marker"></i> 2. LANE &amp; LOCATIONS</div>
					<div class="ltl-detail-grid ltl-detail-grid-2">
						<div class="ltl-field"><label>Origin ZIP <span class="req">*</span></label>
							<input class="ltl-input" data-detail="origin_zip" value="${val(doc.origin_zip)}" ${ro} /></div>
						<div class="ltl-field"><label>Destination ZIP <span class="req">*</span></label>
							<input class="ltl-input" data-detail="destination_zip" value="${val(doc.destination_zip)}" ${ro} /></div>
						<div class="ltl-field"><label>Origin City</label>
							<input class="ltl-input" data-detail="origin_city" value="${val(doc.origin_city)}" ${ro} /></div>
						<div class="ltl-field"><label>Destination City</label>
							<input class="ltl-input" data-detail="destination_city" value="${val(doc.destination_city)}" ${ro} /></div>
						<div class="ltl-field"><label>Origin State</label>
							<input class="ltl-input" data-detail="origin_state" value="${val(doc.origin_state)}" ${ro} /></div>
						<div class="ltl-field"><label>Destination State</label>
							<input class="ltl-input" data-detail="destination_state" value="${val(doc.destination_state)}" ${ro} /></div>
					</div>
				</section>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-truck"></i> 3. PICKUP &amp; DELIVERY ADDRESSES</div>
					<div class="ltl-detail-grid ltl-detail-grid-2">
						<div class="ltl-field"><label>Shipper Company Name</label>
							<input class="ltl-input" data-detail="shipper_company_name" value="${val(doc.shipper_company_name)}"
								placeholder="Enter shipper company name" ${ro} /></div>
						<div class="ltl-field"><label>Consignee Company Name</label>
							<input class="ltl-input" data-detail="consignee_company_name" value="${val(doc.consignee_company_name)}"
								placeholder="Enter consignee company name" ${ro} /></div>
						<div class="ltl-field"><label>Shipper Address</label>
							<textarea class="ltl-input" data-detail="shipper_address" rows="3"
								placeholder="Enter shipper address" ${ro}>${val(doc.shipper_address)}</textarea></div>
						<div class="ltl-field"><label>Consignee Address</label>
							<textarea class="ltl-input" data-detail="consignee_address" rows="3"
								placeholder="Enter consignee address" ${ro}>${val(doc.consignee_address)}</textarea></div>
					</div>
				</section>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-cube"></i> 4. FREIGHT DETAILS</div>
					<div class="ltl-detail-grid ltl-detail-grid-2">
						<div class="ltl-field"><label>Total Weight <span class="req">*</span></label>
							<input type="number" class="ltl-input" data-detail="total_weight" value="${val(doc.total_weight)}" ${ro} /></div>
						<div class="ltl-field"><label>Length</label>
							<input type="number" class="ltl-input" data-detail="length" value="${val(doc.length || 0)}" ${ro} /></div>
						<div class="ltl-field"><label>Weight UOM</label>
							<select class="ltl-input" data-detail="weight_uom" ${dis}>
								<option value="LB" ${doc.weight_uom === "LB" || !doc.weight_uom ? "selected" : ""}>LB</option>
								<option value="KG" ${doc.weight_uom === "KG" ? "selected" : ""}>KG</option>
							</select></div>
						<div class="ltl-field"><label>Width</label>
							<input type="number" class="ltl-input" data-detail="width" value="${val(doc.width || 0)}" ${ro} /></div>
						<div class="ltl-field"><label>Freight Class <span class="req">*</span></label>
							<select class="ltl-input" data-detail="freight_class" ${dis}>${freight_opts}</select></div>
						<div class="ltl-field"><label>Height</label>
							<input type="number" class="ltl-input" data-detail="height" value="${val(doc.height || 0)}" ${ro} /></div>
						<div class="ltl-field"><label>Dimension UOM</label>
							<select class="ltl-input" data-detail="dimension_uom" ${dis}>
								<option value="IN" ${doc.dimension_uom === "IN" || !doc.dimension_uom ? "selected" : ""}>IN</option>
								<option value="CM" ${doc.dimension_uom === "CM" ? "selected" : ""}>CM</option>
							</select></div>
						<div class="ltl-field"><label>Pieces / Pallets</label>
							<input type="number" class="ltl-input" data-detail="pieces" value="${val(doc.pieces || 1)}" ${ro} /></div>
					</div>
					<label class="ltl-check ltl-detail-stackable">
						<input type="checkbox" data-detail="stackable" ${doc.stackable ? "checked" : ""} ${dis} />
						<span>Stackable</span>
					</label>
				</section>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-th-large"></i> 5. ACCESSORIALS</div>
					<table class="ltl-table ltl-detail-acc-table">
						<thead>
							<tr><th>No.</th><th>Accessorial</th><th>Code</th><th>Quantity</th></tr>
						</thead>
						<tbody>${acc_rows}</tbody>
					</table>
				</section>

				<div class="ltl-detail-footer">
					<button type="button" class="ltl-btn" data-action="detail-cancel">Cancel</button>
					<button type="button" class="ltl-btn ltl-btn-primary" data-action="detail-save" ${dis}>
						<i class="fa fa-save"></i> Save Quote Request
					</button>
				</div>
			</div>
		`;
	}

	save_quote_detail() {
		if (!this.detail_doc || !this.detail_doc.doc) return;
		const name = this.detail_doc.doc.name;
		const data = {};
		this.body.find("[data-detail]").each(function () {
			const key = $(this).attr("data-detail");
			if ($(this).attr("type") === "checkbox") {
				data[key] = $(this).is(":checked") ? 1 : 0;
			} else {
				data[key] = ($(this).val() || "").toString().trim();
			}
		});

		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.save_quote_request_detail",
			args: { name, data: JSON.stringify(data) },
			freeze: true,
			freeze_message: __("Saving quote request..."),
			callback: (r) => {
				if (r.exc) return;
				frappe.show_alert({ message: __("Quote request saved"), indicator: "green" }, 4);
				this.open_quote_detail(name);
			},
		});
	}

	render_shipment_detail(payload) {
		const doc = payload.doc || {};
		const line_items = payload.line_items || [];
		const tracking_events = payload.tracking_events || [];
		const accessorials = payload.accessorials || [];
		const esc = (v) => frappe.utils.escape_html(String(v == null ? "" : v));
		const val = (v) => esc(v);
		const readonly = ["Delivered", "Cancelled"].includes(doc.status);
		const ro = readonly ? "readonly" : "";
		const dis = readonly ? "disabled" : "";
		const fmt_dt = (v) => (v ? frappe.datetime.str_to_user(v) : "");

		const status_opts = ["Draft", "Booked", "Dispatched", "In Transit", "Out for Delivery", "Delivered", "Cancelled", "Exception"]
			.map((s) => `<option value="${s}" ${doc.current_status === s || (!doc.current_status && doc.status === s) ? "selected" : ""}>${s}</option>`)
			.join("");
		const dispatch_opts = ["Pending", "Sent to Carrier", "Acknowledged", "Failed"]
			.map((s) => `<option value="${s}" ${doc.dispatch_status === s ? "selected" : ""}>${s}</option>`)
			.join("");

		const tracking_rows = tracking_events.length
			? tracking_events
					.map(
						(row) => `
				<tr>
					<td>${esc(fmt_dt(row.event_datetime))}</td>
					<td class="ltl-mono">${esc(row.status_code || "—")}</td>
					<td>${esc(row.status_description || "—")}</td>
				</tr>`
					)
					.join("")
			: `<tr><td colspan="3" class="ltl-empty-cell">No Data</td></tr>`;

		const line_rows = line_items.length
			? line_items
					.map(
						(row, idx) => `
				<tr>
					<td>${idx + 1}</td>
					<td>${esc(row.idx_line_no || idx + 1)}</td>
					<td>${esc(row.handling_unit_qty || "—")}</td>
					<td>${esc(row.handling_unit_type || "—")}</td>
					<td>${esc(row.freight_class || "—")}</td>
					<td>${esc(row.commodity_description || "—")}</td>
					<td>${esc(row.weight != null ? row.weight : "—")}</td>
				</tr>`
					)
					.join("")
			: `<tr><td colspan="7" class="ltl-empty-cell">No Data</td></tr>`;

		const acc_rows = accessorials.length
			? accessorials
					.map(
						(row, idx) => `
				<tr>
					<td>${idx + 1}</td>
					<td>${esc(row.accessorial_name || row.accessorial || "—")}</td>
					<td class="ltl-mono">${esc(row.accessorial_code || "—")}</td>
					<td>${esc(row.quantity || 1)}</td>
				</tr>`
					)
					.join("")
			: `<tr><td colspan="4" class="ltl-empty-cell">No accessorials</td></tr>`;

		const bol_url = resolve_bol_url(doc);
		const view_bol_btn = bol_url
			? `<button type="button" class="ltl-btn ltl-detail-view-bol" data-bol-url="${esc(bol_url)}">
					<i class="fa fa-file-pdf-o"></i> View BOL
				</button>`
			: `<button type="button" class="ltl-btn ltl-detail-view-bol ltl-detail-view-bol-muted" disabled title="${__("No BOL attached")}">
					<i class="fa fa-file-pdf-o"></i> View BOL
				</button>`;

		return `
			<div class="ltl-detail ltl-shipment-detail">
				<div class="ltl-detail-hero">
					<div class="ltl-detail-hero-left">
						<span class="ltl-detail-hero-icon"><i class="fa fa-truck"></i></span>
						<div>
							<div class="ltl-detail-hero-title">LTL Shipment</div>
							<div class="ltl-detail-hero-sub">Track booked shipments, BOL details, and delivery status</div>
						</div>
					</div>
					<div class="ltl-detail-hero-right">
						<div class="ltl-detail-hero-badge">Shipment ID: ${esc(doc.name)}</div>
						${view_bol_btn}
					</div>
				</div>

				<div class="ltl-ship-detail-columns">
					<div class="ltl-ship-detail-col">
						<section class="ltl-detail-card">
							<div class="ltl-detail-card-head"><i class="fa fa-file-text-o"></i> Shipment Overview</div>
							<div class="ltl-detail-grid ltl-detail-grid-1">
								<div class="ltl-field"><label>Status</label>
									<input class="ltl-input ltl-detail-status" value="${val(doc.status)}" readonly /></div>
								<div class="ltl-field"><label>Quote Request</label>
									<input class="ltl-input ltl-detail-quote-link" data-quote="${esc(doc.quote_request)}"
										value="${val(doc.quote_request)}" readonly style="cursor:pointer;color:var(--ltl-orange);font-weight:600;" /></div>
								<div class="ltl-field"><label>Carrier</label>
									<input class="ltl-input" value="${val(doc.carrier)}" readonly /></div>
								<div class="ltl-field"><label>Carrier Name</label>
									<input class="ltl-input" value="${val(doc.carrier_name)}" readonly /></div>
							</div>
						</section>
					</div>

					<div class="ltl-ship-detail-col">
						<section class="ltl-detail-card">
							<div class="ltl-detail-card-head"><i class="fa fa-map-marker"></i> Visibility &amp; Tracking</div>
							<div class="ltl-detail-grid ltl-detail-grid-2">
								<div class="ltl-field"><label>Current Status</label>
									<select class="ltl-input" data-detail="current_status" ${dis}>${status_opts}</select></div>
								<div class="ltl-field" style="display:flex;align-items:flex-end;">
									<label class="ltl-check"><input type="checkbox" data-detail="has_exception" ${doc.has_exception ? "checked" : ""} ${dis} />
										<span>Has Exception</span></label>
								</div>
							</div>
							<div class="ltl-detail-card-head" style="margin-top:14px;margin-bottom:8px;">Tracking Events</div>
							<table class="ltl-table ltl-detail-acc-table">
								<thead><tr><th>Event Time</th><th>Status Code</th><th>Description</th></tr></thead>
								<tbody>${tracking_rows}</tbody>
							</table>
						</section>

						<section class="ltl-detail-card ltl-ship-card-charges">
							<div class="ltl-detail-card-head"><i class="fa fa-usd"></i> Charges</div>
							<div class="ltl-detail-grid ltl-detail-grid-3">
								<div class="ltl-field"><label>Currency</label>
									<input class="ltl-input" value="${val(doc.currency || "USD")}" readonly /></div>
								<div class="ltl-field"><label>Transit Days</label>
									<input class="ltl-input" value="${val(doc.transit_days)}" readonly /></div>
								<div class="ltl-field"><label>Total Charge</label>
									<input class="ltl-input" value="${val(doc.total_charge)}" readonly /></div>
							</div>
						</section>
					</div>
				</div>

				<section class="ltl-detail-card ltl-ship-card-lifecycle">
					<div class="ltl-detail-card-head"><i class="fa fa-calendar"></i> Shipment Lifecycle</div>
					<div class="ltl-detail-grid ltl-detail-grid-4">
						<div class="ltl-field"><label>Booked On</label>
							<input class="ltl-input" value="${val(fmt_dt(doc.booked_on))}" readonly /></div>
						<div class="ltl-field"><label>BOL Number</label>
							<input class="ltl-input" data-detail="bol_number" value="${val(doc.bol_number)}" ${ro} /></div>
						<div class="ltl-field"><label>Pickup Date</label>
							<input type="date" class="ltl-input" data-detail="pickup_date" value="${esc(doc.pickup_date || "")}" ${ro} /></div>
						<div class="ltl-field"><label>Dayton BOL ID</label>
							<input class="ltl-input" value="${val(doc.dayton_bol_id)}" readonly /></div>
						<div class="ltl-field"><label>Estimated Delivery</label>
							<input type="date" class="ltl-input" data-detail="estimated_delivery_date" value="${esc(doc.estimated_delivery_date || "")}" ${ro} /></div>
						<div class="ltl-field"><label>PRO / Tracking Number</label>
							<input class="ltl-input" data-detail="pro_number" value="${val(doc.pro_number)}" ${ro} /></div>
						<div class="ltl-field"><label>Actual Delivery</label>
							<input type="date" class="ltl-input" data-detail="actual_delivery_date" value="${esc(doc.actual_delivery_date || "")}" ${ro} /></div>
						<div class="ltl-field"><label>Carrier Confirmation #</label>
							<input class="ltl-input" data-detail="carrier_confirmation" value="${val(doc.carrier_confirmation)}" ${ro} /></div>
						<div class="ltl-field ltl-ship-lifecycle-dispatch">
							<label>Dispatch Status</label>
							<select class="ltl-input ltl-detail-status" data-detail="dispatch_status" ${dis}>${dispatch_opts}</select>
						</div>
					</div>
				</section>

				<div class="ltl-ship-detail-parallel">
					<section class="ltl-detail-card">
						<div class="ltl-detail-card-head"><i class="fa fa-file-text-o"></i> Dayton BOL Details</div>
						<div class="ltl-detail-grid ltl-detail-grid-2">
							<div class="ltl-field"><label>Document Type</label>
								<input class="ltl-input" data-detail="bol_document_type" value="${val(doc.bol_document_type || "Bill of Lading")}" ${ro} /></div>
							<div class="ltl-field"><label>Payment Terms</label>
								<input class="ltl-input" data-detail="bol_payment_terms" value="${val(doc.bol_payment_terms)}" ${ro} /></div>
							<div class="ltl-field"><label>SCAC</label>
								<input class="ltl-input" data-detail="bol_scac" value="${val(doc.bol_scac)}" ${ro} /></div>
							<div class="ltl-field"><label>Total Quantity</label>
								<input type="number" class="ltl-input" data-detail="bol_total_quantity" value="${val(doc.bol_total_quantity)}" ${ro} /></div>
							<div class="ltl-field"><label>BOL Date</label>
								<input type="date" class="ltl-input" data-detail="bol_date" value="${esc(doc.bol_date || "")}" ${ro} /></div>
							<div class="ltl-field"><label>Grand Total Weight</label>
								<input type="number" class="ltl-input" data-detail="bol_grand_total_weight" value="${val(doc.bol_grand_total_weight)}" ${ro} /></div>
							<div class="ltl-field"><label>BOL Page Count</label>
								<input type="number" class="ltl-input" data-detail="bol_page_count" value="${val(doc.bol_page_count)}" ${ro} /></div>
						</div>
						<div class="ltl-field" style="margin-top:12px;">
							<label>Special Instructions</label>
							<textarea class="ltl-input" data-detail="bol_special_instructions" rows="2" ${ro}>${val(doc.bol_special_instructions)}</textarea>
						</div>
					</section>

					<section class="ltl-detail-card">
						<div class="ltl-detail-card-head"><i class="fa fa-users"></i> Bill To / Third Party</div>
						<div class="ltl-detail-grid ltl-detail-grid-2">
							<div class="ltl-field"><label>Name</label>
								<input class="ltl-input" data-detail="bol_bill_to_name" value="${val(doc.bol_bill_to_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Contact</label>
								<input class="ltl-input" data-detail="bol_bill_to_contact_name" value="${val(doc.bol_bill_to_contact_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Address</label>
								<input class="ltl-input" data-detail="bol_bill_to_address1" value="${val(doc.bol_bill_to_address1)}" ${ro} /></div>
							<div class="ltl-field"><label>Phone</label>
								<input class="ltl-input" data-detail="bol_bill_to_contact_phone" value="${val(doc.bol_bill_to_contact_phone)}" ${ro} /></div>
							<div class="ltl-field"><label>City</label>
								<input class="ltl-input" data-detail="bol_bill_to_city" value="${val(doc.bol_bill_to_city)}" ${ro} /></div>
							<div class="ltl-field"><label>State</label>
								<input class="ltl-input" data-detail="bol_bill_to_state" value="${val(doc.bol_bill_to_state)}" ${ro} /></div>
							<div class="ltl-field"><label>ZIP</label>
								<input class="ltl-input" data-detail="bol_bill_to_postal_code" value="${val(doc.bol_bill_to_postal_code)}" ${ro} /></div>
						</div>
					</section>
				</div>

				<div class="ltl-ship-detail-parties">
					<section class="ltl-detail-card">
						<div class="ltl-detail-card-head"><i class="fa fa-truck"></i> Ship From</div>
						<div class="ltl-detail-grid ltl-detail-grid-2">
							<div class="ltl-field"><label>Name</label>
								<input class="ltl-input" data-detail="bol_shipper_name" value="${val(doc.bol_shipper_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Contact</label>
								<input class="ltl-input" data-detail="bol_shipper_contact_name" value="${val(doc.bol_shipper_contact_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Address</label>
								<input class="ltl-input" data-detail="bol_shipper_address1" value="${val(doc.bol_shipper_address1)}" ${ro} /></div>
							<div class="ltl-field"><label>Phone</label>
								<input class="ltl-input" data-detail="bol_shipper_contact_phone" value="${val(doc.bol_shipper_contact_phone)}" ${ro} /></div>
							<div class="ltl-field"><label>City</label>
								<input class="ltl-input" data-detail="bol_shipper_city" value="${val(doc.bol_shipper_city)}" ${ro} /></div>
							<div class="ltl-field"><label>State</label>
								<input class="ltl-input" data-detail="bol_shipper_state" value="${val(doc.bol_shipper_state)}" ${ro} /></div>
							<div class="ltl-field"><label>ZIP</label>
								<input class="ltl-input" data-detail="bol_shipper_postal_code" value="${val(doc.bol_shipper_postal_code)}" ${ro} /></div>
						</div>
					</section>

					<section class="ltl-detail-card">
						<div class="ltl-detail-card-head"><i class="fa fa-map-marker"></i> Ship To</div>
						<div class="ltl-detail-grid ltl-detail-grid-2">
							<div class="ltl-field"><label>Name</label>
								<input class="ltl-input" data-detail="bol_consignee_name" value="${val(doc.bol_consignee_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Contact</label>
								<input class="ltl-input" data-detail="bol_consignee_contact_name" value="${val(doc.bol_consignee_contact_name)}" ${ro} /></div>
							<div class="ltl-field"><label>Address</label>
								<input class="ltl-input" data-detail="bol_consignee_address1" value="${val(doc.bol_consignee_address1)}" ${ro} /></div>
							<div class="ltl-field"><label>Phone</label>
								<input class="ltl-input" data-detail="bol_consignee_contact_phone" value="${val(doc.bol_consignee_contact_phone)}" ${ro} /></div>
							<div class="ltl-field"><label>City</label>
								<input class="ltl-input" data-detail="bol_consignee_city" value="${val(doc.bol_consignee_city)}" ${ro} /></div>
							<div class="ltl-field"><label>State</label>
								<input class="ltl-input" data-detail="bol_consignee_state" value="${val(doc.bol_consignee_state)}" ${ro} /></div>
							<div class="ltl-field"><label>ZIP</label>
								<input class="ltl-input" data-detail="bol_consignee_postal_code" value="${val(doc.bol_consignee_postal_code)}" ${ro} /></div>
						</div>
					</section>
				</div>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-list-alt"></i> BOL Commodity / Line Items</div>
					<table class="ltl-table ltl-detail-acc-table">
						<thead>
							<tr><th>No.</th><th>Line No</th><th>HU Qty</th><th>HU Type</th><th>Class</th><th>Description</th><th>Weight</th></tr>
						</thead>
						<tbody>${line_rows}</tbody>
					</table>
				</section>

				<section class="ltl-detail-card">
					<div class="ltl-detail-card-head"><i class="fa fa-tags"></i> Accessories / Accessorials</div>
					<table class="ltl-table ltl-detail-acc-table">
						<thead><tr><th>No.</th><th>Accessorial</th><th>Code</th><th>Quantity</th></tr></thead>
						<tbody>${acc_rows}</tbody>
					</table>
				</section>

				<div class="ltl-detail-footer">
					<button type="button" class="ltl-btn" data-action="detail-cancel">Cancel</button>
					<button type="button" class="ltl-btn ltl-btn-primary" data-action="detail-save" ${dis}>
						<i class="fa fa-save"></i> Save Shipment
					</button>
				</div>
			</div>
		`;
	}

	save_shipment_detail() {
		if (!this.detail_doc || !this.detail_doc.doc || this.detail_type !== "shipment") return;
		const name = this.detail_doc.doc.name;
		const data = {};
		this.body.find("[data-detail]").each(function () {
			const key = $(this).attr("data-detail");
			if ($(this).attr("type") === "checkbox") {
				data[key] = $(this).is(":checked") ? 1 : 0;
			} else {
				data[key] = ($(this).val() || "").toString().trim();
			}
		});

		frappe.call({
			method: "ltl_quote.freight.page.ltl_quote.ltl_quote.save_shipment_detail",
			args: { name, data: JSON.stringify(data) },
			freeze: true,
			freeze_message: __("Saving shipment..."),
			callback: (r) => {
				if (r.exc) return;
				frappe.show_alert({ message: __("Shipment saved"), indicator: "green" }, 4);
				this.open_shipment_detail(name);
			},
		});
	}

	load_list(cfg) {
		const container = this.body.find(".ltl-list-body");
		container.html('<div class="ltl-empty">Loading…</div>');
		frappe.db
			.get_list(cfg.doctype, {
				fields: cfg.fields,
				order_by: cfg.order_by,
				limit: 100,
			})
			.then((rows) => {
				this.list_rows = rows || [];
				this.render_list_table(this.list_rows);
			})
			.catch(() => {
				container.html('<div class="ltl-empty">Unable to load records.</div>');
			});
	}

	filter_list(term) {
		if (!this.current_list) return;
		const q = (term || "").toLowerCase().trim();
		if (!q) {
			this.render_list_table(this.list_rows || []);
			return;
		}
		const keys = this.current_list.search || [];
		const filtered = (this.list_rows || []).filter((row) =>
			keys.some((k) => String(row[k] || "").toLowerCase().includes(q))
		);
		this.render_list_table(filtered);
	}

	render_list_table(rows) {
		const cfg = this.current_list;
		const container = this.body.find(".ltl-list-body");
		if (!cfg) return;
		if (!rows.length) {
			container.html('<div class="ltl-empty">No records found.</div>');
			return;
		}

		const head = cfg.columns.map((c) => `<th>${c.label}</th>`).join("") + "<th>Action</th>";
		const body = rows
			.map((row) => {
				const cells = cfg.columns.map((c) => `<td>${this.format_cell(row, c)}</td>`).join("");
				const actions = this.render_list_actions(row, cfg);
				return `<tr class="ltl-list-row" data-name="${frappe.utils.escape_html(row.name)}">${cells}${actions}</tr>`;
			})
			.join("");

		container.html(`
			<table class="ltl-table">
				<thead><tr>${head}</tr></thead>
				<tbody>${body}</tbody>
			</table>`);
	}

	render_list_actions(row, cfg) {
		if (cfg.doctype === "LTL Shipment") {
			const bol_url = resolve_bol_url(row);
			const bol_icon = bol_url
				? `<span class="ltl-bol-attach" title="${__("View BOL")}" data-bol-url="${frappe.utils.escape_html(bol_url)}"><i class="fa fa-paperclip"></i></span>`
				: `<span class="ltl-bol-attach ltl-bol-attach-muted" title="${__("No BOL attached")}"><i class="fa fa-paperclip"></i></span>`;
			return `<td class="ltl-list-actions">
				<span class="ltl-recent-view" data-name="${frappe.utils.escape_html(row.name)}" title="${__("Open")}"><i class="fa fa-eye"></i></span>
				${bol_icon}
			</td>`;
		}
		return `<td><span class="ltl-recent-view" data-name="${frappe.utils.escape_html(row.name)}" title="${__("Open")}"><i class="fa fa-eye"></i></span></td>`;
	}

	format_cell(row, col) {
		const raw = row[col.key] != null ? row[col.key] : col.fallback ? row[col.fallback] : "";
		switch (col.type) {
			case "mono":
				return `<span class="ltl-mono">${frappe.utils.escape_html(String(raw || "—"))}</span>`;
			case "text":
				return frappe.utils.escape_html(String(raw || "—"));
			case "num":
				return raw !== "" && raw != null ? Number(raw).toLocaleString() : "—";
			case "money":
				return raw ? format_currency(raw, row.currency || "USD") : "—";
			case "datetime":
				return raw ? frappe.datetime.str_to_user(raw) : "—";
			case "bool":
				return raw
					? '<span class="ltl-status ltl-status-green">Yes</span>'
					: '<span class="ltl-status ltl-status-grey">No</span>';
			case "status":
				return `<span class="ltl-status ltl-status-${this.status_class(raw)}">${frappe.utils.escape_html(String(raw || "—"))}</span>`;
			case "origin":
				return frappe.utils.escape_html(
					[row.origin_city, row.origin_state].filter(Boolean).join(", ") || row.origin_zip || "—"
				);
			case "destination":
				return frappe.utils.escape_html(
					[row.destination_city, row.destination_state].filter(Boolean).join(", ") || row.destination_zip || "—"
				);
			default:
				return frappe.utils.escape_html(String(raw || "—"));
		}
	}

	status_class(s) {
		return (
			{
				"Quotes Received": "green",
				Quoted: "green",
				Booked: "blue",
				Accepted: "blue",
				Delivered: "green",
				Draft: "grey",
				Pending: "orange",
				"In Transit": "orange",
				"API Error": "red",
				Cancelled: "red",
				Exception: "red",
			}[s] || "grey"
		);
	}

	clear_form() {
		this.body.find(".ltl-view-quote .ltl-input").each(function () {
			this.value = "";
		});
		this.body.find("input[data-acc]").prop("checked", false);
		this.line_items = [];
		this.refresh_line_items_table();
		this.quotes = [];
		this.quote_request_id = null;
		this.booking_context = null;
		this.quote_request_status = null;
		if (this.load_acc_expanded) this.toggle_load_accessorials();
		if (this.line_items_expanded) this.toggle_line_items_section(false);
		this.render_rates();
		this.load_recent_requests();
	}
};
