import json
import os
import sqlite3

import numpy as np
import pandas as pd
import requests
from shapely import wkt
from shapely.geometry import Point

from wbe_odm import utilities
from wbe_odm.odm_mappers import base_mapper, csv_mapper, ledevoir_mapper, mcgill_mapper

# Set pandas to raise en exception when using chained assignment,
# as that may lead to values being set on a view of the data
# instead of on the data itself.
pd.options.mode.chained_assignment = 'raise'


class Odm:
    """Data class that holds the contents of the
    tables defined in the Ottawa Data Model (ODM).
    The tables are stored as pandas DataFrames. Utility
    functions are provided to manipulate the data for further analysis.
    """
    def __init__(
        self,
        sample=pd.DataFrame(
            columns=utilities.get_table_fields("Sample")),
        ww_measure=pd.DataFrame(
            columns=utilities.get_table_fields("WWMeasure")),
        site=pd.DataFrame(
            columns=utilities.get_table_fields("Site")),
        site_measure=pd.DataFrame(
            columns=utilities.get_table_fields("SiteMeasure")),
        reporter=pd.DataFrame(
            columns=utilities.get_table_fields("Reporter")),
        lab=pd.DataFrame(
            columns=utilities.get_table_fields("Lab")),
        assay_method=pd.DataFrame(
            columns=utilities.get_table_fields("AssayMethod")),
        instrument=pd.DataFrame(
            columns=utilities.get_table_fields("Instrument")),
        polygon=pd.DataFrame(
            columns=utilities.get_table_fields("Polygon")),
        cphd=pd.DataFrame(
            columns=utilities.get_table_fields("CPHD")),
            ) -> None:

        self.sample = sample
        self.ww_measure = ww_measure
        self.site = site
        self.site_measure = site_measure
        self.reporter = reporter
        self.lab = lab
        self.assay_method = assay_method
        self.instrument = instrument
        self.polygon = polygon
        self.cphd = cphd

    def _default_value_by_dtype(
        self, dtype: str
            ):
        """gets you a default value of the correct data type to create new
        columns in a pandas DataFrame

        Parameters
        ----------
        dtype : str
            string name of the data type (found with df[column].dtype)

        Returns
        -------
        [pd.NaT, np.nan, str, None]
            The corresponding default value
        """
        null_values = {
            "datetime64[ns]": pd.NaT,
            "float64": np.nan,
            "int64": np.nan,
            "object": ""
        }
        return null_values.get(dtype, np.nan)

    def clean_qualifier_columns(self, df, qualifiers):
        if df.empty:
            return df
        for qualifier in qualifiers:
            filt1 = df[qualifier].isna()
            filt2 = df[qualifier] == ""
            df.loc[filt1 | filt2, qualifier] = f"unknown-{qualifier}"
            df[qualifier] = df[qualifier].str.replace("/", "-")
            if qualifier == "qualityFlag":
                df[qualifier] = df[qualifier].str\
                    .replace("True", "quality-issue")\
                    .replace("False", "no-quality-issue")
        return df

    def widen(self, df, features, qualifiers):
        """Takes important characteristics inside a table (features) and
        creates new columns to store them based on the value of other columns
        (qualifiers).

        Parameters
        ----------
        df : pd.DataFrame
            The DataFrame we are operating on.
        features : list[str]
            List of column names that contain the features to extract.
        qualifiers : list[str]
            List of column names that contain the qualifying information.

        Returns
        -------
        pd.DataFrame
            DataFrame with the original feature and qualifier columns removed
            and the features spread out over new columns named after the values
            of the qualifier columns.
        """
        if df.empty:
            return df
        df = self.clean_qualifier_columns(df, qualifiers)
        for qualifier in qualifiers:
            df[qualifier] = df[qualifier].astype(str)
        df["col_qualifiers"] = df[qualifiers].agg("_".join, axis=1)
        unique_col_qualifiers = df["col_qualifiers"].unique()
        for col_qualifier in unique_col_qualifiers:
            for feature in features:
                col_name = "_".join([col_qualifier, feature])
                df[col_name] = np.nan
                filt = df["col_qualifiers"] == col_qualifier
                df.loc[filt, col_name] = df.loc[filt, feature]
        df.drop(columns=features+qualifiers, inplace=True)
        df.drop(columns=["col_qualifiers"], inplace=True)
        return df


    def __remove_access(self, df: pd.DataFrame) -> pd.DataFrame:
        """removes all columns that set access rights

        Parameters
        ----------
        df : pd.DataFrame
            The tabel with the access rights columns

        Returns
        -------
        pd.DataFrame
            The same table with the access rights columns removed.
        """
        if df.empty:
            return df
        to_remove = [col for col in df.columns if "access" in col.lower()]
        return df.drop(columns=to_remove)

    # Parsers to go from the standard ODM tables to a unified samples table
    def _parse_ww_measure(self) -> pd.DataFrame:
        """Prepares the WWMeasure Table for merging with
        the samples table to analyzer the data on a per-sample basis

        Returns
        -------
        pd.DataFrame
            Cleaned-up DataFrame indexed by sample.
            - Categorical columns from the WWMeasure table
                are separated into unique columns.
            - Boolean column's values are declared in the column title.
        """
        df = self.ww_measure
        if df.empty:
            return df

        df = self.__remove_access(df)
        df = self.widen(
            df,
            features=[
                "value",
                # "analysisDate",
                # "reportDate",
                # "notes",
                # "qualityFlag",
                # assay_col
            ],
            qualifiers=[
                "fractionAnalyzed",
                "type",
                "unit",
                "aggregation",
            ]
        )
        df.drop(columns=["index"], inplace=True)
        df = df.add_prefix("WWMeasure.")
        return df

    def _parse_site_measure(self) -> pd.DataFrame:
        df = self.site_measure
        if df.empty:
            return df
        df = self.__remove_access(df)
        df = self.widen(
            df,
            features=[
                "value",
                # "notes",
            ],
            qualifiers=[
                "type",
                "unit",
                "aggregation",
            ]
        )

        # Re-arrange the table so that it is arranged by dateTime, as this is
        # how site measures will be joined to samples
        df = df.groupby("dateTime").agg(utilities.reduce_by_type)
        df.reset_index(inplace=True)

        df = df.add_prefix("SiteMeasure.")
        return df

    def _parse_sample(self) -> pd.DataFrame:
        df = self.sample
        if df.empty:
            return df
        df_copy = df.copy(deep=True)

        # we want the sample to show up in any site where it is relevant.
        # Here, we gather all the siteIDs present in the siteID column for a
        # given sample, and we spread them over additional new rows so that in
        # the end, each row of the sample table has only one siteID
        for i, row in df_copy.iterrows():
            # Get the value of the siteID field
            sites = row["siteID"]
            # Check whether there are saveral ids in the field
            if ";" in sites:
                # Get all the site ids in the list
                site_ids = {x.strip() for x in sites.split(";")}
                # Assign one id to the original row
                df["siteID"].iloc[i] = site_ids.pop()
                # Create new rows for each additional siteID and assign them
                # each a siteID
                for site_id in site_ids:
                    new_row = df.iloc[i].copy()
                    new_row["siteID"] = site_id
                    df = df.append(new_row, ignore_index=True)

        df = df.add_prefix("Sample.")
        return df

    def _parse_site(self) -> pd.DataFrame:
        df = self.site
        if df.empty:
            return df
        df = df.add_prefix("Site.")
        return df

    def _parse_polygon(self) -> pd.DataFrame:
        df = self.polygon
        if df.empty:
            return df
        df = df.add_prefix("Polygon.")
        return df

    def _parse_cphd(self) -> pd.DataFrame:
        df = self.cphd
        if df.empty:
            return df
        df = self.__remove_access(df)
        df = self.widen(
            df,
            features=[
                "value",
                # "dateTime",
                # "notes"
            ],
            qualifiers=[
                "polygonID",
                "type",
                "dateType",
            ]
        )

        df = df.groupby("cphdID").agg(utilities.reduce_by_type)
        df.reset_index(inplace=True)
        df = df.add_prefix("CPHD.")
        return df

    def append_from(self, mapper) -> None:
        """Concatenates the Odm object's current data with
        that of a mapper.

        Parameters
        ----------
        mapper : odm_mappers.BaseMapper
            A mapper class implementing BaseMapper and adapted to one's
            specific use case
        """
        validates = True if isinstance(mapper, Odm) else mapper.validates()
        if not validates:
            raise ValueError("mapper object contains invalid data")

        self_attrs = self.__dict__
        for attr, current_df in self_attrs.items():
            new_df = getattr(mapper, attr)
            if current_df.empty:
                setattr(self, attr, new_df)
            elif new_df is None or new_df.empty:
                continue
            else:
                try:
                    combined = current_df.append(new_df)\
                        .drop_duplicates(keep="first", ignore_index=True)
                    setattr(self, attr, combined)
                except Exception as e:
                    setattr(self, attr, current_df)
                    raise e
        return

    def load_from(self, mapper: base_mapper.BaseMapper) -> None:
        """Reads an odm mapper object and loads the data into the Odm object.

        Parameters
        ----------
        mapper : odm_mappers.BaseMapper
            A mapper class implementing BaseMapper and adapted to one's
            specific use case

        """
        if mapper.validates():
            self_attrs = self.__dict__
            mapper_attrs = mapper.__dict__
            for key in self_attrs.keys():
                if key not in mapper_attrs:
                    continue
                new_df = mapper_attrs[key]
                self_attrs[key] = new_df.drop_duplicates(
                    keep="first", ignore_index=True)

    def get_geoJSON(self, types=None) -> dict:
        """[summary]

        Args:
            types ([type], optional): The types of polygons we want to plot.
            Defaults to None, which actually takes everything.

        Returns:
            dict: [description]
        """
        geo = {
            "type": "FeatureCollection",
            "features": []
        }
        polygon_df = self.polygon
        if types is not None:
            if isinstance(types, str):
                types = [types]
            types = [type_.lower() for type_ in types]
            polygon_df = polygon_df.loc[
                polygon_df["type"].str.lower().isin(types)
            ].copy()
        for col in polygon_df.columns:
            is_cat = polygon_df[col].dtype.name == "category"
            polygon_df[col] = polygon_df[col] if is_cat \
                else polygon_df[col].fillna("null")
        for i, row in polygon_df.iterrows():
            if row["wkt"] != "":
                new_feature = {
                    "type": "Feature",
                    "geometry": utilities.convert_wkt_to_geojson(
                        row["wkt"]
                    ),
                    "properties": {
                        col:
                        row[col] for col in polygon_df.columns
                            if "wkt" not in col
                    },
                    "id": i
                }
                geo["features"].append(new_feature)
        return geo

    def combine_per_sample(self) -> pd.DataFrame:
        """Combines data from all tables containing sample-related information
        into a single DataFrame.
        To simplify data mining, the categorical columns are separated into
        distinct columns.

        Returns
        -------
        pd.DataFrame
            DataFrame with each row representing a sample
        """
        # ________________
        # Helper functions
        def agg_ww_measure_per_sample(ww: pd.DataFrame) -> pd.DataFrame:
            """Helper function that aggregates the WWMeasure table by sample.

            Parameters
            ----------
            ww : pd.DataFrame
                The dataframe to rearrange. This dataframe should have gone
                through the _parse_ww_measure funciton before being passed in
                here. This is to ensure that categorical columns have been
                spread out.

            Returns
            -------
            pd.DataFrame
                DataFrame containing the data from the WWMeasure table,
                re-ordered so that each row represents a sample.
            """
            if ww.empty:
                return ww
            return ww.groupby("WWMeasure.sampleID")\
                .agg(utilities.reduce_by_type)

        def combine_ww_measure_and_sample(
            ww: pd.DataFrame,
            sample: pd.DataFrame
                ) -> pd.DataFrame:
            """Merges tables on sampleID

            Parameters
            ----------
            ww : pd.DataFrame
                WWMeasure table re-organized by sample
            sample : pd.DataFrame
                The sample table

            Returns
            -------
            pd.DataFrame
                A combined table containing the data from both DataFrames
            """
            if ww.empty and sample.empty:
                return pd.DataFrame()
            elif sample.empty:
                return ww
            elif ww.empty:
                return sample

            return pd.merge(
                sample, ww,
                how="left",
                left_on="Sample.sampleID",
                right_on="WWMeasure.sampleID")

        def combine_sample_site_measure(
            sample: pd.DataFrame,
            site_measure: pd.DataFrame
                ) -> pd.DataFrame:
            """Combines site measures and sample tables.

            Parameters
            ----------
            sample : pd.DataFrame
                sample DataFrame
            site_measure : pd.DataFrame
                Site Measure DataFrame

            Returns
            -------
            pd.DataFrame
                A combined DataFrame joined on sampling date
            """
            if sample.empty and site_measure.empty:
                return sample
            elif sample.empty:
                return site_measure
            elif site_measure.empty:
                return sample
            # Pandas doesn't provide good joining capability using dates, so we
            # go through SQLite to perform the join and come back to pandas
            # afterwards.
            # Make the db in memory
            conn = sqlite3.connect(':memory:')
            # write the tables
            sample.to_sql('sample', conn, index=False)
            site_measure.to_sql("site_measure", conn, index=False)

            # write the query
            qry = "select * from sample" + \
                " left join site_measure on" + \
                " [SiteMeasure.dateTime] between" + \
                " [Sample.dateTimeStart] and [Sample.dateTimeEnd]"
            merged = pd.read_sql_query(qry, conn)
            conn.close()
            return merged

        def combine_site_sample(
            sample: pd.DataFrame,
            site: pd.DataFrame
                ) -> pd.DataFrame:
            """Combines the sample table with site-specific data.

            Parameters
            ----------
            sample : pd.DataFrame
                The sample table
            site : pd.DataFrame
                The site table

            Returns
            -------
            pd.DataFrame
                A combined DataFrame joined on siteID
            """
            if sample.empty and site.empty:
                return sample
            elif sample.empty:
                return site
            elif site.empty:
                return sample
            return pd.merge(
                sample,
                site,
                how="left",
                left_on="Sample.siteID",
                right_on="Site.siteID")

        def combine_polygons_per_sample(merged, polygons):
            """
                Adds a column called 'polygonIDs' containing a list
                of polygons that pertain to a site
            """
            def convert_wkt(x):
                try:
                    return wkt.loads(x)
                except Exception:
                    return None

            def get_encompassing_polygons(row, poly):
                poly["contains"] = poly["shape"].apply(
                    lambda x: x.contains(row["temp_point"])
                    if x is not None else False)
                poly_ids = poly[
                    "Polygon.polygonID"].loc[poly["contains"]].to_list()
                return ";".join(poly_ids)

            merged["temp_point"] = merged.apply(
                lambda row: Point(
                    row["Site.geoLat"], row["Site.geoLong"]
                ), axis=1)

            polygons["shape"] = polygons["Polygon.wkt"].apply(
                lambda x: convert_wkt(x))
            merged["polygonIDs"] = merged.apply(
                lambda row: get_encompassing_polygons(row, polygons), axis=1)
            merged.drop(["temp_point"], axis=1, inplace=True)
            return merged

        def combine_cphd_by_polygon(
            sample: pd.DataFrame,
            cphd: pd.DataFrame
                ) -> pd.DataFrame:
            """Return the cphd data relevant to a given dsample using the
            geographical intersection between the sample's sewershed polygon
            and the cphd's health region polygon.

            Parameters
            ----------
            sample : pd.DataFrame
                Table containg sample information as well as a site polygonID
            cphd : pd.DataFrame
                Table containing public health data and a polygonID.

            Returns
            -------
            pd.DataFrame
                Combined DataFrame containing bnoth sample data and public
                health data.
            """
            # right now this merge hasn't been developped
            # we have to cphd data just yet
            if sample.empty and cphd.empty:
                return sample
            elif sample.empty:
                return cphd
            elif cphd.empty:
                return sample

            return pd.merge(
                sample,
                site,
                how="left",
                left_on="Sample.siteID",
                right_on="Site.siteID")

        # __________
        # Actual logic of the funciton
        ww_measure = self._parse_ww_measure()
        ww_measure = agg_ww_measure_per_sample(ww_measure)

        sample = self._parse_sample()
        merged = combine_ww_measure_and_sample(ww_measure, sample)

        site_measure = self._parse_site_measure()
        merged = combine_sample_site_measure(merged, site_measure)

        site = self._parse_site()
        merged = combine_site_sample(merged, site)

        polygons = self._parse_polygon()
        merged = combine_polygons_per_sample(merged, polygons)

        # cphd = self._parse_cphd()
        # merged = combine_cphd_by_polygon(merged, cphd)

        merged.set_index("Sample.sampleID", inplace=True)
        merged.drop_duplicates(keep="first", inplace=True)
        return merged

    def to_sqlite3(
        self,
        filepath,
        attrs_to_save: list = None,
            ) -> None:
        if attrs_to_save is None:
            attrs = self.__dict__
            attrs_to_save = [
                name for name, value in attrs.items()
                if not value.empty
            ]
        conversion_dict = base_mapper.BaseMapper.conversion_dict
        if not os.path.exists(filepath):
            create_db(filepath)
        con = sqlite3.connect(filepath)
        for attr in attrs_to_save:
            odm_name = conversion_dict[attr]["odm_name"]
            df = getattr(self, attr)
            if df.empty:
                continue
            df.to_sql(
                name='myTempTable',
                con=con,
                if_exists='replace',
                index=False
            )
            cols = df.columns
            cols_str = f"{tuple(cols)}".replace("'", "\"")

            sql = f"""REPLACE INTO {odm_name} {cols_str}
                    SELECT * from myTempTable """

            con.execute(sql)
            con.execute("drop table if exists myTempTable")
            con.close()
        return

    def to_csv(
        self,
        path: str,
        file_prefix: str,
        attrs_to_save: list = None
    ) -> None:
        if attrs_to_save is None:
            attrs_to_save = []
            attrs = self.__dict__
            for name, df in attrs.items():
                if df is None or df.empty:
                    continue
                attrs_to_save.append(name)

        conversion_dict = base_mapper.BaseMapper.conversion_dict
        if not os.path.exists(path):
            os.mkdir(path)
        for attr in attrs_to_save:
            odm_name = conversion_dict[attr]["odm_name"]
            filename = file_prefix + "_" + odm_name
            df = getattr(self, attr)
            if df is None or df.empty:
                continue
            complete_path = os.path.join(path, filename)
            df.to_csv(complete_path+".csv", sep=",", na_rep="na", index=False)
        return

    def append_odm(self, other_odm):
        for attribute in self.__dict__:
            other_value = getattr(other_odm, attribute)
            self.add_to_attr(attribute, other_value)
        return


class OdmEncoder(json.JSONEncoder):
    def default(self, o):
        if (isinstance(o, Odm)):
            return {
                '__{}__'.format(o.__class__.__name__):
                o.__dict__
            }
        elif isinstance(o, pd.Timestamp):
            return {'__Timestamp__': str(o)}
        elif isinstance(o, pd.DataFrame):
            return {
                '__DataFrame__':
                o.to_json(date_format='iso', orient='split')
            }
        else:
            return json.JSONEncoder.default(self, o)


def create_db(filepath=None):
    url = "https://raw.githubusercontent.com/Big-Life-Lab/covid-19-wastewater/dev/src/wbe_create_table_SQLITE_en.sql"  # noqa
    sql = requests.get(url).text
    conn = None
    if filepath is None:
        filepath = "file::memory"
    try:
        conn = sqlite3.connect(filepath)
        conn.executescript(sql)

    except Exception as e:
        print(e)
    finally:
        if conn:
            conn.close()


def destroy_db(filepath):
    if os.path.exists(filepath):
        os.remove(filepath)


if __name__ == "__main__":
    CSV_FOLDER = "/Users/jeandavidt/OneDrive - Université Laval/COVID/Latest Data/odm_csv"  # noqa
    mapper = csv_mapper.CsvMapper()
    mapper.read(CSV_FOLDER)
    # ldm = ledevoir_mapper.LeDevoirMapper()
    # ldm.read()
    # DATA_FOLDER = "/Users/jeandavidt/OneDrive - Université Laval/COVID/Latest Data"  # noqa
    # mapper = mcgill_mapper.McGillMapper()
    # QC_STATIC_DATA = os.path.join(DATA_FOLDER, "Ville de Quebec - All data - v1.1.xlsx")  # noqa
    # QC_LAB_DATA = os.path.join(DATA_FOLDER, "CentrEau-COVID_Resultats_Quebec_final.xlsx")  # noqa
    # QC_SHEET_NAME = "QC Data Daily Samples (McGill)"
    # mapper.read(
    #     QC_LAB_DATA, QC_STATIC_DATA, QC_SHEET_NAME, "frigon_lab"
    # )
    o = Odm()
    o.load_from(mapper)
    a = o.ww_measure.copy()
    print(len(o.ww_measure))
    o.append_from(mapper)
    b = o.ww_measure.copy()
    print(len(o.ww_measure))
    print(a==b)
    # o.append_from(ldm)
    # test = o.combine_per_sample()
    # test.to_csv("test_data.csv")
