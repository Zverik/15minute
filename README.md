# 15 Minute City

This is a script and a visualization page for presenting a 15-minute
city calculation. Should be pretty simple, no installation required.

For an example of an output, see [github.io](https://zverik.github.io/15minute/).

## Preparing the data

Open the terminal in the `prepare` directory.

### Aquiring the data

First, download an `.osm.pbf` file for your country from [Geofabrik](https://download.geofabrik.de/).
This all will work faster if you trim it with [Osmium Tool](https://osmcode.org/):
open [the bounding box tool](https://boundingbox.klokantech.com/), choose an area, copy the CSV code,
and paste it into the command line:

    osmium extract --bbox <your_csv_bbox> -o city.osm.pbf country.osm.pbf

Now, find the area object on OpenStreetMap: use the query tool (the question mark button) and tap somewhere
inside. Scroll the results down and find the area object you need: a city, or a suburb. Tap it
and copy the identifier into `relation_id` or `way_id` in `config.toml`.

Adjust other settings in the configuration file if you need.

### First run

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

### Second run

Now, on to isochrone calculation. For that you would need Java and GraphHopper. Install the
former, and download [the latest jar](https://github.com/graphhopper/graphhopper/releases)
file for the latter.

The configuration for GraphHopper is in the `graphhopper.yaml`. Look for
the `datareader.file` line at the top: change the value to your pbf file name.
Everything else can stay the same. If GH gives an error on start-up,
it usually tells you what to fix.

Run the router using the appropriate jar file name:

    java -jar graphhopper-web-10.0.jar server graphhopper.yaml

And then build the isochrones:

    ./15minute.py -a area.json -p poi.json -b buildings.json -O 15minute.json

When everything is over, you will get a `15minute.json` file with all the data required
for visualization. Alternatively, you can use `-o` key for producing isochrone polygons
as a GeoJSON, and `-B` for producing a multipolygon with all the buildings in one,
buffered and simplified. Try those to see what the script outputs.

## Publishing the visualization

It's simple. Get the json file you've built in the last step and put it next to the
`display/index.html`. Then open the html file and replace `nomme15` with the
name of your file (which should have the `.json` extension).

Optionally you can also tweak the initial `center` and `zoom` of the map, and initial
values of layers and profile:

    var currentLayers = new Set(['shops']);
    var currentProfile = 'bike';

To check how it looks, run `python -m http.server` in the directory with the files,
and open the URL it shows when started. To publish, just deploy the two files (html
and json) somewhere, e.g. on GitHub Pages.

If you've got more files, you can use the single html file with a query parameter:
e.g. `https://somewhere/?tallinn` to show data from `tallinn.json` in the same directory.

### What do things mean

On the interactive map:

* Choose layers and profiles in the top left box.
* Red polygons mark buildings that are outside of a 15-minute area.
* Toggle isochrones in the box, so they replace buildings and are drawn as light blue polygons.
* Yellow dots are points of interest for the layer. Hover the mouse cursor to see their names.

## Author and License

Written by Ilya Zverev, published under the ISC license. Feel free to do anything,
and I'll be happy to hear that you used the tool.
