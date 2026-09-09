"""Carga conceptos Agua Potable en conceptos_maestro (id_servicio=2)."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from core.db_manager import get_db_config, build_sqlalchemy_engine

sql_path = Path("data/agua/conceptos/conceptos_maestro_insert.sql")
sql = sql_path.read_text(encoding="utf-8")
stmt = [s for s in sql.split(";") if "INSERT" in s.upper()][0]

engine = build_sqlalchemy_engine(get_db_config())
with engine.begin() as conn:
    conn.execute(text(stmt))
    count = conn.execute(
        text("SELECT COUNT(*) AS n FROM conceptos_maestro WHERE servicio = '2'")
    ).scalar()
    print(f"Conceptos AGUA POTABLE en BD: {count}")
