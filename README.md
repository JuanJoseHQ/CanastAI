# CanastAI

Agente que recibe una lista de mercado, busca cada producto en **Éxito, Jumbo y Olímpica** (precios de **Medellín**), compara productos equivalentes y recomienda dónde comprar para pagar menos. Al final genera un PDF con la **Recomendación de compra**.

Trabajo final del curso de IA agéntica. Esta entrega es una demo que corre **en local**.

## Instalación

Requiere Python 3.11 o superior.

```bash
# 1. Crear y activar el entorno virtual
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Crear el archivo .env a partir del ejemplo
copy .env.example .env          # Windows
# cp .env.example .env          # macOS / Linux
```

### Configuración del `.env`

| Variable | Para qué sirve |
|---|---|
| `ANTHROPIC_API_KEY` | API key de Anthropic. Si se deja vacía, el SDK usa la sesión de Claude Code iniciada en el equipo. |
| `CANASTAI_MODEL` | Modelo del agente (p. ej. `claude-sonnet-5-5`). Vacío = el modelo por defecto del SDK. |
| `CANASTAI_LOG_LEVEL` | Nivel de logs: `DEBUG`, `INFO`, `WARNING` o `ERROR`. |

El `.env` está en `.gitignore`: nunca se sube al repositorio.

## Cómo correrlo

```bash
# La aplicación web (se abre en http://localhost:8501)
streamlit run app.py

# Desde la terminal, sin interfaz
python run_cli.py data/lista_prueba.txt
python run_cli.py data/lista_prueba.txt --tiendas exito jumbo --modo "marca específica"
python run_cli.py data/lista_prueba.txt --domicilio exito=5000 --domicilio jumbo=3900 --pdf recomendacion.pdf

# Los tests
pytest
```

La lista es un texto con **un producto por línea**, por ejemplo `Arroz 1 kg`, `2 Panela` o `Huevos AA x30`. Hay una lista de prueba en `data/lista_prueba.txt`.

## Cómo funciona

Este es el recorrido de una ejecución, archivo por archivo.

### 1. `app.py`: la pantalla

El usuario escribe la lista (o carga la de prueba), elige las tiendas, el modo (**estándar**: el equivalente más barato de cualquier marca; **marca específica**: respeta la marca y la presentación) y el costo de domicilio de cada tienda. Al presionar **Comparar precios**, `app.py` llama a una sola función: `run_agent()` de `agent.py`. Le pasa también una función `show_event` que escribe en pantalla cada paso del agente mientras trabaja.

`run_cli.py` hace lo mismo desde la terminal: lee el archivo de la lista, llama a `run_agent()` e imprime el resultado.

### 2. `agent.py`: el agente

`run_agent()` prepara una **sesión**: las líneas de la lista, las tiendas, el modo, los domicilios y dos diccionarios vacíos, uno para los productos encontrados y otro para las selecciones. Después arranca una conversación con Claude usando el **Claude Agent SDK**, con un *system prompt* que le explica su rol y cómo trabajar, y tres herramientas (*tools*):

| Tool | Qué hace |
|---|---|
| `search_store(store_id, query, limit)` | Llama a `stores.py`, guarda los productos encontrados en la sesión y le devuelve al modelo una lista con `product_id`, nombre, marca, categoría, presentación, precio y precio por kg/L/und. Por defecto muestra 20 productos: con 10, el agente no veía las bolsas de 1 kg de arroz en Jumbo, porque el buscador pone primero los paquetes grandes. |
| `select_product(item, store_id, product_id, units, reason)` | Registra qué producto eligió el agente para un ítem en una tienda (o `null` si no está). **Rechaza cualquier `product_id` que no haya salido de una búsqueda**, así el agente no puede inventar productos ni precios. `units` es cuántos paquetes comprar (p. ej. "2 Panela" son 2). |
| `finalize(assumptions)` | Verifica que haya una selección para cada ítem en cada tienda, llama a `products.compare()` y le devuelve al modelo los totales ya calculados. |

El modelo decide **qué buscar**, **reintenta** con sinónimos o términos más genéricos (hasta 2 veces) si no encuentra algo, **elige el producto equivalente** y al final **redacta la recomendación** usando solo las cifras que le devolvió `finalize`. Nunca calcula ni escribe precios propios.

`agent.py` es el único archivo con `async`, porque el SDK es asíncrono. `run_agent()` es síncrona y por dentro usa `asyncio.run()`, así el resto del código no tiene que saber de `async`.

Las tools no le escriben directamente a la pantalla: dejan cada aviso en una cola (`session.notify`) y el bucle principal los muestra (`flush_events`). Así, si Streamlit interrumpe la ejecución (por ejemplo, porque el usuario tocó un botón), la interrupción no rompe el servidor de tools del SDK.

### 3. `stores.py`: las tiendas

Las tres tiendas corren sobre la plataforma **VTEX** y tienen la misma API pública de búsqueda (*Intelligent Search*). `search_store()`:

1. Arma la URL con el texto a buscar y el `regionId` de la tienda de Medellín (está en `config.yaml`).
2. Hace el request con un User-Agent descriptivo, timeout de 15 s, máximo 2 requests simultáneos por tienda y reintentos con espera creciente ante errores 429/5xx.
3. Convierte cada producto del JSON en un `Product`:
   - Toma solo la oferta de la propia tienda (`sellerId "1"`) con precio mayor que 0, y descarta los productos de terceros (marketplace).
   - Lee la presentación del nombre y la contrasta con los datos de la tienda (Éxito y Olímpica los traen; Jumbo no).

Si una tienda falla, se lanza `StoreError`: el agente la marca como no disponible y sigue con las demás.

### 4. `products.py`: los datos y las cuentas

- **Dataclasses**: `Product`, `Selection` (la elección del agente para un ítem en una tienda) y `ComparisonResult` (todo el resultado).
- **Normalización**: `parse_presentation()` entiende nombres como `Arroz Diana x 500 g` (500 g), `Leche Colanta 1,1 Lt X6 Unds` (6600 ml) o `Huevos AA x30` (30 und). Todo queda en una unidad base: g, ml o und.
- **Cálculos** (todo con `Decimal`), en `compare()`:
  - **Tabla**: para cada ítem, la tienda más barata. Si en todas se compra la misma cantidad, gana el menor precio; si las presentaciones difieren, gana el menor precio por kg/L/und.
  - **Total por tienda**: comprar toda la lista en una sola tienda, más su domicilio. Lo que no se encontró se reporta aparte y no se suma.
  - **Óptimo dividido**: cada ítem en la tienda donde sale más barato, más el domicilio de cada tienda usada.
  - **Ahorro**: frente a la tienda más costosa, contando **solo los productos que todas las tiendas tienen en la misma cantidad**, para comparar lo mismo contra lo mismo. Los demás se nombran en una nota ("quedan por fuera porque..."). La pantalla y el PDF muestran los totales con los que se calculó el ahorro, así que la cifra se puede verificar. Si por los domicilios dividir la compra sale más caro, se dice que no conviene.
  - `describe_savings()` redacta esas frases una sola vez; las usan la app, el PDF, el CLI y el agente.

### 5. De vuelta en `app.py`, y `report_pdf.py`

Con el `ComparisonResult`, `app.py` muestra la recomendación, los totales, la tabla con el más barato resaltado, los supuestos y los productos no encontrados. `report_pdf.py` arma el PDF con esos mismos datos; del LLM solo toma el párrafo de recomendación.

Por eso **todo precio visible se puede rastrear** hasta un producto que devolvió `search_store`.

## Estructura

```
app.py              interfaz Streamlit
agent.py            system prompt, tools del agente y ejecución (único archivo async)
stores.py           consulta a las tiendas y conversión a Product
products.py         dataclasses, normalización de presentaciones y cálculos
report_pdf.py       PDF "Recomendación de compra"
run_cli.py          prueba desde la terminal
config.yaml         dominios, endpoints y región de cada tienda
data/lista_prueba.txt
fonts/              DejaVu Sans (licencia libre), para tildes y ñ en el PDF
tests/test_products.py
docs/endpoints.md   investigación de los endpoints (Fase 0)
docs/muestras_fase0.json  respuestas reales recortadas, usadas en los tests
```

## Supuestos y limitaciones

Estos puntos van al informe final. El detalle técnico está en [`docs/endpoints.md`](docs/endpoints.md).

**Región**
- "Medellín" es una tienda concreta por cadena: Éxito 037 (Centro/Laureles), Jumbo 23 (La 65) y Olímpica 340 (San Lucas). En lo que se probó, los precios coincidieron entre tiendas de la misma cadena dentro de la ciudad; cambia un poco el surtido.
- Los `regionId` están fijos en `config.yaml`. Si una cadena reorganiza sus tiendas pueden cambiar; junto a cada uno está la URL para obtenerlo de nuevo.

**Precios**
- Son **precios brutos**: incluyen las promociones generales vigentes, pero **no** los descuentos con tarjeta de la tienda (Tarjeta Éxito, Tarjeta Cencosud, Tarjeta Olímpica).
- Las promociones tienen fecha de fin, así que el PDF indica la fecha y hora de consulta.
- Disponibilidad: un producto se considera disponible si aparece en la tienda de Medellín con precio mayor que 0. La API no da el stock real.

**Datos de las tiendas**
- Son APIs públicas pero no documentadas, y pueden cambiar sin aviso. Éxito responde con HTTP 406 aunque el contenido sea correcto; CanastAI lo acepta, pero es el punto más frágil.
- La presentación no es un dato limpio en ninguna tienda. Se lee del nombre y se contrasta con los datos de la tienda, que en Olímpica a veces están invertidos o errados (p. ej. Leche Alquería x6 de 1,1 L aparece como 1100 ml). Cuando no cuadran, el producto queda marcado como "presentación dudosa" en los supuestos.
- Paquetes de varias unidades: en líquidos el tamaño del nombre es por unidad ("1,1 Lt X6" = 6,6 L). En pesos se toma como total ("Panela 4 Und 1000 gr" = 1 kg), salvo que diga "c/u". Hay nombres ambiguos que la regla no resuelve, como en Jumbo "Atún x 160g x 4und", que queda como 160 g, o "x6 Und x1100", sin unidad.
- La búsqueda devuelve ruido (leche en polvo al buscar "leche entera", pollo archivado bajo "Huevos"). El filtro de código quita el marketplace; lo demás lo resuelve el agente al elegir el equivalente.

**Alcance de esta entrega**
- Solo local: sin despliegue, sin lectura de listas por foto y sin snapshot offline de precios.
