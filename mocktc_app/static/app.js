(function () {
  "use strict";

  function toast(message, error) {
    var box = document.getElementById("toast");
    if (!box) return;
    box.textContent = message;
    box.classList.toggle("error", !!error);
    box.hidden = false;
    window.clearTimeout(box._timer);
    box._timer = window.setTimeout(function () { box.hidden = true; }, 4200);
  }

  function tokenFrom(input) {
    return (input && input.value || "").trim();
  }

  function apiRequest(path, options, tokenInput) {
    options = options || {};
    var token = tokenFrom(tokenInput);
    options.credentials = "same-origin";
    options.headers = Object.assign({"Content-Type": "application/json"}, options.headers || {});
    var authorize = token ? fetch("/tc/v1/admin/session", {
      method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
      body: JSON.stringify({token: token})
    }).then(parseResponse) : Promise.resolve();
    return authorize.then(function () {
      if (tokenInput) tokenInput.value = "";
      return fetch(path, options);
    }).then(function (response) {
      return parseResponse(response);
    });
  }

  function parseResponse(response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok || Number(body.status || response.status) >= 400) throw new Error(body.message || "请求失败（HTTP " + response.status + "）");
        return body;
      });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest(".toggle-body");
    if (!btn) return;
    var body = document.getElementById("body-" + btn.getAttribute("data-id"));
    if (!body) return;
    var hidden = body.hasAttribute("hidden");
    body.toggleAttribute("hidden", !hidden);
    btn.textContent = hidden ? "收起" : "展开";
  });

  var autoRefresh = document.getElementById("auto-refresh");
  var logsContainer = document.getElementById("logs-container");
  if (autoRefresh && logsContainer) {
    var timer = null;
    function currentQuery() {
      var form = document.getElementById("log-filter");
      if (!form) return "";
      var parts = [];
      form.querySelectorAll("input[name], select[name]").forEach(function (input) {
        if (input.value) parts.push(encodeURIComponent(input.name) + "=" + encodeURIComponent(input.value));
      });
      return parts.length ? "?" + parts.join("&") : "";
    }
    function refreshLogs() {
      if (!autoRefresh.checked) return;
      fetch("/logs/table" + currentQuery(), {headers: {"X-Requested-With": "fetch"}})
        .then(function (response) { return response.text(); })
        .then(function (markup) { logsContainer.innerHTML = markup; }).catch(function () {});
    }
    function schedule() { window.clearTimeout(timer); timer = window.setTimeout(function () { refreshLogs(); schedule(); }, 3000); }
    autoRefresh.addEventListener("change", function () { if (autoRefresh.checked) schedule(); else window.clearTimeout(timer); });
    if (autoRefresh.checked) schedule();
    var clearLogs = document.getElementById("clear-logs");
    if (clearLogs) clearLogs.addEventListener("click", function () {
      if (window.confirm("确定清空全部接口日志？")) fetch("/logs/clear", {method: "POST"}).then(refreshLogs).catch(function () {});
    });
  }

  var fixtureDialog = document.getElementById("fixture-editor");
  if (fixtureDialog) {
    var fixtureForm = document.getElementById("fixture-form");
    var fixtureName = fixtureDialog.getAttribute("data-fixture");
    var fixtureTitle = document.getElementById("fixture-editor-title");
    var fixtureDelete = document.getElementById("fixture-delete");
    var fixtureToken = document.getElementById("admin-token");
    tokenFrom(fixtureToken);
    function setFixtureMode(row, parentUid) {
      fixtureForm.reset(); tokenFrom(fixtureToken);
      var editing = !!row;
      fixtureDialog.dataset.mode = editing ? "edit" : "add";
      fixtureTitle.textContent = editing ? "编辑 BOM 节点" : "新增 BOM 子节点";
      fixtureDelete.hidden = !editing;
      fixtureForm.elements.child_uid.value = editing ? String(row.child_uid || "") : "";
      fixtureForm.querySelectorAll(".editor-grid [name]").forEach(function (input) { input.value = editing ? (row[input.name] == null ? "" : String(row[input.name])) : ""; });
      if (!editing) {
        fixtureForm.elements.parent_uid.value = parentUid || "";
        fixtureForm.elements.quantity.value = "1";
        fixtureForm.elements.unit.value = "EA";
      }
      fixtureDialog.showModal();
    }
    document.querySelectorAll(".fixture-edit").forEach(function (button) { button.addEventListener("click", function () { setFixtureMode(JSON.parse(button.getAttribute("data-row")), ""); }); });
    document.querySelectorAll(".fixture-add-child").forEach(function (button) { button.addEventListener("click", function () { setFixtureMode(null, button.getAttribute("data-parent")); }); });
    var fixtureAdd = document.getElementById("fixture-add");
    if (fixtureAdd) fixtureAdd.addEventListener("click", function () { setFixtureMode(null, ""); });
    document.getElementById("fixture-save").addEventListener("click", function () {
      var mode = fixtureDialog.dataset.mode, payload = {};
      fixtureForm.querySelectorAll(".editor-grid [name]").forEach(function (input) { payload[input.name] = input.value; });
      var childUid = fixtureForm.elements.child_uid.value;
      var path = "/tc/v1/fixtures/" + encodeURIComponent(fixtureName) + "/rows" + (mode === "edit" ? "/" + encodeURIComponent(childUid) : "");
      apiRequest(path, {method: mode === "edit" ? "PATCH" : "POST", body: JSON.stringify(payload)}, fixtureToken)
        .then(function () { toast("BOM 数据已保存，历史快照已自动创建"); window.setTimeout(function () { location.reload(); }, 450); })
        .catch(function (error) { toast(error.message, true); });
    });
    fixtureDelete.addEventListener("click", function () {
      var childUid = fixtureForm.elements.child_uid.value;
      if (!childUid || !window.confirm("确定删除该节点及其全部下级？系统会保留历史快照。")) return;
      var path = "/tc/v1/fixtures/" + encodeURIComponent(fixtureName) + "/rows/" + encodeURIComponent(childUid) + "?cascade=1";
      apiRequest(path, {method: "DELETE"}, fixtureToken)
        .then(function () { toast("节点已删除"); window.setTimeout(function () { location.reload(); }, 450); })
        .catch(function (error) { toast(error.message, true); });
    });
  }

  var standardDialog = document.getElementById("standard-editor");
  if (standardDialog) {
    var standardForm = document.getElementById("standard-form");
    var standardTitle = document.getElementById("standard-editor-title");
    var standardDelete = document.getElementById("standard-delete");
    var standardToken = standardDialog.querySelector(".admin-token-input");
    tokenFrom(standardToken);
    function setStandardMode(line) {
      standardForm.reset(); tokenFrom(standardToken);
      var editing = !!line;
      standardDialog.dataset.mode = editing ? "edit" : "add";
      standardTitle.textContent = editing ? "编辑组件" : "新增组件";
      standardDelete.hidden = !editing;
      standardForm.elements.line_uid.value = editing ? String(line.uid || "") : "";
      if (editing) {
        ["position", "sequence", "quantity", "unit", "notes"].forEach(function (key) { standardForm.elements[key].value = line[key] == null ? "" : String(line[key]); });
        standardForm.elements.child_item_uid.value = line.child_item && line.child_item.uid || "";
      } else {
        standardForm.elements.quantity.value = "1"; standardForm.elements.sequence.value = "10"; standardForm.elements.unit.value = "EA";
      }
      standardDialog.showModal();
    }
    document.querySelectorAll(".standard-edit").forEach(function (button) { button.addEventListener("click", function () { setStandardMode(JSON.parse(button.getAttribute("data-line"))); }); });
    var standardAdd = document.getElementById("standard-add");
    if (standardAdd) standardAdd.addEventListener("click", function () { setStandardMode(null); });
    document.getElementById("standard-save").addEventListener("click", function () {
      var payload = {};
      ["child_item_uid", "position", "sequence", "quantity", "unit", "notes"].forEach(function (key) { payload[key] = standardForm.elements[key].value; });
      var editing = standardDialog.dataset.mode === "edit";
      var path = editing ? "/tc/v1/bomlines/" + encodeURIComponent(standardForm.elements.line_uid.value) : "/tc/v1/items/" + encodeURIComponent(standardDialog.getAttribute("data-item-uid")) + "/bomlines";
      apiRequest(path, {method: editing ? "PATCH" : "POST", body: JSON.stringify(payload)}, standardToken)
        .then(function () { toast("标准 BOM 已保存"); window.setTimeout(function () { location.reload(); }, 450); })
        .catch(function (error) { toast(error.message, true); });
    });
    standardDelete.addEventListener("click", function () {
      var uid = standardForm.elements.line_uid.value;
      if (!uid || !window.confirm("确定删除该 BOM 组件？")) return;
      apiRequest("/tc/v1/bomlines/" + encodeURIComponent(uid), {method: "DELETE"}, standardToken)
        .then(function () { toast("组件已删除"); window.setTimeout(function () { location.reload(); }, 450); })
        .catch(function (error) { toast(error.message, true); });
    });
  }
})();
