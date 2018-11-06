"""
Aquarium provenance classes derived using pydent models.
See TraceFactory.create_from to load.

Based on PROV-DM (https://www.w3.org/TR/prov-dm/), which defines provenance
in terms of activities, agents, and entities.

Note that I punted on properly modeling the kinds of entities in Aquarium.
An Item has a sample and object_type;
a collection has no sample but has an object_type; and
a part of a collection has a sample but no object_type.
"""
import abc
import logging
import os
from collections import defaultdict, deque
from copy import copy


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
        self.attributes = dict()
        super().__init__()

    def add_attribute(self, attribute):
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


class AbstractItemEntity(AbstractEntity, AttributesMixin):
    """
    Defines an abstract entity representing an item.
    """

    @abc.abstractmethod
    def __init__(self, *, item_id, item_type):
        self.item_id = str(item_id)
        self.item_type = item_type
        super().__init__()

    def __eq__(self, other):
        return (isinstance(other, AbstractItemEntity) and
                self.item_id == other.item_id)

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

    def __init__(self, *, item):
        self.item = item
        self.sample = item.sample
        self.object_type = item.object_type
        super().__init__(item_id=item.id, item_type='item')

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

    def __init__(self, collection):
        self.object_type = collection.object_type
        self.collection = collection
        self.parts = list()
        super().__init__(item_id=collection.id, item_type='collection')

    def add_part(self, part):
        self.parts.append(part)

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

    def __init__(self, *,
                 part_id: str, sample=None, collection: CollectionEntity):
        self.sample = sample
        self.collection = collection
        self.collection.add_part(self)
        super().__init__(item_id=part_id, item_type='part')

    @property
    def part_ref(self):
        return self.item_id.split('/')[1]

    def get_sample(self):
        return self.sample

    def apply(self, visitor):
        visitor.visit_part(self)

    def as_dict(self):
        item_dict = super().as_dict()
        item_dict['part_of'] = self.collection.item_id
        sample_dict = dict()
        if self.sample:
            sample_dict['sample_id'] = str(self.sample.id)
            sample_dict['sample_name'] = self.sample.name
        item_dict['sample'] = sample_dict
        return item_dict

    def is_part(self):
        return True


class FileEntity(AbstractEntity):
    """
    Defines an entity class for a file
    (corresponds to an Aquarium Upload object).

    Note that a file should only have one source.
    """

    def __init__(self, *, upload, job):
        self.file_id = str(upload.id)
        self.name = upload.name
        self.size = upload.size
        self.job = job
        self.upload = upload
        self.check_sum = None
        super().__init__()

    def __eq__(self, other):
        return isinstance(other, FileEntity) and self.file_id == self.file_id

    def __hash__(self):
        return hash(self.file_id)

    def add_source(self, entity):
        logging.debug("Adding source %s %s for file %s",
                      entity.item_type, entity.item_id,
                      self.file_id)
        super().add_source(entity)

    def apply(self, visitor):
        visitor.visit_file(self)

    def file_type(self):
        _, extension = os.path.splitext(self.name)
        if extension == '.fcs':
            return 'FCS'
        if extension == '.csv':
            return 'CSV'
    type = property(file_type)

    def as_dict(self, *, path=None):
        entity_dict = super().as_dict()
        file_dict = dict()
        file_dict['file_id'] = self.file_id
        file_dict['filename'] = self.get_path(directory=path)
        file_dict['size'] = self.size
        if self.type:
            file_dict['type'] = self.type
        if self.check_sum:
            file_dict['sha256'] = self.check_sum
        return {**file_dict, **entity_dict}

    def get_path(self, *, directory=None):
        name = self.name
        if directory:
            name = os.path.join(directory, name)
        return name


class OperationArgument(abc.ABC):
    """
    Models an argument to an operation, which can be either a
    (though use it to capture output during trace conversion)
    """

    @abc.abstractmethod
    def __init__(self, *, name: str, field_value_id: str):
        self.name = name
        self.field_value_id = str(field_value_id)

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

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['value'] = self.value
        return arg_dict


class OperationInput(OperationArgument):

    def __init__(self, *, name, field_value_id, item, routing_id=None):
        self.item_id = item.item_id
        self.item = item
        self.routing_id = routing_id
        super().__init__(name=name, field_value_id=field_value_id)

    def is_item(self):
        return True

    def as_dict(self):
        arg_dict = super().as_dict()
        arg_dict['item_id'] = self.item_id
        if self.routing_id:
            arg_dict['routing_id'] = self.routing_id
        return arg_dict


class JobActivity:
    def __init__(self, *, job, operations, start_time, end_time):
        self.job_id = str(job.id)
        self.job = job
        self.operations = operations
        self.start_time = start_time
        self.end_time = end_time

    def is_job(self):
        return True

    def get_activity_id(self):
        return "job_{}".format(self.job_id)

    def as_dict(self):
        job_dict = dict()
        job_dict['job_id'] = self.job_id
        job_dict['operations'] = [op.operation_id for op in self.operations]
        return job_dict


class OperationActivity(AttributesMixin):

    def __init__(self, *, id, operation_type, operation,
                 start_time=None, end_time=None):
        self.type = 'operation'
        self.operation_id = str(id)
        self.operation_type = operation_type
        self.operation = operation
        self.start_time = start_time
        self.end_time = end_time
        self.inputs = list()
        super().__init__()

    def apply(self, visitor):
        visitor.visit_operation(self)

    def add_input(self, input: OperationArgument):
        self.inputs.append(input)

    def has_input(self, item_entity: ItemEntity):
        for arg in self.inputs:
            if arg.is_item() and arg.item_id == item_entity.item_id:
                return True
        return False

    def get_named_inputs(self, name: str):
        return [arg for arg in self.inputs if arg.name == name]

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
        op_dict['inputs'] = [input.as_dict() for input in self.inputs]
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


class PlanTrace(AttributesMixin):

    def __init__(self, *, plan_id: str, name: str):
        self.plan_id = str(plan_id)
        self.plan_name = name
        self.operations = dict()
        self.jobs = dict()
        self.items = dict()
        self.files = dict()
        self.input_list = defaultdict(list)  # inverted list: item->op
        super().__init__()

    def add_file(self, file_entity):
        logging.debug("Adding file %s to trace", file_entity.file_id)
        self.files[file_entity.file_id] = file_entity

    def add_item(self, item_entity):
        logging.debug("Adding %s %s to trace",
                      item_entity.item_type, item_entity.item_id)
        self.items[item_entity.item_id] = item_entity

    def add_input(self, item_id, op_activity):
        self.input_list[item_id].append(op_activity)

    def add_operation(self, operation):
        logging.debug("Adding operation %s to trace", operation.operation_id)
        self.operations[operation.operation_id] = operation

    def add_job(self, job):
        logging.debug("Adding job %s to trace", job.job_id)
        self.jobs[job.job_id] = job

    def has_job(self, job_id):
        return bool(job_id) and str(job_id) in self.jobs

    def has_item(self, item_id):
        return bool(item_id) and str(item_id) in self.items

    def has_file(self, file_id):
        return bool(file_id) and str(file_id) in self.files

    def has_operation(self, operation_id):
        return bool(operation_id) and str(operation_id) in self.operations

    def get_collections(self):
        return [item for _, item in self.items.items()
                if item.is_collection()]

    def get_items(self):
        return [item for _, item in self.items.items() if item.is_item()]

    def get_parts(self):
        return [item for _, item in self.items.items() if item.is_part()]

    def get_item(self, item_id):
        item_key = str(item_id)
        if item_key in self.items:
            return self.items[item_key]

    def get_job(self, job_id):
        job_key = str(job_id)
        if job_key in self.jobs:
            return self.jobs[job_key]

    def get_operation(self, operation_id):
        op_key = str(operation_id)
        if op_key in self.operations:
            return self.operations[op_key]

    def get_operations(self, *, input=None):
        """
        Return the list of operations.
        If input is an item ID, return all operations that have the item as an
        input.
        """
        if input:
            return self.input_list[input]
        else:
            return [op for _, op in self.operations.items()]

    def get_file(self, file_id):
        """
        Returns the file with the file_id in this trace.
        Returns None if there is no such file. 
        """
        file_key = str(file_id)
        if file_key in self.files:
            return self.files[file_key]

    def get_files(self, *, generator=None):
        """
        Return the list of files.
        If generator is an activity, return all files generated by the activity.
        """
        if generator:
            return [file for _, file in self.files.items()
                    if file.generated_by(generator)]
        else:
            return [file for _, file in self.files.items()]

    def get_inputs(self):
        """
        Return the array of items that are inputs to the plan of this trace.
        An input is determined as items with no source or generator in the plan
        that is not part of another item.
        """
        return [item for _, item in self.items.items() if self.is_input(item)]

    def is_input(self, item):
        """
        Indicates whether the item is an input to this plan.
        A non-part item will be an input if it is not generated by an activity
        in the plan, or if there is no generators, all sources are not in the
        plan.  A part is never an input.
        """
        if item.is_part():
            return False

        if item.generator:
            if item.generator.is_job():
                if self.has_job(item.generator.job_id):
                    return False
            else:
                if self.has_operation(item.generator.operation_id):
                    return False
        else:
            if item.sources:
                for source in item.sources:
                    if self.has_item(source.item_id):
                        return False

        return True

    def apply(self, visitor):
        visitor.visit_plan(self)

    def apply_all(self, visitor):
        visitor.visit_plan(self)
        for _, operation in self.operations.items():
            operation.apply(visitor)
        for _, item in self.items.items():
            item.apply(visitor)
        for _, file in self.files.items():
            file.apply(visitor)

    def as_dict(self):
        trace_dict = dict()
        trace_dict['plan_id'] = self.plan_id
        trace_dict['plan_name'] = self.plan_name
        trace_dict['plan_inputs'] = [
            item.item_id for item in self.get_inputs()]
        trace_dict['operations'] = [op.as_dict()
                                    for _, op in self.operations.items()]
        trace_dict['jobs'] = [job.as_dict() for _, job in self.jobs.items()]
        trace_dict['items'] = [item.as_dict()
                               for _, item in self.items.items()]
        trace_dict['files'] = [
            file.as_dict(path=file.generator.get_activity_id())
            for _, file in self.files.items()
            if file.generator
        ]
        super_dict = super().as_dict()
        return {**trace_dict, **super_dict}

    def project_from(self, activity):
        trace = PlanTrace(plan_id=self.plan_id, name=self.plan_name)
        trace.attributes = copy(self.attributes)
        operation_queue = deque()
        item_queue = deque()
        if activity.is_job():
            trace.add_job(activity)
            for _, entity in self.files.items():
                if entity.generator and entity.generator.is_job():
                    if entity.generator.job_id == activity.job_id:
                        trace.add_file(entity)
                        item_queue.extend(entity.sources)
            operation_queue.extend(activity.operations)
        else:
            operation_queue.append(activity)
            for _, entity in self.files.items():
                if entity.generator and not entity.generator.is_job():
                    if entity.generator.operation_id == activity.operation_id:
                        trace.add_file(entity)
                        item_queue.extend(entity.sources)

        visited_operations = set()
        visited_items = set()
        while operation_queue or item_queue:
            while operation_queue:
                op = operation_queue.popleft()
                if op.operation_id in visited_operations:
                    continue
                trace.add_operation(op)
                inputs = [self.get_item(input.item_id)
                          for input in op.inputs if input.is_item()]
                item_queue.extend(inputs)
                visited_operations.add(op.operation_id)

            while item_queue:
                item = item_queue.popleft()
                if item.item_id in visited_items:
                    continue
                trace.add_item(item)
                if item.generator:
                    operation_queue.append(item.generator)
                if item.is_part():
                    item_queue.append(item.collection)
                elif item.is_collection():
                    item_queue.extend(item.parts)
                item_queue.extend(item.sources)
                visited_items.add(item.item_id)

        return trace
