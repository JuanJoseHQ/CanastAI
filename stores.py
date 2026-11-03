"""
stores.py: consulta a las tiendas.

Arma la URL de búsqueda de cada tienda, hace el request respetando las reglas de
acceso (User-Agent descriptivo, timeout, máximo 2 requests simultáneos por tienda
y reintentos con espera creciente) y convierte la respuesta JSON en objetos Product.

Las tres tiendas (Éxito, Jumbo y Olímpica) usan la misma API de búsqueda de VTEX
(Intelligent Search), así que el request y casi todo el parseo son iguales. Lo único
que cambia por tienda es de dónde sale la presentación del producto:
- Éxito: propiedades "Factor Neto PUM" y "Unidad de Medida PUM Calculado".
- Olímpica: propiedades "unit_multiplier" y "measurement_unit" (a veces invertidas).
- Jumbo: no trae esos datos; solo sirve el nombre.

Los detalles de cada endpoint están en docs/endpoints.md.
"""

import logging
import threading
import time
from decimal import Decimal
from pathlib import Path

import requests
import yaml

from products import Presentation, Product, choose_presentation, presentation_from_store_fields

logger = logging.getLogger("canastai.stores")

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# Un semáforo por tienda limita cuántos requests simultáneos se le mandan.
# Hace falta porque el agente puede pedir varias búsquedas a la vez.
store_semaphores: dict[str, threading.Semaphore] = {}
semaphores_lock = threading.Lock()


class StoreError(Exception):
    """La tienda falló (timeout, error HTTP o respuesta inválida). Las demás tiendas siguen."""


def load_config(path: Path = CONFIG_PATH) -> dict:
    """Lee config.yaml: tiendas, región y reglas de acceso."""
    with open(path, encoding="utf-8") as config_file:
        return yaml.safe_load(config_file)


def get_store_semaphore(store_id: str, max_concurrent: int) -> threading.Semaphore:
    """Devuelve el semáforo de la tienda, creándolo la primera vez."""
    with semaphores_lock:
        if store_id not in store_semaphores:
            store_semaphores[store_id] = threading.Semaphore(max_concurrent)
        return store_semaphores[store_id]


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------

def build_search_request(store_id: str, query: str, config: dict) -> tuple[str, dict]:
    """Arma la URL y los parámetros de búsqueda de una tienda, con la región de Medellín."""
    store = config["stores"][store_id]
    url = store["domain"] + store["search_path"]
    params = {
        "query": query,
        "page": 1,
        "count": config["results_per_search"],
        "regionId": store["region_id"],
    }
    params.update(store["extra_params"])
    return url, params


def fetch_json(store_id: str, url: str, params: dict, config: dict) -> dict:
    """Hace el GET con reintentos y devuelve el JSON de la respuesta.

    Reintenta ante 429, errores 5xx, timeouts y errores de conexión, esperando
    2 s, 4 s, ... entre intentos. Cualquier otro error termina en StoreError.
    """
    store = config["stores"][store_id]
    headers = {"User-Agent": config["user_agent"], "Accept": "application/json"}
    semaphore = get_store_semaphore(store_id, config["max_concurrent_per_store"])
    total_attempts = config["max_retries"] + 1
    last_error = ""

    for attempt in range(1, total_attempts + 1):
        try:
            with semaphore:
                response = requests.get(url, params=params, headers=headers, timeout=config["timeout_seconds"])
        except requests.Timeout:
            last_error = f"timeout de {config['timeout_seconds']} s"
        except requests.ConnectionError as error:
            last_error = f"error de conexión: {error}"
        except requests.RequestException as error:
            # Otros errores de requests (respuesta cortada, demasiadas redirecciones...): no se reintentan.
            raise StoreError(f"error en el request: {error}") from error
        else:
            status = response.status_code
            if status in store["ok_status_codes"]:
                try:
                    data = response.json()
                except ValueError as error:
                    raise StoreError(f"la respuesta no es JSON válido ({error})") from error
                if not isinstance(data, dict) or "products" not in data:
                    raise StoreError(f"respuesta inesperada con HTTP {status}: no trae 'products'")
                if status == 406:
                    # Éxito siempre responde 406 con el JSON correcto (ver docs/endpoints.md).
                    logger.warning("[%s] HTTP 406 con JSON válido; se acepta como respuesta correcta", store_id)
                return data
            if status == 429 or status >= 500:
                last_error = f"HTTP {status}"
            else:
                raise StoreError(f"HTTP {status}")

        if attempt < total_attempts:
            wait_seconds = 2 ** attempt
            logger.warning("[%s] intento %d falló (%s); reintento en %d s", store_id, attempt, last_error, wait_seconds)
            time.sleep(wait_seconds)

    raise StoreError(f"falló tras {total_attempts} intentos: {last_error}")


# ---------------------------------------------------------------------------
# Parseo de la respuesta
# ---------------------------------------------------------------------------

def get_property(raw_product: dict, property_name: str) -> str | None:
    """Devuelve el primer valor de una propiedad del producto, o None si no la tiene."""
    for prop in raw_product.get("properties", []):
        if prop.get("name") == property_name and prop.get("values"):
            return str(prop["values"][0])
    return None


def find_store_offer(raw_item: dict) -> dict | None:
    """Busca la oferta de la propia tienda (sellerId "1") con precio mayor que cero.

    En productos de marketplace la tienda aparece con precio 0 y el precio real es
    de un tercero. Esos productos se descartan: CanastAI compara la tienda, no terceros.
    """
    for seller in raw_item.get("sellers", []):
        offer = seller.get("commertialOffer", {})
        if seller.get("sellerId") == "1" and offer.get("Price", 0) > 0:
            return offer
    return None


def read_exito_presentation(raw_product: dict) -> Presentation | None:
    """Presentación según Éxito: "Factor Neto PUM" (cantidad) y "Unidad de Medida PUM Calculado"."""
    quantity_text = get_property(raw_product, "Factor Neto PUM")
    unit_text = get_property(raw_product, "Unidad de Medida PUM Calculado")
    if quantity_text is None or unit_text is None:
        return None
    return presentation_from_store_fields(quantity_text, unit_text)


def read_olimpica_presentation(raw_product: dict) -> Presentation | None:
    """Presentación según Olímpica: "unit_multiplier" (cantidad) y "measurement_unit" (unidad).

    A veces vienen invertidos ("3000" en la unidad y "ml" en la cantidad); en ese caso se voltean.
    """
    quantity_text = get_property(raw_product, "unit_multiplier")
    unit_text = get_property(raw_product, "measurement_unit")
    if quantity_text is None or unit_text is None:
        return None
    if unit_text.strip().isdigit():
        quantity_text, unit_text = unit_text, quantity_text
    return presentation_from_store_fields(quantity_text, unit_text)


def parse_product(store_id: str, raw_product: dict, domain: str) -> Product | None:
    """Convierte un producto del JSON de VTEX en un Product.

    Devuelve None si el producto no es de la tienda (marketplace) o no tiene precio.
    """
    items = raw_product.get("items") or []
    if len(items) == 0:
        return None
    raw_item = items[0]   # en mercado siempre hay un solo item (SKU) por producto
    offer = find_store_offer(raw_item)
    if offer is None:
        return None

    name = raw_product.get("productName", "").strip()

    if store_id == "exito":
        store_presentation = read_exito_presentation(raw_product)
    elif store_id == "olimpica":
        store_presentation = read_olimpica_presentation(raw_product)
    else:
        # Jumbo no trae datos de presentación confiables: solo el nombre.
        store_presentation = None

    if raw_item.get("measurementUnit") == "kg":
        # Granel (frutas y verduras): el precio que da la tienda es por kilo.
        presentation = Presentation(quantity=Decimal("1000"), base_unit="g")
        note = "Producto a granel: el precio es por kg."
    else:
        presentation, note = choose_presentation(name, store_presentation)

    link = raw_product.get("link", "")
    if link.startswith("/"):
        link = domain + link

    categories = raw_product.get("categories") or [""]
    list_price = offer.get("ListPrice") or offer["Price"]

    return Product(
        store_id=store_id,
        product_id=f"{store_id}:{raw_item.get('itemId')}",
        name=name,
        brand=raw_product.get("brand", ""),
        quantity=presentation.quantity if presentation else None,
        base_unit=presentation.base_unit if presentation else None,
        # str() evita que el float de JSON meta decimales falsos en el Decimal.
        price=Decimal(str(offer["Price"])),
        list_price=Decimal(str(list_price)),
        available=offer.get("AvailableQuantity", 0) > 0,
        url=link,
        category=categories[0],
        presentation_note=note,
    )


def parse_search_response(store_id: str, data: dict, domain: str) -> list[Product]:
    """Convierte la respuesta completa de una búsqueda en una lista de Product."""
    products = []
    for raw_product in data.get("products") or []:
        try:
            product = parse_product(store_id, raw_product, domain)
        except (KeyError, TypeError, AttributeError, ValueError, ArithmeticError) as error:
            # Un producto con datos incompletos no debe tumbar toda la búsqueda.
            logger.warning("[%s] producto descartado por datos incompletos: %s", store_id, error)
            continue
        if product is not None:
            products.append(product)
    return products


# ---------------------------------------------------------------------------
# Función principal
# ---------------------------------------------------------------------------

def search_store(store_id: str, query: str, config: dict, limit: int = 20) -> list[Product]:
    """Busca un texto en una tienda y devuelve hasta `limit` productos de la tienda.

    Lanza StoreError si la tienda falla, para que quien llama decida qué hacer.
    """
    if store_id not in config["stores"]:
        raise StoreError(f"tienda desconocida: {store_id}")

    url, params = build_search_request(store_id, query, config)
    started = time.monotonic()
    try:
        data = fetch_json(store_id, url, params, config)
    except StoreError as error:
        logger.error("[%s] búsqueda '%s' falló: %s", store_id, query, error)
        raise
    elapsed = time.monotonic() - started

    domain = config["stores"][store_id]["domain"]
    products = parse_search_response(store_id, data, domain)
    logger.info(
        "[%s] búsqueda '%s': %d productos de la tienda (de %d resultados) en %.1f s",
        store_id, query, len(products), len(data.get("products") or []), elapsed,
    )
    return products[:limit]
