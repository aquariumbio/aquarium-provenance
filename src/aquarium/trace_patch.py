import logging
from aquarium.provenance import (
    CollectionEntity,
    FileEntity,
    JobActivity,
    OperationActivity,
    PartEntity,
    PlanTrace
)
from collections import defaultdict
from typing import Dict, List


def file_generator_patch(trace: PlanTrace):
    """
    For files of the trace with no generator, discovers the generating
    operation by looking for an operation that has the source of the file as an
    input, and the operation is a measurement.

    Heuristic requires that the file have a single source, and that the
    operation be tagged as a measurement.
    """
    # TODO: use job operations for upload
    for _, file_entity in trace.files.items():
        if file_entity.generator:
            continue

        # if no source this hack wont work
        sources = file_entity.sources
        if not sources:
            continue

        # if the file has more than one source, this hack is harder
        if len(sources) > 1:
            msg = "File %s has more than one source. Bailing..."
            logging.warning(msg, file_entity.file_id)
            continue

        source = next(iter(sources))
        if source.item_type == 'part':
            source = source.collection

        ops = [op for op in trace.get_operations(source.item_id)
               if op.is_measurement()]

        # need exactly one
        if not ops:
            continue

        if len(ops) > 1:
            jobs = [job.id for job in [
                max(op.operation.jobs, key=lambda job: job.updated_at) for op in ops]]
            if jobs.count(jobs[0]) == len(jobs):
                generator_id = jobs[0]
                generator = JobActivity(job_id=generator_id, operations=ops)
                trace.add_job(generator)
                file_entity.add_generator(generator)
                msg = "Adding job %s as generator for file %s"
                logging.info(msg, generator_id, file_entity.file_id)
            else:
                msg = "Source %s %s for file %s is input to operations in jobs %s. Bailing..."
                logging.warning(msg, source.item_type,
                                source.item_id,
                                file_entity.file_id,
                                jobs)
                continue
        else:
            generator = ops[0]
            generator_id = generator.operation_id
            msg = "Adding operation %s as generator for file %s"
            logging.info(msg, generator_id, file_entity.file_id)
            file_entity.add_generator(generator)


def infer_part_source_from_collection(
        trace: PlanTrace,
        part_entity: PartEntity):
    """
    Heuristic to add sources to a part of a collection that has collection as a
    source and that collection has an object at the same coordinate.

    Assumes a well-to-well transfer.
    """
    if part_entity.sources:
        return

    coll_entity = part_entity.collection
    if not coll_entity.sources:
        return

    coll_sources = [
        source for source in coll_entity.sources if source.is_collection()]

    part_ref = part_entity.part_ref
    for source in coll_sources:
        source_id = source.item_id
        source_part_id = source_id + '/' + part_ref
        if trace.has_item(source_part_id):
            part_entity.add_source(trace.get_item(source_part_id))
            logging.info("use collection routing to add source %s to %s",
                         source_part_id, part_entity.item_id)
        else:
            logging.debug("routing failed, source %s for %s does not exist",
                          source_part_id, part_entity.item_id)


def infer_collection_source_from_parts(
        trace: PlanTrace,
        collection_entity: CollectionEntity):
    """
    Applies heuristic to add sources to the collection based on the sources of
    the parts of the collection
    """
    if collection_entity.sources:
        return

    entity_id = collection_entity.item_id
    parts = [entity for _, entity in trace.items.items()
             if entity.is_part() and entity.collection.item_id == entity_id]

    sources = set()
    for part in parts:
        for source in part.sources:
            if source.is_part():
                source = source.collection
            if source.item_id not in sources:
                logging.info("using part routing to add source %s to %s",
                             source.item_id, entity_id)
                collection_entity.add_source(source)
                sources.add(source.item_id)


def tag_measurement_operations(
        trace: PlanTrace,
        measurements: Dict[str, Dict[str, str]]):
    """
    Adds the measurement_operation attribute to any operation for an operation
    type name that is in the list.

    currently thinking this should only be applied to operations are strictly
    measurement ops.
    """
    for _, operation in trace.operations.items():
        operation_name = operation.operation_type.name
        if operation_name in measurements:
            operation.add_attribute({'measurement_operation': True})
            operation.add_attribute(measurements[operation_name])


def add_measurement_attributes(trace: PlanTrace):
    """
    only add measurement operation attribute to operations that only do the
    measurement.
    """
    instruments_url = 'agave://data-sd2e-community/biofab/instruments'
    accuri_path = 'accuri/5539/11272017/cytometer_configuration.json'
    synergy_path = 'synergy_ht/216503/03132018/platereader_configuration.json'
    accuri_url = "{}/{}".format(instruments_url, accuri_path)
    synergy_url = "{}/{}".format(instruments_url, synergy_path)
    tag_measurement_operations(
        trace,
        {
            'Flow Cytometry 96 well': {
                'measurement_type': 'FLOW',
                'instrument_configuration': accuri_url
            },
            '4. Measure OD and GFP': {
                'measurement_type': 'PLATE_READER',
                'instrument_configuration': synergy_url
            },
            '3. Synchronize by OD': {
                'measurement_type': 'PLATE_READER',
                'instrument_configuration': synergy_url
            },
            'Cytometer Bead Calibration': {
                'measurement_type': 'FLOW',
                'instrument_configuration': accuri_url
            }
        }
    )


def fix_plate_reader_file_sources(file_entity: FileEntity):
    """
    Replaces the sources for a FileEntity with a single source.

    A file should only have one source, but depending on associations more than
    one source may be captured.
    This heuristic chooses a source item whose ID is in the name of the file.

    Specific case is plate_reader data in yeast gates
    """
    # TODO: this should use job information
    for source in file_entity.sources:
        if source.item_id in file_entity.name:
            msg = "Replacing sources for file %s with %s %s"
            logging.info(msg, file_entity.file_id,
                         source.item_type, source.item_id)
            file_entity.sources = [source]
            return

    logging.warning("Unable to select single source for file %s",
                    file_entity.file_id)


def fix_plate_reader_file_generators(
        trace: PlanTrace,
        file_entity: FileEntity):
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

    source = next(iter(file_entity.sources))
    if source.generator.is_job():
        return
    source_gen = source.generator
    op_list = trace.get_operations(source.item_id)
    if source_gen.operation_type.name == "2. Resuspension and Outgrowth":
        if not op_list:  # IGEM protocol
            file_entity.add_generator(source_gen)
            source.add_attribute({'standard': 'IGEM_protocol'})


def fix_resuspension_outgrowth_output(
        trace: PlanTrace,
        coll_entity: CollectionEntity):
    """if not coll_entity.generator:  # wont work if no generator
        return

    generator = coll_entity.generator
    if generator.operation_type.name != '2. Resuspension and Outgrowth':
        return"""

    plates = [arg.item for arg in coll_entity.generator.inputs
              if arg.name == 'Yeast Plate']
    source_map = {plate.sample: plate for plate in plates}

    for part in coll_entity.parts:
        if part.sources:
            continue
        if part.sample in source_map.keys():
            source = source_map[part.sample]
            part.add_source(source)
            coll_entity.add_source(source)
            logging.info("Adding %s %s as source for %s %s",
                         source.item_type, source.item_id,
                         part.item_type, part.item_id)
        else:
            logging.warning("Unable to infer source for %s %s",
                            part.item_type, part.item_id)


def fix_collection_sources(trace: PlanTrace, collection: CollectionEntity):
    generator_name = collection.generator.operation_type.name
    if generator_name == '2. Resuspension and Outgrowth':
        fix_resuspension_outgrowth_output(trace, collection)

    infer_collection_source_from_parts(trace, collection)


def fix_part_sources(trace: PlanTrace, part: PartEntity):
    op_names = ['4. Measure OD and GFP']  # pass through operations
    generator_name = part.collection.generator.operation_type.name
    if generator_name in op_names:  # pass through op
        infer_part_source_from_collection(trace, part)


def log_missing_generator(collection: CollectionEntity):
    logging.warning("%s %s has no generator, can't fix sources",
                    collection.item_type, collection.item_id)


def fix_item_provenance(trace: PlanTrace, stop_list: List[str]):
    no_sources = [item for _, item in trace.items.items()
                  if not item.sources and item.item_id not in stop_list]
    collections = [item for item in no_sources if item.is_collection()]
    for collection in collections:
        if not collection.generator:
            log_missing_generator(collection)
            continue
        fix_collection_sources(trace, collection)

    parts = [part for part in no_sources if part.is_part()]
    for part in parts:
        if not part.collection.generator:
            log_missing_generator(part.collection)
            continue
        fix_part_sources(trace, part)

    items = [item for item in no_sources if item.is_item()]
    if len(items) > 0:
        logging.warning("Items with no sources: %s",
                        str([item.item_id for item in items]))


def group_files_by_job(file_list: List[FileEntity]):
    file_map = defaultdict(list)
    for file in file_list:
        job_id = str(file.upload.job.id)
        file_map[job_id].append(file)
    return file_map


def get_bead_input(operation: OperationActivity):
    for arg in operation.inputs:
        if arg.name == 'calibration beads':
            return arg.item


def fix_calibration_bead_provenance(
        trace: PlanTrace,
        file_list: List[FileEntity],
        bead_ops: List[OperationActivity]):
    # TODO: add error checking
    if len(file_list) == len(bead_ops):
        ops_iter = iter(bead_ops)
        for file_entity in file_list:
            op = next(ops_iter)
            file_entity.add_generator(op)
            bead_item = get_bead_input(op)
            file_entity.add_source(bead_item)
            bead_item.add_attribute({'standard': 'BEAD_FLUORESCENCE'})
            logging.info("Adding beads %s as source for file %s",
                         bead_item.item_id, file_entity.file_id)
    else:
        logging.warning("mismatch of files and cytometry operations")


def find_file_sources(trace: PlanTrace, no_source_files: List[FileEntity]):
    bead_op_names = ['Flow Cytometry 96 well', 'Cytometer Bead Calibration']
    file_map = group_files_by_job(no_source_files)
    for _, file_list in file_map.items():
        file = next(iter(file_list))  # pick one file
        ops = [trace.get_operation(str(op.id))
               for op in file.upload.job.operations]

        # Only case at the moment is calibration beads for flow cytometry
        bead_ops = [op for op in ops
                    if op.operation_type.name in bead_op_names]
        if bead_ops:
            fix_calibration_bead_provenance(trace, file_list, bead_ops)


def prune_file_sources(
        trace: PlanTrace,
        multiple_source_files: List[FileEntity]):
    # TODO: fix so verifies that dealing with plate reader files
    for file_entity in multiple_source_files:
        fix_plate_reader_file_sources(file_entity)


def fix_file_provenance(trace: PlanTrace):
    no_source = [file for _, file in trace.files.items() if not file.sources]
    find_file_sources(trace, no_source)

    multiple_source = [file for _, file in trace.files.items()
                       if len(file.sources) > 1]
    prune_file_sources(trace, multiple_source)

    file_generator_patch(trace)
    for _, file in trace.files.items():
        if not file.generator:
            fix_plate_reader_file_generators(trace, file)


def fix_trace(trace: PlanTrace, stop_list: List[str]):
    """
    Applies heuristic fixes to a PlanTrace object.

    Specifically, adds attributes to measurement operations; fixes sources for
    items, and fixes sources and generators for files.
    """
    add_measurement_attributes(trace)
    fix_item_provenance(trace, stop_list)
    fix_file_provenance(trace)
