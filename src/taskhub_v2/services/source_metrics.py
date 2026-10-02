import ast
from dataclasses import dataclass


@dataclass(frozen=True)
class PythonFunctionMetric:
    name: str
    line: int
    lines: int
    complexity: int


class _ComplexityVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.value = 1

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return None

    def visit_If(self, node: ast.If) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_While(self, node: ast.While) -> None:
        self.value += 1
        self.generic_visit(node)

    def visit_BoolOp(self, node: ast.BoolOp) -> None:
        self.value += max(0, len(node.values) - 1)
        self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        self.value += len(node.handlers)
        self.generic_visit(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self.value += len(node.handlers)
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        self.value += max(0, len(node.cases) - 1)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        self.value += 1 + len(node.ifs)
        self.generic_visit(node)


def python_function_metrics(source: str) -> list[PythonFunctionMetric]:
    tree = ast.parse(source)
    collector = _MetricCollector()
    collector.visit(tree)
    return collector.metrics


class _MetricCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.metrics: list[PythonFunctionMetric] = []
        self._scope: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        if not node.end_lineno:
            return
        visitor = _ComplexityVisitor()
        for statement in node.body:
            visitor.visit(statement)
        name = ".".join([*self._scope, node.name])
        self.metrics.append(
            PythonFunctionMetric(
                name=name,
                line=node.lineno,
                lines=node.end_lineno - node.lineno + 1,
                complexity=visitor.value,
            )
        )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()
