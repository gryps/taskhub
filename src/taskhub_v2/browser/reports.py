"""Fail closed on absent, empty, skipped or incomplete browser test reports."""
import xml.etree.ElementTree as ET


def validate_junit(
    reports: list[bytes], browsers: list[str], *,
    target_url: str | None = None, git_commit: str | None = None,
) -> None:
    if not reports:
        raise ValueError("browser acceptance requires JUnit results")
    covered = set()
    for report in reports:
        if b"<!DOCTYPE" in report.upper() or b"<!ENTITY" in report.upper():
            raise ValueError("unsafe JUnit report")
        try:
            root = ET.fromstring(report)
        except ET.ParseError as exc:
            raise ValueError("invalid JUnit report") from exc
        cases = list(root.iter("testcase"))
        if not cases:
            raise ValueError("browser acceptance requires executed test cases")
        for element in root.iter():
            if element.tag in {"skipped", "failure", "error"}:
                raise ValueError("browser acceptance requires zero skips and failures")
            for key in ("skipped", "failures", "errors", "disabled"):
                if element.get(key, "0") != "0":
                    raise ValueError("browser acceptance requires zero skips and failures")
        for case in cases:
            properties = {prop.get("name"): prop.get("value")
                          for prop in case.findall("./properties/property")}
            for key, expected in (("target_url", target_url), ("git_commit", git_commit)):
                if expected is not None and properties.get(key) != expected:
                    raise ValueError(f"JUnit test case {key} mismatch")
            # Project tests must identify their browser via a JUnit property.
            for prop in case.findall("./properties/property"):
                if prop.get("name") == "browser":
                    covered.add(prop.get("value"))
    if set(browsers) - covered:
        raise ValueError("JUnit results missing required browser coverage")
