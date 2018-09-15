import json
import logging
import re
from collections import defaultdict
from collections.abc import Mapping
from aquarium.provenance import (CollectionEntity,
                                 ItemEntity,
                                 FileEntity,
                                 JobActivity,
                                 OperationActivity,
                                 OperationArgument,
                                 OperationInput,
                                 OperationParameter,
                                 PartEntity,
                                 PlanTrace)
from util.plate import well_coordinates, coordinates_for


class TraceFactory:
    """
    Defines a factory object to create a PlanTrace from a pydent.model.Plan.
    """

    def __init__(self, *, session, trace):
        self.trace = trace
        self.session = session

    @staticmethod
    def create_from(*, session, plan, visitor=None):
        """
        Creates a PlanTrace for the plan from the Aquarium session.
        """
        factory = TraceFactory(
            session=session,
            trace=PlanTrace(plan_id=plan.id, name=plan.name)
        )

        # do associations first so that files are found
        for association in plan.data_associations:
            if association.upload:
                upload_id = str(association.upload.id)
                logging.debug("plan %s has upload %s",
                              factory.trace.plan_id, association.key)
                factory.get_file(upload_id=upload_id)
                if (association.key.endswith('BEAD_UPLOAD')):
                    factory._add_bead_file(upload_id)
            elif association.object:
                logging.debug("plan %s has association %s",
                              factory.trace.plan_id, association.key)
                if is_upload(association):
                    upload_id = str(association.object[association.key]['id'])
                    factory.get_file(upload_id=upload_id)
                    if association.key.endswith('BEAD_UPLOAD'):
                        factory._add_bead_file(upload_id)
                factory.trace.add_attribute(association.object)

        for operation in plan.operations:
            factory._create_operation(operation)

        if visitor:
            TraceFactory._apply(factory, visitor)

        return factory.trace

    def _add_bead_file(self, upload_id):
        upload_list = self.trace.get_attribute('bead_files')
        if not upload_list:
            upload_list = list()
        if upload_id not in upload_list:
            upload_list.append(upload_id)
            self.trace.add_attribute({'bead_files': upload_list})

    @staticmethod
    def _apply(factory, visitor):
        visitor.add_trace(factory.trace)
        visitor.add_factory(factory)

        factory.trace.apply(visitor)
        for operation in factory.trace.get_operations():
            operation.apply(visitor)

        for collection in factory.trace.get_collections():
            collection.apply(visitor)

        for part in factory.trace.get_parts():
            part.apply(visitor)

        for item in factory.trace.get_items():
            item.apply(visitor)

        for file in factory.trace.get_files():
            file.apply(visitor)

    def _add_item_entity(self, *, entity, generator=None):
        if generator:
            entity.add_generator(generator)
        if not self.trace.has_item(entity.item_id):
            self.trace.add_item(entity)

    def _create_argument(self, field_value, op_activity):
        item_id = field_value.child_item_id
        if item_id:
            if not self.trace.has_item(item_id):
                if is_input(field_value):
                    self.create_items(
                        item_id=item_id
                    )
                else:
                    self.create_items(
                        item_id=item_id,
                        generator=op_activity
                    )
            item_entity = self.trace.get_item(item_id)
            routing_id = None
            if field_value.field_type:
                routing_id = field_value.field_type.routing
                msg = "Field type %s role %s array %s routing %s op %s"
                logging.debug(msg, field_value.field_type.name,
                              field_value.field_type.role,
                              field_value.field_type.array,
                              field_value.field_type.routing,
                              op_activity.operation_id)
            else:
                logging.debug("No field type for %s of %s",
                              field_value.name, op_activity.operation_id)
            if routing_id:
                logging.debug("Creating arg object for %s %s with routing %s",
                              field_value.name, item_entity.item_id,
                              routing_id)
            return OperationInput(
                name=field_value.name,
                field_value_id=field_value.id,
                item=item_entity,
                routing_id=routing_id
            )
        else:
            return OperationParameter(
                name=field_value.name,
                field_value_id=field_value.id,
                value=field_value.value)

    def _create_operation(self, operation):
        """
        Creates an OperationActivity object from the pydent.model.Operation
        object.
        """
        op_activity = OperationActivity.create_from(operation)
        self.trace.add_operation(op_activity)

        field_values = sorted(operation.field_values, key=lambda fv: fv.role)
        routing_map = defaultdict(list)
        for field_value in field_values:
            arg = self._create_argument(field_value, op_activity)
            if is_input(field_value):
                op_activity.add_input(arg)

            if arg.is_item():
                if is_input(field_value):
                    self.trace.add_input(arg.item_id, op_activity)
                    if arg.routing_id:
                        msg = "Adding routing %s to input %s %s of op %s"
                        logging.debug(msg, arg.routing_id, arg.item.item_type,
                                      arg.item.item_id,
                                      op_activity.operation_id)
                        routing_map[arg.routing_id].append(arg.item)
                elif is_output(field_value):
                    arg.item.add_generator(op_activity)
                    if arg.routing_id:
                        logging.debug("Output %s of op %s has routing %s",
                                      arg.name, op_activity.operation_id,
                                      arg.routing_id)
                        if routing_map[arg.routing_id]:
                            logging.debug(
                                "Matching routing ID %s", arg.routing_id)
                            for source_id in routing_map[arg.routing_id]:
                                arg.item.add_source(source_id)
                        else:
                            logging.warning(
                                "unmatched routing %s for operation %s output %s",
                                arg.routing_id, arg.item_id, operation.id
                            )

        if operation.data_associations:
            for association in operation.data_associations:
                if association.upload:
                    logging.debug("operation %s has upload %s",
                                  op_activity.operation_id, association.key)
                    file_entity = self.get_file(
                        upload_id=association.upload.id)
                    if file_entity:
                        file_entity.add_generator(op_activity)
                elif association.object:
                    logging.debug("operation %s has association %s",
                                  op_activity.operation_id, association.key)
                    if is_upload(association):
                        upload_id = str(
                            association.object[association.key]['id'])
                        logging.debug("operation %s association %s is upload",
                                      op_activity.operation_id,
                                      association.key)
                        file_entity = self.get_file(
                            upload_id=upload_id
                        )
                        if file_entity:
                            file_entity.add_generator(op_activity)
                    op_activity.add_attribute(association.object)

    def create_items(self, *, item_id, generator=None):
        if self.trace.has_item(item_id):
            return

        item_obj = self.session.Item.find(item_id)
        if is_collection(item_obj):
            self._create_collection_entity(item_id, generator)
        else:
            self._create_item_entity(item_obj, generator)

    def _create_collection_entity(self, item_id, generator):
        item_obj = self.session.Collection.find(item_id)
        item_entity = CollectionEntity(collection=item_obj)
        self._add_item_entity(entity=item_entity, generator=generator)

        upload_matrix = None
        routing_matrix = None
        if item_obj.data_associations:
            for association in item_obj.data_associations:
                if association.upload:
                    logging.debug("collection %s has upload %s",
                                  item_entity.item_id, association.key)
                    file_entity = self.get_file(
                        upload_id=association.upload.id
                    )
                    file_entity.add_source(item_entity)
                elif association.object:
                    logging.debug("collection %s has association %s",
                                  item_entity.item_id, association.key)
                    if is_upload_matrix(association):
                        upload_matrix = get_upload_matrix(association.object)
                    elif is_routing_matrix(association):
                        routing_matrix = get_routing_matrix(
                            association.object, association.key)
                    else:
                        if is_upload(association):
                            upload_id = association.object[association.key]['id']
                            msg = "collection %s association %s is upload %s"
                            logging.debug(msg, item_entity.item_id,
                                          association.key, upload_id)
                            file_entity = self.get_file(
                                upload_id=upload_id
                            )
                            file_entity.add_source(item_entity)
                        item_entity.add_attribute(association.object)

        self._create_parts(entity=item_entity,
                           generator=generator,
                           upload_matrix=upload_matrix,
                           routing_matrix=routing_matrix
                           )

    def _create_item_entity(self, item_obj, generator):
        item_entity = ItemEntity(item=item_obj)
        self._add_item_entity(entity=item_entity, generator=generator)
        if item_obj.data_associations:
            for association in item_obj.data_associations:
                if association.upload:
                    logging.debug("item %s has upload %s",
                                  item_entity.item_id, association.key)
                    file_entity = self.get_file(
                        upload_id=association.upload.id
                    )
                    file_entity.add_source(item_entity)
                elif association.object:
                    if is_upload(association):
                        upload_id = association.object[association.key]['id']
                        msg = "item %s association %s is upload %s"
                        logging.debug(msg, item_entity.item_id,
                                      association.key, upload_id)
                        file_entity = self.get_file(
                            upload_id=association.object[association.key]['id']
                        )
                        file_entity.add_source(item_entity)
                    logging.debug("item %s has association %s",
                                  item_entity.item_id, association.key)
                    item_entity.add_attribute(association.object)

    # TODO: this is for 96 well plates, make work for general collections
    def _create_parts(self, *,
                      entity, generator, upload_matrix, routing_matrix):
        collection = entity.collection
        item_id = entity.item_id
        for i in range(len(collection.matrix)):
            row = collection.matrix[i]
            for j in range(len(row)):
                sample = self._get_sample(row[j])
                source_id = TraceFactory._get_source_id(routing_matrix, i, j)

                # has to be either a sample or source_id
                if not sample and not source_id:
                    continue

                source_entity = None
                if source_id:
                    source_entity = self._get_source(source_id)
                    if not sample and source_entity:
                        sample = source_entity.get_sample()
                        # TODO: decide whether to flag inconsistency

                part_id = str(item_id) + '/' + well_coordinates(i, j)
                if self.trace.has_item(part_id):
                    part_entity = self.trace.get_item(part_id)
                    if generator and not part_entity.generator:
                        part_entity.add_generator(generator)
                    if sample and not part_entity.sample:
                        part_entity.sample = sample
                else:
                    part_entity = PartEntity(
                        part_id=part_id,
                        sample=sample,
                        collection=entity
                    )
                    self._add_item_entity(
                        entity=part_entity, generator=generator)

                if source_entity:
                    part_entity.add_source(source_entity)
                    msg = "Adding %s %s as source for %s %s for ref %s"
                    logging.debug(msg, source_entity.item_type,
                                  source_entity.item_id,
                                  part_entity.item_type,
                                  part_entity.item_id,
                                  source_id)
                    if source_entity.is_item():
                        logging.debug(
                            "source %s %s is an item",
                            source_entity.item_type, source_entity.item_id)
                        part_entity.add_attribute(
                            {'source_reference': source_id})

                # Add part as source to file linked in upload_matrix
                if upload_matrix:
                    upload_id = upload_matrix[i][j]
                    if upload_id and upload_id > 0:
                        msg = "part %s has upload %s"
                        logging.debug(msg, part_entity.item_id, upload_id)
                        file_entity = self.get_file(upload_id=upload_id)
                        file_entity.add_source(part_entity)

                attributes = TraceFactory._get_attributes(routing_matrix, i, j)
                part_entity.add_attribute(attributes)

    def _get_source(self, source_id):
        """
        Returns an entity for the ID, creating the entity if it does not
        already exist.

        Source IDs come from data associations of collections and indicate the
        source for a part.

        May have one of the forms
        - item_id
        - item_id/part_ref
        - object_type_name/item_id/sample_id/part_ref
        The latter form is used in cases where the item is not a collection,
        but consists of subparts that are not explicitly modeled.
        An example is a yeast plate with colonies.
        In this case, return the item.

        Some plans have a part_ref of the form [[i,j]] that needs to be
        converted to alphanumeric form.

        This should not be necessary once part are first order in aquarium.
        """
        if self.trace.has_item(source_id):
            return self.trace.get_item(source_id)

        source_components = source_id.split('/')
        if re.match("[0-9]+", source_id):
            source_item_id = source_components[0]
            part_ref = None
            if len(source_components) == 2:
                part_ref = source_components[1]
                # fix stray numeric coordinates
                pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
                match = re.match(pattern, part_ref)
                if match:
                    part_ref = well_coordinates(
                        int(match[1]), int(match[2]))
                    new_id = source_item_id + '/' + part_ref
                    if self.trace.has_item(new_id):
                        return self.trace.get_item(new_id)
                # TODO: handle bad part ref
        elif len(source_components) == 4:
            # TODO: check this is an identifier
            source_item_id = source_components[1]
            part_ref = source_components[3]
        else:
            # TODO: raise exception here since id is malformed
            msg = "unrecognized source ID: %s"
            logging.warning(msg, source_id)
            return None

        if self.trace.has_item(source_item_id):
            source_item_entity = self.trace.get_item(source_item_id)
        else:
            item_obj = self.session.Item.find(source_item_id)
            if is_collection(item_obj):
                item_obj = self.session.Collection.find(source_item_id)
                source_item_entity = CollectionEntity(collection=item_obj)
            else:
                source_item_entity = ItemEntity(item=item_obj)
            self._add_item_entity(entity=source_item_entity)

        if not part_ref:
            return source_item_entity

        if not source_item_entity.is_collection():
            msg = "ignoring part %s from non-collection %s in source"
            logging.info(msg, part_ref, source_item_id)
            return source_item_entity

        part_id = source_item_id + '/' + part_ref

        # this assumes part_ref is well-formed
        (i, j) = TraceFactory.split_well_coordinate(part_ref)
        sample_id = source_item_entity.collection.matrix[i][j]
        sample = self.session.Sample.find(sample_id)
        if self.trace.has_item(part_id):
            source_part_entity = self.trace.get_item(part_id)
            if not source_part_entity.sample:
                source_part_entity.sample = sample
        else:
            source_part_entity = PartEntity(
                part_id=part_id,
                sample=sample,
                collection=source_item_entity
            )
            self._add_item_entity(entity=source_part_entity)
        return source_part_entity

    @staticmethod
    def split_well_coordinate(part_ref):
        pattern = r"([A-Z])([0-9]+)"
        match = re.match(pattern, part_ref)
        if match:
            return coordinates_for(part_ref)
        pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
        match = re.match(pattern, part_ref)
        if match:
            return (int(match[1]), int(match[2]))

    def get_file(self, *, upload_id):
        """
        Returns the file entity for an upload associated with a plan.
        If the entity is not currently in the trace, creates it.
        """
        logging.debug("Getting file %s", upload_id)
        if not self.trace.has_file(upload_id):
            file_ids = [file_id for file_id in self.trace.files]
            logging.debug("File %s does not exist in trace %s", upload_id, str(file_ids))
            upload = self._get_upload(upload_id)
            if upload:
                logging.debug("Upload %s exists", upload_id)
                file_entity = FileEntity(
                    upload=upload,
                    job=self._get_job(upload.job)
                )
                if not file_entity:
                    logging.debug("Failed to create file entity for %s", upload_id)
                self.trace.add_file(file_entity)
            else:
                logging.error("No upload object for ID %s", upload_id)
                return None
        file_ids = [file_id for file_id in self.trace.files]
        logging.debug("trace files after get_file: %s", str(file_ids))
        return self.trace.get_file(upload_id)

    def _get_job(self, job):
        operations = list()
        for op in job.operations:
            operation_id = str(op.id)
            if not self.trace.has_operation(operation_id):
                self._create_operation(op)
            operations.append(self.trace.get_operation(operation_id))
        logging.debug("creating job %s", job.id)
        return JobActivity(job_id=job.id, operations=operations)

    def _get_upload(self, upload_id):
        uploads = self.session.Upload.where(
            {"id": upload_id},
            {"methods": ["size", "name", "job"]}
        )
        if uploads:
            return uploads[0]

    def _get_sample(self, sample_id: int):
        if sample_id and not sample_id < 0:
            return self.session.Sample.find(sample_id)

    @staticmethod
    def _get_attributes(routing_matrix, i, j):
        entry = TraceFactory._get_routing_entry(routing_matrix, i, j)
        if 'attributes' not in entry:
            return dict()
        return entry['attributes']

    @staticmethod
    def _get_source_id(routing_matrix, i, j):
        entry = TraceFactory._get_routing_entry(routing_matrix, i, j)
        if 'source' in entry:
            source = entry['source']
            if isinstance(source, list):
                return str(source[0]['id'])
            else:
                return str(entry['source'])
        return None

    @staticmethod
    def _get_routing_entry(routing_matrix, i, j):
        if not routing_matrix:
            return dict()
        entry = routing_matrix[i][j]
        if entry and isinstance(entry, Mapping):
            return entry
        return dict()


def is_item_field_value(field_value):
    return bool(field_value.child_item_id)


def is_input(field_value):
    return field_value.role == 'input'


def is_output(field_value):
    return field_value.role == 'output'


def is_collection(item_obj):
    return not bool(item_obj.sample)


def is_upload_matrix(association):
    return association.key == 'SAMPLE_UPLOADs'


def get_upload_matrix(association_object):
    return association_object['SAMPLE_UPLOADs']['upload_matrix']


def is_routing_matrix(association):
    return association.key in ['routing_matrix', 'part_data']


def is_upload(association):
    upload_keys = set([
        'created_at', 'id', 'job_id', 'updated_at', 'upload_content_type',
        'upload_file_name', 'upload_file_size', 'upload_updated_at'
    ])
    association_value = association.object[association.key]
    result = isinstance(association_value,
                        Mapping) and association_value.keys() == upload_keys
    return result


def get_routing_matrix(association_object, key):
    if key == 'routing_matrix':
        return association_object[key]['rows']
    elif key == 'part_data':
        return association_object[key]


def get_routing_id(field_value):
    if field_value.field_type:
        return field_value.field_type.routing
