================================================================================
  AUTOMATIZACION CEEL — GUIA DE USO
================================================================================

Base de datos: conecciones_energia (MySQL)
Sectores: energia | agua | gas | internet | television

Calendario detallado (emision ~dia 29, ejemplos por mes):
  ver FLUJO_OPERATIVO.txt


================================================================================
1. REGLAS FIJAS (leer antes de operar)
================================================================================

  ORDEN OBLIGATORIO por sector y por mes:
    1) Socios (normalizar + sincronizar)
    2) Conceptos (procesar facturacion)
    3) Dashboard (control)

  PERIODO en nuestra BD:
    Siempre el dia 1 del mes: 2026-08-01 = facturacion de agosto/2026.
    No usamos fecha de emision TRYLOGYC (ej. 29/08) ni cambios intra-mes (24/08).

  CSV de socios — nombre: lista_socios_DDMMAAAA.csv
    La fecha del nombre define el MES de vigencia de tarifas.
    Ejemplo: lista_socios_07092026.csv  ->  vigencia desde 2026-09-01
    Ejemplo: lista_socios_29082026.csv  ->  vigencia desde 2026-08-01
    Regla: el MM del nombre debe coincidir con el periodo que vas a procesar.

  TXT de conceptos — ruta: data/<sector>/inbox/<AAAA>/<MM>/
    Procesar con: --año AAAA --mes MM (mismo mes que el CSV de socios).

  NUNCA reprocesar el mismo periodo sin limpiar BD antes (procesar.py hace APPEND).

  TRYLOGYC vs nuestro modelo:
    TRYLOGYC registra cambios de tarifa con fecha exacta (ej. 24/08/2026) y emite
    facturas con fechaEmision (ej. 29/08/2026). Nosotros agrupamos por mes completo.
    Si sincronizas socios de SEPTIEMBRE y procesas facturacion de AGOSTO, las tarifas
    nuevas no cubriran agosto en el dashboard. Usa un CSV de socios del mismo mes.


================================================================================
2. CONFIGURACION INICIAL (solo la primera vez)
================================================================================

  python -m venv venv
  .\venv\Scripts\activate
  pip install -r requirements.txt

  Copiar .env.example -> .env y completar:

    DB_HOST=localhost
    DB_PORT=3306
    DB_USER=tu_usuario
    DB_PASSWORD=tu_password
    DB_NAME=conecciones_energia

  --- Catalogos en BD (una vez o cuando TRYLOGYC agrega IDs/tarifas) ---

  Energia — tarifas base y escalones (desde export TRYLOGYC):
    python scripts/limpiar_tarifas_base.py
    python scripts/generar_escalones_tarifa.py
    (ejecutar SQL generado en data/energia/tarifas/ en MySQL)

  Agua — conceptos maestro:
    python scripts/cargar_conceptos_agua.py

  Agua — tarifas base:
    python scripts/cargar_tarifas_agua.py

  Gas / Television — conceptos maestro:
    python scripts/cargar_conceptos_gas.py
    python scripts/cargar_conceptos_television.py

  --- Vistas y SPs de dashboard (despues de cambios en SQL del repo) ---

  Energia:
    python scripts/cargar_sp_energia.py

  Agua / Gas / Television:
    python scripts/cargar_vista_consolidado_agua.py
    python scripts/cargar_vista_consolidado_gas.py
    python scripts/cargar_vista_consolidado_television.py


================================================================================
3. ARCHIVOS DE ENTRADA (TRYLOGYC -> data/)
================================================================================

  Por sector, depositar exports antes de correr scripts:

  Conceptos (TXT):
    Ruta:     data/<sector>/inbox/<AAAA>/<MM>/
    Nombre:   <servicio>_<id_concepto>.txt   (ej. energia_123.txt, agua_210.txt)
    Formato:  columnas ";", encoding latin1

  Socios (CSV):
    Ruta:     data/<sector>/socios/
    Nombre:   lista_socios_DDMMAAAA.csv
    Nota:     un mismo CSV trae todos los servicios; el sync filtra por sector.

  Tabla periodo <-> archivos:

    Periodo     inbox                  CSV socios (ejemplo)        Vigencia BD
    ----------  ---------------------  --------------------------  ------------
    07/2026     inbox/2026/07/         lista_socios_29072026.csv   2026-07-01
    08/2026     inbox/2026/08/         lista_socios_29082026.csv   2026-08-01
    09/2026     inbox/2026/09/         lista_socios_07092026.csv   2026-09-01


================================================================================
4. PROCEDIMIENTO MENSUAL (por cada sector con datos)
================================================================================

  Repetir para: energia, agua, internet, television, gas (los que tengan export).

  --- Paso 0: Export desde TRYLOGYC ---
  [ ] TXT  -> data/<sector>/inbox/<AAAA>/<MM>/
  [ ] CSV  -> data/<sector>/socios/lista_socios_DDMMAAAA.csv
      (la fecha DDMMAAAA debe caer en el mes MM del periodo)

  --- Paso 1: SOCIOS (siempre primero) ---

  .\venv\Scripts\activate

  python scripts/normalizar.py --sector <sector> ^
    --input data\<sector>\socios\lista_socios_DDMMAAAA.csv

  python scripts/sincronizar.py --sector <sector> --dry-run --export-reportes-csv

  Revisar: data/<sector>/reportes_sincro/<fecha>_<ts>/
    tarifas_no_mapeadas.csv   <- debe estar vacio o justificado
    tarifas_cambiadas.csv
    socios_insertados.csv / socios_actualizados.csv

  python scripts/sincronizar.py --sector <sector> --export-reportes-csv

  Verificar en consola:  Vigencia desde: <AAAA>-<MM>-01

  --- Paso 2: CONCEPTOS (despues de socios) ---

  python scripts/procesar.py --sector <sector> --año <AAAA> --mes <MM> --dry-run

  Revisar Excel: data/<sector>/processed/<AAAA>/<MM>/

  python scripts/procesar.py --sector <sector> --año <AAAA> --mes <MM>

  --- Paso 3: CONTROL ---

  streamlit run dashboards\sector_tabs.py
  (o dashboard individual: dashboards\energia_dashboard.py, etc.)

  Validar periodo <MM>/<AAAA>: totales, tarifas, "Sin Definir" aceptable.
  Reiniciar Streamlit si cambiaste SPs o datos en BD (cache).

  --------------------------------------------------------------------------
  EJEMPLO — Energia periodo 09/2026
  --------------------------------------------------------------------------

  python scripts/normalizar.py --sector energia --input data\energia\socios\lista_socios_07092026.csv
  python scripts/sincronizar.py --sector energia --dry-run --export-reportes-csv
  python scripts/sincronizar.py --sector energia --export-reportes-csv
  python scripts/procesar.py --sector energia --año 2026 --mes 09 --dry-run
  python scripts/procesar.py --sector energia --año 2026 --mes 09
  streamlit run dashboards\sector_tabs.py


================================================================================
5. QUE HACE CADA SCRIPT
================================================================================

  normalizar.py
    CSV crudo TRYLOGYC -> socios_normalizados.csv
    Extrae fecha_fuente del nombre del archivo.

  sincronizar.py
    socios_normalizados.csv -> BD
    Tablas: socios_<sector>, socios_medidores, socio_historial_tarifas
    Abre vigencia tarifa en 01/MM segun fecha_fuente del CSV.

  procesar.py
    TXT inbox -> facturacion_conceptos (append) + Excel en processed/
    periodo guardado como <AAAA>-<MM>-01

  cargar_*.py
    Despliega catalogos (conceptos, tarifas, vistas, SPs) desde data/<sector>/


================================================================================
6. DASHBOARD
================================================================================

  .\venv\Scripts\activate
  streamlit run dashboards\sector_tabs.py

  Dashboards por sector:
    dashboards\energia_dashboard.py
    dashboards\agua_dashboard.py
    dashboards\gas_dashboard.py
    dashboards\internet_dashboard.py
    dashboards\television_dashboard.py

  URL: http://localhost:8501  (solo lectura; no modifica la BD)


================================================================================
7. ESTRUCTURA DE CARPETAS
================================================================================

  automatizacion_ceel/
    core/           db_manager, normalizar_base, sector_sync
    sectors/        logica por sector (energia, agua, gas, internet, television)
    scripts/        CLI: normalizar, sincronizar, procesar, cargar_*
    dashboards/     Streamlit
    data/<sector>/
      inbox/<AAAA>/<MM>/    TXT conceptos (entrada)
      processed/<AAAA>/<MM>/ Excel control (salida procesar)
      socios/               CSV crudo + socios_normalizados.csv
      reportes_sincro/      reportes de cada corrida de sync


================================================================================
8. ERRORES FRECUENTES Y RECUPERACION
================================================================================

  "Sin Definir" o tarifa incorrecta en dashboard
    -> CSV de socios de OTRO mes que el periodo procesado.
    -> Falto sync de socios antes de procesar conceptos.
    -> tarifas_no_mapeadas.csv con filas sin resolver.
    -> Cache Streamlit: reiniciar la app.

  Socio con tarifa nueva en TRYLOGYC pero vieja en dashboard
    -> Cambio intra-mes (ej. 24/08) con sync de mes siguiente (01/09).
    -> Solucion operativa: sync con CSV del MISMO mes del periodo facturado.

  "No files found in data/.../inbox/..."
    -> TXT no estan en data/<sector>/inbox/<AAAA>/<MM>/

  "CONCEPTS NOT FOUND IN MASTER"
    -> Ejecutar cargar_conceptos_<sector>.py o agregar IDs en conceptos_maestro.

  Duplicados en facturacion_conceptos
    -> No reprocesar el mismo periodo; limpiar BD antes de reinyectar.

  "Faltan variables de entorno..."
    -> Completar .env en la raiz del proyecto.

  Vigencia a mitad de mes (ej. 05/08 en lugar de 01/08)
    -> Re-ejecutar sincronizar con el CSV correcto del mes; el sync corrige a 01/MM.

  Ejecutar siempre desde la raiz del proyecto con venv activado:
    .\venv\Scripts\python.exe scripts\...


================================================================================
  Fin — seguir seccion 4 mes a mes. Calendario: FLUJO_OPERATIVO.txt
================================================================================
