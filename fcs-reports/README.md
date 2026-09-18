# fcs-reports

Reportes periódicos de **CrowdStrike Falcon Cloud Security** en HTML estático con
gráficas Chart.js. Un proceso batch (`src/generate.py`) consulta la API vía FalconPy
y deja el HTML en `output/reports/`; FastAPI (`app.py`) solo lista y sirve esos
archivos. Una imagen Docker para todos los clientes: cambia únicamente el `.env`.

## Estado

**Fase 1 completa y validada de punta a punta** (auth → fetcher → processor →
report_builder → HTML), con el dashboard **Cloud Risks**. Los otros seis dashboards
se enchufan en `DASHBOARDS` de `src/generate.py` sin tocar plantilla ni JS.

| Dashboard | Operación FalconPy | Estado |
|---|---|---|
| Cloud Risks | `CloudSecurityDetections.get_combined_iom_by_rule` | ✅ implementado |
| Cloud Assets | `CloudSecurityAssets.query_assets` + `get_assets` | pendiente |
| Cloud Detections | `Alerts.query_alerts` + `get_alerts_v2` | pendiente |
| Drift | `DriftIndicators.search_and_read_drift_indicators` | pendiente |
| Clusters | `KubernetesProtection` (`read_cluster_count`, `read_clusters_by_status`) | pendiente |
| Cloud Compliance | `CloudSecurityAssets.get_combined_compliance_by_account` | pendiente |
| K8s Compliance | `KubernetesContainerCompliance` (por mapear) | pendiente |

Requiere **FalconPy >= 1.6.5**: las clases `CloudSecurityDetections` y
`CloudSecurityAssets` no existen en versiones anteriores.

## Arranque rápido

```bash
cp .env.example .env      # completa FALCON_CLIENT_ID / SECRET / CLOUD / CLIENT_NAME
pip install -r requirements.txt

python -m src.generate                 # genera output/reports/{cliente}_{fecha}.html
uvicorn app:app --port 8000            # sirve GET /reports y GET /reports/{archivo}
```

Scope mínimo de la API client para esta fase: **Cloud security detections: READ**.

### Opciones útiles de `generate.py`

```bash
python -m src.generate --dump-raw          # guarda el JSON crudo en output/raw/
python -m src.generate --only cloud_risks  # un solo dashboard
python -m src.generate --dry-run           # consulta y procesa sin escribir el HTML
python -m src.generate --log-level DEBUG   # traza cada página de la paginación
```

Códigos de salida: `0` ok · `1` ningún dashboard trajo datos · `2` configuración
inválida · `3` fallo de autenticación. Útiles para que el cron alerte.

## Docker

```bash
docker build -t fcs-reports .

# lote (una vez, o desde cron)
docker run --rm --env-file .env.cliente-a -v fcs_out_a:/app/output \
  fcs-reports python -m src.generate

# servidor de consulta
docker run -d --env-file .env.cliente-a -v fcs_out_a:/app/output -p 8080:8000 \
  --name fcs-a fcs-reports
```

Un volumen por cliente mantiene aislados los históricos. La misma imagen sirve a
todos: solo cambia el `--env-file`.

## Arquitectura

```
src/config.py          Variables de entorno, validadas y tipadas (falla temprano)
src/auth.py            UN solo OAuth2 por ejecución; las Service Classes lo comparten
src/fetchers/base.py   Paginación completa + backoff + aislamiento de fallas
src/fetchers/*.py      Uno por dashboard; devuelven FetchResult
src/processors/*.py    JSON crudo -> {labels, values, colors} + totales del texto
src/report_builder.py  Jinja2 + retención de históricos
src/generate.py        Orquestador batch
app.py                 FastAPI: dos endpoints, cero lógica de negocio
```

### Garantías de los fetchers

- **Paginación completa.** Se sigue `meta.pagination.total` (offset/limit) o
  `meta.pagination.after` (cursor) hasta agotar; nunca se asume que la primera
  página trae todo. Se usa el límite máximo de cada endpoint (1000 en Cloud Risks)
  para minimizar llamadas.
- **Reintentos.** Backoff exponencial con jitter ante `429`, `5xx` y errores de
  transporte, máximo 3 (configurable). Respeta `Retry-After` /
  `X-RateLimit-RetryAfter`. Un `403` o un FQL inválido **no** se reintenta.
- **Aislamiento.** `safe_fetch()` atrapa cualquier excepción: el dashboard queda
  marcado como no disponible y el reporte se genera igual, con el error visible
  en el propio HTML y en la tabla de diagnóstico.

### Sobre las gráficas

**Tema claro único**, alineado a los dashboards de avance de proyecto: fondo
`#F3F5F7`, tarjetas blancas con borde hairline `#DDE3E9`, títulos navy en
versalitas, Segoe UI. No hay modo oscuro.

Los processors emiten **claves de color semánticas** (`series-1`, `sev-critical`),
no hex; el JS las resuelve contra variables CSS de `static/style.css`. Para cambiar
la paleta se edita ese archivo y nada más.

| Uso | Colores |
|---|---|
| Severidad (escala de calor) | `#4FB3C6` Info · `#FFC145` Media · `#EF5A45` Alta · `#9E2A1E` Crítica |
| Categóricos (proveedor, región) | `#5B9BD5` · `#F2994A` · `#4FBF98` · `#9AA6B2` "Otros" |
| Umbrales de cumplimiento | `#3DAE74` ≥70% · `#E8A33D` 50–69% · `#E0616E` <50% |

Low/Medium/High y los categóricos son los tonos de los dashboards existentes.
Crítica (`#9E2A1E`) es el único color nuevo: un rojo más profundo de la misma
familia, elegido porque el rojo obvio quedaba a ΔE 11.4 de `#EF5A45` —
indistinguible incluso con visión normal. El elegido separa a ΔE 19.7 (visión
normal) y 17.0 (daltonismo). Los tres categóricos pasan todas las verificaciones
de separación sobre blanco.

`#FFC145` da 1.62:1 de contraste contra blanco (igual que en los dashboards
actuales). Es aceptable **porque** el valor nunca depende del color: cada gráfica
de pocas barras rotula el número al final de la barra, la dona lleva valor y
porcentaje en la leyenda, y toda gráfica trae su **tabla de datos gemela** en un
`<details>`. En series largas (Top 10) no se rotula cada barra — eso no se lee; ahí
cargan el eje, el tooltip y la tabla.

El CSS se incrusta en cada HTML: el reporte es autocontenido y se puede archivar o
enviar por correo. La única dependencia externa es Chart.js por CDN
(`CHARTJS_URL`); en una red sin salida las gráficas no se dibujan, pero las tablas
de datos sí — si hace falta, descarga `chart.umd.min.js` a `static/` y apunta
`CHARTJS_URL` ahí.

## Retención

`REPORT_RETENTION` (default 12) es **por cliente**: al terminar, cada ejecución
conserva los N archivos `{slug}_*.html` más recientes de ese cliente y borra el
resto. Contenedores distintos que compartan volumen no se pisan. `0` desactiva la
limpieza.

## Fase 7 — automatización (pendiente)

`REPORT_FREQUENCY` hoy solo etiqueta el periodo en el reporte. Para programarlo,
lo recomendable es cron **externo** al contenedor (el contenedor corre y muere),
por ejemplo semanal los lunes 06:00:

```cron
0 6 * * 1  docker run --rm --env-file /etc/fcs/.env.cliente-a \
             -v fcs_out_a:/app/output fcs-reports python -m src.generate \
             >> /var/log/fcs-reports/cliente-a.log 2>&1
```

Evita meter cron dentro de la imagen: complica logs, señales y el manejo de fallos.

## Pendientes de validar contra un tenant real

Estos dos puntos no se pueden cerrar sin credenciales; ambos son de configuración,
no de código:

1. **Mayúsculas de `severity` en el FQL.** El default es
   `severity:['High','Critical']` (tal cual se especificó). Si la API devuelve
   vacío, prueba minúsculas en `CLOUD_RISKS_FILTER`. El filtro es una variable de
   entorno justamente para no tocar código.
2. **Nombres reales de los campos del recurso.** `processors/cloud_risks.py` lee
   cada campo con una lista de rutas candidatas (`rule_name` / `rule.name` /
   `name`…) y, si no encuentra el conteo de misconfiguraciones, cuenta 1 por regla
   **y lo anuncia en el reporte**. Corre `--dump-raw` una vez, revisa el JSON y
   ajusta `FIELD_PATHS` con los nombres exactos.

## Verificación hecha

Con la API simulada (sin credenciales), en Python 3.14 + FalconPy 1.6.5:

- superficie de FalconPy: `get_combined_iom_by_rule` existe, `base_url="us-1"`
  resuelve a `https://api.crowdstrike.com` y las Service Classes comparten el
  `auth_object` (un solo login);
- paginación offset: 1200 registros en 2 páginas, corte correcto por `total`;
- paginación cursor: 250 registros en 3 páginas;
- `429` reintentado, `403` propagado sin reintento;
- fetcher caído → reporte generado igual, con el error visible;
- registros sin campos esperados → nota en el reporte, sin excepción;
- retención: conserva 3, borra 2, no toca los de otro cliente;
- `GET /reports` y `GET /reports/{archivo}`; traversal (`../`, `a/b.html`,
  `style.css`) rechazado con 400/404;
- render en Chrome headless y revisión visual del resultado;
- separación de la paleta verificada con el validador (all-pairs, sobre blanco).
