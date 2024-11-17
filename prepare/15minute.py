#!.venv/bin/python
import argparse
import json
import math
import os
import osmium
import pyproj
import requests
import shapely
import tomllib
from collections import defaultdict
from shapely.geometry import shape, mapping
from shapely.ops import transform as shapely_transform
from typing import Any


config: dict = {}


class Transformer3857:
    def __init__(self):
        self._to_3857 = pyproj.Transformer.from_crs(
            pyproj.CRS('epsg:4326'), pyproj.CRS('epsg:3857'), always_xy=True)
        self._to_4326 = pyproj.Transformer.from_crs(
            pyproj.CRS('epsg:3857'), pyproj.CRS('epsg:4326'), always_xy=True)

    def to_3857(self, shape: shapely.Geometry) -> shapely.Geometry:
        return shapely_transform(self._to_3857.transform, shape)

    def to_4326(self, shape: shapely.Geometry) -> shapely.Geometry:
        return shapely_transform(self._to_4326.transform, shape)


class Area:
    def __init__(self, area: shapely.Geometry | None = None):
        self._transformer = Transformer3857()
        self._shape: shapely.Geometry | None = None
        if area:
            self.set_shape(area)

    @property
    def shape(self) -> shapely.Geometry:
        return self._shape

    def set_shape(self, shape: shapely.Geometry):
        self._shape = shape
        shapely.prepare(self._shape)

    def __len__(self) -> int:
        return 0 if not self._shape else 1

    @property
    def lat_multiplier(self) -> float:
        lat = shapely.get_y(shapely.centroid(self._shape))
        return math.cos(math.radians(lat))

    def intersects(self, other: shapely.Geometry) -> bool:
        return shapely.intersects(self._shape, other)

    def buffered(self, buffer: float):
        if buffer <= 0:
            return self
        buffer /= self.lat_multiplier
        transformed = self._transformer.to_3857(self._shape)
        transformed = shapely.buffer(transformed, buffer)
        return Area(self._transformer.to_4326(transformed))

    def simplified(self, tolerance: float):
        if tolerance <= 0:
            return self
        tolerance /= self.lat_multiplier
        transformed = self._transformer.to_3857(self._shape)
        transformed = shapely.simplify(transformed, tolerance)
        return Area(self._transformer.to_4326(transformed))

    def load(self, filename: str | None):
        if filename and os.path.exists(filename):
            try:
                with open(filename, 'r') as f1:
                    self.set_shape(shape(json.load(f1)))
            except Exception:
                pass

    def save(self, filename: str | None):
        if filename:
            with open(filename, 'w') as f2:
                json.dump(mapping(self._shape), f2)


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
    def __init__(self, area: Area):
        self.area = area
        self.poi: defaultdict[str, list[POI]] = defaultdict(list)
        self.buildings: list[shapely.Geometry] = []
        self._all_buildings: shapely.Geometry | None = None
        self.transformer = Transformer3857()

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

    def load_all(self, poi, buildings):
        if poi and os.path.exists(poi):
            try:
                with open(poi, 'r') as f3:
                    data = json.load(f3)
                    self.load_poi(data)
            except Exception:
                pass

        if buildings and os.path.exists(buildings):
            try:
                with open(buildings, 'r') as f3:
                    data = json.load(f3)
                    self.load_buildings(data)
            except Exception:
                pass

    def save_all(self, poi, buildings):
        if self.poi and poi:
            with open(poi, 'w') as f4:
                json.dump(self.save_poi(), f4, ensure_ascii=False)
        if self.buildings and buildings:
            with open(buildings, 'w') as f5:
                json.dump(self.save_buildings(), f5)

    def add_building(self, building):
        self.buildings.append(building)
        self._all_buildings = None

    @property
    def need_reading(self):
        return not self.poi or not self.buildings

    def remove_small_holes(self, geom: shapely.Geometry,
                           min_size: int) -> shapely.Geometry:
        parts = shapely.get_parts(geom)  # TODO: test on a single Polygon
        for i in range(len(parts)):
            p = parts[i]
            if not isinstance(p, shapely.Polygon):
                continue

            if len(p.interiors) > 0:
                fixed_rings = [r for r in p.interiors
                               if shapely.area(shapely.Polygon(r)) > min_size]
                if len(fixed_rings) < len(p.interiors):
                    parts[i] = shapely.Polygon(p.exterior, fixed_rings)
        if len(parts) == 1:
            return parts[0]
        return shapely.multipolygons(parts)

    @property
    def all_buildings(self) -> shapely.Geometry:
        global config
        lm = self.area.lat_multiplier
        simplify_tolerance = config['openstreetmap'].get(
            'simplify', 0) / lm
        buffer = config['openstreetmap'].get('building_buffer', 0) / lm
        hole_size = config['openstreetmap'].get(
            'building_min_hole_area', 0) / lm / lm
        if not self._all_buildings and self.buildings:
            transformed = [self.transformer.to_3857(b)
                           for b in self.buildings]
            if buffer > 0:
                for i in range(len(transformed)):
                    transformed[i] = shapely.buffer(transformed[i], buffer)

            self._all_buildings = shapely.union_all(transformed)
            transformed.clear()
            self._all_buildings = shapely.simplify(
                self._all_buildings, simplify_tolerance)
            if hole_size:
                self._all_buildings = self.remove_small_holes(
                    self._all_buildings, hole_size)
            self._all_buildings = self.transformer.to_4326(
                self._all_buildings)
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
    simplify_tolerance = config['openstreetmap'].get('simplify', 10)
    polys = [isochrone(p, profile, minutes) for p in points]
    shape = Area(shapely.union_all(polys))
    return shape.simplified(simplify_tolerance).shape


def download_area() -> shapely.Geometry:
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


def scan_buildings_and_poi(area: Area, bap: BuildingsAndPOI, osmfile: str):
    global config

    tags_to_layer: defaultdict[str, list[str]] = defaultdict(list)
    for layer, tags in config['layers'].items():
        for tag in tags:
            tags_to_layer[tag].append(layer)

    poi_buffer = config['openstreetmap'].get('poi_area_buffer', 0)
    poi_area = area.buffered(poi_buffer)
    need_buildings = not bap.buildings

    for obj in osmium.FileProcessor(osmfile)\
            .with_areas()\
            .with_filter(osmium.filter.GeoInterfaceFilter(True)):
        if obj.is_node() or obj.is_area():
            if need_buildings and 'building' in obj.tags:
                shp = shape(obj.__geo_interface__['geometry'])
                if area.intersects(shp):
                    if obj.is_node():
                        shp = shapely.buffer(shp, 0.0001)
                    bap.add_building(shp)
            for k, v in obj.tags:
                kv = f'{k}={v}'
                layers = tags_to_layer.get(kv)
                if layers:
                    center = shapely.centroid(shape(
                        obj.__geo_interface__['geometry']))
                    if poi_area.intersects(center):
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
        '-B', '--all-buildings', type=argparse.FileType('w'),
        help='GeoJSON with all buildings merged into a single polygon')
    parser.add_argument(
        '-o', '--output', type=argparse.FileType('w'),
        help='GeoJSON with isochrones by layer')
    parser.add_argument(
        '--coverage', type=argparse.FileType('w'),
        help='GeoJSON with non-covered buildings by layer')
    parser.add_argument(
        '-O', '--package', type=argparse.FileType('w'),
        help='Export JSON for the display tool with everything')
    options = parser.parse_args()

    with open(options.config, 'rb') as f:
        config = tomllib.load(f)

    # Read or download the area.
    area = Area()
    area.load(options.area)
    if not area:
        area.set_shape(download_area())
        area.save(options.area)

    # Read or download buildings and POI.
    bap = BuildingsAndPOI(area)
    bap.load_all(options.poi, options.buildings)

    if not bap.poi and not options.input:
        raise Exception('Please specify input OSM file.')

    if options.input and bap.need_reading:
        scan_buildings_and_poi(area, bap, options.input)
        bap.save_all(options.poi, options.buildings)

    if options.all_buildings and bap.buildings:
        json.dump(mapping(bap.all_buildings), options.all_buildings)

    profiles: list[str] = []
    iso_features: list[dict] = []
    if options.output or options.coverage or options.package:
        # Build isochrones for each layer.
        na_buildings: list[dict] = []
        for k, v in config['isochrones'].items():
            if isinstance(v, list) and len(v) == 2 and isinstance(v[0], str):
                profiles.append(k)
                for layer, coords in bap.poi.items():
                    iso = isochrones([p.coords for p in coords], v[0], v[1])
                    if options.output or options.package:
                        iso_features.append({
                            'type': 'Feature',
                            'geometry': mapping(iso),
                            'properties': {
                                'layer': layer,
                                'profile': k,
                                'amenities': len(coords),
                            },
                        })

                    if options.coverage:
                        no_buildings = shapely.difference(
                            bap.all_buildings, iso)
                        na_buildings.append({
                            'type': 'Feature',
                            'geometry': mapping(no_buildings),
                            'properties': {
                                'layer': layer,
                                'profile': k,
                                'amenities': len(coords),
                            },
                        })

        if options.output:
            json.dump({
                'type': 'FeatureCollection',
                'features': iso_features,
            }, options.output)

        if options.coverage:
            json.dump({
                'type': 'FeatureCollection',
                'features': na_buildings,
            }, options.output)

    if options.package:
        layers: dict[str, dict[str, Any]] = {}
        for layer, pois in bap.poi.items():
            isolines = [f for f in iso_features
                        if f['properties']['layer'] == layer]
            if isolines:
                layers[layer] = {
                    'poi': [p.to_feature() for p in pois],
                    'isochrones': {p['properties']['profile']: p
                                   for p in isolines},
                }
        json.dump({
            'area': mapping(area.shape),
            'buildings': mapping(bap.all_buildings),
            'layers': layers,
            'profiles': profiles,
        }, options.package)
