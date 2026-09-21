from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_python_modules_remain_below_size_limit():
    oversized = []
    for path in (ROOT / "src").rglob("*.py"):
        line_count = len(path.read_text(encoding="utf-8").splitlines())
        if line_count > 400:
            oversized.append(f"{path.relative_to(ROOT)}: {line_count}")
    assert not oversized, "Oversized modules:\n" + "\n".join(oversized)


def test_platform_core_has_no_business_project_coupling():
    forbidden = ("douyin", "抖店", "listing-workbench")
    violations = []
    for path in (ROOT / "src").rglob("*"):
        if not path.is_file() or path.suffix not in {".py", ".js", ".html", ".css"}:
            continue
        content = path.read_text(encoding="utf-8").lower()
        if any(word in content for word in forbidden):
            violations.append(str(path.relative_to(ROOT)))
    assert not violations, f"Business coupling found in: {violations}"
