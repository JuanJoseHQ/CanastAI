"""
app.py: interfaz web de CanastAI, hecha con Streamlit.

Correr con:  streamlit run app.py

Flujo de la pantalla:
1. El usuario escribe la lista (o carga la de prueba), elige tiendas, modo y domicilios.
2. Al presionar "Comparar precios" se llama a run_agent() de agent.py, y cada paso del
   agente (búsquedas, reintentos, elecciones) se muestra en vivo.
3. Con el ComparisonResult se muestran la recomendación, la tabla, los totales, los
   supuestos y el botón para descargar el PDF.
"""

import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

import products
from agent import MODE_BRAND, MODE_STANDARD, AgentError, run_agent
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
CHEAPEST_STYLE = "background-color: #d7f2d7; font-weight: bold"

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


def describe_selection(selection: products.Selection) -> str:
    """Texto de una celda de la tabla: producto, presentación, precio y precio por unidad."""
    if selection.product is None:
        return "No disponible"
    product = selection.product
    text = (
        f"{product.name} ({products.format_quantity(product.quantity, product.base_unit)}): "
        f"{products.format_cop(product.price)} x {selection.units}"
    )
    unit_price = products.price_per_reference_unit(product)
    if unit_price is not None:
        text += f" · {products.format_cop(unit_price)}/{products.reference_unit_label(product.base_unit)}"
    return text


def show_products_table(result: products.ComparisonResult) -> None:
    """Tabla producto x tienda con el más barato resaltado en verde."""
    active_stores = list(result.store_totals.keys())
    table_rows = []
    cheapest_flags = []
    for row in result.rows:
        texts = {"Producto pedido": row.item}
        flags = {"Producto pedido": False}
        for store_id in active_stores:
            texts[store_name(store_id)] = describe_selection(row.selections[store_id])
            flags[store_name(store_id)] = store_id == row.cheapest_store
        texts["Comparado por"] = row.compared_by
        flags["Comparado por"] = False
        table_rows.append(texts)
        cheapest_flags.append(flags)

    table = pd.DataFrame(table_rows)
    flags_table = pd.DataFrame(cheapest_flags)

    def highlight_cheapest(_table: pd.DataFrame) -> pd.DataFrame:
        styles = []
        for _, flag_row in flags_table.iterrows():
            row_styles = []
            for is_cheapest in flag_row:
                row_styles.append(CHEAPEST_STYLE if is_cheapest else "")
            styles.append(row_styles)
        return pd.DataFrame(styles, index=_table.index, columns=_table.columns)

    st.table(table.style.apply(highlight_cheapest, axis=None).hide(axis="index"))


def show_totals(result: products.ComparisonResult) -> None:
    """Totales por tienda y del óptimo dividido."""
    columns = st.columns(len(result.store_totals) + 1)
    for column, (store_id, store_total) in zip(columns, result.store_totals.items()):
        column.metric(
            f"Todo en {store_name(store_id)}",
            products.format_cop(store_total.total),
            help=f"{len(store_total.found_items)} de {len(result.rows)} productos; domicilio {products.format_cop(store_total.delivery_cost)}",
        )
    columns[-1].metric(
        "Óptimo dividido",
        products.format_cop(result.split_total),
        help=f"Incluye domicilios por {products.format_cop(result.split_delivery)}",
    )

    savings_lines = products.describe_savings(result, store_names())
    if len(savings_lines) == 1:
        st.info(escape_dollars(savings_lines[0]))
    else:
        # Las primeras frases dan las cifras del ahorro; la última explica qué productos entraron.
        st.success(escape_dollars(" ".join(savings_lines[:-1])))
        st.caption(escape_dollars(savings_lines[-1]))

    if result.split_plan:
        plan_lines = []
        for item, store_id in result.split_plan.items():
            plan_lines.append(f"- **{item}**: {store_name(store_id)}")
        with st.expander("Dónde comprar cada producto en el óptimo dividido"):
            st.markdown("\n".join(plan_lines))


def show_result(result: products.ComparisonResult, pdf_bytes: bytes, events: list[str]) -> None:
    """Muestra todo el resultado de la comparación."""
    for store_id, error in result.failed_stores.items():
        st.warning(f"{store_name(store_id)} no respondió y quedó por fuera de la comparación: {error}")
    for store_id in result.stores_without_products:
        st.warning(f"{store_name(store_id)} respondió, pero no encontró ningún producto de la lista; no entra en el cálculo del ahorro.")

    st.subheader("Recomendación")
    st.markdown(escape_dollars(result.recommendation_text))

    st.subheader("Totales")
    show_totals(result)

    st.subheader("Precios por producto y tienda")
    st.caption("En verde, la tienda más barata para cada producto.")
    show_products_table(result)

    if result.assumptions:
        st.subheader("Supuestos")
        st.markdown(escape_dollars("\n".join(f"- {assumption}" for assumption in result.assumptions)))
    if result.not_found:
        st.subheader("No encontrados en ninguna tienda")
        st.markdown("\n".join(f"- {item}" for item in result.not_found))

    st.download_button(
        "Descargar recomendación (PDF)",
        data=pdf_bytes,
        file_name=f"recomendacion_canastai_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf",
        mime="application/pdf",
        type="primary",
    )

    with st.expander("Pasos del agente"):
        st.text("\n".join(events))


# ---------------------------------------------------------------------------
# Pantalla
# ---------------------------------------------------------------------------

st.set_page_config(page_title="CanastAI", page_icon="🛒", layout="wide")
st.title("🛒 CanastAI")
st.caption(f"Compara tu lista de mercado en Éxito, Jumbo y Olímpica y te dice dónde comprar para pagar menos. Precios de {config['city']}.")

if "shopping_list" not in st.session_state:
    st.session_state.shopping_list = ""

with st.sidebar:
    st.header("Opciones")
    all_store_ids = list(config["stores"].keys())
    selected_stores = st.multiselect("Tiendas", all_store_ids, default=all_store_ids, format_func=store_name)
    mode = st.radio(
        "Modo",
        [MODE_STANDARD, MODE_BRAND],
        captions=["El equivalente más barato, de cualquier marca.", "Respeta la marca y la presentación de la lista."],
    )
    st.subheader("Domicilio por tienda")
    delivery_costs = {}
    for store_id in selected_stores:
        delivery_costs[store_id] = st.number_input(
            f"{store_name(store_id)} (COP)", min_value=0, value=0, step=500, key=f"delivery_{store_id}"
        )

if st.button("Cargar lista de prueba"):
    st.session_state.shopping_list = TEST_LIST_PATH.read_text(encoding="utf-8")
st.text_area("Lista de mercado (un producto por línea)", key="shopping_list", height=230)

if st.button("Comparar precios", type="primary", disabled=len(selected_stores) == 0):
    events = []
    status = st.status("El agente está trabajando…", expanded=True)

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

if "result" in st.session_state:
    show_result(st.session_state.result, st.session_state.pdf_bytes, st.session_state.events)
