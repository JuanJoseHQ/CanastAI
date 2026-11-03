"""
agent.py: el agente CanastAI.

Contiene:
- El system prompt (rol, tarea, contexto, detalles y salida).
- Las tres tools que puede usar el agente: search_store, select_product y finalize.
- La ejecución de la sesión con el Claude Agent SDK.

Es el único archivo async del proyecto. ¿Por qué? El Claude Agent SDK es asíncrono:
el cliente (ClaudeSDKClient) y las tools se escriben con async/await. Para que app.py
y run_cli.py no tengan que saber nada de async, este archivo expone una sola función
síncrona, run_agent(), que por dentro arranca la sesión con asyncio.run().

Reparto de responsabilidades:
- El LLM decide qué buscar, reintenta con sinónimos, elige el producto equivalente
  y redacta la recomendación.
- El código Python trae los precios (stores.py) y hace todas las cuentas (products.py).
  El LLM nunca produce ni transcribe precios propios.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Callable

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ClaudeSDKError,
    ResultMessage,
    TextBlock,
    create_sdk_mcp_server,
    tool,
)
from dotenv import load_dotenv

import products
from stores import StoreError, load_config, search_store

logger = logging.getLogger("canastai.agent")

MODE_STANDARD = "estándar"
MODE_BRAND = "marca específica"

SERVER_NAME = "canastai"
# El SDK expone las tools propias con el nombre "mcp__<servidor>__<tool>".
ALLOWED_TOOLS = [
    "mcp__canastai__search_store",
    "mcp__canastai__select_product",
    "mcp__canastai__finalize",
]
# Productos que search_store le muestra al agente. La especificación sugería 10, pero
# con 10 el agente no veía las bolsas de 1 kg de arroz en Jumbo (el buscador pone primero
# los paquetes grandes); con 20 sí aparecen. Es lo mismo que trae cada búsqueda a la tienda.
DEFAULT_SEARCH_LIMIT = 20
# Tope de idas y vueltas con el modelo. Una lista de 10 ítems en 3 tiendas
# necesita unas 60-90 tool calls; el modelo suele agrupar varias por turno.
MAX_TURNS = 150

SYSTEM_PROMPT = """\
Rol: eres CanastAI, un asistente experto en compras de mercado que ayuda a familias colombianas a comprar su canasta familiar al menor costo.

Tarea: con la lista de mercado y las tiendas que indique el usuario, busca el precio de cada producto, compara y recomienda dónde comprar para pagar menos.

Contexto: el usuario quiere cuidar su presupuesto pero no tiene tiempo de comparar a mano. Los precios están en pesos colombianos (COP) y corresponden a tiendas de Medellín.

Detalles:
- Respeta el modo:
  - "estándar": elige el producto equivalente más barato, de cualquier marca.
  - "marca específica": respeta la marca y la presentación que indique la línea.
- Compara solo productos equivalentes: misma variedad (leche entera no es deslactosada ni en polvo; arroz blanco no es integral; atún en agua no es en aceite) y una presentación lo más parecida posible a la pedida.
- Si no encuentras un producto, márcalo como no disponible. Nunca inventes precios ni productos.

Cómo trabajar con tus herramientas:
1. Interpreta cada línea de la lista: producto, cantidad y presentación. Si la línea no trae presentación, asume la más común en Colombia (arroz 1 kg, leche 1 L, huevos x30, aceite 1 L, azúcar 1 kg, panela 500 g...) y anótala como supuesto.
2. Para cada línea y cada tienda, llama search_store con una búsqueda corta y genérica: el nombre del producto, más la marca si el modo es "marca específica". No pongas cantidades en la búsqueda ("arroz", no "arroz 1 kg").
3. De los resultados, elige el producto equivalente. Revisa la lista completa: el buscador suele poner primero los paquetes grandes. Si la presentación pedida no aparece, haz una búsqueda un poco más específica (por ejemplo "arroz blanco" o el nombre de una marca común) antes de conformarte con otra presentación. Usa la presentación y el precio por unidad que te da la herramienta; no los calcules tú. Si un resultado trae una nota de "presentación dudosa", prefiere otro equivalente cuando exista.
4. Si no hay un equivalente, reintenta con hasta 2 búsquedas alternativas (un sinónimo, sin marca o un término más genérico) antes de darlo por no disponible.
5. Registra cada decisión con select_product: el ítem copiado EXACTO de la lista, la tienda, el product_id que devolvió search_store (o null si no está disponible), cuántos paquetes hay que comprar para cubrir la cantidad pedida (por ejemplo, "2 Panela" son 2; si piden 1 kg y el paquete es de 500 g, son 2) y una razón corta.
6. Cuando tengas una selección para cada ítem en cada tienda, llama finalize con la lista de supuestos que hiciste.
7. Escribe tu respuesta final: la recomendación de compra en 1 a 3 párrafos cortos, en español. La tabla, los totales y la lista de supuestos los arma la aplicación a partir de finalize, así que no los repitas completos. Usa texto simple: puedes usar **negrillas** y viñetas con "-", pero no títulos con "#" ni emojis (el texto también va al PDF).

Salida (tu respuesta final): dónde conviene comprar (una sola tienda o dividir la compra), cuánto se paga, el ahorro (apóyate en "resumen_del_ahorro"), la cobertura (si alguna tienda no tiene todos los productos) y cualquier advertencia importante. Usa EXCLUSIVAMENTE las cifras y los productos que devolvió finalize (incluida "seleccion_por_item"), copiados tal cual. No hagas cuentas propias ni describas productos distintos a los registrados.
"""


@dataclass
class AgentSession:
    """Todo lo que el agente va acumulando durante una ejecución."""
    items: list[str]                          # líneas de la lista de mercado
    store_ids: list[str]                      # tiendas a comparar
    mode: str
    delivery_costs: dict[str, Decimal]
    config: dict
    on_event: Callable[[str, str], None]      # avisa a la UI de cada paso
    consulted_at: str
    found_products: dict[str, products.Product] = field(default_factory=dict)       # product_id -> Product
    selections: dict[tuple[str, str], products.Selection] = field(default_factory=dict)  # (ítem, tienda) -> Selection
    failed_stores: dict[str, str] = field(default_factory=dict)                     # tienda -> error
    result: products.ComparisonResult | None = None
    # Las tools no llaman on_event directamente: dejan el aviso en esta cola y run_session
    # lo muestra. Así, si la UI lanza una excepción (p. ej. Streamlit al tocar un botón),
    # esta sale por el flujo principal y no rompe el servidor de tools del SDK.
    pending_events: list[tuple[str, str]] = field(default_factory=list)

    def store_name(self, store_id: str) -> str:
        """Nombre bonito de la tienda: "exito" -> "Éxito"."""
        return self.config["stores"][store_id]["name"]

    def store_names(self) -> dict[str, str]:
        """Diccionario id -> nombre de todas las tiendas."""
        names = {}
        for store_id in self.config["stores"]:
            names[store_id] = self.store_name(store_id)
        return names

    def notify(self, kind: str, message: str) -> None:
        """Deja un aviso para la UI en la cola (lo muestra run_session)."""
        self.pending_events.append((kind, message))

    def flush_events(self) -> None:
        """Muestra en la UI los avisos pendientes, en orden."""
        while len(self.pending_events) > 0:
            kind, message = self.pending_events.pop(0)
            self.on_event(kind, message)


def text_result(text: str) -> dict:
    """Respuesta normal de una tool, en el formato que espera el SDK."""
    return {"content": [{"type": "text", "text": text}]}


def error_result(text: str) -> dict:
    """Respuesta de error de una tool: el modelo la lee y puede corregir su llamada."""
    return {"content": [{"type": "text", "text": text}], "is_error": True}


def describe_product_for_agent(product: products.Product) -> dict:
    """Resume un producto para el modelo. Las cifras ya vienen calculadas por products.py."""
    unit_price = products.price_per_reference_unit(product)
    description = {
        "product_id": product.product_id,
        "nombre": product.name,
        "marca": product.brand,
        "categoria": product.category,
        "presentacion": products.format_quantity(product.quantity, product.base_unit),
        "precio": products.format_cop(product.price),
    }
    if unit_price is not None:
        description["precio_por_unidad"] = (
            f"{products.format_cop(unit_price)} por {products.reference_unit_label(product.base_unit)}"
        )
    if product.list_price > product.price:
        description["precio_antes"] = products.format_cop(product.list_price)
    if product.presentation_note:
        description["nota"] = product.presentation_note
    return description


def describe_result_for_agent(session: AgentSession, result: products.ComparisonResult) -> str:
    """Resume el resultado de finalize con las cifras ya formateadas, para que el modelo las copie."""
    summary = {
        "totales_por_tienda": {},
        "optimo_dividido": {
            "total": products.format_cop(result.split_total),
            "domicilios": products.format_cop(result.split_delivery),
            "donde_comprar_cada_item": {},
        },
        "resumen_del_ahorro": products.describe_savings(result, session.store_names()),
        "no_encontrados_en_ninguna_tienda": result.not_found,
        "tiendas_que_fallaron": {},
        "tiendas_sin_ningun_producto": [session.store_name(store_id) for store_id in result.stores_without_products],
        "seleccion_por_item": {},
    }
    # Detalle de lo elegido, para que la recomendación hable de lo que de verdad quedó registrado.
    for row in result.rows:
        detail = {}
        for store_id, selection in row.selections.items():
            if selection.product is None:
                detail[session.store_name(store_id)] = "no disponible"
                continue
            product = selection.product
            detail[session.store_name(store_id)] = (
                f"{product.name} ({products.format_quantity(product.quantity, product.base_unit)}) "
                f"x{selection.units} = {products.format_cop(products.line_cost(selection))}"
            )
        if row.cheapest_store is not None:
            detail["mas_barato"] = session.store_name(row.cheapest_store)
        summary["seleccion_por_item"][row.item] = detail
    for store_id, store_total in result.store_totals.items():
        summary["totales_por_tienda"][session.store_name(store_id)] = {
            "total": products.format_cop(store_total.total),
            "domicilio": products.format_cop(store_total.delivery_cost),
            "productos_encontrados": len(store_total.found_items),
            "productos_faltantes": store_total.missing_items,
        }
    for item, store_id in result.split_plan.items():
        summary["optimo_dividido"]["donde_comprar_cada_item"][item] = session.store_name(store_id)
    for store_id, error in result.failed_stores.items():
        summary["tiendas_que_fallaron"][session.store_name(store_id)] = error
    return json.dumps(summary, ensure_ascii=False, indent=1)


# ---------------------------------------------------------------------------
# Tools del agente
# ---------------------------------------------------------------------------

SEARCH_STORE_SCHEMA = {
    "type": "object",
    "properties": {
        "store_id": {"type": "string", "description": "Tienda: exito, jumbo u olimpica."},
        "query": {"type": "string", "description": "Texto corto a buscar, sin cantidades. Ej: 'arroz', 'leche entera'."},
        "limit": {"type": "integer", "description": f"Máximo de productos a devolver (por defecto {DEFAULT_SEARCH_LIMIT})."},
    },
    "required": ["store_id", "query"],
}

SELECT_PRODUCT_SCHEMA = {
    "type": "object",
    "properties": {
        "item": {"type": "string", "description": "La línea de la lista, copiada exacta."},
        "store_id": {"type": "string", "description": "Tienda: exito, jumbo u olimpica."},
        "product_id": {
            "type": ["string", "null"],
            "description": "product_id devuelto por search_store, o null si no está disponible.",
        },
        "units": {"type": "integer", "description": "Cuántos paquetes comprar (1 o más). Usa 0 si product_id es null."},
        "reason": {"type": "string", "description": "Razón corta de la elección."},
    },
    "required": ["item", "store_id", "product_id", "units", "reason"],
}

FINALIZE_SCHEMA = {
    "type": "object",
    "properties": {
        "assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Supuestos que hiciste, p. ej. 'Arroz: no se indicó presentación, se asumió 1 kg'.",
        },
    },
    "required": ["assumptions"],
}


def create_tools(session: AgentSession) -> list:
    """Crea las tres tools del agente.

    Se definen dentro de esta función para que puedan leer y modificar `session`
    (los productos encontrados y las selecciones de esta ejecución).
    """

    @tool("search_store", "Busca un producto en una tienda y devuelve los productos encontrados con su product_id, presentación y precio.", SEARCH_STORE_SCHEMA)
    async def search_store_tool(args: dict) -> dict:
        """Busca en la tienda, guarda los productos en la sesión y se los describe al modelo."""
        store_id = args["store_id"]
        query = args["query"]
        limit = int(args.get("limit", DEFAULT_SEARCH_LIMIT))

        if store_id not in session.store_ids:
            return error_result(f"Tienda no válida: {store_id}. Usa una de: {', '.join(session.store_ids)}.")
        if store_id in session.failed_stores:
            return error_result(
                f"{session.store_name(store_id)} no está disponible ({session.failed_stores[store_id]}). "
                f"Registra sus ítems con product_id null."
            )

        session.notify("busqueda", f"Buscando «{query}» en {session.store_name(store_id)}")
        try:
            # La consulta HTTP es síncrona; se corre en otro hilo para no frenar
            # las demás búsquedas que el agente pida en paralelo.
            found = await asyncio.to_thread(search_store, store_id, query, session.config, limit)
        except StoreError as error:
            session.failed_stores[store_id] = str(error)
            session.notify("error", f"{session.store_name(store_id)} falló: {error}. Se marca como no disponible.")
            return error_result(
                f"{session.store_name(store_id)} no respondió ({error}). "
                f"La tienda queda marcada como no disponible: registra sus ítems con product_id null."
            )

        for product in found:
            session.found_products[product.product_id] = product
        session.notify("resultado", f"{session.store_name(store_id)}: {len(found)} productos para «{query}»")

        if len(found) == 0:
            return text_result("No se encontraron productos. Prueba con un sinónimo o un término más genérico.")
        descriptions = [describe_product_for_agent(product) for product in found]
        return text_result(json.dumps(descriptions, ensure_ascii=False))

    @tool("select_product", "Registra el producto elegido para un ítem de la lista en una tienda (o null si no está disponible).", SELECT_PRODUCT_SCHEMA)
    async def select_product_tool(args: dict) -> dict:
        """Valida y registra la elección del agente para un ítem en una tienda."""
        item = args["item"]
        store_id = args["store_id"]
        product_id = args.get("product_id")
        units = int(args.get("units", 1))
        reason = args.get("reason", "")

        if item not in session.items:
            valid_items = "; ".join(session.items)
            return error_result(f"Ítem desconocido: «{item}». Copia exacto uno de estos: {valid_items}")
        if store_id not in session.store_ids:
            return error_result(f"Tienda no válida: {store_id}. Usa una de: {', '.join(session.store_ids)}.")

        confirmation = "Registrado."
        if session.result is not None:
            # Cambió una selección después de finalize: el resultado anterior ya no vale.
            session.result = None
            confirmation = "Registrado. Cambiaste una selección después de finalize: vuelve a llamar finalize antes de responder."

        if product_id is None or product_id == "":
            selection = products.Selection(item, store_id, None, 0, reason)
            session.selections[(item, store_id)] = selection
            session.notify("seleccion", f"{item} → {session.store_name(store_id)}: no disponible ({reason})")
            return text_result(confirmation)

        # Solo se aceptan productos que devolvió search_store en esta sesión,
        # para que el agente no pueda inventar productos ni precios.
        product = session.found_products.get(product_id)
        if product is None:
            return error_result(
                f"El product_id «{product_id}» no salió de ninguna búsqueda. "
                f"Usa un product_id devuelto por search_store, o null."
            )
        if product.store_id != store_id:
            return error_result(f"El product_id «{product_id}» es de otra tienda ({product.store_id}), no de {store_id}.")
        if units < 1:
            return error_result("units debe ser 1 o más cuando hay un producto elegido.")

        selection = products.Selection(item, store_id, product, units, reason)
        session.selections[(item, store_id)] = selection
        session.notify(
            "seleccion",
            f"{item} → {session.store_name(store_id)}: {product.name} x{units} ({products.format_cop(product.price)} c/u)",
        )
        return text_result(confirmation)

    @tool("finalize", "Calcula totales, óptimo dividido y ahorro con las selecciones registradas. Llamar una vez al final.", FINALIZE_SCHEMA)
    async def finalize_tool(args: dict) -> dict:
        """Revisa que no falten selecciones y calcula el resultado con products.compare()."""
        assumptions = list(args.get("assumptions", []))

        # Verifica que haya una selección para cada ítem en cada tienda que respondió.
        missing_pairs = []
        for item in session.items:
            for store_id in session.store_ids:
                if store_id in session.failed_stores:
                    continue
                if (item, store_id) not in session.selections:
                    missing_pairs.append(f"«{item}» en {store_id}")
        if len(missing_pairs) > 0:
            return error_result(
                "Faltan selecciones (usa product_id null si no lo encontraste): " + "; ".join(missing_pairs)
            )

        # Las notas de presentación de los productos elegidos también son supuestos.
        for selection in session.selections.values():
            if selection.product is not None and selection.product.presentation_note:
                assumptions.append(
                    f"{session.store_name(selection.store_id)}, {selection.product.name}: "
                    f"{selection.product.presentation_note}"
                )

        result = products.compare(
            items=session.items,
            stores=session.store_ids,
            selections=list(session.selections.values()),
            delivery_costs=session.delivery_costs,
            assumptions=assumptions,
            failed_stores=session.failed_stores,
            mode=session.mode,
            city=session.config["city"],
            consulted_at=session.consulted_at,
        )
        session.result = result
        session.notify("finalizado", "Cálculos listos; el agente está redactando la recomendación.")
        return text_result(describe_result_for_agent(session, result))

    return [search_store_tool, select_product_tool, finalize_tool]


# ---------------------------------------------------------------------------
# Ejecución de la sesión
# ---------------------------------------------------------------------------

class AgentError(Exception):
    """El agente no pudo terminar la comparación."""


def build_user_prompt(session: AgentSession) -> str:
    """Arma el mensaje inicial para el agente con la lista, las tiendas y el modo."""
    lines = ["Lista de mercado (usa estas líneas EXACTAS como `item`):"]
    for item in session.items:
        lines.append(f"- {item}")
    lines.append("")
    store_descriptions = [f"{store_id} ({session.store_name(store_id)})" for store_id in session.store_ids]
    lines.append("Tiendas a comparar: " + ", ".join(store_descriptions))
    lines.append(f"Modo: {session.mode}")
    lines.append(f"Ciudad: {session.config['city']}")
    return "\n".join(lines)


async def run_session(session: AgentSession) -> products.ComparisonResult:
    """Corre la conversación con el modelo hasta que termine (parte async)."""
    server = create_sdk_mcp_server(name=SERVER_NAME, tools=create_tools(session))
    model = os.getenv("CANASTAI_MODEL") or None   # vacío = modelo por defecto del SDK
    options = ClaudeAgentOptions(
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SERVER_NAME: server},
        tools=[],                      # sin herramientas de Claude Code: solo las nuestras
        allowed_tools=ALLOWED_TOOLS,   # nuestras tools corren sin pedir permiso
        permission_mode="dontAsk",     # cualquier otra herramienta se rechaza
        setting_sources=[],            # no carga configuraciones locales de Claude Code
        max_turns=MAX_TURNS,
        model=model,
    )

    final_text = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(build_user_prompt(session))
        async for message in client.receive_response():
            session.flush_events()
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    # Antes de finalize, el texto del agente son comentarios de avance y se
                    # muestran en vivo; después es la recomendación final, que se muestra aparte.
                    if isinstance(block, TextBlock) and block.text.strip() and session.result is None:
                        session.notify("agente", block.text.strip())
            elif isinstance(message, ResultMessage):
                logger.info(
                    "Sesión terminada: %s, %d turnos, %.1f s, costo USD %s",
                    message.subtype, message.num_turns, message.duration_ms / 1000, message.total_cost_usd,
                )
                final_text = message.result or ""
                if message.is_error and session.result is None:
                    raise AgentError(f"El agente terminó con error: {message.subtype} {message.errors or ''}")
                if message.is_error:
                    # Los cálculos ya estaban listos; solo falló la redacción.
                    logger.warning("El modelo falló después de finalize: %s", message.subtype)
                    final_text = "El agente no pudo redactar la recomendación; revisa la tabla y los totales."
    session.flush_events()

    if session.result is None:
        raise AgentError("El agente terminó sin llamar a finalize; no hay resultado que mostrar.")
    session.result.recommendation_text = final_text.strip()
    return session.result


def parse_shopping_list(shopping_list: str) -> list[str]:
    """Convierte el texto de la lista en ítems: una línea por producto, sin líneas vacías ni repetidas."""
    items = []
    for line in shopping_list.splitlines():
        line = line.strip()
        if line == "" or line.startswith("#") or line in items:
            continue
        items.append(line)
    return items


def ignore_event(kind: str, message: str) -> None:
    """on_event por defecto: no muestra nada."""


def run_agent(
    shopping_list: str,
    stores: list[str],
    mode: str,
    delivery_costs: dict[str, Decimal] | None = None,
    on_event: Callable[[str, str], None] | None = None,
) -> products.ComparisonResult:
    """Ejecuta el agente completo y devuelve el resultado de la comparación.

    Es la única puerta de entrada al agente y es síncrona, para que app.py y
    run_cli.py no tengan que usar async.

    shopping_list: texto con una línea por producto.
    stores: ids de tienda, p. ej. ["exito", "jumbo", "olimpica"].
    mode: MODE_STANDARD o MODE_BRAND.
    delivery_costs: costo de domicilio por tienda (por defecto 0).
    on_event: función (tipo, mensaje) que se llama en cada paso, para mostrarlo en vivo.
    """
    load_dotenv()
    config = load_config()

    items = parse_shopping_list(shopping_list)
    if len(items) == 0:
        raise ValueError("La lista de mercado está vacía.")
    if len(stores) == 0:
        raise ValueError("Elige al menos una tienda.")
    for store_id in stores:
        if store_id not in config["stores"]:
            raise ValueError(f"Tienda desconocida: {store_id}")
    if mode not in (MODE_STANDARD, MODE_BRAND):
        raise ValueError(f"Modo desconocido: {mode}")

    clean_delivery_costs = {}
    for store_id in stores:
        value = Decimal("0")
        if delivery_costs is not None and store_id in delivery_costs:
            value = Decimal(str(delivery_costs[store_id]))
        clean_delivery_costs[store_id] = value

    session = AgentSession(
        items=items,
        store_ids=list(stores),
        mode=mode,
        delivery_costs=clean_delivery_costs,
        config=config,
        on_event=on_event or ignore_event,
        consulted_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )
    logger.info("Iniciando agente: %d ítems, tiendas %s, modo %s", len(items), stores, mode)
    try:
        return asyncio.run(run_session(session))
    except ClaudeSDKError as error:
        # Errores del SDK (sin credenciales, proceso caído...) se traducen a AgentError
        # para que app.py y run_cli.py no tengan que conocer el SDK.
        raise AgentError(f"Falló la comunicación con el modelo: {error}") from error
    except AgentError:
        raise
    except Exception as error:
        # El SDK también lanza Exception simple (p. ej. "Control request timeout").
        # Se registra completo en el log y se traduce, no se silencia.
        logger.exception("Error inesperado al correr el agente")
        raise AgentError(f"Falló la comunicación con el modelo: {error}") from error
