"""Crea v_consolidado_television y sp_consolidado_television_por_periodo en MySQL."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from core.db_manager import build_sqlalchemy_engine, get_db_config


def _split_sql(path: Path):
    raw = path.read_text(encoding="utf-8")
    statements = []
    buf = []
    delimiter = ";"
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(maxsplit=1)[1]
            continue
        buf.append(line)
        if stripped.endswith(delimiter):
            stmt = "\n".join(buf).rstrip()
            if delimiter != ";":
                stmt = stmt[: -len(delimiter)].strip()
            buf = []
            if stmt.strip():
                statements.append(stmt)
    if buf:
        tail = "\n".join(buf).strip()
        if tail:
            statements.append(tail)
    return statements


def main():
    sql_path = Path("data/television/sql/v_consolidado_television.sql")
    engine = build_sqlalchemy_engine(get_db_config())
    with engine.begin() as conn:
        for stmt in _split_sql(sql_path):
            conn.execute(text(stmt))

        view_ok = conn.execute(text("SHOW FULL TABLES LIKE 'v_consolidado_television'")).fetchone()
        proc_ok = conn.execute(
            text(
                "SELECT ROUTINE_NAME FROM information_schema.ROUTINES "
                "WHERE ROUTINE_SCHEMA = DATABASE() "
                "AND ROUTINE_NAME = 'sp_consolidado_television_por_periodo'"
            )
        ).fetchone()
        sample = conn.execute(
            text(
                "SELECT periodo, nro_factura, nro_socio, total_factura, "
                "dinero_television, tarifa_aplicada "
                "FROM v_consolidado_television "
                "WHERE periodo = '2026-06-01' LIMIT 3"
            )
        ).fetchall()

    print("Vista creada:", bool(view_ok))
    print("SP creado:", bool(proc_ok))
    print("Muestra jun-2026:")
    for row in sample:
        print(row)


if __name__ == "__main__":
    main()
