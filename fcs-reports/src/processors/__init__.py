"""Processors: convierten el JSON crudo de cada fetcher en datos de grafica.

Contrato de salida (uno por dashboard), pensado para que la plantilla y
Chart.js no vean nunca la respuesta cruda de la API:

    {
      "available": bool,          # hubo datos utilizables
      "error": str | None,        # mensaje si el fetcher fallo
      "stats": [ {label, value, caption, tone?} ],   # tarjetas de cabecera
      "cards": [                  # una grafica + su tabla gemela
        {
          "id", "title", "subtitle",
          "type": "bar-h" | "doughnut",
          "value_label",
          "labels": [...], "values": [...], "colors": [...],
          "percents"?: [...], "tooltips"?: [...],
          "columns": [ {key, label, numeric?, suffix?} ],
          "rows":    [ {...} ],
        }
      ],
      "totals": { ... },          # cifras que van en el texto del reporte
      "notes": [str]              # advertencias (truncado, campos ausentes)
    }

La plantilla recorre `cards` sin saber de que dashboard viene: agregar los
otros seis solo requiere un processor nuevo que respete este contrato.

Los colores se expresan como CLAVES semanticas ("critical", "series-1"), no
como hex: la plantilla las resuelve contra variables CSS para que el modo
claro y el oscuro usen los pasos correctos de la paleta.
"""
