# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Gas.
"""

SERVICIO_TIPO = "Gas"

# Prefijo en archivos TXT (gas_<id>.txt) -> clave servicio en conceptos_maestro.
SERVICIO_TXT_ALIASES = {
    "gas": "gas_envasado",
}
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Gas
TARIFA_EQUIVALENCIAS = {}
