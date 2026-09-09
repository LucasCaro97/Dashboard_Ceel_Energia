"""Estado vacío compartido cuando no hay facturación en el período."""

NO_RECORDS_MESSAGE = (
    "No hay registros para armar los graficos. Actualize la base de datos"
)


def sin_datos_periodo(total_facturado) -> bool:
    """True si no hay facturación registrada para el período."""
    try:
        return float(total_facturado or 0) <= 0
    except (TypeError, ValueError):
        return True
