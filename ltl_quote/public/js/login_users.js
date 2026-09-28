(function () {
	function init() {
		if (!document.querySelector(".for-login .form-login")) return;

		const users = [
			{ id: "administrator", label: "Administrator", usr: "Administrator", pwd: "admin" },
			{ id: "shipper", label: "Shipper", usr: "Shipper", pwd: "Flowwolf@123" },
			{ id: "broker", label: "Broker", usr: "Broker", pwd: "Flowwolf@1212" },
			{ id: "user1", label: "User1", usr: "user1@gmail.com", pwd: "user1@123" },
			{ id: "envoy", label: "Envoy", usr: "Envoy", pwd: "Envoy@123" },
			{ id: "amerilux", label: "Amerilux", usr: "Amerilux", pwd: "Amerilux@123" },
		];

		const form = document.querySelector(".for-login .form-login");
		if (!form || document.querySelector(".ltl-login-users")) return;

		const wrap = document.createElement("div");
		wrap.className = "ltl-login-users";
		wrap.innerHTML =
			'<div class="ltl-login-users-label">Sign in as</div>' +
			'<div class="ltl-login-user-grid">' +
			users
				.map(function (user) {
					return (
						'<button type="button" class="ltl-login-user" data-user="' +
						user.id +
						'">' +
						user.label +
						"</button>"
					);
				})
				.join("") +
			"</div>";
		form.insertBefore(wrap, form.firstElementChild);

		const email = document.getElementById("login_email");
		const password = document.getElementById("login_password");

		function applyUser(user) {
			wrap.querySelectorAll(".ltl-login-user").forEach(function (btn) {
				btn.classList.toggle("is-selected", btn.getAttribute("data-user") === user.id);
			});
			if (email) email.value = user.usr;
			if (password) password.value = user.pwd || "";
		}

		wrap.addEventListener("click", function (event) {
			const btn = event.target.closest(".ltl-login-user");
			if (!btn) return;
			const user = users.find(function (item) {
				return item.id === btn.getAttribute("data-user");
			});
			if (user) applyUser(user);
		});

		applyUser(users[0]);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
	document.addEventListener("login_rendered", init);
})();
