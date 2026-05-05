from __future__ import annotations

import re
from io import BytesIO

import pandas as pd


REPEATED_DIGITS_RE = re.compile(r"^(\d)\1+$")
EMAIL_RE = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+$")
VALID_START_DIGITS = {"6", "7", "8", "9"}
DEFAULT_BLOCKED_EMAIL_DOMAINS = "fakedbleads.com"


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


def has_valid_spanish_prefix(phone: str) -> bool:
    if len(phone) != 9:
        return False

    return phone[0] in VALID_START_DIGITS


def invalid_reasons(phone: str) -> list[str]:
    reasons: list[str] = []

    if not phone:
        return ["vacio"]

    if len(phone) != 9:
        reasons.append("no tiene 9 digitos")

    if not has_valid_spanish_prefix(phone):
        reasons.append("no empieza por 6, 7, 8 o 9")

    if REPEATED_DIGITS_RE.fullmatch(phone):
        reasons.append("digitos repetidos")

    if len(phone) == 9 and len(set(phone[1:])) == 1:
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


def split_email_values(value: object) -> list[str]:
    if not has_value(value):
        return []

    text = str(value).strip()
    return [part.strip().lower() for part in re.split(r"[,;\s]+", text) if part.strip()]


def normalize_domain(domain: str) -> str:
    domain = domain.strip().lower()
    domain = re.sub(r"^https?://", "", domain)
    domain = domain.removeprefix("www.")
    domain = domain.removeprefix("@")
    return domain.strip().strip("/")


def parse_blocked_domains(text: str) -> set[str]:
    domains = set()
    for part in re.split(r"[,;\s]+", text):
        domain = normalize_domain(part)
        if domain:
            domains.add(domain)
    return domains


def invalid_email_reasons(email: str, blocked_domains: set[str]) -> list[str]:
    reasons: list[str] = []
    email = str(email).strip().lower()

    if not email:
        return ["vacio"]

    domain = email.split("@")[-1] if "@" in email else email
    domain = normalize_domain(domain)

    if any(domain == blocked or domain.endswith(f".{blocked}") for blocked in blocked_domains):
        reasons.append("dominio bloqueado")

    if not EMAIL_RE.fullmatch(email):
        reasons.append("formato email invalido")

    return reasons


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


def analyze_phones(
    df: pd.DataFrame,
    agency_col: str,
    phone_cols: list[str],
    deal_id_col: str | None = None,
    email_cols: list[str] | None = None,
    blocked_email_domains: set[str] | None = None,
):
    email_cols = email_cols or []
    blocked_email_domains = blocked_email_domains or set()
    base_cols = list(
        dict.fromkeys(
            [agency_col]
            + phone_cols
            + email_cols
            + ([deal_id_col] if deal_id_col else [])
        )
    )
    base = df[base_cols].copy()
    base["_row_id"] = range(1, len(base) + 1)

    seen_valid_phones: dict[tuple[str, str], dict[str, object]] = {}
    lead_rows = []
    invalid_email_rows = []

    for _, row in base.iterrows():
        agency = row[agency_col]
        if pd.isna(agency) or str(agency).strip() == "":
            agency = "Sin agencia"
        else:
            agency = str(agency).strip()

        deal_id = row[deal_id_col] if deal_id_col else ""
        if pd.isna(deal_id):
            deal_id = ""

        candidates = []
        for phone_col in phone_cols:
            original = row[phone_col]
            if not has_value(original):
                continue

            normalized = normalize_phone(original)
            reasons = invalid_reasons(normalized)
            candidates.append(
                {
                    "fila": int(row["_row_id"]),
                    "agencia": agency,
                    "columna": phone_col,
                    "telefono_original": original,
                    "telefono_normalizado": normalized,
                    "telefono_mostrar": phone_for_display(original, normalized),
                    "motivos": reasons,
                    "es_valido": len(reasons) == 0,
                }
            )

        email_candidates = []
        for email_col in email_cols:
            original_email = row[email_col]
            for email in split_email_values(original_email):
                email_reasons = invalid_email_reasons(email, blocked_email_domains)
                email_candidate = {
                    "fila": int(row["_row_id"]),
                    "deal_id": deal_id,
                    "agencia": agency,
                    "columna_email": email_col,
                    "email": email,
                    "motivos_texto": ", ".join(email_reasons),
                    "es_valido": len(email_reasons) == 0,
                }
                email_candidates.append(email_candidate)

                if email_reasons:
                    invalid_email_rows.append(email_candidate)

        valid_candidates = []
        phones_seen_in_this_lead = set()
        for candidate in candidates:
            normalized = candidate["telefono_normalizado"]
            if not candidate["es_valido"] or normalized in phones_seen_in_this_lead:
                continue
            valid_candidates.append(candidate)
            phones_seen_in_this_lead.add(normalized)

        new_valid_candidates = [
            candidate
            for candidate in valid_candidates
            if (agency, str(candidate["telefono_normalizado"])) not in seen_valid_phones
        ]
        repeated_candidates = [
            candidate
            for candidate in valid_candidates
            if (agency, str(candidate["telefono_normalizado"])) in seen_valid_phones
        ]

        if new_valid_candidates:
            status = "valido_unico"
            main_valid = new_valid_candidates[0]
            repeated = None
        elif repeated_candidates:
            status = "repetido"
            main_valid = repeated_candidates[0]
            repeated = seen_valid_phones[(agency, str(main_valid["telefono_normalizado"]))]
        else:
            status = "no_valido"
            main_valid = None
            repeated = None

        for candidate in valid_candidates:
            key = (agency, str(candidate["telefono_normalizado"]))
            if key not in seen_valid_phones:
                seen_valid_phones[key] = {
                    "fila_original": candidate["fila"],
                    "deal_id_original": deal_id,
                    "columna_original": candidate["columna"],
                    "telefono_original_primero": candidate["telefono_original"],
                }

        if not candidates:
            phones_text = "sin telefono"
            invalid_reason_text = "sin telefono en las columnas seleccionadas"
        else:
            phones_text = "; ".join(
                f"{candidate['columna']}: {candidate['telefono_mostrar']}"
                for candidate in candidates
            )
            invalid_reason_text = "; ".join(
                f"{candidate['columna']}: {candidate['telefono_mostrar']} ({', '.join(candidate['motivos'])})"
                for candidate in candidates
                if not candidate["es_valido"]
            )

        if not email_candidates:
            emails_text = ""
            invalid_email_reason_text = ""
        else:
            emails_text = "; ".join(
                f"{candidate['columna_email']}: {candidate['email']}"
                for candidate in email_candidates
            )
            invalid_email_reason_text = "; ".join(
                f"{candidate['columna_email']}: {candidate['email']} ({candidate['motivos_texto']})"
                for candidate in email_candidates
                if not candidate["es_valido"]
            )

        lead_rows.append(
            {
                "fila": int(row["_row_id"]),
                "deal_id": deal_id,
                "agencia": agency,
                "estado": status,
                "tiene_algun_telefono": len(candidates) > 0,
                "tiene_telefono_valido": len(valid_candidates) > 0,
                "telefono_valido": main_valid["telefono_normalizado"] if main_valid else "",
                "columna_telefono_valido": main_valid["columna"] if main_valid else "",
                "telefonos_encontrados": phones_text,
                "motivo_no_valido": invalid_reason_text,
                "telefono_repetido": main_valid["telefono_normalizado"] if status == "repetido" else "",
                "columna_telefono_repetido": main_valid["columna"] if status == "repetido" else "",
                "fila_original_repetido": repeated["fila_original"] if repeated else "",
                "deal_id_original_repetido": repeated["deal_id_original"] if repeated else "",
                "columna_original_repetido": repeated["columna_original"] if repeated else "",
                "tiene_email_invalido": any(not candidate["es_valido"] for candidate in email_candidates),
                "emails_encontrados": emails_text,
                "motivo_email_invalido": invalid_email_reason_text,
            }
        )

    leads = pd.DataFrame(lead_rows)
    invalid_emails = pd.DataFrame(
        invalid_email_rows,
        columns=["fila", "deal_id", "agencia", "columna_email", "email", "motivos_texto", "es_valido"],
    )

    summary = leads.groupby("agencia").agg(
        leads_totales=("fila", "count"),
        leads_con_algun_telefono=("tiene_algun_telefono", "sum"),
        leads_con_telefono_valido=("tiene_telefono_valido", "sum"),
        leads_con_email_invalido=("tiene_email_invalido", "sum"),
    )
    summary = summary.astype(int)
    summary["leads_sin_telefono"] = summary["leads_totales"] - summary["leads_con_algun_telefono"]
    summary["leads_validos_unicos"] = leads.groupby("agencia")["estado"].apply(lambda values: (values == "valido_unico").sum())
    summary["leads_repetidos"] = leads.groupby("agencia")["estado"].apply(lambda values: (values == "repetido").sum())
    summary["leads_no_validos"] = leads.groupby("agencia")["estado"].apply(lambda values: (values == "no_valido").sum())
    summary = summary.fillna(0).astype(int)
    summary["porcentaje_validos_unicos"] = [
        format_percent(valid, total)
        for valid, total in zip(summary["leads_validos_unicos"], summary["leads_totales"])
    ]

    invalid_examples = (
        leads.loc[leads["estado"] == "no_valido"]
        .groupby("agencia")["telefonos_encontrados"]
        .apply(lambda values: ", ".join(values.astype(str).head(20)))
        .rename("ejemplos_no_validos")
    )
    summary = summary.join(invalid_examples)
    summary["ejemplos_no_validos"] = summary["ejemplos_no_validos"].fillna("")
    summary = summary.reset_index()
    summary = summary.sort_values(["leads_validos_unicos", "agencia"], ascending=[False, True])

    invalids = leads.loc[
        leads["estado"] == "no_valido",
        ["fila", "deal_id", "agencia", "telefonos_encontrados", "motivo_no_valido"],
    ].sort_values(["agencia", "fila"])

    duplicates = leads.loc[
        leads["estado"] == "repetido",
        [
            "fila",
            "deal_id",
            "agencia",
            "telefono_repetido",
            "columna_telefono_repetido",
            "fila_original_repetido",
            "deal_id_original_repetido",
            "columna_original_repetido",
            "telefonos_encontrados",
        ],
    ].sort_values(["agencia", "telefono_repetido", "fila"])

    invalid_emails = invalid_emails.drop(columns=["es_valido"], errors="ignore")
    invalid_emails = invalid_emails.sort_values(["agencia", "fila", "email"])

    return summary, invalids, duplicates, invalid_emails


def to_excel(
    summary: pd.DataFrame,
    invalids: pd.DataFrame,
    duplicates: pd.DataFrame,
    invalid_emails: pd.DataFrame,
) -> bytes:
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        summary.to_excel(writer, index=False, sheet_name="resumen")
        invalids.to_excel(writer, index=False, sheet_name="no_validos")
        duplicates.to_excel(writer, index=False, sheet_name="repetidos")
        invalid_emails.to_excel(writer, index=False, sheet_name="emails_no_validos")

    return output.getvalue()


def guess_column(columns: list[str], options: list[str]) -> str | None:
    normalized = {col.lower().strip(): col for col in columns}

    for option in options:
        if option in normalized:
            return normalized[option]

    return None


def guess_column_contains(columns: list[str], options: list[str]) -> str | None:
    for col in columns:
        normalized = col.lower().strip()
        if any(option in normalized for option in options):
            return col

    return None


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="Validador de leads", layout="wide")

    st.title("Validador de leads por agencia")

    uploaded_file = st.file_uploader(
        "Sube un archivo con agencia, telefonos y emails",
        type=["csv", "xlsx", "xls"],
    )

    if uploaded_file is None:
        st.info("Sube un CSV o Excel. Despues eliges la columna de agencia, telefonos y emails.")
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
    deal_id_guess = guess_column_contains(
        columns,
        ["deal id", "deal_id", "id deal", "negocio id", "id negocio"],
    )
    phone_guesses = [
        col
        for col in columns
        if any(
            keyword in col.lower()
            for keyword in ["telefono", "teléfono", "phone", "movil", "móvil", "mobile"]
        )
    ]
    email_guesses = [
        col
        for col in columns
        if any(
            keyword in col.lower()
            for keyword in ["correo", "email", "mail", "e-mail", "electr"]
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
        deal_id_options = ["No incluir Deal ID"] + columns
        deal_id_col_selected = st.selectbox(
            "Columna de Deal ID",
            deal_id_options,
            index=deal_id_options.index(deal_id_guess) if deal_id_guess in deal_id_options else 0,
        )

    deal_id_col = None if deal_id_col_selected == "No incluir Deal ID" else deal_id_col_selected

    phone_cols = st.multiselect(
        "Columnas de telefono",
        columns,
        default=phone_guesses if phone_guesses else columns[1:2],
    )

    phone_cols = [col for col in phone_cols if col not in {agency_col, deal_id_col}]

    if not phone_cols:
        st.error("Elige al menos una columna de telefono distinta a la columna de agencia.")
        st.stop()

    summary, invalids, duplicates = analyze_phones(
        df,
        agency_col,
        phone_cols,
        deal_id_col=deal_id_col,
    )

    agency_options = ["Todas las agencias"] + sorted(summary["agencia"].astype(str).unique().tolist())
    selected_agency = st.selectbox("Filtrar por agencia", agency_options)

    if selected_agency == "Todas las agencias":
        filtered_summary = summary
        filtered_invalids = invalids
        filtered_duplicates = duplicates
        file_suffix = "todas"
    else:
        filtered_summary = summary.loc[summary["agencia"] == selected_agency]
        filtered_invalids = invalids.loc[invalids["agencia"] == selected_agency]
        filtered_duplicates = duplicates.loc[duplicates["agencia"] == selected_agency]
        file_suffix = re.sub(r"[^A-Za-z0-9_-]+", "_", selected_agency).strip("_").lower() or "agencia"

    total_leads = int(filtered_summary["leads_totales"].sum())
    leads_with_valid_phone = int(filtered_summary["leads_con_telefono_valido"].sum())
    unique_valid_leads = int(filtered_summary["leads_validos_unicos"].sum())
    repeated_leads = int(filtered_summary["leads_repetidos"].sum())
    invalid_leads = int(filtered_summary["leads_no_validos"].sum())

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Leads totales", total_leads)
    m2.metric("Leads con telefono valido", leads_with_valid_phone)
    m3.metric("Leads validos unicos", unique_valid_leads)
    m4.metric("Leads repetidos", repeated_leads)
    m5.metric("Leads no validos", invalid_leads)

    st.subheader("Resumen por agencia")
    st.dataframe(filtered_summary, use_container_width=True, hide_index=True)

    st.subheader("Leads no validos")
    if filtered_invalids.empty:
        st.success("No hay leads no validos.")
    else:
        st.dataframe(filtered_invalids, use_container_width=True, hide_index=True)
        st.download_button(
            label="Descargar no validos filtrados",
            data=filtered_invalids.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"no_validos_{file_suffix}.csv",
            mime="text/csv",
        )

    st.subheader("Leads repetidos por telefono")
    if filtered_duplicates.empty:
        st.success("No hay leads repetidos por telefono dentro de la misma agencia.")
    else:
        st.dataframe(filtered_duplicates, use_container_width=True, hide_index=True)

    excel_file = to_excel(filtered_summary, filtered_invalids, filtered_duplicates)

    st.download_button(
        label="Descargar resultado filtrado en Excel",
        data=excel_file,
        file_name=f"resultado_telefonos_{file_suffix}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()
