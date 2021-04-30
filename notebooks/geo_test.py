# To add a new cell, type '# %%'
# To add a new markdown cell, type '# %% [markdown]'
# %%
import os
# os.chdir("../")

from wbe_odm import odm
from wbe_odm.odm_mappers import mcgill_mapper, csv_mapper, ledevoir_mapper

import json
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import plotly.express as px

# %% [markdown]
# cases, mortality, recovered, testing, active, avaccine, dvaccine or cvaccine

# %%
PLOTLY_COLORS = px.colors.qualitative.Plotly

COLORS = {
    0: {
        "french": "Pas de données",
        "english": "No Data",
        "color": None},
    1: {
        "french": "Très faible",
        "english": "Very Low",
        "color": "#6da06f"},
    2: {
        "french": "Faible",
        "english": "Low",
        "color": "#b6e9d1"},
    3: {
        "french": "Moyennement élevé",
        "english": "Somewhat high",
        "color": "#ffbb43"},
    4: {
        "french": "Élevé",
        "english": "High",
        "color": "#ff8652"},
    5: {
        "french": "Très élevé",
        "english": "Very high",
        "color": "#c13525"},
}

DATA_FOLDER = "/Users/jeandavidt/OneDrive - Université Laval/COVID/Latest Data"  # noqa
CSV_FOLDER = "/Users/jeandavidt/OneDrive - Université Laval/COVID/Latest Data/odm_csv"  # noqa
QC_STATIC_DATA = os.path.join(DATA_FOLDER,
"Ville de Quebec - All data - v1.1.xlsx")  # noqa
QC_LAB_DATA = os.path.join(DATA_FOLDER,
"CentrEau-COVID_Resultats_Quebec_final.xlsx")  # noqa
QC_SHEET_NAME = "QC Data Daily Samples (McGill)"

MTL_STATIC_DATA = os.path.join(DATA_FOLDER,
"mcgill_static.xlsx")  # noqa
MTL_LAB_DATA = os.path.join(DATA_FOLDER,
"CentrEau-COVID_Resultats_Montreal_final.xlsx")  # noqa
MTL_POLY_SHEET_NAME = "Mtl Data Daily Samples (Poly)"
MTL_MCGILL_SHEET_NAME = "Mtl Data Daily Samples (McGill)"


# %%
RELOAD = False

if RELOAD:
    qc_lab = mcgill_mapper.McGillMapper()
    mcgill_lab = mcgill_mapper.McGillMapper()
    poly_lab = mcgill_mapper.McGillMapper()
    ledevoir = ledevoir_mapper.LeDevoirMapper()

    qc_lab.read(QC_LAB_DATA, QC_STATIC_DATA, QC_SHEET_NAME, "frigon_lab")

    mcgill_lab.read(MTL_LAB_DATA, MTL_STATIC_DATA, MTL_MCGILL_SHEET_NAME, "frigon_lab")  # noqa

    poly_lab.read(MTL_LAB_DATA, MTL_STATIC_DATA, MTL_POLY_SHEET_NAME, "dorner_lab")  # noqa

    ledevoir.read()

    store = odm.Odm()
    store.append_from(qc_lab)
    store.append_from(mcgill_lab)
    store.append_from(poly_lab)
    store.append_from(ledevoir)

    prefix = datetime.now().strftime("%Y-%m-%d")
    store.to_csv(CSV_FOLDER, prefix)
    print(f"Saved to folder {CSV_FOLDER} with prefix \"{prefix}\"")

store = odm.Odm()
from_csv = csv_mapper.CsvMapper()
from_csv.read(CSV_FOLDER)
store.append_from(from_csv)


# %%
def make_point_feature(row, props_to_add):
    return {
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": [row["geoLong"], row["geoLat"]],
            },
        "properties": {
            k: row[k] for k in props_to_add
        }
    }


def get_latest_sample_date(df):
    if len(df) == 0:
        return pd.NaT
    df["plotDate"] = get_plot_datetime(df)
    df = df.sort_values(by="plotDate")
    return df.iloc[-1, df.columns.get_loc("plotDate")]


def get_cm_to_plot(samples, thresh_n):
    # the type to plot depends on:
    # 1) What is the latest collection method for samples at that site
    # 2) How many samples of that cm there are
    possible_cms = ["ps", "cp", "grb"]
    last_dates = []
    n_samples = []
    for cm in possible_cms:
        samples_of_type = samples.loc[
            samples["Sample.collection"].str.contains(cm)
        ]
        n_samples.append(len(samples_of_type))
        last_dates.append(get_latest_sample_date(samples_of_type))
    series = [pd.Series(x) for x in [possible_cms, n_samples, last_dates]]
    types = pd.concat(series, axis=1)
    types.columns = ["type", "n", "last_date"]
    types = types.sort_values("last_date", ascending=True)

    # if there is no colleciton method that has enough
    # samples to satisfy the threshold, that condition is moot
    types = types.loc[~types["last_date"].isna()]
    if len(types.loc[types["n"] >= thresh_n]) == 0:
        thresh_n = 0
    types = types.loc[types["n"] >= thresh_n]
    if len(types) == 0:
        return None
    return types.iloc[-1, types.columns.get_loc("type")]


def get_samples_for_site(site_id, df):
    sample_filter1 = df["Sample.siteID"].str.lower() == site_id.lower()
    return df.loc[sample_filter1].copy()


def get_viral_measures(df):
    cols_to_remove = []
    for col in df.columns:
        l_col = col.lower()
        cond1 = "wwmeasure" in l_col
        cond2 = "covn2" in l_col or 'npmmov' in l_col
        cond3 = "gc" in l_col
        if (cond1 and cond2 and cond3) or "plotdate" in l_col:
            continue
        cols_to_remove.append(col)
    df.drop(columns=cols_to_remove, inplace=True)
    return df


def get_midpoint_time(date1, date2):
    if pd.isna(date1) or pd.isna(date2):
        return pd.NaT
    return date1 + (date2 - date1)/2


def get_plot_datetime(df):
    # grb -> "dateTime"
    # ps and cp -> if start and end are present: midpoint
    # ps and cp -> if only end is present: end
    df["Sample.plotDate"] = pd.NaT
    grb_filt = df["Sample.collection"].str.contains("grb")
    s_filt = ~df["Sample.dateTimeStart"].isna()
    e_filt = ~df["Sample.dateTimeEnd"].isna()

    df.loc[grb_filt, "Sample.plotDate"] = df.loc[grb_filt, "Sample.dateTime"]
    df.loc[s_filt & e_filt, "Sample.plotDate"] = df.apply(
        lambda row: get_midpoint_time(
            row["Sample.dateTimeStart"], row["Sample.dateTimeEnd"]
        ),
        axis=1
    )
    df.loc[
        e_filt & ~s_filt, "Sample.plotDate"] = df.loc[
            e_filt & ~s_filt, "Sample.dateTimeEnd"]
    return df["Sample.plotDate"]


def get_site_list(sites):
    return sites["siteID"].dropna().unique().to_list()


def get_last_sunday(date):
    if date is None:
        date = pd.to_datetime("01-01-2020")
    date = date.to_pydatetime()
    offset = (date.weekday() - 6) % 7
    return date - timedelta(days=offset)


def combine_viral_cols(viral):
    sars = []
    pmmov = []
    for col in viral.columns:
        if "plotDate" in col:
            continue
        _, virus, _, _, _ = col.lower().split("_")
        if "cov" in virus:
            sars.append(col)
        elif "pmmov" in virus:
            pmmov.append(col)
    for name, ls in zip(["sars", "pmmov"], [sars, pmmov]):
        viral[name] = viral[ls].mean(axis=1)
    viral.drop(columns=sars+pmmov, inplace=True)
    return viral


def get_samples_in_interval(samples, dateStart, dateEnd):
    samples
    if pd.isna(dateStart) and pd.isna(dateEnd):
        return samples
    elif pd.isna(dateStart):
        return samples.loc[samples["Sample.plotDate"] <= dateEnd]
    elif pd.isna(dateEnd):
        return samples.loc[samples["Sample.plotDate"] >= dateStart]
    return samples.loc[
        samples["Sample.plotDate"] >= dateStart &
        samples["Sample.plotDate"] <= dateEnd]


def get_samples_to_plot(samples, cm):
    if pd.isna(cm):
        return None
    return samples.loc[
        samples["Sample.collection"].str.contains(cm)]


def get_viral_timeseries(samples):
    viral = get_viral_measures(samples)
    viral = combine_viral_cols(viral)
    viral["norm"] = normalize_by_pmmv(viral)
    return viral


def normalize_by_pmmv(df):
    div = df["sars"] / df["pmmov"]
    div = div.replace([np.inf], np.nan)

    return div[~div.isna()]


def build_empty_color_ts(date_range):
    df = pd.DataFrame(date_range)
    df.columns = ["last_sunday"]
    df["norm"] = np.nan
    return df


DEFAULT_START_DATE = pd.to_datetime("2021-01-01")


def get_n_bins(series, all_colors):
    max_len = len(all_colors)
    len_not_null = len(series[~series.isna()])
    if len_not_null == 0:
        return None
    elif len_not_null < max_len:
        return len_not_null
    return max_len


def get_color_ts(samples, dateStart=DEFAULT_START_DATE, dateEnd=None):
    weekly = None
    if samples is not None:
        viral = get_viral_timeseries(samples)
        if viral is not None:
            viral["last_sunday"] = viral["Sample.plotDate"].apply(
                get_last_sunday)
            weekly = viral.resample("W", on="last_sunday").mean()

    date_range_start = get_last_sunday(dateStart)
    if dateEnd is None:
        dateEnd = pd.to_datetime("now")
    date_range = pd.date_range(start=date_range_start, end=dateEnd, freq="W")
    result = pd.DataFrame(date_range)
    result.columns = ["date"]
    result.sort_values("date", inplace=True)

    if weekly is None:
        weekly = build_empty_color_ts(date_range)
    weekly.sort_values("last_sunday", inplace=True)
    result = pd.merge(
        result,
        weekly,
        left_on="date",
        right_on="last_sunday",
        how="left")

    n_bins = get_n_bins(result["norm"], COLORS)
    if n_bins is None:
        result["signal_strength"] = 0
    elif n_bins == 1:
        result["signal_strength"] = 1
    else:
        result["signal_strength"] = pd.cut(
            result["norm"],
            n_bins,
            labels=range(1, n_bins+1))
    result["signal_strength"] = result["signal_strength"].astype("str")
    result.loc[result["signal_strength"].isna(), "signal_strength"] = "0"
    result["date"] = result["date"].dt.strftime("%Y-%m-%d")
    result.set_index("date", inplace=True)
    return pd.Series(result["signal_strength"]).to_dict()


def get_clean_type(types):
    site_types = {
        "wwtpmuc": "Station de traitement des eaux usées municipale pour égouts combinés",  # noqa
        "pstat": "Station de pompage",
        "ltcf": "Établissement de soins de longue durée",
        "airpln": "Avion",
        "corFcil": "Prison",
        "school": "École",
        "hosptl": "Hôpital",
        "shelter": "Refuge",
        "swgTrck": "Camion de vidange",
        "uCampus": "Campus universitaire",
        "mSwrPpl": "Collecteur d'égouts",
        "holdTnk": "Bassin de stockage",
        "retPond": "Bassin de rétention",
        "wwtpMuS": "Station de traitement des eaux usées municipales pour égouts sanitaires seulement",  # noqa
        "wwtpInd": "Station de traitement des eaux usées industrielle",
        "lagoon": "Système de lagunage pour traitement des eaux usées",
        "septTnk": "Fosse septique.",
        "river": "Rivière",
        "lake": "Lac",
        "estuary": "Estuaire",
        "sea": "Mer",
        "ocean": "Océan",
    }
    return types.str.lower().map(site_types)


def get_municipality(ids):
    municipalities = {
        "qc": "Québec",
        "mtl": "Montréal",
        "lvl": "Laval",
        "tr": "Trois-Rivières",
        "dr": "Drummondville",
        "vc": "Victoriaville",
        "riki": "Rimouski",
        "rdl": "Rivière-du-Loup",
        "stak": "Saint-Alexandre-de-Kamouraska",
        "3p": "Trois-Pistoles",
        "mtn": "Matane"
    }
    city_id = ids.str.lower().apply(lambda x: x.split("_")[0])
    return city_id.map(municipalities)


def clean_collection_method(cm):
    collection = {
        "cp": {
            "french": "Composite",
            "english": "Composite"},
        "grb": {
            "french": "Ponctuel",
            "english": "Grab"},
        "ps": {
            "french": "Passif",
            "english": "Passive"
        }
    }
    return cm.map(collection)


def get_site_geoJSON(
        sites,
        combined,
        site_output_dir,
        site_name,
        dateStart=None,
        dateEnd=None,):
    combined["Sample.plotDate"] = get_plot_datetime(combined)
    sites["samples_for_site"] = sites.apply(
        lambda row: get_samples_for_site(row["siteID"], combined),
        axis=1)
    sites["samples_in_range"] = sites.apply(
        lambda row: get_samples_in_interval(
            row["samples_for_site"], dateStart, dateEnd),
        axis=1)
    sites["collection_method"] = sites.apply(
        lambda row: get_cm_to_plot(
            row["samples_in_range"], thresh_n=7),
        axis=1)
    sites["samples_to_plot"] = sites.apply(
        lambda row: get_samples_to_plot(
            row["samples_in_range"], row["collection_method"]),
        axis=1)
    sites["date_color"] = sites.apply(
        lambda row: get_color_ts(
            row["samples_to_plot"], dateStart, dateEnd),
        axis=1)

    sites["clean_type"] = get_clean_type(sites["type"])
    sites["municipality"] = get_municipality(sites["siteID"])
    sites["collection_method"] = clean_collection_method(
        sites["collection_method"])
    cols_to_keep = [
        "siteID",
        "name",
        "description",
        "clean_type",
        "polygonID",
        "municipality",
        "collection_method",
        "date_color"]
    sites.fillna("", inplace=True)
    sites["features"] = sites.apply(
        lambda row: make_point_feature(row, cols_to_keep), axis=1)
    point_list = list(sites["features"])
    js = {
        "type": "FeatureCollection",
        "features": point_list
    }
    path = os.path.join(site_output_dir, site_name)
    with open(path, "w") as f:
        f.write(json.dumps(js, indent=4))
    return


combined = store.combine_per_sample()
combined = combined[~combined.index.duplicated(keep='first')]
sites = store.site
sites["siteID"] = sites["siteID"].str.lower()
sites = sites.drop_duplicates(subset=["siteID"], keep="first").copy()

SITE_OUTPUT_DIR = ""
SITE_NAME = "sites.geojson"
js = get_site_geoJSON(
        sites,
        combined,
        SITE_OUTPUT_DIR,
        SITE_NAME,
        dateStart=None,
        dateEnd=None)


# %%
def build_polygon_geoJSON(polygons, output_dir, name, types=None):
    for col in ["pop", "link"]:
        if col in polygons.columns:
            polygons.drop(columns=[col], inplace=True)
    polys = store.get_geoJSON(types=types)
    path = os.path.join(output_dir, name)
    with open(path, "w") as f:
        f.write(json.dumps(polys))


polygons = store.polygon
polygons["polygonID"] = polygons["polygonID"].str.lower()
polygons = polygons.drop_duplicates(subset=["polygonID"], keep="first").copy()

POLYGON_OUTPUT_DIR = ""
POLY_NAME = "polygons.geojson"
POLYS_TO_EXTRACT = ["swrCat"]
build_polygon_geoJSON(
    polygons, POLYGON_OUTPUT_DIR, POLY_NAME, POLYS_TO_EXTRACT)
