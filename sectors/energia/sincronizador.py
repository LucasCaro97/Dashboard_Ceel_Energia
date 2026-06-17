"""Pipeline mensual de sincronizacion de socios de Energia desde TRYLOGYC."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.db_manager import build_sqlalchemy_engine, get_db_config

ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_INPUT = ROOT / "data" / "energia" / "socios" / "socios_normalizados.csv"
SERVICIO_OBJETIVO = "Energia"
MOTIVO_CAMBIO_TARIFA = "Actualizacion mensual TRYLOGYC"

ESTADO_PRIORIDAD = {
    "activo": 4,
    "stand by": 3,
    "desconectado": 2,
    "baja liquida consumo": 1,
}

TARIFA_EQUIVALENCIAS_RAW = {
    # Nomenclatura TRYLOGYC -> nomenclatura BD
    "Entes de Radiodif.y Telev": "Entes Radio TV",
    "GU-ME >300 KW PEAJE": "GU-ME Peaje",
}


@dataclass
class SyncSummary:
    socios_insertados: int = 0
    socios_actualizados: int = 0
    socios_sin_cambios: int = 0
    medidores_insertados: int = 0
    medidores_mantenidos: int = 0
    medidores_inactivados: int = 0
    tarifas_creadas: int = 0
    tarifas_cambiadas: int = 0
    tarifas_sin_cambios: int = 0
    tarifas_no_mapeadas: int = 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sincroniza socios/medidores/tarifas de Energia.")
    parser.add_argument(
        "--input",
        default=str(DEFAULT_INPUT),
        help="Ruta del CSV normalizado (default: data/socios/socios_normalizados.csv).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Ejecuta todo dentro de transaccion y hace rollback al final.",
    )
    parser.add_argument(
        "--export-reportes-csv",
        action="store_true",
        help="Genera CSVs de control en data/socios/reportes_sincro_socios/.",
    )
    return parser.parse_args()


def validar_columnas(df: pd.DataFrame, columnas_requeridas: Iterable[str]) -> None:
    faltantes = [c for c in columnas_requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas en CSV: {faltantes}")


def parse_fecha_fuente(df: pd.DataFrame) -> datetime.date:
    serie = df["fecha_fuente"].dropna().astype(str).str.strip()
    serie = serie[serie != ""]
    if serie.empty:
        raise ValueError("No se encontro fecha_fuente valida en el CSV.")
    fecha = pd.to_datetime(serie.iloc[0], errors="coerce")
    if pd.isna(fecha):
        raise ValueError(f"fecha_fuente invalida: {serie.iloc[0]}")
    return fecha.date()


def normalizar_texto(valor: object) -> str:
    if valor is None:
        return ""
    text_value = str(valor).strip().lower()
    text_value = re.sub(r"\s+", " ", text_value)
    # Evita diferencias por espacios alrededor de puntuacion (ej: "Serv. Agua" vs "Serv.Agua")
    text_value = re.sub(r"\s*([./-])\s*", r"\1", text_value)
    return text_value


def priorizar_estado(estados: pd.Series) -> str:
    cleaned = estados.fillna("").astype(str).str.strip()
    if cleaned.empty:
        return ""
    ranked = sorted(
        cleaned.tolist(),
        key=lambda s: ESTADO_PRIORIDAD.get(s.lower(), 0),
        reverse=True,
    )
    return ranked[0]


def consolidar_socios(df_energia: pd.DataFrame) -> pd.DataFrame:
    grouped = (
        df_energia.groupby(["nro_socio", "servicio_tipo"], as_index=False)
        .agg(
            nombre_socio=("nombre_socio", lambda s: s.dropna().astype(str).str.strip().replace("", pd.NA).dropna().iloc[0] if not s.dropna().empty else ""),
            estado=("estado", priorizar_estado),
        )
    )
    grouped["nombre_socio"] = grouped["nombre_socio"].fillna("").astype(str).str.strip()
    grouped["estado"] = grouped["estado"].fillna("").astype(str).str.strip()
    return grouped


def consolidar_medidores(df_energia: pd.DataFrame) -> pd.DataFrame:
    med = df_energia.copy()
    med["medidor"] = med["medidor"].fillna("").astype(str).str.strip()
    med = med[med["medidor"] != ""]
    med = med[["nro_socio", "servicio_tipo", "medidor"]].drop_duplicates()
    med = med.rename(columns={"medidor": "nro_medidor"})
    return med


def tarifa_mas_frecuente(series: pd.Series) -> str:
    valores = series.fillna("").astype(str).str.strip()
    valores = valores[valores != ""]
    if valores.empty:
        return ""
    conteo = valores.value_counts()
    return conteo.index[0]


def consolidar_tarifas(df_energia: pd.DataFrame) -> pd.DataFrame:
    tarifas = (
        df_energia.groupby(["nro_socio", "servicio_tipo"], as_index=False)
        .agg(tarifa=("tarifa", tarifa_mas_frecuente))
    )
    tarifas["tarifa"] = tarifas["tarifa"].fillna("").astype(str).str.strip()
    tarifas = tarifas[tarifas["tarifa"] != ""]
    return tarifas


def build_tarifa_mapper(df_tarifa_base: pd.DataFrame):
    exact_map: Dict[str, int] = {}
    candidates: List[Tuple[str, int]] = []
    alias_map: Dict[str, str] = {
        normalizar_texto(k): normalizar_texto(v)
        for k, v in TARIFA_EQUIVALENCIAS_RAW.items()
    }
    for _, row in df_tarifa_base.iterrows():
        nombre = str(row["nombre_base"]).strip()
        tid = int(row["id_tarifa_base"])
        key = normalizar_texto(nombre)
        exact_map[key] = tid
        candidates.append((key, tid))
    # prefijo mas largo primero
    candidates.sort(key=lambda x: len(x[0]), reverse=True)
    return exact_map, candidates, alias_map


def resolver_tarifa_id(
    tarifa_texto: str,
    exact_map: Dict[str, int],
    candidates: List[Tuple[str, int]],
    alias_map: Dict[str, str],
) -> Optional[int]:
    key = normalizar_texto(tarifa_texto)
    if not key:
        return None
    key = alias_map.get(key, key)
    if key in exact_map:
        return exact_map[key]
    for base_name, tid in candidates:
        if key.startswith(base_name):
            return tid
    return None


def load_csv(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8")
    validar_columnas(
        df,
        [
            "nro_socio",
            "nombre_socio",
            "servicio",
            "tarifa",
            "medidor",
            "estado",
            "fecha_fuente",
        ],
    )
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
    return df


def analizar_socios(existing: pd.DataFrame, source: pd.DataFrame) -> Tuple[int, int, int]:
    existing_map = {
        (row["nro_socio"], row["servicio_tipo"]): (
            str(row.get("nombre_socio", "") or ""),
            str(row.get("estado", "") or ""),
        )
        for _, row in existing.iterrows()
    }
    insertados = 0
    actualizados = 0
    sin_cambios = 0
    for _, row in source.iterrows():
        key = (row["nro_socio"], row["servicio_tipo"])
        value = (str(row["nombre_socio"] or ""), str(row["estado"] or ""))
        if key not in existing_map:
            insertados += 1
        elif existing_map[key] != value:
            actualizados += 1
        else:
            sin_cambios += 1
    return insertados, actualizados, sin_cambios


def analizar_medidores(existing: pd.DataFrame, source: pd.DataFrame) -> Tuple[int, int, int]:
    src_set = set(tuple(x) for x in source[["nro_socio", "servicio_tipo", "nro_medidor"]].itertuples(index=False, name=None))
    db_set = set(tuple(x) for x in existing[["nro_socio", "servicio_tipo", "nro_medidor"]].itertuples(index=False, name=None))
    insertados = len(src_set - db_set)
    mantenidos = len(src_set & db_set)
    inactivados = len(db_set - src_set)
    return insertados, mantenidos, inactivados


def analizar_tarifas(existing_vigentes: pd.DataFrame, source_tarifas: pd.DataFrame) -> Tuple[int, int, int]:
    db_map = {
        (row["nro_socio"], row["servicio_tipo"]): int(row["id_tarifa_base"])
        for _, row in existing_vigentes.iterrows()
    }
    creadas = 0
    cambiadas = 0
    sin_cambios = 0
    for _, row in source_tarifas.iterrows():
        key = (row["nro_socio"], row["servicio_tipo"])
        tid = int(row["id_tarifa_base"])
        if key not in db_map:
            creadas += 1
        elif db_map[key] != tid:
            cambiadas += 1
        else:
            sin_cambios += 1
    return creadas, cambiadas, sin_cambios


def exportar_reportes_csv(
    fecha_fuente: datetime.date,
    dataframes: Dict[str, pd.DataFrame],
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "data" / "energia" / "reportes_sincro" / f"{fecha_fuente}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for nombre, df in dataframes.items():
        salida = out_dir / f"{nombre}.csv"
        df.to_csv(salida, index=False, encoding="utf-8")
    print(f"\nReportes CSV generados en: {out_dir}")
    return out_dir


def ejecutar_sync(input_path: Path, dry_run: bool, export_reportes_csv_flag: bool) -> SyncSummary:
    summary = SyncSummary()

    df = load_csv(input_path)
    df_energia = df[df["servicio"].str.lower() == SERVICIO_OBJETIVO.lower()].copy()
    if df_energia.empty:
        raise RuntimeError("No hay filas de servicio Energia para procesar.")

    df_energia["servicio_tipo"] = SERVICIO_OBJETIVO
    fecha_fuente = parse_fecha_fuente(df_energia)
    fecha_cierre = fecha_fuente - timedelta(days=1)

    socios_df = consolidar_socios(df_energia)
    medidores_df = consolidar_medidores(df_energia)
    tarifas_df = consolidar_tarifas(df_energia)

    db_config = get_db_config()
    engine = build_sqlalchemy_engine(db_config)

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # --- Cargar maestros para tarifas ---
            df_tarifa_base = pd.read_sql(
                text("SELECT id_tarifa_base, nombre_base FROM conecciones_energia.tarifa_base"),
                conn,
            )
            exact_map, candidates, alias_map = build_tarifa_mapper(df_tarifa_base)
            tarifas_df["id_tarifa_base"] = tarifas_df["tarifa"].apply(
                lambda t: resolver_tarifa_id(t, exact_map, candidates, alias_map)
            )
            no_map_df = tarifas_df[tarifas_df["id_tarifa_base"].isna()].copy()
            tarifas_map_df = tarifas_df[tarifas_df["id_tarifa_base"].notna()].copy()
            tarifas_map_df["id_tarifa_base"] = tarifas_map_df["id_tarifa_base"].astype(int)
            summary.tarifas_no_mapeadas = len(no_map_df)

            # --- Estado actual en BD para metricas ---
            existing_socios = pd.read_sql(
                text(
                    """
                    SELECT nro_socio, servicio_tipo, COALESCE(nombre_socio,'') AS nombre_socio, COALESCE(estado,'') AS estado
                    FROM conecciones_energia.socios_energia
                    WHERE servicio_tipo = :servicio
                    """
                ),
                conn,
                params={"servicio": SERVICIO_OBJETIVO},
            )
            (
                summary.socios_insertados,
                summary.socios_actualizados,
                summary.socios_sin_cambios,
            ) = analizar_socios(existing_socios, socios_df)
            socios_insertados_df = socios_df.merge(
                existing_socios[["nro_socio", "servicio_tipo"]],
                on=["nro_socio", "servicio_tipo"],
                how="left",
                indicator=True,
            )
            socios_insertados_df = socios_insertados_df[socios_insertados_df["_merge"] == "left_only"][
                ["nro_socio", "servicio_tipo", "nombre_socio", "estado"]
            ].copy()
            socios_actualizados_df = (
                socios_df.merge(
                    existing_socios,
                    on=["nro_socio", "servicio_tipo"],
                    how="inner",
                    suffixes=("_new", "_db"),
                )
            )
            socios_actualizados_df = socios_actualizados_df[
                (socios_actualizados_df["nombre_socio_new"] != socios_actualizados_df["nombre_socio_db"])
                | (socios_actualizados_df["estado_new"] != socios_actualizados_df["estado_db"])
            ][
                [
                    "nro_socio",
                    "servicio_tipo",
                    "nombre_socio_db",
                    "estado_db",
                    "nombre_socio_new",
                    "estado_new",
                ]
            ]

            existing_medidores = pd.read_sql(
                text(
                    """
                    SELECT nro_socio, servicio_tipo, nro_medidor
                    FROM conecciones_energia.socios_medidores
                    WHERE servicio_tipo = :servicio
                    """
                ),
                conn,
                params={"servicio": SERVICIO_OBJETIVO},
            )
            (
                summary.medidores_insertados,
                summary.medidores_mantenidos,
                summary.medidores_inactivados,
            ) = analizar_medidores(existing_medidores, medidores_df)
            medidores_insertados_df = medidores_df.merge(
                existing_medidores[["nro_socio", "servicio_tipo", "nro_medidor"]],
                on=["nro_socio", "servicio_tipo", "nro_medidor"],
                how="left",
                indicator=True,
            )
            medidores_insertados_df = medidores_insertados_df[medidores_insertados_df["_merge"] == "left_only"][
                ["nro_socio", "servicio_tipo", "nro_medidor"]
            ].copy()
            medidores_inactivados_df = existing_medidores.merge(
                medidores_df[["nro_socio", "servicio_tipo", "nro_medidor"]],
                on=["nro_socio", "servicio_tipo", "nro_medidor"],
                how="left",
                indicator=True,
            )
            medidores_inactivados_df = medidores_inactivados_df[
                medidores_inactivados_df["_merge"] == "left_only"
            ][["nro_socio", "servicio_tipo", "nro_medidor"]].copy()

            existing_tarifas = pd.read_sql(
                text(
                    """
                    SELECT nro_socio, servicio_tipo, id_tarifa_base
                    FROM conecciones_energia.socio_historial_tarifas
                    WHERE servicio_tipo = :servicio
                      AND fecha_hasta IS NULL
                    """
                ),
                conn,
                params={"servicio": SERVICIO_OBJETIVO},
            )
            (
                summary.tarifas_creadas,
                summary.tarifas_cambiadas,
                summary.tarifas_sin_cambios,
            ) = analizar_tarifas(existing_tarifas, tarifas_map_df)
            tarifas_creadas_df = tarifas_map_df.merge(
                existing_tarifas[["nro_socio", "servicio_tipo"]],
                on=["nro_socio", "servicio_tipo"],
                how="left",
                indicator=True,
            )
            tarifas_creadas_df = tarifas_creadas_df[tarifas_creadas_df["_merge"] == "left_only"][
                ["nro_socio", "servicio_tipo", "tarifa", "id_tarifa_base"]
            ].copy()
            tarifas_cambiadas_df = (
                tarifas_map_df.merge(
                    existing_tarifas,
                    on=["nro_socio", "servicio_tipo"],
                    how="inner",
                    suffixes=("_new", "_db"),
                )
            )
            tarifas_cambiadas_df = tarifas_cambiadas_df[
                tarifas_cambiadas_df["id_tarifa_base_new"] != tarifas_cambiadas_df["id_tarifa_base_db"]
            ][["nro_socio", "servicio_tipo", "tarifa", "id_tarifa_base_db", "id_tarifa_base_new"]]

            # --- STAGING socios_energia ---
            conn.execute(text("DROP TEMPORARY TABLE IF EXISTS stg_socios_energia"))
            conn.execute(
                text(
                    """
                    CREATE TEMPORARY TABLE stg_socios_energia (
                        nro_socio varchar(20) NOT NULL,
                        servicio_tipo varchar(100) NOT NULL,
                        nombre_socio varchar(150) NULL,
                        estado varchar(20) NULL,
                        PRIMARY KEY (nro_socio, servicio_tipo)
                    )
                    """
                )
            )
            if not socios_df.empty:
                conn.execute(
                    text(
                        """
                        INSERT INTO stg_socios_energia (nro_socio, servicio_tipo, nombre_socio, estado)
                        VALUES (:nro_socio, :servicio_tipo, :nombre_socio, :estado)
                        """
                    ),
                    socios_df.to_dict(orient="records"),
                )
            conn.execute(
                text(
                    """
                    INSERT INTO conecciones_energia.socios_energia (nro_socio, servicio_tipo, nombre_socio, estado)
                    SELECT nro_socio, servicio_tipo, nombre_socio, estado
                    FROM stg_socios_energia
                    ON DUPLICATE KEY UPDATE
                        nombre_socio = VALUES(nombre_socio),
                        estado = IF(COALESCE(socios_energia.estado, '') <> COALESCE(VALUES(estado), ''), VALUES(estado), socios_energia.estado)
                    """
                )
            )

            # --- STAGING medidores snapshot ---
            conn.execute(text("DROP TEMPORARY TABLE IF EXISTS stg_medidores_snapshot"))
            conn.execute(
                text(
                    """
                    CREATE TEMPORARY TABLE stg_medidores_snapshot (
                        nro_socio varchar(20) NOT NULL,
                        servicio_tipo varchar(50) NOT NULL,
                        nro_medidor varchar(50) NOT NULL,
                        PRIMARY KEY (nro_socio, servicio_tipo, nro_medidor)
                    )
                    """
                )
            )
            if not medidores_df.empty:
                conn.execute(
                    text(
                        """
                        INSERT INTO stg_medidores_snapshot (nro_socio, servicio_tipo, nro_medidor)
                        VALUES (:nro_socio, :servicio_tipo, :nro_medidor)
                        """
                    ),
                    medidores_df.to_dict(orient="records"),
                )
            # Insert nuevos medidores (no tocar ACT)
            conn.execute(
                text(
                    """
                    INSERT INTO conecciones_energia.socios_medidores (nro_socio, servicio_tipo, nro_medidor, estado)
                    SELECT s.nro_socio, s.servicio_tipo, s.nro_medidor, 1
                    FROM stg_medidores_snapshot s
                    LEFT JOIN conecciones_energia.socios_medidores m
                      ON m.nro_socio = s.nro_socio
                     AND m.servicio_tipo = s.servicio_tipo
                     AND m.nro_medidor = s.nro_medidor
                    WHERE m.id_medidor_registro IS NULL
                    """
                )
            )
            # Inactivar medidores ausentes en snapshot del periodo
            conn.execute(
                text(
                    """
                    UPDATE conecciones_energia.socios_medidores m
                    LEFT JOIN stg_medidores_snapshot s
                      ON s.nro_socio = m.nro_socio
                     AND s.servicio_tipo = m.servicio_tipo
                     AND s.nro_medidor = m.nro_medidor
                    SET m.estado = 0
                    WHERE m.servicio_tipo = :servicio
                      AND s.nro_socio IS NULL
                      AND COALESCE(m.estado, 1) <> 0
                    """
                ),
                {"servicio": SERVICIO_OBJETIVO},
            )

            # --- STAGING tarifas mapeadas ---
            conn.execute(text("DROP TEMPORARY TABLE IF EXISTS stg_tarifas_objetivo"))
            conn.execute(
                text(
                    """
                    CREATE TEMPORARY TABLE stg_tarifas_objetivo (
                        nro_socio varchar(50) NOT NULL,
                        servicio_tipo varchar(50) NOT NULL,
                        id_tarifa_base int NOT NULL,
                        PRIMARY KEY (nro_socio, servicio_tipo)
                    )
                    """
                )
            )
            if not tarifas_map_df.empty:
                conn.execute(
                    text(
                        """
                        INSERT INTO stg_tarifas_objetivo (nro_socio, servicio_tipo, id_tarifa_base)
                        VALUES (:nro_socio, :servicio_tipo, :id_tarifa_base)
                        """
                    ),
                    tarifas_map_df[["nro_socio", "servicio_tipo", "id_tarifa_base"]].to_dict(orient="records"),
                )

            # Cerrar vigentes cambiadas
            conn.execute(
                text(
                    """
                    UPDATE conecciones_energia.socio_historial_tarifas h
                    JOIN stg_tarifas_objetivo s
                      ON s.nro_socio = h.nro_socio
                     AND s.servicio_tipo = h.servicio_tipo
                    SET h.fecha_hasta = :fecha_cierre
                    WHERE h.fecha_hasta IS NULL
                      AND COALESCE(h.id_tarifa_base, -1) <> COALESCE(s.id_tarifa_base, -1)
                    """
                ),
                {"fecha_cierre": fecha_cierre},
            )

            # Insertar nuevas vigentes (faltantes o cambiadas)
            conn.execute(
                text(
                    """
                    INSERT INTO conecciones_energia.socio_historial_tarifas
                        (nro_socio, servicio_tipo, id_tarifa_base, fecha_desde, fecha_hasta, motivo_cambio)
                    SELECT
                        s.nro_socio,
                        s.servicio_tipo,
                        s.id_tarifa_base,
                        :fecha_fuente,
                        NULL,
                        :motivo
                    FROM stg_tarifas_objetivo s
                    LEFT JOIN conecciones_energia.socio_historial_tarifas h
                      ON h.nro_socio = s.nro_socio
                     AND h.servicio_tipo = s.servicio_tipo
                     AND h.fecha_hasta IS NULL
                    WHERE h.id_historial IS NULL
                       OR COALESCE(h.id_tarifa_base, -1) <> COALESCE(s.id_tarifa_base, -1)
                    """
                ),
                {"fecha_fuente": fecha_fuente, "motivo": MOTIVO_CAMBIO_TARIFA},
            )

            if dry_run:
                trans.rollback()
                print("DRY-RUN: rollback ejecutado, sin cambios persistidos.")
            else:
                trans.commit()

            if not no_map_df.empty:
                resumen_no_mapeadas = (
                    no_map_df.groupby("tarifa", as_index=False)
                    .agg(
                        registros=("nro_socio", "size"),
                        socios_unicos=("nro_socio", "nunique"),
                        ejemplo_nro_socio=("nro_socio", "first"),
                    )
                    .sort_values(by=["registros", "tarifa"], ascending=[False, True])
                )
                print("\nTarifas sin mapear (todas, agrupadas):")
                print(resumen_no_mapeadas.to_string(index=False))

            if export_reportes_csv_flag:
                exportar_reportes_csv(
                    fecha_fuente=fecha_fuente,
                    dataframes={
                        "socios_insertados": socios_insertados_df,
                        "socios_actualizados": socios_actualizados_df,
                        "medidores_insertados": medidores_insertados_df,
                        "medidores_inactivados": medidores_inactivados_df,
                        "tarifas_creadas": tarifas_creadas_df,
                        "tarifas_cambiadas": tarifas_cambiadas_df,
                        "tarifas_no_mapeadas": no_map_df[["nro_socio", "servicio_tipo", "tarifa"]].copy(),
                    },
                )
            if not socios_actualizados_df.empty:
                print("\nMuestra de socios actualizados (antes -> despues):")
                print(socios_actualizados_df.head(5).to_string(index=False))
            if not tarifas_cambiadas_df.empty:
                print("\nMuestra de cambios de tarifa (vigente -> nueva):")
                print(tarifas_cambiadas_df.head(5).to_string(index=False))

            return summary
        except Exception:
            trans.rollback()
            raise


def print_summary(summary: SyncSummary) -> None:
    print("\n=== RESUMEN SINCRONIZACION SOCIOS ENERGIA ===")
    print(f"Socios insertados:   {summary.socios_insertados:,}")
    print(f"Socios actualizados: {summary.socios_actualizados:,}")
    print(f"Socios sin cambios:  {summary.socios_sin_cambios:,}")
    print("---")
    print(f"Medidores insertados:  {summary.medidores_insertados:,}")
    print(f"Medidores mantenidos:  {summary.medidores_mantenidos:,}")
    print(f"Medidores inactivados: {summary.medidores_inactivados:,}")
    print("---")
    print(f"Tarifas creadas:      {summary.tarifas_creadas:,}")
    print(f"Tarifas cambiadas:    {summary.tarifas_cambiadas:,}")
    print(f"Tarifas sin cambios:  {summary.tarifas_sin_cambios:,}")
    print(f"Tarifas no mapeadas:  {summary.tarifas_no_mapeadas:,}")
    print("=============================================\n")


def main() -> None:
    args = parse_args()
    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")

    summary = ejecutar_sync(
        input_path=input_path,
        dry_run=args.dry_run,
        export_reportes_csv_flag=args.export_reportes_csv,
    )
    print_summary(summary)


if __name__ == "__main__":
    main()
