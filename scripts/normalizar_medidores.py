"""Normaliza medidores asignados: una fila por socio-medidor."""

import pandas as pd
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ORIGEN_XLSX = ROOT / "medidores_sin_procesar.xlsx"
SALIDA = ROOT / "medidores_normalizados.csv"


def normalizar_fila(nro_socio: str, medidores_raw: str, categoria_raw: str) -> list[dict]:
    nro_socio = str(nro_socio).strip()
    medidores = [m.strip() for m in str(medidores_raw).split(",") if m.strip()]
    categoria_raw = str(categoria_raw).strip().upper()
    if categoria_raw in ("NAN", ""):
        categoria_raw = ""

    if not medidores:
        return []

    if len(medidores) == 1 and not categoria_raw:
        categorias = ["C"]
    elif categoria_raw:
        categorias = list(categoria_raw)
        if len(categorias) < len(medidores):
            categorias.extend(["C"] * (len(medidores) - len(categorias)))
        elif len(categorias) > len(medidores):
            categorias = categorias[: len(medidores)]
    else:
        categorias = ["C"] * len(medidores)

    return [
        {"NRO_SOCIO": nro_socio, "MEDIDOR": medidor, "CATEGORIA_MEDIDOR": cat}
        for medidor, cat in zip(medidores, categorias)
    ]


def normalizar_archivo(ruta_entrada: Path = ORIGEN_XLSX, ruta_salida: Path = SALIDA) -> pd.DataFrame:
    if ruta_entrada.suffix in (".xlsx", ".xls"):
        df = pd.read_excel(ruta_entrada, dtype=str)
    else:
        df = pd.read_csv(ruta_entrada, dtype=str, keep_default_na=False)

    df.columns = df.columns.str.strip().str.upper()
    # Toleramos el typo "CATGORIA_MEDIDOR" del xlsx
    col_cat = next(
        (c for c in df.columns if "CAT" in c and "MEDIDOR" in c),
        "CATEGORIA_MEDIDOR",
    )
    df = df.rename(columns={col_cat: "CATEGORIA_MEDIDOR"})
    df = df.fillna("")

    filas = []
    for _, row in df.iterrows():
        filas.extend(
            normalizar_fila(
                row["NRO_SOCIO"],
                row["MEDIDORES_ASIGNADOS"],
                row.get("CATEGORIA_MEDIDOR", ""),
            )
        )

    df_out = pd.DataFrame(filas)
    df_out.to_csv(ruta_salida, index=False, encoding="utf-8")
    print(f"Generado: {ruta_salida} ({len(df_out)} filas desde {len(df)} registros origen)")
    return df_out


if __name__ == "__main__":
    normalizar_archivo()
