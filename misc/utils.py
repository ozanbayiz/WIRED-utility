import numpy as np
import geopandas as gpd
import rasterio
from rasterio.features import rasterize

import requests
import folium
from folium.plugins import HeatMap
from folium.raster_layers import ImageOverlay

import matplotlib.pyplot as plt
from PIL import Image
import tempfile

from tqdm import tqdm

def vector_to_heatmap_overlay(geoms, layer_name, aoi_geom):
    ##TODO: adjust sampling resolution
    sampling_resolution = 500
    minx, miny, maxx, maxy = aoi_geom.bounds
    width_raw = maxx - minx
    height_raw = maxy - miny
    width_adj = int(width_raw * sampling_resolution)
    height_adj = int(height_raw * sampling_resolution)
    # this does something?
    trans = rasterio.transform.from_bounds(minx, miny, maxx, maxy, width_adj, height_adj)
    data = np.zeros((height_adj, width_adj))
    # get overlaps
    for geom in tqdm(geoms):
        rasterized_geom = rasterize(shapes=[(geom, 1)], fill=0, transform=trans, out_shape=(height_adj, width_adj), all_touched=True)
        data += rasterized_geom

    plt.imshow(data)
    # create image
    cmap = plt.get_cmap('viridis')
    norm = plt.Normalize(vmin=data.min(), vmax=data.max())
    image_data = cmap(norm(data))
    alpha_channel = np.where(data > 0, 255, 0)
    rgba_image_data = np.dstack((image_data[:, :, :3] * 255, alpha_channel)).astype(np.uint8)
    image = Image.fromarray(rgba_image_data, 'RGBA')
    with tempfile.NamedTemporaryFile(delete=False, suffix='.png') as tmp_file:
        image.save(tmp_file.name, format='PNG')
        tmp_file_path = tmp_file.name
    # create layer
    img_overlay = ImageOverlay(
        name=layer_name,
        image=tmp_file_path,
        bounds=[[miny, minx], [maxy, maxx]],
        opacity=0.6
    )
    return img_overlay