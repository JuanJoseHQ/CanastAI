"""
Tests de products.py (normalización y cálculos) y del parseo de respuestas de stores.py.

Los productos de prueba salen de respuestas reales de las tiendas, guardadas en la
Fase 0 en docs/muestras_fase0.json (consultadas en Medellín el 2026-11-02).

Para correrlos: pytest
"""

import json
from decimal import Decimal
from pathlib import Path

from products import (
    Presentation,
    Product,
    Selection,
    choose_presentation,
    compare,
    describe_savings,
    find_cheapest_store,
    format_cop,
    format_quantity,
    parse_number,
    parse_presentation,
    price_per_reference_unit,
)
from stores import parse_product, parse_search_response

SAMPLES_PATH = Path(__file__).parent.parent / "docs" / "muestras_fase0.json"
DOMAINS = {
    "exito": "https://www.exito.com",
    "jumbo": "https://www.jumbocolombia.com",
    "olimpica": "https://www.olimpica.com",
}
STORE_NAMES = {"exito": "Éxito", "jumbo": "Jumbo", "olimpica": "Olímpica"}


def load_real_product(store_id: str, query: str, sku: str) -> dict:
    """Busca en la muestra real el producto crudo (JSON de la tienda) con ese SKU."""
    with open(SAMPLES_PATH, encoding="utf-8") as samples_file:
        samples = json.load(samples_file)
    for raw_product in samples["tiendas"][store_id][query]:
        if raw_product["items"][0]["itemId"] == sku:
            return raw_product
    raise ValueError(f"No está el SKU {sku} en la muestra de {store_id}/{query}")


def make_product(store_id: str, sku: str, price: str, quantity: str, base_unit: str) -> Product:
    """Crea un Product de prueba sin pasar por la tienda."""
    return Product(
        store_id=store_id,
        product_id=f"{store_id}:{sku}",
        name=f"Producto {sku}",
        brand="MARCA",
        quantity=Decimal(quantity),
        base_unit=base_unit,
        price=Decimal(price),
        list_price=Decimal(price),
        available=True,
    )


def run_compare(items: list[str], stores: list[str], selections: list[Selection], delivery_costs: dict | None = None):
    """Llama a compare() con valores fijos para lo que no importa en el test."""
    return compare(
        items=items,
        stores=stores,
        selections=selections,
        delivery_costs=delivery_costs or {},
        assumptions=[],
        failed_stores={},
        mode="estándar",
        city="Medellín",
        consulted_at="2026-11-02 20:00",
    )


# ---------------------------------------------------------------------------
# Normalización de presentaciones (casos pedidos)
# ---------------------------------------------------------------------------

def test_rice_diana_x_500_g():
    presentation = parse_presentation("Arroz Diana x 500 g")
    assert presentation == Presentation(Decimal("500"), "g")


def test_milk_1100_ml():
    presentation = parse_presentation("Leche 1100 ml")
    assert presentation == Presentation(Decimal("1100"), "ml")


def test_eggs_aa_x30():
    presentation = parse_presentation("Huevos AA x30")
    assert presentation == Presentation(Decimal("30"), "und")


def test_oil_3000_ml():
    presentation = parse_presentation("Aceite 3000 ml")
    assert presentation == Presentation(Decimal("3000"), "ml")


# ---------------------------------------------------------------------------
# Normalización de presentaciones (formatos reales de las tiendas)
# ---------------------------------------------------------------------------

def test_kilos_become_grams():
    presentation = parse_presentation("Arroz Roa x1kg")
    assert presentation == Presentation(Decimal("1000"), "g")


def test_liters_with_decimal_comma():
    presentation = parse_presentation("Aceite Medalla de Oro Mezcla 3 Lt")
    assert presentation == Presentation(Decimal("3000"), "ml")


def test_exito_total_in_parentheses():
    presentation = parse_presentation("Leche deslactosada ALPINA bolsa x6und 1100ml (6600  ml)")
    assert presentation == Presentation(Decimal("6600"), "ml")


def test_milk_pack_is_multiplied():
    presentation = parse_presentation("Leche Colanta Uht Entera 1,1 Lt X6 Unds")
    assert presentation == Presentation(Decimal("6600"), "ml")


def test_milk_pack_with_count_first():
    presentation = parse_presentation("Leche Entera Colanta Bolsa x6 Und x1000ml")
    assert presentation == Presentation(Decimal("6000"), "ml")


def test_panela_pieces_are_not_multiplied():
    # 4 piezas que suman 1000 g, no 4 kg.
    presentation = parse_presentation("Panela 4 Und EL TREBOL 1000 gr")
    assert presentation == Presentation(Decimal("1000"), "g")


def test_rice_bale_is_not_multiplied():
    presentation = parse_presentation("Arroz Diana Paca 12,5 Kg X25 Unds")
    assert presentation == Presentation(Decimal("12500"), "g")


def test_each_unit_is_multiplied():
    presentation = parse_presentation("Arroz Diana blanco x25und x500g c-u")
    assert presentation == Presentation(Decimal("12500"), "g")


def test_name_without_presentation():
    assert parse_presentation("Papa Criolla Granel") is None


def test_zero_quantity_is_ignored():
    assert parse_presentation("Producto de prueba 0 g") is None


def test_dot_as_thousands_separator():
    assert parse_number("1.000") == Decimal("1000")
    assert parse_number("2.5") == Decimal("2.5")
    assert parse_number("1,1") == Decimal("1.1")


# ---------------------------------------------------------------------------
# Contraste entre el nombre y los datos de la tienda
# ---------------------------------------------------------------------------

def test_name_and_store_agree_without_note():
    presentation, note = choose_presentation("Arroz Diana 3 Kg", Presentation(Decimal("3000"), "g"))
    assert presentation == Presentation(Decimal("3000"), "g")
    assert note == ""


def test_wrong_store_data_is_marked_as_doubtful():
    # Olímpica dice 1100 ml para un paquete de 6 bolsas de 1,1 L.
    presentation, note = choose_presentation("Leche Alquería Original 1,1 Lt X6 Unds", Presentation(Decimal("1100"), "ml"))
    assert presentation == Presentation(Decimal("6600"), "ml")
    assert "dudosa" in note


def test_store_resolves_ambiguous_weight_pack():
    # "x2un 750g": la tienda dice 1500 g, así que son 2 x 750 g.
    presentation, note = choose_presentation("Panela Medalla Oro x2un 750g", Presentation(Decimal("1500"), "g"))
    assert presentation == Presentation(Decimal("1500"), "g")
    assert note == ""


def test_store_data_used_when_name_has_no_presentation():
    presentation, note = choose_presentation("Spaghetti N°5 MONTICELLO", Presentation(Decimal("500"), "g"))
    assert presentation == Presentation(Decimal("500"), "g")
    assert "datos de la tienda" in note


# ---------------------------------------------------------------------------
# Parseo de respuestas reales (stores.py)
# ---------------------------------------------------------------------------

def test_exito_real_rice():
    raw_product = load_real_product("exito", "arroz", "730954")
    product = parse_product("exito", raw_product, DOMAINS["exito"])
    assert product.product_id == "exito:730954"
    assert product.brand == "DIANA"
    assert product.price == Decimal("5616")
    assert product.list_price == Decimal("6240")
    assert product.quantity == Decimal("1000")
    assert product.base_unit == "g"
    assert product.presentation_note == ""


def test_jumbo_real_rice_from_name_only():
    raw_product = load_real_product("jumbo", "arroz", "13412")
    product = parse_product("jumbo", raw_product, DOMAINS["jumbo"])
    assert product.name == "Arroz Diana Premium blanco x4000g"
    assert product.price == Decimal("24740")
    assert product.quantity == Decimal("4000")
    assert product.url.startswith("https://www.jumbocolombia.com/")


def test_olimpica_inverted_fields_are_fixed():
    # Olímpica trae measurement_unit="3000" y unit_multiplier="ml" (al revés).
    raw_product = load_real_product("olimpica", "aceite", "1646719")
    product = parse_product("olimpica", raw_product, DOMAINS["olimpica"])
    assert product.quantity == Decimal("3000")
    assert product.base_unit == "ml"
    assert product.presentation_note == ""


def test_olimpica_alqueria_milk_is_doubtful():
    raw_product = load_real_product("olimpica", "leche", "2312885")
    product = parse_product("olimpica", raw_product, DOMAINS["olimpica"])
    assert product.quantity == Decimal("6600")
    assert "dudosa" in product.presentation_note


def test_marketplace_product_is_discarded():
    # El seller "1" (la tienda) viene con precio 0; el precio real es de un tercero.
    raw_product = load_real_product("jumbo", "huevos", "20057330")
    assert parse_product("jumbo", raw_product, DOMAINS["jumbo"]) is None


def test_broken_product_does_not_break_the_search():
    good_product = load_real_product("exito", "arroz", "730954")
    broken_product = {"productName": None, "items": [{"itemId": "1", "sellers": [{"sellerId": "1", "commertialOffer": {"Price": 100}}]}]}
    products = parse_search_response("exito", {"products": [broken_product, good_product]}, DOMAINS["exito"])
    assert len(products) == 1
    assert products[0].product_id == "exito:730954"


# ---------------------------------------------------------------------------
# Formatos y precio por unidad
# ---------------------------------------------------------------------------

def test_colombian_pesos_format():
    assert format_cop(Decimal("5616")) == "$5.616"
    assert format_cop(Decimal("1234567.5")) == "$1.234.568"


def test_quantity_format():
    assert format_quantity(Decimal("1000"), "g") == "1 kg"
    assert format_quantity(Decimal("6600"), "ml") == "6,6 L"
    assert format_quantity(Decimal("30"), "und") == "30 und"


def test_price_per_kg():
    product = make_product("exito", "1", "5616", "1000", "g")
    assert price_per_reference_unit(product) == Decimal("5616")


def test_price_per_egg():
    product = make_product("exito", "1", "18000", "30", "und")
    assert price_per_reference_unit(product) == Decimal("600")


# ---------------------------------------------------------------------------
# Tienda más barata por ítem
# ---------------------------------------------------------------------------

def test_cheapest_with_same_presentation():
    selections = {
        "exito": Selection("Arroz 1 kg", "exito", make_product("exito", "1", "5616", "1000", "g"), 1, ""),
        "jumbo": Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "3712", "1000", "g"), 1, ""),
    }
    store_id, compared_by, same_amount = find_cheapest_store(selections)
    assert store_id == "jumbo"
    assert same_amount is True
    assert "misma cantidad" in compared_by


def test_cheapest_per_kg_when_presentations_differ():
    # Jumbo es más barato por paquete, pero Éxito es más barato por kg.
    selections = {
        "exito": Selection("Arroz", "exito", make_product("exito", "1", "16300", "5000", "g"), 1, ""),
        "jumbo": Selection("Arroz", "jumbo", make_product("jumbo", "2", "3712", "1000", "g"), 1, ""),
    }
    store_id, compared_by, same_amount = find_cheapest_store(selections)
    assert store_id == "exito"
    assert same_amount is False
    assert "precio por kg" in compared_by


def test_small_difference_in_amount_uses_price_per_kg():
    # 960 g a $4.800 ($5.000/kg) contra 1000 g a $4.900 ($4.900/kg): gana el de 1000 g.
    selections = {
        "exito": Selection("Arroz", "exito", make_product("exito", "1", "4800", "960", "g"), 1, ""),
        "jumbo": Selection("Arroz", "jumbo", make_product("jumbo", "2", "4900", "1000", "g"), 1, ""),
    }
    store_id, _, same_amount = find_cheapest_store(selections)
    assert store_id == "jumbo"
    assert same_amount is False


def test_two_packs_of_500_equal_one_of_1000():
    selections = {
        "exito": Selection("Arroz 1 kg", "exito", make_product("exito", "1", "2000", "500", "g"), 2, ""),
        "jumbo": Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "4500", "1000", "g"), 1, ""),
    }
    store_id, _, same_amount = find_cheapest_store(selections)
    assert store_id == "exito"
    assert same_amount is True


# ---------------------------------------------------------------------------
# Totales, óptimo dividido y ahorro
# ---------------------------------------------------------------------------

def build_example_comparison(delivery_costs: dict):
    """Dos ítems y tres tiendas; Olímpica no tiene el segundo ítem."""
    selections = [
        Selection("Arroz 1 kg", "exito", make_product("exito", "1", "5000", "1000", "g"), 1, ""),
        Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "4000", "1000", "g"), 1, ""),
        Selection("Arroz 1 kg", "olimpica", make_product("olimpica", "3", "6000", "1000", "g"), 1, ""),
        Selection("2 Panela", "exito", make_product("exito", "4", "3000", "1000", "g"), 2, ""),
        Selection("2 Panela", "jumbo", make_product("jumbo", "5", "3500", "1000", "g"), 2, ""),
        Selection("2 Panela", "olimpica", None, 0, "No la encontré"),
    ]
    return run_compare(["Arroz 1 kg", "2 Panela"], ["exito", "jumbo", "olimpica"], selections, delivery_costs)


def test_store_total_multiplies_packs():
    result = build_example_comparison({})
    assert result.store_totals["exito"].total == Decimal("11000")   # 5000 + 2 x 3000
    assert result.store_totals["jumbo"].total == Decimal("11000")   # 4000 + 2 x 3500
    assert result.store_totals["olimpica"].total == Decimal("6000")
    assert result.store_totals["olimpica"].missing_items == ["2 Panela"]


def test_split_optimum():
    result = build_example_comparison({})
    assert result.split_plan == {"Arroz 1 kg": "jumbo", "2 Panela": "exito"}
    assert result.split_total == Decimal("10000")   # 4000 + 2 x 3000


def test_savings_only_with_common_items():
    result = build_example_comparison({})
    # Solo el arroz está en las tres tiendas: Olímpica 6000 vs Jumbo 4000.
    assert result.common_items == ["Arroz 1 kg"]
    assert result.comparable_totals == {"exito": Decimal("5000"), "jumbo": Decimal("4000"), "olimpica": Decimal("6000")}
    assert result.most_expensive_store == "olimpica"
    assert result.cheapest_store == "jumbo"
    assert result.savings_single_store == Decimal("2000")
    assert "2 Panela" in result.savings_note


def test_savings_text_shows_the_comparable_totals():
    result = build_example_comparison({})
    first_line = describe_savings(result, STORE_NAMES)[0]
    assert "Jumbo suma $4.000" in first_line
    assert "Olímpica $6.000" in first_line
    assert "ahorras $2.000" in first_line


def test_delivery_is_added_to_both_scenarios():
    result = build_example_comparison({"exito": Decimal("5000"), "jumbo": Decimal("3000")})
    assert result.store_totals["exito"].total == Decimal("16000")
    assert result.store_totals["jumbo"].total == Decimal("14000")
    # El óptimo dividido compra en Jumbo y en Éxito: paga los dos domicilios.
    assert result.split_delivery == Decimal("8000")
    assert result.split_total == Decimal("18000")


def test_different_amounts_stay_out_of_the_savings():
    # Éxito vende 5 kg y Jumbo 1 kg: no se puede decir que uno "ahorra" frente al otro.
    selections = [
        Selection("Arroz", "exito", make_product("exito", "1", "16300", "5000", "g"), 1, ""),
        Selection("Arroz", "jumbo", make_product("jumbo", "2", "3712", "1000", "g"), 1, ""),
        Selection("Azúcar", "exito", make_product("exito", "3", "3390", "1000", "g"), 1, ""),
        Selection("Azúcar", "jumbo", make_product("jumbo", "4", "4510", "1000", "g"), 1, ""),
    ]
    result = run_compare(["Arroz", "Azúcar"], ["exito", "jumbo"], selections)
    assert result.common_items == ["Azúcar"]
    assert result.savings_single_store == Decimal("1120")
    assert "cantidades distintas" in result.savings_note
    assert "Arroz" in result.savings_note


def test_split_more_expensive_because_of_delivery_is_not_called_savings():
    selections = [
        Selection("A", "exito", make_product("exito", "1", "1000", "1", "und"), 1, ""),
        Selection("A", "jumbo", make_product("jumbo", "2", "900", "1", "und"), 1, ""),
        Selection("B", "exito", make_product("exito", "3", "500", "1", "und"), 1, ""),
        Selection("B", "jumbo", make_product("jumbo", "4", "600", "1", "und"), 1, ""),
    ]
    delivery = {"exito": Decimal("5000"), "jumbo": Decimal("5000")}
    result = run_compare(["A", "B"], ["exito", "jumbo"], selections, delivery)
    assert result.savings_split < 0
    lines = describe_savings(result, STORE_NAMES)
    assert "no mejora" in lines[1]
    assert "$-" not in " ".join(lines)


def test_all_stores_cost_the_same():
    selections = [
        Selection("A", "exito", make_product("exito", "1", "1000", "1", "und"), 1, ""),
        Selection("A", "jumbo", make_product("jumbo", "2", "1000", "1", "und"), 1, ""),
    ]
    result = run_compare(["A"], ["exito", "jumbo"], selections)
    assert result.most_expensive_store is None
    assert "cuestan lo mismo" in describe_savings(result, STORE_NAMES)[0]


def test_store_without_products_does_not_cancel_the_savings():
    selections = [
        Selection("A", "exito", make_product("exito", "1", "1000", "1", "und"), 1, ""),
        Selection("A", "jumbo", make_product("jumbo", "2", "900", "1", "und"), 1, ""),
        Selection("A", "olimpica", None, 0, "No está"),
    ]
    result = run_compare(["A"], ["exito", "jumbo", "olimpica"], selections)
    assert result.stores_without_products == ["olimpica"]
    assert result.cheapest_store == "jumbo"
    assert result.savings_single_store == Decimal("100")


def test_item_that_no_store_has():
    selections = [
        Selection("Caviar", "exito", None, 0, "No existe"),
        Selection("Caviar", "jumbo", None, 0, "No existe"),
    ]
    result = run_compare(["Caviar"], ["exito", "jumbo"], selections)
    assert result.not_found == ["Caviar"]
    assert result.split_total == Decimal("0")


def test_failed_store_is_left_out():
    selections = [
        Selection("Arroz 1 kg", "exito", make_product("exito", "1", "5000", "1000", "g"), 1, ""),
        Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "4000", "1000", "g"), 1, ""),
    ]
    result = compare(
        ["Arroz 1 kg"], ["exito", "jumbo", "olimpica"], selections, {}, [],
        {"olimpica": "timeout de 15 s"}, "estándar", "Medellín", "",
    )
    assert "olimpica" not in result.store_totals
    assert result.cheapest_store == "jumbo"
    assert result.savings_single_store == Decimal("1000")


# ---------------------------------------------------------------------------
# Cambios del usuario después de la comparación
# ---------------------------------------------------------------------------

def test_units_to_cover_uses_previous_amount():
    """Si antes se llevaba 1 kg y el nuevo paquete es de 500 g, hacen falta 2."""
    from products import units_to_cover
    previous = Selection("Arroz 1 kg", "exito", make_product("exito", "1", "4800", "1000", "g"), 1, "")
    assert units_to_cover("Arroz 1 kg", previous, make_product("exito", "2", "2500", "500", "g")) == 2
    # 1100 ml contra un paquete de 1000 ml: se redondea a 1, no a 2.
    previous = Selection("Leche", "exito", make_product("exito", "3", "5000", "1100", "ml"), 1, "")
    assert units_to_cover("Leche", previous, make_product("exito", "4", "4500", "1000", "ml")) == 1


def test_units_to_cover_falls_back_to_item_text_or_previous_units():
    """Sin producto anterior usa la presentación de la línea; con unidades distintas repite los paquetes."""
    from products import units_to_cover
    not_available = Selection("Azúcar 1 kg", "jumbo", None, 0, "")
    assert units_to_cover("Azúcar 1 kg", not_available, make_product("jumbo", "1", "2000", "500", "g")) == 2
    previous = Selection("2 Panela", "jumbo", make_product("jumbo", "2", "2700", "500", "g"), 2, "")
    assert units_to_cover("2 Panela", previous, make_product("jumbo", "3", "900", "4", "und")) == 2


def test_replace_selection_recalculates_without_touching_other_items():
    """Cambiar el producto de una tienda recalcula totales, más barato y ahorro."""
    from products import replace_selection
    items = ["Arroz 1 kg", "Leche 1 L"]
    rice_exito = make_product("exito", "1", "5000", "1000", "g")
    rice_jumbo = make_product("jumbo", "1", "4000", "1000", "g")
    milk_exito = make_product("exito", "2", "3000", "1000", "ml")
    milk_jumbo = make_product("jumbo", "2", "3500", "1000", "ml")
    selections = [
        Selection("Arroz 1 kg", "exito", rice_exito, 1, ""),
        Selection("Arroz 1 kg", "jumbo", rice_jumbo, 1, ""),
        Selection("Leche 1 L", "exito", milk_exito, 1, ""),
        Selection("Leche 1 L", "jumbo", milk_jumbo, 1, ""),
    ]
    result = run_compare(items, ["exito", "jumbo"], selections, {"exito": Decimal("1000")})
    result.recommendation_text = "Texto del agente"
    assert result.cheapest_store == "jumbo"   # Éxito 9.000 (con domicilio) vs Jumbo 7.500

    cheaper_rice = make_product("exito", "9", "2000", "500", "g")
    changed = replace_selection(result, "Arroz 1 kg", "exito", cheaper_rice, 2, "cambio")

    assert changed.store_totals["exito"].total == Decimal("8000")   # 2x2000 + 3000 + 1000 de domicilio
    assert changed.store_totals["jumbo"].total == Decimal("7500")
    assert changed.rows[1].selections["exito"].product is milk_exito
    assert changed.user_changes == {("Arroz 1 kg", "exito"): "cambio"}
    assert changed.recommendation_text == "Texto del agente"
    assert result.user_changes == {}   # el original no se modifica, para poder deshacer

    removed = replace_selection(changed, "Leche 1 L", "jumbo", None, 0, "sin leche")
    assert removed.rows[1].cheapest_store == "exito"
    assert "Leche 1 L" not in removed.common_items
    assert len(removed.user_changes) == 2
