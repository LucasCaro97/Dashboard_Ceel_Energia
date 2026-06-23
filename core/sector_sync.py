# -*- coding: utf-8 -*-
"""
Logica generica de sincronizacion de socios/medidores/tarifas desde TRYLOGYC.

Cada sector define un SectorSyncConfig con sus parametros especificos y
llama a ejecutar_sync_sector(). La logica transaccional, el manejo de
staging tables y la deteccion de cambios son comunes a todos los sectores.

Uso tipico desde un modulo de sector:

    from core.sector_sync import SectorSyncConfig, SyncSummary
    from core.sector_sync import ejecutar_sync_sector, print_summary_sector

    CONFIG = SectorSyncConfig(
        servicio_objetivo="Agua",
        db_schema="conecciones_energia",
        tabla_socios="socios_agua",
        ...
    )

    summary = ejecutar_sync_sector(input_path, dry_run, export_csv, CONFIG)
    print_summary_sector(summary, CONFIG)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from sqlalchemy import text

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.db_manager import build_sqlalchemy_engine, get_db_config


# Prioridad de estados para consolidar multiples suministros por socio
ESTADO_PRIORIDAD = {
    "activo": 4,
    "stand by": 3,
    "desconectado": 2,
    "baja liquida consumo": 1,
}

MOTIVO_CAMBIO_TARIFA_DEFAULT = "Actualizacion mensual TRYLOGYC"

# Debe coincidir con la collation de las tablas permanentes (socios_*, socios_medidores, etc.)
MYSQL_COLLATION = "utf8mb4_0900_ai_ci"


@dataclass
class SectorSyncConfig:
    """
    Parametros de sincronizacion especificos de un sector.

    Attributes:
        servicio_objetivo: Valor exacto del campo ``servicio`` en el CSV
            normalizado (ej. "Energia", "Agua", "Gas").
        db_schema: Schema MySQL donde estan las tablas del sector
            (ej. "conecciones_energia").
        tabla_socios: Tabla de socios del sector (ej. "socios_energia").
        tabla_medidores: Tabla de medidores, generalmente compartida entre
            sectores (ej. "socios_medidores").
        tabla_tarifas: Tabla de historial de tarifas, generalmente compartida
            (ej. "socio_historial_tarifas").
        tabla_tarifa_base: Catalogo de tarifas base (ej. "tarifas_base").
        tarifa_equivalencias: Mapeo TRYLOGYC -> BD para nombres de tarifa
            que difieren entre sistemas.
        reportes_dir: Directorio base donde se generan los CSVs de control.
        tiene_medidores: Si False, omite todo el bloque de sincronizacion de
            medidores (util para sectores de tarifa plana como Internet o
            Television donde no hay medicion por consumo).
        motivo_cambio_tarifa: Texto registrado al crear/cambiar una tarifa.
    """
    servicio_objetivo: str
    db_schema: str
    tabla_socios: str
    tabla_medidores: str
    tabla_tarifas: str
    tabla_tarifa_base: str
    tarifa_equivalencias: Dict[str, str]
    reportes_dir: Path
    tiene_medidores: bool = True
    motivo_cambio_tarifa: str = MOTIVO_CAMBIO_TARIFA_DEFAULT


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


# ---------------------------------------------------------------------------
# Helpers de validacion y parsing
# ---------------------------------------------------------------------------

def validar_columnas(df: pd.DataFrame, columnas_requeridas: Iterable[str]) -> None:
    faltantes = [c for c in columnas_requeridas if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas en CSV: {faltantes}")


def parse_fecha_fuente(df: pd.DataFrame):
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
    t = str(valor).strip().lower()
    t = re.sub(r"\s+", " ", t)
    # Normaliza espacios alrededor de puntuacion para comparaciones robustas
    t = re.sub(r"\s*([./-])\s*", r"\1", t)
    return t


# ---------------------------------------------------------------------------
# Consolidacion de datos del CSV
# ---------------------------------------------------------------------------

def _priorizar_estado(estados: pd.Series) -> str:
    cleaned = estados.fillna("").astype(str).str.strip()
    if cleaned.empty:
        return ""
    ranked = sorted(
        cleaned.tolist(),
        key=lambda s: ESTADO_PRIORIDAD.get(s.lower(), 0),
        reverse=True,
    )
    return ranked[0]


def consolidar_socios(df_sector: pd.DataFrame) -> pd.DataFrame:
    """Un registro por (nro_socio, servicio_tipo) con el estado de mayor prioridad."""
    grouped = (
        df_sector.groupby(["nro_socio", "servicio_tipo"], as_index=False)
        .agg(
            nombre_socio=(
                "nombre_socio",
                lambda s: (
                    s.dropna().astype(str).str.strip()
                    .replace("", pd.NA).dropna().iloc[0]
                    if not s.dropna().empty else ""
                ),
            ),
            estado=("estado", _priorizar_estado),
        )
    )
    grouped["nombre_socio"] = grouped["nombre_socio"].fillna("").astype(str).str.strip()
    grouped["estado"] = grouped["estado"].fillna("").astype(str).str.strip()
    return grouped


def consolidar_medidores(df_sector: pd.DataFrame) -> pd.DataFrame:
    """Todos los medidores distintos por (nro_socio, servicio_tipo)."""
    med = df_sector.copy()
    med["medidor"] = med["medidor"].fillna("").astype(str).str.strip()
    med = med[med["medidor"] != ""]
    med = med[["nro_socio", "servicio_tipo", "medidor"]].drop_duplicates()
    return med.rename(columns={"medidor": "nro_medidor"})


def _tarifa_mas_frecuente(series: pd.Series) -> str:
    valores = series.fillna("").astype(str).str.strip()
    valores = valores[valores != ""]
    if valores.empty:
        return ""
    return valores.value_counts().index[0]


def consolidar_tarifas(df_sector: pd.DataFrame) -> pd.DataFrame:
    """Tarifa mas frecuente por (nro_socio, servicio_tipo)."""
    tarifas = (
        df_sector.groupby(["nro_socio", "servicio_tipo"], as_index=False)
        .agg(tarifa=("tarifa", _tarifa_mas_frecuente))
    )
    tarifas["tarifa"] = tarifas["tarifa"].fillna("").astype(str).str.strip()
    return tarifas[tarifas["tarifa"] != ""]


# ---------------------------------------------------------------------------
# Mapeo de tarifas TRYLOGYC -> BD
# ---------------------------------------------------------------------------

def build_tarifa_mapper(
    df_tarifa_base: pd.DataFrame,
    equivalencias: Dict[str, str],
) -> Tuple[Dict[str, int], List[Tuple[str, int]], Dict[str, str]]:
    exact_map: Dict[str, int] = {}
    candidates: List[Tuple[str, int]] = []
    alias_map: Dict[str, str] = {
        normalizar_texto(k): normalizar_texto(v)
        for k, v in equivalencias.items()
    }
    for _, row in df_tarifa_base.iterrows():
        nombre = str(row["nombre_tarifa"]).strip()
        tid = int(row["id_tarifa"])
        key = normalizar_texto(nombre)
        exact_map[key] = tid
        candidates.append((key, tid))
    # Prefijo mas largo primero para evitar falsos positivos
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


# ---------------------------------------------------------------------------
# Carga del CSV normalizado
# ---------------------------------------------------------------------------

def load_csv(input_path: Path) -> pd.DataFrame:
    df = pd.read_csv(input_path, dtype=str, keep_default_na=False, encoding="utf-8")
    validar_columnas(
        df,
        ["nro_socio", "nombre_socio", "servicio", "tarifa", "medidor", "estado", "fecha_fuente"],
    )
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].astype(str).str.strip()
    return df


# ---------------------------------------------------------------------------
# Analisis de diferencias (para el resumen)
# ---------------------------------------------------------------------------

def analizar_socios(
    existing: pd.DataFrame, source: pd.DataFrame
) -> Tuple[int, int, int]:
    existing_map = {
        (row["nro_socio"], row["servicio_tipo"]): (
            str(row.get("nombre_socio", "") or ""),
            str(row.get("estado", "") or ""),
        )
        for _, row in existing.iterrows()
    }
    insertados = actualizados = sin_cambios = 0
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


def analizar_medidores(
    existing: pd.DataFrame, source: pd.DataFrame
) -> Tuple[int, int, int]:
    cols = ["nro_socio", "servicio_tipo", "nro_medidor"]
    src_set = set(source[cols].itertuples(index=False, name=None))
    db_set = set(existing[cols].itertuples(index=False, name=None))
    return len(src_set - db_set), len(src_set & db_set), len(db_set - src_set)


def analizar_tarifas(
    existing_vigentes: pd.DataFrame, source_tarifas: pd.DataFrame
) -> Tuple[int, int, int]:
    db_map = {
        (row["nro_socio"], row["servicio_tipo"]): int(row["id_tarifa_base"])
        for _, row in existing_vigentes.iterrows()
    }
    creadas = cambiadas = sin_cambios = 0
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


# ---------------------------------------------------------------------------
# Exportacion de reportes CSV
# ---------------------------------------------------------------------------

def exportar_reportes_csv(
    fecha_fuente,
    dataframes: Dict[str, pd.DataFrame],
    reportes_dir: Path,
) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = reportes_dir / f"{fecha_fuente}_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for nombre, df in dataframes.items():
        df.to_csv(out_dir / f"{nombre}.csv", index=False, encoding="utf-8")
    print(f"\nReportes CSV generados en: {out_dir}")
    return out_dir


# ---------------------------------------------------------------------------
# Pipeline principal
# ---------------------------------------------------------------------------

def ejecutar_sync_sector(
    input_path: Path,
    dry_run: bool,
    export_reportes_csv_flag: bool,
    config: SectorSyncConfig,
) -> SyncSummary:
    """
    Ejecuta el pipeline completo de sincronizacion para un sector.

    Flujo:
      1. Lee el CSV normalizado y filtra por ``config.servicio_objetivo``.
      2. Consolida socios, medidores y tarifas.
      3. Abre una transaccion MySQL.
      4. Upsert en tabla de socios via staging table.
      5. Inserta medidores nuevos e inactiva los ausentes.
      6. Cierra vigencias de tarifa cambiadas e inserta las nuevas.
      7. Commit (o rollback si dry_run=True).
      8. Genera reportes CSV si se solicita.

    Args:
        input_path: CSV normalizado (salida de ``normalizar()``).
        dry_run: Si True, ejecuta todo en transaccion y hace rollback al final.
        export_reportes_csv_flag: Si True, genera CSVs de control.
        config: Parametros del sector.

    Returns:
        SyncSummary con los conteos de cambios.
    """
    summary = SyncSummary()

    df = load_csv(input_path)
    df_sector = df[df["servicio"].str.lower() == config.servicio_objetivo.lower()].copy()
    if df_sector.empty:
        raise RuntimeError(
            f"No hay filas de servicio '{config.servicio_objetivo}' en {input_path}."
        )

    df_sector["servicio_tipo"] = config.servicio_objetivo
    fecha_fuente = parse_fecha_fuente(df_sector)
    fecha_cierre = fecha_fuente - timedelta(days=1)

    print(f"\n  Procesando {len(df_sector):,} filas de servicio '{config.servicio_objetivo}'")
    print(f"  Fecha fuente: {fecha_fuente}  /  Fecha cierre vigencias: {fecha_cierre}")

    socios_df = consolidar_socios(df_sector)
    medidores_df = consolidar_medidores(df_sector)
    tarifas_df = consolidar_tarifas(df_sector)

    engine = build_sqlalchemy_engine(get_db_config())

    schema = config.db_schema
    t_socios = f"{schema}.{config.tabla_socios}"
    t_medidores = f"{schema}.{config.tabla_medidores}"
    t_tarifas = f"{schema}.{config.tabla_tarifas}"
    t_tarifa_base = f"{schema}.{config.tabla_tarifa_base}"

    # Nombre de staging tables sin caracteres especiales
    stg_suffix = re.sub(r"[^a-z0-9]", "_", config.servicio_objetivo.lower())
    stg_socios = f"stg_socios_{stg_suffix}"

    with engine.connect() as conn:
        trans = conn.begin()
        try:
            # ----------------------------------------------------------------
            # 1. Resolver IDs de tarifa base
            # ----------------------------------------------------------------
            df_tarifa_base = pd.read_sql(
                text(f"SELECT id_tarifa, nombre_tarifa FROM {t_tarifa_base}"),
                conn,
            )
            exact_map, candidates, alias_map = build_tarifa_mapper(
                df_tarifa_base, config.tarifa_equivalencias
            )
            tarifas_df["id_tarifa_base"] = tarifas_df["tarifa"].apply(
                lambda t: resolver_tarifa_id(t, exact_map, candidates, alias_map)
            )
            no_map_df = tarifas_df[tarifas_df["id_tarifa_base"].isna()].copy()
            tarifas_map_df = tarifas_df[tarifas_df["id_tarifa_base"].notna()].copy()
            tarifas_map_df["id_tarifa_base"] = tarifas_map_df["id_tarifa_base"].astype(int)
            summary.tarifas_no_mapeadas = len(no_map_df)

            # ----------------------------------------------------------------
            # 2. Leer estado actual en BD para calcular metricas
            # ----------------------------------------------------------------
            existing_socios = pd.read_sql(
                text(
                    f"""
                    SELECT nro_socio, servicio_tipo,
                           COALESCE(nombre_socio, '') AS nombre_socio,
                           COALESCE(estado, '') AS estado
                    FROM {t_socios}
                    WHERE servicio_tipo = :servicio
                    """
                ),
                conn,
                params={"servicio": config.servicio_objetivo},
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
            socios_insertados_df = socios_insertados_df[
                socios_insertados_df["_merge"] == "left_only"
            ][["nro_socio", "servicio_tipo", "nombre_socio", "estado"]].copy()

            socios_actualizados_df = socios_df.merge(
                existing_socios,
                on=["nro_socio", "servicio_tipo"],
                how="inner",
                suffixes=("_new", "_db"),
            )
            socios_actualizados_df = socios_actualizados_df[
                (socios_actualizados_df["nombre_socio_new"] != socios_actualizados_df["nombre_socio_db"])
                | (socios_actualizados_df["estado_new"] != socios_actualizados_df["estado_db"])
            ][["nro_socio", "servicio_tipo", "nombre_socio_db", "estado_db",
               "nombre_socio_new", "estado_new"]]

            medidores_insertados_df = pd.DataFrame(columns=["nro_socio", "servicio_tipo", "nro_medidor"])
            medidores_inactivados_df = pd.DataFrame(columns=["nro_socio", "servicio_tipo", "nro_medidor"])

            if config.tiene_medidores:
                existing_medidores = pd.read_sql(
                    text(
                        f"""
                        SELECT nro_socio, servicio_tipo, nro_medidor
                        FROM {t_medidores}
                        WHERE servicio_tipo = :servicio
                        """
                    ),
                    conn,
                    params={"servicio": config.servicio_objetivo},
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
                medidores_insertados_df = medidores_insertados_df[
                    medidores_insertados_df["_merge"] == "left_only"
                ][["nro_socio", "servicio_tipo", "nro_medidor"]].copy()

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
                    f"""
                    SELECT nro_socio, servicio_tipo, id_tarifa_base
                    FROM {t_tarifas}
                    WHERE servicio_tipo = :servicio
                      AND fecha_hasta IS NULL
                    """
                ),
                conn,
                params={"servicio": config.servicio_objetivo},
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
            tarifas_creadas_df = tarifas_creadas_df[
                tarifas_creadas_df["_merge"] == "left_only"
            ][["nro_socio", "servicio_tipo", "tarifa", "id_tarifa_base"]].copy()

            tarifas_cambiadas_df = tarifas_map_df.merge(
                existing_tarifas,
                on=["nro_socio", "servicio_tipo"],
                how="inner",
                suffixes=("_new", "_db"),
            )
            tarifas_cambiadas_df = tarifas_cambiadas_df[
                tarifas_cambiadas_df["id_tarifa_base_new"] != tarifas_cambiadas_df["id_tarifa_base_db"]
            ][["nro_socio", "servicio_tipo", "tarifa", "id_tarifa_base_db", "id_tarifa_base_new"]]

            # ----------------------------------------------------------------
            # 3. Upsert socios via staging table
            # ----------------------------------------------------------------
            conn.execute(text(f"DROP TEMPORARY TABLE IF EXISTS {stg_socios}"))
            conn.execute(
                text(
                    f"""
                    CREATE TEMPORARY TABLE {stg_socios} (
                        nro_socio    varchar(20)  NOT NULL,
                        servicio_tipo varchar(100) NOT NULL,
                        nombre_socio varchar(150) NULL,
                        estado       varchar(20)  NULL,
                        PRIMARY KEY (nro_socio, servicio_tipo)
                    ) CHARACTER SET utf8mb4 COLLATE {MYSQL_COLLATION}
                    """
                )
            )
            if not socios_df.empty:
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {stg_socios} (nro_socio, servicio_tipo, nombre_socio, estado)
                        VALUES (:nro_socio, :servicio_tipo, :nombre_socio, :estado)
                        """
                    ),
                    socios_df.to_dict(orient="records"),
                )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {t_socios} (nro_socio, servicio_tipo, nombre_socio, estado)
                    SELECT nro_socio, servicio_tipo, nombre_socio, estado
                    FROM {stg_socios}
                    ON DUPLICATE KEY UPDATE
                        nombre_socio = VALUES(nombre_socio),
                        estado = IF(
                            COALESCE({config.tabla_socios}.estado, '') <> COALESCE(VALUES(estado), ''),
                            VALUES(estado),
                            {config.tabla_socios}.estado
                        )
                    """
                )
            )

            # ----------------------------------------------------------------
            # 4. Sincronizar medidores (insert nuevos, inactivar ausentes)
            #    Solo para sectores con medicion por consumo (tiene_medidores=True)
            # ----------------------------------------------------------------
            if config.tiene_medidores:
                conn.execute(text("DROP TEMPORARY TABLE IF EXISTS stg_medidores_snapshot"))
                conn.execute(
                    text(
                        f"""
                        CREATE TEMPORARY TABLE stg_medidores_snapshot (
                            nro_socio     varchar(20) NOT NULL,
                            servicio_tipo varchar(50) NOT NULL,
                            nro_medidor   varchar(50) NOT NULL,
                            PRIMARY KEY (nro_socio, servicio_tipo, nro_medidor)
                        ) CHARACTER SET utf8mb4 COLLATE {MYSQL_COLLATION}
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
                conn.execute(
                    text(
                        f"""
                        INSERT INTO {t_medidores} (nro_socio, servicio_tipo, nro_medidor, estado)
                        SELECT s.nro_socio, s.servicio_tipo, s.nro_medidor, 1
                        FROM stg_medidores_snapshot s
                        LEFT JOIN {t_medidores} m
                          ON  m.nro_socio     = s.nro_socio
                          AND m.servicio_tipo = s.servicio_tipo
                          AND m.nro_medidor   = s.nro_medidor
                        WHERE m.id_medidor_registro IS NULL
                        """
                    )
                )
                conn.execute(
                    text(
                        f"""
                        UPDATE {t_medidores} m
                        LEFT JOIN stg_medidores_snapshot s
                          ON  s.nro_socio     = m.nro_socio
                          AND s.servicio_tipo = m.servicio_tipo
                          AND s.nro_medidor   = m.nro_medidor
                        SET m.estado = 0
                        WHERE m.servicio_tipo = :servicio
                          AND s.nro_socio IS NULL
                          AND COALESCE(m.estado, 1) <> 0
                        """
                    ),
                    {"servicio": config.servicio_objetivo},
                )

            # ----------------------------------------------------------------
            # 5. Actualizar historial de tarifas (cerrar vigentes y abrir nuevas)
            # ----------------------------------------------------------------
            conn.execute(text("DROP TEMPORARY TABLE IF EXISTS stg_tarifas_objetivo"))
            conn.execute(
                text(
                    f"""
                    CREATE TEMPORARY TABLE stg_tarifas_objetivo (
                        nro_socio     varchar(50) NOT NULL,
                        servicio_tipo varchar(50) NOT NULL,
                        id_tarifa_base int        NOT NULL,
                        PRIMARY KEY (nro_socio, servicio_tipo)
                    ) CHARACTER SET utf8mb4 COLLATE {MYSQL_COLLATION}
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
                    tarifas_map_df[["nro_socio", "servicio_tipo", "id_tarifa_base"]].to_dict(
                        orient="records"
                    ),
                )
            conn.execute(
                text(
                    f"""
                    UPDATE {t_tarifas} h
                    JOIN stg_tarifas_objetivo s
                      ON  s.nro_socio     = h.nro_socio
                      AND s.servicio_tipo = h.servicio_tipo
                    SET h.fecha_hasta = :fecha_cierre
                    WHERE h.fecha_hasta IS NULL
                      AND COALESCE(h.id_tarifa_base, -1) <> COALESCE(s.id_tarifa_base, -1)
                    """
                ),
                {"fecha_cierre": fecha_cierre},
            )
            conn.execute(
                text(
                    f"""
                    INSERT INTO {t_tarifas}
                        (nro_socio, servicio_tipo, id_tarifa_base, fecha_desde, fecha_hasta, motivo_cambio)
                    SELECT s.nro_socio, s.servicio_tipo, s.id_tarifa_base,
                           :fecha_fuente, NULL, :motivo
                    FROM stg_tarifas_objetivo s
                    LEFT JOIN {t_tarifas} h
                      ON  h.nro_socio     = s.nro_socio
                      AND h.servicio_tipo = s.servicio_tipo
                      AND h.fecha_hasta IS NULL
                    WHERE h.id_historial IS NULL
                       OR COALESCE(h.id_tarifa_base, -1) <> COALESCE(s.id_tarifa_base, -1)
                    """
                ),
                {"fecha_fuente": fecha_fuente, "motivo": config.motivo_cambio_tarifa},
            )

            # ----------------------------------------------------------------
            # 6. Commit / Rollback
            # ----------------------------------------------------------------
            if dry_run:
                trans.rollback()
                print("DRY-RUN: rollback ejecutado, sin cambios persistidos.")
            else:
                trans.commit()

            # ----------------------------------------------------------------
            # 7. Reportes post-sync
            # ----------------------------------------------------------------
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
                        "tarifas_no_mapeadas": no_map_df[
                            ["nro_socio", "servicio_tipo", "tarifa"]
                        ].copy(),
                    },
                    reportes_dir=config.reportes_dir,
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


def print_summary_sector(summary: SyncSummary, config: SectorSyncConfig) -> None:
    """Imprime el resumen de la sincronizacion en consola."""
    sector = config.servicio_objetivo.upper()
    sep = "=" * max(44, len(sector) + 35)
    print(f"\n{sep}")
    print(f"  RESUMEN SINCRONIZACION SOCIOS {sector}")
    print(sep)
    print(f"  Socios insertados:     {summary.socios_insertados:>8,}")
    print(f"  Socios actualizados:   {summary.socios_actualizados:>8,}")
    print(f"  Socios sin cambios:    {summary.socios_sin_cambios:>8,}")
    print("  ---")
    print(f"  Medidores insertados:  {summary.medidores_insertados:>8,}")
    print(f"  Medidores mantenidos:  {summary.medidores_mantenidos:>8,}")
    print(f"  Medidores inactivados: {summary.medidores_inactivados:>8,}")
    print("  ---")
    print(f"  Tarifas creadas:       {summary.tarifas_creadas:>8,}")
    print(f"  Tarifas cambiadas:     {summary.tarifas_cambiadas:>8,}")
    print(f"  Tarifas sin cambios:   {summary.tarifas_sin_cambios:>8,}")
    print(f"  Tarifas no mapeadas:   {summary.tarifas_no_mapeadas:>8,}")
    print(f"{sep}\n")
