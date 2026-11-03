"""
products.py: datos y cálculos de CanastAI.

Este archivo tiene tres partes:
1. Las dataclasses con las que se mueve la información (Product, Selection, ComparisonResult...).
2. La normalización de presentaciones: leer "Arroz Diana x 500 g" y entender que son 500 g.
3. Los cálculos: precio por kg/L/und, totales por tienda, óptimo dividido y ahorro.

Todo el dinero se maneja con Decimal para no tener errores de redondeo.
El LLM nunca calcula cifras: las únicas que muestra CanastAI salen de estas funciones.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP


# ---------------------------------------------------------------------------
# 1. Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Presentation:
    """Cantidad de un producto expresada en su unidad base: g, ml o und."""
    quantity: Decimal
    base_unit: str


@dataclass
class Product:
    """Un producto tal como lo devolvió una tienda, ya limpio."""
    store_id: str
    product_id: str              # id único en la sesión: "<tienda>:<sku>", p. ej. "exito:730954"
    name: str
    brand: str
    quantity: Decimal | None     # contenido total en la unidad base; None si no se pudo leer
    base_unit: str | None        # "g", "ml" o "und"
    price: Decimal               # precio de venta en COP (ya trae la promoción vigente)
    list_price: Decimal          # precio "antes" de la promoción
    available: bool
    url: str = ""
    category: str = ""
    presentation_note: str = ""  # vacío si la presentación es confiable


@dataclass
class Selection:
    """La elección del agente para un ítem de la lista en una tienda."""
    item: str                    # la línea de la lista, tal cual la escribió el usuario
    store_id: str
    product: Product | None      # None significa "no disponible en esta tienda"
    units: int                   # cuántos paquetes hay que comprar
    reason: str


@dataclass
class ItemRow:
    """Una fila de la tabla producto x tienda."""
    item: str
    selections: dict[str, Selection]   # store_id -> Selection
    cheapest_store: str | None         # None si ninguna tienda lo tiene
    compared_by: str                   # cómo se decidió el más barato


@dataclass
class StoreTotal:
    """Lo que costaría comprar toda la lista en una sola tienda."""
    store_id: str
    subtotal: Decimal
    delivery_cost: Decimal
    total: Decimal
    found_items: list[str]
    missing_items: list[str]


@dataclass
class ComparisonResult:
    """Resultado completo de una comparación. La UI y el PDF se construyen desde aquí."""
    city: str
    mode: str
    stores: list[str]                       # tiendas que se pidieron comparar
    consulted_at: str                       # fecha y hora de la consulta de precios
    rows: list[ItemRow]
    store_totals: dict[str, StoreTotal]
    common_items: list[str]                 # ítems que encontraron todas las tiendas
    comparable_totals: dict[str, Decimal]   # total por tienda solo con common_items
    split_plan: dict[str, str]              # ítem -> tienda donde sale más barato
    split_subtotal: Decimal
    split_delivery: Decimal
    split_total: Decimal
    split_comparable_total: Decimal         # óptimo dividido solo con common_items
    cheapest_store: str | None
    most_expensive_store: str | None
    savings_single_store: Decimal           # tienda más costosa - tienda más barata
    savings_split: Decimal                  # tienda más costosa - óptimo dividido
    savings_note: str
    assumptions: list[str]
    not_found: list[str]
    failed_stores: dict[str, str] = field(default_factory=dict)   # tienda -> error
    recommendation_text: str = ""           # único texto redactado por el LLM


# ---------------------------------------------------------------------------
# 2. Normalización de presentaciones
# ---------------------------------------------------------------------------

# Palabras de unidad que aparecen en los nombres y cómo se convierten a la unidad base.
# El número es el factor de conversión: 1 kg = 1000 g, 1 L = 1000 ml.
UNIT_CONVERSIONS = {
    "g": ("g", 1), "gr": ("g", 1), "grs": ("g", 1), "gramo": ("g", 1), "gramos": ("g", 1),
    "kg": ("g", 1000), "kilo": ("g", 1000), "kilos": ("g", 1000),
    "kilogramo": ("g", 1000), "kilogramos": ("g", 1000),
    "ml": ("ml", 1), "cc": ("ml", 1), "mililitro": ("ml", 1), "mililitros": ("ml", 1),
    "l": ("ml", 1000), "lt": ("ml", 1000), "lts": ("ml", 1000),
    "litro": ("ml", 1000), "litros": ("ml", 1000),
    "und": ("und", 1), "unds": ("und", 1), "un": ("und", 1),
    "unidad": ("und", 1), "unidades": ("und", 1),
}

# Un número con unidad de peso o volumen: "500 g", "x5kg", "1,1 Lt", "3000Ml".
SIZE_PATTERN = re.compile(
    r"(\d+(?:[.,]\d+)?)\s*(kilogramos?|kilos?|kg|gramos?|grs?|g|mililitros?|ml|cc|litros?|lts?|l)\b",
    re.IGNORECASE,
)

# Éxito pone el contenido total al final, entre paréntesis: "(1000  gr)", "(6600  ml)", "(30  und)".
TOTAL_IN_PARENTHESES = re.compile(
    r"\((\d+(?:[.,]\d+)?)\s*(kg|gr|g|ml|l|und|unds)\)",
    re.IGNORECASE,
)

# Cantidad de unidades después de una "x": "x30", "x 6 und", "X30 Unds".
# No cuenta si lo que sigue es un peso o volumen ("x 500 g" es un tamaño, no 500 unidades).
COUNT_AFTER_X = re.compile(
    r"\bx\s*(\d+)(?![\d.,])(?!\s*(?:kilogramos?|kilos?|kg|gramos?|grs?|g|mililitros?|ml|cc|litros?|lts?|l)\b)",
    re.IGNORECASE,
)

# Cantidad de unidades escrita con la palabra: "6 unds", "2Und", "30 unidades".
COUNT_WITH_WORD = re.compile(r"(\d+)\s*(?:unidades|unds|und|un)\b", re.IGNORECASE)

# "c/u", "c-u" o "cada uno": el tamaño del nombre es de cada unidad del paquete.
EACH_UNIT_PATTERN = re.compile(r"\bc\s*[/-]\s*u\b|cada\s+un[oa]", re.IGNORECASE)

# Si dos cantidades difieren en menos de esto, se consideran iguales.
TOLERANCE = Decimal("0.05")


def parse_number(text: str) -> Decimal:
    """Convierte "1,1", "2.5" o "1.000" en Decimal.

    En Colombia la coma es decimal y el punto separa miles, pero algunas tiendas
    usan el punto como decimal ("x2.5kg"). Regla: si el punto va seguido de
    exactamente tres dígitos ("1.000"), es separador de miles.
    """
    if re.fullmatch(r"\d{1,3}\.\d{3}", text):
        return Decimal(text.replace(".", ""))
    return Decimal(text.replace(",", "."))


def build_presentation(number_text: str, unit_text: str) -> Presentation | None:
    """Arma una Presentation a partir de un número y una palabra de unidad."""
    unit_key = unit_text.lower()
    if unit_key not in UNIT_CONVERSIONS:
        return None
    base_unit, factor = UNIT_CONVERSIONS[unit_key]
    return Presentation(quantity=parse_number(number_text) * factor, base_unit=base_unit)


def find_pack_count(name: str) -> int | None:
    """Busca cuántas unidades trae un paquete: "x6 Und", "X30 Unds", "2Und"."""
    match = COUNT_AFTER_X.search(name)
    if match is None:
        match = COUNT_WITH_WORD.search(name)
    if match is None:
        return None
    return int(match.group(1))


def parse_presentation(name: str) -> Presentation | None:
    """Lee la presentación de un producto a partir de su nombre.

    Ejemplos:
        "Arroz Diana x 500 g"                        -> 500 g
        "Huevos AA x30"                               -> 30 und
        "Leche Entera Colanta Bolsa x6 Und x1000ml"   -> 6000 ml (6 bolsas de 1000 ml)
        "Panela 4 Und EL TREBOL 1000 gr"              -> 1000 g (4 piezas que suman 1 kg)
        "Arroz Diana blanco x25und x500g c-u"         -> 12500 g (25 bolsas de 500 g cada una)
        "Arroz DIANA blanco premium (1000  gr)"       -> 1000 g

    Regla para paquetes con conteo y tamaño ("x6 ... 1000 ml"), sacada de los datos reales:
    - en líquidos el tamaño es de cada bolsa o botella, así que se multiplica;
    - en pesos el tamaño ya es el total (panela en piezas, pacas de arroz), salvo que
      el nombre diga "c/u", "c-u" o "cada uno".

    Devuelve None si el nombre no trae ninguna cantidad reconocible.
    """
    # 1. Si el nombre trae el total entre paréntesis (estilo Éxito), ese es el dato.
    total_match = TOTAL_IN_PARENTHESES.search(name)
    if total_match is not None:
        return build_presentation(total_match.group(1), total_match.group(2))

    size_match = SIZE_PATTERN.search(name)
    pack_count = find_pack_count(name)

    # 2. Sin peso ni volumen: si hay conteo, el producto se vende por unidades (huevos x30).
    if size_match is None:
        if pack_count is None:
            return None
        return Presentation(quantity=Decimal(pack_count), base_unit="und")

    # 3. Con peso o volumen.
    presentation = build_presentation(size_match.group(1), size_match.group(2))
    if presentation is None:
        return None
    if pack_count is None or pack_count <= 1:
        return presentation

    size_is_per_unit = presentation.base_unit == "ml" or EACH_UNIT_PATTERN.search(name) is not None
    if size_is_per_unit:
        presentation.quantity = presentation.quantity * pack_count
    return presentation


def other_weight_reading(name: str, first_reading: Presentation) -> Presentation | None:
    """Devuelve la otra forma de leer un paquete de peso con conteo, o None si no aplica.

    "Panela x2un 750g" puede ser 750 g en total o 2 x 750 g. parse_presentation elige
    una; esta función da la otra, para contrastarla con los datos de la tienda.
    """
    if first_reading.base_unit != "g" or TOTAL_IN_PARENTHESES.search(name) is not None:
        return None
    size_match = SIZE_PATTERN.search(name)
    pack_count = find_pack_count(name)
    if size_match is None or pack_count is None or pack_count <= 1:
        return None
    size = build_presentation(size_match.group(1), size_match.group(2))
    if size is None:
        return None
    if first_reading.quantity == size.quantity:
        return Presentation(quantity=size.quantity * pack_count, base_unit="g")
    return size


def presentation_from_store_fields(quantity_text: str, unit_text: str) -> Presentation | None:
    """Arma una Presentation con los datos que trae la tienda, p. ej. ("6600", "Mililitro").

    Devuelve None si los datos no se entienden (vacíos, unidad desconocida, número inválido).
    """
    quantity_text = quantity_text.strip()
    unit_text = unit_text.strip()
    if not re.fullmatch(r"\d+(?:[.,]\d+)?", quantity_text):
        return None
    return build_presentation(quantity_text, unit_text)


def are_close(first: Decimal, second: Decimal) -> bool:
    """True si dos cantidades difieren en menos del 5 %."""
    if first == second:
        return True
    biggest = max(first, second)
    return abs(first - second) / biggest < TOLERANCE


def presentations_match(first: Presentation, second: Presentation) -> bool:
    """True si dos presentaciones tienen la misma unidad y casi la misma cantidad."""
    return first.base_unit == second.base_unit and are_close(first.quantity, second.quantity)


def choose_presentation(name: str, store_presentation: Presentation | None) -> tuple[Presentation | None, str]:
    """Decide la presentación final de un producto y devuelve (presentación, nota).

    Regla acordada: manda lo que dice el nombre, y los datos de la tienda sirven
    de contraste. La nota queda vacía cuando todo cuadra; si no, explica la duda
    para que llegue a los supuestos del reporte.
    """
    from_name = parse_presentation(name)

    if from_name is None and store_presentation is None:
        return None, "Sin presentación: no se puede calcular el precio por unidad."
    if from_name is None:
        return store_presentation, "Presentación tomada de los datos de la tienda (el nombre no la trae)."
    if store_presentation is None:
        return from_name, ""

    if presentations_match(from_name, store_presentation):
        return from_name, ""

    # En paquetes de peso con conteo el nombre se puede leer de dos formas;
    # si la otra lectura coincide con la tienda, esa es la correcta.
    other_reading = other_weight_reading(name, from_name)
    if other_reading is not None and presentations_match(other_reading, store_presentation):
        return other_reading, ""

    note = (
        f"Presentación dudosa: el nombre dice {format_quantity(from_name.quantity, from_name.base_unit)} "
        f"y la tienda {format_quantity(store_presentation.quantity, store_presentation.base_unit)}; "
        f"se usó la del nombre."
    )
    return from_name, note


# ---------------------------------------------------------------------------
# 3. Formatos para mostrar
# ---------------------------------------------------------------------------

def format_cop(amount: Decimal) -> str:
    """Formatea pesos colombianos sin decimales: Decimal("5616.4") -> "$5.616"."""
    rounded = amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    with_commas = f"{int(rounded):,}"          # "5,616"
    return "$" + with_commas.replace(",", ".")  # "$5.616"


def format_quantity(quantity: Decimal | None, base_unit: str | None) -> str:
    """Muestra una cantidad de forma legible: 1000 g -> "1 kg", 6600 ml -> "6,6 L", 30 und -> "30 und"."""
    if quantity is None or base_unit is None:
        return "sin presentación"
    if base_unit == "g" and quantity >= 1000:
        number, unit = quantity / 1000, "kg"
    elif base_unit == "ml" and quantity >= 1000:
        number, unit = quantity / 1000, "L"
    else:
        number, unit = quantity, base_unit
    text = f"{number.normalize():f}".replace(".", ",")
    return f"{text} {unit}"


def reference_unit_label(base_unit: str | None) -> str:
    """Unidad en la que se muestra el precio por unidad: kg, L o und."""
    if base_unit == "g":
        return "kg"
    if base_unit == "ml":
        return "L"
    return "und"


# ---------------------------------------------------------------------------
# 4. Cálculos de precios
# ---------------------------------------------------------------------------

def price_per_reference_unit(product: Product) -> Decimal | None:
    """Precio por kg (si se mide en g), por litro (si se mide en ml) o por unidad.

    Devuelve None si el producto no tiene presentación conocida.
    """
    if product.quantity is None or product.base_unit is None or product.quantity == 0:
        return None
    if product.base_unit == "und":
        return product.price / product.quantity
    return product.price / product.quantity * 1000


def line_cost(selection: Selection) -> Decimal:
    """Lo que se paga por un ítem en una tienda: precio del paquete x número de paquetes."""
    if selection.product is None:
        return Decimal("0")
    return selection.product.price * selection.units


def purchased_amount(selection: Selection) -> Decimal | None:
    """Cantidad total que se lleva el usuario: contenido del paquete x número de paquetes."""
    if selection.product is None or selection.product.quantity is None:
        return None
    return selection.product.quantity * selection.units


def find_cheapest_store(selections: dict[str, Selection]) -> tuple[str | None, str]:
    """Elige la tienda más barata para un ítem y dice cómo se comparó.

    - Si en todas las tiendas se compra la misma cantidad, gana el menor precio pagado.
    - Si las cantidades difieren pero se miden igual (g, ml o und), gana el menor
      precio por kg/L/und, porque comparar paquetes de distinto tamaño sería injusto.
    - Si no se puede saber la cantidad, se compara el precio pagado y se avisa.
    """
    found = []
    for store_id, selection in selections.items():
        if selection.product is not None:
            found.append((store_id, selection))
    if len(found) == 0:
        return None, ""
    if len(found) == 1:
        return found[0][0], "única tienda que lo tiene"

    amounts = [purchased_amount(selection) for _, selection in found]
    units = [selection.product.base_unit for _, selection in found]
    all_amounts_known = None not in amounts
    same_unit = len(set(units)) == 1

    all_same_amount = False
    if all_amounts_known and same_unit:
        all_same_amount = True
        for amount in amounts:
            if not are_close(amount, amounts[0]):
                all_same_amount = False

    if all_same_amount:
        cheapest = min(found, key=lambda pair: line_cost(pair[1]))
        return cheapest[0], "precio pagado (misma cantidad)"

    if all_amounts_known and same_unit:
        cheapest = min(found, key=lambda pair: price_per_reference_unit(pair[1].product))
        return cheapest[0], f"precio por {reference_unit_label(units[0])} (presentaciones distintas)"

    cheapest = min(found, key=lambda pair: line_cost(pair[1]))
    return cheapest[0], "precio pagado (presentaciones no comparables)"


def compare(
    items: list[str],
    stores: list[str],
    selections: list[Selection],
    delivery_costs: dict[str, Decimal],
    assumptions: list[str],
    failed_stores: dict[str, str],
    mode: str,
    city: str,
    consulted_at: str,
) -> ComparisonResult:
    """Calcula toda la comparación a partir de las selecciones del agente.

    Pasos:
    1. Arma la tabla ítem x tienda y marca la tienda más barata de cada ítem.
    2. Total por tienda: comprar toda la lista en una sola tienda (+ domicilio).
    3. Óptimo dividido: cada ítem en su tienda más barata (+ domicilio de cada tienda usada).
    4. Ahorro frente a la tienda más costosa, usando solo los ítems que tienen todas las
       tiendas, para no comparar totales con distinto número de productos.
    """
    active_stores = []
    for store_id in stores:
        if store_id not in failed_stores:
            active_stores.append(store_id)

    # Índice rápido: (ítem, tienda) -> selección
    selection_by_key = {}
    for selection in selections:
        selection_by_key[(selection.item, selection.store_id)] = selection

    # 1. Tabla ítem x tienda
    rows = []
    for item in items:
        row_selections = {}
        for store_id in active_stores:
            selection = selection_by_key.get((item, store_id))
            if selection is None:
                selection = Selection(item, store_id, None, 0, "El agente no registró una selección.")
            row_selections[store_id] = selection
        cheapest_store, compared_by = find_cheapest_store(row_selections)
        rows.append(ItemRow(item, row_selections, cheapest_store, compared_by))

    not_found = []
    for row in rows:
        if row.cheapest_store is None:
            not_found.append(row.item)

    # Ítems que encontraron todas las tiendas activas
    common_items = []
    for row in rows:
        found_everywhere = True
        for store_id in active_stores:
            if row.selections[store_id].product is None:
                found_everywhere = False
        if found_everywhere and len(active_stores) > 0:
            common_items.append(row.item)

    # 2. Total por tienda y total comparable (solo ítems comunes)
    store_totals = {}
    comparable_totals = {}
    for store_id in active_stores:
        subtotal = Decimal("0")
        comparable_subtotal = Decimal("0")
        found_items = []
        missing_items = []
        for row in rows:
            selection = row.selections[store_id]
            if selection.product is None:
                missing_items.append(row.item)
                continue
            found_items.append(row.item)
            subtotal += line_cost(selection)
            if row.item in common_items:
                comparable_subtotal += line_cost(selection)

        # El domicilio solo se paga si se compra algo en esa tienda.
        delivery = Decimal("0")
        if len(found_items) > 0:
            delivery = delivery_costs.get(store_id, Decimal("0"))
        store_totals[store_id] = StoreTotal(store_id, subtotal, delivery, subtotal + delivery, found_items, missing_items)

        comparable_delivery = Decimal("0")
        if len(common_items) > 0:
            comparable_delivery = delivery_costs.get(store_id, Decimal("0"))
        comparable_totals[store_id] = comparable_subtotal + comparable_delivery

    # 3. Óptimo dividido
    split_plan = {}
    split_subtotal = Decimal("0")
    split_comparable_subtotal = Decimal("0")
    stores_used = []
    stores_used_for_common = []
    for row in rows:
        if row.cheapest_store is None:
            continue
        split_plan[row.item] = row.cheapest_store
        cost = line_cost(row.selections[row.cheapest_store])
        split_subtotal += cost
        if row.cheapest_store not in stores_used:
            stores_used.append(row.cheapest_store)
        if row.item in common_items:
            split_comparable_subtotal += cost
            if row.cheapest_store not in stores_used_for_common:
                stores_used_for_common.append(row.cheapest_store)

    split_delivery = Decimal("0")
    for store_id in stores_used:
        split_delivery += delivery_costs.get(store_id, Decimal("0"))
    split_comparable_delivery = Decimal("0")
    for store_id in stores_used_for_common:
        split_comparable_delivery += delivery_costs.get(store_id, Decimal("0"))

    split_total = split_subtotal + split_delivery
    split_comparable_total = split_comparable_subtotal + split_comparable_delivery

    # 4. Ahorro (solo con ítems comunes, para comparar lo mismo contra lo mismo)
    cheapest_store = None
    most_expensive_store = None
    savings_single_store = Decimal("0")
    savings_split = Decimal("0")
    if len(common_items) > 0 and len(active_stores) > 1:
        cheapest_store = min(active_stores, key=lambda store_id: comparable_totals[store_id])
        most_expensive_store = max(active_stores, key=lambda store_id: comparable_totals[store_id])
        savings_single_store = comparable_totals[most_expensive_store] - comparable_totals[cheapest_store]
        savings_split = comparable_totals[most_expensive_store] - split_comparable_total

    savings_note = build_savings_note(items, active_stores, common_items, not_found)

    return ComparisonResult(
        city=city,
        mode=mode,
        stores=stores,
        consulted_at=consulted_at,
        rows=rows,
        store_totals=store_totals,
        common_items=common_items,
        comparable_totals=comparable_totals,
        split_plan=split_plan,
        split_subtotal=split_subtotal,
        split_delivery=split_delivery,
        split_total=split_total,
        split_comparable_total=split_comparable_total,
        cheapest_store=cheapest_store,
        most_expensive_store=most_expensive_store,
        savings_single_store=savings_single_store,
        savings_split=savings_split,
        savings_note=savings_note,
        assumptions=assumptions,
        not_found=not_found,
        failed_stores=failed_stores,
    )


def build_savings_note(items: list[str], active_stores: list[str], common_items: list[str], not_found: list[str]) -> str:
    """Explica sobre qué productos se calculó el ahorro, para no comparar totales distintos sin avisar."""
    if len(active_stores) < 2:
        return "Solo hay una tienda con resultados, así que no hay ahorro que calcular."
    if len(common_items) == 0:
        return "Ningún producto está en todas las tiendas, así que el ahorro no se puede calcular de forma justa."

    found_somewhere = len(items) - len(not_found)
    if len(common_items) == found_somewhere:
        return f"Todas las tiendas tienen los {len(common_items)} productos encontrados; la comparación es completa."

    left_out = []
    for item in items:
        if item not in common_items and item not in not_found:
            left_out.append(item)
    return (
        f"El ahorro se calcula solo con los {len(common_items)} productos que tienen todas las tiendas. "
        f"Quedan por fuera porque alguna tienda no los tiene: {', '.join(left_out)}."
    )
