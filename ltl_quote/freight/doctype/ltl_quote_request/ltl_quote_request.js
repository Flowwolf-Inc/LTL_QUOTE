// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

frappe.ui.form.on("LTL Quote Request", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0 || frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Fetch Rates"), () => fetch_rates(frm)).addClass("btn-primary");

		render_book_button(frm);
	},
});

function render_book_button(frm) {
	frm.remove_custom_button(__("Book Selected Quote"));

	if (!["Booked", "Accepted"].includes(frm.doc.status) && frm.doc.carrier_quotes?.length) {
		frm.add_custom_button(__("Book Selected Quote"), () => book_quote(frm)).addClass(
			"btn-primary"
		);
	}
}

function fetch_rates(frm) {
	frappe.call({
		method: "fetch_rates",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Aggregating carrier rates..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}

			apply_fetched_quotes(frm, r.message);
			render_book_button(frm);

			const received = r.message.quotes_received || 0;
			frappe.show_alert(
				{
					message: received
						? __("Quotes received — {0} carrier quotes", [received])
						: __("No carrier quotes were returned"),
					indicator: received ? "green" : "orange",
				},
				7
			);
		},
	});
}

function apply_fetched_quotes(frm, message) {
	frm.clear_table("carrier_quotes");
	(message.quotes || []).forEach((quote) => frm.add_child("carrier_quotes", quote));

	const scalar_updates = {
		status: message.status,
		aggregated_on: message.aggregated_on,
		error_log: message.error_log || "",
		recommended_cheapest: message.recommendations?.cheapest || "",
		recommended_fastest: message.recommendations?.fastest || "",
		recommended_best_value: message.recommendations?.best_value || "",
	};
	Object.entries(scalar_updates).forEach(([field, value]) => {
		if (value !== undefined) {
			frm.doc[field] = value;
		}
	});

	if (message.modified) {
		frm.doc.modified = message.modified;
	}

	mark_form_saved(frm);

	[
		"carrier_quotes",
		"status",
		"aggregated_on",
		"error_log",
		"recommended_cheapest",
		"recommended_fastest",
		"recommended_best_value",
	].forEach((field) => frm.refresh_field(field));
}

function mark_form_saved(frm) {
	// The server has already persisted these changes, so drop the in-memory
	// "unsaved" state without triggering a full document reload of the page.
	frm.doc.__unsaved = 0;
	if (frm.beforeUnloadListener) {
		removeEventListener("beforeunload", frm.beforeUnloadListener, { capture: true });
	}
	frm.refresh_header();
}

function book_quote(frm) {
	const quotes = frm.doc.carrier_quotes || [];
	if (!quotes.length) {
		frappe.msgprint(__("No carrier quotes available."));
		return;
	}

	const options = quotes.map((q, i) => ({
		label: `${q.carrier_name} — ${format_currency(q.total_charge, q.currency || "USD")} (${q.transit_days} days)`,
		value: i,
		carrier: q.carrier,
		transit_days: q.transit_days,
		total_charge: q.total_charge,
		carrier_quote_id: q.carrier_quote_id,
	}));

	const prompt_fields =
		quotes.length === 1
			? []
			: [
					{
						fieldname: "row_idx",
						fieldtype: "Select",
						label: __("Select Carrier Quote"),
						options: options.map((o) => o.label).join("\n"),
						reqd: 1,
					},
				];

	const dispatch_booking = (selected_option) => {
		const carrier_name = selected_option.label.split(" — ")[0];
		frappe.confirm(
			__(
				"Are you sure you want to dispatch this cargo shipment and generate a formal Bill of Lading with {0}?",
				[carrier_name]
			),
			() => {
				if (is_arcbest_carrier(selected_option.carrier)) {
					book_arcbest_quote(frm, selected_option);
					return;
				}
				book_dayton_quote(frm, selected_option);
			}
		);
	};

	if (!prompt_fields.length) {
		dispatch_booking(options[0]);
		return;
	}

	frappe.prompt(
		prompt_fields,
		(values) => {
			const idx = options.findIndex((o) => o.label === values.row_idx);
			dispatch_booking(options[idx >= 0 ? idx : 0]);
		},
		__("Book Shipment"),
		__("Book")
	);
}

function is_arcbest_carrier(carrier_code) {
	const code = (carrier_code || "").toUpperCase();
	return ["ARCB", "ARCBEST", "ABF", "ABFS"].includes(code);
}

function book_arcbest_quote(frm, selected_option) {
	frappe.call({
		method: "ltl_quote.api.quote.accept_carrier_quote",
		args: {
			quote_request_id: frm.doc.name,
			carrier_code: selected_option.carrier,
			total_charge: selected_option.total_charge,
			carrier_quote_id: selected_option.carrier_quote_id,
		},
		freeze: true,
		freeze_message: __("Connecting to carrier dispatch server...Mapping Bill of Lading..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}

			if (r.message.status === "success") {
				frappe.show_alert({
					message: __("Quote Booked and Saved Locally!"),
					indicator: "green",
				});

				if (r.message.data?.shipment || r.message.shipment) {
					frappe.set_route(
						"Form",
						"LTL Shipment",
						r.message.data?.shipment || r.message.shipment
					);
					return;
				}

				if (r.message.bol_document_url) {
					frappe.msgprint({
						title: __("ArcBest BOL Ready"),
						indicator: "green",
						message: __(
							"BOL #{0} | PRO #{1}<br><br><a href='{2}' target='_blank'>Download BOL PDF</a>",
							[r.message.bol_number, r.message.pro_number, r.message.bol_document_url]
						),
					});
				}

				frm.reload_doc();
				return;
			}

			if (r.message.status === "failed") {
				frappe.msgprint({
					title: __("Carrier API Rejection"),
					indicator: "red",
					message: r.message.message || r.message.error || __("ArcBest rejected the booking request."),
				});
			}
		},
	});
}

function book_dayton_quote(frm, selected_option) {
	frappe.call({
		method: "ltl_quote.api.flowwolf.book_carrier_quote",
		args: {
			quote_request_id: frm.doc.name,
			carrier_code: selected_option.carrier,
			quote_row_idx: selected_option.value,
			transit_days: selected_option.transit_days,
		},
		freeze: true,
		freeze_message: __("Connecting to carrier dispatch server... Mapping Bill of Lading..."),
		callback(r) {
			if (r.exc || !r.message) {
				return;
			}

			if (r.message.status === "success") {
				frappe.show_alert({
					message: r.message.message,
					indicator: "green",
				});

				if (r.message.data?.shipment) {
					frappe.set_route("Form", "LTL Shipment", r.message.data.shipment);
					return;
				}

				frm.reload_doc();
			}
		},
	});
}

function format_currency(amount, currency) {
	return frappe.format(amount, { fieldtype: "Currency", options: currency });
}
