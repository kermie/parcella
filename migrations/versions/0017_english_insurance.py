"""Versicherungsmodul auf Englisch umstellen (PropertyInsurance/AccidentInsurance)

Revision ID: 0017_english_insurance
Revises: 0016_english_metering
Create Date: 2026-07-12
"""
from typing import Union
from alembic import op
import sqlalchemy as sa

revision: str = "0017_english_insurance"
down_revision: Union[str, None] = "0016_english_metering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Tabellen umbenennen
    # -----------------------------------------------------------------
    op.rename_table("sachversicherung_pakete", "property_insurance_packages")
    op.rename_table("versicherungs_konfiguration", "insurance_configuration")
    op.rename_table("parzelle_versicherung", "parcel_insurance")
    op.rename_table("unfallversicherung_zusatzpersonen", "accident_insurance_additional_persons")

    # -----------------------------------------------------------------
    # 2. Spalten in property_insurance_packages umbenennen
    # -----------------------------------------------------------------
    op.alter_column("property_insurance_packages", "jahr", new_column_name="year")
    op.alter_column("property_insurance_packages", "bezeichnung", new_column_name="name")
    op.alter_column("property_insurance_packages", "betrag_eur", new_column_name="amount_eur")
    op.alter_column("property_insurance_packages", "reihenfolge", new_column_name="sort_order")

    # -----------------------------------------------------------------
    # 3. Spalten in insurance_configuration umbenennen
    # -----------------------------------------------------------------
    op.alter_column("insurance_configuration", "jahr", new_column_name="year")
    op.alter_column("insurance_configuration", "unfall_grundbetrag_eur", new_column_name="accident_base_amount_eur")
    op.alter_column("insurance_configuration", "unfall_zusatzbetrag_eur", new_column_name="accident_additional_amount_eur")

    # -----------------------------------------------------------------
    # 4. Spalten in parcel_insurance umbenennen
    #    (parzelle_id -> parcel_id: FK-Spaltenumbenennung, die beim
    #    Kernmodul-Umbau bewusst zurückgestellt wurde, siehe 0014)
    # -----------------------------------------------------------------
    op.alter_column("parcel_insurance", "parzelle_id", new_column_name="parcel_id")
    op.alter_column("parcel_insurance", "jahr", new_column_name="year")
    op.alter_column("parcel_insurance", "hat_sachversicherung", new_column_name="has_property_insurance")
    op.alter_column("parcel_insurance", "sach_paket_id", new_column_name="property_package_id")
    op.alter_column("parcel_insurance", "hat_unfallversicherung", new_column_name="has_accident_insurance")

    op.execute("ALTER TABLE parcel_insurance DROP CONSTRAINT IF EXISTS uq_parzelle_versicherung_jahr")
    op.create_unique_constraint("uq_parcel_insurance_year", "parcel_insurance", ["parcel_id", "year"])

    # -----------------------------------------------------------------
    # 5. Spalten in accident_insurance_additional_persons umbenennen
    #    (mitglied_id -> member_id, ebenfalls zurueckgestellte FK-Spalte)
    # -----------------------------------------------------------------
    op.alter_column(
        "accident_insurance_additional_persons", "parzelle_versicherung_id",
        new_column_name="parcel_insurance_id",
    )
    op.alter_column("accident_insurance_additional_persons", "mitglied_id", new_column_name="member_id")

    op.execute(
        "ALTER TABLE accident_insurance_additional_persons "
        "DROP CONSTRAINT IF EXISTS uq_versicherung_mitglied"
    )
    op.create_unique_constraint(
        "uq_insurance_member", "accident_insurance_additional_persons",
        ["parcel_insurance_id", "member_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_insurance_member", "accident_insurance_additional_persons", type_="unique")
    op.create_unique_constraint(
        "uq_versicherung_mitglied", "accident_insurance_additional_persons",
        ["parzelle_versicherung_id", "mitglied_id"],
    )
    op.alter_column("accident_insurance_additional_persons", "member_id", new_column_name="mitglied_id")
    op.alter_column(
        "accident_insurance_additional_persons", "parcel_insurance_id",
        new_column_name="parzelle_versicherung_id",
    )

    op.drop_constraint("uq_parcel_insurance_year", "parcel_insurance", type_="unique")
    op.create_unique_constraint("uq_parzelle_versicherung_jahr", "parcel_insurance", ["parzelle_id", "jahr"])

    op.alter_column("parcel_insurance", "has_accident_insurance", new_column_name="hat_unfallversicherung")
    op.alter_column("parcel_insurance", "property_package_id", new_column_name="sach_paket_id")
    op.alter_column("parcel_insurance", "has_property_insurance", new_column_name="hat_sachversicherung")
    op.alter_column("parcel_insurance", "year", new_column_name="jahr")
    op.alter_column("parcel_insurance", "parcel_id", new_column_name="parzelle_id")

    op.alter_column("insurance_configuration", "accident_additional_amount_eur", new_column_name="unfall_zusatzbetrag_eur")
    op.alter_column("insurance_configuration", "accident_base_amount_eur", new_column_name="unfall_grundbetrag_eur")
    op.alter_column("insurance_configuration", "year", new_column_name="jahr")

    op.alter_column("property_insurance_packages", "sort_order", new_column_name="reihenfolge")
    op.alter_column("property_insurance_packages", "amount_eur", new_column_name="betrag_eur")
    op.alter_column("property_insurance_packages", "name", new_column_name="bezeichnung")
    op.alter_column("property_insurance_packages", "year", new_column_name="jahr")

    op.rename_table("accident_insurance_additional_persons", "unfallversicherung_zusatzpersonen")
    op.rename_table("parcel_insurance", "parzelle_versicherung")
    op.rename_table("insurance_configuration", "versicherungs_konfiguration")
    op.rename_table("property_insurance_packages", "sachversicherung_pakete")
