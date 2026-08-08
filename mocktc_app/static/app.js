(function () {
  "use strict";

  // expandable log rows
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".toggle-body");
    if (!btn) return;
    var id = btn.getAttribute("data-id");
    var body = document.getElementById("body-" + id);
    if (!body) return;
    var hidden = body.hasAttribute("hidden");
    if (hidden) {
      body.removeAttribute("hidden");
      btn.textContent = "收起";
    } else {
      body.setAttribute("hidden", "");
      btn.textContent = "展开";
    }
  });

  // auto-refresh the log table
  var box = document.getElementById("auto-refresh");
  var container = document.getElementById("logs-container");
  if (!box || !container) return;

  var timer = null;
  function currentQuery() {
    var form = document.getElementById("log-filter");
    if (!form) return "";
    var inputs = form.querySelectorAll("input[name], select[name]");
    var parts = [];
    for (var i = 0; i < inputs.length; i++) {
      var input = inputs[i];
      if (input.value) {
        parts.push(encodeURIComponent(input.name) + "=" + encodeURIComponent(input.value));
      }
    }
    return parts.length ? "?" + parts.join("&") : "";
  }
  function refresh() {
    if (!box.checked) return;
    fetch("/logs/table" + currentQuery(), { headers: { "X-Requested-With": "fetch" } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        // keep expanded state if possible
        var expanded = [];
        container.querySelectorAll(".log-body:not([hidden])").forEach(function (el) {
          expanded.push(el.id.replace("body-", ""));
        });
        container.innerHTML = html;
        container.querySelectorAll(".log-body").forEach(function (el) {
          var id = el.id.replace("body-", "");
          if (expanded.indexOf(id) >= 0) {
            el.removeAttribute("hidden");
            var btn = container.querySelector('.toggle-body[data-id="' + id + '"]');
            if (btn) btn.textContent = "收起";
          }
        });
      })
      .catch(function () {});
  }
  function schedule() {
    if (timer) clearTimeout(timer);
    timer = setTimeout(function () { refresh(); schedule(); }, 3000);
  }
  box.addEventListener("change", function () {
    if (box.checked) schedule(); else if (timer) clearTimeout(timer);
  });
  if (box.checked) schedule();

  // clear logs
  var clearBtn = document.getElementById("clear-logs");
  if (clearBtn) {
    clearBtn.addEventListener("click", function () {
      if (!window.confirm("确定清空全部接口日志？")) return;
      fetch("/logs/clear", { method: "POST" })
        .then(function () { refresh(); })
        .catch(function () {});
    });
  }
})();
