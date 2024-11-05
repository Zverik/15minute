#!.venv/bin/python
import argparse
import json
import requests
import osmium
import tomllib
import shapely
import pyproj
import os
from collections import defaultdict
from shapely.geometry import shape, mapping
from shapely.ops import transform as shapely_transform
from typing import Any


config: dict = {}


class POI:
    def __init__(self, coords: tuple[float, float], typ: str,
                 name: str | None):
        self.coords = coords
        self.typ = typ
        self.name = name

    def to_feature(self, props: dict[str, Any] | None = None) -> dict:
        feature: dict[str, Any] = {
            'type': 'Feature',
            'geometry': {
                'type': 'Point',
                'coordinates': self.coords,
            },
            'properties': {
                'type': self.typ,
            },
        }
        if self.name:
            feature['properties']['name'] = self.name
        if props:
            feature['properties'].update(props)
        return feature


class BuildingsAndPOI:
    def __init__(self):
        self.poi: defaultdict[str, list[POI]] = defaultdict(list)
        self.buildings: list[shapely.Geometry] = []
        self._all_buildings: shapely.Geometry | None = None
        self.to_3857 = pyproj.Transformer.from_crs(
            pyproj.CRS('epsg:4326'), pyproj.CRS('epsg:3857'), always_xy=True)
        self.to_4326 = pyproj.Transformer.from_crs(
            pyproj.CRS('epsg:3857'), pyproj.CRS('epsg:4326'), always_xy=True)

    def buffered(self, area: shapely.Geometry,
                 buffer: int) -> shapely.Geometry:
        if buffer <= 0:
            return area
        transformed = shapely_transform(self.to_3857.transform, area)
        transformed = shapely.buffer(transformed, buffer)
        return shapely_transform(self.to_4326.transform, transformed)

    def load_poi(self, data: dict):
        self.poi.clear()
        for feature in data['features']:
            self.poi[feature['properties']['layer']].append(POI(
                feature['geometry']['coordinates'],
                feature['properties']['type'],
                feature['properties'].get('name'),
            ))

    def load_buildings(self, data: dict):
        self.buildings.clear()
        for f in data['features']:
            self.buildings.append(shape(f['geometry']))
        self._all_buildings = None

    def save_poi(self) -> dict:
        features: list[dict] = []
        for layer, pois in self.poi.items():
            features.extend(p.to_feature({'layer': layer}) for p in pois)
        return {
            'type': 'FeatureCollection',
            'features': features,
        }

    def save_buildings(self) -> dict:
        features: list[dict] = []
        for b in self.buildings:
            features.append({
                'type': 'Feature',
                'properties': {},
                'geometry': mapping(b),
            })
        return {
            'type': 'FeatureCollection',
            'features': features,
        }

    def add_building(self, building):
        self.buildings.append(building)
        self._all_buildings = None

    @property
    def need_reading(self):
        return not self.poi or not self.buildings

    @property
    def all_buildings(self) -> shapely.Geometry:
        global config
        simplify_tolerance = config['openstreetmap'].get('simplify', 0.001)
        buffer = config['openstreetmap'].get('building_buffer', 0)
        if not self._all_buildings and self.buildings:
            transformed = [shapely_transform(self.to_3857.transform, b)
                           for b in self.buildings]
            if buffer > 0:
                for i in range(len(transformed)):
                    transformed[i] = shapely.buffer(transformed[i], buffer)

            self._all_buildings = shapely.union_all(transformed)
            transformed.clear()
            self._all_buildings = shapely.simplify(
                self._all_buildings, simplify_tolerance)
            self._all_buildings = shapely_transform(
                self.to_4326.transform, self._all_buildings)
        return self._all_buildings

    def add_poi(self, layer: str, poi: POI):
        self.poi[layer].append(poi)


def isochrone(point: tuple[float, float], profile: str,
              minutes: int) -> shapely.Geometry:
    global config
    gp_endpoint = config['isochrones']['graphhopper']
    resp = requests.get(gp_endpoint, {
        'profile': profile,
        'reverse_flow': 'true',
        'time_limit': minutes * 60,
        'point': f'{point[1]},{point[0]}',
    })
    if resp.status_code != 200:
        raise Exception(f'Failed to query {resp.url}: {resp.text}')
    data = resp.json()
    return shape(data['polygons'][0]['geometry'])


def isochrones(points: list[tuple[float, float]], profile: str,
               minutes: int) -> shapely.Geometry:
    global config
    simplify_tolerance = config['openstreetmap'].get('simplify', 0.001)
    polys = [isochrone(p, profile, minutes) for p in points]
    return shapely.simplify(shapely.union_all(polys), simplify_tolerance)


def download_area(options) -> shapely.Geometry:
    global config
    osm_cfg = config['openstreetmap']
    area_id: int = 0
    OSM_API = 'https://api.openstreetmap.org/api/0.6'
    if osm_cfg.get('relation_id'):
        resp = requests.get(
            f'{OSM_API}/relation/{osm_cfg["relation_id"]}/full')
        area_id = osm_cfg['relation_id'] * 2 + 1
    elif osm_cfg.get('way_id'):
        resp = requests.get(
            f'{OSM_API}/way/{osm_cfg["way_id"]}/full')
        area_id = osm_cfg['way_id'] * 2
    elif osm_cfg.get('bbox'):
        return shapely.box(*osm_cfg['bbox'])
    if resp.status_code != 200:
        raise Exception(
            f'Could not request area from OSM: {resp.status_code}')

    for obj in osmium.FileProcessor(osmium.io.FileBuffer(resp.content, 'osm'))\
            .with_areas()\
            .with_filter(osmium.filter.GeoInterfaceFilter(True)):
        if obj.is_area() and obj.id == area_id:
            return shape(obj.__geo_interface__['geometry'])
    raise Exception(f'OpenStreetMap did not return a proper area: {resp.url}')


def scan_buildings_and_poi(area: shapely.Geometry, bap: BuildingsAndPOI,
                           osmfile: str):
    global config

    tags_to_layer: defaultdict[str, list[str]] = defaultdict(list)
    for layer, tags in config['layers'].items():
        for tag in tags:
            tags_to_layer[tag].append(layer)

    shapely.prepare(area)
    poi_buffer = config['openstreetmap'].get('poi_area_buffer', 0)
    poi_area = bap.buffered(area, poi_buffer)
    shapely.prepare(poi_area)

    for obj in osmium.FileProcessor(osmfile)\
            .with_areas()\
            .with_filter(osmium.filter.GeoInterfaceFilter(True)):
        if obj.is_node() or obj.is_area():
            if 'building' in obj.tags:
                shp = shape(obj.__geo_interface__['geometry'])
                if shapely.intersects(area, shp):
                    bap.add_building(shp)
            for k, v in obj.tags:
                kv = f'{k}={v}'
                layers = tags_to_layer.get(kv)
                if layers:
                    center = shapely.centroid(shape(
                        obj.__geo_interface__['geometry']))
                    if shapely.intersects(poi_area, center):
                        coords = (shapely.get_x(center), shapely.get_y(center))
                        for layer in layers:
                            bap.add_poi(layer, POI(
                                coords, kv, obj.tags.get('name')))
    return bap


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Downloads and prepares data for a 15-minute city '
        'assessment')
    parser.add_argument(
        '-c', '--config', default='config.toml',
        help='TOML configuration file name, default=config.toml')
    parser.add_argument(
        '-a', '--area',
        help='JSON file for the area polygon (not downloaded if exists)')
    parser.add_argument(
        '-i', '--input',
        help='OSM file for extracting buildings and POI')
    parser.add_argument(
        '-p', '--poi', help='Storage for POI')
    parser.add_argument(
        '-b', '--buildings', help='GeoJSON storage for buildings')
    parser.add_argument(
        '-o', '--output', type=argparse.FileType('w'),
        help='GeoJSON with isochrones by layer')
    options = parser.parse_args()

    with open(options.config, 'rb') as f:
        config = tomllib.load(f)

    # Read or download the area.
    area: shapely.Geometry | None = None
    if options.area and os.path.exists(options.area):
        try:
            with open(options.area, 'r') as f1:
                area = shape(json.load(f1))
        except Exception:
            pass
    if not area:
        area = download_area(options)
        if options.area:
            with open(options.area, 'w') as f2:
                json.dump(mapping(area), f2)

    # Read or download buildings and POI.
    bap = BuildingsAndPOI()
    if options.poi and os.path.exists(options.poi):
        try:
            with open(options.poi, 'r') as f3:
                data = json.load(f3)
                bap.load_poi(data)
        except Exception:
            pass
    if options.buildings and os.path.exists(options.buildings):
        try:
            with open(options.buildings, 'r') as f3:
                data = json.load(f3)
                bap.load_buildings(data)
        except Exception:
            pass

    if not bap.poi and not options.input:
        raise Exception('Please specify input OSM file.')

    if options.input and bap.need_reading:
        scan_buildings_and_poi(area, bap, options.input)
        if bap.poi and options.poi:
            with open(options.poi, 'w') as f4:
                json.dump(bap.save_poi(), f4, ensure_ascii=False)
        if bap.buildings and options.buildings:
            with open(options.buildings, 'w') as f5:
                json.dump(bap.save_buildings(), f5)

    if options.output:
        # Build isochrones for each layer.
        iso_features: list[dict] = []
        for k, v in config['isochrones'].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                for layer, coords in bap.poi.items():
                    iso = isochrones([p.coords for p in coords], v[0], v[1])
                    no_buildings = shapely.difference(bap.all_buildings, iso)
                    iso_features.append({
                        'type': 'Feature',
                        'geometry': mapping(iso),
                        'properties': {
                            'layer': layer,
                            'profile': k,
                            'amenities': len(coords),
                        },
                    })
        json.dump({
            'type': 'FeatureCollection',
            'features': iso_features,
        }, options.output)
