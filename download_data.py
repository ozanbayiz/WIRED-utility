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
from datetime import date
from dateutil.relativedelta import relativedelta
import warnings
warnings.filterwarnings("ignore")

USERNAME = "ozanbayiz"
SERVICE_URL = "https://m2m.cr.usgs.gov/api/api/json/stable/"

DATA_DIR = 'data'
DIRS = ['data', 'data/B5', 'data/B7']

#exported from EarthExplorer scene search
CA_SHAPE = {"type":"Polygon","coordinates":[[[-124.212020874,41.999721527],[-120.000305176,41.995319367],[-120.000946045,39.000530242],[-114.632873535,35.00207901],[-114.129486084,34.267208099],[-114.724243164,33.404582977], [-114.526489258,32.757965088],[-117.125434876,32.530426025],[-117.475082398,33.303565979],[-118.522705078,34.029914856],[-120.639457703,34.565116883],[-120.640052796,35.135974884],[-121.904922485,36.307968139],[-121.772270203,36.815425873],[-122.521766663,37.53420639],[-123.02445221,37.994209289],[-123.729194641,38.91916275],[-123.851020813,39.828338623],[-124.411186219,40.436016083],[-124.088340759,40.831844329],[-124.212020874,41.999721527]]]}
END_DATE = str(date.today())
START_DATE = str(date.today() - relativedelta(days=14))
CLOUD_COVER_MAX = 20

FILE_TYPE = 'band'
BAND_NAMES = {'SR_B5', 'SR_B7'}
DATASET_NAME = 'landsat_ot_c2_l2'
FILE_GROUP_IDS = {"ls_c2l2_sr_band"}

MAX_THREADS = 5 #
sema = threading.Semaphore(value=MAX_THREADS)

#taken directly from M2M example script
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
        
def get_env_data_as_dict(path: str) -> dict:
    with open(path, 'r') as f:
       return dict(tuple(line.replace('\n', '').split('=')) for line
                in f.readlines() if not line.startswith('#'))

def main():
    ### create directories as needed
    for d in DIRS:
            if not os.path.exists(d): 
                try: 
                    os.makedirs(d)
                    print(f"Directory '{d}' created successfully.") 
                except OSError as e: 
                    print(f"Error creating directory '{d}': {e}") 
            else: 
                print(f"Directory '{d}' already exists.") 
                
    sema = threading.Semaphore(value=MAX_THREADS)
    label = datetime.datetime.now().strftime("%Y%m%d_%H%M%S") # Customized label using date time
    ### log in to get API Key
    print("Logging in...\n")
    ENV_VARS = get_env_data_as_dict('.env')
    TOKEN = ENV_VARS['API_TOKEN']
    AUTH_PAYLOAD = {"username": USERNAME, "token": TOKEN}
    apiKey = sendRequest(SERVICE_URL + "login-token", AUTH_PAYLOAD)
    print("API Key: " + apiKey + "\n")
    ### filter scenes
    spatialFilter =  {'filterType':'geojson', 'geoJson':CA_SHAPE}
    acquisitionFilter = {'start':START_DATE, 'end':END_DATE}
    cloudCoverFilter = {'min':0, 'max':CLOUD_COVER_MAX}
    search_payload = {
        'datasetName' : DATASET_NAME,
        'sceneFilter' : {
            'spatialFilter' : spatialFilter,
            'acquisitionFilter' : acquisitionFilter,
            'cloudCoverFilter' : cloudCoverFilter
        }
    }
    scenes = sendRequest(SERVICE_URL + "scene-search", search_payload, apiKey)
    idField = 'entityId'
    ### get most recent entityId for each path/row
    entity_df = pd.DataFrame([[result['publishDate'], 
                               result['displayId'].split("_")[2], 
                               result['entityId']] 
                              for result in scenes['results'] 
                              if result['options']['bulk']], 
                             columns=['date', 'path/row', idField])
    entity_df_grouped = (entity_df
        .sort_values(by='date')
        .groupby('path/row')
        .agg(lambda sd: sd.iloc[0]))
    entityIds = list(entity_df_grouped[idField]) 
    listId = f"temp_{DATASET_NAME}_list"
    scn_list_add_payload = {
        "listId": listId,
        'idField' : idField,
        "entityIds": entityIds,
        "datasetName": DATASET_NAME
    }
    ### add to scene list
    count = sendRequest(SERVICE_URL + "scene-list-add", scn_list_add_payload, apiKey) 
    print(f"{count} scenes added to list")
    download_opt_payload = {
        "listId": listId,
        "datasetName": DATASET_NAME
    }
    products = sendRequest(SERVICE_URL + "download-options", download_opt_payload, apiKey)
    ### Select products
    print("Selecting products...")
    downloads = []
    threads = []
    print("    Selecting band files...")
    for product in products:  
        if product["secondaryDownloads"] is not None and len(product["secondaryDownloads"]) > 0:
            for secondaryDownload in product["secondaryDownloads"]:
                for bandName in BAND_NAMES:
                    if secondaryDownload["bulkAvailable"] and bandName in secondaryDownload['displayId']:
                        downloads.append({"entityId":secondaryDownload["entityId"], "productId":secondaryDownload["id"]})          
    ### get entityIds of most recent download for each path/row                            
    downloads_df = pd.DataFrame(downloads)
    downloads_df[['path/row', 'date', 'band']] = downloads_df['entityId'].str.extract(r".{15}(\d{6}).{10}(\d{8}).{11}(\d)", expand=True)
    grouped_downloads_df = (downloads_df
                            .sort_values('date')
                            .groupby(by=['path/row', 'band'])
                            .agg(lambda sd: sd.iloc[0])
                            .drop(columns=['date']))
    downloads_filtered = grouped_downloads_df.to_dict('records')
    download_req2_payload = {
        "downloads": downloads_filtered,
        "label": label
    }
    ### send download request
    print(f"Sending download request ...")
    download_request_results = sendRequest(SERVICE_URL + "download-request", download_req2_payload, apiKey)
    print(f"Done sending download request") 
    if len(download_request_results['newRecords']) == 0 and len(download_request_results['duplicateProducts']) == 0:
        print('No records returned, please update your scenes or scene-search filter')
        sys.exit() 
    ### attempt the download URLs
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
    
    ### initiate download
    print("\nDownloading files... Please do not close the program\n")
    for thread in threads:
        thread.join()  
        
if __name__ == "__main__":
    main()