from __future__ import annotations

from loreloop.knowledge.authoritative_detector_markup import detect_markup_source
from loreloop.knowledge.authoritative_detector_typescript import detect_typescript_source


def test_aspx_detector_extracts_page_controls_events_and_targets() -> None:
    source = """<%@ Page Language="C#" Title="人员维护" Inherits="EmployeePage" %>
<form id="employeeForm" action="/employee/save" onsubmit="validateEmployee">
  <asp:TextBox ID="txtName" runat="server" />
  <asp:Button ID="btnSave" runat="server" Text="保存" OnClick="btnSave_Click" />
  <a id="backLink" href="/employee/list">返回</a>
</form>
"""

    report = detect_markup_source(source, ".", "Web/Employee.aspx")

    assert [(item.name, item.surface_type, item.entry) for item in report.ui_surfaces] == [
        ("人员维护", "page", "Web/Employee.aspx")
    ]
    assert report.ui_surfaces[0].actions == (
        "submit:validateEmployee",
        "click:btnSave_Click",
    )
    assert {(item.predicate, item.object, item.detail) for item in report.implementation_facts} >= {
        ("controls", "form:employeeForm", None),
        ("controls", "asp:TextBox:txtName", None),
        ("controls", "asp:Button:btnSave", "保存"),
        ("calls", "/employee/save", "form target"),
        ("calls", "/employee/list", "a target"),
    }


def test_resx_detector_keeps_readable_resource_text_and_skips_binary_payloads() -> None:
    source = (
        """<root>
  <data name="btnSave.Text"><value>保存</value></data>
  <data name="Logo.Image" mimetype="application/x-microsoft.net.object.bytearray.base64">
    <value>"""
        + "A" * 2_000
        + """</value>
  </data>
</root>
"""
    )

    report = detect_markup_source(source, ".", "Forms/Employee.resx")

    assert [(item.object, item.detail) for item in report.implementation_facts] == [
        ("resource:btnSave.Text", "保存")
    ]


def test_xml_detector_extracts_named_settings_without_exposing_secrets() -> None:
    source = """<settings>
  <add key="ServiceUrl" value="https://example.invalid/api" />
  <add key="ApiToken" value="must-not-leak" />
</settings>
"""

    report = detect_markup_source(source, ".", "config/runtime.xml")

    values = {item.key: (item.default, item.redacted) for item in report.configurations}
    assert values == {
        "ServiceUrl": ("https://example.invalid/api", False),
        "ApiToken": (None, True),
    }
    assert report.ui_surfaces == ()
    assert "must-not-leak" not in repr(report)


def test_documentation_html_is_not_presented_as_a_product_page() -> None:
    report = detect_markup_source(
        "<html><head><title>NAnt Help</title></head><body>Reference</body></html>",
        ".",
        "Tools/nant/doc/help/index.html",
    )

    assert report.ui_surfaces == ()


def test_legacy_javascript_detector_extracts_non_module_functions_events_and_requests() -> None:
    source = """
function saveEmployee(id) { return id; }
Employee.prototype.reload = function(force) { return force; }
button.onclick = saveEmployee;
panel.addEventListener("change", reloadPanel);
fetch("/api/employees");
xhr.open("POST", "/api/employees/save");
window.location.href = "/employees";
"""

    report = detect_typescript_source(source, ".", "Web/scripts/employee.js")

    assert {item.qualified_name for item in report.symbols} >= {
        "saveEmployee",
        "Employee.reload",
    }
    assert {(item.predicate, item.object) for item in report.implementation_facts} >= {
        ("controls", "click:saveEmployee"),
        ("controls", "change:reloadPanel"),
        ("calls", "/api/employees"),
        ("calls", "/api/employees/save"),
        ("calls", "/employees"),
    }
