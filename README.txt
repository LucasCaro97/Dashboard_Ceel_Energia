================================================================================
  AUTOMATIZACION CEEL - Guia de uso del proyecto
================================================================================

Base de datos: conecciones_energia (MySQL)

Este proyecto automatiza:
  - Carga mensual de conceptos de facturacion (TRYLOGYC -> facturacion_conceptos)
  - Sincronizacion mensual de socios de Energia (TRYLOGYC -> socios_energia,
    socios_medidores, socio_historial_tarifas)
  - Visualizacion de KPIs y graficos (dashboard Streamlit)


================================================================================
0. CONFIGURACION INICIAL (solo la primera vez)
================================================================================

1) Crear entorno virtual e instalar dependencias:

   python -m venv venv
   .\venv\Scripts\activate
   pip install -r requirements.txt

2) Configurar conexion a MySQL:

   Copiar .env.example a .env y completar credenciales:

   DB_HOST=localhost
   DB_PORT=3306
   DB_USER=tu_usuario
   DB_PASSWORD=tu_password
   DB_NAME=conecciones_energia

   (Alternativa: DB_URL=mysql+pymysql://usuario:password@host:3306/conecciones_energia)

3) Activar el entorno virtual antes de cada tarea:

   .\venv\Scripts\activate


================================================================================
1. INYECTAR DATOS DE FACTURACION (facturacion_conceptos)
================================================================================

Script: scripts/procesar.py

Que hace:
  - Lee archivos .txt exportados de TRYLOGYC desde data/inbox/<anio>/<mes>/
  - Normaliza columnas y cruza con la tabla Conceptos_Maestro
  - Genera Excel de control en data/processed/<anio>/<mes>/
  - Inserta filas en la tabla facturacion_conceptos (modo append)

Estructura esperada de archivos de entrada:

  data/inbox/2026/05/adicionales/adicionales_910.txt
  data/inbox/2026/05/energia/energia_123.txt
  ...

  Formato del nombre: <servicio>_<id_concepto>.txt
  Separador del TXT: punto y coma (;)
  Encoding: latin1

Pasos:

  A) Colocar los .txt del periodo en:
     data/inbox/<AAAA>/<MM>/

  B) Editar en scripts/procesar.py las variables del periodo a procesar
     (al final del archivo):

     anio_a_procesar = "2026"
     mes_a_procesar = "05"

  C) Ejecutar:

     .\venv\Scripts\python.exe scripts\procesar.py

Salidas:
  - Excel: data/processed/<anio>/<mes>/ENERGIA_conceptos_facturados_<anio>_<mes>.xlsx
  - Insercion en BD: tabla facturacion_conceptos

Validaciones automaticas:
  - Si no encuentra archivos en la carpeta del periodo, aborta.
  - Si hay conceptos en los TXT que no existen en Conceptos_Maestro, aborta
    e imprime la lista de faltantes (no inyecta datos invalidos).

Nota:
  - El script usa if_exists='append': cada ejecucion AGREGA filas.
    No borra periodos anteriores. Evitar ejecutar dos veces el mismo periodo
    si no corresponde duplicar datos.


================================================================================
2. INYECTAR / SINCRONIZAR DATOS DE SOCIOS (Energia)
================================================================================

Flujo en 2 pasos: normalizar CSV crudo -> sincronizar contra BD.

Tablas afectadas:
  - socios_energia          (estado y nombre por nro_socio + servicio_tipo)
  - socios_medidores        (relacion 1:N de medidores; baja logica con estado=0)
  - socio_historial_tarifas (historial de tarifas con vigencias)

----------------------------------------------------------------------
PASO 2A - Normalizar export de TRYLOGYC
----------------------------------------------------------------------

Script: scripts/normalizar_socios.py

Entrada:
  - CSV crudo exportado de TRYLOGYC, por ejemplo:
    data/socios/lista_socios_17062026.csv

Salida:
  - data/socios/socios_normalizados.csv

Comando (toma el lista_socios_*.csv mas reciente de data/socios/):

  .\venv\Scripts\python.exe scripts\normalizar_socios.py

Comando con archivo explicito:

  .\venv\Scripts\python.exe scripts\normalizar_socios.py data\socios\lista_socios_17062026.csv

Que normaliza:
  - Extrae columnas utiles (11 a 19 del export TRYLOGYC)
  - nro_socio al formato BD (ej: 00000002/000001 -> 000002/0001)
  - Separa documento en tipo_doc y nro_doc
  - Agrega fecha_fuente desde el nombre del archivo

----------------------------------------------------------------------
PASO 2B - Sincronizar contra MySQL
----------------------------------------------------------------------

Script: scripts/sincronizar_socios_energia.py

Procesa SOLO filas con servicio = "Energia".

Reglas principales:
  - socios_energia: inserta nuevos; actualiza estado si cambio; actualiza nombre
  - socios_medidores: inserta nuevos; mantiene existentes; inactiva (estado=0)
    los que no vienen en el snapshot mensual; NO modifica columna act
  - socio_historial_tarifas: cierra vigencia anterior e inserta nueva si cambia
    la tarifa asignada

RECOMENDADO - Simular antes de guardar (dry-run + reportes CSV):

  .\venv\Scripts\python.exe scripts\sincronizar_socios_energia.py --dry-run --export-reportes-csv

  - No persiste cambios en BD (hace rollback al final)
  - Genera CSVs de control en:
    data/socios/reportes_sincro_socios/<fecha_fuente>_<timestamp>/
      socios_insertados.csv
      socios_actualizados.csv
      medidores_insertados.csv
      medidores_inactivados.csv
      tarifas_creadas.csv
      tarifas_cambiadas.csv
      tarifas_no_mapeadas.csv

EJECUCION REAL (persiste cambios + historial CSV):

  .\venv\Scripts\python.exe scripts\sincronizar_socios_energia.py --export-reportes-csv

Opciones utiles:

  --input <ruta_csv>         CSV normalizado (default: socios_normalizados.csv)
  --dry-run                  Simula sin commit
  --export-reportes-csv      Exporta reportes de cambios para auditoria

Documentacion detallada: scripts/README_sincronizar_socios_energia.md


================================================================================
3. DASHBOARD DE FACTURACION (visualizacion)
================================================================================

Script: app_dashboard.py

Muestra KPIs, graficos de distribucion, top consumidores, correlacion, etc.
Consulta vistas pre-agregadas en MySQL (v_kpi_facturacion,
v_totalizado_por_tarifa_base, v_reporte_facturacion_energia, etc.).

Ejecutar:

  .\venv\Scripts\activate
  streamlit run app_dashboard.py

Se abre en el navegador (por defecto http://localhost:8501).

Filtros en sidebar:
  - Periodo
  - Cantidad de categorias (Top N)

Nota: el dashboard solo LEE datos de la BD; no modifica tablas.


================================================================================
4. NORMALIZAR MEDIDORES (auxiliar, sin inyeccion a BD en este repo)
================================================================================

Script: scripts/normalizar_medidores.py

Entrada:  medidores_sin_procesar.xlsx (raiz del proyecto)
Salida:   medidores_normalizados.csv (raiz del proyecto)

Desglosa filas con varios medidores separados por coma en una fila por
medidor (NRO_SOCIO, MEDIDOR, CATEGORIA_MEDIDOR).

  .\venv\Scripts\python.exe scripts\normalizar_medidores.py

Este script NO escribe en MySQL desde este proyecto. La sincronizacion de
medidores hacia BD se hace via sincronizar_socios_energia.py (Paso 2B).


================================================================================
5. FLUJO MENSUAL RECOMENDADO (checklist)
================================================================================

[ ] 1. Exportar desde TRYLOGYC:
        - TXT de conceptos -> data/inbox/<anio>/<mes>/
        - Listado de socios -> data/socios/lista_socios_DDMMAAAA.csv

[ ] 2. Socios:
        - Ejecutar scripts/normalizar_socios.py
        - Ejecutar sincronizar con --dry-run --export-reportes-csv
        - Revisar CSVs en data/socios/reportes_sincro_socios/
        - Si todo OK: ejecutar sincronizar sin --dry-run


[ ] 3. Facturacion:
        - Ajustar anio/mes en scripts/procesar.py
        - Ejecutar scripts/procesar.py
        - Verificar Excel en data/processed/

[ ] 4. Dashboard:
        - streamlit run app_dashboard.py
        - Validar KPIs y graficos del periodo cargado


================================================================================
6. ESTRUCTURA DE CARPETAS PRINCIPAL
================================================================================

automatizacion_ceel/
  app_dashboard.py              Dashboard Streamlit
  requirements.txt              Dependencias Python
  .env                          Credenciales BD (no commitear)
  scripts/
    procesar.py                 Carga facturacion_conceptos
    normalizar_socios.py        Limpia CSV de socios TRYLOGYC
    sincronizar_socios_energia.py  Sync socios/medidores/tarifas
    normalizar_medidores.py     Normaliza Excel de medidores (auxiliar)
  data/
    inbox/<anio>/<mes>/         TXT conceptos facturacion (entrada)
    processed/<anio>/<mes>/     Excel procesado (salida control)
    socios/                     CSV socios TRYLOGYC y normalizados
    socios/reportes_sincro_socios/  Historial de cambios por corrida


================================================================================
7. TABLAS MYSQL REFERENCIADAS
================================================================================

Facturacion:
  - Conceptos_Maestro       (maestro de conceptos; requerido por procesar.py)
  - facturacion_conceptos   (destino de scripts/procesar.py)

Socios:
  - socios_energia
  - socios_medidores
  - socio_historial_tarifas
  - tarifa_base             (maestro de tarifas)

Dashboard (vistas, solo lectura):
  - v_kpi_facturacion
  - v_totalizado_por_tarifa_base
  - v_reporte_facturacion_energia
  - vista_socios_tarifa_actual


================================================================================
8. SOLUCION DE PROBLEMAS FRECUENTES
================================================================================

"No se encontraron archivos en data/inbox/..."
  -> Verificar ruta anio/mes y que los .txt esten en subcarpetas del periodo.

"CONCEPTOS NO ENCONTRADOS EN EL MAESTRO"
  -> Agregar los conceptos faltantes a Conceptos_Maestro antes de reintentar.

"Faltan variables de entorno..."
  -> Crear/completar archivo .env en la raiz del proyecto.

Dashboard sin datos / KPIs en cero:
  -> Verificar periodo seleccionado y que existan datos cargados para ese mes.

Sincronizacion socios - revisar cambios antes de commit:
  -> Siempre usar --dry-run --export-reportes-csv primero.


================================================================================
Fin de la guia
================================================================================
