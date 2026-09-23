import hashlib
import os
import re
from contextlib import contextmanager
from urllib.parse import quote, urlencode

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


class TestDatabaseManager:
    def __init__(self, admin_dsn: str, environment_names: str):
        self.admin_dsn = admin_dsn.strip()
        names = tuple(name.strip() for name in environment_names.split(",") if name.strip())
        if any(not re.fullmatch(r"[A-Z][A-Z0-9_]{1,79}", name) for name in names):
            raise ValueError("invalid test database environment variable name")
        self.environment_names = names or ("TASKHUB_TEST_POSTGRES_DSN",)

    @classmethod
    def from_environment(cls) -> "TestDatabaseManager":
        return cls(
            os.getenv("TASKHUB_TEST_DATABASE_ADMIN_DSN", ""),
            os.getenv("TASKHUB_TEST_DATABASE_ENV_VARS", "TASKHUB_TEST_POSTGRES_DSN"),
        )

    def probe(self) -> dict:
        if not self.admin_dsn:
            return {"configured": False, "available": False, "detail": "未配置管理员 DSN"}
        try:
            with psycopg.connect(self.admin_dsn, connect_timeout=3) as connection:
                row = connection.execute(
                    "SELECT current_setting('server_version'), current_user, "
                    "(SELECT rolsuper OR rolcreatedb FROM pg_roles WHERE rolname = current_user)"
                ).fetchone()
            return {
                "configured": True,
                "available": bool(row and row[2]),
                "version": row[0] if row else "",
                "detail": "连接及建库权限正常" if row and row[2] else "连接正常，但无建库权限",
                "environment_names": list(self.environment_names),
            }
        except Exception as exc:
            return {
                "configured": True,
                "available": False,
                "detail": f"连接或权限检查失败：{exc.__class__.__name__}",
            }

    @contextmanager
    def database(self, job_id: str):
        name = "taskhub_" + hashlib.sha256(job_id.encode()).hexdigest()[:24]
        with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
            drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(name)
            )
            connection.execute(drop)
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        dsn = make_conninfo(**{**conninfo_to_dict(self.admin_dsn), "dbname": name})
        database_url = _sqlalchemy_postgresql_url(dsn)
        try:
            yield {name: database_url for name in self.environment_names}
        finally:
            with psycopg.connect(self.admin_dsn, autocommit=True) as connection:
                connection.execute(drop)


def _sqlalchemy_postgresql_url(conninfo: str) -> str:
    """Expose an interoperable URL instead of libpq's password-bearing key/value form."""

    parameters = conninfo_to_dict(conninfo)
    user = parameters.pop("user", "")
    password = parameters.pop("password", "")
    host = parameters.pop("host", "localhost")
    port = parameters.pop("port", "")
    database = parameters.pop("dbname", "")

    credentials = quote(user, safe="")
    if password:
        credentials += f":{quote(password, safe='')}"
    if credentials:
        credentials += "@"
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    authority = f"{credentials}{host}"
    if port:
        authority += f":{port}"
    query = urlencode({key: value for key, value in parameters.items() if value})
    suffix = f"?{query}" if query else ""
    return f"postgresql+psycopg://{authority}/{quote(database, safe='')}{suffix}"
