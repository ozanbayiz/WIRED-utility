import os
import json
import time
import datetime
import requests
import urllib.parse
import urllib.request
import warnings
import geopandas as gpd
import pandas as pd
from osgeo import gdal, ogr
from shapely import wkt
import tqdm
# remove warnings
import multiprocessing as mp

warnings.filterwarnings('ignore')


DATA_DIR = "./data/"
FIRE_FP = DATA_DIR + "fire.geojson"
TREAT_DIR = DATA_DIR + "treatment/"
TREAT_FPS = ["point.geojson", "line.geojson", "poly.geojson"]
OUT_FILENAME = "fire_treatment_intersections.csv"
NUM_TREATMENT_FEATURES = 3

def make_valid(geo):
    if not geo.is_valid:
        #this is only one way to make an invalid geometry valid
        return geo.buffer(0)
    return geo

def process_fire_data(data):
    for feat in data['features']:
        geo = {'type':'polygon', 'coordinates':feat['geometry']['rings']}
        feat['geometry'] = geo
        feat['properties'] = feat['attributes']
        del feat['attributes']
    fires = gpd.GeoDataFrame.from_features(data['features'], crs='EPSG:4269')
    #fires.loc[not fires['geometry'].is_valid, :] = fires.loc[fires['geometry'].is_valid, 'geometry'].apply(make_valid)
    fires['geometry'] = fires['geometry'].apply(make_valid)
    #fires = fires.loc[fires['geometry'].is_valid, :]
    columns_lower = { col: col.lower() for col in fires.columns}
    fires = fires.rename(columns=columns_lower)
    fires['alarm_date'] = [datetime.datetime.fromtimestamp(ms/1000.0) for ms in fires['alarm_date']]
    fires['cont_date'] = [datetime.datetime.fromtimestamp(ms/1000.0) for ms in fires['cont_date']]
    fires = fires.set_index('objectid')
    fires = fires.to_crs(32611) 
    return fires
    
def get_fire_data():
    base_url = "https://services1.arcgis.com/jUJYIo9tSA7EHvfZ/arcgis/rest/services/California_Fire_Perimeters/FeatureServer/0/"
    start = time.perf_counter()
    
    # Get record extract limit
    url_string = base_url + "?f=json"
    j = urllib.request.urlopen(url_string)
    js = json.load(j)
    max_records_count = int(js["maxRecordCount"])
    max_records_count = min(max_records_count, 800)
    
    # get count of objects
    count_query = "query?where=%20(YEAR_%20%3D%202022%20OR%20YEAR_%20%3D%209999)%20&outFields=*&returnCountOnly=true&outSR=4326&f=json"
    url_string = base_url + count_query
    j = urllib.request.urlopen(url_string)
    js = json.load(j)
    num_of_records = js['count']
    print(("\n    Number of target records: %s" % num_of_records))
    
    gather_query = "query?where=%20(YEAR_%20%3D%202022%20OR%20YEAR_%20%3D%209999)%20&outFields=*&outSR=4326&f=json"
    url_string = base_url + gather_query
    resp = requests.get(url_string, verify=False)
    data = resp.json()
    print('    Number of requests: {}'.format(1))
    
    print("    Processing data...")
    fires = process_fire_data(data)
    end = time.perf_counter()
    print(f'\n    Finished in {round(end - start, 2)} second(s)')
    return fires
    
def fetch_all_features(base_url):
    start = time.perf_counter()
    
    # Get record extract limit
    url_string = base_url + "?f=json"
    j = urllib.request.urlopen(url_string)
    js = json.load(j)
    max_records_count = int(js["maxRecordCount"])
    max_records_count = min(max_records_count, 800)
    
    # Get object ids of features
    fields = "*"
    where = "1=1"
    url_string = base_url + "/query?where={}&returnIdsOnly=true&f=json".format(where)
    j = urllib.request.urlopen(url_string)
    js = json.load(j)
    id_field = js["objectIdFieldName"]
    id_list = js["objectIds"]
    id_list.sort()
    num_of_records = len(id_list)
    print(("\n    Number of target records: %s" % num_of_records))
    
    print("    Gathering records…")
    features_list = []
    
    def load_features(urlstring, return_dict):
        succeed = False
        while not succeed:
            try:
                resp = requests.get(urlstring, verify=False)
                data = resp.json()
                gdf = gpd.GeoDataFrame.from_features(data['features'], crs='EPSG:4269')
                gdf = gdf.loc[gdf['geometry'].is_valid, :]
                return_dict[urlstring] = gdf
                succeed = True
            except:
                print ('Failed to load {}'.format(urlstring))
    processes = []
    manager = mp.Manager()
    return_dict = manager.dict()
    request_number = 0
    for i in range(0, num_of_records, max_records_count):
        request_number += 1
        to_rec = i + (max_records_count - 1)
        if to_rec > num_of_records:
            to_rec = num_of_records - 1
        from_id = id_list[i]
        to_id = id_list[to_rec]
        where = "{} >= {} and {} <= {}".format(id_field, from_id, id_field, to_id)
        #print("  {}: {}".format(request_number, where))
        url_string = base_url + "/query?where={}&returnGeometry=true&outFields={}&f=geojson".format(where, fields)
    
        p = mp.Process(target=load_features, args=[url_string, return_dict])
        p.start()
        processes.append(p)
    
    for p in processes:
        p.join()
    p.close()
    
    for url in return_dict.keys():
        features_list.append(return_dict[url])
    treats = pd.concat(features_list)
    treats['activity_end'] = [datetime.datetime.fromtimestamp(ms/1000.0) for ms in treats['activity_end']]
    treats = treats.set_index('globalid')
    treats = treats.to_crs(32611)
    end = time.perf_counter()
    print(f'    Finished in in {round(end - start, 2)} second(s)')
    return treats

def get_bbox(poly):
    envelope = poly.GetEnvelope()
    min_x, max_x, min_y, max_y = envelope
    ring = ogr.Geometry(ogr.wkbLinearRing)
    ring.AddPoint(min_x, min_y)
    ring.AddPoint(min_x, max_y)
    ring.AddPoint(max_x, max_y)
    ring.AddPoint(max_x, min_y)
    ring.AddPoint(min_x, min_y)  # Close the ring
    bbox_poly = ogr.Geometry(ogr.wkbPolygon)
    bbox_poly.AddGeometry(ring)
    return bbox_poly
    
def get_feature_intersections(objectid, fire_bbox, fire_poly, treatments, add_buffer, buffer_distance=20):
    intersection_ids = []
    intersection_geoms = []
    for treat_id, treat_row in treatments.iterrows():
        treat_poly = ogr.CreateGeometryFromWkt(treat_row['geometry'].wkt)
        if add_buffer:
            treat_poly = treat_poly.Buffer(buffer_distance)  
        treat_bbox = get_bbox(treat_poly)
        if treat_bbox.Intersects(fire_bbox):
            if treat_poly.Intersects(fire_poly):
                intersection_geom = treat_poly.Intersection(fire_poly)
                # 'ERROR 1: Empty Geometry Cannot Be Constructed' or something
                # try/except did prevent ^that^ error, trying this instead
                if intersection_geom and not intersection_geom.IsEmpty():
                    intersection_geoms.append(intersection_geom.ExportToWkt())
                    intersection_ids.append(treat_id)

    # issues saving geometry objects to file with GeoDataFrame
    # using DataFrame and saving WKT string instead
    if intersection_ids:
        int_df = pd.DataFrame({'treat_globalid':intersection_ids, 'geometry':intersection_geoms})
        #int_df = gpd.GeoDataFrame({'treat_globalid':intersection_ids, 'geometry':intersection_geoms})
    else:
        int_df = pd.DataFrame(columns=['treat_globalid', 'geometry'])
        #int_df = gpd.GeoDataFrame(columns=['treat_globalid', 'geometry'])
    return int_df
    
def get_all_intersections(objectid):
    fire = fires.loc[objectid]
    alarm_date = fire['alarm_date']
    fire_poly = ogr.CreateGeometryFromWkt(fire['geometry'].wkt)
    fire_bbox = get_bbox(fire_poly)
    int_dfs = []
    for item in TREAT_DICT:
        treat_table = item['table']
        add_buffer = item['add_buffer']
        treats_valid_date = treat_table[treat_table['activity_end'] < alarm_date]
        int_dfs.append(get_feature_intersections(objectid, fire_bbox, fire_poly, treats_valid_date, add_buffer))
    all_int_df = pd.concat(int_dfs)
    all_int_df['fire_objectid'] = objectid
    return all_int_df

def main():
    print("getting fire data...")
    global fires
    fires = get_fire_data()

    print("\ngetting treatment data...")
    TREAT_URLS = ["https://gsal.sig-gis.com/server/rest/services/Hosted/ITS_Dashboard_Feature_Layer/FeatureServer/0", 
                  "https://gsal.sig-gis.com/server/rest/services/Hosted/ITS_Dashboard_Feature_Layer/FeatureServer/1", 
                  "https://gsal.sig-gis.com/server/rest/services/Hosted/ITS_Dashboard_Feature_Layer/FeatureServer/2"]
    ADD_BUFFER = [True, 
                  True, 
                  False]
    TREAT_TABLES = [fetch_all_features(url) for url in TREAT_URLS]
    global TREAT_DICT
    TREAT_DICT = [{'table': TREAT_TABLES[i], 'add_buffer': ADD_BUFFER[i]} for i in range(len(TREAT_URLS))]

    print(f"\n{mp.cpu_count()} cores available")
    print("finding intersecions...", "\n")
    with mp.Pool(mp.cpu_count()) as p:
        results = [_ for _ in tqdm.tqdm(p.imap_unordered(get_all_intersections, fires.index), total=len(fires.index))]
        #results = p.map(get_all_intersections, fires.index)

    print("\nsaving data to file...")
    #issues saving as GeoDataFrame, using DataFrame instead
    all_data_df = pd.concat(results, ignore_index=True)
    #all_data_df = gpd.GeoDataFrame(pd.concat(results,ignore_index=True))
    #convert from OGR to Shapely Geometry objects [since Geopandas uses Shapely objects]
    #all_data_df['geometry'] = all_data_df['geometry'].apply(lambda wkt_string: wkt.loads(wkt_string))
    #all_data_df.set_geometry('geometry')
    all_data_df.to_csv(OUT_FILENAME)
    print(f"successfully saved to {OUT_FILENAME}")

    print('\nwith love, from ozan')

if __name__ == "__main__":
    main()