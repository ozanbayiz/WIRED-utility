import json
import geojson
import requests
from getpass import getpass
import sys
import time
import re
import threading
import datetime
import os
import pandas as pd
import warnings
warnings.filterwarnings("ignore")

USERNAME = "ozanbayiz"
SERVICE_URL = "https://m2m.cr.usgs.gov/api/api/json/stable/"
TOKEN = "AvChvofuZv5G1By8khf94@XeBB8OtRNJ!_o3BQms4M7ZudZdPGF9L1PypoUwyFme"
AUTH_PAYLOAD = {"username": USERNAME, "token": TOKEN}

DATA_DIR = 'data'
UTILS_DIR = 'utils' 
BAND_DIRS = ['data/B5', 'data/B7']
DIRS = [DATA_DIR, UTILS_DIR] + BAND_DIRS
MAX_THREADS = 5 # Threads count for downloads

FILE_TYPE = 'band'
BAND_NAMES = {'SR_B5', 'SR_B7'}
DATASET_NAME = 'landsat_ot_c2_l2'
FILE_GROUP_IDS = {"ls_c2l2_sr_band"}
sema = threading.Semaphore(value=MAX_THREADS)

def sendRequest(url, data, apiKey = None, exitIfNoResponse = True):
    """
    Send a request to an M2M endpoint and returns the parsed JSON response.

    Parameters:
    endpoint_url (str): The URL of the M2M endpoint
    payload (dict): The payload to be sent with the request

    Returns:
    dict: Parsed JSON response
    """
    json_data = json.dumps(data)
    if apiKey == None:
        response = requests.post(url, json_data)
    else:
        headers = {'X-Auth-Token': apiKey}              
        response = requests.post(url, json_data, headers = headers)  
    try:
      httpStatusCode = response.status_code 
      if response == None:
          print("No output from service")
          if exitIfNoResponse: sys.exit()
          else: return False
      output = json.loads(response.text)
      if output['errorCode'] != None:
          print(output['errorCode'], "- ", output['errorMessage'])
          if exitIfNoResponse: sys.exit()
          else: return False
      if  httpStatusCode == 404:
          print("404 Not Found")
          if exitIfNoResponse: sys.exit()
          else: return False
      elif httpStatusCode == 401: 
          print("401 Unauthorized")
          if exitIfNoResponse: sys.exit()
          else: return False
      elif httpStatusCode == 400:
          print("Error Code", httpStatusCode)
          if exitIfNoResponse: sys.exit()
          else: return False
    except Exception as e: 
          response.close()
          print(e)
          if exitIfNoResponse: sys.exit()
          else: return False
    response.close()
    return output['data']
    
def runDownload(threads, url):
    """
    runs downloadFile function on new thread
    """
    thread = threading.Thread(target=downloadFile, args=(url,))
    threads.append(thread)
    thread.start()
    
def downloadFile(url):
    sema.acquire()
    try:
        response = requests.get(url, stream=True)
        disposition = response.headers['content-disposition']
        filename = re.findall("filename=(.+)", disposition)[0].strip("\"")
        folder = "/" + filename[-6:-4] + "/"
        print(f"    Downloading: {filename}...")
        open(os.path.join(DATA_DIR + folder, filename), 'wb').write(response.content)
        sema.release()
    except Exception as e:
        print(f"\nFailed to download from {url}. Will try to re-download.")
        sema.release()
        runDownload(threads, url)
        
def main():
    #create directories as needed
    for d in DIRS:
            if not os.path.exists(d): 
                try: 
                    os.makedirs(d)
                    print(f"Directory '{d}' created successfully.") 
                except OSError as e: 
                    print(f"Error creating directory '{d}': {e}") 
            else: 
                print(f"Directory '{d}' already exists.") 
    ## create label / threads
    ####
    sema = threading.Semaphore(value=MAX_THREADS)
    label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") # Customized label using date time
    threads = []

    ## login to get API Key
    ####
    print("Logging in...\n")
    apiKey = sendRequest(SERVICE_URL + "login-token", AUTH_PAYLOAD)
    print("API Key: " + apiKey + "\n")

    ## create filters
    ####
    #create spatial filter
    
    #
    path_to_CA_geojson = "./utils/California_State_Boundary.geojson"
    with open(path_to_CA_geojson) as f:
        gj = geojson.load(f)
    geo = gj['features'][0]['geometry']
    mp = geojson.MultiPolygon(geo)
    spatialFilter =  {'filterType' : 'geojson',
                      'geoJson': mp}
    #create Acquisition Filter (date range)
    acquisitionFilter = {'start' : '2024-06-04', 'end' : '2024-06-05'}
    #create Cloud Cover Filter (may become more relevant later)
    cloudCoverFilter = {'min' : 0, 'max' : 20}
    #final payload
    search_payload = {
        'datasetName' : DATASET_NAME,
        'sceneFilter' : {
            'spatialFilter' : spatialFilter,
            'acquisitionFilter' : acquisitionFilter,
            'cloudCoverFilter' : cloudCoverFilter
        }
    }
    
    ## perform search
    ####
    #get scenes
    scenes = sendRequest(SERVICE_URL + "scene-search", search_payload, apiKey)
    #create list of entity ids
    idField = 'entityId'
    entityIds = []
    for result in scenes['results']:
         # Add this scene to the list I would like to download if bulk is available
        if result['options']['bulk'] == True:
            entityIds.append(result[idField])
    listId = f"temp_{DATASET_NAME}_list" # customized list id
    scn_list_add_payload = {
        "listId": listId,
        'idField' : idField,
        "entityIds": entityIds,
        "datasetName": DATASET_NAME
    }
    count = sendRequest(SERVICE_URL + "scene-list-add", scn_list_add_payload, apiKey) 
    download_opt_payload = {
        "listId": listId,
        "datasetName": DATASET_NAME
    }
    download_opt_payload['includeSecondaryFileGroups'] = True
    products = sendRequest(SERVICE_URL + "download-options", download_opt_payload, apiKey)
    filegroups = sendRequest(SERVICE_URL + "dataset-file-groups", {'datasetName' : DATASET_NAME}, apiKey)
    
    # Select products
    print("Selecting products...")
    downloads = []
    print("    Selecting band files...")
    for product in products:  
        if product["secondaryDownloads"] is not None and len(product["secondaryDownloads"]) > 0:
            for secondaryDownload in product["secondaryDownloads"]:
                for bandName in BAND_NAMES:
                    if secondaryDownload["bulkAvailable"] and bandName in secondaryDownload['displayId']:
                        downloads.append({"entityId":secondaryDownload["entityId"], "productId":secondaryDownload["id"]})
    download_req2_payload = {
        "downloads": downloads,
        "label": label
    }

    ## send download request
    ####
    print(f"Sending download request ...")
    download_request_results = sendRequest(SERVICE_URL + "download-request", download_req2_payload, apiKey)
    print(f"Done sending download request") 
    if len(download_request_results['newRecords']) == 0 and len(download_request_results['duplicateProducts']) == 0:
        print('No records returned, please update your scenes or scene-search filter')
        sys.exit()
    # Attempt the download URLs
    for result in download_request_results['availableDownloads']:       
        print(f"Get download url: {result['url']}\n" )
        runDownload(threads, result['url'])
        
    preparingDownloadCount = len(download_request_results['preparingDownloads'])
    preparingDownloadIds = []
    if preparingDownloadCount > 0:
        for result in download_request_results['preparingDownloads']:  
            preparingDownloadIds.append(result['downloadId'])
        download_ret_payload = {"label" : label}                
        # Retrieve download URLs
        print("Retrieving download urls...\n")
        download_retrieve_results = sendRequest(SERVICE_URL + "download-retrieve", download_ret_payload, apiKey, False)
        if download_retrieve_results != False:
            print(f"    Retrieved: \n" )
            for result in download_retrieve_results['available']:
                if result['downloadId'] in preparingDownloadIds:
                    preparingDownloadIds.remove(result['downloadId'])
                    runDownload(threads, result['url'])
                    print(f"       {result['url']}\n" )
                
            for result in download_retrieve_results['requested']:   
                if result['downloadId'] in preparingDownloadIds:
                    preparingDownloadIds.remove(result['downloadId'])
                    runDownload(threads, result['url'])
                    print(f"       {result['url']}\n" )
        
        # Didn't get all download URLs, retrieve again after 30 seconds
        while len(preparingDownloadIds) > 0: 
            print(f"{len(preparingDownloadIds)} downloads are not available yet. Waiting for 30s to retrieve again\n")
            time.sleep(30)
            download_retrieve_results = sendRequest(SERVICE_URL + "download-retrieve", download_ret_payload, apiKey, False)
            if download_retrieve_results != False:
                for result in download_retrieve_results['available']:                            
                    if result['downloadId'] in preparingDownloadIds:
                        preparingDownloadIds.remove(result['downloadId'])
                        print(f"    Get download url: {result['url']}\n" )
                        runDownload(threads, result['url'])

    #initiate download
    print("\nDownloading files... Please do not close the program\n")
    for thread in threads:
        thread.join()        

if __name__ == "__main__":
    main()


