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
	if (wrapper.ltl_dashboard) {
		wrapper.ltl_dashboard.load_recent_requests();
	}
};

frappe.pages["ltl-quote"].on_page_hide = function () {
	document.body.classList.remove("ltl-fullscreen");
};

window.ltl_quote = window.ltl_quote || {};

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
		fields: ["name", "carrier_name", "carrier", "status", "bol_number", "pro_number", "total_charge", "currency", "transit_days", "booked_on", "creation"],
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

ltl_quote.Dashboard = class Dashboard {
	constructor(wrapper, page) {
		this.wrapper = wrapper;
		this.page = page;
		this.body = $(page.main).addClass("ltl-dashboard-root");
		this.quote_request_id = null;
		this.quotes = [];
		this.expanded = false;
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
		return `
			<div class="ltl-od-card">
				<div class="ltl-od-title">${title}</div>
				<div class="ltl-tabs">
					<span class="ltl-tab active" data-tab-target="${side}-details">Details</span>
					<span class="ltl-tab" data-tab-target="${side}-acc">Accessorials</span>
				</div>
				<div class="ltl-tab-pane" data-tab-pane="${side}-details">
					<div class="ltl-grid ltl-grid-2">
						<div class="ltl-field"><label>${loc_label} <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}location" /></div>
						<div class="ltl-field"><label>Street Address <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}address" /></div>
						<div class="ltl-field"><label>${city_label} <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}city" /></div>
						<div class="ltl-field"><label>State <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}state" /></div>
						<div class="ltl-field"><label>Zip <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="exp_${side}_zip" placeholder="e.g. ${is_origin ? "60601" : "75201"}" /></div>
						<div class="ltl-field"><label>Country <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}country" placeholder="USA" /></div>
						<div class="ltl-field"><label>${date_label} <span class="req">*</span></label>
							<input type="date" class="ltl-input" data-field="${pfx}date" /></div>
						<div class="ltl-field"><label>${hours_label} <span class="req">*</span></label>
							<input type="text" class="ltl-input" data-field="${pfx}hours" placeholder="0800-1700" /></div>
					</div>
					<div class="ltl-grid ltl-grid-1">
						<div class="ltl-field"><label>Contact</label>
							<input type="text" class="ltl-input" data-field="${pfx}contact" /></div>
					</div>
				</div>
				<div class="ltl-tab-pane" data-tab-pane="${side}-acc" style="display:none;">
					<div class="ltl-acc-grid">${this.accessorial_boxes(acc, side)}</div>
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
				<div class="ltl-subhead" style="margin-top:18px;">Load Based Accessorials</div>
				<div class="ltl-acc-grid">${this.accessorial_boxes(this.acc_options.load, "load")}</div>
			</div>`;
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
			const name = $(e.currentTarget).attr("data-name");
			if (this.current_list) frappe.set_route("Form", this.current_list.doctype, name);
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
			const name = $(e.currentTarget).attr("data-name");
			frappe.set_route("Form", "LTL Quote Request", name);
		});

		const zip_selector =
			"[data-field='origin_zip'],[data-field='destination_zip']," +
			"[data-field='exp_origin_zip'],[data-field='exp_destination_zip']";
		const debounced_recent = frappe.utils.debounce(() => this.load_recent_requests(), 400);
		this.body.on("input", zip_selector, debounced_recent);

		this.body.on("click", "[data-action='toggle-ship']", () => this.toggle_shipment());

		this.body.on("click", ".ltl-tab", (e) => {
			const target = $(e.currentTarget).attr("data-tab-target");
			const card = $(e.currentTarget).closest(".ltl-od-card");
			card.find(".ltl-tab").removeClass("active");
			$(e.currentTarget).addClass("active");
			card.find(".ltl-tab-pane").hide();
			card.find(`[data-tab-pane='${target}']`).show();
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
		const codes = [];
		this.body.find("input[data-acc]:checked").each(function () {
			const code = $(this).attr("data-acc");
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
			});
		}
		return payload;
	}

	fetch_rates() {
		const payload = this.collect_payload();
		const missing = [];
		if (!payload.origin_zip) missing.push("Origin ZIP");
		if (!payload.destination_zip) missing.push("Destination ZIP");
		if (!payload.weight) missing.push("Total Weight");
		if (!payload.freight_class) missing.push("Freight Class");
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
				this.render_rates();
				frappe.show_alert(
					{ message: __("Quotes received — {0} carrier rates", [this.quotes.length]), indicator: "green" },
					7
				);
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
						<td><button class="ltl-btn ltl-btn-primary ltl-book-btn" data-idx="${idx}">Book Shipment</button></td>
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

	book_shipment(idx) {
		const quote = this.quotes[idx];
		if (!quote || !this.quote_request_id) return;

		frappe.confirm(
			__("Book shipment with {0} for {1}?", [
				quote.carrier || quote.carrier_code,
				format_currency(quote.total_cost, quote.currency || "USD"),
			]),
			() => {
				frappe.call({
					method: "ltl_quote.api.quote.accept_carrier_quote",
					args: {
						quote_request_id: this.quote_request_id,
						carrier_code: quote.carrier_code,
						total_charge: quote.total_cost,
						carrier_quote_id: quote.carrier_quote_id || "",
					},
					freeze: true,
					freeze_message: __("Booking shipment…"),
					callback: (r) => {
						const res = r.message || {};
						if (res.status === "success") {
							frappe.show_alert(
								{ message: __("Shipment booked — BOL {0}", [res.bol_number || "Pending"]), indicator: "green" },
								8
							);
							this.load_recent_requests();
							if (res.shipment) {
								frappe.set_route("Form", "LTL Shipment", res.shipment);
							}
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
		);
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
			.map(
				(row) => `
			<tr>
				<td class="ltl-mono">${frappe.utils.escape_html(row.name)}</td>
				<td>${loc(row.origin_city, row.origin_state, row.origin_zip)}</td>
				<td>${loc(row.destination_city, row.destination_state, row.destination_zip)}</td>
				<td>${row.total_weight ? Number(row.total_weight).toLocaleString() : "—"}</td>
				<td>${frappe.datetime.str_to_user(row.creation)}</td>
				<td><span class="ltl-status ltl-status-${status_class(row.status)}">${frappe.utils.escape_html(row.status || "—")}</span></td>
				<td><span class="ltl-recent-view" data-name="${frappe.utils.escape_html(row.name)}" title="View"><i class="fa fa-eye"></i></span></td>
			</tr>`
			)
			.join("");

		container.html(`
			<table class="ltl-table">
				<thead>
					<tr><th>Request ID</th><th>Origin</th><th>Destination</th><th>Weight (lbs)</th><th>Created On</th><th>Status</th><th>Action</th></tr>
				</thead>
				<tbody>${body}</tbody>
			</table>`);
	}

	show_view(key) {
		const is_quote = key === "quote";
		this.body.find(".ltl-view-quote").toggle(is_quote);
		this.body.find(".ltl-view-list").toggle(!is_quote);
		this.body.find(".ltl-scroll")[0].scrollTo(0, 0);

		if (is_quote) {
			this.current_list = null;
			this.body.find(".ltl-breadcrumb .current").text("New Carrier Quote");
			return;
		}

		const cfg = LIST_VIEWS[key];
		if (!cfg) return;
		this.current_list = cfg;
		this.body.find(".ltl-breadcrumb .current").text(cfg.title);
		this.body.find(".ltl-list-title").text(cfg.title);
		this.body.find(".ltl-list-sub").text(cfg.sub || "");
		this.body.find(".ltl-list-icon").attr("class", cfg.icon);
		this.body.find(".ltl-list-new").html(`<i class="fa fa-plus"></i> New ${frappe.utils.escape_html(cfg.title)}`);
		this.body.find(".ltl-list-search").val("");
		this.load_list(cfg);
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
				return `<tr class="ltl-list-row" data-name="${frappe.utils.escape_html(row.name)}">${cells}
					<td><span class="ltl-recent-view" title="Open"><i class="fa fa-eye"></i></span></td></tr>`;
			})
			.join("");

		container.html(`
			<table class="ltl-table">
				<thead><tr>${head}</tr></thead>
				<tbody>${body}</tbody>
			</table>`);
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
		this.body.find(".ltl-input").each(function () {
			this.value = "";
		});
		this.body.find("input[data-acc]").prop("checked", false);
		this.quotes = [];
		this.quote_request_id = null;
		this.render_rates();
		this.load_recent_requests();
	}
};
