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
    find_cheapest_store,
    format_cop,
    format_quantity,
    parse_number,
    parse_presentation,
    price_per_reference_unit,
)
from stores import parse_product

SAMPLES_PATH = Path(__file__).parent.parent / "docs" / "muestras_fase0.json"
DOMAINS = {
    "exito": "https://www.exito.com",
    "jumbo": "https://www.jumbocolombia.com",
    "olimpica": "https://www.olimpica.com",
}


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


# ---------------------------------------------------------------------------
# Normalización de presentaciones (casos pedidos)
# ---------------------------------------------------------------------------

def test_arroz_diana_x_500_g():
    presentation = parse_presentation("Arroz Diana x 500 g")
    assert presentation == Presentation(Decimal("500"), "g")


def test_leche_1100_ml():
    presentation = parse_presentation("Leche 1100 ml")
    assert presentation == Presentation(Decimal("1100"), "ml")


def test_huevos_aa_x30():
    presentation = parse_presentation("Huevos AA x30")
    assert presentation == Presentation(Decimal("30"), "und")


def test_aceite_3000_ml():
    presentation = parse_presentation("Aceite 3000 ml")
    assert presentation == Presentation(Decimal("3000"), "ml")


# ---------------------------------------------------------------------------
# Normalización de presentaciones (formatos reales de las tiendas)
# ---------------------------------------------------------------------------

def test_kilos_se_convierten_a_gramos():
    presentation = parse_presentation("Arroz Roa x1kg")
    assert presentation == Presentation(Decimal("1000"), "g")


def test_litros_con_coma_decimal():
    presentation = parse_presentation("Aceite Medalla de Oro Mezcla 3 Lt")
    assert presentation == Presentation(Decimal("3000"), "ml")


def test_exito_total_entre_parentesis():
    presentation = parse_presentation("Leche deslactosada ALPINA bolsa x6und 1100ml (6600  ml)")
    assert presentation == Presentation(Decimal("6600"), "ml")


def test_paquete_de_leche_se_multiplica():
    presentation = parse_presentation("Leche Colanta Uht Entera 1,1 Lt X6 Unds")
    assert presentation == Presentation(Decimal("6600"), "ml")


def test_paquete_de_leche_con_conteo_antes():
    presentation = parse_presentation("Leche Entera Colanta Bolsa x6 Und x1000ml")
    assert presentation == Presentation(Decimal("6000"), "ml")


def test_panela_en_piezas_no_se_multiplica():
    # 4 piezas que suman 1000 g, no 4 kg.
    presentation = parse_presentation("Panela 4 Und EL TREBOL 1000 gr")
    assert presentation == Presentation(Decimal("1000"), "g")


def test_paca_de_arroz_no_se_multiplica():
    presentation = parse_presentation("Arroz Diana Paca 12,5 Kg X25 Unds")
    assert presentation == Presentation(Decimal("12500"), "g")


def test_cada_uno_si_se_multiplica():
    presentation = parse_presentation("Arroz Diana blanco x25und x500g c-u")
    assert presentation == Presentation(Decimal("12500"), "g")


def test_nombre_sin_presentacion():
    assert parse_presentation("Papa Criolla Granel") is None


def test_punto_como_separador_de_miles():
    assert parse_number("1.000") == Decimal("1000")
    assert parse_number("2.5") == Decimal("2.5")
    assert parse_number("1,1") == Decimal("1.1")


# ---------------------------------------------------------------------------
# Contraste entre el nombre y los datos de la tienda
# ---------------------------------------------------------------------------

def test_nombre_y_tienda_coinciden_sin_nota():
    presentation, note = choose_presentation("Arroz Diana 3 Kg", Presentation(Decimal("3000"), "g"))
    assert presentation == Presentation(Decimal("3000"), "g")
    assert note == ""


def test_dato_errado_de_la_tienda_queda_como_dudoso():
    # Olímpica dice 1100 ml para un paquete de 6 bolsas de 1,1 L.
    presentation, note = choose_presentation("Leche Alquería Original 1,1 Lt X6 Unds", Presentation(Decimal("1100"), "ml"))
    assert presentation == Presentation(Decimal("6600"), "ml")
    assert "dudosa" in note


def test_la_tienda_resuelve_un_paquete_de_peso_ambiguo():
    # "x2un 750g": la tienda dice 1500 g, así que son 2 x 750 g.
    presentation, note = choose_presentation("Panela Medalla Oro x2un 750g", Presentation(Decimal("1500"), "g"))
    assert presentation == Presentation(Decimal("1500"), "g")
    assert note == ""


def test_si_el_nombre_no_trae_presentacion_se_usa_la_tienda():
    presentation, note = choose_presentation("Spaghetti N°5 MONTICELLO", Presentation(Decimal("500"), "g"))
    assert presentation == Presentation(Decimal("500"), "g")
    assert "datos de la tienda" in note


# ---------------------------------------------------------------------------
# Parseo de respuestas reales (stores.py)
# ---------------------------------------------------------------------------

def test_exito_arroz_diana_real():
    raw_product = load_real_product("exito", "arroz", "730954")
    product = parse_product("exito", raw_product, DOMAINS["exito"])
    assert product.product_id == "exito:730954"
    assert product.brand == "DIANA"
    assert product.price == Decimal("5616")
    assert product.list_price == Decimal("6240")
    assert product.quantity == Decimal("1000")
    assert product.base_unit == "g"
    assert product.presentation_note == ""


def test_jumbo_arroz_real_solo_con_el_nombre():
    raw_product = load_real_product("jumbo", "arroz", "13412")
    product = parse_product("jumbo", raw_product, DOMAINS["jumbo"])
    assert product.name == "Arroz Diana Premium blanco x4000g"
    assert product.price == Decimal("24740")
    assert product.quantity == Decimal("4000")
    assert product.url.startswith("https://www.jumbocolombia.com/")


def test_olimpica_datos_invertidos_se_corrigen():
    # Olímpica trae measurement_unit="3000" y unit_multiplier="ml" (al revés).
    raw_product = load_real_product("olimpica", "aceite", "1646719")
    product = parse_product("olimpica", raw_product, DOMAINS["olimpica"])
    assert product.quantity == Decimal("3000")
    assert product.base_unit == "ml"
    assert product.presentation_note == ""


def test_olimpica_leche_alqueria_marcada_como_dudosa():
    raw_product = load_real_product("olimpica", "leche", "2312885")
    product = parse_product("olimpica", raw_product, DOMAINS["olimpica"])
    assert product.quantity == Decimal("6600")
    assert "dudosa" in product.presentation_note


def test_producto_de_marketplace_se_descarta():
    # El seller "1" (la tienda) viene con precio 0; el precio real es de un tercero.
    raw_product = load_real_product("jumbo", "huevos", "20057330")
    assert parse_product("jumbo", raw_product, DOMAINS["jumbo"]) is None


# ---------------------------------------------------------------------------
# Formatos y precio por unidad
# ---------------------------------------------------------------------------

def test_formato_pesos_colombianos():
    assert format_cop(Decimal("5616")) == "$5.616"
    assert format_cop(Decimal("1234567.5")) == "$1.234.568"


def test_formato_cantidad():
    assert format_quantity(Decimal("1000"), "g") == "1 kg"
    assert format_quantity(Decimal("6600"), "ml") == "6,6 L"
    assert format_quantity(Decimal("30"), "und") == "30 und"


def test_precio_por_kg():
    product = make_product("exito", "1", "5616", "1000", "g")
    assert price_per_reference_unit(product) == Decimal("5616")


def test_precio_por_unidad_de_huevo():
    product = make_product("exito", "1", "18000", "30", "und")
    assert price_per_reference_unit(product) == Decimal("600")


# ---------------------------------------------------------------------------
# Cálculos de comparación
# ---------------------------------------------------------------------------

def test_mas_barato_con_la_misma_presentacion():
    selections = {
        "exito": Selection("Arroz 1 kg", "exito", make_product("exito", "1", "5616", "1000", "g"), 1, ""),
        "jumbo": Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "3712", "1000", "g"), 1, ""),
    }
    store_id, compared_by = find_cheapest_store(selections)
    assert store_id == "jumbo"
    assert "misma cantidad" in compared_by


def test_mas_barato_por_kg_si_las_presentaciones_difieren():
    # Jumbo es más barato por paquete, pero Éxito es más barato por kg.
    selections = {
        "exito": Selection("Arroz", "exito", make_product("exito", "1", "16300", "5000", "g"), 1, ""),
        "jumbo": Selection("Arroz", "jumbo", make_product("jumbo", "2", "3712", "1000", "g"), 1, ""),
    }
    store_id, compared_by = find_cheapest_store(selections)
    assert store_id == "exito"
    assert "precio por kg" in compared_by


def build_example_comparison(delivery_costs: dict) -> object:
    """Dos ítems y tres tiendas; Olímpica no tiene el segundo ítem."""
    selections = [
        Selection("Arroz 1 kg", "exito", make_product("exito", "1", "5000", "1000", "g"), 1, ""),
        Selection("Arroz 1 kg", "jumbo", make_product("jumbo", "2", "4000", "1000", "g"), 1, ""),
        Selection("Arroz 1 kg", "olimpica", make_product("olimpica", "3", "6000", "1000", "g"), 1, ""),
        Selection("2 Panela", "exito", make_product("exito", "4", "3000", "1000", "g"), 2, ""),
        Selection("2 Panela", "jumbo", make_product("jumbo", "5", "3500", "1000", "g"), 2, ""),
        Selection("2 Panela", "olimpica", None, 0, "No la encontré"),
    ]
    return compare(
        items=["Arroz 1 kg", "2 Panela"],
        stores=["exito", "jumbo", "olimpica"],
        selections=selections,
        delivery_costs=delivery_costs,
        assumptions=[],
        failed_stores={},
        mode="estándar",
        city="Medellín",
        consulted_at="2026-11-02 20:00",
    )


def test_total_por_tienda_multiplica_paquetes():
    result = build_example_comparison({})
    assert result.store_totals["exito"].total == Decimal("11000")   # 5000 + 2 x 3000
    assert result.store_totals["jumbo"].total == Decimal("11000")   # 4000 + 2 x 3500
    assert result.store_totals["olimpica"].total == Decimal("6000")
    assert result.store_totals["olimpica"].missing_items == ["2 Panela"]


def test_optimo_dividido():
    result = build_example_comparison({})
    assert result.split_plan == {"Arroz 1 kg": "jumbo", "2 Panela": "exito"}
    assert result.split_total == Decimal("10000")   # 4000 + 2 x 3000


def test_ahorro_solo_con_items_comunes():
    result = build_example_comparison({})
    # Solo el arroz está en las tres tiendas: Olímpica 6000 vs Jumbo 4000.
    assert result.common_items == ["Arroz 1 kg"]
    assert result.most_expensive_store == "olimpica"
    assert result.cheapest_store == "jumbo"
    assert result.savings_single_store == Decimal("2000")
    assert "2 Panela" in result.savings_note


def test_domicilio_se_suma_a_ambos_escenarios():
    result = build_example_comparison({"exito": Decimal("5000"), "jumbo": Decimal("3000")})
    assert result.store_totals["exito"].total == Decimal("16000")
    assert result.store_totals["jumbo"].total == Decimal("14000")
    # El óptimo dividido compra en Jumbo y en Éxito: paga los dos domicilios.
    assert result.split_delivery == Decimal("8000")
    assert result.split_total == Decimal("18000")


def test_item_que_no_tiene_ninguna_tienda():
    selections = [
        Selection("Caviar", "exito", None, 0, "No existe"),
        Selection("Caviar", "jumbo", None, 0, "No existe"),
    ]
    result = compare(["Caviar"], ["exito", "jumbo"], selections, {}, [], {}, "estándar", "Medellín", "")
    assert result.not_found == ["Caviar"]
    assert result.split_total == Decimal("0")


def test_tienda_caida_no_entra_en_la_comparacion():
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
