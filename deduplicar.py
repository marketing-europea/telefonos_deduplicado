from __future__ import annotations

import re
from io import BytesIO
from pathlib import Path
from typing import Iterable

import pandas as pd


REPEATED_DIGITS_RE = re.compile(r"^(\d)\1+$")


def normalize_phone(value: object) -> str:
    """Return only the meaningful digits for Spanish mobile validation."""
    if pd.isna(value):
        return ""

    text = str(value).strip()

    # Avoid Excel-looking floats such as 612345678.0.
    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    digits = re.sub(r"\D+", "", text)

    # Accept common Spanish prefixes when the remaining number is mobile-sized.
    if len(digits) == 11 and digits.startswith("34"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("0034"):
        digits = digits[4:]

    return digits


def has_repeated_pattern(phone: str) -> bool:
    for size in range(1, (len(phone) // 2) + 1):
        if len(phone) % size == 0:
            pattern = phone[:size]
            if pattern * (len(phone) // size) == phone:
                return True
    return False


def invalid_reasons(phone: str) -> list[str]:
    reasons: list[str] = []

    if not phone:
        return ["vacio"]

    if len(phone) != 9:
        reasons.append("no tiene 9 digitos")

    if phone and phone[0] not in {"6", "7"}:
        reasons.append("no empieza por 6 o 7")

    if REPEATED_DIGITS_RE.fullmatch(phone):
        reasons.append("digitos repetidos")

    # Catches cases like 600000000, 700000000, 611111111...
    if len(phone) == 9 and phone[0] in {"6", "7"} and len(set(phone[1:])) == 1:
        reasons.append("demasiado repetitivo")

    if has_repeated_pattern(phone):
        reasons.append("patron repetido")

    return reasons


def is_valid_phone(phone: str) -> bool:
    return not invalid_reasons(phone)


def format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0,00%"
    return f"{(numerator / denominator) * 100:.2f}%".replace(".", ",")


def phone_for_display(row: pd.Series) -> str:
    normalized = str(row["telefono_normalizado"])
    if normalized:
        return normalized

    original = row["telefono_original"]
    if pd.isna(original) or str(original).strip() == "":
        return "(vacio)"

    return str(original).strip()


def build_analysis(df: pd.DataFrame, agency_col: str, phone_col: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    work = df[[agency_col, phone_col]].copy()
    work.columns = ["agencia", "telefono_original"]
    work["agencia"] = work["agencia"].fillna("Sin agencia").astype(str).str.strip()
    work.loc[work["agencia"].eq(""), "agencia"] = "Sin agencia"
    work["telefono_normalizado"] = work["telefono_original"].apply(normalize_phone)

    work["duplicado_en_agencia"] = work.duplicated(
        subset=["agencia", "telefono_normalizado"],
        keep="first",
    )

    deduped = work.drop_duplicates(subset=["agencia", "telefono_normalizado"], keep="first").copy()
    deduped["motivos"] = deduped["telefono_normalizado"].apply(invalid_reasons)
    deduped["valido"] = deduped["motivos"].apply(lambda reasons: len(reasons) == 0)
    deduped["motivos_texto"] = deduped["motivos"].apply(lambda reasons: ", ".join(reasons))
    deduped["telefono_mostrar"] = deduped.apply(phone_for_display, axis=1)

    totals = work.groupby("agencia", dropna=False).size().rename("telefonos_totales")
    dedup_totals = deduped.groupby("agencia", dropna=False).size().rename("telefonos_deduplicados")
    valid_totals = deduped.groupby("agencia", dropna=False)["valido"].sum().astype(int).rename("telefonos_validos")

    summary = pd.concat([totals, dedup_totals, valid_totals], axis=1).fillna(0).astype(int)
    summary["telefonos_repetidos"] = summary["telefonos_totales"] - summary["telefonos_deduplicados"]
    summary["telefonos_no_validos"] = summary["telefonos_deduplicados"] - summary["telefonos_validos"]
    summary["porcentaje_validos"] = [
        format_percent(valid, deduped_count)
        for valid, deduped_count in zip(summary["telefonos_validos"], summary["telefonos_deduplicados"])
    ]

    invalid_examples = (
        deduped.loc[~deduped["valido"]]
        .groupby("agencia", dropna=False)["telefono_mostrar"]
        .apply(lambda values: ", ".join(values.astype(str).head(20)))
        .rename("ejemplos_no_validos")
    )
    summary = summary.join(invalid_examples).fillna({"ejemplos_no_validos": ""})
    summary = summary.reset_index().sort_values(["telefonos_validos", "agencia"], ascending=[False, True])

    invalids = deduped.loc[
        ~deduped["valido"],
        ["agencia", "telefono_original", "telefono_normalizado", "motivos_texto"],
    ].sort_values(["agencia", "telefono_normalizado"])

    duplicates = work.loc[
        work["duplicado_en_agencia"],
        ["agencia", "telefono_original", "telefono_normalizado"],
    ].sort_values(["agencia", "telefono_normalizado"])

    return summary, invalids, duplicates


def dataframe_to_excel(sheets: dict[str, pd.DataFrame]) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for sheet_name, sheet_df in sheets.items():
            sheet_df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()


def write_analysis_excel(path: str | Path, summary: pd.DataFrame, invalids: pd.DataFrame, duplicates: pd.DataFrame) -> None:
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="resumen")
        invalids.to_excel(writer, index=False, sheet_name="no_validos")
        duplicates.to_excel(writer, index=False, sheet_name="repetidos")


def first_matching_column(columns: Iterable[str], options: tuple[str, ...]) -> str | None:
    normalized = {column.lower().strip(): column for column in columns}
    for option in options:
        if option in normalized:
            return normalized[option]
    return None
