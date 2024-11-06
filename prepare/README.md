# 15 Minute City

This is a script and a visualization page for presenting a 15-minute
city calculation. Should be pretty simple, no installation required.

## Preparing the data

First, download an `.osm.pbf` file for your country from [Geofabrik](https://download.geofabrik.de/).
This all will work faster if you trim it with [Osmium Tool](https://osmcode.org/):
open [the bounding box tool](https://boundingbox.klokantech.com/), choose an area, copy the CSV code,
and paste it into the command line:

    osmium extract --bbox <your_csv_bbox> -o city.osm.pbf country.osm.pbf

Now, find the area object on OpenStreetMap: use the query tool (the question mark button) and tap somewhere
inside. Scroll the results down and find the area object you need: a city, or a suburb. Tap it
and copy the identifier into `relation_id` or `way_id` in `config.toml`.

Adjust other settings in the configuration file if you need.

Then install Python dependencies:

    python3 -m venv .venv
    .venv/bin/pip install -r requirements.txt

And extract the data with the script:

    ./15minute.py -i city.osm.pbf -a area.json -p poi.json -b buildings.json

Having downloaded and extracted everything, you might want to filter buildings. For example,
remove those in industrial areas or cemeteries. Those tend to attract unwanted attention,
and people there rarely need the amenities.

To filter buildings, I use [QGIS](https://qgis.org). Add an XYZ Tiles OpenStreetMap layer,
then add a vector layer from `buildings.json`, turn on the editing mode and use the freehand
selection tool. Having finished, save the layer.

Now, on to isochrone calculation. For that you would need Java and GraphHopper. Install the
former, and download [the latest jar](https://github.com/graphhopper/graphhopper/releases)
file for the latter.

TODO: profiles for graphhopper
