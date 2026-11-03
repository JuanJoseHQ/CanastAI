"""
report_pdf.py: genera la "Recomendación de compra" en PDF.

El PDF se arma con los datos de ComparisonResult (precios, totales, ahorro, supuestos),
que salen de las tiendas y de products.py. Del LLM solo se toma el párrafo de
recomendación.

Usa fpdf2 con la fuente DejaVu Sans (en fonts/), porque las fuentes básicas de los
PDF no muestran tildes ni la ñ.
"""

from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.fonts import FontFace

import products

FONTS_DIR = Path(__file__).parent / "fonts"

TITLE = "Recomendación de compra — CanastAI"
CHEAPEST_FILL = (220, 242, 220)    # verde claro para resaltar el más barato
HEADER_FILL = (230, 230, 230)


def store_name(config: dict, store_id: str) -> str:
    """Nombre bonito de la tienda: "exito" -> "Éxito"."""
    return config["stores"][store_id]["name"]


def describe_selection(selection: products.Selection, is_cheapest: bool) -> str:
    """Texto de una celda de la tabla: producto, presentación, precio y precio por unidad."""
    if selection.product is None:
        return "No disponible"
    product = selection.product
    presentation = products.format_quantity(product.quantity, product.base_unit)
    lines = [
        f"{product.name}",
        f"{product.brand} · {presentation}",
        f"{products.format_cop(product.price)} x {selection.units} = {products.format_cop(products.line_cost(selection))}",
    ]
    unit_price = products.price_per_reference_unit(product)
    if unit_price is not None:
        lines.append(f"{products.format_cop(unit_price)} por {products.reference_unit_label(product.base_unit)}")
    if is_cheapest:
        lines.append("★ Más barato")
    return "\n".join(lines)


def write_section_title(pdf: FPDF, text: str) -> None:
    """Título de sección en negrilla con un poco de espacio antes."""
    pdf.ln(4)
    pdf.set_font("DejaVu", "B", 12)
    pdf.cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 9)


def write_paragraph(pdf: FPDF, text: str) -> None:
    """Párrafo de texto que ocupa todo el ancho."""
    pdf.multi_cell(0, 5, text, new_x="LMARGIN", new_y="NEXT")


def write_recommendation_text(pdf: FPDF, text: str) -> None:
    """Párrafo del agente: respeta las **negrillas** y quita los "#" de títulos si los hubiera."""
    clean_lines = []
    for line in text.splitlines():
        if line.startswith("#"):
            line = line.lstrip("#").strip()
        clean_lines.append(line)
    pdf.multi_cell(0, 5, "\n".join(clean_lines), new_x="LMARGIN", new_y="NEXT", markdown=True)


def write_header(pdf: FPDF, result: products.ComparisonResult, config: dict) -> None:
    """Título y datos de la consulta: fecha y hora, ciudad, tiendas y modo."""
    pdf.set_font("DejaVu", "B", 16)
    pdf.cell(0, 10, TITLE, new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("DejaVu", "", 10)
    store_names = [store_name(config, store_id) for store_id in result.stores]
    pdf.cell(0, 6, f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M')}    Precios consultados: {result.consulted_at}", new_x="LMARGIN", new_y="NEXT")
    pdf.cell(0, 6, f"Ciudad: {result.city}    Tiendas comparadas: {', '.join(store_names)}    Modo: {result.mode}", new_x="LMARGIN", new_y="NEXT")


def write_products_table(pdf: FPDF, result: products.ComparisonResult, config: dict) -> None:
    """Tabla producto x tienda, con el más barato resaltado."""
    write_section_title(pdf, "Precios por producto y tienda")
    active_stores = list(result.store_totals.keys())
    if len(active_stores) == 0:
        write_paragraph(pdf, "Ninguna tienda respondió.")
        return

    pdf.set_font("DejaVu", "", 8)
    first_column_width = 45
    store_column_width = (pdf.epw - first_column_width) / len(active_stores)
    col_widths = [first_column_width] + [store_column_width] * len(active_stores)
    headings_style = FontFace(emphasis="BOLD", fill_color=HEADER_FILL)
    cheapest_style = FontFace(fill_color=CHEAPEST_FILL)

    with pdf.table(col_widths=col_widths, headings_style=headings_style, line_height=4, text_align="LEFT") as table:
        heading = table.row()
        heading.cell("Lo pedido")
        for store_id in active_stores:
            heading.cell(store_name(config, store_id))

        for row in result.rows:
            table_row = table.row()
            item_text = row.item
            if row.compared_by:
                item_text += f"\n(comparado por {row.compared_by})"
            table_row.cell(item_text)
            for store_id in active_stores:
                is_cheapest = store_id == row.cheapest_store
                text = describe_selection(row.selections[store_id], is_cheapest)
                if is_cheapest:
                    table_row.cell(text, style=cheapest_style)
                else:
                    table_row.cell(text)
    pdf.set_font("DejaVu", "", 9)


def write_totals(pdf: FPDF, result: products.ComparisonResult, config: dict) -> None:
    """Totales por tienda y óptimo dividido, con domicilios."""
    write_section_title(pdf, "Totales")
    headings_style = FontFace(emphasis="BOLD", fill_color=HEADER_FILL)
    with pdf.table(col_widths=[60, 40, 40, 40, 40], headings_style=headings_style, line_height=5, width=220, align="LEFT") as table:
        heading = table.row()
        for title in ["Opción", "Productos", "Subtotal", "Domicilio", "Total"]:
            heading.cell(title)
        for store_id, store_total in result.store_totals.items():
            row = table.row()
            row.cell(f"Todo en {store_name(config, store_id)}")
            row.cell(f"{len(store_total.found_items)} de {len(result.rows)}")
            row.cell(products.format_cop(store_total.subtotal))
            row.cell(products.format_cop(store_total.delivery_cost))
            row.cell(products.format_cop(store_total.total))
        row = table.row()
        row.cell("Óptimo dividido")
        row.cell(f"{len(result.split_plan)} de {len(result.rows)}")
        row.cell(products.format_cop(result.split_subtotal))
        row.cell(products.format_cop(result.split_delivery))
        row.cell(products.format_cop(result.split_total))

    if result.split_plan:
        pdf.ln(2)
        plan_lines = []
        for item, store_id in result.split_plan.items():
            plan_lines.append(f"{item}: {store_name(config, store_id)}")
        write_paragraph(pdf, "Óptimo dividido, dónde comprar cada producto: " + "; ".join(plan_lines) + ".")


def write_recommendation(pdf: FPDF, result: products.ComparisonResult, config: dict) -> None:
    """Recomendación redactada por el agente y el ahorro calculado por products.py."""
    write_section_title(pdf, "Recomendación")
    if result.recommendation_text:
        write_recommendation_text(pdf, result.recommendation_text)
    pdf.ln(2)
    store_names = {}
    for store_id in config["stores"]:
        store_names[store_id] = store_name(config, store_id)
    for line in products.describe_savings(result, store_names):
        write_paragraph(pdf, line)
    for store_id, error in result.failed_stores.items():
        write_paragraph(pdf, f"{store_name(config, store_id)} no respondió y quedó por fuera: {error}")
    for store_id in result.stores_without_products:
        write_paragraph(pdf, f"{store_name(config, store_id)} no encontró ningún producto de la lista.")


def write_assumptions(pdf: FPDF, result: products.ComparisonResult) -> None:
    """Supuestos y productos que no se encontraron en ninguna tienda."""
    if result.assumptions:
        write_section_title(pdf, "Supuestos")
        for assumption in result.assumptions:
            write_paragraph(pdf, f"• {assumption}")
    if result.not_found:
        write_section_title(pdf, "No encontrados en ninguna tienda")
        for item in result.not_found:
            write_paragraph(pdf, f"• {item}")


def write_footers(pdf: FPDF, result: products.ComparisonResult) -> None:
    """Escribe el pie de página en todas las páginas, al final, volviendo a cada una."""
    footer = f"Precios de referencia consultados el {result.consulted_at}; pueden cambiar en cada tienda."
    pdf.set_auto_page_break(False)
    total_pages = pdf.page
    for page_number in range(1, total_pages + 1):
        pdf.page = page_number
        pdf.set_y(-12)
        pdf.set_font("DejaVu", "", 8)
        pdf.cell(0, 5, f"{footer}    Página {page_number} de {total_pages}", align="C")


def build_pdf(result: products.ComparisonResult, config: dict) -> bytes:
    """Genera el PDF completo y lo devuelve como bytes (para descargar o guardar)."""
    pdf = FPDF(orientation="landscape", format="A4")
    pdf.add_font("DejaVu", "", str(FONTS_DIR / "DejaVuSans.ttf"))
    pdf.add_font("DejaVu", "B", str(FONTS_DIR / "DejaVuSans-Bold.ttf"))
    # No hay archivo de cursiva: si el texto del agente trae "__cursiva__", se usa la normal.
    pdf.add_font("DejaVu", "I", str(FONTS_DIR / "DejaVuSans.ttf"))
    pdf.set_auto_page_break(True, margin=18)
    pdf.set_title(TITLE)
    pdf.add_page()

    write_header(pdf, result, config)
    write_products_table(pdf, result, config)
    write_totals(pdf, result, config)
    write_recommendation(pdf, result, config)
    write_assumptions(pdf, result)
    write_footers(pdf, result)

    return bytes(pdf.output())
