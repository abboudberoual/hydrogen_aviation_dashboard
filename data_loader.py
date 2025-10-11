import pandas as pd

# ── Conversion constants ────────────────────────────────────────────────────
TBTU_TO_KWH       = 2.93e8    # 1 TBtu = 2.93×10^8 kWh
H2_LHV_KWH_PER_KG = 33.33     # Lower heating value of H₂ in kWh/kg

def load_airport_mapping(path: str) -> pd.DataFrame:
    """Load airport→region mapping (expects Top 20 GA Airports, EMM Region, US Census Region)."""
    return pd.read_excel(path)


def load_scenario_data(elec_path: str, hydro_path: str):
    """
    Reads & cleans all sheets from the two workbooks.
    Returns: ( elec_dict, hydro_dict ), each a mapping
      { region_code : DataFrame(Category,Description,2024…2050,Growth) }
    """
    return _clean(elec_path), _clean(hydro_path)


def _clean(path: str) -> dict:
    raw = pd.read_excel(path, sheet_name=None, header=0, dtype=str)
    out = {}

    for region, df in raw.items():
        # 1) drop fully blank rows
        df = df.dropna(how="all")

        # 2) strip whitespace from column names
        df.columns = [str(c).strip() for c in df.columns]

        # 3) drop the Units column if present
        if "Units" in df.columns:
            df = df.drop(columns="Units")

        # 4) coerce years + Growth → numeric
        for c in df.columns:
            if c not in ("Category", "Description"):
                df[c] = pd.to_numeric(df[c], errors="coerce")

        # 5) drop any row where _all_ the numeric columns are NaN
        num_cols = [c for c in df.columns if c not in ("Category", "Description")]
        df = df[df[num_cols].notna().any(axis=1)].reset_index(drop=True)

        # 6) trim whitespace on Description
        df["Description"] = df["Description"].astype(str).str.strip()

        out[region] = df

    return out


def extract_year_data(df: pd.DataFrame, year: int) -> pd.Series:
    """
    From a cleaned sheet, return the Series for that year, indexed by Description.
    """
    df2 = df.set_index("Description")
    key = str(year)
    if key not in df2.columns:
        raise KeyError(f"Year {key} not found; available columns: {df2.columns.tolist()}")
    return df2[key]


def get_electricity_kpis(region_df: pd.DataFrame, year: int) -> dict:
    s = extract_year_data(region_df, year)

    # total generation
    try:
        total_bkwh = s.loc["Total Generation"]
    except KeyError:
        raise KeyError(f"'Total Generation' row not found; descriptions={list(s.index)}")
    total_kwh = total_bkwh * 1e9

    # emissions g/kWh
    pollutant_map = {
        "CO2": "Carbon Dioxide (million short tons)",
        "SO2": "Sulfur Dioxide (million short tons)",
        "NOx": "Nitrogen Oxide (million short tons)",
    }
    emissions_per_kwh = {}
    for short, full in pollutant_map.items():
        mt    = s.loc[full]                # million short tons
        grams = mt * 1e6 * 907185          # → grams
        emissions_per_kwh[short] = grams / total_kwh

    # generation mix (% of total)
    exclude = set(pollutant_map.values()) | {
        "Total Generation", "Sales to Customers", "Generation for Own Use", "SUM", "DIFFERENCE"
    }
    sources = [desc for desc in s.index if desc not in exclude]
    mix_pct = s.loc[sources] / total_bkwh

    return {
        "total_gen_kwh":     total_kwh,
        "emissions_per_kwh": emissions_per_kwh,
        "mix_pct":           mix_pct,
    }


def get_hydrogen_kpis(region_df: pd.DataFrame,
                      year: int,
                      elec_emission_factor: float = None) -> dict:
    s = extract_year_data(region_df, year)

    # production rows (contains “Production”)
    prod_rows = [d for d in s.index if "production" in d.lower()]
    prod_tbtu = s.loc[prod_rows]

    # input rows (exact match)
    input_tbtu = s.loc[["Natural Gas","Purchased Electricity"]]

    # convert TBtu→kWh→kg
    prod_kwh   = prod_tbtu * TBTU_TO_KWH
    mass_kg    = prod_kwh  / H2_LHV_KWH_PER_KG

    # energy inputs kWh/kg
    input_kwh      = input_tbtu * TBTU_TO_KWH
    energy_per_kg  = input_kwh.div(mass_kg, axis=0)

    # optional emissions per kg
    emis_per_kg = None
    if elec_emission_factor is not None and "Purchased Electricity" in energy_per_kg.index:
        emis_per_kg = energy_per_kg.loc["Purchased Electricity"] * elec_emission_factor

    return {
        "production_tbtu":         prod_tbtu,
        "energy_input_tbtu":       input_tbtu,
        "energy_input_kwh_per_kg": energy_per_kg,
        "emissions_per_kg":        emis_per_kg,
    }


def filter_valid_airports(air_df: pd.DataFrame,
                          elec_regions: set,
                          hydro_regions: set) -> pd.DataFrame:
    return air_df[
        air_df["EMM Region"].isin(elec_regions) &
        air_df["US Census Region"].isin(hydro_regions)
    ]
