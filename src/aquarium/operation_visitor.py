import abc
import json
import logging
import re

from aquarium.provenance import (
    CollectionEntity,
    FileEntity,
    OperationActivity,
    PartEntity,
    PlanTrace
)
from aquarium.trace_visitor import ProvenanceVisitor
from util.plate import well_coordinates, coordinates_for


class OperationProvenanceVisitor(ProvenanceVisitor):
    @abc.abstractmethod
    def __init__(self, *, trace, name):
        self.name = name
        super().__init__(trace)

    def is_match(self, generator):
        if generator is None:
            return False

        # TODO: can we not check operation type of the job?
        if generator.is_job():
            return False

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
            logging.debug("Operation %s is not in generators %s for file %s",
                          self.name,
                          str([op.operation_id for op
                               in file_entity.job.operations]),
                          file_entity.file_id)
            return

        logging.debug("Visiting file %s from MeasurementVisitor with ops %s",
                      file_entity.file_id,
                      str([op.operation_id for op in job_ops]))

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

    def add_job(self, job_id, file_entity):
        if job_id not in self.job_map:
            job_ops = [op for op in file_entity.job.operations
                       if self.is_match(op)]
            self.job_map[job_id] = job_ops

    def get_generator(self, file_entity):
        job_id = file_entity.job.job_id
        self.add_job(job_id, file_entity)

        if not self.job_map[job_id]:
            logging.error("No generator found for file %s",
                          file_entity.file_id)
            return None

        return self.job_map[job_id].pop()

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

        logging.debug("Visiting file %s from CytometryOperationVisitor",
                      file_entity.file_id)

        self.get_bead_source(file_entity)

    def get_bead_source(self, file_entity: FileEntity):
        if file_entity.sources:
            return

        bead_file_list = self.trace.get_attribute('bead_files')
        if not bead_file_list:
            logging.debug("No bead_files attribute")
            return
        if file_entity.file_id not in bead_file_list:
            logging.debug("File %s is not in bead_files %s",
                          file_entity.file_id, str(bead_file_list))
            return

        op = self.get_generator(file_entity)
        if not op:
            return
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
        if not fluorescence:
            logging.error(
                "Expecting collection %s to have cal_fluorescence attribute",
                part.collection.item_id)
            return

        micromoles = list(fluorescence['uM_to_data'])[column]
        unit_str = "{}:micromole".format(micromoles)
        part.add_attribute({'concentration': unit_str})
        part.add_attribute({'volume': '100:microliter'})

    def add_to_volume_well(self, part: PartEntity, column):
        # TODO: change so that can use in add_to_fluorescence well
        volume = (column // 4 * 100) + 100
        volume_str = "{}:microliter".format(volume)
        part.add_attribute({'volume': volume_str})


class FlowCytometry96WellAbstractVisitor(CytometryOperationVisitor):

    @abc.abstractmethod
    def __init__(self, *, trace, name, measurement):
        self.job_map = dict()
        super().__init__(trace=trace, name=name, measurement=measurement)

    def visit_file(self, file_entity: FileEntity):
        super().visit_file(file_entity)

        if file_entity.sources:
            return

        if not file_entity.generator:
            logging.debug("File %s has no generator", file_entity.file_id)
            return

        logging.debug("Visiting file %s from FlowCytometry96WellVisitor",
                      file_entity.file_id)

        if file_entity.generator.is_job():
            op = self.get_generator(file_entity)
            if not op:
                return
            file_entity.add_generator(op)

        plate_inputs = file_entity.generator.get_named_inputs('96 well plate')
        if not plate_inputs:
            return

        plate_arg = next(iter(plate_inputs))
        plate_item = plate_arg.item
        file_entity.add_source(plate_item)
        logging.info("Adding plate %s as source for file %s",
                     plate_item.item_id, file_entity.file_id)


class FlowCytometry96WellVisitor(FlowCytometry96WellAbstractVisitor):
    def __init__(self, trace=None):
        super().__init__(
            trace=trace,
            name='Flow Cytometry 96 well',
            measurement={
                'measurement_type': 'FLOW',
                'instrument_configuration': self.accuri_url(),
                'channels': self.accuri_channels
            })


class FlowCytometry96WellOldVisitor(FlowCytometry96WellAbstractVisitor):
    def __init__(self, trace=None):
        super().__init__(
            trace=trace,
            name='Flow Cytometry 96 well (old)',
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

    def visit_part(self, part):
        if self.is_match(part.generator):
            super().visit_part(part)
            copy_attribute_from_source(part, 'media')


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

    def visit_file(self, file_entity: FileEntity):
        super().visit_file(file_entity)

        if not file_entity.generator:
            return

        if self.is_match(file_entity.generator):
            if not file_entity.sources:
                self.add_calibration_file_source(file_entity)
            elif len(file_entity.sources) > 1:
                operation_id = file_entity.generator.operation_id
                sources = [source for source in file_entity.sources
                           if source.generator
                           and source.generator.operation_id == operation_id]
                if not sources:
                    return
                source = next(iter(sources))
                file_entity.sources = [source]

    def add_calibration_file_source(self, file_entity: FileEntity):
        if not self.calibration_plate:
            return

        plate_id = PlateReaderMeasurementVisitor.get_plate_id(
            file_entity.name)
        if plate_id and plate_id == self.calibration_plate.item_id:
            file_entity.add_source(self.calibration_plate)

    def visit_operation(self, operation: OperationActivity):
        if self.is_match(operation):
            measurement_args = operation.get_named_inputs(
                'Type of Measurement(s)')
            measurement_type = next(iter(measurement_args))
            if measurement_type.value.startswith('CAL_'):
                if not self.calibration_plate:
                    return

                self.calibration_plate.add_generator(operation)

    @staticmethod
    def get_plate_id(filename):
        match = re.search('item(_|)([0-9]+)_', filename)
        if match:
            return match.group(2)

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
            logging.debug("No calibration plate found in plan associations")
            return

        plate_id = PlateReaderMeasurementVisitor.get_plate_id(filename)
        if not plate_id:
            return

        self.factory.get_item(item_id=plate_id)
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
            add_media_attribute(part)
            self.fix_part_source(part)
            copy_attribute_from_source(part, 'media')

    def fix_part_source(self, part: PartEntity):
        """
        Determines the source for the part for a collection generated by a
        Synchronize By OD operation.
        Assumes that is preceded by an operation that has similar arguments to
        Resuspension and Outgrowth that indicate how many parts of the input
        collection contain samples (as opposed to controls).
        """
        if len(part.collection.sources) > 1:
            logging.warning("Collection for part %s has more than one source",
                            part.item_id)
            return

        if part.sources:
            return

        logging.warning("Part %s has no sources in SynchByOD", part.item_id)

        collection_source = next(iter(part.collection.sources))
        if not collection_source.generator:
            logging.warning("Source %s has no generator",
                            collection_source.generator)
            return

        # Assumes preceded by operation that knows number of replicates and
        # input plates
        rep_list = collection_source.generator.get_named_inputs(
            'Biological Replicates')
        plate_list = collection_source.generator.get_named_inputs(
            'Yeast Plate')
        if not rep_list or not plate_list:
            logging.warning("Unable to compute number of parts for source %s",
                            collection_source.item_id)
            return
        replicates = int(next(iter(rep_list)).value)
        num_source_parts = replicates * len(plate_list)
        logging.info("Plate %s has %s sample parts",
                     collection_source.item_id, num_source_parts)

        row, col = coordinates_for(part.part_ref)
        abs_part = row * 12 + col

        if abs_part < num_source_parts * 3:
            abs_source = abs_part % num_source_parts
            ref = well_coordinates(abs_source // 12, abs_source % 12)
        else:
            # controls are added to plate after sample wells
            ref = part.part_ref
            # TODO: deal with controls from other sources

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


def add_media_attribute(entity):
    media_args = entity.generator.get_named_inputs('Type of Media')
    if not media_args:
        logging.debug("Operation %s has no media argument",
                      entity.generator.operation_id)
        return

    media_arg = next(iter(media_args))
    if media_arg.value == 'YPAD':
        sample_id = '11767'
    elif media_arg.value == 'Synthetic_Complete':
        sample_id = '11769'
    elif media_arg.value == 'SC':
        sample_id = '11769'
    elif media_arg.value == 'SC_Sorbitol':
        sample_id = '22798'
    elif media_arg.value == 'SC_Glycerol_EtOH':
        sample_id = '22799'
    else:
        logging.error("Media type %s not recognized", media_arg.value)
        return

    logging.debug("Adding media type %s to %s %s",
                  sample_id, entity.item_type, entity.item_id)
    entity.add_attribute({'media': {'sample_id': sample_id}})


def copy_attribute_from_source(entity, key):
    if entity.has_attribute(key):
        return

    if not entity.sources:
        return

    for source in entity.sources:
        attribute = source.get_attribute(key)
        if attribute:
            logging.debug('Copying attribute with key %s to %s %s',
                          key, entity.item_type, entity.item_id)
            entity.add_attribute({key: attribute})
            return


class YeastOvernightSuspension(OperationProvenanceVisitor):
    def __init__(self, trace=None):
        super().__init__(trace=trace, name='Yeast Overnight Suspension')

    def visit_item(self, item_entity):
        if not item_entity.generator:
            return

        logging.debug("Visiting item %s for Yeast Overnight Suspension",
                      item_entity.item_id)
        if self.is_match(item_entity.generator):
            add_media_attribute(item_entity)


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
            logging.debug("Visiting part %s with ResuspensionOutgrowthVisitor",
                          part.item_id)
            self.fix_part_source(part)
            self.add_colony_attribute(part)
            add_media_attribute(part)

    def fix_part_source(self, part: PartEntity):
        if part.sources:
            return

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

    def add_colony_attribute(self, part: PartEntity):
        logging.debug("Searching for colony attribute on part %s",
                      part.item_id)
        # newest versions of protocol should have a source attribute
        source_attribute = part.get_attribute('source')
        if source_attribute:
            logging.error(
                "Part %s has source attribute, but not yet implemented",
                part.item_id
            )
            return

        # older versions of protocols used a source_reference
        source_reference = part.get_attribute('source_reference')
        if source_reference:
            if source_reference.startswith('Yeast Plate'):
                logging.debug(
                    "Adding colony from source_reference of %s from %s",
                    part.item_id,
                    json.dumps(source_reference, indent=2))
                source_components = source_reference.split('/')
                if len(source_components) != 4:
                    return
                source_id = source_components[1]
                colony = source_components[3][1:]
                part.add_attribute({
                    'yeast_plate': source_id,
                    'colony': colony
                })
                return
        logging.debug("Part %s has no source_reference attribute",
                      part.item_id)

        if len(part.sources) != 1:
            logging.debug("Part %s should only have one source", part.item_id)
            return

        source_item = next(iter(part.sources))
        if source_item:
            dest_attribute = source_item.get_attribute('destination')
            if dest_attribute:
                row, column = coordinates_for(part.part_ref)
                collection_id = part.collection.item_id
                dest_list = [obj for obj in dest_attribute if (
                    str(obj['id']) == collection_id
                    and obj['row'] == row
                    and obj['column'] == column
                )]
                if len(dest_list) == 1:
                    dest = next(iter(dest_list))
                    part.add_attribute({
                        'yeast_plate': source_item.item_id,
                        'colony': dest['source_colony']
                    })

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
            job_activity = self.factory.get_operation_job(op_activity)
            upload = next(iter(job_activity.job.uploads))
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
        if not self.trace.has_item(source_id):
            logging.debug("Source %s for part %s does not exist",
                          source_id, part.item_id)
            return

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


def log_missing_generator(item_entity):
    logging.warning("%s %s has no generator, can't fix sources",
                    item_entity.item_type, item_entity.item_id)


def log_source_add(source, item):
    logging.info("Adding %s %s as source for %s %s",
                 source.item_type, source.item_id,
                 item.item_type, item.item_id)


def log_generator_add(generator, type, entity_id):
    logging.info("Adding %s as generator for %s %s",
                 generator.operation_id, type, entity_id)
