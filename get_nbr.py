import rasterio as rio
import os
import numpy as np
import pandas as pd
import threading
import multiprocessing as mp
import gc

DATA_DIR = "./data/"
B5_DIR = DATA_DIR + "B5/"
B7_DIR = DATA_DIR + "B7/"
NBR_DIR = DATA_DIR + "NBR/"
MAX_CPU = mp.cpu_count()
MAX_THREADS = 5
sema = threading.Semaphore(value=MAX_THREADS)

def delete_raw_bands(file_name):
    #print(f"deleting {file_name.format("B5")} and {file_name.format("B7")}...")
    #not sure if necessary or helpful
    pass
    
def write_NBR(file_name):
    """
    reads ./data/B5/file_name and ./data/B7/file_name
    creates ./data/NBR/file_name
    """
    b5_file = B5_DIR + file_name.format("B5")
    b7_file = B7_DIR + file_name.format("B7")
    print(f"opening {b5_file} and {b7_file}...")
    b5_open = rio.open(b5_file)
    b7_open = rio.open(b7_file)
    print(f"b5 bands: {len(b5_open.shape)} b7 bands: {len(b7_open.shape)}")
    b5 = b5_open.read(1)
    b7 = b7_open.read(1)
    print(f"computing NBR from ratio")
    with np.errstate(divide='ignore', invalid='ignore'):
        nbr = ((b5.astype(float) - b7.astype(float)) / (b5 + b7)).astype(np.int16)
    del b5, b7
    gc.collect()
    nbr[nbr > 10000] = -19999
    nbr[nbr < -10000] = -19999
    nbr_meta = b5_open.meta.copy()
    nbr_meta.update({'driver':'GTiff',
                     'width':b5_open.shape[1],
                     'height':b5_open.shape[0],
                     'count':1,
                     'dtype':'int16',
                     'crs':b5_open.crs,
                     'transform':b5_open.transform,
                     'nodata':0})
    del b5_open, b7_open
    gc.collect()
    nbr_file = NBR_DIR + file_name.format("NBR")
    print(f"writing to {nbr_file}...")
    sema.acquire()
    with rio.open(fp=nbr_file, mode='w',**nbr_meta) as dst:
             dst.write(nbr, 1) # the numer one is the number of bands
    sema.release()
    del nbr, nbr_meta
    gc.collect()
    delete_raw_bands(file_name)
    
def run_write_NBR(threads, file_name):
    """
    for multithreading
    """
    thread = threading.Thread(target=write_NBR, args=(file_name,))
    threads.append(thread)
    thread.start()
    
def main():
    if not os.path.exists(NBR_DIR): 
        try: 
            os.makedirs(NBR_DIR)
            print(f"Directory '{NBR_DIR}' created successfully.") 
        except OSError as e: 
            print(f"Error creating directory '{NBR_DIR}': {e}") 
    else: 
        
        print(f"Directory '{NBR_DIR}' already exists.") 
    nbr_files = [file[:-6] + "{0}.TIF" for file in os.listdir(NBR_DIR)]
    file_names = [b5_file[:-6] + "{0}.TIF" for b5_file in os.listdir(B5_DIR)]
    for file_name in file_names:
        if file_name not in nbr_files:
            write_NBR(file_name)
            
if __name__ == "__main__":
    main()
    