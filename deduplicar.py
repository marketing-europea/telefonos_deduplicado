from __future__ import annotations

import argparse
import re
from pathlib import Path

import pandas as pd


REPEATED_DIGITS_RE = re.compile(r"^(\d)\1+$")


def normalize_phone(value: object) -> str:
    """
    Limpia un telefono para validar moviles espanoles.

    Ejemplos:
    +34 612 345 678 -> 612345678
    0034 612345678  -> 612345678
    612-345-678     -> 612345678
    """
    if pd.isna(value):
        return ""

    text = str(value).strip()

    # Evita valores que Excel puede leer como float: 612345678.0
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    digits = re.sub(r"\D+", "", text)

    # Quita prefijos de Espana si el numero restante tiene 9 digitos.
    if len(digits) == 11 and digits.startswith("34"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("0034"):
        digits = digits[4:]

    return digits


def has_repeated_pattern(phone: str) -> bool:
    """
    Detecta patrones repetidos completos.

    Ejemplos invalidos:
    678678678
    123123123
    666666666
    """
    for size in range(1, (len(phone) // 2) + 1):
        if len(phone) % size != 0:
            continue

        pattern = phone[:size]
        if pattern * (len(phone) // size) == phone:
            return True

    return False


def invalid_reasons(phone: str) -> list[str]:
    """
    Devuelve los motivos por los que un telefono no es valido.

    Reglas:
    - Debe tener 9 digitos.
    - Debe empezar por 6 o 7.
    - No puede ser todo el mismo digito.
    - No puede ser tipo 600000000, 700000000, 611111111.
    - No puede ser un patron repetido tipo 678678678.
    """
    reasons: list[str] = []

    if not phone:
        return ["vacio"]

    if len(phone) != 9:
        reasons.append("no tiene 9 digitos")

    if phone[0] not in {"6", "7"}:
        reasons.append("no empieza por 6 o 7")

    if REPEATED_DIGITS_RE.fullmatch(phone):
        reasons.append("digitos repetidos")

    if len(phone) == 9 and phone[0] in {"6", "7"} and len(set(phone[1:])) == 1:
        reasons.append("demasiado repetitivo")

    if has_repeated_pattern(phone):
        reasons.append("patron repetido")

    return reasons


def is_valid_phone(phone: str) -> bool:
    return len(invalid_reasons(phone)) == 0


def format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


def phone_for_display(original: object, normalized: str) -> str:
    if normalized:
        return normalized

    if pd.isna(original) or str(original).strip() == "":
        return "(vacio)"

    return str(original).strip()


def analyze_phones(
    df: pd.DataFrame,
    agency_col: str = "agencia",
    phone_col: str = "telefono",
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Analiza telefonos por agencia.

    Devuelve:
    - resumen por agencia
    - telefonos no validos
    - telefonos repetidos
    """
    if agency_col not in df.columns:
        raise ValueError(f"No existe la columna de agencia: {agency_col}")

    if phone_col not in df.columns:
        raise ValueError(f"No existe la columna de telefono: {phone_col}")

    work = df[[agency_col, phone_col]].copy()
    work.columns = ["agencia", "telefono_original"]

    work["agencia"] = work["agencia"].fillna("Sin agencia").astype(str).str.strip()
    work.loc[work["agencia"].eq(""), "agencia"] = "Sin agencia"

    work["telefono_normalizado"] = work["telefono_original"].apply(normalize_phone)

    # Deduplicado dentro de cada agencia.
    work["es_repetido"] = work.duplicated(
        subset=["agencia", "telefono_normalizado"],
        keep="first",
    )

    deduped = work.loc[~work["es_repetido"]].copy()
    deduped["motivos"] = deduped["telefono_normalizado"].apply(invalid_reasons)
    deduped["es_valido"] = deduped["motivos"].apply(lambda reasons: len(reasons) == 0)
    deduped["motivos_texto"] = deduped["motivos"].apply(", ".join)
    deduped["telefono_mostrar"] = deduped.apply(
        lambda row: phone_for_display(row["telefono_original"], row["telefono_normalizado"]),
        axis=1,
    )

    totals = work.groupby("agencia").size().rename("telefonos_totales")
    dedup_totals = deduped.groupby("agencia").size().rename("telefonos_deduplicados")
    valid_totals = deduped.groupby("agencia")["es_valido"].sum().astype(int).rename("telefonos_validos")

    summary = pd.concat([totals, dedup_totals, valid_totals], axis=1).fillna(0).astype(int)
    summary["telefonos_repetidos"] = summary["telefonos_totales"] - summary["telefonos_deduplicados"]
    summary["telefonos_no_validos"] = summary["telefonos_deduplicados"] - summary["telefonos_validos"]
    summary["porcentaje_validos"] = [
        format_percent(valid, deduped_count)
        for valid, deduped_count in zip(
            summary["telefonos_validos"],
            summary["telefonos_deduplicados"],
        )
    ]

    invalid_examples = (
        deduped.loc[~deduped["es_valido"]]
        .groupby("agencia")["telefono_mostrar"]
        .apply(lambda values: ", ".join(values.astype(str).head(20)))
        .rename("telefonos_no_validos_ejemplo")
    )

    summary = summary.join(invalid_examples)
    summary["telefonos_no_validos_ejemplo"] = summary["telefonos_no_validos_ejemplo"].fillna("")
    summary = summary.reset_index()
    summary = summary.sort_values(["telefonos_validos", "agencia"], ascending=[False, True])

    invalids = deduped.loc[
        ~deduped["es_valido"],
        ["agencia", "telefono_original", "telefono_normalizado", "motivos_texto"],
    ].sort_values(["agencia", "telefono_normalizado"])

    duplicates = work.loc[
        work["es_repetido"],
        ["agencia", "telefono_original", "telefono_normalizado"],
    ].sort_values(["agencia", "telefono_normalizado"])

    return summary, invalids, duplicates


def read_input_file(path: str | Path) -> pd.DataFrame:
    path = Path(path)

    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)

    if path.suffix.lower() in {".xlsx", ".xls"}:
        return pd.read_excel(path)

    raise ValueError("El archivo debe ser .csv, .xlsx o .xls")


def write_output_file(
    output_path: str | Path,
    summary: pd.DataFrame,
    invalids: pd.DataFrame,
    duplicates: pd.DataFrame,
) -> None:
    output_path = Path(output_path)

    if output_path.suffix.lower() == ".xlsx":
        with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
            summary.to_excel(writer, index=False, sheet_name="resumen")
            invalids.to_excel(writer, index=False, sheet_name="no_validos")
            duplicates.to_excel(writer, index=False, sheet_name="repetidos")
        return

    # Si la salida no es xlsx, se crean 3 CSV separados.
    base = output_path.with_suffix("")
    summary.to_csv(f"{base}_resumen.csv", index=False)
    invalids.to_csv(f"{base}_no_validos.csv", index=False)
    duplicates.to_csv(f"{base}_repetidos.csv", index=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Valida telefonos por agencia.")
    parser.add_argument("archivo", help="Archivo de entrada: CSV, XLSX o XLS.")
    parser.add_argument("--agencia", default="agencia", help="Nombre de la columna de agencia.")
    parser.add_argument("--telefono", default="telefono", help="Nombre de la columna de telefono.")
    parser.add_argument("--salida", default="resultado_telefonos.xlsx", help="Archivo de salida.")
    args = parser.parse_args()

    df = read_input_file(args.archivo)
    summary, invalids, duplicates = analyze_phones(
        df,
        agency_col=args.agencia,
        phone_col=args.telefono,
    )

    write_output_file(args.salida, summary, invalids, duplicates)

    print("\nRESUMEN POR AGENCIA")
    print(summary.to_string(index=False))
    print(f"\nResultado guardado en: {args.salida}")


if __name__ == "__main__":
    main()
