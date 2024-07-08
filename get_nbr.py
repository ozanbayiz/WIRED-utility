print("\nimporting libraries ...", end=" ")

import os
import sys
import gc
import time
import datetime
from dateutil.relativedelta import relativedelta

import json
import geojson
import requests

import numpy as np
import pandas as pd
import geopandas as gpd
os.environ['PROJ_LIB'] =  '/srv/starter_content/_User-Persistent-Storage_/wired-utility/share/proj/'
from osgeo import gdal, ogr, osr

import warnings
warnings.filterwarnings("ignore")

import multiprocessing as mp
from tqdm import tqdm

from download_utils import download_raw_data

print("Done\n")
DELETE_RAW_DATA = True
MAX_PROCS = mp.cpu_count()
DATA_DIR = '/tmp/data/'
B5_DIR = DATA_DIR + 'B5/'
B7_DIR = DATA_DIR + 'B7/'
NBR_DIR = DATA_DIR + 'NBR/'
REPROJ_DIR = DATA_DIR + 'NBR_RP/'
DIRS = [DATA_DIR, B5_DIR, B7_DIR, NBR_DIR, REPROJ_DIR]

NBR_RASTER_MOSAIC = "./nbr_raster_mosaic.TIF"

NODATA_VALUE = -20000
REPROJ_SRS = 'EPSG:5070'

def get_nbr(band1, band2):
    """
    This function takes an input the arrays of the bands from the read_band_image
    function and returns the Normalized Burn ratio (NBR)
    input:  band1   array (n x m)      array of first band image e.g B5
            band2   array (n x m)      array of second band image e.g. B7
    output: nbr     array (n x m)      normalized burn ratio
    """
    num = band1 - band2
    denom = band1 + band2
    denom[denom==0] = np.nan
    nbr = num / denom
    return nbr
    
def array2raster(array, geoTransform, projection, filename, resample=True):
    """ 
    This function tarnsforms a numpy array to a geotiff projected raster
    input:  array                       array (n x m)   input array
            geoTransform                tuple           affine transformation coefficients
            projection                  string          projection
            filename                    string          output filename
    output: dataset                                     gdal raster dataset
            dataset.GetRasterBand(1)                    band object of dataset
    
    """
    if resample:
        np.nan_to_num(array, copy=False, nan=-2, posinf=-2, neginf=-2)
        array = np.round(array * 10000).astype('int16')
        dtype = gdal.GDT_Int16
    else:
        dtype = gdal.GDT_Float32
    
    pixels_x = array.shape[1]
    pixels_y = array.shape[0]
    
    driver = gdal.GetDriverByName('GTiff')
    dataset = driver.Create(
        filename,
        pixels_x,
        pixels_y,
        1,
        dtype,
        options=['COMPRESS=ZSTD'])
    dataset.SetGeoTransform(geoTransform)
    dataset.SetProjection(projection)
    dataset.GetRasterBand(1).WriteArray(array)
    dataset.FlushCache()  # Write to disk.
    del dataset
    gc.collect()
    #return dataset, dataset.GetRasterBand(1)  #If you need to return, remenber to return  also the dataset because the band don`t live without dataset.

def write_NBR(filename_stem: str, delete_bands=DELETE_RAW_DATA):
    """
    input: 
     - filename_stem: str
         something like'LC09_L2SP_038038_20240530_20240531_02_T1_SR_'
    Opens:
     - /tmp/data/B5/<filename_stem>B5.TIF
     - /tmp/data/B7/<filename_stem>B7.TIF
     Writes:
     - /tmp/data/NBR/<filename_stem>NBR.TIF
     ––––––––––––––––––––––––––––––––––––––––––––––––––
     if `delete_bands` not set to False, will also delete: 
     - /tmp/data/B5/<filename_stem>B5.TIF
     - /tmp/data/B7/<filename_stem>B7.TIF
    """
    ## access relevant files
    b5_fp = B5_DIR + filename_stem + 'B5.TIF'
    b7_fp = B7_DIR + filename_stem + "B7.TIF"
    nbr_fp = NBR_DIR + filename_stem + "NBR.TIF"
    ## open B5, B7, and get data
    with gdal.Open(b5_fp) as img:
        b5_data = np.array(img.GetRasterBand(1).ReadAsArray())
        crs = img.GetProjection()
        geoTransform = img.GetGeoTransform()
        targetprj = osr.SpatialReference(wkt = img.GetProjection())
    with gdal.Open(b7_fp) as img:
        b7_data = np.array(img.GetRasterBand(1).ReadAsArray())
    ## compute NBR and manage memory
    nbr_data = get_nbr(b5_data.astype('float'), b7_data.astype('float'))
    del b5_data
    del b7_data
    gc.collect()
    ## write to file
    try:
        array2raster(nbr_data, geoTransform, crs, nbr_fp)
        ## delete raw data
        if DELETE_RAW_DATA:
            os.remove(b5_fp)
            os.remove(b7_fp)
    except FileExistsError:
        pass
    except Exception as e:
        print(f"nbr computation failed: {e}")
    ## reproject data
    try:
        reproject(nbr_fp)
    except Exception as e:
        print(f"reprojection failed: {e}")

def reproject(nbr_fp: str):
    """
    reprojects raster file @ `nbr_fp` to EPSG:5070
    """
    out_raster = REPROJ_DIR + nbr_fp[14:]
    with gdal.Open(nbr_fp) as in_raster:
        gdal.Warp(out_raster, in_raster, dstSRS=REPROJ_SRS, dstNodata = NODATA_VALUE, srcNodata = NODATA_VALUE, options=['-co', 'COMPRESS=ZSTD'])
    if DELETE_RAW_DATA:
        os.remove(nbr_fp)

def main():
    ## create directories as needed
    for d in DIRS:
            if not os.path.exists(d): 
                try: 
                    os.makedirs(d)
                    print(f"Directory '{d}' created successfully.") 
                except OSError as e: 
                    print(f"Error creating directory '{d}': {e}") 
            else: 
                print(f"Directory '{d}' already exists.")
    ## download band 5 and band 7 data
    b5_dir, b7_dir = download_raw_data(DATA_DIR)
    filename_stems = [filename[:-6] for filename in os.listdir(b5_dir)]
    
    ## compute NBR using B5 and B7
    ## ## this will also reproject the NBR files
    print("\nComputing NBR and reprojecting...")
    for filename_stem in tqdm(filename_stems):
        write_NBR(filename_stem)
    # # # # Tried Multiprocessing, didn't work
    """
    with mp.Pool(MAX_PROCS) as p:
        [_ for _ in tqdm(p.imap_unordered(write_NBR, filename_stems), total=len(filename_stems))]
        ##note: multiprocessing is taking a really long time
    """
    ## tile reprojected rasters togethers
    rasters = glob.glob(REPROJ_DIR + "*.TIF")
    gdal.Warp(NBR_RASTER_MOSAIC, rasters, format='GTiff', resampleAlg='bilinear', options=['-wo', 'NUM_THREADS=ALL_CPUS' '-multi', '-co', 'COMPRESS=ZSTD', ])
    

if __name__ == "__main__":
    start = time.perf_counter()
    main()
    end = time.perf_counter()
    print(f'\nFinished in {round(end - start, 2)} second(s)')
    print('with love, from ozan\n')
