from taskhub_v2.node_agent.test_database import TestDatabaseManager as DatabaseManager


class FakeResult:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeConnection:
    def __init__(self, statements, row=None):
        self.statements = statements
        self.row = row

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, statement):
        self.statements.append(statement)
        return FakeResult(self.row)


def test_database_probe_reports_createdb_capability(monkeypatch):
    manager = DatabaseManager("postgresql://admin@localhost/postgres", "TEST_DATABASE_URL")
    monkeypatch.setattr(
        "taskhub_v2.node_agent.test_database.psycopg.connect",
        lambda *args, **kwargs: FakeConnection([], ("16.10", "admin", True)),
    )

    result = manager.probe()

    assert result["available"] is True
    assert result["environment_names"] == ["TEST_DATABASE_URL"]


def test_database_is_created_injected_and_removed(monkeypatch):
    statements = []
    manager = DatabaseManager(
        "postgresql://admin@localhost/postgres",
        "TASKHUB_TEST_POSTGRES_DSN,BE008_TEST_PG_URL",
    )
    monkeypatch.setattr(
        "taskhub_v2.node_agent.test_database.psycopg.connect",
        lambda *args, **kwargs: FakeConnection(statements),
    )

    with manager.database("run-1-acceptance") as environment:
        assert set(environment) == {"TASKHUB_TEST_POSTGRES_DSN", "BE008_TEST_PG_URL"}
        assert all(
            value.startswith("postgresql+psycopg://admin@localhost/taskhub_")
            for value in environment.values()
        )

    assert len(statements) == 3


def test_database_url_escapes_credentials_and_preserves_connection_options(monkeypatch):
    manager = DatabaseManager(
        "host=postgres port=5432 dbname=postgres user='user@example.com' "
        "password='p@ss word' sslmode=require",
        "TASKHUB_TEST_POSTGRES_DSN",
    )
    monkeypatch.setattr(
        "taskhub_v2.node_agent.test_database.psycopg.connect",
        lambda *args, **kwargs: FakeConnection([]),
    )

    with manager.database("run-2") as environment:
        value = environment["TASKHUB_TEST_POSTGRES_DSN"]

    assert value.startswith(
        "postgresql+psycopg://user%40example.com:p%40ss%20word@postgres:5432/taskhub_"
    )
    assert value.endswith("?sslmode=require")
