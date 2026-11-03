# Endpoints de las tiendas (Fase 0)

Investigación hecha el **2026-11-02** con requests reales y secuenciales (≈1 s entre requests, timeout 15 s). Un agente investigó cada tienda y otro, independiente, re-probó sus afirmaciones; aquí queda lo que sobrevivió a esa verificación.

Respuestas reales recortadas (para los tests): [`muestras_fase0.json`](muestras_fase0.json).

## Resumen

Las tres tiendas corren sobre **VTEX** y exponen la misma API pública de búsqueda, **Intelligent Search** (IS). Por eso `stores.py` puede usar **una sola función de request** y casi un solo parseo; las diferencias están en cómo se fija la región y de dónde sale la presentación.

| | Éxito | Jumbo | Olímpica |
|---|---|---|---|
| Dominio | `www.exito.com` | `www.jumbocolombia.com` (tiendasjumbo.co redirige aquí) | `www.olimpica.com` |
| Ruta de búsqueda | `/api/io/_v/api/intelligent-search/product_search/category-1/mercado` | `/_v/api/intelligent-search/product_search/` | `/api/io/_v/api/intelligent-search/product_search/` |
| Status de éxito | **406** (con JSON válido) | 200 | 200 |
| Región Medellín (`regionId`) | `U1cjZXhpdG9jb2wwMzc=` | `v2.46CFB2D3A40B66B7454776AA8386DDAF` | `U1cjb2xpbXBpY2Fzd2wxMzQw` |
| Tienda física | exitocol037 (Centro/Laureles) | swl23 "La 65" | olimpicaswl1340 "San Lucas" |
| Cómo se obtuvo | `sessionByCoords` (coordenadas) | `checkout/pub/regions` (código DANE 05001) | `checkout/pub/regions` (coordenadas) |
| Presentación | propiedades PUM (fiables) + nombre | **solo el nombre** | propiedades PUM (con errores) + nombre |
| Latencia (20 productos) | 2–5 s | 0,3–2 s | 2–8 s |
| Anti-bot / 429 | no | no | no (un 500 aislado) |

**El precio sí cambia por ciudad** en las tres, y también el surtido. Sin `regionId` se obtiene un catálogo "nacional" que no corresponde a Medellín ni a Bogotá, así que **siempre hay que mandar el `regionId`**.

## Lo común a las tres tiendas

### Request

```
GET https://<dominio><ruta>?query=<texto>&page=1&count=20&regionId=<regionId>
```

- `query`: texto libre, codificado para URL.
- `page`: empieza en 1. Para una canasta basta la página 1 con `count` 20–40.
- `count`: productos por página. Cada producto pesa ~30 KB (sin gzip), así que 20 productos ≈ 650 KB. Con 100 funciona en Jumbo y Olímpica pero tarda 9 s o más; en Éxito pasa de 15 s.
- Sin paginar más allá de lo necesario: `recordsFiltered` está inflado por la búsqueda difusa (en Éxito "huevos" da 1069 resultados y la página 50 son yogures). Éxito además rechaza `page > 50` con HTTP 400.

### Respuesta

```
{ "products": [...], "recordsFiltered": 87, "pagination": { "count": <número de páginas>, ... }, ... }
```

Cada producto trae **un solo item (SKU)** en todo lo observado de mercado.

| Dato | Campo | Notas |
|---|---|---|
| id de producto | `productId` | |
| id de SKU | `items[0].itemId` | En Jumbo suele ser igual a `productId`; en Éxito y Olímpica no siempre. |
| nombre | `productName` | |
| marca | `brand` | En Olímpica a veces sale mal ("SIN MARCA 2" para Kikes). |
| categoría | `categories[0]` | Ej. `/Mercado/Despensa/Granos y arroz/`, `/Supermercado/Despensa/Arroz Y Granos/` |
| URL | `link` | Absoluta en Éxito; relativa en Jumbo y Olímpica (anteponer el dominio). |
| **precio de venta** | `items[0].sellers[i].commertialOffer.Price` | Ya incluye la promoción vigente y la región. Es entero en COP. |
| precio de lista | `commertialOffer.ListPrice` | Precio "antes"; si `Price < ListPrice` hay promoción. |
| disponibilidad | `commertialOffer.AvailableQuantity > 0` | IS no da stock real (pone 10000) ni trae `IsAvailable`. En la práctica, si el producto aparece con la región y tiene `Price > 0`, está disponible (verificado en Jumbo: 100 de 100 contra la API de stock). |

### Reglas de limpieza (obligatorias)

1. **Elegir el seller correcto.** En productos de marketplace (terceros), `sellers` trae primero un seller `"1"` con `Price = 0` y `AvailableQuantity = 0`, y luego el tercero con el precio real. Leer `sellers[0]` a ciegas da precio 0. Regla: quedarse **solo con el seller cuyo `sellerId == "1"`** (la tienda propia) y descartar el producto si no tiene `Price > 0`. Esto elimina el marketplace (ollas arroceras, juguetes, cosméticos).
2. **No usar `priceRange.sellingPrice`.** No está regionalizado ni refleja la promo; puede salir más alto o más bajo que `Price`, o venir `null`.
3. **No usar `measurementUnit` / `unitMultiplier`.** Valen `"un"` y `1` en prácticamente todo (la única excepción vista: granel de fruver en Éxito, `measurementUnit = "kg"`, donde `Price` es por kilo).
4. **La búsqueda trae ruido aunque esté en la categoría de mercado**: leche en polvo al buscar "leche entera", pollo apanado archivado bajo `/Supermercado/Desayuno/Huevos/` en Olímpica, aceite de coco cosmético. Esto no se puede limpiar del todo con reglas; **lo resuelve el agente** al elegir el candidato equivalente con `select_product`.

### Lo que NO está en `Price`

Los descuentos con tarjeta de la tienda (Tarjeta Cencosud en Jumbo, Tarjeta Olímpica, Tarjeta Éxito) vienen como `teasers` con una lista de BINs y un porcentaje, y **no** se reflejan en `Price`. CanastAI compara el precio sin tarjeta.

## Éxito

- **URL recomendada** (sin barra antes de `?`; con barra redirige 301 a `http://`):
  ```
  https://www.exito.com/api/io/_v/api/intelligent-search/product_search/category-1/mercado?query={query}&page=1&count=20&regionId=U1cjZXhpdG9jb2wwMzc=&hideUnavailableItems=true
  ```
  - `/category-1/mercado` restringe a la categoría Mercado (quita champús "Leche Pal Pelo", aceite de motor, etc.).
  - `hideUnavailableItems=true` oculta lo que no hay en la tienda de la región.
- **Status raro: responde siempre HTTP 406 "Not Acceptable" con el JSON completo y válido** (pasa por CloudFront con `x-cache: Error from cloudfront`). Hay que tratar el 406 como éxito si el cuerpo es JSON con `products`. Es el mayor riesgo de estabilidad de las tres tiendas.
- **Región.** El sitio (VTEX FastStore, cuenta `exitocol`) fija la tienda con coordenadas:
  ```
  GET https://www.exito.com/api/sessionByCoords?geoCoordinates=-75.5812,6.2442&salesChannel=1
  → { "channel": "1", "regionId": "U1cjZXhpdG9jb2wwMzc=", ... }
  ```
  El `regionId` es el base64 de `SW#exitocol037`. Otras tiendas de Medellín: El Poblado `U1cjZXhpdG9jb2wwMzM=` (033), Bello `U1cjZXhpdG9jb2wwMzA=` (030). Entre 037 y 033 los precios de los SKU comunes fueron iguales (leche y arroz); solo cambia un poco el surtido. Bogotá es `U1cjZXhpdG9jb2wwODg=`.
  `/api/checkout/pub/regions?postalCode=...` **no sirve** aquí: devuelve la misma región para Medellín y Bogotá.
- **Presentación.** Las propiedades del producto son fiables para lo vendido por Éxito:
  - `Factor Neto PUM` = contenido total (`"1000"`, `"6600"`, `"30"`); ya incluye los multipacks (Alpina x6 de 1100 ml → 6600).
  - `Unidad de Medida PUM Calculado` = `Gramo` | `Mililitro` | `Unidad`.
  - El nombre repite el dato al final: `Arroz DIANA blanco premium (1000  gr)` (ojo: doble espacio, y a veces termina en `..`).
  - Casos dudosos: `Huevos AA NAPOLES libre jaula (1  und)` a $22.900 (seguramente es una cubeta). Fruver y algunos combos no traen PUM.
- **Precio cambia por ciudad** (Price por defecto / Medellín 037 / Bogotá 088):
  - Arroz DIANA premium 1000 gr (sku 730954): 5616 / 5616 / 6120
  - Arroz ROA 10000 gr (sku 583252): 46550 / 41600 / 46550
  - Leche deslactosada ALPINA x6 1100 ml (sku 576258): 40250 / 39100 / 35850
- **Ejemplo recortado** (Medellín):
  ```json
  {
    "productId": "130605",
    "productName": "Arroz DIANA blanco premium (1000  gr)",
    "brand": "DIANA",
    "link": "https://www.exito.com/arroz-premium-1000-gr-130605/p",
    "categories": ["/Mercado/Despensa/Granos y arroz/"],
    "properties": [
      {"name": "Factor Neto PUM", "values": ["1000"]},
      {"name": "Unidad de Medida PUM Calculado", "values": ["Gramo"]}
    ],
    "items": [{
      "itemId": "730954",
      "measurementUnit": "un", "unitMultiplier": 1,
      "sellers": [{
        "sellerId": "1", "sellerName": "exito", "sellerDefault": true,
        "commertialOffer": {"Price": 5616, "ListPrice": 6240, "AvailableQuantity": 10000}
      }]
    }]
  }
  ```

## Jumbo

- **URL recomendada:**
  ```
  https://www.jumbocolombia.com/_v/api/intelligent-search/product_search/?query={query}&page=1&count=20&locale=es-CO&regionId=v2.46CFB2D3A40B66B7454776AA8386DDAF
  ```
  (`/api/io/_v/api/...` es un alias que da lo mismo.) Las categorías propias empiezan por `/Supermercado/`; la búsqueda trae también comida de mascotas, jardinería y utensilios.
- **Región.** Cuenta VTEX `jumbocolombiaio`. La región se obtiene por **código DANE de 5 dígitos** (no el código postal de 6):
  ```
  GET https://www.jumbocolombia.com/api/checkout/pub/regions?country=COL&postalCode=05001
  → [{ "id": "v2.46CFB2D3A40B66B7454776AA8386DDAF", "sellers": [{"id": "jumbocolombiaioswl23", ...}] }]
  ```
  Con `050001` devuelve `sellers: []` (no sirve). 05001 corresponde a una sola tienda, la 23 "La 65". Envigado (05266) es `v2.BE9C09CCF1692DC07670EBA88B9BD364` (tienda Las Vegas). Bogotá (11001) es `v2.0DA521DD62CD11FC204C02B40D74F107`.
  El `sc` (sales channel) no es la ciudad: `sc=1` es el canal normal y `sc=6` es "Jumbo Express".
- **Presentación: solo en el nombre.** No hay propiedades PUM; `Contenido Neto` aparece en ~20 % de los productos y a veces contradice el nombre (huevos "X30 Unidades" con `Cantidad = 12`). Formatos vistos:
  `x1kg`, `x4000g`, `x2.5kg`, `x3000Ml`, `x3L`, `x4lt`, `x1000grs`, `x30und`, `X30 Unidades`, `1L x6unds`, `x6 Und x1000ml`, `x6 Und x1100` (¡sin unidad!), `x12Und x1100ml c/u`, `x25und x500g c-u`, `2Und 1800ml`.
- **Precio cambia por ciudad** (Price por defecto / Medellín / Bogotá):
  - Arroz Diana Premium x4000g (13412): 26700 / 24740 / 24820
  - Arroz Roa x1kg (13390): 3912 / 3712 / —
  - Leche Entera Alpina x6 x1100ml (23474): — / 40650 / 45190
- **Ejemplo recortado** (Medellín):
  ```json
  {
    "productId": "13390",
    "productName": "Arroz Roa x1kg",
    "brand": "ROA",
    "link": "/arroz-roa-x-1kg/p",
    "categories": ["/Supermercado/Despensa/Arroz Y Granos/"],
    "items": [{
      "itemId": "13390",
      "measurementUnit": "un", "unitMultiplier": 1,
      "sellers": [{
        "sellerId": "1", "sellerName": "Cencosud - Colombia", "sellerDefault": true,
        "commertialOffer": {"Price": 3712, "ListPrice": 4640, "AvailableQuantity": 10000}
      }]
    }]
  }
  ```

## Olímpica

- **URL recomendada:**
  ```
  https://www.olimpica.com/api/io/_v/api/intelligent-search/product_search/?query={query}&page=1&count=20&regionId=U1cjb2xpbXBpY2Fzd2wxMzQw
  ```
  Hubo un HTTP 500 aislado que se resolvió reintentando a los 2 s: hace falta el reintento con backoff.
- **Región.** Cuenta VTEX `olimpica`. El sitio regionaliza por **coordenadas** (el código postal solo devuelve sellers de marketplace). Las coordenadas de Medellín salen del propio selector de ciudades del sitio (`CitiesSelector.min.js`, dane_code 05001):
  ```
  GET https://www.olimpica.com/api/checkout/pub/regions?country=COL&geoCoordinates=-75.5720205;6.1857539
  → [{ "id": "U1cjb2xpbXBpY2Fzd2wxMzQw", "sellers": [{"id": "olimpicaswl1340", ...}] }]
  ```
  Es el base64 de `SW#olimpicaswl1340` (tienda 340 "San Lucas"). Otras tiendas de Medellín: Centro → `...wxMzQz` (1343), Laureles → `...wxMzQ2` (1346). En "leche entera" los precios de los SKU comunes fueron **idénticos** en las tres; cambia el surtido (28/26/31 resultados). Un punto del norte (6.305, -75.552) no tiene cobertura.
  Bogotá es `U1cjb2xpbXBpY2Fzd2wxNDA0`. Sin región, el frontend usa Barranquilla.
- **Presentación.** Propiedades PUM del grupo `PUM-GENERAL`:
  - `measurement_unit` = `Gramo` | `Mililitro` | `Unidad`; `unit_multiplier` = contenido total (multipack incluido: `1,1 Lt X6` → `6600`).
  - **Tienen errores**, así que hay que contrastarlas con el nombre:
    - Invertidas: `measurement_unit = "3000"`, `unit_multiplier = "ml"` (Aceite Medalla de Oro 3 Lt y 900 Ml).
    - Ausentes en ~15 % (frecuente en huevos y en productos con productId `100…`).
    - Valor errado: Leche Alquería Original **1,1 Lt X6** dice `1100` en vez de `6600`; un vinagre de 200 ml dice `200000`; leches Alquería en `Gramo`.
  - Formatos de nombre: `5 Kg`, `2500 G`, `1,1 Lt X6 Unds`, `900 Ml X6 Unds`, `X30 Unds`, `X15 un`, `Paca 12,5 Kg X25 Unds` (el peso ya es el total), `800ML X6`.
- **Precio cambia por ciudad** (Price por defecto / Medellín / Bogotá; confirmado también con la simulación de checkout):
  - Leche Alquería Original 1,1 Lt X6 (2312885): 35200 / 37350 / 38950
  - Leche Colanta Uht Entera 1,1 Lt X6 (1623329): 35390 / 33900 / 35390
  - Arroz Diana 3 Kg (518233): 13100 / 13100 / 12490
- **Ejemplo recortado** (Medellín):
  ```json
  {
    "productId": "1478933",
    "productName": "Leche Alpina Uht Deslactosada 1,1 Lt X6 Unds",
    "brand": "Alpina",
    "link": "/leche-uht-alpina-deslactosada-bolsa-11-l-x6-7702001029716-1478933/p",
    "categories": ["/Supermercado/Desayuno/Lácteos/"],
    "properties": [
      {"name": "measurement_unit", "values": ["Mililitro"]},
      {"name": "unit_multiplier", "values": ["6600"]}
    ],
    "items": [{
      "itemId": "1478933",
      "measurementUnit": "un", "unitMultiplier": 1,
      "sellers": [{
        "sellerId": "1", "sellerName": "Olimpica", "sellerDefault": true,
        "commertialOffer": {"Price": 37300, "ListPrice": 37300, "AvailableQuantity": 10000}
      }]
    }]
  }
  ```

## Alternativas descartadas

- **Catálogo legacy** `/api/catalog_system/pub/products/search?ft=...`: funciona en las tres (HTTP 206, máximo 50 por request), pero la relevancia es mala (al buscar "arroz" en Éxito el primero es una olla arrocera; con "huevos" salen organizadores de huevos y con "aceite", aceite de motor), pesa ~3 veces más y **ignora `regionId` en la URL**: la región solo se aplica con la cookie `vtex_segment` (base64 de un JSON con `regionId`). Sí sirve para consultar SKUs concretos (`fq=productId:X` o `fq=skuId:X`) con stock real, pero CanastAI no lo necesita.
- **Playwright**: no hizo falta; las tres tiendas tienen endpoint JSON usable.
- **Constructor.io** (Jumbo) y **GraphQL de FastStore** (Éxito): no se investigaron porque IS ya entrega los datos.

## Supuestos y limitaciones (van al README)

- "Medellín" = una tienda concreta por cadena (Éxito 037 Centro/Laureles, Jumbo 23 La 65, Olímpica 340 San Lucas). Dentro de la ciudad los precios coincidieron entre tiendas de la misma cadena en lo que se probó, pero no está garantizado.
- Los `regionId` pueden cambiar si las cadenas reorganizan tiendas. Se guardan en `config.yaml` junto con la URL para volver a obtenerlos.
- Los precios incluyen promociones con fecha de fin (p. ej. "30oct_02nov"); por eso el PDF lleva la fecha y hora de consulta.
- No se incluyen descuentos con tarjeta de la tienda.
- La disponibilidad es "aparece en la tienda de la región con precio > 0"; IS no da stock real.
- Son APIs públicas pero no documentadas: pueden cambiar sin aviso. El 406 de Éxito es el caso más frágil.
- La presentación no es un dato limpio en ninguna tienda; se toma del nombre y de las propiedades PUM, y se marca como dudosa cuando no cuadran.

## Reglas de acceso aplicadas

User-Agent descriptivo (`CanastAI/0.1 (proyecto academico de curso)`), requests secuenciales con ~1 s de pausa por tienda, timeout de 15 s y reintento con backoff ante 429/5xx. En unos 200 requests no hubo 429, captcha ni desafío anti-bot.
