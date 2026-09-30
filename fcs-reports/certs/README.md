# CA corporativas para el build

Los `.crt` de esta carpeta se instalan en el almacen de confianza de la imagen
(ver `Dockerfile`). Sirven cuando la red hace **inspeccion TLS** y presenta
certificados firmados por una CA propia.

Sintoma sin esto:

```
CERTIFICATE_VERIFY_FAILED: self-signed certificate in certificate chain
```

durante `pip install` en el build, y el mismo error en runtime cuando FalconPy
llama a `api.crowdstrike.com`.

## Regenerar la cadena

Los `.crt` estan en `.gitignore` porque son especificos de cada red: no se
commitean. Para regenerarlos donde haga falta:

```bash
cd certs
openssl s_client -connect pypi.org:443 -servername pypi.org -showcerts </dev/null 2>/dev/null \
  | awk '/BEGIN CERTIFICATE/{n++} n>=2{print > ("cert-" n ".crt")}'
for f in cert-*.crt; do openssl x509 -in "$f" -out "$f.clean" && mv "$f.clean" "$f"; done
```

Descarta el certificado del servidor (el primero) y conserva la cadena
intermedia + raiz. Verifica lo que quedo:

```bash
for f in *.crt; do openssl x509 -in "$f" -noout -subject -enddate; done
```

En esta red la cadena es **Netskope**: `ca.ashasolution.goskope.com` (intermedia
del tenant) y `*.dfw3.goskope.com` (raiz de Netskope).

## Antes de desplegar en la red de un cliente

Vacia esta carpeta y reconstruye. Instalar aqui la CA de ASHA haria que el
contenedor confie en certificados firmados por esa CA dentro de una red donde
no tiene por que hacerlo; cada red instala la suya.
