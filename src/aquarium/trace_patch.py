import abc
import logging
import re

from aquarium.provenance import (
    CollectionEntity,
    FileEntity,
    ItemEntity,
    OperationActivity,
    PartEntity,
    PlanTrace,
    select_job
)
from aquarium.trace_visitor import ProvenanceVisitor, FactoryVisitor
from util.plate import well_coordinates, coordinates_for
from collections import defaultdict
from typing import List


class CollectionSourceInferenceVisitor(ProvenanceVisitor):
    """
    Applies heuristic to add sources to the collection based on the sources of
    the parts of the collection
    """

    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_collection(self,
                         collection_entity: CollectionEntity):

        if collection_entity.sources:
            return

        entity_id = collection_entity.item_id
        parts = [entity for _, entity in self.trace.items.items()
                 if entity.is_part()
                 and entity.collection.item_id == entity_id]

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


class FileSourcePruningVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_file(self, file: FileEntity):
        self.prune_file_sources(file)

    def prune_file_sources(self, file_entity: FileEntity):
        """
        Replaces the sources for a FileEntity with a single source.

        A file should only have one source, but depending on associations more
        than one source may be captured.
        This heuristic chooses a source item whose ID is in the name of the
        file.

        Specific case is plate_reader data in yeast gates
        """
        if not file_entity.sources:
            return

        match = re.search('item_([0-9]+)_', file_entity.name)
        if not match:
            return

        file_item_id = match.group(1)
        id_list = [source.item_id for source in file_entity.sources]
        if file_item_id not in id_list:
            logging.error("Item id %s from filename not in sources %s for %s",
                          file_item_id, str(id_list),
                          file_entity.file_id)

        source = self.trace.get_item(file_item_id)
        file_entity.sources = [source]


class FileSourcePrefixVisitor(ProvenanceVisitor):
    """
    A FileVisitor that adds the ID for the source of a file as a prefix to the
    file name.

    Used to avoid name conflicts in situations where files with the same name
    may be written to the same directory.
    An example of this situation is when calibration beads are measured using
    the flow cytometer and the generated file is named A01.fcs.
    Saving this file to the same directory as the cytometry readings for a well
    plate will result in a file name conflict with the first entry in the well.
    """

    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_file(self, file_entity: FileEntity):
        logging.debug("Visiting file %s %s to add prefix",
                      file_entity.file_id, file_entity.name)
        if not file_entity.sources:
            return

        if len(file_entity.sources) > 1:
            return

        source = next(iter(file_entity.sources))
        prefix = source.item_id
        if source.is_part():
            prefix = source.collection.item_id
        file_entity.name = "{}-{}".format(prefix, file_entity.name)
        logging.debug("changing name of %s to %s",
                      file_entity.file_id, file_entity.name)


class OperationProvenanceVisitor(ProvenanceVisitor):
    @abc.abstractmethod
    def __init__(self, *, trace, name):
        self.name = name
        super().__init__(trace)

    def is_match(self, generator):
        return generator.operation_type.name == self.name

    def visit_part(self, part: PartEntity):
        if not part.collection.generator:
            log_missing_generator(part.collection)
            return

        if not part.generator:
            logging.debug("Operation visit part %s operation %s",
                          part.item_id, self.name)
            part.add_generator(part.collection.generator)

    def add_part_attributes(self, part: PartEntity):
        for key, value in part.collection.attributes.items():
            if all(isinstance(elem, list) for elem in value):  # matrix
                if key.endswith('_mat'):
                    part_key = key[:key.rfind('_mat')]
                    i, j = coordinates_for(part.part_ref)
                    entry = value[i][j]
                    if entry:
                        logging.debug("Adding attribute %s: %s to part %s",
                                      part_key, str(entry), part.item_id)
                        part.add_attribute({part_key: entry})


class PassthruOperationVisitor(OperationProvenanceVisitor):
    """
    Heuristic to add sources to a part of a collection that has collection as a
    source and that collection has an object at the same coordinate.

    Assumes operations apply a well-to-well transfer.
    """
    @abc.abstractmethod
    def __init__(self, trace: PlanTrace, name: str):
        super().__init__(trace=trace, name=name)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            self.fix_part_source(part)

    def fix_part_source(self, part: PartEntity):
        if part.sources:
            return

        logging.debug("Passthru visit part %s operation %s",
                      part.item_id,
                      self.name)

        coll_entity = part.collection
        if not coll_entity.sources:
            return

        coll_sources = [
            source for source in coll_entity.sources if source.is_collection()]

        part_ref = part.part_ref
        for source in coll_sources:
            source_id = source.item_id
            source_part_id = source_id + '/' + part_ref
            if self.trace.has_item(source_part_id):
                part.add_source(self.trace.get_item(source_part_id))
                logging.info("use collection routing to add source %s to %s",
                             source_part_id, part.item_id)
            else:
                logging.debug("routing failed, source %s for %s doesn't exist",
                              source_part_id, part.item_id)


class MeasurementVisitor(OperationProvenanceVisitor):
    """
    only add measurement operation attribute to operations that only do the
    measurement.
    """

    instruments_url = 'agave://data-sd2e-community/biofab/instruments'
    accuri_path = 'accuri/5539/11272017/cytometer_configuration.json'
    synergy_path = 'synergy_ht/216503/03132018/platereader_configuration.json'
    accuri_channels = ['FL1-A', 'FL4-A']

    @classmethod
    def accuri_url(cls):
        return "{}/{}".format(cls.instruments_url, cls.accuri_path)

    @classmethod
    def synergy_url(cls):
        return "{}/{}".format(cls.instruments_url, cls.synergy_path)

    @abc.abstractmethod
    def __init__(self, *, trace, name, measurement):
        self.measurement = measurement
        super().__init__(trace=trace, name=name)

    def visit_file(self, file: FileEntity):
        self.find_file_generator(file)

    def visit_operation(self, operation: OperationActivity):
        """
        Adds the measurement_operation attribute to any operation for an
        operation type name that is a key of measurements.

        currently thinking this should only be applied to operations are
        strictly measurement ops.
        """
        if not operation:
            return

        if self.is_match(operation):
            operation.add_attribute({'measurement_operation': True})
            operation.add_attribute(self.measurement)

    @staticmethod
    def get_file_source(file_entity: FileEntity):
        if file_entity.sources:  # if there are sources double check
            if len(file_entity.sources) > 1:
                logging.error("File %s has more than one source %s",
                              file_entity.file_id,
                              [src.item_id for src in file_entity.sources])
                return None

            source = next(iter(file_entity.sources))
            if source.is_part():
                source = source.collection
            return source

    def find_file_generator(self, file_entity: FileEntity):
        """
        If the file entity has no generator, tries to determine the most
        specific generator from the job of the file entity.

        First, looks to see if the operations of the job match the operation of
        this visitor, and if they do, checks these operations against the
        source of the file, if one exists.
        The source is used to filter the job operations by checking that the
        source is either be an input to the operation or is generated by the
        operation.

        Heuristic requires that the file have a single source.
        """
        if file_entity.generator:
            return

        job_ops = [
            op for op in file_entity.job.operations if self.is_match(op)]
        if not job_ops:
            return

        logging.debug("Visiting file %s from MeasurementVisitor",
                      file_entity.file_id)

        ops = job_ops
        source = MeasurementVisitor.get_file_source(file_entity)

        if source:
            if not source.generator:
                logging.error("source %s %s for file %s has no generator",
                              source.item_type, source.item_id,
                              file_entity.file_id)
                return

            ops = [op for op in job_ops
                   if op.has_input(source)
                   or source.generator.operation_id == op.operation_id]
            if not ops:
                msg = "No generator found for file %s matching source %s"
                logging.debug(msg, file_entity.file_id, source.item_id)
                return

        if len(ops) == 1:
            generator = next(iter(ops))
            file_entity.add_generator(generator)
            log_generator_add(generator, 'file', file_entity.file_id)
        elif len(ops) > 1:
            self.trace.add_job(file_entity.job)
            file_entity.add_generator(file_entity.job)
            logging.info("Adding job %s as generator for file %s",
                         file_entity.job.job_id, file_entity.file_id)


class CytometryOperationVisitor(MeasurementVisitor):
    @abc.abstractmethod
    def __init__(self, *, trace, name, measurement):
        self.job_map = dict()
        super().__init__(trace=trace, name=name, measurement=measurement)

    def visit_file(self, file_entity: FileEntity):
        """
        Unless an explicit link is made, we have no way of knowing how to
        connect a file to an operation.
        This method assumes that there is one file per operation in the job for
        a file and allocates one to each file.
        In the case of yeast gates, each file measures from a job has the same
        source.
        """
        super().visit_file(file_entity)

        if file_entity.sources:
            return

        logging.debug("Visiting file %s from CytometryOperationVisitor",
                      file_entity.file_id)

        bead_file_list = self.trace.get_attribute('bead_files')
        if not bead_file_list:
            logging.debug("No bead_files attribute")
            return
        if file_entity.file_id not in bead_file_list:
            logging.debug("File %s is not in bead_files %s",
                          file_entity.file_id, str(bead_file_list))
            return

        job_id = file_entity.job.job_id
        if job_id not in self.job_map:
            job_ops = [op for op in file_entity.job.operations
                       if self.is_match(op)]
            self.job_map[job_id] = job_ops

        if not self.job_map[job_id]:
            logging.error("No generator found for file %s",
                          file_entity.file_id)
            return

        op = self.job_map[job_id].pop()
        file_entity.add_generator(op)
        bead_inputs = op.get_named_inputs('calibration beads')
        bead_arg = next(iter(bead_inputs))
        bead_item = bead_arg.item
        file_entity.add_source(bead_item)
        bead_item.add_attribute({'standard': 'BEAD_FLUORESCENCE'})
        logging.info("Adding beads %s as source for file %s",
                     bead_item.item_id, file_entity.file_id)


class IGEMPlateGeneratorVisitor(OperationProvenanceVisitor):

    @abc.abstractmethod
    def __init__(self, *, trace, name):
        super().__init__(trace=trace, name=name)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            self.fix_igem_attributes(part)

    def fix_igem_attributes(self, part: PartEntity):
        if not part.sample:
            logging.debug("%s %s has no sample", part.item_type, part.item_id)
            return

        row, col = coordinates_for(part.part_ref)
        if part.sample.name == 'Fluorescein Sodium Salt':
            if row > 3:
                logging.error("Found fluorescein %s in row %s > 3",
                              part.item_id, row)
                return
            self.add_to_fluorescence_well(part, col)
        elif part.sample.name == 'LUDOX Stock':
            if row != 4:
                logging.error('Found LUDOX %s in row %s != 4',
                              part.item_id, row)
            self.add_to_volume_well(part, col)
        elif part.sample.name == 'Nuclease-free water':
            if row != 5:
                logging.error('Found Nuclease-free water %s in row %s != 5',
                              part.item_id, row)
            self.add_to_volume_well(part, col)

    def add_to_fluorescence_well(self, part: PartEntity, column):
        fluorescence = part.collection.get_attribute('cal_fluorescence')
        micromoles = list(fluorescence['uM_to_data'])[column]
        unit_str = "{}:micromole".format(micromoles)
        part.add_attribute({'concentration': unit_str})
        part.add_attribute({'volume': '100:microliter'})

    def add_to_volume_well(self, part: PartEntity, column):
        volume = (column // 4 * 100) + 100
        volume_str = "{}:microliter".format(volume)
        part.add_attribute({'volume': volume_str})


class FlowCytometry96WellVisitor(CytometryOperationVisitor):
    def __init__(self, trace=None):
        super().__init__(
            trace=trace,
            name='Flow Cytometry 96 well',
            measurement={
                'measurement_type': 'FLOW',
                'instrument_configuration': self.accuri_url(),
                'channels': self.accuri_channels
            })


class CytometerBeadCalibration(CytometryOperationVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace,
                         name='Cytometer Bead Calibration',
                         measurement={
                             'measurement_type': 'FLOW',
                             'instrument_configuration': self.accuri_url(),
                             'channels': self.accuri_channels
                         })


class MeasureODAndGFP(MeasurementVisitor, PassthruOperationVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace,
                         name='4. Measure OD and GFP',
                         measurement={
                             'measurement_type': 'PLATE_READER',
                             'instrument_configuration': self.synergy_url()
                         })


class IGEMMeasurementVisitor(MeasurementVisitor, IGEMPlateGeneratorVisitor):

    @abc.abstractmethod
    def __init__(self, *, trace=None, name, measurement):
        super().__init__(trace=trace, name=name, measurement=measurement)


class PlateReaderMeasurementVisitor(
        IGEMMeasurementVisitor, PassthruOperationVisitor):

    def __init__(self, trace=None):
        self.factory = None
        self.calibration_plate = None
        super().__init__(trace=trace,
                         name='Plate Reader Measurement',
                         measurement={
                             'measurement_type': 'PLATE_READER',
                             'instrument_configuration': self.synergy_url()
                         })

    def add_factory(self, factory):
        self.factory = factory
        # TODO: deal with mismatched factory.trace.plan_id with trace.plan_id

    def visit_collection(self, collection):
        if not collection.generator:
            log_missing_generator(collection)
            return

        if self.is_match(collection.generator):
            measurement_args = collection.generator.get_named_inputs(
                'Type of Measurement(s)')
            measurement_type = next(iter(measurement_args))
            if not measurement_type.value.startswith('CAL_'):
                self.fix_collection_source(collection)

    def visit_operation(self, operation: OperationActivity):
        if self.is_match(operation):
            measurement_args = operation.get_named_inputs(
                'Type of Measurement(s)')
            measurement_type = next(iter(measurement_args))
            if measurement_type.value.startswith('CAL_'):
                if not self.calibration_plate:
                    logging.error("Expecting calibration plate to exist")
                    return

                self.calibration_plate.add_generator(operation)

    def visit_plan(self, plan: PlanTrace):
        """
        Attempts to resurrect the IGEM protocol plate that is generated by this
        protocol under some circumstances.
        Needs to be done if there is an upload for a calibration OD or GFP file
        associated to the plan, in which case it extracts the item ID from the
        filename, and uses the factory to add the collection to the plan.

        This is the source of all of the nonsense with factories in the
        visitors.
        """
        upload = self.get_calibration_upload(plan)
        filename = None
        if upload:
            filename = upload['upload_file_name']

        if not filename:
            return

        match = re.search('item_([0-9]+)_', filename)
        if not match:
            return

        plate_id = match.group(1)
        self.factory.create_items(item_id=plate_id)
        self.calibration_plate = self.trace.get_item(plate_id)

    def get_calibration_upload(self, plan: PlanTrace):
        for key, value in plan.attributes.items():
            if key.startswith('Calibration_CAL_'):
                return value

    def fix_collection_source(self, collection: CollectionEntity):
        plate_args = collection.generator.get_named_inputs(
            '96 Deep Well Plate')
        if len(plate_args) > 1:
            msg = "Multiple plate inputs to Plate Reader Measurement %s"
            logging.warning(msg, collection.generator.operation_id)
        source_arg = next(iter(plate_args))
        collection.add_source(source_arg.item)
        log_source_add(source_arg.item, collection)


class SynchByODVisitor(MeasurementVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace,
                         name='3. Synchronize by OD',
                         measurement={
                             'measurement_type': 'PLATE_READER',
                             'instrument_configuration': self.synergy_url()
                         })

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            logging.debug("SynchByOD visit part %s operation %s",
                          part.item_id, self.name)
            self.add_part_media(part)
            self.fix_part_source(part)

    def fix_part_source(self, part: PartEntity):
        if len(part.collection.sources) > 1:
            logging.warning("Collection for part has more than one source")
            return

        collection_source = next(iter(part.collection.sources))
        row, col = coordinates_for(part.part_ref)
        abs_part = row * 12 + col
        abs_source = abs_part % 30
        ref = well_coordinates(abs_source // 12, abs_source % 12)
        source_id = "{}/{}".format(collection_source.item_id, ref)
        source = self.trace.get_item(source_id)
        if not source:
            logging.warning("Computed source %s for part %s does not exist",
                            source_id, part.item_id)
            return

        if source.sample.id != part.sample.id:
            msg = "Sample mismatch for source %s (%s) and part %s (%s)"
            logging.error(msg, source_id, source.sample.id,
                          part.item_id, part.sample.id)
            return

        part.add_source(source)
        log_source_add(source, part)

    def add_part_media(self, part: PartEntity):
        media_args = part.generator.get_named_inputs('Type of Media')
        media_name = next(iter(media_args))
        if media_name.value == 'YPAD':
            part.add_attribute({'media': {'sample_id': '11767'}})
        elif media_name.value == 'Synthetic_Complete':
            part.add_attribute({'media': {'sample_id': '11769'}})
        elif media_name.value == 'SC_Sorbitol':
            part.add_attribute({'media': {'sample_id': '22798'}})


class ResuspensionOutgrowthVisitor(IGEMPlateGeneratorVisitor):

    def __init__(self, trace=None):
        super().__init__(trace=trace, name='2. Resuspension and Outgrowth')

    def visit_file(self, file_entity: FileEntity):
        self.fix_file_generators(file_entity)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            self.fix_part_source(part)
            self.add_replicate_attribute(part)

    def fix_part_source(self, part: PartEntity):
        if part.sources:
            return

        logging.debug("ResuspensionOutgrowth visit part %s operation %s",
                      part.item_id, self.name)

        source = None
        plate_args = part.generator.get_named_inputs('Yeast Plate')
        for arg in plate_args:
            if not part.sample:
                logging.error("part %s has no sample", part.item_id)
            elif arg.item.sample.id == part.sample.id:
                source = arg.item
        if source:
            part.add_source(source)
            log_source_add(source, part)

    def add_replicate_attribute(self, part: PartEntity):
        source_reference = part.get_attribute('source_reference')
        if not source_reference:
            return
        if not source_reference.startswith('Yeast Plate'):
            return

        source_components = source_reference.split('/')
        if len(source_components) != 4:
            return

        replicate_id = source_components[3][1:]
        part.add_attribute({'replicate': replicate_id})

    def fix_file_generators(self, file_entity: FileEntity):
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
        if not source.generator:
            logging.error("source %s %s of file %s has no generator",
                          source.item_type, source.item_id,
                          file_entity.file_id)
            return
        if source.generator.is_job():
            return
        source_gen = source.generator
        if self.is_match(source_gen):
            op_list = self.trace.get_operations(input=source.item_id)
            if not op_list:  # IGEM protocol
                file_entity.add_generator(source_gen)
                source.add_attribute({'standard': 'IGEM_protocol'})


class NCInoculationAndMediaVisitor(OperationProvenanceVisitor):
    def __init__(self, trace=None):
        self.factory = None
        super().__init__(trace=trace, name='NC_Inoculation & Media')

    def add_factory(self, factory):
        self.factory = factory
        # TODO: deal with mismatched factory.trace.plan_id with trace.plan_id

    def visit_operation(self, op_activity: OperationActivity):

        if self.is_match(op_activity):
            job = select_job(op_activity.operation)
            upload = next(iter(job.uploads))
            upload_id = upload['id']
            file = self.factory.get_file(upload_id=upload_id)
            file.add_generator(op_activity)
            op_activity.add_attribute(
                {'experimental_design_document': upload_id})

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            logging.debug("NCInoculationAndMedia visit part %s operation %s",
                          part.item_id, self.name)
            self.add_part_attributes(part)


class NCLargeVolumeInductionVisitor(OperationProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace, name='NC_Large_Volume_Induction')

    def visit_collection(self, collection: CollectionEntity):
        if not collection.generator:
            log_missing_generator(collection)
            return

        if self.is_match(collection.generator):
            self.fix_collection_source(collection)

    def fix_collection_source(self, collection: CollectionEntity):
        if collection.sources:
            return

        plate_args = collection.generator.get_named_inputs(
            '96 Well Plate in')
        arg = next(iter(plate_args))
        if arg:
            source = arg.item
            collection.add_source(source)
            log_source_add(source, collection)
        else:
            logging.warning("Failed to find source for %s %s",
                            collection.item_type, collection.item_id)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.collection.generator):
            logging.debug("NCLargeVolumeInduction visit part %s operation %s",
                          part.item_id, self.name)
            self.fix_part_source(part)
            self.add_part_attributes(part)

    def fix_part_source(self, part: PartEntity):
        if part.sources:
            return
        if not part.collection.sources:
            logging.warning("Collection %s has no sources",
                            part.collection.item_id)
            return

        transfer_coords = part.collection.get_attribute(
            'deep_well_transfer_coords')
        i, j = coordinates_for(part.part_ref)
        source_collection = next(iter(part.collection.sources))
        source_id = "{}/{}".format(source_collection.item_id,
                                   transfer_coords[i][j])
        source = self.trace.get_item(source_id)
        part.add_source(source)
        log_source_add(source, part)


class NCSamplingVisitor(OperationProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace, name='NC_Sampling')

    def visit_collection(self, collection: CollectionEntity):
        if not collection.generator:
            log_missing_generator(collection)
            return

        if self.is_match(collection.generator):
            self.fix_collection_source(collection)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.collection.generator):
            logging.debug("NCSampling visit part %s operation %s",
                          part.item_id, self.name)
            self.fix_part_source(part)
            self.add_part_attributes(part)

    def fix_collection_source(self, collection: CollectionEntity):
        """
        Fixes collection routing for NC_Sampling.
        Takes four 24 well plates and constructs three 96 well plates, all of
        which should have the 24 well plates as sources.
        """
        for input in collection.generator.inputs:
            collection.add_source(input.item)

    def fix_part_source(self, part: PartEntity):
        """
        Fixes part routing for NC_Sampling.
        Protocol takes four 24 well plates and constructs 96 well plates.
        These 4 plates were constructed by NC_Large_Volume_Induction by
        selecting wells from a 96 well plate, and this protocol inverts the
        process.

        """
        i, j = coordinates_for(part.part_ref)

        # determine first entry in transfer_coordinates for appropriate plate
        anchor_i = i % 2  # either 0 or 1
        anchor_j = 6 * (j // 6)  # either 0 or 6
        anchor = well_coordinates(anchor_i, anchor_j)

        for input in part.generator.inputs:
            transfer_coords = input.item.get_attribute(
                'deep_well_transfer_coords')
            if transfer_coords[0][0] == anchor:
                source_collection = input.item

        source_id = "{}/{}".format(source_collection.item_id,
                                   well_coordinates(i // 2, j % 6))
        source = self.trace.get_item(source_id)
        part.add_source(source)
        log_source_add(source, part)


class NCRecoveryVisitor(PassthruOperationVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace, name='NC_Recovery')

    def visit_collection(self, collection: CollectionEntity):
        if not collection.generator:
            log_missing_generator(collection)
            return

        if self.is_match(collection.generator):
            self.fix_collection_source(collection)

    def fix_collection_source(self, collection):
        plate_args = collection.generator.get_named_inputs(
            '96 Deep Well Plate in')
        arg = next(iter(plate_args))
        if arg:
            source = arg.item
            collection.add_source(source)
            log_source_add(source, collection)
        else:
            logging.warning("Failed to find source for %s %s",
                            collection.item_type, collection.item_id)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            logging.debug("NCRecovery visit part %s operation %s",
                          part.item_id, self.name)
            self.add_part_attributes(part)


class NCPlateReaderInductionVisitor(PassthruOperationVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace, name='NC_Plate_Reader_Induction')

    def visit_collection(self, collection: CollectionEntity):
        if not collection.generator:
            log_missing_generator(collection)
            return

        if self.is_match(collection.generator):
            self.fix_collection_sources(collection)
            self.fix_timeseries_file(collection)

    def fix_collection_sources(self, collection):
        plate_args = collection.generator.get_named_inputs(
            '96 Deep Well plate')
        arg = next(iter(plate_args))
        if arg:
            source = arg.item
            collection.add_source(source)
            log_source_add(source, collection)
        else:
            logging.warning("Failed to find source for %s %s",
                            collection.item_type, collection.item_id)

    def visit_part(self, part: PartEntity):
        super().visit_part(part)

        if not part.generator:
            log_missing_generator(part)
            return

        if self.is_match(part.generator):
            logging.debug("NCPlateReaderInduction visit part %s operation %s",
                          part.item_id, self.name)
            self.add_part_attributes(part)

    def fix_timeseries_file(self, collection: CollectionEntity):
        file_name = collection.get_attribute('timeseries_filename')
        if not file_name:
            return

        files = [file for _, file in self.trace.files.items()
                 if file.name.startswith(file_name)]
        file_entity = next(iter(files))
        file_entity.add_source(collection)
        logging.info("Adding %s %s as source for %s %s",
                     collection.item_type, collection.item_id,
                     'file', file_entity.file_id)
        file_entity.add_generator(collection.generator)
        log_generator_add(collection.generator, 'file', file_entity.file_id)


class PropagateReplicateVisitor(ProvenanceVisitor):

    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_part(self, part: PartEntity):
        self.propagate_replicate(part)

    def visit_item(self, item: ItemEntity):
        self.propagate_replicate(item)

    def propagate_replicate(self, item_entity):
        replicate = item_entity.get_attribute('replicate')
        if replicate:
            return replicate

        if not item_entity.sample:
            logging.error("%s %s has no sample",
                          item_entity.item_type, item_entity.item_id)
            return

        matching_source = None
        for source in item_entity.sources:
            if not source.sample:
                logging.error("source %s %s has no sample",
                              source.item_type, source.item_id)
                return None
            if source.sample.id == item_entity.sample.id:
                matching_source = source
        if not matching_source:
            return None

        replicate = self.propagate_replicate(matching_source)
        if replicate:
            item_entity.add_attribute({'replicate': replicate})
            return replicate

        return None


def log_missing_generator(item_entity):
    logging.warning("%s %s has no generator, can't fix sources",
                    item_entity.item_type, item_entity.item_id)


def group_files_by_job(file_list: List[FileEntity]):
    file_map = defaultdict(list)
    for file in file_list:
        job_id = str(file.upload.job.id)
        file_map[job_id].append(file)
    return file_map


def create_trace_fix_visitor():
    """
    Creates visitor to apply heuristic fixes to a PlanTrace object.
    """
    visitor = FactoryVisitor()

    visitor.add_visitor(FixMessageVisitor())
    visitor.add_visitor(FileSourcePruningVisitor())
    visitor.add_visitor(FlowCytometry96WellVisitor())
    visitor.add_visitor(CytometerBeadCalibration())
    visitor.add_visitor(MeasureODAndGFP())
    visitor.add_visitor(PlateReaderMeasurementVisitor())
    visitor.add_visitor(SynchByODVisitor())
    visitor.add_visitor(ResuspensionOutgrowthVisitor())
    visitor.add_visitor(NCInoculationAndMediaVisitor())
    visitor.add_visitor(NCLargeVolumeInductionVisitor())
    visitor.add_visitor(NCSamplingVisitor())
    visitor.add_visitor(NCRecoveryVisitor())
    visitor.add_visitor(NCPlateReaderInductionVisitor())
    visitor.add_visitor(CollectionSourceInferenceVisitor())
    visitor.add_visitor(PropagateReplicateVisitor())
    visitor.add_visitor(FileSourcePrefixVisitor())

    return visitor


class FixMessageVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace)

    def visit_plan(self, plan: PlanTrace):
        logging.info("Applying heuristic fixes to plan %s", plan.plan_id)


class ChallengeProblemTraceVisitor(ProvenanceVisitor):

    def __init__(self, *, trace=None, labname, challenge_problem):
        self.labname = labname
        self.challenge_problem = challenge_problem
        super().__init__(trace)

    def visit_plan(self, plan: PlanTrace):
        plan.add_attribute({'lab': self.labname})
        cp_attr = 'challenge_problem'
        if not plan.has_attribute(cp_attr):
            logging.warning("Adding \'%s\' plan attribute", cp_attr)
            if self.challenge_problem == 'yg':
                plan.add_attribute({cp_attr: 'YEAST_GATES'})
            elif self.challenge_problem == 'nc':
                plan.add_attribute({cp_attr: 'NOVEL_CHASSIS'})
            elif self.challenge_problem == 'ps':
                plan.add_attribute({cp_attr: 'PROTEIN_DESIGN'})

        exp_ref_attr = 'experiment_reference'
        if not plan.has_attribute(exp_ref_attr):
            logging.warning("Adding \'%s\' plan attribute", exp_ref_attr)
            if self.challenge_problem == 'yg':
                plan.add_attribute({exp_ref_attr: 'Yeast-Gates'})
            elif self.challenge_problem == 'nc':
                plan.add_attribute({exp_ref_attr: 'NovelChassis-NAND-Gate'})


def log_source_add(source, item):
    logging.info("Adding %s %s as source for %s %s",
                 source.item_type, source.item_id,
                 item.item_type, item.item_id)


def log_generator_add(generator, type, entity_id):
    logging.info("Adding %s as generator for %s %s",
                 generator.operation_id, type, entity_id)
