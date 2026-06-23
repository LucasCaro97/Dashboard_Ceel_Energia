# Reconstruccion BD `conecciones_energia`

Documento de handoff. Resume estado actual y proximos pasos sin depender del historial del chat.

## Estado (completado)

| Paso | Tabla / artefacto | Script / archivo |
|------|-------------------|------------------|
| OK | `servicios` | Cargado manualmente en MySQL |
| OK | `tarifas_base` | `data/energia/tarifas/tarifas_base_insert.sql` |
| OK | `escalones_tarifa` (+ columna `nombre_escalon`) | `data/energia/tarifas/escalones_tarifa_alter.sql` + `escalones_tarifa_insert.sql` |
| OK | DDL socios | `data/energia/socios/socios_create.sql` |
| OK | Vista `tarifa_base` | Incluida en `socios_create.sql` (alias de `tarifas_base`) |
| OK | CSV normalizado | `data/energia/socios/socios_normalizados.csv` (desde `lista_socios_18062026.csv`) |

### Scripts de preparacion de tarifas

```powershell
.\venv\Scripts\python.exe scripts\limpiar_tarifas_base.py
.\venv\Scripts\python.exe scripts\generar_escalones_tarifa.py
```

Entrada: `data/energia/tarifas/tarifas_crudas.csv`

### Script de normalizacion de socios

```powershell
.\venv\Scripts\python.exe scripts\normalizar.py --sector energia
```

- Sobrescribe `socios_normalizados.csv` (no append).
- Limpia saltos de linea en domicilios (evita filas rotas en Excel).

---

## Orden de ejecucion SQL en MySQL

1. `servicios` (ya hecho)
2. `data/energia/tarifas/tarifas_base_insert.sql`
3. `data/energia/tarifas/escalones_tarifa_alter.sql` (solo si la tabla existia sin `nombre_escalon`)
4. `data/energia/tarifas/escalones_tarifa_insert.sql`
5. `data/energia/socios/socios_create.sql`

Verificar:

```sql
USE conecciones_energia;
SELECT COUNT(*) FROM servicios;
SELECT COUNT(*) FROM tarifas_base;      -- esperado: 36
SELECT COUNT(*) FROM escalones_tarifa;  -- esperado: 72
SHOW TABLES LIKE 'socios%';
SELECT * FROM tarifa_base LIMIT 3;
```

---

## Proximo paso: inyectar socios

**No usar INSERT masivo manual.** Usar el sincronizador (transaccional, mapeo de tarifas, reportes).

### 1) Dry-run (obligatorio)

```powershell
.\venv\Scripts\activate
.\venv\Scripts\python.exe scripts\normalizar.py --sector energia
.\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --dry-run --export-reportes-csv
```

Revisar en `data/energia/reportes_sincro/<fecha>_*/`:

- `tarifas_no_mapeadas.csv` — debe estar vacio o justificar excepciones
- Conteos en consola (insertados / actualizados)

### 2) Inyeccion real

```powershell
.\venv\Scripts\python.exe scripts\sincronizar.py --sector energia --export-reportes-csv
```

Tablas que se llenan:

- `socios_energia`
- `socios_medidores`
- `socio_historial_tarifas`

El sync filtra solo filas con `servicio = Energia` (~20.954 filas del export; se consolidan por nro_socio).

### 3) Verificacion post-carga

```sql
SELECT COUNT(*) FROM socios_energia WHERE servicio_tipo = 'Energia';
SELECT COUNT(*) FROM socios_medidores WHERE servicio_tipo = 'Energia';
SELECT COUNT(*) FROM socio_historial_tarifas
 WHERE servicio_tipo = 'Energia' AND fecha_hasta IS NULL;
```

---

## Pendiente (despues de socios)

- `Conceptos_Maestro` + `facturacion_conceptos` (facturacion mensual)
- Vistas del dashboard (`v_kpi_facturacion`, etc.)
- Precios reales en `escalones_tarifa.precio` (hoy placeholder 0)

---

## Referencia rapida de archivos

```
data/energia/
  tarifas/
    tarifas_crudas.csv
    tarifas_base_limpias.csv
    tarifas_base_insert.sql
    escalones_tarifa_insert.sql
    escalones_tarifa_limpios.csv
  socios/
    lista_socios_*.csv          # export TRYLOGYC crudo
    socios_normalizados.csv     # entrada del sincronizador
    socios_create.sql
  reportes_sincro/              # salida del sync
```

Arquitectura de tarifas: ver `.cursorrules`.
