"""Fail closed on absent, empty, skipped or incomplete browser test reports."""
import re
import xml.etree.ElementTree as ET


_ANSI_ESCAPE = re.compile(rb"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_ILLEGAL_XML10_CONTROL = re.compile(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def setup_failure_detail(tests: list, setup_count: int) -> str | None:
    failed = next((test for test in tests[:setup_count] if test.exit_code), None)
    if failed is None:
        return None
    return failed.output_tail or "setup command failed"


def first_junit_failure_detail(reports: list[bytes]) -> str | None:
    for report in reports:
        if b"<!DOCTYPE" in report.upper() or b"<!ENTITY" in report.upper():
            continue
        try:
            root = _parse_junit(report)
        except ValueError:
            continue
        for element in root.iter():
            if element.tag not in {"failure", "error"}:
                continue
            detail = "\n".join(
                item.strip() for item in (element.get("message", ""), element.text or "")
                if item.strip()
            )
            if detail:
                return detail[-2000:]
    return None


def _parse_junit(report: bytes) -> ET.Element:
    try:
        return ET.fromstring(report)
    except ET.ParseError as original:
        # Some JUnit producers copy colorized terminal output into failure nodes.
        # Raw ANSI/C0 bytes are not legal XML 1.0, but do not invalidate the
        # underlying test evidence. Strip only those bytes and retry; all other
        # malformed reports still fail closed.
        sanitized = _ILLEGAL_XML10_CONTROL.sub(b"", _ANSI_ESCAPE.sub(b"", report))
        if sanitized == report:
            raise ValueError("invalid JUnit report") from original
        try:
            return ET.fromstring(sanitized)
        except ET.ParseError as sanitized_error:
            raise ValueError("invalid JUnit report") from sanitized_error


def validate_junit(
    reports: list[bytes], browsers: list[str], *,
    target_url: str | None = None, git_commit: str | None = None,
    scenarios: dict[str, set[str]] | None = None,
) -> None:
    if not reports:
        raise ValueError("browser acceptance requires JUnit results")
    covered = set()
    completed = set()
    for report in reports:
        if b"<!DOCTYPE" in report.upper() or b"<!ENTITY" in report.upper():
            raise ValueError("unsafe JUnit report")
        root = _parse_junit(report)
        cases = list(root.iter("testcase"))
        if not cases:
            raise ValueError("browser acceptance requires executed test cases")
        failures = [
            (element.text or "").strip()
            for element in root.iter()
            if element.tag in {"failure", "error"}
        ]
        if failures:
            raise ValueError("browser acceptance failed: " + failures[0][-2000:])
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
            browser = properties.get("browser")
            for prop in case.findall("./properties/property"):
                if prop.get("name", "").startswith("scenario.") and prop.get("value") == "passed":
                    completed.add((prop.get("name")[9:], browser))
            # Project tests must identify their browser via a JUnit property.
            for prop in case.findall("./properties/property"):
                if prop.get("name") == "browser":
                    covered.add(prop.get("value"))
    if set(browsers) - covered:
        raise ValueError("JUnit results missing required browser coverage")
    required = {(scenario, browser) for scenario, matrix in (scenarios or {}).items()
                for browser in matrix}
    missing = required - completed
    if missing:
        missing_labels = ", ".join(
            f"{scenario}/{browser}" for scenario, browser in sorted(missing)
        )
        raise ValueError(f"JUnit results missing required scenario coverage: {missing_labels}")
