# Reestructuración - Arquitectura Modular Multi-Sector

## Cambios Realizados

La estructura del proyecto ha sido reestructurada para soportar múltiples sectores (Energía, Agua, Internet, Televisión, Gas) manteniendo un código base compartido y modular.

### 1. Nueva Estructura de Directorios

```
automatizacion_ceel/
├── core/                              # Módulos compartidos por todos los sectores
│   ├── __init__.py
│   └── db_manager.py                  # Gestor de conexión a BD (MySQL)
│
├── sectors/                           # Código específico por sector
│   ├── __init__.py
│   ├── energia/
│   │   ├── __init__.py
│   │   ├── config.py                  # Configuración de Energía
│   │   ├── procesador.py              # Procesa facturación
│   │   ├── normalizar.py              # Normaliza CSV de socios
│   │   └── sincronizador.py           # Sincroniza socios/medidores/tarifas
│   ├── agua/
│   ├── internet/
│   ├── television/
│   └── gas/
│
├── scripts/                           # Scripts de CLI para ejecutar tareas
│   ├── procesar.py                    # Wrapper multi-sector
│   ├── sincronizar.py                 # Wrapper multi-sector (a implementar)
│   └── normalizar.py                  # Wrapper multi-sector (a implementar)
│
├── dashboards/                        # Dashboards Streamlit
│   ├── energia_dashboard.py           # Dashboard de Energía
│   ├── agua_dashboard.py              # (a implementar)
│   └── app_main.py                    # Dashboard principal multi-sector (a implementar)
│
├── data/                              # Datos organizados por sector
│   ├── energia/
│   │   ├── inbox/                     # Archivos TXT crudos de TRYLOGYC
│   │   ├── processed/                 # Archivos Excel de control
│   │   ├── socios/                    # CSVs de socios normalizados
│   │   ├── graficos/                  # Gráficos generados
│   │   └── reportes_sincro/           # Reportes de sincronización
│   ├── agua/
│   ├── internet/
│   ├── television/
│   └── gas/
│
├── .env                               # Variables de entorno
├── .gitignore
├── requirements.txt
└── README.txt                         # (será actualizado)
```

### 2. Módulo Core (Compartido)

**`core/db_manager.py`** - Funciones reutilizables para todos los sectores:
- `get_db_config()` - Lee credenciales de BD desde `.env`
- `build_sqlalchemy_engine()` - Crea engine de SQLAlchemy
- `inyectar_a_mysql()` - Inyecta datos en tabla especificada
- `obtener_maestro_conceptos()` - Lee tabla Conceptos_Maestro
- `execute_query()` - Ejecuta queries SQL directas

### 3. Módulo Energía (Ejemplo de Sector)

**`sectors/energia/config.py`** - Configuración específica:
```python
SERVICIO_TIPO = "Energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
# ... tablas y constantes específicas
```

**`sectors/energia/procesador.py`** - Pipeline de procesamiento de facturación:
- `procesar_periodo()` - Lee archivos TXT
- `procesar_facturacion()` - Pipeline completo (leer → normalizar → validar → inyectar)

**`sectors/energia/normalizar.py`** - Normalización de CSV de socios:
- `normalizar()` - Normaliza listados de socios desde TRYLOGYC

**`sectors/energia/sincronizador.py`** - Sincronización con BD (copiar íntegro de `scripts/sincronizar_socios_energia.py` con imports actualizados)

### 4. Scripts CLI (Wrappers Ejecutables)

**`scripts/procesar.py`** - Wrapper que elige sector y llama procesador correspondiente:
```bash
python scripts/procesar.py --sector energia --año 2026 --mes 05
python scripts/procesar.py --sector agua --año 2026 --mes 06
```

### 5. Dashboards

- **`dashboards/energia_dashboard.py`** - Dashboard de Energía (copy de `app_dashboard.py`)
- **`dashboards/app_main.py`** - Dashboard principal multi-sector (a implementar)

### 6. Reorganización de Datos

- `/data/inbox/` → `/data/energia/inbox/`
- `/data/processed/` → `/data/energia/processed/`
- `/data/socios/` → `/data/energia/socios/`
- `/data/graficos/` → `/data/energia/graficos/`

Se crearon carpetas análogas para: `agua/`, `internet/`, `television/`, `gas/`

## Pasos Siguientes

### Fase 2: Actualizar Scripts (Próxima sesión)

1. Actualizar `scripts/normalizar.py` para ser multi-sector
2. Crear `scripts/sincronizar.py` multi-sector
3. Crear `dashboards/app_main.py` con selector de sector

### Fase 3: Replicar para Otros Sectores

1. Crear `sectors/agua/config.py` con configuración específica
2. Crear `sectors/agua/procesador.py` adaptando de Energía
3. Repetir para: internet, television, gas

### Fase 4: Actualizar Documentación

1. Actualizar `README.txt` con nueva estructura y ejemplos
2. Agregar guías de uso para cada sector

## Beneficios de la Nueva Estructura

✅ **Modularidad**: Cada sector es independiente pero reutiliza `core/`  
✅ **Escalabilidad**: Agregar nuevo sector = copiar patrón de `sectors/energia/`  
✅ **Mantenibilidad**: Cambios en lógica compartida benefician todos los sectores  
✅ **Claridad**: Fácil identificar código genérico vs. específico  
✅ **Testing**: Componentes modulares son más testables  
✅ **DRY**: No se repite código de conexión BD, normalización, etc.

## Cómo Usar la Nueva Estructura

### Procesar Facturación de Energía
```bash
.\venv\Scripts\activate
python scripts/procesar.py --sector energia --año 2026 --mes 05
```

### Dashboard Energía
```bash
.\venv\Scripts\activate
streamlit run dashboards/energia_dashboard.py
```

### Importar desde Código Python
```python
from sectors.energia.procesador import procesar_facturacion
from core.db_manager import inyectar_a_mysql

# Procesar y inyectar
success = procesar_facturacion(anio="2026", mes="05", sector="energia")
```

## Archivos Eliminados

Los siguientes archivos antiguos fueron eliminados (la funcionalidad se movió a la nueva estructura):
- `scripts/normalizar_medidores.py`
- `scripts/normalizar_socios.py`
- `scripts/sincronizar_socios_energia.py`
- `scripts/README_sincronizar_socios_energia.md`

## Nota Importante

El archivo `app_dashboard.py` en la raíz del proyecto sigue allí por retrocompatibilidad, pero se recomienda usar `dashboards/energia_dashboard.py` en su lugar.
