# Sincronizacion mensual de socios Energia

Script: `scripts/sincronizar_socios_energia.py`

## Que hace

- Procesa solo filas con `servicio = Energia` del CSV normalizado.
- Sincroniza `socios_energia` (upsert por `nro_socio + servicio_tipo`).
- Sincroniza `socios_medidores`:
  - inserta medidores nuevos,
  - mantiene los existentes,
  - inactiva (`estado = 0`) medidores que no aparecen en el snapshot mensual.
- Actualiza `socio_historial_tarifas` con vigencias:
  - cierra la vigente si cambia tarifa,
  - inserta nueva vigente.
- Mapea tarifas contra `tarifa_base` por:
  1. match exacto,
  2. fallback por prefijo/base.

## Uso

```bash
python scripts/sincronizar_socios_energia.py
```

```bash
python scripts/sincronizar_socios_energia.py --input data/socios/socios_normalizados.csv --dry-run
```

```bash
python scripts/sincronizar_socios_energia.py --dry-run --export-reportes-csv
```

## Notas

- El modo `--dry-run` ejecuta todo dentro de una transaccion y hace rollback al final.
- No modifica `act` en `socios_medidores`.
- Con `--export-reportes-csv` genera historial en:
  - `data/socios/reportes_sincro_socios/<fecha_fuente>_<timestamp>/`
  - archivos: `socios_insertados.csv`, `socios_actualizados.csv`,
    `medidores_insertados.csv`, `medidores_inactivados.csv`,
    `tarifas_creadas.csv`, `tarifas_cambiadas.csv`, `tarifas_no_mapeadas.csv`.
- Reporta al final:
  - socios insertados/actualizados/sin cambios,
  - medidores insertados/mantenidos/inactivados,
  - tarifas creadas/cambiadas/sin cambios/no mapeadas.

  ## Comandos para ejecutar script:
  - Para revisar antes de guardar cambios en BD: 
  .\venv\Scripts\python.exe scripts\sincronizar_socios_energia.py --dry-run --export-reportes-csv
  - Para ejecutar real + guardar historial:
  .\venv\Scripts\python.exe scripts\sincronizar_socios_energia.py --export-reportes-csv
  
## Indices recomendados (si faltan)

```sql
CREATE UNIQUE INDEX ux_socios_energia_key
ON conecciones_energia.socios_energia (nro_socio, servicio_tipo);

CREATE UNIQUE INDEX ux_socios_medidores_key
ON conecciones_energia.socios_medidores (nro_socio, servicio_tipo, nro_medidor);

CREATE INDEX ix_historial_tarifas_vigencia
ON conecciones_energia.socio_historial_tarifas (nro_socio, servicio_tipo, fecha_hasta);
```
