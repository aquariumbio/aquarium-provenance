# Aquarium Provenance

A [trident](http://klavinslab.org/trident)-based library for a
[PROV-DM](https://www.w3.org/TR/2013/REC-prov-dm-20130430/) inspired model of
Aquarium provenance.

## Installing

You can install this package using pip with

```bash
pip install --upgrade git+https://github.com/klavinslab/aquarium-provenance.git
```

## Using the package

The following code illustrates constructing an `aquarium.ProvenanceTrace` object for one or more plans.

```python
from aquarium.trace.factory import TraceFactory
from pydent import AqSession
from resources import resources

def main():
    # create pydent session using resources.py
    session = AqSession(
        resources['aquarium']['login'],
        resources['aquarium']['password'],
        resources['aquarium']['aquarium_url']
    )

    trace = TraceFactory.create_from(session=session,
                                     experiment_id='AN ID FOR EXPERIMENT',
                                     plans=[ONE_OR_MORE_PLAN_IDs],
                                     visitor=None)


if __name__ == "__main__":
    main()
```

Note that `pydent.AqSession` is required and
This assumes a `resources.py` file that defines a hash 

```python
resources = {
    "aquarium": {
        "aquarium_url": "AQUARIUM_SERVER_URL",
        "login": "USERNAME",
        "password": "USERPASSWORD"
    }
}
```

## Protocol conventions

The following conventions are required for the factory to automatically collect provenance:

1. All tracked items should be an input or output of an operation type.
   An internally generated item cannot be discovered without a heuristic fix (see below).

2. All items should have identified source items.

   - If the sample types match, indicate routing in the operation type definition.
     Routing may be one-to-many, so include routing on all outputs.
   - For parts of a collection or other outputs, use the `PartProvenance` module in the [UW BIOFAB standard libraries](https://github.com/klavinslab/standard-libraries) to indicate sources.
     This protocol illustrates using the library to indicate the provenance of mixing two inputs.

     ```ruby
     # frozen_string_literal: true

     needs 'Standard Libs/AssociationManagement'

     class Protocol
        include AssociationManagement
        include PartProvenance

        def mix(first:, second:, mix_output:)
          one_associations = AssociationMap.new(first)
          two_associations = AssociationMap.new(second)
          mix_associations = AssociationMap.new(mix_output)

          show do
            title 'Mix stuff'
          end

          add_provenance(
            from: first, from_map: one_associations,
            to: mix_output, to_map: mix_associations
          )
          add_provenance(
            from: second, from_map: two_associations,
            to: mix_output, to_map: mix_associations
          )
          one_associations.save
          two_associations.save
          mix_associations.save
        end

        def main
          operations.retrieve.make
          operations.each do |operation|
            mix(first: operation.input('one').item,
                second: operation.input('two').item,
                mix_output: operation.output('mix').item)
          end
          operations.store
        end
      end
     ```

3. Only associate a data file with the operation that generated it and the item to which it corresponds.

   For a measurement, only associate the file with the measured item.
   For a collection, this means that the protocol should associate the data to
   the collection if the measurement is of the whole collection.
   Otherwise, associate the data to the part that was measured.

   Files that capture information about an operation should only be associated
   with the operation.

4. A descriptive attribute of an item should be associated to the item allowing it to be captured by the provenance script.

## Heuristic Fixes

The best way to get good provenance is to build protocols that follow the conventions above, but there may be situations where that is difficult.
In this case, it is possible to patch provenance using provenance visitors similar to those defined in the [operation_visitor.py](https://github.com/klavinslab/aquarium-provenance/blob/master/src/aquarium/trace/operation_visitor.py) file.

A visitor is a class that implements one or more methods of [`aquarium.trace.visitor.ProvenanceVisitor`](https://github.com/klavinslab/aquarium-provenance/blob/f9fb07480b1cc6f58388b9e077d3ce66e9fbcf59/src/aquarium/trace/visitor.py#L15).
Look in [operation_visitor.py](https://github.com/klavinslab/aquarium-provenance/blob/master/src/aquarium/trace/operation_visitor.py) for examples.

Suppose you write a visitor named `MyOperationVisitor`, you can add this to your script by importing it and changing the script to

```python
    fix_visitor = MyOperationVisitor()
    trace = TraceFactory.create_from(session=session,
                                     experiment_id='AN ID FOR EXPERIMENT',
                                     plans=[ONE_OR_MORE_PLAN_IDs],
                                     visitor=fix_visitor)
```

If you have more than one visitor use the `BatchVisitor` to manage them:

```python
from aquarium.trace.visitor import BatchVisitor

...

    fix_visitor = BatchVisitor()
    fix_visitor.add_visitor(MyOperationVisitor())
    fix_visitor.add_visitor(MyOtherVisitor())
    trace = TraceFactory.create_from(session=session,
                                     experiment_id='AN ID FOR EXPERIMENT',
                                     plans=[ONE_OR_MORE_PLAN_IDs],
                                     visitor=fix_visitor)
```
