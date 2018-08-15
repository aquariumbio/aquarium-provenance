import abc
import argparse
import json
from pydent import AqSession
from provenance.aquarium import (
    TraceFactory,
    file_generator_patch,
    tag_measurement_operations)
from resources import resources


def add_yg_op_attributes(trace, ):
    """
    only add measurement operation attribute to operations that only do the
    measurement.
    """
    tag_measurement_operations(
        trace,
        ['Flow Cytometry 96 well', '4. Measure OD and GFP', '3. Synchronize by OD']
    )


def fix_plate_reader_file_sources(file_entity):
    """
    Fixes a misinterpretation issue with plate reader files in yeast gates
    where a file may be associated with multiple items to make computations
    easier.

    The correct source ID is usually the first in the list, but file names have
    the ID, so checking just to be certain.
    """
    for source in file_entity.sources:
        if source.item_id in file_entity.name:
            msg = "Replacing sources for file {} with {} {}"
            print(msg.format(file_entity.file_id,
                             source.item_type, source.item_id))
            file_entity.sources = [source]
            return


def fix_plate_reader_file_generators(file_entity, trace):
    """
    YG plate reader files generated in three places:
    - IGEM protocol plate created by Resuspension and Outgrowth
    - Initial OD in Synch by OD
    - Final reading in Measure OD & GFP
    """
    if file_entity.generator:
        return
    if not file_entity.sources:
        return
    if len(file_entity.sources) > 1:
        return

    source = file_entity.sources[0]
    if source.generator.is_job():
        return
    source_gen = source.generator
    input_list = trace.input_list[source.item_id]
    if source_gen.operation_type.name == "2. Resuspension and Outgrowth":
        if not input_list:  # IGEM protocol
            file_entity.add_generator(source_gen)
            source.add_attribute({'standard': 'IGEM_protocol'})


def fix_bead_files(trace, plan):
    """
    The protocol doesn't link the calibration beads to either the operation or
    item, so end up as stray uploads.
    """
    bead_uploads = [up.id for up in [
        assoc.upload for assoc in plan.data_associations
        if assoc.upload and 'BEAD' in assoc.key]]
    flow_ops = [op for _, op in trace.operations.items(
    ) if op.operation_type.name == 'Flow Cytometry 96 well']
    bead_inputs = list()
    for inputs in [op.inputs for op in flow_ops]:
        for arg in inputs:
            if arg.name == 'calibration beads':
                bead_inputs.append(arg.item_id)
    # TODO: check that the counts are the same
    for i in range(len(bead_inputs)):
        file_entity = trace.get_file(bead_uploads[i])
        file_entity.add_generator(flow_ops[i])
        file_entity.add_source(trace.get_item(bead_inputs[i]))
    # TODO: print message


def yeast_gates_patch(trace, plan):
    trace.add_attribute({'challenge_problem': 'YEAST_GATES'})
    add_yg_op_attributes(trace)
    fix_bead_files(trace, plan)
    source_fix = [entity for _, entity in trace.files.items()
                  if len(entity.sources) > 1]
    for entity in source_fix:
        fix_plate_reader_file_sources(entity)
    file_generator_patch(trace)
    for _, entity in trace.files.items():
        if not entity.generator:
            fix_plate_reader_file_generators(entity, trace)


def check_entities(entity_map, stop_list):
    for _, entity in entity_map.items():
        if entity.item_id in stop_list:
            continue

        if not entity.generator:
            print("{} {} has no generators".format(
                entity.item_type, entity.item_id))
        if not entity.sources:
            print("{} {} has no sources".format(
                entity.item_type, entity.item_id))


def check_files(file_map):
    for _, entity in file_map.items():
        if not entity.generator:
            print("{} {} has no generators".format(
                entity.name, entity.file_id))
        if not entity.sources:
            print("{} {} has no sources".format(entity.name, entity.file_id))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p", "--plan_id",
                        help="the ID of the plan",
                        required=True)
    parser.add_argument("-o", "--output",
                        help="the output file")
    parser.add_argument("-v", "--validate",
                        action="store_true",
                        help="check provenance capture")
    args = parser.parse_args()

    session = AqSession(
        resources['login'], resources['password'], resources['aquarium_url'])

    plan = session.Plan.find(args.plan_id)

    trace = TraceFactory.create_from(session=session, plan=plan)
    trace.add_attribute({'lab': 'UW_BIOFAB'})
    yeast_gates_patch(trace, plan)

    if args.validate:
        check_files(trace.files)
        check_entities(
            trace.items, ['109594', '43625', '43624', '43626', '43627', '118359', '139257'])

    if args.output:
        with open(args.output, 'w') as file:
            file.write(json.dumps(trace.as_dict(), indent=2))


if __name__ == "__main__":
    main()
