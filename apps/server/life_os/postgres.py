"""PostgreSQL persistence using the same domain transactions as the SQLite store."""

from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from .store import Store

WRITE_LOCK = 0x4C4946454F53  # One transaction-scoped writer, matching SQLite's semantics.
TABLES = ("metadata", "records", "operations", "changes", "devices", "pairing")


def _driver():
    try:
        import psycopg
        return psycopg
    except ImportError:
        raise RuntimeError("Instala life-os-server[postgres] dentro del entorno virtual.") from None


def connection_parameters(url: str) -> dict:
    psycopg = _driver()
    try:
        values = psycopg.conninfo.conninfo_to_dict(url)
    except psycopg.Error:
        raise ValueError("La conexión PostgreSQL no tiene un formato válido.") from None
    host = values.get("host", "")
    if not host or "," in host or "hostaddr" in values or "service" in values:
        raise ValueError("Configura un único host PostgreSQL explícito, sin hostaddr ni service.")
    if host not in ("localhost", "127.0.0.1", "::1"):
        import certifi
        values["sslmode"] = "verify-full"
        values.setdefault("sslrootcert", certifi.where())
    values["connect_timeout"] = 10
    # PgBouncer rejects session startup options. Apply settings inside transactions instead.
    values.pop("options", None)
    return values


def _connect(url: str):
    driver = _driver()
    try:
        return driver.connect(**connection_parameters(url), autocommit=True, prepare_threshold=None)
    except driver.Error:
        # Connection exceptions must not expose the DSN, password or provider details.
        raise RuntimeError("No se pudo conectar a PostgreSQL; revisa la configuración privada.") from None


class _Connection:
    """Adapt only the internal SQL subset used by Store and Credentials, never user SQL."""
    def __init__(self, raw):
        self.raw = raw

    def _begin(self, *, repeatable=False):
        isolation = "REPEATABLE READ" if repeatable else "READ COMMITTED"
        return self.raw.execute(f"BEGIN ISOLATION LEVEL {isolation}; SET LOCAL search_path=public; "
                                "SET LOCAL statement_timeout='15s'; SET LOCAL lock_timeout='10s'")

    def execute(self, statement, parameters=None):
        if statement == "BEGIN IMMEDIATE":
            self._begin()
            return self.raw.execute("SELECT pg_advisory_xact_lock(%s)", (WRITE_LOCK,))
        if statement == "BEGIN":
            return self._begin(repeatable=True)
        if self.raw.info.transaction_status == _driver().pq.TransactionStatus.IDLE:
            self._begin()
        # All '?' characters in these fixed queries are bound placeholders.
        return self.raw.execute(statement.replace("?", "%s"), parameters)

    def commit(self):
        self.raw.commit()

    def rollback(self):
        self.raw.rollback()


class PostgresStore(Store):
    def __init__(self, url: str):
        self._url = url
        with self.connection() as connection:
            try:
                rows = dict(connection.execute("SELECT key, value FROM public.metadata").fetchall())
            except _driver().errors.UndefinedTable:
                raise RuntimeError("Inicializa PostgreSQL mediante postgres-init antes de arrancar.") from None
        if rows.get("schema_version") != "2":
            raise RuntimeError("Esquema PostgreSQL no compatible; no se modificaron datos.")
        try:
            self.database_id = str(UUID(rows["database_id"]))
        except (KeyError, ValueError):
            raise RuntimeError("La base PostgreSQL no tiene una identidad válida.") from None

    @contextmanager
    def connection(self):
        raw = _connect(self._url)
        try:
            yield _Connection(raw)
            raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()

    @classmethod
    def initialize(cls, url: str):
        raw = _connect(url)
        try:
            db = _Connection(raw)
            db.execute("BEGIN IMMEDIATE")
            present = [name for name in TABLES
                       if raw.execute("SELECT to_regclass(%s)", (f"public.{name}",)).fetchone()[0]]
            if present:
                # An existing schema is only read and validated by the constructor below.
                raw.rollback()
                if set(present) != set(TABLES):
                    raise RuntimeError("Existen tablas parciales; no se intentó completar ni sobrescribir la base.")
            else:
                migration = Path(__file__).parent / "migrations/postgres/001_initial.sql"
                for statement in migration.read_text(encoding="utf-8").split(";"):
                    if statement.strip():
                        raw.execute(statement)
                raw.execute("INSERT INTO public.metadata VALUES ('database_id', %s), ('schema_version', '2')",
                            (str(uuid4()),))
                raw.commit()
        except BaseException:
            raw.rollback()
            raise
        finally:
            raw.close()
        return cls(url)


def open_store(path: Path, database_url: str | None = None) -> Store:
    return PostgresStore(database_url) if database_url else Store(path)
