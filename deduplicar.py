from __future__ import annotations

import re
from io import BytesIO

import pandas as pd


REPEATED_DIGITS_RE = re.compile(r"^(\d)\1+$")


def normalize_phone(value: object) -> str:
    if pd.isna(value):
        return ""

    text = str(value).strip()

    if re.fullmatch(r"\d+\.0", text):
        text = text[:-2]

    digits = re.sub(r"\D+", "", text)

    if len(digits) == 11 and digits.startswith("34"):
        digits = digits[2:]
    elif len(digits) == 13 and digits.startswith("0034"):
        digits = digits[4:]

    return digits


def has_repeated_pattern(phone: str) -> bool:
    for size in range(1, (len(phone) // 2) + 1):
        if len(phone) % size != 0:
            continue

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

    if phone[0] not in {"6", "7"}:
        reasons.append("no empieza por 6 o 7")

    if REPEATED_DIGITS_RE.fullmatch(phone):
        reasons.append("digitos repetidos")

    if len(phone) == 9 and phone[0] in {"6", "7"} and len(set(phone[1:])) == 1:
        reasons.append("demasiado repetitivo")

    if has_repeated_pattern(phone):
        reasons.append("patron repetido")

    return reasons


def phone_for_display(original: object, normalized: str) -> str:
    if normalized:
        return normalized

    if pd.isna(original) or str(original).strip() == "":
        return "(vacio)"

    return str(original).strip()


def format_percent(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return "0.00%"
    return f"{(numerator / denominator) * 100:.2f}%"


def read_file(uploaded_file) -> pd.DataFrame:
    name = uploaded_file.name.lower()

    if name.endswith(".csv"):
        return pd.read_csv(uploaded_file)

    if name.endswith((".xlsx", ".xls")):
        return pd.read_excel(uploaded_file)

    raise ValueError("Sube un archivo CSV, XLSX o XLS.")


def has_value(value: object) -> bool:
    return not pd.isna(value) and str(value).strip() != ""


def analyze_phones(df: pd.DataFrame, agency_col: str, phone_cols: list[str], count_empty_rows: bool = True):
    base_cols = [agency_col] + phone_cols
    base = df[base_cols].copy()
    base["_row_id"] = range(1, len(base) + 1)

    rows = []
    for _, row in base.iterrows():
        agency = row[agency_col]
        phones_found = False

        for phone_col in phone_cols:
            original = row[phone_col]
            if not has_value(original):
                continue

            phones_found = True
            rows.append(
                {
                    "fila": row["_row_id"],
                    "agencia": agency,
                    "columna_telefono": phone_col,
                    "telefono_original": original,
                }
            )

        if count_empty_rows and not phones_found:
            rows.append(
                {
                    "fila": row["_row_id"],
                    "agencia": agency,
                    "columna_telefono": "Sin telefono",
                    "telefono_original": "",
                }
            )

    work = pd.DataFrame(rows)

    if work.empty:
        work = pd.DataFrame(
            columns=[
                "fila",
                "agencia",
                "columna_telefono",
                "telefono_original",
                "telefono_normalizado",
                "es_repetido",
            ]
        )

    work["agencia"] = work["agencia"].fillna("Sin agencia").astype(str).str.strip()
    work.loc[work["agencia"].eq(""), "agencia"] = "Sin agencia"

    work["telefono_normalizado"] = work["telefono_original"].apply(normalize_phone)
    work["clave_deduplicado"] = work.apply(
        lambda row: row["telefono_normalizado"] if row["telefono_normalizado"] else f"__sin_telefono__{row['fila']}",
        axis=1,
    )

    work["es_repetido"] = work.duplicated(
        subset=["agencia", "clave_deduplicado"],
        keep="first",
    )

    deduped = work.loc[~work["es_repetido"]].copy()
    deduped["motivos"] = deduped["telefono_normalizado"].apply(invalid_reasons)
    deduped["es_valido"] = deduped["motivos"].apply(lambda x: len(x) == 0)
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
        .apply(lambda values: ", ".join(values.astype(str).head(30)))
        .rename("telefonos_no_validos_ejemplo")
    )

    summary = summary.join(invalid_examples)
    summary["telefonos_no_validos_ejemplo"] = summary["telefonos_no_validos_ejemplo"].fillna("")
    summary = summary.reset_index()
    summary = summary.sort_values(["telefonos_validos", "agencia"], ascending=[False, True])

    invalids = deduped.loc[
        ~deduped["es_valido"],
        ["fila", "agencia", "columna_telefono", "telefono_original", "telefono_normalizado", "motivos_texto"],
    ].sort_values(["agencia", "telefono_normalizado"])

    duplicates = work.loc[
        work["es_repetido"],
        ["fila", "agencia", "columna_telefono", "telefono_original", "telefono_normalizado"],
    ].sort_values(["agencia", "telefono_normalizado"])

    return summary, invalids, duplicates


def to_excel(summary: pd.DataFrame, invalids: pd.DataFrame, duplicates: pd.DataFrame) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="resumen")
        invalids.to_excel(writer, index=False, sheet_name="no_validos")
        duplicates.to_excel(writer, index=False, sheet_name="repetidos")

    return output.getvalue()


def guess_column(columns: list[str], options: list[str]) -> str | None:
    normalized = {col.lower().strip(): col for col in columns}

    for option in options:
        if option in normalized:
            return normalized[option]

    return None


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Validador de telefonos", layout="wide")

    st.title("Validador de telefonos por agencia")

    uploaded_file = st.file_uploader(
        "Sube un archivo con una columna de agencia y una o varias columnas de telefono",
        type=["csv", "xlsx", "xls"],
    )

    if uploaded_file is None:
        st.info("Sube un CSV o Excel. Despues eliges la columna de agencia y todas las columnas de telefono.")
        st.stop()

    try:
        df = read_file(uploaded_file)
    except Exception as error:
        st.error(f"No he podido leer el archivo: {error}")
        st.stop()

    if df.empty:
        st.warning("El archivo esta vacio.")
        st.stop()

    columns = list(df.columns)

    agency_guess = guess_column(columns, ["agencia", "agency", "fuente", "origen"])
    phone_guesses = [
        col
        for col in columns
        if any(
            keyword in col.lower()
            for keyword in ["telefono", "teléfono", "phone", "movil", "móvil", "mobile"]
        )
    ]

    col1, col2 = st.columns(2)

    with col1:
        agency_col = st.selectbox(
            "Columna de agencia",
            columns,
            index=columns.index(agency_guess) if agency_guess in columns else 0,
        )

    with col2:
        phone_cols = st.multiselect(
            "Columnas de telefono",
            columns,
            default=phone_guesses if phone_guesses else columns[1:2],
        )

    phone_cols = [col for col in phone_cols if col != agency_col]

    if not phone_cols:
        st.error("Elige al menos una columna de telefono distinta a la columna de agencia.")
        st.stop()

    count_empty_rows = st.checkbox(
        "Contar filas sin ningun telefono como no validas",
        value=True,
    )

    summary, invalids, duplicates = analyze_phones(
        df,
        agency_col,
        phone_cols,
        count_empty_rows=count_empty_rows,
    )

    total_phones = int(summary["telefonos_totales"].sum())
    total_deduped = int(summary["telefonos_deduplicados"].sum())
    total_valid = int(summary["telefonos_validos"].sum())
    total_invalid = int(summary["telefonos_no_validos"].sum())

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Telefonos totales", total_phones)
    m2.metric("Telefonos deduplicados", total_deduped)
    m3.metric("Telefonos validos", total_valid)
    m4.metric("Telefonos no validos", total_invalid)

    st.subheader("Resumen por agencia")
    st.dataframe(summary, use_container_width=True, hide_index=True)

    st.subheader("Telefonos no validos")
    if invalids.empty:
        st.success("No hay telefonos no validos.")
    else:
        st.dataframe(invalids, use_container_width=True, hide_index=True)

    st.subheader("Telefonos repetidos")
    if duplicates.empty:
        st.success("No hay telefonos repetidos por agencia.")
    else:
        st.dataframe(duplicates, use_container_width=True, hide_index=True)

    excel_file = to_excel(summary, invalids, duplicates)

    st.download_button(
        label="Descargar resultado en Excel",
        data=excel_file,
        file_name="resultado_telefonos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()
