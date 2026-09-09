"""Despliega SPs del dashboard de Energía en MySQL."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from core.db_manager import get_db_config, build_sqlalchemy_engine


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
    sql_path = Path("data/energia/sql/sp_energia_dashboard.sql")
    engine = build_sqlalchemy_engine(get_db_config())

    with engine.begin() as conn:
        for stmt in _split_sql(sql_path):
            conn.execute(text(stmt))

        procs = conn.execute(
            text(
                """
                SELECT ROUTINE_NAME
                FROM information_schema.ROUTINES
                WHERE ROUTINE_SCHEMA = DATABASE()
                  AND ROUTINE_NAME IN (
                      'sp_kpi_facturacion_por_tarifa',
                      'sp_consolidado_facturas_por_periodo',
                      'sp_kpi_por_sector'
                  )
                ORDER BY ROUTINE_NAME
                """
            )
        ).fetchall()

    print("Procedimientos desplegados:", [row[0] for row in procs])
    print("Listo. Reinicie el dashboard de Streamlit para limpiar cache.")


if __name__ == "__main__":
    main()
