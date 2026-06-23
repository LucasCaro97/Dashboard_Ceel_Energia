# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Gas.
"""

SERVICIO_TIPO = "Gas"
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_gas"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
# Completar segun las equivalencias que existan para Gas
TARIFA_EQUIVALENCIAS = {}
