"""
run_cli.py: prueba CanastAI desde la terminal, sin la interfaz de Streamlit.

Uso:
    python run_cli.py data/lista_prueba.txt
    python run_cli.py data/lista_prueba.txt --tiendas exito jumbo --modo "marca específica"
    python run_cli.py data/lista_prueba.txt --domicilio exito=5000 --domicilio jumbo=3900
"""

import argparse
import logging
import os
import sys
from decimal import Decimal

from dotenv import load_dotenv

import products
from agent import MODE_BRAND, MODE_STANDARD, AgentError, run_agent
from report_pdf import build_pdf
from stores import load_config


def print_event(kind: str, message: str) -> None:
    """Muestra en la terminal cada paso del agente."""
    print(f"  [{kind}] {message}", flush=True)


def parse_delivery_costs(values: list[str]) -> dict[str, Decimal]:
    """Convierte ["exito=5000", "jumbo=3.900"] en {"exito": Decimal("5000"), "jumbo": Decimal("3900")}."""
    delivery_costs = {}
    for value in values:
        store_id, amount = value.split("=")
        delivery_costs[store_id.strip()] = products.parse_number(amount.strip())
    return delivery_costs


def print_result(result: products.ComparisonResult, config: dict) -> None:
    """Imprime la tabla, los totales, la recomendación y los supuestos."""
    names = {store_id: config["stores"][store_id]["name"] for store_id in config["stores"]}

    print("\n=== Productos por tienda (* = más barato) ===")
    for row in result.rows:
        print(f"\n{row.item}   [{row.compared_by}]")
        for store_id, selection in row.selections.items():
            marker = "*" if store_id == row.cheapest_store else " "
            if selection.product is None:
                print(f"  {marker} {names[store_id]:9} no disponible")
                continue
            product = selection.product
            unit_price = products.price_per_reference_unit(product)
            unit_text = ""
            if unit_price is not None:
                unit_text = f"  ({products.format_cop(unit_price)}/{products.reference_unit_label(product.base_unit)})"
            print(
                f"  {marker} {names[store_id]:9} {product.name} "
                f"[{products.format_quantity(product.quantity, product.base_unit)}] "
                f"x{selection.units} = {products.format_cop(products.line_cost(selection))}{unit_text}"
            )

    print("\n=== Totales ===")
    for store_id, store_total in result.store_totals.items():
        print(
            f"  {names[store_id]:9} {products.format_cop(store_total.total):>10}  "
            f"({len(store_total.found_items)} productos, domicilio {products.format_cop(store_total.delivery_cost)})"
        )
    print(f"  {'Dividido':9} {products.format_cop(result.split_total):>10}  (domicilios {products.format_cop(result.split_delivery)})")
    for line in products.describe_savings(result, names):
        print(f"  {line}")

    for store_id, error in result.failed_stores.items():
        print(f"  ¡{names[store_id]} falló!: {error}")
    for store_id in result.stores_without_products:
        print(f"  {names[store_id]} no encontró ningún producto de la lista.")

    print("\n=== Recomendación ===")
    print(result.recommendation_text)

    if result.assumptions:
        print("\n=== Supuestos ===")
        for assumption in result.assumptions:
            print(f"  - {assumption}")
    if result.not_found:
        print("\n=== No encontrados en ninguna tienda ===")
        for item in result.not_found:
            print(f"  - {item}")


def main() -> None:
    """Lee los argumentos, corre el agente con la lista e imprime el resultado (y opcionalmente el PDF)."""
    load_dotenv()
    logging.basicConfig(
        level=os.getenv("CANASTAI_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("fontTools").setLevel(logging.WARNING)   # fpdf2 usa fontTools y es muy verboso
    config = load_config()

    parser = argparse.ArgumentParser(description="Compara una lista de mercado en Éxito, Jumbo y Olímpica.")
    # Las opciones se escriben en español en la terminal; `dest` les da un nombre en inglés en el código.
    parser.add_argument("list_path", metavar="lista", help="Archivo de texto con un producto por línea")
    parser.add_argument("--tiendas", dest="stores", nargs="+", default=list(config["stores"]), help="Ids de tienda (por defecto todas)")
    parser.add_argument("--modo", dest="mode", default=MODE_STANDARD, choices=[MODE_STANDARD, MODE_BRAND])
    parser.add_argument("--domicilio", dest="delivery", action="append", default=[], help="Costo de domicilio, p. ej. exito=5000")
    parser.add_argument("--pdf", dest="pdf_path", help="Ruta donde guardar la Recomendación de compra en PDF")
    args = parser.parse_args()

    with open(args.list_path, encoding="utf-8") as list_file:
        shopping_list = list_file.read()

    print(f"Comparando {args.list_path} en {', '.join(args.stores)} (modo {args.mode})...\n")
    try:
        result = run_agent(
            shopping_list=shopping_list,
            stores=args.stores,
            mode=args.mode,
            delivery_costs=parse_delivery_costs(args.delivery),
            on_event=print_event,
        )
    except (AgentError, ValueError) as error:
        print(f"\nNo se pudo completar la comparación: {error}")
        sys.exit(1)
    print_result(result, config)

    if args.pdf_path:
        with open(args.pdf_path, "wb") as pdf_file:
            pdf_file.write(build_pdf(result, config))
        print(f"\nPDF guardado en {args.pdf_path}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")   # tildes y ñ en la consola de Windows
    main()
