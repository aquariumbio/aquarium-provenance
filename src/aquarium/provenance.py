"""
Aquarium provenance classes derived using pydent models.
See TraceFactory.create_from to load.

Based on PROV-DM (https://www.w3.org/TR/prov-dm/), which defines provenance
in terms of activities, agents, and entities.

No attempt was made to properly model sample and object_type for the kinds of
entities in Aquarium.
An Item has a sample and object_type;
a collection has no sample but has an object_type; and
a part of a collection has a sample but no object_type.
"""
import abc
import logging
import os
from collections import defaultdict
from enum import Enum, auto


class AttributesMixin(abc.ABC):
    """
    Defines an abstract class to serve as a mixin for classes with objects that
    carry attributes.

    In Aquarium, only a Plan, Item and Operation may carry data associations
    from which these are populated, so only apply these to the corresponding
    classes.
    """

    @abc.abstractmethod
    def __init__(self):
        """
        Initialize empty attribute dictionary for this object.
        """
        self.attributes = dict()
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, AttributesMixin):
            return False
        return self.attributes == other.attributes

    def add_attribute(self, attribute):
        """
        Adds all key-value pairs in the given dictionary to the attributes
        dictionary of this class.
        """
        for key, value in attribute.items():
            if value:
                self.attributes[key] = value

    def get_attribute(self, key):
        if key in self.attributes:
            return self.attributes[key]

    def has_attribute(self, key):
        return key in self.attributes.keys()

    def as_dict(self):
        attr_dict = dict()
        if self.attributes:
            attr_dict['attributes'] = self.attributes
        return attr_dict


class AbstractEntity(abc.ABC):
    """
    Defines an abstract class with the properties of an entity from the
    perspective of provenance.
    Specifically, has the generating operations, and a list of source entities.
    """

    @abc.abstractmethod
    def __init__(self):
        self.generator = None
        self.sources = set()
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, AbstractEntity):
            return False
        return (self.generator == other.generator
                and self.sources == other.sources)

    def add_generator(self, activity):
        self.generator = activity

    def add_source(self, entity):
        self.sources.add(entity)

    def get_source_ids(self):
        return [item_entity.item_id for item_entity in self.sources]

    def generated_by(self, activity):
        """
        Determine whether the generator for this file is the given activity.
        """
        # TODO: need to be able to test if activities are equal, not here
        if not self.generator:
            return False

        if self.generator.is_job() and activity.is_job():
            return self.generator.job_id == activity.job_id

        if not self.generator.is_job() and not activity.is_job():
            return self.generator.operation_id == activity.operation_id

        return False

    def as_dict(self):
        entity_dict = dict()
        if self.generator:
            generator_dict = dict()
            if self.generator.is_job():
                generator_dict = self.generator.as_dict()
            else:  # is operation
                generator_dict['operation_id'] = self.generator.operation_id
            entity_dict['generated_by'] = generator_dict
        source_ids = self.get_source_ids()
        if source_ids:
            entity_dict['sources'] = source_ids
        return entity_dict

    def is_missing(self):
        return False


class AbstractItemEntity(AbstractEntity, AttributesMixin):
    """
    Defines an abstract entity representing an item.
    Each object has fields item_id and item_type.
    """

    @abc.abstractmethod
    def __init__(self, *, item_id, item_type):
        self.item_id = str(item_id)
        self.item_type = item_type
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, AbstractItemEntity):
            return False
        return (self.item_id == other.item_id
                and AbstractEntity.__eq__(self, other)
                and AttributesMixin.__eq__(self, other))

    def __hash__(self):
        return hash(self.item_id)

    def add_source(self, entity):
        logging.debug("Adding source %s %s for %s %s",
                      entity.item_type, entity.item_id,
                      self.item_type, self.item_id)
        super().add_source(entity)

    def as_dict(self):
        item_dict = dict()
        item_dict['item_id'] = self.item_id
        item_dict['type'] = self.item_type
        entity_dict = AbstractEntity.as_dict(self)
        attr_dict = AttributesMixin.as_dict(self)
        return {**item_dict, **{**entity_dict, **attr_dict}}

    @abc.abstractmethod
    def apply(self, visitor):
        """
        Apply visitor to this item-like object.
        """

    def is_collection(self):
        return False

    def is_item(self):
        return False

    def is_part(self):
        return False


class ItemEntity(AbstractItemEntity):
    """
    Defines an entity class for an Aquarium Item object.
    """

    def __init__(self, *, item_id, sample, object_type):
        self.sample = sample
        self.object_type = object_type
        super().__init__(item_id=item_id, item_type='item')

    def apply(self, visitor):
        visitor.visit_item(self)

    def as_dict(self):
        item_dict = super().as_dict()
        sample_dict = dict()
        sample_dict['sample_id'] = str(self.sample.id)
        sample_dict['sample_name'] = self.sample.name
        item_dict['sample'] = sample_dict
        type_dict = dict()
        type_dict['object_type_id'] = str(self.object_type.id)
        type_dict['object_type_name'] = self.object_type.name
        item_dict['object_type'] = type_dict
        return item_dict

    def get_sample(self):
        return self.sample

    def is_item(self):
        return True


class CollectionEntity(AbstractItemEntity):
    """
    Defines an entity class for an Aquarium Collection object.
    """

    def __init__(self, *, item_id, object_type):
        self.object_type = object_type
        self.part_map = dict()
        super().__init__(item_id=item_id, item_type='collection')

    def add_part(self, part):
        self.part_map[part.well] = part

    def parts(self):
        return list(self.part_map.values())

    def get_part(self, well):
        if well in self.part_map:
            return self.part_map[well]

    def has_parts(self):
        return bool(self.part_map)

    def apply(self, visitor):
        visitor.visit_collection(self)

    def as_dict(self):
        item_dict = super().as_dict()
        type_dict = dict()
        type_dict['object_type_id'] = str(self.object_type.id)
        type_dict['object_type_name'] = self.object_type.name
        item_dict['object_type'] = type_dict
        return item_dict

    def is_collection(self):
        return True


class PartEntity(AbstractItemEntity):
    """
    Defines and entity class for an Aquarium part object.
    """

    def __init__(self, *,
                 part_id: str, part_ref: str,
                 sample=None, object_type=None,
                 collection: CollectionEntity):
        self.ref = part_ref  # reference string for this part
        self.sample = sample
        self.object_type = object_type
        self.collection = collection
        self.collection.add_part(self)
        super().__init__(item_id=part_id, item_type='part')

    @property
    def well(self):
        return self.ref.split('/')[1]

    def get_sample(self):
        return self.sample

    def apply(self, visitor):
        visitor.visit_part(self)

    def as_dict(self):
        item_dict = super().as_dict()
        item_dict['well'] = self.well
        item_dict['part_of'] = self.collection.item_id
        sample_dict = dict()
        if self.sample:
            sample_dict['sample_id'] = str(self.sample.id)
            sample_dict['sample_name'] = self.sample.name
            item_dict['sample'] = sample_dict
        if self.object_type:
            type_dict = dict()
            type_dict['object_type_id'] = str(self.object_type.id)
            type_dict['object_type_name'] = self.object_type.name
            item_dict['object_type'] = type_dict
        return item_dict

    def is_part(self):
        return True


class FileTypes(Enum):
    CSV = auto()
    FCS = auto()
    XML = auto()


class AbstractFileEntity(AbstractEntity):
    """
    An abstract class for file entities.
    """
    _id_counter = 0

    @classmethod
    def _get_id(cls):
        value = cls._id_counter
        cls._id_counter += 1
        return value

    @abc.abstractmethod
    def __init__(self, *, name):
        self.name = name
        self.id = AbstractFileEntity._get_id()
        self.check_sum = None
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, AbstractFileEntity):
            return False
        if not super().__eq__(other):
            return False
        return (self.name == other.name
                and self.id == other.id
                and self.check_sum == other.check_sum)

    def __hash__(self):
        return hash(self.id)

    def file_type(self):
        _, extension = os.path.splitext(self.name)
        if extension == '.fcs':
            return FileTypes.FCS
        if extension == '.csv':
            return FileTypes.CSV
        if extension == '.xml':
            return FileTypes.XML
    type = property(file_type)

    def get_path(self, *, directory=None):
        name = self.name
        if directory:
            name = os.path.join(directory, name)
        return name

    def as_dict(self, *, path=None):
        entity_dict = super().as_dict()
        file_dict = dict()
        file_dict['id'] = str(self.id)
        file_dict['filename'] = self.get_path(directory=path)
        if self.type:
            file_dict['type'] = self.type.name
        if self.check_sum:
            file_dict['sha256'] = self.check_sum
        return {**file_dict, **entity_dict}

    def add_source(self, entity):
        logging.debug("Adding source %s %s for file %s",
                      entity.item_type, entity.item_id,
                      self.id)
        super().add_source(entity)

    def apply(self, visitor):
        visitor.visit_file(self)

    def is_external(self):
        return False


class FileEntity(AbstractFileEntity):
    """
    Defines an entity class for a file
    (corresponds to an Aquarium Upload object).

    Note that a file should only have one source.
    """

    def __init__(self, *, upload, job):
        self.upload_id = str(upload.id)
        self.size = upload.size
        self.job = job
        self.upload = upload
        super().__init__(name=upload.name)

    def __eq__(self, other):
        if not isinstance(other, FileEntity):
            return False
        if not super().__eq__(other):
            return False
        return (self.upload_id == other.upload_id
                and self.size == other.size
                and self.job == other.job
                and self.upload == other.upload)

    def as_dict(self, *, path=None):
        file_dict = super().as_dict(path=path)
        file_dict['upload_id'] = self.upload_id
        file_dict['size'] = self.size
        return file_dict


class ExternalFileEntity(AbstractFileEntity):
    """
    Represents a file that is stored outside of Aquarium.
    Examples are files on Illumina basespace.
    """

    def __init__(self, *, name):
        super().__init__(name=name)

    def __eq__(self, other):
        if not isinstance(other, ExternalFileEntity):
            return False
        return super().__eq__(other)

    def is_external(self):
        return True


class MissingEntity(AbstractEntity):
    """
    Represents entities that are missing in Aquarium.
    """

    def __init__(self):
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, MissingEntity):
            return False
        return super().__eq__(other)

    def is_missing(self):
        return True


class OperationArgument(abc.ABC):
    """
    Models an argument to an operation, which can be either a
    (though use it to capture output during trace conversion)
    """

    @abc.abstractmethod
    def __init__(self, *, name: str, field_value_id: str):
        self.name = name
        self.field_value_id = str(field_value_id)

    def __eq__(self, other):
        if not isinstance(other, OperationArgument):
            return False
        return (self.name == other.name
                and self.field_value_id == other.field_value_id)

    def is_item(self):
        """
        Return true if this argument is an input item or collection, and
        false, otherwise.
        """
        return False

    def as_dict(self):
        arg_dict = dict()
        arg_dict['name'] = self.name
        arg_dict['field_value_id'] = self.field_value_id
        return arg_dict


class OperationParameter(OperationArgument):

    def __init__(self, *, name: str, field_value_id: str, value):
        self.value = value
        super().__init__(name=name, field_value_id=field_value_id)

    def __eq__(self, other):
        if not isinstance(other, OperationParameter):
            return False
        if not super().__eq__(other):
            return False
        return self.value == other.value

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['value'] = self.value
        return arg_dict


class OperationInput(OperationArgument):

    def __init__(self, *, name, field_value_id, item_entity, routing_id=None):
        self.item_id = item_entity.item_id
        self.item = item_entity
        self.routing_id = routing_id
        super().__init__(name=name, field_value_id=field_value_id)

    def __eq__(self, other):
        if not isinstance(other, OperationInput):
            return False
        if not super().__eq__(other):
            return False
        return (self.item_id == other.item_id
                and self.item == other.item
                and self.routing_id == other.routing_id)

    def is_item(self):
        return True

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['item_id'] = self.item_id
        if self.routing_id:
            arg_dict['routing_id'] = self.routing_id
        return arg_dict


class JobActivity:
    def __init__(self, *, job, operations, start_time, end_time, status):
        self.job_id = str(job.id)
        self.operations = operations
        self.start_time = start_time
        self.end_time = end_time
        self.status = status
        for operation in self.operations:
            operation.job = self

    def __eq__(self, other):
        if not isinstance(other, JobActivity):
            return False
        return (self.job_id == other.job_id
                and self.operations == other.operations
                and self.start_time == other.start_time
                and self.end_time == other.end_time
                and self.status == other.status)

    def is_job(self):
        return True

    def get_activity_id(self):
        return "job_{}".format(self.job_id)

    @property
    def operation_type(self):
        if not self.operations:
            return None

        return next(iter(self.operations)).operation_type

    def apply(self, visitor):
        visitor.visit_job(self)

    def as_dict(self):
        job_dict = dict()
        job_dict['job_id'] = self.job_id
        job_dict['operations'] = [op.operation_id for op in self.operations]
        job_dict['status'] = self.status
        return job_dict


class OperationActivity(AttributesMixin):

    def __init__(self, *, id, operation_type,
                 start_time=None, end_time=None):
        self.type = 'operation'
        self.operation_id = str(id)
        self.operation_type = operation_type
        self.job = None
        self.plan = None
        self.start_time = start_time
        self.end_time = end_time
        self.inputs = defaultdict(list)
        self.outputs = defaultdict(list)
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, OperationActivity):
            return False
        if not super().__eq__(other):
            return False
        return (self.type == other.type
                and self.operation_id == other.operation_id
                and self.operation_type == other.operation_type
                and self.start_time == other.start_time
                and self.end_time == other.end_time
                and self.inputs == other.inputs
                and self.outputs == other.outputs)

    def apply(self, visitor):
        visitor.visit_operation(self)

    def add_input(self, input: OperationArgument):
        self.inputs[input.name].append(input)

    def add_output(self, output: OperationArgument):
        self.outputs[output.name].append(output)

    def has_input(self, item_entity: ItemEntity):
        for _, args in self.inputs.items():
            for arg in args:
                if arg.is_item() and arg.item_id == item_entity.item_id:
                    return True
        return False

    def get_inputs(self):
        return [arg for args in self.inputs.values() for arg in args]

    def get_input_items(self):
        return [
            arg
            for args in self.inputs.values()
            for arg in args if arg.is_item()
        ]

    def get_outputs(self):
        return [arg for args in self.outputs.values() for arg in args]

    def get_named_inputs(self, name: str):
        if name in self.inputs:
            return self.inputs[name]
        return []

    def get_named_outputs(self, name: str):
        if name in self.outputs:
            return self.outputs[name]
        return []

    def get_activity_id(self):
        return "op_{}".format(self.operation_id)

    def as_dict(self):
        op_dict = dict()
        op_dict['operation_id'] = self.operation_id
        op_type = dict()
        op_type['operation_type_id'] = str(self.operation_type.id)
        op_type['category'] = self.operation_type.category
        op_type['name'] = self.operation_type.name
        op_dict['operation_type'] = op_type
        op_dict['inputs'] = [arg.as_dict() for arg in self.get_inputs()]
        op_dict['outputs'] = [arg.as_dict() for arg in self.get_outputs()]
        op_dict['plan_id'] = self.plan.id
        op_dict['start_time'] = self.start_time
        op_dict['end_time'] = self.end_time
        attr_dict = AttributesMixin.as_dict(self)
        return {**op_dict, **attr_dict}

    def is_measurement(self):
        if self.attributes and 'measurement_operation' in self.attributes:
            return self.attributes['measurement_operation']
        return False

    def is_job(self):
        return False


class PlanActivity(AttributesMixin):

    def __init__(self, *, id, name, operations, status):
        self.__id = str(id)
        self.__name = name
        self.__operations = operations
        self.__status = status
        for operation in self.__operations:
            operation.plan = self

    @property
    def id(self):
        return self.__id

    def __eq__(self, other):
        if not isinstance(other, PlanActivity):
            return False
        return (self.__id == other.__id
                and self.__name == other.__name
                and self.__operations == other.__operations)

    def apply(self, visitor):
        visitor.visit_plan(self)

    def as_dict(self):
        plan_dict = dict()
        plan_dict['plan_id'] = self.__id
        plan_dict['name'] = self.__name
        plan_dict['operations'] = [op.operation_id for op in self.__operations]
        plan_dict['status'] = self.__status
        return plan_dict


class ProvenanceTrace(AttributesMixin):

    def __init__(self, *, experiment_id):
        self.__experiment_id = experiment_id
        self.__files = dict()
        self.__input_list = defaultdict(list)  # inverted list: item->op
        self.__items = dict()
        self.__jobs = dict()
        self.__operations = dict()
        self.__plans = dict()
        super().__init__()

    def __eq__(self, other):
        if not isinstance(other, ProvenanceTrace):
            return False
        if not super().__eq__(other):
            return False
        return (self.__files == other.files
                and self.__input_list == other.__input_list
                and self.__items == other.items
                and self.__jobs == other.jobs
                and self.__operations == other.operations
                and self.__plans == other.plans
                )

    @property
    def files(self):
        return self.__files

    @property
    def items(self):
        return self.__items

    @property
    def jobs(self):
        return self.__jobs

    @property
    def operations(self):
        return self.__operations

    @property
    def plans(self):
        return self.__plans

    def add_file(self, file_entity):
        logging.debug("Adding file %s to trace", file_entity.id)
        self.__files[file_entity.id] = file_entity

    def add_input(self, item_id, op_activity):
        self.__input_list[item_id].append(op_activity)

    def add_item(self, item_entity):
        logging.debug("Adding %s %s to trace",
                      item_entity.item_type, item_entity.item_id)
        self.__items[item_entity.item_id] = item_entity

    def add_job(self, job):
        logging.debug("Adding job %s to trace", job.job_id)
        self.__jobs[job.job_id] = job

    def add_operation(self, operation: OperationActivity):
        logging.debug("Adding operation %s to trace", operation.operation_id)
        self.__operations[operation.operation_id] = operation

    def add_plan(self, plan: PlanActivity):
        logging.debug("Adding plan %s to trace", plan.id)
        self.__plans[plan.id] = plan

    def has_file(self, id):
        return bool(id) and str(id) in self.__files

    def has_item(self, item_id):
        return bool(item_id) and str(item_id) in self.__items

    def has_job(self, job_id):
        return bool(job_id) and str(job_id) in self.__jobs

    def has_operation(self, operation_id):
        return bool(operation_id) and str(operation_id) in self.__operations

    def has_plan(self, plan_id):
        return bool(plan_id) and str(plan_id) in self.__plans

    def get_collections(self):
        return [item for _, item in self.__items.items()
                if item.is_collection()]

    def get_items(self):
        return [item for _, item in self.__items.items() if item.is_item()]

    def get_parts(self):
        return [item for _, item in self.__items.items() if item.is_part()]

    def get_item(self, item_id):
        item_key = str(item_id)
        if item_key in self.__items:
            return self.__items[item_key]

    def get_job(self, job_id):
        job_key = str(job_id)
        if job_key in self.__jobs:
            return self.__jobs[job_key]

    def get_jobs(self):
        return [job for _, job in self.__jobs.items()]

    def get_operation(self, operation_id):
        op_key = str(operation_id)
        if op_key in self.__operations:
            return self.__operations[op_key]

    def get_operations(self, *, input=None):
        """
        Return the list of operations.
        If input is an item ID, return all operations that have the item as an
        input.
        """
        if input:
            return self.__input_list[input]
        else:
            return [op for _, op in self.__operations.items()]

    def get_file(self, id):
        """
        Returns the file with the file id in this trace.
        Returns None if there is no such file.
        """
        file_key = str(id)
        if file_key in self.__files:
            return self.__files[file_key]

    def get_files(self, *, generator=None):
        """
        Return the list of files.
        If generator is an activity, return all files with the activity as the
        generator.
        """
        if generator:
            return [file for _, file in self.__files.items()
                    if file.generated_by(generator)]
        else:
            return [file for _, file in self.__files.items()]

    def get_inputs(self):
        """
        Return the array of items that are inputs to the plan of this trace.
        An input is determined as items with no source or generator in the plan
        that is not part of another item.
        """
        return [
            item for _, item in self.__items.items() if self.is_input(item)
        ]

    def is_input(self, item):
        """
        Indicates whether the item is an input to this trace.
        A non-part item will be an input if it is not generated by an activity
        in the trace, or if there are no generators, all sources are not in the
        trace.  A part is never an input.
        """
        if item.is_part():
            return False

        if not self.has_item(item.item_id):
            return False

        if item.generator:
            if item.generator.is_job():
                if self.has_job(item.generator.job_id):
                    return False
            else:
                if self.has_operation(item.generator.operation_id):
                    return False

        if item.sources:
            for source in item.sources:
                if self.has_item(source.item_id):
                    return False

        return True

    def apply(self, visitor):
        visitor.visit_trace(self)

    def apply_all(self, visitor):
        visitor.visit_trace(self)
        for _, plan in self.__plans.items():
            visitor.visit_plan(self)
        for _, operation in self.__operations.items():
            operation.apply(visitor)
        for _, item in self.__items.items():
            item.apply(visitor)
        for _, file in self.__files.items():
            file.apply(visitor)

    def as_dict(self):
        trace_dict = dict()
        trace_dict['experiment_id'] = self.__experiment_id
        trace_dict['inputs'] = [
            item.item_id for item in self.get_inputs()]
        trace_dict['operations'] = [op.as_dict()
                                    for _, op in self.__operations.items()]
        trace_dict['plans'] = [plan.as_dict()
                               for _, plan in self.__plans.items()]
        trace_dict['jobs'] = [job.as_dict() for _, job in self.__jobs.items()]
        trace_dict['items'] = [item.as_dict()
                               for _, item in self.__items.items()]
        trace_dict['files'] = [
            file.as_dict(path=file.generator.get_activity_id())
            for _, file in self.__files.items()
            if file.generator
        ]
        super_dict = super().as_dict()
        return {**trace_dict, **super_dict}
