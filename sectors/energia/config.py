# -*- coding: utf-8 -*-
"""
Configuracion especifica del sector Energia.
"""

SERVICIO_TIPO = "Energia"
DB_SCHEMA = "conecciones_energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"
TABLA_TARIFA_BASE = "tarifas_base"

# Mapeo de nombres TRYLOGYC -> nombres en tarifa_base de la BD
TARIFA_EQUIVALENCIAS = {
    "Entes de Radiodif.y Telev": "Entes Radio TV",
    "GU-ME >300 KW PEAJE": "GU-ME Peaje",
}
