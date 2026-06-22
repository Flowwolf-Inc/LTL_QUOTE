// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

frappe.ui.form.on("LTL Quote Request", {
	refresh(frm) {
		if (frm.doc.docstatus !== 0 || frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Fetch Rates"), () => fetch_rates(frm), __("Actions"));

		if (frm.doc.status !== "Booked" && frm.doc.carrier_quotes?.length) {
			frm.add_custom_button(__("Book Selected Quote"), () => book_quote(frm), __("Actions"));
		}
	},
});

function fetch_rates(frm) {
	frappe.call({
		method: "fetch_rates",
		doc: frm.doc,
		freeze: true,
		freeze_message: __("Aggregating carrier rates..."),
		callback(r) {
			if (!r.exc) {
				frm.reload_doc();
				frappe.show_alert({
					message: __("Received {0} carrier quotes", [r.message?.quotes_received || 0]),
					indicator: "green",
				});
			}
		},
	});
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
		frappe.confirm(
			__(
				"Are you sure you want to dispatch this cargo shipment and generate a formal Bill of Lading with {0}?",
				[selected_option.label.split(" — ")[0]]
			),
			() => {
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

function format_currency(amount, currency) {
	return frappe.format(amount, { fieldtype: "Currency", options: currency });
}
