print("\nimporting libraries ...", end=" ")
import os
import sys
import gc
import glob
import time
from datetime import datetime
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
BAND_NAMES = {'SR_B5', 'SR_B4'}

MAX_PROCS = mp.cpu_count()

DATA_DIR = '/tmp/data/'

B5_DIR = DATA_DIR + 'SR_B5_{0}/'
B4_DIR = DATA_DIR + 'SR_B4_{0}/'
NDVI_DIR = DATA_DIR + 'NDVI_{0}/'
REPROJ_DIR = DATA_DIR + 'NDVI_RP_{0}/'
YEAR_DIRS = [B5_DIR, B4_DIR, NDVI_DIR, REPROJ_DIR]

MOSAICS_DIR = DATA_DIR + 'NDVI_mosaics/'
CONSISTENT_DIRS = [DATA_DIR, MOSAICS_DIR]

START_YEAR = 2020
NODATA_VALUE = -20000
REPROJ_SRS = 'EPSG:5070'

def get_ndvi(band1, band2):
    """
    This function takes an input the arrays of the bands from the read_band_image
    function and returns the Normalized Burn ratio (ndvi)
    input:  band1   array (n x m)      array of first band image e.g B5
            band2   array (n x m)      array of second band image e.g. b5
    output: ndvi     array (n x m)      normalized burn ratio
    """
    num = band1 - band2
    denom = band1 + band2
    denom[denom==0] = np.nan
    ndvi = num / denom
    return ndvi
    
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

def write_ndvi(filename_stem: str, year: int, delete_raw_data=DELETE_RAW_DATA):
    """
    input: 
     - filename_stem: str
         something like 'LC09_L2SP_038038_20240530_20240531_02_T1_SR_'
    Opens:
     - /tmp/data/B5/<filename_stem>B5.TIF
     - /tmp/data/b5/<filename_stem>b5.TIF
     Writes:
     - /tmp/data/ndvi/<filename_stem>ndvi.TIF
     ––––––––––––––––––––––––––––––––––––––––––––––––––
     if `delete_bands` not set to False, will also delete: 
     - /tmp/data/B5/<filename_stem>B5.TIF
     - /tmp/data/b5/<filename_stem>b5.TIF
    """
    ## access relevant files
    b5_fp = B5_DIR.format(year) + filename_stem + 'B5.TIF'
    b4_fp = B4_DIR.format(year) + filename_stem + 'B4.TIF'
    ndvi_fp = NDVI_DIR.format(year) + filename_stem + 'NDVI.TIF'
    ## open B5, b5, and get data
    with gdal.Open(b5_fp) as img:
        b5_data = np.array(img.GetRasterBand(1).ReadAsArray())
        crs = img.GetProjection()
        geoTransform = img.GetGeoTransform()
        targetprj = osr.SpatialReference(wkt = img.GetProjection())
    with gdal.Open(b4_fp) as img:
        b4_data = np.array(img.GetRasterBand(1).ReadAsArray())
    ## compute NDVI
    ndvi_data = get_ndvi(b5_data.astype('float'), b4_data.astype('float'))
    ## clear memory
    del b5_data
    del b4_data
    gc.collect()
    ## write to file
    try:
        array2raster(ndvi_data, geoTransform, crs, ndvi_fp)
        ## delete raw data
        if delete_raw_data:
            os.remove(b5_fp)
            os.remove(b4_fp)
    except FileExistsError:
        pass
    except Exception as e:
        print(f"ndvi computation failed: {e}")
    ## reproject data
    try:
        reproject(ndvi_fp, year)
        if delete_raw_data:
            os.remove(ndvi_fp)
    except Exception as e:
        print(f"reprojection failed: {e}")

def reproject(ndvi_fp: str, year: int):
    """
    reprojects raster file @ `ndvi_fp` to EPSG:5070
    """
    out_raster = REPROJ_DIR.format(year) + ndvi_fp[20:]
    with gdal.Open(ndvi_fp) as in_raster:
        gdal.Warp(out_raster, in_raster, dstSRS=REPROJ_SRS, 
                  dstNodata = NODATA_VALUE, srcNodata = NODATA_VALUE, 
                  options=['-co', 'COMPRESS=ZSTD'])

def get_ndvi_mosaic_for_year(year: int):
    """
    Input
     - Year: Int
       Year to get pre-fire-season NDVI raster
    """
    ## create directories to store intermediary results
    dirs = [d.format(year) for d in YEAR_DIRS]
    for d in dirs:
        if not os.path.exists(d): 
            try: 
                os.makedirs(d)
                print(f"Directory '{d}' created successfully.") 
            except OSError as e: 
                print(f"Error creating directory '{d}': {e}") 
        else: 
            print(f"Directory '{d}' already exists.")
            
    ## download raw B5/B4 data
    download_raw_data(DATA_DIR, BAND_NAMES, year)
    
    ##Compute NDVI and Reproject
    print("\nComputing NDVI and reprojecting...")
    filename_stems = [filename[:-6] for filename in os.listdir(B5_DIR.format(year))]
    for filename_stem in tqdm(filename_stems):
        write_ndvi(filename_stem, year)
    
    ## mosaic raster files together
    mosaic_fp = MOSAICS_DIR + f"{year}.TIF"
    print(f"\nMerging {year} NDVI files and saving to {mosaic_fp} ...")
    rasters = glob.glob(REPROJ_DIR.format(year) + "*.TIF")
    gdal.Warp(mosaic_fp, rasters, 
              format='GTiff', resampleAlg='bilinear', 
              options=['-wo', 'NUM_THREADS=ALL_CPUS', '-multi',
                       '-co', 'COMPRESS=ZSTD',])
    
    ## remove old directories
    if DELETE_RAW_DATA:
        for raster in rasters:
            os.remove(raster)
        for d in YEAR_DIRS:
            os.rmdir(d.format(year))

def main():
    args = sys.argv[1:]
    years = [int(arg) for arg in args]
    for year in years:
        assert year <= datetime.now().year
    ## create directories as needed
    for d in CONSISTENT_DIRS:
        if not os.path.exists(d): 
            try: 
                os.makedirs(d)
                print(f"Directory '{d}' created successfully.") 
            except OSError as e: 
                print(f"Error creating directory '{d}': {e}") 
        else: 
            print(f"Directory '{d}' already exists.")
            
    done_years = [int(file[:4]) for file in os.listdir(MOSAICS_DIR)]
    relevant_years = [year for year in years if year not in done_years]
    #current_year = datetime.now().year
    #relevant_years = [year for year in range(START_YEAR, current_year+1) if year not in done_years]
    for year in relevant_years:
        get_ndvi_mosaic_for_year(year)
    
                          
if __name__ == "__main__":
    start = time.perf_counter()
    main()
    end = time.perf_counter()
    print(f'\nFinished in {round(end - start, 2)} second(s)')
    print('with love, from ozan\n')

