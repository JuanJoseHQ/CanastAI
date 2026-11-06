"""
app.py: interfaz web de CanastAI, hecha con Streamlit.

Correr con:  streamlit run app.py

Flujo de la pantalla:
1. El usuario escribe la lista (o carga la de ejemplo), elige tiendas, modo y domicilios.
2. Al presionar "Comparar precios" se llama a run_agent() de agent.py, y cada paso del
   agente (búsquedas, reintentos, elecciones) se muestra en vivo.
3. Con el ComparisonResult se muestran la recomendación, los totales, la tabla de
   precios, el plan por tienda, los supuestos y el botón para descargar el PDF.

Diseño: colores y letra de la propuesta (verde #2E7D32, azul de enlaces #0563C1, Arial).
El tema base está en .streamlit/config.toml; aquí solo van las clases de las tarjetas
y de la tabla, que se pintan con st.html.
"""

import html
import logging
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

import products
from agent import MODE_BRAND, MODE_STANDARD, AgentError, parse_shopping_list, run_agent
from report_pdf import build_pdf
from stores import load_config

TEST_LIST_PATH = Path(__file__).parent / "data" / "lista_prueba.txt"
EVENT_ICONS = {
    "busqueda": "🔎",
    "resultado": "📦",
    "seleccion": "✅",
    "error": "⚠️",
    "agente": "💬",
    "finalizado": "🧮",
}
MODE_LABELS = {
    MODE_STANDARD: "Lo más barato, de cualquier marca",
    MODE_BRAND: "Respetar marca y presentación",
}
MODE_CAPTIONS = {
    MODE_STANDARD: "Busca el equivalente más económico (arroz de 1 kg, leche de 1 L…).",
    MODE_BRAND: "Usa la marca y el tamaño que escribiste en la lista.",
}
LIST_PLACEHOLDER = "Arroz 1 kg\nLeche entera 1100 ml\nHuevos AA x30\n2 Panela\nCafé molido 500 g"

# Colores de la propuesta. Si se cambian, cambiar también .streamlit/config.toml.
STYLES = """
<style>
:root {
  --ca-green: #2E7D32;
  --ca-green-dark: #1B5E20;
  --ca-green-soft: #E8F5E9;
  --ca-green-wash: #F3F7F3;
  --ca-blue: #0563C1;
  --ca-text: #1B1B1B;
  --ca-muted: #5F6B5F;
  --ca-border: #D9DED9;
  --ca-warn: #A85A00;
}
.ca-header { padding: .25rem 0 .5rem; }
.ca-header h1 {
  font-family: Arial, Helvetica, sans-serif; font-size: 2.75rem; font-weight: 700;
  color: var(--ca-green); margin: 0; padding: 0; line-height: 1.1;
}
.ca-header p { font-size: 1.1rem; color: var(--ca-text); margin: .4rem 0 .6rem; max-width: 46rem; }
.ca-chip {
  display: inline-block; background: var(--ca-green-soft); color: var(--ca-green-dark);
  border-radius: 999px; padding: .2rem .75rem; font-size: .8rem; font-weight: 700; margin-right: .35rem;
}
.ca-step { display: flex; align-items: center; gap: .55rem; margin: 0 0 .15rem; }
.ca-step-num {
  display: inline-flex; align-items: center; justify-content: center; flex: none;
  width: 1.7rem; height: 1.7rem; border-radius: 50%;
  background: var(--ca-green); color: #fff; font-weight: 700; font-size: .9rem;
}
.ca-step-title { font-size: 1.1rem; font-weight: 700; color: var(--ca-text); }
.ca-section { font-size: 1.35rem; font-weight: 700; color: var(--ca-green); margin: .75rem 0 .1rem; }

/* Tarjeta de la recomendación */
.ca-hero {
  background: var(--ca-green); color: #fff; border-radius: .9rem; padding: 1.4rem 1.6rem;
}
.ca-hero.neutral { background: var(--ca-green-wash); color: var(--ca-text); border: 1px solid var(--ca-border); }
.ca-hero-label { font-size: .8rem; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; opacity: .85; }
.ca-hero-title { font-size: 1.9rem; font-weight: 700; margin: .25rem 0 .6rem; line-height: 1.2; }
.ca-hero-figures { display: flex; flex-wrap: wrap; gap: .75rem; margin-bottom: .8rem; }
.ca-hero-figure { background: rgba(255,255,255,.14); border-radius: .6rem; padding: .55rem .9rem; }
.ca-hero.neutral .ca-hero-figure { background: #fff; border: 1px solid var(--ca-border); }
.ca-hero-figure b { display: block; font-size: 1.45rem; }
.ca-hero-figure span { font-size: .85rem; opacity: .9; }
.ca-hero-note { font-size: .9rem; opacity: .92; margin: 0 0 .3rem; line-height: 1.45; }

/* Tarjetas de totales */
.ca-totals { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: .75rem; }
.ca-total {
  border: 1px solid var(--ca-border); border-radius: .75rem; padding: .9rem 1rem; background: #fff; color: var(--ca-text);
}
.ca-total.best { border: 2px solid var(--ca-green); background: var(--ca-green-wash); }
.ca-total.split { border-style: dashed; }
.ca-total-name { font-weight: 700; font-size: .95rem; display: flex; justify-content: space-between; gap: .5rem; }
.ca-total-amount { font-size: 1.6rem; font-weight: 700; margin: .3rem 0 .2rem; }
.ca-total.best .ca-total-amount { color: var(--ca-green); }
.ca-total-meta { font-size: .82rem; color: var(--ca-muted); line-height: 1.4; }
.ca-total-meta .warn { color: var(--ca-warn); font-weight: 700; }
.ca-badge {
  display: inline-block; background: var(--ca-green); color: #fff; border-radius: 999px;
  padding: .1rem .55rem; font-size: .72rem; font-weight: 700; white-space: nowrap;
}

/* Tabla de precios, como las tablas de la propuesta: encabezado verde y bordes grises */
.ca-table-wrap { overflow-x: auto; border: 1px solid var(--ca-border); border-radius: .6rem; }
.ca-table { width: 100%; border-collapse: collapse; font-size: .88rem; color: var(--ca-text); }
.ca-table th {
  background: var(--ca-green); color: #fff; text-align: left; font-weight: 700;
  padding: .6rem .75rem; white-space: nowrap;
}
.ca-table td { border-top: 1px solid var(--ca-border); padding: .6rem .75rem; vertical-align: top; min-width: 11rem; }
.ca-table td + td, .ca-table th + th { border-left: 1px solid var(--ca-border); }
.ca-table td.item { min-width: 9rem; background: #FAFBFA; }
.ca-table td.item b { display: block; }
.ca-table td.best { background: var(--ca-green-soft); }
.ca-table td.missing { color: var(--ca-muted); font-style: italic; }
.ca-small { display: block; font-size: .78rem; color: var(--ca-muted); margin-top: .15rem; line-height: 1.35; }
.ca-pname { font-weight: 700; line-height: 1.3; }
.ca-pname a { color: var(--ca-blue); text-decoration: none; }
.ca-pname a:hover { text-decoration: underline; }
.ca-price { font-size: 1rem; font-weight: 700; margin-top: .3rem; }
.ca-before { text-decoration: line-through; color: var(--ca-muted); font-weight: 400; font-size: .8rem; margin-left: .3rem; }
.ca-table td.best .ca-price { color: var(--ca-green-dark); }
.ca-table .ca-badge { margin-bottom: .35rem; margin-right: .25rem; }
.ca-badge.edited { background: var(--ca-blue); }

/* Plan de compra por tienda */
.ca-plan { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: .75rem; }
.ca-plan-card { border: 1px solid var(--ca-border); border-radius: .75rem; overflow: hidden; background: #fff; color: var(--ca-text); }
.ca-plan-card h4 {
  margin: 0; padding: .6rem .9rem; background: var(--ca-green); color: #fff; font-size: 1rem;
  display: flex; justify-content: space-between;
}
.ca-plan-card ul { margin: 0; padding: .5rem .9rem .7rem 1.9rem; }
.ca-plan-card li { padding: .2rem 0; font-size: .9rem; }
.ca-plan-card li span { color: var(--ca-muted); float: right; margin-left: .5rem; }
</style>
"""

load_dotenv()
logging.basicConfig(
    level=os.getenv("CANASTAI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("fontTools").setLevel(logging.WARNING)   # fpdf2 usa fontTools y es muy verboso
config = load_config()


def store_name(store_id: str) -> str:
    """Nombre bonito de la tienda: "exito" -> "Éxito"."""
    return config["stores"][store_id]["name"]


def store_names() -> dict[str, str]:
    """Diccionario id -> nombre de todas las tiendas."""
    names = {}
    for store_id in config["stores"]:
        names[store_id] = store_name(store_id)
    return names


def escape_dollars(text: str) -> str:
    """Streamlit interpreta "$...$" como fórmula matemática; esto lo evita."""
    return text.replace("$", "\\$")


def esc(text: str) -> str:
    """Escapa texto (de la tienda, del usuario o del agente) antes de meterlo en HTML."""
    return html.escape(str(text))


def join_names(names: list[str]) -> str:
    """["Éxito", "Jumbo", "Olímpica"] -> "Éxito, Jumbo y Olímpica"."""
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " y " + names[-1]


def plural(count: int, singular: str, plural_word: str) -> str:
    """1 -> "1 producto", 3 -> "3 productos"."""
    return f"{count} {singular if count == 1 else plural_word}"


def step_header(number: int, title: str) -> None:
    """Encabezado de un paso del formulario, con el número en un círculo verde."""
    st.html(
        f'<div class="ca-step"><span class="ca-step-num">{number}</span>'
        f'<span class="ca-step-title">{esc(title)}</span></div>'
    )


def section_title(title: str) -> None:
    """Título de sección en verde, como los títulos de la propuesta."""
    st.html(f'<div class="ca-section">{esc(title)}</div>')


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------

def show_recommendation_card(result: products.ComparisonResult) -> None:
    """Tarjeta principal: dónde comprar y cuánto se ahorra, con las cifras de products.py."""
    names = store_names()
    savings_lines = products.describe_savings(result, names)

    if result.cheapest_store is None:
        st.html(
            '<div class="ca-hero neutral">'
            '<div class="ca-hero-label">Resultado</div>'
            '<div class="ca-hero-title">No hay un ahorro que comparar</div>'
            f'<p class="ca-hero-note">{esc(result.savings_note)}</p>'
            "</div>"
        )
        return

    cheapest = names[result.cheapest_store]
    split_extra = result.comparable_totals[result.cheapest_store] - result.split_comparable_total
    figures = []
    if result.most_expensive_store is None:
        title = "Todas las tiendas cuestan lo mismo"
    else:
        title = f"Compra en {cheapest}"
        figures.append(
            f'<div class="ca-hero-figure"><b>{products.format_cop(result.savings_single_store)}</b>'
            f"<span>de ahorro frente a {esc(names[result.most_expensive_store])}</span></div>"
        )
    figures.append(
        f'<div class="ca-hero-figure"><b>{products.format_cop(result.comparable_totals[result.cheapest_store])}</b>'
        f"<span>total en {esc(cheapest)} (productos comparables)</span></div>"
    )
    if split_extra > 0:
        figures.append(
            f'<div class="ca-hero-figure"><b>{products.format_cop(split_extra)} más</b>'
            "<span>si divides la compra entre tiendas</span></div>"
        )

    st.html(
        '<div class="ca-hero">'
        '<div class="ca-hero-label">Nuestra recomendación</div>'
        f'<div class="ca-hero-title">{esc(title)}</div>'
        f'<div class="ca-hero-figures">{"".join(figures)}</div>'
        + "".join(f'<p class="ca-hero-note">{esc(line)}</p>' for line in savings_lines)
        + "</div>"
    )


def show_totals(result: products.ComparisonResult) -> None:
    """Tarjetas con el total de cada tienda y el del óptimo dividido."""
    item_count = len(result.rows)
    cards = []
    for store_id, store_total in result.store_totals.items():
        is_best = store_id == result.cheapest_store
        missing = len(store_total.missing_items)
        meta = f"{len(store_total.found_items)} de {item_count} productos"
        if missing > 0:
            meta += f' · <span class="warn">faltan {missing}</span>'
        meta += f"<br>Domicilio {products.format_cop(store_total.delivery_cost)}"
        badge = '<span class="ca-badge">Recomendada</span>' if is_best else ""
        cards.append(
            f'<div class="ca-total{" best" if is_best else ""}">'
            f'<div class="ca-total-name"><span>Todo en {esc(store_name(store_id))}</span>{badge}</div>'
            f'<div class="ca-total-amount">{products.format_cop(store_total.total)}</div>'
            f'<div class="ca-total-meta">{meta}</div></div>'
        )

    split_stores = []
    for store_id in result.split_plan.values():
        if store_name(store_id) not in split_stores:
            split_stores.append(store_name(store_id))
    cards.append(
        '<div class="ca-total split">'
        '<div class="ca-total-name"><span>Dividiendo la compra</span></div>'
        f'<div class="ca-total-amount">{products.format_cop(result.split_total)}</div>'
        f'<div class="ca-total-meta">{len(result.split_plan)} de {item_count} productos'
        f'<br>{esc(join_names(split_stores)) or "—"} · domicilios {products.format_cop(result.split_delivery)}</div></div>'
    )
    st.html(f'<div class="ca-totals">{"".join(cards)}</div>')
    st.caption(
        "Los totales de cada tienda solo suman lo que esa tienda tiene; por eso el ahorro de arriba "
        "se calcula con los productos que están en todas."
    )


def describe_cell(selection: products.Selection, is_best: bool, only_store: bool, edited: bool) -> str:
    """HTML de una celda de la tabla: producto, presentación, precio y precio por unidad."""
    edited_badge = '<span class="ca-badge edited">Cambiado por ti</span>' if edited else ""
    if selection.product is None:
        return f'<td class="missing">{edited_badge}<div>No disponible</div></td>'
    product = selection.product

    name = esc(product.name)
    if product.url:
        name = f'<a href="{esc(product.url)}" target="_blank" rel="noopener">{name}</a>'
    details = [products.format_quantity(product.quantity, product.base_unit)]
    if product.brand:
        details.insert(0, product.brand)

    price = products.format_cop(product.price)
    if product.list_price > product.price:
        price += f'<span class="ca-before">{products.format_cop(product.list_price)}</span>'
    if selection.units > 1:
        price = f"{selection.units} × {price}"
        line_total = f"Total {products.format_cop(products.line_cost(selection))}"
    else:
        line_total = ""

    unit_price = products.price_per_reference_unit(product)
    unit_text = ""
    if unit_price is not None:
        unit_text = f"{products.format_cop(unit_price)} por {products.reference_unit_label(product.base_unit)}"

    badge = ""
    if is_best:
        badge = '<span class="ca-badge">Única tienda</span>' if only_store else '<span class="ca-badge">★ Más barato</span>'
    badge += edited_badge

    small_lines = [text for text in (line_total, unit_text) if text]
    return (
        f'<td class="{"best" if is_best else ""}">{badge}'
        f'<div class="ca-pname">{name}</div>'
        f'<span class="ca-small">{esc(" · ".join(details))}</span>'
        f'<div class="ca-price">{price}</div>'
        + "".join(f'<span class="ca-small">{esc(text)}</span>' for text in small_lines)
        + "</td>"
    )


def show_products_table(result: products.ComparisonResult) -> None:
    """Tabla producto x tienda con el más barato resaltado en verde."""
    active_stores = list(result.store_totals.keys())
    if len(active_stores) == 0:
        st.info("Ninguna tienda respondió, así que no hay precios que mostrar.")
        return

    header = "<th>Lo que pediste</th>" + "".join(f"<th>{esc(store_name(s))}</th>" for s in active_stores)
    body = []
    for row in result.rows:
        found = [s for s in active_stores if row.selections[s].product is not None]
        cells = [
            f'<td class="item"><b>{esc(row.item)}</b>'
            + (f'<span class="ca-small">Comparado por {esc(row.compared_by)}</span>' if row.compared_by else "")
            + "</td>"
        ]
        for store_id in active_stores:
            cells.append(describe_cell(
                row.selections[store_id],
                store_id == row.cheapest_store,
                len(found) == 1,
                (row.item, store_id) in result.user_changes,
            ))
        body.append(f"<tr>{''.join(cells)}</tr>")

    st.html(
        f'<div class="ca-table-wrap"><table class="ca-table"><thead><tr>{header}</tr></thead>'
        f'<tbody>{"".join(body)}</tbody></table></div>'
    )
    st.caption("En verde, la tienda más barata para cada producto. Toca el nombre de un producto para verlo en la tienda.")


def show_split_plan(result: products.ComparisonResult) -> None:
    """Lista de compras por tienda para el óptimo dividido."""
    if not result.split_plan:
        st.info("No se encontró ningún producto, así que no hay plan de compra.")
        return

    by_store: dict[str, list[str]] = {}
    for item, store_id in result.split_plan.items():
        by_store.setdefault(store_id, []).append(item)
    rows_by_item = {row.item: row for row in result.rows}

    cards = []
    for store_id, items in by_store.items():
        subtotal = Decimal("0")
        lines = []
        for item in items:
            cost = products.line_cost(rows_by_item[item].selections[store_id])
            subtotal += cost
            lines.append(f"<li>{esc(item)}<span>{products.format_cop(cost)}</span></li>")
        cards.append(
            f'<div class="ca-plan-card"><h4><span>{esc(store_name(store_id))}</span>'
            f"<span>{products.format_cop(subtotal)}</span></h4><ul>{''.join(lines)}</ul></div>"
        )
    st.html(f'<div class="ca-plan">{"".join(cards)}</div>')
    st.caption(
        f"Si compras cada producto donde sale más barato: {products.format_cop(result.split_subtotal)} en productos "
        f"+ {products.format_cop(result.split_delivery)} de domicilios = {products.format_cop(result.split_total)}."
    )


# ---------------------------------------------------------------------------
# Cambiar un producto a mano
# ---------------------------------------------------------------------------

NOT_AVAILABLE = "__no_disponible__"


def product_key(item: str, store_id: str) -> str:
    """Clave del selector de producto para ese ítem y tienda."""
    return f"edit_product|{item}|{store_id}"


def units_key(item: str, store_id: str, product_id: str) -> str:
    """Clave del número de paquetes. Incluye el producto para que, al cambiarlo, se recalcule."""
    return f"edit_units|{item}|{store_id}|{product_id}"


def find_row(result: products.ComparisonResult, item: str) -> products.ItemRow:
    """Fila de la tabla para un ítem."""
    for row in result.rows:
        if row.item == item:
            return row
    raise KeyError(item)


def editor_options(result: products.ComparisonResult, item: str, store_id: str) -> dict[str, products.Product]:
    """product_id -> Product de las opciones para ese ítem y tienda, con la elección actual primero."""
    current = find_row(result, item).selections[store_id].product
    options = {}
    if current is not None:
        options[current.product_id] = current
    for product in result.candidates.get(item, {}).get(store_id, []):
        options.setdefault(product.product_id, product)
    return options


def describe_option(product: products.Product) -> str:
    """Texto de una opción del selector: nombre, presentación, precio y precio por unidad."""
    text = f"{product.name} · {products.format_quantity(product.quantity, product.base_unit)} · {products.format_cop(product.price)}"
    unit_price = products.price_per_reference_unit(product)
    if unit_price is not None:
        text += f" ({products.format_cop(unit_price)}/{products.reference_unit_label(product.base_unit)})"
    return text


def apply_change() -> None:
    """Callback de "Aplicar cambio": lee lo elegido, recalcula y rehace el PDF."""
    result = st.session_state.result
    item = st.session_state.edit_item
    store_id = st.session_state.edit_store
    product_id = st.session_state[product_key(item, store_id)]
    if product_id == NOT_AVAILABLE:
        product, units = None, 0
        note = f"«{item}» en {store_name(store_id)}: marcado como no disponible."
    else:
        product = editor_options(result, item, store_id)[product_id]
        units = int(st.session_state[units_key(item, store_id, product_id)])
        note = f"«{item}» en {store_name(store_id)}: {product.name} x{units}."
    new_result = products.replace_selection(result, item, store_id, product, units, note)
    st.session_state.result = new_result
    st.session_state.pdf_bytes = build_pdf(new_result, config)
    st.toast("Cambio aplicado. Totales recalculados.", icon=":material/check_circle:")


def undo_changes() -> None:
    """Callback de "Deshacer mis cambios": vuelve al resultado que entregó el agente."""
    original = st.session_state.original_result
    st.session_state.result = original
    st.session_state.pdf_bytes = build_pdf(original, config)
    for key in list(st.session_state.keys()):
        if str(key).startswith(("edit_product|", "edit_units|")):
            del st.session_state[key]


def show_editor(result: products.ComparisonResult) -> None:
    """Panel para cambiar el producto elegido en una tienda sin volver a correr el agente."""
    active_stores = list(result.store_totals.keys())
    if not active_stores or not result.rows:
        return

    with st.container(border=True):
        st.markdown("**¿No te convence un producto? Cámbialo**")
        st.caption("Elige otro de los que encontró el agente. Los totales, el ahorro y el PDF se recalculan sin volver a buscar.")

        item_column, store_column = st.columns([3, 2])
        item = item_column.selectbox("Producto de tu lista", [row.item for row in result.rows], key="edit_item")
        store_id = store_column.selectbox("Tienda", active_stores, format_func=store_name, key="edit_store")

        current = find_row(result, item).selections[store_id]
        options = editor_options(result, item, store_id)
        current_id = current.product.product_id if current.product is not None else NOT_AVAILABLE
        option_ids = list(options.keys()) + [NOT_AVAILABLE]

        def label(product_id: str) -> str:
            if product_id == NOT_AVAILABLE:
                return "No disponible en esta tienda"
            text = describe_option(options[product_id])
            return text + "  ← elegido ahora" if product_id == current_id else text

        product_column, units_column = st.columns([4, 1])
        chosen_id = product_column.selectbox(
            "Producto en la tienda",
            option_ids,
            index=option_ids.index(current_id),
            format_func=label,
            key=product_key(item, store_id),
        )
        if len(options) <= 1:
            product_column.caption("El agente no guardó otras opciones para este producto en esta tienda.")

        if chosen_id == NOT_AVAILABLE:
            units = 0
            units_column.number_input("Paquetes", value=0, disabled=True, key=f"edit_units_off|{item}|{store_id}")
            new_cost = Decimal("0")
        else:
            chosen = options[chosen_id]
            default_units = current.units if chosen_id == current_id else products.units_to_cover(item, current, chosen)
            units = units_column.number_input(
                "Paquetes", min_value=1, max_value=99, value=default_units, step=1,
                key=units_key(item, store_id, chosen_id),
            )
            new_cost = chosen.price * units

        unchanged = chosen_id == current_id and units == current.units
        with st.container(horizontal=True, vertical_alignment="center"):
            if not unchanged:
                st.caption(
                    f"En {store_name(store_id)}, este producto pasa de {products.format_cop(products.line_cost(current))} "
                    f"a {products.format_cop(new_cost)}."
                )
            st.space("stretch")
            st.button("Aplicar cambio", on_click=apply_change, type="primary", icon=":material/check:", disabled=unchanged)


def show_result(result: products.ComparisonResult, pdf_bytes: bytes, events: list[str]) -> None:
    """Muestra todo el resultado de la comparación."""
    st.divider()
    for store_id, error in result.failed_stores.items():
        st.warning(f"{store_name(store_id)} no respondió y quedó por fuera de la comparación: {error}")
    for store_id in result.stores_without_products:
        st.warning(f"{store_name(store_id)} respondió, pero no encontró ningún producto de la lista; no entra en el cálculo del ahorro.")

    show_recommendation_card(result)

    actions, info = st.columns([1, 2], vertical_alignment="center")
    actions.download_button(
        "Descargar recomendación (PDF)",
        data=pdf_bytes,
        file_name=f"recomendacion_canastai_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf",
        type="primary",
        icon=":material/download:",
        width="stretch",
    )
    info.caption(
        f"Precios consultados el {result.consulted_at} en {result.city} · Modo: {MODE_LABELS.get(result.mode, result.mode).lower()}"
    )

    if result.user_changes:
        with st.container(border=True, horizontal=True, vertical_alignment="center"):
            st.markdown(
                f"**✏️ Cambiaste {plural(len(result.user_changes), 'producto', 'productos')}.** "
                "La recomendación, los totales y el PDF ya incluyen tus cambios."
            )
            st.space("stretch")
            st.button("Deshacer mis cambios", on_click=undo_changes, icon=":material/undo:")
        if result.recommendation_text:
            with st.expander("Recomendación original del agente (escrita antes de tus cambios)"):
                st.markdown(escape_dollars(result.recommendation_text))
    elif result.recommendation_text:
        with st.container(border=True):
            st.markdown("**💬 Lo que dice el agente**")
            st.markdown(escape_dollars(result.recommendation_text))

    section_title("Totales")
    show_totals(result)

    section_title("Detalle")
    notes_count = len(result.assumptions) + len(result.not_found)
    tab_prices, tab_plan, tab_notes, tab_steps = st.tabs([
        "Precios por producto",
        "Lista por tienda",
        f"Supuestos y faltantes ({notes_count})",
        "Pasos del agente",
    ])
    with tab_prices:
        show_products_table(result)
        show_editor(result)
    with tab_plan:
        show_split_plan(result)
    with tab_notes:
        if result.not_found:
            st.markdown("**No encontrados en ninguna tienda**")
            st.markdown("\n".join(f"- {item}" for item in result.not_found))
        if result.assumptions:
            st.markdown("**Supuestos de la comparación**")
            st.markdown(escape_dollars("\n".join(f"- {assumption}" for assumption in result.assumptions)))
        if notes_count == 0:
            st.caption("Sin supuestos: todos los productos se encontraron y se compararon directamente.")
    with tab_steps:
        with st.container(height=360):
            st.text("\n".join(events))


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------

def load_test_list() -> None:
    """Pone la lista de ejemplo en el cuadro de texto (callback del botón)."""
    st.session_state.shopping_list = TEST_LIST_PATH.read_text(encoding="utf-8")


def clear_list() -> None:
    """Vacía el cuadro de texto (callback del botón)."""
    st.session_state.shopping_list = ""


st.set_page_config(page_title="CanastAI", page_icon="🛒", layout="wide")
st.html(STYLES)

all_store_ids = list(config["stores"].keys())
st.html(
    '<div class="ca-header"><h1>CanastAI</h1>'
    f"<p>Escribe tu lista de mercado y te decimos en qué tienda comprar para pagar menos: "
    f"{esc(join_names([store_name(s) for s in all_store_ids]))}.</p>"
    f'<span class="ca-chip">📍 Precios de {esc(config["city"])}</span>'
    '<span class="ca-chip">Agente con Claude</span></div>'
)

if "shopping_list" not in st.session_state:
    st.session_state.shopping_list = ""

with st.container(border=True):
    list_column, options_column = st.columns([3, 2], gap="large")

    with list_column:
        step_header(1, "Tu lista de mercado")
        st.text_area(
            "Lista de mercado",
            key="shopping_list",
            height=260,
            placeholder=LIST_PLACEHOLDER,
            label_visibility="collapsed",
        )
        items = parse_shopping_list(st.session_state.shopping_list)
        with st.container(horizontal=True, vertical_alignment="center", gap="small"):
            if items:
                st.caption(f"**{plural(len(items), 'producto', 'productos')}**")
            else:
                st.caption("Un producto por línea, con cantidad, marca o tamaño si quieres.")
            st.space("stretch")
            st.button("Usar lista de ejemplo", on_click=load_test_list, type="tertiary", icon=":material/list_alt:")
            st.button("Borrar", on_click=clear_list, type="tertiary", icon=":material/close:", disabled=not items)

    with options_column:
        step_header(2, "¿Dónde comparar?")
        selected_stores = st.pills(
            "Tiendas",
            all_store_ids,
            selection_mode="multi",
            default=all_store_ids,
            format_func=store_name,
            key="stores",
            label_visibility="collapsed",
        )

        step_header(3, "¿Cómo comparar?")
        mode = st.radio(
            "Modo",
            [MODE_STANDARD, MODE_BRAND],
            format_func=lambda value: MODE_LABELS[value],
            captions=[MODE_CAPTIONS[MODE_STANDARD], MODE_CAPTIONS[MODE_BRAND]],
            key="mode",
            label_visibility="collapsed",
        )

        delivery_costs = {}
        with st.expander("Costo de domicilio (opcional)", icon=":material/local_shipping:"):
            st.caption("Se suma al total de cada tienda donde compres algo.")
            for store_id in selected_stores:
                delivery_costs[store_id] = st.number_input(
                    store_name(store_id), min_value=0, value=0, step=500, key=f"delivery_{store_id}", format="%d"
                )

    compare_clicked = st.button(
        "Comparar precios",
        type="primary",
        icon=":material/shopping_cart:",
        width="stretch",
        disabled=len(selected_stores) == 0,
        help="Elige al menos una tienda." if len(selected_stores) == 0 else None,
    )

if compare_clicked:
    # La lista se valida al hacer clic (y no deshabilitando el botón) porque el cuadro de
    # texto solo avisa de los cambios cuando pierde el foco: con el botón deshabilitado,
    # el primer clic después de escribir no haría nada.
    items = parse_shopping_list(st.session_state.shopping_list)
    if not items:
        st.error("Escribe al menos un producto en la lista, o usa la lista de ejemplo.", icon=":material/edit_note:")
        st.stop()

    events = []
    status = st.status(
        f"Buscando {plural(len(items), 'producto', 'productos')} en {join_names([store_name(s) for s in selected_stores])}…",
        expanded=True,
    )

    def show_event(kind: str, message: str) -> None:
        """Se llama en cada paso del agente para mostrarlo en vivo."""
        line = f"{EVENT_ICONS.get(kind, '•')} {message}"
        events.append(line)
        status.write(escape_dollars(line))

    try:
        result = run_agent(
            shopping_list=st.session_state.shopping_list,
            stores=selected_stores,
            mode=mode,
            delivery_costs=delivery_costs,
            on_event=show_event,
        )
    except (AgentError, ValueError) as error:
        status.update(label="El agente no pudo terminar", state="error")
        st.error(str(error))
        st.stop()

    status.update(label="Comparación lista", state="complete", expanded=False)
    pdf_bytes = build_pdf(result, config)
    # Se guarda en la sesión para que siga visible cuando Streamlit recargue la página
    # (por ejemplo, al presionar el botón de descarga). Se guarda al final, junto,
    # para no mezclar el resultado nuevo con el PDF de una corrida anterior.
    st.session_state.result = result
    st.session_state.pdf_bytes = pdf_bytes
    st.session_state.events = events
    st.session_state.original_result = result   # para "Deshacer mis cambios"
    # Los selectores del panel "Cámbialo" apuntaban a la lista anterior.
    for key in list(st.session_state.keys()):
        if str(key).startswith("edit_"):
            del st.session_state[key]

if "result" in st.session_state:
    show_result(st.session_state.result, st.session_state.pdf_bytes, st.session_state.events)
else:
    st.caption("Cuando compares, aquí verás la tienda recomendada, el ahorro, los totales y el precio de cada producto.")
