import os, zipfile, tempfile

import streamlit as st
import pandas as pd
import geopandas as gpd
import pydeck as pdk
import altair as alt

from utils.data_loader import (
    load_airport_mapping,
    load_scenario_data,
    filter_valid_airports,
    get_electricity_kpis,
    get_hydrogen_kpis,
)

# ── File paths ──────────────────────────────────────────────────────────────
EMM_KML            = "data/emm_regions.kml"
CENSUS_KMZ         = "data/us_census_regions.kmz"
TOP20_AIRPORTS_KMZ = "data/top_20_usa_ga_airports.kmz"
AIRPORT_MAP        = "data/airport_region_mapping.xlsx"
SCENARIOS = {
    "Reference": ("data/electricity_reference.xlsx", "data/hydrogen_reference.xlsx"),
    "Low O&G":   ("data/electricity_lowog.xlsx",   "data/hydrogen_lowog.xlsx"),
    "High O&G":  ("data/electricity_highog.xlsx",  "data/hydrogen_highog.xlsx"),
}


def load_kmz(path: str) -> gpd.GeoDataFrame:
    tmp = tempfile.mkdtemp()
    with zipfile.ZipFile(path, "r") as z:
        z.extractall(tmp)
    kml = next(f for f in os.listdir(tmp) if f.lower().endswith(".kml"))
    return gpd.read_file(os.path.join(tmp, kml), driver="KML")


# ── Load GIS layers ─────────────────────────────────────────────────────────
emm_gdf      = gpd.read_file(EMM_KML, driver="KML")
census_gdf   = load_kmz(CENSUS_KMZ)
airports_gdf = load_kmz(TOP20_AIRPORTS_KMZ)


# ── Sidebar: map toggles ─────────────────────────────────────────────────────
st.sidebar.title("Map Layers")
show_emm      = st.sidebar.checkbox("EMM regions",     True)
show_census   = st.sidebar.checkbox("Census regions",  True)
show_airports = st.sidebar.checkbox("Top-20 airports", True)


# ── Build pydeck layers ──────────────────────────────────────────────────────
layers = []
if show_emm:
    layers.append(pdk.Layer(
        "GeoJsonLayer", emm_gdf.__geo_interface__,
        stroked=True, filled=False,
        get_line_color=[0,128,255,200],
        line_width_min_pixels=1
    ))
if show_census:
    layers.append(pdk.Layer(
        "GeoJsonLayer", census_gdf.__geo_interface__,
        stroked=True, filled=False,
        get_line_color=[255,128,0,200],
        line_width_min_pixels=1
    ))
if show_airports:
    layers.append(pdk.Layer(
        "ScatterplotLayer", airports_gdf,
        get_position="[geometry.coordinates[0], geometry.coordinates[1]]",
        get_radius=20000, get_fill_color=[200,0,0,200],
        pickable=True, auto_highlight=True
    ))


# ── Load mapping & scenario data ─────────────────────────────────────────────
airports_df = load_airport_mapping(AIRPORT_MAP)
st.sidebar.title("Dashboard Controls")

scenario      = st.sidebar.selectbox("Scenario", list(SCENARIOS.keys()))
elec_data, hydro_data = load_scenario_data(*SCENARIOS[scenario])

if st.sidebar.button("🔍 Debug Columns"):
    first = list(elec_data.keys())[0]
    st.sidebar.write("Sheet:", first)
    st.sidebar.write(elec_data[first].columns.tolist())


# ── Airport & Year selection ─────────────────────────────────────────────────
valid_airports = filter_valid_airports(
    airports_df,
    elec_regions=set(elec_data),
    hydro_regions=set(hydro_data),
)
airport = st.sidebar.selectbox("Airport", valid_airports["Top 20 GA Airports"].tolist())
year    = st.sidebar.slider("Year", 2024, 2050, 2024)

row         = valid_airports.query("`Top 20 GA Airports` == @airport").iloc[0]
emm_code    = row["EMM Region"]
census_code = row["US Census Region"]

# highlight selected airport
sel = airports_gdf[airports_gdf["Name"] == airport]
if show_airports and not sel.empty:
    layers.append(pdk.Layer(
        "ScatterplotLayer", sel,
        get_position="[geometry.coordinates[0], geometry.coordinates[1]]",
        get_radius=40000, get_fill_color=[0,255,0,200]
    ))

# ── Render map ────────────────────────────────────────────────────────────────
view = pdk.ViewState(latitude=39.0, longitude=-98.5, zoom=3.5)
st.pydeck_chart(pdk.Deck(
    layers=layers,
    initial_view_state=view,
    map_style="mapbox://styles/mapbox/light-v9",
    tooltip={"text":"{Name}"}
))


# ── Compute KPIs ─────────────────────────────────────────────────────────────
elec_kpis  = get_electricity_kpis(elec_data[emm_code], year)
hydro_kpis = get_hydrogen_kpis(
    hydro_data[census_code],
    year,
    elec_emission_factor=elec_kpis["emissions_per_kwh"]["CO2"]
)


# ── Page header ──────────────────────────────────────────────────────────────
st.title(f"{airport} — {scenario} — {year}")


# ── Electricity CO₂ intensity ───────────────────────────────────────────────
st.subheader("⚡ Electricity Sector")
st.metric("CO₂ Emissions (g/kWh)",
          f"{elec_kpis['emissions_per_kwh']['CO2']:.1f}")


# ── Electricity mix pie chart ───────────────────────────────────────────────
df_e     = elec_data[emm_code]
year_col = str(year)

desc = df_e["Description"].tolist()
start = desc.index("Coal")
end   = desc.index("Total Generation")
fuel_df = df_e.iloc[start:end][["Description", year_col]].dropna()
fuel_df["pct"] = fuel_df[year_col] / fuel_df[year_col].sum()

pie_e = (
    alt.Chart(fuel_df)
       .mark_arc(innerRadius=50)
       .encode(
           theta=alt.Theta("pct:Q", title="Share"),
           color=alt.Color("Description:N", legend=alt.Legend(title="Source")),
           tooltip=[
             alt.Tooltip("Description:N"),
             alt.Tooltip("pct:Q", format=".1%")
           ]
       )
)
st.altair_chart(pie_e, use_container_width=True)


# ── Hydrogen production by technology ────────────────────────────────────────
st.subheader("💧 Hydrogen Production by Technology")

df_h  = hydro_data[census_code]
desc2 = df_h["Description"].tolist()
s0    = desc2.index("Steam Methane Reforming")
s1    = desc2.index("Total Hydrogen Supply")
tech_df = df_h.iloc[s0:s1][["Description", year_col]]

tbl = tech_df.set_index("Description")
tbl.columns = [f"TBtu ({year})"]
st.table(tbl)

pie_h = (
    alt.Chart(tech_df.assign(
        pct=lambda d: d[year_col] / d[year_col].sum()
    ))
    .mark_arc(innerRadius=50)
    .encode(
        theta=alt.Theta("pct:Q"),
        color=alt.Color("Description:N"),
        tooltip=[
          alt.Tooltip("Description:N"),
          alt.Tooltip("pct:Q", format=".1%")
        ]
    )
)
st.altair_chart(pie_h, use_container_width=True)


# ── Hydrogen cost summary ───────────────────────────────────────────────────
st.subheader("💲 Hydrogen Cost Summary")

# spot price
spot = df_h.loc[
    df_h["Description"].str.contains("Spot Price|Average Market Spot", na=False),
    year_col
].astype(float).iloc[0]

# delivered prices
deliv = (
    df_h[df_h["Category"] == "Delivered End-Use Prices ($/MMBtu)"]
      .set_index("Description")[year_col]
      .astype(float)
)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Spot Price", f"${spot:.2f}/MMBtu")
for (desc, val), col in zip(deliv.items(), (c2, c3, c4)):
    col.metric(f"{desc}", f"${val:.2f}/MMBtu")


# ── Electricity cost summary ────────────────────────────────────────────────
st.subheader("💲 Electricity Cost Summary")

# real (all sectors average)
real_ts = (
    df_e[df_e["Description"] == "All Sectors Average"]
      .filter(regex=r"^\d{4}$")
      .T
      .reset_index(name="Real Price (cents/kWh)")
      .rename(columns={"index":"Year"})
)
line_real = (
    alt.Chart(real_ts)
       .mark_line(point=True)
       .encode(
           x="Year:O",
           y="Real Price (cents/kWh):Q",
           tooltip=["Year","Real Price (cents/kWh)"]
       )
)
st.altair_chart(line_real, use_container_width=True)

# nominal (if still present)
if "Nominal" in df_e.columns or df_e.columns.str.contains("nominal", case=False).any():
    nom_ts = (
        df_e[df_e["Description"] == "All Sectors Average"]
          .filter(regex=r"^\d{4}$")
          .T
          .reset_index(name="Nominal Price (cents/kWh)")
          .rename(columns={"index":"Year"})
    )
    line_nom = (
        alt.Chart(nom_ts)
           .mark_line(point=True, color="orange")
           .encode(x="Year:O", y="Nominal Price (cents/kWh):Q")
    )
    st.altair_chart(line_nom, use_container_width=True)
else:
    st.caption("No nominal series found.")
