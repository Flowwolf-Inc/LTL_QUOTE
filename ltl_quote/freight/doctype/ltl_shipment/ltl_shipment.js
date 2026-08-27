// Copyright (c) 2026, LTL Quote and contributors
// For license information, please see license.txt

function resolve_bol_url(doc) {
	if (!doc) return "";
	const url = String(doc.bol_document_url || "").trim();
	if (url) {
		if (url.startsWith("http://") || url.startsWith("https://")) return url;
		return window.location.origin + (url.startsWith("/") ? url : `/${url}`);
	}
	const attach = String(doc.bol_document || "").trim();
	if (!attach) return "";
	if (attach.startsWith("http://") || attach.startsWith("https://")) return attach;
	return window.location.origin + (attach.startsWith("/") ? attach : `/${attach}`);
}

frappe.core = frappe.core || {};
frappe.core.utils = frappe.core.utils || {};

frappe.core.utils.update_dayton_carrier_bol = function (frm) {
	frappe.call({
		method: "update_electronic_bol",
		doc: frm.doc,
		freeze: true,
		callback(r) {
			if (r.message?.success) {
				frappe.show_alert({
					message: __("Updated BOL saved."),
					indicator: "green",
				});
				frm.reload_doc();
				return;
			}

			if (r.message?.status === "info") {
				frappe.msgprint({
					title: __("Dayton BOL Pending"),
					indicator: "orange",
					message: r.message.message || __("The BOL document is not ready yet."),
				});
				return;
			}

			frappe.msgprint({
				title: __("Dayton BOL Update Failed"),
				indicator: "red",
				message: r.message?.error || r.message?.message || __("Unknown error"),
			});
		},
	});
};

frappe.ui.form.on("LTL Shipment", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.remove_custom_button(__("View BOL"));
			const bol_url = resolve_bol_url(frm.doc);
			if (bol_url) {
				frm.add_custom_button(__("View BOL"), () => {
					window.open(bol_url, "_blank", "noopener,noreferrer");
				}).addClass("btn-primary");
			}

			frm.remove_custom_button(__("Dispatch to Carrier"));
			frm.remove_custom_button(__("Update Electronic BOL"));
			frm.remove_custom_button(__("Track Location"));
			frm.remove_custom_button(__("Generate BOL"));
			frm.remove_custom_button(__("Cancel BOL"));
			frm.remove_custom_button(__("Get BOL Image"));
			frm.remove_custom_button(__("View BOL Image"));
			frm.remove_custom_button(__("Dispatch to Carrier"), __("Actions"));
			frm.remove_custom_button(__("Update Electronic BOL"), __("Actions"));
			frm.remove_custom_button(__("Track Location"), __("Actions"));
			frm.remove_custom_button(__("Cancel BOL"), __("SMC3 Actions"));
			frm.remove_custom_button(__("Get BOL Image"), __("SMC3 Actions"));
			frm.remove_custom_button(__("View BOL Image"), __("SMC3 Actions"));

			frm.add_custom_button(__("Refresh Tracking"), () => {
				frappe.call({
					method: "refresh_tracking",
					doc: frm.doc,
					freeze: true,
					callback() {
						frm.reload_doc();
					},
				});
			}, __("Visibility"));

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.status === "Booked" && frm.doc.dispatch_status !== "Acknowledged") {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				frm.add_custom_button(__("Schedule Pickup"), () => {
					frappe.call({
						method: "dispatch_to_carrier",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Scheduling pickup with {0}...", [carrier_label]),
						callback(r) {
							if (r.message?.status === "acknowledged" || r.message?.pickup_number) {
								frappe.show_alert({
									message: __("Pickup scheduled successfully."),
									indicator: "green",
								});
							}
							frm.reload_doc();
						},
					});
				});
			} else if (frm.doc.status === "Booked" && frm.doc.dispatch_status !== "Acknowledged") {
				frm.add_custom_button(__("Dispatch to Carrier"), () => {
					frappe.call({
						method: "dispatch_to_carrier",
						doc: frm.doc,
						freeze: true,
						callback() {
							frm.reload_doc();
						},
					});
				});
			}

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.pickup_number) {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				const pickup_method = pickup_lookup_method(frm.doc.carrier);
				frm.add_custom_button(__("View Pickup"), () => {
					frappe.call({
						method: pickup_method,
						args: { shipment: frm.doc.name },
						freeze: true,
						callback(r) {
							const pickup = r.message?.pickup || {};
							frappe.msgprint({
								title: __("{0} Pickup", [carrier_label]),
								indicator: r.message?.status === "success" ? "green" : "orange",
								message: [
									`${__("Pickup Number")}: ${pickup.pickup_number || "—"}`,
									`${__("Status")}: ${pickup.status || "—"}`,
									`${__("Ready")}: ${pickup.ready || "—"}`,
									`${__("Close")}: ${pickup.close || "—"}`,
								].join("<br>"),
							});
							frm.reload_doc();
						},
					});
				}, __("{0} Actions", [carrier_label]));

				if (frm.doc.pickup_status !== "Cancelled") {
					frm.add_custom_button(__("Cancel Pickup"), () => {
						frappe.confirm(
							__("Cancel this {0} pickup?", [carrier_label]),
							() => {
								frappe.call({
									method: "cancel_pickup",
									doc: frm.doc,
									freeze: true,
									callback(r) {
										if (r.message?.status === "success") {
											frappe.show_alert({
												message: __("Pickup cancelled."),
												indicator: "green",
											});
										}
										frm.reload_doc();
									},
								});
							}
						);
					}, __("{0} Actions", [carrier_label]));
				}
			}

			if (frm.doc.carrier === "DAYTON" && frm.doc.dayton_bol_id) {
				frm.add_custom_button(__("Generate BOL"), () => {
					frappe.core.utils.update_dayton_carrier_bol(frm);
				}).addClass("btn-primary");
			}

			if (
				is_smc3_carrier(frm.doc.carrier)
				&& frm.doc.status !== "Cancelled"
				&& frm.doc.status !== "Delivered"
				&& (frm.doc.pro_number || frm.doc.bol_number)
			) {
				frm.add_custom_button(__("Get BOL Image"), () => {
					frappe.call({
						method: "fetch_bol_image",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Fetching BOL image…"),
						callback(r) {
							if (r.message?.status === "success") {
								frappe.show_alert({
									message: __("BOL image saved."),
									indicator: "green",
								});
							}
							frm.reload_doc();
						},
					});
				}, __("SMC3 Actions"));
				frm.add_custom_button(__("Cancel BOL"), () => {
					frappe.confirm(__("Cancel this SMC3 bill of lading?"), () => {
						frappe.call({
							method: "cancel_bol",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Cancelling BOL…"),
							callback(r) {
								if (r.message?.status === "success") {
									frappe.show_alert({
										message: __("BOL cancelled."),
										indicator: "green",
									});
								}
								frm.reload_doc();
							},
						});
					});
				}, __("SMC3 Actions"));
			}

			if (is_smc3_carrier(frm.doc.carrier) && frm.doc.bol_image) {
				frm.add_custom_button(__("View BOL Image"), () => {
					const image_url = resolve_bol_url({ bol_document: frm.doc.bol_image });
					if (image_url) {
						window.open(image_url, "_blank", "noopener,noreferrer");
					}
				}, __("SMC3 Actions"));
			}

			if (is_pickup_tracking_carrier(frm.doc.carrier) && frm.doc.pro_number) {
				const carrier_label = pickup_carrier_label(frm.doc.carrier);
				const is_dayton = pickup_connector_key(frm.doc.carrier) === "DAYTON";
				frm.add_custom_button(__("Track Location"), () => {
					frappe.show_alert({
						message: __("Contacting {0} network...", [carrier_label]),
						indicator: "blue",
					});

					frappe.call({
						method: is_dayton ? "fetch_dayton_tracking_updates" : "refresh_tracking",
						doc: frm.doc,
						callback(r) {
							if (!is_dayton) {
								frm.reload_doc();
								frappe.show_alert({
									message: __("Tracking details synchronized."),
									indicator: "green",
								});
								return;
							}
							if (r.message?.status === "success") {
								frm.reload_doc();
								frappe.show_alert({
									message: r.message.message,
									indicator: "green",
								});
								return;
							}

							if (r.message) {
								frappe.msgprint({
									title: __("Tracking System"),
									message: r.message.message,
									indicator: r.message.status === "error" ? "red" : "orange",
								});
							}
						},
					});
				});
			}

			if (frm.doc.carrier === "DAYTON") {
				frm.add_custom_button(__("Track Pending Shipments"), () => {
					get_dayton_customer_code(frm).then((customer_code) => {
						frappe.call({
							method: "ltl_quote.carrier_network.adapters.dayton.fetch_dayton_pending_shipments",
							args: { customer_code },
							freeze: true,
							freeze_message: __("Fetching pending shipments from Dayton..."),
							callback(r) {
								if (r.message?.status === "error") {
									frappe.msgprint({
										title: __("Dayton Tracking"),
										indicator: "red",
										message: r.message.text || __("Failed to fetch pending shipments."),
									});
									return;
								}
								if (r.message?.results) {
									render_dayton_tracking_dashboard(
										frm,
										"Dayton Pending Shipments Scan",
										r.message.results
									);
								} else {
									render_dayton_tracking_dashboard(frm, "Dayton Pending Shipments Scan", []);
								}
							},
						});
					});
				}, __("Dayton Actions"));

				frm.add_custom_button(__("Track By Date Range"), () => {
					frappe.prompt(
						[
							{
								label: __("Start Date"),
								fieldname: "start_date",
								fieldtype: "Date",
								reqd: 1,
								default: frappe.datetime.add_days(frappe.datetime.get_today(), -1),
							},
							{
								label: __("End Date"),
								fieldname: "end_date",
								fieldtype: "Date",
								reqd: 1,
								default: frappe.datetime.get_today(),
							},
						],
						(values) => {
							get_dayton_customer_code(frm).then((customer_code) => {
								frappe.call({
									method: "ltl_quote.carrier_network.adapters.dayton.fetch_dayton_tracking_by_date",
									args: {
										start_date: values.start_date,
										end_date: values.end_date,
										customer_code,
									},
									freeze: true,
									freeze_message: __("Querying historical tracking window..."),
									callback(r) {
										if (r.message?.status === "error") {
											frappe.msgprint({
												title: __("Dayton Tracking"),
												indicator: "red",
												message: r.message.text || __("Failed to fetch tracking data."),
											});
											return;
										}
										if (r.message?.results) {
											render_dayton_tracking_dashboard(
												frm,
												"Historical Shipments Tracking",
												r.message.results
											);
										} else {
											render_dayton_tracking_dashboard(frm, "Historical Shipments Tracking", []);
										}
									},
								});
							});
						},
						__("Specify Tracking Range"),
						__("Fetch Data")
					);
				}, __("Dayton Actions"));
			}

			if (frm.doc.carrier === "ARCB" && frm.doc.status === "Booked") {
				let btn = frm.add_custom_button(__("Generate BOL"), function () {
					frappe.call({
						method: "ltl_quote.api.shipment.attach_arcbest_bol_to_shipment",
						args: {
							shipment_id: frm.doc.name,
						},
						freeze: true,
						freeze_message: __("Downloading PDF from ArcBest and attaching to panel..."),
						callback(r) {
							if (r.message && r.message.status === "success") {
								frappe.show_alert({
									message: __("BOL Attached Successfully!"),
									indicator: "green",
								});
								frm.reload_doc();
							}
						},
					});
				});

				if (btn) {
					btn
						.removeClass("btn-default")
						.css({
							"background-color": "#111111",
							color: "#ffffff",
							"font-weight": "bold",
							border: "1px solid #000000",
						});
				}
			}
		}
	},
});

function get_dayton_customer_code(frm) {
	if (frm.doc.customer_code) {
		return Promise.resolve(frm.doc.customer_code);
	}
	if (frm.doc.carrier) {
		return frappe.db
			.get_value("LTL Carrier", frm.doc.carrier, "account_number")
			.then((r) => r.message?.account_number || "0055666");
	}
	return Promise.resolve("0055666");
}

function render_dayton_tracking_dashboard(frm, title, results) {
	frm.dashboard.clear_headline();
	frm.dashboard.reset();

	if (!results || results.length === 0) {
		frm.dashboard.set_headline_alert(
			__("No active transit tracking events returned from Dayton Freight currently."),
			"orange"
		);
		return;
	}

	let html_content = `
		<div class="dayton-tracking-container" style="padding: 15px; background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-md); margin-bottom: 20px;">
			<h6 class="text-muted text-uppercase tracking-wide" style="margin-bottom: 12px; font-size: 11px; font-weight: 600; color: var(--text-muted);">
				<i class="fa fa-truck" style="margin-right: 6px;"></i> ${__(title)}
			</h6>
			<div class="row">
	`;

	results.forEach((row) => {
		const activity = row.status ? row.status.activity : row.event || "Unknown Status";
		const code = row.status ? row.status.activityCode : "N/A";

		let indicator_color = "blue";
		if (["DELIVERED", "DLV"].includes(code)) indicator_color = "green";
		if (["DELAY", "EXC"].includes(code)) indicator_color = "red";
		if (["ETOFD", "OFD"].includes(code)) indicator_color = "orange";

		html_content += `
				<div class="col-sm-4" style="margin-bottom: 10px;">
					<div style="padding: 10px; background: var(--control-bg); border-left: 4px solid var(--text-${indicator_color}); border-radius: 4px;">
						<div style="font-size: 11px; color: var(--text-muted);">PRO #${row.pro}</div>
						<div style="font-weight: 600; font-size: 13px; margin: 2px 0;">${activity}</div>
						<span class="indicator ${indicator_color}" style="font-size: 10px;">Code: ${code}</span>
					</div>
				</div>
		`;
	});

	html_content += `
			</div>
		</div>
	`;

	frm.dashboard.add_section(html_content);
	frm.dashboard.show();

	frappe.utils.scroll_to(frm.dashboard.wrapper);
}

function pickup_connector_key(carrier_code) {
	const code = String(carrier_code || "").toUpperCase();
	if (code === "DAYTON") return "DAYTON";
	if (["TFORCE", "TFF"].includes(code) || code.includes("TFORCE")) return "TFORCE";
	if (["ARCB", "ARCBEST", "ABF", "ABFS"].includes(code) || code.includes("ARC")) return "ARCB";
	return code;
}

function is_tforce_carrier(carrier_code) {
	return pickup_connector_key(carrier_code) === "TFORCE";
}

function is_arcbest_carrier(carrier_code) {
	return pickup_connector_key(carrier_code) === "ARCB";
}

function is_pickup_tracking_carrier(carrier_code) {
	return ["DAYTON", "TFORCE", "ARCB"].includes(pickup_connector_key(carrier_code));
}

function is_smc3_carrier(carrier_code) {
	const code = String(carrier_code || "").toUpperCase();
	return code === "SMC3" || code.includes("SMC3");
}

function pickup_carrier_label(carrier_code) {
	const key = pickup_connector_key(carrier_code);
	if (key === "TFORCE") return "TForce";
	if (key === "ARCB") return "ArcBest";
	if (key === "DAYTON") return "Dayton";
	return key || "Carrier";
}

function pickup_lookup_method(carrier_code) {
	const key = pickup_connector_key(carrier_code);
	if (key === "TFORCE") return "ltl_quote.api.shipping.get_tforce_pickup";
	if (key === "ARCB") return "ltl_quote.api.shipping.get_arcbest_pickup";
	return "ltl_quote.api.shipping.get_dayton_pickup";
}
