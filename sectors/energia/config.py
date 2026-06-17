# -*- coding: utf-8 -*-
"""
Config specific to Energia sector.
"""

SERVICIO_TIPO = "Energia"
TABLA_FACTURACION = "facturacion_conceptos"
TABLA_SOCIOS = "socios_energia"
TABLA_MEDIDORES = "socios_medidores"
TABLA_TARIFAS = "socio_historial_tarifas"

ESTADO_PRIORIDAD = {
    "activo": 4,
    "stand by": 3,
    "desconectado": 2,
    "baja liquida consumo": 1,
}

TARIFA_EQUIVALENCIAS = {
    "Entes de Radiodif.y Telev": "Entes Radio TV",
    "GU-ME >300 KW PEAJE": "GU-ME Peaje",
}
