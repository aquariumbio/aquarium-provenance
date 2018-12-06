import json
import logging
from collections import defaultdict
from collections.abc import Mapping
from aquarium.provenance import (
    CollectionEntity,
    ItemEntity,
    ExternalFileEntity,
    FileEntity,
    JobActivity,
    OperationActivity,
    OperationInput,
    OperationParameter,
    PartEntity,
    PlanTrace)
from aquarium.trace.visitor import BatchVisitor, ProvenanceVisitor
from aquarium.trace.part_visitor import AddPartsVisitor
from aquarium.trace.patch import create_patch_visitor
from util.plate import well_coordinates, coordinates_for


class TraceFactory:
    """
    Defines a factory object to create a PlanTrace from a pydent.model.Plan.
    """

    def __init__(self, *, session, plan, trace):
        self.trace = trace
        self.session = session
        self.plan = plan
        self.attribute_visitor = AttributeVisitor(trace=plan, factory=self)
        self.item_map = dict()        # item_id -> item
        self.op_map = dict()          # operation_id -> operation
        self.job_map = dict()         # job_id -> job
        self.uploads = dict()         # upload_id -> file_entity
        self.external_files = dict()  # name -> external_file_entity
        self.part_map = dict()        # part ref string -> part_entity

    @staticmethod
    def create_from(*, session, plan, visitor=None):
        """
        Creates a PlanTrace for the plan from the Aquarium session.

        Visits all operations first, gathering input/output items.
        This ensures that all operations in the plan are included before
        adding files (for which jobs are created).

        Then visits the collected items, creating parts for collections.

        Associated uploads are visited last
        """
        trace = PlanTrace(plan_id=plan.id, name=plan.name)
        factory = TraceFactory(
            session=session,
            plan=plan,
            trace=trace
        )
        trace.apply(factory.attribute_visitor)

        for operation in plan.operations:
            factory._add_operation(operation)

        # Apply the primary visitor first, the given visitor, and then patch
        primary_visitor = BatchVisitor()
        primary_visitor.add_visitor(JobVisitor())
        primary_visitor.add_visitor(AddPartsVisitor())
        primary_visitor.add_visitor(FileProvenanceVisitor())
        factory._apply(primary_visitor)

        if visitor:
            factory._apply(visitor)

        patch_visitor = create_patch_visitor()
        factory._apply(patch_visitor)

        return factory.trace

    def _add_operation(self, operation):
        """
        Adds an activity for the given operation, and gathers input/output
        items for the operation.
        """
        self.op_map[str(operation.id)] = operation
        op_activity = self.get_operation(operation)
        self._gather_io_items(op_activity)

    def _apply(self, visitor):
        """
        Applies the visitor to the trace of the factory.

        The visitor may modify the trace, and may add trace elements using the
        factory.
        """
        visitor.add_trace(self.trace)
        visitor.add_factory(self)

        logging.debug("Visit trace")
        self.trace.apply(visitor)

        logging.debug("Visit operations")
        for op_activity in self.trace.get_operations():
            op_activity.apply(visitor)

        logging.debug("Visit jobs")
        for job_activity in self.trace.get_jobs():
            job_activity.apply(visitor)

        logging.debug("Visit items")
        for item_entity in self.trace.get_items():
            item_entity.apply(visitor)

        logging.debug("Visit collections")
        for collection in self.trace.get_collections():
            collection.apply(visitor)

        logging.debug("Visit parts")
        for part_entity in self.trace.get_parts():
            part_entity.apply(visitor)

        logging.debug("Visit files")
        for file_entity in self.trace.get_files():
            file_entity.apply(visitor)

    def _get_routing_id(self, field_value, operation_id):
        """
        Returns the routing ID from the field values, None if there is no ID.
        """
        routing_id = None
        if field_value.field_type:
            routing_id = field_value.field_type.routing
            msg = "Field type %s role %s array %s routing %s op %s"
            logging.debug(msg, field_value.field_type.name,
                          field_value.field_type.role,
                          field_value.field_type.array,
                          field_value.field_type.routing,
                          operation_id)
        else:
            logging.debug("No field type for %s of %s",
                          field_value.name, operation_id)
        return routing_id

    def _create_argument(self, field_value, operation_id):
        """
        Creates an OperationArgument object for the given FieldValue.

        For an input, adds the object to the OperationActivity as an input, and
        if it represents an Item adds it to the input inverted list.
        For an output Item, adds the OperationActivity as the generator.
        """
        item_id = field_value.child_item_id
        if item_id:
            item_entity = self.get_item(item_id=item_id)
            if field_value.row is not None and field_value.column is not None:
                logging.debug("Input is a part %s[%s,%s]",
                              item_id, field_value.row, field_value.column)
                item_entity = self.get_part(collection=item_entity,
                                            row=field_value.row,
                                            column=field_value.column)

            routing_id = self._get_routing_id(field_value, operation_id)
            if routing_id:
                logging.debug("Creating arg object for %s %s with routing %s",
                              field_value.name, item_id, routing_id)

            return OperationInput(
                name=field_value.name,
                field_value_id=field_value.id,
                item_entity=item_entity,
                routing_id=routing_id
            )
        else:
            return OperationParameter(
                name=field_value.name,
                field_value_id=field_value.id,
                value=field_value.value)

    def _gather_io_items(self, op_activity):
        """
        Visits field values of the given OperationActivity to identify
        operation arguments and outputs.

        Adds input items and parameters to the to the operation.
        Sets the operation as the generator for outputs, and adds sources to
        outputs when indicated by routing.
        """
        operation = self.op_map[op_activity.operation_id]
        logging.debug("Getting I/O for operation %s", operation.id)
        field_values = sorted(operation.field_values, key=lambda fv: fv.role)
        routing_map = RoutingMap()
        for field_value in field_values:
            arg = self._create_argument(field_value, operation.id)

            if is_input(field_value):
                op_activity.add_input(arg)

            if arg.is_item():
                if is_input(field_value):
                    routing_map.add(arg)
                    self.trace.add_input(arg.item_id, op_activity)
                elif is_output(field_value):
                    if arg.routing_id:
                        if arg.routing_id in routing_map:
                            for input_item in routing_map.get(arg.routing_id):
                                if arg.item.item_id != input_item.item_id:
                                    arg.item.add_source(input_item)
                        else:
                            logging.debug("Unmatched routing %s for %s",
                                          arg.routing_id, operation.id)
                    arg.item.add_generator(op_activity)

    def get_external_file(self, *, name) -> ExternalFileEntity:
        if name in self.external_files:
            return self.external_files['name']

        file_entity = ExternalFileEntity(name=name)
        self.trace.add_file(file_entity)
        self.external_files[name] = file_entity

        return file_entity

    def get_file(self, *, upload_id) -> FileEntity:
        """
        Returns the file entity for an upload associated with a plan.
        If the entity is not currently in the trace, creates it.
        """
        if upload_id in self.uploads:
            return self.uploads[upload_id]

        file_entity = None
        upload = self.session.Upload.find(upload_id)
        if not upload:
            logging.error("No upload object for ID %s", upload_id)
            return None
        if not upload.job:
            logging.error("No job in upload %s", upload_id)
            return None

        file_job = self._get_job(upload.job.id)
        if not file_job:
            logging.debug("Job %s of file upload %s is not in plan",
                          upload.job.id, upload_id)
            return None

        file_entity = FileEntity(upload=upload, job=file_job)
        self.trace.add_file(file_entity)
        self.uploads[upload_id] = file_entity

        return file_entity

    def get_item(self, *, item_id):
        """
        Returns the item entity for the item ID.
        If the entity is not currently in the trace, creates it.
        """
        logging.debug("Getting item %s", item_id)
        if self.trace.has_item(item_id):
            return self.trace.get_item(item_id)

        item_obj = self.session.Item.find(item_id)
        if is_collection(item_obj):
            item_obj = self.session.Collection.find(item_id)
            item_entity = CollectionEntity(collection=item_obj)

        else:
            item_entity = ItemEntity(
                item_id=item_obj.id,
                sample=item_obj.sample,
                object_type=item_obj.object_type)

        self.item_map[str(item_id)] = item_obj
        self.trace.add_item(item_entity)
        item_entity.apply(self.attribute_visitor)
        if is_collection(item_obj):
            self._collect_parts(item_obj)

        return item_entity

    def get_part(self, *, collection, row=None, column=None, well=None,
                 part_id=None, sample=None, object_type=None):
        if part_id is not None:
            if self.trace.has_item(part_id):
                return self.trace.get_item(part_id)

        if not collection:
            logging.error("No collection given for new part")
            return None

        if not collection.is_collection():
            logging.error("Refusing to create part for non-collection %s",
                          collection.item_id)
            return None

        if well is None:
            if row is None or column is None:
                logging.error("No well coordinates given")
                return None
            well = well_coordinates(row, column)

        part_ref = get_part_ref(collection_id=collection.item_id, well=well)

        logging.debug("Getting part %s", part_ref)
        if part_ref in self.part_map:
            logging.debug("Ref %s in factory part_map", part_ref)
            return self.part_map[part_ref]
        if self.trace.has_item(part_ref):
            logging.debug("Ref %s in plan", part_ref)
            return self.trace.get_item(part_ref)

        if part_id is None:
            if row is None or column is None:
                (row, column) = coordinates_for(well)
            item = self.item_map[collection.item_id]
            part = item.part(row, column)
            if not part:
                logging.warning("Did not find part for ref %s", part_ref)
                return None

            logging.debug("Found part %s for ref %s", part.id, part_ref)
            part_id = part.id
            sample = part.sample
            object_type = part.object_type

        part_entity = PartEntity(part_id=part_id, part_ref=part_ref,
                                 collection=collection)

        if sample is not None:
            part_entity.sample = sample
        if object_type is not None:
            part_entity.object_type = object_type

        self.part_map[part_entity.ref] = part_entity
        self.trace.add_item(part_entity)
        part_entity.apply(self.attribute_visitor)
        return part_entity

    def get_operation(self, operation) -> OperationActivity:
        """
        Returns the operation activity for the operation.
        If the activity is not currently in the trace, creates it.
        """
        logging.debug("Getting operation %s", operation.id)
        if self.trace.has_operation(operation.id):
            return self.trace.get_operation(operation.id)

        op_activity = OperationActivity(
            id=str(operation.id),
            operation_type=operation.operation_type)

        self.trace.add_operation(op_activity)
        op_activity.apply(self.attribute_visitor)
        return op_activity

    def _collect_parts(self, item):
        logging.debug("Collecting parts for %s", item.id)
        for part_association in item.part_associations:
            logging.debug("Getting part %s", part_association.part_id)
            if self.trace.has_item(part_association.part_id):
                return self.trace.get_item(part_association.part_id)

            if part_association.collection_id != item.id:
                logging.error("Collection %s does not match association %s",
                              item.id, part_association.collection_id)
                return None
            logging.debug("part_association: part=%s, coll=%s row=%s, col=%s",
                          part_association.part_id,
                          part_association.collection_id,
                          part_association.row,
                          part_association.column)
            collection = self.trace.get_item(part_association.collection_id)
            part = part_association.part
            part_entity = self.get_part(
                collection=collection,
                row=part_association.row,
                column=part_association.column,
                part_id=part_association.part_id,
                sample=part.sample,
                object_type=part.object_type)
            self.item_map[part_entity.item_id] = part

    def _get_job(self, job_id):
        """
        Returns the job activity for the operation.
        If the activity is not currently in the trace, creates it.
        """
        if self.trace.has_job(job_id):
            return self.trace.get_job(job_id)

        job = self.session.Job.find(job_id)
        if not job:
            logging.debug("No job %s in database", job_id)

        self.job_map[str(job_id)] = job
        start_time = job.start_time
        end_time = job.end_time
        operations = list()
        for op in job.operations:
            operation_id = str(op.id)
            if self.trace.has_operation(operation_id):
                op_activity = self.trace.get_operation(operation_id)
                op_activity.start_time = start_time
                op_activity.end_time = end_time
                operations.append(op_activity)

        if not operations:
            logging.debug("Job %s has no operations in plan", job_id)
            return None

        logging.debug("Creating job %s", job_id)
        job_activity = JobActivity(job=job,
                                   operations=operations,
                                   start_time=start_time,
                                   end_time=end_time)
        self.trace.add_job(job_activity)
        return job_activity

    def get_sample(self, sample_id: int):
        """
        Returns the Sample object for the sample ID.
        """
        if sample_id and not sample_id < 0:
            return self.session.Sample.find(sample_id)


def get_part_ref(*, collection_id, well):
    return "{}/{}".format(collection_id, well)


def is_input(field_value):
    return field_value.role == 'input'


def is_output(field_value):
    return field_value.role == 'output'


def is_collection(item_obj):
    return not bool(item_obj.sample)


def is_upload(association):
    """
    Indicates whether the association corresponds to an upload object.
    """
    upload_keys = set([
        'created_at', 'id', 'job_id', 'updated_at', 'upload_content_type',
        'upload_file_name', 'upload_file_size', 'upload_updated_at'
    ])
    association_value = association.value
    return (isinstance(association_value, Mapping)
            and
            association_value.keys() == upload_keys)


class ItemFileVisitor:
    """
    File visitor that adds an item as the source of any file it the visitor
    is applied to.
    """

    def __init__(self, item_entity):
        self.item_entity = item_entity

    def apply(self, key, file_entity):
        file_entity.add_source(self.item_entity)


class OperationFileVisitor:
    """
    File visitor that adds an operation as the generator of any file that the
    visitor is applied to.
    """

    def __init__(self, op_activity):
        self.op_activity = op_activity

    def apply(self, key, file_entity):
        file_entity.add_generator(self.op_activity)


class PlanFileVisitor:
    """
    File visitor that manages a list of calibration bead files as an attribute
    of the plan.
    """

    def __init__(self, trace):
        self.trace = trace

    def apply(self, key, file_entity):
        if (key.endswith('BEAD_UPLOAD') or key.startswith('BEADS_')):
            self._add_bead_file(file_entity.id)

    def _add_bead_file(self, upload_id):
        upload_list = self.trace.get_attribute('bead_files')
        if not upload_list:
            upload_list = list()
        if upload_id not in upload_list:
            upload_list.append(upload_id)
            self.trace.add_attribute({'bead_files': upload_list})


class AttributeVisitor(ProvenanceVisitor):
    """
    Visitor to add attributes based on non-upload associations to items,
    operations and plans.
    """

    def __init__(self, trace=None, factory=None):
        super().__init__(trace=trace, factory=factory)

    def visit_collection(self, collection: CollectionEntity):
        logging.debug("Getting attributes for %s %s",
                      collection.item_type, collection.item_id)
        item = self.factory.item_map[collection.item_id]
        self._get_attributes(item.data_associations, collection)

    def visit_item(self, item_entity):
        logging.debug("Getting attributes for %s %s",
                      item_entity.item_type, item_entity.item_id)
        item = self.factory.item_map[item_entity.item_id]
        self._get_attributes(item.data_associations, item_entity)

    def visit_part(self, part_entity):
        if part_entity.item_id == part_entity.ref:
            return

        if part_entity.item_id not in self.factory.item_map:
            logging.debug("Part %s not in factory item_map",
                          part_entity.item_id)
            return

        logging.debug("Getting attributes for %s %s",
                      part_entity.item_type, part_entity.item_id)
        item = self.factory.item_map[part_entity.item_id]
        self._get_attributes(item.data_associations, part_entity)

    def visit_operation(self, op_activity):
        operation = self.factory.op_map[op_activity.operation_id]
        logging.debug("Getting attributes for operation %s", operation.id)
        self._get_attributes(operation.data_associations, op_activity)

    def visit_plan(self, trace):
        plan = self.factory.plan
        logging.debug("Getting attributes for plan %s", plan.id)
        self._get_attributes(plan.data_associations, trace)

    def _get_attributes(self, associations, prov_object):
        """
        Gather non-upload associations and attach them as attributes to the
        provenance object.
        """
        if not associations:
            return

        for association in associations:
            if association.object and not association.upload:
                logging.debug("Adding attribute %s", association.key)
                logging.debug(json.dumps(association.object, indent=2))
                prov_object.add_attribute(association.object)


class FileProvenanceVisitor(ProvenanceVisitor):
    """
    Visitor to add files to the provenance trace to which it is applied.
    """

    def __init__(self):
        super().__init__()

    def visit_collection(self, collection: CollectionEntity):
        item = self.factory.item_map[collection.item_id]
        logging.debug("Getting files for %s %s",
                      collection.item_type, collection.item_id)
        self._get_files(item.data_associations, ItemFileVisitor(collection))

    def visit_item(self, item_entity):
        item = self.factory.item_map[item_entity.item_id]
        logging.debug("Getting files for %s %s",
                      item_entity.item_type, item_entity.item_id)
        self._get_files(item.data_associations, ItemFileVisitor(item_entity))

    def visit_part(self, part_entity):
        if part_entity.item_id not in self.factory.item_map:
            logging.debug("Part %s not in factory item_map",
                          part_entity.item_id)
            return

        item = self.factory.item_map[part_entity.item_id]
        logging.debug("Getting files for %s %s",
                      part_entity.item_type, part_entity.item_id)
        self._get_files(item.data_associations, ItemFileVisitor(part_entity))

    def visit_job(self, job_activity):
        job = self.factory.job_map[job_activity.job_id]
        logging.debug("Getting files for job %s", job_activity.job_id)
        for upload in job.uploads:
            self.factory.get_file(upload_id=upload['id'])

    def visit_operation(self, op_activity):
        operation = self.factory.op_map[op_activity.operation_id]
        logging.debug("Getting files for operation %s",
                      op_activity.operation_id)
        self._get_files(operation.data_associations,
                        OperationFileVisitor(op_activity))

    def visit_plan(self, trace):
        plan = self.factory.plan
        logging.debug("Getting files for plan %s", plan.id)
        self._get_files(plan.data_associations, PlanFileVisitor(trace))

    def _get_files(self, associations, visitor):
        """
        Gather file associations, create the provenance file entity and apply
        the visitor to determine how it is handled.

        See :ItemFileVisitor:, :OperationFileVisitor:, :PlanFileVisitor:
        """
        if not associations:
            return

        for association in associations:
            upload_id = None
            if association.upload:
                logging.debug("Association upload %s is a file %s",
                              association.key, association.upload.id)
                upload_id = association.upload.id
            elif association.object:
                if is_upload(association):
                    upload_id = association.value['id']
                    logging.debug("Association object %s is a file %s",
                                  association.key, upload_id)
            if upload_id:
                file_entity = self.factory.get_file(upload_id=upload_id)
                if file_entity:
                    visitor.apply(association.key, file_entity)


class JobVisitor(ProvenanceVisitor):
    def __init__(self):
        self.visited = set()
        self.op_job_map = dict()  # operation_id -> job_activity
        super().__init__()

    def visit_operation(self, op_activity):
        if op_activity.operation_id not in self.visited:
            job_activity = self._get_operation_job(op_activity)
            if job_activity:
                self.visited.update([
                    op.operation_id for op in job_activity.operations
                ])

    def _get_operation_job(self, op_activity):
        if op_activity.operation_id in self.op_job_map:
            return self.op_job_map[op_activity.operation_id]

        operation = self.factory.op_map[op_activity.operation_id]
        if not operation.job_associations:
            logging.error("Operation %s has no job associations", operation.id)
            return None

        completed_jobs = [a.job for a in operation.job_associations
                          if a.job.pc == -2]
        job = max(completed_jobs, key=lambda job: job.updated_at)

        if not job:
            logging.error("Operation %s has no completed jobs", operation.id)
            return None

        job_activity = self.factory._get_job(job.id)
        if job_activity:
            for op in job_activity.operations:
                self.op_job_map[op.operation_id] = job_activity

        return job_activity


class RoutingMap():
    def __init__(self):
        self.routing_map = defaultdict(list)

    def add(self, arg):
        if not arg.routing_id:
            return

        self.routing_map[arg.routing_id].append(arg.item)

    def get(self, routing_id):
        return self.routing_map[routing_id]

    def __contains__(self, routing_id):
        return routing_id in self.routing_map
