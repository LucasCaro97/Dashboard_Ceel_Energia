"""Crea v_consolidado_agua y sp_consolidado_agua_por_periodo en MySQL."""

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
    sql_path = Path("data/agua/sql/v_consolidado_agua.sql")
    engine = build_sqlalchemy_engine(get_db_config())

    with engine.begin() as conn:
        for stmt in _split_sql(sql_path):
            conn.execute(text(stmt))

        view_ok = conn.execute(
            text("SHOW FULL TABLES LIKE 'v_consolidado_agua'")
        ).fetchone()
        proc_ok = conn.execute(
            text(
                """
                SELECT ROUTINE_NAME
                FROM information_schema.ROUTINES
                WHERE ROUTINE_SCHEMA = DATABASE()
                  AND ROUTINE_NAME = 'sp_consolidado_agua_por_periodo'
                """
            )
        ).fetchone()
        view_concepto_ok = conn.execute(
            text("SHOW FULL TABLES LIKE 'v_facturado_agua_concepto_periodo_actual'")
        ).fetchone()
        sample = conn.execute(
            text(
                """
                SELECT periodo, id_concepto, nombre_concepto, total_facturado
                FROM v_facturado_agua_concepto_periodo_actual
                ORDER BY total_facturado DESC
                LIMIT 5
                """
            )
        ).fetchall()

    print("Vista consolidado:", bool(view_ok))
    print("SP consolidado:", bool(proc_ok))
    print("Vista concepto periodo actual:", bool(view_concepto_ok))
    print("Muestra top 5 conceptos:", sample)


if __name__ == "__main__":
    main()
