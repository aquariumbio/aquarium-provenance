# Aquarium Provenance

Defines code to model provenance derived from an Aquarium execution trace.

Includes a less-than-ideal setup for generating a dump using the script
`trace.sh`.
For the script to work, you have to populate `src/resources.py` with Aquarium details.
Running the script with

```
./trace.sh -p 17987 -o yg-provenance-dump.json
```

will crank up docker-compose, run `src/trace.py`, which will (b/c of the docker-compose setup) write the file into `src`.

